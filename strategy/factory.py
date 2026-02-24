from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_scalper import XAUScalper
from strategy.xau_regime import XAURegimeStrategy


def build_strategy(name, config):

    if name == "xau_trend":
        return XAUTrendStrategy(config)

    if name == "xau_scalper":
        return XAUScalper(config)

    if name == "xau_regime":
        return XAURegimeStrategy(config)

    raise ValueError(f"Unknown strategy: {name}")
