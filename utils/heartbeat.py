import time
from datetime import datetime
from utils.telegram import TelegramNotifier
from config.telegram import TOKEN, CHAT_ID

class Heartbeat:

    def __init__(self, interval_minutes=30):
        self.interval = interval_minutes * 60
        self.last_sent = 0
        self.notifier = TelegramNotifier(TOKEN, CHAT_ID)

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