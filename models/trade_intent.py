from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pandas as pd


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ts_utc_iso(value: Any) -> str:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")
    return ts.isoformat()


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, pd.Timestamp):
        return _ts_utc_iso(value)
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    if hasattr(value, "item"):
        try:
            return value.item()
        except Exception:
            pass
    return str(value)


@dataclass
class TradeIntent:
    intent_id: str
    timestamp_utc: str
    symbol: str
    timeframe: str
    strategy: str
    side: str
    entry: float | None
    sl: float
    tp: float
    risk_request: float | None
    min_rr: float | None
    candle_time_utc: str
    signal: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_signal(
        cls,
        *,
        symbol: str,
        timeframe: str,
        candle_time: Any,
        signal: dict[str, Any],
        risk_request: float | None = None,
    ) -> "TradeIntent":
        return cls(
            intent_id=str(uuid4()),
            timestamp_utc=_utcnow_iso(),
            symbol=str(symbol),
            timeframe=str(timeframe),
            strategy=str(signal.get("strategy", "unknown")),
            side=str(signal["side"]),
            entry=float(signal["entry"]) if signal.get("entry") is not None else None,
            sl=float(signal["sl"]),
            tp=float(signal["tp"]),
            risk_request=float(risk_request) if risk_request is not None else None,
            min_rr=float(signal["min_rr"]) if signal.get("min_rr") is not None else None,
            candle_time_utc=_ts_utc_iso(candle_time),
            signal=_json_safe(dict(signal)),
        )

    def to_dict(self) -> dict[str, Any]:
        return _json_safe(asdict(self))

    def to_signal_dict(self) -> dict[str, Any]:
        return {
            "timestamp_utc": self.timestamp_utc,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "strategy": self.strategy,
            "side": self.side,
            "entry": self.entry,
            "sl": self.sl,
            "tp": self.tp,
            "risk_request": self.risk_request,
            "min_rr": self.min_rr,
            "candle_time_utc": self.candle_time_utc,
        }
