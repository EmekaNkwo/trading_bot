from __future__ import annotations

import pandas as pd

from utils.indicators import atr
from utils.logger import setup_logger
from utils.time_utils import SessionFilter


class XAUOpeningRangeDisplacementStrategy:
    """
    v2 setup:
    Breakout from London opening range with displacement and extension guard.
    """

    def __init__(self, config: dict):
        cfg = (config or {}).get("opening_range_displacement", {})
        self.atr_period = int(cfg.get("atr_period", 14))
        self.break_buffer_atr = float(cfg.get("break_buffer_atr", 0.08))
        self.min_body_atr = float(cfg.get("min_body_atr", 0.45))
        self.max_extension_atr = float(cfg.get("max_extension_atr", 0.80))
        self.sl_buffer_atr = float(cfg.get("sl_buffer_atr", 0.25))
        self.rr_target = float(cfg.get("rr_target", 1.7))
        self.require_active_session = bool(cfg.get("require_active_session", True))
        self.require_non_low_vol = bool(cfg.get("require_non_low_vol", True))
        self.min_bars_between_signals = int(cfg.get("min_bars_between_signals", 2))

        self.session = SessionFilter()
        self.logger = setup_logger()
        self.symbol = str(cfg.get("symbol", "XAUUSDm"))
        self.market_state = None
        self._last_signal_at = None

    def bind_symbol(self, symbol: str) -> None:
        self.symbol = str(symbol)

    def bind_market_state(self, market_state) -> None:
        self.market_state = market_state

    def _cooldown_ok(self, now: pd.Timestamp) -> bool:
        if self._last_signal_at is None:
            return True
        bars = int((now - self._last_signal_at).total_seconds() / 300.0)
        return bars >= self.min_bars_between_signals

    def on_candle(self, df: pd.DataFrame):
        if df is None or df.empty or len(df) < (self.atr_period + 10):
            return None
        if self.market_state is None:
            return None

        now = pd.Timestamp(df.index[-1])
        if not self._cooldown_ok(now):
            return None
        if self.require_active_session and (not self.session.allowed(now)):
            return None

        st = self.market_state.get(self.symbol)
        if st is None or (not st.opening_range_ready):
            return None
        if st.opening_range_high is None or st.opening_range_low is None:
            return None
        if self.require_non_low_vol and st.volatility_regime == "low":
            return None

        a = atr(df, self.atr_period).iloc[-1]
        if a is None or pd.isna(a) or float(a) <= 0:
            return None
        atr_val = float(a)

        last = df.iloc[-1]
        entry = float(last.close)
        body = abs(float(last.close) - float(last.open))

        or_high = float(st.opening_range_high)
        or_low = float(st.opening_range_low)
        up_break = or_high + (atr_val * self.break_buffer_atr)
        dn_break = or_low - (atr_val * self.break_buffer_atr)

        if body < (atr_val * self.min_body_atr):
            return None

        # Long displacement
        if entry > up_break:
            extension = entry - or_high
            if extension > (atr_val * self.max_extension_atr):
                return None
            sl = or_low - (atr_val * self.sl_buffer_atr)
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + (risk * self.rr_target)
            self._last_signal_at = now
            self.logger.info(f"OR DISP BUY | {now} | OR=({or_low:.3f},{or_high:.3f}) atr={atr_val:.3f}")
            return {
                "side": "buy",
                "sl": round(sl, 3),
                "tp": round(tp, 3),
                "strategy": "xau_opening_range_displacement",
                "entry": entry,
                "min_rr": float(self.rr_target),
            }

        # Short displacement
        if entry < dn_break:
            extension = or_low - entry
            if extension > (atr_val * self.max_extension_atr):
                return None
            sl = or_high + (atr_val * self.sl_buffer_atr)
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - (risk * self.rr_target)
            self._last_signal_at = now
            self.logger.info(f"OR DISP SELL | {now} | OR=({or_low:.3f},{or_high:.3f}) atr={atr_val:.3f}")
            return {
                "side": "sell",
                "sl": round(sl, 3),
                "tp": round(tp, 3),
                "strategy": "xau_opening_range_displacement",
                "entry": entry,
                "min_rr": float(self.rr_target),
            }

        return None

