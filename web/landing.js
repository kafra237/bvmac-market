(()=>{
"use strict";
const $=s=>document.querySelector(s);
const nf=new Intl.NumberFormat("fr-FR",{maximumFractionDigits:2});
function did(v){const s=String(v??"");return /^\d{8}$/.test(s)?`${s.slice(6,8)}/${s.slice(4,6)}/${s.slice(0,4)}`:"—"}
function num(v){if(v===null||v===undefined||v==='')return null;const n=Number(v);return Number.isFinite(n)?n:null}
function money(v){const n=num(v);return n===null?"—":nf.format(n)+" XAF"}
function move(v){const n=num(v);if(n===null)return '<span class="move flat">—</span>';const c=n>0?"up":n<0?"down":"flat";return `<span class="move ${c}">${n>0?"+":""}${nf.format(n)} %</span>`}
function esc(v){return String(v??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]))}
function quote(name,sub,price,variation){return `<div class="quote"><div class="name"><b>${esc(name||"—")}</b><span>${esc(sub||"")}</span></div><div class="price">${esc(money(price))}</div>${move(variation)}</div>`}
async function boot(){
  try{const me=await fetch('/api/v3/auth/me',{credentials:'same-origin',cache:'no-store'});if(me.ok){location.replace('/app?view=home');return}}catch{}
  try{
    const r=await fetch('/api/landing',{cache:'no-store',headers:{Accept:'application/json'}});if(!r.ok)throw new Error('HTTP '+r.status);const d=await r.json();
    $('#actions-date').textContent=d.meta?.last_session?`Séance du ${did(d.meta.last_session)}`:'Dernière séance';
    $('#landing-actions').innerHTML=(d.actions||[]).length?(d.actions||[]).map(x=>quote(x.ticker||x.name,x.name,x.close_price,x.variation_pct)).join(''):'<p class="placeholder">Aucune cotation disponible.</p>';
    $('#landing-funds').innerHTML=(d.opcvm||[]).length?(d.opcvm||[]).map(x=>quote(x.name,`VL du ${did(x.nav_date_id||x.bulletin_date_id)}`,x.nav,x.variation_pct)).join(''):'<p class="placeholder">Aucune valeur liquidative disponible.</p>';
    $('#landing-status').textContent=`${(d.actions||[]).length} action(s) · ${(d.opcvm||[]).length} OPCVM`;
    if(d.meta?.updated_at)$('#landing-updated').textContent='Import : '+new Date(d.meta.updated_at).toLocaleString('fr-FR');
  }catch(e){$('#landing-status').textContent='Données momentanément indisponibles';$('#landing-actions').innerHTML='<p class="placeholder">Réessayez dans quelques instants.</p>';$('#landing-funds').innerHTML='<p class="placeholder">Réessayez dans quelques instants.</p>'}
}
boot();
})();
