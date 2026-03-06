from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_regime import XAURegimeStrategy
from strategy.xau_sweep import XAUSweepStrategy
from strategy.xau_liquidity_reclaim import XAULiquidityReclaimStrategy
from strategy.xau_opening_range_displacement import XAUOpeningRangeDisplacementStrategy
# later:
# from strategy.us30_breakout import US30BreakoutStrategy
# from strategy.fx_reversion import FXReversionStrategy


STRATEGY_REGISTRY = {
    "xau_trend": XAUTrendStrategy,
    "xau_regime": XAURegimeStrategy,
    "xau_sweep": XAUSweepStrategy,
    "xau_liquidity_reclaim": XAULiquidityReclaimStrategy,
    "xau_opening_range_displacement": XAUOpeningRangeDisplacementStrategy,
    # "us30_breakout": US30BreakoutStrategy,
    # "fx_reversion": FXReversionStrategy,
}
