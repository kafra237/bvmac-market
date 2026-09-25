"use strict";
/* ═══════════════════════════ DONNÉES ═══════════════════════════ */
// ==DATA-LAYER-START==
const CAT_LIB = {A:"Actions", O:"Obligataire", M:"Monétaire", D:"Diversifié", C:"Contractuel"};

function did2date(id){ id=Number(id); if(!id||isNaN(id)) return null;
  const s=String(Math.trunc(id)); return new Date(+s.slice(0,4), +s.slice(4,6)-1, +s.slice(6,8)); }
function fmtDate(id,opts){ const d=did2date(id); return d? d.toLocaleDateString(LANG==="en"?"en-GB":"fr-FR",opts||{day:"2-digit",month:"short",year:"numeric"}) : "—"; }
function fmtN(v,dec=0){ if(v==null||isNaN(v)) return "—";
  return Number(v).toLocaleString(LANG==="en"?"en-GB":"fr-FR",{minimumFractionDigits:dec,maximumFractionDigits:dec}); }
function fmtFCFA(v){ return v==null||isNaN(v)? "—" : fmtN(v)+" FCFA"; }
function fmtPct(v,signe=true){ if(v==null||isNaN(v)) return "—";
  return (signe&&v>0?"+":"")+fmtN(v,2)+" %"; }
function clsVar(v){ return v>0?"pos":(v<0?"neg":"neutre"); }
function compactFCFA(v){ if(v==null||isNaN(v)) return "—";
  const a=Math.abs(v);
  if(a>=1e12) return fmtN(v/1e12,2)+"000 Mds";
  if(a>=1e9)  return fmtN(v/1e9,2)+" Mds";
  if(a>=1e6)  return fmtN(v/1e6,1)+" M";
  return fmtN(v); }

function preparerDonnees(payload){
  const D={
    meta     : payload.meta||{},
    societes : payload.societes||[],
    dates    : [],
    fonds    : payload.fonds||[],
    prix     : payload.prix||[],
    capi     : payload.capi||[],
    indice   : payload.indice||[],
    vl       : payload.vl||[],
    fin      : payload.fin||[],
    qualite  : [],
  };
  if(!D.societes.length || !D.prix.length)
    throw new Error("format de données non reconnu");

  D.socParId = Object.fromEntries(D.societes.map(s=>[s.company_id,s]));
  D.fondParId= Object.fromEntries(D.fonds.map(f=>[f.fund_id,f]));
  D.seances  = [...new Set(D.prix.map(r=>r.bulletin_date_id))].sort((a,b)=>a-b);
  D.prixParSeance = {};
  for(const r of D.prix) (D.prixParSeance[r.bulletin_date_id]??=[]).push(r);
  D.capParSeance = {};
  for(const r of D.capi) (D.capParSeance[r.date_id]??=[]).push(r);
  D.vlParBulletin = {};
  for(const r of D.vl) (D.vlParBulletin[r.bulletin_date_id]??=[]).push(r);
  D.indiceParDate = Object.fromEntries(D.indice.map(r=>[r.date_id,r]));
  D.indice.sort((a,b)=>a.date_id-b.date_id);
  D.finParSociete = {};
  for(const r of D.fin)
    (D.finParSociete[r.company_id]??=[]).push(r);
  for(const k in D.finParSociete)
    D.finParSociete[k].sort((a,b)=>a.fiscal_year-b.fiscal_year);
  return D;
}

function histoSociete(D, companyId){
  return D.prix.filter(r=>r.company_id===companyId)
               .sort((a,b)=>a.bulletin_date_id-b.bulletin_date_id);
}
function histoCap(D, companyId){
  return D.capi.filter(r=>r.company_id===companyId)
               .sort((a,b)=>a.date_id-b.date_id);
}
function histoVL(D, fundId){
  return D.vl.filter(r=>r.fund_id===fundId)
             .sort((a,b)=>a.bulletin_date_id-b.bulletin_date_id);
}
function perfAnnualisee(fond, dernierVL){
  if(!fond || !fond.initial_value || !dernierVL || !dernierVL.nav) return null;
  const d0=did2date(fond.inception_date_id), d1=did2date(dernierVL.nav_date_id||dernierVL.bulletin_date_id);
  if(!d0||!d1) return null;
  const jours=(d1-d0)/86400000;
  if(jours<30) return null;
  return (Math.pow(dernierVL.nav/fond.initial_value, 365/jours)-1)*100;
}
function ratiosFondamentaux(exercices){
  // Calcule pour chaque exercice : marge nette, ROE, ROA, autonomie
  // financiere et croissances annuelles (CA, RN, CP, VA).
  return exercices.map((r,i)=>{
    const prev = i>0 ? exercices[i-1] : null;
    const pct=(a,b)=> (a!=null&&b!=null&&b!==0)? a/b*100 : null;
    const cr =(a,b)=> (a!=null&&b!=null&&b!==0)? (a/b-1)*100 : null;
    return {
      ...r,
      marge_nette_pct : pct(r.resultat_net, r.chiffre_affaires),
      roe_pct         : r.roe_publie_pct ?? pct(r.resultat_net, r.capitaux_propres),
      roa_pct         : pct(r.resultat_net, r.total_bilan),
      autonomie_pct   : pct(r.capitaux_propres, r.total_bilan),
      croissance_ca_pct : cr(r.chiffre_affaires, prev?.chiffre_affaires),
      croissance_rn_pct : cr(r.resultat_net,     prev?.resultat_net),
      croissance_cp_pct : cr(r.capitaux_propres, prev?.capitaux_propres),
      croissance_va_pct : cr(r.valeur_ajoutee,   prev?.valeur_ajoutee),
    };
  });
}
function filtrerPeriode(lignes, cleDate, code){
  // code: "tout" | "3a" | "1a" | "6m" — par rapport à la dernière observation
  if(!lignes.length || code==="tout") return lignes;
  const fin = did2date(lignes[lignes.length-1][cleDate]);
  const mois = {"3a":36,"1a":12,"6m":6}[code];
  const debut = new Date(fin); debut.setMonth(debut.getMonth()-mois);
  return lignes.filter(r=>{const d=did2date(r[cleDate]); return d && d>=debut;});
}

function ech(v){ return String(v??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); }
function moyenne(v){ const a=v.filter(Number.isFinite); return a.length?a.reduce((s,x)=>s+x,0)/a.length:null; }
function ecartType(v){ const a=v.filter(Number.isFinite); if(a.length<2)return null; const m=moyenne(a); return Math.sqrt(a.reduce((s,x)=>s+(x-m)**2,0)/(a.length-1)); }
function rendementDepuis(histo,jours){
  if(histo.length<2) return null;
  const fin=histo[histo.length-1], dFin=did2date(fin.bulletin_date_id), cible=new Date(dFin);
  cible.setDate(cible.getDate()-jours);
  const avant=[...histo].reverse().find(r=>did2date(r.bulletin_date_id)<=cible);
  return avant?.close_price&&fin.close_price ? (fin.close_price/avant.close_price-1)*100 : null;
}
function dernierAvant(lignes,cle,dateId){
  const val=[...lignes].filter(r=>r[cle]<=dateId).sort((a,b)=>a[cle]-b[cle]);
  return val[val.length-1];
}
function percentile(lignes,cle,val,inverse=false){
  if(!Number.isFinite(val)) return null;
  const a=lignes.map(x=>x[cle]).filter(Number.isFinite).sort((x,y)=>x-y);
  if(a.length<2) return null;
  const rang=a.filter(x=>x<=val).length-1, p=Math.max(0,Math.min(100,rang/(a.length-1)*100));
  return inverse?100-p:p;
}
function moyenneScores(...vals){ return vals.every(Number.isFinite)?vals.reduce((a,b)=>a+b,0)/vals.length:null; }
function analyseUnivers(dateId=seanceCourante){
  const cle=String(dateId); if(analysesCache.has(cle)) return analysesCache.get(cle);
  const seances=D.seances.filter(x=>x<=dateId), fenetre=new Set(seances.slice(-60));
  const base=D.societes.map(s=>{
    const hist=histoSociete(D,s.company_id).filter(r=>r.bulletin_date_id<=dateId&&r.close_price!=null);
    const p=hist[hist.length-1];
    const caps=histoCap(D,s.company_id).filter(r=>r.date_id<=dateId), cap=caps[caps.length-1];
    const recent=hist.filter(r=>fenetre.has(r.bulletin_date_id));
    const retours=[]; for(let i=1;i<recent.length;i++) if(recent[i-1].close_price&&recent[i].close_price) retours.push((recent[i].close_price/recent[i-1].close_price-1)*100);
    let pic=-Infinity, drawdown=0; for(const r of hist){ pic=Math.max(pic,r.close_price||-Infinity); if(pic>0)drawdown=Math.min(drawdown,(r.close_price/pic-1)*100); }
    const finBrut=D.finParSociete[s.company_id]||[], fin=ratiosFondamentaux(finBrut).at(-1);
    const actifs=recent.filter(r=>(r.vol_traded||0)>0||(r.value_traded||0)>0).length;
    const rendement=fin?.taux_rendement_brut_pct ?? (p?.close_price&&cap?.last_div_amount!=null?cap.last_div_amount/p.close_price*100:null);
    return {s,p,cap,fin,
      r1m:rendementDepuis(hist,31),r6m:rendementDepuis(hist,183),r1a:rendementDepuis(hist,366),
      volatilite:ecartType(retours)!=null?ecartType(retours)*Math.sqrt(252):null,drawdown,
      frequence:seances.slice(-60).length?actifs/seances.slice(-60).length*100:0,
      valeurMoy:moyenne(recent.map(r=>Number(r.value_traded)||0)),
      per:cap?.per>0?cap.per:null, rendement, roe:fin?.roe_pct, marge:fin?.marge_nette_pct,
      varJour:p?.variation_pct, prix:p?.close_price, couverture:[p,cap,fin].filter(Boolean).length,
    };
  }).filter(x=>x.p);
  for(const x of base){
    const liq=moyenneScores(percentile(base,"frequence",x.frequence),percentile(base,"valeurMoy",x.valeurMoy));
    const renta=moyenneScores(percentile(base,"roe",x.roe),percentile(base,"marge",x.marge));
    const perf=moyenneScores(percentile(base,"r6m",x.r6m),percentile(base,"r1a",x.r1a));
    const valor=moyenneScores(percentile(base,"per",x.per,true),percentile(base,"rendement",x.rendement));
    const risque=moyenneScores(percentile(base,"volatilite",x.volatilite,true),percentile(base,"drawdown",x.drawdown));
    const raw={frequence:x.frequence,valeur_moyenne:x.valeurMoy,roe:x.roe,marge_nette:x.marge,performance_6m:x.r6m,performance_1a:x.r1a,per:x.per,rendement:x.rendement,volatilite:x.volatilite,drawdown:x.drawdown};
    x.scoreManquants=Object.entries(raw).filter(([,v])=>!Number.isFinite(v)).map(([k])=>k);
    x.composantes={liquidite:Number.isFinite(liq)?Math.round(liq):null,rentabilite:Number.isFinite(renta)?Math.round(renta):null,performance:Number.isFinite(perf)?Math.round(perf):null,valorisation:Number.isFinite(valor)?Math.round(valor):null,risque:Number.isFinite(risque)?Math.round(risque):null};
    x.score=Object.values(x.composantes).every(Number.isFinite)?Math.round(liq*.25+renta*.20+perf*.20+valor*.20+risque*.15):null;
  }
  analysesCache.set(cle,base); return base;
}
// ==DATA-LAYER-END==

/* ═══════════════════════════ ÉTAT ═══════════════════════════ */
const PREF=(k,d=null)=>{try{return localStorage.getItem(k)??d}catch{return d}};
const PREFSET=(k,v)=>{try{localStorage.setItem(k,String(v))}catch{}};
let LANG=(PREF("bvmac_pref_lang")||((navigator.language||"").toLowerCase().startsWith("en")?"en":"fr"));
let D=null, seanceCourante=null, seanceApercu=null, vueCourante="accueil";
const graphes={};
const etatTri={};                       // id de table -> {col, dir}
let compSelection=null;                 // Set de company_id pour la comparaison
let compPeriode="tout", indicePeriode="tout";
let compGran="M", indiceGran="M";       // échelle de temps par défaut : J/S/M/A
let recitPeriode=/^[JSMTA]$/.test(PREF("bvmac_pref_story_range","J"))?PREF("bvmac_pref_story_range","J"):"J";
let qualiteSev="";                      // filtre sévérité
let analysesCache=new Map();
let listeSuivi=new Set((()=>{try{const x=JSON.parse(PREF("bvmac_guest_watchlist","[]"));return Array.isArray(x)?x.map(String):[]}catch{return[]}})()); // cache visiteur + watchlist compte côté PostgreSQL

const PALETTE_SERIES=["#2E5E4E","#C9A23F","#5A7FA8","#B3422E","#7A5E8A","#3F8A7A","#A8743F","#4A6A3C"];
const $=s=>document.querySelector(s);

