from __future__ import annotations

from datetime import datetime
from typing import Any

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - optional dependency at runtime
    END = "__end__"
    START = "__start__"
    StateGraph = None


class ModeDecisionGraph:
    """
    Graph wrapper for orchestrator mode selection.

    This is intentionally narrow: LangGraph helps make the decision flow explicit
    while the actual live execution path remains deterministic and centralized.
    """

    def __init__(self, orchestrator) -> None:
        self.orchestrator = orchestrator
        if self.is_available():
            try:
                self._graph = self._build_graph()
            except Exception:
                self._graph = None
        else:
            self._graph = None

    @staticmethod
    def is_available() -> bool:
        return StateGraph is not None

    def is_ready(self) -> bool:
        return self._graph is not None

    def invoke(self) -> dict[str, Any]:
        now = datetime.utcnow()
        state = {
            "requested_at_utc": now.isoformat() + "Z",
            "engine": "langgraph" if self.is_available() else "legacy",
            "live_allowed": False,
            "drawdown_ok": None,
            "walkforward_allowed": False,
            "rotation_allowed": False,
            "backtest_allowed": False,
            "decided_mode": "idle",
            "reason": "default_idle",
        }

        if self._graph is None:
            return state

        result = self._graph.invoke(state)
        return result if isinstance(result, dict) else state

    def _build_graph(self):
        graph = StateGraph(dict)

        graph.add_node("check_live", self._check_live)
        graph.add_node("set_live", self._set_live)
        graph.add_node("check_walkforward", self._check_walkforward)
        graph.add_node("set_walkforward", self._set_walkforward)
        graph.add_node("check_rotation", self._check_rotation)
        graph.add_node("set_rotation", self._set_rotation)
        graph.add_node("check_backtest", self._check_backtest)
        graph.add_node("set_backtest", self._set_backtest)
        graph.add_node("set_idle", self._set_idle)

        graph.add_edge(START, "check_live")
        graph.add_conditional_edges(
            "check_live",
            self._route_after_live,
            {
                "set_live": "set_live",
                "check_walkforward": "check_walkforward",
            },
        )
        graph.add_edge("set_live", END)

        graph.add_conditional_edges(
            "check_walkforward",
            self._route_after_walkforward,
            {
                "set_walkforward": "set_walkforward",
                "check_rotation": "check_rotation",
            },
        )
        graph.add_edge("set_walkforward", END)

        graph.add_conditional_edges(
            "check_rotation",
            self._route_after_rotation,
            {
                "set_rotation": "set_rotation",
                "check_backtest": "check_backtest",
            },
        )
        graph.add_edge("set_rotation", END)

        graph.add_conditional_edges(
            "check_backtest",
            self._route_after_backtest,
            {
                "set_backtest": "set_backtest",
                "set_idle": "set_idle",
            },
        )
        graph.add_edge("set_backtest", END)
        graph.add_edge("set_idle", END)

        return graph.compile()

    def _check_live(self, state: dict[str, Any]) -> dict[str, Any]:
        live_allowed = bool(self.orchestrator.allow_live())
        drawdown_ok = bool(self.orchestrator._check_drawdown(max_drawdown_pct=0.10))
        return {
            **state,
            "live_allowed": live_allowed,
            "drawdown_ok": drawdown_ok,
        }

    def _route_after_live(self, state: dict[str, Any]) -> str:
        if state.get("live_allowed") and state.get("drawdown_ok"):
            return "set_live"
        return "check_walkforward"

    def _set_live(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "decided_mode": "live",
            "reason": "live_allowed",
        }

    def _check_walkforward(self, state: dict[str, Any]) -> dict[str, Any]:
        allowed = bool(self.orchestrator.allow_walkforward())
        return {
            **state,
            "walkforward_allowed": allowed,
        }

    def _route_after_walkforward(self, state: dict[str, Any]) -> str:
        return "set_walkforward" if state.get("walkforward_allowed") else "check_rotation"

    def _set_walkforward(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "decided_mode": "walkforward",
            "reason": "nightly_walkforward_window",
        }

    def _check_rotation(self, state: dict[str, Any]) -> dict[str, Any]:
        allowed = bool(self.orchestrator.allow_rotation())
        return {
            **state,
            "rotation_allowed": allowed,
        }

    def _route_after_rotation(self, state: dict[str, Any]) -> str:
        return "set_rotation" if state.get("rotation_allowed") else "check_backtest"

    def _set_rotation(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "decided_mode": "rotate",
            "reason": "rotation_window",
        }

    def _check_backtest(self, state: dict[str, Any]) -> dict[str, Any]:
        allowed = bool(self.orchestrator.allow_backtest())
        return {
            **state,
            "backtest_allowed": allowed,
        }

    def _route_after_backtest(self, state: dict[str, Any]) -> str:
        return "set_backtest" if state.get("backtest_allowed") else "set_idle"

    def _set_backtest(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "decided_mode": "backtest",
            "reason": "backtest_window",
        }

    def _set_idle(self, state: dict[str, Any]) -> dict[str, Any]:
        return {
            **state,
            "decided_mode": "idle",
            "reason": "no_mode_allowed",
        }
