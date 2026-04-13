"""
QuantFX AI — Telegram Command Listener
Polls for new messages and returns recognised bot commands.
"""

import os
import requests
from logger import logger
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

BASE_URL  = f"https://api.telegram.org/bot{BOT_TOKEN}"

# All commands the bot will act on
SUPPORTED_COMMANDS = {
    "/startbot",
    "/stopbot",
    "/pause1h",
    "/status",
    "/balance",
    "/trades",
    "/signal",
    "/pairs",
    "/closeall",
    "/report",
    "/help",
}

# Tracks the last processed update so we never handle the same message twice
_last_update_id: int | None = None


def get_latest_command() -> str | None:
    """
    Poll Telegram for new messages.
    Returns the first recognised command found, or None.
    Only processes messages from the configured CHAT_ID (security guard).
    """
    global _last_update_id

    if not BOT_TOKEN or not CHAT_ID:
        return None

    params: dict = {"timeout": 5}
    if _last_update_id is not None:
        params["offset"] = _last_update_id + 1

    try:
        response = requests.get(
            f"{BASE_URL}/getUpdates",
            params=params,
            timeout=10,
        )
        if not response.ok:
            logger.warning(f"Telegram getUpdates failed: {response.text}")
            return None

        updates = response.json().get("result", [])

    except requests.exceptions.RequestException as e:
        logger.error(f"Telegram polling error: {e}")
        return None

    for update in updates:
        update_id = update.get("update_id", 0)

        # Always advance the offset pointer, even for ignored messages
        if _last_update_id is None or update_id > _last_update_id:
            _last_update_id = update_id

        message = update.get("message", {})

        # Security: only accept messages from your own chat
        incoming_chat = str(message.get("chat", {}).get("id", ""))
        if incoming_chat != str(CHAT_ID):
            continue

        text = message.get("text", "").strip().lower()

        # Strip bot username suffix e.g. /startbot@MyBotName
        if "@" in text:
            text = text.split("@")[0]

        if text in SUPPORTED_COMMANDS:
            logger.info(f"Telegram command received: {text}")
            return text

    return None


def get_all_pending_commands() -> list[str]:
    """
    Returns every pending recognised command in order.
    Useful if multiple commands arrive between polling cycles.
    """
    global _last_update_id

    if not BOT_TOKEN or not CHAT_ID:
        return []

    params: dict = {"timeout": 5}
    if _last_update_id is not None:
        params["offset"] = _last_update_id + 1

    try:
        response = requests.get(
            f"{BASE_URL}/getUpdates",
            params=params,
            timeout=10,
        )
        if not response.ok:
            return []

        updates = response.json().get("result", [])

    except requests.exceptions.RequestException:
        return []

    commands = []

    for update in updates:
        update_id = update.get("update_id", 0)
        if _last_update_id is None or update_id > _last_update_id:
            _last_update_id = update_id

        message = update.get("message", {})
        incoming_chat = str(message.get("chat", {}).get("id", ""))
        if incoming_chat != str(CHAT_ID):
            continue

        text = message.get("text", "").strip().lower()
        if "@" in text:
            text = text.split("@")[0]

        if text in SUPPORTED_COMMANDS:
            commands.append(text)

    return commands


def set_bot_commands() -> bool:
    """
    Register the command list with Telegram so users see
    autocomplete suggestions when they type / in the chat.
    Call this once at startup.
    """
    command_descriptions = [
        {"command": "startbot",  "description": "Resume automatic trading"},
        {"command": "stopbot",   "description": "Stop automatic trading"},
        {"command": "pause1h",   "description": "Pause trading for 1 hour"},
        {"command": "status",    "description": "Show bot status and latest signal"},
        {"command": "balance",   "description": "Show account balance and equity"},
        {"command": "trades",    "description": "List open positions"},
        {"command": "signal",    "description": "Show latest AI signal"},
        {"command": "pairs",     "description": "Scan all currency pairs"},
        {"command": "closeall",  "description": "Emergency close all open trades"},
        {"command": "report",    "description": "Full daily performance report"},
        {"command": "help",      "description": "Show all commands"},
    ]

    try:
        response = requests.post(
            f"{BASE_URL}/setMyCommands",
            json={"commands": command_descriptions},
            timeout=10,
        )
        ok = response.ok
        if ok:
            logger.info("Telegram command menu registered.")
        else:
            logger.warning(f"setMyCommands failed: {response.text}")
        return ok

    except requests.exceptions.RequestException as e:
        logger.error(f"setMyCommands error: {e}")
        return False


# ─────────────────────────────────────────────
# Quick test
# ─────────────────────────────────────────────

if __name__ == "__main__":
    print("Registering command menu with Telegram...")
    set_bot_commands()

    print("Polling for one command (send /status in your Telegram chat)...")
    import time
    for _ in range(10):
        cmd = get_latest_command()
        if cmd:
            print(f"Received command: {cmd}")
            break
        time.sleep(2)
    else:
        print("No command received within 20 seconds.")
