# ============================================================
# KRAKEN FUTURES MULTI-TIMEFRAME PATTERN BACKTEST
# ============================================================
#
# 1H = MAIN PATTERN
# 5M = ENTRY TRIGGER
#
# MAIN PATTERNS:
#   Double Bottom
#   Double Top
#   Head & Shoulders
#   Inverse H&S
#   Triangle
#   Wedge
#   Flag
#
# 5M ENTRY:
#   Breakout -> Retest -> Confirmation
#
# TRADE:
#   TP = 2%
#   SL = 1%
#
# IMPORTANT:
#   CLOSED CANDLES ONLY
#   NO LOOKAHEAD
#   1H PATTERN MUST BE CONFIRMED
#   1H BREAKOUT MUST BE CONFIRMED
#   5M RETEST MUST OCCUR AFTER BREAKOUT
#   5M CONFIRMATION MUST OCCUR AFTER RETEST
#
# SAME 1H PATTERN:
#   ONE TRADE ONLY
#
# IF TP AND SL ARE BOTH TOUCHED IN SAME 5M CANDLE:
#   SL FIRST -> FAILURE
#
# NO REAL TRADING
# NO API KEY
# ============================================================

import time
import requests
import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TICKER_URL = (
    BASE_URL +
    "/derivatives/api/v3/tickers"
)

CANDLE_URL = (
    BASE_URL +
    "/api/charts/v1/trade/{symbol}/{interval}"
)

INTERVAL_1H = "1h"
INTERVAL_5M = "5m"

LOOKBACK_DAYS = 365

TP_PCT = 0.02
SL_PCT = 0.01

# Maximum time after 5M entry.
MAX_HOLD_5M = 576
# 576 x 5 minutes = 48 hours.

TOP_SYMBOLS = 20

REQUEST_TIMEOUT = 20
MAX_RETRIES = 4

CANDLES_PER_REQUEST = 1000

# ============================================================
# 1H PATTERN SETTINGS
# ============================================================

SWING_LEFT = 3
SWING_RIGHT = 3

MIN_PATTERN_DISTANCE = 8
MAX_PATTERN_DISTANCE = 80

MIN_SWING_MOVE_PCT = 0.004

DOUBLE_TOLERANCE = 0.015

SHOULDER_TOLERANCE = 0.035

HEAD_MIN_DISTANCE = 0.008

BREAKOUT_BUFFER = 0.0015

MIN_CONVERGENCE = 0.15

FLAG_IMPULSE_LOOKBACK = 20
FLAG_CONSOLIDATION = 15

# ============================================================
# 5M ENTRY SETTINGS
# ============================================================

# Retest tolerance around the broken 1H level.
RETEST_TOLERANCE = 0.003

# Confirmation candle minimum body.
CONFIRM_BODY_RATIO = 0.45

# Confirmation must close in breakout direction.
CONFIRM_CLOSE_POSITION = 0.60

# Maximum number of 5M candles allowed between
# 1H breakout and confirmation.
MAX_ENTRY_WAIT_5M = 72
# 6 hours.

# Minimum number of candles after retest before
# confirmation is accepted.
MIN_CONFIRM_DELAY = 1

# Avoid duplicate 5M entries around one 1H signal.
ENTRY_COOLDOWN_5M = 12


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Kraken-MTF-Pattern-Backtest/1.0"
})


# ============================================================
# HTTP
# ============================================================

def request_json(url, params=None):

    last_error = None

    for attempt in range(MAX_RETRIES):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            time.sleep(
                1.5 ** attempt
            )

    raise RuntimeError(
        f"Request failed after retries: {last_error}"
    )


# ============================================================
# CONNECTION
# ============================================================

def test_connection():

    print()
    print(
        "Testing Kraken Futures connection..."
    )

    data = request_json(
        TICKER_URL
    )

    tickers = data.get(
        "tickers",
        []
    )

    if not tickers:

        raise RuntimeError(
            "Kraken returned no ticker records."
        )

    print(
        f"Kraken connection OK - "
        f"{len(tickers)} ticker records received."
    )

    return tickers