/* ═══════════════ FRANÇAIS / ANGLAIS ═══════════════ */
const EN={
  "Plateforme boursière":"Stock market platform","Aperçu":"Overview","Aperçu du marché":"Market overview","Actions":"Equities",
  "Indice":"Index","Filtrer":"Filter","Indice BVMAC-AS":"BVMAC-AS Index","Valeurs suivies":"Watchlist","Données":"Data","Séance":"Session",
  "Actualiser":"Refresh","Chargement…":"Loading…","Ouverture de la séance…":"Opening market session…",
  "Cote des actions":"Equity quotes","OPCVM — meilleures performances depuis l'origine":"Funds — best performance since inception",
  "Marché des actions":"Equity market","Comparaison des valeurs":"Compare securities","base 100 à la première observation":"base 100 at first observation",
  "Échelle":"Scale","Période":"Period","OPCVM":"Funds","Toutes catégories":"All categories",
  "Tous gestionnaires":"All managers","Toutes fréquences":"All frequencies","Indice BVMAC All Share":"BVMAC All Share Index",
  "Évolution":"Performance","Screener actions":"Equity screener","Comparez les valeurs selon leur performance, leur valorisation et leur liquidité.":"Compare securities by performance, valuation and liquidity.",
  "Tous secteurs":"All sectors","Tous pays":"All countries","Tout score BVMAC":"Any BVMAC score","Toute liquidité":"Any liquidity",
  "Valeurs suivies":"Watchlist","Vos valeurs favorites sont conservées uniquement dans ce navigateur.":"Your favourites are stored only in this browser.",
  "Données & méthodologie":"Data & methodology","Comprendre la couverture, les mises à jour et les indicateurs calculés.":"Understand coverage, updates and calculated indicators.",
  "Largeur du marché":"Market breadth","hausse, baisse et stabilité":"advancers, decliners and unchanged","Valeurs les plus actives":"Most active securities",
  "par valeur échangée":"by traded value","Plus fortes hausses":"Top gainers","Plus fortes baisses":"Top decliners","séance sélectionnée":"selected session",
  "Carte du marché":"Market map","variation quotidienne":"daily change","Suivi des publications":"Publication tracking",
  "retards et réparations automatiques":"delays and automatic repairs","Méthodologie du score BVMAC":"BVMAC score methodology","Glossaire":"Glossary",
  "Capitalisation totale":"Total market cap","Volume échangé":"Trading volume","Valeur échangée":"Traded value","Transactions":"Transactions",
  "Valeur":"Security","Cours":"Price","Var. %":"Change %","Capitalisation":"Market cap","Statut":"Status","Volume":"Volume",
  "Fonds":"Fund","Fréquence":"Frequency","Var. préc.":"Previous change","Var. origine":"Since inception","Perf. annualisée":"Annualised return",
  "Pays":"Country","Secteur":"Sector","Rendement":"Yield","Liquidité":"Liquidity","Score BVMAC":"BVMAC score","Suivi":"Watch",
  "Cours de clôture":"Closing price","Caractéristiques":"Characteristics",
  "Indicateurs fondamentaux":"Fundamentals","Volumes échangés par séance":"Volume traded by session","Retour à la cote":"Back to quotes","Retour aux OPCVM":"Back to funds",
  "Tout":"All","3 ans":"3 years","1 an":"1 year","6 mois":"6 months","Jour":"Day","Semaine":"Week","Mois":"Month","Année":"Year",
  "Rechercher une action ou un fonds…":"Search a security or fund…","Rechercher une valeur…":"Search a security…","Filtrer les fonds…":"Filter funds…",
  "Données momentanément indisponibles":"Data temporarily unavailable","Données indisponibles":"Data unavailable","Que faire":"What to do","Réessayer":"Try again",
  "Aucune cotation pour cette séance.":"No quote for this session.","Aucun résultat":"No result","Aucune valeur dans votre liste.":"No security in your watchlist.",
  "Couverture":"Coverage","Sociétés":"Companies","Séances publiées":"Published sessions","Historique":"History","Dernière mise à jour":"Last update",
  "À jour":"Up to date","Dates en attente":"Pending dates","Fichiers manquants":"Missing files","Échecs récents":"Recent failures",
  "Performance":"Performance","Rentabilité":"Profitability","Valorisation":"Valuation","Risque":"Risk","Ajouter aux valeurs suivies":"Add to watchlist","Retirer des valeurs suivies":"Remove from watchlist",
  "Plus haut depuis janvier":"High since January","Plus bas depuis janvier":"Low since January","Variation annuelle":"Annual change","Seuils de séance":"Session limits",
  "Titres en circulation":"Shares outstanding","Flottant":"Free float","Capitalisation globale":"Total market cap","Capitalisation flottante":"Free-float market cap",
  "Dernier dividende":"Latest dividend","Taux de rendement brut":"Gross dividend yield","Liquidité des titres":"Share liquidity","Performance 1 mois":"1-month return",
  "Performance 6 mois":"6-month return","Performance 1 an":"1-year return","Volatilité annualisée":"Annualised volatility","Drawdown maximal":"Maximum drawdown",
  "Fréquence de cotation":"Trading frequency","Valeur moyenne échangée":"Average traded value","Score synthétique":"Composite score",
  "Récits":"Stories","Histoires du marché":"Market stories","Les données BVMAC expliquées en quelques chiffres, sans jargon inutile.":"BVMAC data explained through a few clear figures, without unnecessary jargon.",
  "Chapitres du récit":"Story chapters","01 · La séance racontée":"01 · The session explained","01 · La période racontée":"01 · The period explained","02 · Depuis 2019":"02 · Since 2019","03 · La liquidité réelle":"03 · Real liquidity",
  "Période analysée":"Analysed period","Trimestre":"Quarter","La période racontée":"The period explained","ce qu’il faut retenir de la période sélectionnée":"what matters over the selected period",
  "La séance racontée":"The session explained","ce qu’il faut retenir de la date sélectionnée":"what matters on the selected date",
  "L’histoire de la BVMAC depuis 2019":"The BVMAC story since 2019","capitalisation, indice et nouvelles admissions":"market capitalisation, index and new listings",
  "Le vrai visage de la liquidité":"The true picture of liquidity","activité observée sur les 60 dernières séances disponibles":"activity observed over the latest 60 available sessions","activité observée pendant la période sélectionnée":"activity observed over the selected period",
  "Le chiffre à retenir":"The key figure","Source : bulletins officiels de la BVMAC. Données de fin de séance.":"Source: official BVMAC bulletins. End-of-session data.",
  "Valeur la plus active":"Most active security","Plus forte variation":"Largest move","Séances analysées":"Sessions analysed","Concentration des échanges":"Trading concentration",
  "Valeur la plus régulière":"Most consistently traded","Valeur la moins régulière":"Least consistently traded","Fréquence d’activité":"Trading activity frequency",
  "Valeur échangée cumulée":"Cumulative traded value","Première observation":"First observation","Dernière observation":"Latest observation","Admissions visibles dans les données":"Listings visible in the data",
  "Accueil":"Home","Analyse avancée":"Advanced analysis","Compte & portefeuilles":"Account & portfolios","Feeds BVMAC":"BVMAC feeds",
  "Aucune donnée pour cette séance.":"No data for this session.","Initiative personnelle indépendante · sans affiliation à la BVMAC.":"Independent personal initiative · not affiliated with BVMAC.",
  "À propos, sources et confidentialité":"About, sources and privacy","Contact":"Contact","titres":"securities","hausse":"advancer","hausses":"advancers","baisse":"decliner","baisses":"decliners","stable":"unchanged","stables":"unchanged",
  "Radar prédictif":"Predictive radar","Probabilités calibrées à partir de l’historique des cotations, de la liquidité et de l’offre/demande. Chaque modèle a été validé chronologiquement hors échantillon.":"Calibrated probabilities based on quote history, liquidity and supply/demand. Each model was validated chronologically out of sample.",
  "Liquidité à court terme":"Short-term liquidity","Tendance ~20 bulletins":"~20-bulletin trend","OPCVM · risque faible":"Funds · low risk","Vue des actions":"Stock overview","probabilités, pas des rendements promis":"probabilities, not promised returns","Signaux remarquables":"Notable signals","Toute confiance":"Any confidence","Élevée":"High","Moyenne":"Medium","Faible / expérimental":"Low / experimental","Confiance":"Confidence","Pression":"Pressure","Dernière VL":"Latest NAV","Date VL":"NAV date","Risque prochaine VL":"Next NAV downside risk","Méthode.":"Method.","Fermer":"Close","Historique des probabilités":"Probability history","Historique du risque estimé":"Estimated risk history","Facteurs de sensibilité · liquidité":"Sensitivity factors · liquidity","Facteurs de sensibilité · tendance":"Sensitivity factors · trend","Pression actuelle":"Current pressure","Historique exploité":"History used","VL uniques":"unique NAVs","Acheteuse":"Buy-side","Vendeuse":"Sell-side","Équilibrée":"Balanced","Expérimental":"Experimental","Rechercher…":"Search…"
};
function traduireTexte(source){
  if(LANG!=="en") return source;
  const avant=source.match(/^\s*/)?.[0]||"", apres=source.match(/\s*$/)?.[0]||"", coeur=source.trim();
  if(!coeur) return source;
  let r=EN[coeur]||coeur;
  if(r===coeur) r=r
    .replace(/séance du/gi,"session of").replace(/sur la séance/gi,"during the session")
    .replace(/mis à jour le/gi,"updated on").replace(/séance\(s\)/gi,"session(s)").replace(/sociétés/gi,"companies")
    .replace(/fonds valorisés/gi,"funds valued").replace(/dans ce bulletin/gi,"in this bulletin")
    .replace(/cliquez sur les en-têtes pour trier/gi,"click headers to sort").replace(/dernier bulletin/gi,"latest bulletin")
    .replace(/depuis l'origine/gi,"since inception").replace(/points?\b/gi,"point(s)");
  return avant+r+apres;
}
function appliquerLangueDOM(racine=document){
  document.documentElement.lang=LANG; document.title=LANG==="en"?"BVMAC — Stock market platform":"BVMAC — Plateforme boursière";
  const walker=document.createTreeWalker(racine,NodeFilter.SHOW_TEXT,{acceptNode:n=>/\S/.test(n.nodeValue||"")&&!n.parentElement?.closest("script,style")?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT});
  const noeuds=[]; while(walker.nextNode()) noeuds.push(walker.currentNode);
  for(const n of noeuds){ if(n.__frOriginal===undefined)n.__frOriginal=n.nodeValue; n.nodeValue=LANG==="fr"?n.__frOriginal:traduireTexte(n.__frOriginal); }
  racine.querySelectorAll?.("[placeholder]").forEach(el=>{if(!el.dataset.frPlaceholder)el.dataset.frPlaceholder=el.placeholder; el.placeholder=LANG==="fr"?el.dataset.frPlaceholder:traduireTexte(el.dataset.frPlaceholder);});
  document.querySelectorAll("[data-court]").forEach(el=>{if(!el.dataset.frCourt)el.dataset.frCourt=el.dataset.court; el.dataset.court=LANG==="fr"?el.dataset.frCourt:traduireTexte(el.dataset.frCourt);});
  document.querySelectorAll("#nav [aria-label]").forEach(el=>{if(!el.dataset.frLabel)el.dataset.frLabel=el.getAttribute("aria-label"); el.setAttribute("aria-label",LANG==="fr"?el.dataset.frLabel:traduireTexte(el.dataset.frLabel));});
  document.querySelectorAll("#langue button").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.lang===LANG)));
}
window.BVMACApplyLanguage=appliquerLangueDOM;
document.querySelectorAll("#langue button").forEach(b=>b.addEventListener("click",()=>{
  LANG=b.dataset.lang; PREFSET("bvmac_pref_lang",LANG); analysesCache.clear();
  if(D){ majSelecteurSeance(); majTicker(); rendre(); } appliquerLangueDOM(document); window.dispatchEvent(new CustomEvent("bvmac:language-changed",{detail:{lang:LANG}}));
}));

/* ═══════════════ GUILLOCHE (signature) ═══════════════ */
function dessinerGuilloche(svg, traits, couleur){
  let html="";
  for(let k=0;k<traits;k++){
    const R=86, r=21+k*1.7, d=48+k*2.3, pts=[];
    for(let t=0;t<=Math.PI*14;t+=0.045){
      const x=(R-r)*Math.cos(t)+d*Math.cos(((R-r)/r)*t);
      const y=(R-r)*Math.sin(t)-d*Math.sin(((R-r)/r)*t);
      pts.push((x*0.92).toFixed(1)+","+(y*0.92).toFixed(1));
    }
    html+=`<polyline points="${pts.join(" ")}" fill="none" stroke="${couleur}" stroke-width="0.55" opacity="${0.5+0.5*(k/traits)}"/>`;
  }
  svg.innerHTML=html;
}

/* ═══════════════ CHART.JS ═══════════════ */
Chart.defaults.font.family=getComputedStyle(document.documentElement).getPropertyValue("--f-corps");
Chart.defaults.color="#46544C";
// Activation du plugin de zoom/pan s'il a pu être chargé (CDN ou repli local).
const ZOOM_DISPO = !!window.ChartZoom;
if(ZOOM_DISPO){ try{ Chart.register(window.ChartZoom); }catch(e){ /* déjà enregistré */ } }
// Configuration commune : molette + pincement pour zoomer, glisser pour se
// déplacer, le tout borné aux données d'origine (pas de dérive dans le vide).
const ZOOM_CFG={
  limits:{x:{min:"original",max:"original"}},
  pan:{enabled:true, mode:"x"},
  zoom:{wheel:{enabled:true}, pinch:{enabled:true}, drag:{enabled:false}, mode:"x"},
};
function detruire(id){ if(graphes[id]){graphes[id].destroy(); delete graphes[id];} }
function ligne(idCanvas, libelles, series, opts={}){
  detruire(idCanvas);
  const ctx=document.getElementById(idCanvas);
  if(!ctx) return;
  // Pas de ronds par défaut : le point n'apparaît qu'au survol / au toucher,
  // pour faire ressortir la valeur sélectionnée sans surcharger la courbe.
  const base={
    responsive:true, maintainAspectRatio:false,
    interaction:{mode:"index",intersect:false},
    plugins:{
      legend:{display:series.length>1,labels:{boxWidth:10,boxHeight:10,usePointStyle:true}},
      tooltip:{callbacks:{label:c=>` ${c.dataset.label} : ${fmtN(c.parsed.y, opts.dec??0)}${opts.unite||""}`}},
      zoom: (opts.zoom===false ? undefined : ZOOM_CFG),
    },
    scales:{
      x:{grid:{display:false},ticks:{maxTicksLimit:9,font:{size:11}}},
      y:{grid:{color:"#E4E9E3"},border:{display:false},
         ticks:{font:{family:"IBM Plex Mono",size:11},callback:v=>fmtN(v,opts.dec??0)}},
    },
  };
  // Fusion peu profonde : une surcharge peut redéfinir legend/tooltip ou un axe
  // sans faire disparaître le zoom hérité de la base.
  const sur=opts.surcharge||{};
  const options={
    ...base, ...sur,
    plugins:{...base.plugins, ...(sur.plugins||{})},
    scales:{...base.scales, ...(sur.scales||{})},
    interaction:{...base.interaction, ...(sur.interaction||{})},
  };
  graphes[idCanvas]=new Chart(ctx,{
    type:"line",
    data:{labels:libelles, datasets:series.map((s,i)=>{
      const coul=s.couleur||PALETTE_SERIES[i%PALETTE_SERIES.length];
      return {
        label:s.nom, data:s.valeurs, borderColor:coul, backgroundColor:coul+"22",
        borderWidth:2,
        pointRadius:s.points??0, pointHoverRadius:s.points??5, pointHitRadius:14,
        pointBackgroundColor:coul, pointBorderColor:"#fff", pointBorderWidth:1.5,
        pointHoverBackgroundColor:coul, pointHoverBorderColor:"#fff", pointHoverBorderWidth:2,
        tension:0.38, cubicInterpolationMode:"monotone", fill:s.remplir??false, spanGaps:true,
      };
    })},
    options
  });
  // Double-clic = réinitialisation du zoom.
  if(ZOOM_DISPO && opts.zoom!==false)
    ctx.ondblclick=()=>{ const g=graphes[idCanvas]; if(g&&g.resetZoom) g.resetZoom(); };
}

