# ============================================================
# CRYPTO PUMP / DUMP SCANNER v5.4
# ============================================================
# Features:
# - Kraken Futures 5m closed candles
# - Real trade volume
# - RSI
# - ATR
# - 5m / 10m / 15m momentum
# - Ichimoku
# - Trendline breakout / breakdown
# - 2-candle confirmation
# - Entry / SL / TP1 / TP2 / TP3
# - Risk / Reward
# - Telegram alerts
# ============================================================

import os
import json
import time
import math
import requests
from statistics import mean

VERSION = "v5.4"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

COINS = {
    "BTC": "pf_xbtusd",
    "ETH": "pf_ethusd",
    "BNB": "pf_bnbusd",
    "SOL": "pf_solusd",
    "XRP": "pf_xrpusd",
    "DOGE": "pf_dogeusd",
    "ADA": "pf_adausd",
    "AVAX": "pf_avaxusd",
    "LINK": "pf_linkusd",
    "DOT": "pf_dotusd",
    "TRX": "pf_trxusd",
    "LTC": "pf_ltcusd",
    "BCH": "pf_bchusd",
    "ATOM": "pf_atomusd",
    "UNI": "pf_uniusd",
    "ETC": "pf_etcusd",
    "XLM": "pf_xlmusd",
    "NEAR": "pf_nearusd",
    "APT": "pf_aptusd",
    "FIL": "pf_filusd",
    "ARB": "pf_arbusd",
    "OP": "pf_opusd",
    "SUI": "pf_suiusd",
    "INJ": "pf_injusd",
    "AAVE": "pf_aaveusd",
    "MKR": "pf_mkrusd",
    "ALGO": "pf_algousd",
    "VET": "pf_vetusd",
    "SEI": "pf_seiusd",
    "TIA": "pf_tiausd",
}

STATE_FILE = "signal_state.json"

# ------------------------------------------------------------
# FILE HELPERS
# ------------------------------------------------------------

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception:
        return default


def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
    except Exception as e:
        print("SAVE ERROR:", e)


# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        r = requests.post(url, json=payload, timeout=15)

        if r.status_code != 200:
            print("Telegram error:", r.status_code, r.text)
            return False

        return True

    except Exception as e:
        print("Telegram exception:", e)
        return False


# ------------------------------------------------------------
# KRAKEN
# ------------------------------------------------------------

def get_candles(symbol, resolution="5m"):
    url = f"{BASE_URL}/{symbol}/{resolution}"

    try:
        r = requests.get(url, timeout=15)

        print(f"{symbol} HTTP:", r.status_code)

        if r.status_code != 200:
            return []

        data = r.json()

        result = data.get("candles", [])

        candles = []

        for c in result:
            try:
                candles.append({
                    "time": int(c["time"]),
                    "open": float(c["open"]),
                    "high": float(c["high"]),
                    "low": float(c["low"]),
                    "close": float(c["close"]),
                    "volume": float(c.get("volume", 0)),
                })
            except Exception:
                continue

        # ----------------------------------------
        # Remove current unfinished 5m candle
        # ----------------------------------------

        current_bucket = int(time.time() // 300) * 300

        closed = [
            c for c in candles
            if c["time"] < current_bucket
        ]

        closed.sort(key=lambda x: x["time"])

        print(
            f"{symbol}: {len(closed)} closed candles | "
            f"close={closed[-1]['close'] if closed else 'N/A'} "
            f"volume={closed[-1]['volume'] if closed else 'N/A'}"
        )

        return closed

    except Exception as e:
        print(f"{symbol} ERROR:", e)
        return []


# ------------------------------------------------------------
# PRICE CHANGE
# ------------------------------------------------------------

def pct_change(candles, periods):
    if len(candles) <= periods:
        return 0.0

    old = candles[-1 - periods]["close"]
    new = candles[-1]["close"]

    if old == 0:
        return 0.0

    return ((new / old) - 1) * 100


# ------------------------------------------------------------
# RSI
# ------------------------------------------------------------

def calculate_rsi(candles, period=14):
    if len(candles) < period + 1:
        return 50.0

    closes = [c["close"] for c in candles]

    gains = []
    losses = []

    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]

        if diff >= 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))

    gains = gains[-period:]
    losses = losses[-period:]

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        return 100.0

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


