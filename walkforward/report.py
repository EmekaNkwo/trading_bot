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
