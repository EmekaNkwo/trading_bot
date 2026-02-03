import pandas as pd
from datetime import datetime, timedelta


def backtest_metrics(
    trades,
    equity_curve,
    start_date=None,
    end_date=None,
    period_days=None
):
    """
    Dynamic backtest metrics with time awareness.

    - start_date / end_date: datetime filters
    - period_days: last N days (overrides start_date)
    """

    if not trades or len(equity_curve) < 2:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "net_profit": 0.0,
        }

    # ------------------------------------
    # Trades dataframe
    # ------------------------------------
    df = pd.DataFrame([t.__dict__ for t in trades])

    # ensure datetime
    # Ensure close_time exists (use index if necessary)
    if "close_time" not in df.columns:
        df["close_time"] = pd.to_datetime(df.index)
    else:
        df["close_time"] = pd.to_datetime(df["close_time"])

    # ------------------------------------
    # Period filtering
    # ------------------------------------
    if period_days and not df.empty:

        # ensure datetime
        df["close_time"] = pd.to_datetime(df["close_time"], utc=True)

        latest_trade = df["close_time"].max()
        cutoff = latest_trade - timedelta(days=period_days)

        df = df[df["close_time"] >= cutoff]

    if start_date:
        df = df[df["close_time"] >= pd.to_datetime(start_date)]

    if end_date:
        df = df[df["close_time"] <= pd.to_datetime(end_date)]

    if df.empty:
        return {
            "trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "max_drawdown": 0.0,
            "net_profit": 0.0,
        }

    # ------------------------------------
    # Core metrics
    # ------------------------------------
    wins = df[df.pnl > 0]
    losses = df[df.pnl < 0]

    win_rate = (len(wins) / len(df)) * 100 if len(df) else 0
    profit_factor = (
        wins.pnl.sum() / abs(losses.pnl.sum())
        if not losses.empty
        else float("inf")
    )

    # ------------------------------------
    # Equity & drawdown
    # ------------------------------------
    equity = pd.Series(equity_curve)

    running_max = equity.cummax()
    drawdown = running_max - equity

    return {
        "trades": int(len(df)),
        "win_rate": round(win_rate, 2),
        "profit_factor": round(float(profit_factor), 2),
        "max_drawdown": round(float(drawdown.max()), 2),
        "net_profit": round(float(equity.iloc[-1] - equity.iloc[0]), 2),
    }
