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
        # Derive R:R from the actual SL/TP distances if possible.
        try:
            entry = float(candle.close)
            sl = float(signal["sl"])
            tp = float(signal["tp"])

            stop_dist = abs(entry - sl)
            tp_dist = abs(tp - entry)
            rr = (tp_dist / stop_dist) if stop_dist > 0 else 0.0
        except Exception:
            rr = 2.5

        reward_amount = risk_amount * rr

        self.open_trade = Trade(
            side=signal["side"],
            entry_price=candle.close,
            stop_loss=signal["sl"],
            take_profit=signal["tp"],
            entry_time=candle.name,
        )

        self.open_trade.risk = risk_amount
        self.open_trade.reward = reward_amount
        self.open_trade.rr = rr

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

    def _force_close_end(self, candle):
        """
        Force-close any open trade at end of window (mark-to-market).
        Without this, many backtests report 0 trades when SL/TP wasn't hit yet.
        """
        if not self.open_trade:
            return

        t = self.open_trade
        entry = float(t.entry_price)
        px = float(candle.close)

        if t.side == "buy":
            move = px - entry
            stop_dist = entry - float(t.stop_loss)
        else:
            move = entry - px
            stop_dist = float(t.stop_loss) - entry

        if stop_dist <= 0:
            pnl = 0.0
        else:
            rr = move / stop_dist
            pnl = float(t.risk) * rr
            # Clamp between -1R and +reward.
            pnl = max(-float(t.risk), min(float(t.reward), pnl))

        self.close_trade(candle, pnl, "EOD")

    def run(self, df, strategy, trade_start_idx=200, history_window=800, symbol=None, timeframe=None):

        start_i = max(1, int(trade_start_idx))
        warmup_i = 200
        market_state = getattr(strategy, "market_state", None)
        bound_symbol = str(symbol or getattr(strategy, "symbol", "XAUUSDm"))
        bound_timeframe = str(timeframe or "M5")

        for i in range(warmup_i, len(df)):
            if history_window is None:
                history = df.iloc[:i]
            else:
                start = max(0, i - int(history_window))
                history = df.iloc[start:i]
            candle = df.iloc[i]

            # manage open trade
            self.on_candle(candle)

            # open new trade
            if self.open_trade is None and i >= start_i:
                if market_state is not None:
                    try:
                        market_state.update(symbol=bound_symbol, timeframe=bound_timeframe, df=history)
                    except Exception:
                        pass
                signal = strategy.on_candle(history)
                if signal:
                    self.open_position(signal, candle)

            self.equity_curve.append(self.balance)

        # End-of-window close (mark-to-market)
        if len(df):
            self._force_close_end(df.iloc[-1])

        return self.balance
