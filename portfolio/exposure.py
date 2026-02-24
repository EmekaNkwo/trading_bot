import MetaTrader5 as mt5


class ExposureTracker:

    def _risk_from_comment(self, comment):
        """
        Parse compact comment format written by this bot:
          pb|<strat>|r=<risk>
        Returns risk as float (e.g. 0.0050) or None if not present.
        """
        if not comment:
            return None

        c = str(comment)
        if not c.startswith("pb|"):
            return None

        # Fast parse without regex to keep it lightweight
        # Example: pb|xt|r=0.0050
        parts = c.split("|")
        for part in parts:
            if part.startswith("r="):
                try:
                    return float(part.removeprefix("r="))
                except Exception:
                    return None
        return None

    def total_open_risk(self):
        positions = mt5.positions_get()
        if not positions:
            return 0.0

        risk = 0.0
        for p in positions:
            r = self._risk_from_comment(getattr(p, "comment", None))
            if r is not None and r > 0:
                risk += r

        return risk
