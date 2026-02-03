import time
from core.risk import RiskManager
from utils.telegram import TelegramNotifier
from config.telegram import TOKEN, CHAT_ID
from utils.logger import setup_logger


class TradingEngine:

    def __init__(
        self,
        broker,
        strategy,
        executor,
        symbol,
        timeframe,
        candle_seconds
    ):
        self.broker = broker
        self.strategy = strategy
        self.executor = executor
        self.symbol = symbol

        self.notifier = TelegramNotifier(TOKEN, CHAT_ID)
        self.logger = setup_logger()

        self.timeframe = timeframe
        self.candle_seconds = candle_seconds

        self.risk = RiskManager(
            max_trades_per_day=3,
            max_daily_loss=0.02,
            max_open_positions=1
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
        self.logger.info(
            f"SIGNAL | {self.symbol} | "
            f"{signal['side'].upper()} | "
            f"SL={signal['sl']} TP={signal['tp']}"
        )

        self.notifier.send(
            f"SIGNAL | {self.symbol} | "
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
            lot=self.executor.current_lot
        )

        if result:
            self.risk.record_trade()
            self.logger.info(
                f"ORDER SUCCESS | {self.symbol} | "
                f"TICKET={result.order}"
            )
            self.notifier.send(
                f"ORDER EXECUTED | {self.symbol} | "
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
