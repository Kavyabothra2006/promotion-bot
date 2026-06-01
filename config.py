import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()

ADMIN_IDS = [
    int(x.strip())
    for x in os.getenv("ADMIN_IDS", "").split(",")
    if x.strip()
]

REQUIRED_CHAT_ID = os.getenv("REQUIRED_CHAT_ID", "").strip()
REQUIRED_CHAT_LINK = os.getenv("REQUIRED_CHAT_LINK", "").strip()

TIMEZONE_NAME = os.getenv("TIMEZONE_NAME", "Asia/Kolkata").strip()

WEBHOOK_URL = os.getenv("WEBHOOK_URL", "").strip()