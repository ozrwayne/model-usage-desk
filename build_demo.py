"""Generate public assets from constants. Never read user accounts, devices or caches."""
import json
from datetime import timezone
from pathlib import Path
import server
from workspace import blank, example_scores, snapshot_view

ROOT=Path(__file__).resolve().parent/'demo-data'
STAMP=1794916800000

def generate():
    rows=[]
    for day in range(10):
        for i,model in enumerate(('example-writer','example-coder')):
            n=(day+1)*(i+2);ts=STAMP-day*server.DAY_MS-60000
            rows.append(dict(bucket=ts,firstTs=ts-60000,lastTs=ts,provider='example',model=model,agentType=('main','subagent')[i],requests=n,inputTokens=n*48,outputTokens=n*17,cacheReadTokens=n*20,cost=n*0.004))
        # Fictional local inference workload, separate from metered accounts.
        rows.append(dict(bucket=ts,firstTs=ts-60000,lastTs=ts,provider='local-deepseek',
                         model='DeepSeek-demo',agentType='main',requests=day+1,inputTokens=120*(day+1),
                         outputTokens=45*(day+1),cacheReadTokens=40*(day+1),cacheWriteTokens=0,cost=0))
    snapshots={}
    for r in ('24h','7d','all'):
        report=dict(provider='example',fetchedAt=STAMP,metadata={'email':'studio@example.invalid','accountId':'example-1'},limits=[server.limit_row('demo-week','示例额度','Weekly',0.48,STAMP+3*server.DAY_MS)])
        v=server.build_snapshot({'reports':[report]},[],{},[],rows,STAMP,r,[],day_zone=timezone.utc)
        w=snapshot_view(blank(True),example_scores(),demo=True,now=STAMP//1000);v['dispatch']=w.pop('dispatch');v['workspace']=w;v['usageEnabled']=True;snapshots[r]=v
    return snapshots

if __name__=='__main__':
    snapshots=generate();(ROOT/'snapshots').mkdir(parents=True,exist_ok=True)
    for name,payload in snapshots.items(): (ROOT/'snapshots'/f'{name}.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    (ROOT/'demo-snapshots.js').write_text('window.DEMO_SNAPSHOTS = '+json.dumps(snapshots,ensure_ascii=False,separators=(',',':'))+';\n',encoding='utf-8')
