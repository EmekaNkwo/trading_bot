from datetime import datetime
import pytz


class SpreadFilter:
    """Filter trades based on current spread"""
    
    def __init__(self, max_spread_points=30):
        self.max_spread_points = max_spread_points
    
    def allowed(self, symbol, current_spread=None):
        """
        Check if spread is acceptable for trading
        
        Args:
            symbol: Trading symbol (e.g., 'XAUUSDm')
            current_spread: Current spread in points, if None will check via broker
        
        Returns:
            bool: True if spread is acceptable
        """
        if current_spread is None:
            # Would need to get from broker - for now return True
            return True
            
        return current_spread <= self.max_spread_points


class NewsFilter:
    """Filter trades around high-impact news events"""
    
    def __init__(self, exclude_minutes_before=15, exclude_minutes_after=15):
        self.exclude_before = exclude_minutes_before
        self.exclude_after = exclude_minutes_after
        
        # High-impact news times (UTC) - these would typically come from a news API
        # For now, using common high-impact economic release times
        self.news_times = [
            # US economic data
            (13, 30),  # US CPI, GDP, etc.
            (8, 30),   # UK economic data
            (9, 0),    # Eurozone data
            (23, 50),  # AU data (Sydney open)
        ]
    
    def allowed(self, current_time=None):
        """
        Check if current time is not too close to high-impact news
        
        Args:
            current_time: datetime object, if None uses current time
        
        Returns:
            bool: True if safe to trade (no nearby news)
        """
        if current_time is None:
            current_time = datetime.now(pytz.UTC)
        elif current_time.tzinfo is None:
            current_time = current_time.replace(tzinfo=pytz.UTC)
        
        current_minutes = current_time.hour * 60 + current_time.minute
        
        for news_hour, news_minute in self.news_times:
            news_minutes = news_hour * 60 + news_minute
            
            # Check if within exclusion window
            time_diff = abs(current_minutes - news_minutes)
            if time_diff <= self.exclude_before or time_diff <= self.exclude_after:
                print(f"NEWS BLOCKED | Current: {current_time.hour:02d}:{current_time.minute:02d} | "
                      f"News: {news_hour:02d}:{news_minute:02d} | "
                      f"Diff: {time_diff} min | Window: ±{self.exclude_before}/{self.exclude_after} min")
                return False
                
        print(f"NEWS ALLOWED | Current: {current_time.hour:02d}:{current_time.minute:02d} | "
              f"No high-impact news within {self.exclude_before} min before/{self.exclude_after} min after")
        return True
