# ============================================================
# KRAKEN FUTURES ICHIMOKU INDEPENDENT TELEGRAM LISTENER v1.0
# ============================================================
#
# PURPOSE:
#   Independent real-time analysis listener.
#
# IMPORTANT:
#   This file DOES NOT import scanner.py
#   This file DOES NOT depend on scanner.py
#   This file DOES NOT open trades
#   This file DOES NOT modify scanner state/history
#
# TELEGRAM COMMANDS:
#
#   تحلیل BTC
#   تحلیل ETH
#   تحلیل BTC ETH SOL XRP
#
#   /check BTC
#   /check ETH
#   /check BTC ETH SOL
#
#   تحلیل@BotName BTC
#
# STRATEGY:
#
#   1H  = Trend
#   30M = Confirmation / Lock
#   15M = Pullback Structure
#   5M  = Pullback + Reversal Trigger
#
# CLOSED CANDLES ONLY
#
# ============================================================

import os
import json
import time
import math
import traceback
import requests

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE_URL +
    "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE_URL +
    "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE_URL +
    "/api/charts/v1/trade/{symbol}/{resolution}"
)


VERSION = "Independent Listener v1.0"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

TELEGRAM_TIMEOUT = 30

TELEGRAM_RETRIES = 3

TELEGRAM_MAX_LENGTH = 4000


# ============================================================
# KRAKEN
# ============================================================

REQUEST_TIMEOUT = 20

REQUEST_RETRIES = 3

REQUEST_SLEEP = 0.10


# ============================================================
# STRATEGY
# ============================================================

MIN_1H_SCORE = 4

MIN_30M_SCORE = 3

MIN_15M_SCORE = 2

MIN_5M_SCORE = 2

MIN_TRIGGER_SCORE = 7

MIN_FINAL_SCORE = 78.0


# ============================================================
# PULLBACK
# ============================================================

PULLBACK_TOUCH_ATR = 0.35

PULLBACK_MAX_DISTANCE_ATR = 1.50

PULLBACK_MAX_AGE = 2


# ============================================================
# CANDLE QUALITY
# ============================================================

MIN_TRIGGER_BODY_ATR = 0.10

MIN_REJECTION_WICK_RATIO = 0.35


# ============================================================
# STOP
# ============================================================

MIN_STOP_PCT = 0.50

MAX_STOP_PCT = 2.00

RR = 1.0


# ============================================================
# TIMEFRAMES
# ============================================================

TF_5M = "5m"

TF_15M = "15m"

TF_30M = "30m"

TF_1H = "1h"


RESOLUTION_SECONDS = {

    "1m": 60,

    "5m": 300,

    "15m": 900,

    "30m": 1800,

    "1h": 3600,

    "4h": 14400,

}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Kraken-Independent-Ichimoku-Listener/1.0"
})


# ============================================================
# BASIC
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)

    except Exception:
        return default


def clamp(
    value,
    low,
    high
):

    return max(
        low,
        min(high, value)
    )


def fmt_price(value):

    value = safe_float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 10:
        return f"{value:.4f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.1:
        return f"{value:.6f}"

    if value >= 0.01:
        return f"{value:.7f}"

    return f"{value:.8f}"


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(
    symbol
):

    q = str(
        symbol or ""
    ).strip().upper()

    q = (
        q
        .replace("$", "")
        .replace(",", "")
        .replace("/", "")
    )

    if q in (
        "BTC",
        "XBT",
        "BTCUSD",
        "XBTUSD",
        "PF_BTCUSD",
        "PF_XBTUSD",
    ):

        return "PF_XBTUSD"

    if q.startswith("PF_"):

        return q

    if q.endswith("USD"):

        return "PF_" + q

    return "PF_" + q + "USD"


