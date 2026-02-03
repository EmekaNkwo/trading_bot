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
