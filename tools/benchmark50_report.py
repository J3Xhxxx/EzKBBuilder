"""Aggregate measured receipts after semantic review; does not auto-grade text.

The review manifest must be written explicitly by the evaluator. An answerable
flag alone is never counted as a correct answer.
"""
from pathlib import Path
import collections
import hashlib
import json
import statistics

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'output'/'benchmark50'

def read(path):
    return json.loads(path.read_text('utf-8'))

def summarize():
    evaluation=OUT/'evaluations'/'parent-v3'
    reviewed=read(evaluation/'semantic-review.json')
    judgments={x['id']:x for x in reviewed['judgments']}
    records=[read(p) for p in (evaluation/'answers').glob('*.json') if not p.stem.endswith('-calls')]
    assert len(records)==70 and len(judgments)==70
    for row in records:
        answer_hash=hashlib.sha256(json.dumps(row['result'],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if judgments[row['id']]['answer_sha256']!=answer_hash:
            raise ValueError('Answer changed after semantic review')
    cards=[read(p) for p in (OUT/'cards').glob('*/receipt.json')]
    queries=[read(p) for p in (evaluation/'retrieval').glob('*.json')]
    positives=[r for r in records if r['expected_document']]
    negatives=[r for r in records if not r['expected_document']]
    retrieval=[r for r in queries if r['expected_document']]
    metrics={'cards':len(cards),'machine_audit_pass':sum(r['status']=='PENDING_REVIEW' for r in cards),
             'retrieval_queries':len(retrieval),'top1':sum(r['rank']==1 for r in retrieval),
             'top3':sum(r['rank'] is not None and r['rank']<=3 for r in retrieval),
             'top5':sum(r['rank'] is not None and r['rank']<=5 for r in retrieval),
             'mrr_at_5':sum(1/r['rank'] if r['rank'] else 0 for r in retrieval)/len(retrieval),
             'qa_questions':len(positives),'qa_correct':sum(judgments[r['id']]['passed'] for r in positives),
             'negative_questions':len(negatives),'negative_correct':sum(judgments[r['id']]['passed'] for r in negatives),
             'domains':{},'review_method':reviewed['method']}
    for domain in sorted({r['domain'] for r in retrieval}):
        rs=[r for r in retrieval if r['domain']==domain]
        ans=[r for r in positives if r['domain']==domain]
        ns=[r for r in negatives if r['domain']==domain]
        metrics['domains'][domain]={'cards':len(ans),'top1':sum(r['rank']==1 for r in rs),'top3':sum(r['rank'] is not None and r['rank']<=3 for r in rs),'retrieval_queries':len(rs),'qa_correct':sum(judgments[r['id']]['passed'] for r in ans),'negative_correct':sum(judgments[r['id']]['passed'] for r in ns)}
    calls=[]
    for path in OUT.rglob('*calls.json'):
        # Embedding receipts have a different name/schema and are counted separately.
        values=read(path)
        if isinstance(values,list) and (not values or 'role' in values[0]):
            calls += values
    # Per-card calls.json does not match *-calls.json on some platforms; rglob
    # above includes both because the glob begins with *, so no second scan.
    response_calls=[c for c in calls if c.get('status')=='responded']
    embedding_calls=[]
    for path in OUT.rglob('embedding-calls*.json'):
        embedding_calls += read(path)
    metrics['usage']={'chat_attempts':len(calls),'chat_responses':len(response_calls),
        'chat_tokens':sum((c.get('usage') or {}).get('total_tokens',0) for c in response_calls),
        'chat_errors':dict(collections.Counter(c.get('error_type','unfinished') for c in calls if c.get('status')!='responded')),
        'embedding_requests':len(embedding_calls),'embedding_tokens':sum((c.get('usage') or {}).get('total_tokens',0) for c in embedding_calls),
        'chat_roles':dict(collections.Counter(c['role'] for c in calls)),
        'excludes':'Preflight and interactive browser requests are separate; no monetary price inferred.'}
    durations=sorted(r['seconds'] for r in cards if 'seconds' in r)
    metrics['generation_seconds']={'median':statistics.median(durations),'p95':durations[int(.95*len(durations))-1],'max':max(durations)}
    (evaluation/'summary.json').write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(metrics,ensure_ascii=False,indent=2))

if __name__=='__main__':
    summarize()
