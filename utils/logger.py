import logging
import os


def log_separator(logger, title="", char="=", width=60):
    """Add a visual separator line to logs for readability (ASCII-safe for Windows)"""
    if title:
        padding = (width - len(title) - 4) // 2
        line = char * padding + f"  {title}  " + char * padding
    else:
        line = char * width
    logger.info(line)

def setup_logger(name="trading_bot"):

    os.makedirs("logs", exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)

    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    )

    file_handler = logging.FileHandler("logs/live_trading.log")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
