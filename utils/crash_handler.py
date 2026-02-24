import sys
import traceback
from datetime import datetime
from utils.logger import setup_logger
from utils.telegram import TelegramNotifier
from config.secrets import get_telegram_credentials
from utils.runtime_state import STATE

logger = setup_logger()


class CrashHandler:
    def __init__(self):
        tg = get_telegram_credentials()
        self.notifier = TelegramNotifier(tg.token, tg.chat_id)
    
    def setup_global_handler(self):
        """Install global exception handler"""
        sys.excepthook = self.handle_exception
    
    def handle_exception(self, exc_type, exc_value, exc_traceback):
        """Handle uncaught exceptions"""
        if issubclass(exc_type, KeyboardInterrupt):
            # Don't send notification for manual interruption
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return
        
        error_msg = f"BOT CRASH\nType: {exc_type.__name__}\nMessage: {str(exc_value)}\nTime: {datetime.utcnow()}\n"
        
        # Add traceback details
        tb_lines = traceback.format_exception(exc_type, exc_value, exc_traceback)
        error_msg += f"Traceback:\n{''.join(tb_lines[-5:])}"  # Last 5 lines
        
        logger.error("CRASH DETECTED")
        logger.error(error_msg)
        try:
            STATE.set_error(f"CRASH: {exc_type.__name__}: {str(exc_value)}")
        except Exception:
            pass
        
        if self.notifier:
            try:
                # Send summary (Telegram has message limits)
                summary = f"BOT CRASH\n"
                summary += f"Error: {exc_type.__name__}\n"
                summary += f"Message: {str(exc_value)[:200]}\n"
                summary += f"Time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')}"
                
                self.notifier.send(summary)
            except:
                logger.error("Failed to send crash notification")
        
        sys.__excepthook__(exc_type, exc_value, exc_traceback)