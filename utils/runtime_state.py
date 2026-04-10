from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from threading import Lock
from typing import Any, Optional


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeSnapshot:
    started_at_utc: str
    mode: str
    last_mode_change_utc: str
    last_error: Optional[str]
    last_deal: Optional[dict[str, Any]]
    last_signal: Optional[dict[str, Any]]
    last_intent: Optional[dict[str, Any]]
    portfolio_runtime: Optional[dict[str, Any]]
    orchestrator_graph: Optional[dict[str, Any]]
    research_workflow: Optional[dict[str, Any]]


class RuntimeState:
    def __init__(self):
        self._lock = Lock()
        now = _utcnow_iso()
        self._started_at_utc = now
        self._mode = "boot"
        self._last_mode_change_utc = now
        self._last_error: Optional[str] = None
        self._last_deal: Optional[dict[str, Any]] = None
        self._last_signal: Optional[dict[str, Any]] = None
        self._last_intent: Optional[dict[str, Any]] = None
        self._portfolio_runtime: Optional[dict[str, Any]] = None
        self._orchestrator_graph: Optional[dict[str, Any]] = None
        self._research_workflow: Optional[dict[str, Any]] = None

    def set_mode(self, mode: str):
        with self._lock:
            if mode != self._mode:
                self._mode = mode
                self._last_mode_change_utc = _utcnow_iso()

    def set_error(self, msg: str):
        with self._lock:
            self._last_error = msg

    def set_last_deal(self, deal: dict[str, Any]):
        with self._lock:
            self._last_deal = deal

    def set_last_signal(self, signal: dict[str, Any]):
        with self._lock:
            self._last_signal = signal

    def set_last_intent(self, intent: dict[str, Any]):
        with self._lock:
            self._last_intent = intent

    def set_portfolio_runtime(self, runtime: dict[str, Any]):
        with self._lock:
            self._portfolio_runtime = runtime

    def set_orchestrator_graph(self, graph_state: dict[str, Any]):
        with self._lock:
            self._orchestrator_graph = graph_state

    def set_research_workflow(self, workflow_state: dict[str, Any]):
        with self._lock:
            self._research_workflow = workflow_state

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                started_at_utc=self._started_at_utc,
                mode=self._mode,
                last_mode_change_utc=self._last_mode_change_utc,
                last_error=self._last_error,
                last_deal=self._last_deal,
                last_signal=self._last_signal,
                last_intent=self._last_intent,
                portfolio_runtime=self._portfolio_runtime,
                orchestrator_graph=self._orchestrator_graph,
                research_workflow=self._research_workflow,
            )


STATE = RuntimeState()

