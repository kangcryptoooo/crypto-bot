import time
import os
import html as html_mod
import requests
import pandas as pd
import numpy as np
import ccxt
from datetime import datetime
from flask import Flask, request, abort
from threading import Thread

# === FLASK KEEP-ALIVE ===
app = Flask(__name__)

# Token rahasia untuk proteksi endpoint /status
BOT_STATUS_TOKEN = os.environ.get("BOT_STATUS_TOKEN", "")

@app.route('/')
@app.route('/ping')
def ping():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return f"OK | Bot aktif | {now_str}", 200

@app.route('/status')
def status():
    # Proteksi privacy: harus sertakan ?token=SECRET di URL
    token = request.args.get("token", "")
    if BOT_STATUS_TOKEN and token != BOT_STATUS_TOKEN:
        abort(403)
    sisa_sinyal = MAX_SIGNALS_PER_HOUR - hourly_signal_count
    sisa_ready  = MAX_READY_PER_HOUR  - hourly_ready_count
    active_cd   = len([k for k, v in coin_cooldown.items() if v > time.time()])
    return (
        f"Bot: AKTIF — Premium Scanner (Anti Rugi Berturut)\n"
        f"Sinyal Entry terkirim jam ini : {hourly_signal_count} / {MAX_SIGNALS_PER_HOUR}\n"
        f"Alert Ready terkirim jam ini  : {hourly_ready_count}  / {MAX_READY_PER_HOUR}\n"
        f"Sisa kuota Entry              : {sisa_sinyal}\n"
        f"Sisa kuota Ready              : {sisa_ready}\n"
        f"Volume min filter             : {MIN_VOLUME_24H:,} USDT\n"
        f"Timeframes                    : {', '.join(timeframes)}\n"
        f"Koin dalam cooldown aktif     : {active_cd}\n"
        f"Cooldown per sinyal/alert     : 2 Jam\n"
        f"Time                          : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    ), 200

PORT = int(os.environ.get("PORT", 9000))

def run_flask():
    app.run(host='0.0.0.0', port=PORT, use_reloader=False)

flask_thread = Thread(target=run_flask, daemon=True)
flask_thread.start()
time.sleep(1)
print(f"Flask keep-alive aktif di port {PORT}")

# ── Self-Ping Thread ──
def self_ping():
    time.sleep(30)
    while True:
        try:
            requests.get(f"http://localhost:{PORT}/ping", timeout=5)
            print(f"Self-ping OK ({datetime.now().strftime('%H:%M')})")
        except Exception:
            pass
        time.sleep(240)

Thread(target=self_ping, daemon=True).start()
print("Self-ping thread aktif (tiap 4 menit)")

# === KONFIGURASI BOT TELEGRAM ===
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8960315045:AAFd6pTp6XkYBFykVB_roZ_j2AoXV3fvI9s")
CHAT_ID        = os.environ.get("TELEGRAM_CHAT_ID", "-1003902153888")

# === KONFIGURASI SCANNER PREMIUM ===
timeframes   = ['5m', '15m', '30m']
swing_length = 14
rr_ratio     = 3.0

MAX_SIGNALS_PER_HOUR = 3
MAX_READY_PER_HOUR   = 3
READY_COOLDOWN_SEC   = 21600
ENTRY_COOLDOWN_SEC   = 7200

MIN_VOLUME_24H = 30_000_000

# TP/SL PERCENTAGE (Rumus Premium VIP)
LONG_TP1_PCT  = 0.025   # +2.5%
LONG_TP2_PCT  = 0.045   # +4.5%
LONG_TP3_PCT  = 0.060   # +6.0%
LONG_SL_PCT   = 0.045   # -4.5%

SHORT_TP1_PCT = 0.025   # -2.5%
SHORT_TP2_PCT = 0.045   # -4.5%
SHORT_TP3_PCT = 0.060   # -6.0%
SHORT_SL_PCT  = 0.045   # +4.5%

# RSI Threshold Premium
RSI_LONG_MAX  = 38      # LONG hanya jika RSI < 38
RSI_SHORT_MIN = 62      # SHORT hanya jika RSI > 62

# Volume Whale Filter
VOLUME_WHALE_RATIO = 1.5  # Minimal 1.5x rata-rata 5 candle

exchange = ccxt.binance({
    'enableRateLimit': True,
    'options': {'defaultType': 'future'}
})

last_update_id      = 0
hourly_signal_count = 0
hourly_ready_count  = 0
ready_alerted       = {}
coin_cooldown       = {}
entry_cooldown      = {}

force_recovery_scan     = False
recovery_scan_requested = 0.0

STICKER_TP_LIST = []
STICKER_SL_LIST = []
_sticker_tp_idx = 0
_sticker_sl_idx = 0

def init_stickers():
    global STICKER_TP_LIST, STICKER_SL_LIST
    HAPPY_EMOJI = {"🥳","🎉","😄","🎊","😊","🤩","😁","🏆","✅","🚀","💰","🎯"}
    SAD_EMOJI   = {"😢","😭","😥","💔","😿","🥺","😔","💸","😰","🤦","😓"}
    packs = [
        "HotCherry", "Cate", "Kuvat", "YuriStickers", "Meowgram",
        "CuteAnimals", "AnimatedEmojies", "UnicornStickers",
        "BunniesAndPaws", "DoggosAndKitties", "Tgstickers"
    ]
    for pack_name in packs:
        try:
            url  = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getStickerSet"
            resp = requests.get(url, params={"name": pack_name}, timeout=6).json()
            if not resp.get("ok"):
                continue
            for s in resp["result"]["stickers"]:
                emoji = s.get("emoji", "")
                fid   = s["file_id"]
                if emoji in HAPPY_EMOJI and len(STICKER_TP_LIST) < 5:
                    STICKER_TP_LIST.append(fid)
                if emoji in SAD_EMOJI and len(STICKER_SL_LIST) < 5:
                    STICKER_SL_LIST.append(fid)
                if len(STICKER_TP_LIST) >= 5 and len(STICKER_SL_LIST) >= 5:
                    break
        except Exception:
            continue
        if len(STICKER_TP_LIST) >= 5 and len(STICKER_SL_LIST) >= 5:
            break
    print(f"Sticker TP siap: {len(STICKER_TP_LIST)} variasi | SL: {len(STICKER_SL_LIST)} variasi")

init_stickers()

# =============================================================================
# HELPER: FORMAT HARGA
# =============================================================================
def fmt(val):
    """Format harga dengan presisi yang tepat."""
    if val >= 1000:
        return f"{val:,.2f}"
    elif val >= 1:
        return f"{val:,.4f}"
    else:
        return f"{val:,.6f}"

# =============================================================================
# HITUNG TP/SL PREMIUM (PERCENTAGE-BASED)
# =============================================================================
def calc_tpsl_long(entry):
    tp1 = entry * (1 + LONG_TP1_PCT)
    tp2 = entry * (1 + LONG_TP2_PCT)
    tp3 = entry * (1 + LONG_TP3_PCT)
    sl  = entry * (1 - LONG_SL_PCT)
    return tp1, tp2, tp3, sl

def calc_tpsl_short(entry):
    tp1 = entry * (1 - SHORT_TP1_PCT)
    tp2 = entry * (1 - SHORT_TP2_PCT)
    tp3 = entry * (1 - SHORT_TP3_PCT)
    sl  = entry * (1 + SHORT_SL_PCT)
    return tp1, tp2, tp3, sl

# =============================================================================
# TEMPLATE SINYAL PREMIUM (FORMAT VIP)
# =============================================================================
def template_long(clean_sym, tf, entry, tp1, tp2, tp3, sl, rsi_val, trend_lbl, btc_t, skor, macd_lbl="Naik 2 candle"):
    skor_bar   = "🟩" * (skor // 10) + "⬜" * (10 - skor // 10)
    safe_trend = html_mod.escape(trend_lbl)
    return (
        f"🚀 PREMIUM LONG — <b>{clean_sym}/USDT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry : <b>{fmt(entry)}</b>\n"
        f"\n"
        f"💰 TP1 : {fmt(tp1)}\n"
        f"💰 TP2 : {fmt(tp2)}\n"
        f"💰 TP3 : {fmt(tp3)}\n"
        f"\n"
        f"💥 SL : {fmt(sl)}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ANALISA\n"
        f"⚡ Trend : {safe_trend}\n"
        f"🔵 RSI : {rsi_val:.1f} zona jenuh jual\n"
        f"📡 MACD : {macd_lbl}\n"
        f"🌊 Volume : Whale OK\n"
        f"BTC Trend (1H): {btc_t}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💯 Skor {skor}/100\n"
        f"{skor_bar}"
    )

def template_short(clean_sym, tf, entry, tp1, tp2, tp3, sl, rsi_val, trend_lbl, btc_t, skor, macd_lbl="Turun 2 candle"):
    skor_bar   = "🟥" * (skor // 10) + "⬜" * (10 - skor // 10)
    safe_trend = html_mod.escape(trend_lbl)
    return (
        f"💥 PREMIUM SHORT — <b>{clean_sym}/USDT</b>\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry : <b>{fmt(entry)}</b>\n"
        f"\n"
        f"💰 TP1 : {fmt(tp1)}\n"
        f"💰 TP2 : {fmt(tp2)}\n"
        f"💰 TP3 : {fmt(tp3)}\n"
        f"\n"
        f"💥 SL : {fmt(sl)}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"📊 ANALISA\n"
        f"⚡ Trend : {safe_trend}\n"
        f"🔵 RSI : {rsi_val:.1f} zona jenuh beli\n"
        f"📡 MACD : {macd_lbl}\n"
        f"🌊 Volume : Whale OK\n"
        f"BTC Trend (1H): {btc_t}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💯 Skor {skor}/100\n"
        f"{skor_bar}"
    )

def template_tp1(clean_sym, entry, tp1, tp2, tp3):
    return (
        f"💥 Boooommmm TP 1 berhasil\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{clean_sym}/USDT</b>\n"
        f"\n"
        f"🎯 Entry : {fmt(entry)}\n"
        f"\n"
        f"✅ TP1 : {fmt(tp1)} TERCAPAI\n"
        f"💰 TP2 : {fmt(tp2)}\n"
        f"💰 TP3 : {fmt(tp3)}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Geser SL ke Entry (BEP) sekarang!"
    )

def template_tp2(clean_sym, entry, tp1, tp2, tp3):
    return (
        f"💥 Boooommmm TP 2 berhasil\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{clean_sym}/USDT</b>\n"
        f"\n"
        f"🎯 Entry : {fmt(entry)}\n"
        f"\n"
        f"✅ TP1 : {fmt(tp1)} TERCAPAI\n"
        f"✅ TP2 : {fmt(tp2)} TERCAPAI\n"
        f"💰 TP3 : {fmt(tp3)}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ Geser SL ke TP1 sekarang!"
    )

def template_tp3(clean_sym, entry, tp1, tp2, tp3):
    return (
        f"🏆 Boooommmm TP 3 berhasil — FULL PROFIT!\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{clean_sym}/USDT</b>\n"
        f"\n"
        f"✅ TP1 : {fmt(tp1)} TERCAPAI\n"
        f"✅ TP2 : {fmt(tp2)} TERCAPAI\n"
        f"✅ TP3 : {fmt(tp3)} TERCAPAI\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"🎉 Trade selesai sempurna!"
    )

def template_sl(clean_sym, entry, sl):
    return (
        f"🥲 Yaaaahhh SL\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{clean_sym}/USDT</b>\n"
        f"\n"
        f"🎯 Entry : {fmt(entry)}\n"
        f"\n"
        f"🛑 SL Hit : {fmt(sl)}\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"💪 Santai! Segera kirimkan gambar PnL kamu, dan saya langsung kirimkan recovery nya yang dijamin TP"
    )

# =============================================================================
# TELEGRAM HELPERS
# =============================================================================
def send_telegram(message):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message}
    try:
        r = requests.post(url, json=payload, timeout=10).json()
        if not r.get("ok"):
            print(f"send_telegram gagal: {r.get('description','')}")
        else:
            print(f"TG terkirim: {message[:60].replace(chr(10),' ')}...")
    except Exception as e:
        print(f"send_telegram exception: {e}")

def send_html(message):
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        r = requests.post(url, json=payload, timeout=10).json()
        if not r.get("ok"):
            plain = (message
                     .replace("<b>","").replace("</b>","")
                     .replace("&lt;","<").replace("&gt;",">").replace("&amp;","&"))
            requests.post(url, json={"chat_id": CHAT_ID, "text": plain}, timeout=10)
            print(f"send_html fallback plain: {r.get('description','')}")
        else:
            print(f"TG-HTML terkirim: {message[:60].replace(chr(10),' ')}...")
    except Exception as e:
        print(f"send_html exception: {e}")

def send_sticker(file_id):
    if not file_id:
        return
    url     = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendSticker"
    payload = {"chat_id": CHAT_ID, "sticker": file_id}
    try:
        r = requests.post(url, json=payload, timeout=10).json()
        if not r.get("ok"):
            print(f"sendSticker gagal: {r.get('description','')}")
    except Exception as e:
        print(f"sendSticker error: {e}")

def send_sticker_tp():
    global _sticker_tp_idx
    if not STICKER_TP_LIST:
        return
    fid = STICKER_TP_LIST[_sticker_tp_idx % len(STICKER_TP_LIST)]
    _sticker_tp_idx += 1
    send_sticker(fid)

def send_sticker_sl():
    global _sticker_sl_idx
    if not STICKER_SL_LIST:
        return
    fid = STICKER_SL_LIST[_sticker_sl_idx % len(STICKER_SL_LIST)]
    _sticker_sl_idx += 1
    send_sticker(fid)

def check_telegram_commands():
    global last_update_id, hourly_signal_count, force_recovery_scan, recovery_scan_requested
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    try:
        params   = {"offset": last_update_id + 1, "timeout": 1}
        response = requests.get(url, params=params, timeout=5).json()
        if response.get("ok") and response.get("result"):
            for update in response["result"]:
                last_update_id = update["update_id"]
                msg = update.get("message", {})

                # Deteksi foto PnL merah → picu Recovery Scan
                if "photo" in msg:
                    chat_id = str(msg["chat"]["id"])
                    if chat_id == CHAT_ID and not force_recovery_scan:
                        force_recovery_scan     = True
                        recovery_scan_requested = time.time()
                        send_telegram(
                            "📸 Foto PnL diterima! Saya langsung scan pasar...\n"
                            "🔥 Mencari 2-3 sinyal terbaik saat ini.\n"
                            "⚡ Harap tunggu 5-10 menit.\n\n"
                            "🛡️ Ingat: tetap gunakan manajemen risiko yang baik!"
                        )
                        print("[RECOVERY SCAN] Foto PnL diterima — mulai scan paksa...")

                if "text" not in msg:
                    continue

                chat_id = str(msg["chat"]["id"])
                text    = msg["text"].strip().lower()

                if chat_id == CHAT_ID:
                    if text in ["/test", "test"]:
                        time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        send_telegram("⚡ Siap-siap pantau TEST/USDT (TF: 15m) 🚀")
                        time.sleep(1)
                        send_html(
                            "🚀 PREMIUM LONG — <b>TEST/USDT</b>\n"
                            "━━━━━━━━━━━━━━━━━━━\n"
                            "🎯 Entry : <b>1.0000</b>\n"
                            "\n"
                            "💰 TP1 : 1.0250\n"
                            "💰 TP2 : 1.0450\n"
                            "💰 TP3 : 1.0600\n"
                            "\n"
                            "💥 SL : 0.9550\n"
                            "━━━━━━━━━━━━━━━━━━━\n"
                            "📊 ANALISA\n"
                            "⚡ Trend : EMA15>50 Bullish\n"
                            "🔵 RSI : 35.0 zona jenuh jual\n"
                            "📡 MACD : Naik 2 candle\n"
                            "🌊 Volume : Whale OK\n"
                            "BTC Trend (1H): BULL\n"
                            "━━━━━━━━━━━━━━━━━━━\n"
                            "💯 Skor 80/100\n"
                            "🟩🟩🟩🟩🟩🟩🟩🟩⬜⬜"
                        )
                        time.sleep(1)
                        send_html(template_tp1("TEST", 1.0, 1.025, 1.045, 1.060))

                    elif text in ["/status", "status", "/koin", "koin"]:
                        sisa   = MAX_SIGNALS_PER_HOUR - hourly_signal_count
                        active = len([k for k, v in coin_cooldown.items() if v > time.time()])
                        send_telegram(
                            "⚡ STATUS BOT PREMIUM SCANNER\n"
                            f"• Filter: EMA15/50 + RSI < {RSI_LONG_MAX} (Long) / > {RSI_SHORT_MIN} (Short)\n"
                            f"• Volume: Minimal {VOLUME_WHALE_RATIO}x rata-rata 5 candle\n"
                            f"🌊 Volume min          : {MIN_VOLUME_24H:,} USDT\n"
                            f"🎯 Sinyal Entry jam ini: {hourly_signal_count} / {MAX_SIGNALS_PER_HOUR}\n"
                            f"🔔 Alert Ready jam ini : {hourly_ready_count}  / {MAX_READY_PER_HOUR}\n"
                            f"💎 Sisa kuota entry    : {sisa}\n"
                            f"🛡️ Koin cooldown aktif : {active} koin\n"
                            f"⏱️ Durasi cooldown     : 2 Jam\n"
                            "🚀 Status Koneksi: 100% Aktif"
                        )

                    elif text in ["/ping", "ping"]:
                        send_telegram(
                            "⚡ Pong! Bot aktif & sehat! 🚀\n"
                            f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                            f"🛰️ BTC Trend: {btc_trend_cache.get('trend','?')}"
                        )

                    elif text in ["/help", "help", "/bantuan", "bantuan"]:
                        send_telegram(
                            "🤖 DAFTAR PERINTAH BOT SCANNER\n"
                            "━━━━━━━━━━━━━━━\n"
                            "⚡ /status  — Lihat kuota & info bot\n"
                            "🔎 /cek BTC — Analisa koin manual\n"
                            "           contoh: cek ETH, cek SOL\n"
                            "💎 /topup   — Top 3 koin skor tertinggi + detail\n"
                            "🚀 /scan    — Paksa scan & kirim sinyal entry\n"
                            "✨ /stiker  — Test kirim sticker TP & SL\n"
                            "💥 /ping    — Cek bot masih hidup\n"
                            "📸 Kirim FOTO PnL merah → Recovery Scan\n"
                            "           (bot langsung cari 2-3 sinyal)\n"
                            "━━━━━━━━━━━━━━━\n"
                            "✨ Tips: Ketik 'cek BTC' tanpa slash juga bisa!"
                        )

                    elif text in ["/stiker", "stiker", "/sticker"]:
                        send_telegram("✨ Test sticker TP & SL...")
                        time.sleep(0.5)
                        if STICKER_TP_LIST:
                            send_sticker_tp()
                            time.sleep(0.5)
                            send_telegram(f"🏆 Sticker TP OK ({len(STICKER_TP_LIST)} variasi tersimpan)")
                        else:
                            send_telegram("🚨 Sticker TP belum ditemukan di pack manapun.")
                        time.sleep(0.5)
                        if STICKER_SL_LIST:
                            send_sticker_sl()
                            time.sleep(0.5)
                            send_telegram(f"💥 Sticker SL OK ({len(STICKER_SL_LIST)} variasi tersimpan)")
                        else:
                            send_telegram("🚨 Sticker SL belum ditemukan di pack manapun.")

                    elif text in ["/topup", "topup"]:
                        send_telegram(
                            "💎 Mencari 3 koin dengan skor tertinggi...\n"
                            "⚡ Harap tunggu ~1-2 menit."
                        )
                        Thread(target=do_topup_scan, daemon=True).start()

                    elif text in ["/scan", "scan", "/sinyal", "sinyal"]:
                        active = len([k for k, v in coin_cooldown.items() if v > time.time()])
                        send_telegram(
                            f"🔥 Memulai scan manual 10 koin teratas...\n"
                            f"⚡ Harap tunggu ~1-2 menit.\n"
                            f"💎 Kuota entry tersisa: {MAX_SIGNALS_PER_HOUR - hourly_signal_count}\n"
                            f"🛡️ Koin cooldown: {active}"
                        )
                        Thread(target=do_recovery_scan, daemon=True).start()

                    elif text.startswith("cek ") or text.startswith("/cek "):
                        coin_name = text.replace("/cek ", "").replace("cek ", "").strip()
                        if coin_name:
                            send_telegram(f"⚡ Sedang analisis {coin_name.upper()}... tunggu sebentar.")
                            result = analyze_coin(coin_name)
                            send_telegram(result)
                        else:
                            send_telegram(
                                "💬 Format: cek BTC\n"
                                "Contoh: cek ETH, cek SOL, cek BNB\n"
                                "Atau ketik /help untuk semua perintah."
                            )
    except Exception as e:
        print(f"check_telegram_commands error: {e}")

# =============================================================================
# INDIKATOR
# =============================================================================
def calculate_rsi(df, period=14):
    delta = df['close'].diff()
    gain  = (delta.where(delta > 0, 0)).rolling(window=period).mean()
    loss  = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
    rs    = gain / (loss + 1e-10)
    return 100 - (100 / (1 + rs))

def calculate_macd(df, fast=12, slow=26, signal=9):
    exp1        = df['close'].ewm(span=fast,   adjust=False).mean()
    exp2        = df['close'].ewm(span=slow,   adjust=False).mean()
    macd_line   = exp1 - exp2
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    macd_hist   = macd_line - signal_line
    return macd_line, signal_line, macd_hist

def fetch_data(symbol, tf):
    try:
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=210)
        df   = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        if not df.empty:
            df['rsi']  = calculate_rsi(df)
            df['macd'], df['macd_signal'], df['macd_hist'] = calculate_macd(df)
        return df
    except Exception as e:
        print(f"fetch_data error {symbol} {tf}: {e}")
        return pd.DataFrame()

def get_btc_trend():
    global btc_trend_cache
    if time.time() - btc_trend_cache["updated"] < 900:
        return btc_trend_cache["trend"]
    try:
        bars      = exchange.fetch_ohlcv("BTC/USDT:USDT", timeframe="1h", limit=210)
        df        = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        ema15_btc = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
        ema50_btc = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        close_btc = df['close'].iloc[-1]
        rsi_btc   = calculate_rsi(df).iloc[-1]
        if ema15_btc > ema50_btc and rsi_btc < 75:
            trend = "BULL"
        elif ema15_btc < ema50_btc and rsi_btc > 25:
            trend = "BEAR"
        else:
            trend = "NEUTRAL"
        btc_trend_cache = {"trend": trend, "updated": time.time()}
        print(f"BTC Trend: {trend} | Harga: {close_btc:.1f} | EMA15: {ema15_btc:.1f} | RSI: {rsi_btc:.1f}")
        return trend
    except Exception as e:
        print(f"get_btc_trend error: {e}")
        return btc_trend_cache["trend"]

def volume_whale_ok(df):
    """Volume harus minimal VOLUME_WHALE_RATIO x rata-rata 5 candle terakhir."""
    if len(df) < 7:
        return False
    vol_now  = df['volume'].iloc[-1]
    vol_avg5 = df['volume'].iloc[-6:-1].mean()
    return vol_now > VOLUME_WHALE_RATIO * vol_avg5

def is_in_cooldown(mem_key):
    return coin_cooldown.get(mem_key, 0) > time.time()

def set_cooldown(mem_key):
    coin_cooldown[mem_key] = time.time() + ENTRY_COOLDOWN_SEC
    print(f"COOLDOWN 2 JAM terpasang: {mem_key}")

def is_entry_cooldown(symbol):
    return entry_cooldown.get(symbol, 0) > time.time()

def set_entry_cooldown(symbol):
    entry_cooldown[symbol] = time.time() + ENTRY_COOLDOWN_SEC
    sisa = int(ENTRY_COOLDOWN_SEC / 60)
    print(f"ENTRY COOLDOWN {sisa} menit terpasang untuk {symbol}")

# =============================================================================
# ANALISIS MANUAL (perintah: cek BTC)
# =============================================================================
def analyze_coin(coin_name):
    coin_upper = coin_name.upper().replace("/USDT", "").replace("USDT", "").strip()
    symbol     = f"{coin_upper}/USDT:USDT"
    results      = []
    long_votes   = 0
    short_votes  = 0
    last_close   = None

    for tf in timeframes:
        try:
            bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=150)
            if not bars or len(bars) < 100:
                continue
            df        = pd.DataFrame(bars, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['rsi'] = calculate_rsi(df)
            df['macd'], df['macd_signal'], df['macd_hist'] = calculate_macd(df)
            close_val      = df['close'].iloc[-1]
            rsi_val        = df['rsi'].iloc[-1]
            macd_hist_now  = df['macd_hist'].iloc[-1]
            macd_hist_prev = df['macd_hist'].iloc[-2]
            ema15          = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
            ema50          = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
            vol_ok         = volume_whale_ok(df)
            bull_trend     = ema15 > ema50
            macd_naik      = macd_hist_now > macd_hist_prev

            if bull_trend and rsi_val < RSI_LONG_MAX and macd_naik and vol_ok:
                arah = "📈 LONG"
                long_votes += 1
            elif not bull_trend and rsi_val > RSI_SHORT_MIN and not macd_naik and vol_ok:
                arah = "📉 SHORT"
                short_votes += 1
            else:
                arah = "⏳ NETRAL"
            results.append(
                f"TF {tf}: {arah} | Harga: {fmt(close_val)} | RSI: {rsi_val:.1f} | "
                f"EMA: {'🟢>50' if bull_trend else '🔴<50'} | "
                f"MACD: {'naik' if macd_naik else 'turun'} | "
                f"Vol: {'Whale OK' if vol_ok else 'Tipis'}"
            )
            last_close = close_val
        except Exception as e:
            results.append(f"TF {tf}: Data tidak tersedia ({e})")

    if not results:
        return f"❌ Koin {coin_upper}/USDT tidak ditemukan di Binance Futures."

    detail  = "\n".join(results)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    if long_votes > short_votes and long_votes >= 2 and last_close:
        tp1, tp2, tp3, sl = calc_tpsl_long(last_close)
        kesimpulan = (
            f"🚀 REKOMENDASI: LONG {coin_upper}/USDT\n"
            f"Mayoritas TF bullish + Volume Whale terkonfirmasi.\n\n"
            f"🎯 Entry : {fmt(last_close)}\n"
            f"💰 TP1   : {fmt(tp1)}\n"
            f"💰 TP2   : {fmt(tp2)}\n"
            f"💰 TP3   : {fmt(tp3)}\n"
            f"💥 SL    : {fmt(sl)}"
        )
    elif short_votes > long_votes and short_votes >= 2 and last_close:
        tp1, tp2, tp3, sl = calc_tpsl_short(last_close)
        kesimpulan = (
            f"💥 REKOMENDASI: SHORT {coin_upper}/USDT\n"
            f"Mayoritas TF bearish + Volume Whale terkonfirmasi.\n\n"
            f"🎯 Entry : {fmt(last_close)}\n"
            f"💰 TP1   : {fmt(tp1)}\n"
            f"💰 TP2   : {fmt(tp2)}\n"
            f"💰 TP3   : {fmt(tp3)}\n"
            f"💥 SL    : {fmt(sl)}"
        )
    else:
        kesimpulan = (
            f"🛡️ REKOMENDASI: WAIT / TAHAN\n"
            f"Sinyal {coin_upper}/USDT belum memenuhi syarat premium.\n"
            f"Tunggu konfirmasi sebelum entry."
        )

    return (
        f"🔎 Analisis Premium {coin_upper}/USDT\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{detail}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"{kesimpulan}\n"
        f"🕐 {now_str}"
    )

# =============================================================================
# FETCH TOP GAINERS + LOSERS
# =============================================================================
TOP_GAINERS_LIMIT        = 25
TOP_LOSERS_LIMIT         = 25
SYMBOLS_REFRESH_INTERVAL = 1800

def fetch_top_gainers_losers():
    try:
        exchange.load_markets()
        tickers    = exchange.fetch_tickers()
        candidates = []
        for symbol, market in exchange.markets.items():
            if not (market['linear'] and market['settle'] == 'USDT' and market['active']):
                continue
            ticker       = tickers.get(symbol, {})
            pct_change   = ticker.get('percentage', None)
            quote_volume = ticker.get('quoteVolume', 0)
            if pct_change is None or quote_volume < MIN_VOLUME_24H:
                continue
            candidates.append((symbol, pct_change))
        candidates.sort(key=lambda x: x[1], reverse=True)
        gainers = [s for s, _ in candidates[:TOP_GAINERS_LIMIT]]
        losers  = [s for s, _ in candidates[-TOP_LOSERS_LIMIT:]]
        g_names = [s.split(':')[0].split('/')[0] for s in gainers]
        l_names = [s.split(':')[0].split('/')[0] for s in losers]
        print(f"Top gainers (LONG): {', '.join(g_names[:5])}...")
        print(f"Top losers  (SHORT): {', '.join(l_names[:5])}...")
        return gainers, losers
    except Exception as e:
        print(f"Gagal ambil data pasar: {e}")
        return ['BTC/USDT:USDT', 'ETH/USDT:USDT'], ['BTC/USDT:USDT', 'ETH/USDT:USDT']

print("Mengambil data pasar Binance Futures (vol >= 30 Juta USDT)...")
gainer_symbols, loser_symbols = fetch_top_gainers_losers()
last_symbols_refresh = time.time()

already_alerted  = {}
active_trades    = {}
last_reset_hour  = datetime.now().hour
btc_trend_cache  = {"trend": "NEUTRAL", "updated": 0}

print("Bot Premium Scanner (Anti Rugi Berturut | Volume Whale Filter) AKTIF...")

def send_startup_notification():
    time.sleep(3)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    send_telegram(
        "🚀 BOT SCANNER PREMIUM AKTIF!\n"
        "━━━━━━━━━━━━━━━\n"
        f"🕐 Waktu mulai : {now_str}\n"
        f"📡 Timeframes  : {', '.join(timeframes)}\n"
        f"🌊 Vol minimum : {MIN_VOLUME_24H:,} USDT\n"
        f"🛡️ Cooldown    : 2 Jam per koin (semua TF)\n"
        f"🎯 Kuota/Jam   : {MAX_SIGNALS_PER_HOUR} Entry + {MAX_READY_PER_HOUR} Ready\n"
        f"📊 RSI Filter  : LONG < {RSI_LONG_MAX} | SHORT > {RSI_SHORT_MIN}\n"
        f"🌊 Volume      : Minimal {VOLUME_WHALE_RATIO}x rata-rata 5 candle\n"
        "━━━━━━━━━━━━━━━\n"
        "Ketik /help untuk daftar perintah lengkap ⚡"
    )

Thread(target=send_startup_notification, daemon=True).start()

# =============================================================================
# SCORING
# =============================================================================
def score_candidate(df, direction, btc_trend="NEUTRAL"):
    try:
        rsi_val      = df['rsi'].iloc[-1]
        macd_h       = df['macd_hist'].iloc[-1]
        macd_h_prev  = df['macd_hist'].iloc[-2]
        macd_h_prev2 = df['macd_hist'].iloc[-3]
        ema15        = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
        ema50        = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        bull_trend   = ema15 > ema50
        vol_now      = df['volume'].iloc[-1]
        vol_avg5     = df['volume'].iloc[-6:-1].mean()
        vol_ratio    = vol_now / (vol_avg5 + 1e-10)
        ema_gap_pct  = abs(ema15 - ema50) / (ema50 + 1e-10) * 100
        macd_2c_long  = macd_h > macd_h_prev > macd_h_prev2
        macd_2c_short = macd_h < macd_h_prev < macd_h_prev2
        score = 0

        if direction == "LONG":
            if not bull_trend:               return 0
            if rsi_val >= RSI_LONG_MAX:      return 0
            # Skor RSI: makin rendah makin bagus
            if rsi_val <= 25:
                score += 30
            elif rsi_val <= 30:
                score += 25
            elif rsi_val <= RSI_LONG_MAX:
                score += max(10, int(30 - (rsi_val - 25) * 1.5))

            if macd_2c_long:           score += 25
            elif macd_h > macd_h_prev: score += 10

            if   vol_ratio >= 2.0: score += 20
            elif vol_ratio >= 1.5: score += 15
            elif vol_ratio >= 1.2: score += 8
            else:                  return 0   # Harus minimal 1.5x

            if   ema_gap_pct >= 1.0: score += 15
            elif ema_gap_pct >= 0.5: score += 10
            elif ema_gap_pct >= 0.2: score += 5

            if   btc_trend == "BULL":    score += 10
            elif btc_trend == "NEUTRAL": score += 5

        else:
            if bull_trend:                    return 0
            if rsi_val <= RSI_SHORT_MIN:      return 0
            # Skor RSI: makin tinggi makin bagus
            if rsi_val >= 75:
                score += 30
            elif rsi_val >= 70:
                score += 25
            elif rsi_val > RSI_SHORT_MIN:
                score += max(10, int(30 - (75 - rsi_val) * 1.5))

            if macd_2c_short:          score += 25
            elif macd_h < macd_h_prev: score += 10

            if   vol_ratio >= 2.0: score += 20
            elif vol_ratio >= 1.5: score += 15
            elif vol_ratio >= 1.2: score += 8
            else:                  return 0   # Harus minimal 1.5x

            if   ema_gap_pct >= 1.0: score += 15
            elif ema_gap_pct >= 0.5: score += 10
            elif ema_gap_pct >= 0.2: score += 5

            if   btc_trend == "BEAR":    score += 10
            elif btc_trend == "NEUTRAL": score += 5

        return max(0, min(100, score))
    except Exception:
        return 0

def score_candidate_detail(df, direction, btc_trend="NEUTRAL"):
    try:
        rsi_val      = df['rsi'].iloc[-1]
        macd_h       = df['macd_hist'].iloc[-1]
        macd_h_prev  = df['macd_hist'].iloc[-2]
        macd_h_prev2 = df['macd_hist'].iloc[-3]
        ema15        = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
        ema50        = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
        bull_trend   = ema15 > ema50
        vol_now      = df['volume'].iloc[-1]
        vol_avg5     = df['volume'].iloc[-6:-1].mean()
        vol_ratio    = vol_now / (vol_avg5 + 1e-10)
        ema_gap_pct  = abs(ema15 - ema50) / (ema50 + 1e-10) * 100
        macd_2c_long  = macd_h > macd_h_prev > macd_h_prev2
        macd_2c_short = macd_h < macd_h_prev < macd_h_prev2
        s_rsi = s_macd = s_vol = s_ema = s_btc = 0

        if direction == "LONG":
            if not bull_trend or rsi_val >= RSI_LONG_MAX: return 0, {}
            if rsi_val <= 25:        s_rsi = 30
            elif rsi_val <= 30:      s_rsi = 25
            else:                    s_rsi = max(10, int(30 - (rsi_val - 25) * 1.5))
            if macd_2c_long:         s_macd = 25
            elif macd_h > macd_h_prev: s_macd = 10
            if btc_trend == "BULL":      s_btc = 10
            elif btc_trend == "NEUTRAL": s_btc = 5
        else:
            if bull_trend or rsi_val <= RSI_SHORT_MIN: return 0, {}
            if rsi_val >= 75:        s_rsi = 30
            elif rsi_val >= 70:      s_rsi = 25
            else:                    s_rsi = max(10, int(30 - (75 - rsi_val) * 1.5))
            if macd_2c_short:        s_macd = 25
            elif macd_h < macd_h_prev: s_macd = 10
            if btc_trend == "BEAR":      s_btc = 10
            elif btc_trend == "NEUTRAL": s_btc = 5

        if   vol_ratio >= 2.0: s_vol = 20
        elif vol_ratio >= 1.5: s_vol = 15
        elif vol_ratio >= 1.2: s_vol = 8
        # Vol < 1.5x tidak dihitung (tidak memenuhi syarat whale)

        if   ema_gap_pct >= 1.0: s_ema = 15
        elif ema_gap_pct >= 0.5: s_ema = 10
        elif ema_gap_pct >= 0.2: s_ema = 5

        total = s_rsi + s_macd + s_vol + s_ema + s_btc
        breakdown = {
            "rsi": s_rsi, "macd": s_macd, "vol": s_vol, "ema": s_ema, "btc": s_btc,
            "rsi_val": round(rsi_val, 1), "vol_ratio": round(vol_ratio, 2),
            "ema_gap": round(ema_gap_pct, 2)
        }
        return max(0, min(100, total)), breakdown
    except Exception:
        return 0, {}

# =============================================================================
# DO TOPUP SCAN
# =============================================================================
def do_topup_scan():
    print("[TOPUP SCAN] Mulai scan ranking skor semua koin...")
    btc_t = get_btc_trend()
    candidates = []
    all_sym = list(dict.fromkeys(gainer_symbols + loser_symbols))
    for symbol in all_sym:
        clean_sym = symbol.split(':')[0].split('/')[0]
        for tf in ['5m', '15m', '30m']:
            try:
                df = fetch_data(symbol, tf)
                if df.empty or len(df) < 100:
                    continue
                ema15      = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
                ema50      = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                bull_trend = ema15 > ema50
                close_val  = df['close'].iloc[-1]
                direction  = "LONG" if bull_trend else "SHORT"
                skor, breakdown = score_candidate_detail(df, direction, btc_t)
                if skor < 30:
                    continue
                candidates.append({
                    "clean": clean_sym, "tf": tf, "direction": direction,
                    "score": skor, "close": close_val, "breakdown": breakdown
                })
                time.sleep(0.03)
            except Exception:
                continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    seen_coins = set()
    top3 = []
    for c in candidates:
        if c["clean"] not in seen_coins and len(top3) < 3:
            seen_coins.add(c["clean"])
            top3.append(c)

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if not top3:
        send_telegram(
            "🛡️ Tidak ada koin dengan skor cukup saat ini.\n"
            "Pasar sedang konsolidasi — coba lagi beberapa menit."
        )
        print("[TOPUP SCAN] Tidak ada kandidat.")
        return

    send_telegram(
        f"💎 TOP 3 KOIN TERKUAT SAAT INI\n"
        f"━━━━━━━━━━━━━━━\n"
        f"🕐 {now_str}\n"
        f"🛰️ BTC Trend : {btc_t}\n"
        f"━━━━━━━━━━━━━━━\n"
        f"Skor >= 60 = siap entry  |  < 60 = pantau dulu"
    )
    time.sleep(1)

    for i, c in enumerate(top3, 1):
        try:
            bd     = c["breakdown"]
            edir   = "🚀 LONG" if c["direction"] == "LONG" else "💥 SHORT"
            clr    = "🟩" if c["direction"] == "LONG" else "🟥"
            bar    = clr * (c["score"] // 10) + "⬜" * (10 - c["score"] // 10)
            status = "✅ SIAP ENTRY!" if c["score"] >= 60 else "⏳ Pantau dulu"

            def bar_mini(val, mx):
                filled = round(val / mx * 5) if mx > 0 else 0
                return "▓" * filled + "░" * (5 - filled)

            send_telegram(
                f"#{i} 💎 {c['clean']}/USDT (TF: {c['tf']}) — {edir}\n"
                f"💰 Harga : {fmt(c['close'])}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"💯 Total Skor : {c['score']}/100\n"
                f"{bar}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"🔵 RSI Ideal     {bar_mini(bd['rsi'],30)} {bd['rsi']}/30   RSI={bd['rsi_val']}\n"
                f"📡 MACD Momentum {bar_mini(bd['macd'],25)} {bd['macd']}/25\n"
                f"🌊 Volume Ratio  {bar_mini(bd['vol'],20)} {bd['vol']}/20   x{bd['vol_ratio']} avg\n"
                f"⚡ EMA Gap       {bar_mini(bd['ema'],15)} {bd['ema']}/15   {bd['ema_gap']}%\n"
                f"🛰️ BTC Align    {bar_mini(bd['btc'],10)} {bd['btc']}/10   {btc_t}\n"
                f"━━━━━━━━━━━━━━━\n"
                f"{status}"
            )
            time.sleep(1)
        except Exception as e:
            print(f"[TOPUP SCAN] Error kirim #{i}: {e}")

    print(f"[TOPUP SCAN] Selesai — {len(top3)} koin dikirim.")

# =============================================================================
# DO RECOVERY SCAN
# =============================================================================
def do_recovery_scan():
    global force_recovery_scan, recovery_scan_requested
    print("[RECOVERY SCAN] Mulai scan paksa semua koin...")

    btc_t = get_btc_trend()
    candidates = []
    all_sym = list(dict.fromkeys(gainer_symbols + loser_symbols))

    for symbol in all_sym:
        clean_sym = symbol.split(':')[0].split('/')[0]
        for tf in ['15m', '30m']:
            try:
                if is_entry_cooldown(clean_sym):
                    continue

                df = fetch_data(symbol, tf)
                if df.empty or len(df) < 100:
                    continue

                close_val  = df['close'].iloc[-1]
                rsi_val    = df['rsi'].iloc[-1]
                macd_h     = df['macd_hist'].iloc[-1]
                macd_prev  = df['macd_hist'].iloc[-2]
                ema15      = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
                ema50      = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]
                bull_trend = ema15 > ema50
                vol_ok     = volume_whale_ok(df)

                if not vol_ok:
                    continue

                if bull_trend and rsi_val < RSI_LONG_MAX:
                    direction  = "LONG"
                    trend_lbl  = "EMA15>50 Bullish"
                elif not bull_trend and rsi_val > RSI_SHORT_MIN:
                    direction  = "SHORT"
                    trend_lbl  = "EMA15<50 Bearish"
                else:
                    continue

                skor = score_candidate(df, direction, btc_t)
                if skor < 50:
                    continue

                if direction == "LONG":
                    tp1, tp2, tp3, sl = calc_tpsl_long(close_val)
                else:
                    tp1, tp2, tp3, sl = calc_tpsl_short(close_val)

                candidates.append({
                    "symbol": symbol, "clean": clean_sym, "tf": tf,
                    "direction": direction, "score": skor,
                    "close": close_val, "rsi": rsi_val,
                    "tp1": tp1, "tp2": tp2, "tp3": tp3, "sl": sl,
                    "trend_lbl": trend_lbl, "vol_ok": vol_ok,
                    "macd_up": macd_h > macd_prev, "btc_t": btc_t
                })
                print(f"[RECOVERY] Kandidat: {clean_sym} TF:{tf} Skor:{skor} Arah:{direction}")
                time.sleep(0.05)
            except Exception as e:
                print(f"[RECOVERY SCAN] Error {symbol} {tf}: {e}")
                continue

    candidates.sort(key=lambda x: x["score"], reverse=True)
    seen_coins = set()
    top3 = []
    for c in candidates:
        if c["clean"] not in seen_coins and len(top3) < 3:
            seen_coins.add(c["clean"])
            top3.append(c)

    print(f"[RECOVERY SCAN] Kandidat lolos: {len(candidates)}, dikirim: {len(top3)}")

    if not top3:
        send_telegram(
            "🛡️ Recovery Scan selesai.\n"
            "Saat ini belum ada sinyal premium yang layak dikirim.\n"
            "Pasar sedang konsolidasi — harap tunggu setup yang lebih jelas."
        )
        print("[RECOVERY SCAN] Tidak ada kandidat skor cukup tinggi.")
    else:
        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        send_telegram(
            f"🔥 RECOVERY SCAN SELESAI — {len(top3)} sinyal terbaik ditemukan!\n"
            f"Berikut setup entry paling kuat saat ini:\n"
            f"🕐 {now_str}"
        )
        time.sleep(1)
        for i, c in enumerate(top3, 1):
            try:
                macd_lbl = ("Naik 2 candle" if c["macd_up"] else "Turun 2 candle")
                if c["direction"] == "LONG":
                    msg = template_long(
                        c["clean"], c["tf"], c["close"],
                        c["tp1"], c["tp2"], c["tp3"], c["sl"],
                        c["rsi"], c["trend_lbl"], c["btc_t"], c["score"], macd_lbl
                    )
                    msg = f"🚨 RECOVERY #{i} — 🚀 LONG\n" + msg
                else:
                    msg = template_short(
                        c["clean"], c["tf"], c["close"],
                        c["tp1"], c["tp2"], c["tp3"], c["sl"],
                        c["rsi"], c["trend_lbl"], c["btc_t"], c["score"], macd_lbl
                    )
                    msg = f"🚨 RECOVERY #{i} — 💥 SHORT\n" + msg

                print(f"[RECOVERY SCAN] Kirim sinyal #{i}: {c['clean']} {c['direction']}")
                send_html(msg)
                mem_key = f"{c['symbol']}_{c['tf']}"
                already_alerted[mem_key] = {"type": c["direction"].lower(), "time": time.time()}
                active_trades[mem_key] = {
                    "type": c["direction"], "entry": c["close"],
                    "tp1": c["tp1"], "tp2": c["tp2"], "tp3": c["tp3"], "sl": c["sl"],
                    "tp1_hit": False, "tp2_hit": False
                }
                set_cooldown(mem_key)
                set_entry_cooldown(c["clean"])
                time.sleep(1)
            except Exception as e:
                print(f"[RECOVERY SCAN] Error kirim sinyal #{i}: {e}")

        print(f"[RECOVERY SCAN] Selesai — {len(top3)} sinyal dikirim.")

    force_recovery_scan = False

# =============================================================================
# MAIN LOOP
# =============================================================================
def scan_symbol(symbol, mode):
    global hourly_signal_count, hourly_ready_count

    try:
        check_telegram_commands()
        clean_symbol = symbol.split(':')[0].split('/')[0]

        for tf in timeframes:
            mem_key   = f"{symbol}_{tf}"
            trade_key = f"{symbol}_{tf}"

            # Global cooldown per coin (semua TF diblokir)
            if is_entry_cooldown(clean_symbol):
                continue
            if is_in_cooldown(mem_key):
                continue

            # Reset already_alerted jika cooldown sudah habis — agar koin bisa sinyal ulang
            if mem_key in already_alerted and already_alerted[mem_key]["type"] in ("long", "short", "ready_long", "ready_short"):
                already_alerted[mem_key] = {"type": "", "time": 0}

            df = fetch_data(symbol, tf)
            if df.empty or len(df) < 100:
                continue

            close_val       = df['close'].iloc[-1]
            rsi_val         = df['rsi'].iloc[-1]
            macd_hist       = df['macd_hist'].iloc[-1]
            macd_hist_prev  = df['macd_hist'].iloc[-2]
            macd_hist_prev2 = df['macd_hist'].iloc[-3]

            ema15 = df['close'].ewm(span=15, adjust=False).mean().iloc[-1]
            ema50 = df['close'].ewm(span=50, adjust=False).mean().iloc[-1]

            bull_trend  = ema15 > ema50
            trend_label = "EMA15>50 Bullish" if bull_trend else "EMA15<50 Bearish"

            # Volume Whale: minimal 1.5x rata-rata 5 candle
            vol_ok = volume_whale_ok(df)

            macd_2c_long  = (macd_hist > macd_hist_prev > macd_hist_prev2)
            macd_2c_short = (macd_hist < macd_hist_prev < macd_hist_prev2)

            if mem_key not in already_alerted:
                already_alerted[mem_key] = {"type": "", "time": 0}

            last_alert_type = already_alerted[mem_key]["type"]
            btc_trend       = get_btc_trend()

            # ── LONG PREMIUM ──────────────────────────────────────────────
            if mode in ("long", "both") and hourly_signal_count < MAX_SIGNALS_PER_HOUR:
                already_entry_long = (last_alert_type == "long")
                rsi_ok_long = rsi_val < RSI_LONG_MAX

                if (bull_trend and rsi_ok_long and macd_2c_long
                        and vol_ok and btc_trend != "BEAR"
                        and not already_entry_long):
                    skor = score_candidate(df, "LONG", btc_trend)
                    if skor < 60:
                        print(f"[SKIP LONG] {clean_symbol} TF:{tf} Skor {skor} < 60")
                        continue

                    tp1_val, tp2_val, tp3_val, sl_val = calc_tpsl_long(close_val)
                    macd_lbl = "Naik 2 candle"

                    msg = template_long(
                        clean_symbol, tf, close_val,
                        tp1_val, tp2_val, tp3_val, sl_val,
                        rsi_val, trend_label, btc_trend, skor, macd_lbl
                    )
                    send_html(msg)
                    hourly_signal_count += 1
                    already_alerted[mem_key] = {"type": "long", "time": time.time()}
                    active_trades[trade_key] = {
                        "type": "LONG", "entry": close_val,
                        "tp1": tp1_val, "tp2": tp2_val, "tp3": tp3_val, "sl": sl_val,
                        "tp1_hit": False, "tp2_hit": False
                    }
                    set_cooldown(mem_key)
                    set_entry_cooldown(clean_symbol)
                    print(f"[ENTRY LONG] {clean_symbol} TF:{tf} RSI:{rsi_val:.1f} Skor:{skor}/100")

                elif (close_val and bull_trend and hourly_ready_count < MAX_READY_PER_HOUR):
                    rsi_ready  = RSI_LONG_MAX <= rsi_val < 50
                    macd_ready = macd_hist < macd_hist_prev
                    coin_ok    = (time.time() - ready_alerted.get(clean_symbol, 0)) >= READY_COOLDOWN_SEC
                    if (last_alert_type not in ["ready_long", "long"]
                            and coin_ok and rsi_ready and macd_ready and vol_ok):
                        send_telegram(
                            f"⏳ SIAP-SIAP — LONG\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💎 {clean_symbol}/USDT  TF: {tf}\n"
                            f"⚡ Trend  :  {trend_label}\n"
                            f"🔵 RSI    :  {rsi_val:.1f}  mendekati zona jenuh jual\n"
                            f"📡 MACD   :  Belum reversal\n"
                            f"🌊 Volume :  Whale terkonfirmasi\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💡 Tunggu sinyal ENTRY resmi!"
                        )
                        hourly_ready_count += 1
                        already_alerted[mem_key] = {"type": "ready_long", "time": time.time()}
                        ready_alerted[clean_symbol] = time.time()
                        set_cooldown(mem_key)
                        print(f"[READY LONG] {clean_symbol} TF:{tf}")

            # ── SHORT PREMIUM ─────────────────────────────────────────────
            if mode in ("short", "both") and hourly_signal_count < MAX_SIGNALS_PER_HOUR:
                already_entry_short = (last_alert_type == "short")
                rsi_ok_short = rsi_val > RSI_SHORT_MIN

                if (not bull_trend and rsi_ok_short and macd_2c_short
                        and vol_ok and btc_trend != "BULL"
                        and not already_entry_short):
                    skor = score_candidate(df, "SHORT", btc_trend)
                    if skor < 60:
                        print(f"[SKIP SHORT] {clean_symbol} TF:{tf} Skor {skor} < 60")
                        continue

                    tp1_val, tp2_val, tp3_val, sl_val = calc_tpsl_short(close_val)
                    macd_lbl = "Turun 2 candle"

                    msg = template_short(
                        clean_symbol, tf, close_val,
                        tp1_val, tp2_val, tp3_val, sl_val,
                        rsi_val, trend_label, btc_trend, skor, macd_lbl
                    )
                    send_html(msg)
                    hourly_signal_count += 1
                    already_alerted[mem_key] = {"type": "short", "time": time.time()}
                    active_trades[trade_key] = {
                        "type": "SHORT", "entry": close_val,
                        "tp1": tp1_val, "tp2": tp2_val, "tp3": tp3_val, "sl": sl_val,
                        "tp1_hit": False, "tp2_hit": False
                    }
                    set_cooldown(mem_key)
                    set_entry_cooldown(clean_symbol)
                    print(f"[ENTRY SHORT] {clean_symbol} TF:{tf} RSI:{rsi_val:.1f} Skor:{skor}/100")

                elif (close_val and not bull_trend and hourly_ready_count < MAX_READY_PER_HOUR):
                    rsi_ready  = 50 < rsi_val <= RSI_SHORT_MIN
                    macd_ready = macd_hist > macd_hist_prev
                    coin_ok    = (time.time() - ready_alerted.get(clean_symbol, 0)) >= READY_COOLDOWN_SEC
                    if (last_alert_type not in ["ready_short", "short"]
                            and coin_ok and rsi_ready and macd_ready and vol_ok):
                        send_telegram(
                            f"⏳ SIAP-SIAP — SHORT\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💎 {clean_symbol}/USDT  TF: {tf}\n"
                            f"⚡ Trend  :  {trend_label}\n"
                            f"🔵 RSI    :  {rsi_val:.1f}  mendekati zona jenuh beli\n"
                            f"📡 MACD   :  Belum reversal\n"
                            f"🌊 Volume :  Whale terkonfirmasi\n"
                            f"━━━━━━━━━━━━━━━━━━━\n"
                            f"💡 Tunggu sinyal ENTRY resmi!"
                        )
                        hourly_ready_count += 1
                        already_alerted[mem_key] = {"type": "ready_short", "time": time.time()}
                        ready_alerted[clean_symbol] = time.time()
                        set_cooldown(mem_key)
                        print(f"[READY SHORT] {clean_symbol} TF:{tf}")

    except Exception as e:
        print(f"scan_symbol error {symbol}: {e}")

while True:
    current_time = time.time()
    now          = datetime.now()

    check_telegram_commands()

    if force_recovery_scan:
        do_recovery_scan()

    if now.hour != last_reset_hour:
        hourly_signal_count = 0
        hourly_ready_count  = 0
        last_reset_hour     = now.hour
        print(f"Jam baru ({now.hour}:00). Kuota sinyal & Ready di-reset ke 0.")

    btc_trend = get_btc_trend()

    if time.time() - last_symbols_refresh >= SYMBOLS_REFRESH_INTERVAL:
        print("Refresh top gainers & losers...")
        new_g, new_l = fetch_top_gainers_losers()
        if new_g: gainer_symbols = new_g
        if new_l: loser_symbols  = new_l
        last_symbols_refresh = time.time()

    # =========================================================================
    # TAHAP 1 — CEK TP/SL SEMUA TRADE AKTIF
    # =========================================================================
    for trade_key in list(active_trades.keys()):
        try:
            parts         = trade_key.rsplit('_', 1)
            sym_tk, tf_tk = parts[0], parts[1]
            clean_sym_tk  = sym_tk.split(':')[0].split('/')[0]

            bars_tk = exchange.fetch_ohlcv(sym_tk, timeframe=tf_tk, limit=5)
            if not bars_tk or len(bars_tk) < 2:
                continue

            max_close = max(b[4] for b in bars_tk[-3:])
            min_close = min(b[4] for b in bars_tk[-3:])
            h_tk      = max(b[2] for b in bars_tk[-3:])
            l_tk      = min(b[3] for b in bars_tk[-3:])
            trade     = active_trades[trade_key]

            if trade["type"] == "LONG":
                tp1, tp2, tp3, sl = trade["tp1"], trade["tp2"], trade["tp3"], trade["sl"]

                if l_tk <= sl and not trade.get("tp1_hit", False):
                    send_html(template_sl(clean_sym_tk, trade["entry"], sl))
                    send_sticker_sl()
                    del active_trades[trade_key]; continue

                if trade.get("tp1_hit") and l_tk <= trade["entry"] and not trade.get("tp2_hit"):
                    send_html(
                        f"⚠️ Harga balik ke Entry\n"
                        f"💎 <b>{clean_sym_tk}/USDT</b>\n"
                        f"Posisi aman jika SL sudah digeser ke BEP ✅"
                    )
                    del active_trades[trade_key]; continue

                if max_close >= tp3 and trade.get("tp2_hit"):
                    send_html(template_tp3(clean_sym_tk, trade["entry"], tp1, tp2, tp3))
                    send_sticker_tp()
                    del active_trades[trade_key]
                elif max_close >= tp2 and trade.get("tp1_hit") and not trade.get("tp2_hit"):
                    send_html(template_tp2(clean_sym_tk, trade["entry"], tp1, tp2, tp3))
                    send_sticker_tp()
                    active_trades[trade_key]["tp2_hit"] = True
                elif max_close >= tp1 and not trade.get("tp1_hit"):
                    send_html(template_tp1(clean_sym_tk, trade["entry"], tp1, tp2, tp3))
                    send_sticker_tp()
                    active_trades[trade_key]["tp1_hit"] = True

            elif trade["type"] == "SHORT":
                tp1, tp2, tp3, sl = trade["tp1"], trade["tp2"], trade["tp3"], trade["sl"]

                if h_tk >= sl and not trade.get("tp1_hit", False):
                    send_html(template_sl(clean_sym_tk, trade["entry"], sl))
                    send_sticker_sl()
                    del active_trades[trade_key]; continue

                if trade.get("tp1_hit") and h_tk >= trade["entry"] and not trade.get("tp2_hit"):
                    send_html(
                        f"⚠️ Harga balik ke Entry\n"
                        f"💎 <b>{clean_sym_tk}/USDT</b>\n"
                        f"Posisi aman jika SL sudah digeser ke BEP ✅"
                    )
                    del active_trades[trade_key]; continue

                if min_close <= tp3 and trade.get("tp2_hit"):
                    send_html(template_tp3(clean_sym_tk, trade["entry"], tp1, tp2, tp3))
                    send_sticker_tp()
                    del active_trades[trade_key]
                elif min_close <= tp2 and trade.get("tp1_hit") and not trade.get("tp2_hit"):
                    send_html(template_tp2(clean_sym_tk, trade["entry"], tp1, tp2, tp3))
                    send_sticker_tp()
                    active_trades[trade_key]["tp2_hit"] = True
                elif min_close <= tp1 and not trade.get("tp1_hit"):
                    send_html(template_tp1(clean_sym_tk, trade["entry"], tp1, tp2, tp3))
                    send_sticker_tp()
                    active_trades[trade_key]["tp1_hit"] = True

        except Exception as e:
            print(f"Error cek trade {trade_key}: {e}")
            continue

    # =========================================================================
    # TAHAP 2 — SCAN PREMIUM
    # =========================================================================
    for symbol in gainer_symbols:
        if hourly_signal_count >= MAX_SIGNALS_PER_HOUR:
            break
        scan_symbol(symbol, "long")
        time.sleep(0.3)

    for symbol in loser_symbols:
        if hourly_signal_count >= MAX_SIGNALS_PER_HOUR:
            break
        scan_symbol(symbol, "short")
        time.sleep(0.3)

    print(f"Scan selesai. Sinyal jam ini: {hourly_signal_count}/{MAX_SIGNALS_PER_HOUR}. Istirahat 60 detik...")
    time.sleep(60)
