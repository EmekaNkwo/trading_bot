import time
from datetime import timezone

import pandas as pd
from core.risk import RiskManager
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials
from utils.logger import setup_logger, log_separator
from config.loader import load_config
from models.trade_intent import TradeIntent


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
        market_state=None,
        strategy_name=None,
        safety_cfg=None,
    ):
        self.broker = broker
        self.strategy = strategy
        self.executor = executor
        self.symbol = symbol
        self.strategy_name = str(strategy_name or getattr(strategy, "mode", type(strategy).__name__))

        tg = get_telegram_credentials()
        self.notifier = TelegramNotifier(tg.token, tg.chat_id)
        self.logger = setup_logger()

        self.timeframe = timeframe
        self.candle_seconds = candle_seconds
        self.market_state = market_state
        self.safety_cfg = dict(safety_cfg or {})
        self.last_runtime_issue = None
        self.last_data_advanced = False

        if self.market_state is not None and hasattr(self.strategy, "bind_market_state"):
            self.strategy.bind_market_state(self.market_state)

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

    def _set_runtime_issue(self, reason: str) -> None:
        self.last_runtime_issue = str(reason)

    def pop_runtime_issue(self) -> str | None:
        issue = self.last_runtime_issue
        self.last_runtime_issue = None
        return issue

    def _validate_market_data(self, df: pd.DataFrame, candle_time) -> str | None:
        min_rows = int(self.safety_cfg.get("min_candles_required", 50) or 50)
        if len(df) < min_rows:
            return f"insufficient_history:{len(df)}<{min_rows}"
        if not df.index.is_monotonic_increasing:
            return "candle_index_not_sorted"
        if df[["open", "high", "low", "close"]].isnull().any().any():
            return "ohlc_contains_nulls"

        max_age_factor = float(self.safety_cfg.get("max_candle_age_factor", 3.0) or 3.0)
        max_age_s = max(float(self.candle_seconds) * max_age_factor, 300.0)
        candle_ts = pd.Timestamp(candle_time)
        if candle_ts.tzinfo is None:
            candle_ts = candle_ts.tz_localize(timezone.utc)
        else:
            candle_ts = candle_ts.tz_convert(timezone.utc)
        age_s = (pd.Timestamp.now(tz=timezone.utc) - candle_ts).total_seconds()
        if age_s > max_age_s:
            return f"stale_candle:{age_s:.0f}s"

        last_bar_range = float(df["high"].iloc[-1] - df["low"].iloc[-1])
        if last_bar_range > 0:
            try:
                tick = self.broker.get_symbol_snapshot(self.symbol)
            except Exception:
                tick = {"ok": False, "spread": None}
            if tick.get("ok"):
                spread = float(tick.get("spread", 0.0) or 0.0)
                max_spread_to_range = float(self.safety_cfg.get("max_spread_to_bar_range", 0.35) or 0.35)
                if spread > 0 and spread > (last_bar_range * max_spread_to_range):
                    return f"spread_too_wide:{spread:.5f}"
        return None

    def export_runtime_state(self) -> dict:
        candle_time = None
        if self.last_candle_time is not None:
            ts = pd.Timestamp(self.last_candle_time)
            candle_time = ts.tz_localize("UTC").isoformat() if ts.tzinfo is None else ts.tz_convert("UTC").isoformat()
        return {
            "symbol": self.symbol,
            "strategy": self.strategy_name,
            "timeframe": self.timeframe,
            "last_candle_time_utc": candle_time,
        }

    def restore_runtime_state(self, payload: dict | None) -> None:
        if not isinstance(payload, dict):
            return
        candle_time = payload.get("last_candle_time_utc")
        if candle_time:
            try:
                self.last_candle_time = pd.Timestamp(candle_time)
            except Exception:
                self.last_candle_time = None

    # ====================================================
    # CORE CANDLE PROCESSING (shared)
    # ====================================================

    def _load_new_candle_data(self):
        self.last_data_advanced = False
        df = self.broker.get_historical_data(
            symbol=self.symbol,
            timeframe=self.timeframe,
            bars=300
        )

        candle_time = df.index[-1]

        issue = self._validate_market_data(df, candle_time)
        if issue:
            self._set_runtime_issue(issue)
            return None, None

        # only new candles
        if self.last_candle_time == candle_time:
            return None, None

        self.last_candle_time = candle_time
        self.last_data_advanced = True
        return df, candle_time

    def _build_trade_intent(self, signal: dict, candle_time, risk_request: float | None = None) -> TradeIntent:
        if risk_request is None:
            risk_request = getattr(self.executor, "last_risk_pct", None)
        return TradeIntent.from_signal(
            symbol=self.symbol,
            timeframe=self.timeframe,
            candle_time=candle_time,
            signal=signal,
            risk_request=risk_request,
        )

    def _log_signal(self, intent: TradeIntent) -> None:
        log_separator(self.logger, f"SIGNAL - {intent.strategy.upper()}")
        self.logger.info(
            f"  Symbol: {intent.symbol} | Side: {intent.side.upper()}\n"
            f"  SL: {intent.sl} | TP: {intent.tp}"
        )
        log_separator(self.logger)

        self.notifier.send(
            f"SIGNAL | {intent.symbol} | {intent.strategy.upper()} | "
            f"{intent.side.upper()} | "
            f"SL={intent.sl} TP={intent.tp}"
        )

    def generate_trade_intent(self, risk_request: float | None = None) -> TradeIntent | None:
        """
        Generate one trade intent from the latest candle, if any.
        This keeps strategy evaluation separate from trade approval/execution.
        """
        df, candle_time = self._load_new_candle_data()
        if df is None or candle_time is None:
            return None

        if self.market_state is not None:
            try:
                self.market_state.update(symbol=self.symbol, timeframe=self.timeframe, df=df)
            except Exception as e:
                self.logger.warning(f"MARKET STATE UPDATE FAILED | {self.symbol} | {e}")

        signal = self.strategy.on_candle(df)

        if not signal:
            return None

        intent = self._build_trade_intent(signal, candle_time, risk_request=risk_request)
        self._log_signal(intent)
        return intent

    def approve_trade_intent(self, intent: TradeIntent) -> tuple[bool, str]:
        return self.risk.allow_new_trade()

    def execute_trade_intent(self, intent: TradeIntent):
        result = self.executor.place_market_order(
            signal=intent.signal,
            risk_pct=getattr(self.executor, "last_risk_pct", None),
        )

        if result:
            self.risk.record_trade()
            log_separator(self.logger, "ORDER SUCCESS")
            self.logger.info(
                f"  Symbol: {intent.symbol}\n"
                f"  Strategy: {intent.strategy.upper()}\n"
                f"  Ticket: {result.order}"
            )
            log_separator(self.logger)
            self.notifier.send(
                f"ORDER EXECUTED | {intent.symbol} | {intent.strategy.upper()} | "
                f"TICKET={result.order}"
            )
            try:
                confirmed, reconcile_reason = self.executor.confirm_order_visible(result, intent.strategy)
            except Exception as e:
                confirmed, reconcile_reason = False, f"reconcile_exception:{e}"
            if not confirmed:
                reason = reconcile_reason or "order_reconciliation_failed"
                self.logger.warning(
                    f"ORDER RECONCILE WARNING | {intent.symbol} | {intent.strategy} | {reason}"
                )
                self.notifier.send(
                    f"ORDER RECONCILE WARNING | {intent.symbol} | {intent.strategy.upper()} | {reason}"
                )
        else:
            self.logger.warning("ORDER FAILED")
            self.notifier.send("ORDER FAILED")
        return result

    def _process_candle(self):
        """
        Executes exactly ONE candle cycle.
        Used by both single-symbol and portfolio engines.
        """
        intent = self.generate_trade_intent()
        if intent is None:
            return

        allowed, reason = self.approve_trade_intent(intent)

        if not allowed:
            self.logger.warning(f"TRADE BLOCKED | {reason}")
            self.notifier.send(f"TRADE BLOCKED | {reason}")
            return

        self.execute_trade_intent(intent)

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
