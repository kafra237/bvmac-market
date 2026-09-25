# Application web

Le dossier `web/` contient ce qui s’exécute dans le navigateur. Les fonctions connectées utilisent l’URL canonique `/app?view=...`. Les pages de connexion, d’information et de démonstration restent séparées parce que leur nature est différente.

L’interface est responsive du petit téléphone au grand écran. La navigation dépend des routes et non de la position des boutons, ce qui évite qu’un nouvel onglet décale les libellés existants. `i18n.js` gère le français et l’anglais, y compris les contenus ajoutés après le chargement initial.

La PWA détecte aussi les nouvelles releases grâce à `/version.json`. Le déploiement injecte un identifiant unique dans les assets ; un appareil n’a donc pas besoin de vider son cache manuellement après chaque mise à jour.
