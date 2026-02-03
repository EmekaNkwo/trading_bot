from dataclasses import dataclass


@dataclass
class Trade:
    side: str
    entry_price: float
    stop_loss: float
    take_profit: float
    entry_time: str
    exit_time: str = None
    pnl: float = 0.0


class BacktestEngine:

    def __init__(self, starting_balance=10000, risk_per_trade=0.01):
        self.balance = starting_balance
        self.start_balance = starting_balance
        self.risk_per_trade = risk_per_trade

        self.trades = []
        self.equity_curve = []

        self.open_trade = None

    def open_position(self, signal, candle):

        risk_amount = self.balance * self.risk_per_trade

        reward_ratio = 2.5
        reward_amount = risk_amount * reward_ratio

        self.open_trade = Trade(
            side=signal["side"],
            entry_price=candle.close,
            stop_loss=signal["sl"],
            take_profit=signal["tp"],
            entry_time=candle.name,
        )

        self.open_trade.risk = risk_amount
        self.open_trade.reward = reward_amount

    def close_trade(self, candle, pnl, reason):

        self.open_trade.exit_time = candle.name
        self.open_trade.pnl = pnl
        self.open_trade.reason = reason

        self.balance += pnl
        self.trades.append(self.open_trade)
        self.open_trade = None

    def on_candle(self, candle):

        if not self.open_trade:
            return

        t = self.open_trade

        if t.side == "buy":
            if candle.low <= t.stop_loss:
                self.close_trade(candle, -t.risk, "SL")
            elif candle.high >= t.take_profit:
                self.close_trade(candle, t.reward, "TP")

        elif t.side == "sell":
            if candle.high >= t.stop_loss:
                self.close_trade(candle, -t.risk, "SL")
            elif candle.low <= t.take_profit:
                self.close_trade(candle, t.reward, "TP")

    def run(self, df, strategy):

        for i in range(200, len(df)):
            history = df.iloc[:i]
            candle = df.iloc[i]

            # manage open trade
            self.on_candle(candle)

            # open new trade
            if self.open_trade is None:
                signal = strategy.on_candle(history)
                if signal:
                    self.open_position(signal, candle)

            self.equity_curve.append(self.balance)

        return self.balance
