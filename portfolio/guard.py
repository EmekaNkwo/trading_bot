class SymbolDrawdownGuard:

    def __init__(self, max_drawdown_pct=0.02):
        self.max_dd = max_drawdown_pct
        self.symbol_equity = {}
        self.symbol_peak = {}
        self._last_account_balance = None

    def update(self, symbol, pnl, account_balance=None):
        if account_balance is not None:
            try:
                self._last_account_balance = float(account_balance)
            except Exception:
                pass

        eq = self.symbol_equity.get(symbol, 0) + pnl
        peak = self.symbol_peak.get(symbol, eq)

        self.symbol_equity[symbol] = eq
        self.symbol_peak[symbol] = max(peak, eq)

    def allowed(self, symbol):

        eq = self.symbol_equity.get(symbol, 0)
        peak = self.symbol_peak.get(symbol, eq)

        drawdown = peak - eq
        if self._last_account_balance is None or self._last_account_balance <= 0:
            return True

        # Interpret max_dd as % of account balance (e.g. 0.02 = 2%).
        threshold = self._last_account_balance * self.max_dd
        return drawdown <= threshold
