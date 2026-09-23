# ============================================================
# KRAKEN FUTURES 1H TRENDLINE BREAKOUT SIGNAL SCANNER
# VERSION 1.0.0
# ============================================================
#
# PURPOSE:
#   ONLY detect NEW 1H trendline breakouts.
#
# LONG:
#   1H descending resistance trendline
#   >= 3 confirmed touch points
#   previous closed candle <= trendline
#   latest closed candle CLOSES ABOVE trendline
#
# SHORT:
#   1H ascending support trendline
#   >= 3 confirmed touch points
#   previous closed candle >= trendline
#   latest closed candle CLOSES BELOW trendline
#
# NO:
#   15M
#   5M
#   retest
#   confirmation
#   entry
#   TP
#   SL
#   trade management
#
# PAPER ONLY
# NO EXCHANGE ORDERS
#
# Telegram:
#   Set these GitHub Secrets:
#
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

VERSION = "1.0.0"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "trendline_1h_breakout.db"

KRAKEN_OHLC_URL = (
    "https://futures.kraken.com/derivatives/api/v3/ohlc"
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

# Scan interval.
# GitHub Actions can run the script periodically.
SCAN_INTERVAL_SECONDS = 300

# Number of 1H candles requested.
CANDLE_LIMIT = 240

# Pivot configuration.
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# Minimum number of trendline touches.
MIN_TOUCHES = 3

# Maximum distance of a pivot from trendline.
# 0.002 = 0.20%
TOUCH_TOLERANCE = 0.002

# Minimum slope magnitude.
# Prevents almost-horizontal lines from being treated
# as descending/ascending trendlines.
MIN_SLOPE_PERCENT_PER_CANDLE = 0.01

# Search only this many latest candles for candidate lines.
LOOKBACK_CANDLES = 180

# Maximum number of candles between first and last
# touch used for a trendline.
MIN_LINE_LENGTH = 10

# Assets.
SYMBOLS = [
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_LTCUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_LINKUSD",
    "PF_AVAXUSD",
    "PF_DOTUSD",
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
]


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "1H-Trendline-Breakout-Scanner/1.0"
})


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

            candle_open REAL,
            candle_high REAL,
            candle_low REAL,
            candle_close REAL,

            trendline_type TEXT,
            trendline_start_time INTEGER,
            trendline_end_time INTEGER,

            trendline_start_price REAL,
            trendline_end_price REAL,

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
# DATABASE - INSERT ONLY NEW SIGNAL
# ============================================================