# ============================================================
# SYMBOL SELECTION
# ============================================================

def select_symbols(tickers):

    print()
    print(
        "Selecting Kraken perpetual futures..."
    )

    candidates = []

    for item in tickers:

        symbol = item.get(
            "symbol",
            ""
        )

        tag = item.get(
            "tag",
            ""
        )

        if not symbol.startswith("PI_"):
            continue

        if tag != "perpetual":
            continue

        try:

            volume = float(
                item.get(
                    "volumeQuote",
                    0
                ) or 0
            )

        except Exception:

            volume = 0

        candidates.append(
            (
                symbol,
                volume
            )
        )

    candidates.sort(
        key=lambda x: x[1],
        reverse=True
    )

    selected = [
        x[0]
        for x in candidates[:TOP_SYMBOLS]
    ]

    if not selected:

        raise RuntimeError(
            "No perpetual futures selected."
        )

    print(
        f"Selected {len(selected)} symbols:"
    )

    for symbol in selected:

        print(
            f"  {symbol}"
        )

    return selected


# ============================================================
# DOWNLOAD CANDLES
# ============================================================

def download_candles(
    symbol,
    interval
):

    end_time = int(
        time.time()
    )

    start_time = (
        end_time
        -
        LOOKBACK_DAYS * 86400
    )

    rows = []

    cursor = start_time

    interval_seconds = (
        3600
        if interval == "1h"
        else 300
    )

    while cursor < end_time:

        url = CANDLE_URL.format(
            symbol=symbol,
            interval=interval
        )

        params = {
            "from": cursor,
            "to": end_time
        }

        data = request_json(
            url,
            params=params
        )

        candles = data.get(
            "candles",
            []
        )

        if not candles:
            break

        rows.extend(
            candles
        )

        timestamps = []

        for candle in candles:

            ts = (
                candle.get("time")
                or
                candle.get("timestamp")
                or
                candle.get("ts")
            )

            if ts is not None:

                try:
                    ts = int(ts)

                    if ts > 10_000_000_000:
                        ts //= 1000

                    timestamps.append(ts)

                except Exception:
                    pass

        if not timestamps:
            break

        newest = max(
            timestamps
        )

        if newest <= cursor:
            break

        cursor = (
            newest +
            interval_seconds
        )

        # Safety against unexpected pagination.
        if len(rows) > 120000:
            break

    if not rows:

        return pd.DataFrame()

    parsed = []

    for candle in rows:

        try:

            ts = (
                candle.get("time")
                or
                candle.get("timestamp")
                or
                candle.get("ts")
            )

            if ts is None:
                continue

            ts = int(ts)

            if ts > 10_000_000_000:
                ts //= 1000

            parsed.append({

                "timestamp":
                    pd.to_datetime(
                        ts,
                        unit="s",
                        utc=True
                    ),

                "open":
                    float(
                        candle["open"]
                    ),

                "high":
                    float(
                        candle["high"]
                    ),

                "low":
                    float(
                        candle["low"]
                    ),

                "close":
                    float(
                        candle["close"]
                    ),

                "volume":
                    float(
                        candle.get(
                            "volume",
                            0
                        )
                    )
            })

        except Exception:

            continue

    if not parsed:

        return pd.DataFrame()

    df = pd.DataFrame(
        parsed
    )

    df = (
        df
        .drop_duplicates(
            "timestamp"
        )
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )

    # --------------------------------------------------------
    # Remove currently forming candle.
    # --------------------------------------------------------

    now = pd.Timestamp.now(
        tz="UTC"
    )

    delta = pd.Timedelta(
        hours=1
        if interval == "1h"
        else 5,
        minutes=0
    )

    df = df[
        df["timestamp"] + delta <= now
    ].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# SWINGS
# ============================================================

