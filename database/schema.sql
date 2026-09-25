BEGIN;

CREATE SCHEMA IF NOT EXISTS market;
CREATE SCHEMA IF NOT EXISTS analytics;

CREATE TABLE IF NOT EXISTS market.import_batch (
    import_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_path TEXT NOT NULL,
    source_sha256 CHAR(64) NOT NULL UNIQUE,
    source_size_bytes BIGINT NOT NULL,
    source_mtime TIMESTAMPTZ,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    status TEXT NOT NULL CHECK (status IN ('loading','completed','failed')),
    workbook_sheets JSONB NOT NULL DEFAULT '[]'::jsonb,
    row_count BIGINT NOT NULL DEFAULT 0,
    error_message TEXT
);

CREATE TABLE IF NOT EXISTS market.excel_row (
    import_id BIGINT NOT NULL REFERENCES market.import_batch(import_id) ON DELETE RESTRICT,
    sheet_name TEXT NOT NULL,
    row_number INTEGER NOT NULL,
    data JSONB NOT NULL,
    PRIMARY KEY (import_id, sheet_name, row_number)
);
CREATE INDEX IF NOT EXISTS excel_row_sheet_import_idx ON market.excel_row (sheet_name, import_id);
CREATE INDEX IF NOT EXISTS excel_row_data_gin_idx ON market.excel_row USING gin (data jsonb_path_ops);

CREATE OR REPLACE FUNCTION market.guard_completed_excel_row() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM market.import_batch WHERE import_id=OLD.import_id AND status='completed') THEN
        RAISE EXCEPTION 'snapshot % is immutable once completed', OLD.import_id;
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END;
$$;
DROP TRIGGER IF EXISTS excel_row_immutable_completed ON market.excel_row;
CREATE TRIGGER excel_row_immutable_completed
BEFORE UPDATE OR DELETE ON market.excel_row
FOR EACH ROW EXECUTE FUNCTION market.guard_completed_excel_row();

CREATE OR REPLACE FUNCTION market.guard_completed_import_batch() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status='completed' THEN
        RAISE EXCEPTION 'completed import batch % is immutable', OLD.import_id;
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END;
$$;
DROP TRIGGER IF EXISTS import_batch_immutable_completed ON market.import_batch;
CREATE TRIGGER import_batch_immutable_completed
BEFORE UPDATE OR DELETE ON market.import_batch
FOR EACH ROW EXECUTE FUNCTION market.guard_completed_import_batch();

CREATE OR REPLACE VIEW market.current_import AS
SELECT *
FROM market.import_batch
WHERE status = 'completed'
ORDER BY completed_at DESC, import_id DESC
LIMIT 1;

CREATE OR REPLACE VIEW market.current_excel_row AS
SELECT r.import_id, r.sheet_name, r.row_number, r.data
FROM market.excel_row r
JOIN market.current_import i ON i.import_id = r.import_id;

CREATE TABLE IF NOT EXISTS analytics.visitor (
    visitor_id UUID PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    consent_level TEXT NOT NULL CHECK (consent_level IN ('product')),
    consent_version TEXT NOT NULL,
    sessions_count INTEGER NOT NULL DEFAULT 0,
    first_country_code TEXT,
    last_country_code TEXT,
    first_device_type TEXT,
    last_device_type TEXT
);
CREATE INDEX IF NOT EXISTS visitor_last_seen_idx ON analytics.visitor (last_seen_at DESC);

CREATE TABLE IF NOT EXISTS analytics.consent_event (
    consent_event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visitor_id UUID,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    consent_level TEXT NOT NULL,
    consent_version TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('accepted','refused','withdrawn','deleted'))
);
CREATE INDEX IF NOT EXISTS consent_event_occurred_idx ON analytics.consent_event (occurred_at DESC);

CREATE TABLE IF NOT EXISTS analytics.session (
    session_id UUID PRIMARY KEY,
    visitor_id UUID REFERENCES analytics.visitor(visitor_id) ON DELETE CASCADE,
    started_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ,
    event_count INTEGER NOT NULL DEFAULT 0,
    engaged_seconds INTEGER NOT NULL DEFAULT 0,
    country_code TEXT,
    country TEXT,
    region TEXT,
    city TEXT,
    device_type TEXT,
    os_family TEXT,
    browser_family TEXT,
    language TEXT,
    timezone TEXT,
    screen_width INTEGER,
    screen_height INTEGER,
    viewport_width INTEGER,
    viewport_height INTEGER,
    connection_type TEXT,
    rtt_ms INTEGER,
    downlink_mbps DOUBLE PRECISION,
    save_data BOOLEAN,
    referrer_domain TEXT,
    utm_source TEXT,
    utm_medium TEXT,
    utm_campaign TEXT,
    landing_page TEXT
);
CREATE INDEX IF NOT EXISTS session_started_idx ON analytics.session (started_at DESC);
CREATE INDEX IF NOT EXISTS session_visitor_idx ON analytics.session (visitor_id, started_at DESC);
CREATE INDEX IF NOT EXISTS session_country_idx ON analytics.session (country_code, started_at DESC);

