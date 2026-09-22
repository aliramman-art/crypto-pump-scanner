# ============================================================
# KRAKEN FUTURES TRENDLINE LIVE SIGNAL SCANNER
# VERSION 7.2.8
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
# PAPER_TRADING = True
#
# STRATEGY
#
# LONG:
#   1H descending resistance trendline
#       ↓
#   FRESH 1H BREAKOUT
#       ↓
#   15M descending resistance trendline
#       ↓
#   FRESH 15M BREAKOUT
#       ↓
#   15M RVOL CONFIRMATION
#       ↓
#   5M descending resistance trendline
#       ↓
#   FRESH 5M BREAKOUT
#       ↓
#   FIRST 5M RETEST
#       ↓
#   5M CONFIRMATION
#       ↓
#   SIGNAL
#
# SHORT:
#   1H ascending support trendline
#       ↓
#   FRESH 1H BREAKDOWN
#       ↓
#   15M ascending support trendline
#       ↓
#   FRESH 15M BREAKDOWN
#       ↓
#   15M RVOL CONFIRMATION
#       ↓
#   5M ascending support trendline
#       ↓
#   FRESH 5M BREAKDOWN
#       ↓
#   FIRST 5M RETEST
#       ↓
#   5M CONFIRMATION
#       ↓
#   SIGNAL
#
# IMPORTANT
# - CLOSED CANDLES ONLY
# - NO LOOKAHEAD
# - NO STALE HISTORICAL BREAKOUT
# - FINAL CONFIRMATION MUST BE LATEST CLOSED 5M CANDLE
# - EXISTING DB PRESERVED
# - PERFORMANCE IS CUMULATIVE
# - INVALID LEGACY DB ROWS MUST NOT CRASH SCANNER
# ============================================================

import os
import io
import json
import sqlite3
import traceback
import time

from datetime import (
    datetime,
    timezone,
    timedelta
)

import requests
import pandas as pd
import matplotlib.pyplot as plt


# ============================================================
# VERSION / MODE
# ============================================================

VERSION = "7.2.8"

REAL_TRADING = False
PAPER_TRADING = True

DB_FILE = "kraken_pattern_live_v52.db"


# ============================================================
# KRAKEN API
# ============================================================

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
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
# ASSETS
# ============================================================

ASSETS = [
    "XBT",
    "ETH",
    "SOL",
    "XRP",
    "LTC",
    "DOGE",
    "ADA",
    "LINK",
    "AVAX",
    "DOT",
    "BNB",
    "TRX",
    "UNI",
    "AAVE",
    "SUI",
    "NEAR",
    "ATOM",
    "FIL",
    "ARB",
    "OP",
    "BCH",
    "ETC",
    "XMR",
    "XLM",
    "ALGO",
    "ICP",
    "INJ",
    "TIA",
    "SEI",
    "RUNE",
    "CRV",
    "HBAR",
    "HYPE",
    "ENA",
    "FET",
    "KAS",
    "STX",
    "JUP",
    "PEPE",
    "WIF",
]


# ============================================================
# STRATEGY SETTINGS
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MIN_TRENDLINE_BARS = 8

TRENDLINE_TOLERANCE_PCT = 0.20

BREAKOUT_BUFFER_PCT = 0.05

MIN_TRENDLINE_TOUCHES = 3

RVOL_LOOKBACK = 20
RVOL_MIN = 1.20

MIN_RR = 1.01

SL_BUFFER_PCT = 0.05 / 100.0

MAX_HOLD_HOURS = 48

MAX_SIGNALS_PER_SCAN = 1

OHLC_COUNT = 300


# ============================================================
# FRESHNESS
# ============================================================

MAX_1H_BREAKOUT_AGE_BARS = 1

MAX_15M_BREAKOUT_AGE_BARS = 4

MAX_5M_BREAKOUT_AGE_BARS = 3

MAX_RETEST_WAIT_BARS = 6

MAX_CONFIRMATION_WAIT_BARS = 3

RETEST_TOLERANCE_PCT = 0.15

REQUIRE_LATEST_CONFIRMATION = True


# ============================================================
# REPORT
# ============================================================

PERIODIC_REPORT_SECONDS = 1800


# ============================================================
# TIMEZONE
# ============================================================

TEHRAN_TZ = timezone(
    timedelta(
        hours=3,
        minutes=30
    )
)


# ============================================================
# GLOBALS
# ============================================================

TICKER_CACHE = {}

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0"
})


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():
    return datetime.now(
        timezone.utc
    )


def now_tehran():
    return now_utc().astimezone(
        TEHRAN_TZ
    )


def format_time(value):

    if value is None:
        return "-"

    try:

        if isinstance(
            value,
            pd.Timestamp
        ):
            dt = value.to_pydatetime()

        elif isinstance(
            value,
            datetime
        ):
            dt = value

        else:
            dt = pd.to_datetime(
                value,
                utc=True
            ).to_pydatetime()

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        dt = dt.astimezone(
            TEHRAN_TZ
        )

        return dt.strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    except Exception:
        return str(value)


def iso_time(value):

    if value is None:
        return None

    try:

        if isinstance(
            value,
            pd.Timestamp
        ):
            value = value.to_pydatetime()

        if isinstance(
            value,
            datetime
        ):

            if value.tzinfo is None:
                value = value.replace(
                    tzinfo=timezone.utc
                )

            return value.astimezone(
                timezone.utc
            ).isoformat()

        return pd.to_datetime(
            value,
            utc=True
        ).isoformat()

    except Exception:
        return str(value)


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(
    value,
    default=None
):

    try:

        if value is None:
            return default

        x = float(value)

        if pd.isna(x):
            return default

        return x

    except Exception:
        return default


# ============================================================
# CONTRACT
# ============================================================

