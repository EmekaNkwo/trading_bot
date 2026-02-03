PORTFOLIO = {
    # "max_total_risk": 0.02,
    "max_total_risk": 0.01,

    "symbols": {
        "XAUUSDm": {
            "timeframe": "M15",

            # portion of total risk allocated to this symbol
            "risk": 0.01,

            # strategy identifier
            "strategy": "xau_trend",

            # candle execution interval (seconds)
            "candle_seconds": 900
        },
        
        # "XAUUSDm": {
        #     "timeframe": "M15",
        #     "risk": 0.007,
        #     "strategy": "xau_trend"
        # },
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
