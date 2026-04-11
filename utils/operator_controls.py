from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class OperatorControls:
    def __init__(self, path: str | None = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.path = Path(path) if path else (base_dir / "state" / "operator_controls.json")
        self._lock = Lock()
        self._state: dict[str, Any] = {
            "global_pause": False,
            "global_pause_reason": None,
            "killed_symbols": {},
            "account_brake_clear_nonce": 0,
            "last_account_brake_clear_reason": None,
            "last_account_brake_clear_utc": None,
            "updated_at_utc": _utcnow_iso(),
        }
        self.reload()

    def reload(self) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                return self.snapshot()
            try:
                payload = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                return self.snapshot()
            if not isinstance(payload, dict):
                return self.snapshot()
            self._state["global_pause"] = bool(payload.get("global_pause", False))
            reason = payload.get("global_pause_reason")
            self._state["global_pause_reason"] = str(reason) if reason else None

            killed_symbols: dict[str, Any] = {}
            for symbol, item in dict(payload.get("killed_symbols", {}) or {}).items():
                if not str(symbol).strip():
                    continue
                if isinstance(item, dict):
                    killed_symbols[str(symbol)] = {
                        "reason": str(item.get("reason")) if item.get("reason") else None,
                        "updated_at_utc": (
                            str(item.get("updated_at_utc")) if item.get("updated_at_utc") else None
                        ),
                    }
                else:
                    killed_symbols[str(symbol)] = {
                        "reason": str(item) if item else None,
                        "updated_at_utc": None,
                    }
            self._state["killed_symbols"] = killed_symbols
            try:
                self._state["account_brake_clear_nonce"] = int(payload.get("account_brake_clear_nonce", 0) or 0)
            except Exception:
                self._state["account_brake_clear_nonce"] = 0
            clear_reason = payload.get("last_account_brake_clear_reason")
            self._state["last_account_brake_clear_reason"] = str(clear_reason) if clear_reason else None
            clear_time = payload.get("last_account_brake_clear_utc")
            self._state["last_account_brake_clear_utc"] = str(clear_time) if clear_time else None
            updated_at = payload.get("updated_at_utc")
            self._state["updated_at_utc"] = str(updated_at) if updated_at else _utcnow_iso()
            return self.snapshot()

    def _persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._state["updated_at_utc"] = _utcnow_iso()
        tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
        tmp_path.write_text(json.dumps(self._state, indent=2, sort_keys=True), encoding="utf-8")
        tmp_path.replace(self.path)

    def snapshot(self) -> dict[str, Any]:
        return {
            "global_pause": bool(self._state.get("global_pause", False)),
            "global_pause_reason": self._state.get("global_pause_reason"),
            "killed_symbols": dict(self._state.get("killed_symbols", {}) or {}),
            "account_brake_clear_nonce": int(self._state.get("account_brake_clear_nonce", 0) or 0),
            "last_account_brake_clear_reason": self._state.get("last_account_brake_clear_reason"),
            "last_account_brake_clear_utc": self._state.get("last_account_brake_clear_utc"),
            "updated_at_utc": self._state.get("updated_at_utc"),
        }

    def set_global_pause(self, paused: bool, *, reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            self._state["global_pause"] = bool(paused)
            self._state["global_pause_reason"] = str(reason) if reason else None
            self._persist()
            return self.snapshot()

    def kill_symbol(self, symbol: str, *, reason: str | None = None) -> dict[str, Any]:
        symbol = str(symbol).strip()
        if not symbol:
            raise ValueError("symbol is required")
        with self._lock:
            killed = dict(self._state.get("killed_symbols", {}) or {})
            killed[symbol] = {
                "reason": str(reason) if reason else None,
                "updated_at_utc": _utcnow_iso(),
            }
            self._state["killed_symbols"] = killed
            self._persist()
            return self.snapshot()

    def unkill_symbol(self, symbol: str) -> dict[str, Any]:
        symbol = str(symbol).strip()
        if not symbol:
            raise ValueError("symbol is required")
        with self._lock:
            killed = dict(self._state.get("killed_symbols", {}) or {})
            killed.pop(symbol, None)
            self._state["killed_symbols"] = killed
            self._persist()
            return self.snapshot()

    def request_account_brake_clear(self, *, reason: str | None = None) -> dict[str, Any]:
        with self._lock:
            nonce = int(self._state.get("account_brake_clear_nonce", 0) or 0) + 1
            self._state["account_brake_clear_nonce"] = nonce
            self._state["last_account_brake_clear_reason"] = str(reason) if reason else None
            self._state["last_account_brake_clear_utc"] = _utcnow_iso()
            self._persist()
            return self.snapshot()


CONTROLS = OperatorControls()
