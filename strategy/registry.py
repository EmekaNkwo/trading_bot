from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_regime import XAURegimeStrategy
# later:
# from strategy.us30_breakout import US30BreakoutStrategy
# from strategy.fx_reversion import FXReversionStrategy


STRATEGY_REGISTRY = {
    "xau_trend": XAUTrendStrategy,
    "xau_regime": XAURegimeStrategy,
    # "us30_breakout": US30BreakoutStrategy,
    # "fx_reversion": FXReversionStrategy,
}
