# Data pipeline / Pipeline de données

This is the source-data layer. It downloads public BVMAC bulletins, extracts their tables and updates the canonical Excel workbook. `run.py` only coordinates the two historical ETL programs; the downloader and extractor are deliberately kept unchanged so their behaviour remains reproducible.

C’est la couche d’acquisition. Elle télécharge les bulletins publics BVMAC, extrait leurs tableaux et met à jour le classeur Excel canonique. `run.py` orchestre les deux programmes ETL historiques ; le downloader et l’extracteur sont volontairement conservés à l’identique pour garder un comportement reproductible.

Detailed documentation: [`docs/en/04-data-pipeline.md`](../docs/en/04-data-pipeline.md) · [`docs/fr/04-pipeline-de-donnees.md`](../docs/fr/04-pipeline-de-donnees.md)
