from backtest.simulator import BacktestEngine
from backtest.metrics import backtest_metrics
import pandas as pd


def evaluate_strategy(df, strategy_factory, params):

    strategy = strategy_factory(**params)

    engine = BacktestEngine()

    engine.run(df, strategy)

    metrics = backtest_metrics(engine.trades, engine.equity_curve)

    metrics["params"] = params

    return metrics

def select_best(results, min_trades=20):
    """
    Select best parameter set based on performance.
    """

    df = pd.DataFrame(results)

    if df.empty:
        return None

    if "trades" in df.columns:
        df = df[df["trades"] >= min_trades]

    df = df.sort_values(
        by=["profit_factor", "net_profit"],
        ascending=False
    )

    return df.iloc[0]
