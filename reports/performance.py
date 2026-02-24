import pandas as pd
from datetime import datetime, date


def daily_summary(csv_path="reports/live_deals.csv"):

    try:
        df = pd.read_csv(csv_path)
    except Exception:
        return None

    if df.empty:
        return None

    df["timestamp"] = pd.to_datetime(df["timestamp"])
    today = date.today()

    today_trades = df[df["timestamp"].dt.date == today]

    if today_trades.empty:
        return None
    if "pnl" in today_trades.columns:
        today_trades["pnl"] = pd.to_numeric(today_trades["pnl"], errors="coerce").fillna(0.0)
        wins = (today_trades["pnl"] > 0).sum()
        losses = (today_trades["pnl"] < 0).sum()
    else:
        wins = 0
        losses = 0

    summary = {
        "date": str(today),
        "trades": len(today_trades),
        "wins": int(wins),
        "losses": losses
    }

    return summary
