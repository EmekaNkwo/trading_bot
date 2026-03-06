from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Optional

import pandas as pd

from utils.indicators import atr


def _tf_minutes(tf: str) -> int:
    t = str(tf or "").upper()
    if t.startswith("M"):
        try:
            return max(1, int(t[1:]))
        except Exception:
            return 5
    if t.startswith("H"):
        try:
            return max(60, int(t[1:]) * 60)
        except Exception:
            return 60
    return 5


@dataclass
class SymbolMarketState:
    symbol: str
    timeframe: str
    candle_time: pd.Timestamp
    session: str
    atr: float
    atr_median: float
    volatility_regime: str
    opening_range_ready: bool
    opening_range_high: float | None
    opening_range_low: float | None
    opening_range_start: pd.Timestamp | None
    last_sweep_direction: str | None
    last_sweep_time: pd.Timestamp | None
    last_sweep_level: float | None
    last_sweep_extreme: float | None


class MarketStateStore:
    """
    Shared symbol-level context for strategy gating.
    This is intentionally simple and deterministic for live use.
    """

    def __init__(self, cfg: dict | None = None) -> None:
        c = (cfg or {}).get("market_state", {}) if isinstance(cfg, dict) else {}
        self.atr_period = int(c.get("atr_period", 14))
        self.atr_median_lookback = int(c.get("atr_median_lookback", 120))
        self.high_vol_mult = float(c.get("high_vol_mult", 1.35))
        self.low_vol_mult = float(c.get("low_vol_mult", 0.75))
        self.sweep_lookback_bars = int(c.get("sweep_lookback_bars", 48))
        self.sweep_atr_mult = float(c.get("sweep_atr_mult", 0.20))
        self.london_open_hour_utc = int(c.get("london_open_hour_utc", 8))
        self.opening_range_minutes = int(c.get("opening_range_minutes", 30))

        self._lock = Lock()
        self._states: dict[str, SymbolMarketState] = {}

    def _session_name(self, ts: pd.Timestamp) -> str:
        h = int(pd.Timestamp(ts).hour)
        if 8 <= h <= 17:
            return "london"
        if 13 <= h <= 22:
            return "newyork"
        return "offsession"

    def _compute_opening_range(self, df: pd.DataFrame, now: pd.Timestamp, tf: str) -> tuple[bool, Optional[float], Optional[float], Optional[pd.Timestamp]]:
        idx = df.index
        if not isinstance(idx, pd.DatetimeIndex):
            return False, None, None, None

        today = now.date()
        start = pd.Timestamp(now).replace(
            hour=self.london_open_hour_utc,
            minute=0,
            second=0,
            microsecond=0,
        )
        end = start + pd.Timedelta(minutes=max(5, self.opening_range_minutes))
        if start.date() != today:
            return False, None, None, start

        sub = df[(df.index >= start) & (df.index < end)]
        bars_needed = max(1, int(max(5, self.opening_range_minutes) / max(1, _tf_minutes(tf))))
        if len(sub) < bars_needed:
            return False, None, None, start

        return True, float(sub["high"].max()), float(sub["low"].min()), start

    def update(self, *, symbol: str, timeframe: str, df: pd.DataFrame) -> SymbolMarketState | None:
        if df is None or df.empty:
            return None
        if len(df) < max(self.atr_period + 5, self.sweep_lookback_bars + 5):
            return None

        now = pd.Timestamp(df.index[-1])
        a = atr(df, self.atr_period)
        if a is None or len(a) == 0 or pd.isna(a.iloc[-1]):
            return None

        atr_now = float(a.iloc[-1])
        a_med = a.tail(max(self.atr_median_lookback, self.atr_period * 3)).median()
        atr_med = float(a_med) if not pd.isna(a_med) else atr_now

        if atr_now >= (atr_med * self.high_vol_mult):
            vol_regime = "high"
        elif atr_now <= (atr_med * self.low_vol_mult):
            vol_regime = "low"
        else:
            vol_regime = "normal"

        ready, or_hi, or_lo, or_start = self._compute_opening_range(df, now, timeframe)

        look = int(max(10, self.sweep_lookback_bars))
        prev = df.iloc[-(look + 1):-1]
        prev_high = float(prev["high"].max())
        prev_low = float(prev["low"].min())
        last = df.iloc[-1]
        high = float(last.high)
        low = float(last.low)

        sweep_dir = None
        sweep_lvl = None
        sweep_ext = None
        if high > (prev_high + (atr_now * self.sweep_atr_mult)):
            sweep_dir = "up"
            sweep_lvl = prev_high
            sweep_ext = high
        elif low < (prev_low - (atr_now * self.sweep_atr_mult)):
            sweep_dir = "down"
            sweep_lvl = prev_low
            sweep_ext = low

        with self._lock:
            prev_state = self._states.get(symbol)
            if sweep_dir is None and prev_state is not None:
                sweep_dir = prev_state.last_sweep_direction
                sweep_lvl = prev_state.last_sweep_level
                sweep_ext = prev_state.last_sweep_extreme
                sweep_time = prev_state.last_sweep_time
            else:
                sweep_time = now if sweep_dir is not None else None

            state = SymbolMarketState(
                symbol=str(symbol),
                timeframe=str(timeframe),
                candle_time=now,
                session=self._session_name(now),
                atr=float(atr_now),
                atr_median=float(atr_med),
                volatility_regime=vol_regime,
                opening_range_ready=bool(ready),
                opening_range_high=or_hi,
                opening_range_low=or_lo,
                opening_range_start=or_start,
                last_sweep_direction=sweep_dir,
                last_sweep_time=sweep_time,
                last_sweep_level=sweep_lvl,
                last_sweep_extreme=sweep_ext,
            )
            self._states[symbol] = state
            return state

    def get(self, symbol: str) -> SymbolMarketState | None:
        with self._lock:
            return self._states.get(symbol)

    def is_recent_sweep(self, *, symbol: str, direction: str, now: pd.Timestamp, max_minutes: int) -> bool:
        st = self.get(symbol)
        if st is None or st.last_sweep_direction != direction or st.last_sweep_time is None:
            return False
        age = pd.Timestamp(now) - pd.Timestamp(st.last_sweep_time)
        return pd.Timedelta(0) <= age <= pd.Timedelta(minutes=max(1, int(max_minutes)))

