#!/usr/bin/env python3
from __future__ import annotations
import base64, json, os, socket, subprocess, tempfile, time, urllib.request
from pathlib import Path
import shutil
import websocket

ROOT=Path(__file__).resolve().parents[1]
OUT=Path(os.environ.get('BVMAC_QA_OUT', tempfile.mkdtemp(prefix='bvmac-radar-qa-')))
OUT.mkdir(parents=True, exist_ok=True)
WIDTHS=[360,390,430,768,820,1024,1366,1920]
HEIGHT=900

def free_port():
    s=socket.socket(); s.bind(('127.0.0.1',0)); port=s.getsockname()[1]; s.close(); return port

def request_json(url, retries=60):
    last=None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=1) as r: return json.load(r)
        except Exception as e:
            last=e; time.sleep(.1)
    raise RuntimeError(last)

def call(ws, method, params=None, ident=[0]):
    ident[0]+=1; i=ident[0]
    ws.send(json.dumps({'id':i,'method':method,'params':params or {}}))
    while True:
        msg=json.loads(ws.recv())
        if msg.get('id')==i:
            if 'error' in msg: raise RuntimeError(msg['error'])
            return msg.get('result',{})

def make_html():
    html=(ROOT/'tests'/'radar_responsive_fixture.html').read_text(encoding='utf-8')
    shell=(ROOT/'web'/'shell.css').read_text(encoding='utf-8')
    radar=(ROOT/'web'/'radar.css').read_text(encoding='utf-8')
    js=(ROOT/'web'/'shell.js').read_text(encoding='utf-8')
    i18n=(ROOT/'web'/'i18n.js').read_text(encoding='utf-8')
    html=html.replace('<link rel="stylesheet" href="../web/shell.css">',f'<style>{shell}</style>')
    html=html.replace('<link rel="stylesheet" href="../web/radar.css">',f'<style>{radar}</style>')
    html=html.replace('<script src="../web/shell.js"></script>',f'<script>{i18n}</script><script>{js}</script>')
    return html

JS=r'''(()=>{
 const q=s=>document.querySelector(s), all=s=>[...document.querySelectorAll(s)];
 const rect=e=>e?e.getBoundingClientRect():null;
 const root=q('.radar-wrap'), main=q('.platform-main'), table=q('.radar-table'), rows=all('.radar-table tr[data-key]');
 return {
  innerWidth, docWidth:document.documentElement.scrollWidth, bodyWidth:document.body.scrollWidth,
  mainWidth:main.clientWidth, mainScroll:main.scrollWidth, radarWidth:rect(root).width,
  tableWidth:table.clientWidth, tableScroll:table.scrollWidth,
  rowMax:Math.max(...rows.map(x=>rect(x).width)),
  theadDisplay:getComputedStyle(q('.radar-table thead')).display,
  tableDisplay:getComputedStyle(q('.radar-table table')).display,
  navLabels:all('.platform-nav .nav-txt').map(x=>x.textContent.trim()),
  navShorts:all('.platform-nav .nav-txt').map(x=>x.dataset.court),
  current:q('.platform-nav [aria-current]')?.querySelector('.nav-txt')?.textContent.trim()||'',
  currentShort:q('.platform-nav [aria-current]')?.querySelector('.nav-txt')?.dataset.court||'',
  toolbarWidth:rect(q('.radar-toolbar')).width,
  inputWidth:rect(q('.radar-toolbar input')).width,
  selectWidth:rect(q('.radar-toolbar select')).width,
  inputHeight:rect(q('.radar-toolbar input')).height,
  selectHeight:rect(q('.radar-toolbar select')).height,
  tabHeights:all('.radar-tabs button').map(x=>rect(x).height),
  rowDisplays:rows.map(x=>getComputedStyle(x).display)
 };
})()'''

