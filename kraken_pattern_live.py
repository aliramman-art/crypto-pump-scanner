# ============================================================
# KRAKEN FUTURES 1H TRENDLINE BREAKOUT PAPER SCANNER
# VERSION 3.1.0
# ============================================================
#
# 40 ASSETS
#
# ORIGINAL STRATEGY:
#
# LONG:
#   Descending resistance
#   >= 3 touches
#   Closed 1H candle breaks above resistance
#
# SHORT:
#   Ascending support
#   >= 3 touches
#   Closed 1H candle breaks below support
#
# NEW:
#   WHALE FLOW FILTER
#
# Whale Flow Proxy uses Kraken public market analytics:
#   - Aggressor Differential
#   - CVD
#   - Trade Volume
#   - Open Interest
#
# Whale filter is ONLY a confirmation filter.
# Trendline breakout remains the primary strategy.
#
# PAPER ONLY
#
# ============================================================


import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "3.1.0"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "trendline_1h_breakout_v30.db"

TIMEFRAME = "1h"

CANDLE_COUNT = 250

LOOKBACK_CANDLES = 200

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_TOUCHES = 3

MIN_LINE_LENGTH = 10

TOUCH_TOLERANCE = 0.002

MIN_SLOPE_PERCENT_PER_CANDLE = 0.01

MAX_SIGNAL_AGE_HOURS = 2

CHART_CANDLES = 100

REPORT_ENABLED = True


# ============================================================
# WHALE FLOW CONFIG
# ============================================================

WHALE_FILTER_ENABLED = True

WHALE_ANALYTICS_INTERVAL = 3600

WHALE_LOOKBACK_HOURS = 4

WHALE_MIN_SCORE = 2

WHALE_MIN_RVOL = 1.20

WHALE_STRONG_RVOL = 2.00

WHALE_CVD_THRESHOLD = 0.0

WHALE_OI_CONFIRM = True

WHALE_REQUIRE_DATA = True


# ============================================================
# KRAKEN FUTURES CHART API
# ============================================================

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1"
)


# ============================================================
# KRAKEN FUTURES ANALYTICS API
# ============================================================

KRAKEN_ANALYTICS_URL = (
    "https://futures.kraken.com/api/charts/v1/analytics"
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = (
    os.environ.get("TELEGRAM_BOT_TOKEN", "")
    or os.environ.get("TELEGRAM_TOKEN", "")
)

TELEGRAM_CHAT_ID = (
    os.environ.get("TELEGRAM_CHAT_ID", "")
    or os.environ.get("TELEGRAM_CHAT", "")
)


def telegram_config_status():

    token_ok = bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_BOT_TOKEN.strip()
    )

    chat_ok = bool(
        TELEGRAM_CHAT_ID
        and TELEGRAM_CHAT_ID.strip()
    )

    print("")
    print(
        "=========================================================="
    )
    print(
        "TELEGRAM CONFIGURATION"
    )
    print(
        "=========================================================="
    )

    print(
        "[TELEGRAM] Bot token:",
        "FOUND" if token_ok else "NOT FOUND"
    )

    print(
        "[TELEGRAM] Chat ID:",
        "FOUND" if chat_ok else "NOT FOUND"
    )

    if token_ok and chat_ok:

        print(
            "[TELEGRAM] Configuration: READY"
        )

    else:

        print(
            "[TELEGRAM] Configuration: INCOMPLETE"
        )

        print(
            "[TELEGRAM] "
            "Expected environment variables: "
            "TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID"
        )

    print(
        "=========================================================="
    )
    print("")

    return token_ok and chat_ok


# ============================================================
# 40 ASSETS
# ============================================================

SYMBOLS = [

    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_LINKUSD",
    "PF_AVAXUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",

    "PF_BNBUSD",
    "PF_TRXUSD",
    "PF_UNIUSD",
    "PF_AAVEUSD",
    "PF_SUIUSD",
    "PF_NEARUSD",
    "PF_ATOMUSD",
    "PF_FILUSD",
    "PF_ARBUSD",
    "PF_OPUSD",

    "PF_XLMUSD",
    "PF_BCHUSD",
    "PF_ETCUSD",
    "PF_ALGOUSD",
    "PF_APTUSD",
    "PF_INJUSD",
    "PF_SEIUSD",
    "PF_TAOUSD",
    "PF_WIFUSD",
    "PF_PEPEUSD",

    "PF_SHIBUSD",
    "PF_ZECUSD",
    "PF_EOSUSD",
    "PF_QNTUSD",
    "PF_IMXUSD",
    "PF_RUNEUSD",
    "PF_MKRUSD",
    "PF_CRVUSD",
    "PF_ENSUSD",
    "PF_LDOUSD",
]


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({

    "User-Agent":
        "Mozilla/5.0 "
        "(compatible; Kraken-1H-Trendline/3.1.0)",

    "Accept":
        "application/json",
})


# ============================================================
# GLOBAL RESULTS
# ============================================================

SCAN_RESULTS = []

NEW_SIGNALS = []

ERRORS = []


