from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any


@dataclass
class SymbolHealthStatus:
    symbol: str
    failure_count: int
    disabled_until_utc: str | None
    last_reason: str | None
    last_success_utc: str | None


class SymbolHealthGuard:
    def __init__(self, max_failures: int = 3, cooldown_minutes: int = 30):
        self.max_failures = max(1, int(max_failures))
        self.cooldown = timedelta(minutes=max(1, int(cooldown_minutes)))
        self.failure_count: dict[str, int] = {}
        self.disabled_until: dict[str, datetime] = {}
        self.last_reason: dict[str, str] = {}
        self.last_success: dict[str, datetime] = {}

    def record_failure(self, symbol: str, reason: str) -> bool:
        count = self.failure_count.get(symbol, 0) + 1
        self.failure_count[symbol] = count
        self.last_reason[symbol] = str(reason)
        if count >= self.max_failures:
            self.disabled_until[symbol] = datetime.utcnow() + self.cooldown
            self.failure_count[symbol] = 0
            return True
        return False

    def record_success(self, symbol: str) -> None:
        self.failure_count[symbol] = 0
        self.last_success[symbol] = datetime.utcnow()
        self.last_reason.pop(symbol, None)

    def quarantine(self, symbol: str, reason: str, *, cooldown_minutes: int | None = None) -> None:
        minutes = max(1, int(cooldown_minutes)) if cooldown_minutes is not None else int(
            max(1, self.cooldown.total_seconds() // 60)
        )
        self.disabled_until[symbol] = datetime.utcnow() + timedelta(minutes=minutes)
        self.failure_count[symbol] = 0
        self.last_reason[symbol] = str(reason)

    def allowed(self, symbol: str) -> bool:
        until = self.disabled_until.get(symbol)
        if until is None:
            return True
        return datetime.utcnow() > until

    def status(self, symbol: str) -> SymbolHealthStatus:
        until = self.disabled_until.get(symbol)
        last_success = self.last_success.get(symbol)
        return SymbolHealthStatus(
            symbol=symbol,
            failure_count=int(self.failure_count.get(symbol, 0)),
            disabled_until_utc=until.isoformat() if until else None,
            last_reason=self.last_reason.get(symbol),
            last_success_utc=last_success.isoformat() if last_success else None,
        )

    def snapshot(self) -> dict[str, Any]:
        symbols = set(self.failure_count) | set(self.disabled_until) | set(self.last_reason) | set(self.last_success)
        return {
            symbol: {
                "failure_count": int(self.failure_count.get(symbol, 0)),
                "disabled_until_utc": (
                    self.disabled_until[symbol].isoformat() if symbol in self.disabled_until else None
                ),
                "last_reason": self.last_reason.get(symbol),
                "last_success_utc": (
                    self.last_success[symbol].isoformat() if symbol in self.last_success else None
                ),
            }
            for symbol in sorted(symbols)
        }

    def restore(self, payload: dict[str, Any] | None) -> None:
        if not isinstance(payload, dict):
            return
        for symbol, item in payload.items():
            if not isinstance(item, dict):
                continue
            try:
                self.failure_count[str(symbol)] = int(item.get("failure_count", 0))
            except Exception:
                self.failure_count[str(symbol)] = 0
            reason = item.get("last_reason")
            if reason:
                self.last_reason[str(symbol)] = str(reason)
            disabled_until = item.get("disabled_until_utc")
            if disabled_until:
                try:
                    self.disabled_until[str(symbol)] = datetime.fromisoformat(str(disabled_until))
                except Exception:
                    pass
            last_success = item.get("last_success_utc")
            if last_success:
                try:
                    self.last_success[str(symbol)] = datetime.fromisoformat(str(last_success))
                except Exception:
                    pass