def validate(w,m):
    errors=[]
    tol=2.5
    if m['docWidth']>w+1: errors.append(f"document overflow {m['docWidth']} > {w}")
    if m['mainScroll']>m['mainWidth']+1: errors.append(f"main overflow {m['mainScroll']} > {m['mainWidth']}")
    if m['tableWidth']>m['radarWidth']+tol: errors.append('table container exceeds radar')
    # Semantic route labels must not be position-shifted. Browser language can be FR or EN.
    labels=m['navLabels']
    ok_en=('Predictive radar' in labels and 'BVMAC feeds' in labels and 'Analysis' in labels and 'Portfolios' in labels and labels.count('Portfolios')==1)
    if not ok_en: errors.append(f"semantic nav labels invalid: {labels}")
    expected_shorts=['Home','Overview','Stories','Stocks','Funds','Index','Radar','Watchlist','Data','Feeds','Analysis','Portfolios']
    if m['navShorts']!=expected_shorts: errors.append(f"English short nav labels invalid: {m['navShorts']}")
    if w<=640:
        if m['theadDisplay']!='none': errors.append('mobile thead not hidden')
        if m['rowMax']>m['radarWidth']+tol: errors.append(f"mobile row overflow {m['rowMax']} > {m['radarWidth']}")
        if m['inputWidth']>m['toolbarWidth']+tol or m['selectWidth']>m['toolbarWidth']+tol: errors.append('mobile controls overflow toolbar')
        if m['inputHeight']<43 or m['selectHeight']<43 or min(m['tabHeights'])<43: errors.append('mobile touch target below 44px')
    else:
        # Wide table may scroll, but only inside its dedicated container.
        if m['tableScroll'] < m['tableWidth']-1: errors.append('invalid table scroll metrics')
    return errors

def main():
    port=free_port(); profile=tempfile.mkdtemp(prefix='bvmac-chromium-')
    chromium=os.environ.get('CHROMIUM_BIN') or shutil.which('chromium') or shutil.which('chromium-browser') or shutil.which('google-chrome') or shutil.which('google-chrome-stable')
    if not chromium: raise SystemExit('Chromium is required for responsive QA')
    cmd=[chromium,'--headless=new','--no-sandbox','--disable-gpu','--hide-scrollbars',
         '--remote-allow-origins=*',f'--remote-debugging-port={port}',f'--user-data-dir={profile}','about:blank']
    proc=subprocess.Popen(cmd,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        tabs=request_json(f'http://127.0.0.1:{port}/json')
        tab=next(x for x in tabs if x.get('type')=='page')
        ws=websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=5)
        call(ws,'Page.enable'); call(ws,'Runtime.enable')
        html=make_html(); report=[]; failed=False
        for w in WIDTHS:
            call(ws,'Emulation.setDeviceMetricsOverride',{'width':w,'height':HEIGHT,'deviceScaleFactor':1,'mobile':w<=640})
            frame=call(ws,'Page.getFrameTree')['frameTree']['frame']['id']
            call(ws,'Page.setDocumentContent',{'frameId':frame,'html':html})
            time.sleep(.25)
            call(ws,'Runtime.evaluate',{'expression':"window.BVMACI18N?.setLang('en');window.BVMACI18N?.apply(document);true",'returnByValue':True})
            time.sleep(.10)
            res=call(ws,'Runtime.evaluate',{'expression':JS,'returnByValue':True})
            m=res['result']['value']; errs=validate(w,m); failed |= bool(errs)
            png=call(ws,'Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']
            (OUT/f'radar-{w}.png').write_bytes(base64.b64decode(png))
            if w in (360,390,768):
                call(ws,'Runtime.evaluate',{'expression':"document.querySelector('.radar-table').scrollIntoView({block:'start'})"})
                time.sleep(.12)
                detail=call(ws,'Page.captureScreenshot',{'format':'png','captureBeyondViewport':False})['data']
                (OUT/f'radar-{w}-table.png').write_bytes(base64.b64decode(detail))
                call(ws,'Runtime.evaluate',{'expression':"document.querySelector('.platform-main').scrollTop=0"})
            report.append({'width':w,'height':HEIGHT,'pass':not errs,'errors':errs,'metrics':m})
            print(w,'PASS' if not errs else 'FAIL', '; '.join(errs))
        (OUT/'radar-responsive-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        ws.close()
        if failed: raise SystemExit(1)
    finally:
        proc.terminate()
        try: proc.wait(timeout=3)
        except subprocess.TimeoutExpired: proc.kill()

if __name__=='__main__': main()
