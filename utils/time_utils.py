from datetime import datetime, time, timezone as dt_timezone


class SessionFilter:

    def __init__(
        self,
        london=(6, 18),
        newyork=(12, 23),
        tz_name="UTC"
    ):
        self.london = london
        self.newyork = newyork
        self.tz = dt_timezone.utc   # always operate in UTC

    # -------------------------------------------------
    # ALWAYS RETURN TIMEZONE-AWARE UTC TIME
    # -------------------------------------------------
    def now_utc(self):
        return datetime.now(self.tz)

    def in_london(self):
        t = self.now_utc().time()
        return time(self.london[0]) <= t <= time(self.london[1])

    def in_newyork(self):
        t = self.now_utc().time()
        return time(self.newyork[0]) <= t <= time(self.newyork[1])

    # -------------------------------------------------
    # BACKTEST SESSION CHECK
    # -------------------------------------------------
    def _in_session(self, t):
        london_start = time(self.london[0])
        london_end = time(self.london[1])
        ny_start = time(self.newyork[0])
        ny_end = time(self.newyork[1])

        return (london_start <= t <= london_end) or (ny_start <= t <= ny_end)

    # -------------------------------------------------
    # MASTER ENTRY POINT
    # -------------------------------------------------
    def allowed(self, candle_time=None):

        # LIVE TRADING
        if candle_time is None:
            return self.in_london() or self.in_newyork()

        # BACKTEST / WALKFORWARD
        if candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=self.tz)

        t = candle_time.astimezone(self.tz).time()
        return self._in_session(t)
