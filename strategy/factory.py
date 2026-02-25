from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_scalper import XAUScalper
from strategy.xau_regime import XAURegimeStrategy
from strategy.xau_sweep import XAUSweepStrategy


def build_strategy(name, config):

    if name == "xau_trend":
        return XAUTrendStrategy(config)

    if name == "xau_scalper":
        return XAUScalper(config)

    if name == "xau_regime":
        return XAURegimeStrategy(config)

    if name == "xau_sweep":
        return XAUSweepStrategy(config)

    raise ValueError(f"Unknown strategy: {name}")
