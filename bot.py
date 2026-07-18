import os
import logging
import threading
import sqlite3
import requests
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from config import BOT_TOKEN

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
DB_NAME = "watchlist.db"

# === 1. मास्टर मैनुअल ISIN मैपिंग ===
MANUAL_ISIN_MAPPING = {
    "INE732K01027": "511557.BO",      # प्रो-फिन कैपिटल
    "INE0PQ601019": "530299.BO",      # बालाजी फॉस्फेट्स
}

# === 2. अल्टीमेट क्रम्बलेस JSON API प्राइस इंजन ===
def fetch_stock_data_json(symbol, timeframe="1y"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range={timeframe}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            meta = result.get("meta", {})
            
            # क्लोजिंग प्राइस की लिस्ट निकालें और None वैल्यूज साफ करें
            raw_prices = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            clean_prices = [p for p in raw_prices if p is not None]
            
            # लाइव भाव ढूंढें
            current_price = meta.get("regularMarketPrice")
            if not current_price and clean_prices:
                current_price = clean_prices[-1]
                
            if current_price:
                return {"success": True, "price": current_price, "prices_list": clean_prices, "ticker": meta.get("symbol", symbol)}
    except Exception as e:
        logging.error(f"JSON API Error for {symbol}: {e}")
    return {"success": False, "price": None, "prices_list": [], "ticker": symbol}

# === 3. कंपनी का असली नाम खोजने वाला इंजन ===
def fetch_company_name(ticker):
    url = f"https://query2.finance.yahoo.com/v1/finance/search?q={ticker}&quotesCount=1&newsCount=0"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            quotes = data.get("quotes", [])
            if quotes:
                return quotes[0].get("longname") or quotes[0].get("shortname") or ticker
    except:
        pass
    return ticker

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
                            return sym
                    return quotes[0].get("symbol", "")
        except:
            pass
    return target

class DummyServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"Bot is online on Ultimate Bulletproof JSON Engine.")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🤖 **AI STOCK ASSISTANT v5.1 (Fixed Variable Core)**\n\nसभी वेरिएबल एरर फिक्स कर दिए गए हैं! अब आपका बोट बिना किसी रुकावट के लाइव है। 🚀"
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
    
    await msg.reply_text(f"⏳ **{resolved}** का एडवांस एनालिसिस लोड हो रहा है...")
    
    api_data = fetch_stock_data_json(symbol, "1y")
    if not api_data["success"] or api_data["price"] is None:
        await msg.reply_text("❌ इस स्टॉक का डेटा सर्वर पर नहीं मिल सका।")
        return
        
    price = api_data["price"]
    prices_list = api_data["prices_list"]
    name = fetch_company_name(resolved)
    
    # शुद्ध गणितीय DMA कैलकुलेशन (बिना किसी बाहरी लाइब्रेरी के भारी लोड के)
    if len(prices_list) >= 200:
        dma_50 = sum(prices_list[-50:]) / 50
        dma_200 = sum(prices_list[-200:]) / 200
        d50_str, d200_str = f"₹{dma_50:.2f}", f"₹{dma_200:.2f}"
        sig = "🟢 **Super Bullish**" if price > dma_50 > dma_200 else "🔴 **Bearish**" if price < dma_50 < dma_200 else "🟡 **Sideways**"
    else:
        d50_str, d200_str, sig = "कम डेटा है", "कम डेटा है", "N/A"

    clean_tv = resolved.replace(".BO", "").replace(".NS", "")
    tradingview_link = f"https://www.tradingview.com/symbols/BSE-{clean_tv}/" if resolved.endswith(".BO") else f"https://www.tradingview.com/symbols/NSE-{clean_tv}/"

    res = f"📊 **SCREENER ANALYSIS: {name}**\n"
    res += f"━━━━━━━━━━━━━━━━━━━━\n"
    res += f"💰 **करंट प्राइस:** ₹{price:.2f}\n"
    res += f"📈 **50 DMA:** {d50_str}\n"
    res += f"📉 **200 DMA:** {d200_str}\n"
    res += f"⚡ **चार्ट सिग्नल:** {sig}\n"
    res += f"━━━━━━━━━━━━━━━━━━━━\n"
    res += f"🔗 [TradingView Live Chart]({tradingview_link})\n"
    await msg.reply_text(res, parse_mode="Markdown", disable_web_page_preview=True)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == 'view_wl': await show_watchlist_logic(update, update.effective_user.id)
    elif q.data == 'help_analysis': await q.message.reply_text("📊 टाइप करें: `/analyze STOCK`")
    elif q.data == 'help_technicals': await q.message.reply_text("⚡ Technicals के लिए `/analyze` का उपयोग करें।")
    elif q.data == 'help_add': await q.message.reply_text("➕ टाइप करें: `/add STOCK` या सीधे ISIN")
    elif q.data == 'help_remove': await q.message.reply_text("❌ हटाने के लिए टाइप करें: `/remove STOCK`")

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
    
    await msg.reply_text("🔄 लाइव भाव निकाला जा रहा है...")
    res = "📋 **आपकी पर्सनल वॉचलिस्ट (API Engine):**\n\n"
    
    for r in rows:
        db_ticker = r[0]
        resolved_ticker = resolve_isin_to_ticker(db_ticker)
        symbol = f"{resolved_ticker}.NS" if not (resolved_ticker.endswith(".NS") or resolved_ticker.endswith(".BO")) else resolved_ticker
        
        api_data = fetch_stock_data_json(symbol, "5d")
        
        if api_data["success"] and api_data["price"] is not None:
            price_str = f"₹{api_data['price']:.2f}"
            company_name = fetch_company_name(resolved_ticker)
            res += f"🔹 **{company_name}**: {price_str}\n"
        else:
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
            await update.message.reply_text("ℹ️ STOCK वॉचलिस्ट में नहीं मिला।")
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
    print("🤖 Starting AI Stock Assistant v5.1 (Fixed Variable Core)...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
