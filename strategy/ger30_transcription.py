from __future__ import annotations

from datetime import time

import pandas as pd

from utils.indicators import atr, rsi
from utils.logger import setup_logger
from utils.time_utils import SessionFilter


class GER30TranscriptionStrategy:
    """GER30 intraday breakout playbook kept after evaluation."""

    _VALID_MODES = {"ger30_three_pin_breakout"}

    def __init__(self, config: dict, mode: str):
        if mode not in self._VALID_MODES:
            raise ValueError(f"Unsupported GER30 strategy mode: {mode}")

        cfg = (config or {}).get(mode, {})
        self.mode = mode
        self.symbol = str(cfg.get("symbol", "GER30m"))
        self.atr_period = int(cfg.get("atr_period", 14))
        self.rsi_period = int(cfg.get("rsi_period", 14))
        self.min_rr = float(cfg.get("min_rr", cfg.get("rr_target", 1.5)))
        self.rr_target = float(cfg.get("rr_target", 2.0))
        self.sl_buffer_atr = float(cfg.get("sl_buffer_atr", 0.22))
        self.wick_reject_ratio = float(cfg.get("wick_reject_ratio", 0.35))
        self.min_bars_between_signals = int(cfg.get("min_bars_between_signals", 3))
        self.allow_london = bool(cfg.get("allow_london", True))
        self.allow_newyork = bool(cfg.get("allow_newyork", False))
        self.breakout_lookback_bars = int(cfg.get("breakout_lookback_bars", 12))
        self.impulse_bars = int(cfg.get("impulse_bars", 4))
        self.min_impulse_atr = float(cfg.get("min_impulse_atr", 1.2))
        self.retest_buffer_atr = float(cfg.get("retest_buffer_atr", 0.10))
        self.min_trend_slope_atr = float(cfg.get("min_trend_slope_atr", 0.03))
        self.trend_rsi_buy_min = float(cfg.get("trend_rsi_buy_min", 52.0))
        self.trend_rsi_sell_max = float(cfg.get("trend_rsi_sell_max", 48.0))
        self.breakout_body_min_atr = float(cfg.get("breakout_body_min_atr", 0.18))
        self.breakout_hold_buffer_atr = float(cfg.get("breakout_hold_buffer_atr", 0.03))
        self.max_retest_depth_atr = float(cfg.get("max_retest_depth_atr", 0.08))

        self.session = SessionFilter()
        self.logger = setup_logger()
        self.market_state = None
        self._last_signal_bar: pd.Timestamp | None = None

    def bind_symbol(self, symbol: str) -> None:
        self.symbol = str(symbol)

    def bind_market_state(self, market_state) -> None:
        self.market_state = market_state

    def _skip(self, now: pd.Timestamp, reason: str):
        self.logger.debug(f"{self.mode.upper()} SKIP | {now} | {reason}")
        return None

    def _ensure_utc(self, ts: pd.Timestamp) -> pd.Timestamp:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is None:
            return stamp.tz_localize("UTC")
        return stamp.tz_convert("UTC")

    def _cooldown_ok(self, candle_time: pd.Timestamp, bar_seconds: float) -> bool:
        if self._last_signal_bar is None:
            return True
        step = max(1.0, float(bar_seconds))
        bars = int((candle_time - self._last_signal_bar).total_seconds() / step)
        return bars >= max(1, self.min_bars_between_signals)

    def _session_allowed(self, candle_time: pd.Timestamp) -> bool:
        current = self._ensure_utc(candle_time)
        now_time = current.time()
        london_start, london_end = self.session.london
        ny_start, ny_end = self.session.newyork
        if self.allow_london and (time(london_start) <= now_time <= time(london_end)):
            return True
        if self.allow_newyork and (time(ny_start) <= now_time <= time(ny_end)):
            return True
        return False

    def _reward_to_risk_ok(self, side: str, entry: float, sl: float, tp: float) -> bool:
        risk = (entry - sl) if side == "buy" else (sl - entry)
        reward = (tp - entry) if side == "buy" else (entry - tp)
        if risk <= 0 or reward <= 0:
            return False
        return (reward / risk) >= float(self.min_rr)

    def _signal(self, now: pd.Timestamp, side: str, entry: float, sl: float, tp: float):
        if not self._reward_to_risk_ok(side, entry, sl, tp):
            return None
        self._last_signal_bar = now
        return {
            "side": side,
            "sl": round(float(sl), 3),
            "tp": round(float(tp), 3),
            "strategy": self.mode,
            "entry": float(entry),
            "min_rr": float(self.min_rr),
        }

    def _smma(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(alpha=(1.0 / float(period)), adjust=False).mean()

    def _wick_ratio(self, row: pd.Series, side: str) -> float:
        high = float(row.high)
        low = float(row.low)
        open_price = float(row.open)
        close_price = float(row.close)
        candle_range = max(1e-9, high - low)
        if side == "buy":
            return max(0.0, (min(open_price, close_price) - low) / candle_range)
        return max(0.0, (high - max(open_price, close_price)) / candle_range)

    def _body_size(self, row: pd.Series) -> float:
        return abs(float(row.close) - float(row.open))

    def _trend_bias(self, close: pd.Series, atr_value: float) -> str | None:
        if len(close) < 200:
            return None
        smma50 = self._smma(close, 50)
        smma200 = self._smma(close, 200)
        rsi_value = rsi(close, self.rsi_period).iloc[-1]
        if pd.isna(rsi_value):
            return None
        slope = (float(smma200.iloc[-1]) - float(smma200.iloc[-5])) / max(1e-9, atr_value)
        if (
            float(close.iloc[-1]) > float(smma50.iloc[-1]) > float(smma200.iloc[-1])
            and float(rsi_value) >= self.trend_rsi_buy_min
            and slope >= self.min_trend_slope_atr
        ):
            return "buy"
        if (
            float(close.iloc[-1]) < float(smma50.iloc[-1]) < float(smma200.iloc[-1])
            and float(rsi_value) <= self.trend_rsi_sell_max
            and slope <= -self.min_trend_slope_atr
        ):
            return "sell"
        return None

    def _three_pin_breakout_signal(self, df: pd.DataFrame, now: pd.Timestamp, atr_value: float):
        if len(df) < max(220, self.breakout_lookback_bars + self.impulse_bars + 4):
            return None

        close = df["close"]
        bias = self._trend_bias(close, atr_value)
        recent = df.iloc[-(self.breakout_lookback_bars + self.impulse_bars + 2):]
        correction = recent.iloc[self.impulse_bars:-2]
        breakout = recent.iloc[-2]
        retest = recent.iloc[-1]
        entry = float(retest.close)

        if len(correction) < 5:
            return None

        correction_high = float(correction["high"].max())
        correction_low = float(correction["low"].min())

        if bias == "buy":
            impulse = recent.iloc[:self.impulse_bars]
            impulse_move = float(impulse["close"].iloc[-1] - impulse["open"].iloc[0])
            breakout_body = self._body_size(breakout)
            if (
                impulse_move >= (atr_value * self.min_impulse_atr)
                and breakout_body >= (atr_value * self.breakout_body_min_atr)
                and float(breakout.close) > float(breakout.open)
                and float(breakout.close) > (correction_high + (atr_value * self.retest_buffer_atr))
                and float(retest.low) >= (correction_high - (atr_value * self.max_retest_depth_atr))
                and float(retest.close) > (correction_high + (atr_value * self.breakout_hold_buffer_atr))
                and self._wick_ratio(retest, "buy") >= self.wick_reject_ratio
            ):
                sl = min(float(retest.low), correction_low) - (atr_value * self.sl_buffer_atr)
                risk = entry - sl
                if risk <= 0:
                    return None
                tp = entry + (risk * self.rr_target)
                return self._signal(now, "buy", entry, sl, tp)

        if bias == "sell":
            impulse = recent.iloc[:self.impulse_bars]
            impulse_move = float(impulse["open"].iloc[0] - impulse["close"].iloc[-1])
            breakout_body = self._body_size(breakout)
            if (
                impulse_move >= (atr_value * self.min_impulse_atr)
                and breakout_body >= (atr_value * self.breakout_body_min_atr)
                and float(breakout.close) < float(breakout.open)
                and float(breakout.close) < (correction_low - (atr_value * self.retest_buffer_atr))
                and float(retest.high) <= (correction_low + (atr_value * self.max_retest_depth_atr))
                and float(retest.close) < (correction_low - (atr_value * self.breakout_hold_buffer_atr))
                and self._wick_ratio(retest, "sell") >= self.wick_reject_ratio
            ):
                sl = max(float(retest.high), correction_high) + (atr_value * self.sl_buffer_atr)
                risk = sl - entry
                if risk <= 0:
                    return None
                tp = entry - (risk * self.rr_target)
                return self._signal(now, "sell", entry, sl, tp)

        return None

    def on_candle(self, df: pd.DataFrame):
        if df is None or df.empty:
            return None
        for col in ("open", "high", "low", "close"):
            if col not in df.columns:
                return None

        normalized = df.copy()
        normalized.index = pd.DatetimeIndex([self._ensure_utc(ts) for ts in normalized.index])
        now = pd.Timestamp(normalized.index[-1])
        inferred_bar_seconds = 300.0
        if len(normalized.index) >= 2:
            inferred_bar_seconds = abs((normalized.index[-1] - normalized.index[-2]).total_seconds()) or 300.0

        if not self._session_allowed(now):
            return self._skip(now, "outside allowed session")
        if not self._cooldown_ok(now, inferred_bar_seconds):
            return self._skip(now, "cooldown active")

        min_candles = max(220, self.atr_period + 10, self.breakout_lookback_bars + self.impulse_bars + 4)
        if len(normalized) < min_candles:
            return self._skip(now, f"insufficient candles ({len(normalized)} < {min_candles})")

        atr_value = atr(normalized, self.atr_period).iloc[-1]
        if atr_value is None or pd.isna(atr_value) or float(atr_value) <= 0:
            return self._skip(now, "ATR invalid")
        atr_value = float(atr_value)

        signal = self._three_pin_breakout_signal(normalized, now, atr_value)
        if signal is None:
            return self._skip(now, "setup not present")
        return signal
