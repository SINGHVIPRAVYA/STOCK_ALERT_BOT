import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🤖 AI STOCK ASSISTANT

✅ Bot Successfully Started

🚀 Coming Soon Features

📈 Volume Analysis
📊 Bulk Deal
📊 Block Deal
📄 NSE/BSE PDF Alerts
📰 AI Hindi News Summary
📉 Support & Resistance
📈 20/50/100/200 DMA
📐 Chart Pattern Detection
🔥 Breakout Alerts
🌅 Morning Report
🌙 Night Report
⭐ Unlimited Watchlist
⚙️ Settings Menu

Bot is Online ✅
"""
    await update.message.reply_text(text)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    print("Bot Started Successfully...")

    app.run_polling()

if __name__ == "__main__":
    main()
