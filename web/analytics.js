(()=>{
"use strict";
const API="/api/v3/analytics/events";
const SESSION_KEY="bvmac_analytics_session_v3";
let queue=[],timer=null,lastActivity=Date.now(),engaged=0,lcp=0,cls=0;
const uuid=()=>crypto.randomUUID?crypto.randomUUID():`${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
const clean=(v,n=160)=>String(v??"").replace(/[\s\n\r]+/g," ").trim().slice(0,n);
function sessionId(){let v=sessionStorage.getItem(SESSION_KEY);if(!v){v=uuid();sessionStorage.setItem(SESSION_KEY,v)}return v}
function families(){const ua=navigator.userAgent||"";return{
  device_type:/Mobi|Android|iPhone|iPad/i.test(ua)?(/iPad|Tablet/i.test(ua)?"tablet":"mobile"):"desktop",
  os_family:/Windows/i.test(ua)?"Windows":/Android/i.test(ua)?"Android":/iPhone|iPad|iOS/i.test(ua)?"iOS":/Mac OS/i.test(ua)?"macOS":/Linux/i.test(ua)?"Linux":"Autre",
  browser_family:/Edg\//.test(ua)?"Edge":/Firefox\//.test(ua)?"Firefox":/CriOS|Chrome\//.test(ua)?"Chrome":/Safari\//.test(ua)?"Safari":"Autre"};}
function context(){const c=navigator.connection||navigator.mozConnection||navigator.webkitConnection||{},f=families();return{...f,
 language:navigator.language||null,timezone:Intl.DateTimeFormat().resolvedOptions().timeZone||null,
 screen_width:screen.width,screen_height:screen.height,viewport_width:innerWidth,viewport_height:innerHeight,
 connection_type:c.effectiveType||c.type||null,referrer:document.referrer?new URL(document.referrer).origin:null,landing_page:location.pathname};}
function page(){return location.pathname;}
function permitted(){return true}
function track(event_name,properties={},entity_type=null,entity_id=null){if(!permitted())return;queue.push({event_name,occurred_at:new Date().toISOString(),page:page(),entity_type,entity_id,properties});if(queue.length>=20)flush();else schedule();}
function schedule(){if(timer)return;timer=setTimeout(()=>flush(false),6000)}
function body(){return JSON.stringify({session_id:sessionId(),context:context(),events:queue.splice(0,50)})}
function flush(beacon=false){if(!permitted()){queue=[];return;}if(timer){clearTimeout(timer);timer=null}if(!queue.length)return;const payload=body();if(beacon&&navigator.sendBeacon){navigator.sendBeacon(API,new Blob([payload],{type:"application/json"}));return}fetch(API,{method:"POST",headers:{"Content-Type":"application/json"},credentials:"same-origin",keepalive:true,body:payload}).catch(()=>{});}
function entity(el){const x=el.closest?.("[data-soc],[data-cid],[data-fond],[data-company-id],[data-portfolio-id]");if(!x)return[null,null];if(x.dataset.portfolioId)return["portfolio",x.dataset.portfolioId];if(x.dataset.fond)return["fund",x.dataset.fond];return["company",x.dataset.soc||x.dataset.cid||x.dataset.companyId||null];}
function label(el){const x=el.closest?.("button,a,[role=button]")||el;return clean(x?.getAttribute?.("aria-label")||x?.title||x?.textContent||x?.id||x?.tagName,100)}
function interactions(){
 document.addEventListener("click",e=>{lastActivity=Date.now();const el=e.target;if(!(el instanceof Element))return;const hit=el.closest("button,a,[role=button],tr.cliquable,[data-soc],[data-cid],[data-fond],[data-company-id],[data-portfolio-id]");if(!hit)return;const [et,ei]=entity(hit);const props={target_id:clean(hit.id||"",80),target_label:label(hit),tag:hit.tagName.toLowerCase()};if(hit.dataset.page)props.section=hit.dataset.page;if(hit.dataset.vue)props.view=hit.dataset.vue;track("ui_click",props,et,ei);if(et==="company")track("company_view",{source:"click"},et,ei);if(et==="fund")track("fund_view",{source:"click"},et,ei);if(et==="portfolio")track("portfolio_view",{},et,ei);},true);
 ["pointerdown","keydown","touchstart"].forEach(n=>addEventListener(n,()=>{lastActivity=Date.now()},{passive:true}));
 document.addEventListener("change",e=>{const t=e.target;if(!(t instanceof HTMLSelectElement||t instanceof HTMLInputElement))return;if(t.type==="password"||t.type==="email"||t.type==="tel"||t.type==="text")return;const id=t.id||t.name||"";if(!id)return;track("filter_change",{control:clean(id,80),value:clean(t.value,80)});},true);
 const searchIds=["rg-input","f-recherche","sc-recherche"];const delays={};for(const id of searchIds){const el=document.getElementById(id);if(!el)continue;el.addEventListener("input",()=>{clearTimeout(delays[id]);delays[id]=setTimeout(()=>{const q=clean(el.value,120);if(q.length>=2)track("search",{length:q.length,source:id});},700)});}
 const seen=new Set(),scrollContainer=document.querySelector(".platform-main");(scrollContainer||window).addEventListener("scroll",()=>{const el=scrollContainer||document.documentElement,h=Math.max(el.scrollHeight-(scrollContainer?el.clientHeight:innerHeight),1),p=Math.round((scrollContainer?el.scrollTop:scrollY)/h*100);for(const d of [25,50,75,90])if(p>=d&&!seen.has(d)){seen.add(d);track("scroll_depth",{depth:d})}},{passive:true});
}
function performanceTracking(){
 try{new PerformanceObserver(list=>{for(const e of list.getEntries())lcp=Math.max(lcp,e.startTime)}).observe({type:"largest-contentful-paint",buffered:true})}catch{}
 try{new PerformanceObserver(list=>{for(const e of list.getEntries())if(!e.hadRecentInput)cls+=e.value}).observe({type:"layout-shift",buffered:true})}catch{}
 addEventListener("load",()=>setTimeout(()=>{const n=performance.getEntriesByType("navigation")[0];track("performance",{load_ms:n?Math.round(n.loadEventEnd):null,dom_ms:n?Math.round(n.domContentLoadedEventEnd):null,transfer_bytes:n?.transferSize||null,lcp_ms:Math.round(lcp),cls:+cls.toFixed(4)});},500));
 addEventListener("error",e=>track("client_error",{message:clean(e.message,180),file:clean((e.filename||"").split("/").pop(),80),line:e.lineno||null}));
 addEventListener("unhandledrejection",e=>track("client_error",{message:clean(e.reason?.message||"Promise rejetée",180)}));
}
function engagement(){setInterval(()=>{if(document.visibilityState==="visible"&&Date.now()-lastActivity<60000){engaged+=30;track("engagement",{seconds:30});engaged=0}},30000)}
function boot(){interactions();performanceTracking();engagement();track("page_view",{title:clean(document.title,120)});flush(false)}
window.bvmacTrack=(name,properties={},entity_type=null,entity_id=null)=>track(name,properties,entity_type,entity_id);
addEventListener("pagehide",()=>{if(engaged)track("engagement",{seconds:engaged});flush(true)});
if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",boot);else boot();
})();
