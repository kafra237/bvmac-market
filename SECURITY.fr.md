# Politique de sécurité

Merci de ne pas publier une faille exploitable dans une issue GitHub publique.

Ne jamais pousser dans Git : fichier `.env` de production, dump de base, mot de passe d’application SMTP, clé privée VAPID, token de session, OTP ou clé privée TLS.

L’architecture de production repose sur quelques règles simples :

- Nginx est le point d’entrée HTTPS public ;
- FastAPI écoute uniquement sur `127.0.0.1` ;
- les rôles PostgreSQL sont séparés par fonction ;
- l’administration est protégée côté serveur ;
- les API privées ne sont pas mises en cache par la PWA ;
- une sauvegarde précède le déploiement et la restauration PostgreSQL est réellement testée.

Pour signaler une faille, contacter le propriétaire du dépôt en privé avec le composant concerné, les étapes de reproduction, l’impact et, si possible, une piste de correction.
