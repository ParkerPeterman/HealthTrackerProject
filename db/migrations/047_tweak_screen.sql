-- F2 / B6: what goes with a groin tweak. Rare events, so a Bayesian logistic model with strong
-- priors (Normal(0, 0.5) on log-odds per SD), fitted by Laplace approximation. Factors: game
-- minutes, sprint metres, jump contacts, a cold day, a short night, travel, each over the tweak
-- day and the one before. Exploratory only: never a rule, never advice. Re-running is safe.

CREATE TABLE IF NOT EXISTS analytics.tweak_screen (
    computed_on  DATE NOT NULL,
    factor       TEXT NOT NULL,         -- game_min, sprint_m, plyo, cold, short_sleep, travel
    n_days       INT NOT NULL,
    n_events     INT NOT NULL,
    log_or       NUMERIC(8,3) NOT NULL, -- per SD of the factor (yes vs no for yes/no factors)
    log_or_sd    NUMERIC(8,3) NOT NULL,
    odds_ratio   NUMERIC(8,2) NOT NULL,
    or_lo80      NUMERIC(8,2) NOT NULL,
    or_hi80      NUMERIC(8,2) NOT NULL,
    p_higher     NUMERIC(4,3) NOT NULL, -- P(odds ratio > 1)
    PRIMARY KEY (computed_on, factor)
);
