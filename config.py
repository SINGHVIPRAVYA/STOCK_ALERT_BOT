import os

# Telegram Bot Token
BOT_TOKEN = os.getenv("BOT_TOKEN")

# Check if token is available
if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN environment variable not found!")

# Optional Settings
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
DEBUG = os.getenv("DEBUG", "False").lower() == "true"
