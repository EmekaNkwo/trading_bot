PORTFOLIO = {
    "max_total_risk": 0.035,

    "symbols": {
        "XAUUSDm": {
            "strategies": {
                "xau_liquidity_reclaim": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.005
                },
                "xau_opening_range_displacement": {
                    "timeframe": "H1",
                    "candle_seconds": 3600,
                    "risk": 0.005
                },
                "xau_sweep": {
                    "timeframe": "M30",
                    "candle_seconds": 1800,
                    "risk": 0.005
                },
                "xau_regime": {
                    "timeframe": "H1",
                    "candle_seconds": 3600,
                    "risk": 0.005
                },
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
                "us30_supply_demand": {
                    "timeframe": "M30",
                    "candle_seconds": 1800,
                    "risk": 0.001,
                },
                "us30_fib_retrace": {
                    "timeframe": "H1",
                    "candle_seconds": 3600,
                    "risk": 0.001,
                },
            }
        },
        "DE30m": {
            "strategies": {
                "ger30_three_pin_breakout": {
                    "timeframe": "M30",
                    "candle_seconds": 1800,
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
