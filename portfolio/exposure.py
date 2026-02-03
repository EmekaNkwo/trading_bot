import MetaTrader5 as mt5


class ExposureTracker:

    def total_open_risk(self):
        positions = mt5.positions_get()
        if not positions:
            return 0.0

        risk = 0
        for p in positions:
            risk += abs(p.volume)

        return risk