function barresRecit(idCanvas, libelles, valeurs, opts={}){
  detruire(idCanvas);
  const ctx=document.getElementById(idCanvas);
  if(!ctx) return;
  const couleurs=valeurs.map((_,i)=>i===0?(opts.couleur||"#2E5E4E"):(opts.couleurSecondaire||"#C9A23F"));
  graphes[idCanvas]=new Chart(ctx,{
    type:"bar",
    data:{labels:libelles,datasets:[{label:opts.nom||"",data:valeurs,
      backgroundColor:couleurs,borderRadius:5,borderSkipped:false}]},
    options:{responsive:true,maintainAspectRatio:false,indexAxis:"y",animation:{duration:320},
      interaction:{mode:"nearest",intersect:false},
      plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>
        ` ${opts.formatter?opts.formatter(c.parsed.x):fmtN(c.parsed.x,opts.dec??0)}`}}},
      scales:{x:{beginAtZero:true,grid:{color:"#E4E9E3"},border:{display:false},
        ticks:{font:{family:"IBM Plex Mono",size:10},callback:v=>opts.axe?opts.axe(v):fmtN(v,opts.dec??0)}},
        y:{grid:{display:false},border:{display:false},ticks:{font:{weight:"600",size:11}}}}
    }
  });
}

/* ═══════════════ TRI DES TABLES ═══════════════ */
// Les <td> portent data-v (valeur brute) ; cliquer un <th class="tri"> trie le tbody.
function rendreTriable(conteneurSel){
  const table=document.querySelector(conteneurSel+" table");
  if(!table) return;
  const idT=conteneurSel;
  table.querySelectorAll("th").forEach((th,iCol)=>{
    th.classList.add("tri");
    th.tabIndex=0; th.setAttribute("role","button");
    const e=etatTri[idT];
    if(e && e.col===iCol){th.dataset.dir=e.dir;th.setAttribute("aria-sort",e.dir==="asc"?"ascending":"descending");}else th.setAttribute("aria-sort","none");
    const trier=()=>{
      const prev=etatTri[idT];
      const dir=(prev && prev.col===iCol && prev.dir==="desc")?"asc":"desc";
      etatTri[idT]={col:iCol,dir};
      trierTable(table,iCol,dir);
      table.querySelectorAll("th").forEach(t=>{delete t.dataset.dir;t.setAttribute("aria-sort","none")});
      th.dataset.dir=dir;th.setAttribute("aria-sort",dir==="asc"?"ascending":"descending");
    };
    th.addEventListener("click",trier);
    th.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();trier();}});
  });
  const e=etatTri[idT];
  if(e) trierTable(table,e.col,e.dir);
}
function trierTable(table,iCol,dir){
  const tbody=table.tBodies[0];
  const lignes=[...tbody.rows];
  const val=tr=>{
    const td=tr.cells[iCol]; if(!td) return null;
    if(td.dataset.v!==undefined && td.dataset.v!==""){
      const n=Number(td.dataset.v);
      return isNaN(n)? td.dataset.v : n;
    }
    return td.textContent.trim();
  };
  lignes.sort((a,b)=>{
    const va=val(a), vb=val(b);
    const na=va==null||va==="", nb=vb==null||vb==="";
    if(na&&nb) return 0; if(na) return 1; if(nb) return -1;   // vides en bas
    let c;
    if(typeof va==="number"&&typeof vb==="number") c=va-vb;
    else c=String(va).localeCompare(String(vb),"fr");
    return dir==="asc"?c:-c;
  });
  lignes.forEach(tr=>tbody.appendChild(tr));
}

/* ═══════════════ SEGMENTS DE PÉRIODE ═══════════════ */
const PERIODES=[["tout","Tout"],["3a","3 ans"],["1a","1 an"],["6m","6 mois"]];
function rendreSegments(conteneur, actif, onChoix){
  conteneur.innerHTML=PERIODES.map(([c,l])=>
    `<button data-p="${c}" aria-pressed="${c===actif}">${l}</button>`).join("");
  conteneur.querySelectorAll("button").forEach(b=>
    b.addEventListener("click",()=>onChoix(b.dataset.p)));
}

/* ═══════════════ ÉCHELLE DE TEMPS (granularité) ═══════════════ */
// J = journalier (brut), S = hebdomadaire, M = mensuel, A = annuel.
const GRANS=[["J","Jour"],["S","Semaine"],["M","Mois"],["A","Année"]];
const GRANS_RECIT=[["J","Jour"],["S","Semaine"],["M","Mois"],["T","Trimestre"],["A","Année"]];
function rendreGran(conteneur, actif, onChoix){
  if(!conteneur) return;
  conteneur.innerHTML=GRANS.map(([c,l])=>
    `<button data-g="${c}" aria-pressed="${c===actif}">${l}</button>`).join("");
  conteneur.querySelectorAll("button").forEach(b=>
    b.addEventListener("click",()=>onChoix(b.dataset.g)));
}
function rendreGranRecit(conteneur, actif, onChoix){
  if(!conteneur) return;
  conteneur.innerHTML=GRANS_RECIT.map(([c,l])=>
    `<button data-g="${c}" aria-pressed="${c===actif}">${traduireTexte(l)}</button>`).join("");
  conteneur.querySelectorAll("button").forEach(b=>
    b.addEventListener("click",()=>onChoix(b.dataset.g)));
}
function numSemaine(d){                  // numéro de semaine ISO, préfixé de l'année
  const t=new Date(Date.UTC(d.getFullYear(),d.getMonth(),d.getDate()));
  t.setUTCDate(t.getUTCDate()-((t.getUTCDay()+6)%7)+3);     // jeudi de la semaine
  const premier=new Date(Date.UTC(t.getUTCFullYear(),0,4));
  const sem=1+Math.round(((t-premier)/86400000-3+((premier.getUTCDay()+6)%7))/7);
  return t.getUTCFullYear()*100+sem;
}
function cleBucket(dateId, gran){        // clé de regroupement selon l'échelle
  const d=did2date(dateId); if(!d) return String(dateId);
  if(gran==="A") return "A"+d.getFullYear();
  if(gran==="T") return "T"+(d.getFullYear()*10+Math.floor(d.getMonth()/3)+1);
  if(gran==="M") return "M"+(d.getFullYear()*100+d.getMonth()+1);
  if(gran==="S") return "S"+numSemaine(d);
  return "J"+dateId;
}
// Regroupe des lignes (triées chronologiquement) par échelle de temps et
// renvoie, dans l'ordre, un tableau de groupes [{...}, ...] de lignes.
function agregerBuckets(lignes, cleDate, gran){
  if(gran==="J"||!gran) return lignes.map(r=>[r]);
  const ordre=[], parCle=new Map();
  for(const r of lignes){
    const k=cleBucket(r[cleDate],gran);
    if(!parCle.has(k)){ parCle.set(k,[]); ordre.push(k); }
    parCle.get(k).push(r);
  }
  return ordre.map(k=>parCle.get(k));
}
// Libellé d'axe adapté à l'échelle.
function libGran(dateId, gran){
  const d=did2date(dateId);
  if(gran==="A") return fmtDate(dateId,{year:"numeric"});
  if(gran==="T"&&d) return (LANG==="en"?"Q":"T")+(Math.floor(d.getMonth()/3)+1)+" "+d.getFullYear();
  if(gran==="M") return fmtDate(dateId,{month:"short",year:"2-digit"});
  return fmtDate(dateId,{day:"2-digit",month:"short",year:"2-digit"});   // J et S
}

/* ═══════════════ NAVIGATION ═══════════════ */
document.querySelectorAll("#nav button[data-vue]").forEach(b=>
  b.addEventListener("click",()=>allerVue(b.dataset.vue)));
document.querySelectorAll("[data-recitancre]").forEach(b=>b.addEventListener("click",()=>{
  const cible=document.getElementById(b.dataset.recitancre);
  if(cible) cible.scrollIntoView({behavior:"smooth",block:"start"});
}));
const VIEW_ROUTE={accueil:"home",apercu:"overview",recits:"stories",actions:"stocks",opcvm:"funds",indice:"index",radar:"radar",veille:"watchlist",donnees:"data"};
const ROUTE_VIEW={home:"accueil",overview:"apercu",stories:"recits",stocks:"actions",funds:"opcvm",index:"indice",radar:"radar",watchlist:"veille",data:"donnees",accueil:"accueil",apercu:"apercu",recits:"recits",actions:"actions",opcvm:"opcvm",indice:"indice",veille:"veille",donnees:"donnees",screener:"actions"};
function allerVue(v,{historique=true}={}){
  const changementVue=vueCourante!==v;
  vueCourante=v;
  document.querySelectorAll("#nav button").forEach(b=>
    b.setAttribute("aria-current", b.dataset.vue===v ? "true":"false"));
  document.querySelectorAll(".vue").forEach(s=>s.classList.remove("active"));
  $("#vue-"+v).classList.add("active");
  $("#actions-fiche").hidden=true; $("#actions-liste").hidden=false;
  $("#opcvm-fiche").hidden=true;  $("#opcvm-liste").hidden=false;
  rendre();
  if(changementVue) $("#main").scrollTop=0;
  if(historique&&changementVue){const u=new URL(location.href);u.pathname="/app";u.searchParams.set("view",VIEW_ROUTE[v]||v);history.pushState({view:v},"",u);}
}
window.addEventListener("popstate",()=>{if(D)allerVue(vueInitiale(),{historique:false})});

/* ═══════════════ CHARGEMENT AUTOMATIQUE ═══════════════ */
function ecranAmorce(html){ $("#amorce-corps").innerHTML=html; $("#amorce").style.display=""; }

function vueInitiale(){
  const raw=new URLSearchParams(location.search).get("view")||"home";
  const v=ROUTE_VIEW[raw]||"accueil";
  const canonical=VIEW_ROUTE[v]||"home";
  if(raw!==canonical){const u=new URL(location.href);u.pathname="/app";u.searchParams.set("view",canonical);history.replaceState({view:v},"",u)}
  return v;
}

async function chargerDonnees(silencieux=false){
  const btn=$("#btn-actualiser");
  btn.classList.add("tourne");
  try{
    const rep=await fetch("/api/public-data",{
      // Revalidation conditionnelle : le navigateur peut recevoir un léger 304
      // au lieu de retélécharger tout le JSON si les données n'ont pas changé.
      cache:"no-cache", headers:{Accept:"application/json"}
    });
    if(!rep.ok) throw new Error("HTTP "+rep.status);
    const payload=await rep.json();
    await window.BVMAC_ACCESS;
    const seancePrec=seanceApercu, vuePrec=vueCourante;
    D=preparerDonnees(payload);
    analysesCache.clear();
    seanceCourante = D.seances[D.seances.length-1];
    seanceApercu = (seancePrec && D.seances.includes(seancePrec))
      ? seancePrec : D.seances[D.seances.length-1];
    const modif=payload.meta?.updated_at;
    let freshness=document.getElementById('data-freshness');if(!freshness){freshness=document.createElement('p');freshness.id='data-freshness';freshness.className='release-note';freshness.setAttribute('role','status');document.querySelector('.platform-main').append(freshness);}
    const last=String(seanceCourante||'');const iso=last.length===8?`${last.slice(0,4)}-${last.slice(4,6)}-${last.slice(6,8)}`:'';
    const age=iso?Math.floor((Date.now()-Date.parse(iso+'T00:00:00Z'))/86400000):null;
    freshness.textContent=`Dernière séance disponible : ${iso||'non renseignée'}. Import : ${modif?new Date(modif).toLocaleString('fr-FR'):'non renseigné'}. Les valeurs liquidatives des OPCVM peuvent porter une autre date. Pas de cotations en temps réel.`+(age!==null&&age>7?' Attention : la dernière séance disponible remonte à plus de 7 jours.':'')+(payload.meta?.served_from_cache?' Source temporairement indisponible : affichage de la dernière copie disponible.':'');
    $("#etat-fichier").classList.add("ok");
    $("#etat-texte").textContent=`${D.seances.length} séance(s) · ${D.societes.length} sociétés · ${D.fonds.length} OPCVM`
      + (modif? ` · mis à jour le ${new Date(modif).toLocaleDateString(LANG==="en"?"en-GB":"fr-FR")}`:"");
    btn.hidden=false;
    $("#amorce").style.display="none";
    $("#recherche-g").hidden=false;
    compSelection=null; // recalculé sur les nouvelles données
    majSelecteurSeance(); majTicker(); majRechercheGlobale();
    allerVue(silencieux? vuePrec : vueInitiale(),{historique:false});
    const requestedCompany=new URLSearchParams(location.search).get("company");
    if(!silencieux&&requestedCompany&&vueCourante==="actions")ouvrirFicheSociete(+requestedCompany);
  }catch(err){
    if(D) { $("#etat-texte").textContent="Données momentanément indisponibles"; }
    else ecranAmorce(`
      <h1>Données indisponibles</h1>
      <p>Les données de marché n'ont pas pu être chargées pour le moment.</p>
      <div class="panneau">
        <h3>Que faire</h3>
        <p style="margin-top:0">Le chargement peut échouer si la connexion est interrompue
        ou si la source de données est temporairement inaccessible. Réessayer dans quelques instants
        résout généralement le problème.</p>
        <button class="btn-p" data-action="retry-data">Réessayer</button>
      </div>`);
  }finally{ btn.classList.remove("tourne"); }
}
$("#btn-actualiser").addEventListener("click",()=>chargerDonnees(true));

