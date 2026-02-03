import pandas as pd
import os


def export_trades(trades, filename="trades.csv"):

    if not trades:
        print("No trades to export.")
        return

    os.makedirs("reports", exist_ok=True)

    df = pd.DataFrame([t.__dict__ for t in trades])
    df.to_csv(f"reports/{filename}", index=False)

    print(f"Trades exported → reports/{filename}")
