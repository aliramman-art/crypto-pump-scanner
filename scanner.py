import os
import json
import time
import requests
from statistics import mean


# ============================================================
# CRYPTO PUMP / DUMP SCANNER v5.5
# KRAKEN FUTURES - CLOSED 5M CANDLES
# UT BOT SETUP ENGINE
# ============================================================

VERSION = "5.5"

# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# KRAKEN
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

STATE_FILE = "signal_state.json"

CANDLE_HISTORY = 500

# ============================================================
# UT BOT SETTINGS
# ============================================================

UT_KEY_VALUE = 3
UT_ATR_PERIOD = 10

# ============================================================
# COINS
# ============================================================

COINS = {
    "BTC": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "ADA": "PF_ADAUSD",
    "DOGE": "PF_DOGEUSD",
    "AVAX": "PF_AVAXUSD",
    "LINK": "PF_LINKUSD",
    "DOT": "PF_DOTUSD",
    "LTC": "PF_LTCUSD",
    "BCH": "PF_BCHUSD",
    "UNI": "PF_UNIUSD",
    "ATOM": "PF_ATOMUSD",
    "FIL": "PF_FILUSD",
    "AAVE": "PF_AAVEUSD",
    "SUI": "PF_SUIUSD",
    "SEI": "PF_SEIUSD",
    "TRX": "PF_TRXUSD",
    "NEAR": "PF_NEARUSD",
    "APT": "PF_APTUSD",
    "ARB": "PF_ARBUSD",
    "OP": "PF_OPUSD",
    "INJ": "PF_INJUSD",
    "TIA": "PF_TIAUSD",
    "ETC": "PF_ETCUSD",
    "XLM": "PF_XLMUSD",
    "HBAR": "PF_HBARUSD",
    "ALGO": "PF_ALGOUSD",
    "VET": "PF_VETUSD",
    "MATIC": "PF_MATICUSD",
}


# ============================================================
# JSON STATE
# ============================================================

def load_json_state():
    if not os.path.exists(STATE_FILE):
        return {}

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_json_state(state):
    try:
        tmp_file = STATE_FILE + ".tmp"

        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)

        os.replace(tmp_file, STATE_FILE)

    except Exception as e:
        print("STATE SAVE ERROR:", e)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(message)
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:
        r = requests.post(url, json=payload, timeout=15)

        if r.ok:
            return True

        print("TELEGRAM ERROR:", r.status_code, r.text)
        return False

    except Exception as e:
        print("TELEGRAM EXCEPTION:", e)
        return False


def send_telegram_chunks(message, chunk_size=3800):
    if len(message) <= chunk_size:
        send_telegram(message)
        return

    chunks = []

    while message:
        if len(message) <= chunk_size:
            chunks.append(message)
            break

        cut = message.rfind("\n", 0, chunk_size)

        if cut <= 0:
            cut = chunk_size

        chunks.append(message[:cut])
        message = message[cut:].lstrip()

    for chunk in chunks:
        send_telegram(chunk)
        time.sleep(0.3)


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(symbol, interval=5):
    url = f"{BASE_URL}/{symbol}/{interval}"

    try:
        r = requests.get(url, timeout=15)

        if not r.ok:
            print("KRAKEN ERROR:", symbol, r.status_code)
            return []

        data = r.json()

    except Exception as e:
        print("KRAKEN REQUEST ERROR:", symbol, e)
        return []

    raw = []

    if isinstance(data, dict):
        if isinstance(data.get("candles"), list):
            raw = data["candles"]

        elif isinstance(data.get("data"), list):
            raw = data["data"]

        elif isinstance(data.get("result"), list):
            raw = data["result"]

    elif isinstance(data, list):
        raw = data

    candles = []

    for x in raw:

        try:
            if isinstance(x, dict):

                timestamp = (
                    x.get("time")
                    or x.get("timestamp")
                    or x.get("ts")
                )

                o = x.get("open")
                h = x.get("high")
                l = x.get("low")
                c = x.get("close")
                v = x.get("volume", 0)

            elif isinstance(x, list) and len(x) >= 6:

                timestamp = x[0]
                o = x[1]
                h = x[2]
                l = x[3]
                c = x[4]
                v = x[5]

            else:
                continue

            timestamp = float(timestamp)

            # milliseconds -> seconds
            if timestamp > 10_000_000_000:
                timestamp /= 1000

            candle = {
                "time": int(timestamp),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v or 0)
            }

            candles.append(candle)

        except Exception:
            continue

    candles.sort(key=lambda x: x["time"])

    if not candles:
        return []

    # --------------------------------------------------------
    # REMOVE CURRENT / OPEN 5M CANDLE
    # --------------------------------------------------------

    now = int(time.time())
    current_bucket = now - (now % (interval * 60))

    candles = [
        c for c in candles
        if c["time"] < current_bucket
    ]

    return candles[-CANDLE_HISTORY:]


