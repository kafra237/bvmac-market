CREATE TABLE IF NOT EXISTS admin.communication_campaign (
    campaign_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kind TEXT NOT NULL CHECK(kind IN ('email','weekly_email','push')),
    subject TEXT,
    body TEXT,
    target_url TEXT,
    requested_by TEXT,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed')),
    recipient_count INTEGER NOT NULL DEFAULT 0,
    sent_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS communication_campaign_created_idx ON admin.communication_campaign(created_at DESC);

CREATE TABLE IF NOT EXISTS admin.communication_recipient (
    campaign_id BIGINT NOT NULL REFERENCES admin.communication_campaign(campaign_id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','sent','failed','skipped')),
    sent_count INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    attempted_at TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    PRIMARY KEY(campaign_id,user_id)
);
CREATE INDEX IF NOT EXISTS communication_recipient_status_idx ON admin.communication_recipient(campaign_id,status);

CREATE TABLE IF NOT EXISTS admin.market_data_push_state (
    singleton BOOLEAN PRIMARY KEY DEFAULT true CHECK(singleton),
    last_import_id BIGINT,
    last_data_date_id INTEGER,
    notified_at TIMESTAMPTZ,
    sent_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
