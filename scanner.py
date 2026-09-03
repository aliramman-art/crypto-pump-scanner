# ============================================================
# CRYPTO DIVERGENCE SCANNER v1.0
# 30 COINS
# 1H Hidden Divergence
#        ↓
# 1m Regular Divergence
#        ↓
# ATR SETUP
#        ↓
# Telegram
# ============================================================

import os
import time
import json
import requests
from datetime import datetime, timezone

# ============================================================
# SETTINGS
# ============================================================

VERSION = "v1.0"

BASE_URL = "https://futures.kraken.com/api/charts/v1"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SCAN_INTERVAL = 60

RSI_PERIOD = 14
ATR_PERIOD = 14

# Pivot settings
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# How many candles back we search for divergence
HIDDEN_LOOKBACK_1H = 80
REGULAR_LOOKBACK_1M = 120

# ATR setup
SL_ATR = 1.5
TP1_ATR = 1.5
TP2_ATR = 3.0
TP3_ATR = 4.5

# Minimum divergence strength
MIN_PRICE_DIFF = 0.001
MIN_RSI_DIFF = 2.0

STATE_FILE = "divergence_state.json"

# ============================================================
# 30 IMPORTANT COINS
# ============================================================

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

# ============================================================
# JSON STATE
# ============================================================

def load_state():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}


def save_state(state):
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except Exception as e:
        print("State save error:", e)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("\n" + text)
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    }

    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print("Telegram error:", e)


# ============================================================
# KRAKEN DATA
# ============================================================

def get_candles(symbol, interval, count=200):

    url = f"{BASE_URL}/{symbol}/{interval}"

    try:
        r = requests.get(url, timeout=15)

        if r.status_code != 200:
            print(f"{symbol} HTTP {r.status_code}")
            return []

        data = r.json()

        candles = []

        # Kraken Futures response normally contains candles
        raw = data.get("candles", data.get("data", []))

        for c in raw:

            try:

                if isinstance(c, dict):

                    ts = c.get("time", c.get("timestamp"))
                    o = c.get("open")
                    h = c.get("high")
                    l = c.get("low")
                    close = c.get("close")
                    volume = c.get("volume", 0)

                else:

                    if len(c) < 6:
                        continue

                    ts = c[0]
                    o = c[1]
                    h = c[2]
                    l = c[3]
                    close = c[4]
                    volume = c[5]

                candles.append({
                    "time": float(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(close),
                    "volume": float(volume)
                })

            except:
                continue

        candles.sort(key=lambda x: x["time"])

        # Remove currently forming candle
        now = time.time()

        interval_seconds = interval * 60

        candles = [
            c for c in candles
            if c["time"] + interval_seconds <= now
        ]

        return candles[-count:]

    except Exception as e:
        print(f"{symbol} data error:", e)
        return []


# ============================================================
# RSI
# ============================================================

def calculate_rsi(closes, period=14):

    if len(closes) < period + 1:
        return [None] * len(closes)

    rsi = [None] * len(closes)

    gains = []
    losses = []

    for i in range(1, period + 1):

        change = closes[i] - closes[i - 1]

        gains.append(max(change, 0))
        losses.append(max(-change, 0))

    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period

    if avg_loss == 0:
        rsi[period] = 100
    else:
        rs = avg_gain / avg_loss
        rsi[period] = 100 - (100 / (1 + rs))

    for i in range(period + 1, len(closes)):

        change = closes[i] - closes[i - 1]

        gain = max(change, 0)
        loss = max(-change, 0)

        avg_gain = ((avg_gain * (period - 1)) + gain) / period
        avg_loss = ((avg_loss * (period - 1)) + loss) / period

        if avg_loss == 0:
            rsi[i] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))

    return rsi


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    atr = sum(trs[:period]) / period

    for tr in trs[period:]:
        atr = ((atr * (period - 1)) + tr) / period

    return atr


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_lows(values, left=3, right=3):

    pivots = []

    for i in range(left, len(values) - right):

        current = values[i]

        left_values = values[i-left:i]
        right_values = values[i+1:i+right+1]

        if all(current < x for x in left_values) and \
           all(current <= x for x in right_values):

            pivots.append(i)

    return pivots


