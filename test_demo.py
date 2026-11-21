#!/usr/bin/env python3
"""Privacy, data, live dispatch, authentication and browser checks."""
import http.cookiejar
import json
import os
from pathlib import Path
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen

ROOT = Path(__file__).resolve().parent
RANGES = ("24h", "7d", "all")
CHROME = os.environ.get("CHROME") or shutil.which("google-chrome") or shutil.which("chromium") or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def free_ports():
    sockets = []
    try:
        for _ in range(2):
            sock = socket.socket()
            sock.bind(("127.0.0.1", 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


def request(url, **kwargs):
    with urlopen(url, timeout=8, **kwargs) as response:
        return response.status, response.read()


BROWSER_SCRIPT = r'''const [port, fileUrl, httpUrl, shareUrl] = process.argv.slice(2);
const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
const ws = new WebSocket(targets.find(x => x.type === "page").webSocketDebuggerUrl);
await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
let id = 0; const pending = new Map();
ws.onmessage = ({data}) => { const m = JSON.parse(data); if (m.id && pending.has(m.id)) { const [ok, fail] = pending.get(m.id); pending.delete(m.id); m.error ? fail(Error(m.error.message)) : ok(m.result); } };
function cmd(method, params = {}) { return new Promise((ok, fail) => { const n = ++id; pending.set(n, [ok, fail]); ws.send(JSON.stringify({id:n, method, params})); }); }
async function run(expression) { const r = await cmd("Runtime.evaluate", {expression, returnByValue:true, awaitPromise:true}); if (r.exceptionDetails) throw Error(JSON.stringify(r.exceptionDetails)); return r.result.value; }
async function until(expression) { for (let i=0; i<120; i++) { try { if (await run(expression)) return; } catch (_) {} await new Promise(r=>setTimeout(r,100)); } throw Error("Timed out: " + expression); }
function check(ok, what) { if (!ok) throw Error(what); }
await cmd("Page.enable"); await cmd("Runtime.enable");
await cmd("Page.navigate", {url:fileUrl});
await until('document.querySelectorAll(".accounts .card").length > 0');
await run(`document.querySelector('[data-range="24h"]').click()`);
await until('latestData && latestData.range === "24h"');
await run('document.querySelector("[data-tab=dispatch]").click()');
await until(`!!document.querySelector('[data-quest-idx="2"]')`);
await run(`document.querySelector('[data-quest-idx="2"]').click()`);
check(await run('document.querySelector(".ins-head").textContent.includes("#2")'), "quest inspector");
await run('document.querySelector("[data-tab=models]").click()');
await until('!!document.querySelector("[data-model-row=example-writer]")');
await run('document.querySelector("[data-tier-filter=low]").click()');
check(await run('!document.querySelector("[data-model-row]")'), 'no matching tier');
await cmd("Page.navigate", {url:shareUrl});
await until('!!document.querySelector("input[type=password]")');
await run(`document.querySelector('input[type=password]').value='demo-test-password'; document.querySelector('form').requestSubmit()`);
await until('!!document.querySelector(".accounts .card")');
check(await run(`document.querySelector('.accounts h3').textContent === '账号 01' && document.querySelector('#tabs').hidden`),'read-only dashboard');
await cmd("Page.navigate", {url:httpUrl});
await until('!!document.querySelector(".accounts .card")');
await run(`document.querySelector('[data-range="24h"]').click()`);
await until('latestData && latestData.range === "24h"');
await cmd("Network.enable");
await cmd("Network.emulateNetworkConditions", {offline:true,latency:0,downloadThroughput:0,uploadThroughput:0});
await run('document.querySelector("#refresh").click()');
await until('document.querySelector("#health-text").textContent === "离线"');
check(await run('!document.querySelector(".accounts .card")'), 'never replace failed live request with demo');
ws.close(); console.log("browser interactions passed");
'''


class DemoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(prefix=".test-", dir=ROOT)
        cls.temp = Path(cls.tmp.name)
        cls.port, cls.public_port = free_ports()
        cls.base = f"http://127.0.0.1:{cls.port}"
        cls.public = f"http://127.0.0.1:{cls.public_port}"
        cls.env = os.environ.copy()
        cls.env.update(USAGE_DATA_DIR=str(cls.temp/'data'),USAGE_AUTH_DIR=str(cls.temp/'auth'),PYTHONDONTWRITEBYTECODE='1')
        subprocess.run([sys.executable, "-c", "import server; server.save_auth_config('demo-test-password')"], cwd=ROOT, env=cls.env, check=True)
        cls.proc = subprocess.Popen([sys.executable, "server.py", "--demo", "--port", str(cls.port), "--public-port", str(cls.public_port), "--no-open"], cwd=ROOT, env=cls.env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(100):
            try:
                if request(cls.base + "/")[0] == 200:
                    return
            except (OSError, URLError):
                if cls.proc.poll() is not None:
                    break
                time.sleep(0.1)
        cls.tearDownClass()
        raise RuntimeError("demo server failed to start")

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "proc"):
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                cls.proc.kill()
                cls.proc.wait()
        if hasattr(cls, "tmp"):
            cls.tmp.cleanup()

    def test_demo_data_is_fully_reproducible(self):
        from build_demo import generate
        expected = generate()
        disk = {r: json.loads((ROOT/'demo-data'/'snapshots'/f'{r}.json').read_text()) for r in RANGES}
        self.assertEqual(expected, disk)
        js = (ROOT/'demo-data/demo-snapshots.js').read_text().removeprefix('window.DEMO_SNAPSHOTS = ').strip().removesuffix(';')
        self.assertEqual(json.loads(js), disk)
        self.assertGreater(disk['all']['spend']['requests'],disk['7d']['spend']['requests'])
        self.assertEqual([(p.name) for p in (ROOT/'demo-data').rglob('*.tsv')], [])

    def test_workspace_edits_require_direct_same_origin(self):
        def post(origin, data):
            return request(Request(self.base+'/api/workspace', data=json.dumps(data).encode(), headers={'Origin':origin,'Content-Type':'application/json','X-Dashboard-Action':'edit'}))
        with self.assertRaises(HTTPError) as e: post('https://invalid.example',{'op':'title','name':'bad'})
        self.assertEqual(e.exception.code,403)
        post(self.base,{'op':'title','name':'独立试用'})
        snap=json.loads(request(self.base+'/api/snapshot')[1]);self.assertEqual(snap['workspace']['title'],'独立试用')
        post(self.base,{'op':'title','name':'示例工作室'})

    def test_empty_inventory_and_new_model(self):
        from workspace import blank,snapshot_view
        s=blank();v=snapshot_view(s,{});self.assertEqual({r['preferred'] for r in v['dispatch']['routes']},{'暂无可用推荐'})
        d=s['devices']['local'];d.update(seen=int(time.time()),models=[dict(agent='cli',provider='test',model='new-catalog-model',efforts=['high'],ready=True)])
        v=snapshot_view(s,{});self.assertEqual(v['models'][0]['model'],'new-catalog-model');self.assertIsNone(v['models'][0]['score'])
        self.assertEqual({r['preferred'] for r in v['dispatch']['routes']},{'暂无可用推荐'})

    def test_rename_offline_disable_and_restore_pin(self):
        from workspace import Workspace
        w=Workspace(self.temp/'policies',True);r=w.view()['models'][1];key=r['key'];w.change(dict(op='pin',task=4,key=key));w.change(dict(op='rename',device=r['device'],name='新设备名'))
        w=Workspace(self.temp/'policies',True);route=lambda:w.view()['dispatch']['routes'][3]
        self.assertIn('新设备名',route()['preferred']);self.assertEqual(route()['pin'],key)
        w.s['devices'][r['device']]['error']='离线';self.assertEqual(route()['preferred'],'暂无可用推荐');self.assertEqual(route()['pin'],key)
        w.s['devices'][r['device']]['error']='';w.change(dict(op='toggle',key=key));self.assertEqual(route()['preferred'],'暂无可用推荐');w.change(dict(op='pin',task=4,key=''));self.assertIn('书桌电脑',route()['preferred'])

    def test_rating_pages_commit_together(self):
        import ratings
        path=self.temp/'scores.json'
        def page(n,k):
            return dict(intelligence_index_version=5,pagination=dict(page=n,has_more=n==1),data=[dict(id=str(n),model_creator={'id':'maker'},name=f'Example {n} (High)',slug=f'example-{n}',evaluations={'artificial_analysis_intelligence_index':n,'artificial_analysis_engineering_index':10+n})])
        first=ratings.sync(path,'fixture-key',fetch=page);self.assertEqual(len(first['rows']),2);self.assertEqual(ratings.lookup(first['rows'])[('example-2','high')]['coding'],12)
        def broken(n,k):
            if n==2:raise OSError('offline')
            return page(n,k)
        fail=ratings.sync(path,'fixture-key',force=True,fetch=broken);self.assertEqual(fail['rows'],first['rows']);self.assertEqual(fail['updated'],first['updated']);self.assertTrue(fail['error'])

    def test_07_public_listener_authentication(self):
        with self.assertRaises(HTTPError) as error:
            request(self.public + "/api/snapshot?range=7d")
        self.assertEqual(error.exception.code, 401)
        self.assertEqual(request(self.public + "/login")[0], 200)
        jar = http.cookiejar.CookieJar()
        opener = build_opener(HTTPCookieProcessor(jar))
        req = Request(self.public + "/auth/login", data=b"password=demo-test-password", method="POST")
        with opener.open(req, timeout=8) as response:
            self.assertEqual(response.status, 200)
        with opener.open(self.public + "/api/snapshot?range=7d", timeout=8) as response:
            shared = json.load(response)
            self.assertTrue(shared["publicSession"])
            self.assertNotIn("workspace", shared)
            self.assertNotIn("dispatch", shared)
            self.assertGreater(shared["spend"]["requests"], 0)
            self.assertEqual(shared["accounts"][0]["label"], "账号 01")
            self.assertNotIn("email", shared["accounts"][0])
        with self.assertRaises(HTTPError) as error:
            opener.open(Request(self.public+"/api/workspace", data=b'{}', headers={"Origin":self.public,"Content-Type":"application/json","X-Dashboard-Action":"edit"}), timeout=8)
        self.assertEqual(error.exception.code,403)
        opener.open(Request(self.public + "/auth/logout", data=b"", method="POST"), timeout=8).close()
        with self.assertRaises(HTTPError) as error:
            opener.open(self.public + "/api/snapshot?range=7d", timeout=8)
        self.assertEqual(error.exception.code, 401)

    def test_sharing_requires_credentials_and_distinct_free_ports(self):
        manage, share = map(str, free_ports())
        for auth, m, s, message in [(self.temp/'empty-auth', manage, share, '--set-password'), (self.temp/'auth',manage,manage,'独立有效'), (self.temp/'auth',str(self.port),share,'管理端口无法启动'), (self.temp/'auth',manage,str(self.public_port),'共享端口无法启动')]:
            result = subprocess.run([sys.executable,'server.py','--demo','--no-open','--port',m,'--public-port',s],cwd=ROOT,env=dict(self.env,USAGE_AUTH_DIR=str(auth)),timeout=6,capture_output=True,text=True)
            self.assertNotEqual(result.returncode,0)
            self.assertIn(message,result.stderr)

    @unittest.skipUnless(Path(CHROME).is_file() and shutil.which("node"), "Chrome and Node.js are required")
    def test_08_browser_interactions(self):
        profile = self.temp / "chrome-profile"
        profile.mkdir()
        chrome = subprocess.Popen([CHROME, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run", "--no-default-browser-check", "--remote-debugging-port=0", f"--user-data-dir={profile}", "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            portfile = profile / "DevToolsActivePort"
            for _ in range(100):
                if portfile.exists():
                    break
                time.sleep(0.1)
            self.assertTrue(portfile.exists(), "Chrome DevTools did not start")
            port = portfile.read_text().splitlines()[0]
            script = self.temp / "browser.mjs"
            script.write_text(BROWSER_SCRIPT)
            result = subprocess.run(["node", str(script), port, (ROOT / "index.html").as_uri() + "#overview", self.base + "/#overview", self.public], cwd=ROOT, timeout=45, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        finally:
            chrome.terminate()
            try:
                chrome.wait(timeout=5)
            except subprocess.TimeoutExpired:
                chrome.kill()
                chrome.wait()


if __name__ == "__main__":
    unittest.main(verbosity=2)
