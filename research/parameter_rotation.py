import yaml
from copy import deepcopy
from datetime import datetime

from config.loader import load_config
from strategy.xau_trend import XAUTrendStrategy
from optimizer.rollback import ParameterRollback
from utils.logger import setup_logger

from backtest.simulator import BacktestEngine
from backtest.metrics import backtest_metrics
from core.broker import load_from_csv


logger = setup_logger()


# -------------------------------------------------
# METRIC COMPARISON
# -------------------------------------------------

def is_better(candidate, best):
    """
    Returns True if candidate metrics are better than best metrics.
    """

    if best is None:
        return True

    # hard rejection: must be profitable
    if candidate["profit_factor"] <= 1.0:
        return False

    # primary comparison: profit factor
    if candidate["profit_factor"] > best["profit_factor"]:
        return True

    # secondary comparison: lower drawdown
    if candidate["profit_factor"] == best["profit_factor"]:
        return candidate["max_drawdown"] < best["max_drawdown"]

    return False


# -------------------------------------------------
# SAVE ROTATED CONFIG
# -------------------------------------------------

def save_rotated_config(config):
    """
    Save the winning rotated strategy config to disk
    and record rotation metadata.
    """

    path = "config/strategy_rotated.yaml"

    with open(path, "w") as f:
        yaml.safe_dump(config, f, sort_keys=False)

    rollback = ParameterRollback()
    rollback.record_rotation(
        timestamp=datetime.utcnow().isoformat(),
        config_path=path
    )


# -------------------------------------------------
# STRATEGY EVALUATION (BACKTEST)
# -------------------------------------------------

def evaluate_strategy(strategy, config):
    """
    Evaluates a strategy using backtest metrics.
    Returns a metrics dict or None if invalid.
    """

    df = load_from_csv("XAUUSDm", "M15")

    engine = BacktestEngine()
    engine.run(df, strategy)

    bt_cfg = config["strategy"].get("backtest", {})
    period_days = bt_cfg.get("period_days")
    min_trades = bt_cfg.get("min_trades", 0)

    metrics = backtest_metrics(
        engine.trades,
        engine.equity_curve,
        period_days=period_days
    )

    # enforce minimum trades
    if metrics["trades"] < min_trades:
        return None

    return metrics


# -------------------------------------------------
# PARAMETER ROTATION
# -------------------------------------------------

def run_parameter_rotation():

    base_config = load_config()

    candidate_params = [
        {"atr_period": 14, "sl_mult": 2.0, "rr": 2.0},
        {"atr_period": 21, "sl_mult": 2.5, "rr": 2.5},
        # extend here later
    ]

    best_result = None
    best_config = None

    logger.info("PARAMETER ROTATION MODE STARTED")

    for params in candidate_params:

        cfg = deepcopy(base_config)
        
        cfg["strategy"]["backtest"] = base_config["strategy"].get("backtest", {})

        # inject rotated parameters
        cfg["strategy"]["atr"]["period"] = params["atr_period"]
        cfg["strategy"]["atr"]["sl_multiplier"] = params["sl_mult"]
        cfg["strategy"]["atr"]["rr_ratio"] = params["rr"]

        strategy = XAUTrendStrategy(cfg)

        result = evaluate_strategy(strategy, cfg)

        if result is None:
            logger.warning("ROTATION | invalid parameters (min_trades not met)")
            continue

        if is_better(result, best_result):
            best_result = result         
            best_config = cfg

    if best_config is None:
        logger.info("ROTATION COMPLETE | no valid parameter set found")
        return

    save_rotated_config(best_config)

    logger.info("PARAMETER ROTATION COMPLETE")
    logger.info("SELECTED PARAMETERS:")
    logger.info(best_config)
