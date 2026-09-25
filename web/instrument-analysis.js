/* Instrument history and advanced analysis. Price units remain explicit. */
(function(root){
  const n=v=>v==null||v===''||!Number.isFinite(Number(v))?null:Number(v);
  const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const fmt=(v,d=2)=>n(v)==null?'—':Number(v).toLocaleString('fr-FR',{maximumFractionDigits:d});
  const MONTHS=['Janvier','Février','Mars','Avril','Mai','Juin','Juillet','Août','Septembre','Octobre','Novembre','Décembre'];
  const DAY=86400000;
  function iso(value){const s=String(value||'');return /^\d{8}$/.test(s)?s.slice(0,4)+'-'+s.slice(4,6)+'-'+s.slice(6):s.slice(0,10)}
  function compute(rows){
    let up=0,down=0,e12=null,e26=null,signal=null,peak=null;
    return rows.map((r,i)=>{
      const value=r.value,delta=i?value-rows[i-1].value:0;
      e12=e12==null?value:e12+2/13*(value-e12);e26=e26==null?value:e26+2/27*(value-e26);
      const macd=e12-e26;signal=signal==null?macd:signal+2/10*(macd-signal);
      if(i>0&&i<=14){up+=Math.max(0,delta)/14;down+=Math.max(0,-delta)/14}
      else if(i>14){up=(up*13+Math.max(0,delta))/14;down=(down*13+Math.max(0,-delta))/14}
      const avg=period=>i+1<period?null:rows.slice(i-period+1,i+1).reduce((a,x)=>a+x.value,0)/period;
      const sma20=avg(20),std=sma20==null?null:Math.sqrt(rows.slice(i-19,i+1).reduce((a,x)=>a+(x.value-sma20)**2,0)/20);
      peak=peak==null?value:Math.max(peak,value);
      const bid=n(r.bid),ask=n(r.ask),total=bid!=null&&ask!=null?bid+ask:null;
      return {...r,sma20,sma50:avg(50),sma200:avg(200),bollUp:std==null?null:sma20+2*std,bollLow:std==null?null:sma20-2*std,
        rsi:i<14?null:down===0?(up===0?50:100):100-100/(1+up/down),macd:i<25?null:macd,signal:i<33?null:signal,
        ret:i?(value/rows[i-1].value-1)*100:null,drawdown:(value/peak-1)*100,
        buyPct:total>0?bid/total*100:null,sellPct:total>0?ask/total*100:null,
        buyAmount:bid==null?null:bid*value,sellAmount:ask==null?null:ask*value};
    });
  }
  function avg(values){const a=values.filter(v=>Number.isFinite(v));return a.length?a.reduce((x,y)=>x+y,0)/a.length:null}
  function median(values){const a=values.filter(v=>Number.isFinite(v)).sort((x,y)=>x-y);if(!a.length)return null;const m=Math.floor(a.length/2);return a.length%2?a[m]:(a[m-1]+a[m])/2}
  function compound(values){const a=values.filter(v=>Number.isFinite(v));return a.length?(a.reduce((x,v)=>x*(1+v/100),1)-1)*100:null}
  function periodReturns(rows,keyFn){
    const m=new Map();rows.filter(r=>r.ret!=null).forEach(r=>{const k=keyFn(r);if(!m.has(k))m.set(k,[]);m.get(k).push(r.ret)});
    return [...m].map(([key,values])=>({key,value:compound(values)})).filter(x=>x.value!=null);
  }
  function seasonality(rows){
    const monthly=periodReturns(rows,r=>r.date.slice(0,7));
    const byMonth=MONTHS.map((label,i)=>{const vals=monthly.filter(x=>Number(x.key.slice(5,7))===i+1).map(x=>x.value);return{label,mean:avg(vals),median:median(vals),n:vals.length}});
    const quarterly=periodReturns(rows,r=>{const m=Number(r.date.slice(5,7));return r.date.slice(0,4)+'-T'+(Math.floor((m-1)/3)+1)});
    const byQuarter=[1,2,3,4].map(q=>{const vals=quarterly.filter(x=>x.key.endsWith('T'+q)).map(x=>x.value);return{label:'T'+q,mean:avg(vals),median:median(vals),n:vals.length}});
    return {byMonth,byQuarter};
  }
  function pressureBucketKey(date,gran){
    const d=new Date(date+'T12:00:00');
    if(gran==='day')return date;
    if(gran==='month')return date.slice(0,7);
    if(gran==='quarter')return date.slice(0,4)+' · T'+(Math.floor((Number(date.slice(5,7))-1)/3)+1);
    if(gran==='year')return date.slice(0,4);
    const day=(d.getDay()+6)%7,d2=new Date(d.getTime()-day*DAY);return d2.toISOString().slice(0,10);
  }
  function bucketPressure(rows,gran){
    const groups=new Map();
    rows.forEach(r=>{const k=pressureBucketKey(r.date,gran);if(!groups.has(k))groups.set(k,[]);groups.get(k).push(r)});
    return [...groups].map(([label,g])=>{
      const sum=key=>{const vals=g.map(r=>r[key]).filter(v=>v!=null);return vals.length?vals.reduce((a,b)=>a+b,0):null};
      const bid=sum('bid'),ask=sum('ask'),turnover=sum('turnover'),volume=sum('volume'),buyAmount=sum('buyAmount'),sellAmount=sum('sellAmount'),total=(bid||0)+(ask||0);
      const last=g.at(-1);return {label,date:last.date,bid,ask,volume,turnover,buyPct:total>0?(bid||0)/total*100:null,sellPct:total>0?(ask||0)/total*100:null,buyAmount,sellAmount};
    });
  }
  function mount(container,raw,{kind='action'}={}){
    let old=container.querySelector('.instrument-tools');if(old){old.querySelectorAll('canvas').forEach(c=>root.Chart?.getChart(c)?.destroy());old.remove()}
    const holder=document.createElement('section');holder.className='instrument-tools';container.appendChild(holder);
    const normalized=raw.map(r=>({date:iso(kind==='fund'?(r.nav_date_id||r.bulletin_date_id):kind==='index'?r.date_id:r.bulletin_date_id),
      value:n(kind==='fund'?r.nav:kind==='index'?r.index_value:r.close_price),variation:n(kind==='fund'?r.var_prev_pct:kind==='index'?r.variation_day_pct:r.variation_pct),
      volume:n(r.vol_traded),turnover:n(r.value_traded),bid:n(r.vol_bid??r.bid_volume),ask:n(r.vol_ask??r.ask_volume)}))
      .filter(r=>r.value>0&&/^\d{4}-\d{2}-\d{2}$/.test(r.date)).sort((a,b)=>a.date.localeCompare(b.date));
    const points=compute([...new Map(normalized.map(r=>[r.date,r])).values()]),unit=kind==='index'?'points':'XAF';
    holder.innerHTML='<article class="instrument-card"><h3>'+(kind==='fund'?'Historique des valeurs liquidatives':'Historique de cotation')+'</h3><div class="instrument-controls"><label>Du <input type="date" data-start></label><label>Au <input type="date" data-end></label><button type="button" data-apply>Appliquer</button><button type="button" data-reset>Tout l’historique</button></div><p data-error role="status"></p>'+(kind==='action'?'<p class="instrument-note">Les volumes achat/vente correspondent aux volumes demandés et offerts publiés. Le volume échangé indique les titres effectivement déclarés comme échangés pendant la séance.</p>':'')+'<div class="instrument-table" tabindex="0" aria-label="Historique défilant"><table><thead><tr><th>Date</th><th>'+(kind==='fund'?'VL':kind==='index'?'Indice':'Clôture')+' ('+unit+')</th><th>Variation publiée</th>'+(kind==='action'?'<th>Volume achat</th><th>Volume vente</th><th>Volume échangé</th><th>Montant échangé (XAF)</th>':'')+'</tr></thead><tbody data-history></tbody></table></div><div class="instrument-controls"><button data-prev>← Précédent</button><span data-page aria-live="polite"></span><button data-next>Suivant →</button></div></article>'+
      '<article class="instrument-card"><h3>Analyse avancée</h3><p class="instrument-note">Indicateurs calculés sur les observations disponibles'+(kind==='fund'?' de valeur liquidative, pas sur des séances quotidiennes':'')+'. Les historiques incomplets restent signalés par « — ».</p><div class="instrument-kpis" data-kpis></div><h4>Cours &amp; moyennes mobiles</h4><div class="instrument-chart"><canvas data-trend></canvas></div><h4>RSI 14 observations</h4><div class="instrument-chart"><canvas data-rsi></canvas></div><h4>MACD et signal</h4><div class="instrument-chart"><canvas data-macd></canvas></div><details><summary>Saisonnalité des rendements observés</summary><div class="instrument-season" data-season></div></details>'+
      (kind==='action'?'<section class="pressure-zone"><h4>Pression achat / vente</h4><p class="instrument-note">Achat au-dessus de 0, vente sous 0. Molette ou pincement pour zoomer, glisser pour se déplacer, double-clic pour revenir à la vue choisie. Les montants sont estimés : volume demandé ou offert × dernier cours du regroupement.</p><div class="pressure-toolbar"><div class="pressure-window" data-pressure-window><span>Fenêtre</span><button data-window="week">Semaine</button><button data-window="month" class="on">Mois</button><button data-window="quarter">Trimestre</button><button data-window="year">Année</button><button data-window="all">Tout</button></div><label>Regrouper <select data-pressure-gran><option value="day">Séance</option><option value="week">Semaine</option><option value="month">Mois</option><option value="quarter">Trimestre</option><option value="year">Année</option></select></label></div><div data-pressure-summary></div><h4>Parts achat / vente</h4><div class="instrument-chart pressure-chart"><canvas data-pressure-pct></canvas></div><h4>Montants achat / vente estimés</h4><div class="instrument-chart pressure-chart"><canvas data-pressure-amount></canvas></div></section>':'')+'</article>';
    let filtered=points,page=0,pressureWindow='month',pressureGran='day';const $=q=>holder.querySelector(q),pageSize=25;
    function varCell(v){if(v==null)return'<span class="var-flat">—</span>';const cls=v>0?'var-up':v<0?'var-down':'var-flat',sign=v>0?'+':'';return'<span class="'+cls+'">'+sign+fmt(v)+' %</span>'}
    function volCell(v){if(v==null)return'<span class="volume-none">—</span>';return'<span class="volume-value '+(v>0?'has-volume':'no-volume')+'"><i aria-hidden="true"></i>'+fmt(v,0)+'</span>'}
    function history(){
      const rev=[...filtered].reverse(),pages=Math.max(1,Math.ceil(rev.length/pageSize));page=Math.min(page,pages-1);
      $('[data-history]').innerHTML=rev.slice(page*pageSize,(page+1)*pageSize).map(r=>'<tr class="'+(r.variation>0?'row-up':r.variation<0?'row-down':'row-flat')+'"><td>'+r.date+'</td><td>'+fmt(r.value)+'</td><td>'+varCell(r.variation)+'</td>'+(kind==='action'?'<td>'+volCell(r.bid)+'</td><td>'+volCell(r.ask)+'</td><td>'+volCell(r.volume)+'</td><td>'+fmt(r.turnover,0)+'</td>':'')+'</tr>').join('')||'<tr><td colspan="'+(kind==='action'?7:3)+'">Aucune observation sur cette période.</td></tr>';
      $('[data-page]').textContent=rev.length+' observations · page '+(page+1)+' / '+pages;$('[data-prev]').disabled=page===0;$('[data-next]').disabled=page+1>=pages;
    }
    function chart(selector,datasets,type='line',suffix=unit){
      const canvas=$(selector);if(!canvas)return;root.Chart.getChart(canvas)?.destroy();
      const opts={responsive:true,maintainAspectRatio:false,animation:false,interaction:{mode:'index',intersect:false},plugins:{legend:{display:true},tooltip:{callbacks:{label:c=>c.dataset.label+' : '+fmt(c.parsed.y)+' '+suffix}}},scales:{x:{ticks:{maxTicksLimit:7}},y:{ticks:{maxTicksLimit:6}}}};
      const ch=new root.Chart(canvas,{type,data:{labels:filtered.map(r=>r.date),datasets:datasets.map((d,i)=>({pointRadius:0,borderWidth:2,borderColor:['#2e5e4e','#c9a23f','#67726c','#b4483f','#7b668b'][i],backgroundColor:['#2e5e4e','#b4483f','#c9a23f'][i],...d}))},options:opts});return ch;
    }
    function seasonTable(){
      const s=seasonality(filtered),rows=a=>a.map(r=>'<tr><td>'+r.label+'</td><td class="'+(r.mean>0?'var-up':r.mean<0?'var-down':'var-flat')+'">'+(r.mean==null?'—':(r.mean>0?'+':'')+fmt(r.mean)+' %')+'</td><td>'+(r.median==null?'—':(r.median>0?'+':'')+fmt(r.median)+' %')+'</td><td>'+r.n+'</td></tr>').join('');
      $('[data-season]').innerHTML='<div class="season-block"><h5>Par mois</h5><div class="instrument-table"><table><thead><tr><th>Mois</th><th>Rendement moyen</th><th>Médiane</th><th>Périodes</th></tr></thead><tbody>'+rows(s.byMonth)+'</tbody></table></div></div><div class="season-block"><h5>Par trimestre</h5><div class="instrument-table"><table><thead><tr><th>Trimestre</th><th>Rendement moyen</th><th>Médiane</th><th>Périodes</th></tr></thead><tbody>'+rows(s.byQuarter)+'</tbody></table></div></div>';
    }
    function periodStart(date,key){
      if(!date||key==='all')return null;const d=new Date(date+'T12:00:00');
      if(key==='week'){const day=(d.getDay()+6)%7;d.setDate(d.getDate()-day)}
      else if(key==='month')d.setDate(1);
      else if(key==='quarter'){d.setMonth(Math.floor(d.getMonth()/3)*3,1)}
      else if(key==='year'){d.setMonth(0,1)}
      d.setHours(12,0,0,0);return d.toISOString().slice(0,10);
    }
    function pressureOptions(labels,suffix,windowKey){
      const count=labels.length,last=pressureSeries.at(-1)?.date;let minIndex=0;
      const target=periodStart(last,windowKey);
      if(target){const i=pressureSeries.findIndex(x=>x.date>=target);minIndex=i<0?Math.max(0,count-1):i}
      const maxIndex=Math.max(0,count-1);
      return {responsive:true,maintainAspectRatio:false,animation:{duration:220},interaction:{mode:'index',intersect:false},
        plugins:{legend:{display:true},tooltip:{callbacks:{label:c=>c.dataset.label+' : '+(c.parsed.y>0?'+':'')+fmt(c.parsed.y,suffix==='%'?1:0)+' '+suffix}},zoom:{limits:{x:{min:0,max:maxIndex}},pan:{enabled:true,mode:'x'},zoom:{wheel:{enabled:true,speed:.08},pinch:{enabled:true},drag:{enabled:false},mode:'x'}}},
        scales:{x:{min:minIndex,max:maxIndex,ticks:{maxTicksLimit:10,autoSkip:true},grid:{display:false}},y:{suggestedMin:-1,suggestedMax:1,grid:{color:c=>c.tick.value===0?'#59635d':'#e8ece8',lineWidth:c=>c.tick.value===0?2:1},ticks:{callback:v=>suffix==='%'?Math.abs(v)+' %':Math.abs(v).toLocaleString('fr-FR')}}}};
    }
    let pressureSeries=[];
    function renderPressure(){
      if(kind!=='action')return;pressureSeries=bucketPressure(filtered,pressureGran);const labels=pressureSeries.map(x=>x.label),last=pressureSeries.at(-1);
      $('[data-pressure-summary]').innerHTML=last?'<p class="pressure-summary">Au '+last.date+' · <b class="var-up">Achat '+fmt(last.buyPct)+' %</b> · <b class="var-down">Vente '+fmt(last.sellPct)+' %</b> · Volume achat '+fmt(last.bid,0)+' · Volume vente '+fmt(last.ask,0)+' · Volume échangé '+fmt(last.volume,0)+'.</p>':'<p class="instrument-note">Aucune donnée sur la sélection.</p>';
      const make=(sel,buyKey,sellKey,suffix)=>{const canvas=$(sel);root.Chart.getChart(canvas)?.destroy();const options=pressureOptions(labels,suffix,pressureWindow);const c=new root.Chart(canvas,{type:'bar',data:{labels,datasets:[{label:'Achat (+)',data:pressureSeries.map(x=>x[buyKey]),backgroundColor:'#1e7a4c',borderColor:'#17633e',borderWidth:1,borderRadius:3},{label:'Vente (−)',data:pressureSeries.map(x=>x[sellKey]==null?null:-x[sellKey]),backgroundColor:'#b3422e',borderColor:'#913524',borderWidth:1,borderRadius:3}]},options});canvas.ondblclick=()=>{applyPressureWindow(c,pressureWindow)};return c};
      make('[data-pressure-pct]','buyPct','sellPct','%');make('[data-pressure-amount]','buyAmount','sellAmount','XAF');
    }
    function applyPressureWindow(chartObj,windowKey){
      if(!chartObj||!pressureSeries.length)return;const max=pressureSeries.length-1,target=periodStart(pressureSeries[max].date,windowKey);let min=0;if(target){const idx=pressureSeries.findIndex(x=>x.date>=target);min=idx<0?max:idx}chartObj.options.scales.x.min=min;chartObj.options.scales.x.max=max;chartObj.update('none');
    }
    function render(){
      history();const first=filtered[0],last=filtered.at(-1),returns=filtered.slice(1).map(r=>r.ret).filter(x=>x!=null),mean=returns.length?returns.reduce((a,b)=>a+b,0)/returns.length:0;
      const vol=returns.length>1?Math.sqrt(returns.reduce((a,r)=>a+(r-mean)**2,0)/(returns.length-1)):null;let peak=null,dd=0;filtered.forEach(r=>{peak=peak==null?r.value:Math.max(peak,r.value);dd=Math.min(dd,(r.value/peak-1)*100)});
      $('[data-kpis]').innerHTML=[['Performance de la sélection',first&&last?fmt((last.value/first.value-1)*100)+' %':'—'],['Baisse maximale de la sélection',filtered.length?fmt(dd)+' %':'—'],['Volatilité par observation',vol==null?'—':fmt(vol)+' %'],['Dernier RSI 14',fmt(last?.rsi)]].map(([k,v])=>'<div><small>'+k+'</small><b>'+v+'</b></div>').join('');
      chart('[data-trend]',[['Cours','value'],['MM20','sma20'],['MM50','sma50'],['MM200','sma200'],['Bollinger haute','bollUp'],['Bollinger basse','bollLow']].map(([label,key])=>({label,data:filtered.map(r=>r[key])})));
      chart('[data-rsi]',[{label:'RSI 14',data:filtered.map(r=>r.rsi)}],'line','');chart('[data-macd]',[{label:'MACD',data:filtered.map(r=>r.macd)},{label:'Signal',data:filtered.map(r=>r.signal)}]);seasonTable();renderPressure();
    }
    $('[data-prev]').onclick=()=>{page--;history()};$('[data-next]').onclick=()=>{page++;history()};
    $('[data-apply]').onclick=()=>{const a=$('[data-start]').value,b=$('[data-end]').value;if(a&&b&&a>b){$('[data-error]').textContent='La date de début doit précéder la fin.';return}$('[data-error]').textContent='';filtered=points.filter(r=>(!a||r.date>=a)&&(!b||r.date<=b));page=0;render()};
    $('[data-reset]').onclick=()=>{$('[data-start]').value=$('[data-end]').value='';$('[data-error]').textContent='';filtered=points;page=0;render()};
    if(kind==='action'){
      $('[data-pressure-window]').onclick=e=>{const b=e.target.closest('button[data-window]');if(!b)return;pressureWindow=b.dataset.window;$('[data-pressure-window]').querySelectorAll('button').forEach(x=>x.classList.toggle('on',x===b));const a=root.Chart.getChart($('[data-pressure-pct]')),m=root.Chart.getChart($('[data-pressure-amount]'));applyPressureWindow(a,pressureWindow);applyPressureWindow(m,pressureWindow)};
      $('[data-pressure-gran]').onchange=e=>{pressureGran=e.target.value;renderPressure()};
    }
    render();
  }
  root.BVMACInstrument={compute,mount,seasonality,bucketPressure};if(typeof module!=='undefined')module.exports={compute,seasonality,bucketPressure};
})(typeof window==='undefined'?globalThis:window);
