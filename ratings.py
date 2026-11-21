"""Artificial Analysis free API. No device inventory is sent to the provider."""
import json
import math
import os
import re
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener

DAY = 86400
API = 'https://artificialanalysis.ai/api/v2/language/models/free'
lock = threading.Lock()


def save(path, data):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile('w',encoding='utf-8',dir=path.parent,delete=False) as f:
        try:
            json.dump(data,f,ensure_ascii=False,allow_nan=False); f.flush(); os.fsync(f.fileno())
        except BaseException:
            os.unlink(f.name); raise
    os.replace(f.name,path)


def read(path, default):
    try: return json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError: return default


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args): return None


def page(number, key):
    req = Request(f'{API}?page={number}',headers={'x-api-key':key, 'Accept':'application/json','User-Agent':'Usage-Dashboard/1'})
    with build_opener(NoRedirect()).open(req,timeout=12) as response:
        return json.loads(response.read(16_000_001))


def number(x):
    return x if type(x) in (int,float) and math.isfinite(x) and x>=0 else None


def sync(path, key, *, force=False, fetch=page):
    with lock:
        s=read(path,{}); now=int(time.time())
        if not key: return s
        if now < s.get('retryAfter',0) or (not force and now < s.get('checked',0)+DAY): return s
        try:
            rows=[]; version=None
            for n in range(1,501):
                doc=fetch(n,key); pagination=doc['pagination']; batch=doc['data']; current=doc['intelligence_index_version']
                if pagination['page']!=n or not isinstance(batch,list) or (version is not None and version!=current): raise ValueError()
                version=current
                for model in batch:
                    name=model['name']; match=re.search(r'\s*\((minimal|low|medium|high|xhigh|max|ultra|non-reasoning)\)$',name,re.I); ev=model['evaluations']; cost=(model.get('artificial_analysis_intelligence_index_cost') or {}).get('cost_per_task') or {}
                    rows.append(dict(
                        id=model['id'], creator=model['model_creator']['id'], slug=model['slug'],
                        name=name[:match.start()] if match else name, effort=match[1].lower() if match else 'default',
                        general=number(ev.get('artificial_analysis_intelligence_index')),
                        coding=number(ev.get('artificial_analysis_engineering_index')),
                        agentic=number(ev.get('artificial_analysis_strategy_and_ops_index')),
                        cost=number(cost.get('total_cost'))))
                if pagination['has_more'] is False: break
            else: raise ValueError()
            if not rows: raise ValueError()
            s=dict(rows=rows,updated=now,version=version)
        except (OSError, ValueError,TypeError,KeyError) as err:
            s['error']=f'HTTP {err.code}' if isinstance(err,HTTPError) else '评分源不可用或响应格式已变化'
            delay=900
            if isinstance(err,HTTPError) and err.code==429:
                try: delay=max(delay,int(err.headers.get('Retry-After',900)))
                except ValueError: delay=DAY
            s['retryAfter']=now+delay
        s['checked']=now;save(path,s);return s


def norm(s): return re.sub(r'[^a-z0-9]+','-',s.lower()).strip('-')


def lookup(rows):
    out={}
    for r in rows:
        for k in {norm(r['name']),norm(r['slug'])}:
            key=(k,r['effort']); previous=out.get(key,r)
            out[key]=r if previous and (previous['creator'],previous['id'])==(r['creator'],r['id']) else None
    return out
