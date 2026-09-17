# ============================================================
# KRAKEN FUTURES MULTI-TIMEFRAME PATTERN BACKTEST
# 1H PATTERN -> 1H BREAKOUT -> FIRST 5M RETEST -> CONFIRMATION
#
# VERSION: 2.0
#
# IMPORTANT:
# - Kraken Futures
# - Fixed 20-symbol universe
# - Closed candles only
# - No lookahead
# - One trade maximum per pattern
# - First retest only
# - TP = 2%
# - SL = 1%
# - Max hold = 48 hours
# - Same candle TP + SL => SL first => FAILURE
# - Unresolved trades are excluded
# - No real trading
# ============================================================

import time
import math
import requests
import pandas as pd
import numpy as np
from collections import defaultdict

# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TICKERS_ENDPOINT = "/derivatives/api/v3/tickers"
CANDLE_ENDPOINT = "/api/charts/v1/trade"

INTERVAL_1H = "1h"
INTERVAL_5M = "5m"

LOOKBACK_DAYS = 365

TP_PCT = 0.02
SL_PCT = 0.01

MAX_HOLD_5M = 576          # 48 hours
MAX_ENTRY_WAIT_5M = 72     # 6 hours
MAX_CONFIRM_WAIT_5M = 6    # 30 minutes

# ============================================================
# FIXED UNIVERSE
# ============================================================
#
# We deliberately DO NOT select symbols by today's volume.
# That would introduce historical universe/survivorship bias.
#
# Kraken uses XBT for Bitcoin futures.
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
# NETWORK
# ============================================================

REQUEST_TIMEOUT = 30
MAX_RETRIES = 4

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Kraken-Pattern-Backtest/2.0"
})


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=np.nan):
    try:
        return float(value)
    except Exception:
        return default


def pct(a, b):
    if b == 0 or pd.isna(a) or pd.isna(b):
        return np.nan
    return (a / b) - 1.0


def is_finite(value):
    return value is not None and np.isfinite(value)


# ============================================================
# HTTP
# ============================================================

def get_json(path, params=None):
    url = BASE_URL + path

    last_error = None

    for attempt in range(MAX_RETRIES):
        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise RuntimeError("Unexpected Kraken response")

            return data

        except Exception as exc:
            last_error = exc

            if attempt < MAX_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))

    raise RuntimeError(
        f"Kraken request failed: {url} | {last_error}"
    )


# ============================================================
# SYMBOL DISCOVERY
# ============================================================

def get_available_symbols():
    data = get_json(TICKERS_ENDPOINT)

    tickers = data.get("tickers", [])

    available = {}

    for item in tickers:

        symbol = item.get("symbol", "")
        tag = str(item.get("tag", "")).lower()

        if not symbol:
            continue

        if tag != "perpetual":
            continue

        for asset in TARGET_ASSETS:

            expected = f"PI_{asset}USD"

            if symbol == expected:
                available[asset] = symbol

    return available


# ============================================================
# CANDLE PARSING
# ============================================================

