(()=>{
const root=document.querySelector('.platform-main')||document.querySelector('main');if(!root)return;
if(!root.id)root.id='main-content';root.tabIndex=-1;
const skip=document.createElement('a');skip.className='skip-link';skip.href='#'+root.id;skip.textContent='Aller au contenu';document.body.prepend(skip);
const footer=document.createElement('footer');footer.className='launch-links compact-legal';footer.innerHTML='<span>Initiative personnelle indépendante · sans affiliation à la BVMAC.</span><a href="/information.html">À propos, sources et confidentialité</a><a href="/information.html#contact">Contact</a>';root.append(footer);
if(location.pathname.includes('account')){const note=document.createElement('p');note.className='release-note';note.textContent='Portefeuilles simulés : aucun ordre n’est envoyé à un intermédiaire et aucun actif n’est détenu ici. Calculs sur les cours disponibles, hors frais, fiscalité et dividendes réinvestis ; exécution et liquidité non garanties. Les arrondis aux lots laissent un reliquat en espèces.';document.querySelector('#optResults')?.before(note);}
})();