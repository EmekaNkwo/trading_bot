import MetaTrader5 as mt5


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
            try:
                tick = mt5.symbol_info_tick(symbol)
                info = mt5.symbol_info(symbol)
                if not tick or not info:
                    return True

                point = float(getattr(info, "point", 0.0) or 0.0)
                if point <= 0:
                    return True

                current_spread = float(tick.ask) - float(tick.bid)
                current_spread = current_spread / point  # points
            except Exception:
                return True
            
        return current_spread <= self.max_spread_points
