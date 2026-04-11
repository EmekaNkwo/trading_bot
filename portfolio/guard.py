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

    def snapshot(self):
        return {
            "symbol_equity": {str(symbol): float(value) for symbol, value in self.symbol_equity.items()},
            "symbol_peak": {str(symbol): float(value) for symbol, value in self.symbol_peak.items()},
            "last_account_balance": (
                float(self._last_account_balance) if self._last_account_balance is not None else None
            ),
        }

    def restore(self, payload):
        if not isinstance(payload, dict):
            return
        self.symbol_equity = {}
        for symbol, value in dict(payload.get("symbol_equity", {}) or {}).items():
            try:
                self.symbol_equity[str(symbol)] = float(value)
            except Exception:
                continue
        self.symbol_peak = {}
        for symbol, value in dict(payload.get("symbol_peak", {}) or {}).items():
            try:
                self.symbol_peak[str(symbol)] = float(value)
            except Exception:
                continue
        balance = payload.get("last_account_balance")
        try:
            self._last_account_balance = float(balance) if balance is not None else None
        except Exception:
            self._last_account_balance = None