# ============================================================
# DATABASE
# ============================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            direction TEXT NOT NULL,

            candle_time INTEGER NOT NULL,

            entry_time INTEGER NOT NULL,

            entry_price REAL NOT NULL,

            tp_price REAL,

            sl_price REAL,

            tp_source TEXT,

            sl_source TEXT,

            trendline_type TEXT,

            trendline_start_time INTEGER,

            trendline_end_time INTEGER,

            trendline_start_price REAL,

            trendline_end_price REAL,

            trendline_value REAL,

            touches INTEGER,

            status TEXT NOT NULL DEFAULT 'OPEN',

            exit_time INTEGER,

            exit_price REAL,

            exit_reason TEXT,

            pnl_percent REAL,

            created_at INTEGER NOT NULL,

            UNIQUE(
                symbol,
                direction,
                candle_time
            )
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_signals_status
        ON signals(status)
    """)

    conn.commit()
    conn.close()


# ============================================================
# DATABASE MIGRATION
# ============================================================

def ensure_columns():

    conn = sqlite3.connect(DB_FILE)

    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(signals)"
        ).fetchall()
    }

    required = {

        "tp_price":
            "REAL",

        "sl_price":
            "REAL",

        "tp_source":
            "TEXT",

        "sl_source":
            "TEXT",

        "exit_time":
            "INTEGER",

        "exit_price":
            "REAL",

        "exit_reason":
            "TEXT",

        "pnl_percent":
            "REAL",
    }

    for name, dtype in required.items():

        if name not in columns:

            conn.execute(
                f"""
                ALTER TABLE signals
                ADD COLUMN {name} {dtype}
                """
            )

    conn.commit()
    conn.close()


# ============================================================
# TIME
# ============================================================

def now_ms():

    return int(
        time.time() * 1000
    )


def now_seconds():

    return int(
        time.time()
    )


def format_time(ts):

    if ts > 10_000_000_000:
        ts = ts / 1000

    return datetime.fromtimestamp(
        ts,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# ============================================================
# FETCH 1H CANDLES
# ============================================================

def fetch_ohlc(symbol):

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"trade/"
        f"{symbol}/"
        f"1h"
    )

    try:

        response = SESSION.get(
            url,
            params={
                "count":
                    CANDLE_COUNT
            },
            timeout=25
        )

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        error = (
            f"{symbol}: "
            f"OHLC failed: {exc}"
        )

        print(
            "[ERROR] "
            + error
        )

        ERRORS.append(error)

        return []

    raw = data.get(
        "candles"
    )

    if not isinstance(
        raw,
        list
    ):

        error = (
            f"{symbol}: "
            f"No candles returned"
        )

        ERRORS.append(error)

        return []

    candles = []

    for row in raw:

        try:

            if not isinstance(
                row,
                dict
            ):
                continue

            ts = row.get("time")
            op = row.get("open")
            hi = row.get("high")
            lo = row.get("low")
            cl = row.get("close")

            if None in (
                ts,
                op,
                hi,
                lo,
                cl
            ):
                continue

            ts = int(
                float(ts)
            )

            if ts < 10_000_000_000:
                ts *= 1000

            candles.append({

                "time":
                    ts,

                "open":
                    float(op),

                "high":
                    float(hi),

                "low":
                    float(lo),

                "close":
                    float(cl),
            })

        except Exception:
            continue

    candles.sort(
        key=lambda x:
            x["time"]
    )

    unique = {}

    for candle in candles:

        unique[
            candle["time"]
        ] = candle

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x:
            x["time"]
    )

    return candles[
        -CANDLE_COUNT:
    ]


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    candles
):

    if not candles:
        return []

    current_hour = (
        now_ms()
        // 3_600_000
    ) * 3_600_000

    return [
        c
        for c in candles
        if c["time"]
        < current_hour
    ]


# ============================================================
# KRAKEN MARKET ANALYTICS
# ============================================================

def fetch_analytics(
    symbol,
    analytics_type,
    interval=WHALE_ANALYTICS_INTERVAL
):

    now_sec = int(
        time.time()
    )

    since = (
        now_sec
        - (
            WHALE_LOOKBACK_HOURS
            * 3600
        )
    )

    url = (
        f"{KRAKEN_ANALYTICS_URL}/"
        f"{symbol}/"
        f"{analytics_type}"
    )

    try:

        response = SESSION.get(
            url,
            params={
                "since":
                    since,

                "interval":
                    interval,

                "to":
                    now_sec,
            },
            timeout=20
        )

        response.raise_for_status()

        payload = response.json()

        result = payload.get(
            "result",
            {}
        )

        timestamps = result.get(
            "timestamp",
            []
        )

        data = result.get(
            "data",
            []
        )

        if not timestamps or not data:

            return []

        return list(
            zip(
                timestamps,
                data
            )
        )

    except Exception as exc:

        print(
            f"[WHALE] "
            f"{symbol} "
            f"{analytics_type} "
            f"failed: {exc}"
        )

        return []


# ============================================================
# GENERIC ANALYTICS VALUE EXTRACTION
# ============================================================

def analytics_number(value):

    if value is None:
        return None

    if isinstance(
        value,
        (int, float)
    ):

        return float(value)

    if isinstance(
        value,
        str
    ):

        try:
            return float(value)
        except Exception:
            return None

    return None


def extract_scalar_series(
    rows
):

    values = []

    for ts, value in rows:

        number = analytics_number(
            value
        )

        if number is not None:

            values.append({
                "time":
                    int(ts),

                "value":
                    number
            })

    return values


# ============================================================
# CVD / AGGRESSOR HELPERS
# ============================================================

def get_cvd_direction(
    symbol
):

    rows = fetch_analytics(
        symbol,
        "cvd"
    )

    values = extract_scalar_series(
        rows
    )

    if len(values) < 2:
        return None

    previous = values[-2]["value"]
    latest = values[-1]["value"]

    delta = (
        latest
        - previous
    )

    return {

        "value":
            latest,

        "delta":
            delta,

        "direction":
            (
                "BUY"
                if delta > WHALE_CVD_THRESHOLD
                else
                "SELL"
                if delta < -WHALE_CVD_THRESHOLD
                else
                "NEUTRAL"
            ),
    }


def get_aggressor_direction(
    symbol
):

    rows = fetch_analytics(
        symbol,
        "aggressor-differential"
    )

    values = extract_scalar_series(
        rows
    )

    if not values:
        return None

    latest = values[-1]["value"]

    return {

        "value":
            latest,

        "direction":
            (
                "BUY"
                if latest > 0
                else
                "SELL"
                if latest < 0
                else
                "NEUTRAL"
            ),
    }


# ============================================================
# TRADE VOLUME
# ============================================================

def get_volume_stats(
    symbol
):

    rows = fetch_analytics(
        symbol,
        "trade-volume"
    )

    values = extract_scalar_series(
        rows
    )

    if len(values) < 2:
        return None

    latest = values[-1]["value"]

    previous = [
        x["value"]
        for x in values[:-1]
        if x["value"] >= 0
    ]

    if not previous:

        return {

            "latest":
                latest,

            "average":
                0,

            "rvol":
                0,
        }

    average = (
        sum(previous)
        / len(previous)
    )

    if average <= 0:

        rvol = 0

    else:

        rvol = (
            latest
            / average
        )

    return {

        "latest":
            latest,

        "average":
            average,

        "rvol":
            rvol,
    }


# ============================================================
# OPEN INTEREST
# ============================================================

def get_oi_direction(
    symbol
):

    rows = fetch_analytics(
        symbol,
        "open-interest"
    )

    values = extract_scalar_series(
        rows
    )

    if len(values) < 2:
        return None

    previous = values[-2]["value"]
    latest = values[-1]["value"]

    if previous == 0:

        return {

            "latest":
                latest,

            "delta":
                0,

            "direction":
                "NEUTRAL",
        }

    delta_percent = (
        (
            latest
            - previous
        )
        /
        abs(previous)
        * 100
    )

    return {

        "latest":
            latest,

        "delta":
            delta_percent,

        "direction":
            (
                "UP"
                if delta_percent > 0
                else
                "DOWN"
                if delta_percent < 0
                else
                "NEUTRAL"
            ),
    }


# ============================================================
# WHALE FLOW ANALYSIS
# ============================================================

def analyze_whale_flow(
    symbol,
    direction
):

    result = {

        "available":
            False,

        "direction":
            "NEUTRAL",

        "score":
            0,

        "cvd":
            None,

        "aggressor":
            None,

        "volume":
            None,

        "oi":
            None,

        "reason":
            "NO DATA",
    }

    if not WHALE_FILTER_ENABLED:

        result["available"] = True

        result["direction"] = "DISABLED"

        result["score"] = 0

        result["reason"] = (
            "FILTER DISABLED"
        )

        return result

    cvd = get_cvd_direction(
        symbol
    )

    aggressor = get_aggressor_direction(
        symbol
    )

    volume = get_volume_stats(
        symbol
    )

    oi = get_oi_direction(
        symbol
    )

    result["cvd"] = cvd
    result["aggressor"] = aggressor
    result["volume"] = volume
    result["oi"] = oi

    available_count = sum(
        x is not None
        for x in (
            cvd,
            aggressor,
            volume,
            oi
        )
    )

    if available_count == 0:

        result["reason"] = (
            "NO ANALYTICS DATA"
        )

        return result

    result["available"] = True

    score = 0

    # --------------------------------------------------------
    # Aggressor differential
    # --------------------------------------------------------

    if aggressor:

        if direction == "LONG":

            if aggressor["direction"] == "BUY":
                score += 2

            elif aggressor["direction"] == "SELL":
                score -= 2

        else:

            if aggressor["direction"] == "SELL":
                score += 2

            elif aggressor["direction"] == "BUY":
                score -= 2

    # --------------------------------------------------------
    # CVD
    # --------------------------------------------------------

    if cvd:

        if direction == "LONG":

            if cvd["direction"] == "BUY":
                score += 2

            elif cvd["direction"] == "SELL":
                score -= 2

        else:

            if cvd["direction"] == "SELL":
                score += 2

            elif cvd["direction"] == "BUY":
                score -= 2

    # --------------------------------------------------------
    # Volume
    # --------------------------------------------------------

    if volume:

        if (
            volume["rvol"]
            >= WHALE_MIN_RVOL
        ):

            score += 1

        if (
            volume["rvol"]
            >= WHALE_STRONG_RVOL
        ):

            score += 1

    # --------------------------------------------------------
    # Open Interest
    # --------------------------------------------------------

    if (
        WHALE_OI_CONFIRM
        and oi
    ):

        # Increasing OI confirms
        # fresh participation.
        #
        # It does NOT determine direction
        # by itself.

        if oi["direction"] == "UP":

            score += 1

    result["score"] = score

    if score >= WHALE_MIN_SCORE:

        result["direction"] = direction

        result["reason"] = (
            "WHALE FLOW CONFIRMS"
        )

    elif score <= -WHALE_MIN_SCORE:

        result["direction"] = (
            "OPPOSITE"
        )

        result["reason"] = (
            "WHALE FLOW OPPOSES"
        )

    else:

        result["direction"] = (
            "NEUTRAL"
        )

        result["reason"] = (
            "WHALE FLOW WEAK"
        )

    return result


# ============================================================
# WHALE TEXT
# ============================================================

def whale_flow_text(
    whale
):

    if whale is None:

        return (
            "🐋 Whale Flow: N/A"
        )

    if not whale.get(
        "available",
        False
    ):

        return (
            "🐋 Whale Flow: NO DATA"
        )

    direction = whale.get(
        "direction",
        "NEUTRAL"
    )

    score = whale.get(
        "score",
        0
    )

    if direction == "LONG":

        flow = "BUY"

    elif direction == "SHORT":

        flow = "SELL"

    elif direction == "OPPOSITE":

        flow = "OPPOSITE"

    elif direction == "DISABLED":

        flow = "DISABLED"

    else:

        flow = "NEUTRAL"

    lines = []

    lines.append(
        f"🐋 Whale Flow: {flow}"
    )

    lines.append(
        f"Whale Score: {score:+d}"
    )

    cvd = whale.get(
        "cvd"
    )

    if cvd:

        lines.append(
            "CVD: "
            f"{cvd['direction']}"
        )

    aggressor = whale.get(
        "aggressor"
    )

    if aggressor:

        lines.append(
            "Aggressor: "
            f"{aggressor['direction']}"
        )

    volume = whale.get(
        "volume"
    )

    if volume:

        lines.append(
            "RVOL: "
            f"{volume['rvol']:.2f}x"
        )

    oi = whale.get(
        "oi"
    )

    if oi:

        lines.append(
            "OI: "
            f"{oi['direction']}"
        )

    return "\n".join(lines)


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    candles
):

    if not candles:
        return []

    current_hour = (
        now_ms()
        // 3_600_000
    ) * 3_600_000

    return [
        c
        for c in candles
        if c["time"]
        < current_hour
    ]


# ============================================================
# PIVOTS
# ============================================================

def is_pivot_high(
    candles,
    i
):

    if i < PIVOT_LEFT:
        return False

    if (
        i + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    price = candles[i]["high"]

    for j in range(
        i - PIVOT_LEFT,
        i
    ):

        if candles[j]["high"] >= price:
            return False

    for j in range(
        i + 1,
        i + PIVOT_RIGHT + 1
    ):

        if candles[j]["high"] > price:
            return False

    return True


def is_pivot_low(
    candles,
    i
):

    if i < PIVOT_LEFT:
        return False

    if (
        i + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    price = candles[i]["low"]

    for j in range(
        i - PIVOT_LEFT,
        i
    ):

        if candles[j]["low"] <= price:
            return False

    for j in range(
        i + 1,
        i + PIVOT_RIGHT + 1
    ):

        if candles[j]["low"] < price:
            return False

    return True


def get_pivots(
    candles
):

    highs = []
    lows = []

    start = max(
        PIVOT_LEFT,
        len(candles)
        - LOOKBACK_CANDLES
    )

    end = (
        len(candles)
        - PIVOT_RIGHT
    )

    for i in range(
        start,
        end
    ):

        if is_pivot_high(
            candles,
            i
        ):

            highs.append({

                "index":
                    i,

                "time":
                    candles[i]["time"],

                "price":
                    candles[i]["high"],
            })

        if is_pivot_low(
            candles,
            i
        ):

            lows.append({

                "index":
                    i,

                "time":
                    candles[i]["time"],

                "price":
                    candles[i]["low"],
            })

    return highs, lows


# ============================================================
# LINE
# ============================================================

def line_value(
    x,
    x1,
    y1,
    x2,
    y2
):

    if x1 == x2:
        return None

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return (
        y1
        + slope * (
            x - x1
        )
    )


def slope_percent(
    x1,
    y1,
    x2,
    y2
):

    if x1 == x2:
        return 0

    if y1 == 0:
        return 0

    return (
        (
            y2 - y1
        )
        /
        (
            x2 - x1
        )
        /
        y1
        * 100
    )


# ============================================================
# DESCENDING RESISTANCE
# ============================================================

def find_resistance(
    candles,
    pivots
):

    if len(pivots) < MIN_TOUCHES:
        return None

    best = None

    for a in range(
        len(pivots) - 1
    ):

        for b in range(
            a + 1,
            len(pivots)
        ):

            p1 = pivots[a]
            p2 = pivots[b]

            x1 = p1["index"]
            x2 = p2["index"]

            if (
                x2 - x1
                < MIN_LINE_LENGTH
            ):
                continue

            y1 = p1["price"]
            y2 = p2["price"]

            slope = slope_percent(
                x1,
                y1,
                x2,
                y2
            )

            if (
                slope
                >=
                -MIN_SLOPE_PERCENT_PER_CANDLE
            ):
                continue

            touches = []

            valid = True

            for pivot in pivots:

                x = pivot["index"]

                if (
                    x < x1
                    or x > x2
                ):
                    continue

                line = line_value(
                    x,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if line is None:
                    continue

                distance = (
                    abs(
                        pivot["price"]
                        - line
                    )
                    / line
                )

                if (
                    distance
                    <= TOUCH_TOLERANCE
                ):

                    touches.append(
                        pivot
                    )

            if len(touches) < MIN_TOUCHES:
                continue

            for i in range(
                x1,
                x2 + 1
            ):

                line = line_value(
                    i,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if line is None:
                    continue

                if (
                    candles[i]["close"]
                    >
                    line
                    * (
                        1
                        + TOUCH_TOLERANCE
                    )
                ):

                    valid = False
                    break

            if not valid:
                continue

            candidate = {

                "type":
                    "DESCENDING_RESISTANCE",

                "start":
                    p1,

                "end":
                    p2,

                "touches":
                    len(touches),

                "score":
                    len(touches)
                    * 10000
                    + x2,
            }

            if (
                best is None
                or candidate["score"]
                > best["score"]
            ):

                best = candidate

    return best


# ============================================================
# ASCENDING SUPPORT
# ============================================================

def find_support(
    candles,
    pivots
):

    if len(pivots) < MIN_TOUCHES:
        return None

    best = None

    for a in range(
        len(pivots) - 1
    ):

        for b in range(
            a + 1,
            len(pivots)
        ):

            p1 = pivots[a]
            p2 = pivots[b]

            x1 = p1["index"]
            x2 = p2["index"]

            if (
                x2 - x1
                < MIN_LINE_LENGTH
            ):
                continue

            y1 = p1["price"]
            y2 = p2["price"]

            slope = slope_percent(
                x1,
                y1,
                x2,
                y2
            )

            if (
                slope
                <=
                MIN_SLOPE_PERCENT_PER_CANDLE
            ):
                continue

            touches = []

            valid = True

            for pivot in pivots:

                x = pivot["index"]

                if (
                    x < x1
                    or x > x2
                ):
                    continue

                line = line_value(
                    x,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if line is None:
                    continue

                distance = (
                    abs(
                        pivot["price"]
                        - line
                    )
                    / line
                )

                if (
                    distance
                    <= TOUCH_TOLERANCE
                ):

                    touches.append(
                        pivot
                    )

            if len(touches) < MIN_TOUCHES:
                continue

            for i in range(
                x1,
                x2 + 1
            ):

                line = line_value(
                    i,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if line is None:
                    continue

                if (
                    candles[i]["close"]
                    <
                    line
                    * (
                        1
                        - TOUCH_TOLERANCE
                    )
                ):

                    valid = False
                    break

            if not valid:
                continue

            candidate = {

                "type":
                    "ASCENDING_SUPPORT",

                "start":
                    p1,

                "end":
                    p2,

                "touches":
                    len(touches),

                "score":
                    len(touches)
                    * 10000
                    + x2,
            }

            if (
                best is None
                or candidate["score"]
                > best["score"]
            ):

                best = candidate

    return best


# ============================================================
# VALID TP / SL
# ============================================================

def calculate_levels(
    candles,
    highs,
    lows,
    direction,
    entry_index,
    entry_price
):

    previous_highs = [
        p
        for p in highs
        if p["index"] < entry_index
    ]

    previous_lows = [
        p
        for p in lows
        if p["index"] < entry_index
    ]

    if direction == "LONG":

        targets = [
            p
            for p in previous_highs
            if p["price"] > entry_price
        ]

        targets.sort(
            key=lambda p:
                p["price"]
        )

        tp = (
            targets[0]
            if targets
            else None
        )

        stops = [
            p
            for p in previous_lows
            if p["price"] < entry_price
        ]

        stops.sort(
            key=lambda p:
                abs(
                    entry_price
                    - p["price"]
                )
        )

        sl = (
            stops[0]
            if stops
            else None
        )

        return tp, sl

    targets = [
        p
        for p in previous_lows
        if p["price"] < entry_price
    ]

    targets.sort(
        key=lambda p:
            p["price"],
        reverse=True
    )

    tp = (
        targets[0]
        if targets
        else None
    )

    stops = [
        p
        for p in previous_highs
        if p["price"] > entry_price
    ]

    stops.sort(
        key=lambda p:
            abs(
                entry_price
                - p["price"]
            )
    )

    sl = (
        stops[0]
        if stops
        else None
    )

    return tp, sl


# ============================================================
# CHECK BREAKOUT
# ============================================================

def check_breakout(
    candles,
    trendline,
    direction
):

    if trendline is None:
        return None

    if len(candles) < 2:
        return None

    latest_i = len(candles) - 1
    previous_i = latest_i - 1

    latest = candles[latest_i]
    previous = candles[previous_i]

    start = trendline["start"]
    end = trendline["end"]

    previous_line = line_value(
        previous_i,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    latest_line = line_value(
        latest_i,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    if (
        previous_line is None
        or latest_line is None
    ):
        return None

    if direction == "LONG":

        previous_ok = (
            previous["close"]
            <=
            previous_line
            * (
                1
                + TOUCH_TOLERANCE
            )
        )

        broke = (
            latest["close"]
            >
            latest_line
            * (
                1
                + TOUCH_TOLERANCE
            )
        )

    else:

        previous_ok = (
            previous["close"]
            >=
            previous_line
            * (
                1
                - TOUCH_TOLERANCE
            )
        )

        broke = (
            latest["close"]
            <
            latest_line
            * (
                1
                - TOUCH_TOLERANCE
            )
        )

    if not (
        previous_ok
        and broke
    ):
        return None

    return {

        "direction":
            direction,

        "candle_time":
            latest["time"],

        "entry_price":
            latest["close"],

        "open":
            latest["open"],

        "high":
            latest["high"],

        "low":
            latest["low"],

        "close":
            latest["close"],

        "trendline_type":
            trendline["type"],

        "trendline_start_time":
            start["time"],

        "trendline_end_time":
            end["time"],

        "trendline_start_price":
            start["price"],

        "trendline_end_price":
            end["price"],

        "trendline_value":
            latest_line,

        "touches":
            trendline["touches"],

        "entry_index":
            latest_i,
    }


# ============================================================
# SIGNAL AGE
# ============================================================

def signal_is_fresh(
    candle_time
):

    age_hours = (
        now_ms()
        - candle_time
    ) / (
        60 * 60 * 1000
    )

    return (
        age_hours
        <= MAX_SIGNAL_AGE_HOURS
    )


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    signal
):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.execute("""
        INSERT OR IGNORE INTO signals (

            symbol,
            direction,
            candle_time,
            entry_time,
            entry_price,

            tp_price,
            sl_price,

            tp_source,
            sl_source,

            trendline_type,

            trendline_start_time,
            trendline_end_time,

            trendline_start_price,
            trendline_end_price,

            trendline_value,
            touches,

            status,

            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?,
            ?, ?,
            ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?, ?,
            'OPEN',
            ?
        )
    """, (

        signal["symbol"],
        signal["direction"],
        signal["candle_time"],
        signal["candle_time"],
        signal["entry_price"],

        signal["tp_price"],
        signal["sl_price"],

        signal["tp_source"],
        signal["sl_source"],

        signal["trendline_type"],

        signal["trendline_start_time"],
        signal["trendline_end_time"],

        signal["trendline_start_price"],
        signal["trendline_end_price"],

        signal["trendline_value"],
        signal["touches"],

        now_seconds(),
    ))

    inserted = (
        cursor.rowcount == 1
    )

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# CURRENT PRICE
# ============================================================

def current_price(
    symbol,
    candles
):

    if not candles:
        return None

    return candles[-1]["close"]


# ============================================================
# CHECK OPEN TRADES
# ============================================================

def update_open_trades(
    price_map
):

    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute("""
        SELECT
            id,
            symbol,
            direction,
            entry_price,
            tp_price,
            sl_price,
            candle_time
        FROM signals
        WHERE status = 'OPEN'
    """).fetchall()

    closed = []

    for row in rows:

        (
            trade_id,
            symbol,
            direction,
            entry,
            tp,
            sl,
            candle_time
        ) = row

        price = price_map.get(symbol)

        if price is None:
            continue

        exit_reason = None
        exit_price = None

        if direction == "LONG":

            if (
                tp is not None
                and price >= tp
            ):

                exit_reason = "TP"
                exit_price = tp

            elif (
                sl is not None
                and price <= sl
            ):

                exit_reason = "SL"
                exit_price = sl

        else:

            if (
                tp is not None
                and price <= tp
            ):

                exit_reason = "TP"
                exit_price = tp

            elif (
                sl is not None
                and price >= sl
            ):

                exit_reason = "SL"
                exit_price = sl

        if exit_reason:

            if direction == "LONG":

                pnl = (
                    (
                        exit_price
                        - entry
                    )
                    / entry
                ) * 100

            else:

                pnl = (
                    (
                        entry
                        - exit_price
                    )
                    / entry
                ) * 100

            conn.execute("""
                UPDATE signals
                SET
                    status = 'CLOSED',
                    exit_time = ?,
                    exit_price = ?,
                    exit_reason = ?,
                    pnl_percent = ?
                WHERE id = ?
            """, (

                now_seconds(),
                exit_price,
                exit_reason,
                pnl,
                trade_id,
            ))

            closed.append({

                "symbol":
                    symbol,

                "direction":
                    direction,

                "reason":
                    exit_reason,

                "pnl":
                    pnl,

                "entry":
                    entry,

                "exit":
                    exit_price,
            })

    conn.commit()
    conn.close()

    return closed


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = sqlite3.connect(DB_FILE)

    rows = conn.execute("""
        SELECT
            id,
            symbol,
            direction,
            entry_time,
            entry_price,
            tp_price,
            sl_price,
            touches
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY entry_time DESC
    """).fetchall()

    conn.close()

    return rows


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = sqlite3.connect(DB_FILE)

    total = conn.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    wins = conn.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
        AND pnl_percent > 0
    """).fetchone()[0]

    losses = conn.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
        AND pnl_percent <= 0
    """).fetchone()[0]

    pnl = conn.execute("""
        SELECT COALESCE(
            SUM(pnl_percent),
            0
        )
        FROM signals
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    conn.close()

    if total > 0:

        win_rate = (
            wins
            / total
            * 100
        )

        loss_rate = (
            losses
            / total
            * 100
        )

    else:

        win_rate = 0
        loss_rate = 0

    return {

        "total":
            total,

        "wins":
            wins,

        "losses":
            losses,

        "win_rate":
            win_rate,

        "loss_rate":
            loss_rate,

        "pnl":
            pnl,
    }


