from functools import partial

from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_regime import XAURegimeStrategy
from strategy.xau_sweep import XAUSweepStrategy
from strategy.xau_liquidity_reclaim import XAULiquidityReclaimStrategy
from strategy.xau_opening_range_displacement import XAUOpeningRangeDisplacementStrategy
from strategy.multi_asset_regime import MultiAssetRegimeStrategy
from strategy.ger30_transcription import GER30TranscriptionStrategy
from strategy.btc_transcription import BTCTranscriptionStrategy
from strategy.us30_transcription import US30TranscriptionStrategy
# later:
# from strategy.us30_breakout import US30BreakoutStrategy
# from strategy.fx_reversion import FXReversionStrategy


STRATEGY_REGISTRY = {
    "xau_trend": XAUTrendStrategy,
    "xau_regime": XAURegimeStrategy,
    "xau_sweep": XAUSweepStrategy,
    "xau_liquidity_reclaim": XAULiquidityReclaimStrategy,
    "xau_opening_range_displacement": XAUOpeningRangeDisplacementStrategy,
    "multi_asset_regime": MultiAssetRegimeStrategy,
    "ger30_three_pin_breakout": partial(GER30TranscriptionStrategy, mode="ger30_three_pin_breakout"),
    "btc_bos_retest": partial(BTCTranscriptionStrategy, mode="btc_bos_retest"),
    "us30_supply_demand": partial(US30TranscriptionStrategy, mode="us30_supply_demand"),
    "us30_open_wick": partial(US30TranscriptionStrategy, mode="us30_open_wick"),
    "us30_trend_pullback": partial(US30TranscriptionStrategy, mode="us30_trend_pullback"),
    "us30_asia_eq": partial(US30TranscriptionStrategy, mode="us30_asia_eq"),
    "us30_fib_retrace": partial(US30TranscriptionStrategy, mode="us30_fib_retrace"),
    # "us30_breakout": US30BreakoutStrategy,
    # "fx_reversion": FXReversionStrategy,
}
