import os
import logging
import threading
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from config import BOT_TOKEN

# लॉगर सेटअप
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

DB_NAME = "watchlist.db"

# === SQLite डेटाबेस सेटअप ===
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS watchlist (
            user_id INTEGER,
            stock_symbol TEXT,
            PRIMARY KEY (user_id, stock_symbol)
        )
    ''')
    conn.commit()
    conn.close()

# === Render Health Check डमी सर्वर ===
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running flawlessly on Render!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

# === टेलीग्राम बोट कमांड्स ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🤖 **AI STOCK ASSISTANT (FREE)**

बोट पूरी तरह चालू है! नीचे दिए गए बटन्स का यूज़ करें या डायरेक्ट कमांड टाइप करें। 🔥
"""
    # यहाँ हम इनलाइन बटन का लेआउट बना रहे हैं
    keyboard = [
        [InlineKeyboardButton("📋 अपनी वॉचलिस्ट देखें (Watchlist)", callback_data='view_wl')],
        [InlineKeyboardButton("📈 लाइव भाव कैसे देखें?", callback_data='help_price')],
        [InlineKeyboardButton("➕ स्टॉक कैसे जोड़ें?", callback_data='help_add')],
        [InlineKeyboardButton("❌ स्टॉक कैसे हटाएं?", callback_data='help_remove')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# === बटन क्लिक को हैंडल करने का लॉजिक ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer() # बटन क्लिक को एकनॉलेज करना ज़रूरी है ताकि लोडिंग हटे
    
    user_id = update.effective_user.id
    
    if query.data == 'view_wl':
        # बटन दबाते ही सीधे वॉचलिस्ट डेटाबेस से निकाल कर दिखाएगा
        await show_watchlist_logic(update, user_id)
    elif query.data == 'help_price':
        await query.message.reply_text("📈 **लाइव भाव देखने के लिए:**\nटाइप करें: `/price STOCK` (उदा: `/price SBIN`)", parse_mode="Markdown")
    elif query.data == 'help_add':
        await query.message.reply_text("➕ **वॉचलिस्ट में जोड़ने के लिए:**\nटाइप करें: `/add STOCK` (उदा: `/add TATAMOTORS`)", parse_mode="Markdown")
    elif query.data == 'help_remove':
        await query.message.reply_text("❌ **वॉचलिस्ट से हटाने के लिए:**\nटाइप करें: `/remove STOCK` (उदा: `/remove SBIN`)", parse_mode="Markdown")

# लाइव भाव देखने का लॉजिक
async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ कृपया स्टॉक का नाम लिखें। उदा: `/price SBIN`")
        return
    ticker = context.args[0].upper()
    symbol = f"{ticker}.NS" if not (ticker.endswith(".NS") or ticker.endswith(".BO")) else ticker
    
    await update.message.reply_text(f"🔍 {ticker} का भाव निकाला जा रहा है...")
    try:
        stock = yf.Ticker(symbol)
        todays_data = stock.history(period="1d")
        if todays_data.empty:
            await update.message.reply_text(f"❌ '{ticker}' का डेटा नहीं मिला। सही सिंबल डालें।")
            return
        live_price = todays_data['Close'].iloc[-1]
        await update.message.reply_text(f"📊 **{ticker}**\n💰 **लाइव भाव:** ₹{live_price:.2f}", parse_mode="Markdown")
    except:
        await update.message.reply_text("❌ डेटा फेच करने में एरर आया।")

# वॉचलिस्ट में स्टॉक जोड़ने का लॉजिक
async def add_to_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ कृपया स्टॉक नाम दें। उदा: `/add SBIN`")
        return
    user_id = update.effective_user.id
    ticker = context.args[0].upper()
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO watchlist (user_id, stock_symbol) VALUES (?, ?)", (user_id, ticker))
        conn.commit()
        await update.message.reply_text(f"✅ **{ticker}** को आपकी वॉचलिस्ट में जोड़ दिया गया है।", parse_mode="Markdown")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"ℹ️ {ticker} पहले से ही आपकी वॉचलिस्ट में है।")
    finally:
        conn.close()

# कमांड के ज़रिए वॉचलिस्ट देखना
async def view_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await show_watchlist_logic(update, user_id)

# कॉमन फंक्शन जो बटन और कमांड दोनों जगह काम करेगा
async def show_watchlist_logic(update: Update, user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_symbol FROM watchlist WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    # अगर बटन से रिक्वेस्ट आई है तो query.message यूज़ होगा, नहीं तो normal message
    msg_source = update.callback_query.message if update.callback_query else update.message

    if not rows:
        await msg_source.reply_text("📋 आपकी वॉचलिस्ट अभी खाली है। स्टॉक जोड़ने के लिए `/add STOCK_NAME` टाइप करें।")
        return

    await msg_source.reply_text("🔄 वॉचलिस्ट के स्टॉक्स का लाइव भाव निकाला जा रहा है...")
    
    response_text = "📋 **आपकी पर्सनल वॉचलिस्ट:**\n\n"
    for row in rows:
        ticker = row[0]
        symbol = f"{ticker}.NS" if not (ticker.endswith(".NS") or ticker.endswith(".BO")) else ticker
        try:
            stock = yf.Ticker(symbol)
            todays_data = stock.history(period="1d")
            price_str = f"₹{todays_data['Close'].iloc[-1]:.2f}" if not todays_data.empty else "N/A"
        except:
            price_str = "Error"
        response_text += f"🔹 **{ticker}**: {price_str}\n"

    await msg_source.reply_text(response_text, parse_mode="Markdown")

# वॉचलिस्ट से स्टॉक हटाने का लॉजिक
async def remove_from_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ कृपया स्टॉक नाम दें। उदा: `/remove SBIN`")
        return
    user_id = update.effective_user.id
    ticker = context.args[0].upper()

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM watchlist WHERE user_id = ? AND stock_symbol = ?", (user_id, ticker))
    changes = conn.total_changes
    conn.commit()
    conn.close()

    if changes > 0:
        await update.message.reply_text(f"❌ **{ticker}** को वॉचलिस्ट से हटा दिया गया है।", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"ℹ️ {ticker} आपकी वॉचलिस्ट में नहीं मिला।")

# === मेन फंक्शन ===
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # कमांड्स रजिस्टर्ड
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("price", get_price))
    app.add_handler(CommandHandler("add", add_to_watchlist))
    app.add_handler(CommandHandler("watchlist", view_watchlist))
    app.add_handler(CommandHandler("remove", remove_from_watchlist))
    
    # बटन्स के क्लिक को सुनने के लिए Handler जोड़ा
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Starting Telegram Bot with Interactive Buttons...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