# ============================================================
# BASIC FUNCTIONS
# ============================================================

def pct_change(a, b):
    if a == 0:
        return 0

    return ((b - a) / a) * 100


def calculate_rsi(candles, period=14):
    if len(candles) < period + 1:
        return None

    closes = [x["close"] for x in candles]

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

    avg_gain = mean(gains)
    avg_loss = mean(losses)

    if avg_loss == 0:
        return 100

    rs = avg_gain / avg_loss

    return 100 - (100 / (1 + rs))


def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return mean(trs[-period:])


def volume_ratio(candles, period=20):
    if len(candles) < period + 1:
        return 0

    current_volume = candles[-1]["volume"]

    previous_volumes = [
        x["volume"]
        for x in candles[-period-1:-1]
    ]

    avg_volume = mean(previous_volumes)

    if avg_volume <= 0:
        return 0

    return current_volume / avg_volume


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(candles):
    if len(candles) < 52:
        return None

    highs = [x["high"] for x in candles]
    lows = [x["low"] for x in candles]

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

    price = candles[-1]["close"]

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "price": price
    }


# ============================================================
# SWING / TRENDLINE
# ============================================================

def swing_high(candles, lookback=20):
    if len(candles) < lookback:
        return None

    return max(
        x["high"]
        for x in candles[-lookback:]
    )


def swing_low(candles, lookback=20):
    if len(candles) < lookback:
        return None

    return min(
        x["low"]
        for x in candles[-lookback:]
    )


def trendline_status(candles):
    if len(candles) < 30:
        return "NEUTRAL"

    recent = candles[-10:]
    previous = candles[-30:-10]

    recent_high = max(x["high"] for x in recent)
    previous_high = max(x["high"] for x in previous)

    recent_low = min(x["low"] for x in recent)
    previous_low = min(x["low"] for x in previous)

    close = candles[-1]["close"]

    if recent_high > previous_high and close > previous_high:
        return "BREAKOUT"

    if recent_low < previous_low and close < previous_low:
        return "BREAKDOWN"

    return "NEUTRAL"


# ============================================================
# NORMAL SCANNER SCORE
# ============================================================

def scanner_score(candles):
    if len(candles) < 60:
        return {
            "score": 0,
            "direction": "NEUTRAL",
            "rsi": None,
            "atr": None,
            "vol_ratio": 0,
            "ichimoku": None,
            "trendline": "NEUTRAL"
        }

    close = candles[-1]["close"]

    rsi = calculate_rsi(candles)
    atr = calculate_atr(candles)
    vol_ratio = volume_ratio(candles)
    ichi = calculate_ichimoku(candles)
    trendline = trendline_status(candles)

    score = 0

    if rsi is not None:

        if rsi >= 60:
            score += 20

        elif rsi <= 40:
            score -= 20

    if vol_ratio >= 2:
        score += 20

    elif vol_ratio >= 1.3:
        score += 10

    if ichi:

        if close > ichi["tenkan"]:
            score += 10
        else:
            score -= 10

        if close > ichi["kijun"]:
            score += 10
        else:
            score -= 10

        cloud_top = max(
            ichi["span_a"],
            ichi["span_b"]
        )

        cloud_bottom = min(
            ichi["span_a"],
            ichi["span_b"]
        )

        if close > cloud_top:
            score += 10

        elif close < cloud_bottom:
            score -= 10

    if trendline == "BREAKOUT":
        score += 20

    elif trendline == "BREAKDOWN":
        score -= 20

    score = max(-100, min(100, score))

    if score >= 20:
        direction = "LONG"

    elif score <= -20:
        direction = "SHORT"

    else:
        direction = "NEUTRAL"

    return {
        "score": score,
        "direction": direction,
        "rsi": rsi,
        "atr": atr,
        "vol_ratio": vol_ratio,
        "ichimoku": ichi,
        "trendline": trendline
    }


# ============================================================
# 2 CANDLE CONFIRMATION
# ============================================================

def two_candle_confirmation(candles, direction):
    if len(candles) < 3:
        return False

    c1 = candles[-2]
    c2 = candles[-1]

    if direction == "LONG":

        first = c1["close"] > c1["open"]
        second = c2["close"] > c2["open"]

        return first and second

    if direction == "SHORT":

        first = c1["close"] < c1["open"]
        second = c2["close"] < c2["open"]

        return first and second

    return False


