# ============================================================
# KRAKEN FUTURES MULTI-TIMEFRAME PATTERN BACKTEST
# ============================================================
#
# 1H PATTERN
#      ↓
# 1H CLOSED BREAKOUT
#      ↓
# FIRST 5M RETEST
#      ↓
# 5M CONFIRMATION
#      ↓
# ENTRY
#
# VERSION 3.0
#
# IMPORTANT:
# - Kraken Futures
# - Full 365-day historical download
# - Candle pagination/chunking
# - Closed candles only
# - No lookahead
# - Fixed preferred universe
# - Uses only symbols actually available on Kraken
# - One trade per pattern instance
# - FIRST retest only
# - TP = 2%
# - SL = 1%
# - Max hold = 48 hours
# - Same candle TP + SL => SL first
# - Unresolved trades excluded
# - No real trading
# ============================================================

import time
import requests
import pandas as pd
import numpy as np

from collections import defaultdict


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TICKERS_ENDPOINT = "/derivatives/api/v3/tickers"
INSTRUMENTS_ENDPOINT = "/derivatives/api/v3/instruments"

CANDLE_ENDPOINT = "/api/charts/v1/trade"

INTERVAL_1H = "1h"
INTERVAL_5M = "5m"

LOOKBACK_DAYS = 365

TP_PCT = 0.02
SL_PCT = 0.01

MAX_HOLD_5M = 576          # 48 hours
MAX_ENTRY_WAIT_5M = 72     # 6 hours
MAX_CONFIRM_WAIT_5M = 6     # 30 minutes

# Number of candles requested per Kraken chunk.
# Keep below the observed ~2000 candle response limit.
CHUNK_COUNT = 1900

REQUEST_TIMEOUT = 30
MAX_RETRIES = 5

REQUEST_SLEEP = 0.08


# ============================================================
# PREFERRED UNIVERSE
# ============================================================
#
# We DO NOT select symbols by current volume.
#
# This prevents today's volume from deciding which markets
# are tested over the historical year.
#
# Kraken may not currently expose every requested market.
# Only actually available perpetuals are used.
# ============================================================

TARGET_ASSETS = [
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
    "BCH",
    "UNI",
    "AAVE",
    "ATOM",
    "XLM",
    "ALGO",
    "FIL",
    "ETC",
    "SUI",
    "HBAR",
]


# ============================================================
# PATTERN SETTINGS
# ============================================================

SWING_LEFT = 3
SWING_RIGHT = 3

MIN_PATTERN_DISTANCE = 8
MAX_PATTERN_DISTANCE = 100

MIN_SWING_MOVE_PCT = 0.004

DOUBLE_TOLERANCE = 0.015
SHOULDER_TOLERANCE = 0.035
HEAD_MIN_DISTANCE = 0.008

BREAKOUT_BUFFER = 0.0015


# ============================================================
# 5M ENTRY SETTINGS
# ============================================================

RETEST_TOLERANCE = 0.0035

CONFIRM_BODY_RATIO = 0.45
CONFIRM_CLOSE_POSITION = 0.60


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Kraken-MTF-Pattern-Backtest/3.0",
    "Accept": "application/json",
})


# ============================================================
# BASIC HELPERS
# ============================================================

def is_finite(value):

    return (
        value is not None
        and np.isfinite(value)
    )


def get_json(path, params=None):

    url = BASE_URL + path

    last_error = None

    for attempt in range(MAX_RETRIES):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise RuntimeError(
                    "Unexpected Kraken response"
                )

            time.sleep(REQUEST_SLEEP)

            return data

        except Exception as exc:

            last_error = exc

            if attempt < MAX_RETRIES - 1:

                time.sleep(
                    1.0 + attempt * 1.5
                )

    raise RuntimeError(
        f"Kraken request failed: {url} | {last_error}"
    )


# ============================================================
# SYMBOL DISCOVERY
# ============================================================

def get_available_perpetual_symbols():

    available = {}

    # --------------------------------------------------------
    # Primary source: tickers
    # --------------------------------------------------------

    try:

        data = get_json(
            TICKERS_ENDPOINT
        )

        tickers = data.get(
            "tickers",
            []
        )

        for item in tickers:

            symbol = str(
                item.get("symbol", "")
            )

            tag = str(
                item.get("tag", "")
            ).lower()

            if not symbol:
                continue

            if tag != "perpetual":
                continue

            if not symbol.startswith("PI_"):
                continue

            if not symbol.endswith("USD"):
                continue

            for asset in TARGET_ASSETS:

                expected = (
                    f"PI_{asset}USD"
                )

                if symbol == expected:

                    available[asset] = symbol

    except Exception as exc:

        print(
            f"Ticker discovery warning: {exc}"
        )

    # --------------------------------------------------------
    # Secondary source: instruments endpoint
    #
    # This can discover instruments even when ticker payload
    # is incomplete.
    # --------------------------------------------------------

    try:

        data = get_json(
            INSTRUMENTS_ENDPOINT
        )

        instruments = (
            data.get("instruments")
            or data.get("data")
            or []
        )

        if isinstance(instruments, dict):
            instruments = list(
                instruments.values()
            )

        for item in instruments:

            if not isinstance(item, dict):
                continue

            symbol = str(
                item.get("symbol")
                or item.get("tradeable")
                or item.get("instrument")
                or ""
            )

            if not symbol:
                continue

            if not symbol.startswith("PI_"):
                continue

            if not symbol.endswith("USD"):
                continue

            # Check common perpetual markers.
            text = " ".join(
                str(v).lower()
                for v in item.values()
            )

            is_perpetual = (
                "perpetual" in text
                or "perpetual" in symbol.lower()
            )

            if not is_perpetual:
                # PI_ contracts are Kraken perpetual
                # naming in the relevant universe.
                continue

            for asset in TARGET_ASSETS:

                expected = (
                    f"PI_{asset}USD"
                )

                if symbol == expected:

                    available[asset] = symbol

    except Exception as exc:

        print(
            f"Instruments discovery warning: {exc}"
        )

    return available


