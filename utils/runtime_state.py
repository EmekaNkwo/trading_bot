from __future__ import annotations

from dataclasses import dataclass, asdict
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


class RuntimeState:
    def __init__(self):
        self._lock = Lock()
        now = _utcnow_iso()
        self._started_at_utc = now
        self._mode = "boot"
        self._last_mode_change_utc = now
        self._last_error: Optional[str] = None
        self._last_deal: Optional[dict[str, Any]] = None

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

    def snapshot(self) -> RuntimeSnapshot:
        with self._lock:
            return RuntimeSnapshot(
                started_at_utc=self._started_at_utc,
                mode=self._mode,
                last_mode_change_utc=self._last_mode_change_utc,
                last_error=self._last_error,
                last_deal=self._last_deal,
            )


STATE = RuntimeState()