# ============================================================
# PERCENT TO TP / SL
# ============================================================

def distance_percent(
    direction,
    current,
    level
):

    if (
        current is None
        or level is None
        or current == 0
    ):
        return None

    if direction == "LONG":

        return (
            (
                level
                - current
            )
            / current
            * 100
        )

    return (
        (
            current
            - level
        )
        / current
        * 100
    )


def current_pnl_percent(
    direction,
    entry,
    current
):

    if (
        entry is None
        or current is None
        or entry == 0
    ):
        return 0

    if direction == "LONG":

        return (
            (
                current
                - entry
            )
            / entry
            * 100
        )

    return (
        (
            entry
            - current
        )
        / entry
        * 100
    )


# ============================================================
# TELEGRAM MESSAGE
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "[TELEGRAM] "
            "Message skipped: bot token missing"
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM] "
            "Message skipped: chat ID missing"
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    try:

        r = SESSION.post(
            url,
            json={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message,

                "disable_web_page_preview":
                    True,
            },
            timeout=20
        )

        if not r.ok:

            print(
                "[ERROR] Telegram sendMessage:",
                r.status_code,
                r.text[:500]
            )

            return False

        data = r.json()

        if not data.get("ok"):

            print(
                "[ERROR] Telegram API:",
                data
            )

            return False

        print(
            "[TELEGRAM] Message sent"
        )

        return True

    except Exception as exc:

        print(
            "[ERROR] Telegram:",
            exc
        )

        return False