def find_swings(df):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    for i in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT
    ):

        if (
            h[i]
            >
            h[
                i - SWING_LEFT:i
            ].max()
            and
            h[i]
            >=
            h[
                i + 1:
                i + 1 + SWING_RIGHT
            ].max()
        ):

            highs.append(i)

        if (
            l[i]
            <
            l[
                i - SWING_LEFT:i
            ].min()
            and
            l[i]
            <=
            l[
                i + 1:
                i + 1 + SWING_RIGHT
            ].min()
        ):

            lows.append(i)

    return highs, lows


# ============================================================
# HELPERS
# ============================================================

def pct_difference(a, b):

    if b == 0:
        return 999

    return abs(a - b) / abs(b)


def valid_distance(a, b):

    distance = b - a

    return (
        MIN_PATTERN_DISTANCE
        <= distance
        <= MAX_PATTERN_DISTANCE
    )


def line_value(
    x1,
    y1,
    x2,
    y2,
    x
):

    if x2 == x1:
        return y2

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return y1 + slope * (
        x - x1
    )


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(
    df,
    highs,
    lows,
    entry_index
):

    valid_lows = [
        x for x in lows
        if x < entry_index
    ]

    if len(valid_lows) < 2:
        return None

    l1, l2 = valid_lows[-2:]

    if not valid_distance(
        l1,
        l2
    ):
        return None

    p1 = df.iloc[l1]["low"]
    p2 = df.iloc[l2]["low"]

    if (
        pct_difference(
            p1,
            p2
        )
        >
        DOUBLE_TOLERANCE
    ):
        return None

    between = df.iloc[
        l1:l2 + 1
    ]

    neckline = (
        between["high"].max()
    )

    avg_bottom = (
        p1 + p2
    ) / 2

    if (
        neckline - avg_bottom
    ) / avg_bottom < MIN_SWING_MOVE_PCT:

        return None

    close = df.iloc[
        entry_index
    ]["close"]

    if close <= (
        neckline *
        (1 + BREAKOUT_BUFFER)
    ):

        return None

    # No previous confirmed close above neckline.
    prior = df.iloc[
        l2 + 1:entry_index
    ]

    if not prior.empty:

        if (
            prior["close"]
            >
            neckline *
            (1 + BREAKOUT_BUFFER)
        ).any():

            return None

    return {
        "pattern":
            "Double Bottom",

        "direction":
            "LONG",

        "start":
            l1,

        "end":
            l2,

        "level":
            neckline
    }


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(
    df,
    highs,
    lows,
    entry_index
):

    valid_highs = [
        x for x in highs
        if x < entry_index
    ]

    if len(valid_highs) < 2:
        return None

    h1, h2 = valid_highs[-2:]

    if not valid_distance(
        h1,
        h2
    ):
        return None

    p1 = df.iloc[h1]["high"]
    p2 = df.iloc[h2]["high"]

    if (
        pct_difference(
            p1,
            p2
        )
        >
        DOUBLE_TOLERANCE
    ):
        return None

    between = df.iloc[
        h1:h2 + 1
    ]

    neckline = (
        between["low"].min()
    )

    avg_top = (
        p1 + p2
    ) / 2

    if (
        avg_top - neckline
    ) / avg_top < MIN_SWING_MOVE_PCT:

        return None

    close = df.iloc[
        entry_index
    ]["close"]

    if close >= (
        neckline *
        (1 - BREAKOUT_BUFFER)
    ):

        return None

    prior = df.iloc[
        h2 + 1:entry_index
    ]

    if not prior.empty:

        if (
            prior["close"]
            <
            neckline *
            (1 - BREAKOUT_BUFFER)
        ).any():

            return None

    return {
        "pattern":
            "Double Top",

        "direction":
            "SHORT",

        "start":
            h1,

        "end":
            h2,

        "level":
            neckline
    }


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(
    df,
    highs,
    lows,
    entry_index
):

    hs = [
        x for x in highs
        if x < entry_index
    ]

    if len(hs) < 3:
        return None

    h1, h2, h3 = hs[-3:]

    if (
        not valid_distance(h1, h2)
        or
        not valid_distance(h2, h3)
    ):
        return None

    left = df.iloc[h1]["high"]
    head = df.iloc[h2]["high"]
    right = df.iloc[h3]["high"]

    if head <= left or head <= right:
        return None

    if (
        (head - left) / left
        <
        HEAD_MIN_DISTANCE
    ):
        return None

    if (
        (head - right) / right
        <
        HEAD_MIN_DISTANCE
    ):
        return None

    if (
        pct_difference(
            left,
            right
        )
        >
        SHOULDER_TOLERANCE
    ):
        return None

    middle_lows = [
        x for x in lows
        if h1 < x < h3
    ]

    left_lows = [
        x for x in middle_lows
        if x < h2
    ]

    right_lows = [
        x for x in middle_lows
        if x > h2
    ]

    if not left_lows or not right_lows:
        return None

    l = left_lows[-1]
    r = right_lows[0]

    neckline = line_value(
        l,
        df.iloc[l]["low"],
        r,
        df.iloc[r]["low"],
        entry_index
    )

    close = df.iloc[
        entry_index
    ]["close"]

    if close >= (
        neckline *
        (1 - BREAKOUT_BUFFER)
    ):

        return None

    return {
        "pattern":
            "Head & Shoulders",

        "direction":
            "SHORT",

        "start":
            h1,

        "end":
            h3,

        "level":
            neckline
    }


