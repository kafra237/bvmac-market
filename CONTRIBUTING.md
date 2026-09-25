# Contributing

BVMAC Market is easier to maintain when a pull request changes one concern at a time.

## Before coding

1. Read the relevant document under `docs/en/`.
2. Open an issue when the change alters data meaning, security, deployment or ML behaviour.
3. Never rewrite the extraction rules casually: the Excel workbook is the contract between the pipeline and the rest of the platform.

## Development workflow

Create a branch, install `requirements.txt` and `requirements-dev.txt`, then run:

```bash
python -m unittest discover -s tests -v
python tests/qa_layout_i18n.py
python tests/qa_radar_responsive.py
bash -n deploy.sh checking.sh
```

A pull request should explain what changed, why it changed, how it was tested and whether data/schema/deployment behaviour changed.

## Code style

- Prefer clear names over abbreviations.
- Add a short module/function comment when the reason for a piece of code is not obvious.
- Keep API/database changes backward compatible when practical.
- Never log secrets or raw personal data.
- Keep ML targets and validation evidence explicit; do not expose an unvalidated prediction just because a model can produce it.

## Commit scope

Useful prefixes are optional but practical: `web:`, `api:`, `pipeline:`, `db:`, `ml:`, `ops:`, `docs:` and `tests:`.
