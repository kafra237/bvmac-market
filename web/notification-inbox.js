/* Push notifications are intentionally UI-free outside the account page.
   This helper only keeps an already-authorized browser subscription synchronized. */
(()=>{
  let syncing=false;
  async function json(path,options={}){
    const r=await fetch(path,{credentials:'same-origin',cache:'no-store',...options});
    if(!r.ok)return null;try{return await r.json()}catch{return null}
  }
  async function syncSubscription(){
    if(syncing||!navigator.onLine||!('serviceWorker'in navigator)||!('Notification'in window)||Notification.permission!=='granted')return;
    syncing=true;
    try{
      const me=await json('/api/v3/auth/me');if(!me||me.must_change_password)return;
      const registration=await navigator.serviceWorker.getRegistration('/'),sub=await registration?.pushManager?.getSubscription();if(!sub)return;
      const status=await json('/api/v3/push/status');if(!status?.available)return;
      const csrf=await json('/api/v3/auth/csrf');if(!csrf?.csrf_token)return;
      const keys=sub.toJSON().keys;if(!keys?.p256dh||!keys?.auth)return;
      await json('/api/v3/push/subscription',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf.csrf_token},body:JSON.stringify({endpoint:sub.endpoint,keys})});
    }catch{}finally{syncing=false}
  }
  window.addEventListener('online',syncSubscription);
  document.addEventListener('visibilitychange',()=>{if(document.visibilityState==='visible')syncSubscription()});
  syncSubscription();
})();