# ============================================================
# INVERSE H&S
# ============================================================

def detect_inverse_head_shoulders(
    df,
    highs,
    lows,
    entry_index
):

    ls = [
        x for x in lows
        if x < entry_index
    ]

    if len(ls) < 3:
        return None

    l1, l2, l3 = ls[-3:]

    if (
        not valid_distance(l1, l2)
        or
        not valid_distance(l2, l3)
    ):
        return None

    left = df.iloc[l1]["low"]
    head = df.iloc[l2]["low"]
    right = df.iloc[l3]["low"]

    if head >= left or head >= right:
        return None

    if (
        (left - head) / left
        <
        HEAD_MIN_DISTANCE
    ):
        return None

    if (
        (right - head) / right
        <
        HEAD_MIN_DISTANCE
    ):
        return None

    if (
        pct_difference(
            left,
            right
        )
        >
        SHOULDER_TOLERANCE
    ):
        return None

    middle_highs = [
        x for x in highs
        if l1 < x < l3
    ]

    left_highs = [
        x for x in middle_highs
        if x < l2
    ]

    right_highs = [
        x for x in middle_highs
        if x > l2
    ]

    if not left_highs or not right_highs:
        return None

    l = left_highs[-1]
    r = right_highs[0]

    neckline = line_value(
        l,
        df.iloc[l]["high"],
        r,
        df.iloc[r]["high"],
        entry_index
    )

    close = df.iloc[
        entry_index
    ]["close"]

    if close <= (
        neckline *
        (1 + BREAKOUT_BUFFER)
    ):

        return None

    return {
        "pattern":
            "Inverse H&S",

        "direction":
            "LONG",

        "start":
            l1,

        "end":
            l3,

        "level":
            neckline
    }


# ============================================================
# TRIANGLE
# ============================================================

