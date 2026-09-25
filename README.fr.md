# BVMAC Market

**BVMAC Market** est un projet indépendant qui transforme les publications publiques de la BVMAC en une plateforme web consultable et historisée.

La BVMAC (Bourse des Valeurs Mobilières de l’Afrique Centrale) publie l’essentiel de ses informations de marché dans des bulletins PDF. Ces documents sont la source officielle, mais ils ne sont pas toujours pratiques lorsqu’on veut suivre les cours, la liquidité, les OPCVM ou l’évolution du marché dans le temps. BVMAC Market construit une chaîne reproductible autour de ces documents publics :

```text
Bulletins officiels BVMAC (PDF)
        ↓
Téléchargement et extraction
        ↓
Classeur Excel canonique
        ↓
Historique PostgreSQL
        ↓
API + application web
        ↓
Graphiques, portefeuilles, alertes, rapports hebdo et Radar prédictif
```

Le projet **ne passe aucun ordre de bourse** et ne donne pas de conseil d’investissement. Le Radar prédictif affiche des probabilités accompagnées d’un niveau de confiance ; il sert à analyser, pas à donner un ordre d’achat ou de vente.

[Read the English README](README.md)

## Ce que la plateforme permet

- consulter les actions, OPCVM et l’indice BVMAC-AS au même endroit ;
- suivre l’historique sans rouvrir les bulletins un par un ;
- observer la liquidité, la pression achat/vente et l’activité des séances ;
- créer des listes de suivi et des portefeuilles virtuels ;
- recevoir des notifications web push et une synthèse hebdomadaire par email ;
- vérifier toutes les 30 minutes si un nouveau bulletin semble disponible ;
- explorer les modèles probabilistes validés du Radar prédictif ;
- administrer les utilisateurs, les envois, la qualité des données et l’état du serveur depuis `/stat` ;
- utiliser l’interface en français ou en anglais, sur ordinateur, tablette, mobile ou PWA installée.

## Organisation du dépôt

| Dossier | Rôle |
| --- | --- |
| `web/` | HTML, CSS, JavaScript, PWA et interface bilingue |
| `backend/api/` | Application FastAPI et modules métier/API |
| `backend/jobs/` | Tâches planifiées : alertes, feeds, notifications, statistiques web |
| `pipeline/` | Téléchargement et extraction des bulletins publics BVMAC |
| `database/` | Schéma PostgreSQL, migrations et import des snapshots Excel |
| `ml/` | Inférence ML, modèles validés, model card et fiabilité |
| `operations/` | Outils d’administration et d’exploitation serveur |
| `docs/` | Documentation humaine en français et en anglais |
| `tests/` | Tests unitaires, intégration et contrôles responsive |

Deux scripts shell seulement sont exposés à la racine :

- `deploy.sh` — déploiement guidé vers un VPS ;
- `checking.sh` — vérification indépendante de la production.

## Démarrage rapide

```bash
git clone <url-de-ton-fork-ou-du-depot> bvmac-market
cd bvmac-market
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

Pour déployer depuis Linux, macOS, WSL ou Git Bash :

```bash
./deploy.sh
```

Le script demande l’adresse du VPS, l’utilisateur SSH, le domaine public et l’email administrateur. Il crée une sauvegarde avant bascule et teste la restaurabilité de PostgreSQL.

Ensuite :

```bash
./checking.sh
```

Lis [la documentation de déploiement](docs/fr/08-deploiement-et-exploitation.md) avant une mise en production.

## Contribuer

Les corrections, tests, améliorations d’interface, contrôles de qualité des données, travaux ML et améliorations de documentation sont les bienvenus. Voir [CONTRIBUTING.fr.md](CONTRIBUTING.fr.md). [Règles de la communauté](CODE_OF_CONDUCT.fr.md).

## Licence

Le code source est publié sous [licence MIT](LICENSE). Cette licence couvre le code du projet, pas les publications de marché, marques ou droits sur les données de tiers. Les documents sources BVMAC restent soumis aux conditions de leur propriétaire.
