#!/usr/bin/env python3
from __future__ import annotations
import json,re,socket,subprocess,tempfile,time,urllib.request,os
from pathlib import Path
import shutil
import websocket

ROOT=Path(__file__).resolve().parents[1]
WEB=ROOT/'web'
OUT=Path(os.environ.get('BVMAC_QA_OUT', tempfile.mkdtemp(prefix='bvmac-layout-qa-')))
OUT.mkdir(parents=True,exist_ok=True)
WIDTHS=[360,390,430,768,820,1024,1366,1920]
HEIGHT=1000
PAGES={
 'home':('app.html','home','Home'),
 'radar':('app.html','radar','Predictive radar'),
 'feeds':('app-feeds.html','feeds','BVMAC feeds'),
 'analysis':('app-analysis.html','analysis','Analysis'),
 'portfolios':('app-portfolios.html','portfolios','Portfolios'),
 'auth':('auth.html',None,None),
 'landing':('index.html',None,None),
 'information':('information.html',None,None),
 'demo':('demo.html',None,None),
 'stat':('../backend/admin/stat.html',None,None),
}
# High-signal phrases which must not remain visible once English is active.
FORBIDDEN=['Pilotage','Serveur & infrastructure','Comptes utilisateurs','Synthèse hebdomadaire par email','Envois ciblés','Actualiser','Accueil','Aperçu du marché','Histoires du marché','Valeurs suivies','Plateforme boursière','Mes portefeuilles','Suivi & alertes','Créer une simulation','Aujourd’hui à la BVMAC','Avis de la BVMAC','Chargement…','Déconnexion','Changer le mot de passe','Connexion','Inscription','Synthèse hebdomadaire du marché','Données','Analyse','Plateforme boursière','Période analysée','Sources utilisées','Valeurs les plus actives','Plus fortes hausses','Plus fortes baisses','Liquidité à court terme','Signaux remarquables','Données personnelles et choix','Comprendre la plateforme','Choisis ton scénario']
REQUIRED_NAV=['Home','Market overview','Market stories','Stocks','Funds','BVMAC-AS Index','Predictive radar','Watchlist','Data','BVMAC feeds','Analysis','Portfolios']
REQUIRED_SHORT=['Home','Overview','Stories','Stocks','Funds','Index','Radar','Watchlist','Data','Feeds','Analysis','Portfolios']
DYNAMIC_FR=[
 'Connexion au serveur interrompue. Réessaie dans quelques instants.','Réponse serveur indisponible. Réessaie.','Vérification humaine','Chargement du calcul…','Connexion réussie.',
 'Calcul du Radar…','Pression actuelle','Dernière VL','Risque prochaine nouvelle VL','Historique exploité','Saisonnalité des rendements observés','Historique de cotation','Pression achat / vente','Tout l’historique',
 'Données momentanément indisponibles','Réessayez dans quelques instants.','Aucune valeur suivie.','Aucune règle.','Calcul en cours…','Calcul terminé. Les résultats sont affichés ci-dessous.','Ouvrir la publication officielle ↗'
]

CSS_RE=re.compile(r'<link[^>]+href=["\'](/[^"\']+\.css)(?:\?[^"\']*)?["\'][^>]*>',re.I)
SCRIPT_RE=re.compile(r'<script[^>]+src=["\'](/[^"\']+\.js)(?:\?[^"\']*)?["\'][^>]*>\s*</script>',re.I)

def render_source(filename:str,view:str|None)->str:
    src=(WEB/filename).resolve()
    html=src.read_text(encoding='utf-8')
    def css(m):
        p=WEB/m.group(1).lstrip('/')
        return '<style>'+p.read_text(encoding='utf-8')+'</style>' if p.exists() else ''
    html=CSS_RE.sub(css,html)
    keep={'/i18n.js','/shell.js'}
    deferred=[]
    def js(m):
        src=m.group(1)
        if src not in keep:return ''
        p=WEB/src.lstrip('/')
        deferred.append('<script>'+p.read_text(encoding='utf-8')+'</script>')
        return ''
    html=SCRIPT_RE.sub(js,html)
    if view and filename=='app.html':
        html=html.replace('<body>','<body data-app-view="'+view+'">',1)
    # External production scripts are deferred. Execute the inlined QA copies after
    # the body exists so the shell is genuinely exercised instead of reading the
    # static aria-current marker from the source HTML.
    html=html.replace('</body>',''.join(deferred)+'</body>',1)
    # Force English before deferred UI code starts. about:blank localStorage is unavailable,
    # so navigator --lang plus this marker makes the intent explicit for future helpers.
    html=html.replace('</head>','<script>window.__BVMAC_QA_LANG="en";</script></head>',1)
    return html

