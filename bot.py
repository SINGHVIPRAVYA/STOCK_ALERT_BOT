import os
import logging
import threading
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf
import pandas as pd  # DMA कैलकुलेट करने के लिए
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from config import BOT_TOKEN

# लॉगर सेटअप
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)

DB_NAME = "watchlist.db"

# === 1. SQLite डेटाबेस सेटअप ===
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

# === 2. Render Health Check डमी सर्वर ===
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

# === 3. टेलीग्राम कमांड्स ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🤖 **AI STOCK ASSISTANT v2.0**

बोट पूरी तरह अपग्रेड हो चुका है! नीचे दिए गए बटन्स का उपयोग करें। 🚀
"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Screener Analysis", callback_data='help_analysis'),
            InlineKeyboardButton("⚡ Technicals (DMA)", callback_data='help_technicals')
        ],
        [
            InlineKeyboardButton("➕ Add Stock", callback_data='help_add'),
            InlineKeyboardButton("❌ Remove Stock", callback_data='help_remove')
        ],
        [
            InlineKeyboardButton("📋 Watchlist देखें", callback_data='view_wl')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# === 4. एडवांस स्टॉक एनालिसिस इंजन (Screener.in स्टाइल + 50/200 DMA) ===
async def analyze_stock_data(update: Update, ticker: str, user_id: int):
    # स्टॉक का सही सिंबल सेट करें (.NS भारतीय मार्केट के लिए)
    symbol = f"{ticker}.NS" if not (ticker.endswith(".NS") or ticker.endswith(".BO")) else ticker
    msg_source = update.callback_query.message if update.callback_query else update.message

    await msg_source.reply_text(f"⏳ **{ticker}** का 'Screener' एनालिसिस और DMA डेटा निकाला जा रहा है...")

    try:
        # yfinance से डेटा फेच करें
        stock = yf.Ticker(symbol)
        
        # 1. फंडामेंटल डेटा (Screener.in स्टाइल)
        info = stock.info
        company_name = info.get('longName', ticker)
        market_cap_raw = info.get('marketCap', 0)
        market_cap_cr = market_cap_raw / 10000000 if market_cap_raw else 0  # Crores में बदलने के लिए
        pe_ratio = info.get('trailingPE', "N/A")
        if isinstance(pe_ratio, (int, float)):
            pe_ratio = f"{pe_ratio:.2f}"
        div_yield = info.get('dividendYield', 0) * 100 if info.get('dividendYield') else 0
        debt_to_equity = info.get('debtToEquity', "N/A")
        if isinstance(debt_to_equity, (int, float)):
            debt_to_equity = f"{debt_to_equity / 100:.2f}" # Ratio फॉर्मेट में
        
        # 2. टेक्निकल डेटा (50 DMA & 200 DMA कैलकुलेशन)
        hist = stock.history(period="1y") # 1 साल का डेटा DMA निकालने के लिए
        if hist.empty or len(hist) < 200:
            dma_50_str = "कम डेटा है"
            dma_200_str = "कम डेटा है"
            signal = "N/A"
        else:
            close_prices = hist['Close']
            dma_50 = close_prices.rolling(window=50).mean().iloc[-1]
            dma_200 = close_prices.rolling(window=200).mean().iloc[-1]
            current_price = close_prices.iloc[-1]
            
            dma_50_str = f"₹{dma_50:.2f}"
            dma_200_str = f"₹{dma_200:.2f}"
            
            # Crossover/Trend Signal
            if current_price > dma_50 and dma_50 > dma_200:
                signal = "🟢 **Super Bullish (Golden Trend)**"
            elif current_price < dma_50 and dma_50 < dma_200:
                signal = "🔴 **Bearish Trend**"
            else:
                signal = "🟡 **Consolidating (Sideways)**"

        # 3. TradingView चार्ट लिंक बनाना
        tradingview_link = f"https://www.tradingview.com/symbols/NSE-{ticker}/"

        # 4. सुंदर रिप्लाई मैसेज तैयार करना
        response_text = f"📊 **SCREENER ANALYSIS: {company_name}**\n"
        response_text += f"━━━━━━━━━━━━━━━━━━━━\n"
        response_text += f"💰 **करंट प्राइस:** ₹{close_prices.iloc[-1]:.2f}\n"
        response_text += f"🏢 **मार्केट कैप:** ₹{market_cap_cr:,.2f} Cr\n"
        response_text += f"🎯 **स्टॉक P/E:** {pe_ratio}\n"
        response_text += f"💸 **डिविडेंड यील्ड:** {div_yield:.2f}%\n"
        response_text += f"⚖️ **Debt to Equity:** {debt_to_equity}\n"
        response_text += f"━━━━━━━━━━━━━━━━━━━━\n"
        response_text += f"📈 **50 DMA (Moving Average):** {dma_50_str}\n"
        response_text += f"📉 **200 DMA (Moving Average):** {dma_200_str}\n"
        response_text += f"⚡ **चार्ट सिग्नल:** {signal}\n"
        response_text += f"━━━━━━━━━━━━━━━━━━━━\n"
        response_text += f"🔗 [TradingView Live Chart Link]({tradingview_link})\n"

        await msg_source.reply_text(response_text, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        logging.error(f"Analysis Error: {e}")
        await msg_source.reply_text("❌ स्टॉक एनालिसिस करने में एरर आया। कृपया सही सिंबल चेक करें।")

# === 5. बटन और कमांड हैंडलर ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    if query.data == 'view_wl':
        await show_watchlist_logic(update, user_id)
    elif query.data == 'help_analysis':
        await query.message.reply_text("📊 **Screener-Style एनालिसिस के लिए:**\nटाइप करें: `/analyze STOCK` (उदा: `/analyze SBIN` या `/analyze RELIANCE`)", parse_mode="Markdown")
    elif query.data == 'help_technicals':
        await query.message.reply_text("⚡ **Technicals (50/200 DMA) देखने के लिए:**\nटाइप करें: `/analyze STOCK` लिखें। बोट आपको फंडामेंटल्स के साथ-साथ Moving Average और चार्ट सिग्नल एक साथ निकाल कर देगा!", parse_mode="Markdown")
    elif query.data == 'help_add':
        await query.message.reply_text("➕ **वॉचलिस्ट में जोड़ने के लिए:**\nटाइप करें: `/add STOCK` (उदा: `/add TATAMOTORS`)", parse_mode="Markdown")
    elif query.data == 'help_remove':
        await query.message.reply_text("❌ **वॉचलिस्ट से हटाने के लिए:**\nटाइप करें: `/remove STOCK` (उदा: `/remove SBIN`)", parse_mode="Markdown")

# कमांड हैंडलर्स
async def run_analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ कृपया स्टॉक का नाम लिखें। उदा: `/analyze SBIN`")
        return
    ticker = context.args[0].upper()
    await analyze_stock_data(update, ticker, update.effective_user.id)

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
        await update.message.reply_text(f"✅ **{ticker}** आपकी वॉचलिस्ट में जुड़ गया है। इसे देखने के लिए नीचे बटन दबाएं या `/watchlist` टाइप करें।", parse_mode="Markdown")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"ℹ️ {ticker} पहले से ही आपकी वॉचलिस्ट में है।")
    finally:
        conn.close()

async def view_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    await show_watchlist_logic(update, user_id)

async def show_watchlist_logic(update: Update, user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_symbol FROM watchlist WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

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

# === 6. मेन फंक्शन ===
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # कमांड्स रजिस्टर करें
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", run_analyze_command))
    app.add_handler(CommandHandler("add", add_to_watchlist))
    app.add_handler(CommandHandler("watchlist", view_watchlist))
    app.add_handler(CommandHandler("remove", remove_from_watchlist))
    
    # बटन्स क्लिक हैंडल करने के लिए
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Starting AI Stock Assistant v2.0...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