def contract_name(asset):

    return f"PF_{asset}USD"


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(asset):

    contract = contract_name(
        asset
    )

    ticker = TICKER_CACHE.get(
        contract
    )

    if ticker is not None:

        for key in (
            "last",
            "lastPrice",
            "markPrice",
            "indexPrice",
            "price",
        ):

            price = safe_float(
                ticker.get(key)
            )

            if price is not None:
                return price

    try:

        response = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=15
        )

        response.raise_for_status()

        data = response.json()

        rows = (
            data.get("tickers")
            or data.get("data")
            or []
        )

        for row in rows:

            symbol = (
                row.get("symbol")
                or row.get("instrument")
                or row.get("contractSymbol")
            )

            if symbol != contract:
                continue

            TICKER_CACHE[
                contract
            ] = row

            for key in (
                "last",
                "lastPrice",
                "markPrice",
                "indexPrice",
                "price",
            ):

                price = safe_float(
                    row.get(key)
                )

                if price is not None:
                    return price

    except Exception:
        pass

    return None


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle_row(row):

    try:

        if isinstance(
            row,
            dict
        ):

            ts = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
            )

            op = (
                row.get("open")
                or row.get("o")
            )

            hi = (
                row.get("high")
                or row.get("h")
            )

            lo = (
                row.get("low")
                or row.get("l")
            )

            cl = (
                row.get("close")
                or row.get("c")
            )

            vol = (
                row.get("volume")
                or row.get("v")
                or 0
            )

            if ts is None:
                return None

            if isinstance(
                ts,
                (int, float)
            ):

                if ts > 10_000_000_000:
                    ts = ts / 1000.0

                dt = datetime.fromtimestamp(
                    ts,
                    tz=timezone.utc
                )

            else:

                dt = pd.to_datetime(
                    ts,
                    utc=True
                )

            open_price = safe_float(
                op
            )

            high_price = safe_float(
                hi
            )

            low_price = safe_float(
                lo
            )

            close_price = safe_float(
                cl
            )

            volume = safe_float(
                vol,
                0
            )

            if (
                open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                return None

            return {
                "time": pd.Timestamp(dt),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }

        if isinstance(
            row,
            (list, tuple)
        ):

            if len(row) < 6:
                return None

            ts = row[0]

            if isinstance(
                ts,
                str
            ):

                dt = pd.to_datetime(
                    ts,
                    utc=True
                )

            else:

                ts = float(ts)

                if ts > 10_000_000_000:
                    ts = ts / 1000.0

                dt = datetime.fromtimestamp(
                    ts,
                    tz=timezone.utc
                )

            open_price = safe_float(
                row[1]
            )

            high_price = safe_float(
                row[2]
            )

            low_price = safe_float(
                row[3]
            )

            close_price = safe_float(
                row[4]
            )

            volume = safe_float(
                row[5],
                0
            )

            if (
                open_price is None
                or high_price is None
                or low_price is None
                or close_price is None
            ):
                return None

            return {
                "time": pd.Timestamp(dt),
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            }

    except Exception:
        return None

    return None


# ============================================================
# OHLC
# ============================================================

def get_ohlc(
    asset,
    interval
):

    contract = contract_name(
        asset
    )

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/"
        f"{interval}"
    )

    try:

        response = SESSION.get(
            url,
            params={
                "from": 0,
                "count": OHLC_COUNT,
            },
            timeout=20
        )

        response.raise_for_status()

        payload = response.json()

        rows = (
            payload.get("candles")
            or payload.get("data")
            or payload.get("result")
            or []
        )

        parsed = []

        for row in rows:

            candle = parse_candle_row(
                row
            )

            if candle is not None:
                parsed.append(
                    candle
                )

        if not parsed:
            return None

        df = pd.DataFrame(
            parsed
        )

        df["time"] = pd.to_datetime(
            df["time"],
            utc=True
        )

        df = (
            df
            .sort_values("time")
            .drop_duplicates(
                subset=["time"],
                keep="last"
            )
            .reset_index(drop=True)
        )

        # Last candle is current/open candle.
        # Only closed candles are used.
        if len(df) >= 2:
            df = df.iloc[:-1].copy()

        df = df.reset_index(
            drop=True
        )

        if len(df) < 50:
            return None

        return df

    except Exception as e:

        print(
            f"OHLC ERROR "
            f"{asset} {interval}: "
            f"{e}"
        )

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(df):

    result = []

    if (
        df is None
        or len(df)
        < (
            PIVOT_LEFT
            + PIVOT_RIGHT
            + 1
        )
    ):
        return result

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        try:

            value = safe_float(
                df.iloc[i]["high"]
            )

            if value is None:
                continue

            left = df.iloc[
                i - PIVOT_LEFT:i
            ]["high"]

            right = df.iloc[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ]["high"]

            left_max = safe_float(
                left.max()
            )

            right_max = safe_float(
                right.max()
            )

            if (
                left_max is None
                or right_max is None
            ):
                continue

            if (
                value > left_max
                and value >= right_max
            ):

                result.append({
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": value,
                })

        except Exception:
            continue

    return result


def find_pivot_lows(df):

    result = []

    if (
        df is None
        or len(df)
        < (
            PIVOT_LEFT
            + PIVOT_RIGHT
            + 1
        )
    ):
        return result

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        try:

            value = safe_float(
                df.iloc[i]["low"]
            )

            if value is None:
                continue

            left = df.iloc[
                i - PIVOT_LEFT:i
            ]["low"]

            right = df.iloc[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ]["low"]

            left_min = safe_float(
                left.min()
            )

            right_min = safe_float(
                right.min()
            )

            if (
                left_min is None
                or right_min is None
            ):
                continue

            if (
                value < left_min
                and value <= right_min
            ):

                result.append({
                    "index": i,
                    "time": df.iloc[i]["time"],
                    "price": value,
                })

        except Exception:
            continue

    return result


# ============================================================
# TRENDLINE MATH
# ============================================================

def line_value(
    p1,
    p2,
    index
):

    try:

        if (
            p1 is None
            or p2 is None
            or index is None
        ):
            return None

        x1 = safe_float(
            p1.get("index")
        )

        x2 = safe_float(
            p2.get("index")
        )

        y1 = safe_float(
            p1.get("price")
        )

        y2 = safe_float(
            p2.get("price")
        )

        x = safe_float(
            index
        )

        if (
            x1 is None
            or x2 is None
            or y1 is None
            or y2 is None
            or x is None
        ):
            return None

        if x1 == x2:
            return None

        slope = (
            y2 - y1
        ) / (
            x2 - x1
        )

        value = (
            y1
            + slope * (
                x - x1
            )
        )

        if value is None:
            return None

        if pd.isna(value):
            return None

        return float(value)

    except Exception:
        return None


def trendline_slope(
    p1,
    p2
):

    try:

        if (
            p1 is None
            or p2 is None
        ):
            return None

        x1 = safe_float(
            p1.get("index")
        )

        x2 = safe_float(
            p2.get("index")
        )

        y1 = safe_float(
            p1.get("price")
        )

        y2 = safe_float(
            p2.get("price")
        )

        if (
            x1 is None
            or x2 is None
            or y1 is None
            or y2 is None
        ):
            return None

        if x1 == x2:
            return None

        slope = (
            y2 - y1
        ) / (
            x2 - x1
        )

        if pd.isna(slope):
            return None

        return float(
            slope
        )

    except Exception:
        return None


def within_tolerance(
    price,
    line
):

    price = safe_float(
        price
    )

    line = safe_float(
        line
    )

    if (
        price is None
        or line is None
        or line == 0
    ):
        return False

    error = (
        abs(price - line)
        / abs(line)
        * 100.0
    )

    if pd.isna(error):
        return False

    return (
        error
        <= TRENDLINE_TOLERANCE_PCT
    )


# ============================================================
# VALID TRENDLINE
# ============================================================

def find_valid_trendline(
    df,
    direction,
    end_index=None,
    min_index=None
):

    if df is None:
        return None

    if len(df) < 50:
        return None

    if end_index is None:
        end_index = len(df) - 1

    try:
        end_index = int(
            end_index
        )
    except Exception:
        return None

    end_index = min(
        end_index,
        len(df) - 1
    )

    if min_index is None:
        min_index = 0

    try:
        min_index = max(
            0,
            int(min_index)
        )
    except Exception:
        min_index = 0

    if direction == "LONG":

        raw_pivots = find_pivot_highs(
            df
        )

    elif direction == "SHORT":

        raw_pivots = find_pivot_lows(
            df
        )

    else:
        return None

    if not raw_pivots:
        return None

    pivots = []

    for p in raw_pivots:

        if p is None:
            continue

        idx = p.get(
            "index"
        )

        price = safe_float(
            p.get("price")
        )

        if idx is None:
            continue

        if price is None:
            continue

        try:
            idx = int(idx)
        except Exception:
            continue

        if idx < min_index:
            continue

        if idx > end_index:
            continue

        pivots.append({
            "index": idx,
            "time": p.get("time"),
            "price": price,
        })

    if len(pivots) < MIN_TRENDLINE_TOUCHES:
        return None

    pivots = pivots[-35:]

    best = None

    for a in range(
        len(pivots)
    ):

        p1 = pivots[a]

        for b in range(
            a + 1,
            len(pivots)
        ):

            p2 = pivots[b]

            span = (
                p2["index"]
                - p1["index"]
            )

            if span < MIN_TRENDLINE_BARS:
                continue

            slope = trendline_slope(
                p1,
                p2
            )

            if slope is None:
                continue

            if direction == "LONG":

                if slope >= 0:
                    continue

            elif direction == "SHORT":

                if slope <= 0:
                    continue

            touches = []

            for p in pivots:

                if (
                    p["index"]
                    < p1["index"]
                ):
                    continue

                if (
                    p["index"]
                    > end_index
                ):
                    continue

                line = line_value(
                    p1,
                    p2,
                    p["index"]
                )

                if line is None:
                    continue

                if within_tolerance(
                    p["price"],
                    line
                ):

                    touches.append(
                        p
                    )

            if (
                len(touches)
                < MIN_TRENDLINE_TOUCHES
            ):
                continue

            first_touch = touches[0]
            last_touch = touches[-1]

            touch_span = (
                last_touch["index"]
                - first_touch["index"]
            )

            if touch_span < MIN_TRENDLINE_BARS:
                continue

            errors = []

            for p in touches:

                line = line_value(
                    p1,
                    p2,
                    p["index"]
                )

                price = safe_float(
                    p.get("price")
                )

                if (
                    line is None
                    or price is None
                    or line == 0
                ):
                    continue

                error = (
                    abs(price - line)
                    / abs(line)
                    * 100.0
                )

                if not pd.isna(
                    error
                ):
                    errors.append(
                        float(error)
                    )

            if not errors:
                continue

            avg_error = (
                sum(errors)
                / len(errors)
            )

            score = (
                len(touches)
                * 1000
                + touch_span
                - avg_error
                * 100
            )

            if pd.isna(score):
                continue

            candidate = {
                "p1": p1,
                "p2": p2,
                "touches": touches,
                "slope": float(slope),
                "score": float(score),
            }

            if (
                best is None
                or candidate["score"]
                > best["score"]
            ):

                best = candidate

    return best


