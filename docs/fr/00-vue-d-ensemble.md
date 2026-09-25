# Vue d’ensemble

BVMAC Market rend les informations publiques de la BVMAC plus faciles à suivre dans le temps. Le bulletin PDF officiel reste la source ; le projet le télécharge, extrait ses tableaux, conserve des snapshots dans PostgreSQL et les présente dans une application web.

Pour quelqu’un de non technique, on peut voir la plateforme comme une bibliothèque avec un bibliothécaire automatique : le pipeline collecte les nouveaux documents, la base conserve le catalogue et l’historique, le backend répond aux questions et l’interface transforme ces réponses en écrans lisibles.
