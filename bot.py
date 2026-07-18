import os
import logging
import threading
import sqlite3
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from config import BOT_TOKEN

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
DB_NAME = "watchlist.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS watchlist (user_id INTEGER, stock_symbol TEXT, PRIMARY KEY (user_id, stock_symbol))')
    conn.commit()
    conn.close()

def resolve_isin_to_ticker(symbol_or_isin):
    target = symbol_or_isin.strip().upper()
    if len(target) == 12 and target.startswith("INE"):
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={target}&quotesCount=3&newsCount=0"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            res = requests.get(url, headers=headers, timeout=5)
            if res.status_code == 200:
                data = res.json()
                quotes = data.get("quotes", [])
                if quotes:
                    for q in quotes:
                        sym = q.get("symbol", "")
                        if sym.endswith(".NS") or sym.endswith(".BO"):
                            # प्रो-फिन का स्पेशल ऑटो-करेक्शन पैच
                            if "PROFINC.BO" in sym: return "511557.BO"
                            return sym
                    fallback = quotes[0].get("symbol", "")
                    if "PROFINC.BO" in fallback: return "511557.BO"
                    return fallback
        except Exception as e:
            logging.error(f"ISIN Error: {e}")
    
    if "PROFINC.BO" in target: return "511557.BO"
    return target

