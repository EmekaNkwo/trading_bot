import pandas as pd
from datetime import datetime, date


def daily_summary(csv_path="reports/live_trades.csv"):

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

    wins = today_trades[today_trades["comment"].str.contains("executed", case=False)]
    losses = len(today_trades) - len(wins)

    summary = {
        "date": str(today),
        "trades": len(today_trades),
        "wins": len(wins),
        "losses": losses
    }

    return summary
