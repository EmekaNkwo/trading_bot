import pandas as pd
from backtest.simulator import BacktestEngine
from backtest.metrics import backtest_metrics


class WalkForwardEngine:

    def __init__(
        self,
        train_bars=2000,
        test_bars=500,
        step_bars=500,
        starting_balance=10000
    ):
        self.train_bars = train_bars
        self.test_bars = test_bars
        self.step_bars = step_bars
        self.starting_balance = starting_balance

        self.results = []

    def run(self, df, strategy_factory):
        """
        strategy_factory() must return a NEW strategy instance.
        """

        start = 0

        while True:

            train_end = start + self.train_bars
            test_end = train_end + self.test_bars

            if test_end > len(df):
                break

            train_df = df.iloc[start:train_end]
            test_df = df.iloc[train_end:test_end]

            strategy = strategy_factory()

            engine = BacktestEngine(
                starting_balance=self.starting_balance
            )

            # Warm up indicators on training slice, but only allow entries in the test slice.
            combined = df.iloc[start:test_end]
            engine.run(
                combined,
                strategy,
                trade_start_idx=len(train_df),
                history_window=None,
            )

            metrics = backtest_metrics(
                engine.trades,
                engine.equity_curve
            )

            metrics["train_start"] = train_df.index[0]
            metrics["train_end"] = train_df.index[-1]
            metrics["test_start"] = test_df.index[0]
            metrics["test_end"] = test_df.index[-1]

            self.results.append(metrics)

            start += self.step_bars

        return pd.DataFrame(self.results)
