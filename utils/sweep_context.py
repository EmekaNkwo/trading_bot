from __future__ import annotations

from dataclasses import dataclass
from threading import Lock

import pandas as pd


@dataclass(frozen=True)
class SweepEvent:
    symbol: str
    direction: str
    timestamp: pd.Timestamp
    band_center: float
    extreme: float


class SweepEventStore:
    def __init__(self) -> None:
        self._lock = Lock()
        self._events: dict[str, SweepEvent] = {}

    def record(self, *, symbol: str, direction: str, timestamp: pd.Timestamp, band_center: float, extreme: float) -> None:
        event = SweepEvent(
            symbol=str(symbol),
            direction=str(direction),
            timestamp=pd.Timestamp(timestamp),
            band_center=float(band_center),
            extreme=float(extreme),
        )
        with self._lock:
            self._events[event.symbol] = event

    def get(self, symbol: str) -> SweepEvent | None:
        with self._lock:
            return self._events.get(str(symbol))


SWEEP_EVENTS = SweepEventStore()