class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is active with advanced ISIN & Name resolving feature.")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🤖 **AI STOCK ASSISTANT v3.1 (Smart Watchlist)**\n\nअब आपकी वॉचलिस्ट टिकर कोड की जगह सीधे **कंपनी के नाम** और वीकडे/वीकेंड सेफ लाइव रेट के साथ दिखेगी! 🚀"
    keyboard = [
        [InlineKeyboardButton("📊 Screener Analysis", callback_data='help_analysis'), InlineKeyboardButton("⚡ Technicals (DMA)", callback_data='help_technicals')],
        [InlineKeyboardButton("➕ Add Stock / ISIN", callback_data='help_add'), InlineKeyboardButton("❌ Remove Stock", callback_data='help_remove')],
        [InlineKeyboardButton("📋 Watchlist देखें", callback_data='view_wl')]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def analyze_stock_data(update: Update, ticker: str, user_id: int):
    msg = update.callback_query.message if update.callback_query else update.message
    resolved = resolve_isin_to_ticker(ticker)
    symbol = f"{resolved}.NS" if not (resolved.endswith(".NS") or resolved.endswith(".BO")) else resolved
    
    await msg.reply_text(f"⏳ **{resolved}** का एडवांस Screener डेटा निकाला जा रहा है...")
    try:
        stock = yf.Ticker(symbol)
        info = stock.info
        name = info.get('longName', resolved)
        mcap = (info.get('marketCap', 0) or 0) / 10000000
        pe = info.get('trailingPE', "N/A")
        if isinstance(pe, (int, float)): pe = f"{pe:.2f}"
        div_yield = (info.get('dividendYield', 0) or 0) * 100
        debt_to_equity = info.get('debtToEquity', "N/A")
        if isinstance(debt_to_equity, (int, float)): debt_to_equity = f"{debt_to_equity / 100:.2f}"

        hist = stock.history(period="5d")  # वीकेंड सेफ विंडो
        if hist.empty or len(hist) < 1:
            d50, d200, sig, price = "N/A", "N/A", "N/A", 0.0
        else:
            cp = hist['Close']
            price = cp.iloc[-1]
            
            # पूर्ण 1 साल का डेटा सिर्फ DMA के लिए निकालें
            full_hist = stock.history(period="1y")
            if not full_hist.empty and len(full_hist) >= 200:
                f_cp = full_hist['Close']
                d50 = f_cp.rolling(50).mean().iloc[-1]
                d200 = f_cp.rolling(200).mean().iloc[-1]
                d50_str, d200_str = f"₹{d50:.2f}", f"₹{d200:.2f}"
                sig = "🟢 **Super Bullish**" if price > d50 > d200 else "🔴 **Bearish**" if price < d50 < d200 else "🟡 **Sideways**"
            else:
                d50_str, d200_str, sig = "कम डेटा है", "कम डेटा है", "N/A"
        
        clean_tv = resolved.replace(".BO", "").replace(".NS", "")
        tradingview_link = f"https://www.tradingview.com/symbols/NSE-{clean_tv}/"

        res = f"📊 **SCREENER ANALYSIS: {name}**\n"
        res += f"━━━━━━━━━━━━━━━━━━━━\n"
        res += f"💰 **करंट प्राइस:** ₹{price:.2f}\n"
        res += f"🏢 **मार्केट कैप:** ₹{mcap:,.2f} Cr\n"
        res += f"🎯 **स्टॉक P/E:** {pe}\n"
        res += f"💸 **डिविडेंड यील्ड:** {div_yield:.2f}%\n"
        res += f"⚖️ **Debt to Equity:** {debt_to_equity}\n"
        res += f"━━━━━━━━━━━━━━━━━━━━\n"
        res += f"📈 **50 DMA:** {d50_str}\n"
        res += f"📉 **200 DMA:** {d200_str}\n"
        res += f"⚡ **चार्ट सिग्नल:** {sig}\n"
        res += f"━━━━━━━━━━━━━━━━━━━━\n"
        res += f"🔗 [TradingView Live Chart]({tradingview_link})\n"
        await msg.reply_text(res, parse_mode="Markdown", disable_web_page_preview=True)
    except Exception as e:
        await msg.reply_text(f"❌ एनालिसिस एरर: सही सिंबल चेक करें।")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == 'view_wl': await show_watchlist_logic(update, update.effective_user.id)
    elif q.data == 'help_analysis': await q.message.reply_text("📊 टाइप करें: `/analyze STOCK_NAME` या `/analyze ISIN`")
    elif q.data == 'help_technicals': await q.message.reply_text("⚡ Technicals के लिए `/analyze` कमांड का उपयोग करें।")
    elif q.data == 'help_add': await q.message.reply_text("➕ टाइप करें: `/add STOCK_NAME` या सीधे `/add ISIN`")
    elif q.data == 'help_remove': await q.message.reply_text("❌ हटाने के लिए टाइप करें: `/remove STOCK_NAME`")

async def run_analyze_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    await analyze_stock_data(update, context.args[0].upper(), update.effective_user.id)

async def add_to_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    user_id = update.effective_user.id
    raw_input = context.args[0].upper()
    ticker = resolve_isin_to_ticker(raw_input)
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("INSERT INTO watchlist VALUES (?, ?)", (user_id, ticker))
        conn.commit()
        await update.message.reply_text(f"✅ **{ticker}** वॉचलिस्ट में ऐड हो गया है!", parse_mode="Markdown")
    except:
        await update.message.reply_text(f"ℹ️ {ticker} पहले से मौजूद है।")
    finally: conn.close()

async def show_watchlist_logic(update: Update, user_id: int):
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("SELECT stock_symbol FROM watchlist WHERE user_id = ?", (user_id,))
    rows = c.fetchall()
    conn.close()
    msg = update.callback_query.message if update.callback_query else update.message
    if not rows:
        await msg.reply_text("📋 आपकी वॉचलिस्ट अभी खाली है।")
        return
    
    await msg.reply_text("🔄 वॉचलिस्ट का लाइव भाव निकाला जा रहा है...")
    res = "📋 **आपकी पर्सनल वॉचलिस्ट:**\n\n"
    
    for r in rows:
        ticker = r[0]
        symbol = f"{ticker}.NS" if not (ticker.endswith(".NS") or ticker.endswith(".BO")) else ticker
        
        try:
            stock = yf.Ticker(symbol)
            info = stock.info
            # कोड की जगह कंपनी का असली नाम दिखाना
            company_name = info.get('longName', ticker)
            
            # वीकेंड/हॉलिडे सेफ प्राइस फेचिंग रणनीति
            price = info.get('currentPrice') or info.get('regularMarketPrice')
            if not price:
                hist = stock.history(period="5d")
                if not hist.empty: price = hist['Close'].iloc[-1]
            
            price_str = f"₹{price:.2f}" if price else "N/A"
            res += f"🔹 **{company_name}**: {price_str}\n"
        except:
            res += f"🔹 **{ticker}**: Error\n"
            
    await msg.reply_text(res, parse_mode="Markdown")

async def remove_from_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    user_id = update.effective_user.id
    ticker = context.args[0].upper()
    if "PROFINC.BO" in ticker: ticker = "511557.BO"
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    c.execute("DELETE FROM watchlist WHERE user_id = ? AND stock_symbol = ?", (user_id, ticker))
    changes = c.total_changes
    conn.commit()
    conn.close()
    if changes > 0: await update.message.reply_text(f"❌ **{ticker}** को हटा दिया गया है।")
    else: await update.message.reply_text("ℹ️ स्टॉक वॉचलिस्ट में नहीं मिला।")

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", run_analyze_command))
    app.add_handler(CommandHandler("add", add_to_watchlist))
    app.add_handler(CommandHandler("watchlist", view_watchlist := view_watchlist if 'view_watchlist' in locals() else (lambda u, c: show_watchlist_logic(u, u.effective_user.id))))
    app.add_handler(CommandHandler("remove", remove_from_watchlist))
    app.add_handler(CallbackQueryHandler(button_handler))
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
