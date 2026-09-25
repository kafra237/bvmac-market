(()=>{
  const nav=document.querySelector('.platform-nav');
  if(!nav)return;
  const LABELS={
    accueil:['Accueil','Accueil','Home','Home'],
    apercu:['Aperçu du marché','Aperçu','Market overview','Overview'],
    recits:['Histoires du marché','Récits','Market stories','Stories'],
    actions:['Actions','Actions','Stocks','Stocks'],
    opcvm:['OPCVM','OPCVM','Funds','Funds'],
    indice:['Indice BVMAC-AS','Indice','BVMAC-AS Index','Index'],
    radar:['Radar prédictif','Radar','Predictive radar','Radar'],
    veille:['Valeurs suivies','Suivi','Watchlist','Watchlist'],
    donnees:['Données','Données','Data','Data'],
    feeds:['Feeds BVMAC','Feeds','BVMAC feeds','Feeds'],
    analysis:['Analyse','Analyse','Analysis','Analysis'],
    portfolios:['Mes portefeuilles','Portefeuilles','Portfolios','Portfolios'],
  };
  const ROUTE_TO_KEY={home:'accueil',overview:'apercu',stories:'recits',stocks:'actions',funds:'opcvm',index:'indice',radar:'radar',watchlist:'veille',data:'donnees',feeds:'feeds',analysis:'analysis',portfolios:'portfolios',accueil:'accueil',apercu:'apercu',recits:'recits',actions:'actions',opcvm:'opcvm',indice:'indice',veille:'veille',donnees:'donnees',analyse:'analysis',portefeuilles:'portfolios'};
  function keyFromHref(href){const raw=String(href||'');if(raw.startsWith('/feeds.html'))return'feeds';if(raw.startsWith('/lab.html'))return'analysis';if(raw.startsWith('/account.html'))return'portfolios';try{const base=(location.origin&&location.origin!=='null')?location.origin:'https://bvmac.local';const u=new URL(raw,base);if(u.pathname==='/app'||u.pathname==='/app.html')return ROUTE_TO_KEY[u.searchParams.get('view')||'home']||'accueil'}catch{}return null}
  function navKey(link){const view=link.dataset.vue;if(view&&LABELS[view])return view;const target=link.dataset.nav||link.getAttribute('href')||'';return keyFromHref(target)}
  function currentKey(){const route=new URLSearchParams(location.search).get('view');if(route&&ROUTE_TO_KEY[route])return ROUTE_TO_KEY[route];const embedded=document.body?.dataset?.appView;if(embedded&&ROUTE_TO_KEY[embedded])return ROUTE_TO_KEY[embedded];return keyFromHref(location.href)||'accueil'}
  function languageIsEnglish(){if(window.BVMACI18N)return window.BVMACI18N.lang==='en';let language='';try{language=localStorage.getItem('bvmac_pref_lang')||''}catch{}return (language||((navigator.language||'').startsWith('en')?'en':'fr'))==='en'}
  function sync(){const en=languageIsEnglish(),current=currentKey();[...nav.querySelectorAll('a,button')].forEach(link=>{const key=navKey(link),span=link.querySelector('.nav-txt');if(!key||!span)return;const spec=LABELS[key],label=spec[en?2:0],short=spec[en?3:1];if(span.textContent!==label)span.textContent=label;if(span.dataset.court!==short)span.dataset.court=short;if(link.getAttribute('aria-label')!==label)link.setAttribute('aria-label',label);if(key===current){if(link.getAttribute('aria-current')!=='page')link.setAttribute('aria-current','page')}else if(link.hasAttribute('aria-current'))link.removeAttribute('aria-current')})}
  function revealCurrent(){if(getComputedStyle(nav).flexDirection!=='row')return;const current=nav.querySelector('[aria-current]');if(!current)return;const item=current.getBoundingClientRect(),bounds=nav.getBoundingClientRect();if(item.left<bounds.left||item.right>bounds.right)nav.scrollLeft+=item.left-bounds.left-(nav.clientWidth-item.width)/2}
  if('ResizeObserver'in window)new ResizeObserver(revealCurrent).observe(nav);
  new MutationObserver(()=>{sync();revealCurrent()}).observe(nav,{subtree:true,childList:true,attributes:true,attributeFilter:['href','data-nav','data-vue']});
  window.addEventListener('storage',sync);window.addEventListener('popstate',()=>{sync();revealCurrent()});window.addEventListener('bvmac:language-changed',sync);
  sync();requestAnimationFrame(revealCurrent);
})();
