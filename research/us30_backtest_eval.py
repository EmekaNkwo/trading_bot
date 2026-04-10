from __future__ import annotations

import argparse
import logging
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backtest.metrics import backtest_metrics
from backtest.simulator import BacktestEngine
from config.loader import load_config
from core.broker import MT5Broker, load_from_csv, save_to_csv
from core.market_state import MarketStateStore
from strategy.factory import build_strategy


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
        df = load_from_csv(symbol, timeframe)
    except FileNotFoundError:
        broker = MT5Broker()
        try:
            df = broker.get_historical_data(symbol=symbol, timeframe=timeframe, bars=bars)
            save_to_csv(df, symbol, timeframe)
        finally:
            broker.shutdown()
    return df.tail(bars).copy()


def _parse_args() -> argparse.Namespace:
    cfg = load_config()
    wf_cfg = (cfg or {}).get("walkforward", {}) or {}
    default_strategies = wf_cfg.get("strategies") or [wf_cfg.get("strategy", "us30_open_wick")]

    parser = argparse.ArgumentParser(description="Run quick backtests for US30 strategies.")
    parser.add_argument("--symbol", default=str(wf_cfg.get("symbol", "US30m")))
    parser.add_argument("--timeframe", default=str(wf_cfg.get("timeframe", "M5")))
    parser.add_argument("--bars", type=int, default=5000)
    parser.add_argument(
        "--strategies",
        nargs="+",
        default=[str(name) for name in default_strategies if str(name).strip()],
    )
    parser.add_argument(
        "--output",
        default="reports/us30_backtest_summary.csv",
        help="CSV output path for the summary table.",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    config = load_config()
    df = _load_history(args.symbol, args.timeframe, args.bars)
    strategies = list(dict.fromkeys(str(name).strip() for name in args.strategies if str(name).strip()))

    changed = _set_console_log_level(logging.WARNING)
    try:
        rows = []
        for strategy_name in strategies:
            print(f"Backtesting {strategy_name}...")
            market_state = MarketStateStore(config)
            strategy = build_strategy(strategy_name, config, symbol=args.symbol)
            if hasattr(strategy, "bind_market_state"):
                strategy.bind_market_state(market_state)

            engine = BacktestEngine()
            engine.run(
                df,
                strategy,
                trade_start_idx=200,
                history_window=None,
                symbol=args.symbol,
                timeframe=args.timeframe,
            )
            metrics = backtest_metrics(engine.trades, engine.equity_curve)
            metrics["strategy"] = strategy_name
            rows.append(metrics)
    finally:
        _restore_console_log_level(changed)

    summary = pd.DataFrame(rows)[
        ["strategy", "trades", "win_rate", "profit_factor", "max_drawdown", "net_profit"]
    ].sort_values(["profit_factor", "net_profit"], ascending=[False, False])

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary.to_csv(output, index=False)

    print()
    print(summary.to_string(index=False))
    print()
    print(f"Saved summary to {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
