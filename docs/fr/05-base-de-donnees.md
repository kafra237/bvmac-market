# Base de données

PostgreSQL conserve l’état courant et l’historique des imports. Le fichier Excel est hashé : un contenu réellement nouveau crée un nouveau lot d’import immuable. On peut ainsi comprendre comment le jeu de données a évolué.

Les schémas séparent marché, authentification, administration, portefeuilles et prédictions ML. Les rôles PostgreSQL ont aussi des droits différents selon leur mission.

Lors d’un déploiement, un `pg_dump` au format custom est restauré dans une base temporaire avant mutation. Une sauvegarde n’est donc pas considérée valide simplement parce qu’un fichier existe.