/* ═══════════════ RECHERCHE GLOBALE ═══════════════ */
let rgIndex=[];
function majRechercheGlobale(){
  rgIndex=[
    ...D.societes.map(s=>({type:"soc",id:s.company_id,
      titre:s.ticker+" — "+s.short_name, sous:s.full_name, cle:(s.ticker+" "+s.short_name+" "+s.full_name).toLowerCase()})),
    ...D.fonds.map(f=>({type:"fond",id:f.fund_id,
      titre:f.fund_name, sous:(f.manager||"")+" · "+(CAT_LIB[f.category]||""), cle:(f.fund_name+" "+(f.manager||"")).toLowerCase()})),
  ];
}
const rgInput=$("#rg-input"), rgListe=$("#resultats-g");
rgInput.addEventListener("input",()=>{
  const q=rgInput.value.trim().toLowerCase();
  if(!q){ rgListe.classList.remove("ouvert"); rgInput.setAttribute("aria-expanded","false"); return; }
  const res=rgIndex.filter(e=>e.cle.includes(q)).slice(0,8);
  rgListe.innerHTML=res.length? res.map(e=>
    `<button data-type="${e.type}" data-id="${e.id}" role="option">
       <b>${e.titre}</b><span>${e.sous}</span></button>`).join("")
    : `<button disabled><span>Aucun résultat pour « ${rgInput.value} »</span></button>`;
  rgListe.classList.add("ouvert");
  rgInput.setAttribute("aria-expanded","true");
  rgListe.querySelectorAll("button[data-id]").forEach(b=>
    b.addEventListener("click",()=>{
      rgListe.classList.remove("ouvert"); rgInput.value="";
      if(b.dataset.type==="soc"){ allerVue("actions"); ouvrirFicheSociete(+b.dataset.id); }
      else { allerVue("opcvm"); ouvrirFicheFonds(+b.dataset.id); }
    }));
});
document.addEventListener("click",e=>{
  if(!$("#recherche-g").contains(e.target)) rgListe.classList.remove("ouvert");
});
rgInput.addEventListener("keydown",e=>{
  if(e.key==="Enter"){ const p=rgListe.querySelector("button[data-id]"); if(p) p.click(); }
  if(e.key==="Escape"){ rgListe.classList.remove("ouvert"); }
});

/* ═══════════════ SÉLECTEUR DE SÉANCE ═══════════════ */
function majSelecteurSeance(){
  const sel=$("#se-select");
  sel.innerHTML=D.seances.map(s=>
    `<option value="${s}">${fmtDate(s,{day:"2-digit",month:"2-digit",year:"numeric"})}</option>`).join("");
  sel.value=seanceApercu;
  sel.onchange=()=>{seanceApercu=+sel.value; rendre();};
  const idx=()=>D.seances.indexOf(seanceApercu);
  $("#se-prec").onclick=()=>{ if(idx()>0){seanceApercu=D.seances[idx()-1]; sel.value=seanceApercu; rendre();} };
  $("#se-suiv").onclick=()=>{ if(idx()<D.seances.length-1){seanceApercu=D.seances[idx()+1]; sel.value=seanceApercu; rendre();} };
  majBoutonsSeance();
}
function majBoutonsSeance(){
  const i=D.seances.indexOf(seanceApercu);
  $("#se-prec").disabled=i<=0;
  $("#se-suiv").disabled=i>=D.seances.length-1;
}

/* ═══════════════ TICKER ═══════════════ */
function majTicker(){
  if(!D.indice.length){ $("#tick").hidden=true; return; }
  $("#tick").hidden=false;
  const dern=D.indice[D.indice.length-1];
  $("#tick-val").textContent=fmtN(dern.index_value,2);
  $("#tick-var").textContent=fmtPct(dern.variation_day_pct);
  $("#tick-var").className="var "+clsVar(dern.variation_day_pct);
  detruire("tick-spark");
  const ptsTick=agregerBuckets(D.indice,"date_id","M").map(g=>g[g.length-1]);  // échelle mensuelle
  graphes["tick-spark"]=new Chart($("#tick-spark"),{
    type:"line",
    data:{labels:ptsTick.map(r=>r.date_id),
          datasets:[{data:ptsTick.map(r=>r.index_value),
            borderColor:"#C9A23F",borderWidth:1.6,pointRadius:0,
            tension:0.38,cubicInterpolationMode:"monotone",pointHoverRadius:4,pointHitRadius:10,fill:false}]},
    options:{responsive:true,maintainAspectRatio:false,resizeDelay:80,
      animation:false,layout:{padding:0},
      interaction:{mode:"nearest",intersect:false},plugins:{legend:{display:false},tooltip:{enabled:true,callbacks:{label:c=>" BVMAC-AS : "+fmtN(c.parsed.y,2)}}},
      scales:{x:{display:false},y:{display:false}}}
  });
}

/* ═══════════════ RENDU ═══════════════ */
function rendre(){
  $("#seance").hidden=vueCourante!=="apercu" || !D;
  const vueEl=$("#vue-"+vueCourante);
  if(vueCourante==="accueil"){ if(vueEl) appliquerLangueDOM(vueEl); return; }
  if(!D) return;
  majBoutonsSeance();
  const fn=({apercu:rendreApercu, actions:rendreActions, opcvm:rendreOPCVM,
    recits:rendreRecits, indice:rendreIndice, radar:()=>window.BVMACRadar?.render(), veille:rendreVeille,
    donnees:rendreDonnees, qualite:rendreQualite})[vueCourante];
  if(fn) fn();
  if(vueEl) appliquerLangueDOM(vueEl);
}

/* ─────────── APERÇU ─────────── */
function miniMouvements(id,lignes,metrique="variation_pct"){
  const el=$(id);
  if(!lignes.length){el.innerHTML=`<div class="vide">Aucune donnée pour cette séance.</div>`;return;}
  el.innerHTML=`<div class="liste-mouvements">${lignes.map(r=>{
    const s=D.socParId[r.company_id]; if(!s)return "";
    const val=metrique==="value_traded"?(r.value_traded?compactFCFA(r.value_traded)+" FCFA":"—"):fmtPct(r.variation_pct);
    return `<div class="mouvement" data-soc="${ech(r.company_id)}"><div><b>${ech(s.ticker)} · ${ech(s.short_name)}</b><small>${fmtN(r.close_price)} FCFA</small></div><div class="mv-val ${clsVar(r.variation_pct)}">${val}</div></div>`;
  }).join("")}</div>`;
  el.querySelectorAll("[data-soc]").forEach(x=>x.addEventListener("click",()=>{allerVue("actions");ouvrirFicheSociete(+x.dataset.soc);}));
}
function couleurVariation(v){
  const n=Math.max(-5,Math.min(5,Number(v)||0)), force=Math.abs(n)/5;
  return n>0?`rgb(${Math.round(65-20*force)},${Math.round(135+25*force)},${Math.round(91+15*force)})`:n<0?`rgb(${Math.round(170+20*force)},${Math.round(85-18*force)},${Math.round(65-12*force)})`:"#75827A";
}
function rendreApercu(){
  const prix=D.prixParSeance[seanceApercu]||[];
  const caps=D.capParSeance[seanceApercu]||[];
  const idx=D.indiceParDate[seanceApercu];

  const hero=$("#hero"), col=hero.querySelector(".col-ind");
  dessinerGuilloche(hero.querySelector(".guill"), 9, "#E8D9A8");
  if(idx){
    hero.classList.remove("sans-indice");
    col.innerHTML=`<div class="eti">BVMAC All Share Index — séance du ${fmtDate(seanceApercu)}</div>
      <div class="ind">${fmtN(idx.index_value,2)} <small>pts</small></div>
      <div class="varj" style="color:${idx.variation_day_pct>0?"#7FD6A5":idx.variation_day_pct<0?"#F0A28E":"#BBD0C3"}">${fmtPct(idx.variation_day_pct)} sur la séance</div>`;
    const serie=D.indice.filter(r=>r.date_id<=seanceApercu);
    const pts=agregerBuckets(serie,"date_id","M").map(g=>g[g.length-1]);  // échelle mensuelle
    ligne("hero-graphe", pts.map(r=>fmtDate(r.date_id,{month:"short",year:"2-digit"})),
      [{nom:"BVMAC-AS",valeurs:pts.map(r=>r.index_value),couleur:"#C9A23F",points:0,remplir:true}],
      {dec:1,zoom:false,surcharge:{plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>" "+fmtN(c.parsed.y,2)+" pts"}}},
        scales:{x:{display:false},y:{grid:{color:"rgba(255,255,255,.12)"},border:{display:false},
          ticks:{color:"#BBD0C3",font:{family:"IBM Plex Mono",size:10},maxTicksLimit:4}}}}});
  }else{
    hero.classList.add("sans-indice");
    col.innerHTML=`<div class="eti">BVMAC All Share Index</div>
      <div class="vide-indice">L'indice n'est pas publié dans le bulletin de cette séance
      (première publication fin 2023). Choisissez une séance plus récente pour le consulter.</div>`;
    detruire("hero-graphe");
  }

  const volT=prix.reduce((s,r)=>s+(r.vol_traded||0),0);
  const valT=prix.reduce((s,r)=>s+(r.value_traded||0),0);
  const nbT =prix.reduce((s,r)=>s+(r.num_transactions||0),0);
  const capT=caps.reduce((s,r)=>s+(r.total_market_cap||0),0);
  $("#stats-seance").innerHTML=`
    <div class="stat"><div class="l">Capitalisation totale</div>
      <div class="v">${capT?compactFCFA(capT):"—"} <small>${capT?"FCFA":""}</small></div></div>
    <div class="stat"><div class="l">Volume échangé</div>
      <div class="v">${fmtN(volT)} <small>titres</small></div></div>
    <div class="stat"><div class="l">Valeur échangée</div>
      <div class="v">${valT?compactFCFA(valT):"0"} <small>FCFA</small></div></div>
    <div class="stat"><div class="l">Transactions</div>
      <div class="v">${fmtN(nbT)}</div></div>`;

  const hausses=prix.filter(r=>(r.variation_pct||0)>0).length;
  const baisses=prix.filter(r=>(r.variation_pct||0)<0).length;
  const stables=prix.length-hausses-baisses, total=Math.max(prix.length,1);
  $("#largeur-marche").innerHTML=`<div class="largeur-marche"><b class="pos">${hausses} hausse${hausses>1?"s":""}</b><span>·</span><b>${stables} stable${stables>1?"s":""}</b><span>·</span><b class="neg">${baisses} baisse${baisses>1?"s":""}</b><div class="barre" title="${hausses} / ${stables} / ${baisses}"><span class="h" style="width:${hausses/total*100}%"></span><span class="b" style="width:${baisses/total*100}%"></span></div></div>`;
  miniMouvements("#plus-actives",[...prix].sort((a,b)=>(b.value_traded||0)-(a.value_traded||0)).slice(0,4),"value_traded");
  miniMouvements("#plus-hausses",prix.filter(r=>(r.variation_pct||0)>0).sort((a,b)=>b.variation_pct-a.variation_pct).slice(0,4));
  miniMouvements("#plus-baisses",prix.filter(r=>(r.variation_pct||0)<0).sort((a,b)=>a.variation_pct-b.variation_pct).slice(0,4));
  $("#carte-marche").innerHTML=prix.map(r=>{const s=D.socParId[r.company_id];return s?`<button class="case-chaleur" data-soc="${ech(r.company_id)}" style="background:${couleurVariation(r.variation_pct)}"><b>${ech(s.ticker)}</b><span>${fmtPct(r.variation_pct)}</span><small>${fmtN(r.close_price)} F</small></button>`:"";}).join("");
  $("#carte-marche").querySelectorAll("[data-soc]").forEach(x=>x.addEventListener("click",()=>{allerVue("actions");ouvrirFicheSociete(+x.dataset.soc);}));

  $("#note-actions").textContent="Séance du "+fmtDate(seanceApercu)+" — cliquez sur les en-têtes pour trier";
  $("#table-apercu").innerHTML=tableActions(prix, caps, true);
  brancherLignesSociete("#table-apercu");
  rendreTriable("#table-apercu");

  const vls=D.vlParBulletin[seanceApercu]||[];
  if(vls.length){
    const meilleurs=[...vls].sort((a,b)=>(b.var_inception_pct??-1e9)-(a.var_inception_pct??-1e9)).slice(0,5);
    $("#note-opcvm-ap").textContent=vls.length+" fonds valorisés dans ce bulletin";
    $("#opcvm-apercu").innerHTML=`<table><thead><tr>
      <th>Fonds</th><th>Cat.</th><th>VL</th><th>Var. origine</th><th>Var. préc.</th>
      </tr></thead><tbody>${meilleurs.map(r=>{const f=D.fondParId[r.fund_id];return `
      <tr class="cliquable" data-fond="${r.fund_id}">
        <td data-v="${f.fund_name}"><div class="tick-nom"><b>${f.fund_name}</b><span>${f.manager||""}</span></div></td>
        <td data-v="${f.category||""}"><span class="cat" title="${CAT_LIB[f.category]||""}">${f.category||"—"}</span></td>
        <td class="num" data-v="${r.nav??""}">${fmtN(r.nav,2)}</td>
        <td class="num ${clsVar(r.var_inception_pct)}" data-v="${r.var_inception_pct??""}">${fmtPct(r.var_inception_pct)}</td>
        <td class="num ${clsVar(r.var_prev_pct)}" data-v="${r.var_prev_pct??""}">${fmtPct(r.var_prev_pct)}</td></tr>`;}).join("")}</tbody></table>`;
    $("#opcvm-apercu").querySelectorAll("tr.cliquable").forEach(tr=>{
      tr.tabIndex=0;tr.setAttribute("role","link");
      const ouvrir=()=>{allerVue("opcvm"); ouvrirFicheFonds(+tr.dataset.fond);};
      tr.addEventListener("click",ouvrir);tr.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();ouvrir();}});
    });
    rendreTriable("#opcvm-apercu");
  }else{
    $("#note-opcvm-ap").textContent="";
    $("#opcvm-apercu").innerHTML=`<div class="vide">Pas de section OPCVM dans ce bulletin —
      publiée par la BVMAC depuis courant 2023.</div>`;
  }
}

