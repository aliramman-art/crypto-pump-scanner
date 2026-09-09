# ============================================================
# KRAKEN FUTURES PIVOT TREND REVERSAL TELEGRAM LISTENER v1.0
# ============================================================
#
# STRATEGY:
#   Pivot Trend
#   -> Trend
#   -> Break of valid Pivot with CLOSED candle
#   -> Pullback / Retest
#   -> Reversal candle
#   -> Entry
#   -> Structural SL
#   -> TP = RR 1:1
#
# TELEGRAM COMMANDS:
#
#   تحلیل BTC 5m
#   تحلیل BTC 15m
#   تحلیل BTC 30m
#   تحلیل BTC 1h
#   تحلیل BTC 4h
#
#   /check BTC 5m
#   /check ETH 15m
#
# ENVIRONMENT VARIABLES:
#
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# ============================================================

import os
import re
import time
import math
import requests
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

REQUEST_TIMEOUT = 20
RETRIES = 3

# Pivot settings
PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# ATR
ATR_PERIOD = 14

# Pullback
PULLBACK_ATR_MAX = 0.35

# SL buffer
SL_BUFFER_ATR = 0.10

# Minimum / maximum SL distance
MIN_STOP_PERCENT = 0.20
MAX_STOP_PERCENT = 3.00

# RR
RR = 1.0

# Candle quality
MIN_BODY_ATR = 0.10

# How many candles to request
CANDLE_LIMIT = 500

# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 Kraken-Pivot-Trend-Bot/1.0"
})


# ============================================================
# LOG
# ============================================================

def log(message):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{now}] {message}", flush=True)


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            r = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:
            last_error = e
            log(f"HTTP attempt {attempt}/{RETRIES} failed: {e}")
            time.sleep(attempt)

    raise last_error


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram credentials are missing.")
        print(text)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }

    try:
        r = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        return True

    except Exception as e:
        log(f"Telegram send error: {e}")
        return False


# ============================================================
# SYMBOL NORMALIZATION
# ============================================================

def normalize_symbol(symbol):
    """
    Examples:

    BTC
    XBT
    BTCUSD
    PF_XBTUSD
    PF_BTCUSD

    -> PF_XBTUSD
    """

    s = symbol.upper().strip()

    s = s.replace("/", "")
    s = s.replace("-", "")
    s = s.replace("_", "")

    if s in ("BTC", "XBT", "BTCUSD", "XBTUSD"):
        return "PF_XBTUSD"

    if s.endswith("USD"):
        base = s[:-3]

        if base == "BTC":
            base = "XBT"

        return f"PF_{base}USD"

    return f"PF_{s}USD"


# ============================================================
# TIMEFRAME
# ============================================================

TIMEFRAME_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "12h": 720,
    "1d": 1440,
    "1w": 10080,
}


