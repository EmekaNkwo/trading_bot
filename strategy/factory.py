from strategy.xau_trend import XAUTrendStrategy
from strategy.xau_scalper import XAUScalper
from strategy.xau_regime import XAURegimeStrategy
from strategy.xau_sweep import XAUSweepStrategy
from strategy.xau_liquidity_reclaim import XAULiquidityReclaimStrategy
from strategy.xau_opening_range_displacement import XAUOpeningRangeDisplacementStrategy
from strategy.multi_asset_regime import MultiAssetRegimeStrategy
from strategy.ger30_transcription import GER30TranscriptionStrategy
from strategy.btc_transcription import BTCTranscriptionStrategy
from strategy.us30_transcription import US30TranscriptionStrategy


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
    elif name == "multi_asset_regime":
        strategy = MultiAssetRegimeStrategy(config)
    elif name in {
        "ger30_three_pin_breakout",
    }:
        strategy = GER30TranscriptionStrategy(config, mode=name)
    elif name in {
        "btc_bos_retest",
    }:
        strategy = BTCTranscriptionStrategy(config, mode=name)
    elif name in {
        "us30_supply_demand",
        "us30_open_wick",
        "us30_trend_pullback",
        "us30_asia_eq",
        "us30_fib_retrace",
    }:
        strategy = US30TranscriptionStrategy(config, mode=name)
    else:
        raise ValueError(f"Unknown strategy: {name}")

    if symbol is not None and hasattr(strategy, "bind_symbol"):
        strategy.bind_symbol(symbol)
    return strategy
