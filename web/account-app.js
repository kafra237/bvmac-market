const $=s=>document.querySelector(s);const pref=(k,d='')=>{try{return localStorage.getItem(k)??d}catch{return d}},setPref=(k,v)=>{try{localStorage.setItem(k,String(v))}catch{}};const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));let CSRF='',ME=null,COMPANIES=[],CURRENT_PORTFOLIO=null,P_RANGE=pref('bvmac_portfolio_range','1y'),P_START='',P_END='',pChart=null,btChart=null,portfolioRequest=0,NOTIF_BEFORE=null,NOTIF_TOP=null;const fmt=(v,d=2)=>v==null?'—':Number(v).toLocaleString('fr-FR',{maximumFractionDigits:d});const pct=v=>v==null?'—':`${fmt(v)} %`;
async function req(url,opt={}){let r;try{r=await fetch(url,{credentials:'same-origin',cache:'no-store',...opt})}catch{throw new Error('Connexion au serveur interrompue. Réessaie dans quelques instants.')}let data=null;try{data=await r.json()}catch{}if(r.status===401){location.replace('/auth.html?mode=login');throw new Error('Connexion requise.')}if(!r.ok)throw new Error(typeof data?.detail==='string'?data.detail:data?.message||'Vérifie les champs saisis puis réessaie.');if(data==null)throw new Error('Réponse serveur invalide.');return data}
function jsonPost(url,obj,csrf=true){return req(url,{method:'POST',headers:{'Content-Type':'application/json',...(csrf&&CSRF?{'X-CSRF-Token':CSRF}:{})},body:JSON.stringify(obj)})}function del(url){return req(url,{method:'DELETE',headers:{'X-CSRF-Token':CSRF}})}
function formObj(f){return Object.fromEntries(new FormData(f).entries())}function msg(id,t,ok=false){const e=$(id);if(!e)return;e.textContent=t;e.className=ok?'ok':'error'}
function vapidBytes(s){const pad='='.repeat((4-s.length%4)%4),b=(s+pad).replace(/-/g,'+').replace(/_/g,'/'),raw=atob(b);return Uint8Array.from([...raw].map(c=>c.charCodeAt(0)))}
async function pushRegistration(){if(!('serviceWorker'in navigator)||!('PushManager'in window))throw new Error('Les notifications push ne sont pas prises en charge par ce navigateur.');return navigator.serviceWorker.register('/sw.js',{scope:'/'})}
async function loadPushStatus(){const box=$('#pushStatus');if(!box)return;try{const d=await req('/api/v3/push/status');if(!d.available){box.textContent='État : serveur push non configuré.';return}if(!('Notification'in window)||!('serviceWorker'in navigator)||!('PushManager'in window)){box.textContent='État : notifications non prises en charge sur cet appareil.';return}const reg=await pushRegistration(),sub=await reg.pushManager.getSubscription();box.textContent=sub&&Notification.permission==='granted'?`État : actives sur cet appareil · ${d.subscription_count||1} appareil(s) lié(s) au compte.`:`État : non activées sur cet appareil${Notification.permission==='denied'?' (permission refusée dans le navigateur)':''}.`}catch(x){box.textContent='État : '+x.message}}
async function enablePush(){try{const d=await req('/api/v3/push/status');if(!d.available||!d.public_key)throw new Error('Le serveur push n’est pas encore configuré.');if(!('Notification'in window))throw new Error('Les notifications ne sont pas prises en charge sur cet appareil.');const permission=await Notification.requestPermission();if(permission!=='granted')throw new Error('Permission de notification non accordée.');const reg=await pushRegistration();let sub=await reg.pushManager.getSubscription();if(!sub)sub=await reg.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:vapidBytes(d.public_key)});const j=sub.toJSON();await jsonPost('/api/v3/push/subscription',{endpoint:sub.endpoint,keys:{p256dh:j.keys.p256dh,auth:j.keys.auth}});await loadPushStatus();msg('#memberMsg','Notifications push activées sur cet appareil.',true)}catch(x){msg('#memberMsg',x.message)}}
async function disablePush(){try{if(!('serviceWorker'in navigator))throw new Error('Aucun service worker sur cet appareil.');const reg=await navigator.serviceWorker.getRegistration('/'),sub=reg?await reg.pushManager.getSubscription():null;if(sub){await jsonPost('/api/v3/push/unsubscribe',{endpoint:sub.endpoint});await sub.unsubscribe()}await loadPushStatus();msg('#memberMsg','Notifications désactivées sur cet appareil.',true)}catch(x){msg('#memberMsg',x.message)}}

