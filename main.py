import time
import logging
import os
import threading
from datetime import datetime, timedelta

from utils.logger import setup_logger
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials
from utils.runtime_state import STATE

from core.orchestrator import BotOrchestrator
from core.risk import RiskManager

from core.broker import MT5Broker, save_to_csv, load_from_csv

from backtest.simulator import BacktestEngine
from backtest.metrics import backtest_metrics
from backtest.report import export_trades

from walkforward.engine import WalkForwardEngine
from walkforward.report import summarize_walkforward

from research.parameter_rotation import run_parameter_rotation
from reports.performance import daily_summary

from strategy.xau_trend import XAUTrendStrategy
from strategy.factory import build_strategy
from config.loader import load_config

from portfolio.engine import PortfolioEngine
from utils.crash_handler import CrashHandler
from utils.heartbeat import Heartbeat



# ---------------------------------------------------
# GLOBALS
# ---------------------------------------------------

logger = setup_logger()
_tg = get_telegram_credentials()
notifier = TelegramNotifier(_tg.token, _tg.chat_id)

def _start_monitoring_api_background() -> None:
    """
    Starts an HTTP API for external monitoring.
    Defaults to localhost only. Set API_TOKEN to protect remote access.
    """
    if os.getenv("MONITORING_API_DISABLED", "").strip() in {"1", "true", "TRUE", "yes", "YES"}:
        return
    try:
        import uvicorn  # noqa: F401
        from api.server import app
    except Exception as e:
        logger.warning(f"Monitoring API not started (missing deps?): {e}")
        return

    host = os.getenv("MONITORING_API_HOST", "127.0.0.1")
    port = int(os.getenv("MONITORING_API_PORT", "8000"))
    log_level = os.getenv("MONITORING_API_LOG_LEVEL", "warning")

    def _run():
        import uvicorn
        uvicorn.run(app, host=host, port=port, log_level=log_level)

    t = threading.Thread(target=_run, name="monitoring-api", daemon=True)
    t.start()


def _set_console_log_level(level: int):
    """
    Reduce noisy console logs (keep file logs intact).
    Returns a list of (handler, old_level) to restore.
    """
    lg = logging.getLogger("trading_bot")
    changed = []
    for h in getattr(lg, "handlers", []):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            old = h.level
            h.setLevel(level)
            changed.append((h, old))
    return changed


def _restore_console_log_level(changed):
    for h, old in changed:
        try:
            h.setLevel(old)
        except Exception:
            pass


# ---------------------------------------------------
# DATA MODULE
# ---------------------------------------------------

def run_data_module():
    broker = MT5Broker()

    df = broker.get_historical_data(
        symbol="XAUUSDm",
        timeframe="M15",
        bars=3000
    )

    save_to_csv(df, "XAUUSDm", "M15")
    broker.shutdown()


# ---------------------------------------------------
# BACKTEST MODULE
# ---------------------------------------------------

def run_backtest_module():

    logger.info("BACKTEST MODE STARTED")
    notifier.send("BACKTEST MODE STARTED")

    config = load_config()
    try:
        df = load_from_csv("XAUUSDm", "M15")
    except FileNotFoundError:
        logger.warning("Historical data missing, fetching from MT5")
        broker = MT5Broker()
        df = broker.get_historical_data(
            symbol="XAUUSDm",
            timeframe="M15",
            bars=5000
        )
        save_to_csv(df, "XAUUSDm", "M15")
        broker.shutdown()

    strategy = XAUTrendStrategy(config)
    engine = BacktestEngine()

    # Backtests are verbose (strategy logs every candle). Keep console quiet.
    changed = _set_console_log_level(logging.WARNING)
    try:
        final_balance = engine.run(df, strategy)
    finally:
        _restore_console_log_level(changed)

    # -----------------------------------------
    # Backtest config (from strategy.yaml)
    # -----------------------------------------
    bt_cfg = config.get("backtest", {})
    period_days = bt_cfg.get("period_days")
    min_trades = bt_cfg.get("min_trades", 0)

    metrics = backtest_metrics(
        engine.trades,
        engine.equity_curve,
        period_days=period_days
    )
    
    

    logger.info(f"BACKTEST COMPLETE | BALANCE={round(final_balance, 2)}")
    notifier.send(f"BACKTEST COMPLETE | BALANCE={round(final_balance, 2)}")
    logger.info(f"BACKTEST WINDOW: last {period_days} days")
    notifier.send(f"BACKTEST WINDOW: last {period_days} days")
    logger.info(f"METRICS: {metrics}")
    notifier.send(f"METRICS: {metrics}")

    # -----------------------------------------
    # Minimum trades validation
    # -----------------------------------------
    if metrics["trades"] < min_trades:
        logger.warning(
            f"BACKTEST INVALID | only {metrics['trades']} trades "
            f"(min required: {min_trades})"
        )
        notifier.send(
            f"BACKTEST INVALID | only {metrics['trades']} trades "
            f"(min required: {min_trades})"
        )
        return  # ← stop here, do NOT treat as valid research result

    # -----------------------------------------
    # Export only if valid
    # -----------------------------------------
    export_trades(engine.trades)



