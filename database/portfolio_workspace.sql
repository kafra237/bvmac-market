-- Additive and idempotent; old application versions ignore this column.
ALTER TABLE portfolio.portfolio
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
