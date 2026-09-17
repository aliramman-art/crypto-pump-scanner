# ============================================================
# KRAKEN FUTURES MULTI-TIMEFRAME PATTERN BACKTEST
# VERSION 4.1
# ============================================================
#
# FIXED 20-SYMBOL UNIVERSE
#
# 1H  = MAIN PATTERN
# 5M  = ENTRY
#
# FLOW:
# PATTERN
#   -> 1H BREAKOUT
#   -> FIRST 5M RETEST
#   -> 5M CONFIRMATION
#   -> ENTRY
#
# TP = 2%
# SL = 1%
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
# NO REAL TRADING
# NO API KEY
#
# IMPORTANT:
# - Exactly 20 symbols are defined in the universe.
# - Symbols are NEVER silently removed from the universe.
# - If Kraken does not provide data for a symbol:
#       UNAVAILABLE / INSUFFICIENT HISTORY
# - Such symbols are reported separately.
#
# ============================================================

import time
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timedelta, timezone
from collections import defaultdict


# ============================================================
# SETTINGS
# ============================================================

BASE_URL = "https://futures.kraken.com"

DAYS = 365

MAIN_INTERVAL = "1h"
ENTRY_INTERVAL = "5m"

TP_PCT = 0.02
SL_PCT = 0.01

MAX_HOLD_HOURS = 48

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.10

# Historical API chunk size
CANDLE_CHUNK = 1900

# ------------------------------------------------------------
# Pivot settings
# ------------------------------------------------------------

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# ------------------------------------------------------------
# Double Top / Bottom
# ------------------------------------------------------------

DOUBLE_TOLERANCE = 0.015
DOUBLE_MIN_SEPARATION = 5
DOUBLE_MAX_SEPARATION = 60

# ------------------------------------------------------------
# Head & Shoulders
# ------------------------------------------------------------

HS_MIN_SEPARATION = 5
HS_MAX_SEPARATION = 80

# ------------------------------------------------------------
# Triangle / Wedge
# ------------------------------------------------------------

MIN_STRUCTURE_BARS = 12
MAX_STRUCTURE_BARS = 60

# ------------------------------------------------------------
# Flag
# ------------------------------------------------------------

FLAG_IMPULSE_LOOKBACK = 35
FLAG_CONSOLIDATION_BARS = 15

FLAG_MIN_IMPULSE = 0.04
FLAG_MAX_RETRACE = 0.55
FLAG_MAX_RANGE_TO_IMPULSE = 0.60
FLAG_MIN_DIRECTIONAL_EFFICIENCY = 0.45

# ------------------------------------------------------------
# Breakout
# ------------------------------------------------------------

BREAKOUT_BUFFER = 0.0010

# ------------------------------------------------------------
# Retest
# ------------------------------------------------------------

RETEST_MAX_BARS = 12

# ------------------------------------------------------------
# Confirmation
# ------------------------------------------------------------

CONFIRM_MAX_BARS = 6
MIN_BODY_RATIO = 0.45
MIN_CLOSE_POSITION = 0.60


# ============================================================
# FIXED 20-SYMBOL UNIVERSE
# ============================================================
#
# IMPORTANT:
# This list is fixed.
# The program does NOT shrink it according to Kraken discovery.
#
# ============================================================

FIXED_SYMBOLS = [
    "PI_XBTUSD",
    "PI_ETHUSD",
    "PI_SOLUSD",
    "PI_XRPUSD",
    "PI_LTCUSD",
    "PI_DOGEUSD",
    "PI_ADAUSD",
    "PI_LINKUSD",
    "PI_AVAXUSD",
    "PI_DOTUSD",
    "PI_BCHUSD",
    "PI_UNIUSD",
    "PI_AAVEUSD",
    "PI_ATOMUSD",
    "PI_XLMUSD",
    "PI_ALGOUSD",
    "PI_FILUSD",
    "PI_ETCUSD",
    "PI_SUIUSD",
    "PI_HBARUSD",
]


# ============================================================
# VALIDATE UNIVERSE
# ============================================================

if len(FIXED_SYMBOLS) != 20:
    raise RuntimeError(
        f"Universe configuration error: "
        f"expected 20 symbols, got {len(FIXED_SYMBOLS)}"
    )


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
        "Mozilla/5.0 Kraken-Pattern-Backtest/4.1"
    }
)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value):

    try:
        return float(value)

    except Exception:
        return None


# ============================================================
# KRAKEN GET
# ============================================================

def kraken_get(path, params=None):

    url = BASE_URL + path

    last_error = None

    for attempt in range(5):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:

                data = response.json()

                if (
                    isinstance(data, dict)
                    and data.get("result") == "error"
                ):
                    raise RuntimeError(
                        str(data)
                    )

                return data

            if response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                time.sleep(
                    1.5 * (attempt + 1)
                )

                continue

            response.raise_for_status()

        except Exception as e:

            last_error = e

            if attempt == 4:
                break

            time.sleep(
                1.5 * (attempt + 1)
            )

    raise RuntimeError(
        f"Kraken request failed: {last_error}"
    )


# ============================================================
# OPTIONAL DISCOVERY
# ============================================================
#
# Discovery is ONLY diagnostic.
# It NEVER changes FIXED_SYMBOLS.
#
# ============================================================

