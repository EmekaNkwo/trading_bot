from __future__ import annotations

from datetime import time

import pandas as pd

from utils.indicators import atr, rsi
from utils.logger import setup_logger
from utils.time_utils import SessionFilter


class US30TranscriptionStrategy:
    """US30 intraday playbooks derived from transcription notes."""

    _VALID_MODES = {
        "us30_supply_demand",
        "us30_open_wick",
        "us30_trend_pullback",
        "us30_asia_eq",
        "us30_fib_retrace",
    }

    def __init__(self, config: dict, mode: str):
        if mode not in self._VALID_MODES:
            raise ValueError(f"Unsupported US30 strategy mode: {mode}")

        cfg = (config or {}).get(mode, {})
        self.mode = mode
        self.symbol = str(cfg.get("symbol", "US30m"))
        self.atr_period = int(cfg.get("atr_period", 14))
        self.rsi_period = int(cfg.get("rsi_period", 14))
        self.min_rr = float(cfg.get("min_rr", cfg.get("rr_target", 1.5)))
        self.rr_target = float(cfg.get("rr_target", 2.0))
        self.sl_buffer_atr = float(cfg.get("sl_buffer_atr", 0.20))
        self.wick_reject_ratio = float(cfg.get("wick_reject_ratio", 0.40))
        self.min_bars_between_signals = int(cfg.get("min_bars_between_signals", 3))
        self.allow_london = bool(cfg.get("allow_london", True))
        self.allow_newyork = bool(cfg.get("allow_newyork", True))

        self.newyork_open_hour_utc = int(cfg.get("newyork_open_hour_utc", 13))
        self.newyork_open_minute_utc = int(cfg.get("newyork_open_minute_utc", 30))
        self.preopen_lookback_bars = int(cfg.get("preopen_lookback_bars", 24))

        self.min_trend_slope_atr = float(cfg.get("min_trend_slope_atr", 0.04))
        self.min_ma_separation_atr = float(cfg.get("min_ma_separation_atr", 0.12))

        self.asia_start_hour_utc = int(cfg.get("asia_start_hour_utc", 1))
        self.asia_end_hour_utc = int(cfg.get("asia_end_hour_utc", 6))
        self.eq_buffer_atr = float(cfg.get("eq_buffer_atr", 0.18))
        self.sweep_buffer_atr = float(cfg.get("sweep_buffer_atr", 0.12))

        self.lookback_bars = int(cfg.get("lookback_bars", 24))
        self.impulse_bars = int(cfg.get("impulse_bars", 3))
        self.min_impulse_atr = float(cfg.get("min_impulse_atr", 1.4))
        self.zone_buffer_atr = float(cfg.get("zone_buffer_atr", 0.10))

        self.swing_lookback = int(cfg.get("swing_lookback", 30))
        self.pullback_floor = float(cfg.get("pullback_floor", 0.382))
        self.pullback_ceiling = float(cfg.get("pullback_ceiling", 0.618))
        self.round_number_step = float(cfg.get("round_number_step", 100.0))
        self.round_number_buffer_atr = float(cfg.get("round_number_buffer_atr", 0.35))

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

    def _is_near_round_number(self, price: float, atr_value: float) -> bool:
        if self.round_number_step <= 0:
            return False
        nearest = round(price / self.round_number_step) * self.round_number_step
        return abs(price - nearest) <= (atr_value * self.round_number_buffer_atr)

    def _today_window(
        self,
        now: pd.Timestamp,
        start_hour: int,
        start_minute: int,
        end_hour: int,
        end_minute: int,
    ) -> tuple[pd.Timestamp, pd.Timestamp]:
        base = self._ensure_utc(now)
        start = base.normalize() + pd.Timedelta(hours=start_hour, minutes=start_minute)
        end = base.normalize() + pd.Timedelta(hours=end_hour, minutes=end_minute)
        return start, end

    def _opening_wick_signal(self, df: pd.DataFrame, now: pd.Timestamp, atr_value: float):
        open_start, open_end = self._today_window(
            now,
            self.newyork_open_hour_utc,
            self.newyork_open_minute_utc,
            self.newyork_open_hour_utc,
            self.newyork_open_minute_utc + 10,
        )
        open_window = df[(df.index >= open_start) & (df.index < open_end)]
        if len(open_window) < 2 or open_window.index[-1] != now:
            return None

        pre_market = df[df.index < open_start].tail(max(5, self.preopen_lookback_bars))
        if len(pre_market) < 5:
            return None

        first = open_window.iloc[0]
        confirm = open_window.iloc[1]
        pre_high = float(pre_market["high"].max())
        pre_low = float(pre_market["low"].min())
        entry = float(confirm.close)

        bullish_sweep = (
            float(first.high) > pre_high
            and self._wick_ratio(first, "sell") >= self.wick_reject_ratio
            and float(confirm.close) < min(float(first.open), float(first.close))
        )
        if bullish_sweep:
            sl = max(float(first.high), float(confirm.high)) + (atr_value * self.sl_buffer_atr)
            risk = sl - entry
            if risk <= 0:
                return None
            tp = entry - (risk * self.rr_target)
            return self._signal(now, "sell", entry, sl, tp)

        bearish_sweep = (
            float(first.low) < pre_low
            and self._wick_ratio(first, "buy") >= self.wick_reject_ratio
            and float(confirm.close) > max(float(first.open), float(first.close))
        )
        if bearish_sweep:
            sl = min(float(first.low), float(confirm.low)) - (atr_value * self.sl_buffer_atr)
            risk = entry - sl
            if risk <= 0:
                return None
            tp = entry + (risk * self.rr_target)
            return self._signal(now, "buy", entry, sl, tp)

        return None

    def _trend_pullback_signal(self, df: pd.DataFrame, now: pd.Timestamp, atr_value: float):
        close = df["close"]
        smma21 = self._smma(close, 21)
        smma50 = self._smma(close, 50)
        smma100 = self._smma(close, 100)
        smma200 = self._smma(close, 200)
        rsi_value = rsi(close, self.rsi_period).iloc[-1]
        if pd.isna(rsi_value):
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]
        entry = float(last.close)
        trend_slope = abs(float(smma200.iloc[-1]) - float(smma200.iloc[-4])) / max(1e-9, atr_value)
        separation = abs(float(smma21.iloc[-1]) - float(smma50.iloc[-1])) / max(1e-9, atr_value)
        if trend_slope < self.min_trend_slope_atr or separation < self.min_ma_separation_atr:
            return None

        bullish_stack = (
            float(smma21.iloc[-1]) > float(smma50.iloc[-1]) > float(smma100.iloc[-1]) > float(smma200.iloc[-1])
        )
        bearish_stack = (
            float(smma21.iloc[-1]) < float(smma50.iloc[-1]) < float(smma100.iloc[-1]) < float(smma200.iloc[-1])
        )

        if bullish_stack and float(rsi_value) > 50:
            tested_ma = min(float(smma100.iloc[-1]), float(smma200.iloc[-1]))
            if (
                float(last.low) <= (tested_ma + (atr_value * 0.10))
                and float(last.close) > tested_ma
                and float(last.close) > float(last.open)
                and self._wick_ratio(last, "buy") >= self.wick_reject_ratio
                and float(prev.low) <= float(smma100.iloc[-2]) + (atr_value * 0.15)
            ):
                swing_low = min(float(last.low), float(prev.low))
                sl = swing_low - (atr_value * self.sl_buffer_atr)
                risk = entry - sl
                if risk <= 0:
                    return None
                tp = entry + (risk * self.rr_target)
                return self._signal(now, "buy", entry, sl, tp)

        if bearish_stack and float(rsi_value) < 50:
            tested_ma = max(float(smma100.iloc[-1]), float(smma200.iloc[-1]))
            if (
                float(last.high) >= (tested_ma - (atr_value * 0.10))
                and float(last.close) < tested_ma
                and float(last.close) < float(last.open)
                and self._wick_ratio(last, "sell") >= self.wick_reject_ratio
                and float(prev.high) >= float(smma100.iloc[-2]) - (atr_value * 0.15)
            ):
                swing_high = max(float(last.high), float(prev.high))
                sl = swing_high + (atr_value * self.sl_buffer_atr)
                risk = sl - entry
                if risk <= 0:
                    return None
                tp = entry - (risk * self.rr_target)
                return self._signal(now, "sell", entry, sl, tp)

        return None

    def _asia_eq_signal(self, df: pd.DataFrame, now: pd.Timestamp, atr_value: float):
        asia_start, asia_end = self._today_window(
            now,
            self.asia_start_hour_utc,
            0,
            self.asia_end_hour_utc,
            0,
        )
        asia = df[(df.index >= asia_start) & (df.index < asia_end)]
        if len(asia) < 6 or now < asia_end:
            return None

        asia_high = float(asia["high"].max())
        asia_low = float(asia["low"].min())
        eq = (asia_high + asia_low) / 2.0
        post_asia = df[df.index >= asia_end]
        if len(post_asia) < 3:
            return None

        last = post_asia.iloc[-1]
        prev = post_asia.iloc[-2]
        entry = float(last.close)

        long_setup = (
            float(prev.low) < (asia_low - (atr_value * self.sweep_buffer_atr))
            and float(prev.close) > asia_low
            and float(last.low) <= (eq + (atr_value * self.eq_buffer_atr))
            and float(last.close) > eq
            and float(last.close) > float(last.open)
        )
        if long_setup:
            sl = min(float(prev.low), float(last.low)) - (atr_value * self.sl_buffer_atr)
            target = max(asia_high, entry + ((entry - sl) * self.rr_target))
            return self._signal(now, "buy", entry, sl, target)

        short_setup = (
            float(prev.high) > (asia_high + (atr_value * self.sweep_buffer_atr))
            and float(prev.close) < asia_high
            and float(last.high) >= (eq - (atr_value * self.eq_buffer_atr))
            and float(last.close) < eq
            and float(last.close) < float(last.open)
        )
        if short_setup:
            sl = max(float(prev.high), float(last.high)) + (atr_value * self.sl_buffer_atr)
            target = min(asia_low, entry - ((sl - entry) * self.rr_target))
            return self._signal(now, "sell", entry, sl, target)

        return None

    def _supply_demand_signal(self, df: pd.DataFrame, now: pd.Timestamp, atr_value: float):
        if len(df) < (self.lookback_bars + self.impulse_bars + 4):
            return None

        last = df.iloc[-1]
        entry = float(last.close)

        bullish_impulse = df.iloc[-(self.impulse_bars + 2):-2]
        bearish_impulse = bullish_impulse
        total_bull_body = bullish_impulse.apply(self._body_size, axis=1).sum()
        all_bullish = bool((bullish_impulse["close"] > bullish_impulse["open"]).all())
        all_bearish = bool((bearish_impulse["close"] < bearish_impulse["open"]).all())

        first_impulse = bullish_impulse.iloc[0]
        demand_low = float(first_impulse.low)
        demand_high = float(first_impulse.open)
        if all_bullish and total_bull_body >= (atr_value * self.min_impulse_atr):
            retest = (
                float(last.low) <= (demand_high + (atr_value * self.zone_buffer_atr))
                and float(last.close) > demand_high
                and float(last.close) > float(last.open)
            )
            if retest:
                sl = demand_low - (atr_value * self.sl_buffer_atr)
                risk = entry - sl
                if risk > 0:
                    tp = entry + (risk * self.rr_target)
                    return self._signal(now, "buy", entry, sl, tp)

        first_supply = bearish_impulse.iloc[0]
        supply_high = float(first_supply.high)
        supply_low = float(first_supply.open)
        total_bear_body = bearish_impulse.apply(self._body_size, axis=1).sum()
        if all_bearish and total_bear_body >= (atr_value * self.min_impulse_atr):
            retest = (
                float(last.high) >= (supply_low - (atr_value * self.zone_buffer_atr))
                and float(last.close) < supply_low
                and float(last.close) < float(last.open)
            )
            if retest:
                sl = supply_high + (atr_value * self.sl_buffer_atr)
                risk = sl - entry
                if risk > 0:
                    tp = entry - (risk * self.rr_target)
                    return self._signal(now, "sell", entry, sl, tp)

        return None

    def _fib_retrace_signal(self, df: pd.DataFrame, now: pd.Timestamp, atr_value: float):
        if len(df) < max(220, self.swing_lookback + 5):
            return None

        close = df["close"]
        smma200 = self._smma(close, 200)
        rsi_value = rsi(close, self.rsi_period).iloc[-1]
        if pd.isna(rsi_value):
            return None

        recent = df.iloc[-self.swing_lookback:]
        last = recent.iloc[-1]
        entry = float(last.close)
        trend_bias_up = float(close.iloc[-1]) > float(smma200.iloc[-1]) and float(rsi_value) > 50
        trend_bias_down = float(close.iloc[-1]) < float(smma200.iloc[-1]) and float(rsi_value) < 50

        if trend_bias_up:
            source = recent.iloc[:-1]
            low_idx = source["low"].iloc[:-3].idxmin()
            post_low = source.loc[low_idx:]
            swing_low = float(source.loc[low_idx, "low"])
            swing_high = float(post_low["high"].max())
            leg = swing_high - swing_low
            if leg <= (atr_value * 1.5):
                return None
            fib_38 = swing_high - (leg * self.pullback_floor)
            fib_62 = swing_high - (leg * self.pullback_ceiling)
            zone_low = min(fib_38, fib_62)
            zone_high = max(fib_38, fib_62)
            if (
                zone_low <= float(last.low) <= zone_high
                and float(last.close) > float(last.open)
                and self._is_near_round_number(entry, atr_value)
            ):
                sl = float(last.low) - (atr_value * self.sl_buffer_atr)
                tp = entry + ((entry - sl) * self.rr_target)
                return self._signal(now, "buy", entry, sl, tp)

        if trend_bias_down:
            source = recent.iloc[:-1]
            high_idx = source["high"].iloc[:-3].idxmax()
            post_high = source.loc[high_idx:]
            swing_high = float(source.loc[high_idx, "high"])
            swing_low = float(post_high["low"].min())
            leg = swing_high - swing_low
            if leg <= (atr_value * 1.5):
                return None
            fib_38 = swing_low + (leg * self.pullback_floor)
            fib_62 = swing_low + (leg * self.pullback_ceiling)
            zone_low = min(fib_38, fib_62)
            zone_high = max(fib_38, fib_62)
            if (
                zone_low <= float(last.high) <= zone_high
                and float(last.close) < float(last.open)
                and self._is_near_round_number(entry, atr_value)
            ):
                sl = float(last.high) + (atr_value * self.sl_buffer_atr)
                tp = entry - ((sl - entry) * self.rr_target)
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

        min_candles_by_mode = {
            "us30_open_wick": max(self.atr_period + 10, self.preopen_lookback_bars + 5),
            "us30_trend_pullback": max(205, self.atr_period + 10),
            "us30_asia_eq": max(self.atr_period + 10, 96),
            "us30_supply_demand": max(self.atr_period + 10, self.lookback_bars + self.impulse_bars + 4),
            "us30_fib_retrace": max(220, self.swing_lookback + 5),
        }
        min_candles = min_candles_by_mode[self.mode]
        if len(normalized) < min_candles:
            return self._skip(now, f"insufficient candles ({len(normalized)} < {min_candles})")

        atr_series = atr(normalized, self.atr_period)
        atr_value = atr_series.iloc[-1]
        if atr_value is None or pd.isna(atr_value) or float(atr_value) <= 0:
            return self._skip(now, "ATR invalid")
        atr_value = float(atr_value)

        handlers = {
            "us30_open_wick": self._opening_wick_signal,
            "us30_trend_pullback": self._trend_pullback_signal,
            "us30_asia_eq": self._asia_eq_signal,
            "us30_supply_demand": self._supply_demand_signal,
            "us30_fib_retrace": self._fib_retrace_signal,
        }
        signal = handlers[self.mode](normalized, now, atr_value)
        if signal is None:
            return self._skip(now, "setup not present")
        return signal
