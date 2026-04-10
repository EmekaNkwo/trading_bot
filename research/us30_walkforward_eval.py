from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config.loader import load_config
from core.broker import MT5Broker, load_from_csv, save_to_csv
from core.market_state import MarketStateStore
from strategy.factory import build_strategy
from walkforward.engine import WalkForwardEngine
from walkforward.report import summarize_walkforward_by_strategy


def _set_console_log_level(level: int):
    logger = logging.getLogger("trading_bot")
    changed = []
    for handler in getattr(logger, "handlers", []):
        if isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.FileHandler):
            old = handler.level
            handler.setLevel(level)
            changed.append((handler, old))
    return changed


def _restore_console_log_level(changed):
    for handler, old in changed:
        try:
            handler.setLevel(old)
        except Exception:
            pass


def _load_history(symbol: str, timeframe: str, bars: int) -> pd.DataFrame:
    try:
        return load_from_csv(symbol, timeframe)
    except FileNotFoundError:
        broker = MT5Broker()
        try:
            df = broker.get_historical_data(symbol=symbol, timeframe=timeframe, bars=bars)
            save_to_csv(df, symbol, timeframe)
            return df
        finally:
            broker.shutdown()


def _evaluate_strategy(
    *,
    strategy_name: str,
    df: pd.DataFrame,
    config: dict,
    symbol: str,
    timeframe: str,
    train_bars: int,
    test_bars: int,
    step_bars: int,
) -> pd.DataFrame:
    wf = WalkForwardEngine(
        train_bars=train_bars,
        test_bars=test_bars,
        step_bars=step_bars,
    )
    market_state = MarketStateStore(config)
    results = wf.run(
        df,
        strategy_factory=lambda: _build_strategy(strategy_name, config, symbol, market_state),
        symbol=symbol,
        timeframe=timeframe,
    )
    if results.empty:
        return results
    tagged = results.copy()
    tagged["strategy"] = strategy_name
    return tagged


def _build_strategy(strategy_name: str, config: dict, symbol: str, market_state: MarketStateStore):
    strategy = build_strategy(strategy_name, config, symbol=symbol)
    if hasattr(strategy, "bind_market_state"):
        strategy.bind_market_state(market_state)
    return strategy


def _parse_args() -> argparse.Namespace:
    cfg = load_config()
    wf_cfg = (cfg or {}).get("walkforward", {}) or {}
    default_strategies = wf_cfg.get("strategies") or [wf_cfg.get("strategy", "us30_open_wick")]

    parser = argparse.ArgumentParser(description="Run walk-forward evaluation for US30 strategies.")
    parser.add_argument("--symbol", default=str(wf_cfg.get("symbol", "US30m")))
    parser.add_argument("--timeframe", default=str(wf_cfg.get("timeframe", "M5")))
    parser.add_argument("--bars", type=int, default=int(wf_cfg.get("bars", 20000)))
    parser.add_argument("--train-bars", type=int, default=int(wf_cfg.get("train_bars", 6000)))
    parser.add_argument("--test-bars", type=int, default=int(wf_cfg.get("test_bars", 2000)))
    parser.add_argument("--step-bars", type=int, default=int(wf_cfg.get("step_bars", 2000)))
    parser.add_argument("--min-trades", type=int, default=int(wf_cfg.get("min_trades", 10)))
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=[str(name) for name in default_strategies if str(name).strip()],
        help="Strategy names to evaluate.",
    )
    parser.add_argument(
        "--output-prefix",
        default="reports/us30_walkforward",
        help="Output path prefix without file extension.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_config()
    df = _load_history(args.symbol, args.timeframe, args.bars)

    strategies = list(dict.fromkeys(str(name).strip() for name in args.strategies if str(name).strip()))
    console_log_state = _set_console_log_level(logging.WARNING)
    try:
        strategy_frames = []
        for strategy_name in strategies:
            print(f"Running {strategy_name}...")
            result = _evaluate_strategy(
                strategy_name=strategy_name,
                df=df,
                config=config,
                symbol=args.symbol,
                timeframe=args.timeframe,
                train_bars=args.train_bars,
                test_bars=args.test_bars,
                step_bars=args.step_bars,
            )
            if result.empty:
                print(f"  no windows produced for {strategy_name}")
                continue
            trades = int(result["trades"].sum()) if "trades" in result.columns else 0
            print(f"  windows={len(result)} trades={trades}")
            strategy_frames.append(result)
    finally:
        _restore_console_log_level(console_log_state)

    if not strategy_frames:
        print("No walk-forward results were produced.")
        return 1

    detailed = pd.concat(strategy_frames, ignore_index=True)
    summary_rows = summarize_walkforward_by_strategy(detailed)
    summary = pd.DataFrame(summary_rows)

    total_trades = int(detailed["trades"].sum()) if "trades" in detailed.columns else 0
    if total_trades < int(args.min_trades):
        print(f"Invalid run: only {total_trades} trades (min required: {int(args.min_trades)})")
        return 1

    output_prefix = Path(args.output_prefix)
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    detailed.to_csv(output_prefix.with_name(output_prefix.name + "_report.csv"), index=False)
    summary.to_csv(output_prefix.with_name(output_prefix.name + "_summary.csv"), index=False)

    print()
    print(summary.to_string(index=False))
    print()
    print(f"Saved detailed report to {output_prefix.with_name(output_prefix.name + '_report.csv')}")
    print(f"Saved summary report to {output_prefix.with_name(output_prefix.name + '_summary.csv')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
