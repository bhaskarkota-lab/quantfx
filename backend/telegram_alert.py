"""
QuantFX AI — Telegram Alert Sender
Sends messages to your Telegram bot chat.
"""

import os
import requests
from logger import logger
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"


def send_telegram_alert(message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a text message to your Telegram chat.
    Returns True on success, False on failure.
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials missing — alert not sent.")
        return False

    try:
        response = requests.post(
            f"{BASE_URL}/sendMessage",
            json={
                "chat_id":    CHAT_ID,
                "text":       message,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        if not response.ok:
            logger.warning(f"Telegram send failed: {response.text}")
            return False
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram request error: {e}")
        return False


def send_telegram_photo(image_path: str, caption: str = "") -> bool:
    """
    Send a local image file (e.g. a chart screenshot) to your chat.
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials missing — photo not sent.")
        return False

    try:
        with open(image_path, "rb") as photo:
            response = requests.post(
                f"{BASE_URL}/sendPhoto",
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"photo": photo},
                timeout=15,
            )
        if not response.ok:
            logger.warning(f"Telegram photo failed: {response.text}")
            return False
        return True

    except (requests.exceptions.RequestException, FileNotFoundError) as e:
        logger.error(f"Telegram photo error: {e}")
        return False


def send_telegram_document(file_path: str, caption: str = "") -> bool:
    """
    Send a local file (e.g. trading_bot.log) as a document attachment.
    """
    if not BOT_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials missing — document not sent.")
        return False

    try:
        with open(file_path, "rb") as doc:
            response = requests.post(
                f"{BASE_URL}/sendDocument",
                data={"chat_id": CHAT_ID, "caption": caption},
                files={"document": doc},
                timeout=20,
            )
        if not response.ok:
            logger.warning(f"Telegram document failed: {response.text}")
            return False
        return True

    except (requests.exceptions.RequestException, FileNotFoundError) as e:
        logger.error(f"Telegram document error: {e}")
        return False


# ─────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    ok = send_telegram_alert(
        "<b>QuantFX AI</b> — Telegram connection test ✅\n"
        "If you see this, your bot token and chat ID are working correctly."
    )
    print("Message sent:", ok)