# ============================================================
# PRICE FORMAT
# ============================================================

def format_price(price):
    if price is None:
        return "N/A"

    price = float(price)

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
# PNL
# ============================================================

def calculate_pnl_pct(entry, exit_price, direction):
    entry = float(entry)
    exit_price = float(exit_price)

    if entry == 0:
        return 0

    if direction == "LONG":
        return ((exit_price - entry) / entry) * 100

    return ((entry - exit_price) / entry) * 100


# ============================================================
# NORMAL SIGNAL LEVELS
# ============================================================

def calculate_normal_levels(candles, direction):
    atr = calculate_atr(candles)

    if atr is None:
        return None

    entry = candles[-1]["close"]

    if direction == "LONG":

        sl = entry - (atr * 1.2)

        risk = entry - sl

        tp1 = entry + risk
        tp2 = entry + (risk * 1.5)
        tp3 = entry + (risk * 2)

    else:

        sl = entry + (atr * 1.2)

        risk = sl - entry

        tp1 = entry - risk
        tp2 = entry - (risk * 1.5)
        tp3 = entry - (risk * 2)

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk": risk,
        "atr": atr
    }


# ============================================================
# BTC REGIME
# ============================================================

def btc_regime():
    try:
        candles = get_candles(COINS["BTC"], 5)

        if len(candles) < 60:
            return "UNKNOWN"

        result = scanner_score(candles)

        if result["score"] >= 20:
            return "BULLISH"

        if result["score"] <= -20:
            return "BEARISH"

        return "NEUTRAL"

    except Exception:
        return "UNKNOWN"


# ============================================================
# NORMAL SIGNAL MESSAGE
# ============================================================

def normal_signal_message(symbol, candles, analysis):
    direction = analysis["direction"]

    levels = calculate_normal_levels(
        candles,
        direction
    )

    if not levels:
        return None

    emoji = "🟢" if direction == "LONG" else "🔴"

    return (
        f"🚨 **NORMAL SIGNAL v5.5**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} **#{symbol} — {direction}**\n\n"
        f"⭐ Score: {analysis['score']}/100\n"
        f"💰 Entry: {format_price(levels['entry'])}\n"
        f"🛑 SL: {format_price(levels['sl'])}\n"
        f"🎯 TP1: {format_price(levels['tp1'])}\n"
        f"🎯 TP2: {format_price(levels['tp2'])}\n"
        f"🎯 TP3: {format_price(levels['tp3'])}\n\n"
        f"RSI: {analysis['rsi']:.1f}\n"
        f"Volume: {analysis['vol_ratio']:.2f}x\n"
        f"Trendline: {analysis['trendline']}\n"
        f"BTC Regime: {btc_regime()}"
    )


# ============================================================
# WATCHLIST
# ============================================================

def create_watchlist(results, limit=5):

    sorted_results = sorted(
        results,
        key=lambda x: abs(x["analysis"]["score"]),
        reverse=True
    )

    top = sorted_results[:limit]

    lines = [
        "👀 **TOP 5 WATCHLIST v5.5**",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for i, item in enumerate(top, 1):

        symbol = item["symbol"]
        candles = item["candles"]
        a = item["analysis"]

        direction = a["direction"]

        if direction == "LONG":
            emoji = "🟢"

        elif direction == "SHORT":
            emoji = "🔴"

        else:
            emoji = "⚪"

        p5 = 0
        p10 = 0
        p15 = 0

        if len(candles) >= 2:
            p5 = pct_change(
                candles[-2]["close"],
                candles[-1]["close"]
            )

        if len(candles) >= 3:
            p10 = pct_change(
                candles[-3]["close"],
                candles[-1]["close"]
            )

        if len(candles) >= 4:
            p15 = pct_change(
                candles[-4]["close"],
                candles[-1]["close"]
            )

        rsi = a["rsi"]

        rsi_text = (
            f"{rsi:.1f}"
            if rsi is not None
            else "N/A"
        )

        lines.append(
            f"{i}. {emoji} **{symbol}** ⭐ "
            f"{abs(a['score'])}/100\n"
            f"5m {p5:+.2f}% | "
            f"10m {p10:+.2f}% | "
            f"15m {p15:+.2f}%\n"
            f"RSI {rsi_text} | "
            f"Vol {a['vol_ratio']:.2f}x\n"
            f"📌 {a['trendline']} | "
            f"{direction}"
        )

    return "\n".join(lines)


# ============================================================
# UT BOT ATR
# ============================================================

def calculate_ut_atr(candles, period=10):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(current["high"] - previous["close"]),
            abs(current["low"] - previous["close"])
        )

        trs.append(tr)

    return mean(trs[-period:])