def detect_triangle(
    df,
    highs,
    lows,
    entry_index
):

    hs = [
        x for x in highs
        if x < entry_index
    ]

    ls = [
        x for x in lows
        if x < entry_index
    ]

    if len(hs) < 3 or len(ls) < 3:
        return None

    hs = hs[-3:]
    ls = ls[-3:]

    high_slope = (
        df.iloc[hs[-1]]["high"]
        -
        df.iloc[hs[0]]["high"]
    ) / (
        hs[-1] - hs[0]
    )

    low_slope = (
        df.iloc[ls[-1]]["low"]
        -
        df.iloc[ls[0]]["low"]
    ) / (
        ls[-1] - ls[0]
    )

    if high_slope >= 0:
        return None

    if low_slope <= 0:
        return None

    initial_high = (
        df.iloc[hs[0]]["high"]
    )

    initial_low = (
        df.iloc[ls[0]]["low"]
    )

    final_high = (
        df.iloc[hs[-1]]["high"]
    )

    final_low = (
        df.iloc[ls[-1]]["low"]
    )

    initial_width = (
        initial_high -
        initial_low
    )

    final_width = (
        final_high -
        final_low
    )

    if initial_width <= 0:
        return None

    convergence = (
        1 -
        final_width /
        initial_width
    )

    if convergence < MIN_CONVERGENCE:
        return None

    resistance = line_value(
        hs[0],
        df.iloc[hs[0]]["high"],
        hs[-1],
        df.iloc[hs[-1]]["high"],
        entry_index
    )

    support = line_value(
        ls[0],
        df.iloc[ls[0]]["low"],
        ls[-1],
        df.iloc[ls[-1]]["low"],
        entry_index
    )

    close = df.iloc[
        entry_index
    ]["close"]

    if close > (
        resistance *
        (1 + BREAKOUT_BUFFER)
    ):

        return {
            "pattern":
                "Triangle",

            "direction":
                "LONG",

            "start":
                min(hs[0], ls[0]),

            "end":
                max(hs[-1], ls[-1]),

            "level":
                resistance
        }

    if close < (
        support *
        (1 - BREAKOUT_BUFFER)
    ):

        return {
            "pattern":
                "Triangle",

            "direction":
                "SHORT",

            "start":
                min(hs[0], ls[0]),

            "end":
                max(hs[-1], ls[-1]),

            "level":
                support
        }

    return None


# ============================================================
# WEDGE
# ============================================================

def detect_wedge(
    df,
    highs,
    lows,
    entry_index
):

    hs = [
        x for x in highs
        if x < entry_index
    ]

    ls = [
        x for x in lows
        if x < entry_index
    ]

    if len(hs) < 3 or len(ls) < 3:
        return None

    hs = hs[-3:]
    ls = ls[-3:]

    high_slope = (
        df.iloc[hs[-1]]["high"]
        -
        df.iloc[hs[0]]["high"]
    ) / (
        hs[-1] - hs[0]
    )

    low_slope = (
        df.iloc[ls[-1]]["low"]
        -
        df.iloc[ls[0]]["low"]
    ) / (
        ls[-1] - ls[0]
    )

    same_direction = (
        high_slope > 0
        and
        low_slope > 0
    ) or (
        high_slope < 0
        and
        low_slope < 0
    )

    if not same_direction:
        return None

    initial_width = (
        df.iloc[hs[0]]["high"]
        -
        df.iloc[ls[0]]["low"]
    )

    final_width = (
        df.iloc[hs[-1]]["high"]
        -
        df.iloc[ls[-1]]["low"]
    )

    if initial_width <= 0:
        return None

    convergence = (
        1 -
        final_width /
        initial_width
    )

    if convergence < MIN_CONVERGENCE:
        return None

    upper = line_value(
        hs[0],
        df.iloc[hs[0]]["high"],
        hs[-1],
        df.iloc[hs[-1]]["high"],
        entry_index
    )

    lower = line_value(
        ls[0],
        df.iloc[ls[0]]["low"],
        ls[-1],
        df.iloc[ls[-1]]["low"],
        entry_index
    )

    close = df.iloc[
        entry_index
    ]["close"]

    # Rising wedge = bearish.
    if high_slope > 0:

        if close < (
            lower *
            (1 - BREAKOUT_BUFFER)
        ):

            return {
                "pattern":
                    "Wedge",

                "direction":
                    "SHORT",

                "start":
                    min(hs[0], ls[0]),

                "end":
                    max(hs[-1], ls[-1]),

                "level":
                    lower
            }

    # Falling wedge = bullish.
    if high_slope < 0:

        if close > (
            upper *
            (1 + BREAKOUT_BUFFER)
        ):

            return {
                "pattern":
                    "Wedge",

                "direction":
                    "LONG",

                "start":
                    min(hs[0], ls[0]),

                "end":
                    max(hs[-1], ls[-1]),

                "level":
                    upper
            }

    return None


# ============================================================
# FLAG
# ============================================================

