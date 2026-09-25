# Carte pour contribuer

Pour changer l’interface, commencer dans `web/`. Pour une route HTTP, regarder `backend/api/`. Pour un traitement planifié, voir `backend/jobs/` ou `operations/`. Les règles de parsing des documents sont dans `pipeline/`. Les contrats SQL sont dans `database/`. L’inférence ML est dans `ml/`.

Une bonne pull request ajoute un test ciblé. Pour le responsive, vérifier 360, 390, 430, 768, 820, 1024, 1366 et 1920 px. Pour le ML, conserver les validations chronologiques et les comparaisons avec des baselines simples.