# ============================================================
# UT BOT SIGNAL
# ============================================================

def calculate_utbot_signal(candles):

    if len(candles) < UT_ATR_PERIOD + 5:
        return None

    closes = [
        x["close"]
        for x in candles
    ]

    atr = calculate_ut_atr(
        candles,
        UT_ATR_PERIOD
    )

    if atr is None:
        return None

    key = UT_KEY_VALUE

    stop_distance = key * atr

    trailing = [None] * len(closes)

    trailing[0] = closes[0] - stop_distance

    for i in range(1, len(closes)):

        prev_close = closes[i - 1]
        close = closes[i]

        previous_stop = trailing[i - 1]

        if previous_stop is None:
            previous_stop = close - stop_distance

        if close > previous_stop and prev_close > previous_stop:

            trailing[i] = max(
                previous_stop,
                close - stop_distance
            )

        elif close < previous_stop and prev_close < previous_stop:

            trailing[i] = min(
                previous_stop,
                close + stop_distance
            )

        elif close > previous_stop:

            trailing[i] = close - stop_distance

        else:

            trailing[i] = close + stop_distance

    previous_close = closes[-2]
    current_close = closes[-1]

    previous_stop = trailing[-2]
    current_stop = trailing[-1]

    if (
        previous_close <= previous_stop
        and current_close > current_stop
    ):
        signal = "BUY"

    elif (
        previous_close >= previous_stop
        and current_close < current_stop
    ):
        signal = "SELL"

    else:
        signal = None

    if not signal:
        return None

    return {
        "signal": signal,
        "price": current_close,
        "atr": atr,
        "candle_time": candles[-1]["time"],
        "trailing_stop": current_stop
    }


# ============================================================
# UT SETUP LEVELS
# ============================================================