# ------------------------------------------------------------
# ATR
# ------------------------------------------------------------

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(1, len(candles)):
        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close),
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return sum(trs[-period:]) / period


# ------------------------------------------------------------
# VOLUME RATIO
# ------------------------------------------------------------

def volume_ratio(candles, period=20):
    if len(candles) < period + 1:
        return 0.0

    current = candles[-1]["volume"]

    previous = [
        c["volume"]
        for c in candles[-period - 1:-1]
        if c["volume"] > 0
    ]

    if not previous:
        return 0.0

    avg_volume = mean(previous)

    if avg_volume <= 0:
        return 0.0

    return current / avg_volume


# ------------------------------------------------------------
# ICHIMOKU
# ------------------------------------------------------------

def ichimoku_signal(candles):
    if len(candles) < 60:
        return 0

    highs = [c["high"] for c in candles]
    lows = [c["low"] for c in candles]
    close = candles[-1]["close"]

    tenkan_high = max(highs[-9:])
    tenkan_low = min(lows[-9:])
    tenkan = (tenkan_high + tenkan_low) / 2

    kijun_high = max(highs[-26:])
    kijun_low = min(lows[-26:])
    kijun = (kijun_high + kijun_low) / 2

    span_a = (tenkan + kijun) / 2

    span_b_high = max(highs[-52:])
    span_b_low = min(lows[-52:])
    span_b = (span_b_high + span_b_low) / 2

    cloud_top = max(span_a, span_b)
    cloud_bottom = min(span_a, span_b)

    if close > cloud_top and tenkan > kijun:
        return 1

    if close < cloud_bottom and tenkan < kijun:
        return -1

    return 0


# ------------------------------------------------------------
# SWING POINTS
# ------------------------------------------------------------

def find_swing_highs(candles, window=2):
    points = []

    if len(candles) < window * 2 + 1:
        return points

    for i in range(window, len(candles) - window):

        high = candles[i]["high"]

        left = [
            candles[j]["high"]
            for j in range(i - window, i)
        ]

        right = [
            candles[j]["high"]
            for j in range(i + 1, i + window + 1)
        ]

        if high >= max(left) and high >= max(right):
            points.append((i, high))

    return points


def find_swing_lows(candles, window=2):
    points = []

    if len(candles) < window * 2 + 1:
        return points

    for i in range(window, len(candles) - window):

        low = candles[i]["low"]

        left = [
            candles[j]["low"]
            for j in range(i - window, i)
        ]

        right = [
            candles[j]["low"]
            for j in range(i + 1, i + window + 1)
        ]

        if low <= min(left) and low <= min(right):
            points.append((i, low))

    return points


# ------------------------------------------------------------
# TRENDLINE BREAK
# ------------------------------------------------------------

def trendline_break(candles):
    """
    Returns:

    {
        "type": "BREAKOUT" / "BREAKDOWN" / None,
        "price": trendline price / None
    }

    Only closed candles are used.
    """

    if len(candles) < 30:
        return {
            "type": None,
            "price": None,
        }

    highs = find_swing_highs(candles[-60:], window=2)
    lows = find_swing_lows(candles[-60:], window=2)

    current_close = candles[-1]["close"]
    previous_close = candles[-2]["close"]

    # --------------------------------------------------------
    # BEARISH TRENDLINE -> BREAKOUT
    # --------------------------------------------------------

    if len(highs) >= 2:

        p1 = highs[-2]
        p2 = highs[-1]

        x1, y1 = p1
        x2, y2 = p2

        if x2 > x1 and y2 < y1:

            slope = (y2 - y1) / (x2 - x1)

            line_prev = y1 + slope * ((len(candles[-60:]) - 2) - x1)
            line_now = y1 + slope * ((len(candles[-60:]) - 1) - x1)

            # 0.05% breakout buffer
            buffer = line_now * 0.0005

            if (
                previous_close <= line_prev
                and current_close > line_now + buffer
            ):
                return {
                    "type": "BREAKOUT",
                    "price": line_now,
                }

    # --------------------------------------------------------
    # BULLISH TRENDLINE -> BREAKDOWN
    # --------------------------------------------------------

    if len(lows) >= 2:

        p1 = lows[-2]
        p2 = lows[-1]

        x1, y1 = p1
        x2, y2 = p2

        if x2 > x1 and y2 > y1:

            slope = (y2 - y1) / (x2 - x1)

            line_prev = y1 + slope * ((len(candles[-60:]) - 2) - x1)
            line_now = y1 + slope * ((len(candles[-60:]) - 1) - x1)

            buffer = line_now * 0.0005

            if (
                previous_close >= line_prev
                and current_close < line_now - buffer
            ):
                return {
                    "type": "BREAKDOWN",
                    "price": line_now,
                }

    return {
        "type": None,
        "price": None,
    }


# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

def calculate_score(candles):

    p5 = pct_change(candles, 1)
    p10 = pct_change(candles, 2)
    p15 = pct_change(candles, 3)

    rsi = calculate_rsi(candles)
    vol = volume_ratio(candles)

    ichi = ichimoku_signal(candles)

    tl = trendline_break(candles)

    long_score = 0
    short_score = 0

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    if p5 > 0:
        long_score += 15

    if p10 > 0:
        long_score += 15

    if p15 > 0:
        long_score += 15

    if p5 < 0:
        short_score += 15

    if p10 < 0:
        short_score += 15

    if p15 < 0:
        short_score += 15

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if rsi >= 55:
        long_score += 10

    if rsi <= 45:
        short_score += 10

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if vol >= 1.5:
        if p5 > 0:
            long_score += 15

        if p5 < 0:
            short_score += 15

    # --------------------------------------------------------
    # ICHIMOKU
    # --------------------------------------------------------

    if ichi == 1:
        long_score += 10

    if ichi == -1:
        short_score += 10

    # --------------------------------------------------------
    # TRENDLINE
    # --------------------------------------------------------

    if tl["type"] == "BREAKOUT":
        long_score += 10

    if tl["type"] == "BREAKDOWN":
        short_score += 10

    long_score = min(long_score, 100)
    short_score = min(short_score, 100)

    if long_score >= short_score:
        direction = "LONG"
        score = long_score
    else:
        direction = "SHORT"
        score = short_score

    return {
        "direction": direction,
        "score": score,
        "long_score": long_score,
        "short_score": short_score,
        "p5": p5,
        "p10": p10,
        "p15": p15,
        "rsi": rsi,
        "volume": vol,
        "ichimoku": ichi,
        "trendline": tl,
    }


# ------------------------------------------------------------
# CONFIRMATION
# ------------------------------------------------------------

def get_state():
    state = load_json(STATE_FILE, {})

    if not isinstance(state, dict):
        state = {}

    return state


def confirmation_ok(symbol, direction, candle_time):
    state = get_state()

    if symbol not in state:
        state[symbol] = {}

    item = state[symbol]

    last_direction = item.get("direction")
    last_candle = item.get("last_candle_time")

    # New direction starts a fresh confirmation sequence
    if last_direction != direction:
        state[symbol] = {
            "direction": direction,
            "count": 1,
            "last_candle_time": candle_time,
        }

        save_json(STATE_FILE, state)

        return False, 1

    # Same candle must never be counted twice
    if last_candle == candle_time:
        return item.get("count", 1) >= 2, item.get("count", 1)

    count = int(item.get("count", 0)) + 1

    state[symbol] = {
        "direction": direction,
        "count": min(count, 2),
        "last_candle_time": candle_time,
    }

    save_json(STATE_FILE, state)

    return count >= 2, min(count, 2)


# ------------------------------------------------------------
# PRICE FORMATTING
# ------------------------------------------------------------

def fmt_price(price):

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ------------------------------------------------------------
# ENTRY / SL / TP
# ------------------------------------------------------------

