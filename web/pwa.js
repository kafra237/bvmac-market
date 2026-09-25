(()=>{
  const RELEASE='__BVMAC_RELEASE__',VERSION_URL='/version.json';
  if(!window.isSecureContext)return;
  let registration=null,reloading=false,lastCheck=0;
  const safeReload=()=>{if(reloading)return;reloading=true;try{sessionStorage.setItem('bvmac_release_reload',RELEASE)}catch{};location.reload()};
  async function activateWaiting(){if(!registration)return false;if(registration.waiting){registration.waiting.postMessage({type:'SKIP_WAITING'});return true}try{await registration.update()}catch{}if(registration.waiting){registration.waiting.postMessage({type:'SKIP_WAITING'});return true}return false}
  async function checkRelease(force=false){const now=Date.now();if(!force&&now-lastCheck<60000)return;lastCheck=now;try{const r=await fetch(VERSION_URL+'?t='+now,{cache:'no-store',credentials:'same-origin'});if(!r.ok)return;const d=await r.json();if(d.version&&d.version!==RELEASE){const waiting=await activateWaiting();if(!waiting||!('serviceWorker'in navigator))safeReload();else setTimeout(safeReload,2500)}}catch{}}
  if('serviceWorker'in navigator){window.addEventListener('load',async()=>{try{registration=await navigator.serviceWorker.register('/sw.js?v='+RELEASE,{scope:'/',updateViaCache:'none'});registration.addEventListener('updatefound',()=>{const w=registration.installing;if(!w)return;w.addEventListener('statechange',()=>{if(w.state==='installed'&&navigator.serviceWorker.controller)w.postMessage({type:'SKIP_WAITING'})})});navigator.serviceWorker.addEventListener('controllerchange',safeReload);await checkRelease(true)}catch{await checkRelease(true)}})}else window.addEventListener('load',()=>checkRelease(true));
  window.addEventListener('focus',()=>checkRelease(true));window.addEventListener('online',()=>checkRelease(true));document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')checkRelease(true)});setInterval(()=>checkRelease(),60000);
})();
