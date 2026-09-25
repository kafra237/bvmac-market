# Pipeline de données

Le pipeline est volontairement séparé en trois rôles. `bvmac_downloader.py` trouve et télécharge les bulletins publics. `bvmac_extract.py` lit les PDF et construit `bvmac_master.xlsx`. `run.py` orchestre les deux.

Le classeur est une frontière fonctionnelle : le code en aval ne doit pas réinventer les corrections d’extraction. Si une règle métier d’extraction change, on la corrige dans l’extracteur puis on régénère le classeur.

En production, le pipeline complet tourne quotidiennement. Un watcher léger vérifie toutes les 30 minutes si un bulletin plus récent semble disponible et ne lance le traitement lourd que si nécessaire.