# ============================================================
# TIME HELPERS
# ============================================================

def floor_time(ts, interval):

    ts = pd.Timestamp(ts)

    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    else:
        ts = ts.tz_convert("UTC")

    if interval == "1h":

        return ts.floor("h")

    if interval == "5m":

        return ts.floor("5min")

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


def interval_delta(interval):

    if interval == "1h":
        return pd.Timedelta(hours=1)

    if interval == "5m":
        return pd.Timedelta(minutes=5)

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


def interval_seconds(interval):

    if interval == "1h":
        return 3600

    if interval == "5m":
        return 300

    raise ValueError(
        f"Unsupported interval: {interval}"
    )


# ============================================================
# CANDLE ROW PARSER
# ============================================================

def parse_candle_row(row):

    if isinstance(row, dict):

        ts = (
            row.get("time")
            or row.get("timestamp")
            or row.get("t")
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

        return ts, o, h, l, c, v

    if isinstance(row, (list, tuple)):

        if len(row) >= 6:

            return (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
            )

    return None


# ============================================================
# FETCH ONE CHUNK
# ============================================================

def fetch_candle_chunk(
    symbol,
    interval,
    start_ts,
    end_ts,
):

    path = (
        f"{CANDLE_ENDPOINT}/"
        f"{symbol}/"
        f"{interval}"
    )

    params = {
        "from": int(start_ts),
        "to": int(end_ts),
        "count": CHUNK_COUNT,
    }

    data = get_json(
        path,
        params=params
    )

    raw = (
        data.get("candles")
        or data.get("data")
        or []
    )

    rows = []

    for row in raw:

        parsed = parse_candle_row(row)

        if parsed is None:
            continue

        ts, o, h, l, c, v = parsed

        try:

            ts = float(ts)

            # Kraken candle timestamps are documented
            # as milliseconds, but this also handles seconds.
            if ts > 10_000_000_000:

                timestamp = pd.to_datetime(
                    ts,
                    unit="ms",
                    utc=True
                )

            else:

                timestamp = pd.to_datetime(
                    ts,
                    unit="s",
                    utc=True
                )

            rows.append({
                "timestamp": timestamp,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            })

        except Exception:
            continue

    return rows


# ============================================================
# FULL HISTORICAL CANDLES
# ============================================================

def fetch_candles(
    symbol,
    interval,
    days,
):

    delta = interval_delta(interval)

    step_seconds = (
        interval_seconds(interval)
        * (CHUNK_COUNT - 1)
    )

    now = pd.Timestamp.now(
        tz="UTC"
    )

    # Only completed candles.
    end_time = (
        floor_time(now, interval)
        - delta
    )

    start_time = (
        end_time
        - pd.Timedelta(days=days)
        + delta
    )

    start_ts = int(
        start_time.timestamp()
    )

    end_ts = int(
        end_time.timestamp()
    )

    all_rows = []

    cursor = start_ts

    expected_minimum = (
        int(
            days
            * 24
            * 60
            / (
                60
                if interval == "1h"
                else 5
            )
        )
        * 0.90
    )

    request_number = 0

    while cursor <= end_ts:

        chunk_end = min(
            cursor + step_seconds,
            end_ts
        )

        request_number += 1

        rows = fetch_candle_chunk(
            symbol,
            interval,
            cursor,
            chunk_end,
        )

        if not rows:

            # Move forward even if a chunk is empty.
            cursor = (
                chunk_end
                + interval_seconds(interval)
            )

            continue

        all_rows.extend(rows)

        timestamps = [
            int(
                x["timestamp"].timestamp()
            )
            for x in rows
        ]

        last_timestamp = max(
            timestamps
        )

        next_cursor = (
            last_timestamp
            + interval_seconds(interval)
        )

        # Safety against API returning same data.
        if next_cursor <= cursor:

            cursor = (
                chunk_end
                + interval_seconds(interval)
            )

        else:

            cursor = next_cursor

        # If the requested range was completely covered.
        if last_timestamp >= end_ts:
            break

    if not all_rows:

        return pd.DataFrame()

    df = pd.DataFrame(
        all_rows
    )

    df = (
        df
        .drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # Strict requested range.
    df = df[
        (df["timestamp"] >= start_time)
        &
        (df["timestamp"] <= end_time)
    ].copy()

    # Remove incomplete candles again defensively.
    now = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["timestamp"] + delta <= now
    ].copy()

    df = (
        df
        .drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    if len(df) < expected_minimum:

        print(
            f"WARNING: {symbol} {interval} "
            f"returned only {len(df):,} candles; "
            f"expected approximately {expected_minimum:,.0f}+"
        )

    return df


# ============================================================
# DATA QUALITY
# ============================================================

def validate_candles(
    df,
    interval,
):

    if df.empty:
        return False

    required = [
        "timestamp",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in required:

        if col not in df.columns:
            return False

    if df["timestamp"].duplicated().any():
        return False

    if not df["timestamp"].is_monotonic_increasing:
        return False

    if (
        df["high"] < df["low"]
    ).any():

        return False

    if (
        df["high"] < df["open"]
    ).any():

        return False

    if (
        df["high"] < df["close"]
    ).any():

        return False

    if (
        df["low"] > df["open"]
    ).any():

        return False

    if (
        df["low"] > df["close"]
    ).any():

        return False

    delta = interval_delta(
        interval
    )

    now = pd.Timestamp.now(
        tz="UTC"
    )

    if (
        df["timestamp"].iloc[-1]
        + delta
        > now
    ):
        return False

    return True


# ============================================================
# SWINGS
# ============================================================

def find_swings(df):

    highs = []
    lows = []

    for i in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT
    ):

        current_high = df.iloc[
            i
        ]["high"]

        current_low = df.iloc[
            i
        ]["low"]

        left_highs = df.iloc[
            i - SWING_LEFT:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:
            i + SWING_RIGHT + 1
        ]["high"]

        left_lows = df.iloc[
            i - SWING_LEFT:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:
            i + SWING_RIGHT + 1
        ]["low"]

        if (
            current_high > left_highs.max()
            and current_high >= right_highs.max()
        ):

            highs.append({
                "index": i,
                "price": current_high,
                "confirm_index": (
                    i + SWING_RIGHT
                ),
                "type": "H",
            })

        if (
            current_low < left_lows.min()
            and current_low <= right_lows.min()
        ):

            lows.append({
                "index": i,
                "price": current_low,
                "confirm_index": (
                    i + SWING_RIGHT
                ),
                "type": "L",
            })

    return highs, lows


# ============================================================
# ALTERNATING SWINGS
# ============================================================

def get_recent_alternating_swings(
    highs,
    lows,
    max_count=7,
):

    combined = (
        highs + lows
    )

    combined = sorted(
        combined,
        key=lambda x: x["index"]
    )

    result = []

    for swing in combined:

        if not result:

            result.append(swing)
            continue

        previous = result[-1]

        if swing["type"] == previous["type"]:

            if swing["type"] == "H":

                if (
                    swing["price"]
                    > previous["price"]
                ):
                    result[-1] = swing

            else:

                if (
                    swing["price"]
                    < previous["price"]
                ):
                    result[-1] = swing

        else:

            result.append(swing)

    return result[-max_count:]


# ============================================================
# PATTERN OBJECT
# ============================================================

def make_pattern(
    name,
    direction,
    start_index,
    end_index,
    breakout_level,
    anchors,
    dynamic=False,
):

    return {
        "name": name,
        "direction": direction,
        "start_index": start_index,
        "end_index": end_index,
        "breakout_level": float(
            breakout_level
        ),
        "anchors": tuple(
            int(x["index"])
            for x in anchors
        ),
        "dynamic": dynamic,
    }


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(swings):

    if len(swings) < 3:
        return None

    s = swings[-3:]

    if [
        x["type"] for x in s
    ] != ["L", "H", "L"]:

        return None

    left, middle, right = s

    distance = (
        right["index"]
        - left["index"]
    )

    if (
        distance < MIN_PATTERN_DISTANCE
        or distance > MAX_PATTERN_DISTANCE
    ):
        return None

    avg_low = (
        left["price"]
        + right["price"]
    ) / 2

    if avg_low <= 0:
        return None

    if (
        abs(
            left["price"]
            - right["price"]
        )
        / avg_low
        > DOUBLE_TOLERANCE
    ):
        return None

    if (
        middle["price"]
        - avg_low
    ) / avg_low < MIN_SWING_MOVE_PCT:

        return None

    return make_pattern(
        "Double Bottom",
        "LONG",
        left["index"],
        right["index"],
        middle["price"],
        s,
    )


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(swings):

    if len(swings) < 3:
        return None

    s = swings[-3:]

    if [
        x["type"] for x in s
    ] != ["H", "L", "H"]:

        return None

    left, middle, right = s

    distance = (
        right["index"]
        - left["index"]
    )

    if (
        distance < MIN_PATTERN_DISTANCE
        or distance > MAX_PATTERN_DISTANCE
    ):
        return None

    avg_high = (
        left["price"]
        + right["price"]
    ) / 2

    if avg_high <= 0:
        return None

    if (
        abs(
            left["price"]
            - right["price"]
        )
        / avg_high
        > DOUBLE_TOLERANCE
    ):
        return None

    if (
        avg_high
        - middle["price"]
    ) / avg_high < MIN_SWING_MOVE_PCT:

        return None

    return make_pattern(
        "Double Top",
        "SHORT",
        left["index"],
        right["index"],
        middle["price"],
        s,
    )


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(swings):

    if len(swings) < 5:
        return None

    s = swings[-5:]

    if [
        x["type"] for x in s
    ] != [
        "H", "L", "H", "L", "H"
    ]:
        return None

    ls, nl1, head, nl2, rs = s

    shoulders_avg = (
        ls["price"]
        + rs["price"]
    ) / 2

    if shoulders_avg <= 0:
        return None

    shoulder_diff = (
        abs(
            ls["price"]
            - rs["price"]
        )
        / shoulders_avg
    )

    if shoulder_diff > SHOULDER_TOLERANCE:
        return None

    if (
        head["price"]
        - shoulders_avg
    ) / shoulders_avg < HEAD_MIN_DISTANCE:

        return None

    neckline = (
        nl1["price"]
        + nl2["price"]
    ) / 2

    return make_pattern(
        "Head & Shoulders",
        "SHORT",
        ls["index"],
        rs["index"],
        neckline,
        s,
    )


# ============================================================
# INVERSE H&S
# ============================================================

def detect_inverse_head_shoulders(
    swings
):

    if len(swings) < 5:
        return None

    s = swings[-5:]

    if [
        x["type"] for x in s
    ] != [
        "L", "H", "L", "H", "L"
    ]:
        return None

    ls, nl1, head, nl2, rs = s

    shoulders_avg = (
        ls["price"]
        + rs["price"]
    ) / 2

    if shoulders_avg <= 0:
        return None

    shoulder_diff = (
        abs(
            ls["price"]
            - rs["price"]
        )
        / shoulders_avg
    )

    if shoulder_diff > SHOULDER_TOLERANCE:
        return None

    if (
        shoulders_avg
        - head["price"]
    ) / shoulders_avg < HEAD_MIN_DISTANCE:

        return None

    neckline = (
        nl1["price"]
        + nl2["price"]
    ) / 2

    return make_pattern(
        "Inverse H&S",
        "LONG",
        ls["index"],
        rs["index"],
        neckline,
        s,
    )


# ============================================================
# TRIANGLE
# ============================================================

def detect_triangle(swings):

    if len(swings) < 5:
        return None

    s = swings[-5:]

    highs = [
        x for x in s
        if x["type"] == "H"
    ]

    lows = [
        x for x in s
        if x["type"] == "L"
    ]

    if (
        len(highs) < 2
        or len(lows) < 2
    ):
        return None

    first_high = highs[0]
    last_high = highs[-1]

    first_low = lows[0]
    last_low = lows[-1]

    high_change = (
        first_high["price"]
        - last_high["price"]
    ) / first_high["price"]

    low_change = (
        last_low["price"]
        - first_low["price"]
    ) / first_low["price"]

    if (
        high_change
        < MIN_SWING_MOVE_PCT
    ):
        return None

    if (
        low_change
        < MIN_SWING_MOVE_PCT
    ):
        return None

    upper = last_high["price"]
    lower = last_low["price"]

    return make_pattern(
        "Triangle",
        "BOTH",
        s[0]["index"],
        s[-1]["index"],
        (upper + lower) / 2,
        s,
        dynamic=True,
    )


# ============================================================
# WEDGE
# ============================================================

def detect_wedge(swings):

    if len(swings) < 5:
        return None

    s = swings[-5:]

    highs = [
        x for x in s
        if x["type"] == "H"
    ]

    lows = [
        x for x in s
        if x["type"] == "L"
    ]

    if (
        len(highs) < 2
        or len(lows) < 2
    ):
        return None

    h0 = highs[0]["price"]
    h1 = highs[-1]["price"]

    l0 = lows[0]["price"]
    l1 = lows[-1]["price"]

    high_change = (
        h1 - h0
    ) / h0

    low_change = (
        l1 - l0
    ) / l0

    rising = (
        high_change
        > MIN_SWING_MOVE_PCT
        and
        low_change
        > MIN_SWING_MOVE_PCT
    )

    falling = (
        high_change
        < -MIN_SWING_MOVE_PCT
        and
        low_change
        < -MIN_SWING_MOVE_PCT
    )

    if not (rising or falling):
        return None

    initial_width = abs(
        h0 - l0
    )

    final_width = abs(
        h1 - l1
    )

    if initial_width <= 0:
        return None

    if (
        final_width
        >= initial_width * 0.85
    ):
        return None

    return make_pattern(
        "Wedge",
        "BOTH",
        s[0]["index"],
        s[-1]["index"],
        (h1 + l1) / 2,
        s,
        dynamic=True,
    )


# ============================================================
# FLAG
# ============================================================

def detect_flag(
    df,
    current_index,
):

    if current_index < 40:
        return None

    impulse_start = (
        current_index - 35
    )

    impulse_end = (
        current_index - 15
    )

    start_price = df.iloc[
        impulse_start
    ]["close"]

    end_price = df.iloc[
        impulse_end
    ]["close"]

    if start_price <= 0:
        return None

    impulse_return = (
        end_price / start_price
    ) - 1

    if abs(impulse_return) < 0.03:
        return None

    consolidation = df.iloc[
        current_index - 15:
        current_index
    ]

    if len(consolidation) < 15:
        return None

    cons_high = (
        consolidation["high"].max()
    )

    cons_low = (
        consolidation["low"].min()
    )

    cons_mid = (
        cons_high + cons_low
    ) / 2

    if cons_mid <= 0:
        return None

    range_pct = (
        cons_high - cons_low
    ) / cons_mid

    if range_pct > 0.04:
        return None

    if impulse_return > 0:

        direction = "LONG"
        level = cons_high

    else:

        direction = "SHORT"
        level = cons_low

    return make_pattern(
        "Flag",
        direction,
        impulse_start,
        current_index - 1,
        level,
        [],
        dynamic=False,
    )


# ============================================================
# DETECT PATTERNS
# ============================================================

def detect_patterns(
    df,
    confirmed_highs,
    confirmed_lows,
    i,
):

    swings = (
        get_recent_alternating_swings(
            confirmed_highs,
            confirmed_lows,
            max_count=7,
        )
    )

    patterns = []

    detectors = [
        detect_double_bottom,
        detect_double_top,
        detect_head_shoulders,
        detect_inverse_head_shoulders,
        detect_triangle,
        detect_wedge,
    ]

    for detector in detectors:

        try:

            pattern = detector(
                swings
            )

            if pattern is not None:
                patterns.append(pattern)

        except Exception:
            pass

    flag = detect_flag(
        df,
        i
    )

    if flag is not None:
        patterns.append(flag)

    unique = {}

    for p in patterns:

        fingerprint = (
            p["name"],
            p["direction"],
            p["start_index"],
            p["end_index"],
            p["anchors"],
        )

        unique[fingerprint] = p

    return list(
        unique.values()
    )


# ============================================================
# BREAKOUT
# ============================================================

def breakout_signal(
    df,
    i,
    pattern,
):

    if i <= pattern["end_index"]:
        return None

    close = df.iloc[
        i
    ]["close"]

    previous_close = df.iloc[
        i - 1
    ]["close"]

    level = pattern[
        "breakout_level"
    ]

    if not is_finite(level):
        return None

    if pattern[
        "direction"
    ] == "LONG":

        if (
            previous_close <= level
            and
            close
            > level * (
                1 + BREAKOUT_BUFFER
            )
        ):
            return "LONG"

    elif pattern[
        "direction"
    ] == "SHORT":

        if (
            previous_close >= level
            and
            close
            < level * (
                1 - BREAKOUT_BUFFER
            )
        ):
            return "SHORT"

    else:

        if (
            previous_close <= level
            and
            close
            > level * (
                1 + BREAKOUT_BUFFER
            )
        ):
            return "LONG"

        if (
            previous_close >= level
            and
            close
            < level * (
                1 - BREAKOUT_BUFFER
            )
        ):
            return "SHORT"

    return None


# ============================================================
# DYNAMIC LEVEL
# ============================================================

def dynamic_level(
    df,
    pattern,
):

    if not pattern["dynamic"]:
        return pattern[
            "breakout_level"
        ]

    anchors = pattern[
        "anchors"
    ]

    if len(anchors) < 3:
        return pattern[
            "breakout_level"
        ]

    highs = []
    lows = []

    for idx in anchors:

        if idx >= len(df):
            continue

        highs.append(
            df.iloc[idx]["high"]
        )

        lows.append(
            df.iloc[idx]["low"]
        )

    if not highs or not lows:
        return pattern[
            "breakout_level"
        ]

    return (
        max(highs)
        + min(lows)
    ) / 2


# ============================================================
# FIRST RETEST + CONFIRMATION
# ============================================================

def find_first_retest_and_confirmation(
    df5,
    breakout_close_time,
    direction,
    level,
):

    candidates = df5[
        df5["timestamp"]
        >= breakout_close_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.iloc[
        :MAX_ENTRY_WAIT_5M
    ]

    retest_position = None

    # --------------------------------------------------------
    # FIRST RETEST ONLY
    # --------------------------------------------------------

    for pos in range(
        len(candidates)
    ):

        row = candidates.iloc[
            pos
        ]

        tolerance_low = (
            level
            * (
                1 - RETEST_TOLERANCE
            )
        )

        tolerance_high = (
            level
            * (
                1 + RETEST_TOLERANCE
            )
        )

        touched = (
            row["low"]
            <= tolerance_high
            and
            row["high"]
            >= tolerance_low
        )

        if not touched:
            continue

        # Long retest:
        # candle may touch level but must finish
        # on/above the broken side.
        if direction == "LONG":

            if row["close"] < level:
                continue

        # Short retest.
        else:

            if row["close"] > level:
                continue

        retest_position = pos
        break

    if retest_position is None:
        return None

    # --------------------------------------------------------
    # Confirmation must occur AFTER retest.
    # --------------------------------------------------------

    start = (
        retest_position + 1
    )

    end = min(
        start + MAX_CONFIRM_WAIT_5M,
        len(candidates)
    )

    for pos in range(
        start,
        end
    ):

        row = candidates.iloc[
            pos
        ]

        candle_range = (
            row["high"]
            - row["low"]
        )

        if candle_range <= 0:
            continue

        body = abs(
            row["close"]
            - row["open"]
        )

        body_ratio = (
            body / candle_range
        )

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            bullish = (
                row["close"]
                > row["open"]
            )

            close_above = (
                row["close"]
                >
                level * (
                    1 + BREAKOUT_BUFFER
                )
            )

            close_position = (
                row["close"]
                - row["low"]
            ) / candle_range

            strong = (
                body_ratio
                >= CONFIRM_BODY_RATIO
            )

            strong_close = (
                close_position
                >= CONFIRM_CLOSE_POSITION
            )

            if (
                bullish
                and close_above
                and strong
                and strong_close
            ):

                return {
                    "retest_time":
                        candidates.iloc[
                            retest_position
                        ]["timestamp"],

                    "entry_time":
                        row["timestamp"],

                    "entry_price":
                        row["close"],
                }

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            bearish = (
                row["close"]
                < row["open"]
            )

            close_below = (
                row["close"]
                <
                level * (
                    1 - BREAKOUT_BUFFER
                )
            )

            close_position = (
                row["high"]
                - row["close"]
            ) / candle_range

            strong = (
                body_ratio
                >= CONFIRM_BODY_RATIO
            )

            strong_close = (
                close_position
                >= CONFIRM_CLOSE_POSITION
            )

            if (
                bearish
                and close_below
                and strong
                and strong_close
            ):

                return {
                    "retest_time":
                        candidates.iloc[
                            retest_position
                        ]["timestamp"],

                    "entry_time":
                        row["timestamp"],

                    "entry_price":
                        row["close"],
                }

    return None


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df5,
    entry_position,
    direction,
    entry_price,
):

    if direction == "LONG":

        tp = (
            entry_price
            * (1 + TP_PCT)
        )

        sl = (
            entry_price
            * (1 - SL_PCT)
        )

    else:

        tp = (
            entry_price
            * (1 - TP_PCT)
        )

        sl = (
            entry_price
            * (1 + SL_PCT)
        )

    end_position = min(
        entry_position
        + 1
        + MAX_HOLD_5M,
        len(df5)
    )

    # Start from NEXT candle.
    for pos in range(
        entry_position + 1,
        end_position
    ):

        row = df5.iloc[
            pos
        ]

        high = row["high"]
        low = row["low"]

        if direction == "LONG":

            hit_tp = (
                high >= tp
            )

            hit_sl = (
                low <= sl
            )

        else:

            hit_tp = (
                low <= tp
            )

            hit_sl = (
                high >= sl
            )

        # Conservative assumption.
        if hit_tp and hit_sl:

            return {
                "result": "FAILURE",
                "exit_time":
                    row["timestamp"],
                "exit_price": sl,
            }

        if hit_sl:

            return {
                "result": "FAILURE",
                "exit_time":
                    row["timestamp"],
                "exit_price": sl,
            }

        if hit_tp:

            return {
                "result": "SUCCESS",
                "exit_time":
                    row["timestamp"],
                "exit_price": tp,
            }

    return None


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    df1,
    df5,
):

    highs, lows = find_swings(
        df1
    )

    highs_by_confirm = (
        defaultdict(list)
    )

    lows_by_confirm = (
        defaultdict(list)
    )

    for h in highs:

        highs_by_confirm[
            h["confirm_index"]
        ].append(h)

    for l in lows:

        lows_by_confirm[
            l["confirm_index"]
        ].append(l)

    confirmed_highs = []
    confirmed_lows = []

    active_patterns = {}
    dead_patterns = set()

    trades = []

    diagnostics = {
        "candidates":
            defaultdict(int),

        "breakouts":
            defaultdict(int),

        "retests":
            defaultdict(int),

        "confirmations":
            defaultdict(int),
    }

    # --------------------------------------------------------
    # Main 1H loop
    # --------------------------------------------------------

    for i in range(
        len(df1)
    ):

        # ----------------------------------------------------
        # Add only swings whose confirmation is now known.
        # ----------------------------------------------------

        for h in highs_by_confirm.get(
            i,
            []
        ):

            confirmed_highs.append(h)

        for l in lows_by_confirm.get(
            i,
            []
        ):

            confirmed_lows.append(l)

        # ----------------------------------------------------
        # Detect new patterns.
        # ----------------------------------------------------

        detected = detect_patterns(
            df1,
            confirmed_highs,
            confirmed_lows,
            i,
        )

        for pattern in detected:

            if (
                pattern["end_index"]
                >= i
            ):
                continue

            # Every swing anchor must already
            # have its right-side confirmation.
            known = True

            for anchor in pattern[
                "anchors"
            ]:

                if (
                    anchor
                    + SWING_RIGHT
                    >= i
                ):

                    known = False
                    break

            if not known:
                continue

            fingerprint = (
                pattern["name"],
                pattern["direction"],
                pattern["start_index"],
                pattern["end_index"],
                pattern["anchors"],
            )

            if (
                fingerprint
                in dead_patterns
            ):
                continue

            if (
                fingerprint
                not in active_patterns
            ):

                active_patterns[
                    fingerprint
                ] = {
                    "pattern": pattern,
                    "state":
                        "WAITING_FOR_BREAKOUT",
                }

                diagnostics[
                    "candidates"
                ][
                    pattern["name"]
                ] += 1

        # ----------------------------------------------------
        # Process active patterns.
        # ----------------------------------------------------

        for fingerprint in list(
            active_patterns.keys()
        ):

            state = active_patterns[
                fingerprint
            ]

            if (
                state["state"]
                != "WAITING_FOR_BREAKOUT"
            ):
                continue

            pattern = state[
                "pattern"
            ]

            # Expire old patterns.
            if (
                i
                - pattern["end_index"]
                > 150
            ):

                dead_patterns.add(
                    fingerprint
                )

                del active_patterns[
                    fingerprint
                ]

                continue

            direction = breakout_signal(
                df1,
                i,
                pattern,
            )

            if direction is None:
                continue

            # ------------------------------------------------
            # IMPORTANT:
            #
            # The 1H breakout becomes known only after the
            # breakout candle closes.
            #
            # Therefore 5M search starts AFTER that close.
            # ------------------------------------------------

            breakout_open_time = (
                df1.iloc[i]["timestamp"]
            )

            breakout_close_time = (
                breakout_open_time
                + pd.Timedelta(hours=1)
            )

            level = dynamic_level(
                df1,
                pattern,
            )

            pattern[
                "breakout_level"
            ] = level

            diagnostics[
                "breakouts"
            ][
                pattern["name"]
            ] += 1

            # ------------------------------------------------
            # Pattern is now single-use.
            # ------------------------------------------------

            dead_patterns.add(
                fingerprint
            )

            del active_patterns[
                fingerprint
            ]

            # ------------------------------------------------
            # First 5M retest only.
            # ------------------------------------------------

            setup = (
                find_first_retest_and_confirmation(
                    df5=df5,
                    breakout_close_time=
                        breakout_close_time,
                    direction=direction,
                    level=level,
                )
            )

            if setup is None:
                continue

            diagnostics[
                "retests"
            ][
                pattern["name"]
            ] += 1

            diagnostics[
                "confirmations"
            ][
                pattern["name"]
            ] += 1

            entry_time = setup[
                "entry_time"
            ]

            entry_price = setup[
                "entry_price"
            ]

            matching = df5.index[
                df5["timestamp"]
                == entry_time
            ]

            if len(matching) == 0:
                continue

            entry_position = int(
                matching[0]
            )

            result = simulate_trade(
                df5=df5,
                entry_position=
                    entry_position,
                direction=direction,
                entry_price=
                    entry_price,
            )

            # Unresolved trade is excluded.
            if result is None:
                continue

            trades.append({
                "symbol":
                    symbol,

                "pattern":
                    pattern["name"],

                "direction":
                    direction,

                "pattern_start":
                    df1.iloc[
                        pattern[
                            "start_index"
                        ]
                    ]["timestamp"],

                "pattern_end":
                    df1.iloc[
                        pattern[
                            "end_index"
                        ]
                    ]["timestamp"],

                "breakout_time":
                    breakout_close_time,

                "retest_time":
                    setup[
                        "retest_time"
                    ],

                "entry_time":
                    entry_time,

                "entry_price":
                    entry_price,

                "exit_time":
                    result[
                        "exit_time"
                    ],

                "exit_price":
                    result[
                        "exit_price"
                    ],

                "result":
                    result["result"],
            })

    return {
        "trades": trades,
        "diagnostics": diagnostics,
    }


# ============================================================
# RESULT REPORT
# ============================================================

def print_pattern_results(
    trades
):

    print()
    print("=" * 72)
    print("PATTERN RESULTS")
    print("=" * 72)

    if not trades:

        print(
            "No completed trades."
        )

        return

    df = pd.DataFrame(
        trades
    )

    grouped = (
        df.groupby("pattern")
        .agg(
            trades=(
                "result",
                "count"
            ),

            success=(
                "result",
                lambda x:
                    (x == "SUCCESS").sum()
            ),

            failure=(
                "result",
                lambda x:
                    (x == "FAILURE").sum()
            ),
        )
        .reset_index()
    )

    grouped[
        "success_rate"
    ] = (
        grouped["success"]
        / grouped["trades"]
        * 100
    )

    grouped = grouped.sort_values(
        "trades",
        ascending=False
    )

    for _, row in grouped.iterrows():

        print(
            f"{row['pattern']}: "
            f"{int(row['trades'])} trades | "
            f"Success {int(row['success'])} | "
            f"Failure {int(row['failure'])} | "
            f"{row['success_rate']:.2f}%"
        )


def print_symbol_results(
    trades
):

    print()
    print("=" * 72)
    print("SYMBOL RESULTS")
    print("=" * 72)

    if not trades:
        return

    df = pd.DataFrame(
        trades
    )

    grouped = (
        df.groupby("symbol")
        .agg(
            trades=(
                "result",
                "count"
            ),

            success=(
                "result",
                lambda x:
                    (x == "SUCCESS").sum()
            ),

            failure=(
                "result",
                lambda x:
                    (x == "FAILURE").sum()
            ),
        )
        .reset_index()
    )

    grouped[
        "success_rate"
    ] = (
        grouped["success"]
        / grouped["trades"]
        * 100
    )

    grouped = grouped.sort_values(
        "trades",
        ascending=False
    )

    for _, row in grouped.iterrows():

        print(
            f"{row['symbol']}: "
            f"{int(row['trades'])} trades | "
            f"Success {int(row['success'])} | "
            f"Failure {int(row['failure'])} | "
            f"{row['success_rate']:.2f}%"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 72)
    print(
        "KRAKEN FUTURES MULTI-TIMEFRAME "
        "PATTERN BACKTEST"
    )
    print("=" * 72)

    print(
        f"Period: {LOOKBACK_DAYS} days"
    )

    print(
        "Main timeframe: 1H"
    )

    print(
        "Entry timeframe: 5M"
    )

    print(
        f"TP: {TP_PCT * 100:.2f}%"
    )

    print(
        f"SL: {SL_PCT * 100:.2f}%"
    )

    print(
        "Max hold: 48 hours"
    )

    print(
        "Entry: "
        "1H Breakout -> FIRST 5M Retest "
        "-> 5M Confirmation"
    )

    print(
        "Lookahead: DISABLED"
    )

    print(
        "Real trading: DISABLED"
    )

    print()

    # ========================================================
    # SYMBOLS
    # ========================================================

    try:

        available = (
            get_available_perpetual_symbols()
        )

    except Exception as exc:

        print(
            f"Kraken connection failed: {exc}"
        )

        return

    print(
        f"Kraken perpetual symbols found: "
        f"{len(available)}"
    )

    selected = []

    missing = []

    for asset in TARGET_ASSETS:

        if asset in available:

            selected.append(
                available[asset]
            )

        else:

            missing.append(
                f"PI_{asset}USD"
            )

    print()
    print(
        "FIXED PREFERRED UNIVERSE"
    )

    print("-" * 72)

    for symbol in selected:
        print(symbol)

    if missing:

        print()
        print(
            "NOT AVAILABLE FROM CURRENT "
            "KRAKEN PUBLIC DISCOVERY:"
        )

        for symbol in missing:
            print(symbol)

    print()
    print(
        f"Selected symbols: "
        f"{len(selected)}"
    )

    if not selected:

        print(
            "No usable perpetual symbols."
        )

        return

    # ========================================================
    # GLOBAL RESULTS
    # ========================================================

    all_trades = []

    global_candidates = (
        defaultdict(int)
    )

    global_breakouts = (
        defaultdict(int)
    )

    global_retests = (
        defaultdict(int)
    )

    global_confirmations = (
        defaultdict(int)
    )

    tested_symbols = []

    # ========================================================
    # BACKTEST EACH SYMBOL
    # ========================================================

    for symbol in selected:

        print()
        print("-" * 72)
        print(
            f"LOADING {symbol}"
        )
        print("-" * 72)

        try:

            # ------------------------------------------------
            # FULL 1 YEAR 1H
            # ------------------------------------------------

            df1 = fetch_candles(
                symbol,
                INTERVAL_1H,
                LOOKBACK_DAYS,
            )

            # ------------------------------------------------
            # FULL 1 YEAR 5M
            # ------------------------------------------------

            df5 = fetch_candles(
                symbol,
                INTERVAL_5M,
                LOOKBACK_DAYS,
            )

            # ------------------------------------------------
            # Validate
            # ------------------------------------------------

            if not validate_candles(
                df1,
                INTERVAL_1H,
            ):

                print(
                    f"{symbol}: "
                    "1H data validation failed."
                )

                continue

            if not validate_candles(
                df5,
                INTERVAL_5M,
            ):

                print(
                    f"{symbol}: "
                    "5M data validation failed."
                )

                continue

            print(
                f"{symbol} "
                f"1H={len(df1):,} "
                f"5M={len(df5):,}"
            )

            # ------------------------------------------------
            # Important sanity check.
            # ------------------------------------------------

            expected_1h = (
                LOOKBACK_DAYS * 24
            )

            expected_5m = (
                LOOKBACK_DAYS
                * 24
                * 12
            )

            if (
                len(df1)
                < expected_1h * 0.90
            ):

                print(
                    f"WARNING: {symbol} "
                    f"1H history is materially "
                    f"shorter than one year."
                )

            if (
                len(df5)
                < expected_5m * 0.90
            ):

                print(
                    f"WARNING: {symbol} "
                    f"5M history is materially "
                    f"shorter than one year."
                )

            # ------------------------------------------------
            # Run
            # ------------------------------------------------

            result = backtest_symbol(
                symbol,
                df1,
                df5,
            )

            trades = result[
                "trades"
            ]

            all_trades.extend(
                trades
            )

            diagnostics = result[
                "diagnostics"
            ]

            for key, value in (
                diagnostics[
                    "candidates"
                ].items()
            ):

                global_candidates[
                    key
                ] += value

            for key, value in (
                diagnostics[
                    "breakouts"
                ].items()
            ):

                global_breakouts[
                    key
                ] += value

            for key, value in (
                diagnostics[
                    "retests"
                ].items()
            ):

                global_retests[
                    key
                ] += value

            for key, value in (
                diagnostics[
                    "confirmations"
                ].items()
            ):

                global_confirmations[
                    key
                ] += value

            tested_symbols.append(
                symbol
            )

            print(
                f"{symbol} completed trades: "
                f"{len(trades)}"
            )

        except Exception as exc:

            print(
                f"{symbol} ERROR: {exc}"
            )

    # ========================================================
    # FINAL STATS
    # ========================================================

    total = len(
        all_trades
    )

    success = sum(
        1
        for x in all_trades
        if x["result"]
        == "SUCCESS"
    )

    failure = sum(
        1
        for x in all_trades
        if x["result"]
        == "FAILURE"
    )

    if total > 0:

        success_rate = (
            success
            / total
            * 100
        )

        failure_rate = (
            failure
            / total
            * 100
        )

    else:

        success_rate = 0.0
        failure_rate = 0.0

    # ========================================================
    # FINAL RESULT
    # ========================================================

    print()
    print()
    print("=" * 72)
    print("FINAL RESULT")
    print("=" * 72)

    print(
        f"SYMBOLS TESTED: "
        f"{len(tested_symbols)}"
    )

    print(
        f"TOTAL TRADES: "
        f"{total}"
    )

    print(
        f"SUCCESS: "
        f"{success}"
    )

    print(
        f"FAILURE: "
        f"{failure}"
    )

    print(
        f"SUCCESS RATE: "
        f"{success_rate:.2f}%"
    )

    print(
        f"FAILURE RATE: "
        f"{failure_rate:.2f}%"
    )

    # ========================================================
    # RAW BREAK EVEN
    # ========================================================

    breakeven = (
        SL_PCT
        / (
            TP_PCT
            + SL_PCT
        )
        * 100
    )

    print()
    print(
        f"RAW BREAK-EVEN: "
        f"{breakeven:.2f}%"
    )

    print(
        f"EDGE VS RAW BREAK-EVEN: "
        f"{success_rate - breakeven:+.2f} pp"
    )

    # ========================================================
    # RESULTS
    # ========================================================

    print_pattern_results(
        all_trades
    )

    print_symbol_results(
        all_trades
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    print()
    print("=" * 72)
    print(
        "PATTERN LIFECYCLE DIAGNOSTICS"
    )
    print("=" * 72)

    pattern_names = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag",
    ]

    for name in pattern_names:

        print(
            f"{name}: "
            f"Candidates="
            f"{global_candidates[name]} | "
            f"Breakouts="
            f"{global_breakouts[name]} | "
            f"Retests="
            f"{global_retests[name]} | "
            f"Confirmations="
            f"{global_confirmations[name]}"
        )

    # ========================================================
    # DATA SANITY
    # ========================================================

    print()
    print("=" * 72)
    print(
        "DATA SANITY CHECK"
    )
    print("=" * 72)

    print(
        "Expected 1H candles/year: "
        f"~{LOOKBACK_DAYS * 24:,}"
    )

    print(
        "Expected 5M candles/year: "
        f"~{LOOKBACK_DAYS * 24 * 12:,}"
    )

    print(
        "Historical candle download: "
        "PAGINATED"
    )

    print(
        "Current-volume universe bias: "
        "DISABLED"
    )

    print(
        "1H breakout candle reused as "
        "5M retest: NO"
    )

    print(
        "Multiple trades from same pattern: "
        "NO"
    )

    print(
        "Unresolved trades counted: "
        "NO"
    )

    print(
        "Same-candle TP/SL assumption: "
        "SL FIRST"
    )

    print()
    print("=" * 72)
    print(
        "BACKTEST COMPLETE"
    )
    print("=" * 72)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
