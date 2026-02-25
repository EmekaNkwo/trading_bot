from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_regime import XAURegimeStrategy
from strategy.xau_sweep import XAUSweepStrategy
# later:
# from strategy.us30_breakout import US30BreakoutStrategy
# from strategy.fx_reversion import FXReversionStrategy


STRATEGY_REGISTRY = {
    "xau_trend": XAUTrendStrategy,
    "xau_regime": XAURegimeStrategy,
    "xau_sweep": XAUSweepStrategy,
    # "us30_breakout": US30BreakoutStrategy,
    # "fx_reversion": FXReversionStrategy,
}