/* ─────────── HISTOIRES DU MARCHÉ ─────────── */
function txtRecit(fr,en){ return LANG==="en"?en:fr; }
function formatCourtRecit(v){
  if(v==null||!Number.isFinite(Number(v))) return "—";
  const n=Number(v), a=Math.abs(n);
  if(a>=1e12) return fmtN(n/1e12,2)+" T";
  if(a>=1e9) return fmtN(n/1e9,2)+" "+txtRecit("Mds","bn");
  if(a>=1e6) return fmtN(n/1e6,1)+" M";
  if(a>=1e3) return fmtN(n/1e3,1)+" k";
  return fmtN(n);
}
function dateIdDepuisISO(v){
  if(!v) return null;
  const m=String(v).match(/(\d{4})[-/]?(\d{2})[-/]?(\d{2})/);
  return m?Number(m[1]+m[2]+m[3]):null;
}
function boutonSocieteRecit(s){
  return s?`<button class="recit-action" data-soc-recit="${ech(s.company_id)}">${ech(s.ticker)}</button>`:"—";
}
function brancherSocietesRecit(racine="#vue-recits"){
  document.querySelectorAll(racine+" [data-soc-recit]").forEach(b=>b.addEventListener("click",()=>{
    allerVue("actions"); ouvrirFicheSociete(+b.dataset.socRecit);
  }));
}
function statRecit(label,valeur,unite=""){
  return `<div class="stat"><div class="l">${label}</div><div class="v">${valeur}${unite?` <small>${unite}</small>`:""}</div></div>`;
}
function dateIdDepuisDate(d){
  return d.getFullYear()*10000+(d.getMonth()+1)*100+d.getDate();
}
function contextePeriodeRecit(){
  const fin=did2date(seanceCourante), debut=new Date(fin);
  if(recitPeriode==="S") debut.setDate(debut.getDate()-((debut.getDay()+6)%7));
  if(recitPeriode==="M") debut.setDate(1);
  if(recitPeriode==="T"){ debut.setMonth(Math.floor(debut.getMonth()/3)*3,1); }
  if(recitPeriode==="A") debut.setMonth(0,1);
  const debutId=dateIdDepuisDate(debut);
  const seances=D.seances.filter(d=>d>=debutId&&d<=seanceCourante);
  const trimestre=Math.floor(fin.getMonth()/3)+1;
  const noms={J:txtRecit("Jour","Day"),S:txtRecit("Semaine","Week"),M:txtRecit("Mois","Month"),T:txtRecit("Trimestre","Quarter"),A:txtRecit("Année","Year")};
  const titres={J:txtRecit("La séance racontée","The session explained"),S:txtRecit("La semaine racontée","The week explained"),M:txtRecit("Le mois raconté","The month explained"),T:txtRecit("Le trimestre raconté","The quarter explained"),A:txtRecit("L’année racontée","The year explained")};
  let libelle;
  if(recitPeriode==="J") libelle=txtRecit("Jour du ","Day of ")+fmtDate(seanceCourante);
  else if(recitPeriode==="S") libelle=txtRecit("Semaine du ","Week from ")+fmtDate(debutId)+txtRecit(" au "," to ")+fmtDate(seanceCourante);
  else if(recitPeriode==="M") libelle=txtRecit("Mois de ","Month of ")+fmtDate(seanceCourante,{month:"long",year:"numeric"});
  else if(recitPeriode==="T") libelle=txtRecit(`Trimestre ${trimestre} · ${fin.getFullYear()}`,`Quarter ${trimestre} · ${fin.getFullYear()}`);
  else libelle=txtRecit("Année ","Year ")+fin.getFullYear();
  return {debutId,seances,set:new Set(seances),libelle,nom:noms[recitPeriode],titre:titres[recitPeriode]};
}
function agregerPrixRecit(contexte){
  const parSoc=new Map();
  for(const r of D.prix){
    if(!contexte.set.has(r.bulletin_date_id)) continue;
    if(!parSoc.has(r.company_id)) parSoc.set(r.company_id,[]);
    parSoc.get(r.company_id).push(r);
  }
  return [...parSoc.entries()].map(([company_id,lignes])=>{
    lignes.sort((a,b)=>a.bulletin_date_id-b.bulletin_date_id);
    const premier=lignes[0], dernier=lignes.at(-1);
    let variation=Number(dernier.variation_pct);
    if(lignes.length>1&&Number(premier.close_price)>0&&Number(dernier.close_price)>0)
      variation=(Number(dernier.close_price)/Number(premier.close_price)-1)*100;
    return {company_id,variation_pct:Number.isFinite(variation)?variation:null,
      close_price:dernier.close_price,
      value_traded:lignes.reduce((s,r)=>s+(Number(r.value_traded)||0),0),
      vol_traded:lignes.reduce((s,r)=>s+(Number(r.vol_traded)||0),0),
      num_transactions:lignes.reduce((s,r)=>s+(Number(r.num_transactions)||0),0),
      jours_actifs:lignes.filter(r=>(Number(r.value_traded)||0)>0||(Number(r.vol_traded)||0)>0||(Number(r.num_transactions)||0)>0).length,
      observations:lignes.length};
  });
}
function rendreRecits(){
  const contexte=contextePeriodeRecit();
  rendreGranRecit($("#recit-gran"),recitPeriode,g=>{
    recitPeriode=g; PREFSET("bvmac_pref_story_range",recitPeriode); rendreRecits(); appliquerLangueDOM($("#vue-recits"));
  });
  $("#recit-periode-libelle").textContent=`${contexte.libelle} · ${contexte.seances.length} ${txtRecit("séance(s) disponible(s)","available session(s)")}`;
  $("#recit-periode-titre").textContent=contexte.titre;
  $("#recit-periode-note").textContent=txtRecit(
    `synthèse de ${contexte.seances.length} séance(s) disponible(s)`,
    `summary of ${contexte.seances.length} available session(s)`);
  $("#recit-liquidite-note").textContent=txtRecit(
    `activité observée sur ${contexte.seances.length} séance(s) de la période`,
    `activity observed across ${contexte.seances.length} session(s) in the period`);

  const prix=agregerPrixRecit(contexte);
  const indicesPeriode=D.indice.filter(r=>contexte.set.has(r.date_id)&&Number.isFinite(Number(r.index_value)));
  const idx=indicesPeriode.at(-1)||null;
  let variationIndice=idx?Number(idx.variation_day_pct):null;
  if(indicesPeriode.length>1&&Number(indicesPeriode[0].index_value)>0)
    variationIndice=(Number(indicesPeriode.at(-1).index_value)/Number(indicesPeriode[0].index_value)-1)*100;
  const actifs=prix.filter(r=>(Number(r.value_traded)||0)>0||(Number(r.vol_traded)||0)>0||(Number(r.num_transactions)||0)>0);
  const valeurTotale=prix.reduce((s,r)=>s+(Number(r.value_traded)||0),0);
  const volumeTotal=prix.reduce((s,r)=>s+(Number(r.vol_traded)||0),0);
  const transactions=prix.reduce((s,r)=>s+(Number(r.num_transactions)||0),0);
  const classesValeur=[...prix].sort((a,b)=>(Number(b.value_traded)||0)-(Number(a.value_traded)||0));
  const leader=classesValeur[0]||null, socLeader=leader?D.socParId[leader.company_id]:null;
  const concentration=leader&&valeurTotale>0?(Number(leader.value_traded)||0)/valeurTotale*100:null;
  const mouvements=prix.filter(r=>Number.isFinite(Number(r.variation_pct)))
    .sort((a,b)=>Math.abs(Number(b.variation_pct))-Math.abs(Number(a.variation_pct)));
  const mouvement=mouvements[0]||null, socMouvement=mouvement?D.socParId[mouvement.company_id]:null;

  dessinerGuilloche($("#recit-hero .guill"),9,"#E8D9A8");
  const ratioActif=prix.length?actifs.length/prix.length*100:0;
  $("#recit-hero-contenu").innerHTML=valeurTotale>0?`
    <div class="recit-kicker">${txtRecit("Le chiffre à retenir","The key figure")} · ${contexte.libelle}</div>
    <div class="recit-chiffre">${fmtN(concentration,1)} %</div>
    <div class="recit-accroche">${txtRecit(
      `${socLeader?ech(socLeader.ticker):"Une valeur"} a concentré cette part de la valeur échangée pendant la période analysée.`,
      `${socLeader?ech(socLeader.ticker):"One security"} accounted for this share of traded value over the analysed period.`)}</div>
    <div class="recit-source">${txtRecit("Source : bulletins officiels de la BVMAC. Données de fin de séance.","Source: official BVMAC bulletins. End-of-session data.")}</div>`:`
    <div class="recit-kicker">${txtRecit("Le chiffre à retenir","The key figure")} · ${contexte.libelle}</div>
    <div class="recit-chiffre">${fmtN(ratioActif,0)} %</div>
    <div class="recit-accroche">${txtRecit(
      `${actifs.length} valeur${actifs.length>1?"s":""} sur ${prix.length} ont enregistré une activité mesurable pendant la période.`,
      `${actifs.length} of ${prix.length} securities recorded measurable trading activity during the period.`)}</div>
    <div class="recit-source">${txtRecit("Source : bulletins officiels de la BVMAC. Données de fin de séance.","Source: official BVMAC bulletins. End-of-session data.")}</div>`;

  const hausses=prix.filter(r=>Number(r.variation_pct)>0).length;
  const baisses=prix.filter(r=>Number(r.variation_pct)<0).length;
  const stables=prix.length-hausses-baisses;
  const sens=hausses>baisses?txtRecit("positive","positive"):baisses>hausses?txtRecit("négative","negative"):txtRecit("équilibrée","balanced");
  $("#recit-seance-texte").innerHTML=`
    <p>${txtRecit(
      `<strong>${contexte.libelle}</strong> présente une largeur de marché <strong>${sens}</strong> : ${hausses} hausse${hausses>1?"s":""}, ${baisses} baisse${baisses>1?"s":""} et ${stables} valeur${stables>1?"s":""} stable${stables>1?"s":""}.`,
      `<strong>${contexte.libelle}</strong> shows <strong>${sens}</strong> market breadth: ${hausses} advancer${hausses===1?"":"s"}, ${baisses} decliner${baisses===1?"":"s"} and ${stables} unchanged securit${stables===1?"y":"ies"}.`)}</p>
    ${socLeader?`<p>${txtRecit(
      `${boutonSocieteRecit(socLeader)} arrive en tête des échanges avec <strong>${formatCourtRecit(Number(leader.value_traded)||0)} FCFA</strong>.`,
      `${boutonSocieteRecit(socLeader)} led trading with <strong>${formatCourtRecit(Number(leader.value_traded)||0)} FCFA</strong>.`)}</p>`:""}
    ${socMouvement?`<p class="recit-phrase-cle">${txtRecit(
      `Le mouvement de cours le plus marqué revient à ${boutonSocieteRecit(socMouvement)} : <strong>${fmtPct(Number(mouvement.variation_pct))}</strong>.`,
      `The largest price move was recorded by ${boutonSocieteRecit(socMouvement)}: <strong>${fmtPct(Number(mouvement.variation_pct))}</strong>.`)}</p>`:""}
    ${idx?`<p>${txtRecit(
      `L’indice BVMAC-AS termine à <strong>${fmtN(idx.index_value,2)} points</strong>, soit ${fmtPct(variationIndice)} sur la période.`,
      `The BVMAC-AS index closed at <strong>${fmtN(idx.index_value,2)} points</strong>, ${fmtPct(variationIndice)} over the period.`)}</p>`:""}`;
  $("#recit-seance-stats").innerHTML=
    statRecit(txtRecit("Valeur échangée","Traded value"),formatCourtRecit(valeurTotale),"FCFA")+
    statRecit(txtRecit("Volume échangé","Trading volume"),fmtN(volumeTotal),txtRecit("titres","shares"))+
    statRecit(txtRecit("Transactions","Transactions"),fmtN(transactions))+
    statRecit(txtRecit("Valeurs actives","Active securities"),`${actifs.length} / ${prix.length}`);

  const metriqueValeur=classesValeur.some(r=>(Number(r.value_traded)||0)>0);
  const topSeance=(metriqueValeur?classesValeur:[...prix].sort((a,b)=>(Number(b.vol_traded)||0)-(Number(a.vol_traded)||0))).slice(0,7);
  barresRecit("recit-seance-graphe",topSeance.map(r=>D.socParId[r.company_id]?.ticker||"—"),
    topSeance.map(r=>metriqueValeur?(Number(r.value_traded)||0):(Number(r.vol_traded)||0)),{
      nom:metriqueValeur?txtRecit("Valeur échangée","Traded value"):txtRecit("Volume","Volume"),
      formatter:v=>metriqueValeur?formatCourtRecit(v)+" FCFA":fmtN(v),axe:v=>metriqueValeur?formatCourtRecit(v):fmtN(v)});
  $("#recit-seance-legende").textContent=metriqueValeur?
    txtRecit("Classement par valeur échangée cumulée pendant la période.","Ranking by cumulative traded value over the period."):
    txtRecit("Aucune valeur échangée déclarée : classement de repli par volume cumulé.","No traded value reported: fallback ranking by cumulative volume.");

  const caps=[];
  for(const [date,lignes] of Object.entries(D.capParSeance)){
    const dateId=Number(date); if(dateId>seanceCourante) continue;
    const total=lignes.reduce((s,r)=>s+(Number(r.total_market_cap)||0),0);
    if(total>0) caps.push({date_id:dateId,total});
  }
  caps.sort((a,b)=>a.date_id-b.date_id);
  const capAgregees=agregerBuckets(caps,"date_id",recitPeriode).map(g=>g[g.length-1]);
  const capDebut=caps[0], capFin=caps.at(-1);
  const variationCap=capDebut?.total&&capFin?.total?(capFin.total/capDebut.total-1)*100:null;
  const indices=D.indice.filter(r=>r.date_id<=seanceCourante&&Number.isFinite(Number(r.index_value)));
  const dernierIndice=indices.at(-1), recordIndice=indices.length?[...indices].sort((a,b)=>Number(b.index_value)-Number(a.index_value))[0]:null;
  const jalons=D.societes.map(s=>{
    const dateOfficielle=dateIdDepuisISO(s.listing_date);
    const premiere=D.prix.filter(r=>r.company_id===s.company_id).sort((a,b)=>a.bulletin_date_id-b.bulletin_date_id)[0]?.bulletin_date_id;
    return {s,date_id:dateOfficielle||premiere,officiel:!!dateOfficielle};
  }).filter(x=>x.date_id&&x.date_id<=seanceCourante).sort((a,b)=>a.date_id-b.date_id);
  $("#recit-histoire-texte").innerHTML=`
    <p>${txtRecit(
      `Entre <strong>${capDebut?fmtDate(capDebut.date_id):"—"}</strong> et <strong>${capFin?fmtDate(capFin.date_id):"—"}</strong>, la capitalisation observée est passée de <strong>${capDebut?formatCourtRecit(capDebut.total):"—"} FCFA</strong> à <strong>${capFin?formatCourtRecit(capFin.total):"—"} FCFA</strong>.`,
      `Between <strong>${capDebut?fmtDate(capDebut.date_id):"—"}</strong> and <strong>${capFin?fmtDate(capFin.date_id):"—"}</strong>, observed market capitalisation moved from <strong>${capDebut?formatCourtRecit(capDebut.total):"—"} FCFA</strong> to <strong>${capFin?formatCourtRecit(capFin.total):"—"} FCFA</strong>.`)}</p>
    ${variationCap!=null?`<p class="recit-phrase-cle">${txtRecit(
      `Cela représente une évolution cumulée de <strong>${fmtPct(variationCap)}</strong> sur la période couverte.`,
      `This represents a cumulative change of <strong>${fmtPct(variationCap)}</strong> over the covered period.`)}</p>`:""}
    ${dernierIndice?`<p>${txtRecit(
      `Depuis son apparition dans les données, l’indice BVMAC-AS atteint un plus haut observé de <strong>${fmtN(recordIndice.index_value,2)} points</strong> le ${fmtDate(recordIndice.date_id)}. À la date sélectionnée, sa dernière valeur disponible est de <strong>${fmtN(dernierIndice.index_value,2)} points</strong>.`,
      `Since first appearing in the dataset, the BVMAC-AS index reached an observed high of <strong>${fmtN(recordIndice.index_value,2)} points</strong> on ${fmtDate(recordIndice.date_id)}. At the selected date, its latest available value is <strong>${fmtN(dernierIndice.index_value,2)} points</strong>.`)}</p>`:""}`;
  $("#recit-frise").innerHTML=jalons.slice(-6).map(x=>`<div class="recit-jalon"><time>${fmtDate(x.date_id,{month:"short",year:"numeric"})}</time><b>${boutonSocieteRecit(x.s)} · ${ech(x.s.short_name||"")}</b><span>${x.officiel?txtRecit("date d’admission renseignée","reported listing date"):txtRecit("première cotation disponible","first available quote")}</span></div>`).join("");
  if(capAgregees.length){
    ligne("recit-histoire-graphe",capAgregees.map(r=>libGran(r.date_id,recitPeriode)),
      [{nom:txtRecit("Capitalisation","Market capitalisation"),valeurs:capAgregees.map(r=>r.total/1e9),couleur:"#2E5E4E",points:0,remplir:true}],
      {dec:0,unite:" "+txtRecit("Mds FCFA","bn FCFA"),zoom:false});
  }else detruire("recit-histoire-graphe");
  $("#recit-histoire-legende").textContent=txtRecit(
    `Capitalisation totale de fin de séance, au dernier point disponible de chaque ${contexte.nom.toLowerCase()}.`,
    `End-of-session total market capitalisation, sampled at the latest available point of each ${contexte.nom.toLowerCase()}.`);

  const fenetre=contexte.seances, fenetreSet=contexte.set;
  const liquidite=D.societes.map(s=>{
    const observations=D.prix.filter(r=>r.company_id===s.company_id&&fenetreSet.has(r.bulletin_date_id));
    const joursActifs=observations.filter(r=>(Number(r.value_traded)||0)>0||(Number(r.vol_traded)||0)>0||(Number(r.num_transactions)||0)>0).length;
    return {s,joursActifs,frequence:fenetre.length?joursActifs/fenetre.length*100:0,
      valeur:observations.reduce((n,r)=>n+(Number(r.value_traded)||0),0),observations:observations.length};
  }).filter(x=>x.observations).sort((a,b)=>b.frequence-a.frequence||b.valeur-a.valeur);
  const plusReguliere=liquidite[0], moinsReguliere=liquidite.at(-1);
  const valeurFenetre=liquidite.reduce((s,x)=>s+x.valeur,0);
  const concentree=valeurFenetre&&liquidite[0]?liquidite[0].valeur/valeurFenetre*100:null;
  $("#recit-liquidite-texte").innerHTML=liquidite.length?`
    <p>${txtRecit(
      `Pendant <strong>${contexte.libelle.toLowerCase()}</strong>, ${boutonSocieteRecit(plusReguliere.s)} a présenté l’activité la plus régulière : des échanges ont été observés pendant <strong>${plusReguliere.joursActifs} séance${plusReguliere.joursActifs>1?"s":""}</strong> sur ${fenetre.length}, soit ${fmtN(plusReguliere.frequence,1)} %.`,
      `During <strong>${contexte.libelle.toLowerCase()}</strong>, ${boutonSocieteRecit(plusReguliere.s)} traded most consistently: activity was observed in <strong>${plusReguliere.joursActifs} session${plusReguliere.joursActifs===1?"":"s"}</strong> out of ${fenetre.length}, or ${fmtN(plusReguliere.frequence,1)}%.`)}</p>
    <p>${txtRecit(
      `À l’autre extrémité, ${boutonSocieteRecit(moinsReguliere.s)} affiche une fréquence de ${fmtN(moinsReguliere.frequence,1)} %. Une absence d’échange ne signifie pas nécessairement une absence d’intérêt : elle mesure seulement ce qui est effectivement déclaré dans les bulletins.`,
      `At the other end, ${boutonSocieteRecit(moinsReguliere.s)} shows a ${fmtN(moinsReguliere.frequence,1)}% frequency. No recorded trade does not necessarily mean no investor interest; it only measures activity actually reported in the bulletins.`)}</p>
    ${concentree!=null?`<p class="recit-phrase-cle">${txtRecit(
      `La valeur la plus échangée concentre <strong>${fmtN(concentree,1)} %</strong> de la valeur cumulée sur cette période.`,
      `The most traded security accounts for <strong>${fmtN(concentree,1)}%</strong> of cumulative traded value over this period.`)}</p>`:""}`:`<div class="vide">${txtRecit("Données de liquidité indisponibles.","Liquidity data unavailable.")}</div>`;
  $("#recit-liquidite-stats").innerHTML=
    statRecit(txtRecit("Séances analysées","Sessions analysed"),fmtN(fenetre.length))+
    statRecit(txtRecit("Valeur échangée cumulée","Cumulative traded value"),formatCourtRecit(valeurFenetre),"FCFA")+
    statRecit(txtRecit("Valeur la plus régulière","Most consistent security"),plusReguliere?ech(plusReguliere.s.ticker):"—")+
    statRecit(txtRecit("Concentration des échanges","Trading concentration"),concentree!=null?fmtN(concentree,1):"—","%");
  barresRecit("recit-liquidite-graphe",liquidite.slice(0,7).map(x=>x.s.ticker),liquidite.slice(0,7).map(x=>x.frequence),{
    nom:txtRecit("Fréquence d’activité","Trading activity frequency"),dec:1,formatter:v=>fmtN(v,1)+" %",axe:v=>fmtN(v,0)+" %"});
  $("#recit-liquidite-legende").textContent=txtRecit(
    "Part des séances avec volume, valeur échangée ou transaction strictement positive.",
    "Share of sessions with strictly positive volume, traded value or transaction count.");
  brancherSocietesRecit();
}

