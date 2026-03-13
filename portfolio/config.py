PORTFOLIO = {
    "max_total_risk": 0.02,

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
        
        # "US30m": {
        #     "timeframe": "M5",
        #     "risk": 0.007,
        #     "strategy": "xau_trend"
        # },
        # "GER30m": {
        #     "timeframe": "M15",
        #     "risk": 0.006,
        #     "strategy": "xau_trend"
        # }
    }
}
