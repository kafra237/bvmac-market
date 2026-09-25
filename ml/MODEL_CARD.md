# BVMAC Predictive Radar — Model Card

Generated from market data through **2026-09-23**.

## Production candidates

### 1. Action liquidity — at least one exchange in the next 5 observed bulletins
Model: HistGradientBoosting classifier, calibrated with out-of-sample Platt mapping.
Validation: 6 chronological half-year windows (2024H1 → 2026H2), purged at the target boundary.
Mean AUC: **0.775**; minimum AUC: **0.694**; mean Brier skill vs global prevalence: **+0.304**.
It also beats the ticker-history baseline on average (AUC ~0.775 vs ~0.747), although the uplift is modest and varies by ticker.

### 2. Action medium-horizon upside — close higher after 20 observed bulletins
Model: HistGradientBoosting classifier + Platt calibration.
Mean AUC: **0.717**; minimum AUC: **0.628** in the primary six-window run.
This signal remains when ticker/sector/country identity columns are removed (AUC ~0.726), indicating that it is not only learning which companies historically rise more often. Calibration is weaker than liquidity; predictions must be confidence-gated per ticker.

### 3. OPCVM downside risk — next newly published NAV lower than current NAV
The raw NAV table was deduplicated first: **4,825 unique fund/date observations** instead of 26,565 repeated bulletin rows.
Validation across 6 chronological half-years: mean AUC **0.780**, minimum **0.609**, mean precision-recall lift for the rare downside event **8.4×**, mean Brier skill **0.119**.
This is a risk-ranking model, not a promised return forecast. Reliability varies substantially by fund because some funds have very few historical declines.

## Statistical indicators retained but not marketed as ML

- Next-session action activity: ML performs similarly to simple recent activity; use as a contextual probability, not a flagship model.
- 20-observation action liquidity: strong pooled AUC, but historical ticker liquidity is often just as good or better. Prefer an empirical/hybrid liquidity score.
- Market turnover: absolute turnover can be fitted, but relative-to-recent turnover does not remain stable enough. Use broad statistical ranges only.

## Rejected from the initial validated model set

- Exact action return at 5/20/60 observations: all tested regressors underperform naive baselines.
- Exact OPCVM return: underperforms naive baselines after NAV deduplication.
- BVMAC-AS direction at 5/20 observations: unstable, mean AUC around chance and negative Brier skill.
- Action downside at 20/60 observations: unstable across regimes.
- Exact next-trade window (1 / 2–5 / 6–20 / >20): insufficiently stable vs ticker-specific baselines.
- Per-ticker standalone ML models: datasets are too small and unstable; pooled models generalize better.

## Validation safeguards

1. Chronological walk-forward only — no random train/test split.
2. Purge of rows whose target horizon crosses into the test period.
3. Comparison against naive and ticker-specific historical baselines.
4. Separate analysis by ticker/fund; confidence lowered where local OOS evidence is weak.
5. Feature leakage audit. A future-derived `next_trade_distance` variable was detected during testing, removed, and all action validation was rerun from scratch.
6. OPCVM duplicate-publication leakage was detected and eliminated before final OPCVM validation.
7. Exact-return models were rejected despite superficially attractive experiments because they failed baseline tests.

## Product rule

Never display a forecast without its horizon, probability/range, confidence level, historical validation context, and a clear statement that it is probabilistic rather than investment advice. Low-confidence outputs should be hidden or marked experimental.
