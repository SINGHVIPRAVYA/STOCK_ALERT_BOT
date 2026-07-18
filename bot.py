import os
import logging
import threading
import sqlite3
import requests  # ISIN कोड से Ticker ढूंढने के लिए आवश्यक लाइब्रेरी
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf
import pandas as pd
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

# === 2. ISIN से Ticker में कन्वर्ट करने का मैजिक इंजन ===
def resolve_isin_to_ticker(symbol_or_isin):
    target = symbol_or_isin.strip().upper()
    
    # भारतीय ISIN कोड हमेशा 12 अक्षरों के होते हैं और आम तौर पर INE से शुरू होते हैं
    if len(target) == 12 and target.startswith("INE"):
        logging.info(f"🔄 ISIN कोड डिटेक्ट हुआ: {target} | Yahoo Finance से Ticker खोजा जा रहा है...")
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={target}&quotesCount=3&newsCount=0"
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        }
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                quotes = data.get("quotes", [])
                if quotes:
                    # पहले NSE (.NS) या BSE (.BO) वाले सिंबल को प्राथमिकता दें
                    for q in quotes:
                        sym = q.get("symbol", "")
                        if sym.endswith(".NS") or sym.endswith(".BO"):
                            logging.info(f"✅ ISIN {target} का सही Ticker मिला: {sym}")
                            return sym
                    # अगर एक्सचेंज न मिले तो पहला मैच रिटर्न करें
                    fallback = quotes[0].get("symbol", "")
                    logging.info(f"⚠️ फॉलबैक Ticker मिला: {fallback}")
                    return fallback
        except Exception as e:
            logging.error(f"❌ ISIN रिज़ॉल्यूशन एरर: {e}")
    return target

# === 3. Render Health Check डमी सर्वर ===
class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is running flawlessly with ISIN Engine!")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

# === 4. टेलीग्राम कमांड्स ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = """
🤖 **AI STOCK ASSISTANT v3.0 (ISIN Enabled)**

