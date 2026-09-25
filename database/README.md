# Database / Base de données

PostgreSQL stores imported market snapshots, user-facing application state, notification history and Predictive Radar outputs. SQL files are additive/idempotent where practical so deployment can preserve existing data. `import_excel.py` imports the canonical Excel workbook into the database; it does not replace the extraction pipeline.

PostgreSQL conserve les snapshots de marché, l’état applicatif, l’historique des notifications et les résultats du Radar. Les fichiers SQL sont additifs/idempotents autant que possible afin de préserver les données existantes. `import_excel.py` importe le classeur Excel canonique ; il ne remplace pas le pipeline d’extraction.

Detailed documentation: [`docs/en/05-database.md`](../docs/en/05-database.md) · [`docs/fr/05-base-de-donnees.md`](../docs/fr/05-base-de-donnees.md)