def discover_available_symbols():

    available = set()

    # --------------------------------------------------------
    # TICKERS
    # --------------------------------------------------------

    try:

        data = kraken_get(
            "/derivatives/api/v3/tickers"
        )

        for item in data.get(
            "tickers",
            []
        ):

            if not isinstance(
                item,
                dict
            ):
                continue

            symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(
                symbol
            ).upper()

            if (
                symbol.startswith("PI_")
                and symbol.endswith("USD")
            ):
                available.add(symbol)

    except Exception as e:

        print(
            "Ticker discovery warning:",
            e
        )

    # --------------------------------------------------------
    # INSTRUMENTS
    # --------------------------------------------------------

    try:

        data = kraken_get(
            "/derivatives/api/v3/instruments"
        )

        for item in data.get(
            "instruments",
            []
        ):

            if not isinstance(
                item,
                dict
            ):
                continue

            symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(
                symbol
            ).upper()

            if (
                symbol.startswith("PI_")
                and symbol.endswith("USD")
            ):
                available.add(symbol)

    except Exception as e:

        print(
            "Instrument discovery warning:",
            e
        )

    return available


# ============================================================
# PRINT FIXED UNIVERSE
# ============================================================

def print_universe():

    print()
    print("=" * 70)
    print("FIXED 20-SYMBOL UNIVERSE")
    print("=" * 70)

    for i, symbol in enumerate(
        FIXED_SYMBOLS,
        start=1
    ):

        print(
            f"{i:02d}. {symbol}"
        )

    print()
    print(
        f"FINAL UNIVERSE SIZE: "
        f"{len(FIXED_SYMBOLS)}"
    )


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(
    symbol,
    interval,
    start_dt,
    end_dt
):

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    all_rows = []

    current = start_ts

    interval_seconds = (
        3600
        if interval == "1h"
        else 300
    )

    while current < end_ts:

        chunk_end = min(
            end_ts,
            current
            + (CANDLE_CHUNK - 1)
            * interval_seconds
        )

        params = {
            "from": current,
            "to": chunk_end,
        }

        path = (
            "/api/charts/v1/trade/"
            f"{symbol}/{interval}"
        )

        try:

            data = kraken_get(
                path,
                params=params
            )

        except Exception as e:

            raise RuntimeError(
                f"{symbol} {interval}: {e}"
            )

        rows = data.get(
            "candles",
            []
        )

        if not rows:
            break

        for row in rows:

            # ------------------------------------------------
            # Dictionary response
            # ------------------------------------------------

            if isinstance(
                row,
                dict
            ):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                )

                o = row.get("open")
                h = row.get("high")
                l = row.get("low")
                c = row.get("close")

            # ------------------------------------------------
            # Array response
            # ------------------------------------------------

            else:

                if len(row) < 5:
                    continue

                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]

            ts = safe_float(ts)
            o = safe_float(o)
            h = safe_float(h)
            l = safe_float(l)
            c = safe_float(c)

            if None in (
                ts,
                o,
                h,
                l,
                c
            ):
                continue

            # milliseconds -> seconds
            if ts > 10_000_000_000:
                ts /= 1000.0

            ts = int(ts)

            if (
                ts < start_ts
                or ts > end_ts
            ):
                continue

            all_rows.append(
                {
                    "timestamp": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                }
            )

        if not all_rows:
            break

        last_ts = max(
            r["timestamp"]
            for r in all_rows
        )

        next_current = (
            last_ts
            + interval_seconds
        )

        if next_current <= current:
            break

        current = next_current

        time.sleep(
            REQUEST_SLEEP
        )

    if not all_rows:

        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "datetime",
            ]
        )

    df = pd.DataFrame(
        all_rows
    )

    df = df.drop_duplicates(
        subset=[
            "timestamp"
        ]
    )

    df = df.sort_values(
        "timestamp"
    )

    df = df.reset_index(
        drop=True
    )

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    return df


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    high_values = (
        df["high"].values
    )

    low_values = (
        df["low"].values
    )

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        left_highs = high_values[
            i - PIVOT_LEFT:i
        ]

        right_highs = high_values[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        left_lows = low_values[
            i - PIVOT_LEFT:i
        ]

        right_lows = low_values[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        h = high_values[i]
        l = low_values[i]

        if (
            h > np.max(left_highs)
            and h >= np.max(right_highs)
        ):

            highs.append(i)

        if (
            l < np.min(left_lows)
            and l <= np.min(right_lows)
        ):

            lows.append(i)

    return highs, lows


# ============================================================
# PATTERN FACTORY
# ============================================================

def make_pattern(
    name,
    direction,
    start,
    end,
    breakout_level,
    upper_level=None,
    lower_level=None,
    anchors=None,
    quality=0.0
):

    return {
        "name": name,
        "direction": direction,
        "start": int(start),
        "end": int(end),
        "breakout_level": (
            float(breakout_level)
        ),
        "upper_level": (
            float(upper_level)
            if upper_level is not None
            else None
        ),
        "lower_level": (
            float(lower_level)
            if lower_level is not None
            else None
        ),
        "anchors": tuple(
            anchors or []
        ),
        "quality": float(
            quality
        ),
        "state":
            "WAITING_FOR_BREAKOUT",
    }


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(
    df,
    pivot_lows,
    current_i
):

    lows = [
        x
        for x in pivot_lows
        if x < current_i
    ]

    if len(lows) < 2:
        return []

    b = lows[-1]

    for a in reversed(
        lows[:-1]
    ):

        separation = b - a

        if (
            separation
            < DOUBLE_MIN_SEPARATION
        ):
            continue

        if (
            separation
            > DOUBLE_MAX_SEPARATION
        ):
            break

        p1 = float(
            df.iloc[a]["low"]
        )

        p2 = float(
            df.iloc[b]["low"]
        )

        similarity = (
            abs(p1 - p2)
            / ((p1 + p2) / 2)
        )

        if (
            similarity
            > DOUBLE_TOLERANCE
        ):
            continue

        between = df.iloc[
            a:b + 1
        ]

        neckline = float(
            between["high"].max()
        )

        if neckline <= max(
            p1,
            p2
        ):
            continue

        quality = (
            1.0
            - similarity
            / DOUBLE_TOLERANCE
        )

        return [
            make_pattern(
                "Double Bottom",
                "LONG",
                a,
                b,
                neckline,
                anchors=[
                    a,
                    b
                ],
                quality=quality
            )
        ]

    return []


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(
    df,
    pivot_highs,
    current_i
):

    highs = [
        x
        for x in pivot_highs
        if x < current_i
    ]

    if len(highs) < 2:
        return []

    b = highs[-1]

    for a in reversed(
        highs[:-1]
    ):

        separation = b - a

        if (
            separation
            < DOUBLE_MIN_SEPARATION
        ):
            continue

        if (
            separation
            > DOUBLE_MAX_SEPARATION
        ):
            break

        p1 = float(
            df.iloc[a]["high"]
        )

        p2 = float(
            df.iloc[b]["high"]
        )

        similarity = (
            abs(p1 - p2)
            / ((p1 + p2) / 2)
        )

        if (
            similarity
            > DOUBLE_TOLERANCE
        ):
            continue

        between = df.iloc[
            a:b + 1
        ]

        neckline = float(
            between["low"].min()
        )

        if neckline >= min(
            p1,
            p2
        ):
            continue

        quality = (
            1.0
            - similarity
            / DOUBLE_TOLERANCE
        )

        return [
            make_pattern(
                "Double Top",
                "SHORT",
                a,
                b,
                neckline,
                anchors=[
                    a,
                    b
                ],
                quality=quality
            )
        ]

    return []


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(
    df,
    pivot_highs,
    pivot_lows,
    current_i
):

    highs = [
        x
        for x in pivot_highs
        if x < current_i
    ]

    if len(highs) < 3:
        return []

    h1, h2, h3 = highs[-3:]

    if (
        h3 - h1
        > HS_MAX_SEPARATION
    ):
        return []

    if (
        h2 - h1
        < HS_MIN_SEPARATION
    ):
        return []

    if (
        h3 - h2
        < HS_MIN_SEPARATION
    ):
        return []

    p1 = float(
        df.iloc[h1]["high"]
    )

    p2 = float(
        df.iloc[h2]["high"]
    )

    p3 = float(
        df.iloc[h3]["high"]
    )

    if not (
        p2 > p1
        and p2 > p3
    ):
        return []

    shoulder_similarity = (
        abs(p1 - p3)
        / ((p1 + p3) / 2)
    )

    if (
        shoulder_similarity
        > 0.20
    ):
        return []

    lows_between = [
        x
        for x in pivot_lows
        if h1 < x < h3
    ]

    if len(lows_between) < 2:
        return []

    l1 = lows_between[-2]
    l2 = lows_between[-1]

    neckline = (
        float(df.iloc[l1]["low"])
        + float(df.iloc[l2]["low"])
    ) / 2

    quality = max(
        0.0,
        1.0
        - shoulder_similarity
        / 0.20
    )

    return [
        make_pattern(
            "Head & Shoulders",
            "SHORT",
            h1,
            h3,
            neckline,
            anchors=[
                h1,
                l1,
                h2,
                l2,
                h3
            ],
            quality=quality
        )
    ]


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(
    df,
    pivot_highs,
    pivot_lows,
    current_i
):

    lows = [
        x
        for x in pivot_lows
        if x < current_i
    ]

    if len(lows) < 3:
        return []

    l1, l2, l3 = lows[-3:]

    if (
        l3 - l1
        > HS_MAX_SEPARATION
    ):
        return []

    if (
        l2 - l1
        < HS_MIN_SEPARATION
    ):
        return []

    if (
        l3 - l2
        < HS_MIN_SEPARATION
    ):
        return []

    p1 = float(
        df.iloc[l1]["low"]
    )

    p2 = float(
        df.iloc[l2]["low"]
    )

    p3 = float(
        df.iloc[l3]["low"]
    )

    if not (
        p2 < p1
        and p2 < p3
    ):
        return []

    shoulder_similarity = (
        abs(p1 - p3)
        / ((p1 + p3) / 2)
    )

    if (
        shoulder_similarity
        > 0.20
    ):
        return []

    highs_between = [
        x
        for x in pivot_highs
        if l1 < x < l3
    ]

    if len(highs_between) < 2:
        return []

    h1 = highs_between[-2]
    h2 = highs_between[-1]

    neckline = (
        float(df.iloc[h1]["high"])
        + float(df.iloc[h2]["high"])
    ) / 2

    quality = max(
        0.0,
        1.0
        - shoulder_similarity
        / 0.20
    )

    return [
        make_pattern(
            "Inverse H&S",
            "LONG",
            l1,
            l3,
            neckline,
            anchors=[
                l1,
                h1,
                l2,
                h2,
                l3
            ],
            quality=quality
        )
    ]


# ============================================================
# TRIANGLE
# ============================================================

def detect_triangle(
    df,
    pivot_highs,
    pivot_lows,
    current_i
):

    highs = [
        x
        for x in pivot_highs
        if (
            current_i
            - MAX_STRUCTURE_BARS
            <= x < current_i
        )
    ]

    lows = [
        x
        for x in pivot_lows
        if (
            current_i
            - MAX_STRUCTURE_BARS
            <= x < current_i
        )
    ]

    if (
        len(highs) < 2
        or len(lows) < 2
    ):
        return []

    h1, h2 = highs[-2:]
    l1, l2 = lows[-2:]

    if min(
        h2 - h1,
        l2 - l1
    ) < 3:
        return []

    start = min(
        h1,
        l1
    )

    end = max(
        h2,
        l2
    )

    if (
        end - start
        < MIN_STRUCTURE_BARS
    ):
        return []

    if (
        end - start
        > MAX_STRUCTURE_BARS
    ):
        return []

    high1 = float(
        df.iloc[h1]["high"]
    )

    high2 = float(
        df.iloc[h2]["high"]
    )

    low1 = float(
        df.iloc[l1]["low"]
    )

    low2 = float(
        df.iloc[l2]["low"]
    )

    upper_slope = (
        high2 - high1
    ) / (h2 - h1)

    lower_slope = (
        low2 - low1
    ) / (l2 - l1)

    # Contracting triangle
    if upper_slope >= 0:
        return []

    if lower_slope <= 0:
        return []

    width_start = (
        high1 - low1
    )

    width_end = (
        high2 - low2
    )

    if width_start <= 0:
        return []

    if (
        width_end
        >= width_start * 0.90
    ):
        return []

    upper_now = (
        high2
        + upper_slope
        * (current_i - h2)
    )

    lower_now = (
        low2
        + lower_slope
        * (current_i - l2)
    )

    if (
        upper_now
        <= lower_now
    ):
        return []

    quality = (
        width_start
        - width_end
    ) / width_start

    return [
        make_pattern(
            "Triangle",
            "BOTH",
            start,
            end,
            (
                upper_now
                + lower_now
            ) / 2,
            upper_level=upper_now,
            lower_level=lower_now,
            anchors=[
                h1,
                h2,
                l1,
                l2
            ],
            quality=quality
        )
    ]


# ============================================================
# WEDGE
# ============================================================

def detect_wedge(
    df,
    pivot_highs,
    pivot_lows,
    current_i
):

    highs = [
        x
        for x in pivot_highs
        if (
            current_i
            - MAX_STRUCTURE_BARS
            <= x < current_i
        )
    ]

    lows = [
        x
        for x in pivot_lows
        if (
            current_i
            - MAX_STRUCTURE_BARS
            <= x < current_i
        )
    ]

    if (
        len(highs) < 2
        or len(lows) < 2
    ):
        return []

    h1, h2 = highs[-2:]
    l1, l2 = lows[-2:]

    if min(
        h2 - h1,
        l2 - l1
    ) < 3:
        return []

    start = min(
        h1,
        l1
    )

    end = max(
        h2,
        l2
    )

    if (
        end - start
        < MIN_STRUCTURE_BARS
    ):
        return []

    if (
        end - start
        > MAX_STRUCTURE_BARS
    ):
        return []

    high1 = float(
        df.iloc[h1]["high"]
    )

    high2 = float(
        df.iloc[h2]["high"]
    )

    low1 = float(
        df.iloc[l1]["low"]
    )

    low2 = float(
        df.iloc[l2]["low"]
    )

    upper_slope = (
        high2 - high1
    ) / (h2 - h1)

    lower_slope = (
        low2 - low1
    ) / (l2 - l1)

    # Falling wedge
    if (
        upper_slope < 0
        and lower_slope < 0
    ):

        if (
            abs(lower_slope)
            <= abs(upper_slope)
        ):
            return []

    # Rising wedge
    elif (
        upper_slope > 0
        and lower_slope > 0
    ):

        if (
            abs(upper_slope)
            <= abs(lower_slope)
        ):
            return []

    else:
        return []

    width_start = (
        high1 - low1
    )

    width_end = (
        high2 - low2
    )

    if width_start <= 0:
        return []

    if (
        width_end
        >= width_start * 0.90
    ):
        return []

    upper_now = (
        high2
        + upper_slope
        * (current_i - h2)
    )

    lower_now = (
        low2
        + lower_slope
        * (current_i - l2)
    )

    if (
        upper_now
        <= lower_now
    ):
        return []

    quality = (
        width_start
        - width_end
    ) / width_start

    return [
        make_pattern(
            "Wedge",
            "BOTH",
            start,
            end,
            (
                upper_now
                + lower_now
            ) / 2,
            upper_level=upper_now,
            lower_level=lower_now,
            anchors=[
                h1,
                h2,
                l1,
                l2
            ],
            quality=quality
        )
    ]


# ============================================================
# FLAG
# ============================================================

def detect_flag(
    df,
    current_i
):

    if (
        current_i
        < FLAG_IMPULSE_LOOKBACK
    ):
        return []

    impulse_start = (
        current_i
        - FLAG_IMPULSE_LOOKBACK
    )

    consolidation_start = (
        current_i
        - FLAG_CONSOLIDATION_BARS
    )

    impulse = df.iloc[
        impulse_start:
        consolidation_start
    ]

    consolidation = df.iloc[
        consolidation_start:
        current_i
    ]

    if len(impulse) < 10:
        return []

    if (
        len(consolidation)
        < FLAG_CONSOLIDATION_BARS
    ):
        return []

    impulse_open = float(
        impulse.iloc[0]["open"]
    )

    impulse_close = float(
        impulse.iloc[-1]["close"]
    )

    if impulse_open <= 0:
        return []

    impulse_return = (
        impulse_close
        - impulse_open
    ) / impulse_open

    if (
        abs(impulse_return)
        < FLAG_MIN_IMPULSE
    ):
        return []

    candle_moves = (
        impulse["close"]
        - impulse["open"]
    ).abs().sum()

    if candle_moves <= 0:
        return []

    efficiency = (
        abs(
            impulse_close
            - impulse_open
        )
        / candle_moves
    )

    if (
        efficiency
        < FLAG_MIN_DIRECTIONAL_EFFICIENCY
    ):
        return []

    direction = (
        "LONG"
        if impulse_return > 0
        else "SHORT"
    )

    impulse_high = float(
        impulse["high"].max()
    )

    impulse_low = float(
        impulse["low"].min()
    )

    impulse_range = (
        impulse_high
        - impulse_low
    )

    if impulse_range <= 0:
        return []

    consolidation_high = float(
        consolidation["high"].max()
    )

    consolidation_low = float(
        consolidation["low"].min()
    )

    consolidation_range = (
        consolidation_high
        - consolidation_low
    )

    if (
        consolidation_range
        > impulse_range
        * FLAG_MAX_RANGE_TO_IMPULSE
    ):
        return []

    if direction == "LONG":

        retracement = (
            impulse_close
            - consolidation_low
        ) / impulse_range

        retracement = max(
            0,
            retracement
        )

        if (
            retracement
            > FLAG_MAX_RETRACE
        ):
            return []

        breakout_level = (
            consolidation_high
        )

    else:

        retracement = (
            consolidation_high
            - impulse_close
        ) / impulse_range

        retracement = max(
            0,
            retracement
        )

        if (
            retracement
            > FLAG_MAX_RETRACE
        ):
            return []

        breakout_level = (
            consolidation_low
        )

    return [
        make_pattern(
            "Flag",
            direction,
            impulse_start,
            current_i - 1,
            breakout_level,
            anchors=[
                impulse_start,
                consolidation_start,
                current_i - 1
            ],
            quality=efficiency
        )
    ]


# ============================================================
# ALL PATTERNS
# ============================================================

def detect_patterns(
    df,
    pivot_highs,
    pivot_lows,
    current_i
):

    patterns = []

    patterns.extend(
        detect_double_bottom(
            df,
            pivot_lows,
            current_i
        )
    )

    patterns.extend(
        detect_double_top(
            df,
            pivot_highs,
            current_i
        )
    )

    patterns.extend(
        detect_head_shoulders(
            df,
            pivot_highs,
            pivot_lows,
            current_i
        )
    )

    patterns.extend(
        detect_inverse_head_shoulders(
            df,
            pivot_highs,
            pivot_lows,
            current_i
        )
    )

    patterns.extend(
        detect_triangle(
            df,
            pivot_highs,
            pivot_lows,
            current_i
        )
    )

    patterns.extend(
        detect_wedge(
            df,
            pivot_highs,
            pivot_lows,
            current_i
        )
    )

    patterns.extend(
        detect_flag(
            df,
            current_i
        )
    )

    return patterns


# ============================================================
# PATTERN FINGERPRINT
# ============================================================

def pattern_fingerprint(p):

    return (
        p["name"],
        p["direction"],
        p["start"],
        p["end"],
        p["anchors"],
    )


# ============================================================
# STRUCTURAL OVERLAP
# ============================================================

def structures_overlap(
    a,
    b
):

    a_start = a["start"]
    a_end = a["end"]

    b_start = b["start"]
    b_end = b["end"]

    overlap_start = max(
        a_start,
        b_start
    )

    overlap_end = min(
        a_end,
        b_end
    )

    if (
        overlap_end
        < overlap_start
    ):
        return False

    overlap = (
        overlap_end
        - overlap_start
        + 1
    )

    shorter = min(
        a_end - a_start + 1,
        b_end - b_start + 1
    )

    if shorter <= 0:
        return False

    return (
        overlap / shorter
    ) >= 0.70


# ============================================================
# BREAKOUT
# ============================================================

def breakout_signal(
    df,
    i,
    p
):

    if i <= p["end"]:
        return None

    prev_close = float(
        df.iloc[i - 1]["close"]
    )

    close = float(
        df.iloc[i]["close"]
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if p["direction"] == "LONG":

        level = p[
            "breakout_level"
        ]

        if (
            prev_close <= level
            and close
            > level
            * (
                1 + BREAKOUT_BUFFER
            )
        ):
            return "LONG"

        return None

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if p["direction"] == "SHORT":

        level = p[
            "breakout_level"
        ]

        if (
            prev_close >= level
            and close
            < level
            * (
                1 - BREAKOUT_BUFFER
            )
        ):
            return "SHORT"

        return None

    # --------------------------------------------------------
    # TRIANGLE / WEDGE
    # --------------------------------------------------------

    if p["direction"] == "BOTH":

        upper = p[
            "upper_level"
        ]

        lower = p[
            "lower_level"
        ]

        if (
            upper is None
            or lower is None
        ):
            return None

        # Upper boundary -> LONG
        if (
            prev_close <= upper
            and close
            > upper
            * (
                1 + BREAKOUT_BUFFER
            )
        ):
            return "LONG"

        # Lower boundary -> SHORT
        if (
            prev_close >= lower
            and close
            < lower
            * (
                1 - BREAKOUT_BUFFER
            )
        ):
            return "SHORT"

    return None


# ============================================================
# 5M RETEST + CONFIRMATION
# ============================================================

def find_entry(
    df5,
    breakout_time,
    direction,
    breakout_level
):

    future = df5[
        df5["datetime"]
        > breakout_time
    ].copy()

    if future.empty:
        return None

    future = future.head(
        RETEST_MAX_BARS
        + CONFIRM_MAX_BARS
        + 5
    )

    rows = future.to_dict(
        "records"
    )

    retest_pos = None

    # --------------------------------------------------------
    # FIRST RETEST ONLY
    # --------------------------------------------------------

    for pos, row in enumerate(
        rows
    ):

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        close = float(
            row["close"]
        )

        touched = (
            low
            <= breakout_level
            <= high
        )

        if not touched:
            continue

        if direction == "LONG":

            if (
                close
                >= breakout_level
            ):
                retest_pos = pos
                break

        else:

            if (
                close
                <= breakout_level
            ):
                retest_pos = pos
                break

    if retest_pos is None:
        return None

    # --------------------------------------------------------
    # CONFIRMATION AFTER RETEST
    # --------------------------------------------------------

    start = (
        retest_pos + 1
    )

    end = min(
        len(rows),
        start + CONFIRM_MAX_BARS
    )

    for pos in range(
        start,
        end
    ):

        row = rows[pos]

        o = float(
            row["open"]
        )

        h = float(
            row["high"]
        )

        l = float(
            row["low"]
        )

        c = float(
            row["close"]
        )

        candle_range = (
            h - l
        )

        if candle_range <= 0:
            continue

        body = abs(
            c - o
        )

        body_ratio = (
            body
            / candle_range
        )

        if (
            body_ratio
            < MIN_BODY_RATIO
        ):
            continue

        # ----------------------------------------------------
        # LONG CONFIRMATION
        # ----------------------------------------------------

        if direction == "LONG":

            if c <= o:
                continue

            close_position = (
                c - l
            ) / candle_range

            if (
                close_position
                < MIN_CLOSE_POSITION
            ):
                continue

            if (
                c
                <= breakout_level
            ):
                continue

        # ----------------------------------------------------
        # SHORT CONFIRMATION
        # ----------------------------------------------------

        else:

            if c >= o:
                continue

            close_position = (
                h - c
            ) / candle_range

            if (
                close_position
                < MIN_CLOSE_POSITION
            ):
                continue

            if (
                c
                >= breakout_level
            ):
                continue

        return {
            "entry_time":
                row["datetime"],

            "entry_price":
                c,

            "retest_time":
                rows[
                    retest_pos
                ]["datetime"],

            "confirmation_time":
                row["datetime"],
        }

    return None


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df5,
    entry_time,
    entry_price,
    direction
):

    future = df5[
        df5["datetime"]
        > entry_time
    ].copy()

    if future.empty:
        return None

    max_bars = int(
        MAX_HOLD_HOURS
        * 60
        / 5
    )

    future = future.head(
        max_bars
    )

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

    for _, row in future.iterrows():

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
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

        # ----------------------------------------------------
        # CONSERVATIVE SAME-CANDLE RULE
        # ----------------------------------------------------

        if (
            hit_tp
            and hit_sl
        ):

            return {
                "result":
                    "FAILURE",

                "exit_time":
                    row["datetime"],

                "exit_price":
                    sl,
            }

        if hit_sl:

            return {
                "result":
                    "FAILURE",

                "exit_time":
                    row["datetime"],

                "exit_price":
                    sl,
            }

        if hit_tp:

            return {
                "result":
                    "SUCCESS",

                "exit_time":
                    row["datetime"],

                "exit_price":
                    tp,
            }

    # Unresolved = excluded
    return None


# ============================================================
# EMPTY DIAGNOSTICS
# ============================================================

def empty_diagnostics():

    return defaultdict(
        lambda: {
            "candidates": 0,
            "breakouts": 0,
            "retests": 0,
            "confirmations": 0,
        }
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol,
    start_dt,
    end_dt
):

    print()
    print("=" * 60)
    print(symbol)
    print("=" * 60)

    diagnostics = (
        empty_diagnostics()
    )

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    try:

        df1h = fetch_candles(
            symbol,
            MAIN_INTERVAL,
            start_dt,
            end_dt
        )

    except Exception as e:

        print(
            f"{symbol} UNAVAILABLE "
            f"1H: {e}"
        )

        return [], diagnostics, "UNAVAILABLE"

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    try:

        df5 = fetch_candles(
            symbol,
            ENTRY_INTERVAL,
            start_dt,
            end_dt
        )

    except Exception as e:

        print(
            f"{symbol} UNAVAILABLE "
            f"5M: {e}"
        )

        return [], diagnostics, "UNAVAILABLE"

    print(
        f"{symbol} "
        f"1H={len(df1h):,} "
        f"5M={len(df5):,}"
    )

    # --------------------------------------------------------
    # Minimum data
    # --------------------------------------------------------

    if len(df1h) < 500:

        print(
            f"{symbol}: "
            f"INSUFFICIENT 1H HISTORY"
        )

        return [], diagnostics, "INSUFFICIENT"

    if len(df5) < 5000:

        print(
            f"{symbol}: "
            f"INSUFFICIENT 5M HISTORY"
        )

        return [], diagnostics, "INSUFFICIENT"

    # --------------------------------------------------------
    # PIVOTS
    # --------------------------------------------------------

    pivot_highs, pivot_lows = (
        find_pivots(df1h)
    )

    active = []

    seen_fingerprints = set()

    # Same symbol + same 1H breakout candle
    # cannot create multiple trades.
    consumed_events = set()

    trades = []

    # --------------------------------------------------------
    # 1H LOOP
    # --------------------------------------------------------

    start_index = max(
        100,
        PIVOT_LEFT
        + PIVOT_RIGHT
        + 10
    )

    for i in range(
        start_index,
        len(df1h)
    ):

        # ----------------------------------------------------
        # NEW PATTERNS
        # ----------------------------------------------------

        new_patterns = detect_patterns(
            df1h,
            pivot_highs,
            pivot_lows,
            i
        )

        for p in new_patterns:

            fp = pattern_fingerprint(
                p
            )

            if fp in seen_fingerprints:
                continue

            # ------------------------------------------------
            # Avoid repeated same-type structures
            # overlapping heavily.
            # ------------------------------------------------

            overlap = False

            for old in active:

                if (
                    old["state"]
                    != "WAITING_FOR_BREAKOUT"
                ):
                    continue

                if (
                    old["name"]
                    != p["name"]
                ):
                    continue

                if (
                    old["direction"]
                    != p["direction"]
                ):
                    continue

                if structures_overlap(
                    old,
                    p
                ):
                    overlap = True
                    break

            if overlap:
                continue

            seen_fingerprints.add(
                fp
            )

            active.append(
                p
            )

            diagnostics[
                p["name"]
            ]["candidates"] += 1

        # ----------------------------------------------------
        # ACTIVE PATTERNS
        # ----------------------------------------------------

        still_active = []

        for p in active:

            if (
                p["state"]
                != "WAITING_FOR_BREAKOUT"
            ):
                continue

            # Pattern expiration
            if (
                i - p["end"]
                > 30
            ):

                p["state"] = "DEAD"

                continue

            signal = breakout_signal(
                df1h,
                i,
                p
            )

            if signal is None:

                still_active.append(
                    p
                )

                continue

            # ------------------------------------------------
            # BREAKOUT EVENT
            # ------------------------------------------------

            breakout_event = (
                symbol,
                i,
                signal
            )

            if (
                breakout_event
                in consumed_events
            ):

                p["state"] = "DEAD"

                continue

            consumed_events.add(
                breakout_event
            )

            diagnostics[
                p["name"]
            ]["breakouts"] += 1

            # 1H candle closes one hour after
            # its opening timestamp.
            breakout_time = (
                df1h.iloc[i]["datetime"]
                + pd.Timedelta(
                    hours=1
                )
            )

            # ------------------------------------------------
            # Direction-specific level
            # ------------------------------------------------

            if signal == "LONG":

                if (
                    p["direction"]
                    == "BOTH"
                ):

                    breakout_level = (
                        p["upper_level"]
                    )

                else:

                    breakout_level = (
                        p["breakout_level"]
                    )

            else:

                if (
                    p["direction"]
                    == "BOTH"
                ):

                    breakout_level = (
                        p["lower_level"]
                    )

                else:

                    breakout_level = (
                        p["breakout_level"]
                    )

            if breakout_level is None:

                p["state"] = "DEAD"

                continue

            # ------------------------------------------------
            # FIRST 5M RETEST
            # ------------------------------------------------

            entry = find_entry(
                df5,
                breakout_time,
                signal,
                float(
                    breakout_level
                )
            )

            if entry is None:

                p["state"] = "DEAD"

                continue

            diagnostics[
                p["name"]
            ]["retests"] += 1

            diagnostics[
                p["name"]
            ]["confirmations"] += 1

            # ------------------------------------------------
            # TRADE
            # ------------------------------------------------

            trade = simulate_trade(
                df5,
                entry["entry_time"],
                entry["entry_price"],
                signal
            )

            # Unresolved trade
            if trade is None:

                p["state"] = "DEAD"

                continue

            trades.append(
                {
                    "symbol":
                        symbol,

                    "pattern":
                        p["name"],

                    "direction":
                        signal,

                    "pattern_start":
                        df1h.iloc[
                            p["start"]
                        ]["datetime"],

                    "pattern_end":
                        df1h.iloc[
                            p["end"]
                        ]["datetime"],

                    "breakout_time":
                        df1h.iloc[
                            i
                        ]["datetime"],

                    "retest_time":
                        entry[
                            "retest_time"
                        ],

                    "entry_time":
                        entry[
                            "entry_time"
                        ],

                    "entry_price":
                        entry[
                            "entry_price"
                        ],

                    "result":
                        trade[
                            "result"
                        ],

                    "exit_time":
                        trade[
                            "exit_time"
                        ],

                    "exit_price":
                        trade[
                            "exit_price"
                        ],
                }
            )

            # One trade per pattern
            p["state"] = "DEAD"

        active = still_active

    print(
        f"{symbol} completed trades: "
        f"{len(trades)}"
    )

    return (
        trades,
        diagnostics,
        "OK"
    )


# ============================================================
# FINAL SUMMARY
# ============================================================

def print_summary(
    all_trades,
    all_diagnostics,
    symbols,
    symbol_status
):

    print()
    print("=" * 70)
    print("FINAL BACKTEST RESULT")
    print("=" * 70)

    total = len(
        all_trades
    )

    success = sum(
        1
        for t in all_trades
        if t["result"]
        == "SUCCESS"
    )

    failure = sum(
        1
        for t in all_trades
        if t["result"]
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

    raw_break_even = (
        SL_PCT
        / (
            TP_PCT
            + SL_PCT
        )
        * 100
    )

    print(
        f"Universe configured: "
        f"{len(symbols)}"
    )

    tested = sum(
        1
        for s in symbols
        if symbol_status.get(s)
        == "OK"
    )

    print(
        f"Symbols with usable data: "
        f"{tested}"
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

    print(
        f"RAW BREAK-EVEN: "
        f"{raw_break_even:.2f}%"
    )

    print(
        f"EDGE VS RAW BREAK-EVEN: "
        f"{success_rate - raw_break_even:+.2f} pp"
    )

    # --------------------------------------------------------
    # SYMBOL STATUS
    # --------------------------------------------------------

    print()
    print(
        "20-SYMBOL STATUS"
    )

    print("-" * 70)

    for symbol in symbols:

        print(
            f"{symbol:<18} "
            f"{symbol_status.get(symbol, 'UNKNOWN')}"
        )

    # --------------------------------------------------------
    # PATTERN BREAKDOWN
    # --------------------------------------------------------

    print()
    print(
        "PATTERN BREAKDOWN"
    )

    print("-" * 70)

    pattern_stats = defaultdict(
        lambda: {
            "trades": 0,
            "success": 0,
            "failure": 0,
        }
    )

    for t in all_trades:

        p = t["pattern"]

        pattern_stats[p][
            "trades"
        ] += 1

        if (
            t["result"]
            == "SUCCESS"
        ):

            pattern_stats[p][
                "success"
            ] += 1

        else:

            pattern_stats[p][
                "failure"
            ] += 1

    for p, s in sorted(
        pattern_stats.items(),
        key=lambda x:
        -x[1]["trades"]
    ):

        trades = s["trades"]

        wr = (
            s["success"]
            / trades
            * 100
            if trades
            else 0
        )

        print(
            f"{p:<22}"
            f"{trades:>7} "
            f"{s['success']:>7} "
            f"{s['failure']:>7} "
            f"{wr:>7.2f}%"
        )

    # --------------------------------------------------------
    # SYMBOL BREAKDOWN
    # --------------------------------------------------------

    print()
    print(
        "SYMBOL BREAKDOWN"
    )

    print("-" * 70)

    symbol_stats = defaultdict(
        lambda: {
            "trades": 0,
            "success": 0,
            "failure": 0,
        }
    )

    for t in all_trades:

        s = t["symbol"]

        symbol_stats[s][
            "trades"
        ] += 1

        if (
            t["result"]
            == "SUCCESS"
        ):

            symbol_stats[s][
                "success"
            ] += 1

        else:

            symbol_stats[s][
                "failure"
            ] += 1

    for symbol in symbols:

        s = symbol_stats[
            symbol
        ]

        trades = s[
            "trades"
        ]

        wr = (
            s["success"]
            / trades
            * 100
            if trades
            else 0
        )

        print(
            f"{symbol:<18}"
            f"{trades:>7} "
            f"{s['success']:>7} "
            f"{s['failure']:>7} "
            f"{wr:>7.2f}%"
        )

    # --------------------------------------------------------
    # LIFECYCLE
    # --------------------------------------------------------

    print()
    print(
        "LIFECYCLE DIAGNOSTICS"
    )

    print("-" * 70)

    names = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag",
    ]

    for name in names:

        d = all_diagnostics.get(
            name,
            {
                "candidates": 0,
                "breakouts": 0,
                "retests": 0,
                "confirmations": 0,
            }
        )

        print(
            f"{name:<22}"
            f"Candidates="
            f"{d['candidates']:<6} "
            f"Breakouts="
            f"{d['breakouts']:<6} "
            f"Retests="
            f"{d['retests']:<6} "
            f"Confirmations="
            f"{d['confirmations']:<6}"
        )


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(
    all_trades
):

    if not all_trades:

        print(
            "\nNo completed trades "
            "to save."
        )

        return

    df = pd.DataFrame(
        all_trades
    )

    filename = (
        "kraken_pattern_backtest_1y_trades.csv"
    )

    df.to_csv(
        filename,
        index=False
    )

    print()
    print(
        "Trade file saved:"
    )

    print(
        filename
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
    print(
        "VERSION: 4.1"
    )
    print("=" * 70)

    print(
        f"Period: "
        f"{DAYS} days"
    )

    print(
        "Main timeframe: 1H"
    )

    print(
        "Entry timeframe: 5M"
    )

    print(
        f"TP: "
        f"{TP_PCT * 100:.2f}%"
    )

    print(
        f"SL: "
        f"{SL_PCT * 100:.2f}%"
    )

    print(
        f"Max hold: "
        f"{MAX_HOLD_HOURS} hours"
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
        "Closed candles only: YES"
    )

    print(
        "Real trading: DISABLED"
    )

    # --------------------------------------------------------
    # UNIVERSE
    # --------------------------------------------------------

    print_universe()

    # --------------------------------------------------------
    # DIAGNOSTIC DISCOVERY
    # --------------------------------------------------------

    print()
    print(
        "Checking Kraken symbol availability..."
    )

    available = (
        discover_available_symbols()
    )

    print(
        f"Kraken perpetuals discovered: "
        f"{len(available)}"
    )

    for symbol in FIXED_SYMBOLS:

        if symbol in available:

            print(
                f"{symbol:<18} AVAILABLE"
            )

        else:

            print(
                f"{symbol:<18} "
                f"NOT FOUND IN DISCOVERY"
            )

    # --------------------------------------------------------
    # PERIOD
    # --------------------------------------------------------

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        - timedelta(
            days=DAYS
        )
    )

    # --------------------------------------------------------
    # RESULTS
    # --------------------------------------------------------

    all_trades = []

    all_diagnostics = (
        empty_diagnostics()
    )

    symbol_status = {}

    # --------------------------------------------------------
    # PROCESS ALL 20
    # --------------------------------------------------------

    for symbol in FIXED_SYMBOLS:

        try:

            result = process_symbol(
                symbol,
                start_dt,
                end_dt
            )

            trades, diagnostics, status = (
                result
            )

            symbol_status[
                symbol
            ] = status

            all_trades.extend(
                trades
            )

            for name, d in (
                diagnostics.items()
            ):

                for key in [
                    "candidates",
                    "breakouts",
                    "retests",
                    "confirmations",
                ]:

                    all_diagnostics[
                        name
                    ][key] += d[key]

        except Exception as e:

            print()
            print(
                f"{symbol} ERROR: "
                f"{e}"
            )

            symbol_status[
                symbol
            ] = "ERROR"

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print_summary(
        all_trades,
        all_diagnostics,
        FIXED_SYMBOLS,
        symbol_status
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_results(
        all_trades
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
