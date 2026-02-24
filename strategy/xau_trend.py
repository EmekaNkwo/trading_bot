from utils.indicators import atr, adx, rsi
from utils.time_utils import SessionFilter
from utils.logger import setup_logger
import pandas as pd


class XAUTrendStrategy:
    """
    PROPER Trend Following Strategy - Only trades strong trends.
    
    Key Improvements:
    - ADX > 25 filter (only trade strong trends)
    - RSI confirmation (buy pullbacks, not tops)
    - Asian session blocked (choppy, fake moves)
    - Don't chase extended moves
    - Tighter SL (1.5x ATR instead of 2x)
    """

    def __init__(self, config):

        strategy_cfg = config["strategy"]

        # Trend parameters
        self.ema_fast = strategy_cfg.get("ema_fast", 50)
        self.ema_slow = strategy_cfg.get("ema_slow", 200)

        # ATR risk model
        atr_cfg = strategy_cfg["atr"]
        self.atr_period = atr_cfg["period"]
        self.sl_mult = atr_cfg.get("sl_multiplier", 1.5)  # Reduced from 2.0
        self.tp_mult = atr_cfg.get("tp_multiplier", 2.0)  # Reduced from 3.0 for faster profits

        # NEW: Trend strength filter
        self.min_adx = strategy_cfg.get("min_adx", 25)  # Only trade ADX > 25
        self.adx_period = strategy_cfg.get("adx_period", 14)

        # NEW: RSI pullback confirmation
        self.rsi_period = strategy_cfg.get("rsi_period", 14)
        self.rsi_buy_max = strategy_cfg.get("rsi_buy_max", 60)  # Don't buy if RSI > 60 (overbought)
        self.rsi_sell_min = strategy_cfg.get("rsi_sell_min", 40)  # Don't sell if RSI < 40 (oversold)

        # Entry permissiveness (lets you tune trade frequency)
        self.max_ema_distance_atr = strategy_cfg.get("max_ema_distance_atr", 1.5)
        self.block_asian_session = strategy_cfg.get("block_asian_session", True)

        # Session filter
        self.session = SessionFilter()

        # Volatility monitoring
        self.atr_history = []
        self.max_atr_history = 20
        self.last_signal_time = None

        # Logging
        self.logger = setup_logger()

    def _is_asian_session(self, current_time):
        """Check if we're in Asian session (typically choppy for XAUUSD)"""
        hour = current_time.hour if hasattr(current_time, 'hour') else current_time.to_pydatetime().hour
        # Asian session: 00:00 - 08:00 UTC
        return 0 <= hour < 8

    def _is_price_extended(self, price, ema_fast, ema_slow, atr_val):
        """Check if price is too far from EMAs (chasing = bad entries)"""
        avg_ema = (ema_fast + ema_slow) / 2
        distance = abs(price - avg_ema)
        max_distance = atr_val * float(self.max_ema_distance_atr)
        return distance > max_distance

    def on_candle(self, df):

        # Session filter
        current_time = df.index[-1]
        price = df.iloc[-1].close

        self.logger.info(
            f"TREND CHECK | Time: {current_time} | Price: {price:.3f} | "
            f"Candles: {len(df)} | Session: {self.session.allowed(current_time)}"
        )

        if not self.session.allowed(current_time):
            self.logger.info("SESSION FILTER: Market closed")
            return None

        # Optional: Block Asian session (choppy, fake breakouts)
        if self.block_asian_session and self._is_asian_session(current_time):
            self.logger.info("ASIAN SESSION BLOCKED: Avoiding choppy conditions")
            return None

        # Data safety
        min_candles = max(self.ema_slow + 5, self.adx_period + 5, self.rsi_period + 5)
        if len(df) < min_candles:
            self.logger.info(f"DATA INSUFFICIENT | Need {min_candles} candles")
            return None

        close = df["close"]

        # Calculate indicators
        ema_fast = close.ewm(span=self.ema_fast).mean()
        ema_slow = close.ewm(span=self.ema_slow).mean()
        atr_val = atr(df, self.atr_period).iloc[-1]
        adx_val, plus_di, minus_di = adx(df, self.adx_period)
        rsi_val = rsi(close, self.rsi_period)

        if atr_val is None or atr_val <= 0:
            self.logger.info("ATR INVALID")
            return None

        if adx_val.iloc[-1] is None or pd.isna(adx_val.iloc[-1]):
            self.logger.info("ADX INVALID")
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        current_adx = adx_val.iloc[-1]
        current_rsi = rsi_val.iloc[-1]
        fast_ema_now = ema_fast.iloc[-1]
        slow_ema_now = ema_slow.iloc[-1]
        plus_di_now = plus_di.iloc[-1]
        minus_di_now = minus_di.iloc[-1]

        # Log technicals
        trend_direction = "BULLISH" if fast_ema_now > slow_ema_now else "BEARISH"
        trend_strength = "WEAK" if current_adx < 20 else "MODERATE" if current_adx < 40 else "STRONG"

        self.logger.info(
            f"TREND TECHNICALS | Direction: {trend_direction} | "
            f"Strength: {trend_strength} (ADX: {current_adx:.1f}) | "
            f"RSI: {current_rsi:.1f} | "
            f"Fast EMA: {fast_ema_now:.3f} | Slow EMA: {slow_ema_now:.3f} | "
            f"ATR: {atr_val:.3f}"
        )

        # ============================================================
        # FILTER 1: Trend Strength (ADX)
        # ============================================================
        if current_adx < self.min_adx:
            self.logger.info(f"FILTER BLOCKED: ADX too weak ({current_adx:.1f} < {self.min_adx})")
            return None

        # ============================================================
        # FILTER 2: Don't chase extended moves
        # ============================================================
        if self._is_price_extended(price, fast_ema_now, slow_ema_now, atr_val):
            self.logger.info("FILTER BLOCKED: Price too extended from EMAs (don't chase)")
            return None

        # ==================================================
        # BUY SETUP - Only in confirmed uptrend
        # ==================================================
        if fast_ema_now > slow_ema_now and plus_di_now > minus_di_now:

            # Entry: Price pulls back to fast EMA and RSI shows not overbought
            if prev.close < fast_ema_now and last.close > fast_ema_now:

                # FILTER 3: RSI pullback confirmation
                if current_rsi > self.rsi_buy_max:
                    self.logger.info(f"BUY BLOCKED: RSI too high ({current_rsi:.1f} > {self.rsi_buy_max}) - overbought")
                    return None

                self.logger.info(f"TREND BUY SIGNAL | ADX: {current_adx:.1f} | RSI: {current_rsi:.1f}")

                from datetime import datetime
                self.last_signal_time = datetime.utcnow()

                # Tighter SL for better R:R
                sl = last.close - (atr_val * self.sl_mult)
                tp = last.close + (atr_val * self.tp_mult)

                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_trend",
                }

        # ==================================================
        # SELL SETUP - Only in confirmed downtrend
        # ==================================================
        if fast_ema_now < slow_ema_now and minus_di_now > plus_di_now:

            # Entry: Price pulls back to fast EMA
            if prev.close > fast_ema_now and last.close < fast_ema_now:

                # FILTER 3: RSI pullback confirmation
                if current_rsi < self.rsi_sell_min:
                    self.logger.info(f"SELL BLOCKED: RSI too low ({current_rsi:.1f} < {self.rsi_sell_min}) - oversold")
                    return None

                self.logger.info(f"TREND SELL SIGNAL | ADX: {current_adx:.1f} | RSI: {current_rsi:.1f}")

                from datetime import datetime
                self.last_signal_time = datetime.utcnow()

                # Tighter SL for better R:R
                sl = last.close + (atr_val * self.sl_mult)
                tp = last.close - (atr_val * self.tp_mult)

                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_trend",
                }

        self.logger.info("NO TREND SIGNAL | Conditions not met")
        return None
