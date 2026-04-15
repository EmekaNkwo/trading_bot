import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from utils.logger import setup_logger


logger = setup_logger()

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "M30": mt5.TIMEFRAME_M30,
    "H1": mt5.TIMEFRAME_H1,
    "H4": mt5.TIMEFRAME_H4,
}


class MT5Broker:
    """
    Handles:
    - MT5 connection
    - Historical candle download
    """

    def __init__(self):
        self.connected = False

    def connect(self):
        if not mt5.initialize():
            raise RuntimeError("MT5 initialization failed")
        self.connected = True
        logger.debug("MT5 connected")
        
        # Log account details
        self._log_account_info()

    def _log_account_info(self):
        """Log detailed account information"""
        account = mt5.account_info()
        if not account:
            logger.error("Failed to get account info")
            return
            
        logger.info(
            f"ACCOUNT INFO | "
            f"Login: {account.login} | "
            f"Server: {account.server} | "
            f"Balance: ${account.balance:.2f} | "
            f"Equity: ${account.equity:.2f} | "
            f"Margin: ${account.margin:.2f} | "
            f"Free Margin: ${account.margin_free:.2f} | "
            f"Leverage: 1:{account.leverage}"
        )
        
        # Log account currency and type
        logger.info(
            f"ACCOUNT DETAILS | "
            f"Currency: {account.currency} | "
            f"Trade Mode: {self._get_trade_mode_name(account.trade_mode)} | "
            f"Stop Out Mode: {self._get_stopout_mode_name(getattr(account, 'stopout_mode', 0))}"
        )
    
    def _get_trade_mode_name(self, mode):
        """Convert trade mode number to readable name"""
        modes = {
            0: "Demo",
            1: "Contest", 
            2: "Real"
        }
        return modes.get(mode, f"Unknown({mode})")
    
    def _get_stopout_mode_name(self, mode):
        """Convert stopout mode number to readable name"""
        modes = {
            0: "Balance",
            1: "Equity"
        }
        return modes.get(mode, f"Unknown({mode})")

    def shutdown(self):
        mt5.shutdown()
        self.connected = False
        logger.debug("MT5 shutdown")

    def ensure_symbol(self, symbol: str) -> bool:
        if not self.connected:
            self.connect()
        try:
            info = mt5.symbol_info(symbol)
        except Exception:
            info = None
        if info is None:
            return False
        if bool(getattr(info, "visible", False)):
            return True
        try:
            return bool(mt5.symbol_select(symbol, True))
        except Exception:
            return False

    def get_symbol_snapshot(self, symbol: str) -> dict:
        if not self.ensure_symbol(symbol):
            return {"ok": False, "symbol": symbol, "reason": "symbol_unavailable"}

        info = mt5.symbol_info(symbol)
        tick = mt5.symbol_info_tick(symbol)
        if info is None:
            return {"ok": False, "symbol": symbol, "reason": "missing_symbol_info"}
        if tick is None:
            return {"ok": False, "symbol": symbol, "reason": "missing_tick"}

        bid = float(getattr(tick, "bid", 0.0) or 0.0)
        ask = float(getattr(tick, "ask", 0.0) or 0.0)
        point = float(getattr(info, "point", 0.0) or 0.0)
        trade_mode = int(getattr(info, "trade_mode", 0) or 0)
        tick_ok = bool(bid > 0 and ask > 0 and ask >= bid)
        tradable = trade_mode != 0
        return {
            "ok": bool(tick_ok and tradable),
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "spread": max(0.0, ask - bid),
            "point": point,
            "visible": bool(getattr(info, "visible", False)),
            "trade_mode": trade_mode,
            "volume_min": float(getattr(info, "volume_min", 0.0) or 0.0),
            "volume_step": float(getattr(info, "volume_step", 0.0) or 0.0),
            "reason": None if tick_ok and tradable else ("trade_disabled" if tick_ok else "invalid_tick_prices"),
        }

    def get_historical_data(
        self,
        symbol: str,
        timeframe: str,
        bars: int = 5000
    ) -> pd.DataFrame:

        if not self.connected:
            self.connect()
        if not self.ensure_symbol(symbol):
            raise RuntimeError(f"Symbol not available in MT5: {symbol}")

        if timeframe not in TIMEFRAMES:
            raise ValueError(f"Unsupported timeframe: {timeframe}")

        rates = mt5.copy_rates_from_pos(
            symbol,
            TIMEFRAMES[timeframe],
            0,
            bars
        )

        if rates is None:
            raise RuntimeError("No data returned from MT5")

        df = pd.DataFrame(rates)

        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)

        df = df[["open", "high", "low", "close", "tick_volume"]]

        return df

import os


def save_to_csv(df, symbol, timeframe):
    os.makedirs("data/historical", exist_ok=True)

    filename = f"data/historical/{symbol}_{timeframe}.csv"
    df.to_csv(filename)

    print(f"Saved: {filename}")


def load_from_csv(symbol, timeframe):
    filename = f"data/historical/{symbol}_{timeframe}.csv"

    if not os.path.exists(filename):
        raise FileNotFoundError(filename)

    df = pd.read_csv(filename, parse_dates=["time"])
    df.set_index("time", inplace=True)
    
    df.index = pd.to_datetime(df.index, utc=True)

    return df
