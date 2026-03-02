import time
from core.risk import RiskManager
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials
from utils.logger import setup_logger, log_separator
from config.loader import load_config


class TradingEngine:

    def __init__(
        self,
        broker,
        strategy,
        executor,
        symbol,
        timeframe,
        candle_seconds,
        risk_cfg=None,
    ):
        self.broker = broker
        self.strategy = strategy
        self.executor = executor
        self.symbol = symbol

        tg = get_telegram_credentials()
        self.notifier = TelegramNotifier(tg.token, tg.chat_id)
        self.logger = setup_logger()

        self.timeframe = timeframe
        self.candle_seconds = candle_seconds

        if risk_cfg is None:
            try:
                risk_cfg = (load_config() or {}).get("risk", {})
            except Exception:
                risk_cfg = {}

        def _to_int(v, default):
            try:
                return int(v)
            except Exception:
                return int(default)

        def _to_float(v, default):
            try:
                return float(v)
            except Exception:
                return float(default)

        self.risk = RiskManager(
            max_trades_per_day=_to_int((risk_cfg or {}).get("max_trades_per_day", 10), 10),
            max_daily_loss=_to_float((risk_cfg or {}).get("max_daily_loss", 0.02), 0.02),
            max_open_positions=_to_int((risk_cfg or {}).get("max_open_positions", 1), 1),
        )

        self.last_candle_time = None

    # ====================================================
    # CORE CANDLE PROCESSING (shared)
    # ====================================================

    def _process_candle(self):
        """
        Executes exactly ONE candle cycle.
        Used by both single-symbol and portfolio engines.
        """

        df = self.broker.get_historical_data(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bars=300
        )

        candle_time = df.index[-1]

        # only new candles
        if self.last_candle_time == candle_time:
            return

        self.last_candle_time = candle_time

        signal = self.strategy.on_candle(df)

        if not signal:
            return

        # ---------------- SIGNAL ----------------
        strategy_name = signal.get("strategy", "unknown")
        log_separator(self.logger, f"SIGNAL - {strategy_name.upper()}")
        self.logger.info(
            f"  Symbol: {self.symbol} | Side: {signal['side'].upper()}\n"
            f"  SL: {signal['sl']} | TP: {signal['tp']}"
        )
        log_separator(self.logger)

        self.notifier.send(
            f"SIGNAL | {self.symbol} | {strategy_name.upper()} | "
            f"{signal['side'].upper()} | "
            f"SL={signal['sl']} TP={signal['tp']}"
        )

        # ---------------- RISK CHECK ----------------
        allowed, reason = self.risk.allow_new_trade()

        if not allowed:
            self.logger.warning(f"TRADE BLOCKED | {reason}")
            self.notifier.send(f"TRADE BLOCKED | {reason}")
            return

        # ---------------- EXECUTION ----------------
        result = self.executor.place_market_order(
            signal=signal,
            risk_pct=getattr(self.executor, "last_risk_pct", None),
        )

        if result:
            self.risk.record_trade()
            log_separator(self.logger, "ORDER SUCCESS")
            self.logger.info(
                f"  Symbol: {self.symbol}\n"
                f"  Strategy: {strategy_name.upper()}\n"
                f"  Ticket: {result.order}"
            )
            log_separator(self.logger)
            self.notifier.send(
                f"ORDER EXECUTED | {self.symbol} | {strategy_name.upper()} | "
                f"TICKET={result.order}"
            )
        else:
            self.logger.warning("ORDER FAILED")
            self.notifier.send("ORDER FAILED")

    # ====================================================
    # SINGLE SYMBOL MODE
    # ====================================================

    def run(self, allow_fn):
        """
        Legacy single-symbol live trading loop.
        """

        self.logger.info("LIVE TRADING STARTED")
        self.notifier.send("LIVE TRADING STARTED")

        while allow_fn():
            try:
                self._process_candle()
            except Exception as e:
                self.logger.exception(f"ENGINE ERROR: {e}")
                self.notifier.send(f"ENGINE ERROR: {e}")

            time.sleep(self.candle_seconds)

        self.logger.info("LIVE TRADING STOPPED")
        self.notifier.send("LIVE TRADING STOPPED")

    # ====================================================
    # PORTFOLIO MODE
    # ====================================================

    def step_once(self):
        """
        Executes exactly one candle step.
        Used by PortfolioEngine scheduler.
        """
        try:
            self._process_candle()
        except Exception as e:
            self.logger.exception(f"ENGINE ERROR: {e}")
            self.notifier.send(f"ENGINE ERROR: {e}")
