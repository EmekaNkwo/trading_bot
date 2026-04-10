from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from backtest.metrics import backtest_metrics
from backtest.simulator import BacktestEngine
from config.loader import BASE_CONFIG
from core.broker import MT5Broker, load_from_csv, save_to_csv
from core.market_state import MarketStateStore
from optimizer.rollback import ParameterRollback
from portfolio.config import PORTFOLIO
from strategy.factory import build_strategy
from utils.logger import setup_logger


logger = setup_logger()


def load_rotation_base_config():
    with open(BASE_CONFIG, "r") as f:
        cfg = yaml.safe_load(f)
    cfg["_meta"] = {"source": BASE_CONFIG}
    return cfg


def get_rotation_candidate_params():
    cfg = load_rotation_base_config()
    rotation_cfg = (cfg or {}).get("rotation", {}) or {}
    return deepcopy(rotation_cfg.get("symbols", {}) or {})


# -------------------------------------------------
# METRIC COMPARISON
# -------------------------------------------------

def is_better(candidate: dict[str, Any], best: dict[str, Any] | None):
    """
    Returns True if candidate metrics are better than best metrics.
    """

    # hard rejection: must be profitable
    if float(candidate.get("profit_factor", 0.0)) <= 1.0:
        return False

    if best is None:
        return True

    # primary comparison: profit factor
    if float(candidate["profit_factor"]) > float(best["profit_factor"]):
        return True

    # secondary comparison: higher net profit
    if float(candidate["profit_factor"]) == float(best["profit_factor"]):
        if float(candidate.get("net_profit", 0.0)) > float(best.get("net_profit", 0.0)):
            return True
        if float(candidate.get("net_profit", 0.0)) == float(best.get("net_profit", 0.0)):
            return float(candidate.get("max_drawdown", 0.0)) < float(best.get("max_drawdown", 0.0))

    return False


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[float, float, float]:
    return (
        float(candidate.get("profit_factor", 0.0)),
        float(candidate.get("net_profit", 0.0)),
        -float(candidate.get("max_drawdown", 0.0)),
    )


# -------------------------------------------------
# SAVE ROTATED CONFIG
# -------------------------------------------------

def _strip_runtime_keys(config: dict[str, Any]) -> dict[str, Any]:
    clean = deepcopy(config)
    clean.pop("_meta", None)
    return clean