def parse_candle_row(row):
    """
    Kraken chart responses can vary slightly between versions.
    Handle common list/dict formats.
    """

    if isinstance(row, dict):

        ts = (
            row.get("time")
            or row.get("timestamp")
            or row.get("t")
        )

        o = row.get("open", row.get("o"))
        h = row.get("high", row.get("h"))
        l = row.get("low", row.get("l"))
        c = row.get("close", row.get("c"))
        v = row.get("volume", row.get("v", 0))

        return ts, o, h, l, c, v

    if isinstance(row, (list, tuple)):

        if len(row) >= 6:
            return (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5]
            )

    return None


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(symbol, interval, days):
    """
    Fetch historical candles.

    Kraken's chart endpoint is used directly.
    The endpoint returns a historical series, so we normalize,
    sort, deduplicate and trim it locally.
    """

    path = f"{CANDLE_ENDPOINT}/{symbol}/{interval}"

    data = get_json(path)

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

            # Kraken timestamps are normally seconds.
            # Protect against millisecond timestamps.
            if ts > 10_000_000_000:
                ts /= 1000.0

            rows.append({
                "timestamp": pd.to_datetime(
                    ts,
                    unit="s",
                    utc=True
                ),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            })

        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = (
        df
        .drop_duplicates(subset=["timestamp"])
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # Keep approximately requested period.
    end_time = df["timestamp"].max()
    start_time = end_time - pd.Timedelta(days=days)

    df = df[df["timestamp"] >= start_time].copy()

    # Remove incomplete final candle.
    now = pd.Timestamp.now(tz="UTC")

    interval_delta = (
        pd.Timedelta(hours=1)
        if interval == "1h"
        else pd.Timedelta(minutes=5)
    )

    df = df[
        df["timestamp"] + interval_delta <= now
    ].copy()

    df = df.reset_index(drop=True)

    return df


# ============================================================
# SWINGS
# ============================================================

def find_swings(df):
    """
    Confirmed pivots.

    A swing at index i becomes known only after
    SWING_RIGHT candles have closed.

    We explicitly track the confirmation index.
    """

    highs = []
    lows = []

    for i in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT
    ):

        current_high = df.iloc[i]["high"]
        current_low = df.iloc[i]["low"]

        left_highs = df.iloc[
            i - SWING_LEFT:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:i + SWING_RIGHT + 1
        ]["high"]

        left_lows = df.iloc[
            i - SWING_LEFT:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:i + SWING_RIGHT + 1
        ]["low"]

        if (
            current_high > left_highs.max()
            and current_high >= right_highs.max()
        ):
            highs.append({
                "index": i,
                "price": current_high,
                "confirm_index": i + SWING_RIGHT,
                "type": "H",
            })

        if (
            current_low < left_lows.min()
            and current_low <= right_lows.min()
        ):
            lows.append({
                "index": i,
                "price": current_low,
                "confirm_index": i + SWING_RIGHT,
                "type": "L",
            })

    return highs, lows


# ============================================================
# SWING SEQUENCE
# ============================================================