def calculate_levels(candles, direction):

    entry = candles[-1]["close"]

    atr = calculate_atr(candles, 14)

    if atr <= 0:
        return None

    recent = candles[-12:]

    if direction == "LONG":

        swing_low = min(c["low"] for c in recent)

        # Use the larger structural distance
        structural_risk = entry - swing_low

        min_risk = atr * 0.8

        risk = max(structural_risk, min_risk)

        # Prevent absurdly large SL
        max_risk = atr * 2.2

        risk = min(risk, max_risk)

        sl = entry - risk

        tp1 = entry + risk * 1.0
        tp2 = entry + risk * 2.0
        tp3 = entry + risk * 3.0

    else:

        swing_high = max(c["high"] for c in recent)

        structural_risk = swing_high - entry

        min_risk = atr * 0.8

        risk = max(structural_risk, min_risk)

        max_risk = atr * 2.2

        risk = min(risk, max_risk)

        sl = entry + risk

        tp1 = entry - risk * 1.0
        tp2 = entry - risk * 2.0
        tp3 = entry - risk * 3.0

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk,
        "rr_tp1": 1.0,
        "rr_tp2": 2.0,
        "rr_tp3": 3.0,
        "atr": atr,
    }


# ------------------------------------------------------------
# BTC REGIME
# ------------------------------------------------------------

def btc_regime(candles):

    if len(candles) < 20:
        return "NEUTRAL"

    p15 = pct_change(candles, 3)

    rsi = calculate_rsi(candles)

    if p15 >= 1.0 and rsi >= 55:
        return "BULLISH"

    if p15 <= -1.0 and rsi <= 45:
        return "BEARISH"

    return "NEUTRAL"


# ------------------------------------------------------------
# SIGNAL MESSAGE
# ------------------------------------------------------------

def signal_message(symbol, data, levels, confirmation):

    direction = data["direction"]

    if direction == "LONG":
        emoji = "🟢"
        title = "STRONG PUMP SIGNAL"
        trendline_text = "🔺 Trendline BREAKOUT"
    else:
        emoji = "🔴"
        title = "STRONG DUMP SIGNAL"
        trendline_text = "🔻 Trendline BREAKDOWN"

    tl = data["trendline"]["type"]

    if tl is None:
        trendline_text = "➖ Trendline: None"

    message = f"""
🚨 <b>{title}</b>
━━━━━━━━━━━━━━━━━━

{emoji} <b>{symbol}/USDT — {direction}</b>

⭐ <b>Score:</b> {data["score"]}/100

💰 <b>Entry:</b> {fmt_price(levels["entry"])}
🛑 <b>Stop Loss:</b> {fmt_price(levels["sl"])}

🎯 <b>TP1:</b> {fmt_price(levels["tp1"])}
🎯 <b>TP2:</b> {fmt_price(levels["tp2"])}
🚀 <b>TP3:</b> {fmt_price(levels["tp3"])}

📊 <b>Risk:</b> {fmt_price(levels["risk"])}
📈 <b>R:R:</b> 1:1 / 1:2 / 1:3

📊 <b>5m:</b> {data["p5"]:+.2f}%
📊 <b>10m:</b> {data["p10"]:+.2f}%
📊 <b>15m:</b> {data["p15"]:+.2f}%

📈 <b>RSI:</b> {data["rsi"]:.1f}
📊 <b>Volume:</b> {data["volume"]:.2f}x

{trendline_text}

✅ <b>Confirmation:</b> {confirmation}/2
📐 <b>ATR:</b> {fmt_price(levels["atr"])}

━━━━━━━━━━━━━━━━━━
🤖 <b>Engine {VERSION}</b>
"""

    return message.strip()


# ------------------------------------------------------------
# WATCHLIST
# ------------------------------------------------------------

def watchlist_line(rank, symbol, data):

    direction = data["direction"]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    features = []

    if data["trendline"]["type"] == "BREAKOUT":
        features.append("Trendline 🔺")

    elif data["trendline"]["type"] == "BREAKDOWN":
        features.append("Trendline 🔻")

    if data["ichimoku"] == 1:
        features.append("Ichimoku 🟢")

    elif data["ichimoku"] == -1:
        features.append("Ichimoku 🔴")

    if data["p5"] > 0 and data["p10"] > 0 and data["p15"] > 0:
        features.append("MOMENTUM")

    if data["p5"] < 0 and data["p10"] < 0 and data["p15"] < 0:
        features.append("BREAKDOWN")

    feature_text = " | ".join(features)

    return (
        f"{rank}. {emoji} <b>{symbol}</b> ⭐ "
        f"{data['score']}/100\n"
        f"5m {data['p5']:+.2f}% | "
        f"10m {data['p10']:+.2f}% | "
        f"15m {data['p15']:+.2f}%\n"
        f"RSI {data['rsi']:.1f} | "
        f"Vol {data['volume']:.2f}x\n"
        f"📌 {feature_text}"
    )


