
import pandas as pd


class PerformanceGuard:

    def __init__(
        self,
        max_drawdown_pct=0.05,
        min_trades=20,
        history_file="reports/live_deals.csv"
    ):
        self.max_dd = max_drawdown_pct
        self.min_trades = min_trades
        self.history_file = history_file

    def evaluate(self):

        try:
            df = pd.read_csv(self.history_file)
        except Exception:
            return True

        if df is None or df.empty:
            return True

        # We require balance snapshots to compute drawdown % robustly.
        if "balance" not in df.columns:
            return True

        df["balance"] = pd.to_numeric(df["balance"], errors="coerce")
        df = df.dropna(subset=["balance"])

        if len(df) < self.min_trades:
            return True

        bal = df["balance"]
        peak = bal.cummax()
        peak = peak.where(peak > 0)

        dd_pct = ((peak - bal) / peak).max()
        if pd.isna(dd_pct):
            return True

        return float(dd_pct) <= float(self.max_dd)
