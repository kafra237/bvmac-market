# Radar prédictif et machine learning

Le dossier `ml/` contient l’inférence de production. Toutes les prédictions possibles ne sont pas exposées : seules les cibles qui ont gardé un signal lors de validations chronologiques hors échantillon ont été retenues.

Le Radar couvre notamment l’activité de cotation à court terme, une probabilité de cours supérieur à moyen horizon et le risque de baisse de la prochaine nouvelle VL d’un OPCVM. Le projet ne présente pas de prix futur exact comme une cible validée.

`MODEL_CARD.md` décrit les limites et les résultats de validation. Les fichiers de fiabilité servent à distinguer la probabilité prédite de la confiance accordée au modèle sur un titre ou un fonds.

Le calcul ML intervient après un import réussi et reste séparé de l’ETL PDF.
