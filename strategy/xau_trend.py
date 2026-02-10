from utils.indicators import atr
from utils.time_utils import SessionFilter
from utils.logger import setup_logger


class XAUTrendStrategy:
    """
    Fully config-driven trend strategy.
    No hard-coded parameters.
    """

    def __init__(self, config):

        strategy_cfg = config["strategy"]

        # --------------------------
        # Trend parameters
        # --------------------------
        self.ema_fast = strategy_cfg.get("ema_fast", 50)
        self.ema_slow = strategy_cfg.get("ema_slow", 200)

        # --------------------------
        # ATR risk model
        # --------------------------
        atr_cfg = strategy_cfg["atr"]

        self.atr_period = atr_cfg["period"]
        self.sl_mult = atr_cfg["sl_multiplier"]
        self.tp_mult = atr_cfg.get("tp_multiplier", 3.0)  # Use tp_multiplier for wider trend targets

        # --------------------------
        # Session filter
        # --------------------------
        self.session = SessionFilter()
        
        # --------------------------
        # Volatility monitoring
        # --------------------------
        self.atr_history = []
        self.max_atr_history = 20
        self.last_signal_time = None

        # --------------------------
        # Logging
        # --------------------------
        self.logger = setup_logger()

    # --------------------------------------------------
    # MAIN STRATEGY LOGIC
    # --------------------------------------------------

    def on_candle(self, df):

        # ---- Session filter ----
        current_time = df.index[-1]
        price = df.iloc[-1].close
        
        self.logger.info(
            f"TREND CHECK | Time: {current_time} | Price: {price:.3f} | "
            f"Candles: {len(df)} | Session: {self.session.allowed(current_time)}"
        )
        
        if not self.session.allowed(current_time):
            self.logger.info("SESSION FILTER: Market closed - no trading allowed")
            return None

        # ---- Data safety ----
        if len(df) < self.ema_slow + 5:
            self.logger.info(f"DATA INSUFFICIENT | Need {self.ema_slow + 5} candles, have {len(df)}")
            return None

        close = df["close"]

        ema_fast = close.ewm(span=self.ema_fast).mean()
        ema_slow = close.ewm(span=self.ema_slow).mean()

        atr_val = atr(df, self.atr_period).iloc[-1]

        if atr_val is None or atr_val <= 0:
            self.logger.info("ATR INVALID | No valid ATR calculation")
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # Log technical indicators
        trend_direction = "BULLISH" if ema_fast.iloc[-1] > ema_slow.iloc[-1] else "BEARISH"
        
        self.logger.info(
            f"TREND TECHNICALS | Direction: {trend_direction} | "
            f"Fast EMA: {ema_fast.iloc[-1]:.3f} | "
            f"Slow EMA: {ema_slow.iloc[-1]:.3f} | "
            f"ATR: {atr_val:.3f}"
        )

        # Monitor volatility changes
        self.atr_history.append(atr_val)
        if len(self.atr_history) > self.max_atr_history:
            self.atr_history.pop(0)
        
        if len(self.atr_history) >= 5:
            avg_atr = sum(self.atr_history[-5:]) / 5
            volatility_change = (atr_val - avg_atr) / avg_atr * 100
            
            self.logger.info(
                f"TREND VOLATILITY | Current ATR: {atr_val:.3f} | "
                f"5-candle Avg: {avg_atr:.3f} | "
                f"Change: {volatility_change:+.1f}%"
            )
            
            # Alert on significant volatility increase
            if volatility_change > 20:  # 20% increase in volatility
                self.logger.info(f"VOLATILITY SPIKE: {volatility_change:+.1f}% - Trend opportunities may increase")

        # ==================================================
        # BUY SETUP
        # ==================================================
        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:

            if prev.close < ema_fast.iloc[-1] and last.close > ema_fast.iloc[-1]:

                self.logger.info("TREND BUY SIGNAL - EMA crossover detected")
                
                # Track signal timing
                from datetime import datetime
                self.last_signal_time = datetime.utcnow()
                
                sl = last.close - (atr_val * self.sl_mult)
                tp = last.close + (atr_val * self.tp_mult)

                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_trend",
                }

        # ==================================================
        # SELL SETUP
        # ==================================================
        if ema_fast.iloc[-1] < ema_slow.iloc[-1]:

            if prev.close > ema_fast.iloc[-1] and last.close < ema_fast.iloc[-1]:

                self.logger.info("TREND SELL SIGNAL - EMA crossover detected")
                
                # Track signal timing
                from datetime import datetime
                self.last_signal_time = datetime.utcnow()
                
                sl = last.close + (atr_val * self.sl_mult)
                tp = last.close - (atr_val * self.tp_mult)

                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_trend",
                }

        # Log no signal conditions
        self.logger.info("NO TREND SIGNAL | EMA crossover conditions not met")
        return None
