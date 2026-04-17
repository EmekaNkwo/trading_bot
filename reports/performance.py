import pandas as pd
from datetime import datetime, timezone
from pathlib import Path

from core.execution import _parse_strategy_from_comment as _parse_comment

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

    today = datetime.now(timezone.utc).date()
    today_trades = df[df["timestamp"].dt.date == today].copy()

    if today_trades.empty:
        return None

    today_trades["pnl"] = pd.to_numeric(today_trades.get("pnl", 0), errors="coerce").fillna(0.0)

    total_trades = len(today_trades)
    wins = int((today_trades["pnl"] > 0).sum())
    losses = int((today_trades["pnl"] < 0).sum())
    breakeven = total_trades - wins - losses
    net_pnl = round(float(today_trades["pnl"].sum()), 2)
    gross_profit = round(float(today_trades.loc[today_trades["pnl"] > 0, "pnl"].sum()), 2)
    gross_loss = round(float(today_trades.loc[today_trades["pnl"] < 0, "pnl"].sum()), 2)
    win_rate = round((wins / total_trades) * 100, 1) if total_trades else 0.0

    pf = round(gross_profit / abs(gross_loss), 2) if gross_loss != 0 else float("inf")

    balance = None
    if "balance" in today_trades.columns:
        bal_series = pd.to_numeric(today_trades["balance"], errors="coerce").dropna()
        if not bal_series.empty:
            balance = round(float(bal_series.iloc[-1]), 2)

    by_symbol = {}
    if "symbol" in today_trades.columns:
        for sym, grp in today_trades.groupby("symbol", dropna=False):
            sym_pnl = round(float(grp["pnl"].sum()), 2)
            sym_trades = len(grp)
            sym_wins = int((grp["pnl"] > 0).sum())
            by_symbol[str(sym)] = {
                "trades": sym_trades,
                "wins": sym_wins,
                "losses": sym_trades - sym_wins - int((grp["pnl"] == 0).sum()),
                "net_pnl": sym_pnl,
            }

    by_strategy = {}
    if "comment" in today_trades.columns:
        for _, row in today_trades.iterrows():
            strat = _parse_comment(str(row.get("comment", "")))
            if strat not in by_strategy:
                by_strategy[strat] = {"trades": 0, "wins": 0, "losses": 0, "net_pnl": 0.0}
            by_strategy[strat]["trades"] += 1
            rpnl = float(row["pnl"])
            by_strategy[strat]["net_pnl"] = round(by_strategy[strat]["net_pnl"] + rpnl, 2)
            if rpnl > 0:
                by_strategy[strat]["wins"] += 1
            elif rpnl < 0:
                by_strategy[strat]["losses"] += 1

    return {
        "date": str(today),
        "trades": total_trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": pf,
        "balance": balance,
        "by_symbol": by_symbol,
        "by_strategy": by_strategy,
    }


def format_daily_report(summary: dict) -> str:
    if not summary:
        return "DAILY REPORT | No trades today."

    lines = [
        f"DAILY PERFORMANCE — {summary['date']}",
        f"{'─' * 36}",
        f"Trades: {summary['trades']}  |  W: {summary['wins']}  L: {summary['losses']}  BE: {summary.get('breakeven', 0)}",
        f"Win Rate: {summary['win_rate']}%",
        f"Net PnL: ${summary['net_pnl']:+.2f}",
        f"Gross P/L: +${summary['gross_profit']:.2f} / ${summary['gross_loss']:.2f}",
        f"Profit Factor: {summary['profit_factor']}",
    ]

    if summary.get("balance") is not None:
        lines.append(f"Balance: ${summary['balance']:.2f}")

    by_symbol = summary.get("by_symbol", {})
    if by_symbol:
        lines.append(f"{'─' * 36}")
        lines.append("By Symbol:")
        for sym, data in sorted(by_symbol.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
            lines.append(
                f"  {sym}: {data['trades']}t  W:{data['wins']} L:{data['losses']}  ${data['net_pnl']:+.2f}"
            )

    by_strategy = summary.get("by_strategy", {})
    if by_strategy:
        lines.append(f"{'─' * 36}")
        lines.append("By Strategy:")
        for strat, data in sorted(by_strategy.items(), key=lambda x: x[1]["net_pnl"], reverse=True):
            lines.append(
                f"  {strat}: {data['trades']}t  W:{data['wins']} L:{data['losses']}  ${data['net_pnl']:+.2f}"
            )

    return "\n".join(lines)