def get_recent_alternating_swings(highs, lows, max_count=7):
    combined = highs + lows

    combined = sorted(
        combined,
        key=lambda x: x["index"]
    )

    result = []

    for swing in combined:

        if not result:
            result.append(swing)
            continue

        if swing["type"] == result[-1]["type"]:

            # Keep the more extreme same-type swing.
            if swing["type"] == "H":
                if swing["price"] > result[-1]["price"]:
                    result[-1] = swing

            else:
                if swing["price"] < result[-1]["price"]:
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
        "breakout_level": float(breakout_level),
        "anchors": tuple(
            int(x["index"]) for x in anchors
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

    if [x["type"] for x in s] != ["L", "H", "L"]:
        return None

    left = s[0]
    middle = s[1]
    right = s[2]

    if right["index"] - left["index"] < MIN_PATTERN_DISTANCE:
        return None

    if right["index"] - left["index"] > MAX_PATTERN_DISTANCE:
        return None

    avg_low = (left["price"] + right["price"]) / 2

    if avg_low <= 0:
        return None

    if (
        abs(left["price"] - right["price"])
        / avg_low
        > DOUBLE_TOLERANCE
    ):
        return None

    if (
        (middle["price"] - avg_low)
        / avg_low
        < MIN_SWING_MOVE_PCT
    ):
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

    if [x["type"] for x in s] != ["H", "L", "H"]:
        return None

    left = s[0]
    middle = s[1]
    right = s[2]

    if right["index"] - left["index"] < MIN_PATTERN_DISTANCE:
        return None

    if right["index"] - left["index"] > MAX_PATTERN_DISTANCE:
        return None

    avg_high = (left["price"] + right["price"]) / 2

    if avg_high <= 0:
        return None

    if (
        abs(left["price"] - right["price"])
        / avg_high
        > DOUBLE_TOLERANCE
    ):
        return None

    if (
        (avg_high - middle["price"])
        / avg_high
        < MIN_SWING_MOVE_PCT
    ):
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

    if [x["type"] for x in s] != [
        "H", "L", "H", "L", "H"
    ]:
        return None

    left_shoulder = s[0]
    neckline_left = s[1]
    head = s[2]
    neckline_right = s[3]
    right_shoulder = s[4]

    shoulders_avg = (
        left_shoulder["price"]
        + right_shoulder["price"]
    ) / 2

    if shoulders_avg <= 0:
        return None

    shoulder_diff = (
        abs(
            left_shoulder["price"]
            - right_shoulder["price"]
        )
        / shoulders_avg
    )

    if shoulder_diff > SHOULDER_TOLERANCE:
        return None

    if (
        (head["price"] - shoulders_avg)
        / shoulders_avg
        < HEAD_MIN_DISTANCE
    ):
        return None

    neckline = (
        neckline_left["price"]
        + neckline_right["price"]
    ) / 2

    if (
        right_shoulder["index"]
        - left_shoulder["index"]
        < MIN_PATTERN_DISTANCE
    ):
        return None

    return make_pattern(
        "Head & Shoulders",
        "SHORT",
        left_shoulder["index"],
        right_shoulder["index"],
        neckline,
        s,
    )


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(swings):

    if len(swings) < 5:
        return None

    s = swings[-5:]

    if [x["type"] for x in s] != [
        "L", "H", "L", "H", "L"
    ]:
        return None

    left_shoulder = s[0]
    neckline_left = s[1]
    head = s[2]
    neckline_right = s[3]
    right_shoulder = s[4]

    shoulders_avg = (
        left_shoulder["price"]
        + right_shoulder["price"]
    ) / 2

    if shoulders_avg <= 0:
        return None

    shoulder_diff = (
        abs(
            left_shoulder["price"]
            - right_shoulder["price"]
        )
        / shoulders_avg
    )

    if shoulder_diff > SHOULDER_TOLERANCE:
        return None

    if (
        (shoulders_avg - head["price"])
        / shoulders_avg
        < HEAD_MIN_DISTANCE
    ):
        return None

    neckline = (
        neckline_left["price"]
        + neckline_right["price"]
    ) / 2

    if (
        right_shoulder["index"]
        - left_shoulder["index"]
        < MIN_PATTERN_DISTANCE
    ):
        return None

    return make_pattern(
        "Inverse H&S",
        "LONG",
        left_shoulder["index"],
        right_shoulder["index"],
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

    if [x["type"] for x in s] != [
        "H", "L", "H", "L", "H"
    ]:
        # Try inverse sequence
        if [x["type"] for x in s] != [
            "L", "H", "L", "H", "L"
        ]:
            return None

    highs = [x for x in s if x["type"] == "H"]
    lows = [x for x in s if x["type"] == "L"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    first_high = highs[0]
    last_high = highs[-1]

    first_low = lows[0]
    last_low = lows[-1]

    descending_highs = (
        last_high["price"] < first_high["price"]
    )

    ascending_lows = (
        last_low["price"] > first_low["price"]
    )

    if not (descending_highs and ascending_lows):
        return None

    high_change = (
        first_high["price"]
        - last_high["price"]
    ) / first_high["price"]

    low_change = (
        last_low["price"]
        - first_low["price"]
    ) / first_low["price"]

    if high_change < MIN_SWING_MOVE_PCT:
        return None

    if low_change < MIN_SWING_MOVE_PCT:
        return None

    # For a symmetrical triangle, direction is decided
    # by the eventual breakout.
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

    highs = [x for x in s if x["type"] == "H"]
    lows = [x for x in s if x["type"] == "L"]

    if len(highs) < 2 or len(lows) < 2:
        return None

    h0 = highs[0]["price"]
    h1 = highs[-1]["price"]

    l0 = lows[0]["price"]
    l1 = lows[-1]["price"]

    high_change = (h1 - h0) / h0
    low_change = (l1 - l0) / l0

    rising = (
        high_change > MIN_SWING_MOVE_PCT
        and low_change > MIN_SWING_MOVE_PCT
    )

    falling = (
        high_change < -MIN_SWING_MOVE_PCT
        and low_change < -MIN_SWING_MOVE_PCT
    )

    if not (rising or falling):
        return None

    # Convergence requirement.
    initial_width = abs(h0 - l0)
    final_width = abs(h1 - l1)

    if initial_width <= 0:
        return None

    if final_width >= initial_width * 0.85:
        return None

    if rising:
        # Rising wedge tends to break downward,
        # but breakout direction is still confirmed by price.
        direction = "BOTH"
    else:
        direction = "BOTH"

    return make_pattern(
        "Wedge",
        direction,
        s[0]["index"],
        s[-1]["index"],
        (h1 + l1) / 2,
        s,
        dynamic=True,
    )


# ============================================================
# FLAG
# ============================================================

def detect_flag(df, current_index):

    # Need enough history.
    if current_index < 40:
        return None

    impulse_start = current_index - 35
    impulse_end = current_index - 15

    start_price = df.iloc[impulse_start]["close"]
    end_price = df.iloc[impulse_end]["close"]

    if start_price <= 0:
        return None

    impulse_return = (
        end_price / start_price
    ) - 1.0

    if abs(impulse_return) < 0.03:
        return None

    consolidation = df.iloc[
        current_index - 15:current_index
    ]

    if len(consolidation) < 15:
        return None

    cons_high = consolidation["high"].max()
    cons_low = consolidation["low"].min()

    cons_mid = (
        consolidation["high"].mean()
        + consolidation["low"].mean()
    ) / 2

    if cons_mid <= 0:
        return None

    range_pct = (
        cons_high - cons_low
    ) / cons_mid

    # Consolidation should be materially tighter than impulse.
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
# PATTERN DETECTION
# ============================================================

def detect_patterns(df, confirmed_highs, confirmed_lows, i):

    swings = get_recent_alternating_swings(
        confirmed_highs,
        confirmed_lows,
        max_count=7
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
            pattern = detector(swings)

            if pattern is not None:
                patterns.append(pattern)

        except Exception:
            continue

    flag = detect_flag(df, i)

    if flag is not None:
        patterns.append(flag)

    # Remove duplicate fingerprints.
    unique = {}

    for p in patterns:

        fingerprint = (
            p["name"],
            p["start_index"],
            p["end_index"],
            p["anchors"],
        )

        unique[fingerprint] = p

    return list(unique.values())


# ============================================================
# BREAKOUT CHECK
# ============================================================

def breakout_signal(df, i, pattern):

    if i <= pattern["end_index"]:
        return None

    close = df.iloc[i]["close"]

    previous_close = df.iloc[i - 1]["close"]

    level = pattern["breakout_level"]

    if not is_finite(level):
        return None

    buffer = BREAKOUT_BUFFER

    if pattern["direction"] == "LONG":

        if (
            previous_close <= level
            and close > level * (1 + buffer)
        ):
            return "LONG"

    elif pattern["direction"] == "SHORT":

        if (
            previous_close >= level
            and close < level * (1 - buffer)
        ):
            return "SHORT"

    else:
        # Triangle / Wedge.
        if (
            previous_close <= level
            and close > level * (1 + buffer)
        ):
            return "LONG"

        if (
            previous_close >= level
            and close < level * (1 - buffer)
        ):
            return "SHORT"

    return None


# ============================================================
# DYNAMIC BREAKOUT LEVEL
# ============================================================

def dynamic_level(df, pattern, current_i):

    if not pattern["dynamic"]:
        return pattern["breakout_level"]

    anchors = pattern["anchors"]

    if len(anchors) < 3:
        return pattern["breakout_level"]

    recent = [
        df.iloc[idx]
        for idx in anchors
        if idx < len(df)
    ]

    highs = [
        x["high"]
        for x in recent
        if pd.notna(x["high"])
    ]

    lows = [
        x["low"]
        for x in recent
        if pd.notna(x["low"])
    ]

    if not highs or not lows:
        return pattern["breakout_level"]

    return (
        max(highs) + min(lows)
    ) / 2


# ============================================================
# 5M RETEST + CONFIRMATION
# ============================================================

def find_first_retest_and_confirmation(
    df5,
    breakout_close_time,
    direction,
    level,
):
    """
    IMPORTANT:
    Search starts AFTER the 1H breakout candle has CLOSED.

    This avoids the major temporal overlap problem in the
    previous version.
    """

    candidates = df5[
        df5["timestamp"] >= breakout_close_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.iloc[
        :MAX_ENTRY_WAIT_5M
    ]

    retest_index = None

    for idx, row in candidates.iterrows():

        tolerance_low = level * (
            1 - RETEST_TOLERANCE
        )

        tolerance_high = level * (
            1 + RETEST_TOLERANCE
        )

        touched = (
            row["low"] <= tolerance_high
            and row["high"] >= tolerance_low
        )

        if not touched:
            continue

        # Retest must respect the broken side.
        if direction == "LONG":

            if row["close"] < level:
                continue

        else:

            if row["close"] > level:
                continue

        retest_index = idx
        break

    if retest_index is None:
        return None

    # Confirmation must happen AFTER retest.
    start_position = df5.index.get_loc(
        retest_index
    ) + 1

    end_position = min(
        start_position + MAX_CONFIRM_WAIT_5M,
        len(df5)
    )

    for pos in range(
        start_position,
        end_position
    ):

        row = df5.iloc[pos]

        candle_range = (
            row["high"] - row["low"]
        )

        if candle_range <= 0:
            continue

        body = abs(
            row["close"] - row["open"]
        )

        body_ratio = body / candle_range

        close_position = (
            row["close"] - row["low"]
        ) / candle_range

        # LONG confirmation
        if direction == "LONG":

            bullish = row["close"] > row["open"]

            close_above_level = (
                row["close"]
                > level * (1 + BREAKOUT_BUFFER)
            )

            strong_body = (
                body_ratio >= CONFIRM_BODY_RATIO
            )

            strong_close = (
                close_position >= CONFIRM_CLOSE_POSITION
            )

            if (
                bullish
                and close_above_level
                and strong_body
                and strong_close
            ):
                return {
                    "retest_time": df5.loc[
                        retest_index,
                        "timestamp"
                    ],
                    "entry_time": row["timestamp"],
                    "entry_price": row["close"],
                }

        # SHORT confirmation
        else:

            bearish = row["close"] < row["open"]

            close_below_level = (
                row["close"]
                < level * (1 - BREAKOUT_BUFFER)
            )

            strong_body = (
                body_ratio >= CONFIRM_BODY_RATIO
            )

            close_position_short = (
                (row["high"] - row["close"])
                / candle_range
            )

            strong_close = (
                close_position_short
                >= CONFIRM_CLOSE_POSITION
            )

            if (
                bearish
                and close_below_level
                and strong_body
                and strong_close
            ):
                return {
                    "retest_time": df5.loc[
                        retest_index,
                        "timestamp"
                    ],
                    "entry_time": row["timestamp"],
                    "entry_price": row["close"],
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
    """
    Entry candle itself is NOT used to hit TP/SL.

    Simulation starts from the NEXT 5M candle.
    """

    if direction == "LONG":

        tp = entry_price * (
            1 + TP_PCT
        )

        sl = entry_price * (
            1 - SL_PCT
        )

    else:

        tp = entry_price * (
            1 - TP_PCT
        )

        sl = entry_price * (
            1 + SL_PCT
        )

    end_position = min(
        entry_position + 1 + MAX_HOLD_5M,
        len(df5)
    )

    for pos in range(
        entry_position + 1,
        end_position
    ):

        row = df5.iloc[pos]

        high = row["high"]
        low = row["low"]

        if direction == "LONG":

            hit_tp = high >= tp
            hit_sl = low <= sl

        else:

            hit_tp = low <= tp
            hit_sl = high >= sl

        # Conservative assumption:
        # if both are touched in same candle,
        # SL is considered first.
        if hit_tp and hit_sl:
            return {
                "result": "FAILURE",
                "exit_time": row["timestamp"],
                "exit_price": sl,
            }

        if hit_sl:
            return {
                "result": "FAILURE",
                "exit_time": row["timestamp"],
                "exit_price": sl,
            }

        if hit_tp:
            return {
                "result": "SUCCESS",
                "exit_time": row["timestamp"],
                "exit_price": tp,
            }

    return None


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(symbol, df1, df5):

    highs, lows = find_swings(df1)

    highs_by_confirm = defaultdict(list)
    lows_by_confirm = defaultdict(list)

    for h in highs:
        highs_by_confirm[h["confirm_index"]].append(h)

    for l in lows:
        lows_by_confirm[l["confirm_index"]].append(l)

    confirmed_highs = []
    confirmed_lows = []

    active_patterns = {}
    dead_fingerprints = set()

    trades = []

    # Diagnostics.
    pattern_candidates = defaultdict(int)
    breakouts = defaultdict(int)
    retests = defaultdict(int)
    confirmations = defaultdict(int)

    for i in range(len(df1)):

        # ----------------------------------------------------
        # Add only swings whose confirmation is now available.
        # ----------------------------------------------------

        for h in highs_by_confirm.get(i, []):
            confirmed_highs.append(h)

        for l in lows_by_confirm.get(i, []):
            confirmed_lows.append(l)

        # ----------------------------------------------------
        # Generate new patterns only from confirmed swings.
        # ----------------------------------------------------

        detected = detect_patterns(
            df1,
            confirmed_highs,
            confirmed_lows,
            i
        )

        for pattern in detected:

            # Pattern cannot use current candle as an anchor.
            if pattern["end_index"] >= i:
                continue

            # All swing confirmations must already be known.
            anchors_are_known = True

            for anchor in pattern["anchors"]:

                if anchor + SWING_RIGHT >= i:
                    anchors_are_known = False
                    break

            if not anchors_are_known:
                continue

            fingerprint = (
                pattern["name"],
                pattern["direction"],
                pattern["start_index"],
                pattern["end_index"],
                pattern["anchors"],
            )

            if fingerprint in dead_fingerprints:
                continue

            if fingerprint not in active_patterns:

                active_patterns[fingerprint] = {
                    "pattern": pattern,
                    "state": "WAITING_FOR_BREAKOUT",
                    "created_index": i,
                }

                pattern_candidates[
                    pattern["name"]
                ] += 1

        # ----------------------------------------------------
        # Process active patterns.
        # ----------------------------------------------------

        for fingerprint in list(active_patterns.keys()):

            state = active_patterns[fingerprint]

            if state["state"] != "WAITING_FOR_BREAKOUT":
                continue

            pattern = state["pattern"]

            # Pattern is too old.
            if (
                i - pattern["end_index"]
                > 150
            ):
                state["state"] = "DEAD"
                dead_fingerprints.add(fingerprint)
                del active_patterns[fingerprint]
                continue

            direction = breakout_signal(
                df1,
                i,
                pattern
            )

            if direction is None:
                continue

            # ------------------------------------------------
            # Breakout candle is known only at its CLOSE.
            # Therefore 5M search starts at:
            #
            # 1H candle timestamp + 1 hour
            # ------------------------------------------------

            breakout_time = df1.iloc[
                i
            ]["timestamp"]

            breakout_close_time = (
                breakout_time
                + pd.Timedelta(hours=1)
            )

            # For dynamic patterns, use the current level.
            level = dynamic_level(
                df1,
                pattern,
                i
            )

            pattern["breakout_level"] = level

            breakouts[
                pattern["name"]
            ] += 1

            state["state"] = "BROKEN"

            # ------------------------------------------------
            # Find FIRST 5M retest.
            # ------------------------------------------------

            setup = find_first_retest_and_confirmation(
                df5=df5,
                breakout_close_time=breakout_close_time,
                direction=direction,
                level=level,
            )

            # Pattern is now single-use regardless of result.
            state["state"] = "DEAD"

            dead_fingerprints.add(fingerprint)

            del active_patterns[fingerprint]

            if setup is None:
                continue

            retests[
                pattern["name"]
            ] += 1

            confirmations[
                pattern["name"]
            ] += 1

            entry_time = setup["entry_time"]
            entry_price = setup["entry_price"]

            # Find exact 5M entry candle.
            entry_rows = df5.index[
                df5["timestamp"] == entry_time
            ]

            if len(entry_rows) == 0:
                continue

            entry_index = int(
                entry_rows[0]
            )

            result = simulate_trade(
                df5=df5,
                entry_position=entry_index,
                direction=direction,
                entry_price=entry_price,
            )

            if result is None:
                # Unresolved trade is excluded.
                continue

            trades.append({
                "symbol": symbol,
                "pattern": pattern["name"],
                "direction": direction,
                "pattern_start": df1.iloc[
                    pattern["start_index"]
                ]["timestamp"],
                "pattern_end": df1.iloc[
                    pattern["end_index"]
                ]["timestamp"],
                "breakout_time": breakout_close_time,
                "retest_time": setup["retest_time"],
                "entry_time": entry_time,
                "entry_price": entry_price,
                "exit_time": result["exit_time"],
                "exit_price": result["exit_price"],
                "result": result["result"],
            })

    return {
        "trades": trades,
        "pattern_candidates": pattern_candidates,
        "breakouts": breakouts,
        "retests": retests,
        "confirmations": confirmations,
    }


# ============================================================
# AGGREGATION
# ============================================================

def print_pattern_results(all_trades):

    print()
    print("=" * 72)
    print("PATTERN RESULTS")
    print("=" * 72)

    if not all_trades:
        print("No completed trades.")
        return

    df = pd.DataFrame(all_trades)

    grouped = (
        df.groupby("pattern")
        .agg(
            trades=("result", "count"),
            success=("result", lambda x: (x == "SUCCESS").sum()),
            failure=("result", lambda x: (x == "FAILURE").sum()),
        )
        .reset_index()
    )

    grouped["success_rate"] = (
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


def print_symbol_results(all_trades):

    print()
    print("=" * 72)
    print("SYMBOL RESULTS")
    print("=" * 72)

    if not all_trades:
        return

    df = pd.DataFrame(all_trades)

    grouped = (
        df.groupby("symbol")
        .agg(
            trades=("result", "count"),
            success=("result", lambda x: (x == "SUCCESS").sum()),
            failure=("result", lambda x: (x == "FAILURE").sum()),
        )
        .reset_index()
    )

    grouped["success_rate"] = (
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
    print("KRAKEN FUTURES MULTI-TIMEFRAME PATTERN BACKTEST")
    print("=" * 72)

    print(
        f"Period: last {LOOKBACK_DAYS} days"
    )

    print("Main timeframe: 1H")
    print("Entry timeframe: 5M")
    print(f"TP: {TP_PCT * 100:.2f}%")
    print(f"SL: {SL_PCT * 100:.2f}%")
    print("Max hold: 48 hours")
    print(
        "Entry: 1H Breakout -> FIRST 5M Retest -> 5M Confirmation"
    )
    print("Lookahead: DISABLED")
    print("Real trading: DISABLED")
    print()

    # --------------------------------------------------------
    # Kraken symbols
    # --------------------------------------------------------

    try:
        available = get_available_symbols()

    except Exception as exc:
        print(f"Kraken connection failed: {exc}")
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
    print("FIXED TEST UNIVERSE")
    print("-" * 72)

    for symbol in selected:
        print(symbol)

    if missing:
        print()
        print("NOT AVAILABLE ON KRAKEN:")
        for symbol in missing:
            print(symbol)

    print()
    print(
        f"Selected symbols: {len(selected)}"
    )

    if not selected:
        print("No valid symbols found.")
        return

    # --------------------------------------------------------
    # Global containers
    # --------------------------------------------------------

    all_trades = []

    global_candidates = defaultdict(int)
    global_breakouts = defaultdict(int)
    global_retests = defaultdict(int)
    global_confirmations = defaultdict(int)

    symbol_summary = {}

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    for symbol in selected:

        print()
        print("-" * 72)
        print(f"LOADING {symbol}")
        print("-" * 72)

        try:

            df1 = fetch_candles(
                symbol,
                INTERVAL_1H,
                LOOKBACK_DAYS
            )

            df5 = fetch_candles(
                symbol,
                INTERVAL_5M,
                LOOKBACK_DAYS
            )

            if df1.empty or df5.empty:
                print(
                    f"{symbol}: insufficient candle data"
                )
                continue

            print(
                f"{symbol} "
                f"1H={len(df1):,} "
                f"5M={len(df5):,}"
            )

            result = backtest_symbol(
                symbol,
                df1,
                df5
            )

            trades = result["trades"]

            all_trades.extend(trades)

            for key, value in result[
                "pattern_candidates"
            ].items():
                global_candidates[key] += value

            for key, value in result[
                "breakouts"
            ].items():
                global_breakouts[key] += value

            for key, value in result[
                "retests"
            ].items():
                global_retests[key] += value

            for key, value in result[
                "confirmations"
            ].items():
                global_confirmations[key] += value

            symbol_summary[symbol] = len(trades)

            print(
                f"{symbol} completed trades: "
                f"{len(trades)}"
            )

        except Exception as exc:

            print(
                f"{symbol} ERROR: {exc}"
            )

    # ========================================================
    # FINAL RESULTS
    # ========================================================

    total = len(all_trades)

    success = sum(
        1
        for x in all_trades
        if x["result"] == "SUCCESS"
    )

    failure = sum(
        1
        for x in all_trades
        if x["result"] == "FAILURE"
    )

    if total > 0:
        success_rate = (
            success / total * 100
        )

        failure_rate = (
            failure / total * 100
        )

    else:
        success_rate = 0.0
        failure_rate = 0.0

    print()
    print()
    print("=" * 72)
    print("FINAL RESULT")
    print("=" * 72)

    print(
        f"SYMBOLS TESTED: {len(symbol_summary)}"
    )

    print(
        f"TOTAL TRADES: {total}"
    )

    print(
        f"SUCCESS: {success}"
    )

    print(
        f"FAILURE: {failure}"
    )

    print(
        f"SUCCESS RATE: {success_rate:.2f}%"
    )

    print(
        f"FAILURE RATE: {failure_rate:.2f}%"
    )

    # --------------------------------------------------------
    # Raw mathematical break-even
    # --------------------------------------------------------

    breakeven = (
        SL_PCT
        / (TP_PCT + SL_PCT)
        * 100
    )

    print()
    print(
        f"RAW BREAK-EVEN: {breakeven:.2f}%"
    )

    print(
        f"EDGE VS RAW BREAK-EVEN: "
        f"{success_rate - breakeven:+.2f} pp"
    )

    # --------------------------------------------------------
    # Pattern
    # --------------------------------------------------------

    print_pattern_results(
        all_trades
    )

    # --------------------------------------------------------
    # Symbol
    # --------------------------------------------------------

    print_symbol_results(
        all_trades
    )

    # --------------------------------------------------------
    # Compact diagnostics
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("PATTERN LIFECYCLE DIAGNOSTICS")
    print("=" * 72)

    all_pattern_names = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag",
    ]

    for name in all_pattern_names:

        print(
            f"{name}: "
            f"Candidates={global_candidates[name]} | "
            f"Breakouts={global_breakouts[name]} | "
            f"Retests={global_retests[name]} | "
            f"Confirmations={global_confirmations[name]}"
        )

    # --------------------------------------------------------
    # Final methodology reminder
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("METHODOLOGY")
    print("=" * 72)

    print(
        "1H pattern -> 1H candle CLOSE breakout -> "
        "first 5M retest -> 5M confirmation -> trade"
    )

    print(
        "Only closed candles are used."
    )

    print(
        "The breakout 1H candle is NOT used as a 5M retest candle."
    )

    print(
        "Only one trade is allowed per pattern instance."
    )

    print(
        "If TP and SL are touched in the same candle, "
        "SL is counted first."
    )

    print(
        "Unresolved trades after 48 hours are excluded."
    )

    print(
        "No real trading."
    )

    print()
    print("=" * 72)


if __name__ == "__main__":
    main()
