# Backend and API

`backend/api/` is the FastAPI application. `bvmac_api.py` is the entry point and assembles smaller modules instead of keeping every endpoint in one file.

Important modules include authentication, market data, portfolios, Radar, feeds, push notifications, weekly email, administration and first-party browser analytics. Shared cryptography/database/origin helpers live in `common.py`.

The API is private by default for detailed market and account data. Nginx exposes it over HTTPS, while uvicorn itself listens only on localhost.

`backend/jobs/` contains processes that run on schedules rather than HTTP requests: feed collection, alert evaluation, notification dispatch and Nginx statistics import.

## API compatibility namespace

Some HTTP routes still begin with `/api/v3/`. This is an API compatibility namespace, not the project name or release number. It is intentionally kept stable so existing browsers, installed PWAs and integrations do not break when the repository is reorganized. A future API namespace should only be introduced for a real breaking contract change.
