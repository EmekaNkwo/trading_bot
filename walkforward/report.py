def summarize_walkforward(df):

    if df.empty:
        return {
            "windows": 0,
            "avg_profit_factor": 0.0,
            "avg_drawdown": 0.0,
            "profitable_windows": 0,
            "loss_windows": 0,
            "consistency_%": 0.0,
        }

    windows = len(df)
    profitable = int((df["profit_factor"] > 1.0).sum())
    loss = int((df["profit_factor"] <= 1.0).sum())

    avg_pf = float(df["profit_factor"].mean())
    avg_dd = float(df["max_drawdown"].mean())
    consistency = (profitable / windows) * 100 if windows else 0

    return {
        "windows": int(windows),
        "avg_profit_factor": round(avg_pf, 2),
        "avg_drawdown": round(avg_dd, 2),
        "profitable_windows": profitable,
        "loss_windows": loss,
        "consistency_%": round(float(consistency), 2),
    }


def summarize_walkforward_by_strategy(df):
    if df.empty or "strategy" not in df.columns:
        return []

    summaries = []
    grouped = df.groupby("strategy", dropna=False)
    for strategy, group in grouped:
        summary = summarize_walkforward(group)
        summary.update(
            {
                "strategy": str(strategy),
                "total_trades": int(group["trades"].sum()) if "trades" in group.columns else 0,
                "avg_win_rate": round(float(group["win_rate"].mean()), 2) if "win_rate" in group.columns else 0.0,
                "net_profit_sum": round(float(group["net_profit"].sum()), 2) if "net_profit" in group.columns else 0.0,
            }
        )
        summaries.append(summary)

    summaries.sort(
        key=lambda item: (
            float(item.get("avg_profit_factor", 0.0)),
            float(item.get("consistency_%", 0.0)),
            float(item.get("net_profit_sum", 0.0)),
        ),
        reverse=True,
    )
    return summaries