# ============================================================
# BREAKOUT CONDITION
# ============================================================

def breakout_condition(
    previous_close,
    current_close,
    previous_line,
    current_line,
    direction
):

    previous_close = safe_float(
        previous_close
    )

    current_close = safe_float(
        current_close
    )

    previous_line = safe_float(
        previous_line
    )

    current_line = safe_float(
        current_line
    )

    if (
        previous_close is None
        or current_close is None
        or previous_line is None
        or current_line is None
    ):
        return False

    if (
        previous_line == 0
        or current_line == 0
    ):
        return False

    buffer = (
        BREAKOUT_BUFFER_PCT
        / 100.0
    )

    if direction == "LONG":

        previous_level = (
            previous_line
            * (1 + buffer)
        )

        current_level = (
            current_line
            * (1 + buffer)
        )

        return (
            previous_close
            <= previous_level
            and
            current_close
            > current_level
        )

    if direction == "SHORT":

        previous_level = (
            previous_line
            * (1 - buffer)
        )

        current_level = (
            current_line
            * (1 - buffer)
        )

        return (
            previous_close
            >= previous_level
            and
            current_close
            < current_level
        )

    return False


# ============================================================
# FRESH BREAKOUT
# ============================================================

def find_fresh_breakout(
    df,
    direction,
    max_age_bars,
    after_time=None
):

    if df is None:
        return None

    if len(df) < 60:
        return None

    last_index = (
        len(df) - 1
    )

    start_index = max(
        PIVOT_LEFT
        + PIVOT_RIGHT
        + 5,
        last_index
        - max_age_bars
    )

    for i in range(
        start_index,
        last_index + 1
    ):

        current_time = (
            df.iloc[i]["time"]
        )

        if (
            after_time is not None
            and current_time <= after_time
        ):
            continue

        line = find_valid_trendline(
            df,
            direction,
            end_index=i - 1
        )

        if line is None:
            continue

        p1 = line.get(
            "p1"
        )

        p2 = line.get(
            "p2"
        )

        if (
            p1 is None
            or p2 is None
        ):
            continue

        previous = df.iloc[
            i - 1
        ]

        current = df.iloc[
            i
        ]

        previous_line = line_value(
            p1,
            p2,
            i - 1
        )

        current_line = line_value(
            p1,
            p2,
            i
        )

        if (
            previous_line is None
            or current_line is None
        ):
            continue

        if breakout_condition(
            previous["close"],
            current["close"],
            previous_line,
            current_line,
            direction
        ):

            if (
                p2["index"]
                >= i
            ):
                continue

            return {
                "index": i,
                "time": current_time,
                "price": safe_float(
                    current["close"]
                ),
                "trendline": line,
                "line_price": current_line,
                "direction": direction,
            }

    return None


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    index
):

    if df is None:
        return None

    if index < RVOL_LOOKBACK:
        return None

    current_volume = safe_float(
        df.iloc[index]["volume"]
    )

    if current_volume is None:
        return None

    previous = df.iloc[
        index - RVOL_LOOKBACK:index
    ]["volume"]

    average_volume = safe_float(
        previous.mean()
    )

    if (
        average_volume is None
        or average_volume <= 0
    ):
        return None

    rvol = (
        current_volume
        / average_volume
    )

    if pd.isna(rvol):
        return None

    return float(
        rvol
    )


# ============================================================
# RETEST + CONFIRMATION
# ============================================================

def find_first_retest_and_confirmation(
    df,
    breakout,
    direction
):

    if (
        df is None
        or breakout is None
    ):
        return None

    line = breakout.get(
        "trendline"
    )

    if line is None:
        return None

    p1 = line.get(
        "p1"
    )

    p2 = line.get(
        "p2"
    )

    if (
        p1 is None
        or p2 is None
    ):
        return None

    b_index = breakout[
        "index"
    ]

    last_index = (
        len(df) - 1
    )

    # --------------------------------------------------------
    # FIRST RETEST
    # --------------------------------------------------------

    retest = None

    retest_start = (
        b_index + 1
    )

    retest_end = min(
        last_index,
        b_index
        + MAX_RETEST_WAIT_BARS
    )

    for i in range(
        retest_start,
        retest_end + 1
    ):

        candle = df.iloc[i]

        level = line_value(
            p1,
            p2,
            i
        )

        if level is None:
            continue

        tolerance = (
            RETEST_TOLERANCE_PCT
            / 100.0
        )

        candle_low = safe_float(
            candle["low"]
        )

        candle_high = safe_float(
            candle["high"]
        )

        candle_close = safe_float(
            candle["close"]
        )

        if (
            candle_low is None
            or candle_high is None
            or candle_close is None
        ):
            continue

        if direction == "LONG":

            touched = (
                candle_low
                <= level
                * (1 + tolerance)
            )

            held = (
                candle_close
                > level
            )

        else:

            touched = (
                candle_high
                >= level
                * (1 - tolerance)
            )

            held = (
                candle_close
                < level
            )

        if touched and held:

            retest = {
                "index": i,
                "time": candle["time"],
                "price": candle_close,
                "line_price": level,
            }

            break

    if retest is None:
        return None

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    confirmation = None

    confirm_start = (
        retest["index"] + 1
    )

    confirm_end = min(
        last_index,
        retest["index"]
        + MAX_CONFIRMATION_WAIT_BARS
    )

    for i in range(
        confirm_start,
        confirm_end + 1
    ):

        candle = df.iloc[i]

        previous = df.iloc[
            i - 1
        ]

        candle_open = safe_float(
            candle["open"]
        )

        candle_close = safe_float(
            candle["close"]
        )

        previous_high = safe_float(
            previous["high"]
        )

        previous_low = safe_float(
            previous["low"]
        )

        if (
            candle_open is None
            or candle_close is None
            or previous_high is None
            or previous_low is None
        ):
            continue

        if direction == "LONG":

            confirmed = (
                candle_close
                > candle_open
                and
                candle_close
                > previous_high
            )

        else:

            confirmed = (
                candle_close
                < candle_open
                and
                candle_close
                < previous_low
            )

        if not confirmed:
            continue

        if (
            REQUIRE_LATEST_CONFIRMATION
            and
            i != last_index
        ):
            continue

        confirmation = {
            "index": i,
            "time": candle["time"],
            "price": candle_close,
        }

        break

    if confirmation is None:
        return None

    return {
        "retest": retest,
        "confirmation": confirmation,
    }


