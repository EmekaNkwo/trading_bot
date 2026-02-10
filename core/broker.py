import MetaTrader5 as mt5
import pandas as pd
from datetime import datetime
from utils.logger import setup_logger


logger = setup_logger()

TIMEFRAMES = {
    "M1": mt5.TIMEFRAME_M1,
    "M5": mt5.TIMEFRAME_M5,
    "M15": mt5.TIMEFRAME_M15,
    "H1": mt5.TIMEFRAME_H1,
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

    def get_historical_data(
        self,
        symbol: str,
        timeframe: str,
        bars: int = 5000
    ) -> pd.DataFrame:

        if not self.connected:
            self.connect()

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

        df["time"] = pd.to_datetime(df["time"], unit="s")
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
