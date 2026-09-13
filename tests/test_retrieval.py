import json
import math
import sys
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from knowledge_pipeline.core.contracts import KnowledgeSpec, PipelineState
from knowledge_pipeline.core.service import PipelineService
from knowledge_pipeline.providers.base import ModelResponse, ModelResponseError
from knowledge_pipeline.providers.mock import MockProvider
from knowledge_pipeline.retrieval import EmbeddingProvider, KnowledgeIndex, answer_question, document_chunks, normalize
from knowledge_pipeline.storage.sqlite import WorkspaceStore


class FakeEmbedding:
    model='fixture'
    identity='fixture-v1'
    def __init__(self):
        self.inputs=[]
    def embed(self,texts):
        self.inputs.extend(texts)
        return [[1.,0.] if 'Aurora' in t else [0.,1.] for t in texts]


class RetrievalTests(unittest.TestCase):
    def test_embedding_contract_order_dimension_and_nonfinite(self):
        provider=EmbeddingProvider('https://api.example/v1/chat/completions','fixture')
        self.assertEqual('https://api.example/v1/embeddings',provider.url)
        self.assertEqual(provider.url,EmbeddingProvider('https://api.example/v1/embeddings','fixture').url)
        with patch('knowledge_pipeline.retrieval.post_json',return_value=({'data':[{'index':1,'embedding':[0,2]},{'index':0,'embedding':[3,0]}]},None)):
            self.assertEqual([[1.,0.],[0.,1.]],provider.embed(['a','b']))
        invalid=[[],[{'index':0,'embedding':[1,0]}]*2,[{'index':0,'embedding':[1,0]},{'index':1,'embedding':[1]}],
                 [{'index':0,'embedding':[float('nan'),1]},{'index':1,'embedding':[0,1]}]]
        for data in invalid:
            with self.subTest(data=data),patch('knowledge_pipeline.retrieval.post_json',return_value=({'data':data},None)):
                with self.assertRaises(ModelResponseError):
                    provider.embed(['a','b'])
        for vector in [[0,0],[True,1],[math.inf,0]]:
            with self.assertRaises(ModelResponseError):
                normalize(vector)

    def test_query_instruction_and_cache_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            embedding=FakeEmbedding()
            index=KnowledgeIndex(Path(temp),embedding)
            index.vectors(['Aurora','Aurora'])
            self.assertEqual(['Aurora'],embedding.inputs)
            index.vectors(['Aurora'])
            self.assertEqual(1,len(embedding.inputs))
            index.vectors(['Aurora'],query=True)
            self.assertTrue(embedding.inputs[-1].startswith('Instruct: '))
            self.assertTrue(embedding.inputs[-1].endswith('\nQuery: Aurora'))
            embedding.identity='another-model'
            index.vectors(['Aurora'])
            self.assertEqual(3,len(embedding.inputs))

    def test_drafts_separate_and_returned_or_stale_version_not_retrieved(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            source=root/'source.md'
            source.write_text('Aurora 支持离线初始化。',encoding='utf-8')
            service=PipelineService(root/'work',MockProvider())
            spec=KnowledgeSpec(document_id='CARD-A',title='Aurora')
            run=service.run_from_paths(spec,[source],allowed_roots=[root])
            embedding=FakeEmbedding()
            live=KnowledgeIndex(service.store.root,embedding)
            sandbox=KnowledgeIndex(service.store.root,embedding,sandbox=True)
            self.assertEqual(0,live.rebuild(service.store)['documents'])
            self.assertEqual(1,sandbox.rebuild(service.store)['documents'])
            hit=sandbox.search('Aurora',store=service.store)[0]
            self.assertEqual('CARD-A',hit['document_id'])
            self.assertEqual('CARD-A:card',hit['evidence_chunks'][0]['chunk_id'])
            self.assertIn('Aurora',hit['evidence_chunks'][0]['text'])
            service.review('CARD-A',run['document_sha256'],'test','approve')
            self.assertEqual(1,live.rebuild(service.store)['documents'])
            self.assertEqual(1,len(live.search('Aurora',store=service.store)))
            service.store.transition('CARD-A',PipelineState.RETURNED,'test quality withdrawal')
            self.assertEqual([],live.search('Aurora',store=service.store))
            service.run_from_paths(spec,[source],allowed_roots=[root])
            # New pending version must not accidentally surface old sandbox text.
            self.assertEqual([],sandbox.search('Aurora',store=service.store))
            sandbox.rebuild(service.store)
            self.assertEqual(1,len(sandbox.search('Aurora',store=service.store)))
            embedding.identity='changed'
            with self.assertRaisesRegex(ValueError,'rebuild'):
                sandbox.search('Aurora',store=service.store)

    def test_rebuild_embedding_failure_preserves_previous_index(self):
        class Store:
            def list_documents(self):
                return [{'document_id':'A'}]
            def get_document(self,_):
                return {'current_version_id':'v1','state':'APPROVED'}
            def get_current_version(self,_):
                return {'document':{'document_id':'A','title':self.title,'sections':{'summary':'scope','key_points':'facts'},'content_sha256':self.title,'used_source_ids':['SRC001'],'source_bundle_sha256':'bundle','profile_version':'1'}}
        with tempfile.TemporaryDirectory() as temp:
            embedding=FakeEmbedding()
            index=KnowledgeIndex(Path(temp),embedding)
            store=Store()
            store.title='Aurora'
            index.rebuild(store)
            store.title='Changed'
            with patch.object(embedding,'embed',side_effect=ModelResponseError('failed')):
                with self.assertRaises(ModelResponseError):
                    index.rebuild(store)
            self.assertEqual('Aurora',index.search('Aurora')[0]['title'])

    def test_chunks_retain_scope_and_whole_conditions(self):
        condition='只有传感器处于检定有效期内才能采用此读数；过期必须重新检定。'
        document={'title':'M100 传感器','sections':{'summary':'仅适用 M100 v2。','key_points':('a'*100+'\n\n')+condition}}
        chunks=document_chunks(document,max_chars=100)
        self.assertTrue(all('仅适用 M100 v2。' in c for c in chunks))
        self.assertTrue(any(condition in c for c in chunks))
        stored={'title':'M100','sections':{'boundaries':'b'*100,'examples':'e'*100,'key_points':'核心规则：'+condition,'summary':'仅适用 v2。'}}
        self.assertIn('核心规则',document_chunks(stored,max_chars=100)[0])

    def test_answer_requires_real_citations_and_empty_index_skips_model(self):
        class Provider:
            def complete(self,request):
                return ModelResponse(text=json.dumps(self.value),provider='fixture')
        provider=Provider()
        self.assertFalse(answer_question(provider,'unknown',[])['answerable'])
        hits=[{'chunk_id':'A:0','text':'A supports offline mode.'}]
        for value in [{'answer':'yes','answerable':True,'citations':[]},
                      {'answer':'yes','answerable':True,'citations':['INVENTED:0']},
                      {'answer':'yes','answerable':'true','citations':['A:0']}]:
            provider.value=value
            with self.assertRaises(ModelResponseError):
                answer_question(provider,'question',hits)
        provider.value={'answer':'资料未说明。','answerable':False,'citations':[]}
        self.assertFalse(answer_question(provider,'question',hits)['answerable'])
        # A condition may live in a second unit of the same card. That unit
        # must actually reach the model and be accepted as citation evidence.
        hits[0]['evidence_chunks']=[{'chunk_id':'A:0','text':'scope'}, {'chunk_id':'A:1','text':'only offline'}]
        provider.value={'answer':'only offline','answerable':True,'citations':['A:1']}
        with patch.object(provider,'complete',wraps=provider.complete) as call:
            self.assertEqual(['A:1'],answer_question(provider,'condition?',hits)['citations'])
            context=json.loads(call.call_args.args[0].user_prompt)['contexts']
            self.assertEqual(['A:0','A:1'],[c['citation_id'] for c in context])

    def test_parallel_initial_workspace_creation(self):
        with tempfile.TemporaryDirectory() as temp:
            with ThreadPoolExecutor(max_workers=8) as pool:
                stores=list(pool.map(lambda _:WorkspaceStore(Path(temp)/'new-workspace'),range(16)))
            self.assertTrue(all(s.list_documents()==[] for s in stores))

if __name__=='__main__':
    unittest.main()
