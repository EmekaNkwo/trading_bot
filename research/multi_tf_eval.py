"""
Multi-timeframe backtest evaluation.

For every (symbol, strategy, timeframe) combination, runs a full backtest
and prints a ranked summary showing the best timeframe per strategy.

Usage:
    python research/multi_tf_eval.py --symbol XAUUSDm
    python research/multi_tf_eval.py --symbol US30m
    python research/multi_tf_eval.py --symbol all
"""
from __future__ import annotations

import argparse
import functools
import logging
import sys
from pathlib import Path

import pandas as pd

print = functools.partial(print, flush=True)  # type: ignore[assignment]

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)

from config.loader import load_config
from core.broker import MT5Broker, load_from_csv, save_to_csv
from core.market_state import MarketStateStore
from strategy.factory import build_strategy
from backtest.simulator import BacktestEngine
from backtest.metrics import backtest_metrics

CANDLE_SECONDS = {"M5": 300, "M15": 900, "M30": 1800, "H1": 3600}
TIMEFRAMES_TO_TEST = ["M5", "M15", "M30", "H1"]

SYMBOL_STRATEGIES: dict[str, list[str]] = {
    "XAUUSDm": [
        "xau_liquidity_reclaim",
        "xau_opening_range_displacement",
        "xau_sweep",
        "xau_regime",
    ],
    "US30m": [
        "us30_open_wick",
        "us30_asia_eq",
        "us30_trend_pullback",
        "us30_supply_demand",
        "us30_fib_retrace",
    ],
    "DE30m": [
        "ger30_three_pin_breakout",
    ],
    "BTCUSDm": [
        "btc_bos_retest",
        "multi_asset_regime",
    ],
}

BARS_PER_TF = {"M5": 25000, "M15": 15000, "M30": 10000, "H1": 8000}


def _load_history(symbol: str, timeframe: str, bars: int, broker: MT5Broker) -> pd.DataFrame:
    try:
        df = load_from_csv(symbol, timeframe)
        if len(df) >= bars * 0.8:
            return df
    except FileNotFoundError:
        pass
    df = broker.get_historical_data(symbol=symbol, timeframe=timeframe, bars=bars)
    save_to_csv(df, symbol, timeframe)
    return df


def _run_backtest(
    strategy_name: str,
    symbol: str,
    timeframe: str,
    df: pd.DataFrame,
    config: dict,
) -> dict | None:
    market_state = MarketStateStore(config)
    strategy = build_strategy(strategy_name, config, symbol=symbol)
    if hasattr(strategy, "bind_market_state"):
        strategy.bind_market_state(market_state)

    bt = BacktestEngine(starting_balance=10000, risk_per_trade=0.005)
    bt.run(df, strategy, trade_start_idx=200, history_window=800, symbol=symbol, timeframe=timeframe)
    metrics = backtest_metrics(bt.trades, bt.equity_curve)

    trades = int(metrics.get("trades", 0))
    if trades < 3:
        return None

    return {
        "symbol": symbol,
        "strategy": strategy_name,
        "timeframe": timeframe,
        "trades": trades,
        "win_rate": round(float(metrics.get("win_rate", 0)), 1),
        "profit_factor": round(float(metrics.get("profit_factor", 0)), 2),
        "max_drawdown": round(float(metrics.get("max_drawdown", 0)), 2),
        "net_profit": round(float(metrics.get("net_profit", 0)), 2),
    }