def normalize_timeframe(tf):
    tf = tf.lower().strip()

    aliases = {
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "1": "1m",
        "240": "4h",
        "4h": "4h",
        "1hour": "1h",
        "1hr": "1h",
        "hour": "1h",
        "daily": "1d",
        "day": "1d",
    }

    return aliases.get(tf, tf)


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(symbol, timeframe):
    """
    Kraken Futures chart endpoint.

    Returns CLOSED candles only.
    """

    resolution = TIMEFRAME_MAP[timeframe]

    url = (
        f"{BASE_URL}/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    data = http_get(url)

    candles = []

    raw = data.get("candles", [])

    for c in raw:
        try:
            ts = (
                c.get("time")
                or c.get("timestamp")
                or c.get("timeStamp")
            )

            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            close = float(c["close"])

            volume = float(
                c.get("volume", 0) or 0
            )

            if ts is None:
                continue

            ts = float(ts)

            # Kraken timestamps can be milliseconds
            if ts > 10_000_000_000:
                ts /= 1000.0

            candles.append({
                "time": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": volume
            })

        except Exception:
            continue

    candles.sort(key=lambda x: x["time"])

    if len(candles) < 20:
        raise RuntimeError(
            f"Not enough candles for {symbol} {timeframe}"
        )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------
    now = time.time()
    interval_seconds = resolution * 60

    closed = []

    for c in candles:
        candle_end = c["time"] + interval_seconds

        if candle_end <= now:
            closed.append(c)

    return closed[-CANDLE_LIMIT:]


# ============================================================
# ATR
# ============================================================

def true_range(current, previous_close):
    return max(
        current["high"] - current["low"],
        abs(current["high"] - previous_close),
        abs(current["low"] - previous_close)
    )


def atr(candles, period=ATR_PERIOD):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        tr = true_range(
            candles[i],
            candles[i - 1]["close"]
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(c):
    return abs(c["close"] - c["open"])


def candle_range(c):
    return c["high"] - c["low"]


def upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


def is_bullish(c):
    return c["close"] > c["open"]


def is_bearish(c):
    return c["close"] < c["open"]


# ============================================================
# PIVOT DETECTION
# ============================================================

def find_pivots(candles):
    """
    Confirmed Pivot:

    Pivot High:
        high > highs of N candles left/right

    Pivot Low:
        low < lows of N candles left/right

    IMPORTANT:
    A pivot is only known after PIVOT_RIGHT candles have closed.
    """

    pivots = []

    start = PIVOT_LEFT
    end = len(candles) - PIVOT_RIGHT

    for i in range(start, end):

        current = candles[i]

        left = candles[
            i - PIVOT_LEFT:i
        ]

        right = candles[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        left_highs = [
            x["high"] for x in left
        ]

        right_highs = [
            x["high"] for x in right
        ]

        left_lows = [
            x["low"] for x in left
        ]

        right_lows = [
            x["low"] for x in right
        ]

        pivot_high = (
            current["high"] > max(left_highs)
            and
            current["high"] > max(right_highs)
        )

        pivot_low = (
            current["low"] < min(left_lows)
            and
            current["low"] < min(right_lows)
        )

        if pivot_high:
            pivots.append({
                "index": i,
                "type": "HIGH",
                "price": current["high"],
                "time": current["time"]
            })

        if pivot_low:
            pivots.append({
                "index": i,
                "type": "LOW",
                "price": current["low"],
                "time": current["time"]
            })

    pivots.sort(key=lambda x: x["index"])

    return pivots


# ============================================================
# LAST PIVOTS
# ============================================================

def last_two_pivots(pivots, pivot_type):
    selected = [
        p for p in pivots
        if p["type"] == pivot_type
    ]

    if len(selected) < 2:
        return None, None

    return selected[-2], selected[-1]


# ============================================================
# TREND
# ============================================================

def determine_trend(pivots):
    """
    UP:
        HH + HL

    DOWN:
        LH + LL
    """

    h1, h2 = last_two_pivots(
        pivots,
        "HIGH"
    )

    l1, l2 = last_two_pivots(
        pivots,
        "LOW"
    )

    if not h1 or not h2 or not l1 or not l2:
        return {
            "trend": "UNKNOWN",
            "h1": h1,
            "h2": h2,
            "l1": l1,
            "l2": l2
        }

    higher_high = h2["price"] > h1["price"]
    higher_low = l2["price"] > l1["price"]

    lower_high = h2["price"] < h1["price"]
    lower_low = l2["price"] < l1["price"]

    if higher_high and higher_low:
        trend = "UP"

    elif lower_high and lower_low:
        trend = "DOWN"

    else:
        trend = "RANGE"

    return {
        "trend": trend,
        "h1": h1,
        "h2": h2,
        "l1": l1,
        "l2": l2
    }


# ============================================================
# STRUCTURE DESCRIPTION
# ============================================================

def structure_description(trend_data):

    h1 = trend_data["h1"]
    h2 = trend_data["h2"]

    l1 = trend_data["l1"]
    l2 = trend_data["l2"]

    if not all([h1, h2, l1, l2]):
        return "INSUFFICIENT DATA"

    high_structure = (
        "HH"
        if h2["price"] > h1["price"]
        else "LH"
    )

    low_structure = (
        "HL"
        if l2["price"] > l1["price"]
        else "LL"
    )

    return f"{high_structure} + {low_structure}"


# ============================================================
# BREAK DETECTION
# ============================================================

def detect_break(
    candles,
    pivots,
    trend_data,
    atr_value
):
    """
    We look for the latest confirmed structural break.

    UP trend:
        break below last valid Pivot Low

    DOWN trend:
        break above last valid Pivot High

    RANGE:
        check both directions.

    Break requires CLOSED candle.
    """

    if not candles or not pivots:
        return None

    trend = trend_data["trend"]

    latest = candles[-1]

    # --------------------------------------------------------
    # DOWN TREND -> BULLISH BREAK
    # --------------------------------------------------------

    if trend == "DOWN":

        pivot = trend_data["h2"]

        if pivot is None:
            return None

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            c = candles[i]

            if (
                c["close"] > pivot["price"]
            ):
                return {
                    "direction": "LONG",
                    "pivot": pivot,
                    "break_index": i,
                    "break_candle": c,
                    "break_price": c["close"],
                    "atr": atr_value
                }

    # --------------------------------------------------------
    # UP TREND -> BEARISH BREAK
    # --------------------------------------------------------

    if trend == "UP":

        pivot = trend_data["l2"]

        if pivot is None:
            return None

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            c = candles[i]

            if (
                c["close"] < pivot["price"]
            ):
                return {
                    "direction": "SHORT",
                    "pivot": pivot,
                    "break_index": i,
                    "break_candle": c,
                    "break_price": c["close"],
                    "atr": atr_value
                }

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    if trend == "RANGE":

        high_pivot = trend_data["h2"]
        low_pivot = trend_data["l2"]

        candidates = []

        if high_pivot:
            for i in range(
                high_pivot["index"] + 1,
                len(candles)
            ):
                c = candles[i]

                if c["close"] > high_pivot["price"]:
                    candidates.append({
                        "direction": "LONG",
                        "pivot": high_pivot,
                        "break_index": i,
                        "break_candle": c,
                        "break_price": c["close"],
                        "atr": atr_value
                    })
                    break

        if low_pivot:
            for i in range(
                low_pivot["index"] + 1,
                len(candles)
            ):
                c = candles[i]

                if c["close"] < low_pivot["price"]:
                    candidates.append({
                        "direction": "SHORT",
                        "pivot": low_pivot,
                        "break_index": i,
                        "break_candle": c,
                        "break_price": c["close"],
                        "atr": atr_value
                    })
                    break

        if candidates:
            candidates.sort(
                key=lambda x: x["break_index"]
            )

            return candidates[-1]

    return None


# ============================================================
# REVERSAL CANDLES
# ============================================================

def bullish_engulfing(prev, cur):

    return (
        is_bearish(prev)
        and
        is_bullish(cur)
        and
        cur["open"] <= prev["close"]
        and
        cur["close"] >= prev["open"]
        and
        candle_body(cur) > candle_body(prev) * 0.8
    )


def bearish_engulfing(prev, cur):

    return (
        is_bullish(prev)
        and
        is_bearish(cur)
        and
        cur["open"] >= prev["close"]
        and
        cur["close"] <= prev["open"]
        and
        candle_body(cur) > candle_body(prev) * 0.8
    )


def hammer(c, atr_value):

    r = candle_range(c)

    if r <= 0:
        return False

    body = candle_body(c)
    lw = lower_wick(c)
    uw = upper_wick(c)

    return (
        lw >= body * 2
        and
        lw > uw * 1.5
        and
        c["close"] > c["low"] + r * 0.55
        and
        body >= atr_value * MIN_BODY_ATR
    )


def shooting_star(c, atr_value):

    r = candle_range(c)

    if r <= 0:
        return False

    body = candle_body(c)
    uw = upper_wick(c)
    lw = lower_wick(c)

    return (
        uw >= body * 2
        and
        uw > lw * 1.5
        and
        c["close"] < c["low"] + r * 0.45
        and
        body >= atr_value * MIN_BODY_ATR
    )


def bullish_pin_bar(c, atr_value):

    r = candle_range(c)

    if r <= 0:
        return False

    body = candle_body(c)
    lw = lower_wick(c)

    return (
        lw >= r * 0.50
        and
        lw >= body * 2
        and
        c["close"] > c["open"]
        and
        body >= atr_value * MIN_BODY_ATR
    )


def bearish_pin_bar(c, atr_value):

    r = candle_range(c)

    if r <= 0:
        return False

    body = candle_body(c)
    uw = upper_wick(c)

    return (
        uw >= r * 0.50
        and
        uw >= body * 2
        and
        c["close"] < c["open"]
        and
        body >= atr_value * MIN_BODY_ATR
    )


def strong_bullish(c, atr_value):

    r = candle_range(c)

    if r <= 0:
        return False

    return (
        is_bullish(c)
        and
        candle_body(c) >= atr_value * 0.50
        and
        c["close"] >= c["low"] + r * 0.70
    )


def strong_bearish(c, atr_value):

    r = candle_range(c)

    if r <= 0:
        return False

    return (
        is_bearish(c)
        and
        candle_body(c) >= atr_value * 0.50
        and
        c["close"] <= c["low"] + r * 0.30
    )


# ============================================================
# REVERSAL ANALYSIS
# ============================================================

def detect_reversal(candles, index, direction, atr_value):

    if index < 1:
        return {
            "valid": False,
            "name": None
        }

    prev = candles[index - 1]
    cur = candles[index]

    if direction == "LONG":

        if bullish_engulfing(prev, cur):
            return {
                "valid": True,
                "name": "Bullish Engulfing"
            }

        if hammer(cur, atr_value):
            return {
                "valid": True,
                "name": "Hammer"
            }

        if bullish_pin_bar(cur, atr_value):
            return {
                "valid": True,
                "name": "Bullish Pin Bar"
            }

        if strong_bullish(cur, atr_value):
            return {
                "valid": True,
                "name": "Strong Bullish Candle"
            }

    if direction == "SHORT":

        if bearish_engulfing(prev, cur):
            return {
                "valid": True,
                "name": "Bearish Engulfing"
            }

        if shooting_star(cur, atr_value):
            return {
                "valid": True,
                "name": "Shooting Star"
            }

        if bearish_pin_bar(cur, atr_value):
            return {
                "valid": True,
                "name": "Bearish Pin Bar"
            }

        if strong_bearish(cur, atr_value):
            return {
                "valid": True,
                "name": "Strong Bearish Candle"
            }

    return {
        "valid": False,
        "name": None
    }


# ============================================================
# PULLBACK
# ============================================================

def detect_pullback(
    candles,
    break_data,
    atr_value
):
    """
    After break:

    LONG:
        price returns toward broken resistance

    SHORT:
        price returns toward broken support
    """

    if not break_data:
        return None

    break_index = break_data["break_index"]

    pivot = break_data["pivot"]

    direction = break_data["direction"]

    if break_index >= len(candles) - 1:
        return {
            "valid": False,
            "reason": "Waiting for pullback"
        }

    tolerance = atr_value * PULLBACK_ATR_MAX

    best = None

    for i in range(
        break_index + 1,
        len(candles)
    ):

        c = candles[i]

        distance = abs(
            c["low"] - pivot["price"]
        ) if direction == "LONG" else abs(
            c["high"] - pivot["price"]
        )

        # --------------------------------------------
        # LONG RETEST
        # --------------------------------------------

        if direction == "LONG":

            touched = (
                c["low"] <=
                pivot["price"] + tolerance
                and
                c["high"] >=
                pivot["price"] - tolerance
            )

            rejection = (
                c["close"] > pivot["price"]
            )

            if touched:

                best = {
                    "index": i,
                    "candle": c,
                    "distance": distance,
                    "touched": True,
                    "rejection": rejection
                }

        # --------------------------------------------
        # SHORT RETEST
        # --------------------------------------------

        if direction == "SHORT":

            touched = (
                c["high"] >=
                pivot["price"] - tolerance
                and
                c["low"] <=
                pivot["price"] + tolerance
            )

            rejection = (
                c["close"] < pivot["price"]
            )

            if touched:

                best = {
                    "index": i,
                    "candle": c,
                    "distance": distance,
                    "touched": True,
                    "rejection": rejection
                }

    if best is None:

        return {
            "valid": False,
            "reason": "No pullback to broken Pivot"
        }

    return best


# ============================================================
# SETUP SEARCH
# ============================================================

def find_setup(
    candles,
    trend_data,
    break_data,
    atr_value
):

    if not break_data:
        return {
            "valid": False,
            "reason": "No confirmed Pivot break"
        }

    pullback = detect_pullback(
        candles,
        break_data,
        atr_value
    )

    if not pullback or not pullback.get("touched"):
        return {
            "valid": False,
            "reason": "Pullback not confirmed",
            "break": break_data,
            "pullback": pullback
        }

    pb_index = pullback["index"]

    # --------------------------------------------------------
    # We need a candle AFTER the pullback touch
    # for reversal confirmation.
    # --------------------------------------------------------

    reversal_index = pb_index

    reversal = detect_reversal(
        candles,
        reversal_index,
        break_data["direction"],
        atr_value
    )

    if not reversal["valid"]:

        if pb_index + 1 < len(candles):

            reversal_index = pb_index + 1

            reversal = detect_reversal(
                candles,
                reversal_index,
                break_data["direction"],
                atr_value
            )

    if not reversal["valid"]:

        return {
            "valid": False,
            "reason": "No valid reversal candle",
            "break": break_data,
            "pullback": pullback,
            "reversal": reversal
        }

    entry_candle = candles[reversal_index]

    direction = break_data["direction"]

    pivot = break_data["pivot"]

    # --------------------------------------------------------
    # Find structural extreme around pullback
    # --------------------------------------------------------

    start = break_data["break_index"]

    section = candles[
        start:reversal_index + 1
    ]

    if not section:
        return {
            "valid": False,
            "reason": "Invalid structural section"
        }

    if direction == "LONG":

        structural_low = min(
            c["low"] for c in section
        )

        entry = entry_candle["close"]

        sl = (
            structural_low
            - atr_value * SL_BUFFER_ATR
        )

        risk = entry - sl

        if risk <= 0:
            return {
                "valid": False,
                "reason": "Invalid LONG risk"
            }

        tp = entry + risk

    else:

        structural_high = max(
            c["high"] for c in section
        )

        entry = entry_candle["close"]

        sl = (
            structural_high
            + atr_value * SL_BUFFER_ATR
        )

        risk = sl - entry

        if risk <= 0:
            return {
                "valid": False,
                "reason": "Invalid SHORT risk"
            }

        tp = entry - risk

    stop_percent = (
        risk / entry
    ) * 100

    if stop_percent < MIN_STOP_PERCENT:

        return {
            "valid": False,
            "reason": (
                f"Stop too tight: "
                f"{stop_percent:.2f}%"
            ),
            "break": break_data,
            "pullback": pullback,
            "reversal": reversal
        }

    if stop_percent > MAX_STOP_PERCENT:

        return {
            "valid": False,
            "reason": (
                f"Stop too wide: "
                f"{stop_percent:.2f}%"
            ),
            "break": break_data,
            "pullback": pullback,
            "reversal": reversal
        }

    return {
        "valid": True,
        "direction": direction,
        "pivot": pivot,
        "break": break_data,
        "pullback": pullback,
        "reversal": reversal,
        "entry_candle": entry_candle,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "stop_percent": stop_percent,
        "rr": RR,
        "structural_low": (
            structural_low
            if direction == "LONG"
            else None
        ),
        "structural_high": (
            structural_high
            if direction == "SHORT"
            else None
        )
    }


# ============================================================
# PRICE FORMAT
# ============================================================

def format_price(price):

    if price is None:
        return "-"

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.01:
        return f"{price:.6f}"

    if price >= 0.0001:
        return f"{price:.8f}"

    return f"{price:.10f}"


# ============================================================
# TIME
# ============================================================

def timeframe_label(tf):
    return tf.upper()


# ============================================================
# ANALYSIS ENGINE
# ============================================================

def analyze_symbol(symbol_input, timeframe):

    symbol = normalize_symbol(symbol_input)

    candles = get_candles(
        symbol,
        timeframe
    )

    if len(candles) < 50:
        raise RuntimeError(
            "Not enough closed candles."
        )

    atr_value = atr(candles)

    if atr_value is None:
        raise RuntimeError(
            "ATR could not be calculated."
        )

    pivots = find_pivots(candles)

    if len(pivots) < 4:
        return {
            "valid": False,
            "symbol_input": symbol_input.upper(),
            "symbol": symbol,
            "timeframe": timeframe,
            "reason": "Not enough confirmed Pivots",
            "candles": len(candles),
            "pivots": pivots
        }

    trend_data = determine_trend(pivots)

    break_data = detect_break(
        candles,
        pivots,
        trend_data,
        atr_value
    )

    setup = find_setup(
        candles,
        trend_data,
        break_data,
        atr_value
    )

    return {
        "valid": setup.get("valid", False),
        "symbol_input": symbol_input.upper(),
        "symbol": symbol,
        "timeframe": timeframe,
        "candles": candles,
        "pivots": pivots,
        "atr": atr_value,
        "trend": trend_data,
        "structure": structure_description(
            trend_data
        ),
        "break": break_data,
        "setup": setup
    }


# ============================================================
# REPORT
# ============================================================

def build_report(result):

    symbol = result["symbol_input"]
    tf = timeframe_label(
        result["timeframe"]
    )

    candles_count = result.get(
        "candles",
        []
    )

    if isinstance(candles_count, list):
        candles_count = len(candles_count)

    pivots = result.get("pivots", [])

    trend_data = result.get("trend")

    atr_value = result.get("atr")

    structure = result.get(
        "structure",
        "UNKNOWN"
    )

    report = []

    report.append(
        f"📊 PIVOT TREND ANALYSIS"
    )

    report.append(
        f"💰 {symbol} | {tf}"
    )

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    report.append(
        f"🕯 Closed Candles: {candles_count}"
    )

    report.append(
        f"🔹 Confirmed Pivots: {len(pivots)}"
    )

    if atr_value:
        report.append(
            f"📐 ATR: {format_price(atr_value)}"
        )

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if not trend_data:

        report.append(
            "📈 TREND: UNKNOWN"
        )

    else:

        trend = trend_data["trend"]

        if trend == "UP":
            trend_text = "🟢 UP TREND"

        elif trend == "DOWN":
            trend_text = "🔴 DOWN TREND"

        elif trend == "RANGE":
            trend_text = "🟡 RANGE"

        else:
            trend_text = "⚪ UNKNOWN"

        report.append(
            f"📈 TREND: {trend_text}"
        )

        report.append(
            f"Structure: {structure}"
        )

        h1 = trend_data.get("h1")
        h2 = trend_data.get("h2")
        l1 = trend_data.get("l1")
        l2 = trend_data.get("l2")

        if h1:
            report.append(
                f"Previous PH: "
                f"{format_price(h1['price'])}"
            )

        if h2:
            report.append(
                f"Latest PH: "
                f"{format_price(h2['price'])}"
            )

        if l1:
            report.append(
                f"Previous PL: "
                f"{format_price(l1['price'])}"
            )

        if l2:
            report.append(
                f"Latest PL: "
                f"{format_price(l2['price'])}"
            )

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # BREAK
    # --------------------------------------------------------

    break_data = result.get("break")

    if not break_data:

        report.append(
            "💥 TREND BREAK: ❌"
        )

        report.append(
            "Reason: No confirmed Pivot break."
        )

        report.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        report.append(
            "⚪ RESULT: NO TRADE"
        )

        return "\n".join(report)

    direction = break_data["direction"]

    report.append(
        f"💥 TREND BREAK: "
        f"✅ {direction}"
    )

    report.append(
        f"Broken Pivot: "
        f"{format_price(break_data['pivot']['price'])}"
    )

    report.append(
        f"Break Close: "
        f"{format_price(break_data['break_price'])}"
    )

    report.append(
        "Close Confirmation: ✅"
    )

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    setup = result.get("setup", {})

    pullback = setup.get(
        "pullback"
    )

    if not pullback:

        report.append(
            "🔄 PULLBACK: ❌"
        )

    elif pullback.get("touched"):

        report.append(
            "🔄 PULLBACK / RETEST: ✅"
        )

        report.append(
            f"Retest Price: "
            f"{format_price("
                pullback['candle']['close']
            )}"
        )

        report.append(
            "Pivot Retest: ✅"
        )

        if pullback.get("rejection"):
            report.append(
                "Pivot Rejection: ✅"
            )
        else:
            report.append(
                "Pivot Rejection: ⚠️"
            )

    else:

        report.append(
            "🔄 PULLBACK: ❌"
        )

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # REVERSAL
    # --------------------------------------------------------

    reversal = setup.get(
        "reversal"
    )

    if reversal and reversal.get("valid"):

        report.append(
            f"🕯 REVERSAL: ✅"
        )

        report.append(
            f"Pattern: "
            f"{reversal.get('name')}"
        )

    else:

        report.append(
            "🕯 REVERSAL: ❌"
        )

        reason = setup.get(
            "reason",
            "No valid reversal candle"
        )

        report.append(
            f"Reason: {reason}"
        )

        report.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        report.append(
            "⚪ RESULT: NO TRADE"
        )

        return "\n".join(report)

    # --------------------------------------------------------
    # VALID SETUP
    # --------------------------------------------------------

    if not setup.get("valid"):

        report.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        report.append(
            "⚪ RESULT: NO TRADE"
        )

        return "\n".join(report)

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    report.append(
        "🚀 VALID SETUP"
    )

    if setup["direction"] == "LONG":
        report.append(
            "🟢 DIRECTION: LONG"
        )
    else:
        report.append(
            "🔴 DIRECTION: SHORT"
        )

    report.append(
        f"Entry: "
        f"{format_price(setup['entry'])}"
    )

    report.append(
        f"SL: "
        f"{format_price(setup['sl'])}"
    )

    report.append(
        f"TP: "
        f"{format_price(setup['tp'])}"
    )

    report.append(
        f"Risk: "
        f"{setup['stop_percent']:.2f}%"
    )

    report.append(
        f"RR: 1:{setup['rr']:.0f}"
    )

    report.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    report.append(
        "✅ FINAL SIGNAL"
    )

    if setup["direction"] == "LONG":
        report.append(
            "🟢 LONG"
        )
    else:
        report.append(
            "🔴 SHORT"
        )

    return "\n".join(report)


# ============================================================
# TELEGRAM COMMAND PARSER
# ============================================================

def parse_command(text):

    if not text:
        return None

    text = text.strip()

    # Remove bot username if Telegram sends it
    text = re.sub(
        r"@\w+",
        "",
        text
    ).strip()

    # ---------------------------------------------
    # Persian:
    #
    # تحلیل BTC 15m
    # ---------------------------------------------

    pattern_analysis = re.match(
        r"^(?:تحلیل|تحليل)\s+"
        r"([A-Za-z0-9_\-/]+)"
        r"(?:\s+([A-Za-z0-9]+))?"
        r"\s*$",
        text,
        re.IGNORECASE
    )

    if pattern_analysis:

        symbol = pattern_analysis.group(1)

        tf = (
            pattern_analysis.group(2)
            or "15m"
        )

        tf = normalize_timeframe(tf)

        if tf not in TIMEFRAME_MAP:
            return {
                "error":
                    f"Timeframe not supported: {tf}"
            }

        return {
            "symbol": symbol,
            "timeframe": tf
        }

    # ---------------------------------------------
    # /check BTC 15m
    # ---------------------------------------------

    pattern_check = re.match(
        r"^/check\s+"
        r"([A-Za-z0-9_\-/]+)"
        r"(?:\s+([A-Za-z0-9]+))?"
        r"\s*$",
        text,
        re.IGNORECASE
    )

    if pattern_check:

        symbol = pattern_check.group(1)

        tf = (
            pattern_check.group(2)
            or "15m"
        )

        tf = normalize_timeframe(tf)

        if tf not in TIMEFRAME_MAP:
            return {
                "error":
                    f"Timeframe not supported: {tf}"
            }

        return {
            "symbol": symbol,
            "timeframe": tf
        }

    # ---------------------------------------------
    # HELP
    # ---------------------------------------------

    if text in (
        "/start",
        "/help",
        "راهنما",
        "کمک"
    ):
        return {
            "help": True
        }

    return None


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def telegram_get_updates(offset=None):

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is not configured."
        )

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 25,
        "allowed_updates": ["message"]
    }

    if offset is not None:
        params["offset"] = offset

    try:

        r = SESSION.get(
            url,
            params=params,
            timeout=35
        )

        r.raise_for_status()

        data = r.json()

        if not data.get("ok"):
            raise RuntimeError(
                str(data)
            )

        return data.get(
            "result",
            []
        )

    except Exception as e:

        log(
            f"Telegram getUpdates error: {e}"
        )

        return []


