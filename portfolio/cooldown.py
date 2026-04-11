from datetime import datetime, timedelta


class SymbolCooldown:

    def __init__(self, max_losses=3, cooldown_minutes=1440):
        self.max_losses = max_losses
        self.cooldown = timedelta(minutes=cooldown_minutes)
        self.loss_count = {}
        self.disabled_until = {}

    def record_trade(self, symbol, pnl):

        if pnl < 0:
            self.loss_count[symbol] = self.loss_count.get(symbol, 0) + 1
        else:
            self.loss_count[symbol] = 0

        if self.loss_count[symbol] >= self.max_losses:
            self.disabled_until[symbol] = (
                datetime.utcnow() + self.cooldown
            )
            self.loss_count[symbol] = 0

    def allowed(self, symbol):

        until = self.disabled_until.get(symbol)

        if not until:
            return True

        return datetime.utcnow() > until

    def snapshot(self):
        return {
            "loss_count": {str(symbol): int(count) for symbol, count in self.loss_count.items()},
            "disabled_until": {
                str(symbol): until.isoformat()
                for symbol, until in self.disabled_until.items()
            },
        }

    def restore(self, payload):
        if not isinstance(payload, dict):
            return
        self.loss_count = {}
        for symbol, count in dict(payload.get("loss_count", {}) or {}).items():
            safe_count = self._safe_int(count)
            if safe_count is not None:
                self.loss_count[str(symbol)] = safe_count
        self.disabled_until = {}
        for symbol, until in dict(payload.get("disabled_until", {}) or {}).items():
            try:
                self.disabled_until[str(symbol)] = datetime.fromisoformat(str(until))
            except Exception:
                continue

    @staticmethod
    def _safe_int(value):
        try:
            return int(value)
        except Exception:
            return None
