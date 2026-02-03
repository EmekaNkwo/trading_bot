class SymbolDrawdownGuard:

    def __init__(self, max_drawdown_pct=0.02):
        self.max_dd = max_drawdown_pct
        self.symbol_equity = {}
        self.symbol_peak = {}

    def update(self, symbol, pnl):

        eq = self.symbol_equity.get(symbol, 0) + pnl
        peak = self.symbol_peak.get(symbol, eq)

        self.symbol_equity[symbol] = eq
        self.symbol_peak[symbol] = max(peak, eq)

    def allowed(self, symbol):

        eq = self.symbol_equity.get(symbol, 0)
        peak = self.symbol_peak.get(symbol, eq)

        drawdown = peak - eq

        return drawdown <= self.max_dd
