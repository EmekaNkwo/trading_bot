from backtest.simulator import BacktestEngine
from backtest.metrics import backtest_metrics


def evaluate_strategy(df, strategy_factory, params):

    strategy = strategy_factory(**params)

    engine = BacktestEngine()

    engine.run(df, strategy)

    metrics = backtest_metrics(engine.trades, engine.equity_curve)

    metrics["params"] = params

    return metrics
