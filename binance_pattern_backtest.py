# ============================================================
# KRAKEN FUTURES PATTERN BACKTEST 1Y
# VERSION 4.4 - RR COMPARISON
# ============================================================
#
# DATA:
#   Kraken Futures
#
# UNIVERSE:
#   20 FIXED ASSETS
#   Dynamic exact perpetual-contract discovery
#
# TIMEFRAMES:
#   1H = Pattern + Breakout
#   5M = FIRST Retest + Confirmation + Trade
#
# STRATEGY:
#
#   1H Pattern
#       ↓
#   1H CLOSED-CANDLE BREAKOUT
#       ↓
#   FIRST 5M RETEST
#       ↓
#   5M CONFIRMATION
#       ↓
#   ENTRY
#
# RR TESTS:
#
#   RR 1.0 = TP 1.00% / SL 1.00%
#   RR 1.5 = TP 1.50% / SL 1.00%
#   RR 2.0 = TP 2.00% / SL 1.00%
#
# IMPORTANT:
# - ENTRY LOGIC IS IDENTICAL FOR ALL RR TESTS
# - SAME ENTRY IS TESTED AGAINST ALL 3 RR VALUES
# - CLOSED CANDLES ONLY
# - NO LOOKAHEAD
# - ONE TRADE PER PATTERN INSTANCE
# - FIRST RETEST ONLY
# - CONFIRMATION MUST COME AFTER RETEST
# - ENTRY CANDLE IS NOT USED FOR TP/SL
# - SAME CANDLE TP + SL = SL FIRST
# - UNRESOLVED TRADE = EXCLUDED
#
# ============================================================

import time
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timedelta, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

DAYS = 365

MAIN_INTERVAL = "1h"
ENTRY_INTERVAL = "5m"

# ------------------------------------------------------------
# STOP LOSS IS FIXED
# ------------------------------------------------------------

SL_PCT = 0.01

# ------------------------------------------------------------
# RR TESTS
#
# TP changes.
# SL remains fixed at 1%.
# ------------------------------------------------------------

RR_TESTS = {
    "RR_1.0": 0.01,
    "RR_1.5": 0.015,
    "RR_2.0": 0.02,
}

MAX_HOLD_HOURS = 48

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.10

CANDLE_CHUNK = 1900


# ============================================================
# FIXED 20-ASSET UNIVERSE
# ============================================================

FIXED_ASSETS = [
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

EXPECTED_UNIVERSE_SIZE = len(
    FIXED_ASSETS
)


# ============================================================
# PATTERN PARAMETERS
# ============================================================

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

DOUBLE_TOLERANCE = 0.015
DOUBLE_MIN_SEPARATION = 5
DOUBLE_MAX_SEPARATION = 60

HS_MIN_SEPARATION = 5
HS_MAX_SEPARATION = 80

MIN_STRUCTURE_BARS = 12
MAX_STRUCTURE_BARS = 60

FLAG_IMPULSE_LOOKBACK = 35
FLAG_CONSOLIDATION_BARS = 15

FLAG_MIN_IMPULSE = 0.04
FLAG_MAX_RETRACE = 0.55
FLAG_MAX_RANGE_TO_IMPULSE = 0.60
FLAG_MIN_DIRECTIONAL_EFFICIENCY = 0.45

BREAKOUT_BUFFER = 0.0010

RETEST_MAX_BARS = 12
CONFIRM_MAX_BARS = 6

MIN_BODY_RATIO = 0.45
MIN_CLOSE_POSITION = 0.60


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "KrakenPatternBacktest/4.4"
    }
)


# ============================================================
# GENERAL HELPERS
# ============================================================

def pct(a, b):

    if b == 0:
        return 0.0

    return abs(a - b) / abs(b)


# ============================================================
# API
# ============================================================

def api_get(path, params=None):

    url = BASE_URL + path

    last_error = None

    for attempt in range(3):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            data = response.json()

            if isinstance(data, dict):

                if data.get("result") == "error":

                    raise RuntimeError(
                        str(data)
                    )

                if data.get("error"):

                    raise RuntimeError(
                        str(data["error"])
                    )

            time.sleep(
                REQUEST_SLEEP
            )

            return data

        except Exception as exc:

            last_error = exc

            if attempt < 2:

                time.sleep(
                    1.0 * (attempt + 1)
                )

    raise RuntimeError(
        f"API request failed: "
        f"{path} | {last_error}"
    )


# ============================================================
# KRAKEN INSTRUMENT DISCOVERY
# ============================================================

def discover_instruments():

    data = api_get(
        "/derivatives/api/v3/instruments"
    )

    raw = data.get(
        "instruments",
        []
    )

    if not isinstance(raw, list):

        raise RuntimeError(
            "Invalid instruments response"
        )

    instruments = []

    for item in raw:

        if not isinstance(item, dict):
            continue

        symbol = str(
            item.get("symbol")
            or item.get("instrument")
            or ""
        ).upper().strip()

        if not symbol:
            continue

        instruments.append(item)

    return instruments


# ============================================================
# PERPETUAL CHECK
# ============================================================

def is_perpetual_instrument(item):

    symbol = str(
        item.get("symbol")
        or item.get("instrument")
        or ""
    ).upper()

    instrument_type = str(
        item.get("type")
        or item.get("instrumentType")
        or ""
    ).upper()

    if "PERPETUAL" in instrument_type:
        return True

    if "PERPETUAL" in symbol:
        return True

    if symbol.startswith("PI_"):
        return True

    if symbol.startswith("PF_"):
        return True

    return False


# ============================================================
# EXACT CONTRACT MATCH
# ============================================================

def choose_contract_for_asset(
    asset,
    instruments,
):

    valid_symbols = {
        f"PF_{asset}USD",
        f"PI_{asset}USD",
    }

    candidates = []

    for item in instruments:

        if not is_perpetual_instrument(
            item
        ):
            continue

        symbol = str(
            item.get("symbol")
            or item.get("instrument")
            or ""
        ).upper().strip()

        if symbol not in valid_symbols:
            continue

        candidates.append(symbol)

    if not candidates:
        return None

    if f"PF_{asset}USD" in candidates:
        return f"PF_{asset}USD"

    if f"PI_{asset}USD" in candidates:
        return f"PI_{asset}USD"

    return candidates[0]


# ============================================================
# BUILD UNIVERSE
# ============================================================

