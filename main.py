import time
from datetime import datetime, timedelta
from tkinter import W

from utils.logger import setup_logger
from utils.telegram import TelegramNotifier
from config.telegram import TOKEN, CHAT_ID

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
from config.loader import load_config

from portfolio.engine import PortfolioEngine


# ---------------------------------------------------
# GLOBALS
# ---------------------------------------------------

logger = setup_logger()
notifier = TelegramNotifier(TOKEN, CHAT_ID)


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
    df = load_from_csv("XAUUSDm", "M15")

    strategy = XAUTrendStrategy(config)
    engine = BacktestEngine()

    final_balance = engine.run(df, strategy)

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
    df = load_from_csv("XAUUSDm", "M15")

    # -----------------------------------------
    # Strategy backtest constraints (shared)
    # -----------------------------------------
    wt_cfg = config.get("backtest", {})
    min_trades = wt_cfg.get("min_trades", 0)

    wf = WalkForwardEngine(
        train_bars=2000,
        test_bars=500,
        step_bars=500
    )

    results = wf.run(
        df,
        strategy_factory=lambda: XAUTrendStrategy(config)
    )

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

    risk = RiskManager()
    orchestrator = BotOrchestrator(risk)

    last_mode = None
    last_backtest_day = None
    last_walkforward_day = None
    last_report_date = None

    while True:

        mode = orchestrator.decide_mode()
    
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