function tableActions(prix, caps, compacte){
  if(!prix.length) return `<div class="vide">Aucune cotation pour cette séance.</div>`;
  const capPar=Object.fromEntries((caps||[]).map(c=>[c.company_id,c]));
  const lignes=[...prix].sort((a,b)=>
    (D.socParId[a.company_id].ticker).localeCompare(D.socParId[b.company_id].ticker));
  return `<table><thead><tr>
      <th>Valeur</th><th>Cours</th><th>Var. %</th><th>Volume</th><th>Valeur échangée</th>
      ${compacte?"":"<th>Vol. demandé</th><th>Vol. offert</th>"}<th>Capitalisation</th><th>Statut</th>
    </tr></thead><tbody>${lignes.map(r=>{
      const s=D.socParId[r.company_id], c=capPar[r.company_id];
      return `<tr class="cliquable" data-soc="${r.company_id}">
        <td data-v="${s.ticker}"><div class="tick-nom"><b>${s.ticker}</b><span>${s.short_name} · ${s.country}</span></div></td>
        <td class="num" data-v="${r.close_price??""}">${fmtN(r.close_price)}</td>
        <td class="num ${clsVar(r.variation_pct)}" data-v="${r.variation_pct??""}">${fmtPct(r.variation_pct)}</td>
        <td class="num" data-v="${r.vol_traded??""}">${fmtN(r.vol_traded)}</td>
        <td class="num" data-v="${r.value_traded??""}">${r.value_traded?fmtN(r.value_traded):"—"}</td>
        ${compacte?"":`<td class="num" data-v="${r.vol_bid??""}">${fmtN(r.vol_bid)}</td>
                       <td class="num" data-v="${r.vol_ask??""}">${fmtN(r.vol_ask)}</td>`}
        <td class="num" data-v="${c?.total_market_cap??""}">${c?compactFCFA(c.total_market_cap):"—"}</td>
        <td data-v="${r.status||""}">${r.status?`<span class="pastille statut-${r.status}">${r.status}</span>`:"—"}</td>
      </tr>`;}).join("")}</tbody></table>`;
}
function brancherLignesSociete(sel){
  document.querySelectorAll(sel+" tr.cliquable").forEach(tr=>{
    tr.tabIndex=0;tr.setAttribute("role","link");
    const ouvrir=()=>{allerVue("actions"); ouvrirFicheSociete(+tr.dataset.soc);};
    tr.addEventListener("click",ouvrir);tr.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();ouvrir();}});
  });
}

/* ─────────── ACTIONS ─────────── */
function rendreActions(){
  $("#actions-sous").textContent=`${D.societes.length} sociétés cotées — séance du ${fmtDate(seanceCourante)}. Cliquez sur une valeur pour ouvrir sa fiche, sur les en-têtes pour trier.`;
  $("#table-actions").innerHTML=tableActions(D.prixParSeance[seanceCourante]||[],
                                             D.capParSeance[seanceCourante]||[], false);
  brancherLignesSociete("#table-actions");
  rendreTriable("#table-actions");
  rendreComparaison();
  rendreScreener();
}

function rendreComparaison(){
  // sociétés avec au moins 2 observations
  const eligibles=D.societes.filter(s=>histoSociete(D,s.company_id).length>=2);
  if(compSelection===null)
    compSelection=new Set(eligibles.slice(0,3).map(s=>s.company_id));

  const puces=$("#comp-puces");
  puces.innerHTML=eligibles.map((s,i)=>{
    const c=PALETTE_SERIES[i%PALETTE_SERIES.length];
    return `<button data-cid="${s.company_id}" style="--c:${c}"
      aria-pressed="${compSelection.has(s.company_id)}">
      <span class="pt"></span>${s.ticker}</button>`;}).join("");
  puces.querySelectorAll("button").forEach(b=>
    b.addEventListener("click",()=>{
      const cid=+b.dataset.cid;
      compSelection.has(cid)? compSelection.delete(cid) : compSelection.add(cid);
      rendreComparaison();
    }));

  rendreSegments($("#comp-periode"), compPeriode, p=>{compPeriode=p; rendreComparaison();});
  rendreGran($("#comp-gran"), compGran, g=>{compGran=g; rendreComparaison();});

  // Pour chaque société retenue : filtrage période → agrégation à l'échelle
  // choisie (dernière valeur du bucket). On indexe par clé de bucket pour aligner
  // toutes les séries sur un axe temps commun.
  const choisies=eligibles.filter(s=>compSelection.has(s.company_id));
  const cleDate={};                       // clé bucket -> date représentative (pour le tri/label)
  const parSoc=choisies.map(s=>{
    const h=filtrerPeriode(histoSociete(D,s.company_id),"bulletin_date_id",compPeriode);
    const m=new Map();
    for(const g of agregerBuckets(h,"bulletin_date_id",compGran)){
      const last=g[g.length-1], k=cleBucket(last.bulletin_date_id,compGran);
      m.set(k,last); cleDate[k]=last.bulletin_date_id;
    }
    return {s,m};
  });
  const cles=[...new Set(parSoc.flatMap(p=>[...p.m.keys()]))]
              .sort((a,b)=>cleDate[a]-cleDate[b]);

  const series=parSoc.map(({s,m})=>{
    const idxCouleur=eligibles.indexOf(s);
    const valsBrutes=cles.map(k=> m.get(k)?.close_price ?? null);
    const base=valsBrutes.find(v=>v!=null);
    return {nom:s.ticker,
      couleur:PALETTE_SERIES[idxCouleur%PALETTE_SERIES.length],
      valeurs:cles.map((k,i)=> valsBrutes[i]!=null && base ? valsBrutes[i]/base*100 : null)};
  });

  if(!series.length){ detruire("comp-graphe"); return; }
  ligne("comp-graphe", cles.map(k=>libGran(cleDate[k],compGran)), series,
    {dec:1,surcharge:{plugins:{legend:{display:true,labels:{boxWidth:10,usePointStyle:true}},
      tooltip:{callbacks:{label:c=>` ${c.dataset.label} : ${fmtN(c.parsed.y,1)} (base 100)`}}}}});
}

