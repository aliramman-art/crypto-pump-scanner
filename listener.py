# ============================================================
# TELEGRAM KRAKEN STRATEGY LISTENER v2.0
# ============================================================
#
# PURPOSE:
#   Telegram command listener for Kraken Futures strategy analysis
#
# COMMANDS:
#   تحلیل BTC 5m
#   تحلیل BTC 15m
#   تحلیل BTC 30m
#   تحلیل BTC 1h
#   تحلیل BTC 4h
#
#   /check BTC 15m
#
# STRATEGY:
#   1. Detect valid pivots
#   2. Determine current trend
#   3. Detect confirmed trend break
#   4. Wait for pullback / retest
#   5. Confirm reversal candle
#   6. Generate LONG / SHORT setup
#   7. Structural SL
#   8. RR 1:1 TP
#
# IMPORTANT:
#   Only CLOSED candles are used.
#
# TELEGRAM:
#   This file is the ONLY component that calls getUpdates.
#
# ENV:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# REQUIREMENTS:
#   requests
# ============================================================

import os
import time
import traceback
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

KRAKEN_BASE_URL = "https://futures.kraken.com"

TELEGRAM_TIMEOUT = 35
KRAKEN_TIMEOUT = 20

POLL_RETRY_SECONDS = 5
ERROR_RETRY_SECONDS = 10

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MAX_CANDLES = 500

# Number of candles after a break in which a retest is accepted
MAX_PULLBACK_CANDLES = 20

# Small structural SL buffer
SL_BUFFER_PERCENT = 0.001

# RR
RR = 1.0


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Kraken-Telegram-Strategy-Listener/2.0"
})


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):
    return (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )


def send_telegram(text, chat_id=None):
    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing.")
        return False

    target_chat = chat_id or TELEGRAM_CHAT_ID

    if not target_chat:
        print("ERROR: TELEGRAM_CHAT_ID is missing.")
        return False

    payload = {
        "chat_id": target_chat,
        "text": text
    }

    for attempt in range(3):

        try:

            response = session.post(
                telegram_url("sendMessage"),
                json=payload,
                timeout=TELEGRAM_TIMEOUT
            )

            if response.ok:
                return True

            print(
                "Telegram send error:",
                response.status_code,
                response.text[:500]
            )

        except Exception as e:
            print("Telegram send exception:", repr(e))

        time.sleep(2)

    return False


def telegram_get_updates(offset=None):

    params = {
        "timeout": TELEGRAM_TIMEOUT
    }

    if offset is not None:
        params["offset"] = offset

    response = session.get(
        telegram_url("getUpdates"),
        params=params,
        timeout=TELEGRAM_TIMEOUT + 10
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data.get("result", [])


# ============================================================
# KRAKEN
# ============================================================

def normalize_symbol(symbol):

    symbol = symbol.upper().strip()

    symbol = symbol.replace("/", "")
    symbol = symbol.replace("-", "")
    symbol = symbol.replace("_", "")

    if symbol.endswith("USD"):
        return symbol[:-3] + "USD"

    if symbol.endswith("USDT"):
        return symbol[:-4] + "USD"

    return symbol + "USD"


def resolution_to_minutes(tf):

    tf = tf.lower().strip()

    mapping = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240
    }

    if tf not in mapping:
        raise ValueError(
            "Timeframe must be 1m, 5m, 15m, 30m, 1h or 4h."
        )

    return mapping[tf]


def resolution_to_kraken(tf):

    tf = tf.lower().strip()

    mapping = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240
    }

    return mapping[tf]