def save_rotated_config(config):
    """
    Save the winning rotated strategy config to disk
    and record rotation metadata.
    """

    rollback = ParameterRollback()
    path = Path("config/strategy_rotated.yaml")
    rollback.stage_previous_state(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(".tmp")

    with open(temp_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(_strip_runtime_keys(config), f, sort_keys=False)
    temp_path.replace(path)

    rollback.record_rotation(
        timestamp=datetime.utcnow().isoformat(),
        config_path=str(path)
    )


# -------------------------------------------------
# STRATEGY EVALUATION (BACKTEST)
# -------------------------------------------------

def _load_rotation_history(symbol: str, timeframe: str, bars: int):
    try:
        df = load_from_csv(symbol, timeframe)
    except FileNotFoundError:
        logger.warning(f"ROTATION | missing history for {symbol} {timeframe}, fetching from MT5")
        broker = MT5Broker()
        try:
            df = broker.get_historical_data(symbol=symbol, timeframe=timeframe, bars=bars)
            save_to_csv(df, symbol, timeframe)
        finally:
            broker.shutdown()
    return df.tail(max(1, int(bars)))


def _evaluate_candidate(
    *,
    base_config: dict[str, Any],
    symbol: str,
    candidate: dict[str, Any],
    global_min_trades: int,
) -> dict[str, Any] | None:
    strategy_name = str(candidate.get("strategy", "")).strip()
    timeframe = str(candidate.get("timeframe", "M5"))
    bars = int(candidate.get("bars", 5000))
    min_trades = int(candidate.get("min_trades", global_min_trades))
    history_window = candidate.get("history_window")
    trade_start_idx = int(candidate.get("trade_start_idx", 200))

    if not strategy_name:
        logger.warning(f"ROTATION | {symbol} candidate missing strategy name")
        return None

    try:
        df = _load_rotation_history(symbol, timeframe, bars)
    except Exception as exc:
        logger.warning(f"ROTATION | failed loading history for {symbol} {timeframe}: {exc}")
        return None

    try:
        strategy = build_strategy(strategy_name, base_config, symbol=symbol)
        market_state = MarketStateStore(base_config)
        if hasattr(strategy, "bind_market_state"):
            strategy.bind_market_state(market_state)
    except Exception as exc:
        logger.warning(f"ROTATION | failed building {strategy_name} for {symbol}: {exc}")
        return None

    engine = BacktestEngine()
    try:
        engine.run(
            df,
            strategy,
            trade_start_idx=trade_start_idx,
            history_window=history_window,
            symbol=symbol,
            timeframe=timeframe,
        )
    except Exception as exc:
        logger.warning(f"ROTATION | backtest failed for {symbol} {strategy_name}: {exc}")
        return None

    bt_cfg = (base_config or {}).get("backtest", {}) or {}
    metrics = backtest_metrics(
        engine.trades,
        engine.equity_curve,
        period_days=bt_cfg.get("period_days"),
    )
    if int(metrics.get("trades", 0)) < min_trades:
        logger.info(
            f"ROTATION | {symbol} {strategy_name} rejected "
            f"(trades={metrics.get('trades', 0)} < min_trades={min_trades})"
        )
        return None

    metrics.update(
        {
            "symbol": symbol,
            "strategy": strategy_name,
            "timeframe": timeframe,
            "bars": int(bars),
            "min_trades": int(min_trades),
        }
    )
    return metrics


def _timeframe_to_seconds(timeframe: str) -> int:
    tf = str(timeframe or "M5").upper()
    if tf.startswith("M"):
        try:
            return max(60, int(tf[1:]) * 60)
        except Exception:
            return 300
    if tf.startswith("H"):
        try:
            return max(3600, int(tf[1:]) * 3600)
        except Exception:
            return 3600
    return 300


def _portfolio_execution_defaults(symbol: str, strategy_name: str) -> dict[str, Any]:
    symbol_cfg = (PORTFOLIO.get("symbols", {}) or {}).get(symbol, {}) or {}
    if "strategies" in symbol_cfg:
        scfg = dict((symbol_cfg.get("strategies", {}) or {}).get(strategy_name, {}) or {})
        if scfg:
            return {
                "timeframe": str(scfg.get("timeframe", "M5")),
                "candle_seconds": int(scfg.get("candle_seconds", _timeframe_to_seconds(scfg.get("timeframe", "M5")))),
                "risk": float(scfg.get("risk", 0.001)),
            }

    return {
        "timeframe": str(symbol_cfg.get("timeframe", "M5")),
        "candle_seconds": int(symbol_cfg.get("candle_seconds", _timeframe_to_seconds(symbol_cfg.get("timeframe", "M5")))),
        "risk": float(symbol_cfg.get("risk", 0.001)),
    }


def _symbol_risk_budget(symbol: str) -> float:
    symbol_cfg = (PORTFOLIO.get("symbols", {}) or {}).get(symbol, {}) or {}
    if "strategies" in symbol_cfg:
        strategies_cfg = dict(symbol_cfg.get("strategies", {}) or {})
        total = sum(float((cfg or {}).get("risk", 0.0)) for cfg in strategies_cfg.values())
        return float(total)
    return float(symbol_cfg.get("risk", 0.0))


def _resolve_candidate_execution(symbol: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> dict[str, Any]:
    strategy_name = str(metrics.get("strategy") or candidate.get("strategy") or "")
    defaults = _portfolio_execution_defaults(symbol, strategy_name)
    timeframe = str(candidate.get("live_timeframe", candidate.get("timeframe", defaults["timeframe"])))
    try:
        candle_seconds = int(candidate.get("candle_seconds", _timeframe_to_seconds(timeframe)))
    except (TypeError, ValueError):
        candle_seconds = int(defaults["candle_seconds"])
    if candle_seconds <= 0:
        candle_seconds = int(defaults["candle_seconds"])

    try:
        risk = float(candidate.get("risk", defaults["risk"]))
    except (TypeError, ValueError):
        risk = float(defaults["risk"])
    if risk <= 0:
        risk = float(defaults["risk"])

    return {
        "strategy": strategy_name,
        "timeframe": timeframe,
        "candle_seconds": candle_seconds,
        "base_risk": risk,
    }


def _weight_score(candidate: dict[str, Any]) -> float:
    pf_edge = max(0.01, float(candidate.get("profit_factor", 0.0)) - 1.0)
    trades = max(1.0, float(candidate.get("trades", 0.0)))
    min_trades = max(1.0, float(candidate.get("min_trades", 1.0)))
    trade_factor = max(1.0, trades / min_trades)
    return pf_edge * trade_factor


def _normalize_selected_execution(symbol: str, winners: list[dict[str, Any]]) -> dict[str, Any]:
    budget = max(0.0, _symbol_risk_budget(symbol))
    scores = [_weight_score(item) for item in winners]
    total_score = sum(scores)
    if total_score <= 0:
        scores = [1.0 for _ in winners]
        total_score = float(len(scores))

    strategy_execution = {}
    for item, score in zip(winners, scores):
        execution = dict(item.get("execution", {}) or {})
        weight = float(score / total_score) if total_score > 0 else 0.0
        execution["risk_weight"] = round(weight, 6)
        execution["risk"] = round(budget * weight, 6)
        strategy_execution[str(item["strategy"])] = execution

    return {
        "symbol_risk_budget": round(budget, 6),
        "strategies": strategy_execution,
    }


def evaluate_rotation_candidates(base_config, candidate_params):
    rotation_cfg = (base_config or {}).get("rotation", {}) or {}
    global_min_trades = int(rotation_cfg.get("min_trades_required", 0))

    selected_strategies: dict[str, list[str]] = {}
    selected_execution: dict[str, dict[str, Any]] = {}
    per_symbol_results: dict[str, Any] = {}
    evaluated = 0
    valid_candidates = 0

    for symbol, symbol_cfg in (candidate_params or {}).items():
        candidates = list((symbol_cfg or {}).get("candidates", []) or [])
        top_winners = int((symbol_cfg or {}).get("top_winners", rotation_cfg.get("top_winners", 1)))
        top_winners = max(1, top_winners)
        symbol_valid_results: list[dict[str, Any]] = []
        symbol_valid = 0

        for candidate in candidates:
            evaluated += 1
            candidate_cfg = dict(candidate or {})
            result = _evaluate_candidate(
                base_config=base_config,
                symbol=str(symbol),
                candidate=candidate_cfg,
                global_min_trades=global_min_trades,
            )
            if result is None:
                logger.warning(f"ROTATION | {symbol} candidate invalid or insufficient")
                continue

            result["execution"] = _resolve_candidate_execution(str(symbol), candidate_cfg, result)
            symbol_valid += 1
            valid_candidates += 1
            symbol_valid_results.append(result)

        eligible_results = [
            item for item in symbol_valid_results
            if float(item.get("profit_factor", 0.0)) > 1.0
        ]
        eligible_results.sort(key=_candidate_sort_key, reverse=True)
        selected_winners = eligible_results[:top_winners]

        per_symbol_results[str(symbol)] = {
            "evaluated_candidates": int(len(candidates)),
            "valid_candidates": int(symbol_valid),
            "selected": deepcopy(selected_winners),
        }
        if not selected_winners:
            continue

        selected_strategies[str(symbol)] = [str(item["strategy"]) for item in selected_winners]
        selected_execution[str(symbol)] = _normalize_selected_execution(str(symbol), selected_winners)

    rotated_config = None
    if selected_strategies:
        rotated_config = {
            "rotation": {
                "selected_strategies": dict(selected_strategies),
                "selected_execution": deepcopy(selected_execution),
            }
        }

    return {
        "best_result": deepcopy(selected_strategies),
        "best_config": rotated_config,
        "best_params": {
            "selected_strategies": deepcopy(selected_strategies),
            "selected_execution": deepcopy(selected_execution),
            "symbols": per_symbol_results,
        },
        "evaluated_candidates": int(evaluated),
        "valid_candidates": int(valid_candidates),
        "selected_strategies": deepcopy(selected_strategies),
        "selected_execution": deepcopy(selected_execution),
        "by_symbol": per_symbol_results,
    }


def save_best_rotation(best_config, best_result=None, best_params=None):
    if best_config is None:
        logger.info("ROTATION COMPLETE | no valid symbol strategy winner found")
        return {
            "rotation_started": True,
            "saved": False,
            "selected_params": best_params,
            "best_result": best_result,
        }

    save_rotated_config(best_config)

    logger.info("MULTI-ASSET ROTATION COMPLETE")
    logger.info(f"SELECTED STRATEGIES: {best_result}")

    return {
        "rotation_started": True,
        "saved": True,
        "selected_params": deepcopy(best_params) if best_params is not None else None,
        "best_result": best_result,
        "selected_strategies": deepcopy(best_result or {}),
        "selected_execution": deepcopy((best_params or {}).get("selected_execution", {}) or {}),
    }


# -------------------------------------------------
# PARAMETER ROTATION
# -------------------------------------------------

def run_parameter_rotation():
    base_config = load_rotation_base_config()
    candidate_params = get_rotation_candidate_params()

    logger.info("PARAMETER ROTATION MODE STARTED")
    evaluation = evaluate_rotation_candidates(base_config, candidate_params)
    return save_best_rotation(
        evaluation["best_config"],
        best_result=evaluation["best_result"],
        best_params=evaluation["best_params"],
    )
