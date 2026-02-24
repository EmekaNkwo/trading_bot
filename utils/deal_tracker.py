from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

import MetaTrader5 as mt5


@dataclass(frozen=True)
class ClosedDealEvent:
    timestamp_utc: datetime
    symbol: str
    side: str  # buy/sell/unknown
    volume: float
    price: float
    pnl: float
    balance: Optional[float]
    magic: Optional[int]
    deal_ticket: Optional[int]
    order_ticket: Optional[int]
    comment: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _to_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _deal_is_exit(deal) -> bool:
    entry = getattr(deal, "entry", None)
    try:
        return entry == mt5.DEAL_ENTRY_OUT
    except Exception:
        # Fallback: MT5 usually encodes OUT as 1
        return entry == 1


def _deal_side(deal) -> str:
    t = getattr(deal, "type", None)
    try:
        if t == mt5.DEAL_TYPE_BUY:
            return "buy"
        if t == mt5.DEAL_TYPE_SELL:
            return "sell"
    except Exception:
        pass
    return "unknown"


class ClosedDealTracker:
    """
    Polls MT5 history for newly closed deals and yields events once.
    """

    def __init__(
        self,
        *,
        magic: int = 2601,
        poll_lookback_minutes: int = 60,
        overlap_seconds: int = 10,
    ):
        self.magic = magic
        self.poll_lookback = timedelta(minutes=poll_lookback_minutes)
        self.overlap = timedelta(seconds=overlap_seconds)
        self._seen: set[int] = set()
        self._last_to: Optional[datetime] = None

    def _history_window(self) -> tuple[datetime, datetime]:
        to = _utcnow()

        if self._last_to is None:
            frm = to - self.poll_lookback
        else:
            frm = self._last_to - self.overlap

        # MT5 expects naive datetimes in local timezone on some setups;
        # most terminals accept UTC-aware too, but we normalize anyway.
        self._last_to = to
        return frm, to

    def poll(self) -> list[ClosedDealEvent]:
        frm, to = self._history_window()

        deals = mt5.history_deals_get(frm, to)
        if not deals:
            return []

        bal = None
        try:
            acc = mt5.account_info()
            if acc:
                bal = float(getattr(acc, "balance", None))
        except Exception:
            bal = None

        events: list[ClosedDealEvent] = []
        for d in deals:  # type: ignore[assignment]
            if not _deal_is_exit(d):
                continue

            if self.magic is not None and getattr(d, "magic", None) != self.magic:
                continue

            ticket = getattr(d, "ticket", None)
            if ticket is None:
                continue

            try:
                ticket_i = int(ticket)
            except Exception:
                continue

            if ticket_i in self._seen:
                continue

            self._seen.add(ticket_i)

            profit = float(getattr(d, "profit", 0.0) or 0.0)
            commission = float(getattr(d, "commission", 0.0) or 0.0)
            swap = float(getattr(d, "swap", 0.0) or 0.0)
            pnl = profit + commission + swap

            # MT5 deal time is usually seconds since epoch, but sometimes datetime.
            ts = getattr(d, "time", None)
            if isinstance(ts, (int, float)):
                ts_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
            elif isinstance(ts, datetime):
                ts_dt = _to_utc(ts)
            else:
                ts_dt = _utcnow()

            events.append(
                ClosedDealEvent(
                    timestamp_utc=ts_dt,
                    symbol=str(getattr(d, "symbol", "") or ""),
                    side=_deal_side(d),
                    volume=float(getattr(d, "volume", 0.0) or 0.0),
                    price=float(getattr(d, "price", 0.0) or 0.0),
                    pnl=float(pnl),
                    balance=bal,
                    magic=getattr(d, "magic", None),
                    deal_ticket=ticket_i,
                    order_ticket=getattr(d, "order", None),
                    comment=str(getattr(d, "comment", "") or ""),
                )
            )

        events.sort(key=lambda e: e.timestamp_utc)
        return events

