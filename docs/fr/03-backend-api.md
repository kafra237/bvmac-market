# Backend et API

`backend/api/` contient l’application FastAPI. `bvmac_api.py` assemble des modules plus petits : authentification, données de marché, portefeuilles, Radar, feeds, push, email hebdomadaire, administration et statistiques first-party.

`backend/jobs/` regroupe les tâches qui ne répondent pas directement à une requête HTTP : collecte RSS, calcul des alertes, envoi des notifications et import des logs Nginx.

En production, uvicorn écoute uniquement sur localhost ; Nginx est le point d’entrée public HTTPS.

## Namespace de compatibilité API

Certaines routes HTTP commencent encore par `/api/v3/`. Il s’agit d’un namespace de compatibilité de l’API, pas du nom du projet ni de sa version. Il est volontairement conservé pour ne pas casser les navigateurs, PWA installées et intégrations existantes. Un nouveau namespace ne doit être créé que pour une vraie rupture de contrat API.
