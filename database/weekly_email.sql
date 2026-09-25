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
CREATE UNIQUE INDEX IF NOT EXISTS weekly_email_scheduled_period_uidx ON admin.weekly_email_run(period_start) WHERE trigger='scheduled';
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
