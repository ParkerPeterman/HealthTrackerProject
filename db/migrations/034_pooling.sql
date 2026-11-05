-- B5: partial pooling within hypothesis families (a shared hyperprior per family,
-- DerSimonian-Laird) in place of a multiple-testing correction. Pooled estimates sit next to
-- each B1 estimate; COMP_BAYES_EFFECTS decides whether either reaches coaching. Re-running is safe.

ALTER TABLE analytics.effect_estimate
    ADD COLUMN IF NOT EXISTS se_sd               NUMERIC(8,4),   -- posterior scale, standardized
    ADD COLUMN IF NOT EXISTS family              TEXT,
    ADD COLUMN IF NOT EXISTS pooled_sd           NUMERIC(8,4),   -- pooled posterior mean, standardized
    ADD COLUMN IF NOT EXISTS pooled_p_meaningful NUMERIC(4,3);
