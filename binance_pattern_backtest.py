# ============================================================
# KRAKEN FUTURES PATTERN BACKTEST - 1 YEAR
# ============================================================
#
# Classical Chart Pattern Detector
#
# Patterns:
#   - Double Bottom
#   - Double Top
#   - Head & Shoulders
#   - Inverse Head & Shoulders
#   - Triangle
#   - Wedge
#   - Flag
#
# DATA:
#   Kraken Futures
#
# TIMEFRAME:
#   1H
#
# TRADE:
#   TP = 2%
#   SL = 1%
#
# RULES:
#   - CLOSED CANDLES ONLY
#   - NO LOOKAHEAD
#   - ENTRY AFTER CONFIRMED BREAKOUT
#   - ONE TRADE PER PATTERN INSTANCE
#   - SAME PATTERN STRUCTURE CANNOT RE-ENTER
#   - IF TP AND SL ARE TOUCHED IN SAME CANDLE:
#       SL IS ASSUMED FIRST
#
# NO REAL TRADING
# NO API KEY
# ============================================================

import time
import math
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

INTERVAL = "1h"

LOOKBACK_DAYS = 365

TP_PCT = 0.02
SL_PCT = 0.01

MAX_HOLD_CANDLES = 48

TOP_SYMBOLS = 20

CANDLES_PER_REQUEST = 1000

REQUEST_TIMEOUT = 20
MAX_RETRIES = 4

# ------------------------------------------------------------
# Pattern detection
# ------------------------------------------------------------

SWING_LEFT = 3
SWING_RIGHT = 3

MIN_PATTERN_DISTANCE = 8
MAX_PATTERN_DISTANCE = 80

# Minimum meaningful movement between structural points.
MIN_SWING_MOVE_PCT = 0.004

# Equality tolerance for double tops/bottoms.
DOUBLE_TOLERANCE = 0.015

# H&S shoulder tolerance.
SHOULDER_TOLERANCE = 0.035

# Minimum head dominance over shoulders.
HEAD_MIN_DISTANCE = 0.008

# Breakout must close beyond neckline/trendline.
BREAKOUT_BUFFER = 0.0015

# Minimum triangle convergence.
MIN_CONVERGENCE = 0.15

# Flag configuration.
FLAG_IMPULSE_LOOKBACK = 20
FLAG_CONSOLIDATION = 15

# Prevent repeated signals from same structure.
SIGNAL_COOLDOWN = 20


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "KrakenPatternBacktest/2.0"
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

            wait = 1.5 ** attempt

            time.sleep(wait)

    raise RuntimeError(
        f"Request failed after retries: {last_error}"
    )


# ============================================================
# KRAKEN CONNECTION
# ============================================================