async function loadEmailPreferences(){const box=$('#weeklyEmail'),status=$('#weeklyEmailStatus');if(!box)return;try{const d=await req('/api/v3/email/preferences');box.checked=!!d.weekly_market_summary;status.textContent=box.checked?'Activée · prochain envoi le lundi à 09:00 (heure de Douala).':'Désactivée · aucun bulletin hebdomadaire ne sera envoyé.'}catch(x){status.textContent='Préférence indisponible : '+x.message}}
async function saveEmailPreference(){const box=$('#weeklyEmail'),status=$('#weeklyEmailStatus');if(!box)return;box.disabled=true;try{const d=await jsonPost('/api/v3/email/preferences',{weekly_market_summary:box.checked});status.textContent=d.weekly_market_summary?'Activée · prochain envoi le lundi à 09:00 (heure de Douala).':'Désactivée · aucun bulletin hebdomadaire ne sera envoyé.';msg('#memberMsg','Préférence email enregistrée.',true)}catch(x){box.checked=!box.checked;status.textContent=x.message;msg('#memberMsg',x.message)}finally{box.disabled=false}}
async function initMember(){try{ME=await req('/api/v3/auth/me');CSRF=(await req('/api/v3/auth/csrf')).csrf_token;$('#who').textContent=ME.pseudo;$('#profile').textContent=`${ME.email} · ${ME.country}${ME.phone?' · '+ME.phone:''}`;if(ME.must_change_password){msg('#memberMsg','Mot de passe temporaire détecté : change-le maintenant dans « Mot de passe ».');document.querySelectorAll('[data-page]').forEach(x=>x.classList.toggle('on',x.dataset.page==='security'));document.querySelectorAll('.page').forEach(x=>x.classList.add('hidden'));$('#page-security').classList.remove('hidden');return}await loadMember();await loadPushStatus();await loadEmailPreferences();const requested=(location.hash||'').replace(/^#/,'');if(requested==='optimize'){location.replace('/app?view=analysis#portfolio-analysis');return}const tab=['portfolios','tracking','notifications','feedback','security'].includes(requested)?requested:'portfolios';const btn=[...document.querySelectorAll('[data-page]')].find(x=>x.dataset.page===tab);if(btn)btn.click()}catch(x){msg('#memberMsg',x.message)}}
$('#logout').onclick=async()=>{try{await jsonPost('/api/v3/auth/logout',{});location.replace('/')}catch(x){msg('#memberMsg',x.message)}};
document.querySelectorAll('[data-page]').forEach(b=>b.onclick=()=>{setPref('bvmac_account_tab',b.dataset.page);history.replaceState(null,'','#'+b.dataset.page);document.querySelectorAll('[data-page]').forEach(x=>x.classList.toggle('on',x===b));document.querySelectorAll('.page').forEach(x=>x.classList.add('hidden'));$('#page-'+b.dataset.page).classList.remove('hidden');if(b.dataset.page==='feedback')loadFeedback();if(b.dataset.page==='tracking')loadTracking();if(b.dataset.page==='notifications'){loadPushStatus();loadEmailPreferences();loadNotifications(false)}});
let previewTimer=null;
function optionsHtml(){return `<option value="">Choisir une valeur…</option>`+COMPANIES.map(c=>`<option value="${c.company_id}">${c.ticker||c.short_name||c.company_id}</option>`).join('')}
function addPos(value=0){const d=document.createElement('div');d.className='pos';d.innerHTML=`<label>Valeur<select class="pcid" required>${optionsHtml()}</select></label><label>Mode<select class="pmode"><option value="percent">Pourcentage</option><option value="amount">Montant XAF</option><option value="quantity">Quantité</option></select></label><label>Valeur<input class="pvalue" type="number" min="0.000001" step="any" value="${value||''}" placeholder="ex. 50"></label><button type="button" class="btn remove-pos" aria-label="Retirer">×</button>`;d.querySelector('.remove-pos').onclick=()=>{d.remove();schedulePreview()};d.querySelectorAll('input,select').forEach(x=>x.addEventListener('input',schedulePreview));$('#positions').appendChild(d);schedulePreview()}
$('#addPos').onclick=()=>addPos(0);
$('#equalize').onclick=()=>{const rows=[...document.querySelectorAll('.pos')];if(!rows.length)return;const base=Math.floor((100/rows.length)*10000)/10000;let used=0;rows.forEach((r,i)=>{r.querySelector('.pmode').value='percent';const v=i===rows.length-1?100-used:base;used+=v;r.querySelector('.pvalue').value=v.toFixed(4).replace(/0+$/,'').replace(/\.$/,'')});schedulePreview()};
function portfolioPayload(){const f=$('#portfolioForm'),o=formObj(f);o.total_amount=+o.total_amount;o.positions=[...document.querySelectorAll('.pos')].map(x=>({company_id:+x.querySelector('.pcid').value,mode:x.querySelector('.pmode').value,value:+x.querySelector('.pvalue').value}));return o}
function localPlanned(o){let total=0,known=true;for(const p of o.positions){if(!p.company_id||!Number.isFinite(p.value)||p.value<=0){known=false;continue}if(p.mode==='percent')total+=o.total_amount*p.value/100;else if(p.mode==='amount')total+=p.value;else known=false}return {total,known}}
function renderPreview(d){const budget=Number(d.amount||0),pctFill=budget?Math.min(120,Math.max(0,d.planned_total/budget*100)):0;$('#aBudget').textContent=`${fmt(budget,0)} XAF`;$('#aPlanned').textContent=`${fmt(d.planned_total,0)} XAF`;$('#aInvested').textContent=`${fmt(d.invested,0)} XAF`;$('#aCash').textContent=`${fmt(d.rounding_cash,0)} XAF`;$('#allocationFill').style.width=`${Math.min(100,pctFill)}%`;$('#allocationFill').classList.toggle('bad',!d.balanced);const st=$('#allocationState');st.textContent=d.balanced?'100 % affecté':`${fmt(Math.abs(d.planning_gap),0)} XAF ${d.planning_gap>0?'à affecter':'en trop'}`;st.className='preview-state '+(d.balanced?'ok':'bad');$('#createPortfolio').disabled=creating||!d.balanced;$('#allocationPreview').innerHTML=d.rows?.length?`<div class="scroll"><table><thead><tr><th>Valeur</th><th>Cible</th><th>Cours</th><th>Lot</th><th>Qté</th><th>Investi</th><th>Reliquat lot</th></tr></thead><tbody>${d.rows.map(x=>`<tr><td><b>${esc(x.ticker||x.company_id)}</b></td><td>${fmt(x.target_amount,0)}</td><td>${fmt(x.price)}<br><small>${x.price_date}</small></td><td>${fmt(x.lot_size,0)}</td><td>${fmt(x.quantity,0)}</td><td>${fmt(x.invested_amount,0)}</td><td>${fmt(x.rounding_cash,0)}</td></tr>`).join('')}</tbody></table></div>`:''}
async function previewAllocation(){const sequence=++previewSeq;const o=portfolioPayload();if(!o.start_date||!o.total_amount||!o.positions.length||o.positions.some(p=>!p.company_id||!p.value)){const loc=localPlanned(o);$('#aBudget').textContent=o.total_amount?`${fmt(o.total_amount,0)} XAF`:'—';$('#aPlanned').textContent=loc.known?`${fmt(loc.total,0)} XAF`:'—';$('#aInvested').textContent='—';$('#aCash').textContent='—';$('#allocationState').textContent='Complète les lignes';$('#allocationState').className='preview-state bad';$('#allocationFill').style.width='0';$('#createPortfolio').disabled=true;$('#allocationPreview').innerHTML='';return}try{const d=await jsonPost('/api/v3/portfolio/preview',o);if(sequence!==previewSeq||creating)return;renderPreview(d)}catch(x){if(sequence!==previewSeq||creating)return;$('#allocationState').textContent=x.message;$('#allocationState').className='preview-state bad';$('#createPortfolio').disabled=true;$('#allocationPreview').innerHTML=''}}
function schedulePreview(){++previewSeq;$('#createPortfolio').disabled=true;clearTimeout(previewTimer);previewTimer=setTimeout(previewAllocation,280)}
$('#portfolioForm').addEventListener('input',schedulePreview);$('#portfolioForm').addEventListener('change',schedulePreview);
function isoDate(d){return d.toISOString().slice(0,10)}
function setDefaultDates(){const today=new Date(),y1=new Date(today),y2=new Date(today);y1.setFullYear(today.getFullYear()-1);y2.setFullYear(today.getFullYear()-2);const pf=$('#portfolioForm');if(pf&&!pf.start_date.value)pf.start_date.value=isoDate(today);const of=$('#optForm');if(of){if(!of.start_date.value)of.start_date.value=isoDate(y1);if(!of.end_date.value)of.end_date.value=isoDate(today)}const bf=$('#btForm');if(bf){if(!bf.start_date.value)bf.start_date.value=isoDate(y2);if(!bf.end_date.value)bf.end_date.value=isoDate(today)}}
let listSequence=0,portfolioItems=[],SHOW_ARCHIVED=false,portfolioData=null,creating=false,previewSeq=0,manageAction='';
function notice(text,ok=true){msg('#portfolioNotice',text,ok)}
function showComposer(open=true){
  $('#portfolioComposer').classList.toggle('hidden',!open);
  $('#newPortfolio').setAttribute('aria-expanded',String(open));
  if(open){$('#portfolioComposer').scrollIntoView({block:'start',behavior:'smooth'});$('#portfolioForm').elements.namedItem('name').focus({preventScroll:true})}
}
$('#newPortfolio').onclick=()=>showComposer();
$('#closeComposer').onclick=()=>{showComposer(false);$('#newPortfolio').focus()};
$('#portfolioScope').onchange=async()=>{SHOW_ARCHIVED=$('#portfolioScope').value==='archived';CURRENT_PORTFOLIO=null;await loadPortfolios()};
$('#portfolioAlerts').onclick=e=>{e.preventDefault();document.querySelector('[data-page="tracking"]').click()};
async function loadMember(){
  COMPANIES=(await req('/api/v3/market/companies')).companies||[];
  const opts=COMPANIES.map(c=>'<option value="'+c.company_id+'">'+esc(c.ticker||c.short_name)+'</option>').join('');
  $('#watchCompany').innerHTML=opts;$('#alertCompany').innerHTML=opts;
  if(!$('#positions').children.length)addPos(100);
  setDefaultDates();$('#portfolioForm').elements.start_date.max=isoDate(new Date());
  await loadPortfolios();
  const params=new URLSearchParams(location.search),cid=params.get('company');
  if(cid&&COMPANIES.some(c=>String(c.company_id)===cid)){$('.pcid').value=cid;showComposer()}
  else if(params.get('new')==='1')showComposer();
  schedulePreview();
}
$('#portfolioForm').onsubmit=async e=>{
  e.preventDefault();if(creating)return;creating=true;clearTimeout(previewTimer);++previewSeq;
  const button=$('#createPortfolio');button.disabled=true;button.textContent='Création en cours…';
  let created=false;msg('#portfolioFormError','');
  try{
    const o=portfolioPayload(),pre=await jsonPost('/api/v3/portfolio/preview',o);
    if(!pre.balanced)throw new Error('Affecte 100 % du budget avant l’arrondi aux lots.');
    const d=await jsonPost('/api/v3/portfolio/create',o);created=true;
    SHOW_ARCHIVED=false;$('#portfolioScope').value='active';showComposer(false);
    notice('« '+o.name+' » a été créé.');await loadPortfolios(d.portfolio_id,true);
  }catch(x){if(created)notice('Portefeuille enregistré. Son affichage a échoué : '+x.message,false);else msg('#portfolioFormError',x.message)}
  finally{creating=false;button.textContent='Créer et suivre';schedulePreview()}
};
async function loadPortfolios(selectedId=null,reveal=false){
  const sequence=++listSequence,list=$('#portfolioList');list.setAttribute('aria-busy','true');
  try{
    const data=await req('/api/v3/portfolio/list?archived='+SHOW_ARCHIVED);if(sequence!==listSequence)return;portfolioItems=data.portfolios||[];
    list.innerHTML=portfolioItems.map(p=>'<button type="button" class="portfolio-entry openP" data-id="'+p.portfolio_id+'" aria-pressed="false"><span><b>'+esc(p.name)+'</b><small>Créé pour le '+esc(p.start_date)+'</small></span><span class="portfolio-entry-value"><b>'+fmt(p.initial_amount,0)+' XAF</b><small>Capital initial</small></span><span class="portfolio-entry-open">Ouvrir →</span></button>').join('')||
      '<div class="portfolio-empty"><b>'+ (SHOW_ARCHIVED?'Aucune simulation archivée.':'Ton premier portefeuille commence ici.')+'</b><span>'+(SHOW_ARCHIVED?'Les portefeuilles archivés restent récupérables.':'Utilise « Nouveau portefeuille » pour choisir tes valeurs et ton capital.')+'</span></div>';
    const name=$('#portfolioForm').elements.namedItem('name');
    if(!name.value||/^Mon portefeuille(?: \d+)?$/.test(name.value)){const used=new Set(portfolioItems.map(p=>p.name));let n=1,c='Mon portefeuille';while(used.has(c))c='Mon portefeuille '+(++n);name.value=c}
    document.querySelectorAll('.openP').forEach(b=>b.onclick=()=>openPortfolio(+b.dataset.id,{reveal:true}));
    const target=selectedId||(portfolioItems.some(p=>+p.portfolio_id===CURRENT_PORTFOLIO)?CURRENT_PORTFOLIO:portfolioItems[0]?.portfolio_id);
    if(target)await openPortfolio(+target,{reveal});
    else{++portfolioRequest;CURRENT_PORTFOLIO=null;portfolioData=null;$('#portfolioDetail').classList.add('hidden');pChart?.destroy();pChart=null;}
  }catch(x){notice('Impossible de charger les portefeuilles : '+x.message,false)}
  finally{if(sequence===listSequence)list.removeAttribute('aria-busy')}
}
function money(value){return value==null?'—':fmt(value,0)+' XAF'}
async function openPortfolio(id,{reveal=false}={}){
  const sequence=++portfolioRequest,detail=$('#portfolioDetail');CURRENT_PORTFOLIO=id;portfolioData=null;
  detail.classList.remove('hidden');detail.setAttribute('aria-busy','true');$('#portfolioContent').classList.add('hidden');$('#portfolioManage').classList.add('hidden');
  $('#pTitle').textContent=portfolioItems.find(p=>+p.portfolio_id===id)?.name||'Portefeuille';
  $('#portfolioDetailStatus').textContent='Chargement de la valorisation…';$('#retryPortfolio').classList.add('hidden');
  for(const id of ['renamePortfolio','duplicatePortfolio','archivePortfolio'])$('#'+id).disabled=true;
  document.querySelectorAll('.openP').forEach(b=>{const active=+b.dataset.id===id;b.classList.toggle('active',active);b.setAttribute('aria-pressed',String(active))});
  if(reveal){detail.focus({preventScroll:true});detail.scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'})}
  try{
    const d=await req('/api/v3/portfolio/'+id+'?'+new URLSearchParams({range:P_RANGE,...(P_START?{start:P_START}:{}),...(P_END?{end:P_END}:{})}));
    if(sequence!==portfolioRequest)return;portfolioData=d;
    $('#pTitle').textContent=d.name;$('#pInitial').textContent=fmt(d.initial_amount,0);$('#pValue').textContent=fmt(d.latest?.value,0);
    $('#pGain').textContent=money(d.latest?.gain);$('#pPerf').textContent=pct(d.latest?.performance_pct);$('#pCash').textContent=fmt(d.cash,0);
    $('#pAsOf').textContent='Valorisation au '+(d.as_of||d.latest?.date||'—')+' · cours le plus ancien utilisé : '+(d.oldest_price_date||'date non disponible')+(d.archived?' · Archivé':'');
    $('#pBasis').textContent=d.basis+(d.has_new_quotes?'':' Les cours de départ sont conservés : aucune nouvelle cotation disponible.');
    $('#pPeriod').textContent=d.period?'Sur la période du '+d.period.start+' au '+d.period.end+' : '+money(d.period.gain)+' ('+pct(d.period.performance_pct)+').':'Pas de variation calculable sur cette sélection.';
    $('#pPositions').innerHTML='<h3>Mes positions</h3><table><caption>Valorisation actuelle, indépendante de la période du graphique</caption><thead><tr><th>Valeur</th><th>Quantité</th><th>Cours de départ</th><th>Dernier cours connu</th><th>Valeur estimée</th><th>Gain / perte</th><th>Poids</th></tr></thead><tbody>'+(d.positions||[]).map(x=>'<tr><td><a href="/app?view=stocks&company='+x.company_id+'">'+esc(x.ticker||x.name||x.company_id)+'</a></td><td>'+fmt(x.quantity,0)+'</td><td>'+money(x.start_price)+'</td><td>'+money(x.last_price)+'<br><small>'+esc(x.price_date||'Date non disponible')+'</small></td><td>'+money(x.value)+'</td><td>'+money(x.gain)+'<br>'+pct(x.performance_pct)+'</td><td>'+pct(x.weight_pct)+'</td></tr>').join('')+'</tbody></table>';
    $('#portfolioNews').href='/app?view=feeds&portfolio='+id;
    $('#archivePortfolio').textContent=d.archived?'Restaurer':'Archiver';
    const canvas=$('#portfolioChart'),curve=d.curve||[];let empty=$('#portfolioChartEmpty');
    if(!empty){empty=document.createElement('p');empty.id='portfolioChartEmpty';empty.className='chart-empty';canvas.parentElement.appendChild(empty)}
    pChart?.destroy();pChart=null;canvas.hidden=!curve.length;empty.hidden=!!curve.length;empty.textContent='Aucune valorisation sur cette période. Le récapitulatif du portefeuille reste disponible.';
    if(curve.length){pChart=new Chart(canvas,{type:'line',data:{labels:curve.map(x=>x.date),datasets:[BVMACCharts.smoothDataset({label:'Valeur estimée (XAF)',data:curve.map(x=>x.value)})]},options:BVMACCharts.lineOptions()});BVMACCharts.bindReset(canvas,pChart)}
    $('#portfolioContent').classList.remove('hidden');$('#portfolioDetailStatus').textContent='';
    for(const id of ['renamePortfolio','duplicatePortfolio','archivePortfolio'])$('#'+id).disabled=false;
  }catch(x){if(sequence===portfolioRequest){$('#portfolioDetailStatus').textContent=x.message;$('#retryPortfolio').classList.remove('hidden')}}
  finally{if(sequence===portfolioRequest)detail.removeAttribute('aria-busy')}
}
$('#retryPortfolio').onclick=()=>openPortfolio(CURRENT_PORTFOLIO);
function manage(action){
  if(!portfolioData)return;manageAction=action;$('#portfolioManage').classList.remove('hidden');
  $('#portfolioNameLabel').classList.toggle('hidden',action==='archive');
  $('#portfolioNewName').required=action!=='archive';
  $('#portfolioNewName').value=portfolioData.name+(action==='duplicate'?' — copie':'');
  $('#portfolioManageHelp').textContent=action==='archive'?(portfolioData.archived?'Restaurer cette simulation dans la liste en cours ?':'Cette simulation sera conservée dans Archives. Tu pourras la restaurer.') :action==='duplicate'?'La copie conserve la date, les positions et le capital de départ.':'Choisis un nom pour retrouver facilement cette simulation.';
  $('#portfolioSave').textContent=action==='archive'?(portfolioData.archived?'Restaurer':'Confirmer l’archivage'):action==='duplicate'?'Créer la copie':'Enregistrer le nom';
  msg('#portfolioManageError','');(action==='archive'?$('#portfolioSave'):$('#portfolioNewName')).focus();
}
$('#renamePortfolio').onclick=()=>manage('rename');$('#duplicatePortfolio').onclick=()=>manage('duplicate');$('#archivePortfolio').onclick=()=>manage('archive');
$('#portfolioCancel').onclick=()=>$('#portfolioManage').classList.add('hidden');
$('#portfolioManage').onsubmit=async e=>{
  e.preventDefault();const id=CURRENT_PORTFOLIO,action=manageAction,button=$('#portfolioSave');if(button.disabled)return;button.disabled=true;
  try{
    const d=await jsonPost('/api/v3/portfolio/'+id+'/'+action,action==='archive'?{archived:!portfolioData.archived}:{name:$('#portfolioNewName').value});
    if(action==='duplicate'){SHOW_ARCHIVED=false;$('#portfolioScope').value='active'}
    CURRENT_PORTFOLIO=action==='archive'?null:d.portfolio_id;
    notice(action==='duplicate'?'Copie créée.':action==='archive'?'Liste mise à jour. La simulation reste conservée.':'Nom enregistré.');
    await loadPortfolios(CURRENT_PORTFOLIO,action!=='archive');
  }catch(x){msg('#portfolioManageError',x.message)}finally{button.disabled=false}
};
$('#pCustom').onclick=()=>{if(!CURRENT_PORTFOLIO)return;if($('#pStart').value&&$('#pEnd').value&&$('#pStart').value>$('#pEnd').value){notice('La date de début doit précéder la date de fin.',false);return}P_RANGE='all';P_START=$('#pStart').value;P_END=$('#pEnd').value;document.querySelectorAll('#pRanges button').forEach(x=>x.classList.remove('on'));openPortfolio(CURRENT_PORTFOLIO)};
$('#pRanges').onclick=e=>{const b=e.target.closest('button[data-r]');if(!b||!CURRENT_PORTFOLIO)return;P_RANGE=b.dataset.r;setPref('bvmac_portfolio_range',P_RANGE);P_START=P_END='';$('#pStart').value=$('#pEnd').value='';document.querySelectorAll('#pRanges button').forEach(x=>x.classList.toggle('on',x===b));openPortfolio(CURRENT_PORTFOLIO)};

async function loadTracking(){try{const [w,a,sc]=await Promise.all([req('/api/v3/user-tools/watchlist'),req('/api/v3/user-tools/alerts'),req('/api/v3/user-tools/screens')]);const cmap=Object.fromEntries(COMPANIES.map(c=>[c.company_id,c.ticker||c.short_name||c.company_id]));$('#watchList').innerHTML=(w.items||[]).map(x=>`<div style="display:flex;gap:8px;align-items:center;padding:7px 0;border-bottom:1px solid #eee"><b>${esc(cmap[x.company_id]||x.company_id)}</b><span class="spacer"></span><button class="btn delWatch" data-id="${x.company_id}">Retirer</button></div>`).join('')||'<span class="muted">Aucune valeur suivie.</span>';document.querySelectorAll('.delWatch').forEach(b=>b.onclick=async()=>{await del('/api/v3/user-tools/watchlist/'+b.dataset.id);loadTracking()});$('#alertRules').innerHTML=(a.rules||[]).map(x=>`<div style="padding:7px 0;border-bottom:1px solid #eee"><b>${x.kind}</b> ${x.company_id?'· '+(cmap[x.company_id]||x.company_id):''} ${x.threshold!=null?'· '+x.threshold:''} <button class="btn delAlert" data-id="${x.rule_id}">×</button></div>`).join('')||'<p class="muted">Aucune règle.</p>';document.querySelectorAll('.delAlert').forEach(b=>b.onclick=async()=>{await del('/api/v3/user-tools/alerts/'+b.dataset.id);loadTracking()});$('#screenList').innerHTML=(sc.screens||[]).map(x=>`<div style="padding:7px 0;border-bottom:1px solid #eee"><b>${esc(x.name)}</b><br><span class="muted">${esc(JSON.stringify(x.filters))}</span></div>`).join('')||'<span class="muted">Aucun screener enregistré.</span>'}catch(x){msg('#memberMsg',x.message)}}
$('#watchForm').onsubmit=async e=>{e.preventDefault();try{await jsonPost('/api/v3/user-tools/watchlist',{company_id:+e.target.company_id.value});loadTracking()}catch(x){msg('#memberMsg',x.message)}};
$('#alertForm').onsubmit=async e=>{e.preventDefault();try{const o=formObj(e.target);o.company_id=+o.company_id;o.threshold=o.threshold===''?null:+o.threshold;if(['new_import','quality_error'].includes(o.kind)){o.company_id=null;o.threshold=null}await jsonPost('/api/v3/user-tools/alerts',o);loadTracking()}catch(x){msg('#memberMsg',x.message)}};
$('#screenForm').onsubmit=async e=>{e.preventDefault();try{const o=formObj(e.target),filters={min_score:+o.min_score};if(o.max_volatility!=='')filters.max_volatility=+o.max_volatility;if(o.min_momentum_6m!=='')filters.min_momentum_6m=+o.min_momentum_6m;await jsonPost('/api/v3/user-tools/screens',{name:o.name,filters});loadTracking()}catch(x){msg('#memberMsg',x.message)}};


function notificationURL(value){try{const u=new URL(value||'/app',location.origin);return u.origin===location.origin&&u.pathname.startsWith('/')?u.pathname+u.search+u.hash:'/app'}catch{return '/app'}}
async function loadNotifications(append=false){
  const list=$('#notificationHistory'),status=$('#notificationStatus'),more=$('#notificationsMore'),read=$('#notificationsReadAll');if(!list||!status)return;
  try{
    const suffix=append&&NOTIF_BEFORE?'?before='+encodeURIComponent(NOTIF_BEFORE):'';const d=await req('/api/v3/push/inbox'+suffix);
    if(!append){list.innerHTML='';NOTIF_TOP=d.items?.[0]?.notification_id||null}
    const html=(d.items||[]).map(x=>`<article class="notification-item ${x.read?'':'unread'}"><h3>${esc(x.title)}</h3><p>${esc(x.body)}</p><small>${esc(new Date(x.created_at).toLocaleString('fr-FR'))}${x.read?'':' · Non lu'}</small><br><a href="${esc(notificationURL(x.url))}">Consulter →</a></article>`).join('');
    if(html)list.insertAdjacentHTML('beforeend',html);else if(!append)list.innerHTML='<p class="muted">Aucune notification enregistrée.</p>';
    NOTIF_BEFORE=d.next_before||null;status.textContent=`${d.unread||0} notification(s) non lue(s).`;if(more){more.classList.toggle('hidden',!NOTIF_BEFORE);more.disabled=false}if(read)read.disabled=!NOTIF_TOP||!(d.unread||0);
  }catch(x){status.textContent='Historique indisponible : '+x.message;if(more)more.disabled=false}
}
async function markNotificationsRead(){if(!NOTIF_TOP)return;const b=$('#notificationsReadAll');if(b)b.disabled=true;try{await jsonPost('/api/v3/push/inbox/read',{through_id:NOTIF_TOP});NOTIF_BEFORE=null;await loadNotifications(false)}catch(x){msg('#memberMsg',x.message)}finally{if(b)b.disabled=false}}
if($('#notificationsMore'))$('#notificationsMore').onclick=()=>{const b=$('#notificationsMore');b.disabled=true;loadNotifications(true)};
if($('#notificationsReadAll'))$('#notificationsReadAll').onclick=markNotificationsRead;

if($('#pushEnable'))$('#pushEnable').onclick=enablePush;if($('#pushDisable'))$('#pushDisable').onclick=disablePush;if($('#weeklyEmail'))$('#weeklyEmail').onchange=saveEmailPreference;

$('#feedbackForm').onsubmit=async e=>{e.preventDefault();try{const o=formObj(e.target);if(!o.page)delete o.page;if(!o.subject)delete o.subject;const d=await jsonPost('/api/v3/feedback',o,false);msg('#memberMsg',d.message,true);e.target.reset();loadFeedback()}catch(x){msg('#memberMsg',x.message)}};
async function loadFeedback(){try{const d=await req('/api/v3/feedback/mine');$('#myFeedback').innerHTML=(d.reports||[]).map(x=>`<div style="padding:8px 0;border-bottom:1px solid #eee"><b>${esc(x.subject)}</b><br><span class="muted">${x.category} · ${x.status} · ${new Date(x.created_at).toLocaleString('fr-FR')}</span></div>`).join('')||'<span class="muted">Aucune remontée.</span>'}catch{}}
$('#passwordForm').onsubmit=async e=>{e.preventDefault();try{await jsonPost('/api/v3/auth/change-password',formObj(e.target));msg('#memberMsg','Mot de passe modifié. Les autres sessions ont été révoquées.',true);e.target.reset()}catch(x){msg('#memberMsg',x.message)}};
initMember();


/* Aides contextuelles : une seule bulle ouverte, fermeture automatique après 5 s. */
function initInfoHelp(){
  const helps=[...document.querySelectorAll('details.info-help')];
  if(!helps.length)return;
  const close=d=>d.removeAttribute('open');
  const closeOthers=keep=>helps.forEach(d=>{if(d!==keep)close(d)});
  helps.forEach(d=>{
    d.addEventListener('toggle',()=>{if(d.open)closeOthers(d)});
  });
  document.addEventListener('pointerdown',e=>{if(!e.target.closest('details.info-help'))closeOthers(null)});
  document.addEventListener('keydown',e=>{if(e.key==='Escape')closeOthers(null)});
}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',initInfoHelp,{once:true});else initInfoHelp();
