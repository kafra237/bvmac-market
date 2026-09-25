# Reprise du projet par un mainteneur

Cette page est le chemin le plus court pour comprendre où intervenir quand on n’a pas construit le projet soi-même.

## Lire le système de gauche à droite

Le modèle mental le plus utile est :

```text
Bulletin public BVMAC
→ pipeline
→ classeur Excel canonique
→ import/historique PostgreSQL
→ FastAPI
→ web/PWA
→ notifications, rapports et Radar prédictif
```

Le pipeline est responsable de l’extraction. L’importeur base de données stocke ce que contient le classeur. L’API ne doit pas “corriger” silencieusement les données de marché. Cette séparation permet de remonter une valeur surprenante jusqu’au document source au lieu d’avoir des règles cachées dans plusieurs couches.

## Où intervenir selon le besoin

| Besoin | Commencer ici | Souvent lié aussi à |
| --- | --- | --- |
| Mise en page / navigation | `web/` | `web/i18n.js`, QA navigateur |
| Authentification / comptes | `backend/api/auth_module.py` | `email_auth.py`, base, tests sécurité |
| API marché | `backend/api/market.py` | `database/`, consommateurs web |
| Portefeuilles | `backend/api/portfolio_module.py` | vue/JS/CSS portefeuille |
| Interface Radar | `web/radar-app.js`, `web/radar.css` | `backend/api/ml_radar.py` |
| Modèle Radar | `ml/` | model card, validation, inférence |
| Téléchargement/extraction PDF | `pipeline/` | tests qualité des données ; modification prudente |
| Excel → PostgreSQL | `database/import_excel.py` | schémas SQL |
| Email hebdomadaire | `backend/api/weekly_email.py` | job notifications, communications admin |
| Push | `backend/api/push_*` | job notifications, Service Worker |
| Feeds | `backend/api/feed_module.py` | `backend/jobs/feeds.py`, vue feeds |
| Administration `/stat` | `backend/admin/stat.html` | `web/stat-app.js`, `admin_module.py` |
| Déploiement | `deploy.sh` | `operations/`, `checking.sh` |

## Avant de modifier le pipeline

`bvmac_downloader.py` et `bvmac_extract.py` sont les composants ETL historiques ; leurs empreintes sont protégées par un test du dépôt. C’est volontaire. Si l’extraction doit réellement évoluer, traite cela comme un changement de contrat de données : documente le cas de bulletin concerné, ajoute un test de régression, explique l’impact sur le classeur canonique puis mets à jour consciemment l’empreinte protégée.

## Avant de modifier le ML

Commence par `ml/MODEL_CARD.md`. Un modèle ne doit apparaître dans le produit que si sa cible, son horizon, sa validation temporelle, sa comparaison à une baseline et sa gestion de la confiance sont explicites. Un score élevé sur un split aléatoire n’est pas suffisant. Le marché BVMAC est peu liquide et certaines baselines naïves sont naturellement fortes.

## Avant de modifier la base

Privilégie du SQL additif et idempotent. Ne supprime jamais des données de production simplement pour faire passer une migration. Le déploiement sauvegarde PostgreSQL et teste une restauration avant mutation, mais chaque migration doit quand même avoir une stratégie de compatibilité claire.

## Avant de modifier l’interface

Les fonctions utilisateur authentifiées vivent sous `/app?view=...`. La connexion/inscription, les informations publiques et `/stat` restent volontairement séparées. Tout texte visible doit être vérifié en français et en anglais. Toute modification de mise en page doit passer la QA navigateur à 360, 390, 430, 768, 820, 1024, 1366 et 1920 px.

## Une bonne pull request

Un reviewer doit pouvoir répondre rapidement à cinq questions :

1. Quel problème utilisateur ou maintenance est résolu ?
2. Quelle couche porte le changement, et pourquoi ?
3. Est-ce que le sens des données, l’API, la sécurité ou le déploiement changent ?
4. Quels tests prouvent le comportement ?
5. Comment reconnaître un échec en production et revenir à un état sain ?

Quand ces réponses sont claires, la reprise du code devient beaucoup plus simple.
