"""Paired stage 2 comparison of two L1 runs on one split (Qwen 3.8 vs Gemma, KOS-qwen38). Aggregates only.

Usage: python scripts/qwen38/compare_runs.py <baseline run> <new run> <dev|test>
  e.g. python scripts/qwen38/compare_runs.py evals/reports/20261116-193741-KOS-192-condyears evals/reports/<qwen run> test
Refusal = stage 2 picked no table (or no candidates), as the service gate does. H1_service counts a refused
answerable question as a miss.
"""
import json,sys
def load(r): return {x['qid']:x for x in map(json.loads,open(r+'/results.jsonl',encoding='utf-8'))}
a,b,split=load(sys.argv[1]),load(sys.argv[2]),sys.argv[3]
q=[k for k in b if b[k]['split']==split and k in a]
def stats(d):
    ans=[k for k in q if d[k]['answerable']]; na=[k for k in q if not d[k]['answerable']]
    ref=lambda k: d[k].get('stage2_picked_n')==0 or not d[k]['keys']
    tp=sum(ref(k) for k in na); fp=sum(ref(k) for k in ans)
    p=tp/(tp+fp) if tp+fp else 0; r=tp/len(na) if na else 0
    h1=sum((d[k]['hit@1'] or 0) and not ref(k) for k in ans)/len(ans)
    mrr=sum(d[k]['rr'] or 0 for k in ans)/len(ans)
    err=sum(bool(d[k].get('stage2_error')) for k in q)
    lat=sorted(d[k]['latency_ms'] for k in q); 
    return dict(n=len(q),ans=len(ans),na=len(na),H1_raw=round(sum(d[k]['hit@1'] or 0 for k in ans)/len(ans),3),H1_service=round(h1,3),MRR=round(mrr,3),refuse=f"{tp}/{len(na)}",false_refuse=f"{fp}/{len(ans)}",F1=round(2*p*r/(p+r),3) if p+r else 0,errors=err,p50_s=round(lat[len(lat)//2]/1000,2))
print('A', sys.argv[1], stats(a)); print('B', sys.argv[2], stats(b))
ans=[k for k in q if a[k]['answerable']]
w=sum((b[k]['hit@1'] or 0)>(a[k]['hit@1'] or 0) for k in ans); l=sum((b[k]['hit@1'] or 0)<(a[k]['hit@1'] or 0) for k in ans)
print(f'H1 paired: B wins {w}, loses {l}')
