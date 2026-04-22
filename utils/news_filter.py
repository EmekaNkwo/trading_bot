"""
Economic calendar news filter.

Fetches high-impact events from free APIs, caches them, and exposes a
simple check: "is a major event imminent for this symbol?"

Fail-open design — if all sources fail, trades proceed normally.
"""

from __future__ import annotations

import logging
import time
import threading
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

logger = logging.getLogger(__name__)

SYMBOL_CURRENCIES: dict[str, list[str]] = {
    "XAUUSDm": ["USD"],
    "US30m":   ["USD"],
    "DE30m":   ["EUR"],
    "BTCUSDm": ["USD"],
}

_ALL_IMPACT = ("high", "medium", "low")


class NewsFilter:
    """Checks whether a high-impact economic event is imminent for a symbol."""

    def __init__(self, cfg: dict[str, Any] | None = None):
        cfg = dict(cfg or {})
        self.enabled: bool = bool(cfg.get("enabled", False))
        self.blackout_minutes_before: int = int(cfg.get("blackout_minutes_before", 15) or 15)
        self.blackout_minutes_after: int = int(cfg.get("blackout_minutes_after", 10) or 10)
        self.poll_interval_s: int = int(cfg.get("poll_interval_seconds", 3600) or 3600)
        self.min_impact: str = str(cfg.get("min_impact", "high") or "high").lower()
        self.fmp_api_key: str = str(cfg.get("fmp_api_key", "") or "")
        self.extra_currencies: dict[str, list[str]] = dict(cfg.get("symbol_currencies", {}) or {})

        self._events: list[dict] = []
        self._last_fetch: float = 0.0
        self._lock = threading.Lock()

        self._currency_map = dict(SYMBOL_CURRENCIES)
        for sym, currencies in self.extra_currencies.items():
            if isinstance(currencies, list):
                self._currency_map[sym] = [c.upper() for c in currencies]

    def refresh_if_stale(self) -> None:
        if not self.enabled:
            return
        now = time.time()
        if now - self._last_fetch < self.poll_interval_s:
            return
        with self._lock:
            if now - self._last_fetch < self.poll_interval_s:
                return
            self._fetch_events()

    def is_blocked(self, symbol: str) -> tuple[bool, str | None]:
        """Return (blocked, reason) for the given symbol."""
        if not self.enabled:
            return False, None

        self.refresh_if_stale()

        currencies = self._currency_map.get(symbol)
        if not currencies:
            return False, None

        now_utc = datetime.now(timezone.utc)
        before = timedelta(minutes=self.blackout_minutes_before)
        after = timedelta(minutes=self.blackout_minutes_after)
        threshold_impacts = self._impact_threshold()

        with self._lock:
            events = list(self._events)

        for ev in events:
            if ev.get("impact") not in threshold_impacts:
                continue
            if ev.get("currency") not in currencies:
                continue

            ev_time = ev.get("datetime_utc")
            if ev_time is None:
                continue

            if (ev_time - before) <= now_utc <= (ev_time + after):
                reason = (
                    f"news_blackout|{ev.get('currency')}|{ev.get('title','?')}|"
                    f"{ev_time.strftime('%H:%M UTC')}|impact={ev.get('impact')}"
                )
                return True, reason

        return False, None

    def status_summary(self) -> dict[str, Any]:
        with self._lock:
            upcoming = [
                {
                    "title": ev.get("title"),
                    "currency": ev.get("currency"),
                    "impact": ev.get("impact"),
                    "time_utc": ev["datetime_utc"].isoformat() if ev.get("datetime_utc") else None,
                }
                for ev in self._events
                if ev.get("datetime_utc") and ev["datetime_utc"] > datetime.now(timezone.utc)
            ]
        return {
            "enabled": self.enabled,
            "cached_events": len(self._events),
            "upcoming_today": len(upcoming),
            "last_fetch_ago_s": round(time.time() - self._last_fetch) if self._last_fetch else None,
            "next_events": upcoming[:5],
        }

    def _impact_threshold(self) -> set[str]:
        if self.min_impact == "low":
            return {"high", "medium", "low"}
        if self.min_impact == "medium":
            return {"high", "medium"}
        return {"high"}

    def _fetch_events(self) -> None:
        events: list[dict] | None = None

        events = self._fetch_forex_factory()
        if not events and self.fmp_api_key:
            events = self._fetch_fmp()

        if events is not None:
            self._events = events
            self._last_fetch = time.time()
            logger.info(f"NEWS CALENDAR | fetched {len(events)} events for today")
        else:
            if self._last_fetch > 0:
                logger.warning("NEWS CALENDAR | all sources failed, keeping stale cache")
            else:
                logger.warning("NEWS CALENDAR | all sources failed, no cache available (fail-open)")

    def _fetch_forex_factory(self) -> list[dict] | None:
        """Forex Factory's undocumented calendar JSON feed."""
        try:
            today = datetime.now(timezone.utc).date()
            url = f"https://nfs.faireconomy.media/ff_calendar_thisweek.json"
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            raw = resp.json()

            events = []
            for item in raw:
                ev_date_str = item.get("date", "")
                if not ev_date_str:
                    continue

                try:
                    ev_dt = datetime.fromisoformat(ev_date_str.replace("Z", "+00:00"))
                    if ev_dt.tzinfo is None:
                        ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue

                if ev_dt.date() != today:
                    continue

                impact = self._normalize_impact(item.get("impact", ""))
                events.append({
                    "title": item.get("title", "Unknown"),
                    "currency": (item.get("country", "") or "").upper(),
                    "impact": impact,
                    "datetime_utc": ev_dt,
                })

            logger.info(f"NEWS CALENDAR | forex_factory source: {len(events)} events today")
            return events

        except Exception as e:
            logger.warning(f"NEWS CALENDAR | forex_factory fetch failed: {e}")
            return None

    def _fetch_fmp(self) -> list[dict] | None:
        """FinancialModelingPrep economic calendar (free tier, 250 calls/day)."""
        try:
            today = datetime.now(timezone.utc).date()
            url = (
                f"https://financialmodelingprep.com/api/v3/economic_calendar"
                f"?from={today.isoformat()}&to={today.isoformat()}"
                f"&apikey={self.fmp_api_key}"
            )
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            raw = resp.json()

            if isinstance(raw, dict) and "Error Message" in raw:
                logger.warning(f"NEWS CALENDAR | FMP API error: {raw['Error Message']}")
                return None

            events = []
            for item in raw:
                date_str = item.get("date", "")
                if not date_str:
                    continue
                try:
                    ev_dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    if ev_dt.tzinfo is None:
                        ev_dt = ev_dt.replace(tzinfo=timezone.utc)
                except Exception:
                    continue

                impact = self._normalize_impact(item.get("impact", ""))
                events.append({
                    "title": item.get("event", "Unknown"),
                    "currency": (item.get("currency", "") or "").upper(),
                    "impact": impact,
                    "datetime_utc": ev_dt,
                })

            logger.info(f"NEWS CALENDAR | FMP source: {len(events)} events today")
            return events

        except Exception as e:
            logger.warning(f"NEWS CALENDAR | FMP fetch failed: {e}")
            return None

    @staticmethod
    def _normalize_impact(raw: str) -> str:
        raw = str(raw).strip().lower()
        if raw in ("high", "holiday"):
            return "high"
        if raw in ("medium", "moderate", "med"):
            return "medium"
        if raw in ("low",):
            return "low"
        if "high" in raw:
            return "high"
        if "med" in raw:
            return "medium"
        return "low"
