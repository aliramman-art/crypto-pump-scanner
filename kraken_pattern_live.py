# ============================================================
# KRAKEN FUTURES 1H TRENDLINE BREAKOUT SCANNER
# VERSION 2.0.0
# ============================================================
#
# PURPOSE
# ------------------------------------------------------------
# ONLY detect NEW 1H trendline breakouts.
#
# LONG:
#   Descending resistance trendline
#   Minimum 3 confirmed touches
#   Previous closed 1H candle <= trendline
#   Latest closed 1H candle CLOSES ABOVE trendline
#
# SHORT:
#   Ascending support trendline
#   Minimum 3 confirmed touches
#   Previous closed 1H candle >= trendline
#   Latest closed 1H candle CLOSES BELOW trendline
#
# NO:
#   15M
#   5M
#   Retest
#   Confirmation
#   Entry
#   TP
#   SL
#   Trade management
#
# PAPER ONLY
# REAL_TRADING = False
#
# ============================================================


import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests


# ============================================================
# VERSION / MODE
# ============================================================

VERSION = "2.0.0"

REAL_TRADING = False
PAPER_TRADING = True


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "trendline_1h_breakout.db"


# ============================================================
# KRAKEN FUTURES CHART API
# ============================================================
#
# Current Kraken Futures Charts API:
#
# https://futures.kraken.com/api/charts/v1/{tick_type}/{symbol}/{resolution}
#
# tick_type:
#   trade
#
# resolution:
#   1h
#
# ============================================================

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1"
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# SCAN SETTINGS
# ============================================================

TIMEFRAME = "1h"

CANDLE_COUNT = 250

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_TOUCHES = 3

LOOKBACK_CANDLES = 200

MIN_LINE_LENGTH = 10

# Maximum pivot distance from the trendline.
# 0.002 = 0.20%
TOUCH_TOLERANCE = 0.002

# Minimum absolute slope:
# 0.01% per candle.
MIN_SLOPE_PERCENT_PER_CANDLE = 0.01


# ============================================================
# 40 KRAKEN FUTURES USD MARKETS
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
    "PF_SNXXUSD",
    "PF_CRVUSD",
    "PF_ENSUSD",
]


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": (
        "Mozilla/5.0 "
        "(compatible; Kraken-1H-Trendline-Scanner/2.0)"
    ),
    "Accept": "application/json",
})


# ============================================================
# GLOBAL SCAN RESULTS
# ============================================================

SCAN_RESULTS = []

NEW_SIGNALS = []

ERRORS = []


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now_timestamp():
    return int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )


def format_utc(timestamp_ms):
    if timestamp_ms > 10_000_000_000:
        timestamp = timestamp_ms / 1000
    else:
        timestamp = timestamp_ms

    dt = datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc
    )

    return dt.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# ============================================================
# DATABASE INIT
# ============================================================

def init_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            direction TEXT NOT NULL,

            candle_time INTEGER NOT NULL,

            candle_open REAL,

            candle_high REAL,

            candle_low REAL,

            candle_close REAL,

            trendline_type TEXT,

            trendline_start_time INTEGER,

            trendline_end_time INTEGER,

            trendline_start_price REAL,

            trendline_end_price REAL,

            trendline_value REAL,

            touches INTEGER,

            created_at INTEGER NOT NULL,

            UNIQUE(
                symbol,
                direction,
                candle_time
            )
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# INSERT NEW SIGNAL
# ============================================================

def insert_signal(signal):

    conn = sqlite3.connect(
        DB_FILE
    )

    cursor = conn.execute("""
        INSERT OR IGNORE INTO signals (

            symbol,
            direction,
            candle_time,

            candle_open,
            candle_high,
            candle_low,
            candle_close,

            trendline_type,

            trendline_start_time,
            trendline_end_time,

            trendline_start_price,
            trendline_end_price,

            trendline_value,

            touches,

            created_at

        )
        VALUES (
            ?, ?, ?,
            ?, ?, ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?,
            ?,
            ?
        )
    """, (

        signal["symbol"],
        signal["direction"],
        signal["candle_time"],

        signal["open"],
        signal["high"],
        signal["low"],
        signal["close"],

        signal["trendline_type"],

        signal["trendline_start_time"],
        signal["trendline_end_time"],

        signal["trendline_start_price"],
        signal["trendline_end_price"],

        signal["trendline_value"],

        signal["touches"],

        utc_now_timestamp()
    ))

    inserted = (
        cursor.rowcount == 1
    )

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# FETCH KRAKEN 1H CANDLES
# ============================================================