def get_kraken_candles(symbol, timeframe, limit=MAX_CANDLES):

    symbol = normalize_symbol(symbol)
    resolution = resolution_to_kraken(timeframe)

    url = (
        f"{KRAKEN_BASE_URL}/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    response = session.get(
        url,
        timeout=KRAKEN_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    candles = []

    # Kraken response may contain "candles"
    # or directly return a list depending on endpoint/version.

    if isinstance(data, dict):

        raw = (
            data.get("candles")
            or data.get("data")
            or data.get("result")
            or []
        )

    elif isinstance(data, list):

        raw = data

    else:

        raw = []

    for item in raw:

        try:

            if isinstance(item, dict):

                timestamp = (
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("ts")
                )

                open_price = (
                    item.get("open")
                    or item.get("o")
                )

                high_price = (
                    item.get("high")
                    or item.get("h")
                )

                low_price = (
                    item.get("low")
                    or item.get("l")
                )

                close_price = (
                    item.get("close")
                    or item.get("c")
                )

                volume = (
                    item.get("volume")
                    or item.get("v")
                    or 0
                )

            else:

                # Common OHLCV order:
                # timestamp, open, high, low, close, volume

                if len(item) < 5:
                    continue

                timestamp = item[0]
                open_price = item[1]
                high_price = item[2]
                low_price = item[3]
                close_price = item[4]

                volume = item[5] if len(item) > 5 else 0

            if timestamp is None:
                continue

            candles.append({
                "time": float(timestamp),
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
                "volume": float(volume)
            })

        except Exception:
            continue

    if not candles:
        raise RuntimeError(
            f"No candle data returned for {symbol} {timeframe}"
        )

    candles.sort(key=lambda x: x["time"])

    # Remove duplicate timestamps
    unique = {}

    for candle in candles:
        unique[candle["time"]] = candle

    candles = list(unique.values())

    candles.sort(key=lambda x: x["time"])

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    minutes = resolution_to_minutes(timeframe)

    now_ts = time.time()

    closed = []

    for candle in candles:

        candle_end = (
            candle["time"] + minutes * 60
        )

        if candle_end <= now_ts:
            closed.append(candle)

    if len(closed) > limit:
        closed = closed[-limit:]

    if len(closed) < 50:
        raise RuntimeError(
            f"Not enough CLOSED candles for "
            f"{symbol} {timeframe}: {len(closed)}"
        )

    return closed


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(
                current["high"] -
                previous["close"]
            ),
            abs(
                current["low"] -
                previous["close"]
            )
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return sum(trs[-period:]) / period


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(candles):

    highs = []
    lows = []

    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    for i in range(
        left,
        len(candles) - right
    ):

        current_high = candles[i]["high"]
        current_low = candles[i]["low"]

        is_high = True
        is_low = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= current_high:
                is_high = False

            if candles[j]["low"] <= current_low:
                is_low = False

        if is_high:

            highs.append({
                "index": i,
                "price": current_high
            })

        if is_low:

            lows.append({
                "index": i,
                "price": current_low
            })

    return highs, lows


# ============================================================
# TREND
# ============================================================

def determine_trend(highs, lows):

    if len(highs) < 2 or len(lows) < 2:
        return "RANGE"

    h1 = highs[-2]["price"]
    h2 = highs[-1]["price"]

    l1 = lows[-2]["price"]
    l2 = lows[-1]["price"]

    if h2 > h1 and l2 > l1:
        return "UP"

    if h2 < h1 and l2 < l1:
        return "DOWN"

    return "RANGE"


# ============================================================
# BREAK DETECTION
# ============================================================

def detect_break(candles, highs, lows, trend):

    if len(candles) < 2:
        return None

    last_index = len(candles) - 1

    # --------------------------------------------------------
    # UP TREND
    # Bearish break = close below latest structural low
    # --------------------------------------------------------

    if trend == "UP" and lows:

        pivot = lows[-1]

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            if candles[i]["close"] < pivot["price"]:

                return {
                    "direction": "BEARISH",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"]
                }

    # --------------------------------------------------------
    # DOWN TREND
    # Bullish break = close above latest structural high
    # --------------------------------------------------------

    if trend == "DOWN" and highs:

        pivot = highs[-1]

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            if candles[i]["close"] > pivot["price"]:

                return {
                    "direction": "BULLISH",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"]
                }

    # --------------------------------------------------------
    # RANGE
    # Check latest structural break
    # --------------------------------------------------------

    candidates = []

    if highs:

        pivot = highs[-1]

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            if candles[i]["close"] > pivot["price"]:

                candidates.append({
                    "direction": "BULLISH",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"]
                })

    if lows:

        pivot = lows[-1]

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            if candles[i]["close"] < pivot["price"]:

                candidates.append({
                    "direction": "BEARISH",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"]
                })

    if candidates:

        candidates.sort(
            key=lambda x: x["index"]
        )

        return candidates[-1]

    return None


# ============================================================
# CANDLE PATTERNS
# ============================================================

def candle_parts(c):

    body = abs(c["close"] - c["open"])

    total = c["high"] - c["low"]

    upper = (
        c["high"] -
        max(c["open"], c["close"])
    )

    lower = (
        min(c["open"], c["close"]) -
        c["low"]
    )

    return body, total, upper, lower


def bullish_engulfing(prev, cur):

    return (
        prev["close"] < prev["open"]
        and cur["close"] > cur["open"]
        and cur["open"] <= prev["close"]
        and cur["close"] >= prev["open"]
    )


def bearish_engulfing(prev, cur):

    return (
        prev["close"] > prev["open"]
        and cur["close"] < cur["open"]
        and cur["open"] >= prev["close"]
        and cur["close"] <= prev["open"]
    )


def hammer(c, atr):

    body, total, upper, lower = candle_parts(c)

    if total <= 0:
        return False

    return (
        body >= atr * 0.05
        and lower >= body * 2
        and upper <= body
        and c["close"] >= c["open"]
    )


def shooting_star(c, atr):

    body, total, upper, lower = candle_parts(c)

    if total <= 0:
        return False

    return (
        body >= atr * 0.05
        and upper >= body * 2
        and lower <= body
        and c["close"] <= c["open"]
    )


def bullish_pin_bar(c):

    body, total, upper, lower = candle_parts(c)

    if total <= 0:
        return False

    return (
        lower >= body * 2
        and lower >= upper * 1.5
    )


def bearish_pin_bar(c):

    body, total, upper, lower = candle_parts(c)

    if total <= 0:
        return False

    return (
        upper >= body * 2
        and upper >= lower * 1.5
    )


def strong_bullish(c, atr):

    body, total, upper, lower = candle_parts(c)

    if total <= 0:
        return False

    return (
        c["close"] > c["open"]
        and body >= atr * 0.5
        and c["close"] >= c["high"] - total * 0.30
    )


def strong_bearish(c, atr):

    body, total, upper, lower = candle_parts(c)

    if total <= 0:
        return False

    return (
        c["close"] < c["open"]
        and body >= atr * 0.5
        and c["close"] <= c["low"] + total * 0.30
    )


def reversal_confirmation(candles, break_info):

    break_index = break_info["index"]
    direction = break_info["direction"]

    atr = calculate_atr(candles)

    start = break_index + 1

    if start >= len(candles):
        return None

    end = min(
        len(candles),
        start + MAX_PULLBACK_CANDLES
    )

    # --------------------------------------------------------
    # Look for retest and reversal
    # --------------------------------------------------------

    level = break_info["level"]

    best = None

    for i in range(start, end):

        c = candles[i]

        tolerance = max(
            atr * 0.30,
            abs(level) * 0.001
        )

        touched = (
            c["low"] <= level + tolerance
            and c["high"] >= level - tolerance
        )

        if not touched:
            continue

        pattern = None

        prev = candles[i - 1] if i > 0 else None

        if direction == "BULLISH":

            if prev and bullish_engulfing(prev, c):
                pattern = "Bullish Engulfing"

            elif hammer(c, atr):
                pattern = "Hammer"

            elif bullish_pin_bar(c):
                pattern = "Bullish Pin Bar"

            elif strong_bullish(c, atr):
                pattern = "Strong Bullish Candle"

            # Reversal candle must close back above level
            valid_close = c["close"] > level

        else:

            if prev and bearish_engulfing(prev, c):
                pattern = "Bearish Engulfing"

            elif shooting_star(c, atr):
                pattern = "Shooting Star"

            elif bearish_pin_bar(c):
                pattern = "Bearish Pin Bar"

            elif strong_bearish(c, atr):
                pattern = "Strong Bearish Candle"

            # Reversal candle must close back below level
            valid_close = c["close"] < level

        if pattern and valid_close:

            best = {
                "index": i,
                "pattern": pattern,
                "level": level
            }

    return best


# ============================================================
# TRADE SETUP
# ============================================================

def build_trade_setup(candles, break_info, confirmation):

    direction = break_info["direction"]

    index = confirmation["index"]

    candle = candles[index]

    if direction == "BULLISH":

        entry = candle["close"]

        structural_low = min(
            c["low"]
            for c in candles[
                break_info["index"]: index + 1
            ]
        )

        sl = (
            structural_low *
            (1 - SL_BUFFER_PERCENT)
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + risk

        side = "LONG"

    else:

        entry = candle["close"]

        structural_high = max(
            c["high"]
            for c in candles[
                break_info["index"]: index + 1
            ]
        )

        sl = (
            structural_high *
            (1 + SL_BUFFER_PERCENT)
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - risk

        side = "SHORT"

    return {
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "pattern": confirmation["pattern"],
        "break_level": break_info["level"],
        "break_index": break_info["index"],
        "confirmation_index": confirmation["index"]
    }


# ============================================================
# ANALYSIS
# ============================================================

def analyze(symbol, timeframe):

    symbol = normalize_symbol(symbol)

    candles = get_kraken_candles(
        symbol,
        timeframe
    )

    highs, lows = find_pivots(candles)

    trend = determine_trend(
        highs,
        lows
    )

    break_info = detect_break(
        candles,
        highs,
        lows,
        trend
    )

    result = {
        "symbol": symbol,
        "timeframe": timeframe,
        "trend": trend,
        "candles": len(candles),
        "pivots_high": len(highs),
        "pivots_low": len(lows),
        "break": None,
        "confirmation": None,
        "setup": None
    }

    if not break_info:
        return result

    result["break"] = break_info

    confirmation = reversal_confirmation(
        candles,
        break_info
    )

    if not confirmation:
        return result

    result["confirmation"] = confirmation

    setup = build_trade_setup(
        candles,
        break_info,
        confirmation
    )

    result["setup"] = setup

    return result


# ============================================================
# FORMAT REPORT
# ============================================================

def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def trend_text(trend):

    mapping = {
        "UP": "🟢 صعودی",
        "DOWN": "🔴 نزولی",
        "RANGE": "🟡 رنج"
    }

    return mapping.get(
        trend,
        trend
    )


def build_report(result):

    symbol = result["symbol"]
    timeframe = result["timeframe"]

    lines = []

    lines.append("📊 KRAKEN STRATEGY ANALYSIS")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(f"💰 {symbol}")
    lines.append(f"⏱ تایم‌فریم: {timeframe}")
    lines.append(
        f"📈 روند: {trend_text(result['trend'])}"
    )
    lines.append(
        f"🕯 کندل بسته‌شده: {result['candles']}"
    )
    lines.append(
        f"🔺 Pivot High: {result['pivots_high']}"
    )
    lines.append(
        f"🔻 Pivot Low: {result['pivots_low']}"
    )

    if not result["break"]:

        lines.append("")
        lines.append("❌ شکست روند معتبر پیدا نشد.")
        lines.append("⏳ وضعیت: NO TRADE")

        return "\n".join(lines)

    br = result["break"]

    lines.append("")
    lines.append("⚡ TREND BREAK")
    lines.append(
        f"نوع شکست: {br['direction']}"
    )
    lines.append(
        f"سطح شکست: {fmt_price(br['level'])}"
    )

    if not result["confirmation"]:

        lines.append("")
        lines.append(
            "⏳ پولبک/ریتست و کندل برگشتی معتبر هنوز تأیید نشده."
        )
        lines.append("وضعیت: WAIT")

        return "\n".join(lines)

    conf = result["confirmation"]

    lines.append("")
    lines.append("🔄 REVERSAL CONFIRMATION")
    lines.append(
        f"🕯 الگو: {conf['pattern']}"
    )
    lines.append(
        f"📍 Retest Level: {fmt_price(conf['level'])}"
    )

    setup = result["setup"]

    if not setup:

        lines.append("")
        lines.append("⚠️ Setup ساخته نشد.")
        lines.append("وضعیت: NO TRADE")

        return "\n".join(lines)

    lines.append("")
    lines.append("━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"🚨 SIGNAL: {setup['side']}"
    )
    lines.append("━━━━━━━━━━━━━━━━━━")

    lines.append(
        f"🎯 Entry: {fmt_price(setup['entry'])}"
    )

    lines.append(
        f"🛑 SL: {fmt_price(setup['sl'])}"
    )

    lines.append(
        f"🎯 TP: {fmt_price(setup['tp'])}"
    )

    lines.append(
        f"📐 RR: 1:{RR:.0f}"
    )

    lines.append(
        f"🕯 Confirmation: {setup['pattern']}"
    )

    lines.append("")
    lines.append("⚠️ این سیگنال بر اساس کندل‌های بسته‌شده است.")

    return "\n".join(lines)


# ============================================================
# COMMAND PARSER
# ============================================================

def parse_command(text):

    if not text:
        return None

    text = text.strip()

    # Remove multiple spaces
    parts = text.split()

    if not parts:
        return None

    # --------------------------------------------------------
    # Persian command
    # --------------------------------------------------------

    if parts[0] == "تحلیل":

        if len(parts) < 3:
            return None

        symbol = parts[1]
        timeframe = parts[2].lower()

        return symbol, timeframe

    # --------------------------------------------------------
    # English command
    # --------------------------------------------------------

    if parts[0].lower() in (
        "/check",
        "check"
    ):

        if len(parts) < 3:
            return None

        symbol = parts[1]
        timeframe = parts[2].lower()

        return symbol, timeframe

    return None


# ============================================================
# HELP
# ============================================================

HELP_TEXT = """
🤖 KRAKEN STRATEGY BOT

دستور تحلیل:

تحلیل BTC 5m
تحلیل BTC 15m
تحلیل BTC 30m
تحلیل BTC 1h
تحلیل BTC 4h

یا:

/check BTC 5m

تایم‌فریم‌های مجاز:
1m
5m
15m
30m
1h
4h

استراتژی:
Pivot → Trend Break → Pullback/Retest
→ Reversal Candle → Entry → SL → TP

RR = 1:1
"""


# ============================================================
# HANDLE MESSAGE
# ============================================================

def handle_message(message):

    chat = message.get("chat", {})

    chat_id = str(
        chat.get("id", "")
    )

    text = message.get(
        "text",
        ""
    ).strip()

    if not chat_id:
        return

    # --------------------------------------------------------
    # /start
    # --------------------------------------------------------

    if text.lower() in (
        "/start",
        "/help",
        "help"
    ):

        send_telegram(
            HELP_TEXT,
            chat_id
        )

        return

    parsed = parse_command(text)

    if not parsed:

        send_telegram(
            "❌ دستور نامعتبر.\n\n"
            "مثال:\n"
            "تحلیل BTC 5m\n\n"
            "یا:\n"
            "/check BTC 15m",
            chat_id
        )

        return

    symbol, timeframe = parsed

    try:

        resolution_to_minutes(
            timeframe
        )

    except Exception:

        send_telegram(
            "❌ تایم‌فریم نامعتبر.\n\n"
            "مجاز:\n"
            "1m, 5m, 15m, 30m, 1h, 4h",
            chat_id
        )

        return

    # --------------------------------------------------------
    # Processing message
    # --------------------------------------------------------

    send_telegram(
        f"🔎 در حال بررسی {symbol.upper()} "
        f"در تایم‌فریم {timeframe} ...",
        chat_id
    )

    try:

        result = analyze(
            symbol,
            timeframe
        )

        report = build_report(
            result
        )

        send_telegram(
            report,
            chat_id
        )

    except Exception as e:

        print(
            "Analysis error:",
            repr(e)
        )

        traceback.print_exc()

        send_telegram(
            "❌ خطا در تحلیل.\n\n"
            f"ارز: {symbol.upper()}\n"
            f"تایم‌فریم: {timeframe}\n\n"
            "ممکن است نماد در Kraken Futures "
            "وجود نداشته باشد یا API موقتاً پاسخ ندهد.",
            chat_id
        )


# ============================================================
# TELEGRAM LISTENER
# ============================================================

def listener_loop():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    print("=" * 60)
    print("TELEGRAM LISTENER v2.0")
    print("=" * 60)
    print("Listener started.")
    print("Waiting for Telegram commands...")
    print("=" * 60)

    offset = None

    while True:

        try:

            updates = telegram_get_updates(
                offset
            )

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = update_id + 1

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                try:

                    handle_message(
                        message
                    )

                except Exception as e:

                    print(
                        "Message handling error:",
                        repr(e)
                    )

                    traceback.print_exc()

            # Small pause prevents excessive requests
            if not updates:
                time.sleep(0.5)

        except requests.exceptions.Timeout:

            print(
                "Telegram polling timeout. Reconnecting..."
            )

            time.sleep(
                POLL_RETRY_SECONDS
            )

        except requests.exceptions.ConnectionError:

            print(
                "Network connection error. Reconnecting..."
            )

            time.sleep(
                ERROR_RETRY_SECONDS
            )

        except requests.exceptions.HTTPError as e:

            print(
                "Telegram HTTP error:",
                repr(e)
            )

            time.sleep(
                ERROR_RETRY_SECONDS
            )

        except Exception as e:

            print(
                "Listener error:",
                repr(e)
            )

            traceback.print_exc()

            time.sleep(
                ERROR_RETRY_SECONDS
            )


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    while True:

        try:

            listener_loop()

        except KeyboardInterrupt:

            print(
                "Listener stopped manually."
            )

            break

        except Exception as e:

            print(
                "FATAL ERROR:",
                repr(e)
            )

            traceback.print_exc()

            print(
                "Restarting listener in 15 seconds..."
            )

            time.sleep(15)
