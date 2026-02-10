from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_scalper import XAUScalper


def build_strategy(name, config):

    if name == "xau_trend":
        return XAUTrendStrategy(config)

    if name == "xau_scalper":
        return XAUScalper(config)

    raise ValueError(f"Unknown strategy: {name}")
