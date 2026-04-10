from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable

from utils.logger import setup_logger

try:
    from langgraph.graph import END, START, StateGraph
except Exception:  # pragma: no cover - optional dependency at runtime
    END = "__end__"
    START = "__start__"
    StateGraph = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ResearchWorkflowGraph:
    """
    LangGraph workflow for non-live research operations.

    Supported actions:
    - walkforward
    - rotate
    - report
    """

    def __init__(
        self,
        *,
        walkforward_runner: Callable[[], Any],
        rotation_load_config_runner: Callable[[], Any],
        rotation_candidate_runner: Callable[[], Any],
        rotation_evaluate_runner: Callable[[Any, Any], Any],
        rotation_save_runner: Callable[[Any, Any, Any], Any],
        report_runner: Callable[[], Any],
        guard_evaluate: Callable[[], bool],
        rollback_runner: Callable[[], Any],
        rotation_accept_runner: Callable[[], Any] | None = None,
    ) -> None:
        self.walkforward_runner = walkforward_runner
        self.rotation_load_config_runner = rotation_load_config_runner
        self.rotation_candidate_runner = rotation_candidate_runner
        self.rotation_evaluate_runner = rotation_evaluate_runner
        self.rotation_save_runner = rotation_save_runner
        self.report_runner = report_runner
        self.guard_evaluate = guard_evaluate
        self.rollback_runner = rollback_runner
        self.rotation_accept_runner = rotation_accept_runner or (lambda: None)
        self.logger = setup_logger()

        if self.is_available():
            try:
                self._graph = self._build_graph()
            except Exception as e:
                self.logger.warning(f"Research LangGraph disabled, falling back to legacy workflow: {e}")
                self._graph = None
        else:
            self._graph = None

    @staticmethod
    def is_available() -> bool:
        return StateGraph is not None

    def is_ready(self) -> bool:
        return self._graph is not None

    def invoke(self, action: str) -> dict[str, Any]:
        state = {
            "requested_at_utc": _utcnow_iso(),
            "engine": "langgraph" if self.is_ready() else "legacy",
            "action": str(action or "").lower(),
            "status": "pending",
            "result": None,
            "guard_ok": None,
            "rollback_triggered": False,
            "reason": "pending",
        }

        if not self.is_ready():
            return state

        result = self._graph.invoke(state)
        return result if isinstance(result, dict) else state

    def run(self, action: str) -> dict[str, Any]:
        if not self.is_ready():
            return self._run_legacy(action)
        try:
            return self.invoke(action)
        except Exception as e:
            self.logger.warning(f"Research LangGraph invoke failed, using legacy workflow: {e}")
            return self._run_legacy(action)

    def _run_legacy(self, action: str) -> dict[str, Any]:
        state = {
            "requested_at_utc": _utcnow_iso(),
            "engine": "legacy",
            "action": str(action or "").lower(),
            "status": "pending",
            "result": None,
            "guard_ok": None,
            "rollback_triggered": False,
            "reason": "legacy_pending",
        }

        action = state["action"]
        if action == "walkforward":
            state["result"] = self.walkforward_runner()
            state["status"] = "completed"
            state["reason"] = "walkforward_completed"
            return state
        if action == "rotate":
            base_config = self.rotation_load_config_runner()
            candidate_params = self.rotation_candidate_runner()
            evaluation = self.rotation_evaluate_runner(base_config, candidate_params)
            state["result"] = self.rotation_save_runner(
                evaluation.get("best_config"),
                evaluation.get("best_result"),
                evaluation.get("best_params"),
            )
            state["rotation_evaluation"] = {
                "evaluated_candidates": int(evaluation.get("evaluated_candidates", 0)),
                "valid_candidates": int(evaluation.get("valid_candidates", 0)),
                "selected_params": evaluation.get("best_params"),
            }

            if not evaluation.get("best_config"):
                state["status"] = "completed"
                state["reason"] = "rotation_no_valid_parameter_set"
                return state

            state["guard_ok"] = bool(self.guard_evaluate())
            if state["guard_ok"]:
                accept_result = self.rotation_accept_runner()
                if isinstance(state["result"], dict) and isinstance(accept_result, dict):
                    state["result"] = {**state["result"], **accept_result}
                else:
                    state["result"] = accept_result
                state["status"] = "completed"
                state["reason"] = "rotation_parameters_accepted"
            else:
                rollback_result = self.rollback_runner()
                if isinstance(state["result"], dict) and isinstance(rollback_result, dict):
                    state["result"] = {**state["result"], **rollback_result}
                else:
                    state["result"] = rollback_result
                state["rollback_triggered"] = True
                state["status"] = "completed"
                state["reason"] = "rotation_rollback_triggered"
            return state
        if action == "report":
            state["result"] = self.report_runner()
            state["status"] = "completed"
            state["reason"] = "report_completed"
            return state

        state["status"] = "invalid"
        state["reason"] = "unknown_action"
        return state

    def _build_graph(self):
        graph = StateGraph(dict)

        graph.add_node("route_action", self._route_action_state)
        graph.add_node("run_walkforward", self._run_walkforward)
        graph.add_node("rotation_load_config", self._rotation_load_config)
        graph.add_node("rotation_build_candidates", self._rotation_build_candidates)
        graph.add_node("rotation_evaluate_candidates", self._rotation_evaluate_candidates)
        graph.add_node("rotation_save_best", self._rotation_save_best)
        graph.add_node("rotation_no_valid", self._rotation_no_valid)
        graph.add_node("evaluate_rotation", self._evaluate_rotation)
        graph.add_node("rollback_rotation", self._rollback_rotation)
        graph.add_node("accept_rotation", self._accept_rotation)
        graph.add_node("run_report", self._run_report)
        graph.add_node("invalid_action", self._invalid_action)

        graph.add_edge(START, "route_action")
        graph.add_conditional_edges(
            "route_action",
            self._route_after_action,
            {
                "run_walkforward": "run_walkforward",
                "rotation_load_config": "rotation_load_config",
                "run_report": "run_report",
                "invalid_action": "invalid_action",
            },
        )

        graph.add_edge("run_walkforward", END)
        graph.add_edge("run_report", END)
        graph.add_edge("invalid_action", END)

        graph.add_edge("rotation_load_config", "rotation_build_candidates")
        graph.add_edge("rotation_build_candidates", "rotation_evaluate_candidates")
        graph.add_conditional_edges(
            "rotation_evaluate_candidates",
            self._route_after_rotation_evaluation_setup,
            {
                "rotation_save_best": "rotation_save_best",
                "rotation_no_valid": "rotation_no_valid",
            },
        )
        graph.add_edge("rotation_save_best", "evaluate_rotation")
        graph.add_conditional_edges(
            "evaluate_rotation",
            self._route_after_rotation_evaluation,
            {
                "rollback_rotation": "rollback_rotation",
                "accept_rotation": "accept_rotation",
            },
        )
        graph.add_edge("rotation_no_valid", END)
        graph.add_edge("rollback_rotation", END)
        graph.add_edge("accept_rotation", END)

        return graph.compile()

    def _finalize_state(self, state: dict[str, Any], **updates) -> dict[str, Any]:
        out = {**state, **updates}
        out.pop("_rotation_base_config", None)
        out.pop("_rotation_candidate_params", None)
        out.pop("_rotation_evaluation", None)
        return out

    def _route_action_state(self, state: dict[str, Any]) -> dict[str, Any]:
        return state

    def _route_after_action(self, state: dict[str, Any]) -> str:
        action = str(state.get("action", "")).lower()
        if action == "walkforward":
            return "run_walkforward"
        if action == "rotate":
            return "rotation_load_config"
        if action == "report":
            return "run_report"
        return "invalid_action"

    def _run_walkforward(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.walkforward_runner()
        return self._finalize_state(
            state,
            status="completed",
            result=result,
            reason="walkforward_completed",
        )

    def _rotation_load_config(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.rotation_load_config_runner()
        return {
            **state,
            "status": "rotation_config_loaded",
            "result": result,
            "reason": "rotation_config_loaded",
            "_rotation_base_config": result,
        }

    def _rotation_build_candidates(self, state: dict[str, Any]) -> dict[str, Any]:
        candidate_params = self.rotation_candidate_runner()
        return {
            **state,
            "status": "rotation_candidates_built",
            "reason": "rotation_candidates_built",
            "_rotation_candidate_params": candidate_params,
        }

    def _rotation_evaluate_candidates(self, state: dict[str, Any]) -> dict[str, Any]:
        evaluation = self.rotation_evaluate_runner(
            state.get("_rotation_base_config"),
            state.get("_rotation_candidate_params"),
        )
        return {
            **state,
            "status": "rotation_candidates_evaluated",
            "reason": "rotation_candidates_evaluated",
            "rotation_evaluation": {
                "evaluated_candidates": int(evaluation.get("evaluated_candidates", 0)),
                "valid_candidates": int(evaluation.get("valid_candidates", 0)),
                "selected_params": evaluation.get("best_params"),
            },
            "_rotation_evaluation": evaluation,
        }

    def _route_after_rotation_evaluation_setup(self, state: dict[str, Any]) -> str:
        evaluation = state.get("_rotation_evaluation") or {}
        return "rotation_save_best" if evaluation.get("best_config") is not None else "rotation_no_valid"

    def _rotation_save_best(self, state: dict[str, Any]) -> dict[str, Any]:
        evaluation = state.get("_rotation_evaluation") or {}
        result = self.rotation_save_runner(
            evaluation.get("best_config"),
            evaluation.get("best_result"),
            evaluation.get("best_params"),
        )
        return {
            **state,
            "status": "rotation_saved",
            "result": result,
            "reason": "rotation_saved",
        }

    def _rotation_no_valid(self, state: dict[str, Any]) -> dict[str, Any]:
        evaluation = state.get("_rotation_evaluation") or {}
        result = self.rotation_save_runner(
            None,
            evaluation.get("best_result"),
            evaluation.get("best_params"),
        )
        return self._finalize_state(
            state,
            status="completed",
            rollback_triggered=False,
            reason="rotation_no_valid_parameter_set",
            result=result,
        )

    def _evaluate_rotation(self, state: dict[str, Any]) -> dict[str, Any]:
        guard_ok = bool(self.guard_evaluate())
        return {
            **state,
            "guard_ok": guard_ok,
        }

    def _route_after_rotation_evaluation(self, state: dict[str, Any]) -> str:
        return "accept_rotation" if state.get("guard_ok") else "rollback_rotation"

    def _rollback_rotation(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.rollback_runner()
        prior_result = state.get("result")
        if isinstance(prior_result, dict) and isinstance(result, dict):
            result = {**prior_result, **result}
        return self._finalize_state(
            state,
            status="completed",
            result=result,
            rollback_triggered=True,
            reason="rotation_rollback_triggered",
        )

    def _accept_rotation(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.rotation_accept_runner()
        prior_result = state.get("result")
        if isinstance(prior_result, dict) and isinstance(result, dict):
            result = {**prior_result, **result}
        return self._finalize_state(
            state,
            status="completed",
            result=result,
            rollback_triggered=False,
            reason="rotation_parameters_accepted",
        )

    def _run_report(self, state: dict[str, Any]) -> dict[str, Any]:
        result = self.report_runner()
        return self._finalize_state(
            state,
            status="completed",
            result=result,
            reason="report_completed",
        )

    def _invalid_action(self, state: dict[str, Any]) -> dict[str, Any]:
        return self._finalize_state(
            state,
            status="invalid",
            reason="unknown_action",
        )
