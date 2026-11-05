-- C5: structured insight records. Each insight carries its claim as fields (claim type,
-- exposure, outcome, effect with units, interval, probability and confidence word, data
-- window, what would change it); the sentence is rendered from them by code, and the LLM
-- guard rejects any number or confidence word that isn't in the record. Re-running is safe.

ALTER TABLE analytics.insight
    ADD COLUMN IF NOT EXISTS claim_type        TEXT,
    ADD COLUMN IF NOT EXISTS effect_unit       TEXT,
    ADD COLUMN IF NOT EXISTS probability       NUMERIC(4,3),
    ADD COLUMN IF NOT EXISTS probability_kind  TEXT CHECK (probability_kind IN ('direction', 'meaningful')),
    ADD COLUMN IF NOT EXISTS confidence        TEXT CHECK (confidence IN ('too early to tell', 'possibly', 'likely', 'very likely')),
    ADD COLUMN IF NOT EXISTS what_would_change TEXT,
    ADD COLUMN IF NOT EXISTS shown             BOOLEAN NOT NULL DEFAULT TRUE,  -- false: rests on a poor-tier measure
    ADD COLUMN IF NOT EXISTS record            JSONB;
