import os
import logging
import threading
import sqlite3
import requests
import re
from http.server import BaseHTTPRequestHandler, HTTPServer
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from config import BOT_TOKEN

logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
DB_NAME = "watchlist.db"

# === 1. मास्टर मैनुअल ISIN मैपिंग ===
MANUAL_ISIN_MAPPING = {
    "INE732K01027": "511557.BO",       # प्रो-फिन कैपिटल (BSE)
    "INE0PQ601019": "BALAJIPHOS.NS",   # बालाजी फॉस्फेट्स (NSE)
}

# === 2. गूगल फाइनेंस बाईपास इंजन (डायनेमिक क्लास फिक्स के साथ) ===
def fetch_live_price_google(ticker):
    target = ticker.strip().upper()
    if target.endswith(".NS"):
        g_ticker = target.replace(".NS", ":NSE")
    elif target.endswith(".BO"):
        g_ticker = target.replace(".BO", ":BSE")
    else:
        g_ticker = f"{target}:NSE"
        
    url = f"https://www.google.com/finance/quote/{g_ticker}?hl=en"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    cookies = {"CONSENT": "YES+cb.20230510-04-p0.en+GB+pagead"}
    
    try:
        res = requests.get(url, headers=headers, cookies=cookies, timeout=5)
        if res.status_code == 200:
            html = res.text
            # [FIXED] अब यह गूगल की डायनेमिक क्लास (YMlKec के आगे कुछ भी हो) को आसानी से पार्स कर लेगा
            price_match = re.search(r'class="[^"]*YMlKec[^"]*">([^<]+)', html)
            name_match = re.search(r'class="[^"]*ZZ33Fa[^"]*">([^<]+)', html)
            
            if price_match:
                price_raw = price_match.group(1).replace("₹", "").replace(",", "").strip()
                name_raw = name_match.group(1) if name_match else ticker
                return {"success": True, "price": float(price_raw), "name": name_raw}
    except Exception as e:
        logging.error(f"Google Engine Error for {g_ticker}: {e}")
    return {"success": False, "price": None, "name": ticker}

# === 3. बैकअप चार्ट एपीआई ===
def fetch_dma_backup(symbol):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?interval=1d&range=1y"
    headers = {"User-Agent": "Mozilla/5.0"}
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            data = res.json()
            result = data.get("chart", {}).get("result", [{}])[0]
            raw_prices = result.get("indicators", {}).get("quote", [{}])[0].get("close", [])
            return [p for p in raw_prices if p is not None]
    except:
        pass
    return []

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
        self.wfile.write(b"Bot is online on Dynamic Regex Engine v6.3")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🤖 **AI STOCK ASSISTANT v6.3 (Dynamic Regex Core)**\n\nगूगल के नए लेआउट/क्लास चेंजेस को बाईपास कर दिया गया है! अब NTPC और बाकी सभी हैवी स्टॉक्स परफेक्ट काम करेंगे। 🚀"
    keyboard = [
        [InlineKeyboardButton("📊 Screener Analysis", callback_data='help_analysis'), InlineKeyboardButton("⚡ Technicals (DMA)", callback_data='help_technicals')],
        [InlineKeyboardButton("➕ Add Stock / ISIN", callback_data='help_add'), InlineKeyboardButton("❌ Remove Stock", callback_data='help_remove')],
        [InlineKeyboardButton("📋 Watchlist देखें", callback_data='view_wl')]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def analyze_stock_data(update: Update, ticker: str, user_id: int):
    msg = update.callback_query.message if update.callback_query else update.message
    resolved = resolve_isin_to_ticker(ticker)
    
    await msg.reply_text(f"⏳ **{resolved}** का लाइव डेटा निकाला जा रहा है...")
    
    g_data = fetch_live_price_google(resolved)
    if not g_data["success"] or g_data["price"] is None:
        await msg.reply_text("❌ इस स्टॉक का लाइव भाव गूगल फाइनेंस पर नहीं मिल सका।")
        return
        
    price = g_data["price"]
    name = g_data["name"]
    
    symbol = f"{resolved}.NS" if not (resolved.endswith(".NS") or resolved.endswith(".BO")) else resolved
    prices_list = fetch_dma_backup(symbol)
    
    if len(prices_list) >= 200:
        dma_50 = sum(prices_list[-50:]) / 50
        dma_200 = sum(prices_list[-200:]) / 200
        d50_str, d200_str = f"₹{dma_50:.2f}", f"₹{dma_200:.2f}"
        sig = "🟢 **Super Bullish**" if price > dma_50 > dma_200 else "🔴 **Bearish**" if price < dma_50 < dma_200 else "🟡 **Sideways**"
    else:
        d50_str, d200_str, sig = "N/A", "N/A", "N/A"

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
    
    await msg.reply_text("🔄 गूगल फाइनेंस से लाइव भाव निकाला जा रहा है...")
    res = "📋 **आपकी पर्सनल वॉचलिस्ट (Google Core):**\n\n"
    
    for r in rows:
        db_ticker = r[0]
        resolved_ticker = resolve_isin_to_ticker(db_ticker)
        
        g_data = fetch_live_price_google(resolved_ticker)
        
        if g_data["success"] and g_data["price"] is not None:
            price_str = f"₹{g_data['price']:.2f}"
            res += f"🔹 **{g_data['name']}**: {price_str}\n"
        else:
            res += f"🔹 **{resolved_ticker}**: भाव अस्थायी रूप से अनुपलब्ध\n"
            
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
    print("🤖 Starting AI Stock Assistant v6.3 (Dynamic Regex Core)...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