बोट अब पूरी तरह स्मार्ट हो चुका है! अब आप स्टॉक का नाम, Ticker या सीधे **ISIN कोड** (जैसे: `INE733E01010`) डालकर भी स्टॉक ऐड या एनालाइज कर सकते हैं! 🚀
"""
    keyboard = [
        [
            InlineKeyboardButton("📊 Screener Analysis", callback_data='help_analysis'),
            InlineKeyboardButton("⚡ Technicals (DMA)", callback_data='help_technicals')
        ],
        [
            InlineKeyboardButton("➕ Add Stock / ISIN", callback_data='help_add'),
            InlineKeyboardButton("❌ Remove Stock", callback_data='help_remove')
        ],
        [
            InlineKeyboardButton("📋 Watchlist देखें", callback_data='view_wl')
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, reply_markup=reply_markup, parse_mode="Markdown")

# === 5. एडवांस स्टॉक एनालिसिस इंजन ===
async def analyze_stock_data(update: Update, ticker: str, user_id: int):
    msg_source = update.callback_query.message if update.callback_query else update.message
    
    # इनपुट अगर ISIN है तो उसे Ticker में बदलें
    resolved_ticker = resolve_isin_to_ticker(ticker)
    symbol = f"{resolved_ticker}.NS" if not (resolved_ticker.endswith(".NS") or resolved_ticker.endswith(".BO")) else resolved_ticker

    await msg_source.reply_text(f"⏳ **{resolved_ticker}** का डेटा निकाला जा रहा है...")

    try:
        stock = yf.Ticker(symbol)
        
        # फंडामेंटल डेटा
        info = stock.info
        company_name = info.get('longName', resolved_ticker)
        market_cap_raw = info.get('marketCap', 0)
        market_cap_cr = (market_cap_raw or 0) / 10000000
        pe_ratio = info.get('trailingPE', "N/A")
        if isinstance(pe_ratio, (int, float)):
            pe_ratio = f"{pe_ratio:.2f}"
        div_yield = (info.get('dividendYield', 0) or 0) * 100
        debt_to_equity = info.get('debtToEquity', "N/A")
        if isinstance(debt_to_equity, (int, float)):
            debt_to_equity = f"{debt_to_equity / 100:.2f}"
        
        # टेक्निकल डेटा (50/200 DMA)
        hist = stock.history(period="1y")
        if hist.empty or len(hist) < 200:
            dma_50_str, dma_200_str, signal, current_p = "N/A", "N/A", "N/A", 0.0
        else:
            close_prices = hist['Close']
            dma_50 = close_prices.rolling(window=50).mean().iloc[-1]
            dma_200 = close_prices.rolling(window=200).mean().iloc[-1]
            current_p = close_prices.iloc[-1]
            
            dma_50_str = f"₹{dma_50:.2f}"
            dma_200_str = f"₹{dma_200:.2f}"
            
            if current_p > dma_50 and dma_50 > dma_200:
                signal = "🟢 **Super Bullish (Golden Trend)**"
            elif current_p < dma_50 and dma_50 < dma_200:
                signal = "🔴 **Bearish Trend**"
            else:
                signal = "🟡 **Consolidating (Sideways)**"

        # TradingView लिंक बनाना
        clean_tv = resolved_ticker.replace(".BO", "").replace(".NS", "")
        tradingview_link = f"https://www.tradingview.com/symbols/NSE-{clean_tv}/"

        response_text = f"📊 **SCREENER ANALYSIS: {company_name}**\n"
        response_text += f"━━━━━━━━━━━━━━━━━━━━\n"
        response_text += f"💰 **करंट प्राइस:** ₹{current_p:.2f}\n"
        response_text += f"🏢 **मार्केट कैप:** ₹{market_cap_cr:,.2f} Cr\n"
        response_text += f"🎯 **स्टॉक P/E:** {pe_ratio}\n"
        response_text += f"💸 **डिविडेंड यील्ड:** {div_yield:.2f}%\n"
        response_text += f"⚖️ **Debt to Equity:** {debt_to_equity}\n"
        response_text += f"━━━━━━━━━━━━━━━━━━━━\n"
        response_text += f"📈 **50 DMA:** {dma_50_str}\n"
        response_text += f"📉 **200 DMA:** {dma_200_str}\n"
        response_text += f"⚡ **चार्ट सिग्नल:** {signal}\n"
        response_text += f"━━━━━━━━━━━━━━━━━━━━\n"
        response_text += f"🔗 [TradingView Live Chart Link]({tradingview_link})\n"

        await msg_source.reply_text(response_text, parse_mode="Markdown", disable_web_page_preview=True)

    except Exception as e:
        logging.error(f"Analysis Error: {e}")
        await msg_source.reply_text("❌ स्टॉक एनालिसिस करने में एरर आया। कृपया सही सिंबल या ISIN चेक करें।")

# === 6. बटन और कमांड हैंडलर ===
async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    
    if query.data == 'view_wl':
        await show_watchlist_logic(update, user_id)
    elif query.data == 'help_analysis':
        await query.message.reply_text("📊 **एनालिसिस के लिए:**\nटाइप करें: `/analyze STOCK` या सीधे ISIN कोड डालें जैसे: `/analyze INE733E01010`", parse_mode="Markdown")
    elif query.data == 'help_technicals':
        await query.message.reply_text("⚡ **Technicals (50/200 DMA) के लिए:**\n`/analyze` कमांड के आगे स्टॉक नाम या ISIN कोड लिखें।", parse_mode="Markdown")
    elif query.data == 'help_add':
        await query.message.reply_text("➕ **जोड़ने के लिए:**\nटाइप करें: `/add STOCK` या सीधे `/add ISIN_CODE`", parse_mode="Markdown")
    elif query.data == 'help_remove':
        await query.message.reply_text("❌ **हटाने के लिए:**\nटाइप करें: `/remove STOCK` (उदा: `/remove SBIN`)", parse_mode="Markdown")

async def run_analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ कृपया स्टॉक नाम या ISIN लिखें। उदा: `/analyze INE733E01010`")
        return
    ticker = context.args[0].upper()
    await analyze_stock_data(update, ticker, update.effective_user.id)

async def add_to_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("❌ कृपया स्टॉक या ISIN कोड दें। उदा: `/add INE732K01027`")
        return
    user_id = update.effective_user.id
    input_data = context.args[0].upper()
    
    # ISIN को बैकग्राउंड में ऑटोमैटिकली कन्वर्ट करें
    ticker = resolve_isin_to_ticker(input_data)
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO watchlist (user_id, stock_symbol) VALUES (?, ?)", (user_id, ticker))
        conn.commit()
        await update.message.reply_text(f"✅ **{ticker}** आपकी वॉचलिस्ट में सफ़लतापूर्वक जुड़ गया है! 🚀", parse_mode="Markdown")
    except sqlite3.IntegrityError:
        await update.message.reply_text(f"ℹ️ {ticker} पहले से ही आपकी वॉचलिस्ट में है।")
    finally:
        conn.close()

async def view_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await show_watchlist_logic(update, update.effective_user.id)

async def show_watchlist_logic(update: Update, user_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT stock_symbol FROM watchlist WHERE user_id = ?", (user_id,))
    rows = cursor.fetchall()
    conn.close()

    msg_source = update.callback_query.message if update.callback_query else update.message

    if not rows:
        await msg_source.reply_text("📋 आपकी वॉचलिस्ट अभी खाली है।")
        return

    await msg_source.reply_text("🔄 वॉचलिस्ट का लाइव भाव निकाला जा रहा है...")
    
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
        await update.message.reply_text("❌ कृपया स्टॉक नाम दें।")
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

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", run_analyze_command))
    app.add_handler(CommandHandler("add", add_to_watchlist))
    app.add_handler(CommandHandler("watchlist", view_watchlist))
    app.add_handler(CommandHandler("remove", remove_from_watchlist))
    app.add_handler(CallbackQueryHandler(button_handler))

    print("🤖 Starting AI Stock Assistant v3.0 (ISIN Enabled)...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
