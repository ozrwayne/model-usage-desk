import hashlib
import json
import os
import re
import threading
import time
import uuid

import inventory
import ratings

TASKS = [('对话问答','general'),('项目规划','agentic'),('结果核对','general'),('编写代码','coding'),('调试程序','coding'),('起草文稿','general'),('语言翻译','general'),('编辑校对','general'),('资料检索','agentic'),('提取数据','general'),('分析图像','general'),('摘要汇总','general'),('自动化操作','agentic')]
METRIC={'general':'综合指数','coding':'工程指数（参考）','agentic':'策略与运营指数（参考）'}
lock=threading.RLock()


def device(name,alias=''):
    return dict(name=name,alias=alias,agents={},models=[],manual=[],seen=0,error='')


def blank(demo=False):
    state=dict(title='模型用量',consent=False,devices={'local':device('本机')},pins={},disabled=[],scores=False)
    if demo:
        state.update(title='示例工作室',consent=True)
        for id,name,agent,model in [('local','书桌电脑','示例编辑器','example-writer'),('studio','计算工作站','示例终端','example-coder')]:
            d=device(name); d.update(agents={agent:'演示目录'},models=[dict(agent=agent,provider='example',model=model,efforts=['high'],ready=True,image=id=='local')],seen=1);state['devices'][id]=d
    return state


def example_scores():
    return dict(rows=[dict(id=m,creator='example',slug=m,name=m,effort='high',general=g,coding=c,agentic=a,cost=p) for m,g,c,a,p in [('example-writer',48,25,36,0.08),('example-coder',40,51,46,0.12)]],updated=0,version='示例')


def text(value,limit=100):
    if not isinstance(value,str) or not (v:=value.strip()) or len(v)>limit or not v.isprintable(): raise ValueError('名称为空、含控制字符或过长')
    return v


def snapshot_view(s, scores, accounts=(), demo=False, now=None):
    now=int(time.time()) if now is None else now
    by_rating=ratings.lookup(scores.get('rows',[])); models=[];devices=[]; caps={}; options={}
    quotas={}
    for a in accounts:
        if a.get('stored') or not 0 <= now-(a.get('dataAt') or 0)/1000 < 21600: continue
        windows=[w['used'] for w in a.get('windows',[]) if isinstance(w.get('used'),(int,float)) and not w.get('stored')]
        if windows: quotas.setdefault(a.get('provider'),[]).append(max(windows))
    for id,d in s['devices'].items():
        online=not d['error'] and bool(d['seen']) and (demo or 0<=now-d['seen']<86400)
        devices.append(dict(id=id,name=d['name'],alias=d['alias'],agents=d['agents'],seen=d['seen'],status='离线' if d['error'] else '已盘点' if online else '待读取' if not d['seen'] else '已过期',error=d['error']))
        entries={}
        for m in d['models']+d['manual']:
            for e in (m.get('efforts') or ['default']): entries[(m['agent'],m['provider'],m['model'],e)]=m
        for (agent,p,model,e),m in entries.items():
            key=hashlib.sha256(json.dumps([id,agent,p,model,e],ensure_ascii=False).encode()).hexdigest()[:24]; disabled=key in s['disabled']; ready=online and bool(m.get('ready')); score=by_rating.get((ratings.norm(model),e)) or {}; q=min(quotas[p]) if id=='local' and agent=='omp' and p in quotas else None
            label=f'{model} · {e} · {d["name"]} / {agent} / {p}'
            row=dict(key=key,device=id,name=d['name'],label=label,agent=agent,provider=p,model=model,effort=e,ready=ready,disabled=disabled,score=score.get('general'),coding=score.get('coding'),agentic=score.get('agentic'),cost=score.get('cost'),used=q,image=bool(m.get('image')),status='设备离线或已过期' if not online else '用户禁用' if disabled else '手动确认' if m.get('manual') else '目录可用' if ready else '权限待确认')
            models.append(row);options[key]=row
            cap=caps.setdefault(model,dict(model=model,tags=[],efforts=[],provider=p)); tag=d['name']+' / '+agent
            if tag not in cap['tags']:cap['tags'].append(tag)
            if not any(x['effort']==e for x in cap['efforts']): cap['efforts'].append(dict(effort=e,index=score.get('general'),cost=score.get('cost')))
    routes=[]
    for i,(task,metric) in enumerate(TASKS,1):
        field='score' if metric=='general' else metric
        candidates=[r for r in models if r['ready'] and not r['disabled'] and (r['used'] is None or r['used']<0.995) and (i!=11 or r['image'])]; pin=s['pins'].get(str(i)); fit=[r for r in candidates if r[field] is not None]; fit.sort(key=lambda r:(r['used'] is not None and r['used']>=0.8,-r[field],float('inf') if r['cost'] is None else r['cost'],r['label']))
        chosen=next((r for r in candidates if r['key']==pin),None) if pin else (fit[0] if fit else None)
        reason=('用户固定' if chosen else '固定项当前不可用，保留选择') if pin else (METRIC[metric]+' '+str(chosen[field]) if chosen else '缺少可用型号或对应评分，可手动指定')
        alt=next((r for r in fit if r!=chosen),None)
        routes.append(dict(index=i,task=task,floor=METRIC[metric],reason=reason,pin=pin,preferred=chosen['label'] if chosen else '暂无可用推荐',alternate=alt['label'] if alt else '',avoid='',preferredUsed=chosen['used'] if chosen else None))
    return dict(title=s['title'],consent=s['consent'],demo=demo,devices=devices,models=models,scoreInfo=dict(enabled=s['scores'],hasKey=bool(os.getenv('AA_API_KEY')) and not demo,updated=scores.get('updated',0),version=scores.get('version'),error=scores.get('error',''),stale=not demo and (not scores.get('updated') or now-scores['updated']>=86400)),dispatch=dict(routes=routes,capabilities=list(caps.values()),channels=[],sampling=[]))