# ============================================================
# TP / SL
# ============================================================

def evaluate_tp_sl(
    df15,
    direction,
    entry_price,
    entry_time
):

    if df15 is None:
        return None

    entry_price = safe_float(
        entry_price
    )

    if entry_price is None:
        return None

    highs = find_pivot_highs(
        df15
    )

    lows = find_pivot_lows(
        df15
    )

    try:

        highs = [
            p for p in highs
            if p["time"] < entry_time
        ]

        lows = [
            p for p in lows
            if p["time"] < entry_time
        ]

    except Exception:
        return None

    if direction == "LONG":

        valid_sl = [
            p for p in lows
            if safe_float(
                p.get("price")
            ) is not None
            and
            p["price"]
            < entry_price
        ]

        valid_tp = [
            p for p in highs
            if safe_float(
                p.get("price")
            ) is not None
            and
            p["price"]
            > entry_price
        ]

        if (
            not valid_sl
            or not valid_tp
        ):
            return None

        sl_pivot = valid_sl[-1]

        tp_candidates = sorted(
            valid_tp,
            key=lambda x:
                x["price"]
        )

        tp_pivot = (
            tp_candidates[0]
        )

        sl = (
            sl_pivot["price"]
            * (1 - SL_BUFFER_PCT)
        )

        tp = (
            tp_pivot["price"]
        )

        risk = (
            entry_price - sl
        )

        reward = (
            tp - entry_price
        )

    else:

        valid_sl = [
            p for p in highs
            if safe_float(
                p.get("price")
            ) is not None
            and
            p["price"]
            > entry_price
        ]

        valid_tp = [
            p for p in lows
            if safe_float(
                p.get("price")
            ) is not None
            and
            p["price"]
            < entry_price
        ]

        if (
            not valid_sl
            or not valid_tp
        ):
            return None

        sl_pivot = valid_sl[-1]

        tp_candidates = sorted(
            valid_tp,
            key=lambda x:
                x["price"],
            reverse=True
        )

        tp_pivot = (
            tp_candidates[0]
        )

        sl = (
            sl_pivot["price"]
            * (1 + SL_BUFFER_PCT)
        )

        tp = (
            tp_pivot["price"]
        )

        risk = (
            sl - entry_price
        )

        reward = (
            entry_price - tp
        )

    if (
        risk is None
        or reward is None
        or risk <= 0
        or reward <= 0
    ):
        return None

    rr = (
        reward / risk
    )

    if pd.isna(rr):
        return None

    if rr < MIN_RR:
        return None

    return {
        "tp": float(tp),
        "sl": float(sl),
        "rr": float(rr),
        "tp_pivot": tp_pivot,
        "sl_pivot": sl_pivot,
    }


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def column_exists(
    conn,
    table,
    column
):

    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return any(
        row["name"] == column
        for row in rows
    )


def init_db():

    conn = db_connect()

    try:

        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset TEXT,
                contract TEXT,
                direction TEXT,
                pattern TEXT,
                entry_price REAL,
                tp REAL,
                sl REAL,
                rr REAL,
                signal_time TEXT,
                status TEXT,
                exit_price REAL,
                exit_time TEXT,
                exit_reason TEXT,
                duration_seconds REAL,
                trendline_1h TEXT,
                trendline_15m TEXT,
                breakout_1h_time TEXT,
                breakout_15m_time TEXT,
                breakout_5m_time TEXT,
                trendline_5m TEXT,
                rvol REAL
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS scanner_meta (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)

        migrations = {
            "contract": "TEXT",
            "exit_reason": "TEXT",
            "duration_seconds": "REAL",
            "trendline_1h": "TEXT",
            "trendline_15m": "TEXT",
            "breakout_1h_time": "TEXT",
            "breakout_15m_time": "TEXT",
            "breakout_5m_time": "TEXT",
            "trendline_5m": "TEXT",
            "rvol": "REAL",
            "retest_5m_time": "TEXT",
            "confirmation_5m_time": "TEXT",
        }

        for column, col_type in migrations.items():

            if not column_exists(
                conn,
                "signals",
                column
            ):

                conn.execute(
                    f"""
                    ALTER TABLE signals
                    ADD COLUMN {column} {col_type}
                    """
                )

        conn.execute("""
            UPDATE signals
            SET contract =
                'PF_' || asset || 'USD'
            WHERE contract IS NULL
               OR contract = ''
        """)

        conn.commit()

    finally:
        conn.close()


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade(
    asset,
    direction
):

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE asset = ?
              AND direction = ?
              AND status = 'OPEN'
            LIMIT 1
        """, (
            asset,
            direction,
        )).fetchone()

        return row is not None

    finally:
        conn.close()


# ============================================================
# DUPLICATE CHECK
# ============================================================

def signal_already_exists(
    asset,
    direction,
    confirmation_time
):

    conn = db_connect()

    try:

        confirmation_iso = iso_time(
            confirmation_time
        )

        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE asset = ?
              AND direction = ?
              AND (
                    confirmation_5m_time = ?
                    OR signal_time = ?
                  )
            LIMIT 1
        """, (
            asset,
            direction,
            confirmation_iso,
            confirmation_iso,
        )).fetchone()

        return row is not None

    finally:
        conn.close()


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    asset,
    direction,
    entry_price,
    tp,
    sl,
    rr,
    signal_time,
    trendline_1h,
    trendline_15m,
    breakout_1h_time,
    breakout_15m_time,
    breakout_5m_time,
    trendline_5m,
    rvol,
    retest_5m_time,
    confirmation_5m_time
):

    if signal_already_exists(
        asset,
        direction,
        confirmation_5m_time
    ):
        return None

    if has_open_trade(
        asset,
        direction
    ):
        return None

    conn = db_connect()

    try:

        cur = conn.execute("""
            INSERT INTO signals (
                asset,
                contract,
                direction,
                pattern,
                entry_price,
                tp,
                sl,
                rr,
                signal_time,
                status,
                exit_price,
                exit_time,
                exit_reason,
                duration_seconds,
                trendline_1h,
                trendline_15m,
                breakout_1h_time,
                breakout_15m_time,
                breakout_5m_time,
                trendline_5m,
                rvol,
                retest_5m_time,
                confirmation_5m_time
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'OPEN',
                NULL, NULL, NULL, NULL,
                ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
        """, (
            asset,
            contract_name(asset),
            direction,
            "1H→15M→5M Trendline Breakout + Retest",
            float(entry_price),
            float(tp),
            float(sl),
            float(rr),
            iso_time(signal_time),
            trendline_1h,
            trendline_15m,
            iso_time(
                breakout_1h_time
            ),
            iso_time(
                breakout_15m_time
            ),
            iso_time(
                breakout_5m_time
            ),
            trendline_5m,
            safe_float(rvol),
            iso_time(
                retest_5m_time
            ),
            iso_time(
                confirmation_5m_time
            ),
        ))

        conn.commit()

        return cur.lastrowid

    finally:
        conn.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
        """).fetchall()

        closed = []

        for row in rows:

            asset = row["asset"]

            direction = row[
                "direction"
            ]

            if (
                not asset
                or not direction
            ):
                continue

            price = get_current_price(
                asset
            )

            if price is None:
                continue

            entry = safe_float(
                row["entry_price"]
            )

            tp = safe_float(
                row["tp"]
            )

            sl = safe_float(
                row["sl"]
            )

            # ------------------------------------------------
            # Legacy / incomplete DB row.
            # Do NOT crash and do NOT delete it.
            # ------------------------------------------------

            if (
                entry is None
                or tp is None
                or sl is None
                or entry <= 0
            ):
                print(
                    "WARNING: "
                    f"Incomplete OPEN trade "
                    f"id={row['id']} "
                    f"asset={asset}. "
                    "Skipped."
                )
                continue

            reason = None

            if direction == "LONG":

                if price >= tp:
                    reason = "TP"

                elif price <= sl:
                    reason = "SL"

            elif direction == "SHORT":

                if price <= tp:
                    reason = "TP"

                elif price >= sl:
                    reason = "SL"

            signal_dt = None

            try:

                signal_dt = pd.to_datetime(
                    row["signal_time"],
                    utc=True
                ).to_pydatetime()

            except Exception:
                pass

            if (
                reason is None
                and signal_dt is not None
            ):

                elapsed = (
                    now_utc()
                    - signal_dt
                ).total_seconds()

                if elapsed >= (
                    MAX_HOLD_HOURS
                    * 3600
                ):

                    reason = "TIME"

            if reason is None:
                continue

            exit_time = now_utc()

            duration = None

            if signal_dt is not None:

                duration = (
                    exit_time
                    - signal_dt
                ).total_seconds()

            conn.execute("""
                UPDATE signals
                SET status = 'CLOSED',
                    exit_price = ?,
                    exit_time = ?,
                    exit_reason = ?,
                    duration_seconds = ?
                WHERE id = ?
            """, (
                float(price),
                exit_time.isoformat(),
                reason,
                duration,
                row["id"],
            ))

            closed.append({
                "id": row["id"],
                "asset": asset,
                "direction": direction,
                "entry": entry,
                "exit": price,
                "reason": reason,
                "duration": duration,
            })

        conn.commit()

        return closed

    finally:
        conn.close()


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY signal_time DESC
        """).fetchall()

        result = []

        for row in rows:

            duration = None

            try:

                signal_dt = pd.to_datetime(
                    row["signal_time"],
                    utc=True
                ).to_pydatetime()

                duration = (
                    now_utc()
                    - signal_dt
                ).total_seconds()

            except Exception:
                pass

            result.append({
                "id": row["id"],
                "asset": row["asset"],
                "direction": row["direction"],
                "entry": safe_float(
                    row["entry_price"]
                ),
                "tp": safe_float(
                    row["tp"]
                ),
                "sl": safe_float(
                    row["sl"]
                ),
                "rr": safe_float(
                    row["rr"]
                ),
                "signal_time": row[
                    "signal_time"
                ],
                "duration": duration,
            })

        return result

    finally:
        conn.close()


