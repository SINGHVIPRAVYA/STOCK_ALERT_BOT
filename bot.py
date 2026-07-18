import os
import logging
import threading
import sqlite3
import requests
import re  # गूगल फाइनेंस का डेटा पार्स करने के लिए
from http.server import BaseHTTPRequestHandler, HTTPServer
import yfinance as yf  # सिर्फ 50/200 DMA के इतिहास के लिए बैकअप में रखा है
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

# === 2. याहू की जगह गूगल फाइनेंस से लाइव रेट निकालने का इंजन ===
def fetch_from_google_finance(ticker):
    target = ticker.strip().upper()
    
    # याहू फॉर्मेट को गूगल फॉर्मेट में बदलें (.NS -> :NSE, .BO -> :BSE)
    if target.endswith(".NS"):
        g_ticker = target.replace(".NS", ":NSE")
    elif target.endswith(".BO"):
        g_ticker = target.replace(".BO", ":BSE")
    else:
        g_ticker = f"{target}:NSE"
        
    url = f"https://www.google.com/finance/quote/{g_ticker}"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        res = requests.get(url, headers=headers, timeout=5)
        if res.status_code == 200:
            html = res.text
            # रेगुलर एक्सप्रेशन से लाइव प्राइस और कंपनी का नाम निकालना
            price_match = re.search(r'class="YMlKec fxKbKc">([^<]+)', html)
            name_match = re.search(r'class="ZZ33Fa">([^<]+)', html)
            
            if price_match:
                price_raw = price_match.group(1).replace("₹", "").replace(",", "").strip()
                price = float(price_raw)
                name = name_match.group(1) if name_match else ticker
                return {"name": name, "price": price, "success": True}
    except Exception as e:
        logging.error(f"Google Finance Error for {g_ticker}: {e}")
        
    return {"name": ticker, "price": None, "success": False}

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
        # इसे भी गूगल सर्च या बैकअप से निकाला जा सकता है, अभी के लिए याहू सर्च एपीआई बैकअप हेडर्स के साथ
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
        self.wfile.write(b"Bot is active and running on Google Finance Engine.")

def run_dummy_server():
    port = int(os.environ.get("PORT", 10000))
    server = HTTPServer(("0.0.0.0", port), DummyServer)
    server.serve_forever()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "🤖 **AI STOCK ASSISTANT v4.0 (Google Finance Core)**\n\nयाहू फाइनेंस को हटाकर मुख्य प्राइस इंजन को **गूगल फाइनेंस** पर शिफ्ट कर दिया गया है! अब रेट एरर कभी नहीं आएगा। 🚀"
    keyboard = [
        [InlineKeyboardButton("📊 Screener Analysis", callback_data='help_analysis'), InlineKeyboardButton("⚡ Technicals (DMA)", callback_data='help_technicals')],
        [InlineKeyboardButton("➕ Add Stock / ISIN", callback_data='help_add'), InlineKeyboardButton("❌ Remove Stock", callback_data='help_remove')],
        [InlineKeyboardButton("📋 Watchlist देखें", callback_data='view_wl')]
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

async def analyze_stock_data(update: Update, ticker: str, user_id: int):
    msg = update.callback_query.message if update.callback_query else update.message
    resolved = resolve_isin_to_ticker(ticker)
    
    await msg.reply_text(f"⏳ **{resolved}** का Google + Technical डेटा निकाला जा रहा है...")
    
    # 1. लाइव भाव गूगल फाइनेंस से लाएं (100% सटीक)
    g_data = fetch_from_google_finance(resolved)
    if not g_data["success"] or g_data["price"] is None:
        await msg.reply_text("❌ इस स्टॉक का लाइव डेटा गूगल फाइनेंस पर नहीं मिला।")
        return
        
    price = g_data["price"]
    name = g_data["name"]
    
    # 2. टेक्निकल डेटा (DMA) के लिए इतिहास याहू से बैकअप की तरह लेंगे (जो कभी-कभार ही कॉल होता है)
    symbol = f"{resolved}.NS" if not (resolved.endswith(".NS") or resolved.endswith(".BO")) else resolved
    try:
        stock = yf.Ticker(symbol)
        full_hist = stock.history(period="1y")
        if not full_hist.empty and len(full_hist) >= 200:
            f_cp = full_hist['Close']
            d50 = f_cp.rolling(50).mean().iloc[-1]
            d200 = f_cp.rolling(200).mean().iloc[-1]
            d50_str, d200_str = f"₹{d50:.2f}", f"₹{d200:.2f}"
            sig = "🟢 **Super Bullish**" if price > d50 > d200 else "🔴 **Bearish**" if price < d50 < d200 else "🟡 **Sideways**"
        else:
            d50_str, d200_str, sig = "N/A", "N/A", "N/A"
    except:
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
    elif q.data == 'help_analysis': await q.message.reply_text("📊 टाइप करें: `/analyze STOCK` या `/analyze ISIN`")
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
    res = "📋 **आपकी पर्सनल वॉचलिस्ट (Google Live):**\n\n"
    
    for r in rows:
        db_ticker = r[0]
        resolved_ticker = resolve_isin_to_ticker(db_ticker)
        
        # गूगल से डेटा लाएं
        g_data = fetch_from_google_finance(resolved_ticker)
        
        if g_data["success"] and g_data["price"] is not None:
            price_str = f"₹{g_data['price']:.2f}"
            company_name = g_data["name"]
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
    print("🤖 Starting AI Stock Assistant v4.0 (Google Core)...")
    threading.Thread(target=run_dummy_server, daemon=True).start()
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