def display_symbol(
    symbol
):

    symbol = str(
        symbol or ""
    ).upper()

    if symbol.startswith("PF_"):

        symbol = symbol[3:]

    if symbol in (
        "XBTUSD",
        "XBT",
    ):

        return "BTC"

    if symbol.endswith("USD"):

        symbol = symbol[:-3]

    return symbol


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None
):

    last_error = None

    for attempt in range(
        1,
        REQUEST_RETRIES + 1
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            time.sleep(
                REQUEST_SLEEP
            )

            return data

        except Exception as e:

            last_error = e

            print(
                f"[KRAKEN ERROR] "
                f"{attempt}/{REQUEST_RETRIES} "
                f"{url} | {e}"
            )

            if attempt < REQUEST_RETRIES:

                time.sleep(
                    attempt * 1.5
                )

    raise RuntimeError(
        "Kraken request failed: "
        f"{last_error}"
    )


# ============================================================
# TIMESTAMP
# ============================================================

def normalize_timestamp(
    value
):

    try:

        ts = float(value)

        if ts > 10_000_000_000:

            ts /= 1000

        return int(ts)

    except Exception:

        return 0


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    data = http_get(
        INSTRUMENTS_URL
    )

    if isinstance(
        data,
        dict
    ):

        return (
            data.get("instruments")
            or data.get("data")
            or []
        )

    if isinstance(
        data,
        list
    ):

        return data

    return []


# ============================================================
# FIND MARKET
# ============================================================

def find_market(
    requested_symbol
):

    target = normalize_symbol(
        requested_symbol
    )

    instruments = get_instruments()

    for item in instruments:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = str(
            item.get("symbol")
            or item.get("instrument")
            or ""
        )

        if (
            symbol.upper()
            != target.upper()
        ):
            continue

        if not symbol.upper().startswith(
            "PF_"
        ):
            continue

        return {
            "symbol": symbol,

            "tick_size":
                safe_float(
                    item.get("tickSize")
                    or item.get("tick_size")
                    or item.get(
                        "priceIncrement"
                    ),
                    0.0
                ),
        }

    return None


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit=250
):

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = http_get(
        url,
        params={
            "from": 0,
            "to": int(
                time.time()
            ),
        }
    )

    if isinstance(
        data,
        dict
    ):

        rows = (
            data.get("candles")
            or data.get("data")
            or []
        )

    elif isinstance(
        data,
        list
    ):

        rows = data

    else:

        rows = []

    candles = []

    for row in rows:

        try:

            if isinstance(
                row,
                dict
            ):

                t = (
                    row.get("time")
                    or row.get("timestamp")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

                v = (
                    row.get("volume")
                    or row.get("v")
                    or 0
                )

            else:

                if len(row) < 5:
                    continue

                t = row[0]

                o = row[1]

                h = row[2]

                l = row[3]

                c = row[4]

                v = (
                    row[5]
                    if len(row) > 5
                    else 0
                )

            candles.append({

                "time":
                    normalize_timestamp(t),

                "open":
                    safe_float(o),

                "high":
                    safe_float(h),

                "low":
                    safe_float(l),

                "close":
                    safe_float(c),

                "volume":
                    safe_float(v),

            })

        except Exception:

            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    # ========================================================
    # CLOSED CANDLES ONLY
    # ========================================================

    now_ts = int(
        time.time()
    )

    seconds = RESOLUTION_SECONDS.get(
        resolution,
        300
    )

    closed = []

    for candle in candles:

        if (
            candle["time"]
            + seconds
            <= now_ts
        ):

            closed.append(
                candle
            )

    return closed[-limit:]


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_range(c):

    return max(
        c["high"] - c["low"],
        1e-12
    )


def candle_body(c):

    return abs(
        c["close"]
        - c["open"]
    )


def upper_wick(c):

    return (
        c["high"]
        - max(
            c["open"],
            c["close"]
        )
    )


def lower_wick(c):

    return (
        min(
            c["open"],
            c["close"]
        )
        - c["low"]
    )


def bullish(c):

    return c["close"] > c["open"]


def bearish(c):

    return c["close"] < c["open"]


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return 0.0

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        cur = candles[i]

        prev = candles[i - 1]

        tr = max(

            cur["high"]
            - cur["low"],

            abs(
                cur["high"]
                - prev["close"]
            ),

            abs(
                cur["low"]
                - prev["close"]
            ),

        )

        trs.append(tr)

    if len(trs) < period:

        return 0.0

    return (
        sum(
            trs[-period:]
        )
        / period
    )


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku(
    candles
):

    if len(candles) < 52:

        raise RuntimeError(
            "Not enough candles for Ichimoku"
        )

    highs = [
        c["high"]
        for c in candles
    ]

    lows = [
        c["low"]
        for c in candles
    ]

    closes = [
        c["close"]
        for c in candles
    ]

    tenkan = (

        max(highs[-9:])
        +
        min(lows[-9:])

    ) / 2

    kijun = (

        max(highs[-26:])
        +
        min(lows[-26:])

    ) / 2

    span_a = (
        tenkan
        + kijun
    ) / 2

    span_b = (

        max(highs[-52:])
        +
        min(lows[-52:])

    ) / 2

    price = closes[-1]

    cloud_top = max(
        span_a,
        span_b
    )

    cloud_bottom = min(
        span_a,
        span_b
    )

    return {

        "price": price,

        "tenkan": tenkan,

        "kijun": kijun,

        "span_a": span_a,

        "span_b": span_b,

        "cloud_top": cloud_top,

        "cloud_bottom":
            cloud_bottom,

    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(
    candles
):

    info = ichimoku(
        candles
    )

    price = info["price"]

    tenkan = info["tenkan"]

    kijun = info["kijun"]

    score = 0

    # --------------------------------------------------------
    # Price vs cloud
    # --------------------------------------------------------

    if price > info["cloud_top"]:

        score += 4

    elif price < info["cloud_bottom"]:

        score -= 4

    # --------------------------------------------------------
    # Price vs Kijun
    # --------------------------------------------------------

    if price > kijun:

        score += 2

    elif price < kijun:

        score -= 2

    # --------------------------------------------------------
    # Tenkan vs Kijun
    # --------------------------------------------------------

    if tenkan > kijun:

        score += 2

    elif tenkan < kijun:

        score -= 2

    # --------------------------------------------------------
    # Cloud direction
    # --------------------------------------------------------

    if info["span_a"] > info["span_b"]:

        score += 2

    elif info["span_a"] < info["span_b"]:

        score -= 2

    score = int(
        clamp(
            score,
            -10,
            10
        )
    )

    info["score"] = score

    return score, info


# ============================================================
# REVERSAL PATTERNS
# ============================================================

def bullish_engulfing(
    a,
    b
):

    return (

        bearish(a)

        and bullish(b)

        and b["open"] <= a["close"]

        and b["close"] >= a["open"]

    )


def bearish_engulfing(
    a,
    b
):

    return (

        bullish(a)

        and bearish(b)

        and b["open"] >= a["close"]

        and b["close"] <= a["open"]

    )


def hammer(c):

    body = max(
        candle_body(c),
        candle_range(c) * 0.02
    )

    return (

        lower_wick(c)
        >= body * 2

        and upper_wick(c)
        <= body * 0.8

        and (
            body
            / candle_range(c)
        ) <= 0.45

    )


def shooting_star(c):

    body = max(
        candle_body(c),
        candle_range(c) * 0.02
    )

    return (

        upper_wick(c)
        >= body * 2

        and lower_wick(c)
        <= body * 0.8

        and (
            body
            / candle_range(c)
        ) <= 0.45

    )


def bullish_pin_bar(c):

    r = candle_range(c)

    return (

        lower_wick(c)
        >= r * 0.55

        and upper_wick(c)
        <= r * 0.20

        and candle_body(c)
        <= r * 0.35

    )


def bearish_pin_bar(c):

    r = candle_range(c)

    return (

        upper_wick(c)
        >= r * 0.55

        and lower_wick(c)
        <= r * 0.20

        and candle_body(c)
        <= r * 0.35

    )


def get_reversal_patterns(
    side,
    candles
):

    if len(candles) < 3:

        return []

    cur = candles[-1]

    prev = candles[-2]

    patterns = []

    if side == "LONG":

        if bullish_engulfing(
            prev,
            cur
        ):

            patterns.append(
                "Bullish Engulfing"
            )

        if (
            hammer(cur)
            and bullish(cur)
        ):

            patterns.append(
                "Hammer"
            )

        if bullish_pin_bar(cur):

            patterns.append(
                "Bullish Pin Bar"
            )

    else:

        if bearish_engulfing(
            prev,
            cur
        ):

            patterns.append(
                "Bearish Engulfing"
            )

        if (
            shooting_star(cur)
            and bearish(cur)
        ):

            patterns.append(
                "Shooting Star"
            )

        if bearish_pin_bar(cur):

            patterns.append(
                "Bearish Pin Bar"
            )

    return patterns


# ============================================================
# STRUCTURE
# ============================================================

def structure_ok(
    side,
    candles,
    lookback=6
):

    if len(candles) < lookback + 2:

        return False

    window = candles[
        -(lookback + 1):
    ]

    highs = [
        c["high"]
        for c in window
    ]

    lows = [
        c["low"]
        for c in window
    ]

    if side == "LONG":

        return (

            highs[-1]
            >= max(highs[:-1])

            or

            lows[-1]
            > min(lows[:-1])

        )

    return (

        lows[-1]
        <= min(lows[:-1])

        or

        highs[-1]
        < max(highs[:-1])

    )


# ============================================================
# PULLBACK ANALYSIS
# ============================================================

def analyze_pullback(
    side,
    candles
):

    if len(candles) < 20:

        return {
            "valid": False,
            "reason":
                "Not enough 5M candles",
            "touch": False,
            "reversal": False,
            "reclaim": False,
            "structure": False,
            "body_ok": False,
            "rejection": False,
            "age": 999,
            "distance_atr": 999,
        }

    atr5 = calculate_atr(
        candles,
        14
    )

    if atr5 <= 0:

        return {
            "valid": False,
            "reason": "Invalid ATR",
            "touch": False,
            "reversal": False,
            "reclaim": False,
            "structure": False,
            "body_ok": False,
            "rejection": False,
            "age": 999,
            "distance_atr": 999,
        }

    info = ichimoku(
        candles
    )

    tenkan = info["tenkan"]

    kijun = info["kijun"]

    cur = candles[-1]

    prev = candles[-2]

    prev2 = candles[-3]

    # --------------------------------------------------------
    # Detect recent touch
    # --------------------------------------------------------

    touch = False

    touch_index = None

    for back in range(
        1,
        PULLBACK_MAX_AGE + 2
    ):

        if len(candles) <= back:

            continue

        c = candles[-1 - back]

        if side == "LONG":

            d1 = abs(
                c["low"]
                - tenkan
            )

            d2 = abs(
                c["low"]
                - kijun
            )

        else:

            d1 = abs(
                c["high"]
                - tenkan
            )

            d2 = abs(
                c["high"]
                - kijun
            )

        distance = min(
            d1,
            d2
        )

        if distance <= (
            atr5
            * PULLBACK_TOUCH_ATR
        ):

            touch = True

            touch_index = (
                len(candles)
                - 1
                - back
            )

            break

    # --------------------------------------------------------
    # Current distance from Tenkan/Kijun
    # --------------------------------------------------------

    if side == "LONG":

        current_distance = min(

            abs(
                cur["close"]
                - tenkan
            ),

            abs(
                cur["close"]
                - kijun
            )

        )

    else:

        current_distance = min(

            abs(
                cur["close"]
                - tenkan
            ),

            abs(
                cur["close"]
                - kijun
            )

        )

    distance_atr = (
        current_distance
        / atr5
    )

    # --------------------------------------------------------
    # Pullback age
    # --------------------------------------------------------

    if touch_index is None:

        age = 999

    else:

        age = (
            len(candles)
            - 1
            - touch_index
        )

    # --------------------------------------------------------
    # Reversal candle
    # --------------------------------------------------------

    if side == "LONG":

        reversal = bullish(cur)

    else:

        reversal = bearish(cur)

    # --------------------------------------------------------
    # Rejection
    # --------------------------------------------------------

    if side == "LONG":

        rejection = (
            lower_wick(prev)
            >= candle_range(prev)
            * MIN_REJECTION_WICK_RATIO
        )

    else:

        rejection = (
            upper_wick(prev)
            >= candle_range(prev)
            * MIN_REJECTION_WICK_RATIO
        )

    # --------------------------------------------------------
    # Reclaim
    # --------------------------------------------------------

    if side == "LONG":

        reclaim = (
            cur["close"] > tenkan
            or cur["close"] > kijun
        )

    else:

        reclaim = (
            cur["close"] < tenkan
            or cur["close"] < kijun
        )

    # --------------------------------------------------------
    # Structure
    # --------------------------------------------------------

    structure = structure_ok(
        side,
        candles
    )

    # --------------------------------------------------------
    # Candle body
    # --------------------------------------------------------

    body_ok = (
        candle_body(cur)
        >= atr5
        * MIN_TRIGGER_BODY_ATR
    )

    # --------------------------------------------------------
    # Fresh distance
    # --------------------------------------------------------

    distance_ok = (
        distance_atr
        <= PULLBACK_MAX_DISTANCE_ATR
    )

    # --------------------------------------------------------
    # Fresh age
    # --------------------------------------------------------

    age_ok = (
        age <= PULLBACK_MAX_AGE
    )

    # --------------------------------------------------------
    # Final pullback
    # --------------------------------------------------------

    valid = (

        touch

        and age_ok

        and distance_ok

        and reversal

        and reclaim

        and structure

        and body_ok

    )

    # --------------------------------------------------------
    # Reason
    # --------------------------------------------------------

    if not touch:

        reason = (
            "No fresh Tenkan/Kijun touch"
        )

    elif not age_ok:

        reason = (
            f"Pullback too old "
            f"({age} candles)"
        )

    elif not distance_ok:

        reason = (
            f"Price too far from "
            f"Tenkan/Kijun "
            f"({distance_atr:.2f} ATR)"
        )

    elif not reversal:

        reason = (
            "5M reversal candle "
            "not confirmed"
        )

    elif not reclaim:

        reason = (
            "Tenkan/Kijun reclaim "
            "not confirmed"
        )

    elif not structure:

        reason = (
            "5M structure break "
            "not confirmed"
        )

    elif not body_ok:

        reason = (
            "Trigger candle body "
            "too weak"
        )

    else:

        reason = "Pullback valid"

    return {

        "valid": valid,

        "reason": reason,

        "touch": touch,

        "reversal": reversal,

        "reclaim": reclaim,

        "structure": structure,

        "body_ok": body_ok,

        "rejection": rejection,

        "age": age,

        "distance_atr":
            distance_atr,

    }


# ============================================================
# TRIGGER SCORE
# ============================================================

def calculate_trigger_score(
    side,
    candles,
    pb
):

    if not pb.get(
        "valid"
    ):

        return 0

    if len(candles) < 3:

        return 0

    cur = candles[-1]

    prev = candles[-2]

    score = 0

    if pb.get("touch"):

        score += 1

    if pb.get("reversal"):

        score += 2

    if pb.get("rejection"):

        score += 1

    if pb.get("reclaim"):

        score += 1

    if pb.get("structure"):

        score += 2

    if pb.get("body_ok"):

        score += 1

    if side == "LONG":

        if cur["close"] > prev["high"]:

            score += 2

    else:

        if cur["close"] < prev["low"]:

            score += 2

    return min(
        score,
        10
    )


# ============================================================
# FINAL SCORE
# ============================================================

def final_score(
    side,
    scores,
    trigger
):

    sign = (
        1
        if side == "LONG"
        else -1
    )

    s1 = scores["1h"]

    s30 = scores["30m"]

    s15 = scores["15m"]

    s5 = scores["5m"]

    trend = (

        max(
            sign * s1,
            0
        )
        / 10
        * 35

    )

    confirm = (

        max(
            sign * s30,
            0
        )
        / 10
        * 25

    )

    structure = (

        max(
            sign * s15,
            0
        )
        / 10
        * 20

    )

    trigger_part = (

        trigger
        / 10
        * 15

    )

    five = (

        max(
            sign * s5,
            0
        )
        / 10
        * 5

    )

    return round(
        clamp(
            trend
            + confirm
            + structure
            + trigger_part
            + five,
            0,
            100
        ),
        1
    )


# ============================================================
# TICK SIZE
# ============================================================

def round_to_tick(
    price,
    tick_size
):

    if tick_size <= 0:

        if price >= 1000:
            return round(price, 2)

        if price >= 100:
            return round(price, 3)

        if price >= 10:
            return round(price, 4)

        if price >= 1:
            return round(price, 5)

        if price >= 0.1:
            return round(price, 6)

        if price >= 0.01:
            return round(price, 7)

        return round(price, 8)

    return round(
        math.floor(
            price
            / tick_size
            + 0.5
        )
        * tick_size,
        12
    )


# ============================================================
# SL / TP
# ============================================================

def calculate_levels(
    side,
    entry,
    c5,
    c15,
    tick_size
):

    atr5 = calculate_atr(
        c5,
        14
    )

    if atr5 <= 0:

        raise RuntimeError(
            "Invalid ATR"
        )

    recent5 = c5[-10:]

    recent15 = c15[-4:]

    combined = (
        recent5
        + recent15
    )

    if side == "LONG":

        structural = (

            min(
                c["low"]
                for c in combined
            )

            - atr5 * 0.10

        )

    else:

        structural = (

            max(
                c["high"]
                for c in combined
            )

            + atr5 * 0.10

        )

    entry_r = round_to_tick(
        entry,
        tick_size
    )

    sl = round_to_tick(
        structural,
        tick_size
    )

    if side == "LONG":

        risk = (
            entry_r
            - sl
        )

    else:

        risk = (
            sl
            - entry_r
        )

    if risk <= 0:

        raise RuntimeError(
            "Invalid structural risk"
        )

    risk_pct = (
        risk
        / entry_r
        * 100
    )

    # --------------------------------------------------------
    # Minimum stop
    # --------------------------------------------------------

    if risk_pct < MIN_STOP_PCT:

        min_distance = (

            entry_r
            * MIN_STOP_PCT
            / 100

        )

        if side == "LONG":

            sl = round_to_tick(
                entry_r
                - min_distance,
                tick_size
            )

        else:

            sl = round_to_tick(
                entry_r
                + min_distance,
                tick_size
            )

        if side == "LONG":

            risk = (
                entry_r
                - sl
            )

        else:

            risk = (
                sl
                - entry_r
            )

        risk_pct = (
            risk
            / entry_r
            * 100
        )

    # --------------------------------------------------------
    # Maximum stop
    # --------------------------------------------------------

    if risk_pct > MAX_STOP_PCT:

        raise RuntimeError(
            f"Stop too wide: "
            f"{risk_pct:.2f}%"
        )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    if side == "LONG":

        tp = round_to_tick(
            entry_r
            + risk * RR,
            tick_size
        )

    else:

        tp = round_to_tick(
            entry_r
            - risk * RR,
            tick_size
        )

    return (
        entry_r,
        sl,
        tp,
        risk_pct
    )


# ============================================================
# COMPLETE SYMBOL ANALYSIS
# ============================================================

def analyze_symbol(
    requested_symbol
):

    market = find_market(
        requested_symbol
    )

    if not market:

        raise RuntimeError(
            f"Kraken symbol not found: "
            f"{requested_symbol}"
        )

    symbol = market["symbol"]

    print(
        f"[ANALYZE] "
        f"{symbol}"
    )

    # --------------------------------------------------------
    # Download MTF candles directly
    # --------------------------------------------------------

    c1 = get_candles(
        symbol,
        TF_1H,
        180
    )

    c30 = get_candles(
        symbol,
        TF_30M,
        180
    )

    c15 = get_candles(
        symbol,
        TF_15M,
        180
    )

    c5 = get_candles(
        symbol,
        TF_5M,
        250
    )

    lengths = {

        "1H": len(c1),

        "30M": len(c30),

        "15M": len(c15),

        "5M": len(c5),

    }

    if min(lengths.values()) < 60:

        raise RuntimeError(
            "Not enough CLOSED candles: "
            f"{lengths}"
        )

    # --------------------------------------------------------
    # Ichimoku
    # --------------------------------------------------------

    s1, i1 = ichimoku_score(
        c1
    )

    s30, i30 = ichimoku_score(
        c30
    )

    s15, i15 = ichimoku_score(
        c15
    )

    s5, i5 = ichimoku_score(
        c5
    )

    scores = {

        "1h": s1,

        "30m": s30,

        "15m": s15,

        "5m": s5,

    }

    results = {}

    # ========================================================
    # LONG
    # ========================================================

    long_pb = analyze_pullback(
        "LONG",
        c5
    )

    long_trigger = calculate_trigger_score(
        "LONG",
        c5,
        long_pb
    )

    long_score = final_score(
        "LONG",
        scores,
        long_trigger
    )

    long_reasons = []

    if s1 < MIN_1H_SCORE:

        long_reasons.append(
            f"1H {s1:+d} "
            f"< +{MIN_1H_SCORE}"
        )

    if s30 < MIN_30M_SCORE:

        long_reasons.append(
            f"30M Lock {s30:+d} "
            f"< +{MIN_30M_SCORE}"
        )

    if s15 < MIN_15M_SCORE:

        long_reasons.append(
            f"15M {s15:+d} "
            f"< +{MIN_15M_SCORE}"
        )

    if s5 < MIN_5M_SCORE:

        long_reasons.append(
            f"5M {s5:+d} "
            f"< +{MIN_5M_SCORE}"
        )

    if not long_pb["touch"]:

        long_reasons.append(
            "No fresh pullback touch"
        )

    if long_pb["age"] > PULLBACK_MAX_AGE:

        long_reasons.append(
            f"Pullback age "
            f"{long_pb['age']}"
        )

    if (
        long_pb["distance_atr"]
        > PULLBACK_MAX_DISTANCE_ATR
    ):

        long_reasons.append(
            f"Distance "
            f"{long_pb['distance_atr']:.2f} ATR"
        )

    if not long_pb["reversal"]:

        long_reasons.append(
            "No bullish 5M reversal"
        )

    if not long_pb["reclaim"]:

        long_reasons.append(
            "No Tenkan/Kijun reclaim"
        )

    if not long_pb["structure"]:

        long_reasons.append(
            "5M structure break absent"
        )

    if not long_pb["body_ok"]:

        long_reasons.append(
            "5M trigger body weak"
        )

    if long_trigger < MIN_TRIGGER_SCORE:

        long_reasons.append(
            f"Trigger "
            f"{long_trigger}/10"
        )

    if long_score < MIN_FINAL_SCORE:

        long_reasons.append(
            f"Score "
            f"{long_score:.1f}"
        )

    long_valid = (
        len(long_reasons) == 0
    )

    long_levels = None

    if long_valid:

        try:

            long_levels = calculate_levels(
                "LONG",
                c5[-1]["close"],
                c5,
                c15,
                market["tick_size"]
            )

        except Exception as e:

            long_valid = False

            long_reasons.append(
                f"SL/TP: {e}"
            )

    results["LONG"] = {

        "score": long_score,

        "trigger": long_trigger,

        "pullback": long_pb,

        "patterns":
            get_reversal_patterns(
                "LONG",
                c5
            ),

        "reasons":
            long_reasons,

        "valid":
            long_valid,

        "levels":
            long_levels,

    }

    # ========================================================
    # SHORT
    # ========================================================

    short_pb = analyze_pullback(
        "SHORT",
        c5
    )

    short_trigger = calculate_trigger_score(
        "SHORT",
        c5,
        short_pb
    )

    short_score = final_score(
        "SHORT",
        scores,
        short_trigger
    )

    short_reasons = []

    if s1 > -MIN_1H_SCORE:

        short_reasons.append(
            f"1H {s1:+d} "
            f"> -{MIN_1H_SCORE}"
        )

    if s30 > -MIN_30M_SCORE:

        short_reasons.append(
            f"30M Lock {s30:+d} "
            f"> -{MIN_30M_SCORE}"
        )

    if s15 > -MIN_15M_SCORE:

        short_reasons.append(
            f"15M {s15:+d} "
            f"> -{MIN_15M_SCORE}"
        )

    if s5 > -MIN_5M_SCORE:

        short_reasons.append(
            f"5M {s5:+d} "
            f"> -{MIN_5M_SCORE}"
        )

    if not short_pb["touch"]:

        short_reasons.append(
            "No fresh pullback touch"
        )

    if short_pb["age"] > PULLBACK_MAX_AGE:

        short_reasons.append(
            f"Pullback age "
            f"{short_pb['age']}"
        )

    if (
        short_pb["distance_atr"]
        > PULLBACK_MAX_DISTANCE_ATR
    ):

        short_reasons.append(
            f"Distance "
            f"{short_pb['distance_atr']:.2f} ATR"
        )

    if not short_pb["reversal"]:

        short_reasons.append(
            "No bearish 5M reversal"
        )

    if not short_pb["reclaim"]:

        short_reasons.append(
            "No Tenkan/Kijun reclaim"
        )

    if not short_pb["structure"]:

        short_reasons.append(
            "5M structure break absent"
        )

    if not short_pb["body_ok"]:

        short_reasons.append(
            "5M trigger body weak"
        )

    if short_trigger < MIN_TRIGGER_SCORE:

        short_reasons.append(
            f"Trigger "
            f"{short_trigger}/10"
        )

    if short_score < MIN_FINAL_SCORE:

        short_reasons.append(
            f"Score "
            f"{short_score:.1f}"
        )

    short_valid = (
        len(short_reasons) == 0
    )

    short_levels = None

    if short_valid:

        try:

            short_levels = calculate_levels(
                "SHORT",
                c5[-1]["close"],
                c5,
                c15,
                market["tick_size"]
            )

        except Exception as e:

            short_valid = False

            short_reasons.append(
                f"SL/TP: {e}"
            )

    results["SHORT"] = {

        "score": short_score,

        "trigger": short_trigger,

        "pullback": short_pb,

        "patterns":
            get_reversal_patterns(
                "SHORT",
                c5
            ),

        "reasons":
            short_reasons,

        "valid":
            short_valid,

        "levels":
            short_levels,

    }

    # ========================================================
    # BEST SIDE
    # ========================================================

    valid_sides = []

    if results["LONG"]["valid"]:

        valid_sides.append(
            results["LONG"]
        )

    if results["SHORT"]["valid"]:

        valid_sides.append(
            results["SHORT"]
        )

    if valid_sides:

        best = max(
            valid_sides,
            key=lambda x:
                x["score"]
        )

        verdict = best

    else:

        best = max(
            (
                results["LONG"],
                results["SHORT"]
            ),
            key=lambda x:
                x["score"]
        )

        verdict = None

    return {

        "symbol": symbol,

        "display":
            display_symbol(symbol),

        "scores":
            scores,

        "infos": {

            "1h": i1,

            "30m": i30,

            "15m": i15,

            "5m": i5,

        },

        "candles": {

            "1h":
                c1[-1]["time"],

            "30m":
                c30[-1]["time"],

            "15m":
                c15[-1]["time"],

            "5m":
                c5[-1]["time"],

        },

        "long":
            results["LONG"],

        "short":
            results["SHORT"],

        "verdict":
            verdict,

        "best":
            best,

    }


# ============================================================
# DIRECTION
# ============================================================

def direction(
    score
):

    if score >= 4:

        return "BULLISH 🟢"

    if score <= -4:

        return "BEARISH 🔴"

    return "NEUTRAL 🟡"


def cloud_status(
    info
):

    price = info["price"]

    if price > info["cloud_top"]:

        return "ABOVE 🟢"

    if price < info["cloud_bottom"]:

        return "BELOW 🔴"

    return "INSIDE 🟡"


# ============================================================
# REPORT
# ============================================================

def generate_report(
    result
):

    symbol = result["display"]

    scores = result["scores"]

    infos = result["infos"]

    long_r = result["long"]

    short_r = result["short"]

    verdict = result["verdict"]

    best = result["best"]

    lines = []

    lines.append(
        "🔎 ابرشکن ۱۰۰ | تحلیل مستقل"
    )

    lines.append(
        f"🤖 {VERSION}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🪙 {symbol}"
    )

    lines.append(
        "⚡ تحلیل مستقیم از Kraken"
    )

    lines.append(
        "⏱ فقط کندل‌های بسته‌شده"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # MTF
    # --------------------------------------------------------

    lines.append(
        "📊 MTF ICHIMOKU"
    )

    lines.append(
        f"1H  : "
        f"{direction(scores['1h'])} "
        f"{scores['1h']:+d}/10"
    )

    lines.append(
        f"30M : "
        f"{direction(scores['30m'])} "
        f"{scores['30m']:+d}/10"
    )

    lines.append(
        f"15M : "
        f"{direction(scores['15m'])} "
        f"{scores['15m']:+d}/10"
    )

    lines.append(
        f"5M  : "
        f"{direction(scores['5m'])} "
        f"{scores['5m']:+d}/10"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # 5M ICHIMOKU
    # --------------------------------------------------------

    i5 = infos["5m"]

    lines.append(
        "☁️ 5M ICHIMOKU"
    )

    lines.append(
        f"Price : "
        f"{fmt_price(i5['price'])}"
    )

    lines.append(
        f"Tenkan: "
        f"{fmt_price(i5['tenkan'])}"
    )

    lines.append(
        f"Kijun : "
        f"{fmt_price(i5['kijun'])}"
    )

    lines.append(
        f"Cloud : "
        f"{fmt_price(i5['cloud_bottom'])}"
        f" - "
        f"{fmt_price(i5['cloud_top'])}"
    )

    lines.append(
        f"Position: "
        f"{cloud_status(i5)}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # LONG
    # ========================================================

    lines.append(
        "🟢 LONG CHECK"
    )

    lines.append(
        f"1H Trend    : "
        f"{long_r['score']:.1f}/100"
    )

    lines.append(
        f"Trigger     : "
        f"{long_r['trigger']}/10"
    )

    pb = long_r["pullback"]

    lines.append(
        f"Pullback    : "
        f"{'✅' if pb['valid'] else '❌'}"
    )

    lines.append(
        f"Touch       : "
        f"{'✅' if pb['touch'] else '❌'}"
    )

    lines.append(
        f"Reversal    : "
        f"{'✅' if pb['reversal'] else '❌'}"
    )

    lines.append(
        f"Reclaim     : "
        f"{'✅' if pb['reclaim'] else '❌'}"
    )

    lines.append(
        f"Structure   : "
        f"{'✅' if pb['structure'] else '❌'}"
    )

    lines.append(
        f"Body        : "
        f"{'✅' if pb['body_ok'] else '❌'}"
    )

    lines.append(
        f"Distance    : "
        f"{pb['distance_atr']:.2f} ATR"
    )

    lines.append(
        f"Score       : "
        f"{long_r['score']:.1f}/100"
    )

    if long_r["patterns"]:

        lines.append(
            "🔄 "
            + ", ".join(
                long_r["patterns"]
            )
        )

    if long_r["valid"]:

        lines.append(
            "✅ LONG VALID"
        )

    else:

        lines.append(
            "❌ LONG rejected:"
        )

        for reason in long_r[
            "reasons"
        ][:5]:

            lines.append(
                f"• {reason}"
            )

    # ========================================================
    # SHORT
    # ========================================================

    lines.append(
        "────────────"
    )

    lines.append(
        "🔴 SHORT CHECK"
    )

    lines.append(
        f"Trigger     : "
        f"{short_r['trigger']}/10"
    )

    pb = short_r["pullback"]

    lines.append(
        f"Pullback    : "
        f"{'✅' if pb['valid'] else '❌'}"
    )

    lines.append(
        f"Touch       : "
        f"{'✅' if pb['touch'] else '❌'}"
    )

    lines.append(
        f"Reversal    : "
        f"{'✅' if pb['reversal'] else '❌'}"
    )

    lines.append(
        f"Reclaim     : "
        f"{'✅' if pb['reclaim'] else '❌'}"
    )

    lines.append(
        f"Structure   : "
        f"{'✅' if pb['structure'] else '❌'}"
    )

    lines.append(
        f"Body        : "
        f"{'✅' if pb['body_ok'] else '❌'}"
    )

    lines.append(
        f"Distance    : "
        f"{pb['distance_atr']:.2f} ATR"
    )

    lines.append(
        f"Score       : "
        f"{short_r['score']:.1f}/100"
    )

    if short_r["patterns"]:

        lines.append(
            "🔄 "
            + ", ".join(
                short_r["patterns"]
            )
        )

    if short_r["valid"]:

        lines.append(
            "✅ SHORT VALID"
        )

    else:

        lines.append(
            "❌ SHORT rejected:"
        )

        for reason in short_r[
            "reasons"
        ][:5]:

            lines.append(
                f"• {reason}"
            )

    # ========================================================
    # VERDICT
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if verdict is not None:

        side = verdict.get(
            "side",
            "LONG"
        )

        # Since side isn't stored in result,
        # identify it from object identity/content.
        if verdict is long_r:

            side = "LONG"

        else:

            side = "SHORT"

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        lines.append(
            f"🎯 VERDICT: "
            f"{emoji} {side}"
        )

        lines.append(
            f"Confidence: "
            f"{verdict['score']:.1f}/100"
        )

        levels = verdict.get(
            "levels"
        )

        if levels:

            entry, sl, tp, risk_pct = (
                levels
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(entry)}"
            )

            lines.append(
                f"SL: "
                f"{fmt_price(sl)} "
                f"({risk_pct:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{fmt_price(tp)}"
            )

            lines.append(
                f"RR: "
                f"{RR:.1f}:1"
            )

        lines.append(
            "⚠️ این فقط تحلیل است."
        )

        lines.append(
            "🚫 هیچ معامله‌ای باز نمی‌شود."
        )

    else:

        lines.append(
            "⛔ VERDICT: NO TRADE"
        )

        if best is long_r:

            side = "LONG"

        else:

            side = "SHORT"

        lines.append(
            f"بهترین سمت: "
            f"{side} "
            f"{best['score']:.1f}/100"
        )

        lines.append(
            "علت اصلی:"
        )

        for reason in best[
            "reasons"
        ][:7]:

            lines.append(
                f"• {reason}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM SEND
# ============================================================

def split_message(
    message
):

    if len(message) <= TELEGRAM_MAX_LENGTH:

        return [message]

    chunks = []

    remaining = message

    while len(remaining) > TELEGRAM_MAX_LENGTH:

        cut = remaining.rfind(
            "\n",
            0,
            TELEGRAM_MAX_LENGTH
        )

        if cut <= 0:

            cut = TELEGRAM_MAX_LENGTH

        chunks.append(
            remaining[:cut]
        )

        remaining = remaining[
            cut:
        ].lstrip()

    if remaining:

        chunks.append(
            remaining
        )

    return chunks


def send_telegram(
    text,
    chat_id=None
):

    if not TELEGRAM_TOKEN:

        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN missing"
        )

        return False

    target_chat = (
        chat_id
        or TELEGRAM_CHAT_ID
    )

    if not target_chat:

        print(
            "[TELEGRAM ERROR] "
            "TELEGRAM_CHAT_ID missing"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/sendMessage"
    )

    success = True

    for chunk in split_message(text):

        sent = False

        for attempt in range(
            1,
            TELEGRAM_RETRIES + 1
        ):

            try:

                response = SESSION.post(

                    url,

                    json={

                        "chat_id":
                            target_chat,

                        "text":
                            chunk,

                        "disable_web_page_preview":
                            True,

                    },

                    timeout=REQUEST_TIMEOUT

                )

                data = response.json()

                if (
                    response.ok
                    and data.get("ok")
                ):

                    sent = True

                    break

                print(
                    f"[TELEGRAM ERROR] "
                    f"{data}"
                )

                if attempt < TELEGRAM_RETRIES:

                    time.sleep(2)

            except Exception as e:

                print(
                    f"[TELEGRAM ERROR] "
                    f"{e}"
                )

                if attempt < TELEGRAM_RETRIES:

                    time.sleep(2)

        if not sent:

            success = False

    return success


# ============================================================
# TELEGRAM GET ME
# ============================================================

def get_bot_info():

    if not TELEGRAM_TOKEN:

        return None

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/getMe"
    )

    try:

        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        data = response.json()

        if data.get("ok"):

            return data.get(
                "result"
            )

    except Exception as e:

        print(
            f"[TELEGRAM] getMe error: {e}"
        )

    return None


# ============================================================
# TELEGRAM UPDATES
# ============================================================

def get_updates(
    offset
):

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_TOKEN}/getUpdates"
    )

    response = SESSION.get(

        url,

        params={

            "offset":
                offset,

            "timeout":
                TELEGRAM_TIMEOUT,

            "allowed_updates":
                json.dumps(
                    ["message"]
                ),

        },

        timeout=TELEGRAM_TIMEOUT + 10

    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            str(data)
        )

    return data.get(
        "result",
        []
    )


# ============================================================
# COMMAND PARSER
# ============================================================

def parse_command(
    text
):

    if not text:

        return []

    text = text.strip()

    if not text:

        return []

    parts = text.split()

    if not parts:

        return []

    command = parts[0].lower()

    # --------------------------------------------------------
    # Persian
    # --------------------------------------------------------

    valid = False

    if command in (
        "تحلیل",
        "/تحلیل",
    ):

        valid = True

    # --------------------------------------------------------
    # English
    # --------------------------------------------------------

    if command in (
        "analysis",
        "/analysis",
        "check",
        "/check",
    ):

        valid = True

    # --------------------------------------------------------
    # @BotName
    # --------------------------------------------------------

    if command.startswith(
        "تحلیل@"
    ):

        valid = True

    if command.startswith(
        "/تحلیل@"
    ):

        valid = True

    if command.startswith(
        "/check@"
    ):

        valid = True

    if command.startswith(
        "analysis@"
    ):

        valid = True

    if not valid:

        return []

    symbols = []

    for item in parts[1:]:

        item = (
            item
            .replace(",", "")
            .replace("$", "")
            .strip()
        )

        if item:

            symbols.append(
                item.upper()
            )

    return symbols[:5]


# ============================================================
# PROCESS MESSAGE
# ============================================================

def process_message(
    message
):

    if not isinstance(
        message,
        dict
    ):

        return

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    text = message.get(
        "text",
        ""
    )

    if not text:

        return

    print(
        "------------------------------------------------------------"
    )

    print(
        f"[TELEGRAM] "
        f"chat={chat_id} "
        f"text={text!r}"
    )

    symbols = parse_command(
        text
    )

    if not symbols:

        return

    # --------------------------------------------------------
    # Security
    # --------------------------------------------------------

    if (
        TELEGRAM_CHAT_ID
        and
        str(chat_id)
        != str(TELEGRAM_CHAT_ID)
    ):

        print(
            f"[SECURITY] "
            f"Unauthorized chat: "
            f"{chat_id}"
        )

        return

    # --------------------------------------------------------
    # Immediate response
    # --------------------------------------------------------

    send_telegram(
        "⏳ تحلیل دریافت شد\n"
        f"🪙 {', '.join(symbols)}\n"
        "⚡ در حال بررسی مستقیم Kraken...\n"
        "1H → 30M → 15M → 5M",
        chat_id
    )

    # --------------------------------------------------------
    # Analyze each symbol independently
    # --------------------------------------------------------

    for symbol in symbols:

        try:

            result = analyze_symbol(
                symbol
            )

            report = generate_report(
                result
            )

            send_telegram(
                report,
                chat_id
            )

        except Exception as e:

            print(
                f"[ANALYSIS ERROR] "
                f"{symbol}: {e}"
            )

            traceback.print_exc()

            send_telegram(

                "❌ خطا در تحلیل\n"
                f"🪙 {symbol}\n"
                f"Reason: {e}",

                chat_id

            )


# ============================================================
# MAIN LISTENER LOOP
# ============================================================

def main():

    print(
        "============================================================"
    )

    print(
        "KRAKEN FUTURES "
        "INDEPENDENT ICHIMOKU LISTENER"
    )

    print(
        VERSION
    )

    print(
        "============================================================"
    )

    print(
        "scanner.py dependency: NONE"
    )

    print(
        "Trade execution: DISABLED"
    )

    print(
        "Analysis: DIRECT KRAKEN API"
    )

    print(
        "============================================================"
    )

    if not TELEGRAM_TOKEN:

        print(
            "[FATAL] "
            "TELEGRAM_BOT_TOKEN is missing."
        )

        return

    bot = get_bot_info()

    if bot:

        print(
            f"[TELEGRAM] "
            f"Connected: "
            f"@{bot.get('username', 'unknown')}"
        )

    else:

        print(
            "[WARNING] "
            "Could not verify bot."
        )

    # --------------------------------------------------------
    # Offset
    # --------------------------------------------------------

    offset = 0

    print(
        "[LISTENER] Waiting for commands..."
    )

    print(
        "Examples:"
    )

    print(
        "  تحلیل BTC"
    )

    print(
        "  تحلیل BTC ETH SOL"
    )

    print(
        "  /check BTC"
    )

    print(
        "============================================================"
    )

    while True:

        try:

            updates = get_updates(
                offset
            )

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = (
                        int(update_id)
                        + 1
                    )

                try:

                    process_message(
                        update.get(
                            "message"
                        )
                    )

                except Exception as e:

                    print(
                        f"[MESSAGE ERROR] "
                        f"{e}"
                    )

                    traceback.print_exc()

        except KeyboardInterrupt:

            print(
                "\n[STOP] Listener stopped."
            )

            break

        except Exception as e:

            print(
                f"[LISTENER ERROR] "
                f"{e}"
            )

            traceback.print_exc()

            time.sleep(5)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
