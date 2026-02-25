PORTFOLIO = {
    # "max_total_risk": 0.02,
    "max_total_risk": 0.01,

    "symbols": {
        "XAUUSDm": {
            "strategies": {
                # Adaptive regime strategy (enabled)
                "xau_regime": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.007
                },

                # Liquidity sweep dual-mode strategy (enabled, conservative)
                "xau_sweep": {
                    "timeframe": "M5",
                    "candle_seconds": 300,
                    "risk": 0.003
                },

                # Previous strategies (paused)
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