def free_port():
    s=socket.socket();s.bind(('127.0.0.1',0));p=s.getsockname()[1];s.close();return p

def req_json(url,retries=60):
    last=None
    for _ in range(retries):
        try:
            with urllib.request.urlopen(url,timeout=1) as r:return json.load(r)
        except Exception as e:last=e;time.sleep(.1)
    raise RuntimeError(last)

def call(ws,method,params=None,c=[0]):
    c[0]+=1;i=c[0];ws.send(json.dumps({'id':i,'method':method,'params':params or {}}))
    while True:
        m=json.loads(ws.recv())
        if m.get('id')==i:
            if 'error'in m:raise RuntimeError(m['error'])
            return m.get('result',{})

def evalv(ws,expr):
    r=call(ws,'Runtime.evaluate',{'expression':expr,'returnByValue':True,'awaitPromise':True})
    return r.get('result',{}).get('value')

JS=r'''(()=>{const nav=document.querySelector('.platform-nav'),main=document.querySelector('.platform-main')||document.querySelector('main');const visible=[...document.querySelectorAll('body *')].filter(e=>{const s=getComputedStyle(e);return s.display!=='none'&&s.visibility!=='hidden'&&e.getClientRects().length>0&&e.children.length===0}).map(e=>e.textContent.trim()).filter(Boolean).join('\n');return {docW:document.documentElement.scrollWidth,bodyW:document.body.scrollWidth,mainW:main?.clientWidth||innerWidth,mainScroll:main?.scrollWidth||document.documentElement.scrollWidth,navCount:nav?.querySelectorAll('a,button').length||0,current:nav?.querySelector('[aria-current] .nav-txt')?.textContent.trim()||'',labels:nav?[...nav.querySelectorAll('.nav-txt')].map(x=>x.textContent.trim()):[],shorts:nav?[...nav.querySelectorAll('.nav-txt')].map(x=>x.dataset.court):[],hrefs:nav?[...nav.querySelectorAll('a')].map(x=>x.getAttribute('href')):[],dataNav:nav?[...nav.querySelectorAll('[data-nav]')].map(x=>x.dataset.nav):[],text:visible,lang:document.documentElement.lang}})()'''

