# Sécurité et confidentialité

Les mots de passe sont hashés avec Argon2id et les coordonnées sensibles sont chiffrées avant stockage. Les sessions sont contrôlées côté serveur. Les pages privées ne doivent jamais être « protégées » uniquement en les cachant avec JavaScript.

Le Service Worker ne met pas en cache les réponses API privées. Les statistiques first-party pseudonymisent les visiteurs avant l’insertion en base. Les secrets de production vivent sous `/etc/bvmac` et ne doivent jamais entrer dans Git.

Le checker vérifie que FastAPI écoute uniquement sur `127.0.0.1` et que Nginx/HTTPS reste le chemin public.