def calculate_ut_setup_levels(candles, signal):

    atr = calculate_ut_atr(
        candles,
        UT_ATR_PERIOD
    )

    if atr is None:
        return None

    entry = candles[-1]["close"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if signal == "BUY":

        sl = entry - (atr * 1.0)

        risk = entry - sl

        tp1 = entry + risk
        tp15 = entry + (risk * 1.5)
        tp2 = entry + (risk * 2)

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        sl = entry + (atr * 1.0)

        risk = sl - entry

        tp1 = entry - risk
        tp15 = entry - (risk * 1.5)
        tp2 = entry - (risk * 2)

    return {
        "entry": entry,
        "sl": sl,
        "tp1": tp1,
        "tp15": tp15,
        "tp2": tp2,
        "risk": risk,
        "atr": atr
    }


# ============================================================
# UT SETUP MESSAGE
# ============================================================

def ut_setup_message(symbol, signal, levels, setup_id):

    direction = "LONG" if signal == "BUY" else "SHORT"

    emoji = "🟢" if direction == "LONG" else "🔴"

    return (
        f"🚨 **UT BOT SIGNAL**\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{emoji} **#{symbol} — {direction}**\n\n"
        f"🆔 ID: `{setup_id}`\n\n"
        f"💰 Entry: {format_price(levels['entry'])}\n"
        f"🛑 SL: {format_price(levels['sl'])}\n"
        f"🎯 TP1: {format_price(levels['tp1'])} — 1R\n"
        f"🎯 TP2: {format_price(levels['tp15'])} — 1.5R\n"
        f"🎯 TP3: {format_price(levels['tp2'])} — 2R\n\n"
        f"📊 ATR: {format_price(levels['atr'])}"
    )


# ============================================================
# UT STATE
# IMPORTANT:
# THIS ALSO REPAIRS OLD CLOSED SIGNALS
# ============================================================

def get_ut_state():

    state = load_json_state()

    if not isinstance(state, dict):
        state = {}

    if "_utbot" not in state:
        state["_utbot"] = {}

    if not isinstance(state["_utbot"], dict):
        state["_utbot"] = {}

    if "setups" not in state["_utbot"]:
        state["_utbot"]["setups"] = []

    if not isinstance(
        state["_utbot"]["setups"],
        list
    ):
        state["_utbot"]["setups"] = []

    if "stats" not in state["_utbot"]:
        state["_utbot"]["stats"] = {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "tp1": 0,
            "tp15": 0,
            "tp2": 0
        }

    stats = state["_utbot"]["stats"]

    for key in [
        "total",
        "wins",
        "losses",
        "tp1",
        "tp15",
        "tp2"
    ]:

        if key not in stats:
            stats[key] = 0

    # ========================================================
    # REPAIR LEGACY CLOSED SIGNALS
    # ========================================================

    repaired = False

    for setup in state["_utbot"]["setups"]:

        if setup.get("status") != "CLOSED":
            continue

        result = setup.get("result")

        entry = setup.get("entry")

        if entry is None:
            continue

        try:
            entry = float(entry)
        except Exception:
            continue

        old_exit = setup.get("exit_price")

        try:
            old_exit = (
                float(old_exit)
                if old_exit is not None
                else None
            )
        except Exception:
            old_exit = None

        # ----------------------------------------------------
        # WIN 1R
        # ----------------------------------------------------

        if result == "WIN_1R":

            tp = setup.get("tp1")

            if tp is not None:

                tp = float(tp)

                if (
                    old_exit is None
                    or abs(old_exit - entry) < 1e-12
                ):

                    setup["exit_price"] = tp

                    direction = setup.get(
                        "direction",
                        "LONG"
                    )

                    setup["result_pct"] = calculate_pnl_pct(
                        entry,
                        tp,
                        direction
                    )

                    repaired = True

        # ----------------------------------------------------
        # WIN 1.5R
        # ----------------------------------------------------

        elif result == "WIN_15R":

            tp = setup.get("tp15")

            if tp is not None:

                tp = float(tp)

                if (
                    old_exit is None
                    or abs(old_exit - entry) < 1e-12
                ):

                    setup["exit_price"] = tp

                    direction = setup.get(
                        "direction",
                        "LONG"
                    )

                    setup["result_pct"] = calculate_pnl_pct(
                        entry,
                        tp,
                        direction
                    )

                    repaired = True

        # ----------------------------------------------------
        # WIN 2R
        # ----------------------------------------------------

        elif result == "WIN_2R":

            tp = setup.get("tp2")

            if tp is not None:

                tp = float(tp)

                if (
                    old_exit is None
                    or abs(old_exit - entry) < 1e-12
                ):

                    setup["exit_price"] = tp

                    direction = setup.get(
                        "direction",
                        "LONG"
                    )

                    setup["result_pct"] = calculate_pnl_pct(
                        entry,
                        tp,
                        direction
                    )

                    repaired = True

        # ----------------------------------------------------
        # LOSS
        # ----------------------------------------------------

        elif result == "LOSS":

            sl = setup.get("sl")

            if sl is not None:

                sl = float(sl)

                if (
                    old_exit is None
                    or abs(old_exit - entry) < 1e-12
                ):

                    setup["exit_price"] = sl

                    direction = setup.get(
                        "direction",
                        "LONG"
                    )

                    setup["result_pct"] = calculate_pnl_pct(
                        entry,
                        sl,
                        direction
                    )

                    repaired = True

    if repaired:
        save_json_state(state)

    return state


# ============================================================
# UT STATISTICS
# ============================================================

def calculate_ut_stats(state):

    setups = state["_utbot"]["setups"]

    total = len(setups)

    closed = [
        x for x in setups
        if x.get("status") == "CLOSED"
    ]

    wins = [
        x for x in closed
        if str(x.get("result", "")).startswith("WIN")
    ]

    losses = [
        x for x in closed
        if x.get("result") == "LOSS"
    ]

    win_rate = (
        len(wins) / len(closed) * 100
        if closed
        else 0
    )

    return {
        "total": total,
        "closed": len(closed),
        "open": total - len(closed),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate
    }


# ============================================================
# UPDATE UT SETUPS
# ============================================================

def update_ut_setups(state, candles_by_symbol):

    changed = False

    setups = state["_utbot"]["setups"]

    for setup in setups:

        if setup.get("status") == "CLOSED":
            continue

        symbol = setup.get("symbol")

        candles = candles_by_symbol.get(symbol)

        if not candles:
            continue

        entry = float(setup["entry"])
        sl = float(setup["sl"])
        tp1 = float(setup["tp1"])
        tp15 = float(setup["tp15"])
        tp2 = float(setup["tp2"])

        direction = setup["direction"]

        # ====================================================
        # CHECK ALL NEW CLOSED CANDLES
        # ====================================================

        last_processed = setup.get(
            "last_checked_candle",
            setup.get("candle_time", 0)
        )

        new_candles = [
            c for c in candles
            if c["time"] > last_processed
        ]

        if not new_candles:
            continue

        for candle in new_candles:

            high = candle["high"]
            low = candle["low"]

            hit_sl = False
            hit_tp1 = False
            hit_tp15 = False
            hit_tp2 = False

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if direction == "LONG":

                hit_sl = low <= sl
                hit_tp1 = high >= tp1
                hit_tp15 = high >= tp15
                hit_tp2 = high >= tp2

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                hit_sl = high >= sl
                hit_tp1 = low <= tp1
                hit_tp15 = low <= tp15
                hit_tp2 = low <= tp2

            # =================================================
            # CONSERVATIVE SAME-CANDLE RULE
            # If SL and TP are both touched in same candle:
            # LOSS
            # =================================================

            if hit_sl and (
                hit_tp1
                or hit_tp15
                or hit_tp2
            ):

                setup["status"] = "CLOSED"
                setup["result"] = "LOSS"

                setup["exit_price"] = sl

                setup["result_pct"] = calculate_pnl_pct(
                    entry,
                    sl,
                    direction
                )

                setup["close_candle_time"] = candle["time"]

                state["_utbot"]["stats"]["losses"] += 1

                changed = True

                break

            # =================================================
            # TP3 / 2R
            # =================================================

            if hit_tp2:

                setup["status"] = "CLOSED"
                setup["result"] = "WIN_2R"

                setup["exit_price"] = tp2

                setup["result_pct"] = calculate_pnl_pct(
                    entry,
                    tp2,
                    direction
                )

                setup["close_candle_time"] = candle["time"]

                state["_utbot"]["stats"]["wins"] += 1
                state["_utbot"]["stats"]["tp2"] += 1

                changed = True

                break

            # =================================================
            # TP2 / 1.5R
            # =================================================

            if hit_tp15:

                setup["status"] = "CLOSED"
                setup["result"] = "WIN_15R"

                setup["exit_price"] = tp15

                setup["result_pct"] = calculate_pnl_pct(
                    entry,
                    tp15,
                    direction
                )

                setup["close_candle_time"] = candle["time"]

                state["_utbot"]["stats"]["wins"] += 1
                state["_utbot"]["stats"]["tp15"] += 1

                changed = True

                break

            # =================================================
            # TP1 / 1R
            # =================================================

            if hit_tp1:

                setup["status"] = "CLOSED"
                setup["result"] = "WIN_1R"

                setup["exit_price"] = tp1

                setup["result_pct"] = calculate_pnl_pct(
                    entry,
                    tp1,
                    direction
                )

                setup["close_candle_time"] = candle["time"]

                state["_utbot"]["stats"]["wins"] += 1
                state["_utbot"]["stats"]["tp1"] += 1

                changed = True

                break

            # ------------------------------------------------
            # Mark candle processed
            # ------------------------------------------------

            setup["last_checked_candle"] = candle["time"]

        # ----------------------------------------------------
        # If still open, remember latest checked candle
        # ----------------------------------------------------

        if setup.get("status") != "CLOSED":

            setup["last_checked_candle"] = new_candles[-1]["time"]

            setup["last_price"] = candles[-1]["close"]

            setup["current_pnl_pct"] = calculate_pnl_pct(
                entry,
                candles[-1]["close"],
                direction
            )

            changed = True

    if changed:
        save_json_state(state)

    return state


# ============================================================
# UT LIVE STATUS MESSAGE
# ============================================================

def ut_live_status_message(state, candles_by_symbol):

    setups = state["_utbot"]["setups"]

    if not setups:
        return (
            "📊 **UT BOT LIVE STATUS**\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "📭 هنوز سیگنال UT Bot ثبت نشده است."
        )

    lines = [
        "📊 **UT BOT LIVE STATUS**",
        "━━━━━━━━━━━━━━━━━━"
    ]

    for i, setup in enumerate(setups, 1):

        symbol = setup.get("symbol", "?")

        direction = setup.get(
            "direction",
            "LONG"
        )

        status = setup.get(
            "status",
            "OPEN"
        )

        setup_id = setup.get(
            "id",
            "N/A"
        )

        entry = float(
            setup.get("entry", 0)
        )

        candles = candles_by_symbol.get(symbol)

        # ====================================================
        # OPEN
        # ====================================================

        if status != "CLOSED":

            current_price = setup.get(
                "last_price",
                entry
            )

            if candles:
                current_price = candles[-1]["close"]

            pnl = calculate_pnl_pct(
                entry,
                current_price,
                direction
            )

            pnl_emoji = "🟢" if pnl >= 0 else "🔴"

            lines.append(
                f"\n{'🟢' if direction == 'LONG' else '🔴'} "
                f"**#{i} {symbol}/USDT — {direction}**\n"
                f"🆔 ID: `{setup_id}`\n"
                f"📂 Status: **OPEN**\n"
                f"💰 Entry: {format_price(entry)}\n"
                f"📍 Current: {format_price(current_price)}\n"
                f"{pnl_emoji} PnL: {pnl:+.2f}%\n"
                f"🛑 SL: {format_price(setup.get('sl'))}\n"
                f"🎯 TP1: {format_price(setup.get('tp1'))}\n"
                f"🎯 TP2: {format_price(setup.get('tp15'))}\n"
                f"🎯 TP3: {format_price(setup.get('tp2'))}"
            )

        # ====================================================
        # CLOSED
        # ====================================================

        else:

            result = setup.get(
                "result",
                "UNKNOWN"
            )

            exit_price = setup.get(
                "exit_price",
                entry
            )

            result_pct = setup.get(
                "result_pct",
                0
            )

            if result == "LOSS":

                result_text = "❌ SL HIT"

            elif result == "WIN_1R":

                result_text = "✅ TP1 HIT — 1R"

            elif result == "WIN_15R":

                result_text = "✅ TP2 HIT — 1.5R"

            elif result == "WIN_2R":

                result_text = "🏆 TP3 HIT — 2R"

            else:

                result_text = f"ℹ️ {result}"

            lines.append(
                f"\n{'🟢' if direction == 'LONG' else '🔴'} "
                f"**#{i} {symbol}/USDT — {direction}**\n"
                f"🆔 ID: `{setup_id}`\n"
                f"📂 Status: **CLOSED**\n"
                f"💰 Entry: {format_price(entry)}\n"
                f"🏁 Exit: {format_price(exit_price)}\n"
                f"🎯 Result R: "
                f"{'1R' if result == 'WIN_1R' else '1.5R' if result == 'WIN_15R' else '2R' if result == 'WIN_2R' else '0R'}\n"
                f"📊 Result %: {float(result_pct):+.2f}%\n"
                f"{result_text}"
            )

    # ========================================================
    # STATS
    # ========================================================

    stats = calculate_ut_stats(state)

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━\n"
        "📈 **UT BOT STATISTICS**\n"
        f"📊 Total Signals: {stats['total']}\n"
        f"🟢 Wins: {stats['wins']}\n"
        f"🔴 Losses: {stats['losses']}\n"
        f"🟡 Open: {stats['open']}\n"
        f"📉 Closed: {stats['closed']}\n"
        f"🎯 Win Rate: {stats['win_rate']:.1f}%"
    )

    return "\n".join(lines)