def run_symbol(
    symbol: str,
    strategies: list[str],
    config: dict,
    broker: MT5Broker,
    timeframes: list[str],
) -> pd.DataFrame:
    rows: list[dict] = []

    data_cache: dict[str, pd.DataFrame] = {}
    for tf in timeframes:
        bars = BARS_PER_TF.get(tf, 15000)
        try:
            df = _load_history(symbol, tf, bars, broker)
            data_cache[tf] = df
            print(f"  Loaded {symbol} {tf}: {len(df)} bars")
        except Exception as exc:
            print(f"  SKIP {symbol} {tf}: data fetch failed ({exc})")

    for strategy_name in strategies:
        print(f"\n  {strategy_name}:")
        for tf in timeframes:
            if tf not in data_cache:
                continue
            df = data_cache[tf]
            try:
                result = _run_backtest(strategy_name, symbol, tf, df, config)
            except Exception as exc:
                print(f"    {tf}: ERROR ({exc})")
                continue
            if result:
                print(
                    f"    {tf}: trades={result['trades']:>4}  "
                    f"WR={result['win_rate']:>5.1f}%  "
                    f"PF={result['profit_factor']:>5.2f}  "
                    f"DD={result['max_drawdown']:>8.2f}  "
                    f"net={result['net_profit']:>8.2f}"
                )
                rows.append(result)
            else:
                print(f"    {tf}: <3 trades")

    return pd.DataFrame(rows) if rows else pd.DataFrame()


def pick_best_timeframe(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    scored = df.copy()
    max_dd = scored["max_drawdown"].replace(0, 1).abs().max()
    scored["score"] = (
        scored["profit_factor"].clip(upper=5.0) * 0.35
        + scored["win_rate"] / 100.0 * 0.25
        + (1 - scored["max_drawdown"].abs() / max_dd) * 0.20
        + scored["net_profit"].clip(lower=0) / max(scored["net_profit"].clip(lower=0).max(), 1) * 0.20
    )
    best_idx = scored.groupby("strategy")["score"].idxmax()
    return scored.loc[best_idx].sort_values("score", ascending=False).reset_index(drop=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Multi-timeframe backtest evaluation")
    parser.add_argument("--symbol", default="all")
    parser.add_argument("--timeframes", nargs="+", default=TIMEFRAMES_TO_TEST)
    args = parser.parse_args()

    timeframes = [tf.upper() for tf in args.timeframes]
    config = load_config()
    broker = MT5Broker()

    symbols = list(SYMBOL_STRATEGIES.keys()) if args.symbol.lower() == "all" else [args.symbol]

    all_results: list[pd.DataFrame] = []

    try:
        for symbol in symbols:
            strategies = SYMBOL_STRATEGIES.get(symbol)
            if not strategies:
                print(f"No strategies configured for {symbol}, skipping.")
                continue
            print(f"\n{'='*70}")
            print(f"  {symbol} — {len(strategies)} strategies × {len(timeframes)} timeframes")
            print(f"{'='*70}")

            result = run_symbol(symbol, strategies, config, broker, timeframes)
            if not result.empty:
                all_results.append(result)
    finally:
        try:
            broker.shutdown()
        except Exception:
            pass

    if not all_results:
        print("\nNo results produced.")
        return 1

    full = pd.concat(all_results, ignore_index=True)

    out_dir = Path("reports")
    out_dir.mkdir(parents=True, exist_ok=True)
    full.to_csv(out_dir / "multi_tf_full_results.csv", index=False)

    print(f"\n{'='*70}")
    print("  ALL RESULTS")
    print(f"{'='*70}")
    print(full.to_string(index=False))

    best = pick_best_timeframe(full)
    best.to_csv(out_dir / "multi_tf_best_per_strategy.csv", index=False)

    print(f"\n{'='*70}")
    print("  BEST TIMEFRAME PER STRATEGY (ranked by composite score)")
    print(f"{'='*70}")
    cols = ["symbol", "strategy", "timeframe", "trades", "win_rate", "profit_factor", "max_drawdown", "net_profit", "score"]
    print(best[cols].to_string(index=False))

    print(f"\n\nSaved: reports/multi_tf_full_results.csv")
    print(f"Saved: reports/multi_tf_best_per_strategy.csv")

    print(f"\n{'='*70}")
    print("  RECOMMENDED portfolio/config.py UPDATES:")
    print(f"{'='*70}")
    for _, row in best.iterrows():
        cs = CANDLE_SECONDS.get(row["timeframe"], 300)
        print(
            f'  "{row["strategy"]}": '
            f'{{"timeframe": "{row["timeframe"]}", "candle_seconds": {cs}, ...}}  '
            f'(PF={row["profit_factor"]}, WR={row["win_rate"]}%, trades={row["trades"]})'
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
