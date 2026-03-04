-- Add classifier column to track which classifier was used per request.
-- Values: 'heuristic' | 'learned'
-- Using IF NOT EXISTS so this is safe to re-run on existing databases.

ALTER TABLE usage_events
    ADD COLUMN IF NOT EXISTS classifier TEXT NOT NULL DEFAULT 'heuristic';

CREATE INDEX IF NOT EXISTS idx_usage_classifier_ts ON usage_events (classifier, ts DESC);