def main():
    port=free_port();profile=tempfile.mkdtemp(prefix='bvmac390-')
    chromium=os.environ.get('CHROMIUM_BIN') or shutil.which('chromium') or shutil.which('chromium-browser') or shutil.which('google-chrome') or shutil.which('google-chrome-stable')
    if not chromium: raise SystemExit('Chromium is required for responsive QA')
    proc=subprocess.Popen([chromium,'--headless=new','--no-sandbox','--disable-gpu','--hide-scrollbars','--lang=en-US','--remote-allow-origins=*',f'--remote-debugging-port={port}',f'--user-data-dir={profile}','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    rows=[];untranslated=[];failed=False
    try:
        tab=next(x for x in req_json(f'http://127.0.0.1:{port}/json') if x.get('type')=='page')
        ws=websocket.create_connection(tab['webSocketDebuggerUrl'],timeout=8);call(ws,'Page.enable');call(ws,'Runtime.enable')
        for w in WIDTHS:
            call(ws,'Emulation.setDeviceMetricsOverride',{'width':w,'height':HEIGHT,'deviceScaleFactor':1,'mobile':w<=640})
            for name,(filename,view,expected_current) in PAGES.items():
                call(ws,'Page.setDocumentContent',{'frameId':tab['id'] if False else call(ws,'Page.getFrameTree')['frameTree']['frame']['id'],'html':render_source(filename,view)})
                time.sleep(.18)
                # i18n defaults to navigator language; explicitly reapply if available.
                evalv(ws,"window.BVMACI18N?.setLang('en'); window.BVMACI18N?.apply(document); true")
                time.sleep(.04);m=evalv(ws,JS);errs=[]
                if m['docW']>w+1 or m['bodyW']>w+1:errs.append(f"global overflow doc={m['docW']} body={m['bodyW']} width={w}")
                if m['mainScroll']>m['mainW']+1:errs.append(f"main horizontal overflow {m['mainScroll']}>{m['mainW']}")
                if name in ('home','radar','feeds','analysis','portfolios'):
                    if m['navCount']!=12:errs.append(f"nav count {m['navCount']} != 12")
                    if m['labels']!=REQUIRED_NAV:errs.append(f"nav labels {m['labels']}")
                    if m['shorts']!=REQUIRED_SHORT:errs.append(f"nav short labels {m['shorts']}")
                    if expected_current and m['current']!=expected_current:errs.append(f"current={m['current']} expected={expected_current}")
                    for h in m['hrefs']+m['dataNav']:
                        if h and not h.startswith('/app?view='):errs.append(f"non canonical private nav target {h}")
                bad=[x for x in FORBIDDEN if x in m['text']]
                if bad:untranslated.append({'page':name,'width':w,'phrases':bad});errs.append('French UI: '+','.join(bad))
                if m['lang']!='en':errs.append('html lang is not en')
                rows.append({'width':w,'page':name,'pass':not errs,'errors':errs,'current':m['current']});failed|=bool(errs)
                print(w,name,'PASS' if not errs else 'FAIL','; '.join(errs))
        # Representative strings injected by runtime modules must also translate.
        i18n=(WEB/'i18n.js').read_text(encoding='utf-8')
        fid=call(ws,'Page.getFrameTree')['frameTree']['frame']['id']
        fixture='<!doctype html><html lang="fr"><body>'+''.join('<p>'+x+'</p>' for x in DYNAMIC_FR)+'<script>'+i18n+'</script></body></html>'
        call(ws,'Page.setDocumentContent',{'frameId':fid,'html':fixture});time.sleep(.12)
        evalv(ws,"window.BVMACI18N?.setLang('en');window.BVMACI18N?.apply(document);true");time.sleep(.03)
        dyn=evalv(ws,"document.body.innerText") or ''
        dyn_bad=[x for x in DYNAMIC_FR if x in dyn]
        if dyn_bad: untranslated.append({'page':'dynamic-fixtures','width':None,'phrases':dyn_bad});failed=True;print('dynamic i18n FAIL',dyn_bad)
        else: print('dynamic i18n PASS')

        standalone=[]
        # Emulate installed PWA standalone display for representative phone/tablet widths.
        call(ws,'Emulation.setEmulatedMedia',{'media':'screen','features':[{'name':'display-mode','value':'standalone'}]})
        for w in (390,768):
            call(ws,'Emulation.setDeviceMetricsOverride',{'width':w,'height':HEIGHT,'deviceScaleFactor':1,'mobile':w<=640})
            for name,(filename,view,expected_current) in {k:PAGES[k] for k in ('home','radar','feeds','analysis','portfolios')}.items():
                fid=call(ws,'Page.getFrameTree')['frameTree']['frame']['id'];call(ws,'Page.setDocumentContent',{'frameId':fid,'html':render_source(filename,view)});time.sleep(.16)
                evalv(ws,"window.BVMACI18N?.setLang('en');window.BVMACI18N?.apply(document);true");m=evalv(ws,JS)
                errs=[]
                if m['docW']>w+1 or m['bodyW']>w+1 or m['mainScroll']>m['mainW']+1:errs.append('standalone horizontal overflow')
                if m['labels']!=REQUIRED_NAV or m['shorts']!=REQUIRED_SHORT:errs.append('standalone nav mismatch')
                standalone.append({'width':w,'page':name,'pass':not errs,'errors':errs});failed|=bool(errs)
                print('standalone',w,name,'PASS' if not errs else 'FAIL','; '.join(errs))
        call(ws,'Emulation.setEmulatedMedia',{'media':'screen','features':[]})
        (OUT/'layout-report.json').write_text(json.dumps({'pass':not failed,'widths':WIDTHS,'rows':rows,'standalone':standalone,'standalone_pass':all(x['pass'] for x in standalone)},ensure_ascii=False,indent=2),encoding='utf-8')
        (OUT/'i18n-report.json').write_text(json.dumps({'pass':not untranslated,'untranslated':untranslated},ensure_ascii=False,indent=2),encoding='utf-8')
        ws.close()
        if failed:raise SystemExit(1)
    finally:
        proc.terminate()
        try:proc.wait(timeout=3)
        except subprocess.TimeoutExpired:proc.kill()

if __name__=='__main__':main()