# ============================================================
# CUMULATIVE PERFORMANCE
# ============================================================

def get_performance():

    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT
                entry_price,
                exit_price,
                direction,
                exit_reason
            FROM signals
            WHERE status = 'CLOSED'
              AND entry_price IS NOT NULL
              AND exit_price IS NOT NULL
        """).fetchall()

        total = 0

        wins = 0

        losses = 0

        time_exits = 0

        pnl_pct = 0.0

        for row in rows:

            entry = safe_float(
                row["entry_price"]
            )

            exit_price = safe_float(
                row["exit_price"]
            )

            if (
                entry is None
                or exit_price is None
                or entry == 0
            ):
                continue

            total += 1

            if row["direction"] == "LONG":

                trade_pct = (
                    exit_price - entry
                ) / entry * 100.0

            elif row["direction"] == "SHORT":

                trade_pct = (
                    entry - exit_price
                ) / entry * 100.0

            else:

                continue

            if not pd.isna(
                trade_pct
            ):

                pnl_pct += trade_pct

            reason = row[
                "exit_reason"
            ]

            if reason == "TP":

                wins += 1

            elif reason == "SL":

                losses += 1

            elif reason == "TIME":

                time_exits += 1

        decided = (
            wins + losses
        )

        if decided > 0:

            win_rate = (
                wins
                / decided
                * 100.0
            )

        else:

            win_rate = 0.0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "time": time_exits,
            "win_rate": win_rate,
            "pnl_pct": pnl_pct,
        }

    finally:
        conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text,
    disable_preview=True
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = SESSION.post(
            url,
            json={
                "chat_id":
                    TELEGRAM_CHAT_ID,
                "text":
                    text,
                "disable_web_page_preview":
                    disable_preview,
            },
            timeout=20
        )

        return response.ok

    except Exception:
        return False


def telegram_send_photo(
    image_bytes,
    caption=""
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        response = SESSION.post(
            url,
            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,
                "caption":
                    caption,
            },
            files={
                "photo": (
                    "signal.png",
                    image_bytes,
                    "image/png",
                )
            },
            timeout=30
        )

        return response.ok

    except Exception:
        return False


# ============================================================
# FORMATTING
# ============================================================

def fmt_price(value):

    value = safe_float(
        value
    )

    if value is None:
        return "-"

    if value >= 1000:

        return f"{value:,.2f}"

    if value >= 1:

        return f"{value:.4f}"

    if value >= 0.01:

        return f"{value:.6f}"

    return f"{value:.8f}"


def format_duration(
    seconds
):

    seconds = safe_float(
        seconds
    )

    if seconds is None:
        return "-"

    seconds = max(
        0,
        int(seconds)
    )

    days, rem = divmod(
        seconds,
        86400
    )

    hours, rem = divmod(
        rem,
        3600
    )

    minutes, _ = divmod(
        rem,
        60
    )

    if days:

        return (
            f"{days}d "
            f"{hours}h "
            f"{minutes}m"
        )

    if hours:

        return (
            f"{hours}h "
            f"{minutes}m"
        )

    return f"{minutes}m"


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    signal
):

    emoji = (
        "🟢"
        if signal["direction"]
        == "LONG"
        else "🔴"
    )

    return (
        f"{emoji} "
        f"{signal['direction']} "
        f"{signal['asset']}\n"
        f"Entry: "
        f"{fmt_price(signal['entry'])}\n"
        f"SL: "
        f"{fmt_price(signal['sl'])}\n"
        f"TP: "
        f"{fmt_price(signal['tp'])}\n"
        f"RR: "
        f"{signal['rr']:.2f}\n"
        f"RVOL: "
        f"{signal['rvol']:.2f}\n"
        f"1H Breakout: "
        f"{format_time(signal['breakout_1h_time'])}\n"
        f"15M Breakout: "
        f"{format_time(signal['breakout_15m_time'])}\n"
        f"5M Breakout: "
        f"{format_time(signal['breakout_5m_time'])}\n"
        f"5M Retest: "
        f"{format_time(signal['retest_5m_time'])}\n"
        f"5M Confirmation: "
        f"{format_time(signal['confirmation_5m_time'])}\n"
        f"Signal: "
        f"{format_time(signal['signal_time'])}"
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def close_message(
    trade
):

    if trade["reason"] == "TP":

        emoji = "🟢"

    elif trade["reason"] == "SL":

        emoji = "🔴"

    else:

        emoji = "⏱"

    entry = safe_float(
        trade["entry"]
    )

    exit_price = safe_float(
        trade["exit"]
    )

    if (
        entry is None
        or exit_price is None
        or entry == 0
    ):

        pct_text = "-"

    elif trade["direction"] == "LONG":

        pct = (
            exit_price - entry
        ) / entry * 100.0

        pct_text = f"{pct:+.2f}%"

    else:

        pct = (
            entry - exit_price
        ) / entry * 100.0

        pct_text = f"{pct:+.2f}%"

    return (
        f"{emoji} CLOSE "
        f"{trade['asset']} "
        f"{trade['direction']}\n"
        f"Entry: "
        f"{fmt_price(entry)}\n"
        f"Exit: "
        f"{fmt_price(exit_price)}\n"
        f"Result: "
        f"{pct_text}\n"
        f"Reason: "
        f"{trade['reason']}\n"
        f"Duration: "
        f"{format_duration(trade['duration'])}"
    )


# ============================================================
# PERFORMANCE MESSAGE
# ============================================================

def performance_message():

    p = get_performance()

    return (
        "📊 PERFORMANCE\n\n"
        f"Total Closed: "
        f"{p['total']}\n"
        f"🟢 Wins: "
        f"{p['wins']}\n"
        f"🔴 Losses: "
        f"{p['losses']}\n"
        f"⏱ Time Exit: "
        f"{p['time']}\n"
        f"Win Rate: "
        f"{p['win_rate']:.2f}%\n"
        f"Cumulative PnL: "
        f"{p['pnl_pct']:+.2f}%"
    )


# ============================================================
# OPEN TRADES MESSAGE
# ============================================================

def open_trades_message():

    trades = get_open_trades()

    if not trades:

        return (
            "📂 OPEN TRADES\n\n"
            "None"
        )

    lines = [
        "📂 OPEN TRADES",
        ""
    ]

    for t in trades:

        asset = t.get(
            "asset"
        )

        direction = t.get(
            "direction"
        )

        entry = safe_float(
            t.get("entry")
        )

        tp = safe_float(
            t.get("tp")
        )

        sl = safe_float(
            t.get("sl")
        )

        rr = safe_float(
            t.get("rr")
        )

        duration = t.get(
            "duration"
        )

        # ----------------------------------------------------
        # INVALID / LEGACY DB ROW
        # ----------------------------------------------------

        if (
            not asset
            or not direction
            or entry is None
            or entry <= 0
        ):

            lines.append(
                f"⚠️ "
                f"{asset or 'UNKNOWN'} "
                f"{direction or 'UNKNOWN'}"
            )

            lines.append(
                "Incomplete DB record "
                "| skipped safely"
            )

            lines.append(
                "Duration: "
                f"{format_duration(duration)}"
            )

            lines.append("")

            continue

        # ----------------------------------------------------
        # NORMAL OPEN TRADE
        # ----------------------------------------------------

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        current = get_current_price(
            asset
        )

        pct = None

        if (
            current is not None
            and entry is not None
            and entry > 0
        ):

            if direction == "LONG":

                pct = (
                    current - entry
                ) / entry * 100.0

            elif direction == "SHORT":

                pct = (
                    entry - current
                ) / entry * 100.0

        pct_text = (
            f"{pct:+.2f}%"
            if pct is not None
            else "-"
        )

        rr_text = (
            f"{rr:.2f}"
            if rr is not None
            else "-"
        )

        lines.append(
            f"{emoji} "
            f"{asset} "
            f"{direction} "
            f"{pct_text}"
        )

        lines.append(
            f"Entry "
            f"{fmt_price(entry)} | "
            f"TP "
            f"{fmt_price(tp)} | "
            f"SL "
            f"{fmt_price(sl)} | "
            f"RR {rr_text}"
        )

        lines.append(
            "Duration: "
            f"{format_duration(duration)}"
        )

        lines.append("")

    return "\n".join(
        lines
    )


# ============================================================
# META
# ============================================================

def get_meta(
    key
):

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT value
            FROM scanner_meta
            WHERE key = ?
        """, (
            key,
        )).fetchone()

        if row is None:
            return None

        return row["value"]

    finally:
        conn.close()


