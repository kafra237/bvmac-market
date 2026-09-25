# Contribuer

Le projet reste plus simple à reprendre lorsqu’une pull request traite un sujet à la fois.

## Avant de coder

1. Lire le document concerné dans `docs/fr/`.
2. Ouvrir une issue si le changement modifie le sens des données, la sécurité, le déploiement ou le comportement ML.
3. Ne pas modifier les règles d’extraction à la légère : le classeur Excel est le contrat entre le pipeline et le reste de la plateforme.

## Travail local

```bash
python -m unittest discover -s tests -v
python tests/qa_layout_i18n.py
python tests/qa_radar_responsive.py
bash -n deploy.sh checking.sh
```

Une pull request doit expliquer ce qui change, pourquoi, comment cela a été testé et si la base, le pipeline ou le déploiement sont concernés.

## Style

- préférer des noms clairs aux abréviations opaques ;
- commenter le « pourquoi » lorsqu’il n’est pas évident ;
- conserver la compatibilité des API et de la base lorsque c’est raisonnable ;
- ne jamais journaliser de secret ni de donnée personnelle brute ;
- ne pas publier une cible ML non validée simplement parce qu’un modèle sait produire une valeur.
