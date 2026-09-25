const $=s=>document.querySelector(s), charts={};
const pref=(k,d='')=>{try{return localStorage.getItem(k)??d}catch{return d}},setPref=(k,v)=>{try{localStorage.setItem(k,String(v))}catch{}};
let RANGE=pref('bvmac_lab_range','1y'),COMPANIES=[],LOAD_SEQ=0;
const fmt=(v,d=2)=>v==null||Number.isNaN(+v)?'—':Number(v).toLocaleString('fr-FR',{maximumFractionDigits:d});const pct=v=>v==null?'—':`${fmt(v)} %`;
async function api(u){let r;try{r=await fetch(u,{credentials:'same-origin'})}catch{throw new Error('Connexion au serveur interrompue. Réessaie dans quelques instants.')}let d=null;try{d=await r.json()}catch{}if(r.status===401){location.replace('/auth.html?mode=login');throw new Error('Connexion requise.')}if(!r.ok)throw new Error(d?.detail||d?.message||'Service temporairement indisponible.');if(d==null)throw new Error('Réponse serveur invalide.');return d}
function qRange(){const s=$('#start').value,e=$('#end').value;return s||e?`&start=${encodeURIComponent(s)}&end=${encodeURIComponent(e)}`:''}
function chart(id,type,labels,datasets,opts={}){charts[id]?.destroy();const canvas=$(id);const sets=type==='line'?datasets.map(BVMACCharts.smoothDataset):datasets;const options=type==='line'?BVMACCharts.lineOptions(opts):{responsive:true,maintainAspectRatio:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:true}},scales:type==='doughnut'?{}:{x:{ticks:{maxTicksLimit:10}},y:{},...(opts.scales||{})},...opts};charts[id]=new Chart(canvas,{type,data:{labels,datasets:sets},options});if(type==='line')BVMACCharts.bindReset(canvas,charts[id])}
async function boot(){const d=await api('/api/v3/market/companies');COMPANIES=d.companies||[];$('#company').innerHTML=COMPANIES.map(c=>`<option value="${c.company_id}">${c.ticker||c.short_name||c.company_id} · ${c.short_name||''}</option>`).join('');const saved=pref('bvmac_lab_company','');if(saved&&COMPANIES.some(c=>String(c.company_id)===saved))$('#company').value=saved;document.querySelectorAll('#ranges button').forEach(x=>x.classList.toggle('on',x.dataset.r===RANGE));$('#compareChoices').innerHTML=COMPANIES.map(c=>`<label><input type="checkbox" value="${c.company_id}">${c.ticker||c.short_name}</label>`).join('');await Promise.all([loadAll(),loadToday()])}
async function loadAll(){const seq=++LOAD_SEQ;try{$('#err').textContent='';const id=$('#company').value,qr=qRange();const [t,s,o]=await Promise.all([api(`/api/v3/market/technical/${id}?range=${RANGE}${qr}`),api(`/api/v3/market/seasonality/${id}?range=${RANGE}${qr}`),api(`/api/v3/market/orderbook/${id}?range=${RANGE}${qr}`)]);if(seq!==LOAD_SEQ)return;renderTech(t);renderSeason(s);renderBook(o)}catch(e){if(seq===LOAD_SEQ)$('#err').textContent=e.message}}
function renderTech(d){const s=d.summary||{};$('#k-close').textContent=fmt(s.last_close);$('#k-rsi').textContent=fmt(s.rsi14);$('#k-vol').textContent=pct(s.volatility20);$('#k-dd').textContent=pct(s.max_drawdown);$('#k-m1').textContent=pct(s.momentum_1m);$('#k-m3').textContent=pct(s.momentum_3m);$('#k-m6').textContent=pct(s.momentum_6m);$('#k-m12').textContent=pct(s.momentum_12m);const p=d.points||[];chart('#priceChart','line',p.map(x=>x.date),[{label:'Cours',data:p.map(x=>x.close)},{label:'SMA 20',data:p.map(x=>x.sma20)},{label:'SMA 50',data:p.map(x=>x.sma50)},{label:'SMA 200',data:p.map(x=>x.sma200)},{label:'Bollinger +',data:p.map(x=>x.boll_up),borderDash:[4,4],pointRadius:0},{label:'Bollinger -',data:p.map(x=>x.boll_low),borderDash:[4,4],pointRadius:0}],{elements:{point:{radius:0}}});chart('#momentumChart','line',p.map(x=>x.date),[{label:'RSI 14',data:p.map(x=>x.rsi14)},{label:'MACD',data:p.map(x=>x.macd)},{label:'Signal MACD',data:p.map(x=>x.macd_signal)}],{elements:{point:{radius:0}}})}
function renderSeason(d){const rows=x=>x.map(r=>`<tr><td>${r.label}</td><td>${pct(r.mean)}</td><td>${pct(r.median)}</td><td>${pct(r.positive_pct)}</td><td>${r.n}</td></tr>`).join('');$('#weekday').innerHTML=rows(d.weekday||[]);$('#months').innerHTML=rows(d.month||[])}
function pressureBuckets(points,maxBars=36){
  if(!points.length)return[];
  const size=Math.max(1,Math.ceil(points.length/maxBars)),out=[];
  for(let i=0;i<points.length;i+=size){
    const g=points.slice(i,i+size),bid=g.reduce((a,x)=>a+(+x.bid_volume||0),0),ask=g.reduce((a,x)=>a+(+x.ask_volume||0),0),total=bid+ask;
    out.push({label:g.length===1?g[0].date:`${g[0].date} → ${g[g.length-1].date}`,buy:total?bid/total*100:0,sell:total?-ask/total*100:0});
  }
  return out;
}
function renderBook(d){
  const p=d.points||[],bars=pressureBuckets(p),canvas=$('#bookChart');charts['#bookChart']?.destroy();
  const holder=canvas.parentElement;if(holder)holder.style.height=`${Math.min(720,Math.max(300,bars.length*22+90))}px`;
  charts['#bookChart']=new Chart(canvas,{type:'bar',data:{labels:bars.map(x=>x.label),datasets:[
    {label:'Vente (−)',stack:'pression',data:bars.map(x=>x.sell),backgroundColor:'#b4483f',borderColor:'#96382f',borderWidth:1,borderRadius:3},
    {label:'Achat (+)',stack:'pression',data:bars.map(x=>x.buy),backgroundColor:'#2e7d57',borderColor:'#245f44',borderWidth:1,borderRadius:3}
  ]},options:BVMACCharts.pressureOptions()});BVMACCharts.bindReset(canvas,charts['#bookChart']);
  const last=p.at(-1);$('#bookSummary').innerHTML=`<p><span class="tag">${d.coverage}</span></p><p>${d.note}</p><p>Dernière pression acheteuse : <b>${last?pct(last.bid_share_pct):'—'}</b></p><p>Dernière pression vendeuse : <b>${last&&last.bid_share_pct!=null?pct(100-last.bid_share_pct):'—'}</b></p><p>Déséquilibre : <b>${last?pct(last.imbalance_pct):'—'}</b></p><p>Histogramme : <b>${bars.length}</b> période(s) agrégée(s) sur la temporalité choisie.</p><p>Snapshots complets disponibles : <b>${(d.full_snapshots||[]).length}</b></p>`;
}
async function doCompare(){const ids=[...document.querySelectorAll('#compareChoices input:checked')].slice(0,5).map(x=>x.value);if(ids.length<2)return alert('Choisis au moins 2 valeurs');const d=await api(`/api/v3/market/compare?ids=${ids.join(',')}&range=${RANGE}${qRange()}`);const dates=[...new Set(d.series.flatMap(s=>s.points.map(p=>p.date)))].sort();const sets=d.series.map(s=>{const m=new Map(s.points.map(p=>[p.date,p.base100]));return{label:s.company.ticker||s.company.short_name,data:dates.map(x=>m.get(x)??null),spanGaps:true}});chart('#compareChart','line',dates,sets,{elements:{point:{radius:0}}});$('#compareMetrics').innerHTML=`<table><thead><tr><th>Valeur</th><th>Performance</th><th>Volatilité</th><th>Drawdown</th></tr></thead><tbody>${d.series.map(s=>`<tr><td>${s.company.ticker||s.company.short_name}</td><td>${pct(s.performance_pct)}</td><td>${pct(s.volatility_pct)}</td><td>${pct(s.max_drawdown_pct)}</td></tr>`).join('')}</tbody></table>`}
async function loadToday(){const d=await api('/api/v3/market/today');$('#today').innerHTML=`<p><b>${d.date||'—'}</b></p><p>${d.up||0} hausse(s) · ${d.down||0} baisse(s) · ${d.flat||0} stable(s)</p><p>Volume : <b>${fmt(d.volume,0)}</b> · Valeur : <b>${fmt(d.value_traded,0)} FCFA</b></p><div class="scroll"><table class="today-table"><tbody>${(d.movers||[]).map(x=>`<tr><td>${x.ticker||x.short_name||('Valeur '+x.company_id)}</td><td>${pct(x.variation_pct)}</td><td>${fmt(x.close)}</td></tr>`).join('')}</tbody></table></div>`}
$('#ranges').onclick=e=>{const b=e.target.closest('button[data-r]');if(!b)return;RANGE=b.dataset.r;setPref('bvmac_lab_range',RANGE);$('#start').value=$('#end').value='';document.querySelectorAll('#ranges button').forEach(x=>x.classList.toggle('on',x===b));loadAll()};$('#custom').onclick=()=>{RANGE='all';setPref('bvmac_lab_range',RANGE);setPref('bvmac_lab_start',$('#start').value);setPref('bvmac_lab_end',$('#end').value);document.querySelectorAll('#ranges button').forEach(x=>x.classList.remove('on'));loadAll()};$('#company').onchange=()=>{setPref('bvmac_lab_company',$('#company').value);loadAll()};$('#compareGo').onclick=doCompare;boot().catch(e=>$('#err').textContent=e.message);


/* Aides contextuelles : une seule bulle ouverte, fermeture explicite ou avec Échap. */
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
