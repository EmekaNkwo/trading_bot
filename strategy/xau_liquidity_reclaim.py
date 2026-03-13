from __future__ import annotations

import pandas as pd

from utils.indicators import atr
from utils.logger import setup_logger
from utils.time_utils import SessionFilter


class XAULiquidityReclaimStrategy:
    """
    v2 setup:
    Trade reclaim after a detected liquidity sweep.
    """

    def __init__(self, config: dict):
        cfg = (config or {}).get("liquidity_reclaim", {})
        self.atr_period = int(cfg.get("atr_period", 14))
        self.recent_sweep_minutes = int(cfg.get("recent_sweep_minutes", 20))
        self.reclaim_buffer_atr = float(cfg.get("reclaim_buffer_atr", 0.05))
        self.wick_reject_ratio = float(cfg.get("wick_reject_ratio", 0.45))
        self.sl_buffer_atr = float(cfg.get("sl_buffer_atr", 0.20))
        self.rr_target = float(cfg.get("rr_target", 1.6))
        self.min_bars_between_signals = int(cfg.get("min_bars_between_signals", 2))
        self.require_active_session = bool(cfg.get("require_active_session", True))
        self.block_high_vol = bool(cfg.get("block_high_vol", True))

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

    def _wick_reject(self, row: pd.Series, side: str) -> bool:
        high = float(row.high)
        low = float(row.low)
        o = float(row.open)
        c = float(row.close)
        rng = max(1e-9, high - low)
        if side == "buy":
            lower_wick = min(o, c) - low
            return (lower_wick / rng) >= self.wick_reject_ratio
        upper_wick = high - max(o, c)
        return (upper_wick / rng) >= self.wick_reject_ratio

    def _skip(self, now: pd.Timestamp, reason: str):
        self.logger.info(f"LIQ RECLAIM SKIP | {now} | {reason}")
        return None

    def on_candle(self, df: pd.DataFrame):
        if df is None or df.empty or len(df) < (self.atr_period + 5):
            now = pd.Timestamp(df.index[-1]) if (df is not None and not df.empty) else pd.Timestamp.utcnow()
            return self._skip(now, "insufficient candles")

        now = pd.Timestamp(df.index[-1])
        if not self._cooldown_ok(now):
            return self._skip(now, "cooldown active")

        if self.require_active_session and (not self.session.allowed(now)):
            return self._skip(now, "outside active session")

        a = atr(df, self.atr_period).iloc[-1]
        if a is None or pd.isna(a) or float(a) <= 0:
            return self._skip(now, "ATR invalid")
        atr_val = float(a)
        last = df.iloc[-1]
        entry = float(last.close)

        if self.market_state is None:
            return self._skip(now, "market_state unavailable")
        st = self.market_state.get(self.symbol)
        if st is None or st.last_sweep_direction is None or st.last_sweep_time is None:
            return self._skip(now, "no recent sweep context")
        if self.block_high_vol and st.volatility_regime == "high":
            return self._skip(now, "blocked in high volatility regime")

        age = now - pd.Timestamp(st.last_sweep_time)
        if age < pd.Timedelta(0) or age > pd.Timedelta(minutes=max(1, self.recent_sweep_minutes)):
            return self._skip(now, f"sweep age out of window ({age})")
        if st.last_sweep_level is None or st.last_sweep_extreme is None:
            return self._skip(now, "sweep level/extreme missing")

        # Sweep down then reclaim above level => buy
        if st.last_sweep_direction == "down":
            reclaim_level = float(st.last_sweep_level) + (atr_val * self.reclaim_buffer_atr)
            if entry > reclaim_level and self._wick_reject(last, "buy"):
                sl = min(float(last.low), float(st.last_sweep_extreme)) - (atr_val * self.sl_buffer_atr)
                risk = entry - sl
                if risk <= 0:
                    return self._skip(now, "buy risk <= 0 after SL calc")
                tp = entry + (risk * self.rr_target)
                self._last_signal_at = now
                self.logger.info(f"LIQ RECLAIM BUY | {now} | lvl={st.last_sweep_level:.3f} atr={atr_val:.3f}")
                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_liquidity_reclaim",
                    "entry": entry,
                    "min_rr": float(self.rr_target),
                }
            return self._skip(now, "buy reclaim/wick conditions not met")

        # Sweep up then reclaim below level => sell
        if st.last_sweep_direction == "up":
            reclaim_level = float(st.last_sweep_level) - (atr_val * self.reclaim_buffer_atr)
            if entry < reclaim_level and self._wick_reject(last, "sell"):
                sl = max(float(last.high), float(st.last_sweep_extreme)) + (atr_val * self.sl_buffer_atr)
                risk = sl - entry
                if risk <= 0:
                    return self._skip(now, "sell risk <= 0 after SL calc")
                tp = entry - (risk * self.rr_target)
                self._last_signal_at = now
                self.logger.info(f"LIQ RECLAIM SELL | {now} | lvl={st.last_sweep_level:.3f} atr={atr_val:.3f}")
                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_liquidity_reclaim",
                    "entry": entry,
                    "min_rr": float(self.rr_target),
                }
            return self._skip(now, "sell reclaim/wick conditions not met")

        return self._skip(now, "sweep direction not actionable")