def test_connection():

    print()
    print("Testing Kraken Futures connection...")

    data = request_json(TICKER_URL)

    tickers = data.get("tickers", [])

    if not tickers:

        raise RuntimeError(
            "Kraken connection succeeded but returned no tickers."
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
    print("Selecting Kraken perpetual futures...")

    candidates = []

    for item in tickers:

        symbol = item.get("symbol", "")
        tag = item.get("tag", "")

        if not symbol.startswith("PI_"):
            continue

        if tag != "perpetual":
            continue

        try:

            volume = float(
                item.get("volumeQuote", 0) or 0
            )

        except Exception:

            volume = 0

        candidates.append(
            (symbol, volume)
        )

    candidates.sort(
        key=lambda x: x[1],
        reverse=True
    )

    selected = [
        symbol
        for symbol, _ in candidates[:TOP_SYMBOLS]
    ]

    if not selected:

        raise RuntimeError(
            "No Kraken perpetual futures found."
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
# CANDLE DOWNLOAD
# ============================================================

def download_candles(symbol):

    end_time = int(time.time())

    start_time = (
        end_time -
        LOOKBACK_DAYS * 24 * 60 * 60
    )

    rows = []

    cursor = start_time

    while cursor < end_time:

        params = {
            "from": cursor,
            "to": end_time
        }

        url = CANDLE_URL.format(
            symbol=symbol,
            interval=INTERVAL
        )

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

        rows.extend(candles)

        timestamps = []

        for c in candles:

            ts = (
                c.get("time")
                or c.get("timestamp")
                or c.get("ts")
            )

            if ts is not None:

                try:
                    timestamps.append(
                        int(ts)
                    )
                except Exception:
                    pass

        if not timestamps:
            break

        newest = max(timestamps)

        # Kraken may return milliseconds.
        if newest > 10_000_000_000:
            newest_seconds = newest // 1000
        else:
            newest_seconds = newest

        if newest_seconds <= cursor:
            break

        cursor = newest_seconds + 3600

        if len(rows) > 10000:

            break

    if not rows:

        return pd.DataFrame()

    parsed = []

    for c in rows:

        try:

            ts = (
                c.get("time")
                or c.get("timestamp")
                or c.get("ts")
            )

            if ts is None:
                continue

            ts = int(ts)

            if ts > 10_000_000_000:
                ts = ts // 1000

            parsed.append({
                "timestamp": pd.to_datetime(
                    ts,
                    unit="s",
                    utc=True
                ),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(
                    c.get("volume", 0)
                )
            })

        except Exception:

            continue

    if not parsed:

        return pd.DataFrame()

    df = pd.DataFrame(parsed)

    df = (
        df
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # Remove currently forming candle.
    # --------------------------------------------------------

    now = pd.Timestamp.now(tz="UTC")

    df = df[
        df["timestamp"] +
        pd.Timedelta(hours=1)
        <= now
    ].copy()

    return df.reset_index(drop=True)


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    return tr.rolling(period).mean()


# ============================================================
# SWING POINTS
# ============================================================

def find_swings(df):

    highs = []
    lows = []

    high = df["high"].values
    low = df["low"].values

    n = len(df)

    for i in range(
        SWING_LEFT,
        n - SWING_RIGHT
    ):

        left_high = high[
            i - SWING_LEFT:i
        ]

        right_high = high[
            i + 1:i + 1 + SWING_RIGHT
        ]

        left_low = low[
            i - SWING_LEFT:i
        ]

        right_low = low[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if (
            high[i] > left_high.max()
            and
            high[i] >= right_high.max()
        ):

            highs.append(i)

        if (
            low[i] < left_low.min()
            and
            low[i] <= right_low.min()
        ):

            lows.append(i)

    return highs, lows


# ============================================================
# STRUCTURE HELPERS
# ============================================================

def pct_difference(a, b):

    if b == 0:
        return float("inf")

    return abs(a - b) / abs(b)


def line_value(x1, y1, x2, y2, x):

    if x2 == x1:
        return y2

    slope = (
        (y2 - y1) /
        (x2 - x1)
    )

    return y1 + slope * (x - x1)


def valid_distance(a, b):

    d = b - a

    return (
        MIN_PATTERN_DISTANCE
        <= d
        <= MAX_PATTERN_DISTANCE
    )


def meaningful_move(a, b):

    if a <= 0 or b <= 0:
        return False

    return (
        abs(b - a) / a
        >= MIN_SWING_MOVE_PCT
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

    l2 = valid_lows[-1]
    l1 = valid_lows[-2]

    if not valid_distance(l1, l2):
        return None

    p1 = df.iloc[l1]["low"]
    p2 = df.iloc[l2]["low"]

    if pct_difference(p1, p2) > DOUBLE_TOLERANCE:
        return None

    between = df.iloc[l1:l2 + 1]

    if between.empty:
        return None

    neckline = between["high"].max()

    # Neckline must be meaningfully above bottoms.
    avg_bottom = (p1 + p2) / 2

    if (
        neckline - avg_bottom
    ) / avg_bottom < MIN_SWING_MOVE_PCT:
        return None

    close = df.iloc[entry_index]["close"]

    if close <= neckline * (
        1 + BREAKOUT_BUFFER
    ):
        return None

    # Pattern must not already have broken before
    # the current confirmation candle.
    prior = df.iloc[l2 + 1:entry_index]

    if not prior.empty:

        if (
            prior["close"]
            > neckline * (
                1 + BREAKOUT_BUFFER
            )
        ).any():

            return None

    return {
        "pattern": "Double Bottom",
        "direction": "LONG",
        "pattern_start": l1,
        "pattern_end": l2,
        "breakout": entry_index
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

    h2 = valid_highs[-1]
    h1 = valid_highs[-2]

    if not valid_distance(h1, h2):
        return None

    p1 = df.iloc[h1]["high"]
    p2 = df.iloc[h2]["high"]

    if pct_difference(p1, p2) > DOUBLE_TOLERANCE:
        return None

    between = df.iloc[h1:h2 + 1]

    if between.empty:
        return None

    neckline = between["low"].min()

    avg_top = (p1 + p2) / 2

    if (
        avg_top - neckline
    ) / avg_top < MIN_SWING_MOVE_PCT:
        return None

    close = df.iloc[entry_index]["close"]

    if close >= neckline * (
        1 - BREAKOUT_BUFFER
    ):
        return None

    prior = df.iloc[h2 + 1:entry_index]

    if not prior.empty:

        if (
            prior["close"]
            < neckline * (
                1 - BREAKOUT_BUFFER
            )
        ).any():

            return None

    return {
        "pattern": "Double Top",
        "direction": "SHORT",
        "pattern_start": h1,
        "pattern_end": h2,
        "breakout": entry_index
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

    # Head must be higher than both shoulders.
    if head <= left or head <= right:
        return None

    if (
        (head - left) / left
        < HEAD_MIN_DISTANCE
    ):
        return None

    if (
        (head - right) / right
        < HEAD_MIN_DISTANCE
    ):
        return None

    # Shoulders should be reasonably similar.
    if (
        pct_difference(left, right)
        > SHOULDER_TOLERANCE
    ):
        return None

    middle_lows = [
        x for x in lows
        if h1 < x < h3
    ]

    if len(middle_lows) < 2:
        return None

    # Use the trough closest to each shoulder/head transition.
    left_candidates = [
        x for x in middle_lows
        if x < h2
    ]

    right_candidates = [
        x for x in middle_lows
        if x > h2
    ]

    if not left_candidates or not right_candidates:
        return None

    l = left_candidates[-1]
    r = right_candidates[0]

    neckline = line_value(
        l,
        df.iloc[l]["low"],
        r,
        df.iloc[r]["low"],
        entry_index
    )

    close = df.iloc[entry_index]["close"]

    if close >= neckline * (
        1 - BREAKOUT_BUFFER
    ):
        return None

    prior = df.iloc[h3 + 1:entry_index]

    if not prior.empty:

        previous_neckline = (
            df.iloc[l]["low"]
            if r == l
            else line_value(
                l,
                df.iloc[l]["low"],
                r,
                df.iloc[r]["low"],
                prior.index
            )
        )

        if (
            prior["close"]
            < previous_neckline * (
                1 - BREAKOUT_BUFFER
            )
        ).any():

            return None

    return {
        "pattern": "Head & Shoulders",
        "direction": "SHORT",
        "pattern_start": h1,
        "pattern_end": h3,
        "breakout": entry_index
    }


# ============================================================
# INVERSE HEAD & SHOULDERS
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

    # Head must be lower than both shoulders.
    if head >= left or head >= right:
        return None

    if (
        (left - head) / left
        < HEAD_MIN_DISTANCE
    ):
        return None

    if (
        (right - head) / right
        < HEAD_MIN_DISTANCE
    ):
        return None

    if (
        pct_difference(left, right)
        > SHOULDER_TOLERANCE
    ):
        return None

    middle_highs = [
        x for x in highs
        if l1 < x < l3
    ]

    if len(middle_highs) < 2:
        return None

    left_candidates = [
        x for x in middle_highs
        if x < l2
    ]

    right_candidates = [
        x for x in middle_highs
        if x > l2
    ]

    if not left_candidates or not right_candidates:
        return None

    l = left_candidates[-1]
    r = right_candidates[0]

    neckline = line_value(
        l,
        df.iloc[l]["high"],
        r,
        df.iloc[r]["high"],
        entry_index
    )

    close = df.iloc[entry_index]["close"]

    if close <= neckline * (
        1 + BREAKOUT_BUFFER
    ):
        return None

    prior = df.iloc[l3 + 1:entry_index]

    if not prior.empty:

        if (
            prior["close"]
            > neckline * (
                1 + BREAKOUT_BUFFER
            )
        ).any():

            return None

    return {
        "pattern": "Inverse H&S",
        "direction": "LONG",
        "pattern_start": l1,
        "pattern_end": l3,
        "breakout": entry_index
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

    # Resistance should descend.
    high_slope = (
        df.iloc[hs[-1]]["high"]
        -
        df.iloc[hs[0]]["high"]
    ) / (hs[-1] - hs[0])

    # Support should ascend.
    low_slope = (
        df.iloc[ls[-1]]["low"]
        -
        df.iloc[ls[0]]["low"]
    ) / (ls[-1] - ls[0])

    if high_slope >= 0:
        return None

    if low_slope <= 0:
        return None

    start_high = line_value(
        hs[0],
        df.iloc[hs[0]]["high"],
        hs[-1],
        df.iloc[hs[-1]]["high"],
        hs[0]
    )

    start_low = line_value(
        ls[0],
        df.iloc[ls[0]]["low"],
        ls[-1],
        df.iloc[ls[-1]]["low"],
        ls[0]
    )

    end_high = df.iloc[hs[-1]]["high"]
    end_low = df.iloc[ls[-1]]["low"]

    initial_width = start_high - start_low
    final_width = end_high - end_low

    if initial_width <= 0:
        return None

    convergence = (
        1 -
        final_width / initial_width
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

    close = df.iloc[entry_index]["close"]

    if close > resistance * (
        1 + BREAKOUT_BUFFER
    ):

        return {
            "pattern": "Triangle",
            "direction": "LONG",
            "pattern_start": min(
                hs[0], ls[0]
            ),
            "pattern_end": max(
                hs[-1], ls[-1]
            ),
            "breakout": entry_index
        }

    if close < support * (
        1 - BREAKOUT_BUFFER
    ):

        return {
            "pattern": "Triangle",
            "direction": "SHORT",
            "pattern_start": min(
                hs[0], ls[0]
            ),
            "pattern_end": max(
                hs[-1], ls[-1]
            ),
            "breakout": entry_index
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
    ) / (hs[-1] - hs[0])

    low_slope = (
        df.iloc[ls[-1]]["low"]
        -
        df.iloc[ls[0]]["low"]
    ) / (ls[-1] - ls[0])

    # Both sides must move in same direction.
    same_direction = (
        high_slope > 0 and low_slope > 0
    ) or (
        high_slope < 0 and low_slope < 0
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
        final_width / initial_width
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

    close = df.iloc[entry_index]["close"]

    # Rising wedge -> bearish breakdown.
    if high_slope > 0:

        if close < lower * (
            1 - BREAKOUT_BUFFER
        ):

            return {
                "pattern": "Wedge",
                "direction": "SHORT",
                "pattern_start": min(
                    hs[0], ls[0]
                ),
                "pattern_end": max(
                    hs[-1], ls[-1]
                ),
                "breakout": entry_index
            }

    # Falling wedge -> bullish breakout.
    if high_slope < 0:

        if close > upper * (
            1 + BREAKOUT_BUFFER
        ):

            return {
                "pattern": "Wedge",
                "direction": "LONG",
                "pattern_start": min(
                    hs[0], ls[0]
                ),
                "pattern_end": max(
                    hs[-1], ls[-1]
                ),
                "breakout": entry_index
            }

    return None


# ============================================================
# FLAG
# ============================================================

def detect_flag(
    df,
    entry_index
):

    if entry_index < (
        FLAG_IMPULSE_LOOKBACK +
        FLAG_CONSOLIDATION +
        5
    ):
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
        - 1
    )

    cons_return = (
        consolidation["close"].iloc[-1]
        /
        consolidation["close"].iloc[0]
        - 1
    )

    impulse_range = (
        impulse["high"].max()
        -
        impulse["low"].min()
    )

    if impulse_range <= 0:
        return None

    # Need a strong directional impulse.
    if abs(impulse_return) < 0.025:
        return None

    # Consolidation must be substantially smaller.
    cons_range = (
        consolidation["high"].max()
        -
        consolidation["low"].min()
    )

    if (
        cons_range / impulse_range
        > 0.50
    ):
        return None

    flag_high = consolidation["high"].max()
    flag_low = consolidation["low"].min()

    close = df.iloc[entry_index]["close"]

    # Bull flag.
    if impulse_return > 0:

        # Consolidation should not strongly
        # reverse the impulse.
        if cons_return < -0.025:
            return None

        if close > flag_high * (
            1 + BREAKOUT_BUFFER
        ):

            return {
                "pattern": "Flag",
                "direction": "LONG",
                "pattern_start": impulse_start,
                "pattern_end": impulse_end,
                "breakout": entry_index
            }

    # Bear flag.
    if impulse_return < 0:

        if cons_return > 0.025:
            return None

        if close < flag_low * (
            1 - BREAKOUT_BUFFER
        ):

            return {
                "pattern": "Flag",
                "direction": "SHORT",
                "pattern_start": impulse_start,
                "pattern_end": impulse_end,
                "breakout": entry_index
            }

    return None


# ============================================================
# PATTERN DETECTOR
# ============================================================

def detect_pattern(
    df,
    highs,
    lows,
    entry_index
):

    detectors = [

        detect_double_bottom,

        detect_double_top,

        detect_head_shoulders,

        detect_inverse_head_shoulders,

        detect_triangle,

        detect_wedge,

    ]

    for detector in detectors:

        result = detector(
            df,
            highs,
            lows,
            entry_index
        )

        if result is not None:

            return result

    result = detect_flag(
        df,
        entry_index
    )

    if result is not None:

        return result

    return None


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df,
    entry_index,
    direction
):

    entry = float(
        df.iloc[entry_index]["close"]
    )

    if direction == "LONG":

        tp = entry * (
            1 + TP_PCT
        )

        sl = entry * (
            1 - SL_PCT
        )

    else:

        tp = entry * (
            1 - TP_PCT
        )

        sl = entry * (
            1 + SL_PCT
        )

    end = min(
        len(df),
        entry_index +
        MAX_HOLD_CANDLES +
        1
    )

    for i in range(
        entry_index + 1,
        end
    ):

        high = float(
            df.iloc[i]["high"]
        )

        low = float(
            df.iloc[i]["low"]
        )

        if direction == "LONG":

            hit_tp = high >= tp
            hit_sl = low <= sl

        else:

            hit_tp = low <= tp
            hit_sl = high >= sl

        # Conservative assumption:
        # if both occur in same candle,
        # SL is considered first.
        if hit_tp and hit_sl:

            return "FAILURE"

        if hit_sl:

            return "FAILURE"

        if hit_tp:

            return "SUCCESS"

    # Neither TP nor SL reached.
    return None


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    df
):

    if len(df) < 500:

        return []

    df = df.copy()

    df["atr"] = calculate_atr(df)

    highs, lows = find_swings(df)

    trades = []

    last_signal_index = -999999

    used_patterns = set()

    start = LOOKBACK_DAYS * 24

    # We don't actually need to start at 1 year because
    # df already represents one year. Use enough warmup.
    start = max(
        100,
        SWING_LEFT +
        SWING_RIGHT +
        20
    )

    for i in range(
        start,
        len(df) - 1
    ):

        if (
            i - last_signal_index
            < SIGNAL_COOLDOWN
        ):
            continue

        # ----------------------------------------------------
        # Only information available before / at signal candle.
        # Swings themselves require right-side confirmation,
        # therefore all recognized swings are already confirmed.
        # ----------------------------------------------------

        pattern = detect_pattern(
            df,
            highs,
            lows,
            i
        )

        if pattern is None:
            continue

        start_idx = pattern[
            "pattern_start"
        ]

        end_idx = pattern[
            "pattern_end"
        ]

        # Unique pattern fingerprint.
        fingerprint = (
            pattern["pattern"],
            pattern["direction"],
            start_idx,
            end_idx
        )

        if fingerprint in used_patterns:
            continue

        used_patterns.add(
            fingerprint
        )

        result = simulate_trade(
            df,
            i,
            pattern["direction"]
        )

        # Exclude unresolved trades.
        if result is None:
            continue

        trades.append({
            "symbol": symbol,
            "pattern": pattern["pattern"],
            "direction": pattern["direction"],
            "entry_index": i,
            "result": result
        })

        last_signal_index = i

    return trades


# ============================================================
# RESULTS
# ============================================================

def print_results(trades):

    print()
    print("=" * 60)
    print("FINAL RESULTS")
    print("=" * 60)

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

        print("TOTAL TRADES: 0")
        print()
        print(
            "No completed trades were generated."
        )

        raise RuntimeError(
            "Backtest produced zero completed trades."
        )

    success_rate = (
        success / total * 100
    )

    failure_rate = (
        failure / total * 100
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
    print("PATTERN RESULTS")
    print("-" * 60)

    patterns = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag"
    ]

    for pattern_name in patterns:

        subset = [
            x for x in trades
            if x["pattern"] == pattern_name
        ]

        count = len(subset)

        if count == 0:

            print(
                f"{pattern_name}: "
                f"Trades=0 | "
                f"Success=0 | "
                f"Failure=0 | "
                f"Success Rate=0.00% | "
                f"Failure Rate=0.00%"
            )

            continue

        s = sum(
            1
            for x in subset
            if x["result"] == "SUCCESS"
        )

        f = sum(
            1
            for x in subset
            if x["result"] == "FAILURE"
        )

        sr = s / count * 100
        fr = f / count * 100

        print(
            f"{pattern_name}: "
            f"Trades={count} | "
            f"Success={s} | "
            f"Failure={f} | "
            f"Success Rate={sr:.2f}% | "
            f"Failure Rate={fr:.2f}%"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 60)
    print("KRAKEN FUTURES PATTERN BACKTEST - 1 YEAR")
    print("=" * 60)

    end_date = pd.Timestamp.now(
        tz="UTC"
    ).date()

    start_date = (
        pd.Timestamp.now(
            tz="UTC"
        )
        -
        pd.Timedelta(days=LOOKBACK_DAYS)
    ).date()

    print(
        f"Period: "
        f"{start_date} -> {end_date}"
    )

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        f"TP: {TP_PCT * 100:.2f}%"
    )

    print(
        f"SL: {SL_PCT * 100:.2f}%"
    )

    print(
        f"Symbols: {TOP_SYMBOLS}"
    )

    print()
    print(
        "Pattern engine: "
        "CONFIRMED STRUCTURE + BREAKOUT"
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
    # Backtest
    # --------------------------------------------------------

    print()
    print(
        "DOWNLOADING DATA AND RUNNING BACKTEST"
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

            df = download_candles(
                symbol
            )

            print(
                f"  Candles: {len(df)}"
            )

            if df.empty:

                print(
                    "  Skipped: no candle data."
                )

                continue

            trades = backtest_symbol(
                symbol,
                df
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
    # Final report
    # --------------------------------------------------------

    print_results(
        all_trades
    )

    print()
    print(
        "BACKTEST COMPLETED SUCCESSFULLY"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
