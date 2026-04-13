import logging
import os

os.makedirs("logs", exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("logs/trading_bot.log"),
        logging.StreamHandler(),
    ],
)

logger = logging.getLogger("quantfx")