CREATE TABLE IF NOT EXISTS analytics.event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visitor_id UUID REFERENCES analytics.visitor(visitor_id) ON DELETE CASCADE,
    session_id UUID REFERENCES analytics.session(session_id) ON DELETE CASCADE,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_name TEXT NOT NULL,
    page TEXT,
    entity_type TEXT,
    entity_id TEXT,
    country_code TEXT,
    city TEXT,
    device_type TEXT,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb,
    detailed BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS event_occurred_idx ON analytics.event (occurred_at DESC);
CREATE INDEX IF NOT EXISTS event_session_idx ON analytics.event (session_id, occurred_at);
CREATE INDEX IF NOT EXISTS event_visitor_idx ON analytics.event (visitor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS event_name_idx ON analytics.event (event_name, occurred_at DESC);
CREATE INDEX IF NOT EXISTS event_entity_idx ON analytics.event (entity_type, entity_id, occurred_at DESC) WHERE entity_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS event_search_query_idx ON analytics.event ((properties->>'query_normalized'), occurred_at DESC) WHERE event_name = 'search';
CREATE INDEX IF NOT EXISTS event_occurred_brin_idx ON analytics.event USING brin (occurred_at);

CREATE TABLE IF NOT EXISTS analytics.interest_score (
    visitor_id UUID NOT NULL REFERENCES analytics.visitor(visitor_id) ON DELETE CASCADE,
    dimension TEXT NOT NULL,
    interest_key TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (visitor_id, dimension, interest_key)
);
CREATE INDEX IF NOT EXISTS interest_last_seen_idx ON analytics.interest_score (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS interest_dimension_idx ON analytics.interest_score (dimension, interest_key, last_seen_at DESC);

-- Historique anonymisé et agrégé : aucune suppression automatique.
CREATE TABLE IF NOT EXISTS analytics.daily_summary (
    day DATE PRIMARY KEY,
    sessions BIGINT NOT NULL DEFAULT 0,
    unique_visitors BIGINT NOT NULL DEFAULT 0,
    events BIGINT NOT NULL DEFAULT 0,
    searches BIGINT NOT NULL DEFAULT 0,
    engaged_seconds BIGINT NOT NULL DEFAULT 0,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS analytics.daily_breakdown (
    day DATE NOT NULL,
    dimension TEXT NOT NULL,
    key TEXT NOT NULL,
    event_count BIGINT NOT NULL DEFAULT 0,
    unique_visitors BIGINT NOT NULL DEFAULT 0,
    total_value DOUBLE PRECISION,
    generated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (day, dimension, key)
);
CREATE INDEX IF NOT EXISTS daily_breakdown_dimension_idx ON analytics.daily_breakdown (dimension, day DESC);

CREATE TABLE IF NOT EXISTS analytics.admin_session (
    token_hash CHAR(64) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    user_agent_hash CHAR(64)
);
CREATE INDEX IF NOT EXISTS admin_session_exp_idx ON analytics.admin_session (expires_at);

REVOKE ALL ON SCHEMA market FROM PUBLIC;
REVOKE ALL ON SCHEMA analytics FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA market FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA analytics FROM PUBLIC;

GRANT USAGE ON SCHEMA market TO bvmacapi, bvmacimport;
GRANT SELECT ON ALL TABLES IN SCHEMA market TO bvmacapi;
GRANT SELECT, INSERT, UPDATE ON market.import_batch TO bvmacimport;
GRANT SELECT, INSERT, DELETE ON market.excel_row TO bvmacimport;
GRANT SELECT ON market.current_import, market.current_excel_row TO bvmacimport;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA market TO bvmacimport;

GRANT USAGE ON SCHEMA analytics TO bvmacapi;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA analytics TO bvmacapi;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA analytics TO bvmacapi;

ALTER DEFAULT PRIVILEGES IN SCHEMA market REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA analytics REVOKE ALL ON TABLES FROM PUBLIC;

COMMIT;
