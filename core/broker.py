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