# ============================================================
# HELP MESSAGE
# ============================================================

def help_message():

    return (
        "🤖 PIVOT TREND ANALYZER\n\n"
        "دستور تحلیل:\n\n"
        "تحلیل BTC 5m\n"
        "تحلیل BTC 15m\n"
        "تحلیل BTC 30m\n"
        "تحلیل BTC 1h\n"
        "تحلیل BTC 4h\n\n"
        "یا:\n\n"
        "/check BTC 15m\n\n"
        "استراتژی:\n"
        "Pivot معتبر → Trend → Break → "
        "Pullback → Reversal → Entry → SL → TP\n\n"
        "RR = 1:1"
    )


# ============================================================
# PROCESS MESSAGE
# ============================================================

def process_message(message):

    if not message:
        return

    chat = message.get("chat", {})

    chat_id = str(
        chat.get("id", "")
    )

    text = message.get(
        "text",
        ""
    ).strip()

    if not text:
        return

    log(
        f"Telegram message "
        f"from {chat_id}: {text}"
    )

    parsed = parse_command(text)

    if parsed is None:
        return

    if parsed.get("help"):

        telegram_send(
            help_message()
        )

        return

    if parsed.get("error"):

        telegram_send(
            "❌ "
            + parsed["error"]
            + "\n\n"
            + help_message()
        )

        return

    symbol = parsed["symbol"]
    timeframe = parsed["timeframe"]

    telegram_send(
        f"⏳ در حال تحلیل "
        f"{symbol.upper()} "
        f"{timeframe.upper()} ...\n\n"
        f"Pivot → Trend → Break → "
        f"Pullback → Reversal"
    )

    try:

        result = analyze_symbol(
            symbol,
            timeframe
        )

        report = build_report(
            result
        )

        telegram_send(report)

    except Exception as e:

        log(
            f"Analysis error: {e}"
        )

        telegram_send(
            f"❌ خطا در تحلیل "
            f"{symbol.upper()} "
            f"{timeframe.upper()}\n\n"
            f"{str(e)[:800]}"
        )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print(
        """
============================================================
KRAKEN FUTURES PIVOT TREND TELEGRAM LISTENER v1.0
============================================================

Commands:

تحلیل BTC 5m
تحلیل BTC 15m
تحلیل BTC 30m
تحلیل BTC 1h
تحلیل BTC 4h

/check BTC 15m

Strategy:

Pivot
  ↓
Trend
  ↓
Break by CLOSED candle
  ↓
Pullback
  ↓
Reversal candle
  ↓
Entry
  ↓
Structural SL
  ↓
RR 1:1 TP

============================================================
"""
    )

    if not TELEGRAM_BOT_TOKEN:

        log(
            "ERROR: TELEGRAM_BOT_TOKEN "
            "is missing."
        )

        return

    if not TELEGRAM_CHAT_ID:

        log(
            "WARNING: TELEGRAM_CHAT_ID "
            "is missing."
        )

    offset = None

    log(
        "Listener started."
    )

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

                if message:

                    process_message(
                        message
                    )

        except KeyboardInterrupt:

            log(
                "Listener stopped."
            )

            break

        except Exception as e:

            log(
                f"Main loop error: {e}"
            )

            time.sleep(5)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
