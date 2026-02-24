from __future__ import annotations

import os
import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from utils.runtime_state import STATE


BASE_DIR = Path(__file__).resolve().parents[1]
REPORTS_DIR = BASE_DIR / "reports"
LOGS_DIR = BASE_DIR / "logs"
LIVE_DEALS_CSV = REPORTS_DIR / "live_deals.csv"

try:
    # Reuse the project's minimal .env loader (no extra dependency).
    from config.secrets import _load_dotenv_if_present  # type: ignore

    _load_dotenv_if_present(str(BASE_DIR / ".env"))
except Exception:
    pass


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_token(authorization: Optional[str] = Header(default=None)) -> None:
    token = os.getenv("API_TOKEN")
    if not token:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Bearer token")
    got = authorization.split(" ", 1)[1].strip()
    if got != token:
        raise HTTPException(status_code=403, detail="Invalid token")


def _read_recent_deals(limit: int) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    if not LIVE_DEALS_CSV.exists():
        return []
    try:
        with LIVE_DEALS_CSV.open("r", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
    except Exception:
        return []
    if not rows:
        return []
    return rows[-limit:]


_SAFE_LOG_NAME_RE = re.compile(r"^[a-zA-Z0-9_.-]+\.log$")


def _resolve_log_path(name: str) -> Path:
    """
    Prevent path traversal. Only allow <something>.log inside ./logs.
    """
    if not _SAFE_LOG_NAME_RE.match(name or ""):
        raise HTTPException(status_code=400, detail="Invalid log name")
    p = (LOGS_DIR / name).resolve()
    try:
        p.relative_to(LOGS_DIR.resolve())
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid log path")
    return p


def _tail_lines(path: Path, lines: int, *, max_bytes: int = 2_000_000) -> list[str]:
    """
    Efficient-ish tail without reading unbounded data.
    """
    if lines <= 0:
        return []
    if not path.exists():
        return []

    try:
        size = path.stat().st_size
    except Exception:
        return []

    read_size = min(size, max_bytes)
    chunk_size = 8192
    data = b""
    try:
        with path.open("rb") as f:
            f.seek(max(0, size - read_size))
            while True:
                chunk = f.read(chunk_size)
                if not chunk:
                    break
                data += chunk
                # Stop early if we already have enough lines
                if data.count(b"\n") >= lines + 20:
                    break
    except Exception:
        return []

    text = data.decode("utf-8", errors="replace")
    out = text.splitlines()[-lines:]
    return out


def create_app() -> FastAPI:
    app = FastAPI(title="trading_bot monitoring API", version="1.0.0")

    cors = os.getenv("CORS_ORIGINS")
    if cors:
        origins = [o.strip() for o in cors.split(",") if o.strip()]
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    @app.get("/health", dependencies=[Depends(_require_token)])
    def health() -> dict[str, Any]:
        return {"ok": True, "time_utc": _utcnow_iso()}

    @app.get("/status", dependencies=[Depends(_require_token)])
    def status() -> dict[str, Any]:
        snap = STATE.snapshot()
        deals_count = 0
        if LIVE_DEALS_CSV.exists():
            try:
                with LIVE_DEALS_CSV.open("r", newline="", encoding="utf-8") as f:
                    deals_count = max(0, sum(1 for _ in f) - 1)
            except Exception:
                deals_count = 0
        return {
            "time_utc": _utcnow_iso(),
            "runtime": {
                "started_at_utc": snap.started_at_utc,
                "mode": snap.mode,
                "last_mode_change_utc": snap.last_mode_change_utc,
                "last_error": snap.last_error,
                "last_deal": snap.last_deal,
            },
            "reports": {
                "live_deals_csv": str(LIVE_DEALS_CSV),
                "live_deals_count": deals_count,
            },
        }

    @app.get("/deals/recent", dependencies=[Depends(_require_token)])
    def deals_recent(limit: int = 50) -> dict[str, Any]:
        limit = int(max(1, min(500, limit)))
        rows = _read_recent_deals(limit)
        return {"time_utc": _utcnow_iso(), "limit": limit, "items": rows}

    @app.get("/logs/list", dependencies=[Depends(_require_token)])
    def logs_list() -> dict[str, Any]:
        items: list[dict[str, Any]] = []
        if LOGS_DIR.exists():
            try:
                for p in sorted(LOGS_DIR.glob("*.log")):
                    try:
                        st = p.stat()
                        items.append(
                            {
                                "name": p.name,
                                "size_bytes": int(st.st_size),
                                "modified_utc": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat(),
                            }
                        )
                    except Exception:
                        continue
            except Exception:
                items = []
        return {"time_utc": _utcnow_iso(), "items": items}

    @app.get("/logs/tail", dependencies=[Depends(_require_token)])
    def logs_tail(name: str = "live_trading.log", lines: int = 200) -> dict[str, Any]:
        lines = int(max(1, min(2000, lines)))
        path = _resolve_log_path(name)
        out = _tail_lines(path, lines)
        return {"time_utc": _utcnow_iso(), "name": name, "lines": lines, "items": out}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    host = os.getenv("MONITORING_API_HOST", "127.0.0.1")
    port = int(os.getenv("MONITORING_API_PORT", "8000"))
    uvicorn.run(app, host=host, port=port, log_level=os.getenv("MONITORING_API_LOG_LEVEL", "warning"))


if __name__ == "__main__":
    main()

