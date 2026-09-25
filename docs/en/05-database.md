# Database

PostgreSQL stores both current application state and historical imports. The Excel importer hashes each workbook. A genuinely new workbook becomes a new immutable import batch, which makes it possible to understand how the published dataset evolved.

Schemas separate concerns such as market data, authentication, administration, portfolios and ML predictions. Database roles also have different permissions: the API should not automatically have the same rights as the import process or log importer.

When changing SQL, prefer additive migrations and keep existing data readable. Production deployment creates a custom-format `pg_dump` and restores it into a disposable database before applying changes.
