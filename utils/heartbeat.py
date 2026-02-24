import time
from datetime import datetime
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials

class Heartbeat:

    def __init__(self, interval_minutes=30):
        self.interval = interval_minutes * 60
        self.last_sent = 0
        tg = get_telegram_credentials()
        self.notifier = TelegramNotifier(tg.token, tg.chat_id)

    def tick(self):
        now = time.time()

        if now - self.last_sent < self.interval:
            return

        msg = (
            "BOT HEARTBEAT: BOT IS ALIVE\n"
            f"Time UTC: {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"
        )

        try:
            self.notifier.send(msg)
        except Exception as e:
            print("Heartbeat failed:", e)

        self.last_sent = now