function ouvrirFicheSociete(cid){
  const s=D.socParId[cid];
  if(!s)return;
  const histoTout=histoSociete(D,cid);
  const dern=histoTout[histoTout.length-1];
  const caps=histoCap(D,cid);
  const dCap=caps[caps.length-1];
  const ana=analyseUnivers(seanceCourante).find(x=>String(x.s.company_id)===String(cid));
  $("#actions-liste").hidden=true;
  const f=$("#actions-fiche"); f.hidden=false;
  let periode="tout", gran="M";
  f.innerHTML=`
    <button class="retour" id="ret-soc">← Retour à la cote</button>
    <div class="entete-fiche">
      <h2>${s.short_name}</h2>
      <span class="isin">${s.isin} · ${s.ticker}</span>
      <span class="secteur">${s.sector}</span>
      <a class="bouton-secondaire" href="/app?view=portfolios&company=${encodeURIComponent(cid)}">Simuler cette action</a><button class="bouton-secondaire" id="btn-suivi-soc" style="margin-left:auto;margin-top:0">${listeSuivi.has(String(cid))?"★ Retirer des valeurs suivies":"☆ Ajouter aux valeurs suivies"}</button>
    </div>
    <div style="color:var(--encre-2);font-size:.9rem;margin-top:4px">${s.full_name} — ${
      {CM:"Cameroun",GA:"Gabon",GQ:"Guinée équatoriale"}[s.country]||s.country}${
      s.listing_date?` · introduite le ${new Date(s.listing_date).toLocaleDateString("fr-FR")}`:""}</div>
    <div class="cours-fiche">
      <span class="c">${fmtN(dern?.close_price)} <small style="font-size:1rem;color:var(--encre-2)">FCFA</small></span>
      <span class="v ${clsVar(dern?.variation_pct)}">${fmtPct(dern?.variation_pct)}</span>
      <span style="font-size:.82rem;color:var(--encre-2)">dernier bulletin : ${fmtDate(dern?.bulletin_date_id)}</span>
    </div>
    <div class="grille-stats">
      <div class="stat"><div class="l">Performance 1 mois</div><div class="v ${clsVar(ana?.r1m)}">${fmtPct(ana?.r1m)}</div></div>
      <div class="stat"><div class="l">Performance 6 mois</div><div class="v ${clsVar(ana?.r6m)}">${fmtPct(ana?.r6m)}</div></div>
      <div class="stat"><div class="l">Performance 1 an</div><div class="v ${clsVar(ana?.r1a)}">${fmtPct(ana?.r1a)}</div></div>
      <div class="stat"><div class="l">Rendement</div><div class="v">${fmtPct(ana?.rendement,false)}</div></div>
    </div>
    <div class="bloc">
      <header><h3>Score BVMAC</h3><span class="note">comparaison relative des valeurs cotées</span></header>
      <div class="corps plein"><div class="grille-score">
        <div><div class="score-total ${Number.isFinite(ana?.score)?"":"indispo"}" style="--score:${Number.isFinite(ana?.score)?ana.score:0}"><div style="position:relative;text-align:center"><strong>${Number.isFinite(ana?.score)?ana.score:"N/D"}</strong><br><small>${Number.isFinite(ana?.score)?"/ 100":"données insuffisantes"}</small></div></div>${!Number.isFinite(ana?.score)?`<div class="score-manquants">Score non calculé : ${((ana?.scoreManquants||[]).map(k=>({frequence:"fréquence de cotation",valeur_moyenne:"valeur moyenne échangée",roe:"ROE",marge_nette:"marge nette",performance_6m:"performance 6 mois",performance_1a:"performance 1 an",per:"PER",rendement:"rendement brut",volatilite:"volatilité",drawdown:"drawdown"})[k]||k)).join(", ")||"données requises absentes"}.</div>`:""}</div>
        <div class="score-lignes">${Object.entries(ana?.composantes||{}).map(([k,v])=>`<div class="score-ligne"><span>${({liquidite:"Liquidité",rentabilite:"Rentabilité",performance:"Performance",valorisation:"Valorisation",risque:"Risque"})[k]}</span><span class="rail-score"><i style="width:${Number.isFinite(v)?v:0}%"></i></span><b class="num">${Number.isFinite(v)?v:"N/D"}</b></div>`).join("")}</div>
      </div><div class="grille-stats" style="margin-top:18px">
        <div class="stat"><div class="l">Volatilité annualisée</div><div class="v">${fmtPct(ana?.volatilite,false)}</div></div>
        <div class="stat"><div class="l">Drawdown maximal</div><div class="v neg">${fmtPct(ana?.drawdown,false)}</div></div>
        <div class="stat"><div class="l">Fréquence de cotation</div><div class="v">${fmtPct(ana?.frequence,false)}</div></div>
        <div class="stat"><div class="l">Valeur moyenne échangée</div><div class="v">${ana?.valeurMoy?compactFCFA(ana.valeurMoy):"—"}</div></div>
      </div></div>
    </div>
    <div class="duo" style="margin-top:20px">
      <div class="bloc" style="margin-top:0">
        <header><h3>Cours de clôture</h3><span class="note" id="note-h-soc"></span>
          <div class="ctrl-temps">
            <span class="ctrl-lbl">Échelle</span><div class="segments" id="soc-gran"></div>
            <span class="ctrl-lbl">Période</span><div class="segments" id="soc-periode"></div>
          </div></header>
        <div class="corps plein"><div class="graphe-bloc"><canvas id="g-soc-prix"></canvas></div>
          <div class="graphe-hint"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/><path d="M11 8v6M8 11h6"/></svg>Molette ou pincement pour zoomer, glisser pour se déplacer, double-clic pour réinitialiser.</div>
        </div>
      </div>
      <div class="bloc" style="margin-top:0">
        <header><h3>Caractéristiques</h3></header>
        <div class="corps plein"><dl class="dl">
          <dt>Plus haut depuis janvier</dt><dd>${fmtN(dern?.ytd_high)}</dd>
          <dt>Plus bas depuis janvier</dt><dd>${fmtN(dern?.ytd_low)}</dd>
          <dt>Variation annuelle</dt><dd class="${clsVar(dern?.yoy_variation_pct)}">${fmtPct(dern?.yoy_variation_pct)}</dd>
          <dt>Seuils de séance</dt><dd>${dern?.lower_limit?fmtN(dern.lower_limit)+" / "+fmtN(dern.upper_limit):"—"}</dd>
          <dt>Titres en circulation</dt><dd>${fmtN(dCap?.total_shares)}</dd>
          <dt>Flottant</dt><dd>${fmtN(dCap?.float_shares)}</dd>
          <dt>Capitalisation globale</dt><dd>${fmtFCFA(dCap?.total_market_cap)}</dd>
          <dt>Capitalisation flottante</dt><dd>${fmtFCFA(dCap?.float_market_cap)}</dd>
          <dt>Dernier dividende</dt><dd>${dCap?.last_div_amount?fmtN(dCap.last_div_amount,2)+" FCFA"+(dCap.last_div_year?" ("+dCap.last_div_year+")":""):"—"}</dd>
          <dt>BPA (bénéfice / action)</dt><dd>${dCap?.eps!=null?fmtN(dCap.eps,2):"— <small>non publié</small>"}</dd>
          <dt>PER</dt><dd>${dCap?.per!=null?fmtN(dCap.per,2):"— <small>non publié</small>"}</dd>
          <dt>Taux de rendement brut</dt><dd id="dd-rendement">—</dd>
          <dt>Liquidité des titres</dt><dd>${dCap?.liquidity_pct!=null?fmtN(dCap.liquidity_pct,2)+" %":"—"}</dd>
        </dl></div>
      </div>
    </div>
    <div class="bloc" id="bloc-fondamentaux"></div>
    <div class="bloc">
      <header><h3>Volumes échangés par séance</h3></header>
      <div class="corps plein"><div class="graphe-bloc" style="height:200px"><canvas id="g-soc-vol"></canvas></div></div>
    </div>`;
  BVMACInstrument.mount(f,histoTout,{kind:"action"});
  $("#ret-soc").addEventListener("click",()=>{f.hidden=true;$("#actions-liste").hidden=false;rendreActions();});
  $("#btn-suivi-soc").addEventListener("click",()=>{basculerSuivi(cid);ouvrirFicheSociete(cid);});

  const tracer=()=>{
    const histo=filtrerPeriode(histoTout,"bulletin_date_id",periode);
    const groupes=agregerBuckets(histo,"bulletin_date_id",gran);
    const pts=groupes.map(g=>g[g.length-1]);             // dernière cotation du bucket
    const vol=groupes.map(g=>g.reduce((s,r)=>s+(r.vol_traded||0),0)); // volume cumulé
    $("#note-h-soc").textContent=pts.length+" point(s)";
    const lib=pts.map(r=>libGran(r.bulletin_date_id,gran));
    ligne("g-soc-prix",lib,[{nom:"Clôture",valeurs:pts.map(r=>r.close_price),couleur:"#2E5E4E",remplir:true}],{unite:" FCFA"});
    detruire("g-soc-vol");
    graphes["g-soc-vol"]=new Chart($("#g-soc-vol"),{type:"bar",
      data:{labels:lib,datasets:[{label:"Volume",data:vol,
        backgroundColor:"#C9A23F",borderRadius:4}]},
      options:{responsive:true,maintainAspectRatio:false,
        plugins:{legend:{display:false},tooltip:{callbacks:{label:c=>" "+fmtN(c.parsed.y)+" titres"}}},
        scales:{x:{grid:{display:false},ticks:{maxTicksLimit:9,font:{size:11}}},
                y:{grid:{color:"#E4E9E3"},border:{display:false},
                   ticks:{font:{family:"IBM Plex Mono",size:11}}}}}});
  };
  const onPer=p=>{periode=p; rendreSegments($("#soc-periode"),periode,onPer); tracer();};
  const onGr =g=>{gran=g;    rendreGran($("#soc-gran"),gran,onGr);          tracer();};
  rendreSegments($("#soc-periode"),periode,onPer);
  rendreGran($("#soc-gran"),gran,onGr);
  tracer();

  rendreFondamentaux(cid);


}

function rendreFondamentaux(cid){
  const bloc=$("#bloc-fondamentaux");
  const brut=D.finParSociete[cid]||[];
  if(!brut.length){
    bloc.innerHTML=`<header><h3>Indicateurs fondamentaux</h3></header>
      <div class="vide">Pas de fiche signalétique pour cette valeur dans les bulletins chargés
      (publiées depuis 2024).</div>`;
    return;
  }
  const ex=ratiosFondamentaux(brut);
  const dern=ex[ex.length-1];
  // rendement brut du dernier exercice → dans Caractéristiques
  const dd=$("#dd-rendement");
  if(dd) dd.innerHTML = dern.taux_rendement_brut_pct!=null
    ? fmtN(dern.taux_rendement_brut_pct,2)+" % <small>(exercice "+dern.fiscal_year+")</small>" : "—";

  const M=(v)=>v!=null?compactFCFA(v):"—";
  const P=(v,s=true)=>v!=null?`<span class="${clsVar(v)}">${fmtPct(v,s)}</span>`:"—";
  const lignes=[
    ["Chiffre d'affaires / PNB", r=>M(r.chiffre_affaires), "Activité"],
    ["&nbsp;&nbsp;↳ croissance",  r=>P(r.croissance_ca_pct)],
    ["Valeur ajoutée",            r=>M(r.valeur_ajoutee)],
    ["&nbsp;&nbsp;↳ croissance",  r=>P(r.croissance_va_pct)],
    ["Résultat net",              r=>M(r.resultat_net), "Rentabilité"],
    ["&nbsp;&nbsp;↳ croissance",  r=>P(r.croissance_rn_pct)],
    ["Marge nette",               r=>P(r.marge_nette_pct,false)],
    ["ROE (RN / Capitaux propres)",r=>P(r.roe_pct,false)],
    ["ROA (RN / Total bilan)",    r=>P(r.roa_pct,false)],
    ["Capitaux propres",          r=>M(r.capitaux_propres), "Solvabilité"],
    ["&nbsp;&nbsp;↳ croissance",  r=>P(r.croissance_cp_pct)],
    ["Total bilan",               r=>M(r.total_bilan)],
    ["Autonomie financière (CP/TB)",r=>P(r.autonomie_pct,false)],
    ["Dividende brut / action",   r=>r.dividende_unitaire!=null?fmtN(r.dividende_unitaire,0)+" F":"—", "Dividendes"],
    ["Taux de rendement brut",    r=>P(r.taux_rendement_brut_pct,false)],
  ];
  bloc.innerHTML=`
    <header><h3>Indicateurs fondamentaux</h3>
      <span class="note">fiches signalétiques BVMAC — exercices ${ex[0].fiscal_year} à ${dern.fiscal_year}, montants en FCFA</span></header>
    <div class="corps market-table-scroll"><table>
      <thead><tr><th style="text-align:left">Indicateur</th>${
        ex.map(r=>`<th>${r.fiscal_year}</th>`).join("")}</tr></thead>
      <tbody>${lignes.map(([lib,fn,cat])=>`
        ${cat?`<tr><td colspan="${ex.length+1}" style="font-size:.68rem;letter-spacing:.12em;text-transform:uppercase;color:var(--encre-2);background:#F4F6F3;padding:6px 12px">${cat}</td></tr>`:""}
        <tr><td style="text-align:left">${lib}</td>${
          ex.map(r=>`<td class="num">${fn(r)}</td>`).join("")}</tr>`).join("")}
      </tbody></table></div>`;
}

/* ─────────── OPCVM ─────────── */
function rendreOPCVM(){
  const seanceOPCVM=Object.keys(D.vlParBulletin).reduce((date,key)=>Math.max(date,Number(key)),0);
  const vls=D.vlParBulletin[seanceOPCVM]||[];
  $("#opcvm-sous").textContent= vls.length
    ? `${vls.length} fonds valorisés dans le bulletin du ${fmtDate(seanceOPCVM)}. Tri par en-têtes, clic pour ouvrir la fiche.`
    : "Aucune valorisation OPCVM disponible.";

  const selC=$("#f-categorie"), selG=$("#f-gestionnaire"), selF=$("#f-frequence");
  if(selC.options.length===1){
    [...new Set(D.fonds.map(f=>f.category).filter(Boolean))].sort().forEach(c=>
      selC.insertAdjacentHTML("beforeend",`<option value="${c}">${c} — ${CAT_LIB[c]||c}</option>`));
    [...new Set(D.fonds.map(f=>f.manager).filter(Boolean))].sort().forEach(g=>
      selG.insertAdjacentHTML("beforeend",`<option>${g}</option>`));
    [...new Set(D.fonds.map(f=>f.valuation_frequency).filter(Boolean))].sort().forEach(fr=>
      selF.insertAdjacentHTML("beforeend",`<option>${fr}</option>`));
    [selC,selG,selF].forEach(s=>s.addEventListener("change",rendreOPCVM));
    $("#f-recherche").addEventListener("input",rendreOPCVM);
  }

  const q=$("#f-recherche").value.trim().toLowerCase();
  const fc=selC.value, fg=selG.value, ff=selF.value;
  const lignes=vls.map(r=>({r,f:D.fondParId[r.fund_id]}))
    .filter(({f})=>(!fc||f.category===fc)&&(!fg||f.manager===fg)&&(!ff||f.valuation_frequency===ff)
      &&(!q||(f.fund_name+" "+(f.manager||"")).toLowerCase().includes(q)))
    .sort((a,b)=>a.f.fund_name.localeCompare(b.f.fund_name));

  $("#table-opcvm").innerHTML = !lignes.length
    ? `<div class="vide">Aucun fonds ne correspond ${vls.length?"aux filtres":"à cette séance"}.</div>`
    : `<table><thead><tr>
        <th>Fonds</th><th>Cat.</th><th>Fréquence</th><th>VL</th><th>au</th>
        <th>Var. préc.</th><th>Var. origine</th><th>Perf. annualisée</th>
      </tr></thead><tbody>${lignes.map(({r,f})=>{
        const pa=perfAnnualisee(f,r);
        return `<tr class="cliquable" data-fond="${f.fund_id}">
        <td data-v="${f.fund_name}"><div class="tick-nom"><b>${f.fund_name}</b><span>${f.manager||""} · ${f.custodian||""}</span></div></td>
        <td data-v="${f.category||""}"><span class="cat" title="${CAT_LIB[f.category]||""}">${f.category||"—"}</span></td>
        <td data-v="${f.valuation_frequency||""}" style="text-align:right;font-size:.82rem;color:var(--encre-2)">${f.valuation_frequency||"—"}</td>
        <td class="num" data-v="${r.nav??""}">${fmtN(r.nav,2)}</td>
        <td data-v="${r.nav_date_id??""}" style="font-size:.8rem;color:var(--encre-2)">${fmtDate(r.nav_date_id,{day:"2-digit",month:"2-digit",year:"2-digit"})}</td>
        <td class="num ${clsVar(r.var_prev_pct)}" data-v="${r.var_prev_pct??""}">${fmtPct(r.var_prev_pct)}</td>
        <td class="num ${clsVar(r.var_inception_pct)}" data-v="${r.var_inception_pct??""}">${fmtPct(r.var_inception_pct)}</td>
        <td class="num" data-v="${pa??""}">${fmtPct(pa,false)}</td>
      </tr>`;}).join("")}</tbody></table>`;
  $("#table-opcvm").querySelectorAll("tr.cliquable").forEach(tr=>{
    tr.tabIndex=0;tr.setAttribute("role","link");
    const ouvrir=()=>ouvrirFicheFonds(+tr.dataset.fond);
    tr.addEventListener("click",ouvrir);tr.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();ouvrir();}});
  });
  rendreTriable("#table-opcvm");
}

