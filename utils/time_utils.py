from datetime import datetime, time, timezone


class SessionFilter:

    def __init__(
        self,
        london=(8, 17),
        newyork=(13, 22),
        tz_name="UTC"
    ):
        self.london = london
        self.newyork = newyork
        self.tz = timezone.utc if tz_name == "UTC" else timezone

    def now_utc(self):
        return datetime.utcnow().time()

    def in_london(self):
        t = self.now_utc()
        return time(self.london[0]) <= t <= time(self.london[1])

    def in_newyork(self):
        t = self.now_utc()
        return time(self.newyork[0]) <= t <= time(self.newyork[1])

    def _in_session(self, t):
        """Check if time t is within any allowed trading session"""
        london_start = time(self.london[0])
        london_end = time(self.london[1])
        ny_start = time(self.newyork[0])
        ny_end = time(self.newyork[1])
        
        return (london_start <= t <= london_end) or (ny_start <= t <= ny_end)
 
    def allowed(self, candle_time=None):
        if candle_time is None:
            # Live trading - use current UTC time
            return self.in_london() or self.in_newyork()
 
        # Backtesting - use candle timestamp
        if candle_time.tzinfo is None:
            # assume UTC if naive
            t = candle_time.time()
        else:
            t = candle_time.astimezone(self.tz).time()
 
        return self._in_session(t)