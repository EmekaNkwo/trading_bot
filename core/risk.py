from datetime import datetime, date
import MetaTrader5 as mt5


class RiskManager:
    """
    Handles all live trading risk limits.
    """

    def __init__(
        self,
        max_trades_per_day=3,
        max_daily_loss=0.02,
        max_open_positions=1,
    ):
        self.max_trades = max_trades_per_day
        self.max_daily_loss = max_daily_loss
        self.max_open_positions = max_open_positions

        self.today = date.today()
        self.trades_today = 0

        self.start_balance = None
        self.kill_switch = False

        self.last_reset = datetime.utcnow()

    # -------------------------------------------------------
    # ACCOUNT DATA
    # -------------------------------------------------------

    def get_account(self):
        return mt5.account_info()

    def get_balance(self):
        acc = self.get_account()
        return acc.balance if acc else 0.0

    def get_equity(self):
        acc = self.get_account()
        return acc.equity if acc else 0.0

    # -------------------------------------------------------
    # DAILY RESET
    # -------------------------------------------------------

    def reset_if_new_day(self):
        today = date.today()

        if today != self.today:
            self.today = today
            self.trades_today = 0
            self.start_balance = None
            self.kill_switch = False
            self.last_reset = datetime.utcnow()

    # -------------------------------------------------------
    # POSITION CHECKS
    # -------------------------------------------------------

    def open_positions_count(self):
        positions = mt5.positions_get()
        return len(positions) if positions else 0

    # -------------------------------------------------------
    # LOSS CONTROL
    # -------------------------------------------------------

    def daily_loss_exceeded(self):

        balance = self.get_balance()

        if balance == 0:
            return False

        if self.start_balance is None:
            self.start_balance = balance
            return False

        loss = self.start_balance - balance
        loss_pct = loss / self.start_balance

        if loss_pct >= self.max_daily_loss:
            self.kill_switch = True
            return True

        return False

    # -------------------------------------------------------
    # CORE DECISION LOGIC
    # -------------------------------------------------------

    def allow_new_trade(self):
        """
        Returns:
            (bool, reason)
        """

        self.reset_if_new_day()

        if self.kill_switch:
            return False, "Daily kill switch active"

        if self.trades_today >= self.max_trades:
            return False, "Max trades per day reached"

        if self.daily_loss_exceeded():
            return False, "Max daily loss exceeded"

        if self.open_positions_count() >= self.max_open_positions:
            return False, "Max open positions reached"

        return True, "Trade allowed"

    # -------------------------------------------------------
    # TRADE REGISTRATION
    # -------------------------------------------------------

    def record_trade(self):
        self.trades_today += 1

    # -------------------------------------------------------
    # STATUS SNAPSHOT (optional)
    # -------------------------------------------------------

    def status(self):
        return {
            "date": str(self.today),
            "trades_today": self.trades_today,
            "balance": round(self.get_balance(), 2),
            "equity": round(self.get_equity(), 2),
            "kill_switch": self.kill_switch,
            "last_reset": self.last_reset.strftime("%Y-%m-%d %H:%M:%S"),
        }