def find_pivot_highs(values, left=3, right=3):

    pivots = []

    for i in range(left, len(values) - right):

        current = values[i]

        left_values = values[i-left:i]
        right_values = values[i+1:i+right+1]

        if all(current > x for x in left_values) and \
           all(current >= x for x in right_values):

            pivots.append(i)

    return pivots


# ============================================================
# HIDDEN DIVERGENCE
# ============================================================

def detect_hidden_bullish(candles):

    closes = [c["close"] for c in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)

    lows = [c["low"] for c in candles]

    pivots = find_pivot_lows(
        lows,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    pivots = [
        i for i in pivots
        if rsi[i] is not None
    ]

    if len(pivots) < 2:
        return None

    i1 = pivots[-2]
    i2 = pivots[-1]

    price1 = lows[i1]
    price2 = lows[i2]

    rsi1 = rsi[i1]
    rsi2 = rsi[i2]

    # Hidden Bullish:
    # Price Higher Low
    # RSI Lower Low

    price_condition = price2 > price1 * (1 + MIN_PRICE_DIFF)
    rsi_condition = rsi2 < rsi1 - MIN_RSI_DIFF

    if price_condition and rsi_condition:

        return {
            "type": "HIDDEN_BULLISH",
            "index1": i1,
            "index2": i2,
            "price1": price1,
            "price2": price2,
            "rsi1": rsi1,
            "rsi2": rsi2,
            "time": candles[i2]["time"]
        }

    return None


def detect_hidden_bearish(candles):

    closes = [c["close"] for c in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)

    highs = [c["high"] for c in candles]

    pivots = find_pivot_highs(
        highs,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    pivots = [
        i for i in pivots
        if rsi[i] is not None
    ]

    if len(pivots) < 2:
        return None

    i1 = pivots[-2]
    i2 = pivots[-1]

    price1 = highs[i1]
    price2 = highs[i2]

    rsi1 = rsi[i1]
    rsi2 = rsi[i2]

    # Hidden Bearish:
    # Price Lower High
    # RSI Higher High

    price_condition = price2 < price1 * (1 - MIN_PRICE_DIFF)
    rsi_condition = rsi2 > rsi1 + MIN_RSI_DIFF

    if price_condition and rsi_condition:

        return {
            "type": "HIDDEN_BEARISH",
            "index1": i1,
            "index2": i2,
            "price1": price1,
            "price2": price2,
            "rsi1": rsi1,
            "rsi2": rsi2,
            "time": candles[i2]["time"]
        }

    return None


# ============================================================
# REGULAR DIVERGENCE
# ============================================================

def detect_regular_bullish(candles):

    closes = [c["close"] for c in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)

    lows = [c["low"] for c in candles]

    pivots = find_pivot_lows(
        lows,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    pivots = [
        i for i in pivots
        if rsi[i] is not None
    ]

    if len(pivots) < 2:
        return None

    i1 = pivots[-2]
    i2 = pivots[-1]

    price1 = lows[i1]
    price2 = lows[i2]

    rsi1 = rsi[i1]
    rsi2 = rsi[i2]

    # Regular Bullish:
    # Price Lower Low
    # RSI Higher Low

    price_condition = price2 < price1 * (1 - MIN_PRICE_DIFF)
    rsi_condition = rsi2 > rsi1 + MIN_RSI_DIFF

    if price_condition and rsi_condition:

        return {
            "type": "REGULAR_BULLISH",
            "index1": i1,
            "index2": i2,
            "price1": price1,
            "price2": price2,
            "rsi1": rsi1,
            "rsi2": rsi2,
            "time": candles[i2]["time"]
        }

    return None


def detect_regular_bearish(candles):

    closes = [c["close"] for c in candles]
    rsi = calculate_rsi(closes, RSI_PERIOD)

    highs = [c["high"] for c in candles]

    pivots = find_pivot_highs(
        highs,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    pivots = [
        i for i in pivots
        if rsi[i] is not None
    ]

    if len(pivots) < 2:
        return None

    i1 = pivots[-2]
    i2 = pivots[-1]

    price1 = highs[i1]
    price2 = highs[i2]

    rsi1 = rsi[i1]
    rsi2 = rsi[i2]

    # Regular Bearish:
    # Price Higher High
    # RSI Lower High

    price_condition = price2 > price1 * (1 + MIN_PRICE_DIFF)
    rsi_condition = rsi2 < rsi1 - MIN_RSI_DIFF

    if price_condition and rsi_condition:

        return {
            "type": "REGULAR_BEARISH",
            "index1": i1,
            "index2": i2,
            "price1": price1,
            "price2": price2,
            "rsi1": rsi1,
            "rsi2": rsi2,
            "time": candles[i2]["time"]
        }

    return None


# ============================================================
# ATR SETUP
# ============================================================

def calculate_atr_setup(candles, direction):

    if len(candles) < ATR_PERIOD + 2:
        return None

    entry = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        ATR_PERIOD
    )

    if atr is None or atr <= 0:
        return None

    if direction == "LONG":

        sl = entry - SL_ATR * atr

        tp1 = entry + TP1_ATR * atr
        tp2 = entry + TP2_ATR * atr
        tp3 = entry + TP3_ATR * atr

    else:

        sl = entry + SL_ATR * atr

        tp1 = entry - TP1_ATR * atr
        tp2 = entry - TP2_ATR * atr
        tp3 = entry - TP3_ATR * atr

    risk = abs(entry - sl)

    rr1 = abs(tp1 - entry) / risk
    rr2 = abs(tp2 - entry) / risk
    rr3 = abs(tp3 - entry) / risk

    return {
        "entry": entry,
        "atr": atr,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "rr1": rr1,
        "rr2": rr2,
        "rr3": rr3
    }


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(price):

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal(
    coin,
    direction,
    hidden,
    regular,
    setup
):

    emoji = "🟢" if direction == "LONG" else "🔴"

    hidden_name = (
        "Hidden Bullish"
        if direction == "LONG"
        else "Hidden Bearish"
    )

    regular_name = (
        "Regular Bullish"
        if direction == "LONG"
        else "Regular Bearish"
    )

    text = f"""
🚨 <b>DIVERGENCE SETUP {VERSION}</b>

{emoji} <b>{coin} / {direction}</b>

━━━━━━━━━━━━━━━━━━

📊 <b>1H HIDDEN DIVERGENCE</b>
{hidden_name}

Price:
{fmt_price(hidden["price1"])}
→
{fmt_price(hidden["price2"])}

RSI:
{hidden["rsi1"]:.1f}
→
{hidden["rsi2"]:.1f}

━━━━━━━━━━━━━━━━━━

⚡ <b>1M REGULAR DIVERGENCE</b>
{regular_name}

Price:
{fmt_price(regular["price1"])}
→
{fmt_price(regular["price2"])}

RSI:
{regular["rsi1"]:.1f}
→
{regular["rsi2"]:.1f}

━━━━━━━━━━━━━━━━━━

📐 <b>ATR SETUP</b>

Entry: <b>{fmt_price(setup["entry"])}</b>

🛑 SL:
{fmt_price(setup["sl"])}

🎯 TP1:
{fmt_price(setup["tp1"])}
RR {setup["rr1"]:.2f}

🎯 TP2:
{fmt_price(setup["tp2"])}
RR {setup["rr2"]:.2f}

🎯 TP3:
{fmt_price(setup["tp3"])}
RR {setup["rr3"]:.2f}

📏 ATR:
{fmt_price(setup["atr"])}

━━━━━━━━━━━━━━━━━━

⏱ 1H → Hidden
⚡ 1M → Regular
📐 ATR → Entry / SL / TP

<i>Only closed candles used</i>
"""

    return text.strip()


# ============================================================
# PROCESS ONE COIN
# ============================================================

def scan_coin(coin, symbol, state):

    try:

        # ----------------------------------------------------
        # STEP 1
        # 1H HIDDEN DIVERGENCE
        # ----------------------------------------------------

        candles_1h = get_candles(
            symbol,
            60,
            150
        )

        if len(candles_1h) < 50:
            return None

        hidden_bull = detect_hidden_bullish(
            candles_1h
        )

        hidden_bear = detect_hidden_bearish(
            candles_1h
        )

        hidden = None
        direction = None

        if hidden_bull:

            hidden = hidden_bull
            direction = "LONG"

        elif hidden_bear:

            hidden = hidden_bear
            direction = "SHORT"

        else:

            return None

        # ----------------------------------------------------
        # STEP 2
        # 1M REGULAR DIVERGENCE
        # ----------------------------------------------------

        candles_1m = get_candles(
            symbol,
            1,
            300
        )

        if len(candles_1m) < 80:
            return None

        if direction == "LONG":

            regular = detect_regular_bullish(
                candles_1m
            )

        else:

            regular = detect_regular_bearish(
                candles_1m
            )

        if regular is None:
            return None

        # ----------------------------------------------------
        # CHECK CHRONOLOGY
        # ----------------------------------------------------

        if regular["time"] < hidden["time"]:
            return None

        # ----------------------------------------------------
        # ATR SETUP
        # ----------------------------------------------------

        setup = calculate_atr_setup(
            candles_1m,
            direction
        )

        if setup is None:
            return None

        # ----------------------------------------------------
        # UNIQUE SIGNAL ID
        # ----------------------------------------------------

        signal_id = (
            f"{coin}_"
            f"{direction}_"
            f"{int(hidden['time'])}_"
            f"{int(regular['time'])}"
        )

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        if state.get(signal_id):
            return None

        state[signal_id] = {
            "coin": coin,
            "direction": direction,
            "hidden_time": hidden["time"],
            "regular_time": regular["time"],
            "created_at": time.time(),
            "status": "NEW",
            "entry": setup["entry"],
            "sl": setup["sl"],
            "tp1": setup["tp1"],
            "tp2": setup["tp2"],
            "tp3": setup["tp3"]
        }

        save_state(state)

        return {
            "coin": coin,
            "direction": direction,
            "hidden": hidden,
            "regular": regular,
            "setup": setup,
            "signal_id": signal_id
        }

    except Exception as e:

        print(f"{coin} scan error:", e)

        return None


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print("=" * 60)
    print(f"CRYPTO DIVERGENCE SCANNER {VERSION}")
    print("30 COINS")
    print("1H Hidden → 1M Regular → ATR")
    print("=" * 60)

    state = load_state()

    while True:

        cycle_start = time.time()

        print("\n")
        print(
            datetime.now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        )

        signals = []

        for coin, symbol in COINS.items():

            print(f"Scanning {coin}...", end=" ")

            result = scan_coin(
                coin,
                symbol,
                state
            )

            if result:

                signals.append(result)

                print("🔥 SIGNAL")

            else:

                print(".")

            time.sleep(0.15)

        # ----------------------------------------------------
        # SEND SIGNALS
        # ----------------------------------------------------

        for signal in signals:

            message = build_signal(
                signal["coin"],
                signal["direction"],
                signal["hidden"],
                signal["regular"],
                signal["setup"]
            )

            send_telegram(message)

            print(
                f"\n🚨 {signal['coin']} "
                f"{signal['direction']} SIGNAL"
            )

        # ----------------------------------------------------
        # CLEAN OLD STATE
        # ----------------------------------------------------

        now = time.time()

        old_keys = []

        for key, value in state.items():

            if not isinstance(value, dict):
                continue

            created = value.get(
                "created_at",
                now
            )

            # Keep state for 24 hours
            if now - created > 86400:

                old_keys.append(key)

        for key in old_keys:

            del state[key]

        save_state(state)

        # ----------------------------------------------------
        # WAIT
        # ----------------------------------------------------

        elapsed = time.time() - cycle_start

        sleep_time = max(
            5,
            SCAN_INTERVAL - elapsed
        )

        print(
            f"\nNext scan in "
            f"{sleep_time:.0f} seconds..."
        )

        time.sleep(sleep_time)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