def set_meta(
    key,
    value
):

    conn = db_connect()

    try:

        conn.execute("""
            INSERT INTO scanner_meta (
                key,
                value
            )
            VALUES (?, ?)
            ON CONFLICT(key)
            DO UPDATE SET
                value = excluded.value
        """, (
            key,
            str(value),
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# PERIODIC REPORT
# ============================================================

def maybe_send_periodic_report():

    key = (
        "last_periodic_report"
    )

    last = get_meta(
        key
    )

    current = time.time()

    if last is not None:

        try:

            last = float(
                last
            )

            if (
                current - last
                < PERIODIC_REPORT_SECONDS
            ):
                return

        except Exception:
            pass

    text = (
        "📊 PERIODIC REPORT\n\n"
        f"{open_trades_message()}\n\n"
        f"{performance_message()}"
    )

    telegram_send(
        text
    )

    set_meta(
        key,
        current
    )


# ============================================================
# TRENDLINE SERIALIZATION
# ============================================================

def serialize_trendline(
    line
):

    if line is None:
        return None

    try:

        p1 = line.get(
            "p1"
        )

        p2 = line.get(
            "p2"
        )

        if (
            p1 is None
            or p2 is None
        ):
            return None

        data = {
            "p1": {
                "index":
                    int(
                        p1["index"]
                    ),
                "time":
                    iso_time(
                        p1["time"]
                    ),
                "price":
                    float(
                        p1["price"]
                    ),
            },
            "p2": {
                "index":
                    int(
                        p2["index"]
                    ),
                "time":
                    iso_time(
                        p2["time"]
                    ),
                "price":
                    float(
                        p2["price"]
                    ),
            },
            "touches": [],
            "slope":
                safe_float(
                    line.get("slope")
                ),
            "score":
                safe_float(
                    line.get("score")
                ),
        }

        for p in line.get(
            "touches",
            []
        ):

            if p is None:
                continue

            index = p.get(
                "index"
            )

            price = safe_float(
                p.get("price")
            )

            if (
                index is None
                or price is None
            ):
                continue

            data["touches"].append({
                "index": int(index),
                "time": iso_time(
                    p.get("time")
                ),
                "price": price,
            })

        return json.dumps(
            data,
            ensure_ascii=False
        )

    except Exception:
        return None


# ============================================================
# CHART
# ============================================================

def trendline_y_for_index(
    trendline,
    index
):

    if trendline is None:
        return None

    return line_value(
        trendline.get("p1"),
        trendline.get("p2"),
        index
    )


def create_signal_chart(
    signal
):

    df = signal.get(
        "df5"
    )

    if df is None:
        return None

    if len(df) < 30:
        return None

    line = signal.get(
        "trendline_5m"
    )

    breakout = signal.get(
        "b5"
    )

    retest = signal.get(
        "retest"
    )

    confirmation = signal.get(
        "confirmation"
    )

    entry_price = safe_float(
        signal.get("entry")
    )

    tp = safe_float(
        signal.get("tp")
    )

    sl = safe_float(
        signal.get("sl")
    )

    if (
        entry_price is None
        or tp is None
        or sl is None
    ):
        return None

    start = max(
        0,
        len(df) - 120
    )

    plot_df = df.iloc[
        start:
    ].copy()

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    for local_i, (
        _,
        row
    ) in enumerate(
        plot_df.iterrows()
    ):

        op = safe_float(
            row["open"]
        )

        hi = safe_float(
            row["high"]
        )

        lo = safe_float(
            row["low"]
        )

        cl = safe_float(
            row["close"]
        )

        if (
            op is None
            or hi is None
            or lo is None
            or cl is None
        ):
            continue

        body_low = min(
            op,
            cl
        )

        body_high = max(
            op,
            cl
        )

        ax.vlines(
            local_i,
            lo,
            hi,
            linewidth=1
        )

        ax.add_patch(
            plt.Rectangle(
                (
                    local_i - 0.3,
                    body_low
                ),
                0.6,
                max(
                    body_high
                    - body_low,
                    1e-12
                ),
                fill=True
            )
        )

    # --------------------------------------------------------
    # TRENDLINE
    # --------------------------------------------------------

    if line is not None:

        xs = list(
            range(
                len(plot_df)
            )
        )

        global_indices = [
            start + i
            for i in xs
        ]

        ys = [
            trendline_y_for_index(
                line,
                i
            )
            for i in global_indices
        ]

        valid = [
            (
                x,
                y
            )
            for x, y in zip(
                xs,
                ys
            )
            if y is not None
        ]

        if valid:

            vx = [
                item[0]
                for item in valid
            ]

            vy = [
                item[1]
                for item in valid
            ]

            ax.plot(
                vx,
                vy,
                linewidth=2,
                label="5M Trendline"
            )

        for p in line.get(
            "touches",
            []
        ):

            if p is None:
                continue

            idx = p.get(
                "index"
            )

            price = safe_float(
                p.get("price")
            )

            if (
                idx is None
                or price is None
            ):
                continue

            if (
                start
                <= idx
                < len(df)
            ):

                local_x = (
                    idx - start
                )

                ax.scatter(
                    [local_x],
                    [price],
                    s=45,
                    zorder=5
                )

                ax.annotate(
                    "T",
                    (
                        local_x,
                        price
                    ),
                    xytext=(
                        0,
                        7
                    ),
                    textcoords=(
                        "offset points"
                    ),
                    ha="center"
                )

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    if breakout is not None:

        idx = breakout.get(
            "index"
        )

        price = safe_float(
            breakout.get("price")
        )

        if (
            idx is not None
            and price is not None
            and start <= idx < len(df)
        ):

            local_x = (
                idx - start
            )

            marker = (
                "^"
                if signal["direction"]
                == "LONG"
                else "v"
            )

            ax.scatter(
                [local_x],
                [price],
                s=110,
                marker=marker,
                zorder=10,
                label="5M Breakout"
            )

    # --------------------------------------------------------
    # RETEST
    # --------------------------------------------------------

    if retest is not None:

        idx = retest.get(
            "index"
        )

        price = safe_float(
            retest.get("price")
        )

        if (
            idx is not None
            and price is not None
            and start <= idx < len(df)
        ):

            local_x = (
                idx - start
            )

            ax.scatter(
                [local_x],
                [price],
                s=90,
                marker="o",
                zorder=10,
                label="Retest"
            )

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    if confirmation is not None:

        idx = confirmation.get(
            "index"
        )

        price = safe_float(
            confirmation.get("price")
        )

        if (
            idx is not None
            and price is not None
            and start <= idx < len(df)
        ):

            local_x = (
                idx - start
            )

            ax.scatter(
                [local_x],
                [price],
                s=140,
                marker="*",
                zorder=11,
                label="Confirmation"
            )

    # --------------------------------------------------------
    # ENTRY / TP / SL
    # --------------------------------------------------------

    ax.axhline(
        entry_price,
        linestyle="--",
        linewidth=1.5,
        label=(
            f"Entry "
            f"{fmt_price(entry_price)}"
        )
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1,
        label=(
            f"TP "
            f"{fmt_price(tp)}"
        )
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1,
        label=(
            f"SL "
            f"{fmt_price(sl)}"
        )
    )

    ax.set_title(
        f"{signal['asset']} "
        f"{signal['direction']} | "
        f"5M Breakout + Retest + Confirmation"
    )

    ax.set_xlabel(
        "5M Closed Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.25
    )

    ax.legend(
        loc="best"
    )

    plt.tight_layout()

    buffer = io.BytesIO()

    plt.savefig(
        buffer,
        format="png",
        dpi=150
    )

    plt.close(
        fig
    )

    buffer.seek(0)

    return buffer.getvalue()


# ============================================================
# SCAN ONE ASSET
# ============================================================

def scan_asset(
    asset,
    stats
):

    # ========================================================
    # 1H
    # ========================================================

    df1 = get_ohlc(
        asset,
        "1h"
    )

    if df1 is None:

        stats[
            "ohlc_1h_failed"
        ] += 1

        return []

    stats[
        "ohlc_1h_ok"
    ] += 1

    # ========================================================
    # 1H BREAKOUT
    # ========================================================

    b1_candidates = []

    for direction in (
        "LONG",
        "SHORT"
    ):

        line1 = find_valid_trendline(
            df1,
            direction
        )

        if line1 is not None:

            stats[
                "trendlines_1h"
            ] += 1

        b1 = find_fresh_breakout(
            df1,
            direction,
            MAX_1H_BREAKOUT_AGE_BARS
        )

        if b1 is not None:

            stats[
                "breakouts_1h"
            ] += 1

            b1_candidates.append(
                b1
            )

    if not b1_candidates:
        return []

    b1 = max(
        b1_candidates,
        key=lambda x:
            x["time"]
    )

    direction = b1[
        "direction"
    ]

    if has_open_trade(
        asset,
        direction
    ):
        return []

    # ========================================================
    # 15M
    # ========================================================

    df15 = get_ohlc(
        asset,
        "15m"
    )

    if df15 is None:

        stats[
            "ohlc_15m_failed"
        ] += 1

        return []

    stats[
        "ohlc_15m_ok"
    ] += 1

    line15 = find_valid_trendline(
        df15,
        direction
    )

    if line15 is None:
        return []

    stats[
        "trendlines_15m"
    ] += 1

    b15 = find_fresh_breakout(
        df15,
        direction,
        MAX_15M_BREAKOUT_AGE_BARS,
        after_time=b1["time"]
    )

    if b15 is None:
        return []

    stats[
        "breakouts_15m"
    ] += 1

    # ========================================================
    # RVOL
    # ========================================================

    rvol = calculate_rvol(
        df15,
        b15["index"]
    )

    if rvol is None:
        return []

    if rvol < RVOL_MIN:
        return []

    stats[
        "rvol_confirmed"
    ] += 1

    # ========================================================
    # 5M
    # ========================================================

    df5 = get_ohlc(
        asset,
        "5m"
    )

    if df5 is None:

        stats[
            "ohlc_5m_failed"
        ] += 1

        return []

    stats[
        "ohlc_5m_ok"
    ] += 1

    line5 = find_valid_trendline(
        df5,
        direction
    )

    if line5 is None:
        return []

    stats[
        "trendlines_5m"
    ] += 1

    # ========================================================
    # FRESH 5M BREAKOUT
    # ========================================================

    b5 = find_fresh_breakout(
        df5,
        direction,
        MAX_5M_BREAKOUT_AGE_BARS,
        after_time=b15["time"]
    )

    if b5 is None:
        return []

    stats[
        "breakouts_5m"
    ] += 1

    # ========================================================
    # RETEST + CONFIRMATION
    # ========================================================

    rc = (
        find_first_retest_and_confirmation(
            df5,
            b5,
            direction
        )
    )

    if rc is None:
        return []

    retest = rc[
        "retest"
    ]

    confirmation = rc[
        "confirmation"
    ]

    stats[
        "retests_5m"
    ] += 1

    stats[
        "confirmations_5m"
    ] += 1

    if REQUIRE_LATEST_CONFIRMATION:

        if confirmation[
            "index"
        ] != (
            len(df5) - 1
        ):
            return []

    # ========================================================
    # ENTRY
    # ========================================================

    entry_price = safe_float(
        confirmation[
            "price"
        ]
    )

    if entry_price is None:
        return []

    entry_time = confirmation[
        "time"
    ]

    # ========================================================
    # TP / SL
    # ========================================================

    risk = evaluate_tp_sl(
        df15,
        direction,
        entry_price,
        entry_time
    )

    if risk is None:
        return []

    candidate = {

        "asset":
            asset,

        "direction":
            direction,

        "stage":
            "READY",

        "entry":
            entry_price,

        "tp":
            risk["tp"],

        "sl":
            risk["sl"],

        "rr":
            risk["rr"],

        "rvol":
            rvol,

        "b1":
            b1,

        "b15":
            b15,

        "b5":
            b5,

        "trendline_1h":
            b1["trendline"],

        "trendline_15m":
            b15["trendline"],

        "trendline_5m":
            b5["trendline"],

        "retest":
            retest,

        "confirmation":
            confirmation,

        "signal_time":
            confirmation["time"],

        "breakout_1h_time":
            b1["time"],

        "breakout_15m_time":
            b15["time"],

        "breakout_5m_time":
            b5["time"],

        "retest_5m_time":
            retest["time"],

        "confirmation_5m_time":
            confirmation["time"],

        "df1":
            df1,

        "df15":
            df15,

        "df5":
            df5,
    }

    stats[
        "ready"
    ] += 1

    return [
        candidate
    ]


# ============================================================
# SUMMARY
# ============================================================

def summary_message(
    stats,
    assets_count
):

    return (
        "📊 SCAN SUMMARY\n\n"
        f"Version: {VERSION}\n"
        f"Mode: PAPER ONLY\n"
        f"Time: Tehran\n\n"
        f"Assets Scanned: "
        f"{assets_count}/{len(ASSETS)}\n"
        f"1H OHLC OK: "
        f"{stats['ohlc_1h_ok']}\n"
        f"15M OHLC OK: "
        f"{stats['ohlc_15m_ok']}\n"
        f"5M OHLC OK: "
        f"{stats['ohlc_5m_ok']}\n\n"
        f"1H Trendlines: "
        f"{stats['trendlines_1h']}\n"
        f"1H Fresh Breakouts: "
        f"{stats['breakouts_1h']}\n"
        f"15M Trendlines: "
        f"{stats['trendlines_15m']}\n"
        f"15M Fresh Breakouts: "
        f"{stats['breakouts_15m']}\n"
        f"RVOL Confirmed: "
        f"{stats['rvol_confirmed']}\n"
        f"5M Trendlines: "
        f"{stats['trendlines_5m']}\n"
        f"5M Fresh Breakouts: "
        f"{stats['breakouts_5m']}\n"
        f"5M Retests: "
        f"{stats['retests_5m']}\n"
        f"5M Confirmations: "
        f"{stats['confirmations_5m']}\n"
        f"READY: "
        f"{stats['ready']}\n"
        f"New Signals: "
        f"{stats['new_signals']}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "KRAKEN FUTURES TRENDLINE "
        "LIVE SIGNAL SCANNER "
        f"{VERSION}"
    )

    print(
        "=" * 70
    )

    print(
        "REAL_TRADING:",
        REAL_TRADING
    )

    print(
        "PAPER_TRADING:",
        PAPER_TRADING
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    if not PAPER_TRADING:

        raise RuntimeError(
            "PAPER_TRADING must remain True."
        )

    # ========================================================
    # DATABASE
    # ========================================================

    init_db()

    # ========================================================
    # TICKERS
    # ========================================================

    try:

        response = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=20
        )

        response.raise_for_status()

        payload = response.json()

        rows = (
            payload.get("tickers")
            or payload.get("data")
            or []
        )

        TICKER_CACHE.clear()

        for row in rows:

            symbol = (
                row.get("symbol")
                or row.get("instrument")
                or row.get("contractSymbol")
            )

            if symbol:

                TICKER_CACHE[
                    symbol
                ] = row

    except Exception as e:

        print(
            "Ticker error:",
            str(e)
        )

    # ========================================================
    # UPDATE OPEN TRADES
    # ========================================================

    closed = update_open_trades()

    for trade in closed:

        print(
            "CLOSED:",
            trade
        )

        telegram_send(
            close_message(
                trade
            )
        )

    # ========================================================
    # STATS
    # ========================================================

    stats = {

        "ohlc_1h_ok":
            0,

        "ohlc_1h_failed":
            0,

        "ohlc_15m_ok":
            0,

        "ohlc_15m_failed":
            0,

        "ohlc_5m_ok":
            0,

        "ohlc_5m_failed":
            0,

        "trendlines_1h":
            0,

        "trendlines_15m":
            0,

        "trendlines_5m":
            0,

        "breakouts_1h":
            0,

        "breakouts_15m":
            0,

        "breakouts_5m":
            0,

        "rvol_confirmed":
            0,

        "retests_5m":
            0,

        "confirmations_5m":
            0,

        "ready":
            0,

        "new_signals":
            0,
    }

    candidates = []

    scanned = 0

    # ========================================================
    # SCAN ALL ASSETS
    # ========================================================

    for asset in ASSETS:

        scanned += 1

        try:

            found = scan_asset(
                asset,
                stats
            )

            if found:

                candidates.extend(
                    found
                )

        except Exception as e:

            print(
                f"{asset} scan error: "
                f"{e}"
            )

            traceback.print_exc()

    # ========================================================
    # RANK CANDIDATES
    # ========================================================

    candidates.sort(
        key=lambda x: (
            safe_float(
                x.get("rr"),
                0
            ),
            safe_float(
                x.get("rvol"),
                0
            )
        ),
        reverse=True
    )

    selected = candidates[
        :MAX_SIGNALS_PER_SCAN
    ]

    # ========================================================
    # INSERT SIGNALS
    # ========================================================

    for signal in selected:

        inserted_id = insert_signal(

            asset=signal[
                "asset"
            ],

            direction=signal[
                "direction"
            ],

            entry_price=signal[
                "entry"
            ],

            tp=signal[
                "tp"
            ],

            sl=signal[
                "sl"
            ],

            rr=signal[
                "rr"
            ],

            signal_time=signal[
                "signal_time"
            ],

            trendline_1h=
                serialize_trendline(
                    signal[
                        "trendline_1h"
                    ]
                ),

            trendline_15m=
                serialize_trendline(
                    signal[
                        "trendline_15m"
                    ]
                ),

            breakout_1h_time=
                signal[
                    "breakout_1h_time"
                ],

            breakout_15m_time=
                signal[
                    "breakout_15m_time"
                ],

            breakout_5m_time=
                signal[
                    "breakout_5m_time"
                ],

            trendline_5m=
                serialize_trendline(
                    signal[
                        "trendline_5m"
                    ]
                ),

            rvol=signal[
                "rvol"
            ],

            retest_5m_time=
                signal[
                    "retest_5m_time"
                ],

            confirmation_5m_time=
                signal[
                    "confirmation_5m_time"
                ],
        )

        if inserted_id is None:
            continue

        signal[
            "id"
        ] = inserted_id

        stats[
            "new_signals"
        ] += 1

        print(
            f"NEW SIGNAL "
            f"#{inserted_id}: "
            f"{signal['asset']} "
            f"{signal['direction']} "
            f"Entry="
            f"{signal['entry']}"
        )

        telegram_send(
            signal_message(
                signal
            )
        )

        # ====================================================
        # CHART
        # ====================================================

        try:

            chart = create_signal_chart(
                signal
            )

            if chart is not None:

                caption = (
                    f"{'🟢' if signal['direction'] == 'LONG' else '🔴'} "
                    f"{signal['asset']} "
                    f"{signal['direction']}\n"
                    f"5M Breakout → Retest → Confirmation\n"
                    f"Entry: "
                    f"{fmt_price(signal['entry'])}\n"
                    f"TP: "
                    f"{fmt_price(signal['tp'])}\n"
                    f"SL: "
                    f"{fmt_price(signal['sl'])}\n"
                    f"RR: "
                    f"{signal['rr']:.2f}"
                )

                telegram_send_photo(
                    chart,
                    caption
                )

        except Exception:

            traceback.print_exc()

    # ========================================================
    # CONSOLE SUMMARY
    # ========================================================

    print()

    print(
        summary_message(
            stats,
            scanned
        )
    )

    print()

    print(
        performance_message()
    )

    print()

    # This is now SAFE even if the DB contains
    # incomplete legacy OPEN rows.
    print(
        open_trades_message()
    )

    # ========================================================
    # PERIODIC REPORT
    # ========================================================

    try:

        maybe_send_periodic_report()

    except Exception:

        traceback.print_exc()

    print()

    print(
        "=" * 70
    )

    print(
        "SCAN FINISHED:",
        format_time(
            now_utc()
        )
    )

    print(
        "=" * 70
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "FATAL ERROR:",
            str(e)
        )

        traceback.print_exc()

        try:

            telegram_send(
                "⚠️ SCANNER ERROR\n\n"
                f"Version: {VERSION}\n"
                f"{str(e)}"
            )

        except Exception:
            pass

        raise
