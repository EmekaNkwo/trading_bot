
import pandas as pd


class PerformanceGuard:

    def __init__(
        self,
        max_drawdown_pct=0.05,
        min_trades=20,
        history_file="reports/live_trades.csv"
    ):
        self.max_dd = max_drawdown_pct
        self.min_trades = min_trades
        self.history_file = history_file

    def evaluate(self):

        try:
            df = pd.read_csv(self.history_file)
        except Exception:
            return True

        if len(df) < self.min_trades:
            return True

        pnl = df["pnl"].dropna()

        equity = pnl.cumsum()

        dd = (equity.cummax() - equity).max()

        return dd <= self.max_dd
