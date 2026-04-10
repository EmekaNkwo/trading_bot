PORTFOLIO = {
    "max_total_risk": 0.035,

    "symbols": {
        "XAUUSDm": {
            "strategies": {
                # v2: liquidity reclaim around recent sweep context
                "xau_liquidity_reclaim": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.005
                },

                # v2: opening-range displacement breakout
                "xau_opening_range_displacement": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.005
                },

                # Re-enable a proven higher-frequency strategy to avoid zero-trade weeks
                "xau_sweep": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.005
                },

                # Re-enable regime strategy so all 4 run together
                "xau_regime": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.005
                },

                # Previous strategies (paused)
                # "xau_sweep": {
                #     "timeframe": "M5",
                #     "candle_seconds": 300,
                #     "risk": 0.005
                # },
                # "xau_trend": {
                #     "timeframe": "M15",
                #     "candle_seconds": 900,
                #     "risk": 0.004
                # },
                # "xau_scalper": {
                #     "timeframe": "M5",
                #     "candle_seconds": 300,
                #     "risk": 0.004
                # },
            }
        },
        "US30m": {
            "strategies": {
                "us30_open_wick": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.001,
                },
                "us30_asia_eq": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.001,
                },
                "us30_trend_pullback": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.001,
                },
                "us30_supply_demand": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.001,
                },
                "us30_fib_retrace": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.001,
                },
            }
        },
        "GER30m": {
            "strategies": {
                "ger30_three_pin_breakout": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.003,
                },
            }
        },
        "BTCUSDm": {
            "strategies": {
                "btc_bos_retest": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.0015,
                },
                "multi_asset_regime": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.0015,
                },
            }
        },
    }
}
