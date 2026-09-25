(()=>{
  const ZOOM_X={
    limits:{x:{min:'original',max:'original'}},
    pan:{enabled:true,mode:'x'},
    zoom:{wheel:{enabled:true,speed:.08},pinch:{enabled:true},drag:{enabled:false},mode:'x'}
  };
  const ZOOM_Y={
    limits:{y:{min:'original',max:'original'}},
    pan:{enabled:true,mode:'y'},
    zoom:{wheel:{enabled:true,speed:.08},pinch:{enabled:true},drag:{enabled:false},mode:'y'}
  };
  function smoothDataset(ds={}){
    return {
      ...ds,
      tension: ds.tension ?? .38,
      cubicInterpolationMode: ds.cubicInterpolationMode ?? 'monotone',
      pointRadius: ds.pointRadius ?? 0,
      pointHoverRadius: ds.pointHoverRadius ?? 5,
      pointHitRadius: ds.pointHitRadius ?? 14,
      spanGaps: ds.spanGaps ?? true,
      borderWidth: ds.borderWidth ?? 2,
    };
  }
  function mergeOptions(base,extra={}){
    return {
      ...base,...extra,
      interaction:{...(base.interaction||{}),...(extra.interaction||{})},
      elements:{...(base.elements||{}),...(extra.elements||{}),line:{...(base.elements?.line||{}),...(extra.elements?.line||{})},point:{...(base.elements?.point||{}),...(extra.elements?.point||{})}},
      plugins:{...(base.plugins||{}),...(extra.plugins||{}),zoom:extra.plugins?.zoom===false?undefined:{...(base.plugins?.zoom||{}),...(extra.plugins?.zoom||{})}},
      scales:{...(base.scales||{}),...(extra.scales||{})},
    };
  }
  function lineOptions(extra={}){
    const base={
      responsive:true,maintainAspectRatio:false,
      interaction:{mode:'index',intersect:false},
      animation:{duration:260},
      elements:{line:{tension:.38,cubicInterpolationMode:'monotone'},point:{radius:0,hoverRadius:5,hitRadius:14}},
      plugins:{legend:{display:true},tooltip:{enabled:true},zoom:ZOOM_X},
      scales:{x:{ticks:{maxTicksLimit:10}},y:{}}
    };
    return mergeOptions(base,extra);
  }
  function pressureOptions(extra={}){
    const base={
      responsive:true,maintainAspectRatio:false,indexAxis:'y',
      interaction:{mode:'index',intersect:false},
      animation:{duration:260},
      plugins:{
        legend:{display:true},tooltip:{enabled:true,callbacks:{label:c=>`${c.dataset.label} : ${Math.abs(Number(c.parsed.x||0)).toLocaleString('fr-FR',{maximumFractionDigits:1})} %`}},
        zoom:ZOOM_Y
      },
      scales:{
        x:{min:-100,max:100,stacked:true,grid:{color:c=>c.tick.value===0?'#59635d':'#e8ece8',lineWidth:c=>c.tick.value===0?2:1},ticks:{callback:v=>`${Math.abs(v)} %`}},
        y:{stacked:true,ticks:{autoSkip:true,maxTicksLimit:24}}
      }
    };
    return mergeOptions(base,extra);
  }
  function bindReset(canvas,chart){
    if(!canvas||!chart) return;
    canvas.ondblclick=()=>{ if(typeof chart.resetZoom==='function') chart.resetZoom(); };
  }
  window.BVMACCharts={ZOOM_X,ZOOM_Y,smoothDataset,lineOptions,pressureOptions,bindReset};
})();
