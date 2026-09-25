CREATE TABLE IF NOT EXISTS auth.notification_inbox (
    notification_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES auth.user_account(user_id) ON DELETE CASCADE,
    event_key TEXT NOT NULL,
    category TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    target_url TEXT NOT NULL DEFAULT '/app',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    read_at TIMESTAMPTZ,
    UNIQUE(user_id,event_key)
);
CREATE INDEX IF NOT EXISTS notification_inbox_user_idx ON auth.notification_inbox(user_id,notification_id DESC);
INSERT INTO auth.notification_inbox(user_id,event_key,category,title,body,target_url,created_at)
SELECT DISTINCT ON (user_id,event_key) user_id,event_key,category,title,body,target_url,attempted_at
FROM auth.notification_delivery ORDER BY user_id,event_key,attempted_at
ON CONFLICT(user_id,event_key) DO NOTHING;
