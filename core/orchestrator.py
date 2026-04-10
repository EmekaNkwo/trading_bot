from datetime import datetime
import os
import MetaTrader5 as mt5

from utils.time_utils import SessionFilter
from utils.logger import setup_logger
from optimizer.rollback import ParameterRollback
from core.performance_guard import PerformanceGuard
from core.orchestrator_graph import ModeDecisionGraph


class BotOrchestrator:

    def __init__(self, risk_manager):
        self.logger = setup_logger()
        self.session = SessionFilter()
        self.risk = risk_manager

        self.last_walkforward_date = None
        self.last_backtest_date = None
        self.last_rotation_date = None

        self.rollback = ParameterRollback()
        self.guard = PerformanceGuard()
        self._mode_graph = ModeDecisionGraph(self)
        self._last_graph_snapshot = {
            "engine": "langgraph" if self._mode_graph.is_ready() else "legacy",
            "decided_mode": "boot",
            "reason": "boot",
        }

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

    def _apply_mode_side_effects(self, mode: str, now: datetime) -> None:
        if mode == "walkforward":
            self.last_walkforward_date = now.date()
        elif mode == "rotate":
            self.last_rotation_date = now.date()
        elif mode == "backtest":
            self.last_backtest_date = now.date()

    def graph_snapshot(self) -> dict:
        return dict(self._last_graph_snapshot or {})

    def _decide_mode_legacy(self):
        now = datetime.utcnow()

        if self.allow_live() and self._check_drawdown(max_drawdown_pct=0.10):
            mode = "live"
        elif self.allow_walkforward():
            mode = "walkforward"
        elif self.allow_rotation():
            mode = "rotate"
        elif self.allow_backtest():
            mode = "backtest"
        else:
            mode = "idle"

        self._apply_mode_side_effects(mode, now)
        self._last_graph_snapshot = {
            "requested_at_utc": now.isoformat() + "Z",
            "engine": "legacy",
            "decided_mode": mode,
            "reason": "legacy_fallback",
        }
        return mode

    # -------------------------------------------------
    # MAIN DECISION ENGINE
    # -------------------------------------------------

    def decide_mode(self):
        now = datetime.utcnow()
        forced_mode = str(os.getenv("FORCE_BOT_MODE", "")).strip().lower()
        if forced_mode in {"live", "walkforward", "rotate", "backtest", "idle"}:
            self._last_graph_snapshot = {
                "requested_at_utc": now.isoformat() + "Z",
                "engine": "forced",
                "decided_mode": forced_mode,
                "reason": "forced_mode_override",
            }
            self._apply_mode_side_effects(forced_mode, now)
            return forced_mode
        if not self._mode_graph.is_ready():
            return self._decide_mode_legacy()

        try:
            state = self._mode_graph.invoke()
            mode = str(state.get("decided_mode") or "idle")
            self._apply_mode_side_effects(mode, now)
            self._last_graph_snapshot = state
            return mode
        except Exception as e:
            self.logger.warning(f"LangGraph orchestrator fallback engaged: {e}")
            return self._decide_mode_legacy()