# ============================================================
# TELEGRAM PHOTO
# ============================================================

def send_telegram_photo(
    path,
    caption=""
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM] "
            "Photo skipped: bot token missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM] "
            "Photo skipped: chat ID missing"
        )

        return False

    if not os.path.exists(path):

        print(
            "[TELEGRAM] "
            "Photo skipped: file not found"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}"
        "/sendPhoto"
    )

    try:

        with open(
            path,
            "rb"
        ) as photo:

            r = SESSION.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,

                    "caption":
                        caption,
                },
                files={
                    "photo":
                        photo
                },
                timeout=40
            )

        if not r.ok:

            print(
                "[ERROR] Telegram sendPhoto:",
                r.status_code,
                r.text[:500]
            )

            return False

        data = r.json()

        if not data.get("ok"):

            print(
                "[ERROR] Telegram photo API:",
                data
            )

            return False

        print(
            "[TELEGRAM] Photo sent"
        )

        return True

    except Exception as exc:

        print(
            "[ERROR] Telegram photo:",
            exc
        )

        return False


# ============================================================
# CHART
# ============================================================

def create_signal_chart(
    symbol,
    candles,
    highs,
    lows,
    signal,
    tp_price,
    sl_price
):

    df = pd.DataFrame(
        candles[-CHART_CANDLES:]
    )

    if df.empty:
        return None

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    fig, ax = plt.subplots(
        figsize=(13, 7)
    )

    for i, row in df.iterrows():

        x = i

        op = row["open"]
        hi = row["high"]
        lo = row["low"]
        cl = row["close"]

        if cl >= op:

            body_bottom = op
            body_height = cl - op

        else:

            body_bottom = cl
            body_height = op - cl

        ax.vlines(
            x,
            lo,
            hi,
            linewidth=1
        )

        ax.add_patch(
            plt.Rectangle(
                (
                    x - 0.3,
                    body_bottom
                ),
                0.6,
                max(
                    body_height,
                    abs(cl) * 0.00001
                ),
            )
        )

    chart_start_time = (
        candles[-CHART_CANDLES]["time"]
    )

    chart_candles = (
        candles[-CHART_CANDLES:]
    )

    for pivot in highs:

        if (
            pivot["time"]
            >= chart_start_time
        ):

            index = next(
                (
                    i
                    for i, c
                    in enumerate(
                        chart_candles
                    )
                    if c["time"]
                    == pivot["time"]
                ),
                None
            )

            if index is not None:

                ax.scatter(
                    index,
                    pivot["price"],
                    marker="^",
                    s=45
                )

    for pivot in lows:

        if (
            pivot["time"]
            >= chart_start_time
        ):

            index = next(
                (
                    i
                    for i, c
                    in enumerate(
                        chart_candles
                    )
                    if c["time"]
                    == pivot["time"]
                ),
                None
            )

            if index is not None:

                ax.scatter(
                    index,
                    pivot["price"],
                    marker="v",
                    s=45
                )

    # --------------------------------------------------------
    # Trendline
    # --------------------------------------------------------

    start_time = signal[
        "trendline_start_time"
    ]

    end_time = signal[
        "trendline_end_time"
    ]

    start_price = signal[
        "trendline_start_price"
    ]

    end_price = signal[
        "trendline_end_price"
    ]

    start_index = None
    end_index = None

    for i, candle in enumerate(candles):

        if candle["time"] == start_time:
            start_index = i

        if candle["time"] == end_time:
            end_index = i

    if (
        start_index is not None
        and end_index is not None
    ):

        line_x = []
        line_y = []

        chart_offset = (
            len(candles)
            - CHART_CANDLES
        )

        for i in range(
            max(
                start_index,
                chart_offset
            ),
            len(candles)
        ):

            value = line_value(
                i,
                start_index,
                start_price,
                end_index,
                end_price
            )

            if value is not None:

                line_x.append(
                    i - chart_offset
                )

                line_y.append(
                    value
                )

        if line_x:

            ax.plot(
                line_x,
                line_y,
                linewidth=2
            )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    breakout_index = None

    for i, candle in enumerate(
        chart_candles
    ):

        if (
            candle["time"]
            == signal["candle_time"]
        ):

            breakout_index = i
            break

    if breakout_index is not None:

        ax.scatter(
            breakout_index,
            signal["entry_price"],
            s=120,
            marker="*"
        )

        ax.annotate(
            "BREAKOUT",
            (
                breakout_index,
                signal["entry_price"]
            ),
            xytext=(
                10,
                20
            ),
            textcoords="offset points"
        )

        ax.axhline(
            signal["entry_price"],
            linewidth=1,
            linestyle="--"
        )

        ax.annotate(
            f"ENTRY {signal['entry_price']:.8g}",
            (
                breakout_index,
                signal["entry_price"]
            ),
            xytext=(
                10,
                -25
            ),
            textcoords="offset points"
        )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    if tp_price is not None:

        ax.axhline(
            tp_price,
            linewidth=1,
            linestyle="--"
        )

        ax.text(
            0.01,
            tp_price,
            f"TP {tp_price:.8g}",
            transform=ax.get_yaxis_transform()
        )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    if sl_price is not None:

        ax.axhline(
            sl_price,
            linewidth=1,
            linestyle="--"
        )

        ax.text(
            0.01,
            sl_price,
            f"SL {sl_price:.8g}",
            transform=ax.get_yaxis_transform()
        )

    ax.set_title(
        f"{symbol} | 1H Trendline Breakout"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.25
    )

    fig.autofmt_xdate()

    plt.tight_layout()

    safe_symbol = (
        symbol.replace(
            "/",
            "_"
        )
    )

    path = (
        f"chart_{safe_symbol}.png"
    )

    plt.savefig(
        path,
        dpi=160
    )

    plt.close(fig)

    return path


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    signal
):

    emoji = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )

    tp = signal["tp_price"]
    sl = signal["sl_price"]

    whale = signal.get(
        "whale"
    )

    return (
        f"{emoji} NEW 1H SIGNAL\n"
        f"\n"
        f"{signal['symbol']}\n"
        f"Direction: "
        f"{signal['direction']}\n"
        f"\n"
        f"Entry: "
        f"{signal['entry_price']}\n"
        f"TP: "
        f"{tp}\n"
        f"SL: "
        f"{sl}\n"
        f"\n"
        f"TP source: "
        f"{signal['tp_source']}\n"
        f"SL source: "
        f"{signal['sl_source']}\n"
        f"\n"
        f"Trendline: "
        f"{signal['trendline_type']}\n"
        f"Touches: "
        f"{signal['touches']}\n"
        f"\n"
        f"{whale_flow_text(whale)}\n"
        f"\n"
        f"Break: "
        f"{format_time(signal['candle_time'])}\n"
        f"\n"
        f"⏱ Signal age <= 2H\n"
        f"📌 PAPER ONLY"
    )


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report(
    price_map
):

    open_trades = get_open_trades()

    performance = get_performance()

    lines = []

    lines.append(
        "📊 1H TRENDLINE REPORT"
    )

    lines.append("")

    lines.append(
        f"Assets scanned: "
        f"{len(SYMBOLS)}"
    )

    lines.append(
        f"🟢 New LONG: "
        f"{sum(1 for s in NEW_SIGNALS if s['direction'] == 'LONG')}"
    )

    lines.append(
        f"🔴 New SHORT: "
        f"{sum(1 for s in NEW_SIGNALS if s['direction'] == 'SHORT')}"
    )

    lines.append("")

    lines.append(
        f"📌 OPEN TRADES: "
        f"{len(open_trades)}"
    )

    lines.append("")

    if not open_trades:

        lines.append(
            "No open paper trades."
        )

    else:

        for row in open_trades:

            (
                trade_id,
                symbol,
                direction,
                entry_time,
                entry,
                tp,
                sl,
                touches
            ) = row

            current = price_map.get(
                symbol
            )

            pnl = current_pnl_percent(
                direction,
                entry,
                current
            )

            tp_pct = distance_percent(
                direction,
                current,
                tp
            )

            sl_pct = distance_percent(
                direction,
                current,
                sl
            )

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} {symbol} "
                f"{direction}"
            )

            lines.append(
                f"Entry: {entry}"
            )

            lines.append(
                f"Current: {current}"
            )

            lines.append(
                f"P/L: {pnl:+.2f}%"
            )

            if tp_pct is not None:

                lines.append(
                    f"TP distance: "
                    f"{tp_pct:+.2f}%"
                )

            else:

                lines.append(
                    "TP distance: N/A"
                )

            if sl_pct is not None:

                lines.append(
                    f"SL distance: "
                    f"{sl_pct:+.2f}%"
                )

            else:

                lines.append(
                    "SL distance: N/A"
                )

            lines.append(
                f"TP: {tp}"
            )

            lines.append(
                f"SL: {sl}"
            )

            lines.append(
                f"Touches: {touches}"
            )

            lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append("")

    lines.append(
        f"Closed trades: "
        f"{performance['total']}"
    )

    lines.append(
        f"✅ Wins: "
        f"{performance['wins']}"
    )

    lines.append(
        f"❌ Losses: "
        f"{performance['losses']}"
    )

    lines.append(
        f"Win rate: "
        f"{performance['win_rate']:.2f}%"
    )

    lines.append(
        f"Loss rate: "
        f"{performance['loss_rate']:.2f}%"
    )

    lines.append(
        f"PnL: "
        f"{performance['pnl']:+.2f}%"
    )

    lines.append("")

    lines.append(
        "Mode: PAPER ONLY"
    )

    return "\n".join(lines)


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    price_map
):

    print(
        f"\n[SCAN] {symbol}"
    )

    candles = fetch_ohlc(symbol)

    if len(candles) < 50:

        SCAN_RESULTS.append({
            "symbol":
                symbol,

            "status":
                "ERROR"
        })

        return

    price_map[
        symbol
    ] = current_price(
        symbol,
        candles
    )

    closed = get_closed_candles(
        candles
    )

    if len(closed) < 50:

        SCAN_RESULTS.append({
            "symbol":
                symbol,

            "status":
                "ERROR"
        })

        return

    analysis = closed[:-1]

    highs, lows = get_pivots(
        analysis
    )

    resistance = find_resistance(
        analysis,
        highs
    )

    support = find_support(
        analysis,
        lows
    )

    signal = None

    long_break = check_breakout(
        closed,
        resistance,
        "LONG"
    )

    if long_break:

        if signal_is_fresh(
            long_break["candle_time"]
        ):

            signal = long_break

    short_break = check_breakout(
        closed,
        support,
        "SHORT"
    )

    if short_break:

        if signal_is_fresh(
            short_break["candle_time"]
        ):

            signal = short_break

    if signal:

        # ----------------------------------------------------
        # WHALE FLOW FILTER
        # ----------------------------------------------------

        whale = analyze_whale_flow(
            symbol,
            signal["direction"]
        )

        signal["whale"] = whale

        print(
            f"[WHALE] "
            f"{symbol} "
            f"{signal['direction']} | "
            f"Score={whale['score']} | "
            f"{whale['reason']}"
        )

        if (
            WHALE_FILTER_ENABLED
            and WHALE_REQUIRE_DATA
            and not whale["available"]
        ):

            print(
                f"[WHALE BLOCK] "
                f"{symbol} "
                f"analytics unavailable"
            )

            SCAN_RESULTS.append({
                "symbol":
                    symbol,

                "status":
                    "WHALE_DATA_ERROR"
            })

            return

        if (
            WHALE_FILTER_ENABLED
            and whale["direction"]
            != signal["direction"]
        ):

            print(
                f"[WHALE BLOCK] "
                f"{symbol} "
                f"{signal['direction']} "
                f"blocked by whale flow"
            )

            SCAN_RESULTS.append({
                "symbol":
                    symbol,

                "status":
                    "WHALE_BLOCKED"
            })

            return

        # ----------------------------------------------------
        # TP / SL
        # ----------------------------------------------------

        tp, sl = calculate_levels(
            analysis,
            highs,
            lows,
            signal["direction"],
            signal["entry_index"],
            signal["entry_price"]
        )

        if tp is not None and sl is not None:

            if signal["direction"] == "LONG":

                valid_levels = (
                    sl["price"]
                    < signal["entry_price"]
                    < tp["price"]
                )

            else:

                valid_levels = (
                    tp["price"]
                    < signal["entry_price"]
                    < sl["price"]
                )

            if valid_levels:

                signal["tp_price"] = tp["price"]

                signal["sl_price"] = sl["price"]

                signal["tp_source"] = (
                    "Valid prior swing "
                    f"{'HIGH' if signal['direction'] == 'LONG' else 'LOW'}"
                )

                signal["sl_source"] = (
                    "Valid prior swing "
                    f"{'LOW' if signal['direction'] == 'LONG' else 'HIGH'}"
                )

                signal["symbol"] = symbol

                inserted = insert_signal(
                    signal
                )

                if inserted:

                    NEW_SIGNALS.append(
                        signal
                    )

                    print(
                        f"[NEW SIGNAL] "
                        f"{symbol} "
                        f"{signal['direction']} "
                        f"| Whale Score="
                        f"{whale['score']}"
                    )

                    # Telegram signal
                    send_telegram(
                        signal_message(
                            signal
                        )
                    )

                    # Telegram chart
                    try:

                        chart = create_signal_chart(
                            symbol,
                            closed,
                            highs,
                            lows,
                            signal,
                            signal["tp_price"],
                            signal["sl_price"]
                        )

                        if chart:

                            caption = (
                                f"{symbol} "
                                f"{signal['direction']} | "
                                f"1H Trendline Breakout | "
                                f"Whale Score "
                                f"{whale['score']:+d}"
                            )

                            send_telegram_photo(
                                chart,
                                caption
                            )

                            try:
                                os.remove(chart)
                            except Exception:
                                pass

                    except Exception as exc:

                        print(
                            "[ERROR] Chart:",
                            exc
                        )

                else:

                    print(
                        "[DUPLICATE]",
                        symbol
                    )

            else:

                print(
                    f"[LEVELS INVALID] "
                    f"{symbol}"
                )

        else:

            print(
                f"[NO VALID TP/SL] "
                f"{symbol}"
            )

    SCAN_RESULTS.append({
        "symbol":
            symbol,

        "status":
            "OK"
    })


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    global SCAN_RESULTS
    global NEW_SIGNALS
    global ERRORS

    SCAN_RESULTS = []
    NEW_SIGNALS = []
    ERRORS = []

    price_map = {}

    print(
        "\n"
        "=========================================================="
    )

    print(
        f"KRAKEN FUTURES "
        f"1H TRENDLINE SCANNER "
        f"v{VERSION}"
    )

    print(
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

    print(
        f"Assets: {len(SYMBOLS)}"
    )

    print(
        "REAL_TRADING = False"
    )

    print(
        "PAPER_TRADING = True"
    )

    print(
        "WHALE_FILTER_ENABLED = "
        f"{WHALE_FILTER_ENABLED}"
    )

    print(
        "WHALE_MIN_SCORE = "
        f"{WHALE_MIN_SCORE}"
    )

    print(
        "=========================================================="
    )

    telegram_config_status()

    # --------------------------------------------------------
    # Scan 40 assets
    # --------------------------------------------------------

    for symbol in SYMBOLS:

        try:

            analyze_symbol(
                symbol,
                price_map
            )

        except Exception as exc:

            error = (
                f"{symbol}: "
                f"{exc}"
            )

            print(
                "[ERROR]",
                error
            )

            traceback.print_exc()

            ERRORS.append(error)

            SCAN_RESULTS.append({
                "symbol":
                    symbol,

                "status":
                    "ERROR"
            })

    # --------------------------------------------------------
    # Update existing open trades
    # --------------------------------------------------------

    closed_trades = (
        update_open_trades(
            price_map
        )
    )

    # --------------------------------------------------------
    # Close notifications
    # --------------------------------------------------------

    for trade in closed_trades:

        emoji = (
            "✅"
            if trade["reason"] == "TP"
            else "❌"
        )

        message = (
            f"{emoji} TRADE CLOSED\n"
            f"\n"
            f"{trade['symbol']} "
            f"{trade['direction']}\n"
            f"Reason: "
            f"{trade['reason']}\n"
            f"Entry: "
            f"{trade['entry']}\n"
            f"Exit: "
            f"{trade['exit']}\n"
            f"PnL: "
            f"{trade['pnl']:+.2f}%\n"
            f"\n"
            f"📌 PAPER ONLY"
        )

        send_telegram(message)

    # --------------------------------------------------------
    # Periodic report
    # --------------------------------------------------------

    if REPORT_ENABLED:

        report = periodic_report(
            price_map
        )

        print(
            "\n"
            + report
        )

        send_telegram(report)

    print(
        "\n[SCAN COMPLETE]"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    init_db()

    ensure_columns()

    run_scan()


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
