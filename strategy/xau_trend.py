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
        self.rr = atr_cfg["rr_ratio"]

        # --------------------------
        # Session filter
        # --------------------------
        self.session = SessionFilter()

        # --------------------------
        # Logging
        # --------------------------
        self.logger = setup_logger()

    # --------------------------------------------------
    # MAIN STRATEGY LOGIC
    # --------------------------------------------------

    def on_candle(self, df):

        # ---- Session filter ----
        if not self.session.allowed(df.index[-1]):
            return None

        # ---- Data safety ----
        if len(df) < self.ema_slow + 5:
            return None

        close = df["close"]

        ema_fast = close.ewm(span=self.ema_fast).mean()
        ema_slow = close.ewm(span=self.ema_slow).mean()

        atr_val = atr(df, self.atr_period).iloc[-1]

        if atr_val is None or atr_val <= 0:
            return None

        last = df.iloc[-1]
        prev = df.iloc[-2]

        # ==================================================
        # BUY SETUP
        # ==================================================
        if ema_fast.iloc[-1] > ema_slow.iloc[-1]:

            if prev.close < ema_fast.iloc[-1] and last.close > ema_fast.iloc[-1]:

                sl = last.close - (atr_val * self.sl_mult)
                tp = last.close + (atr_val * self.sl_mult * self.rr)

                return {
                    "side": "buy",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                }

        # ==================================================
        # SELL SETUP
        # ==================================================
        if ema_fast.iloc[-1] < ema_slow.iloc[-1]:

            if prev.close > ema_fast.iloc[-1] and last.close < ema_fast.iloc[-1]:

                sl = last.close + (atr_val * self.sl_mult)
                tp = last.close - (atr_val * self.sl_mult * self.rr)

                return {
                    "side": "sell",
                    "sl": round(sl, 3),
                    "tp": round(tp, 3),
                }

        return None