# ============================================================
# PROCESS UT TOP 5
# ============================================================

def process_ut_top5(
    top5_results,
    state,
    candles_by_symbol
):

    existing_keys = set()

    for setup in state["_utbot"]["setups"]:

        key = setup.get("signal_key")

        if key:
            existing_keys.add(key)

    new_signals = []

    # ========================================================
    # IMPORTANT:
    # PROCESS ALL FRESH UT SIGNALS IN TOP5
    # NOT ONLY THE BEST ONE
    # ========================================================

    for item in top5_results:

        symbol = item["symbol"]
        candles = item["candles"]

        ut = calculate_utbot_signal(candles)

        if not ut:
            continue

        signal = ut["signal"]

        candle_time = ut["candle_time"]

        signal_key = (
            f"{symbol}_"
            f"{signal}_"
            f"{candle_time}"
        )

        # ----------------------------------------------------
        # DUPLICATE PROTECTION
        # ----------------------------------------------------

        if signal_key in existing_keys:
            continue

        levels = calculate_ut_setup_levels(
            candles,
            signal
        )

        if not levels:
            continue

        direction = (
            "LONG"
            if signal == "BUY"
            else "SHORT"
        )

        setup_id = (
            f"UT-{symbol}-"
            f"{signal}-"
            f"{int(candle_time)}"
        )

        setup = {
            "id": setup_id,
            "symbol": symbol,
            "direction": direction,
            "signal": signal,

            "signal_key": signal_key,

            "candle_time": candle_time,

            "entry": levels["entry"],
            "sl": levels["sl"],

            "tp1": levels["tp1"],
            "tp15": levels["tp15"],
            "tp2": levels["tp2"],

            "risk": levels["risk"],
            "atr": levels["atr"],

            "status": "OPEN",

            "result": None,

            "exit_price": None,
            "result_pct": 0,

            "last_price": levels["entry"],

            "current_pnl_pct": 0,

            "last_checked_candle": candle_time,

            "created_at": int(time.time())
        }

        state["_utbot"]["setups"].append(
            setup
        )

        existing_keys.add(signal_key)

        new_signals.append(
            setup
        )

    if new_signals:

        state["_utbot"]["stats"]["total"] = len(
            state["_utbot"]["setups"]
        )

        save_json_state(state)

        for setup in new_signals:

            levels = {
                "entry": setup["entry"],
                "sl": setup["sl"],
                "tp1": setup["tp1"],
                "tp15": setup["tp15"],
                "tp2": setup["tp2"],
                "risk": setup["risk"],
                "atr": setup["atr"]
            }

            message = ut_setup_message(
                setup["symbol"],
                setup["signal"],
                levels,
                setup["id"]
            )

            send_telegram_chunks(message)

            time.sleep(0.5)

    return state