def detect_flag(
    df,
    entry_index
):

    required = (
        FLAG_IMPULSE_LOOKBACK
        +
        FLAG_CONSOLIDATION
        +
        5
    )

    if entry_index < required:
        return None

    impulse_start = (
        entry_index
        -
        FLAG_IMPULSE_LOOKBACK
        -
        FLAG_CONSOLIDATION
    )

    impulse_end = (
        entry_index
        -
        FLAG_CONSOLIDATION
    )

    impulse = df.iloc[
        impulse_start:impulse_end
    ]

    consolidation = df.iloc[
        impulse_end:entry_index
    ]

    if len(impulse) < 10:
        return None

    if len(consolidation) < 8:
        return None

    impulse_return = (
        impulse["close"].iloc[-1]
        /
        impulse["close"].iloc[0]
        -
        1
    )

    cons_return = (
        consolidation["close"].iloc[-1]
        /
        consolidation["close"].iloc[0]
        -
        1
    )

    impulse_range = (
        impulse["high"].max()
        -
        impulse["low"].min()
    )

    if impulse_range <= 0:
        return None

    if abs(impulse_return) < 0.025:
        return None

    cons_range = (
        consolidation["high"].max()
        -
        consolidation["low"].min()
    )

    if (
        cons_range /
        impulse_range
        >
        0.50
    ):
        return None

    flag_high = (
        consolidation["high"].max()
    )

    flag_low = (
        consolidation["low"].min()
    )

    close = df.iloc[
        entry_index
    ]["close"]

    if impulse_return > 0:

        if cons_return < -0.025:
            return None

        if close > (
            flag_high *
            (1 + BREAKOUT_BUFFER)
        ):

            return {
                "pattern":
                    "Flag",

                "direction":
                    "LONG",

                "start":
                    impulse_start,

                "end":
                    impulse_end,

                "level":
                    flag_high
            }

    else:

        if cons_return > 0.025:
            return None

        if close < (
            flag_low *
            (1 - BREAKOUT_BUFFER)
        ):

            return {
                "pattern":
                    "Flag",

                "direction":
                    "SHORT",

                "start":
                    impulse_start,

                "end":
                    impulse_end,

                "level":
                    flag_low
            }

    return None


# ============================================================
# DETECT 1H PATTERN
# ============================================================

def detect_pattern(
    df,
    highs,
    lows,
    index
):

    detectors = [

        detect_double_bottom,

        detect_double_top,

        detect_head_shoulders,

        detect_inverse_head_shoulders,

        detect_triangle,

        detect_wedge
    ]

    for detector in detectors:

        result = detector(
            df,
            highs,
            lows,
            index
        )

        if result:
            return result

    result = detect_flag(
        df,
        index
    )

    if result:
        return result

    return None


# ============================================================
# 5M CANDLE CONFIRMATION
# ============================================================

def bullish_confirmation(
    candle
):

    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    candle_range = h - l

    if candle_range <= 0:
        return False

    body = abs(c - o)

    body_ratio = (
        body /
        candle_range
    )

    close_position = (
        c - l
    ) / candle_range

    return (
        c > o
        and
        body_ratio >= CONFIRM_BODY_RATIO
        and
        close_position >= CONFIRM_CLOSE_POSITION
    )


def bearish_confirmation(
    candle
):

    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    candle_range = h - l

    if candle_range <= 0:
        return False

    body = abs(c - o)

    body_ratio = (
        body /
        candle_range
    )

    close_position = (
        h - c
    ) / candle_range

    return (
        c < o
        and
        body_ratio >= CONFIRM_BODY_RATIO
        and
        close_position >= CONFIRM_CLOSE_POSITION
    )


# ============================================================
# 5M ENTRY ENGINE
# ============================================================

