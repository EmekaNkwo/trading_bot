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

    # -------------------------------------------------
    # MAIN DECISION ENGINE
    # -------------------------------------------------

    def decide_mode(self):

        now = datetime.utcnow()

        # HARD STOPS
        if self.is_weekend():
            return "idle"

        if self.risk.kill_switch:
            return "idle"

        if not self.mt5_connected():
            return "idle"

        # LIVE (PORTFOLIO)
        if self.allow_live():
            return "live"

        # WALK-FORWARD (NIGHTLY RESEARCH)
        if self.allow_walkforward():
            self.last_walkforward_date = now.date()
            return "walkforward"

        # PARAMETER ROTATION (MONTHLY)
        if self.allow_rotation():
            return "rotate"

        # DEFAULT OFF-SESSION ACTIVITY
        return "backtest"
