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

# === 1. Yahoo Finance ब्लॉकिंग से बचने के लिए स्पेशल ब्राउज़र सेशन ===
def get_browser_session():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5"
    })
    return session

# === 2. मास्टर मैनुअल ISIN मैपिंग ===
MANUAL_ISIN_MAPPING = {
    "INE732K01027": "511557.BO",      # प्रो-फिन कैपिटल
    "INE0PQ601019": "530299.BO",      # बालाजी फॉस्फेट्स
}

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('CREATE TABLE IF NOT EXISTS watchlist (user_id INTEGER, stock_symbol TEXT, PRIMARY KEY (user_id, stock_symbol))')
    conn.commit()
    conn.close()

def resolve_isin_to_ticker(symbol_or_isin):
    target = symbol_or_isin.strip().upper()
    
    if target in MANUAL_ISIN_MAPPING:
        return MANUAL_ISIN_MAPPING[target]
        
    if len(target) == 12 and target.startswith("INE"):
        url = f"https://query2.finance.yahoo.com/v1/finance/search?q={target}&quotesCount=3&newsCount=0"
        try:
            res = get_browser_session().get(url, timeout=5)
            if res.status_code == 200:
                data = res.json()
                quotes = data.get("quotes", [])
                if quotes:
                    for q in quotes:
                        sym = q.get("symbol", "")
                        if sym.endswith(".NS") or sym.endswith(".BO"):
                            if "PROFINC.BO" in sym: return "511557.BO"
                            return sym
                    fallback = quotes[0].get("symbol", "")
                    if "PROFINC.BO" in fallback: return "511557.BO"
                    return fallback
        except Exception as e:
            logging.error(f"ISIN Error: {e}")
    return target

class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is online with anti-block headers.")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🤖 **AI STOCK ASSISTANT v3.4 (Anti-Block Version)**\n\nयाहू फाइनेंस ब्लॉकिंग सिस्टम को पूरी तरह बाईपास कर दिया गया है। अब SBIN से लेकर पेनी स्टॉक्स तक सब ट्रैक होंगे! 🚀"
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
    
    await msg.reply_text(f"⏳ **{resolved}** का सुरक्षित डेटा निकाला जा रहा है...")
    try:
        # सेफ सेशन के साथ टिकर ऑब्जेक्ट बनाना
        session = get_browser_session()
        stock = yf.Ticker(symbol, session=session)
        
        hist = stock.history(period="5d")
        if hist.empty:
            await msg.reply_text("❌ इस स्टॉक का लाइव डेटा याहू पर ब्लॉक है या उपलब्ध नहीं है।")
            return
        price = hist['Close'].iloc[-1]
        
        try:
            info = stock.info
            name = info.get('longName', resolved)
            mcap = (info.get('marketCap', 0) or 0) / 10000000
            pe = info.get('trailingPE', "N/A")
            if isinstance(pe, (int, float)): pe = f"{pe:.2f}"
            div_yield = (info.get('dividendYield', 0) or 0) * 100
            debt_to_equity = info.get('debtToEquity', "N/A")
            if isinstance(debt_to_equity, (int, float)): debt_to_equity = f"{debt_to_equity / 100:.2f}"
        except:
            name, mcap, pe, div_yield, debt_to_equity = resolved, 0.0, "N/A", 0.0, "N/A"

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
        tradingview_link = f"https://www.tradingview.com/symbols/BSE-{clean_tv}/" if resolved.endswith(".BO") else f"https://www.tradingview.com/symbols/NSE-{clean_tv}/"

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
        await msg.reply_text(f"❌ एनालिसिस एरर: कृपया सही सिंबल चेक करें।")

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == 'view_wl': await show_watchlist_logic(update, update.effective_user.id)
    elif q.data == 'help_analysis': await q.message.reply_text("📊 टाइप करें: `/analyze STOCK_NAME`")
    elif q.data == 'help_technicals': await q.message.reply_text("⚡ Technicals के लिए `/analyze` का उपयोग करें।")
    elif q.data == 'help_add': await q.message.reply_text("➕ टाइप करें: `/add STOCK_NAME` या सीधे ISIN")
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
    
    session = get_browser_session()
    for r in rows:
        db_ticker = r[0]
        resolved_ticker = resolve_isin_to_ticker(db_ticker)
        symbol = f"{resolved_ticker}.NS" if not (resolved_ticker.endswith(".NS") or resolved_ticker.endswith(".BO")) else resolved_ticker
        
        try:
            # यहाँ भी सुरक्षित सेशन पास किया गया है
            stock = yf.Ticker(symbol, session=session)
            hist = stock.history(period="5d")
            
            if not hist.empty:
                price = hist['Close'].iloc[-1]
                price_str = f"₹{price:.2f}"
            else:
                price_str = "N/A"
            
            company_name = resolved_ticker
            try:
                info = stock.info
                if info and 'longName' in info:
                    company_name = info['longName']
            except:
                pass
            
            res += f"🔹 **{company_name}**: {price_str}\n"
        except Exception as e:
            res += f"🔹 **{resolved_ticker}**: Error\n"
            
    await msg.reply_text(res, parse_mode="Markdown")

async def remove_from_watchlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args: return
    user_id = update.effective_user.id
    ticker = context.args[0].upper()
    resolved_ticker = resolve_isin_to_ticker(ticker)
    
    conn = sqlite3.connect(DB_NAME)
    c = conn.cursor()
    try:
        c.execute("DELETE FROM watchlist WHERE user_id = ? AND (stock_symbol = ? OR stock_symbol = ?)", (user_id, ticker, resolved_ticker))
        changes = conn.total_changes
        conn.commit()
        if changes > 0: 
            await update.message.reply_text(f"❌ **{ticker}** को वॉचलिस्ट से हटा दिया गया है।")
        else: 
            await update.message.reply_text("ℹ️ स्टॉक वॉचलिस्ट में नहीं मिला।")
    except Exception as e:
        await update.message.reply_text("❌ हटाने के दौरान कोई एरर आया।")
    finally: 
        conn.close()

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("analyze", run_analyze_command))
    app.add_handler(CommandHandler("add", add_to_watchlist))
    app.add_handler(CommandHandler("watchlist", lambda u, c: show_watchlist_logic(u, u.effective_user.id)))
    app.add_handler(CommandHandler("remove", remove_from_watchlist))
    app.add_handler(CallbackQueryHandler(button_handler))
    print("🤖 Starting AI Stock Assistant v3.4...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
