// Shared analysis workspace: comparison, saved portfolios and allocation tools.
const $=s=>document.querySelector(s);
const fmt=(v,d=2)=>v==null?'—':Number(v).toLocaleString('fr-FR',{maximumFractionDigits:d}),pct=v=>v==null?'—':fmt(v)+' %';
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
let RANGE='1y',compareChart=null,compareSequence=0;
async function api(url){const r=await fetch(url,{credentials:'same-origin',cache:'no-store'});const d=await r.json();if(r.status===401){location.replace('/auth.html?mode=login');throw new Error('Connexion requise.')}if(!r.ok)throw new Error(typeof d.detail==='string'?d.detail:'Service indisponible.');return d}
function formObj(f){return Object.fromEntries(new FormData(f).entries())}
async function compare(){
  const seq=++compareSequence,selected=[...document.querySelectorAll('#compareChoices input:checked')];
  if(selected.length<2||selected.length>5){$('#err').textContent='Choisis entre 2 et 5 actions ou portefeuilles.';return}
  const start=$('#start').value,end=$('#end').value;
  if(start&&end&&start>end){$('#err').textContent='La date de début doit précéder la date de fin.';return}
  $('#compareGo').disabled=true;$('#err').textContent='Comparaison en cours…';
  try{
    const params=new URLSearchParams({range:RANGE,...(start?{start}:{}),...(end?{end}:{})});
    const ids=selected.filter(x=>x.dataset.kind==='action').map(x=>x.value);
    let series=[];
    if(ids.length){const data=await api('/api/v3/market/compare?ids='+ids.join(',')+'&'+params);series=data.series.map(x=>({name:x.company.ticker||x.company.short_name,points:x.points.map(p=>({date:p.date,value:p.close??p.base100}))}))}
    for(const input of selected.filter(x=>x.dataset.kind==='portfolio')){
      const d=await api('/api/v3/portfolio/'+input.value+'?'+params);series.push({name:d.name+' (virtuel)',points:d.curve.map(x=>({date:x.date,value:x.value}))});
    }
    if(seq!==compareSequence)return;
    if(series.length!==selected.length||series.some(s=>!s.points.length))throw new Error('Une sélection ne possède pas de données sur cette période.');
    const left=series.map(s=>s.points[0].date).sort().at(-1),right=series.map(s=>s.points.at(-1).date).sort()[0];
    if(left>=right)throw new Error('Historique commun insuffisant pour comparer cette sélection.');
    const dates=[...new Set([left,right,...series.flatMap(s=>s.points.map(p=>p.date).filter(d=>d>=left&&d<=right))])].sort();
    const normalized=series.map(s=>{let i=0;const values=dates.map(d=>{while(i+1<s.points.length&&s.points[i+1].date<=d)i++;return s.points[i].value});return {...s,values:values.map(v=>v/values[0]*100)}});
    compareChart?.destroy();compareChart=new Chart($('#compareChart'),{type:'line',data:{labels:dates,datasets:normalized.map(s=>BVMACCharts.smoothDataset({label:s.name,data:s.values}))},options:BVMACCharts.lineOptions()});
    BVMACCharts.bindReset($('#compareChart'),compareChart);
    $('#compareMetrics').innerHTML='<p>Base 100 au '+esc(left)+' · fin commune au '+esc(right)+' · cours connus reportés entre deux observations.</p><table><thead><tr><th>Sélection</th><th>Performance sur la période commune</th></tr></thead><tbody>'+normalized.map(s=>'<tr><td>'+esc(s.name)+'</td><td>'+pct(s.values.at(-1)-100)+'</td></tr>').join('')+'</tbody></table>';
    $('#err').textContent='';
  }catch(e){if(seq===compareSequence)$('#err').textContent=e.message}
  finally{if(seq===compareSequence)$('#compareGo').disabled=false}
}
async function boot(){
  const [companies,portfolios]=await Promise.all([api('/api/v3/market/companies'),api('/api/v3/portfolio/list')]);
  $('#compareChoices').innerHTML=(companies.companies||[]).map(c=>'<label><input type="checkbox" data-kind="action" value="'+c.company_id+'">'+esc(c.ticker||c.short_name)+'</label>').join('')+(portfolios.portfolios||[]).map(p=>'<label><input type="checkbox" data-kind="portfolio" value="'+p.portfolio_id+'">'+esc(p.name)+' · portefeuille</label>').join('');
  $('#compareGo').onclick=compare;
  $('#ranges').onclick=e=>{const b=e.target.closest('[data-r]');if(!b)return;RANGE=b.dataset.r;$('#start').value=$('#end').value='';document.querySelectorAll('#ranges button').forEach(x=>x.classList.toggle('on',x===b));if($('#compareChoices input:checked'))compare()};
  $('#custom').onclick=()=>{RANGE='all';compare()};
  const d=await api('/api/v3/market/today');$('#today').textContent=(d.date||'—')+' · '+(d.up||0)+' hausses · '+(d.down||0)+' baisses · '+fmt(d.value_traded,0)+' XAF échangés';
}
boot().catch(e=>$('#err').textContent=e.message);
