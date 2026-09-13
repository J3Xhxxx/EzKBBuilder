"""Opt-in live 50-card benchmark. Reuses durable receipts; never approves cards."""
from __future__ import annotations
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import inspect
import json
import os
from pathlib import Path
import sys
import time
from dotenv import load_dotenv

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from knowledge_pipeline.core.contracts import KnowledgeSpec
from knowledge_pipeline.core.service import PipelineService
from knowledge_pipeline.providers.factory import provider_from_env
from knowledge_pipeline.providers.base import ModelProviderError
from knowledge_pipeline.retrieval import EmbeddingProvider, KnowledgeIndex, answer_question

OUT=ROOT/'output'/'benchmark50'
WORKSPACE=ROOT/'.knowledge-workspace-benchmark50'

def save(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.replace(path)

def qa_fingerprint(row):
    contexts=[{'citation_id':c['chunk_id'],'text':c['text']} for h in row['hits'][:3] for c in h.get('evidence_chunks',[h])]
    configuration=[os.getenv(name,'') for name in ('KP_PROVIDER','KP_MODEL','KP_API_BASE','KP_ENABLE_THINKING','KP_THINKING_BUDGET')]
    value=[row['question'],contexts,configuration,inspect.getsource(answer_question)]
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()

class Measured:
    def __init__(self,path):
        self.provider=provider_from_env()
        if self.provider.name=='mock':
            raise ValueError('This benchmark requires a real provider')
        self.name=self.provider.name
        self.path=path
        self.calls=[]

    def complete(self,request):
        if len(self.calls)>=6:
            raise ModelProviderError('per-item API call limit reached')
        entry={'role':request.role,'status':'started','trace_id':request.trace_id}
        self.calls.append(entry)
        save(self.path,self.calls)
        start=time.monotonic()
        try:
            response=self.provider.complete(request)
            entry.update(status='responded',usage=response.usage,model=response.model,request_id=response.request_id)
            self.path.with_name(self.path.stem+f'-{len(self.calls):02d}-{request.role}.txt').write_text(response.text,encoding='utf-8')
            return response
        except Exception as exc:
            # Avoid secrets, prompt text and provider URLs in error receipts.
            entry.update(status='error',error_type=type(exc).__name__)
            raise
        finally:
            entry['seconds']=round(time.monotonic()-start,2)
            save(self.path,self.calls)

def generate(case):
    key=case['id']
    folder=OUT/'cards'/key
    receipt=folder/'receipt.json'
    if receipt.exists():
        return json.loads(receipt.read_text('utf-8'))
    # A previous unknown outcome must be reconciled, not silently paid for twice.
    if (folder/'calls.json').exists():
        service=PipelineService(WORKSPACE,provider_from_env())
        try:
            status=service.status('B50-'+key)
        except KeyError:
            raise RuntimeError(f'{key}: interrupted call receipt requires manual reconciliation')
        if status['state']=='PENDING_REVIEW':
            result={'id':key,'status':'PENDING_REVIEW','recovered':True}
            save(folder/'document.json',status['version']['document'])
            save(receipt,result)
            return result
        raise RuntimeError(f'{key}: previous attempt incomplete; inspect state before retrying')
    provider=Measured(folder/'calls.json')
    service=PipelineService(WORKSPACE,provider)
    source=case['source']
    body=(OUT/source['file']).read_text('utf-8')
    if hashlib.sha256(body.encode()).hexdigest()!=source['sha256']:
        raise ValueError('Frozen source hash mismatch')
    prepared=folder/'source.md'
    folder.mkdir(parents=True,exist_ok=True)
    prepared.write_text(f"# {case['title']} 来源\n\n原文：{source['url']}\n采集：{source['collected_at']}\n范围：下列官方文档快照。\n\n{body}",encoding='utf-8')
    spec=KnowledgeSpec(document_id='B50-'+key,title=case['title'],audience=f"{case['domain']}业务知识库检索与问答",
        objective='围绕“'+case['scope']+'”形成可独立检索、引用和回答实际使用问题的中文知识单元。保留相关参数原名、适用条件和例外，不扩展到未提供的现场数据或未覆盖功能。无需复述来源整页的无关主题。',
        metadata={'domain':case['domain'],'benchmark':'benchmark50-v1','upstream_url':source['url']})
    save(folder/'spec.json',spec.to_dict())
    start=time.monotonic()
    result={'id':key,'human_review_performed':False,'publication_performed':False}
    try:
        output=service.run_from_paths(spec,[prepared],allowed_roots=[folder])
        status=service.status(spec.document_id)
        result.update(status=output['status'],document_id=spec.document_id,content_sha256=status['version']['document']['content_sha256'])
        save(folder/'document.json',status['version']['document'])
        save(folder/'audit.json',status['version']['audit'])
        markdown=service.store.root/status['version']['markdown_path']
        (folder/'card.md').write_text(markdown.read_text('utf-8'),encoding='utf-8')
    except Exception as exc:
        result.update(status='ERROR',error_type=type(exc).__name__)
    result['seconds']=round(time.monotonic()-start,2)
    save(receipt,result)
    print(json.dumps(result,ensure_ascii=True),flush=True)
    return result

def evaluate(data,split,label):
    if not label.replace('-','').replace('_','').isalnum():
        raise ValueError('evaluation label must contain only letters, numbers, hyphens or underscores')
    evaluation=OUT/'evaluations'/label
    service=PipelineService(WORKSPACE,provider_from_env())
    embeddings=EmbeddingProvider.from_env()
    index=KnowledgeIndex(WORKSPACE,embeddings,sandbox=True)
    rebuilt=index.rebuild(service.store)
    save(OUT/'index.json',rebuilt)
    selected=[c for c in data['cards'] if split=='all' or c['split']==split]
    queries=[{'id':c['id']+f'-Q{i+1}','question':q,'expected_document':'B50-'+c['id'],'split':c['split'],'domain':c['domain'],'qa':i==1,'reference_facts':c['reference_facts']} for c in selected for i,q in enumerate(c['questions'])]
    # Freeze negative development/heldout assignment by position within domain.
    negatives=[{**q,'expected_document':None,'qa':True,'split':'dev' if i%4<2 else 'heldout'} for i,q in enumerate(data['negative_questions'])]
    queries += [q for q in negatives if split=='all' or q['split']==split]
    # Cache query embeddings in batches before ranking (same instruction as interactive search).
    index.vectors([q['question'] for q in queries],query=True)
    retrieval=[]
    for q in queries:
        hits=index.search(q['question'],top_k=5,store=service.store)
        rank=next((i+1 for i,h in enumerate(hits) if h['document_id']==q['expected_document']),None)
        record={**q,'rank':rank,'hits':hits}
        save(evaluation/'retrieval'/f"{q['id']}.json",record)
        retrieval.append(record)
    save(evaluation/f'embedding-calls-{split}.json',embeddings.calls)
    def qa(row):
        path=evaluation/'answers'/f"{row['id']}.json"
        fingerprint=qa_fingerprint(row)
        if path.exists():
            cached=json.loads(path.read_text('utf-8'))
            if cached.get('input_sha256')!=fingerprint:
                raise RuntimeError('QA inputs or configuration changed; use a new --label instead of reusing old answers')
            return cached
        calls=evaluation/'answers'/f"{row['id']}-calls.json"
        if calls.exists():
            raise RuntimeError('Uncertain previous QA attempt; reconcile before retry')
        provider=Measured(calls)
        try:
            answer=answer_question(provider,row['question'],row['hits'][:3])
            result={'id':row['id'],'question':row['question'],'expected_document':row['expected_document'],'reference_facts':row.get('reference_facts'),'split':row['split'],'domain':row['domain'],'result':answer}
        except Exception as exc:
            result={'id':row['id'],'error_type':type(exc).__name__}
        result['input_sha256']=fingerprint
        save(path,result)
        print('ANSWER',row['id'],result.get('result',{}).get('answerable'),flush=True)
        return result
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(qa,[r for r in retrieval if r['qa']]))
    positive=[r for r in retrieval if r['expected_document']]
    metrics={'split':split,'queries':len(positive),'index':rebuilt}
    for k in [1,3,5]:
        metrics[f'recall_at_{k}']=sum(r['rank'] is not None and r['rank']<=k for r in positive)/len(positive)
    metrics['mrr_at_5']=sum(1/r['rank'] if r['rank'] else 0 for r in positive)/len(positive)
    save(evaluation/f'metrics-{split}.json',metrics)
    print(json.dumps(metrics,ensure_ascii=True),flush=True)

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('phase',choices=['generate','evaluate'])
    p.add_argument('--ids',nargs='*')
    p.add_argument('--workers',type=int,default=4)
    p.add_argument('--split',choices=['dev','heldout','all'],default='dev')
    p.add_argument('--label',default='parent-v3',help='New label preserves prior evaluation receipts when retrieval or prompts change')
    args=p.parse_args()
    if not 1<=args.workers<=4:
        p.error('workers must be 1..4')
    load_dotenv(ROOT/'.env',encoding='utf-8-sig')
    os.environ.setdefault('KP_ENABLE_THINKING','false')
    os.environ['KP_EMBEDDING_MODEL']='Qwen/Qwen3-Embedding-8B'
    content=(OUT/'cases.frozen.json').read_text('utf-8').encode('utf-8')
    if hashlib.sha256(content).hexdigest()!=(OUT/'cases.sha256').read_text('utf-8'):
        raise ValueError('Frozen case manifest hash mismatch')
    data=json.loads(content)
    save(OUT/'run-config.json',{'provider':os.getenv('KP_PROVIDER'),'model':os.getenv('KP_MODEL'),'enable_thinking':os.getenv('KP_ENABLE_THINKING'),'embedding_model':os.getenv('KP_EMBEDDING_MODEL'),'workspace':str(WORKSPACE),'case_sha256':hashlib.sha256(content).hexdigest(),'human_review':False})
    if args.phase=='generate':
        cases=[c for c in data['cards'] if not args.ids or c['id'] in args.ids]
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            for future in as_completed([pool.submit(generate,c) for c in cases]):
                future.result()
    else:
        evaluate(data,args.split,args.label)

if __name__=='__main__':
    main()
