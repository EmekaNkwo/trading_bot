from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Any


class PortfolioState:
    def __init__(self, path: str | None = None):
        base_dir = Path(__file__).resolve().parent.parent
        self.path = Path(path) if path else (base_dir / "state" / "portfolio_runtime.json")
        self.last_trade_result: dict[str, float] = {}
        self.engine_last_candle: dict[str, str] = {}
        self.cooldown_state: dict[str, Any] = {}
        self.drawdown_state: dict[str, Any] = {}
        self.health_state: dict[str, Any] = {}
        self.updated_at_utc: str | None = None
        self._lock = Lock()
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        self.last_trade_result = dict(payload.get("last_trade_result", {}) or {})
        self.engine_last_candle = dict(payload.get("engine_last_candle", {}) or {})
        self.cooldown_state = dict(payload.get("cooldown_state", {}) or {})
        self.drawdown_state = dict(payload.get("drawdown_state", {}) or {})
        self.health_state = dict(payload.get("health_state", {}) or {})
        updated_at = payload.get("updated_at_utc")
        self.updated_at_utc = str(updated_at) if updated_at else None

    def snapshot(self) -> dict[str, Any]:
        return {
            "last_trade_result": dict(self.last_trade_result),
            "engine_last_candle": dict(self.engine_last_candle),
            "cooldown_state": dict(self.cooldown_state),
            "drawdown_state": dict(self.drawdown_state),
            "health_state": dict(self.health_state),
            "updated_at_utc": self.updated_at_utc,
        }

    def persist(self) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.updated_at_utc = datetime.utcnow().isoformat()
            payload = json.dumps(self.snapshot(), indent=2, sort_keys=True)
            tmp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            tmp_path.replace(self.path)

    def record(self, symbol: str, pnl: float, *, persist: bool = True) -> None:
        self.last_trade_result[str(symbol)] = float(pnl)
        if persist:
            self.persist()

    def set_engine_last_candle(
        self,
        engine_key: str,
        candle_time_utc: str | None,
        *,
        persist: bool = True,
    ) -> None:
        if candle_time_utc:
            self.engine_last_candle[str(engine_key)] = str(candle_time_utc)
        else:
            self.engine_last_candle.pop(str(engine_key), None)
        if persist:
            self.persist()

    def get_engine_last_candle(self, engine_key: str) -> str | None:
        value = self.engine_last_candle.get(str(engine_key))
        return str(value) if value else None

    def set_cooldown_state(self, payload: dict[str, Any], *, persist: bool = True) -> None:
        self.cooldown_state = dict(payload or {})
        if persist:
            self.persist()

    def set_drawdown_state(self, payload: dict[str, Any], *, persist: bool = True) -> None:
        self.drawdown_state = dict(payload or {})
        if persist:
            self.persist()

    def set_health_state(self, payload: dict[str, Any], *, persist: bool = True) -> None:
        self.health_state = dict(payload or {})
        if persist:
            self.persist()
