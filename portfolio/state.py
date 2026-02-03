class PortfolioState:

    def __init__(self):
        self.last_trade_result = {}

    def record(self, symbol, pnl):
        self.last_trade_result[symbol] = pnl
