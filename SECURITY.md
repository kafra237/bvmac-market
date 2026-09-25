# Security policy

Please do not publish exploitable security issues in a public GitHub issue.

Sensitive material must never be committed: production `.env` files, database dumps, SMTP app passwords, VAPID private keys, session tokens, OTPs or TLS private keys.

The production architecture assumes:

- Nginx is the public HTTPS entry point;
- the FastAPI backend listens on `127.0.0.1` only;
- PostgreSQL roles are separated by purpose;
- administrator routes are protected server-side;
- private API responses are network-only in the PWA Service Worker;
- backups are made before deployment and PostgreSQL restore is actually tested.

If you discover a vulnerability, contact the repository owner privately with the affected component, reproduction steps, impact and any suggested fix.
