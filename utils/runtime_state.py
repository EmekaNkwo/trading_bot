from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
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
    operator_controls: Optional[dict[str, Any]]
    orchestrator_graph: Optional[dict[str, Any]]
    research_workflow: Optional[dict[str, Any]]


class RuntimeState:
    def __init__(self):
        self._lock = Lock()
        self._path = Path(__file__).resolve().parent.parent / "state" / "runtime_snapshot.json"
        now = _utcnow_iso()
        self._started_at_utc = now
        self._mode = "boot"
        self._last_mode_change_utc = now
        self._last_error: Optional[str] = None
        self._last_deal: Optional[dict[str, Any]] = None
        self._last_signal: Optional[dict[str, Any]] = None
        self._last_intent: Optional[dict[str, Any]] = None
        self._portfolio_runtime: Optional[dict[str, Any]] = None
        self._operator_controls: Optional[dict[str, Any]] = None
        self._orchestrator_graph: Optional[dict[str, Any]] = None
        self._research_workflow: Optional[dict[str, Any]] = None
        self._restore()

    def _restore(self) -> None:
        if not self._path.exists():
            return
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        self._started_at_utc = str(payload.get("started_at_utc") or self._started_at_utc)
        self._mode = str(payload.get("mode") or self._mode)
        self._last_mode_change_utc = str(payload.get("last_mode_change_utc") or self._last_mode_change_utc)
        self._last_error = payload.get("last_error")
        self._last_deal = payload.get("last_deal")
        self._last_signal = payload.get("last_signal")
        self._last_intent = payload.get("last_intent")
        self._portfolio_runtime = payload.get("portfolio_runtime")
        self._operator_controls = payload.get("operator_controls")
        self._orchestrator_graph = payload.get("orchestrator_graph")
        self._research_workflow = payload.get("research_workflow")

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "started_at_utc": self._started_at_utc,
            "mode": self._mode,
            "last_mode_change_utc": self._last_mode_change_utc,
            "last_error": self._last_error,
            "last_deal": self._last_deal,
            "last_signal": self._last_signal,
            "last_intent": self._last_intent,
            "portfolio_runtime": self._portfolio_runtime,
            "operator_controls": self._operator_controls,
            "orchestrator_graph": self._orchestrator_graph,
            "research_workflow": self._research_workflow,
        }
        tmp_path = self._path.with_suffix(f"{self._path.suffix}.tmp")
        tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self._path)

    def set_mode(self, mode: str):
        with self._lock:
            if mode != self._mode:
                self._mode = mode
                self._last_mode_change_utc = _utcnow_iso()
                self._persist()

    def set_error(self, msg: str):
        with self._lock:
            self._last_error = msg
            self._persist()

    def set_last_deal(self, deal: dict[str, Any]):
        with self._lock:
            self._last_deal = deal
            self._persist()

    def set_last_signal(self, signal: dict[str, Any]):
        with self._lock:
            self._last_signal = signal
            self._persist()

    def set_last_intent(self, intent: dict[str, Any]):
        with self._lock:
            self._last_intent = intent
            self._persist()

    def set_portfolio_runtime(self, runtime: dict[str, Any]):
        with self._lock:
            self._portfolio_runtime = runtime
            self._persist()

    def set_operator_controls(self, controls: dict[str, Any]):
        with self._lock:
            self._operator_controls = controls
            self._persist()

    def set_orchestrator_graph(self, graph_state: dict[str, Any]):
        with self._lock:
            self._orchestrator_graph = graph_state
            self._persist()

    def set_research_workflow(self, workflow_state: dict[str, Any]):
        with self._lock:
            self._research_workflow = workflow_state
            self._persist()

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
                operator_controls=self._operator_controls,
                orchestrator_graph=self._orchestrator_graph,
                research_workflow=self._research_workflow,
            )


STATE = RuntimeState()