def fetch_ohlc(symbol):

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"trade/"
        f"{symbol}/"
        f"{TIMEFRAME}"
    )

    params = {
        "count": CANDLE_COUNT
    }

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=25
        )

        response.raise_for_status()

        data = response.json()

    except Exception as exc:

        error = (
            f"{symbol}: "
            f"OHLC request failed: "
            f"{exc}"
        )

        print(
            "[ERROR] "
            + error
        )

        ERRORS.append(
            error
        )

        return []

    if not isinstance(
        data,
        dict
    ):
        error = (
            f"{symbol}: "
            f"Invalid API response."
        )

        print(
            "[ERROR] "
            + error
        )

        ERRORS.append(
            error
        )

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
            f"No candles in API response."
        )

        print(
            "[ERROR] "
            + error
        )

        ERRORS.append(
            error
        )

        return []

    candles = []

    for row in raw:

        try:

            if not isinstance(
                row,
                dict
            ):
                continue

            timestamp = row.get(
                "time"
            )

            open_price = row.get(
                "open"
            )

            high_price = row.get(
                "high"
            )

            low_price = row.get(
                "low"
            )

            close_price = row.get(
                "close"
            )

            if (
                timestamp is None
                or open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                continue

            timestamp = int(
                float(timestamp)
            )

            # Kraken returns milliseconds.
            if timestamp < 10_000_000_000:
                timestamp *= 1000

            candles.append({
                "time": timestamp,

                "open": float(
                    open_price
                ),

                "high": float(
                    high_price
                ),

                "low": float(
                    low_price
                ),

                "close": float(
                    close_price
                )
            })

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    # Remove duplicates.
    unique = {}

    for candle in candles:
        unique[
            candle["time"]
        ] = candle

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles[-CANDLE_COUNT:]


# ============================================================
# REMOVE CURRENT 1H CANDLE
# ============================================================

def get_closed_candles(
    candles
):

    if not candles:
        return []

    now_ms = int(
        time.time() * 1000
    )

    ONE_HOUR_MS = 60 * 60 * 1000

    current_hour_start = (
        now_ms // ONE_HOUR_MS
    ) * ONE_HOUR_MS

    closed = [
        candle
        for candle in candles
        if candle["time"]
        < current_hour_start
    ]

    return closed


# ============================================================
# PIVOT HIGH
# ============================================================

def is_pivot_high(
    candles,
    index
):

    if index < PIVOT_LEFT:
        return False

    if (
        index + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    price = candles[index][
        "high"
    ]

    for i in range(
        index - PIVOT_LEFT,
        index
    ):

        if candles[i]["high"] >= price:
            return False

    for i in range(
        index + 1,
        index + PIVOT_RIGHT + 1
    ):

        if candles[i]["high"] > price:
            return False

    return True


# ============================================================
# PIVOT LOW
# ============================================================

def is_pivot_low(
    candles,
    index
):

    if index < PIVOT_LEFT:
        return False

    if (
        index + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    price = candles[index][
        "low"
    ]

    for i in range(
        index - PIVOT_LEFT,
        index
    ):

        if candles[i]["low"] <= price:
            return False

    for i in range(
        index + 1,
        index + PIVOT_RIGHT + 1
    ):

        if candles[i]["low"] < price:
            return False

    return True


# ============================================================
# FIND PIVOTS
# ============================================================

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
                "index": i,
                "time": candles[i][
                    "time"
                ],
                "price": candles[i][
                    "high"
                ]
            })

        if is_pivot_low(
            candles,
            i
        ):

            lows.append({
                "index": i,
                "time": candles[i][
                    "time"
                ],
                "price": candles[i][
                    "low"
                ]
            })

    return highs, lows


# ============================================================
# LINE VALUE
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


# ============================================================
# SLOPE %
# ============================================================

def slope_percent(
    x1,
    y1,
    x2,
    y2
):

    if x1 == x2:
        return 0.0

    if y1 == 0:
        return 0.0

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return (
        slope / y1
    ) * 100.0


# ============================================================
# DESCENDING RESISTANCE
# ============================================================

def find_descending_resistance(
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

            # Must be descending.
            if (
                slope
                >= -MIN_SLOPE_PERCENT_PER_CANDLE
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

                expected = line_value(
                    x,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if expected is None:
                    continue

                deviation = (
                    abs(
                        pivot["price"]
                        - expected
                    )
                    / expected
                )

                if (
                    deviation
                    <= TOUCH_TOLERANCE
                ):
                    touches.append(
                        pivot
                    )

            if len(touches) < MIN_TOUCHES:
                continue

            # No previous confirmed close
            # may have broken this resistance.
            for i in range(
                x1,
                x2 + 1
            ):

                expected = line_value(
                    i,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if expected is None:
                    continue

                if (
                    candles[i]["close"]
                    >
                    expected
                    * (
                        1
                        + TOUCH_TOLERANCE
                    )
                ):
                    valid = False
                    break

            if not valid:
                continue

            score = (
                len(touches) * 10000
                + x2
            )

            candidate = {
                "type":
                    "DESCENDING_RESISTANCE",

                "start": p1,

                "end": p2,

                "touches":
                    len(touches),

                "score":
                    score
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

def find_ascending_support(
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

            # Must be ascending.
            if (
                slope
                <= MIN_SLOPE_PERCENT_PER_CANDLE
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

                expected = line_value(
                    x,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if expected is None:
                    continue

                deviation = (
                    abs(
                        pivot["price"]
                        - expected
                    )
                    / expected
                )

                if (
                    deviation
                    <= TOUCH_TOLERANCE
                ):
                    touches.append(
                        pivot
                    )

            if len(touches) < MIN_TOUCHES:
                continue

            # No previous confirmed close
            # may have broken support.
            for i in range(
                x1,
                x2 + 1
            ):

                expected = line_value(
                    i,
                    x1,
                    y1,
                    x2,
                    y2
                )

                if expected is None:
                    continue

                if (
                    candles[i]["close"]
                    <
                    expected
                    * (
                        1
                        - TOUCH_TOLERANCE
                    )
                ):
                    valid = False
                    break

            if not valid:
                continue

            score = (
                len(touches) * 10000
                + x2
            )

            candidate = {
                "type":
                    "ASCENDING_SUPPORT",

                "start": p1,

                "end": p2,

                "touches":
                    len(touches),

                "score":
                    score
            }

            if (
                best is None
                or candidate["score"]
                > best["score"]
            ):
                best = candidate

    return best


# ============================================================
# CHECK LONG BREAKOUT
# ============================================================

def check_long_breakout(
    candles,
    resistance
):

    if resistance is None:
        return None

    if len(candles) < 2:
        return None

    latest_index = (
        len(candles) - 1
    )

    previous_index = (
        len(candles) - 2
    )

    latest = candles[
        latest_index
    ]

    previous = candles[
        previous_index
    ]

    start = resistance[
        "start"
    ]

    end = resistance[
        "end"
    ]

    previous_line = line_value(
        previous_index,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    latest_line = line_value(
        latest_index,
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

    previous_ok = (
        previous["close"]
        <=
        previous_line
        * (
            1
            + TOUCH_TOLERANCE
        )
    )

    latest_break = (
        latest["close"]
        >
        latest_line
        * (
            1
            + TOUCH_TOLERANCE
        )
    )

    if not (
        previous_ok
        and latest_break
    ):
        return None

    return {
        "direction":
            "LONG",

        "candle_time":
            latest["time"],

        "open":
            latest["open"],

        "high":
            latest["high"],

        "low":
            latest["low"],

        "close":
            latest["close"],

        "trendline_type":
            resistance["type"],

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
            resistance["touches"]
    }


# ============================================================
# CHECK SHORT BREAKDOWN
# ============================================================

def check_short_breakout(
    candles,
    support
):

    if support is None:
        return None

    if len(candles) < 2:
        return None

    latest_index = (
        len(candles) - 1
    )

    previous_index = (
        len(candles) - 2
    )

    latest = candles[
        latest_index
    ]

    previous = candles[
        previous_index
    ]

    start = support[
        "start"
    ]

    end = support[
        "end"
    ]

    previous_line = line_value(
        previous_index,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    latest_line = line_value(
        latest_index,
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

    previous_ok = (
        previous["close"]
        >=
        previous_line
        * (
            1
            - TOUCH_TOLERANCE
        )
    )

    latest_break = (
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
        and latest_break
    ):
        return None

    return {
        "direction":
            "SHORT",

        "candle_time":
            latest["time"],

        "open":
            latest["open"],

        "high":
            latest["high"],

        "low":
            latest["low"],

        "close":
            latest["close"],

        "trendline_type":
            support["type"],

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
            support["touches"]
    }


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "[INFO] "
            "TELEGRAM_BOT_TOKEN "
            "is not configured."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[INFO] "
            "TELEGRAM_CHAT_ID "
            "is not configured."
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    payload = {
        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "disable_web_page_preview":
            True
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        return True

    except Exception as exc:

        print(
            "[ERROR] Telegram send failed: "
            f"{exc}"
        )

        return False


# ============================================================
# FORMAT NEW SIGNAL
# ============================================================

def format_signal(
    signal
):

    if signal["direction"] == "LONG":

        emoji = "🟢"

        title = (
            "1H TRENDLINE BREAKOUT"
        )

    else:

        emoji = "🔴"

        title = (
            "1H TRENDLINE BREAKDOWN"
        )

    return (
        f"{emoji} {title}\n"
        f"\n"
        f"Symbol: {signal['symbol']}\n"
        f"Direction: "
        f"{signal['direction']}\n"
        f"Timeframe: 1H\n"
        f"\n"
        f"Break Close: "
        f"{signal['close']}\n"
        f"Trendline: "
        f"{signal['trendline_value']}\n"
        f"Touches: "
        f"{signal['touches']}\n"
        f"\n"
        f"Candle: "
        f"{format_utc(signal['candle_time'])}\n"
        f"\n"
        f"⚠️ NEW SIGNAL\n"
        f"1H trendline break only."
    )


# ============================================================
# FORMAT SUMMARY
# ============================================================

def format_summary():

    scanned = len(
        SCAN_RESULTS
    )

    successful = sum(
        1
        for x in SCAN_RESULTS
        if x["status"] == "OK"
    )

    failed = sum(
        1
        for x in SCAN_RESULTS
        if x["status"] != "OK"
    )

    longs = sum(
        1
        for x in NEW_SIGNALS
        if x["direction"] == "LONG"
    )

    shorts = sum(
        1
        for x in NEW_SIGNALS
        if x["direction"] == "SHORT"
    )

    lines = []

    lines.append(
        "📊 1H TRENDLINE SCANNER"
    )

    lines.append("")

    lines.append(
        f"Version: {VERSION}"
    )

    lines.append(
        f"Assets: {scanned}/40"
    )

    lines.append(
        f"Data OK: {successful}"
    )

    lines.append(
        f"Errors: {failed}"
    )

    lines.append("")

    lines.append(
        f"🟢 New LONG: {longs}"
    )

    lines.append(
        f"🔴 New SHORT: {shorts}"
    )

    lines.append("")

    if not NEW_SIGNALS:

        lines.append(
            "No new 1H trendline breakouts."
        )

    else:

        lines.append(
            "NEW SIGNALS:"
        )

        lines.append("")

        for signal in NEW_SIGNALS:

            if signal["direction"] == "LONG":
                emoji = "🟢"
            else:
                emoji = "🔴"

            lines.append(
                f"{emoji} "
                f"{signal['symbol']} "
                f"{signal['direction']}"
            )

            lines.append(
                f"Close: "
                f"{signal['close']}"
            )

            lines.append(
                f"Line: "
                f"{signal['trendline_value']}"
            )

            lines.append(
                f"Touches: "
                f"{signal['touches']}"
            )

            lines.append(
                f"Time: "
                f"{format_utc(signal['candle_time'])}"
            )

            lines.append("")

    if ERRORS:

        lines.append(
            "⚠️ DATA ERRORS:"
        )

        # Don't flood Telegram with 40 errors.
        for error in ERRORS[:8]:

            lines.append(
                "• "
                + error[:180]
            )

        if len(ERRORS) > 8:

            lines.append(
                f"• ... "
                f"{len(ERRORS) - 8} more"
            )

    lines.append("")

    lines.append(
        "Mode: PAPER ONLY"
    )

    return "\n".join(
        lines
    )


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol
):

    print(
        "\n"
        f"[SCAN] {symbol}"
    )

    candles = fetch_ohlc(
        symbol
    )

    if len(candles) < 40:

        error = (
            f"{symbol}: "
            f"not enough candles "
            f"({len(candles)})"
        )

        print(
            "[SKIP] "
            + error
        )

        SCAN_RESULTS.append({
            "symbol":
                symbol,

            "status":
                "ERROR"
        })

        if not any(
            error in x
            for x in ERRORS
        ):
            ERRORS.append(
                error
            )

        return

    closed = get_closed_candles(
        candles
    )

    if len(closed) < 40:

        error = (
            f"{symbol}: "
            f"not enough CLOSED "
            f"candles ({len(closed)})"
        )

        print(
            "[SKIP] "
            + error
        )

        SCAN_RESULTS.append({
            "symbol":
                symbol,

            "status":
                "ERROR"
        })

        ERRORS.append(
            error
        )

        return

    # --------------------------------------------------------
    # The latest CLOSED candle is the possible breakout
    # candle.
    #
    # It must NOT be used to create the trendline pivots.
    # --------------------------------------------------------

    analysis_candles = closed[:-1]

    highs, lows = get_pivots(
        analysis_candles
    )

    print(
        f"[INFO] {symbol} "
        f"pivot_highs={len(highs)} "
        f"pivot_lows={len(lows)}"
    )

    resistance = (
        find_descending_resistance(
            analysis_candles,
            highs
        )
    )

    support = (
        find_ascending_support(
            analysis_candles,
            lows
        )
    )

    symbol_signal = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    long_signal = (
        check_long_breakout(
            closed,
            resistance
        )
    )

    if long_signal:

        long_signal[
            "symbol"
        ] = symbol

        inserted = insert_signal(
            long_signal
        )

        if inserted:

            print(
                f"[NEW LONG] "
                f"{symbol}"
            )

            NEW_SIGNALS.append(
                long_signal
            )

            symbol_signal = (
                long_signal
            )

        else:

            print(
                f"[DUPLICATE] "
                f"{symbol} LONG"
            )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    short_signal = (
        check_short_breakout(
            closed,
            support
        )
    )

    if short_signal:

        short_signal[
            "symbol"
        ] = symbol

        inserted = insert_signal(
            short_signal
        )

        if inserted:

            print(
                f"[NEW SHORT] "
                f"{symbol}"
            )

            NEW_SIGNALS.append(
                short_signal
            )

            symbol_signal = (
                short_signal
            )

        else:

            print(
                f"[DUPLICATE] "
                f"{symbol} SHORT"
            )

    # --------------------------------------------------------
    # PRINT TRENDLINES
    # --------------------------------------------------------

    if resistance:

        print(
            f"[LINE] {symbol} "
            f"DESCENDING "
            f"touches="
            f"{resistance['touches']} "
            f"start="
            f"{resistance['start']['price']} "
            f"end="
            f"{resistance['end']['price']}"
        )

    if support:

        print(
            f"[LINE] {symbol} "
            f"ASCENDING "
            f"touches="
            f"{support['touches']} "
            f"start="
            f"{support['start']['price']} "
            f"end="
            f"{support['end']['price']}"
        )

    SCAN_RESULTS.append({
        "symbol":
            symbol,

        "status":
            "OK"
    })


# ============================================================
# RUN SCAN
# ============================================================

def run_scan():

    global SCAN_RESULTS
    global NEW_SIGNALS
    global ERRORS

    SCAN_RESULTS = []
    NEW_SIGNALS = []
    ERRORS = []

    print(
        "\n"
        "============================================================"
    )

    print(
        f"KRAKEN FUTURES "
        f"1H TRENDLINE BREAKOUT SCANNER "
        f"v{VERSION}"
    )

    print(
        f"UTC: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
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
        "============================================================"
    )

    for symbol in SYMBOLS:

        try:

            analyze_symbol(
                symbol
            )

        except Exception as exc:

            error = (
                f"{symbol}: "
                f"unexpected error: "
                f"{exc}"
            )

            print(
                "[ERROR] "
                + error
            )

            traceback.print_exc()

            ERRORS.append(
                error
            )

            SCAN_RESULTS.append({
                "symbol":
                    symbol,

                "status":
                    "ERROR"
            })

    # --------------------------------------------------------
    # TELEGRAM INDIVIDUAL NEW SIGNALS
    # --------------------------------------------------------

    for signal in NEW_SIGNALS:

        message = format_signal(
            signal
        )

        send_telegram(
            message
        )

    # --------------------------------------------------------
    # TELEGRAM PERIODIC SCAN REPORT
    # --------------------------------------------------------

    summary = format_summary()

    print(
        "\n"
        + summary
    )

    send_telegram(
        summary
    )


# ============================================================
# MAIN
# ============================================================

def main():

    init_db()

    run_scan()


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()
