from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = "8875474288:AAHrwzJKbskhDABeH5P4x8Eh0VOI13Iy_X0"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✅ STOCK ALERT BOT START HO GAYA.\n\n"
        "Ab jaldi hi Volume, News, PDF, Bulk Deal aur Technical Alerts add honge."
    )

app = ApplicationBuilder().token(BOT_TOKEN).build()

app.add_handler(CommandHandler("start", start))

app.run_polling()