def find_5m_entry(
    df5,
    start_timestamp,
    direction,
    level
):

    candidates = df5[
        df5["timestamp"]
        >
        start_timestamp
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.iloc[
        :MAX_ENTRY_WAIT_5M
    ]

    retested = False
    retest_index = None

    last_entry = -999999

    for idx in candidates.index:

        candle = df5.loc[idx]

        high = candle["high"]
        low = candle["low"]
        close = candle["close"]

        # ----------------------------------------------------
        # RETEST
        # ----------------------------------------------------

        upper = (
            level *
            (1 + RETEST_TOLERANCE)
        )

        lower = (
            level *
            (1 - RETEST_TOLERANCE)
        )

        touched_level = (
            low <= upper
            and
            high >= lower
        )

        if not retested:

            if not touched_level:
                continue

            # Retest must happen on correct side.
            if direction == "LONG":

                # Candle should not close deeply below level.
                if close < lower:
                    continue

            else:

                if close > upper:
                    continue

            retested = True
            retest_index = idx

            continue

        # ----------------------------------------------------
        # Minimum delay after retest.
        # ----------------------------------------------------

        if (
            idx - retest_index
            <
            MIN_CONFIRM_DELAY
        ):
            continue

        # ----------------------------------------------------
        # CONFIRMATION
        # ----------------------------------------------------

        if direction == "LONG":

            if not bullish_confirmation(
                candle
            ):
                continue

            if close <= level:
                continue

        else:

            if not bearish_confirmation(
                candle
            ):
                continue

            if close >= level:
                continue

        if (
            idx - last_entry
            <
            ENTRY_COOLDOWN_5M
        ):
            continue

        return {
            "index": idx,
            "timestamp": candle["timestamp"],
            "entry": close
        }

    return None


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df5,
    entry_index,
    direction
):

    entry = float(
        df5.iloc[
            entry_index
        ]["close"]
    )

    if direction == "LONG":

        tp = (
            entry *
            (1 + TP_PCT)
        )

        sl = (
            entry *
            (1 - SL_PCT)
        )

    else:

        tp = (
            entry *
            (1 - TP_PCT)
        )

        sl = (
            entry *
            (1 + SL_PCT)
        )

    end = min(
        len(df5),
        entry_index
        +
        MAX_HOLD_5M
        +
        1
    )

    for i in range(
        entry_index + 1,
        end
    ):

        candle = df5.iloc[i]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

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

            return "FAILURE"

        if hit_sl:

            return "FAILURE"

        if hit_tp:

            return "SUCCESS"

    return None


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    df1,
    df5
):

    if (
        len(df1) < 500
        or
        len(df5) < 5000
    ):
        return []

    highs, lows = find_swings(
        df1
    )

    trades = []

    used_patterns = set()

    last_trade_5m_index = -999999

    # --------------------------------------------------------
    # Scan each CLOSED 1H candle.
    # --------------------------------------------------------

    for i in range(
        100,
        len(df1) - 1
    ):

        pattern = detect_pattern(
            df1,
            highs,
            lows,
            i
        )

        if pattern is None:
            continue

        # ----------------------------------------------------
        # Unique pattern instance.
        # ----------------------------------------------------

        fingerprint = (
            pattern["pattern"],
            pattern["direction"],
            pattern["start"],
            pattern["end"]
        )

        if fingerprint in used_patterns:
            continue

        used_patterns.add(
            fingerprint
        )

        breakout_time = (
            df1.iloc[i]["timestamp"]
        )

        # ----------------------------------------------------
        # Find 5M retest + confirmation
        # after the 1H breakout.
        # ----------------------------------------------------

        entry = find_5m_entry(
            df5,
            breakout_time,
            pattern["direction"],
            pattern["level"]
        )

        if entry is None:
            continue

        entry_index_5m = (
            entry["index"]
        )

        if (
            entry_index_5m
            -
            last_trade_5m_index
            <
            ENTRY_COOLDOWN_5M
        ):
            continue

        result = simulate_trade(
            df5,
            entry_index_5m,
            pattern["direction"]
        )

        # Ignore unresolved trades.
        if result is None:
            continue

        trades.append({

            "symbol":
                symbol,

            "pattern":
                pattern["pattern"],

            "direction":
                pattern["direction"],

            "breakout_time":
                breakout_time,

            "entry_time":
                entry["timestamp"],

            "entry":
                entry["entry"],

            "result":
                result
        })

        last_trade_5m_index = (
            entry_index_5m
        )

    return trades


