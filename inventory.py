"""Opt-in inventory. Runs on the selected machine; emits metadata, never credentials."""
import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

AGENTS = ('omp', 'codex', 'claude', 'agy', 'ollama', 'gemini', 'cursor-agent', 'grok')


def command(args):
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, timeout=20, text=True)
        return json.loads(p.stdout) if p.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def scan():
    omp = os.environ.get("OMP_BIN", "omp")
    agents = {a: '已安装 · 型号待确认' for a in AGENTS if shutil.which(omp if a == "omp" else a)}
    models = []
    def add(agent, provider, model, efforts, ready, image=False):
        if provider and model:
            models.append(dict(agent=agent, provider=provider, model=model, efforts=efforts or ['default'], ready=ready, image=image))
    if 'omp' in agents:
        accounts = command([omp, 'usage', 'accounts', '--json']) or {}
        providers = {a.get('provider') for a in accounts.get('accounts', [])}
        catalog = command([omp, 'models', 'ls', '--json', '--no-extensions'])
        for m in (catalog or {}).get('models', []):
            if m.get('kind', 'chat') == 'chat':
                add('omp', m.get('provider'), m.get('id'), m.get('thinking'), m.get('provider') in providers, 'image' in (m.get('input') or []))
        agents['omp'] = '目录已读取' if catalog else '目录不可读取'
    if 'codex' in agents:
        cache = Path(os.environ.get('CODEX_HOME', Path.home()/'.codex'))/'models_cache.json'
        try:
            cached = json.loads(cache.read_text()); fresh = 0 <= time.time()-cache.stat().st_mtime < 86400
            p = subprocess.run(['codex','login','status'], capture_output=True, timeout=8)
            for m in cached.get('models', []):
                if m.get('visibility','list') == 'list':
                    add('codex','openai',m.get('slug'),[e['effort'] for e in m.get('supported_reasoning_levels',[]) if e.get('effort')], fresh and p.returncode == 0, 'image' in (m.get('input_modalities') or []))
            agents['codex'] = '未登录' if p.returncode else ('已登录 · 缓存目录' if fresh else '目录已过期')
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
    if 'ollama' in agents:
        try:
            env = dict(os.environ, OLLAMA_HOST='127.0.0.1:11434')
            p = subprocess.run(['ollama','list'],capture_output=True,text=True,timeout=8,env=env)
            for line in p.stdout.splitlines()[1:] if p.returncode == 0 else []:
                if line.split(): add('ollama','local',line.split()[0],[],True)
            agents['ollama'] = '本机目录已读取' if p.returncode == 0 else '服务离线'
        except (OSError, subprocess.TimeoutExpired):
            pass
    return {'agents':agents,'models':models,'seen':int(time.time())}


def fetch(alias):
    if not alias:
        return scan()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,79}', alias):
        raise ValueError('请输入已配置的 SSH Host 别名')
    cmd = ['ssh','-o','BatchMode=yes','-o','StrictHostKeyChecking=yes','-o','ConnectTimeout=5','-o','ClearAllForwardings=yes','-o','ForwardAgent=no','--',alias,'python3','-']
    try:
        p = subprocess.run(cmd,input=Path(__file__).read_bytes(),capture_output=True,timeout=90)
        result = json.loads(p.stdout) if p.returncode == 0 else None
        if not isinstance(result,dict) or not isinstance(result.get('agents'),dict) or not isinstance(result.get('models'),list): raise ValueError()
        return result
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise ValueError('设备未连接，或远端缺少 Python 3') from None


if __name__ == '__main__':
    print(json.dumps(scan(),ensure_ascii=False))