def build_dynamic_universe():

    instruments = discover_instruments()

    mapping = {}

    print()
    print("=" * 70)
    print(
        "KRAKEN FUTURES CONTRACT DISCOVERY"
    )
    print("=" * 70)

    print(
        f"Configured assets: "
        f"{EXPECTED_UNIVERSE_SIZE}"
    )

    for asset in FIXED_ASSETS:

        contract = choose_contract_for_asset(
            asset,
            instruments,
        )

        mapping[asset] = contract

        if contract:

            print(
                f"{asset:<8} -> {contract}"
            )

        else:

            print(
                f"{asset:<8} -> "
                f"NO EXACT PERPETUAL CONTRACT"
            )

    print("=" * 70)

    return mapping


# ============================================================
# CANDLE RESPONSE PARSER
# ============================================================

def parse_candle_response(data):

    rows = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "history",
            "result",
        ):

            value = data.get(key)

            if isinstance(value, list):

                rows = value
                break

    elif isinstance(data, list):

        rows = data

    if not rows:
        return []

    output = []

    for row in rows:

        if isinstance(row, dict):

            ts = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
            )

            o = row.get("open")
            h = row.get("high")
            l = row.get("low")
            c = row.get("close")

            v = (
                row.get("volume")
                or row.get("vol")
                or 0
            )

        elif (
            isinstance(row, (list, tuple))
            and len(row) >= 5
        ):

            ts = row[0]
            o = row[1]
            h = row[2]
            l = row[3]
            c = row[4]

            v = (
                row[5]
                if len(row) > 5
                else 0
            )

        else:

            continue

        try:

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000.0

            output.append(
                {
                    "timestamp": int(ts),
                    "open": float(o),
                    "high": float(h),
                    "low": float(l),
                    "close": float(c),
                    "volume": float(v),
                }
            )

        except Exception:

            continue

    return output


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(
    symbol,
    interval,
    start_ts,
    end_ts,
):

    all_rows = []

    cursor = int(start_ts)
    end_ts = int(end_ts)

    interval_seconds_map = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }

    interval_seconds = (
        interval_seconds_map.get(
            interval,
            3600,
        )
    )

    while cursor < end_ts:

        chunk_end = min(
            end_ts,
            cursor
            + CANDLE_CHUNK
            * interval_seconds,
        )

        path = (
            f"/api/charts/v1/trade/"
            f"{symbol}/{interval}"
        )

        params = {
            "from": cursor,
            "to": chunk_end,
        }

        try:

            data = api_get(
                path,
                params=params,
            )

            rows = parse_candle_response(
                data
            )

            if rows:
                all_rows.extend(rows)

            next_cursor = (
                chunk_end
                + interval_seconds
            )

            if next_cursor <= cursor:
                break

            cursor = next_cursor

        except Exception as exc:

            print(
                f"  Candle fetch error "
                f"{symbol} {interval}: "
                f"{exc}"
            )

            break

    if not all_rows:

        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    df = pd.DataFrame(
        all_rows
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    now_ts = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    df = df[
        df["timestamp"] <= now_ts
    ].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# EXPECTED CANDLE COUNT
# ============================================================

def expected_candles(interval):

    interval_minutes = {
        "1h": 60,
        "5m": 5,
    }

    if interval not in interval_minutes:

        raise ValueError(
            f"Unsupported interval: "
            f"{interval}"
        )

    total_minutes = (
        DAYS * 24 * 60
    )

    return int(
        total_minutes
        / interval_minutes[interval]
    )


# ============================================================
# HISTORY STATUS
# ============================================================

def history_status(
    df_1h,
    df_5m,
):

    expected_1h = expected_candles(
        "1h"
    )

    expected_5m = expected_candles(
        "5m"
    )

    actual_1h = len(df_1h)
    actual_5m = len(df_5m)

    minimum_1h = int(
        expected_1h * 0.90
    )

    minimum_5m = int(
        expected_5m * 0.90
    )

    if actual_1h < minimum_1h:

        return (
            "INSUFFICIENT_1H",
            expected_1h,
            expected_5m,
        )

    if actual_5m < minimum_5m:

        return (
            "INSUFFICIENT_5M",
            expected_1h,
            expected_5m,
        )

    if (
        actual_1h >= int(
            expected_1h * 0.98
        )
        and
        actual_5m >= int(
            expected_5m * 0.98
        )
    ):

        return (
            "OK",
            expected_1h,
            expected_5m,
        )

    return (
        "PARTIAL_HISTORY",
        expected_1h,
        expected_5m,
    )


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    n = len(df)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        left_h = h[
            i - PIVOT_LEFT:i
        ]

        right_h = h[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        left_l = l[
            i - PIVOT_LEFT:i
        ]

        right_l = l[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            h[i] > left_h.max()
            and h[i] >= right_h.max()
        ):

            highs.append(i)

        if (
            l[i] < left_l.min()
            and l[i] <= right_l.min()
        ):

            lows.append(i)

    return highs, lows


# ============================================================
# PATTERN OBJECT
# ============================================================

def make_pattern(
    pattern_type,
    direction,
    start_i,
    end_i,
    breakout_level,
    upper_level=None,
    lower_level=None,
    anchors=None,
    quality=0.0,
):

    return {
        "pattern": pattern_type,
        "direction": direction,
        "start": int(start_i),
        "end": int(end_i),
        "breakout_level": float(
            breakout_level
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
        "anchors": anchors or [],
        "quality": float(quality),
        "state": "DETECTED",
    }


# ============================================================
# STRUCTURE OVERLAP
# ============================================================

def structures_overlap(a, b):

    a0 = a["start"]
    a1 = a["end"]

    b0 = b["start"]
    b1 = b["end"]

    overlap_start = max(
        a0,
        b0,
    )

    overlap_end = min(
        a1,
        b1,
    )

    if overlap_end <= overlap_start:
        return False

    overlap = (
        overlap_end
        - overlap_start
    )

    len_a = max(
        1,
        a1 - a0,
    )

    len_b = max(
        1,
        b1 - b0,
    )

    ratio = (
        overlap
        / min(
            len_a,
            len_b,
        )
    )

    return ratio >= 0.70


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(
    df,
    pivots_high,
):

    patterns = []

    for j in range(
        1,
        len(pivots_high),
    ):

        p1 = pivots_high[
            j - 1
        ]

        p2 = pivots_high[j]

        separation = p2 - p1

        if not (
            DOUBLE_MIN_SEPARATION
            <= separation
            <= DOUBLE_MAX_SEPARATION
        ):
            continue

        h1 = df.iloc[p1]["high"]
        h2 = df.iloc[p2]["high"]

        if pct(h1, h2) > DOUBLE_TOLERANCE:
            continue

        between = df.iloc[
            p1:p2 + 1
        ]

        neckline = float(
            between["low"].min()
        )

        if neckline >= min(
            h1,
            h2,
        ):
            continue

        patterns.append(
            make_pattern(
                "Double Top",
                "SHORT",
                p1,
                p2,
                neckline,
                upper_level=max(
                    h1,
                    h2,
                ),
                lower_level=neckline,
                anchors=[
                    p1,
                    p2,
                ],
                quality=(
                    1.0
                    - pct(h1, h2)
                ),
            )
        )

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(
    df,
    pivots_low,
):

    patterns = []

    for j in range(
        1,
        len(pivots_low),
    ):

        p1 = pivots_low[
            j - 1
        ]

        p2 = pivots_low[j]

        separation = p2 - p1

        if not (
            DOUBLE_MIN_SEPARATION
            <= separation
            <= DOUBLE_MAX_SEPARATION
        ):
            continue

        l1 = df.iloc[p1]["low"]
        l2 = df.iloc[p2]["low"]

        if pct(l1, l2) > DOUBLE_TOLERANCE:
            continue

        between = df.iloc[
            p1:p2 + 1
        ]

        neckline = float(
            between["high"].max()
        )

        if neckline <= max(
            l1,
            l2,
        ):
            continue

        patterns.append(
            make_pattern(
                "Double Bottom",
                "LONG",
                p1,
                p2,
                neckline,
                upper_level=neckline,
                lower_level=min(
                    l1,
                    l2,
                ),
                anchors=[
                    p1,
                    p2,
                ],
                quality=(
                    1.0
                    - pct(l1, l2)
                ),
            )
        )

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(
    df,
    pivots_high,
    pivots_low,
):

    patterns = []

    for i in range(
        len(pivots_high) - 2
    ):

        ls = pivots_high[i]
        head = pivots_high[
            i + 1
        ]
        rs = pivots_high[
            i + 2
        ]

        if not (
            HS_MIN_SEPARATION
            <= head - ls
            <= HS_MAX_SEPARATION
        ):
            continue

        if not (
            HS_MIN_SEPARATION
            <= rs - head
            <= HS_MAX_SEPARATION
        ):
            continue

        h_ls = df.iloc[
            ls
        ]["high"]

        h_head = df.iloc[
            head
        ]["high"]

        h_rs = df.iloc[
            rs
        ]["high"]

        if (
            h_head <= h_ls
            or h_head <= h_rs
        ):
            continue

        if pct(
            h_ls,
            h_rs,
        ) > 0.05:
            continue

        lows_between_1 = [
            x
            for x in pivots_low
            if ls < x < head
        ]

        lows_between_2 = [
            x
            for x in pivots_low
            if head < x < rs
        ]

        if (
            not lows_between_1
            or not lows_between_2
        ):
            continue

        n1 = min(
            lows_between_1,
            key=lambda x:
            df.iloc[x]["low"],
        )

        n2 = min(
            lows_between_2,
            key=lambda x:
            df.iloc[x]["low"],
        )

        neckline = (
            df.iloc[n1]["low"]
            + df.iloc[n2]["low"]
        ) / 2.0

        patterns.append(
            make_pattern(
                "Head & Shoulders",
                "SHORT",
                ls,
                rs,
                neckline,
                upper_level=h_head,
                lower_level=neckline,
                anchors=[
                    ls,
                    head,
                    rs,
                    n1,
                    n2,
                ],
                quality=(
                    h_head
                    / max(
                        h_ls,
                        h_rs,
                    )
                ),
            )
        )

    return patterns


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(
    df,
    pivots_high,
    pivots_low,
):

    patterns = []

    for i in range(
        len(pivots_low) - 2
    ):

        ls = pivots_low[i]
        head = pivots_low[
            i + 1
        ]
        rs = pivots_low[
            i + 2
        ]

        if not (
            HS_MIN_SEPARATION
            <= head - ls
            <= HS_MAX_SEPARATION
        ):
            continue

        if not (
            HS_MIN_SEPARATION
            <= rs - head
            <= HS_MAX_SEPARATION
        ):
            continue

        l_ls = df.iloc[
            ls
        ]["low"]

        l_head = df.iloc[
            head
        ]["low"]

        l_rs = df.iloc[
            rs
        ]["low"]

        if (
            l_head >= l_ls
            or l_head >= l_rs
        ):
            continue

        if pct(
            l_ls,
            l_rs,
        ) > 0.05:
            continue

        highs_between_1 = [
            x
            for x in pivots_high
            if ls < x < head
        ]

        highs_between_2 = [
            x
            for x in pivots_high
            if head < x < rs
        ]

        if (
            not highs_between_1
            or not highs_between_2
        ):
            continue

        n1 = max(
            highs_between_1,
            key=lambda x:
            df.iloc[x]["high"],
        )

        n2 = max(
            highs_between_2,
            key=lambda x:
            df.iloc[x]["high"],
        )

        neckline = (
            df.iloc[n1]["high"]
            + df.iloc[n2]["high"]
        ) / 2.0

        patterns.append(
            make_pattern(
                "Inverse H&S",
                "LONG",
                ls,
                rs,
                neckline,
                upper_level=neckline,
                lower_level=l_head,
                anchors=[
                    ls,
                    head,
                    rs,
                    n1,
                    n2,
                ],
                quality=(
                    min(
                        l_ls,
                        l_rs,
                    )
                    / max(
                        l_head,
                        1e-12,
                    )
                ),
            )
        )

    return patterns


# ============================================================
# LINEAR LEVEL
# ============================================================

def linear_level(
    i1,
    p1,
    i2,
    p2,
    x,
):

    if i2 == i1:
        return p2

    slope = (
        p2 - p1
    ) / (
        i2 - i1
    )

    return (
        p1
        + slope * (x - i1)
    )


# ============================================================
# TRIANGLE / WEDGE
# ============================================================

def detect_triangle_wedge(
    df,
    pivots_high,
    pivots_low,
):

    patterns = []

    if len(pivots_high) < 2:
        return patterns

    if len(pivots_low) < 2:
        return patterns

    hi1 = pivots_high[-2]
    hi2 = pivots_high[-1]

    lo1 = pivots_low[-2]
    lo2 = pivots_low[-1]

    start = min(
        hi1,
        lo1,
    )

    end = max(
        hi2,
        lo2,
    )

    bars = end - start

    if not (
        MIN_STRUCTURE_BARS
        <= bars
        <= MAX_STRUCTURE_BARS
    ):
        return patterns

    h1 = df.iloc[
        hi1
    ]["high"]

    h2 = df.iloc[
        hi2
    ]["high"]

    l1 = df.iloc[
        lo1
    ]["low"]

    l2 = df.iloc[
        lo2
    ]["low"]

    upper_now = linear_level(
        hi1,
        h1,
        hi2,
        h2,
        end,
    )

    lower_now = linear_level(
        lo1,
        l1,
        lo2,
        l2,
        end,
    )

    if upper_now <= lower_now:
        return patterns

    upper_slope = (
        h2 - h1
    )

    lower_slope = (
        l2 - l1
    )

    contracting = (
        upper_slope < 0
        and lower_slope > 0
    )

    if contracting:

        patterns.append(
            make_pattern(
                "Triangle",
                "LONG",
                start,
                end,
                upper_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

        patterns.append(
            make_pattern(
                "Triangle",
                "SHORT",
                start,
                end,
                lower_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

    rising_wedge = (
        upper_slope > 0
        and lower_slope > 0
        and upper_slope < lower_slope
    )

    if rising_wedge:

        patterns.append(
            make_pattern(
                "Wedge",
                "SHORT",
                start,
                end,
                lower_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

    falling_wedge = (
        upper_slope < 0
        and lower_slope < 0
        and upper_slope > lower_slope
    )

    if falling_wedge:

        patterns.append(
            make_pattern(
                "Wedge",
                "LONG",
                start,
                end,
                upper_now,
                upper_level=upper_now,
                lower_level=lower_now,
                anchors=[
                    hi1,
                    hi2,
                    lo1,
                    lo2,
                ],
                quality=1.0,
            )
        )

    return patterns


# ============================================================
# FLAG
# ============================================================

def detect_flags(
    df,
    current_i,
):

    patterns = []

    consolidation = (
        FLAG_CONSOLIDATION_BARS
    )

    impulse_lb = (
        FLAG_IMPULSE_LOOKBACK
    )

    if (
        current_i
        < impulse_lb
        + consolidation
    ):
        return patterns

    impulse_start = (
        current_i
        - impulse_lb
    )

    impulse_end = (
        current_i
        - consolidation
    )

    if impulse_end <= impulse_start:
        return patterns

    start_price = float(
        df.iloc[
            impulse_start
        ]["close"]
    )

    impulse_end_price = float(
        df.iloc[
            impulse_end
        ]["close"]
    )

    impulse_return = (
        impulse_end_price
        / start_price
    ) - 1.0

    impulse_abs = abs(
        impulse_return
    )

    if impulse_abs < FLAG_MIN_IMPULSE:
        return patterns

    impulse_high = float(
        df.iloc[
            impulse_start:
            impulse_end + 1
        ]["high"].max()
    )

    impulse_low = float(
        df.iloc[
            impulse_start:
            impulse_end + 1
        ]["low"].min()
    )

    impulse_range = (
        impulse_high
        - impulse_low
    )

    if impulse_range <= 0:
        return patterns

    consolidation_df = df.iloc[
        impulse_end:
        current_i + 1
    ]

    c_high = float(
        consolidation_df["high"].max()
    )

    c_low = float(
        consolidation_df["low"].min()
    )

    c_range = (
        c_high
        - c_low
    )

    if (
        c_range
        / impulse_range
        > FLAG_MAX_RANGE_TO_IMPULSE
    ):
        return patterns

    closes = (
        consolidation_df[
            "close"
        ].values
    )

    if len(closes) < 2:
        return patterns

    net_move = abs(
        closes[-1]
        - closes[0]
    )

    path = np.abs(
        np.diff(closes)
    ).sum()

    efficiency = (
        net_move / path
        if path > 0
        else 0.0
    )

    if (
        efficiency
        < FLAG_MIN_DIRECTIONAL_EFFICIENCY
    ):
        return patterns

    # Bull flag
    if impulse_return > 0:

        retrace = (
            impulse_end_price
            - closes[-1]
        ) / impulse_range

        if (
            retrace
            <= FLAG_MAX_RETRACE
        ):

            patterns.append(
                make_pattern(
                    "Flag",
                    "LONG",
                    impulse_start,
                    current_i,
                    c_high,
                    upper_level=c_high,
                    lower_level=c_low,
                    anchors=[
                        impulse_start,
                        impulse_end,
                        current_i,
                    ],
                    quality=efficiency,
                )
            )

    # Bear flag
    elif impulse_return < 0:

        retrace = (
            closes[-1]
            - impulse_end_price
        ) / impulse_range

        if (
            retrace
            <= FLAG_MAX_RETRACE
        ):

            patterns.append(
                make_pattern(
                    "Flag",
                    "SHORT",
                    impulse_start,
                    current_i,
                    c_low,
                    upper_level=c_high,
                    lower_level=c_low,
                    anchors=[
                        impulse_start,
                        impulse_end,
                        current_i,
                    ],
                    quality=efficiency,
                )
            )

    return patterns


# ============================================================
# BREAKOUT
# ============================================================

def breakout_signal(
    df,
    pattern,
    candle_i,
):

    if candle_i <= pattern["end"]:
        return False

    row = df.iloc[
        candle_i
    ]

    close = float(
        row["close"]
    )

    direction = pattern[
        "direction"
    ]

    if direction == "LONG":

        level = (
            pattern.get(
                "upper_level"
            )
            or pattern[
                "breakout_level"
            ]
        )

        return (
            close
            >
            level
            * (
                1.0
                + BREAKOUT_BUFFER
            )
        )

    level = (
        pattern.get(
            "lower_level"
        )
        or pattern[
            "breakout_level"
        ]
    )

    return (
        close
        <
        level
        * (
            1.0
            - BREAKOUT_BUFFER
        )
    )


# ============================================================
# CONFIRMATION
# ============================================================

def confirmation_ok(
    row,
    direction,
):

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
        return False

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
        return False

    if direction == "LONG":

        close_position = (
            c - l
        ) / candle_range

        if (
            close_position
            < MIN_CLOSE_POSITION
        ):
            return False

        return c > o

    close_position = (
        h - c
    ) / candle_range

    if (
        close_position
        < MIN_CLOSE_POSITION
    ):
        return False

    return c < o


# ============================================================
# FIRST RETEST + CONFIRMATION
# ============================================================

def find_entry(
    df_5m,
    breakout_time,
    direction,
    level,
):

    future = df_5m[
        df_5m["timestamp"]
        > breakout_time
    ].copy()

    if future.empty:
        return None

    future = future.head(
        RETEST_MAX_BARS
        + CONFIRM_MAX_BARS
        + 5
    )

    retest_pos = None

    for pos, (_, row) in enumerate(
        future.iterrows()
    ):

        h = float(
            row["high"]
        )

        l = float(
            row["low"]
        )

        c = float(
            row["close"]
        )

        touched = (
            l <= level <= h
        )

        if not touched:
            continue

        if direction == "LONG":

            if c >= level:

                retest_pos = pos
                break

        else:

            if c <= level:

                retest_pos = pos
                break

    if retest_pos is None:
        return None

    confirmation_slice = future.iloc[
        retest_pos + 1:
        retest_pos
        + 1
        + CONFIRM_MAX_BARS
    ]

    if confirmation_slice.empty:
        return None

    for _, row in (
        confirmation_slice.iterrows()
    ):

        if not confirmation_ok(
            row,
            direction,
        ):
            continue

        return {
            "entry_time": int(
                row["timestamp"]
            ),
            "entry_price": float(
                row["close"]
            ),
            "retest_time": int(
                future.iloc[
                    retest_pos
                ]["timestamp"]
            ),
            "retest_price": float(
                future.iloc[
                    retest_pos
                ]["close"]
            ),
        }

    return None


# ============================================================
# TRADE SIMULATION
# ============================================================
#
# IMPORTANT:
# This function now receives TP_PCT explicitly.
#
# Therefore the EXACT SAME ENTRY can be simulated with:
#
# RR 1.0
# RR 1.5
# RR 2.0
#
# No new entry signal is generated for each RR.
#
# ============================================================

def simulate_trade(
    df_5m,
    entry_time,
    entry_price,
    direction,
    tp_pct,
    rr_name,
):

    if direction == "LONG":

        tp_price = (
            entry_price
            * (
                1.0
                + tp_pct
            )
        )

        sl_price = (
            entry_price
            * (
                1.0
                - SL_PCT
            )
        )

    else:

        tp_price = (
            entry_price
            * (
                1.0
                - tp_pct
            )
        )

        sl_price = (
            entry_price
            * (
                1.0
                + SL_PCT
            )
        )

    max_hold_seconds = (
        MAX_HOLD_HOURS
        * 3600
    )

    future = df_5m[
        (
            df_5m["timestamp"]
            > entry_time
        )
        &
        (
            df_5m["timestamp"]
            <=
            entry_time
            + max_hold_seconds
        )
    ].copy()

    for _, row in (
        future.iterrows()
    ):

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        if direction == "LONG":

            hit_tp = (
                high >= tp_price
            )

            hit_sl = (
                low <= sl_price
            )

        else:

            hit_tp = (
                low <= tp_price
            )

            hit_sl = (
                high >= sl_price
            )

        # ----------------------------------------------------
        # SAME CANDLE:
        # SL FIRST
        # ----------------------------------------------------

        if hit_tp and hit_sl:

            return {
                "rr": rr_name,
                "tp_pct": tp_pct,
                "result": "FAILURE",
                "exit_time": int(
                    row["timestamp"]
                ),
                "exit_price": sl_price,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "r_multiple": -1.0,
            }

        if hit_sl:

            return {
                "rr": rr_name,
                "tp_pct": tp_pct,
                "result": "FAILURE",
                "exit_time": int(
                    row["timestamp"]
                ),
                "exit_price": sl_price,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "r_multiple": -1.0,
            }

        if hit_tp:

            return {
                "rr": rr_name,
                "tp_pct": tp_pct,
                "result": "SUCCESS",
                "exit_time": int(
                    row["timestamp"]
                ),
                "exit_price": tp_price,
                "tp_price": tp_price,
                "sl_price": sl_price,
                "r_multiple": (
                    tp_pct / SL_PCT
                ),
            }

    return None


# ============================================================
# PATTERN ENGINE
# ============================================================

def detect_patterns(df):

    pivots_high, pivots_low = (
        find_pivots(df)
    )

    patterns = []

    patterns.extend(
        detect_double_top(
            df,
            pivots_high,
        )
    )

    patterns.extend(
        detect_double_bottom(
            df,
            pivots_low,
        )
    )

    patterns.extend(
        detect_head_shoulders(
            df,
            pivots_high,
            pivots_low,
        )
    )

    patterns.extend(
        detect_inverse_head_shoulders(
            df,
            pivots_high,
            pivots_low,
        )
    )

    patterns.extend(
        detect_triangle_wedge(
            df,
            pivots_high,
            pivots_low,
        )
    )

    start_flag = (
        FLAG_IMPULSE_LOOKBACK
        + FLAG_CONSOLIDATION_BARS
    )

    for i in range(
        start_flag,
        len(df) - 1,
    ):

        patterns.extend(
            detect_flags(
                df,
                i,
            )
        )

    # --------------------------------------------------------
    # Exact duplicate removal
    # --------------------------------------------------------

    unique = {}

    for p in patterns:

        key = (
            p["pattern"],
            p["direction"],
            p["start"],
            p["end"],
            round(
                p[
                    "breakout_level"
                ],
                10,
            ),
        )

        unique[key] = p

    patterns = list(
        unique.values()
    )

    patterns.sort(
        key=lambda x: (
            x["end"],
            x["start"],
        )
    )

    # --------------------------------------------------------
    # Same-type + same-direction overlap
    # --------------------------------------------------------

    accepted = []

    for p in patterns:

        duplicate = False

        for old in accepted:

            if (
                p["pattern"]
                != old["pattern"]
            ):
                continue

            if (
                p["direction"]
                != old["direction"]
            ):
                continue

            if structures_overlap(
                p,
                old,
            ):

                if (
                    p["quality"]
                    <= old["quality"]
                ):

                    duplicate = True

                else:

                    try:
                        accepted.remove(
                            old
                        )
                    except ValueError:
                        pass

                break

        if not duplicate:

            accepted.append(p)

    return accepted


# ============================================================
# PROCESS ONE ASSET
# ============================================================

def process_asset(
    asset,
    contract,
    start_ts,
    end_ts,
):

    print()
    print("=" * 70)
    print(
        f"{asset} | CONTRACT={contract}"
    )
    print("=" * 70)

    if not contract:

        print(
            f"{asset}: "
            f"NO EXACT PERPETUAL CONTRACT"
        )

        return {
            "asset": asset,
            "contract": None,
            "status": "NO_CONTRACT",
            "entries": [],
            "history_1h": 0,
            "history_5m": 0,
            "expected_1h": expected_candles(
                "1h"
            ),
            "expected_5m": expected_candles(
                "5m"
            ),
            "lifecycle": {},
        }

    df_1h = fetch_candles(
        contract,
        MAIN_INTERVAL,
        start_ts,
        end_ts,
    )

    df_5m = fetch_candles(
        contract,
        ENTRY_INTERVAL,
        start_ts,
        end_ts,
    )

    print(
        f"{asset} {contract} "
        f"1H={len(df_1h)} "
        f"5M={len(df_5m)}"
    )

    status, expected_1h, expected_5m = (
        history_status(
            df_1h,
            df_5m,
        )
    )

    print(
        f"{asset}: {status}"
    )

    print(
        f"{asset}: expected "
        f"1H={expected_1h} "
        f"5M={expected_5m}"
    )

    if status in (
        "INSUFFICIENT_1H",
        "INSUFFICIENT_5M",
    ):

        return {
            "asset": asset,
            "contract": contract,
            "status": status,
            "entries": [],
            "history_1h": len(df_1h),
            "history_5m": len(df_5m),
            "expected_1h": expected_1h,
            "expected_5m": expected_5m,
            "lifecycle": {},
        }

    # --------------------------------------------------------
    # PATTERN DETECTION
    # --------------------------------------------------------

    patterns = detect_patterns(
        df_1h
    )

    print(
        f"{asset}: detected patterns="
        f"{len(patterns)}"
    )

    entries = []

    consumed_events = set()

    lifecycle = {}

    # --------------------------------------------------------
    # PATTERN LIFECYCLE
    # --------------------------------------------------------

    for p in patterns:

        pattern_name = p[
            "pattern"
        ]

        if (
            pattern_name
            not in lifecycle
        ):

            lifecycle[
                pattern_name
            ] = {
                "Candidates": 0,
                "Breakouts": 0,
                "Retests": 0,
                "Confirmations": 0,
            }

        lifecycle[
            pattern_name
        ]["Candidates"] += 1

        # ----------------------------------------------------
        # FIRST VALID 1H BREAKOUT
        # ----------------------------------------------------

        breakout_i = None

        search_end = min(
            len(df_1h),
            p["end"] + 200,
        )

        for i in range(
            p["end"] + 1,
            search_end,
        ):

            if not breakout_signal(
                df_1h,
                p,
                i,
            ):
                continue

            event_key = (
                asset,
                i,
                p["direction"],
            )

            if event_key in (
                consumed_events
            ):
                continue

            breakout_i = i

            consumed_events.add(
                event_key
            )

            break

        if breakout_i is None:
            continue

        lifecycle[
            pattern_name
        ]["Breakouts"] += 1

        breakout_time = int(
            df_1h.iloc[
                breakout_i
            ]["timestamp"]
        )

        # ----------------------------------------------------
        # FREEZE PATTERN BOUNDARY
        # ----------------------------------------------------

        if (
            p["direction"]
            == "LONG"
        ):

            level = (
                p.get(
                    "upper_level"
                )
                or p[
                    "breakout_level"
                ]
            )

        else:

            level = (
                p.get(
                    "lower_level"
                )
                or p[
                    "breakout_level"
                ]
            )

        # ----------------------------------------------------
        # FIRST RETEST + CONFIRMATION
        # ----------------------------------------------------

        entry = find_entry(
            df_5m,
            breakout_time,
            p["direction"],
            level,
        )

        if entry is None:
            continue

        lifecycle[
            pattern_name
        ]["Retests"] += 1

        lifecycle[
            pattern_name
        ]["Confirmations"] += 1

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # Store ENTRY ONLY.
        #
        # We DO NOT simulate RR here.
        #
        # This guarantees the exact same entry is used
        # for RR 1.0, RR 1.5 and RR 2.0.
        # ----------------------------------------------------

        entries.append(
            {
                "asset": asset,
                "symbol": contract,
                "pattern": p[
                    "pattern"
                ],
                "direction": p[
                    "direction"
                ],
                "pattern_start": int(
                    df_1h.iloc[
                        p["start"]
                    ]["timestamp"]
                ),
                "pattern_end": int(
                    df_1h.iloc[
                        p["end"]
                    ]["timestamp"]
                ),
                "breakout_time":
                    breakout_time,
                "retest_time": entry[
                    "retest_time"
                ],
                "entry_time": entry[
                    "entry_time"
                ],
                "entry_price": entry[
                    "entry_price"
                ],
            }
        )

    print(
        f"{asset}: completed entries="
        f"{len(entries)}"
    )

    return {
        "asset": asset,
        "contract": contract,
        "status": status,
        "entries": entries,
        "history_1h": len(df_1h),
        "history_5m": len(df_5m),
        "expected_1h": expected_1h,
        "expected_5m": expected_5m,
        "lifecycle": lifecycle,

        # Keep dataframes available for RR simulation.
        "df_5m": df_5m,
    }


# ============================================================
# SIMULATE ALL RR VALUES
# ============================================================
#
# Each ENTRY is tested independently against:
#
# RR 1.0
# RR 1.5
# RR 2.0
#
# ============================================================

def simulate_all_rr(
    results,
):

    all_trades = []

    for result in results:

        entries = result.get(
            "entries",
            []
        )

        df_5m = result.get(
            "df_5m"
        )

        if df_5m is None:
            continue

        for entry in entries:

            for rr_name, tp_pct in (
                RR_TESTS.items()
            ):

                simulation = simulate_trade(
                    df_5m,
                    entry[
                        "entry_time"
                    ],
                    entry[
                        "entry_price"
                    ],
                    entry[
                        "direction"
                    ],
                    tp_pct,
                    rr_name,
                )

                if simulation is None:
                    continue

                trade = dict(entry)

                trade.update(
                    {
                        "rr": rr_name,
                        "tp_pct": tp_pct,
                        "tp_price":
                            simulation[
                                "tp_price"
                            ],
                        "sl_price":
                            simulation[
                                "sl_price"
                            ],
                        "exit_time":
                            simulation[
                                "exit_time"
                            ],
                        "exit_price":
                            simulation[
                                "exit_price"
                            ],
                        "result":
                            simulation[
                                "result"
                            ],
                        "r_multiple":
                            simulation[
                                "r_multiple"
                            ],
                    }
                )

                all_trades.append(
                    trade
                )

    return all_trades


# ============================================================
# RR SUMMARY
# ============================================================

def calculate_rr_stats(
    trades,
    rr_name,
):

    subset = [
        x
        for x in trades
        if x["rr"] == rr_name
    ]

    wins = sum(
        1
        for x in subset
        if x["result"]
        == "SUCCESS"
    )

    losses = sum(
        1
        for x in subset
        if x["result"]
        == "FAILURE"
    )

    total = (
        wins
        + losses
    )

    if total:

        win_rate = (
            wins
            / total
            * 100
        )

    else:

        win_rate = 0.0

    tp_pct = RR_TESTS[
        rr_name
    ]

    breakeven = (
        SL_PCT
        / (
            tp_pct
            + SL_PCT
        )
        * 100
    )

    net_r = sum(
        x["r_multiple"]
        for x in subset
    )

    gross_profit_r = sum(
        x["r_multiple"]
        for x in subset
        if x["r_multiple"] > 0
    )

    gross_loss_r = abs(
        sum(
            x["r_multiple"]
            for x in subset
            if x["r_multiple"] < 0
        )
    )

    if gross_loss_r > 0:

        profit_factor = (
            gross_profit_r
            / gross_loss_r
        )

    else:

        profit_factor = (
            float("inf")
            if gross_profit_r > 0
            else 0.0
        )

    edge = (
        win_rate
        - breakeven
    )

    return {
        "rr": rr_name,
        "tp_pct": tp_pct,
        "sl_pct": SL_PCT,
        "total": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "breakeven": breakeven,
        "edge": edge,
        "net_r": net_r,
        "gross_profit_r":
            gross_profit_r,
        "gross_loss_r":
            gross_loss_r,
        "profit_factor":
            profit_factor,
    }


# ============================================================
# FINAL SUMMARY
# ============================================================

def print_summary(
    results,
    all_trades,
):

    print()
    print("=" * 70)
    print("FINAL BACKTEST RESULT")
    print("=" * 70)

    print(
        f"Universe configured: "
        f"{EXPECTED_UNIVERSE_SIZE}"
    )

    usable = [
        r
        for r in results
        if r["status"]
        in (
            "OK",
            "PARTIAL_HISTORY",
        )
    ]

    full_history = [
        r
        for r in results
        if r["status"] == "OK"
    ]

    print(
        f"Assets with usable data: "
        f"{len(usable)}"
    )

    print(
        f"Full-history assets: "
        f"{len(full_history)}"
    )

    # --------------------------------------------------------
    # ENTRY COUNT
    # --------------------------------------------------------

    total_entries = sum(
        len(
            r.get(
                "entries",
                []
            )
        )
        for r in results
    )

    print()
    print(
        f"UNIQUE ENTRIES: "
        f"{total_entries}"
    )

    print(
        "Each entry tested on all RR values."
    )

    # ========================================================
    # RR COMPARISON
    # ========================================================

    print()
    print("=" * 70)
    print("RR COMPARISON")
    print("=" * 70)

    print(
        f"{'RR':<10}"
        f"{'TP':>9}"
        f"{'SL':>9}"
        f"{'Trades':>10}"
        f"{'Wins':>9}"
        f"{'Losses':>9}"
        f"{'WR':>9}"
        f"{'BE':>9}"
        f"{'Edge':>10}"
        f"{'Net R':>12}"
        f"{'PF':>10}"
    )

    print("-" * 110)

    for rr_name in RR_TESTS:

        stats = calculate_rr_stats(
            all_trades,
            rr_name,
        )

        pf = stats[
            "profit_factor"
        ]

        pf_text = (
            f"{pf:.3f}"
            if np.isfinite(pf)
            else "INF"
        )

        print(
            f"{rr_name:<10}"
            f"{stats['tp_pct'] * 100:>8.2f}%"
            f"{SL_PCT * 100:>8.2f}%"
            f"{stats['total']:>10}"
            f"{stats['wins']:>9}"
            f"{stats['losses']:>9}"
            f"{stats['win_rate']:>8.2f}%"
            f"{stats['breakeven']:>8.2f}%"
            f"{stats['edge']:>+9.2f}pp"
            f"{stats['net_r']:>12.2f}"
            f"{pf_text:>10}"
        )

    # ========================================================
    # RR DETAILED REPORT
    # ========================================================

    for rr_name in RR_TESTS:

        stats = calculate_rr_stats(
            all_trades,
            rr_name,
        )

        print()
        print("=" * 70)
        print(
            f"{rr_name} DETAILED RESULT"
        )
        print("=" * 70)

        print(
            f"TP: "
            f"{stats['tp_pct'] * 100:.2f}%"
        )

        print(
            f"SL: "
            f"{SL_PCT * 100:.2f}%"
        )

        print(
            f"Trades: "
            f"{stats['total']}"
        )

        print(
            f"Wins: "
            f"{stats['wins']}"
        )

        print(
            f"Losses: "
            f"{stats['losses']}"
        )

        print(
            f"Win Rate: "
            f"{stats['win_rate']:.2f}%"
        )

        print(
            f"Break-Even: "
            f"{stats['breakeven']:.2f}%"
        )

        print(
            f"Edge vs Break-Even: "
            f"{stats['edge']:+.2f} pp"
        )

        print(
            f"Gross Profit: "
            f"{stats['gross_profit_r']:.2f}R"
        )

        print(
            f"Gross Loss: "
            f"{stats['gross_loss_r']:.2f}R"
        )

        print(
            f"Net R: "
            f"{stats['net_r']:.2f}R"
        )

        pf = stats[
            "profit_factor"
        ]

        if np.isfinite(pf):

            print(
                f"Profit Factor: "
                f"{pf:.3f}"
            )

        else:

            print(
                "Profit Factor: INF"
            )

    # ========================================================
    # UNIVERSE STATUS
    # ========================================================

    print()
    print(
        "20-ASSET UNIVERSE STATUS"
    )

    print("-" * 70)

    for r in results:

        contract = (
            r["contract"]
            or "NONE"
        )

        print(
            f"{r['asset']:<8} "
            f"{contract:<16} "
            f"{r['status']:<20} "
            f"1H={r['history_1h']:<6} "
            f"5M={r['history_5m']:<7} "
            f"Entries="
            f"{len(r.get('entries', []))}"
        )

    # ========================================================
    # PATTERN BREAKDOWN BY RR
    # ========================================================

    for rr_name in RR_TESTS:

        print()
        print(
            f"PATTERN BREAKDOWN - "
            f"{rr_name}"
        )

        print("-" * 70)

        pattern_stats = {}

        for trade in all_trades:

            if trade["rr"] != rr_name:
                continue

            p = trade[
                "pattern"
            ]

            if p not in pattern_stats:

                pattern_stats[p] = {
                    "total": 0,
                    "success": 0,
                    "failure": 0,
                    "net_r": 0.0,
                }

            pattern_stats[
                p
            ]["total"] += 1

            pattern_stats[
                p
            ]["net_r"] += trade[
                "r_multiple"
            ]

            if (
                trade["result"]
                == "SUCCESS"
            ):

                pattern_stats[
                    p
                ]["success"] += 1

            else:

                pattern_stats[
                    p
                ]["failure"] += 1

        for p, s in sorted(
            pattern_stats.items(),
            key=lambda x:
            -x[1]["total"],
        ):

            wr = (
                s["success"]
                / s["total"]
                * 100
                if s["total"]
                else 0.0
            )

            print(
                f"{p:<24}"
                f"{s['total']:>8}"
                f"{s['success']:>8}"
                f"{s['failure']:>8}"
                f"{wr:>9.2f}%"
                f"{s['net_r']:>12.2f}R"
            )

    # ========================================================
    # ASSET BREAKDOWN BY RR
    # ========================================================

    for rr_name in RR_TESTS:

        print()
        print(
            f"ASSET BREAKDOWN - "
            f"{rr_name}"
        )

        print("-" * 70)

        asset_stats = {}

        for asset in FIXED_ASSETS:

            asset_stats[
                asset
            ] = {
                "total": 0,
                "success": 0,
                "failure": 0,
                "net_r": 0.0,
            }

        for trade in all_trades:

            if trade["rr"] != rr_name:
                continue

            asset = trade[
                "asset"
            ]

            asset_stats[
                asset
            ]["total"] += 1

            asset_stats[
                asset
            ]["net_r"] += trade[
                "r_multiple"
            ]

            if (
                trade["result"]
                == "SUCCESS"
            ):

                asset_stats[
                    asset
                ]["success"] += 1

            else:

                asset_stats[
                    asset
                ]["failure"] += 1

        for asset in FIXED_ASSETS:

            s = asset_stats[
                asset
            ]

            wr = (
                s["success"]
                / s["total"]
                * 100
                if s["total"]
                else 0.0
            )

            print(
                f"{asset:<8}"
                f"{s['total']:>8}"
                f"{s['success']:>8}"
                f"{s['failure']:>8}"
                f"{wr:>9.2f}%"
                f"{s['net_r']:>12.2f}R"
            )

    # ========================================================
    # LIFECYCLE DIAGNOSTICS
    # ========================================================

    print()
    print(
        "LIFECYCLE DIAGNOSTICS"
    )

    print("-" * 70)

    lifecycle_total = {}

    for r in results:

        for pattern, stats in (
            r.get(
                "lifecycle",
                {}
            ).items()
        ):

            if (
                pattern
                not in lifecycle_total
            ):

                lifecycle_total[
                    pattern
                ] = {
                    "Candidates": 0,
                    "Breakouts": 0,
                    "Retests": 0,
                    "Confirmations": 0,
                }

            for key in (
                lifecycle_total[
                    pattern
                ]
            ):

                lifecycle_total[
                    pattern
                ][key] += stats.get(
                    key,
                    0,
                )

    for pattern, stats in sorted(
        lifecycle_total.items()
    ):

        print(
            f"{pattern:<20}"
            f"Candidates="
            f"{stats['Candidates']:<7}"
            f"Breakouts="
            f"{stats['Breakouts']:<7}"
            f"Retests="
            f"{stats['Retests']:<7}"
            f"Confirmations="
            f"{stats['Confirmations']:<7}"
        )


# ============================================================
# SAVE TRADES
# ============================================================

def save_results(
    all_trades,
):

    filename = (
        "kraken_pattern_backtest_1y_rr_comparison.csv"
    )

    columns = [
        "rr",
        "tp_pct",
        "sl_pct",
        "asset",
        "symbol",
        "pattern",
        "direction",
        "pattern_start",
        "pattern_end",
        "breakout_time",
        "retest_time",
        "entry_time",
        "entry_price",
        "tp_price",
        "sl_price",
        "exit_time",
        "exit_price",
        "result",
        "r_multiple",
    ]

    if all_trades:

        df = pd.DataFrame(
            all_trades
        )

    else:

        df = pd.DataFrame(
            columns=columns
        )

    # Force desired column order.
    existing_columns = [
        c
        for c in columns
        if c in df.columns
    ]

    df = df[
        existing_columns
    ]

    df.to_csv(
        filename,
        index=False,
    )

    print()
    print(
        f"Trade file saved: "
        f"{filename}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "KRAKEN FUTURES PATTERN BACKTEST 1Y"
    )
    print(
        "VERSION 4.4 - RR COMPARISON"
    )
    print("=" * 70)

    print(
        f"Assets configured: "
        f"{EXPECTED_UNIVERSE_SIZE}"
    )

    print(
        f"Main timeframe: "
        f"{MAIN_INTERVAL}"
    )

    print(
        f"Entry timeframe: "
        f"{ENTRY_INTERVAL}"
    )

    print()
    print(
        "RR TESTS:"
    )

    for rr_name, tp_pct in (
        RR_TESTS.items()
    ):

        rr_value = (
            tp_pct / SL_PCT
        )

        print(
            f"  {rr_name}: "
            f"TP={tp_pct * 100:.2f}% "
            f"SL={SL_PCT * 100:.2f}% "
            f"RR={rr_value:.2f}"
        )

    print()

    print(
        f"Max hold: "
        f"{MAX_HOLD_HOURS}h"
    )

    print(
        f"Expected 1H candles: "
        f"{expected_candles('1h')}"
    )

    print(
        f"Expected 5M candles: "
        f"{expected_candles('5m')}"
    )

    # ========================================================
    # UTC WINDOW
    # ========================================================

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        - timedelta(
            days=DAYS
        )
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    print(
        f"Start UTC: "
        f"{start_dt.isoformat()}"
    )

    print(
        f"End UTC: "
        f"{end_dt.isoformat()}"
    )

    # ========================================================
    # CONTRACT DISCOVERY
    # ========================================================

    universe = (
        build_dynamic_universe()
    )

    # ========================================================
    # PROCESS ALL ASSETS
    # ========================================================

    results = []

    for asset in FIXED_ASSETS:

        contract = universe.get(
            asset
        )

        try:

            result = process_asset(
                asset,
                contract,
                start_ts,
                end_ts,
            )

        except Exception as exc:

            print(
                f"{asset}: ERROR -> "
                f"{exc}"
            )

            result = {
                "asset": asset,
                "contract": contract,
                "status": "ERROR",
                "entries": [],
                "history_1h": 0,
                "history_5m": 0,
                "expected_1h":
                    expected_candles(
                        "1h"
                    ),
                "expected_5m":
                    expected_candles(
                        "5m"
                    ),
                "lifecycle": {},
            }

        results.append(
            result
        )

    # ========================================================
    # SAFETY CHECK
    # ========================================================

    if (
        len(results)
        != EXPECTED_UNIVERSE_SIZE
    ):

        raise RuntimeError(
            "Universe processing error: "
            f"expected "
            f"{EXPECTED_UNIVERSE_SIZE}, "
            f"got {len(results)}"
        )

    # ========================================================
    # SIMULATE SAME ENTRIES
    # AGAINST ALL RR VALUES
    # ========================================================

    print()
    print("=" * 70)
    print(
        "SIMULATING SAME ENTRIES "
        "AGAINST ALL RR VALUES"
    )
    print("=" * 70)

    all_trades = simulate_all_rr(
        results
    )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    print_summary(
        results,
        all_trades,
    )

    # ========================================================
    # SAVE
    # ========================================================

    save_results(
        all_trades
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