def insert_signal(signal):
    conn = sqlite3.connect(DB_FILE)

    cur = conn.execute("""
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

            touches,

            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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

        signal["touches"],

        int(time.time())
    ))

    inserted = cur.rowcount == 1

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# TIME
# ============================================================

def utc_datetime(timestamp):
    return datetime.fromtimestamp(
        timestamp,
        tz=timezone.utc
    )


def format_time(timestamp):
    dt = utc_datetime(timestamp)

    return dt.strftime(
        "%Y-%m-%d %H:%M UTC"
    )


# ============================================================
# KRAKEN OHLC
# ============================================================

def fetch_ohlc(symbol):
    params = {
        "symbol": symbol,
        "interval": 60
    }

    try:
        response = SESSION.get(
            KRAKEN_OHLC_URL,
            params=params,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:
        print(
            f"[ERROR] OHLC request failed "
            f"{symbol}: {e}"
        )

        return []

    if not isinstance(data, dict):
        return []

    # Kraken Futures responses can vary slightly.
    raw = data.get("candles")

    if raw is None:
        raw = data.get("result")

    if raw is None:
        raw = data.get("data")

    if raw is None:
        print(
            f"[ERROR] No candle data for {symbol}"
        )
        return []

    # Some APIs return:
    # {"candles": [...]}
    #
    # Some wrappers may return:
    # {"result": {"candles": [...]}}
    if isinstance(raw, dict):
        raw = raw.get("candles")

    if not isinstance(raw, list):
        return []

    candles = []

    for row in raw:

        try:
            # Common Kraken Futures OHLC format:
            #
            # [
            #   time,
            #   open,
            #   high,
            #   low,
            #   close,
            #   ...
            # ]

            if isinstance(row, dict):

                timestamp = (
                    row.get("time")
                    or row.get("timestamp")
                )

                open_price = (
                    row.get("open")
                    or row.get("o")
                )

                high_price = (
                    row.get("high")
                    or row.get("h")
                )

                low_price = (
                    row.get("low")
                    or row.get("l")
                )

                close_price = (
                    row.get("close")
                    or row.get("c")
                )

            else:

                if len(row) < 5:
                    continue

                timestamp = row[0]
                open_price = row[1]
                high_price = row[2]
                low_price = row[3]
                close_price = row[4]

            timestamp = int(
                float(timestamp)
            )

            # If timestamp is milliseconds.
            if timestamp > 10_000_000_000:
                timestamp //= 1000

            candle = {
                "time": timestamp,
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price)
            }

            candles.append(candle)

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles[-CANDLE_LIMIT:]


# ============================================================
# REMOVE CURRENT OPEN CANDLE
# ============================================================

def get_closed_candles(candles):
    if not candles:
        return []

    now = int(time.time())

    # 1H candle beginning timestamp.
    current_hour = (
        now // 3600
    ) * 3600

    closed = [
        c for c in candles
        if c["time"] < current_hour
    ]

    return closed


# ============================================================
# PIVOT HIGH
# ============================================================

def is_pivot_high(
    candles,
    index
):
    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if index < left:
        return False

    if index + right >= len(candles):
        return False

    price = candles[index]["high"]

    for i in range(
        index - left,
        index
    ):
        if candles[i]["high"] >= price:
            return False

    for i in range(
        index + 1,
        index + right + 1
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
    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if index < left:
        return False

    if index + right >= len(candles):
        return False

    price = candles[index]["low"]

    for i in range(
        index - left,
        index
    ):
        if candles[i]["low"] <= price:
            return False

    for i in range(
        index + 1,
        index + right + 1
    ):
        if candles[i]["low"] < price:
            return False

    return True


# ============================================================
# GET PIVOTS
# ============================================================

def get_pivots(candles):
    highs = []
    lows = []

    start = max(
        PIVOT_LEFT,
        len(candles) - LOOKBACK_CANDLES
    )

    end = len(candles) - PIVOT_RIGHT

    for i in range(start, end):

        if is_pivot_high(
            candles,
            i
        ):
            highs.append({
                "index": i,
                "time": candles[i]["time"],
                "price": candles[i]["high"]
            })

        if is_pivot_low(
            candles,
            i
        ):
            lows.append({
                "index": i,
                "time": candles[i]["time"],
                "price": candles[i]["low"]
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
    if x2 == x1:
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
# LINE SLOPE %
# ============================================================

def slope_percent_per_candle(
    x1,
    y1,
    x2,
    y2
):
    if x2 == x1:
        return 0.0

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    if y1 == 0:
        return 0.0

    return (
        slope / y1
    ) * 100.0


# ============================================================
# FIND BEST DESCENDING RESISTANCE
# ============================================================

def find_descending_resistance(
    candles,
    pivots
):
    if len(pivots) < MIN_TOUCHES:
        return None

    best = None

    # Candidate lines from pairs of pivot highs.
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

            if x2 - x1 < MIN_LINE_LENGTH:
                continue

            y1 = p1["price"]
            y2 = p2["price"]

            slope_pct = slope_percent_per_candle(
                x1,
                y1,
                x2,
                y2
            )

            # Must be descending.
            if slope_pct >= -MIN_SLOPE_PERCENT_PER_CANDLE:
                continue

            touches = []

            valid = True

            for p in pivots:

                x = p["index"]

                if x < x1 or x > x2:
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

                deviation = abs(
                    p["price"] - expected
                ) / expected

                if deviation <= TOUCH_TOLERANCE:
                    touches.append(p)

            if len(touches) < MIN_TOUCHES:
                continue

            # Resistance line should not have been
            # decisively broken by previous candles.
            #
            # We check candles from x1 to x2.
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

                # A previous close above the line invalidates
                # the old resistance.
                if candles[i]["close"] > (
                    expected * (
                        1 + TOUCH_TOLERANCE
                    )
                ):
                    valid = False
                    break

            if not valid:
                continue

            score = (
                len(touches) * 1000
                + x2
            )

            candidate = {
                "type": "DESCENDING_RESISTANCE",
                "start": p1,
                "end": p2,
                "touches": len(touches),
                "score": score
            }

            if (
                best is None
                or candidate["score"] > best["score"]
            ):
                best = candidate

    return best


# ============================================================
# FIND BEST ASCENDING SUPPORT
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

            if x2 - x1 < MIN_LINE_LENGTH:
                continue

            y1 = p1["price"]
            y2 = p2["price"]

            slope_pct = slope_percent_per_candle(
                x1,
                y1,
                x2,
                y2
            )

            # Must be ascending.
            if slope_pct <= MIN_SLOPE_PERCENT_PER_CANDLE:
                continue

            touches = []

            valid = True

            for p in pivots:

                x = p["index"]

                if x < x1 or x > x2:
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

                deviation = abs(
                    p["price"] - expected
                ) / expected

                if deviation <= TOUCH_TOLERANCE:
                    touches.append(p)

            if len(touches) < MIN_TOUCHES:
                continue

            # Previous candle closes below support
            # => old support already broken.
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

                if candles[i]["close"] < (
                    expected * (
                        1 - TOUCH_TOLERANCE
                    )
                ):
                    valid = False
                    break

            if not valid:
                continue

            score = (
                len(touches) * 1000
                + x2
            )

            candidate = {
                "type": "ASCENDING_SUPPORT",
                "start": p1,
                "end": p2,
                "touches": len(touches),
                "score": score
            }

            if (
                best is None
                or candidate["score"] > best["score"]
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

    latest_index = len(candles) - 1
    previous_index = len(candles) - 2

    start = resistance["start"]
    end = resistance["end"]

    latest = candles[latest_index]
    previous = candles[previous_index]

    latest_line = line_value(
        latest_index,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    previous_line = line_value(
        previous_index,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    if (
        latest_line is None
        or previous_line is None
    ):
        return None

    # Fresh breakout:
    #
    # Previous closed candle was at/below line.
    # Latest closed candle closes above line.

    previous_ok = (
        previous["close"]
        <= previous_line * (
            1 + TOUCH_TOLERANCE
        )
    )

    latest_break = (
        latest["close"]
        > latest_line * (
            1 + TOUCH_TOLERANCE
        )
    )

    if not (
        previous_ok
        and latest_break
    ):
        return None

    return {
        "direction": "LONG",

        "candle_time": latest["time"],

        "open": latest["open"],
        "high": latest["high"],
        "low": latest["low"],
        "close": latest["close"],

        "trendline_type": (
            resistance["type"]
        ),

        "trendline_start_time": (
            start["time"]
        ),

        "trendline_end_time": (
            end["time"]
        ),

        "trendline_start_price": (
            start["price"]
        ),

        "trendline_end_price": (
            end["price"]
        ),

        "trendline_value": latest_line,

        "touches": resistance["touches"]
    }


# ============================================================
# CHECK SHORT BREAKOUT
# ============================================================

def check_short_breakout(
    candles,
    support
):
    if support is None:
        return None

    if len(candles) < 2:
        return None

    latest_index = len(candles) - 1
    previous_index = len(candles) - 2

    start = support["start"]
    end = support["end"]

    latest = candles[latest_index]
    previous = candles[previous_index]

    latest_line = line_value(
        latest_index,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    previous_line = line_value(
        previous_index,
        start["index"],
        start["price"],
        end["index"],
        end["price"]
    )

    if (
        latest_line is None
        or previous_line is None
    ):
        return None

    # Fresh breakdown:
    #
    # Previous closed candle was at/above line.
    # Latest closed candle closes below line.

    previous_ok = (
        previous["close"]
        >= previous_line * (
            1 - TOUCH_TOLERANCE
        )
    )

    latest_break = (
        latest["close"]
        < latest_line * (
            1 - TOUCH_TOLERANCE
        )
    )

    if not (
        previous_ok
        and latest_break
    ):
        return None

    return {
        "direction": "SHORT",

        "candle_time": latest["time"],

        "open": latest["open"],
        "high": latest["high"],
        "low": latest["low"],
        "close": latest["close"],

        "trendline_type": (
            support["type"]
        ),

        "trendline_start_time": (
            start["time"]
        ),

        "trendline_end_time": (
            end["time"]
        ),

        "trendline_start_price": (
            start["price"]
        ),

        "trendline_end_price": (
            end["price"]
        ),

        "trendline_value": latest_line,

        "touches": support["touches"]
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print(
            "[INFO] TELEGRAM_BOT_TOKEN "
            "not configured."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[INFO] TELEGRAM_CHAT_ID "
            "not configured."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message
    }

    try:
        response = SESSION.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(
            f"[ERROR] Telegram failed: {e}"
        )

        return False


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    symbol,
    signal
):
    if signal["direction"] == "LONG":
        emoji = "🟢"
        title = "1H TRENDLINE BREAKOUT"
        direction = "LONG"
    else:
        emoji = "🔴"
        title = "1H TRENDLINE BREAKDOWN"
        direction = "SHORT"

    candle_time = format_time(
        signal["candle_time"]
    )

    message = (
        f"{emoji} {title}\n"
        f"\n"
        f"Symbol: {symbol}\n"
        f"Direction: {direction}\n"
        f"Timeframe: 1H\n"
        f"\n"
        f"Break Candle Close: "
        f"{signal['close']}\n"
        f"Trendline Price: "
        f"{signal['trendline_value']}\n"
        f"\n"
        f"Touches: "
        f"{signal['touches']}\n"
        f"Trendline: "
        f"{signal['trendline_type']}\n"
        f"\n"
        f"Candle: {candle_time}\n"
        f"\n"
        f"⚠️ NEW SIGNAL\n"
        f"1H trendline break only."
    )

    return message


# ============================================================
# ANALYZE ONE SYMBOL
# ============================================================

def analyze_symbol(symbol):

    print(
        f"\n[SCAN] {symbol}"
    )

    candles = fetch_ohlc(
        symbol
    )

    if len(candles) < 40:
        print(
            f"[SKIP] {symbol} "
            f"not enough candles: "
            f"{len(candles)}"
        )
        return

    candles = get_closed_candles(
        candles
    )

    if len(candles) < 40:
        print(
            f"[SKIP] {symbol} "
            f"not enough CLOSED candles."
        )
        return

    # --------------------------------------------------------
    # IMPORTANT
    #
    # The latest candle is the latest CLOSED 1H candle.
    #
    # Pivots are calculated from candles BEFORE that candle.
    # This prevents the current breakout candle from becoming
    # a pivot and contaminating the trendline.
    # --------------------------------------------------------

    analysis_candles = candles[:-1]

    highs, lows = get_pivots(
        analysis_candles
    )

    print(
        f"[INFO] {symbol} "
        f"pivot highs={len(highs)} "
        f"pivot lows={len(lows)}"
    )

    resistance = find_descending_resistance(
        analysis_candles,
        highs
    )

    support = find_ascending_support(
        analysis_candles,
        lows
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    long_signal = check_long_breakout(
        candles,
        resistance
    )

    if long_signal:

        long_signal["symbol"] = symbol

        inserted = insert_signal(
            long_signal
        )

        if inserted:

            message = format_signal(
                symbol,
                long_signal
            )

            print(
                "\n"
                + message
            )

            send_telegram(
                message
            )

        else:

            print(
                f"[DUPLICATE] "
                f"{symbol} LONG "
                f"{long_signal['candle_time']}"
            )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    short_signal = check_short_breakout(
        candles,
        support
    )

    if short_signal:

        short_signal["symbol"] = symbol

        inserted = insert_signal(
            short_signal
        )

        if inserted:

            message = format_signal(
                symbol,
                short_signal
            )

            print(
                "\n"
                + message
            )

            send_telegram(
                message
            )

        else:

            print(
                f"[DUPLICATE] "
                f"{symbol} SHORT "
                f"{short_signal['candle_time']}"
            )

    # --------------------------------------------------------
    # DEBUG
    # --------------------------------------------------------

    if resistance:

        print(
            f"[TRENDLINE] {symbol} "
            f"DESCENDING RESISTANCE "
            f"touches={resistance['touches']} "
            f"start={resistance['start']['price']} "
            f"end={resistance['end']['price']}"
        )

    if support:

        print(
            f"[TRENDLINE] {symbol} "
            f"ASCENDING SUPPORT "
            f"touches={support['touches']} "
            f"start={support['start']['price']} "
            f"end={support['end']['price']}"
        )


# ============================================================
# RUN ONE SCAN
# ============================================================

def run_scan():

    print(
        "\n"
        "===================================================="
    )

    print(
        f"KRAKEN 1H TRENDLINE BREAKOUT SCANNER "
        f"v{VERSION}"
    )

    print(
        f"Time: "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    print(
        "REAL_TRADING = False"
    )

    print(
        "PAPER_TRADING = True"
    )

    print(
        "===================================================="
    )

    for symbol in SYMBOLS:

        try:

            analyze_symbol(
                symbol
            )

        except Exception as e:

            print(
                f"[ERROR] "
                f"{symbol}: {e}"
            )

            traceback.print_exc()

    print(
        "\n[SCAN COMPLETE]"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    init_db()

    # --------------------------------------------------------
    # SINGLE RUN
    #
    # GitHub Actions can call this script every few minutes.
    # --------------------------------------------------------

    run_scan()


if __name__ == "__main__":
    main()
