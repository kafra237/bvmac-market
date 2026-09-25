# Tests / Tests

The test suite covers security contracts, database integration, targeted communications, repository structure, responsive layouts and the bilingual interface. Browser QA checks the main screens from 360 px to 1920 px and also simulates PWA standalone mode.

La suite couvre les contrats de sécurité, l’intégration PostgreSQL, les communications ciblées, la structure du dépôt, le responsive et l’interface bilingue. La QA navigateur teste les principaux écrans de 360 px à 1920 px et simule aussi le mode PWA standalone.

GitHub Actions runs the full suite with an ephemeral PostgreSQL instance. Local tests that need PostgreSQL are skipped when no test DSN is configured.
