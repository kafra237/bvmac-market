# BVMAC Market
https://bvmac-market.duckdns.org/
**BVMAC Market** is an independent, open project that turns public BVMAC market publications into a searchable web platform.

The BVMAC (Central African Stock Exchange) mainly publishes its market information through official PDF bulletins. Those documents are useful, but they are not the easiest way to follow prices, funds, liquidity, market history or changes over time. BVMAC Market builds a reproducible chain around those public documents:

```text
Official BVMAC bulletins (PDF)
        ↓
Download and extraction pipeline
        ↓
Canonical Excel workbook
        ↓
PostgreSQL history
        ↓
API + web application
        ↓
Charts, portfolios, alerts, weekly reports and Predictive Radar
```

The project does **not** place market orders and does not provide investment advice. The Predictive Radar exposes probabilistic models with confidence information; it is designed as an analytical aid, not a trading signal.

[Lire le README en français](README.fr.md)

## What you can do with it

- browse equities, funds and the BVMAC-AS index from one interface;
- follow market history instead of opening bulletins one by one;
- inspect liquidity, bid/ask pressure and trading activity;
- create watchlists and virtual portfolios;
- receive web-push notifications and weekly email reports;
- run a 30-minute lightweight publication watch so fresh data is picked up quickly;
- explore the Predictive Radar for validated probabilistic ML targets;
- administer users, delivery channels, data quality and platform health from `/stat`;
- use the interface in French or English, on desktop, tablet, mobile browser or installed PWA.

## Repository map

| Folder | Purpose |
| --- | --- |
| `web/` | HTML, CSS, JavaScript, PWA and bilingual user interface |
| `backend/api/` | FastAPI application and business/API modules |
| `backend/jobs/` | Scheduled jobs: alerts, feeds, notifications and web analytics |
| `pipeline/` | Download and extraction of public BVMAC bulletins |
| `database/` | PostgreSQL schema, migrations and Excel snapshot importer |
| `ml/` | Production inference, validated models, model card and reliability metadata |
| `operations/` | Administration and server-operation helpers |
| `docs/` | Human-readable documentation in English and French |
| `tests/` | Unit, integration and browser-layout checks |

Only two shell entry points are exposed at the root:

- `deploy.sh` — guided deployment to a VPS;
- `checking.sh` — independent production verification.

Everything else is regular source code and can be read, tested or changed without learning a custom deployment command vocabulary.

## Quick start

### 1. Clone

```bash
git clone <your-fork-or-repository-url> bvmac-market
cd bvmac-market
```

### 2. Run the tests locally

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

### 3. Deploy

On Linux, macOS, WSL or Git Bash:

```bash
./deploy.sh
```

The script asks for:

- VPS hostname or IP;
- SSH user;
- public domain;
- administrator / Let's Encrypt email;
- whether an existing custom pipeline should be preserved or replaced.

It packages the repository, uploads it through SSH, creates a backup, verifies PostgreSQL restore capability, prepares an isolated candidate release and cuts over only after health checks pass.

Then run the independent checker:

```bash
./checking.sh
```

See [Deployment](docs/en/08-deployment-and-operations.md) before using this on a production server.

## Documentation

Start with [docs/en/00-overview.md](docs/en/00-overview.md) or [docs/fr/00-vue-d-ensemble.md](docs/fr/00-vue-d-ensemble.md).

The documentation explains the web app, backend, data pipeline, database, ML, notifications, deployment, security and contribution workflow separately.

## Data and model responsibility

The platform depends on public market documents and on the quality of their publication. A missing observation is not automatically a zero. A last traded price is not automatically an executable price. ML probabilities are estimates learned from historical observations; they are not promises about future market behaviour.

## Contributing

Bug fixes, documentation improvements, UI work, data-quality checks, tests and model research are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) before opening a pull request. [Community conduct](CODE_OF_CONDUCT.md).

## Security

Do not commit credentials, SMTP application passwords, VAPID private keys, session secrets or production database dumps. See [SECURITY.md](SECURITY.md).

## License

The source code is released under the [MIT License](LICENSE). The license covers this project’s code, not third-party market publications, trademarks or data rights. BVMAC source documents remain subject to their respective owner’s terms.
