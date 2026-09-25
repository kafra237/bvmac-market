BEGIN;

CREATE SCHEMA IF NOT EXISTS auth;
CREATE SCHEMA IF NOT EXISTS portfolio;
CREATE SCHEMA IF NOT EXISTS feedback;
CREATE SCHEMA IF NOT EXISTS webstats;
CREATE SCHEMA IF NOT EXISTS admin;

-- Comptes : email/téléphone sont chiffrés côté application (AES-GCM).
-- Les *_lookup_hash sont des HMAC pour l'unicité et les recherches sans déchiffrement.
CREATE TABLE IF NOT EXISTS auth.user_account (
    user_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username TEXT NOT NULL,
    username_norm TEXT NOT NULL UNIQUE,
    email_cipher BYTEA NOT NULL,
    email_lookup_hash CHAR(64) NOT NULL UNIQUE,
    phone_cipher BYTEA,
    phone_lookup_hash CHAR(64),
    country TEXT NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user' CHECK (role IN ('user','admin')),
    status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','disabled')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_login_at TIMESTAMPTZ,
    must_change_password BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS user_account_created_idx ON auth.user_account(created_at DESC);
CREATE INDEX IF NOT EXISTS user_account_phone_lookup_idx ON auth.user_account(phone_lookup_hash) WHERE phone_lookup_hash IS NOT NULL;
ALTER TABLE auth.user_account ADD COLUMN IF NOT EXISTS must_change_password BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS auth.login_guard (
    user_id BIGINT PRIMARY KEY REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    failures_in_cycle INTEGER NOT NULL DEFAULT 0,
    lock_strikes INTEGER NOT NULL DEFAULT 0,
    strike_window_started_at TIMESTAMPTZ,
    locked_until TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth.client_throttle (
    client_hash CHAR(64) PRIMARY KEY,
    failure_count INTEGER NOT NULL DEFAULT 0,
    window_started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    locked_until TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth.user_session (
    token_hash CHAR(64) PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    csrf_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    user_agent_hash CHAR(64),
    client_hash CHAR(64),
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS user_session_user_idx ON auth.user_session(user_id, expires_at DESC);
CREATE INDEX IF NOT EXISTS user_session_exp_idx ON auth.user_session(expires_at);


CREATE TABLE IF NOT EXISTS auth.watchlist (
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(user_id,company_id)
);

CREATE TABLE IF NOT EXISTS auth.alert_rule (
    rule_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    company_id INTEGER,
    kind TEXT NOT NULL CHECK(kind IN ('price_above','price_below','volume_above','new_import','quality_error')),
    threshold DOUBLE PRECISION,
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alert_rule_user_idx ON auth.alert_rule(user_id,active);

CREATE TABLE IF NOT EXISTS auth.alert_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id BIGINT NOT NULL REFERENCES auth.alert_rule(rule_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    import_id BIGINT,
    condition_key TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at TIMESTAMPTZ,
    UNIQUE(rule_id,import_id,condition_key)
);
CREATE INDEX IF NOT EXISTS alert_event_user_idx ON auth.alert_event(user_id,created_at DESC);

CREATE TABLE IF NOT EXISTS auth.password_help_request (
    request_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    username_supplied TEXT NOT NULL,
    email_cipher BYTEA NOT NULL,
    phone_cipher BYTEA,
    matched_user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE SET NULL,
    client_hash CHAR(64),
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewing','resolved','rejected')),
    admin_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS password_help_status_idx ON auth.password_help_request(status, created_at DESC);

CREATE TABLE IF NOT EXISTS auth.audit_log (
    audit_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor_user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE SET NULL,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id TEXT,
    client_hash CHAR(64),
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS auth_audit_occurred_idx ON auth.audit_log(occurred_at DESC);
CREATE INDEX IF NOT EXISTS auth_audit_action_idx ON auth.audit_log(action, occurred_at DESC);

CREATE TABLE IF NOT EXISTS feedback.report (
    report_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE SET NULL,
    category TEXT NOT NULL CHECK (category IN ('suggestion','anomaly','data_quality','security','other')),
    subject TEXT NOT NULL,
    message TEXT NOT NULL,
    page TEXT,
    status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','reviewing','resolved','rejected')),
    priority TEXT NOT NULL DEFAULT 'normal' CHECK (priority IN ('low','normal','high','critical')),
    admin_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS feedback_status_idx ON feedback.report(status, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.portfolio (
    portfolio_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    start_date DATE NOT NULL,
    initial_amount NUMERIC(24,4) NOT NULL CHECK (initial_amount > 0),
    currency TEXT NOT NULL DEFAULT 'XAF',
    cash_initial NUMERIC(24,4) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);
CREATE INDEX IF NOT EXISTS portfolio_user_idx ON portfolio.portfolio(user_id, created_at DESC);

CREATE TABLE IF NOT EXISTS portfolio.position (
    portfolio_id BIGINT NOT NULL REFERENCES portfolio.portfolio(portfolio_id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL,
    input_mode TEXT NOT NULL CHECK (input_mode IN ('percent','amount','quantity')),
    input_value NUMERIC(24,6) NOT NULL CHECK (input_value >= 0),
    lot_size INTEGER NOT NULL DEFAULT 1 CHECK (lot_size >= 1),
    quantity NUMERIC(24,6) NOT NULL CHECK (quantity >= 0),
    start_price NUMERIC(24,6) NOT NULL CHECK (start_price > 0),
    allocated_amount NUMERIC(24,4) NOT NULL CHECK (allocated_amount >= 0),
    PRIMARY KEY (portfolio_id, company_id)
);

CREATE TABLE IF NOT EXISTS portfolio.instrument_rule (
    company_id INTEGER PRIMARY KEY,
    lot_size INTEGER NOT NULL DEFAULT 1 CHECK (lot_size >= 1),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS portfolio.saved_screen (
    screen_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    filters JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, name)
);

CREATE TABLE IF NOT EXISTS webstats.request_event (
    request_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    host TEXT NOT NULL,
    visitor_hash CHAR(64) NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    method TEXT NOT NULL,
    path TEXT NOT NULL,
    status INTEGER NOT NULL,
    request_time_ms INTEGER,
    referrer_domain TEXT,
    user_agent_family TEXT,
    device_type TEXT,
    is_bot BOOLEAN NOT NULL DEFAULT false
);
CREATE INDEX IF NOT EXISTS webstats_req_time_idx ON webstats.request_event(host, occurred_at DESC);
CREATE INDEX IF NOT EXISTS webstats_req_visitor_idx ON webstats.request_event(host, visitor_hash, occurred_at DESC);
CREATE INDEX IF NOT EXISTS webstats_req_path_idx ON webstats.request_event(host, path, occurred_at DESC);

CREATE TABLE IF NOT EXISTS webstats.visitor_day (
    host TEXT NOT NULL,
    visitor_hash CHAR(64) NOT NULL,
    day DATE NOT NULL,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    request_count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(host, visitor_hash, day)
);
CREATE INDEX IF NOT EXISTS webstats_visitor_day_idx ON webstats.visitor_day(host, day DESC);

CREATE TABLE IF NOT EXISTS webstats.import_run (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    source_file TEXT,
    raw_lines INTEGER NOT NULL DEFAULT 0,
    inserted_lines INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running','completed','failed')),
    error_message TEXT
);


-- Analytics first-party navigateur. Le visiteur reçoit un identifiant UUID aléatoire
-- dans un cookie HttpOnly ; aucun identifiant personnel n'est placé dans ce cookie.
CREATE TABLE IF NOT EXISTS webstats.browser_visitor (
    visitor_id UUID PRIMARY KEY,
    first_seen_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    sessions_count INTEGER NOT NULL DEFAULT 0,
    last_user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS browser_visitor_last_idx ON webstats.browser_visitor(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS browser_visitor_user_idx ON webstats.browser_visitor(last_user_id,last_seen_at DESC) WHERE last_user_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS webstats.browser_session (
    session_id UUID PRIMARY KEY,
    visitor_id UUID NOT NULL REFERENCES webstats.browser_visitor(visitor_id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE SET NULL,
    started_at TIMESTAMPTZ NOT NULL,
    last_seen_at TIMESTAMPTZ NOT NULL,
    landing_page TEXT,
    referrer_domain TEXT,
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
    user_agent TEXT
);
CREATE INDEX IF NOT EXISTS browser_session_visitor_idx ON webstats.browser_session(visitor_id,started_at DESC);
CREATE INDEX IF NOT EXISTS browser_session_user_idx ON webstats.browser_session(user_id,started_at DESC) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS browser_session_time_idx ON webstats.browser_session(started_at DESC);

CREATE TABLE IF NOT EXISTS webstats.browser_event (
    event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    visitor_id UUID NOT NULL REFERENCES webstats.browser_visitor(visitor_id) ON DELETE CASCADE,
    session_id UUID NOT NULL REFERENCES webstats.browser_session(session_id) ON DELETE CASCADE,
    user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE SET NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    event_name TEXT NOT NULL,
    path TEXT NOT NULL,
    entity_type TEXT,
    entity_id TEXT,
    properties JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE INDEX IF NOT EXISTS browser_event_time_idx ON webstats.browser_event(occurred_at DESC);
CREATE INDEX IF NOT EXISTS browser_event_visitor_idx ON webstats.browser_event(visitor_id,occurred_at DESC);
CREATE INDEX IF NOT EXISTS browser_event_user_idx ON webstats.browser_event(user_id,occurred_at DESC) WHERE user_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS browser_event_name_idx ON webstats.browser_event(event_name,occurred_at DESC);
CREATE INDEX IF NOT EXISTS browser_event_entity_idx ON webstats.browser_event(entity_type,entity_id,occurred_at DESC) WHERE entity_id IS NOT NULL;

-- Carnets complets futurs : l'Excel courant ne fournit que les volumes agrégés bid/ask.
CREATE TABLE IF NOT EXISTS market.orderbook_snapshot (
    snapshot_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    company_id INTEGER NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL DEFAULT 'future_feed',
    bid_total NUMERIC(24,6),
    ask_total NUMERIC(24,6),
    best_bid NUMERIC(24,6),
    best_ask NUMERIC(24,6),
    levels JSONB NOT NULL DEFAULT '[]'::jsonb,
    UNIQUE(company_id, captured_at, source)
);
CREATE INDEX IF NOT EXISTS orderbook_company_time_idx ON market.orderbook_snapshot(company_id, captured_at DESC);

CREATE TABLE IF NOT EXISTS admin.session (
    token_hash CHAR(64) PRIMARY KEY,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    user_agent_hash CHAR(64)
);
CREATE INDEX IF NOT EXISTS admin_session_exp_idx ON admin.session(expires_at);

CREATE TABLE IF NOT EXISTS auth.recovery_code (
    user_id BIGINT PRIMARY KEY REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    code_hash CHAR(64) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE auth.user_account ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMPTZ;
ALTER TABLE auth.user_account ADD COLUMN IF NOT EXISTS registration_pending BOOLEAN NOT NULL DEFAULT false;
ALTER TABLE auth.user_account ADD COLUMN IF NOT EXISTS profile JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE admin.session ADD COLUMN IF NOT EXISTS tab_hash CHAR(64);
ALTER TABLE admin.session ADD COLUMN IF NOT EXISTS admin_name TEXT;
CREATE TABLE IF NOT EXISTS auth.human_challenge (
 ticket_hash CHAR(64) PRIMARY KEY, device_hash CHAR(64) NOT NULL, purpose TEXT NOT NULL,
 answer_hash CHAR(64) NOT NULL, expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS human_device_idx ON auth.human_challenge(device_hash,purpose);
CREATE TABLE IF NOT EXISTS auth.mail_quota (
 quota_key TEXT PRIMARY KEY, day DATE NOT NULL, cycles INTEGER NOT NULL, sends INTEGER NOT NULL, started_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS auth.email_challenge (
 ticket_hash CHAR(64) PRIMARY KEY, user_id BIGINT REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
 email_hash CHAR(64) NOT NULL, device_hash CHAR(64) NOT NULL, purpose TEXT NOT NULL,
 secret_hash CHAR(64) NOT NULL, expires_at TIMESTAMPTZ NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
 consumed_at TIMESTAMPTZ, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS email_challenge_secret_idx ON auth.email_challenge(secret_hash);
CREATE INDEX IF NOT EXISTS email_challenge_email_idx ON auth.email_challenge(email_hash,purpose);
CREATE TABLE IF NOT EXISTS admin.console_challenge (
 ticket CHAR(48) PRIMARY KEY, tab_hash CHAR(64) NOT NULL, admin_name TEXT NOT NULL,
 code_hash CHAR(64), attempts INTEGER NOT NULL DEFAULT 0, expires_at TIMESTAMPTZ NOT NULL
);
CREATE TABLE IF NOT EXISTS admin.mail_settings (
 singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton), api_key_cipher BYTEA,
 sender TEXT NOT NULL DEFAULT '', template_html TEXT NOT NULL,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);



-- Weekly market email tables.
-- Absence de ligne = préférence par défaut activée, y compris pour les comptes existants.
CREATE TABLE IF NOT EXISTS auth.email_preference (
    user_id BIGINT PRIMARY KEY REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    weekly_market_summary BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS admin.weekly_email_run (
    run_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    previous_start DATE NOT NULL,
    previous_end DATE NOT NULL,
    trigger TEXT NOT NULL CHECK(trigger IN ('scheduled','manual')),
    requested_by TEXT,
    subject TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
    recipient_count INTEGER NOT NULL DEFAULT 0,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS weekly_email_scheduled_period_uidx
    ON admin.weekly_email_run(period_start) WHERE trigger='scheduled';
CREATE INDEX IF NOT EXISTS weekly_email_run_created_idx ON admin.weekly_email_run(created_at DESC);

CREATE TABLE IF NOT EXISTS admin.weekly_email_delivery (
    delivery_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES admin.weekly_email_run(run_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK(status IN ('sent','failed')),
    error TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    UNIQUE(run_id,user_id)
);
CREATE INDEX IF NOT EXISTS weekly_email_delivery_run_idx ON admin.weekly_email_delivery(run_id,status);

-- Notifications Web Push / PWA.
ALTER TABLE portfolio.saved_screen ADD COLUMN IF NOT EXISTS notify BOOLEAN NOT NULL DEFAULT true;

CREATE TABLE IF NOT EXISTS auth.push_subscription (
    subscription_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    endpoint TEXT NOT NULL,
    p256dh TEXT NOT NULL,
    auth_secret TEXT NOT NULL,
    user_agent_hash CHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_success_at TIMESTAMPTZ,
    last_failure_at TIMESTAMPTZ,
    failure_count INTEGER NOT NULL DEFAULT 0,
    disabled_at TIMESTAMPTZ,
    UNIQUE(user_id, endpoint)
);
CREATE INDEX IF NOT EXISTS push_subscription_user_idx ON auth.push_subscription(user_id, disabled_at);

CREATE TABLE IF NOT EXISTS auth.notification_preference (
    user_id BIGINT PRIMARY KEY REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    market_updates BOOLEAN NOT NULL DEFAULT true,
    alerts BOOLEAN NOT NULL DEFAULT true,
    screeners BOOLEAN NOT NULL DEFAULT true,
    opcvm BOOLEAN NOT NULL DEFAULT true,
    portfolio BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS auth.notification_delivery (
    delivery_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    subscription_id BIGINT REFERENCES auth.push_subscription(subscription_id) ON DELETE SET NULL,
    event_key TEXT NOT NULL,
    category TEXT NOT NULL CHECK(category IN ('market','alert','screener','opcvm','portfolio')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    target_url TEXT NOT NULL DEFAULT '/app',
    status TEXT NOT NULL CHECK(status IN ('pending','sent','failed','skipped')),
    error TEXT,
    attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_id, subscription_id, event_key)
);
CREATE INDEX IF NOT EXISTS notification_delivery_user_idx ON auth.notification_delivery(user_id, attempted_at DESC);
CREATE INDEX IF NOT EXISTS notification_delivery_event_idx ON auth.notification_delivery(event_key, attempted_at DESC);

CREATE TABLE IF NOT EXISTS auth.screener_state (
    screen_id BIGINT NOT NULL REFERENCES portfolio.saved_screen(screen_id) ON DELETE CASCADE,
    company_id INTEGER NOT NULL,
    is_matching BOOLEAN NOT NULL DEFAULT false,
    last_import_id BIGINT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY(screen_id, company_id)
);


-- Global notification controls, admin communications and BVMAC RSS monitoring.
CREATE TABLE IF NOT EXISTS admin.notification_settings (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
    market_digest BOOLEAN NOT NULL DEFAULT true,
    alerts BOOLEAN NOT NULL DEFAULT true,
    screeners BOOLEAN NOT NULL DEFAULT true,
    opcvm BOOLEAN NOT NULL DEFAULT true,
    portfolio BOOLEAN NOT NULL DEFAULT true,
    feeds_avis BOOLEAN NOT NULL DEFAULT true,
    feeds_communique BOOLEAN NOT NULL DEFAULT true,
    platform_updates BOOLEAN NOT NULL DEFAULT true,
    admin_communications BOOLEAN NOT NULL DEFAULT true,
    weekly_digest BOOLEAN NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO admin.notification_settings(singleton) VALUES(true) ON CONFLICT(singleton) DO NOTHING;

-- Screener notification activation is controlled globally by the administrator.
-- Les anciens choix individuels ne doivent donc plus neutraliser un screener existant.
UPDATE portfolio.saved_screen SET notify=true WHERE notify IS DISTINCT FROM true;

CREATE TABLE IF NOT EXISTS admin.notification_broadcast (
    broadcast_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('admin','platform_update')),
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    target_url TEXT NOT NULL DEFAULT '/app',
    created_by TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    sent_at TIMESTAMPTZ,
    sent_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS market.bvmac_communication (
    wp_id BIGINT PRIMARY KEY,
    publication_type VARCHAR(20) NOT NULL CHECK(publication_type IN ('avis','communique')),
    title TEXT NOT NULL,
    url TEXT,
    guid TEXT NOT NULL,
    published_at TIMESTAMPTZ,
    author TEXT,
    detected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    source_feed TEXT NOT NULL CHECK(source_feed IN ('avis','communique'))
);
CREATE INDEX IF NOT EXISTS bvmac_communication_published_idx ON market.bvmac_communication(published_at DESC, wp_id DESC);
CREATE INDEX IF NOT EXISTS bvmac_communication_type_idx ON market.bvmac_communication(publication_type, published_at DESC);

CREATE TABLE IF NOT EXISTS admin.feed_monitor (
    feed TEXT PRIMARY KEY CHECK(feed IN ('avis','communique')),
    feed_url TEXT NOT NULL,
    bootstrap_completed BOOLEAN NOT NULL DEFAULT false,
    etag TEXT,
    last_modified TEXT,
    last_check TIMESTAMPTZ,
    last_success TIMESTAMPTZ,
    items_received INTEGER NOT NULL DEFAULT 0,
    new_items_total BIGINT NOT NULL DEFAULT 0,
    errors_total BIGINT NOT NULL DEFAULT 0,
    last_error TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Extends delivery categories while preserving existing history.
ALTER TABLE auth.notification_delivery DROP CONSTRAINT IF EXISTS notification_delivery_category_check;
ALTER TABLE auth.notification_delivery ADD CONSTRAINT notification_delivery_category_check
    CHECK(category IN ('market','alert','screener','opcvm','portfolio','feed_avis','feed_communique','platform','communication','weekly'));

REVOKE ALL ON SCHEMA auth, portfolio, feedback, webstats, admin FROM PUBLIC;
REVOKE ALL ON ALL TABLES IN SCHEMA auth, portfolio, feedback, webstats, admin FROM PUBLIC;

GRANT USAGE ON SCHEMA auth, portfolio, feedback, webstats, admin, market TO __API_ROLE__;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA auth, portfolio, feedback, admin TO __API_ROLE__;
GRANT SELECT ON ALL TABLES IN SCHEMA webstats TO __API_ROLE__;
GRANT INSERT, UPDATE ON webstats.browser_visitor, webstats.browser_session, webstats.browser_event TO __API_ROLE__;
GRANT USAGE, SELECT ON SEQUENCE webstats.browser_event_event_id_seq TO __API_ROLE__;
GRANT SELECT, INSERT, UPDATE, DELETE ON market.orderbook_snapshot, market.bvmac_communication TO __API_ROLE__;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA auth, portfolio, feedback, admin, market TO __API_ROLE__;

GRANT USAGE ON SCHEMA webstats TO __STATS_ROLE__;
GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA webstats TO __STATS_ROLE__;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA webstats TO __STATS_ROLE__;

ALTER DEFAULT PRIVILEGES IN SCHEMA auth REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA portfolio REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA feedback REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA webstats REVOKE ALL ON TABLES FROM PUBLIC;
ALTER DEFAULT PRIVILEGES IN SCHEMA admin REVOKE ALL ON TABLES FROM PUBLIC;

COMMIT;
