# Maintainer handover

This page is the shortest path into the codebase when you did not build it yourself.

## Read the system from left to right

A useful mental model is:

```text
BVMAC public bulletin
→ pipeline
→ canonical Excel workbook
→ PostgreSQL import/history
→ FastAPI
→ web/PWA
→ notifications, reports and Predictive Radar
```

The pipeline owns extraction. The database importer stores what the workbook contains. The API should not silently “repair” market values. This separation is important: it makes a surprising number traceable back to a source document instead of hiding corrections in several layers.

## Where to change a feature

| Need | Start here | Usually also touches |
| --- | --- | --- |
| Page layout / navigation | `web/` | `web/i18n.js`, browser QA |
| Auth / accounts | `backend/api/auth_module.py` | `email_auth.py`, database schema, security tests |
| Market endpoints | `backend/api/market.py` | `database/`, web consumers |
| Portfolios | `backend/api/portfolio_module.py` | `web/app-portfolios.html`, portfolio JS/CSS |
| Predictive Radar UI | `web/radar-app.js`, `web/radar.css` | `backend/api/ml_radar.py` |
| Predictive Radar model | `ml/` | model card, validation metadata, inference tests |
| PDF download/extraction | `pipeline/` | data-quality tests; change with care |
| Excel → PostgreSQL | `database/import_excel.py` | `database/schema.sql`, `database/application.sql` |
| Weekly email | `backend/api/weekly_email.py` | `backend/jobs/notifications.py`, admin communications |
| Push notifications | `backend/api/push_*` | `backend/jobs/notifications.py`, Service Worker |
| Feeds | `backend/api/feed_module.py` | `backend/jobs/feeds.py`, feeds web view |
| Admin `/stat` | `backend/admin/stat.html` | `web/stat-app.js`, `backend/api/admin_module.py` |
| Deployment | `deploy.sh` | `operations/`, `checking.sh` |

## Before changing the pipeline

`bvmac_downloader.py` and `bvmac_extract.py` are historical ETL components whose hashes are protected by a repository test. That is deliberate. If extraction rules genuinely need to change, treat it as a data-contract change: explain the source bulletin case, add a regression fixture/test, document the effect on the canonical workbook and then consciously update the protected fingerprint.

## Before changing ML

Start with `ml/MODEL_CARD.md`. A model belongs in the product only when its target, horizon, temporal validation, baseline comparison and confidence behaviour are explicit. Do not promote a model because it looks accurate on a random split. The BVMAC data is sparse and illiquid; naive baselines can be deceptively strong.

## Before changing the database

Prefer additive/idempotent SQL. Never delete production data just to make a migration pass. Deployment creates and restore-tests a PostgreSQL backup before mutation, but a migration should still have a clear compatibility story.

## Before changing the web UI

Authenticated user functionality belongs under `/app?view=...`. Login/registration, public information and `/stat` are intentionally separate. Any user-facing text change must be checked in both French and English. Any layout change must pass the browser QA at 360, 390, 430, 768, 820, 1024, 1366 and 1920 px.

## A good pull request

A reviewer should be able to answer five questions quickly:

1. What user/maintenance problem does this solve?
2. Which layer owns the change and why?
3. Does it alter data meaning, API compatibility, security or deployment?
4. What tests prove the change?
5. How would we recognize and recover from a failure in production?

If those answers are clear, the code is usually much easier to maintain.
