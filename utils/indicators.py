import pandas as pd


def atr(df: pd.DataFrame, period: int = 14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    atr = tr.rolling(period).mean()

    return atr


def adx(df: pd.DataFrame, period: int = 14):
    """
    Average Directional Index - measures trend strength (0-100)
    Values: <20 = weak, 20-40 = moderate, >40 = strong trend
    """
    high = df["high"]
    low = df["low"]
    close = df["close"]

    # True Range
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    # Directional Movement
    plus_dm = high.diff()
    minus_dm = -low.diff()

    plus_dm = plus_dm.where((plus_dm > minus_dm) & (plus_dm > 0), 0)
    minus_dm = minus_dm.where((minus_dm > plus_dm) & (minus_dm > 0), 0)

    # Smoothed averages
    atr = tr.rolling(period).mean()
    plus_di = 100 * plus_dm.rolling(period).mean() / atr
    minus_di = 100 * minus_dm.rolling(period).mean() / atr

    # ADX
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di)
    adx = dx.rolling(period).mean()

    return adx, plus_di, minus_di


def rsi(series: pd.Series, period: int = 14):
    """
    Relative Strength Index - momentum oscillator (0-100)
    <30 = oversold, >70 = overbought
    """
    delta = series.diff()

    gain = delta.where(delta > 0, 0).rolling(period).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(period).mean()

    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))

    return rsi


def ema(series: pd.Series, span: int):
    """Exponential Moving Average"""
    return series.ewm(span=span, adjust=False).mean()


def sma(series: pd.Series, period: int):
    """Simple Moving Average"""
    return series.rolling(period).mean()
