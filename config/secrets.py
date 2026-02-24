import os
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class TelegramCredentials:
    token: Optional[str]
    chat_id: Optional[str]


def _load_dotenv_if_present(path: str = ".env") -> None:
    """
    Minimal .env loader (no dependency). Only sets env vars that aren't already set.
    Supports lines like KEY=value or KEY="value". Ignores comments/blank lines.
    """
    try:
        if not os.path.exists(path):
            return

        with open(path, "r", encoding="utf-8") as f:
            for raw in f.readlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue

                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip("'").strip('"')
                if not k:
                    continue
                if os.getenv(k) is None:
                    os.environ[k] = v
    except Exception:
        return


def _env(*names: str) -> Optional[str]:
    for n in names:
        v = os.getenv(n)
        if v is not None and str(v).strip() != "":
            return str(v).strip()
    return None


def get_telegram_credentials() -> TelegramCredentials:
    """
    Loads Telegram credentials from environment variables.

    Supported env vars:
      - TG_TOKEN / TELEGRAM_TOKEN
      - TG_CHAT_ID / TELEGRAM_CHAT_ID

    Backwards compatible fallback:
      - config/telegram.py providing TOKEN, CHAT_ID (file is gitignored)
    """
    _load_dotenv_if_present()

    token = _env("TG_TOKEN", "TELEGRAM_TOKEN")
    chat_id = _env("TG_CHAT_ID", "TELEGRAM_CHAT_ID")

    if token and chat_id:
        return TelegramCredentials(token=token, chat_id=chat_id)

    # Fallback to local gitignored config/telegram.py if present
    try:
        from config.telegram import TOKEN, CHAT_ID  # type: ignore

        return TelegramCredentials(
            token=token or TOKEN,
            chat_id=chat_id or CHAT_ID,
        )
    except Exception:
        return TelegramCredentials(token=token, chat_id=chat_id)