function ouvrirFicheFonds(fid){
  const f=D.fondParId[fid];
  const histoTout=histoVL(D,fid);
  const dern=histoTout[histoTout.length-1];
  $("#opcvm-liste").hidden=true;
  const el=$("#opcvm-fiche"); el.hidden=false;
  let periode="tout", gran="M";
  el.innerHTML=`
    <button class="retour" id="ret-fond">← Retour aux OPCVM</button>
    <div class="entete-fiche">
      <h2>${f.fund_name}</h2>
      <span class="secteur">${CAT_LIB[f.category]||f.category||""}</span>
    </div>
    <div style="color:var(--encre-2);font-size:.9rem;margin-top:4px">
      Géré par <b>${f.manager||"—"}</b> · dépositaire ${f.custodian||"—"} ·
      valorisation ${(f.valuation_frequency||"—").toLowerCase()}</div>
    <div class="cours-fiche">
      <span class="c">${fmtN(dern?.nav,2)} <small style="font-size:1rem;color:var(--encre-2)">FCFA</small></span>
      <span class="v ${clsVar(dern?.var_inception_pct)}">${fmtPct(dern?.var_inception_pct)} depuis l'origine</span>
      <span style="font-size:.82rem;color:var(--encre-2)">VL au ${fmtDate(dern?.nav_date_id)}</span>
    </div>
    <div class="duo" style="margin-top:20px">
      <div class="bloc" style="margin-top:0">
        <header><h3>Valeur liquidative</h3><span class="note" id="note-h-vl"></span>
          <div class="ctrl-temps">
            <span class="ctrl-lbl">Échelle</span><div class="segments" id="vl-gran"></div>
            <span class="ctrl-lbl">Période</span><div class="segments" id="vl-periode"></div>
          </div></header>
        <div class="corps plein"><div class="graphe-bloc"><canvas id="g-vl"></canvas></div>
          <div class="graphe-hint"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/><path d="M11 8v6M8 11h6"/></svg>Molette ou pincement pour zoomer, glisser pour se déplacer, double-clic pour réinitialiser.</div>
        </div>
      </div>
      <div class="bloc" style="margin-top:0">
        <header><h3>Caractéristiques</h3></header>
        <div class="corps plein"><dl class="dl">
          <dt>Valeur d'origine</dt><dd>${fmtN(f.initial_value,2)} FCFA</dd>
          <dt>Date de création</dt><dd>${fmtDate(f.inception_date_id)}</dd>
          <dt>Performance annualisée</dt><dd class="${clsVar(perfAnnualisee(f,dern))}">${fmtPct(perfAnnualisee(f,dern),false)}</dd>
          <dt>Variation vs précédente</dt><dd class="${clsVar(dern?.var_prev_pct)}">${fmtPct(dern?.var_prev_pct)}</dd>
          <dt>VL précédente</dt><dd>${fmtN(dern?.prev_nav,2)}${dern?.prev_nav_date_id?" ("+fmtDate(dern.prev_nav_date_id,{day:"2-digit",month:"2-digit",year:"2-digit"})+")":""}</dd>
        </dl></div>
      </div>
    </div>`;
  BVMACInstrument.mount(el,histoTout,{kind:"fund"});
  $("#ret-fond").addEventListener("click",()=>{el.hidden=true;$("#opcvm-liste").hidden=false;rendreOPCVM();});

  const tracer=()=>{
    const histo=filtrerPeriode(histoTout,"bulletin_date_id",periode);
    const pts=agregerBuckets(histo,"bulletin_date_id",gran).map(g=>g[g.length-1]);
    $("#note-h-vl").textContent=pts.length+" point(s)";
    ligne("g-vl",pts.map(r=>libGran(r.nav_date_id||r.bulletin_date_id,gran)),
      [{nom:"VL",valeurs:pts.map(r=>r.nav),couleur:"#2E5E4E",remplir:true}],{dec:2,unite:" FCFA"});
  };
  const onPer=p=>{periode=p; rendreSegments($("#vl-periode"),periode,onPer); tracer();};
  const onGr =g=>{gran=g;    rendreGran($("#vl-gran"),gran,onGr);          tracer();};
  rendreSegments($("#vl-periode"),periode,onPer);
  rendreGran($("#vl-gran"),gran,onGr);
  tracer();


}

/* ─────────── INDICE ─────────── */
function rendreIndice(){
  BVMACInstrument.mount($("#vue-indice"),D.indice,{kind:"index"});
  const disponible=D.indice.length>0;
  $("#indice-vide").hidden=disponible;
  $("#indice-graphique").hidden=!disponible;
  $("#indice-aide").hidden=!disponible;
  if(!D.indice.length){
    detruire("indice-graphe");
    $("#indice-periode").innerHTML=""; $("#indice-gran").innerHTML="";
    return;
  }
  rendreSegments($("#indice-periode"), indicePeriode, p=>{indicePeriode=p; rendreIndice();});
  rendreGran($("#indice-gran"), indiceGran, g=>{indiceGran=g; rendreIndice();});
  const serie=filtrerPeriode(D.indice,"date_id",indicePeriode);
  const pts=agregerBuckets(serie,"date_id",indiceGran).map(g=>g[g.length-1]);
  ligne("indice-graphe", pts.map(r=>libGran(r.date_id,indiceGran)),
    [{nom:"BVMAC-AS",valeurs:pts.map(r=>r.index_value),couleur:"#C9A23F",remplir:true}],
    {dec:2,unite:" pts"});

}

/* ─────────── SCREENER & LISTE DE SUIVI ─────────── */
function basculerSuivi(cid){
  const cle=String(cid); listeSuivi.has(cle)?listeSuivi.delete(cle):listeSuivi.add(cle);
  try{localStorage.setItem("bvmac_guest_watchlist",JSON.stringify([...listeSuivi]))}catch{}
}
function classeScore(v){return !Number.isFinite(v)?"indispo":v>=70?"bon":v<40?"faible":"";}
function tableAnalyses(lignes,id){
  if(!lignes.length)return `<div class="liste-vide"><h3>Aucune valeur ne correspond</h3><p>Modifiez les filtres ou ajoutez des actions depuis le screener.</p><button class="bouton-secondaire" id="voir-screener">Ouvrir le screener</button></div>`;
  return `<table><thead><tr><th>Valeur</th><th>Cours</th><th>Jour</th><th>1 mois</th><th>6 mois</th><th>1 an</th><th>Rendement</th><th>PER</th><th>Liquidité</th><th>Score BVMAC</th><th>Suivi</th></tr></thead><tbody>${lignes.map(x=>`<tr class="cliquable" data-soc="${ech(x.s.company_id)}">
    <td data-v="${ech(x.s.ticker)}"><div class="tick-nom"><b>${ech(x.s.ticker)}</b><span>${ech(x.s.short_name)} · ${ech(x.s.sector||"")}</span></div></td>
    <td class="num" data-v="${x.prix??""}">${fmtN(x.prix)}</td><td class="num ${clsVar(x.varJour)}" data-v="${x.varJour??""}">${fmtPct(x.varJour)}</td>
    <td class="num ${clsVar(x.r1m)}" data-v="${x.r1m??""}">${fmtPct(x.r1m)}</td><td class="num ${clsVar(x.r6m)}" data-v="${x.r6m??""}">${fmtPct(x.r6m)}</td><td class="num ${clsVar(x.r1a)}" data-v="${x.r1a??""}">${fmtPct(x.r1a)}</td>
    <td class="num" data-v="${x.rendement??""}">${fmtPct(x.rendement,false)}</td><td class="num" data-v="${x.per??""}">${fmtN(x.per,2)}</td>
    <td data-v="${x.composantes.liquidite??""}"><span class="score ${classeScore(x.composantes.liquidite)}">${Number.isFinite(x.composantes.liquidite)?x.composantes.liquidite:"N/D"}</span></td>
    <td data-v="${x.score??""}"><span class="score ${classeScore(x.score)}" title="${Number.isFinite(x.score)?"Score BVMAC":"Score non calculé : données requises absentes"}">${Number.isFinite(x.score)?x.score:"N/D"}</span></td>
    <td><button class="etoile ${listeSuivi.has(String(x.s.company_id))?"active":""}" data-suivi="${ech(x.s.company_id)}" aria-label="Ajouter ou retirer des valeurs suivies">${listeSuivi.has(String(x.s.company_id))?"★":"☆"}</button></td>
  </tr>`).join("")}</tbody></table>`;
}
function brancherTableAnalyses(conteneur){
  conteneur.querySelectorAll("tr[data-soc]").forEach(tr=>tr.addEventListener("click",e=>{if(e.target.closest("[data-suivi]"))return;allerVue("actions");ouvrirFicheSociete(+tr.dataset.soc);}));
  conteneur.querySelectorAll("[data-suivi]").forEach(b=>b.addEventListener("click",()=>{basculerSuivi(b.dataset.suivi);vueCourante==="veille"?rendreVeille():rendreScreener();}));
  conteneur.querySelector("#voir-screener")?.addEventListener("click",()=>{allerVue("actions");setTimeout(()=>document.querySelector("#actions-screener")?.scrollIntoView({behavior:"smooth",block:"start"}),60)});
}
function rendreScreener(){
  const secteur=$("#sc-secteur"),pays=$("#sc-pays");
  if(!secteur.dataset.pret){
    [...new Set(D.societes.map(s=>s.sector).filter(Boolean))].sort().forEach(v=>secteur.insertAdjacentHTML("beforeend",`<option>${ech(v)}</option>`));
    [...new Set(D.societes.map(s=>s.country).filter(Boolean))].sort().forEach(v=>pays.insertAdjacentHTML("beforeend",`<option>${ech(v)}</option>`));
    [$("#sc-recherche"),secteur,pays,$("#sc-score"),$("#sc-liquidite")].forEach(el=>{el.addEventListener(el.tagName==="INPUT"?"input":"change",rendreScreener);});
    secteur.dataset.pret="1";
  }
  const q=$("#sc-recherche").value.trim().toLowerCase(), score=+$("#sc-score").value, liq=+$("#sc-liquidite").value;
  const lignes=analyseUnivers(seanceCourante).filter(x=>(!q||(x.s.ticker+" "+x.s.short_name+" "+x.s.full_name).toLowerCase().includes(q))&&(!secteur.value||x.s.sector===secteur.value)&&(!pays.value||x.s.country===pays.value)&&(!score||(Number.isFinite(x.score)&&x.score>=score))&&(!liq||(Number.isFinite(x.composantes.liquidite)&&x.composantes.liquidite>=liq))).sort((a,b)=>(b.score??-Infinity)-(a.score??-Infinity));
  const el=$("#table-screener");el.innerHTML=tableAnalyses(lignes,"screener");brancherTableAnalyses(el);rendreTriable("#table-screener");
}
function rendreVeille(){
  const lignes=analyseUnivers(seanceCourante).filter(x=>listeSuivi.has(String(x.s.company_id))).sort((a,b)=>(b.score??-Infinity)-(a.score??-Infinity));
  const el=$("#table-veille");el.innerHTML=tableAnalyses(lignes,"veille");brancherTableAnalyses(el);rendreTriable("#table-veille");
}

/* ─────────── DONNÉES, SOURCES & LEXIQUE ─────────── */
function rendreDonnees(){
  // Page volontairement documentaire : aucun état opérationnel ni contrôle qualité public.
}

/* ─────────── QUALITÉ ─────────── */
function rendreQualite(){
  const fq=$("#filtres-qualite");
  const compte=s=>D.qualite.filter(r=>r.severity===s).length;
  fq.innerHTML=["","ERROR","WARNING","INFO"].map(s=>
    `<button class="sev ${s||"INFO"}" style="${s===""?"background:var(--encre);color:#fff":""};cursor:pointer;border:none"
      data-s="${s}" aria-pressed="${qualiteSev===s}">${s||"Tout"} ${s?`(${compte(s)})`:`(${D.qualite.length})`}</button>`).join("");
  fq.querySelectorAll("button").forEach(b=>
    b.addEventListener("click",()=>{qualiteSev=b.dataset.s; rendreQualite();}));

  const lignes=D.qualite.filter(r=>!qualiteSev||r.severity===qualiteSev);
  if(!lignes.length || (lignes.length===1 && lignes[0].severity==="OK")){
    $("#table-qualite").innerHTML=`<div class="vide">Aucune anomalie : tous les contrôles de cohérence sont au vert.</div>`;
    return;
  }
  $("#table-qualite").innerHTML=`<table><thead><tr>
      <th>Sévérité</th><th>Valeur</th><th>Séance</th><th>Contrôle</th><th style="text-align:left">Détail</th>
    </tr></thead><tbody>${lignes.map(r=>`<tr>
      <td data-v="${r.severity}"><span class="sev ${r.severity}">${r.severity}</span></td>
      <td data-v="${r.ticker||""}" style="text-align:left"><b>${r.ticker||"—"}</b></td>
      <td data-v="${r.date_id||""}">${r.date_id?fmtDate(r.date_id):"—"}</td>
      <td data-v="${r.check||""}" style="text-align:left">${r.check||""}</td>
      <td style="text-align:left;white-space:normal;color:var(--encre-2)">${r.detail||""}</td>
    </tr>`).join("")}</tbody></table>`;
  rendreTriable("#table-qualite");
}

/* ═══════════════ DÉMARRAGE ═══════════════ */
appliquerLangueDOM(document);
dessinerGuilloche($("#amorce .rosace"), 7, "#2E5E4E");
if(location.protocol==="file:"){
  ecranAmorce(`
    <h1>Accès via un serveur requis</h1>
    <p>Pour des raisons de sécurité, un navigateur n'autorise pas la lecture
       des données lorsque la page est ouverte directement depuis le disque.</p>
    <div class="panneau">
      <h3>Démarrage rapide</h3>
      <p style="margin-top:0">Cette application doit être servie par son backend afin de protéger les sessions et les données privées.</p>
      <h3 style="margin-top:18px">Déploiement</h3>
      <pre>./deploy.sh
./checking.sh</pre>
    </div>`);
}else{
  allerVue("accueil");
  chargerDonnees();
}

// Navigation/actions sans gestionnaires inline (CSP stricte).
document.addEventListener('click',e=>{const n=e.target.closest('[data-nav]');if(n){location.href=n.dataset.nav;return}const a=e.target.closest('[data-action=\"retry-data\"]');if(a)chargerDonnees()});
