from __future__ import annotations

from datetime import time
import pandas as pd

from utils.indicators import atr, adx, rsi
from utils.logger import setup_logger
from utils.time_utils import SessionFilter


class MultiAssetRegimeStrategy:
    """
    Symbol-agnostic regime strategy for indices/crypto/commodities.

    Regimes:
      - trend: EMA alignment + pullback bounce
      - meanrev: RSI extremes in weak trend conditions
    """

    def __init__(self, config: dict):
        root = (config or {}).get("multi_asset_regime", {})

        # Global defaults
        self.atr_period = int(root.get("atr_period", 14))
        self.adx_period = int(root.get("adx_period", 14))
        self.rsi_period = int(root.get("rsi_period", 14))
        self.ema_fast = int(root.get("ema_fast", 20))
        self.ema_slow = int(root.get("ema_slow", 80))

        self.trend_adx_min = float(root.get("trend_adx_min", 24))
        self.meanrev_adx_max = float(root.get("meanrev_adx_max", 18))
        self.trend_rsi_buy_max = float(root.get("trend_rsi_buy_max", 62))
        self.trend_rsi_sell_min = float(root.get("trend_rsi_sell_min", 38))
        self.mr_rsi_buy = float(root.get("mr_rsi_buy", 28))
        self.mr_rsi_sell = float(root.get("mr_rsi_sell", 72))

        self.sl_atr_trend = float(root.get("sl_atr_trend", 1.6))
        self.tp_atr_trend = float(root.get("tp_atr_trend", 2.2))
        self.sl_atr_meanrev = float(root.get("sl_atr_meanrev", 1.3))
        self.tp_atr_meanrev = float(root.get("tp_atr_meanrev", 1.8))
        self.min_rr = float(root.get("min_rr", 1.2))
        self.min_bars_between_signals = int(root.get("min_bars_between_signals", 3))
        self.bar_seconds = int(root.get("bar_seconds", 300))

        # Session defaults
        self.session_mode = str(root.get("session_mode", "london_newyork")).lower()
        self.allow_london = bool(root.get("allow_london", True))
        self.allow_newyork = bool(root.get("allow_newyork", True))
        self.allow_asia = bool(root.get("allow_asia", False))

        # Symbol-specific overrides
        self.symbol_overrides = dict(root.get("symbols", {}) or {})

        self.logger = setup_logger()
        self.session = SessionFilter()
        self.symbol = "UNKNOWN"
        self._last_signal_bar = None

    def bind_symbol(self, symbol: str) -> None:
        self.symbol = str(symbol)
        cfg = self.symbol_overrides.get(self.symbol, {})
        if not isinstance(cfg, dict):
            return

        # Allow per-symbol tuning while sharing the same strategy class
        for k in (
            "atr_period",
            "adx_period",
            "rsi_period",
            "ema_fast",
            "ema_slow",
            "trend_adx_min",
            "meanrev_adx_max",
            "trend_rsi_buy_max",
            "trend_rsi_sell_min",
            "mr_rsi_buy",
            "mr_rsi_sell",
            "sl_atr_trend",
            "tp_atr_trend",
            "sl_atr_meanrev",
            "tp_atr_meanrev",
            "min_rr",
            "min_bars_between_signals",
            "bar_seconds",
            "session_mode",
            "allow_london",
            "allow_newyork",
            "allow_asia",
        ):
            if k in cfg:
                setattr(self, k, cfg[k])

        # Normalize numeric/bool fields that may come from YAML as strings
        self.atr_period = int(self.atr_period)
        self.adx_period = int(self.adx_period)
        self.rsi_period = int(self.rsi_period)
        self.ema_fast = int(self.ema_fast)
        self.ema_slow = int(self.ema_slow)
        self.trend_adx_min = float(self.trend_adx_min)
        self.meanrev_adx_max = float(self.meanrev_adx_max)
        self.trend_rsi_buy_max = float(self.trend_rsi_buy_max)
        self.trend_rsi_sell_min = float(self.trend_rsi_sell_min)
        self.mr_rsi_buy = float(self.mr_rsi_buy)
        self.mr_rsi_sell = float(self.mr_rsi_sell)
        self.sl_atr_trend = float(self.sl_atr_trend)
        self.tp_atr_trend = float(self.tp_atr_trend)
        self.sl_atr_meanrev = float(self.sl_atr_meanrev)
        self.tp_atr_meanrev = float(self.tp_atr_meanrev)
        self.min_rr = float(self.min_rr)
        self.min_bars_between_signals = int(self.min_bars_between_signals)
        self.bar_seconds = int(self.bar_seconds)
        self.session_mode = str(self.session_mode).lower()
        self.allow_london = bool(self.allow_london)
        self.allow_newyork = bool(self.allow_newyork)
        self.allow_asia = bool(self.allow_asia)

    def _session_allowed(self, candle_time: pd.Timestamp) -> bool:
        mode = str(self.session_mode).lower()
        if mode in ("24x7", "always", "all"):
            return True

        t = pd.Timestamp(candle_time)
        if t.tzinfo is None:
            t = t.tz_localize("UTC")
        t_utc = t.tz_convert("UTC")
        hour = int(t_utc.hour)
        tod = t_utc.time()

        # Asia proxy window for UTC (00:00-07:59)
        if self.allow_asia and (0 <= hour < 8):
            return True

        london = self.session.london
        ny = self.session.newyork

        if self.allow_london and (time(london[0]) <= tod <= time(london[1])):
            return True
        if self.allow_newyork and (time(ny[0]) <= tod <= time(ny[1])):
            return True

        return False

    def _reward_to_risk_ok(self, side: str, entry: float, sl: float, tp: float) -> bool:
        risk = (entry - sl) if side == "buy" else (sl - entry)
        reward = (tp - entry) if side == "buy" else (entry - tp)
        if risk <= 0 or reward <= 0:
            return False
        return (reward / risk) >= float(self.min_rr)

    def _cooldown_ok(self, candle_time: pd.Timestamp) -> bool:
        if self._last_signal_bar is None:
            return True
        try:
            step = float(max(1, self.bar_seconds))
            bars = int((pd.Timestamp(candle_time) - pd.Timestamp(self._last_signal_bar)).total_seconds() / step)
        except Exception:
            return True
        return bars >= max(1, self.min_bars_between_signals)

    def on_candle(self, df: pd.DataFrame):
        if df is None or df.empty:
            return None

        for col in ("open", "high", "low", "close"):
            if col not in df.columns:
                return None

        candle_time = pd.Timestamp(df.index[-1])
        if not self._session_allowed(candle_time):
            return None
        if not self._cooldown_ok(candle_time):
            return None

        min_candles = max(self.ema_slow + 5, self.atr_period + 5, self.adx_period + 5, self.rsi_period + 5)
        if len(df) < min_candles:
            return None

        close = df["close"]
        ema_fast = close.ewm(span=self.ema_fast, adjust=False).mean()
        ema_slow = close.ewm(span=self.ema_slow, adjust=False).mean()

        atr_val = atr(df, self.atr_period).iloc[-1]
        if atr_val is None or pd.isna(atr_val) or float(atr_val) <= 0:
            return None
        atr_val = float(atr_val)

        adx_val, plus_di, minus_di = adx(df, self.adx_period)
        if pd.isna(adx_val.iloc[-1]) or pd.isna(plus_di.iloc[-1]) or pd.isna(minus_di.iloc[-1]):
            return None
        cur_adx = float(adx_val.iloc[-1])
        pdi = float(plus_di.iloc[-1])
        mdi = float(minus_di.iloc[-1])

        rsi_val = rsi(close, self.rsi_period).iloc[-1]
        if pd.isna(rsi_val):
            return None
        cur_rsi = float(rsi_val)

        last = df.iloc[-1]
        prev = df.iloc[-2]
        fast = float(ema_fast.iloc[-1])
        slow = float(ema_slow.iloc[-1])
        prev_fast = float(ema_fast.iloc[-2])
        entry = float(last.close)

        # ---------------------------
        # TREND regime
        # ---------------------------
        if cur_adx >= self.trend_adx_min:
            uptrend = fast > slow and pdi > mdi
            downtrend = fast < slow and mdi > pdi

            if (
                uptrend
                and float(last.low) <= fast
                and float(last.close) > fast
                and float(prev.close) >= prev_fast
                and cur_rsi <= self.trend_rsi_buy_max
            ):
                sl = entry - (atr_val * self.sl_atr_trend)
                tp = entry + (atr_val * self.tp_atr_trend)
                if self._reward_to_risk_ok("buy", entry, sl, tp):
                    self._last_signal_bar = candle_time
                    return {
                        "side": "buy",
                        "sl": round(sl, 3),
                        "tp": round(tp, 3),
                        "strategy": "multi_asset_regime",
                        "entry": entry,
                        "min_rr": float(self.min_rr),
                    }

            if (
                downtrend
                and float(last.high) >= fast
                and float(last.close) < fast
                and float(prev.close) <= prev_fast
                and cur_rsi >= self.trend_rsi_sell_min
            ):
                sl = entry + (atr_val * self.sl_atr_trend)
                tp = entry - (atr_val * self.tp_atr_trend)
                if self._reward_to_risk_ok("sell", entry, sl, tp):
                    self._last_signal_bar = candle_time
                    return {
                        "side": "sell",
                        "sl": round(sl, 3),
                        "tp": round(tp, 3),
                        "strategy": "multi_asset_regime",
                        "entry": entry,
                        "min_rr": float(self.min_rr),
                    }

            return None

        # ---------------------------
        # MEAN REVERSION regime
        # ---------------------------
        if cur_adx > self.meanrev_adx_max:
            return None

        if cur_rsi <= self.mr_rsi_buy and entry < fast:
            sl = entry - (atr_val * self.sl_atr_meanrev)
            tp = entry + (atr_val * self.tp_atr_meanrev)
            if self._reward_to_risk_ok("buy", entry, sl, tp):
                self._last_signal_bar = candle_time
                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "multi_asset_regime",
                    "entry": entry,
                    "min_rr": float(self.min_rr),
                }

        if cur_rsi >= self.mr_rsi_sell and entry > fast:
            sl = entry + (atr_val * self.sl_atr_meanrev)
            tp = entry - (atr_val * self.tp_atr_meanrev)
            if self._reward_to_risk_ok("sell", entry, sl, tp):
                self._last_signal_bar = candle_time
                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "multi_asset_regime",
                    "entry": entry,
                    "min_rr": float(self.min_rr),
                }

        return None
