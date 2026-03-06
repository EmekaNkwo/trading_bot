from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_scalper import XAUScalper
from strategy.xau_regime import XAURegimeStrategy
from strategy.xau_sweep import XAUSweepStrategy
from strategy.xau_liquidity_reclaim import XAULiquidityReclaimStrategy
from strategy.xau_opening_range_displacement import XAUOpeningRangeDisplacementStrategy


def build_strategy(name, config, symbol=None):

    if name == "xau_trend":
        strategy = XAUTrendStrategy(config)
    elif name == "xau_scalper":
        strategy = XAUScalper(config)
    elif name == "xau_regime":
        strategy = XAURegimeStrategy(config)
    elif name == "xau_sweep":
        strategy = XAUSweepStrategy(config)
    elif name == "xau_liquidity_reclaim":
        strategy = XAULiquidityReclaimStrategy(config)
    elif name == "xau_opening_range_displacement":
        strategy = XAUOpeningRangeDisplacementStrategy(config)
    else:
        raise ValueError(f"Unknown strategy: {name}")

    if symbol is not None and hasattr(strategy, "bind_symbol"):
        strategy.bind_symbol(symbol)
    return strategy
