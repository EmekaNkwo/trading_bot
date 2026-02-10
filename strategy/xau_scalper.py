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

        # Session filter (very important for scalping)
        if not self.session.allowed(df.index[-1]):
            return None

        # News filter - avoid high-impact news events
        if not self.news_filter.allowed(df.index[-1]):
            self.logger.info("NEWS FILTER: Skipping trade due to nearby news event")
            return None

        if len(df) < self.min_candles:
            return None

        close = df["close"]

        # Indicators
        ema = close.ewm(span=self.ema_period).mean()
        upper_bb, lower_bb = self.bollinger_bands(close)
        atr_val = atr(df, self.atr_period).iloc[-1]

        if atr_val == 0 or pd.isna(atr_val):
            return None

        last = df.iloc[-1]

        price = last.close

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

                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
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

                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                }

        return None