# ============================================================
# RESULTS
# ============================================================

def print_results(
    trades
):

    print()
    print("=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)

    total = len(trades)

    success = sum(
        1
        for x in trades
        if x["result"] == "SUCCESS"
    )

    failure = sum(
        1
        for x in trades
        if x["result"] == "FAILURE"
    )

    if total == 0:

        print(
            "TOTAL TRADES: 0"
        )

        raise RuntimeError(
            "No completed trades."
        )

    success_rate = (
        success /
        total *
        100
    )

    failure_rate = (
        failure /
        total *
        100
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
        f"SUCCESS RATE: "
        f"{success_rate:.2f}%"
    )

    print(
        f"FAILURE RATE: "
        f"{failure_rate:.2f}%"
    )

    print()
    print(
        "PATTERN RESULTS"
    )

    print("-" * 70)

    pattern_names = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag"
    ]

    for name in pattern_names:

        subset = [
            x
            for x in trades
            if x["pattern"] == name
        ]

        count = len(
            subset
        )

        if count == 0:

            print(
                f"{name}: "
                f"Trades=0 | "
                f"Success=0 | "
                f"Failure=0 | "
                f"Success Rate=0.00%"
            )

            continue

        s = sum(
            1
            for x in subset
            if x["result"] ==
            "SUCCESS"
        )

        f = sum(
            1
            for x in subset
            if x["result"] ==
            "FAILURE"
        )

        sr = (
            s /
            count *
            100
        )

        print(
            f"{name}: "
            f"Trades={count} | "
            f"Success={s} | "
            f"Failure={f} | "
            f"Success Rate={sr:.2f}%"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 70)
    print(
        "KRAKEN FUTURES "
        "MULTI-TIMEFRAME PATTERN BACKTEST"
    )
    print("=" * 70)

    end_date = pd.Timestamp.now(
        tz="UTC"
    ).date()

    start_date = (
        pd.Timestamp.now(
            tz="UTC"
        )
        -
        pd.Timedelta(
            days=LOOKBACK_DAYS
        )
    ).date()

    print(
        f"Period: "
        f"{start_date} -> {end_date}"
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
        "Entry: 1H Breakout -> "
        "5M Retest -> 5M Confirmation"
    )

    print(
        "Lookahead: DISABLED"
    )

    print(
        "Real trading: DISABLED"
    )

    # --------------------------------------------------------
    # Connection
    # --------------------------------------------------------

    tickers = test_connection()

    # --------------------------------------------------------
    # Symbols
    # --------------------------------------------------------

    symbols = select_symbols(
        tickers
    )

    # --------------------------------------------------------
    # Run
    # --------------------------------------------------------

    print()
    print(
        "DOWNLOADING DATA "
        "AND RUNNING BACKTEST"
    )

    all_trades = []

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        print()
        print(
            f"[{number}/{len(symbols)}] "
            f"{symbol}"
        )

        try:

            print(
                "  Downloading 1H..."
            )

            df1 = download_candles(
                symbol,
                INTERVAL_1H
            )

            print(
                f"  1H Candles: "
                f"{len(df1)}"
            )

            print(
                "  Downloading 5M..."
            )

            df5 = download_candles(
                symbol,
                INTERVAL_5M
            )

            print(
                f"  5M Candles: "
                f"{len(df5)}"
            )

            if (
                df1.empty
                or
                df5.empty
            ):

                print(
                    "  Skipped: "
                    "missing data."
                )

                continue

            trades = backtest_symbol(
                symbol,
                df1,
                df5
            )

            print(
                f"  Completed trades: "
                f"{len(trades)}"
            )

            all_trades.extend(
                trades
            )

        except Exception as exc:

            print(
                f"  ERROR: {exc}"
            )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    print_results(
        all_trades
    )

    print()
    print(
        "BACKTEST COMPLETED SUCCESSFULLY"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
