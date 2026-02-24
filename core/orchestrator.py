from datetime import datetime
import MetaTrader5 as mt5

from utils.time_utils import SessionFilter
from optimizer.rollback import ParameterRollback
from core.performance_guard import PerformanceGuard


class BotOrchestrator:

    def __init__(self, risk_manager):
        self.session = SessionFilter()
        self.risk = risk_manager

        self.last_walkforward_date = None
        self.last_backtest_date = None
        self.last_rotation_date = None

        self.rollback = ParameterRollback()
        self.guard = PerformanceGuard()

    # -------------------------------------------------
    # BASIC STATE CHECKS
    # -------------------------------------------------

    def is_weekend(self):
        return datetime.utcnow().weekday() >= 5

    def mt5_connected(self):
        try:
            return mt5.initialize()
        except Exception:
            return False

    def _check_drawdown(self, max_drawdown_pct=0.10):
        """Check if account drawdown exceeds threshold"""
        account = mt5.account_info()
        if not account:
            return True  # Fail safe - block if no account info
        
        balance = account.balance
        equity = account.equity
        
        if balance <= 0:
            return True
        
        drawdown_pct = (balance - equity) / balance
        
        if drawdown_pct >= max_drawdown_pct:
            return False  # Drawdown too high
        
        return True  # Drawdown acceptable

    # -------------------------------------------------
    # MODE PERMISSIONS
    # -------------------------------------------------

    def allow_live(self):
        return (
            not self.is_weekend()
            and not self.risk.kill_switch
            and self.mt5_connected()
            and self.session.allowed()
        )

    def allow_rotation(self):
        now = datetime.utcnow()

        # Run rotation only once per day, during quiet hours (night UTC).
        if self.last_rotation_date == now.date():
            return False

        if not (now.hour >= 22 or now.hour <= 2):
            return False

        state = self.rollback.load_state()
        last = state.get("last_rotation")

        if not last:
            return True

        last = datetime.fromisoformat(last)
        return (datetime.utcnow() - last).days >= 30

    def allow_walkforward(self):
        now = datetime.utcnow()

        # only once per day
        if self.last_walkforward_date == now.date():
            return False

        # late night window: 22:00 – 02:59 UTC
        if not (now.hour >= 22 or now.hour <= 2):
            return False

        return True

    def allow_backtest(self):
        """
        Backtest is heavy and verbose; run it autonomously only in a short night window.
        """
        now = datetime.utcnow()

        # only once per day
        if self.last_backtest_date == now.date():
            return False

        # Backtest window: 03:00–06:59 UTC (after walk-forward, before London)
        if 3 <= now.hour <= 6:
            return True

        return False

    # -------------------------------------------------
    # MAIN DECISION ENGINE
    # -------------------------------------------------

    def decide_mode(self):

        now = datetime.utcnow()

        # LIVE is the only mode that requires MT5 connection.
        if self.allow_live() and self._check_drawdown(max_drawdown_pct=0.10):
            return "live"

        # WALK-FORWARD (NIGHTLY RESEARCH)
        if self.allow_walkforward():
            self.last_walkforward_date = now.date()
            return "walkforward"

        # PARAMETER ROTATION (MONTHLY)
        if self.allow_rotation():
            self.last_rotation_date = now.date()
            return "rotate"

        # BACKTEST (NIGHTLY, light schedule)
        if self.allow_backtest():
            self.last_backtest_date = now.date()
            return "backtest"

        return "idle"