# ---------------------------------------------------
# WALK-FORWARD MODULE
# ---------------------------------------------------

def run_walkforward_module():

    logger.info("WALK-FORWARD MODE STARTED")
    notifier.send("WALK-FORWARD MODE STARTED")

    config = load_config()
    wf_cfg = (config or {}).get("walkforward", {}) or {}
    symbol = str(wf_cfg.get("symbol", "XAUUSDm"))
    timeframe = str(wf_cfg.get("timeframe", "M5"))

    try:
        df = load_from_csv(symbol, timeframe)
    except FileNotFoundError:
        logger.warning("Historical data missing for walk-forward, fetching from MT5")
        broker = MT5Broker()
        bars = int(wf_cfg.get("bars", 20000))
        df = broker.get_historical_data(
            symbol=symbol,
            timeframe=timeframe,
            bars=bars,
        )
        save_to_csv(df, symbol, timeframe)
        broker.shutdown()

    # -----------------------------------------
    # Strategy backtest constraints (shared)
    # -----------------------------------------
    wt_cfg = config.get("backtest", {}) or {}
    min_trades = int(wf_cfg.get("min_trades", wt_cfg.get("min_trades", 0)))

    wf = WalkForwardEngine(
        train_bars=int(wf_cfg.get("train_bars", 6000)),
        test_bars=int(wf_cfg.get("test_bars", 2000)),
        step_bars=int(wf_cfg.get("step_bars", 2000)),
    )

    # Walk-forward is also verbose; keep console quiet.
    changed = _set_console_log_level(logging.WARNING)
    try:
        strat_name = str(wf_cfg.get("strategy", "xau_sweep"))
        results = wf.run(
            df,
            strategy_factory=lambda: build_strategy(strat_name, config)
        )
    finally:
        _restore_console_log_level(changed)

    if results.empty:
        logger.warning("WALK-FORWARD INVALID | no results produced")
        notifier.send("WALK-FORWARD INVALID | no results produced")
        return

    # -----------------------------------------
    # Aggregate trade count
    # -----------------------------------------
    total_trades = results["trades"].sum()

    if total_trades < min_trades:
        logger.warning(
            f"WALK-FORWARD INVALID | only {total_trades} trades "
            f"(min required: {min_trades})"
        )
        notifier.send(
            f"WALK-FORWARD INVALID | only {total_trades} trades "
            f"(min required: {min_trades})"
        )
        return

    # -----------------------------------------
    # Save & summarize only if valid
    # -----------------------------------------
    results.to_csv("reports/walkforward_report.csv", index=False)

    summary = summarize_walkforward(results)

    logger.info("WALK-FORWARD COMPLETE")
    notifier.send("WALK-FORWARD COMPLETE")
    logger.info(summary)
    notifier.send(summary)



# ---------------------------------------------------
# PORTFOLIO LIVE MODULE
# ---------------------------------------------------

def run_portfolio_live(orchestrator):

    portfolio = PortfolioEngine()
    
    logger.info("LIVE MODE STARTED | MT5 OK | PORTFOLIO ACTIVE")
    notifier.send("LIVE MODE STARTED | PORTFOLIO ACTIVE")

    portfolio.run(
        allow_fn=lambda: orchestrator.decide_mode() == "live"
    )

    logger.info("PORTFOLIO LIVE MODE EXITED")
    notifier.send("PORTFOLIO LIVE MODE EXITED")
    