# ============================================================
# MAIN SCANNER
# ============================================================

def run_scanner():

    print(
        "\n"
        "========================================\n"
        f"CRYPTO PUMP / DUMP SCANNER v{VERSION}\n"
        "KRAKEN FUTURES / CLOSED 5M CANDLES\n"
        "========================================\n"
    )

    state = get_ut_state()

    candles_by_symbol = {}

    results = []

    # ========================================================
    # GET DATA
    # ========================================================

    for symbol, kraken_symbol in COINS.items():

        try:

            candles = get_candles(
                kraken_symbol,
                5
            )

            if len(candles) < 60:

                print(
                    f"{symbol}: insufficient candles"
                )

                continue

            candles_by_symbol[symbol] = candles

            analysis = scanner_score(
                candles
            )

            results.append({
                "symbol": symbol,
                "candles": candles,
                "analysis": analysis
            })

            print(
                f"{symbol}: "
                f"{analysis['direction']} "
                f"{analysis['score']}"
            )

        except Exception as e:

            print(
                f"{symbol} ERROR:",
                e
            )

    if not results:

        print("No results.")

        return

    # ========================================================
    # TOP 5
    # ========================================================

    sorted_results = sorted(
        results,
        key=lambda x: abs(
            x["analysis"]["score"]
        ),
        reverse=True
    )

    top5 = sorted_results[:5]

    # ========================================================
    # WATCHLIST
    # ========================================================

    watchlist = create_watchlist(
        results,
        5
    )

    send_telegram_chunks(
        watchlist
    )

    # ========================================================
    # NORMAL SIGNALS
    # ========================================================

    for item in top5:

        symbol = item["symbol"]
        candles = item["candles"]
        analysis = item["analysis"]

        if analysis["direction"] == "NEUTRAL":
            continue

        if analysis["score"] == 0:
            continue

        # ----------------------------------------------------
        # 2 CANDLE CONFIRMATION
        # ----------------------------------------------------

        if not two_candle_confirmation(
            candles,
            analysis["direction"]
        ):
            continue

        message = normal_signal_message(
            symbol,
            candles,
            analysis
        )

        if message:

            send_telegram_chunks(
                message
            )

            time.sleep(0.3)

    # ========================================================
    # UPDATE OLD UT SETUPS
    # ========================================================

    state = update_ut_setups(
        state,
        candles_by_symbol
    )

    # ========================================================
    # PROCESS FRESH UT SIGNALS
    # ========================================================

    state = process_ut_top5(
        top5,
        state,
        candles_by_symbol
    )

    # ========================================================
    # LIVE UT STATUS
    # ========================================================

    live_status = ut_live_status_message(
        state,
        candles_by_symbol
    )

    send_telegram_chunks(
        live_status
    )

    print(
        "\nSCAN COMPLETE."
    )


# ============================================================
# MAIN LOOP
# ============================================================

if __name__ == "__main__":

    while True:

        try:

            run_scanner()

        except KeyboardInterrupt:

            print(
                "\nScanner stopped."
            )

            break

        except Exception as e:

            print(
                "\nMAIN ERROR:",
                e
            )

        # ====================================================
        # 5 MINUTE LOOP
        # ====================================================

        now = int(time.time())

        next_5m = (
            ((now // 300) + 1) * 300
        )

        sleep_seconds = (
            next_5m - now + 5
        )

        print(
            f"\nNext scan in "
            f"{sleep_seconds} seconds..."
        )

        time.sleep(
            max(10, sleep_seconds)
        )
