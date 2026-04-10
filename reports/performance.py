import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

_DEFAULT_CSV = Path(__file__).resolve().parent.parent / "reports" / "live_deals.csv"


def daily_summary(csv_path=None):
    path = Path(csv_path) if csv_path is not None else _DEFAULT_CSV

    try:
        df = pd.read_csv(path)
    except Exception:
        return None

    if df.empty or "timestamp" not in df.columns:
        return None

    try:
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    except Exception:
        return None

    # Deals are logged with UTC timestamps; align "today" with UTC (main schedules report at 23:00 UTC).
    today = datetime.now(timezone.utc).date()
    today_trades = df[df["timestamp"].dt.date == today]

    if today_trades.empty:
        return None
    if "pnl" in today_trades.columns:
        today_trades = today_trades.copy()
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
        "losses": int(losses),
    }

    return summary