def get_smart_sleep(mode=None):
    """
    Adaptive sleep based on time and current bot mode.
    Always returns a short sleep when precision matters.
    """
 
    now = datetime.utcnow()
 
    # --- LIVE MODE: be responsive ---
    if mode == "live":
        return 5   # fast checks while trading
 
    # --- BACKTEST / IDLE MODE ---
    today = now.date()
 
    def next_time(hour, minute=0):
        t = datetime.combine(today, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
        if t <= now:
            t += timedelta(days=1)
        return t
 
    london_open = next_time(8)
    london_close = next_time(17)
    wf_start = next_time(22)
 
    # Before London open
    if now < london_open:
        return min(300, (london_open - now).total_seconds())
 
    # During London session
    if now < london_close:
        return 30
 
    # Between London close and walk-forward
    if now < wf_start:
        return min(300, (wf_start - now).total_seconds())
 
    # Late night
    return 300

def get_next_event_info(mode):
    now = datetime.utcnow()
    today = now.date()
    
    def next_time(hour, minute=0):
        t = datetime.combine(today, datetime.min.time()).replace(
            hour=hour, minute=minute
        )
        if t <= now:
            t += timedelta(days=1)
        return t
    
    london_open = next_time(8)
    london_close = next_time(17)
    wf_start = next_time(22)
    
    if now < london_open:
        return f"London open ({london_open.strftime('%H:%M')})"
    elif now < london_close:
        return f"London close ({london_close.strftime('%H:%M')})"
    elif now < wf_start:
        return f"Walk-forward ({wf_start.strftime('%H:%M')})"
    else:
        return "Next day"


# ---------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------

def main():

    cfg = load_config() or {}
    r = cfg.get("risk", {}) or {}
    risk = RiskManager(
        max_trades_per_day=int(r.get("max_trades_per_day", 10)),
        max_daily_loss=float(r.get("max_daily_loss", 0.02)),
        max_open_positions=int(r.get("max_open_positions", 1)),
    )
    orchestrator = BotOrchestrator(risk)
    heartbeat = Heartbeat(interval_minutes=30)

    _start_monitoring_api_background()
    
    crash_handler = CrashHandler()
    crash_handler.setup_global_handler()

    last_mode = None
    last_backtest_day = None
    last_walkforward_day = None
    last_report_date = None

    while True:

        heartbeat.tick()

        mode = orchestrator.decide_mode()
        STATE.set_mode(mode)
    
        now = datetime.utcnow()

        if mode != last_mode:
            logger.info(f"MODE TRANSITION: {mode.upper()}")
            last_mode = mode

        # ---------------------------------------------
        # NIGHTLY PERFORMANCE SUMMARY (23:00 UTC)
        # ---------------------------------------------
        if now.hour == 23 and last_report_date != now.date():

            summary = daily_summary()

            if summary:
                notifier.send(
                    "DAILY PERFORMANCE SUMMARY\n"
                    f"Date: {summary['date']}\n"
                    f"Trades: {summary['trades']}\n"
                    f"Wins: {summary['wins']}\n"
                    f"Losses: {summary['losses']}"
                )

            last_report_date = now.date()

        # ---------------------------------------------
        # LIVE (PORTFOLIO)
        # ---------------------------------------------
        if mode == "live":
            run_portfolio_live(orchestrator)

        # ---------------------------------------------
        # BACKTEST (OFF SESSION)
        # ---------------------------------------------
        elif mode == "backtest":

            if last_backtest_day != now.date():
                run_backtest_module()
                last_backtest_day = now.date()

        # ---------------------------------------------
        # WALK-FORWARD (NIGHTLY)
        # ---------------------------------------------
        elif mode == "walkforward":

            if last_walkforward_day != now.date():
                run_walkforward_module()
                last_walkforward_day = now.date()

        # ---------------------------------------------
        # PARAMETER ROTATION
        # ---------------------------------------------
        elif mode == "rotate":

            logger.info("PARAMETER ROTATION MODE STARTED")
            notifier.send("PARAMETER ROTATION MODE STARTED")

            run_parameter_rotation()

            if not orchestrator.guard.evaluate():
                orchestrator.rollback.rollback()
                notifier.send("PARAMETER ROLLBACK ACTIVATED")
                logger.warning("ROLLBACK ACTIVATED")
            else:
                notifier.send("NEW PARAMETERS ACCEPTED")
                logger.info("NEW PARAMETERS ACCEPTED")

        # ---------------------------------------------
        # ORCHESTRATOR HEARTBEAT
        # ---------------------------------------------
        sleep_s = get_smart_sleep(mode)
        if sleep_s > 60:
            next_event = get_next_event_info(mode)
            logger.info(f"Sleeping {int(sleep_s)}s until {next_event}")
        time.sleep(sleep_s)

if __name__ == "__main__":
    main()