# ------------------------------------------------------------
# MAIN
# ------------------------------------------------------------

def main():

    print("=" * 60)
    print(f"CRYPTO PUMP / DUMP SCANNER {VERSION}")
    print("=" * 60)

    results = []

    btc_data = []

    # --------------------------------------------------------
    # FETCH BTC FIRST
    # --------------------------------------------------------

    btc_candles = get_candles(COINS["BTC"])

    if btc_candles:
        btc_data = btc_candles

    regime = btc_regime(btc_data)

    print("BTC regime:", regime)

    # --------------------------------------------------------
    # SCAN ALL COINS
    # --------------------------------------------------------

    for symbol, kraken_symbol in COINS.items():

        candles = get_candles(kraken_symbol)

        if len(candles) < 60:
            print(symbol, "not enough candles")
            continue

        data = calculate_score(candles)

        data["candles"] = candles

        results.append({
            "symbol": symbol,
            "data": data,
        })

    # --------------------------------------------------------
    # SORT WATCHLIST
    # --------------------------------------------------------

    results.sort(
        key=lambda x: x["data"]["score"],
        reverse=True
    )

    top5 = results[:5]

    watchlist = [
        "👀 <b>TOP 5 WATCHLIST v5.4</b>",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for i, item in enumerate(top5, 1):

        watchlist.append(
            watchlist_line(
                i,
                item["symbol"],
                item["data"]
            )
        )

    watchlist.append("━━━━━━━━━━━━━━━━━━")
    watchlist.append(f"₿ BTC Regime: <b>{regime}</b>")
    watchlist.append("📡 Closed 5M TRADE OHLCV")
    watchlist.append("📊 REAL VOLUME")
    watchlist.append("🤖 Engine v5.4")

    watchlist_message = "\n".join(watchlist)

    print(watchlist_message)

    # Send watchlist every run
    send_telegram(watchlist_message)

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    for item in results:

        symbol = item["symbol"]
        data = item["data"]
        candles = data["candles"]

        score = data["score"]
        volume = data["volume"]
        direction = data["direction"]

        # --------------------------------------------
        # SCORE FILTER
        # --------------------------------------------

        if score < 75:
            continue

        # --------------------------------------------
        # VOLUME FILTER
        # --------------------------------------------

        if volume < 1.5:
            continue

        # --------------------------------------------
        # MOMENTUM DIRECTION
        # --------------------------------------------

        if direction == "LONG":

            if not (
                data["p5"] > 0
                and data["p10"] > 0
                and data["p15"] > 0
            ):
                continue

        else:

            if not (
                data["p5"] < 0
                and data["p10"] < 0
                and data["p15"] < 0
            ):
                continue

        # --------------------------------------------
        # TRENDLINE BREAK
        # --------------------------------------------

        tl_type = data["trendline"]["type"]

        if direction == "LONG" and tl_type != "BREAKOUT":
            continue

        if direction == "SHORT" and tl_type != "BREAKDOWN":
            continue

        # --------------------------------------------
        # CONFIRMATION
        # --------------------------------------------

        candle_time = candles[-1]["time"]

        confirmed, count = confirmation_ok(
            symbol,
            direction,
            candle_time
        )

        if not confirmed:
            print(
                f"{symbol}: waiting confirmation "
                f"{count}/2"
            )
            continue

        # --------------------------------------------
        # ENTRY / SL / TP
        # --------------------------------------------

        levels = calculate_levels(
            candles,
            direction
        )

        if not levels:
            continue

        # --------------------------------------------
        # FINAL MESSAGE
        # --------------------------------------------

        message = signal_message(
            symbol,
            data,
            levels,
            2
        )

        print(message)

        send_telegram(message)

        # --------------------------------------------
        # ONE SIGNAL PER COIN PER CANDLE
        # --------------------------------------------

        state = get_state()

        if symbol not in state:
            state[symbol] = {}

        state[symbol]["last_signal_time"] = candle_time
        state[symbol]["last_signal_direction"] = direction

        save_json(STATE_FILE, state)


if __name__ == "__main__":
    main()