class Workspace:
    def __init__(self,root,demo=False):
        self.demo=demo;self.path=root/('demo-workspace.json' if demo else 'workspace.json');self.cache=root/'aa-cache.json';self.s=ratings.read(self.path,blank(demo))

    def view(self,accounts=()):
        with lock: return snapshot_view(self.s,example_scores() if self.demo else ratings.read(self.cache,{}),accounts,self.demo)

    def scan(self,id):
        d=self.s['devices'][id]
        try:
            info=inventory.fetch(d['alias']); d.update(agents=info['agents'],models=info['models'],seen=int(time.time()),error='')
        except ValueError as err: d['error']=str(err)

    def change(self,body):
        with lock:
            op=body.get('op');s=self.s;id=body.get('device','local');d=s['devices'].get(id)
            if op=='title': s['title']=text(body.get('name'),64)
            elif op=='rename' and d: d['name']=text(body.get('name'),64)
            elif op=='scan' and d and not self.demo:
                if id=='local': s['consent']=True
                self.scan(id)
            elif op=='pair' and not self.demo and s['consent']:
                alias=text(body.get('alias'),80);name=text(body.get('name'),64)
                if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9._-]*',alias) or any(x['alias']==alias for x in s['devices'].values()): raise ValueError('别名无效或已登记')
                id=uuid.uuid4().hex;s['devices'][id]=device(name,alias);self.scan(id)
            elif op=='manual' and d and s['consent']:
                agent,provider,model,effort=[text(body.get(k)) for k in ('agent','provider','model','effort')]; m=dict(agent=agent,provider=provider,model=model,efforts=[effort],ready=True,image=body.get('image') is True,manual=True); d['manual']=[x for x in d['manual'] if (x['agent'],x['provider'],x['model'],x['efforts'])!=(agent,provider,model,[effort])]+[m]
            elif op in ('pin','toggle'):
                key=body.get('key',''); known={r['key']:r for r in self.view()['models']}
                if key and key not in known: raise ValueError('型号已变更，请刷新')
                if op=='pin':
                    task=str(body.get('task')); 
                    if task not in {str(i) for i in range(1,len(TASKS)+1)}: raise ValueError('任务无效')
                    if key:s['pins'][task]=key
                    else:s['pins'].pop(task,None)
                elif key:
                    disabled=set(s['disabled']);disabled.symmetric_difference_update([key]);s['disabled']=sorted(disabled)
            elif op in ('sync','stopSync') and not self.demo:
                if op=='sync' and not os.getenv('AA_API_KEY'):raise ValueError('请在服务环境设置 AA_API_KEY 后重启')
                s['scores']=op=='sync'
                if s['scores']:ratings.sync(self.cache,os.environ['AA_API_KEY'],force=True)
            else: raise ValueError('此操作不可用')
            ratings.save(self.path,s)

    def maintain(self):
        if self.demo:return
        with lock:
            for id,d in self.s['devices'].items() if self.s['consent'] else ():
                if time.time()-d.get('attempt',d['seen'])>=3600:
                    self.scan(id);d['attempt']=int(time.time());ratings.save(self.path,self.s)
        if self.s['scores']:ratings.sync(self.cache,os.getenv('AA_API_KEY'))
