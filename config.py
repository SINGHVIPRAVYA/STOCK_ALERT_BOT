import os

# Render के Environment Variables से टोकन उठाएगा
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("❌ BOT_TOKEN missing! Please add it in Render Environment Variables.")
