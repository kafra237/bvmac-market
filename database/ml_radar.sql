BEGIN;
CREATE SCHEMA IF NOT EXISTS ml;

CREATE TABLE IF NOT EXISTS ml.prediction_run (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    import_id BIGINT NOT NULL,
    model_version TEXT NOT NULL,
    data_through DATE,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    action_count INTEGER NOT NULL DEFAULT 0,
    opcvm_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    UNIQUE(import_id, model_version)
);

CREATE TABLE IF NOT EXISTS ml.action_prediction (
    run_id BIGINT NOT NULL REFERENCES ml.prediction_run(run_id) ON DELETE CASCADE,
    company_id TEXT,
    ticker TEXT NOT NULL,
    prediction_date DATE NOT NULL,
    close_price DOUBLE PRECISION,
    vol_bid DOUBLE PRECISION,
    vol_ask DOUBLE PRECISION,
    vol_traded DOUBLE PRECISION,
    book_imbalance DOUBLE PRECISION,
    trade_recent20_rate DOUBLE PRECISION,
    trade_1_prob DOUBLE PRECISION,
    trade1_confidence TEXT,
    trade_5_prob DOUBLE PRECISION,
    trade5_confidence TEXT,
    trade_20_stat_prob DOUBLE PRECISION,
    up_20_prob DOUBLE PRECISION,
    up20_confidence TEXT,
    trade5_factors JSONB NOT NULL DEFAULT '[]'::jsonb,
    up20_factors JSONB NOT NULL DEFAULT '[]'::jsonb,
    PRIMARY KEY (run_id, ticker)
);
CREATE INDEX IF NOT EXISTS action_prediction_ticker_run_idx ON ml.action_prediction(ticker, run_id DESC);

CREATE TABLE IF NOT EXISTS ml.opcvm_prediction (
    run_id BIGINT NOT NULL REFERENCES ml.prediction_run(run_id) ON DELETE CASCADE,
    fund_id TEXT NOT NULL,
    fund_name TEXT,
    category TEXT,
    valuation_frequency TEXT,
    nav_date DATE NOT NULL,
    nav DOUBLE PRECISION,
    risk_down_next_nav DOUBLE PRECISION,
    confidence TEXT,
    history_points INTEGER,
    PRIMARY KEY (run_id, fund_id)
);
CREATE INDEX IF NOT EXISTS opcvm_prediction_fund_run_idx ON ml.opcvm_prediction(fund_id, run_id DESC);

CREATE OR REPLACE VIEW ml.latest_run AS
SELECT * FROM ml.prediction_run
WHERE status='completed'
ORDER BY generated_at DESC, run_id DESC
LIMIT 1;

CREATE OR REPLACE VIEW ml.latest_action_prediction AS
SELECT p.* FROM ml.action_prediction p JOIN ml.latest_run r USING(run_id);

CREATE OR REPLACE VIEW ml.latest_opcvm_prediction AS
SELECT p.* FROM ml.opcvm_prediction p JOIN ml.latest_run r USING(run_id);

GRANT USAGE ON SCHEMA ml TO __API_ROLE__, __IMPORT_ROLE__;
GRANT SELECT ON ALL TABLES IN SCHEMA ml TO __API_ROLE__;
GRANT SELECT, INSERT, UPDATE, DELETE ON ml.prediction_run, ml.action_prediction, ml.opcvm_prediction TO __IMPORT_ROLE__;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA ml TO __IMPORT_ROLE__;
COMMIT;
