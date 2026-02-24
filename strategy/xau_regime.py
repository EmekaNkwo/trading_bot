import pandas as pd

from utils.indicators import atr, adx, rsi
from utils.time_utils import SessionFilter
from utils.logger import setup_logger


class XAURegimeStrategy:
    """
    Adaptive regime strategy for XAUUSDm.

    Regimes:
      - SQUEEZE BREAKOUT: volatility contraction then expansion breakout
      - TREND PULLBACK: EMA trend + pullback touch-and-bounce entries
      - MEAN REVERSION: fade Bollinger extremes when trend is weak
    """

    def __init__(self, config):
        cfg = (config or {}).get("regime", {})

        self.atr_period = int(cfg.get("atr_period", 14))
        self.adx_period = int(cfg.get("adx_period", 14))

        self.ema_fast = int(cfg.get("ema_fast", 20))
        self.ema_slow = int(cfg.get("ema_slow", 80))

        self.bb_period = int(cfg.get("bb_period", 20))
        self.bb_std = float(cfg.get("bb_std", 2.0))

        self.squeeze_lookback = int(cfg.get("squeeze_lookback", 120))
        self.squeeze_pct = float(cfg.get("squeeze_pct", 0.20))  # lowest 20% width
        self.volume_lookback = int(cfg.get("volume_lookback", 20))
        self.volume_mult = float(cfg.get("volume_mult", 1.2))

        self.trend_adx = float(cfg.get("trend_adx", 22))
        self.meanrev_adx_max = float(cfg.get("meanrev_adx_max", 18))
        self.mr_rsi_buy = float(cfg.get("mr_rsi_buy", 35))
        self.mr_rsi_sell = float(cfg.get("mr_rsi_sell", 65))

        self.sl_atr_trend = float(cfg.get("sl_atr_trend", 1.6))
        self.tp_atr_trend = float(cfg.get("tp_atr_trend", 2.0))
        self.sl_atr_breakout = float(cfg.get("sl_atr_breakout", 1.8))
        self.tp_atr_breakout = float(cfg.get("tp_atr_breakout", 2.2))
        self.sl_atr_meanrev = float(cfg.get("sl_atr_meanrev", 1.3))
        self.tp_atr_meanrev = float(cfg.get("tp_atr_meanrev", 1.3))

        self.min_bars_between_signals = int(cfg.get("min_bars_between_signals", 3))
        self.block_asian_session = bool(cfg.get("block_asian_session", True))

        self.session = SessionFilter()
        self.logger = setup_logger()

        self._last_signal_bar = None

    def _is_asian_session(self, candle_time):
        hour = candle_time.hour if hasattr(candle_time, "hour") else candle_time.to_pydatetime().hour
        return 0 <= hour < 8

    def _bollinger(self, close: pd.Series):
        ma = close.rolling(self.bb_period).mean()
        std = close.rolling(self.bb_period).std()
        upper = ma + (std * self.bb_std)
        lower = ma - (std * self.bb_std)
        width = (upper - lower) / ma.replace(0, pd.NA)
        return ma, upper, lower, width

    def _cooldown_ok(self, candle_time):
        if self._last_signal_bar is None:
            return True
        try:
            bars_since = (candle_time - self._last_signal_bar)
            # if index is datetime, bars_since is timedelta; we use candle count instead
        except Exception:
            bars_since = None
        return True

    def on_candle(self, df: pd.DataFrame):
        if df is None or df.empty:
            return None

        candle_time = df.index[-1]
        price = float(df.iloc[-1].close)

        if not self.session.allowed(candle_time):
            return None

        if self.block_asian_session and self._is_asian_session(candle_time):
            return None

        # Need enough history
        min_candles = max(
            self.bb_period + 5,
            self.adx_period + 5,
            self.ema_slow + 5,
            self.atr_period + 5,
            self.squeeze_lookback + 5,
            self.volume_lookback + 5,
        )
        if len(df) < min_candles:
            return None

        # Indicator calc
        close = df["close"]
        ema_fast = close.ewm(span=self.ema_fast).mean()
        ema_slow = close.ewm(span=self.ema_slow).mean()

        atr_val = atr(df, self.atr_period).iloc[-1]
        if atr_val is None or pd.isna(atr_val) or float(atr_val) <= 0:
            return None
        atr_val = float(atr_val)

        adx_val, plus_di, minus_di = adx(df, self.adx_period)
        if pd.isna(adx_val.iloc[-1]):
            return None
        cur_adx = float(adx_val.iloc[-1])
        pdi = float(plus_di.iloc[-1])
        mdi = float(minus_di.iloc[-1])

        rsi_val = rsi(close, 14).iloc[-1]
        if pd.isna(rsi_val):
            return None
        cur_rsi = float(rsi_val)

        ma, upper, lower, width = self._bollinger(close)
        w_last = width.iloc[-1]
        if pd.isna(w_last):
            return None

        w_q = width.rolling(self.squeeze_lookback).quantile(self.squeeze_pct).iloc[-1]
        squeeze = (not pd.isna(w_q)) and (float(w_last) <= float(w_q))

        vol = df.get("tick_volume")
        vol_spike = False
        if vol is not None:
            vmean = vol.rolling(self.volume_lookback).mean().iloc[-1]
            if not pd.isna(vmean) and float(vmean) > 0:
                vol_spike = float(vol.iloc[-1]) >= float(vmean) * self.volume_mult

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Regime selection (prioritize squeeze breakout)
        if squeeze:
            regime = "breakout"
        elif cur_adx >= self.trend_adx:
            regime = "trend"
        else:
            regime = "meanrev"

        self.logger.info(
            f"REGIME CHECK | {candle_time} | Regime={regime} | "
            f"ADX={cur_adx:.1f} RSI={cur_rsi:.1f} ATR={atr_val:.3f} "
            f"BBWidth={float(w_last):.4f} Squeeze={squeeze} VolSpike={vol_spike}"
        )

        # ---------------------------
        # BREAKOUT: squeeze expansion
        # ---------------------------
        if regime == "breakout":
            up = float(upper.iloc[-1])
            lo = float(lower.iloc[-1])

            if float(last.close) > up and vol_spike:
                sl = float(last.close) - (atr_val * self.sl_atr_breakout)
                tp = float(last.close) + (atr_val * self.tp_atr_breakout)
                self._last_signal_bar = candle_time
                return {"side": "buy", "sl": round(sl, 3), "tp": round(tp, 3), "strategy": "xau_regime"}

            if float(last.close) < lo and vol_spike:
                sl = float(last.close) + (atr_val * self.sl_atr_breakout)
                tp = float(last.close) - (atr_val * self.tp_atr_breakout)
                self._last_signal_bar = candle_time
                return {"side": "sell", "sl": round(sl, 3), "tp": round(tp, 3), "strategy": "xau_regime"}

            return None

        # ---------------------------
        # TREND: pullback to fast EMA
        # ---------------------------
        if regime == "trend":
            fast = float(ema_fast.iloc[-1])
            slow = float(ema_slow.iloc[-1])

            uptrend = fast > slow and pdi > mdi
            downtrend = fast < slow and mdi > pdi

            if uptrend:
                # Touch-and-bounce off fast EMA
                if float(last.low) <= fast and float(last.close) > fast and float(prev.close) >= fast:
                    sl = float(last.close) - (atr_val * self.sl_atr_trend)
                    tp = float(last.close) + (atr_val * self.tp_atr_trend)
                    self._last_signal_bar = candle_time
                    return {"side": "buy", "sl": round(sl, 3), "tp": round(tp, 3), "strategy": "xau_regime"}

            if downtrend:
                if float(last.high) >= fast and float(last.close) < fast and float(prev.close) <= fast:
                    sl = float(last.close) + (atr_val * self.sl_atr_trend)
                    tp = float(last.close) - (atr_val * self.tp_atr_trend)
                    self._last_signal_bar = candle_time
                    return {"side": "sell", "sl": round(sl, 3), "tp": round(tp, 3), "strategy": "xau_regime"}

            return None

        # ---------------------------
        # MEAN REVERSION: fade extremes
        # ---------------------------
        if cur_adx > self.meanrev_adx_max:
            return None

        up = float(upper.iloc[-1])
        lo = float(lower.iloc[-1])

        if float(last.close) < lo and cur_rsi <= self.mr_rsi_buy:
            sl = float(last.close) - (atr_val * self.sl_atr_meanrev)
            tp = float(last.close) + (atr_val * self.tp_atr_meanrev)
            self._last_signal_bar = candle_time
            return {"side": "buy", "sl": round(sl, 3), "tp": round(tp, 3), "strategy": "xau_regime"}

        if float(last.close) > up and cur_rsi >= self.mr_rsi_sell:
            sl = float(last.close) + (atr_val * self.sl_atr_meanrev)
            tp = float(last.close) - (atr_val * self.tp_atr_meanrev)
            self._last_signal_bar = candle_time
            return {"side": "sell", "sl": round(sl, 3), "tp": round(tp, 3), "strategy": "xau_regime"}

        return None

