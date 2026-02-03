import requests
from datetime import datetime
import time


class TelegramNotifier:

    def __init__(self, token, chat_id):
        self.token = token
        self.chat_id = chat_id
        self.url = f"https://api.telegram.org/bot{token}/sendMessage"

    def _send_raw(self, message):
        """Internal send method without retry logic"""
        payload = {
            "chat_id": self.chat_id,
            "text": f"[{datetime.utcnow()}]\n{message}"
        }

        try:
            response = requests.post(self.url, data=payload, timeout=30)
            response.raise_for_status()  # Raise exception for HTTP errors
            return True
        except Exception as e:
            print("Telegram send failed:", e)
            return False

    def send(self, message, max_retries=3):
        """Send message with retry logic"""
        for attempt in range(max_retries):
            if self._send_raw(message):
                return True
            
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1, 2, 4 seconds
                print(f"Telegram attempt {attempt + 1} failed, retrying in {wait_time}s...")
                time.sleep(wait_time)
        
        print(f"Telegram failed after {max_retries} attempts")
        return False