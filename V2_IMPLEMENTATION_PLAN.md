# V2 Implementation Plan

## Objective
Restructure the bot around shared context (`MarketState`) and two focused strategies:
- `xau_liquidity_reclaim`
- `xau_opening_range_displacement`

This aims to improve expectancy by reducing low-quality entries and strategy conflict.

## Current Status (Completed)
- Added shared state service: `core/market_state.py`
- Wired shared state into execution loop: `core/engine.py`, `portfolio/engine.py`
- Implemented `xau_liquidity_reclaim`: `strategy/xau_liquidity_reclaim.py`
- Implemented `xau_opening_range_displacement`: `strategy/xau_opening_range_displacement.py`
- Wired new strategies into factory/registry
- Switched portfolio defaults to v2 pair in `portfolio/config.py`
- Added v2 configuration blocks in `config/strategy.yaml`

## Phase 1: Stabilize Runtime (next)
1. Run live on demo with small risk for 2-4 weeks.
2. Validate logs for:
   - sweep detection timing
   - opening range readiness and breakout triggers
   - blocked/accepted entries by volatility/session
3. Add daily trade tags to reports:
   - setup_type (`liquidity_reclaim` or `opening_range_displacement`)
   - session (`london`, `newyork`, `offsession`)
   - volatility regime (`low`, `normal`, `high`)

Exit criteria:
- No runtime errors
- Deterministic signal behavior across restarts
- Valid trade count for both strategies

## Phase 2: Execution Hardening
1. Add spread gate at order time for both v2 strategies.
2. Add "too-late-to-enter" guard (extension in ATR at execution).
3. Add strategy-specific time-stop:
   - reclaim setup: short hold limit
   - displacement setup: moderate hold limit

Exit criteria:
- Lower average MAE
- Fewer full-SL losses caused by late execution

## Phase 3: Position Management Upgrade
1. Add optional partial at `1R`.
2. Move stop to breakeven only after structural confirmation.
3. Add conditional trailing only when trade is in expansion.

Exit criteria:
- Better realized R multiple distribution
- Lower variance in daily results

## Phase 4: Research Loop
1. Expand closed-trade reporting with feature columns.
2. Build monthly performance slices by:
   - setup type
   - session bucket
   - volatility bucket
3. Remove any setup sub-variant that has negative expectancy over enough samples.

Exit criteria:
- Positive expectancy in forward data
- Stable drawdown profile

## Metrics To Gate Promotion
- Profit factor
- Expectancy (R/trade)
- Max drawdown
- Win rate is secondary; do not optimize it alone
- Number of trades (avoid low-sample overfitting)

## Deployment Rules
1. Start with smallest risk tier.
2. Promote risk only after two consecutive profitable evaluation windows.
3. Roll back immediately on drawdown breach or setup drift.

