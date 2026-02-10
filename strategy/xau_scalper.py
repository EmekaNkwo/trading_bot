import pandas as pd
from utils.indicators import atr
from utils.time_utils import SessionFilter
from utils.filters import SpreadFilter, NewsFilter
from utils.logger import setup_logger


class XAUScalper:

    def __init__(self, config):

        cfg = config["scalper"]

        self.ema_period = cfg["ema_period"]
        self.bb_period = cfg["bb_period"]
        self.bb_std = cfg["bb_std"]

        self.atr_period = cfg["atr_period"]
        self.sl_mult = cfg["sl_atr"]
        self.tp_mult = cfg["tp_atr"]

        self.min_candles = cfg["min_candles"]
        
        # Trailing stop settings
        self.trailing_enabled = cfg.get("trailing_stop", True)
        self.trailing_atr_multiplier = cfg.get("trailing_atr_multiplier", 1.0)
        self.trailing_step = cfg.get("trailing_step", 0.5)
        self.best_price = None  # Track best price for trailing
        
        # Volatility monitoring
        self.atr_history = []
        self.max_atr_history = 20
        self.last_signal_time = None

        self.session = SessionFilter()
        self.spread_filter = SpreadFilter(max_spread_points=cfg.get("max_spread_points", 30))
        self.news_filter = NewsFilter(
            exclude_minutes_before=cfg.get("news_exclude_before", 15),
            exclude_minutes_after=cfg.get("news_exclude_after", 15)
        )
        self.logger = setup_logger()

    # ---------------------------------------------------
    # Indicator helpers
    # ---------------------------------------------------

    def bollinger_bands(self, series):
        ma = series.rolling(self.bb_period).mean()
        std = series.rolling(self.bb_period).std()

        upper = ma + (std * self.bb_std)
        lower = ma - (std * self.bb_std)

        return upper, lower

    # ---------------------------------------------------
    # Main strategy logic
    # ---------------------------------------------------

    def on_candle(self, df):
        if len(df) < self.min_candles:
            return None

        # Log current market conditions
        current_time = df.index[-1]
        price = df.iloc[-1].close
        
        self.logger.info(
            f"MARKET CHECK | Time: {current_time} | Price: {price:.3f} | "
            f"Candles: {len(df)} | Session: {self.session.allowed()}"
        )

        # Check filters first
        if not self.session.allowed():
            self.logger.info("SESSION FILTER: Market closed - no trading allowed")
            return None

        # Log news and spread filter status
        news_allowed = self.news_filter.allowed(current_time)
        spread_allowed = self.spread_filter.allowed("XAUUSDm")
        
        self.logger.info(
            f"FILTER STATUS | News: {'ALLOWED' if news_allowed else 'BLOCKED'} | "
            f"Spread: {'ALLOWED' if spread_allowed else 'BLOCKED'}"
        )

        # Calculate indicators
        close = df["close"]
        ema = close.ewm(span=self.ema_period).mean()
        upper_bb, lower_bb = self.bollinger_bands(close)
        atr_val = atr(df, self.atr_period).iloc[-1]

        if atr_val == 0 or pd.isna(atr_val):
            return None

        last = df.iloc[-1]

        price = last.close

        # Log technical indicators
        self.logger.info(
            f"TECHNICALS | EMA: {ema.iloc[-1]:.3f} | "
            f"Upper BB: {upper_bb.iloc[-1]:.3f} | "
            f"Lower BB: {lower_bb.iloc[-1]:.3f} | "
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
                f"VOLATILITY | Current ATR: {atr_val:.3f} | "
                f"5-candle Avg: {avg_atr:.3f} | "
                f"Change: {volatility_change:+.1f}%"
            )
            
            # Alert on significant volatility increase
            if volatility_change > 20:  # 20% increase in volatility
                self.logger.info(f"VOLATILITY SPIKE: {volatility_change:+.1f}% - Watch for trading opportunities")

        # Update trailing stop reference for potential trades
        if self.best_price is None:
            self.best_price = price

        # =================================================
        # BUY SCALP (liquidity grab below)
        # =================================================
        if price > ema.iloc[-1]:

            # candle wicked below band but closed back inside
            if last.low < lower_bb.iloc[-1] and last.close > lower_bb.iloc[-1]:

                # News filter check right before signal generation
                if not self.news_filter.allowed(df.index[-1]):
                    self.logger.info("NEWS FILTER: Skipping BUY trade due to nearby news event")
                    return None

                # Spread filter check
                if not self.spread_filter.allowed("XAUUSDm"):
                    self.logger.info("SPREAD FILTER: Spread too wide for buy trade")
                    return None

                # Calculate initial SL/TP
                sl = price - (atr_val * self.sl_mult)
                tp = price + (atr_val * self.tp_mult)

                # Apply trailing stop if enabled
                if self.trailing_enabled:
                    trailing_distance = atr_val * self.trailing_atr_multiplier
                    trailing_sl = price - trailing_distance  # Current price minus trail distance
                    
                    if trailing_sl > sl:  # Only move SL up, not down
                        sl = trailing_sl
                        self.logger.info(f"TRAILING STOP | Buy SL moved to {sl:.3f}")

                # Update best price for future trailing
                self.best_price = price

                self.logger.info("SCALPER BUY SIGNAL")
                
                # Track signal timing
                from datetime import datetime
                self.last_signal_time = datetime.utcnow()
                
                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_scalper",
                }

        # =================================================
        # SELL SCALP (liquidity grab above)
        # =================================================
        if price < ema.iloc[-1]:

            if last.high > upper_bb.iloc[-1] and last.close < upper_bb.iloc[-1]:

                # News filter check right before signal generation
                if not self.news_filter.allowed(df.index[-1]):
                    self.logger.info("NEWS FILTER: Skipping SELL trade due to nearby news event")
                    return None

                # Spread filter check
                if not self.spread_filter.allowed("XAUUSDm"):
                    self.logger.info("SPREAD FILTER: Spread too wide for sell trade")
                    return None

                # Calculate initial SL/TP
                sl = price + (atr_val * self.sl_mult)
                tp = price - (atr_val * self.tp_mult)

                # Apply trailing stop if enabled
                if self.trailing_enabled:
                    trailing_distance = atr_val * self.trailing_atr_multiplier
                    trailing_sl = price + trailing_distance  # Current price plus trail distance
                    
                    if trailing_sl < sl:  # Only move SL down, not up
                        sl = trailing_sl
                        self.logger.info(f"TRAILING STOP | Sell SL moved to {sl:.3f}")

                # Update best price for future trailing
                self.best_price = price

                self.logger.info("SCALPER SELL SIGNAL")
                
                # Track signal timing
                from datetime import datetime
                self.last_signal_time = datetime.utcnow()
                
                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                    "strategy": "xau_scalper",
                }

        # Log no signal conditions
        self.logger.info("NO SIGNAL | Market conditions not met for scalping")
        return None
