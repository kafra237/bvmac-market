# Security and privacy

Passwords are hashed with Argon2id. Contact details are encrypted before storage. Administrative and account sessions are server-controlled, and state-changing administrative routes use server-side protections rather than relying on hidden URLs.

The browser Service Worker treats private API routes as network-only. First-party analytics pseudonymise visitor identifiers before database storage. Production secrets live under `/etc/bvmac`, not in the repository.

The deployment checker verifies that the backend is bound to `127.0.0.1` and that Nginx/TLS are the public path. Never weaken authentication, CSRF, TLS or role permissions just to make a failing test disappear.
