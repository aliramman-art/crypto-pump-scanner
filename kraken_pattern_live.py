# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.4
# ============================================================
#
# FRESHNESS UPDATE:
#
# - SCANNER CAN RUN EVERY 1 MINUTE
# - NEW SIGNAL TELEGRAM IS SENT IMMEDIATELY
# - CLOSE TELEGRAM IS SENT IMMEDIATELY
# - NO REPEATED NEW SIGNAL ALERTS
# - NO REPEATED CLOSE ALERTS
# - PERIODIC REPORT EVERY 15 MINUTES
# - PERIODIC REPORT INCLUDES OPEN TRADES
#
# STRATEGY:
#
#   1H Pattern
#       ↓
#   1H Closed Candle Breakout
#       ↓
#   First 5M Retest
#       ↓
#   5M Confirmation
#       ↓
#   Entry
#
# FRESHNESS:
#
#   Only latest closed 5M candle
#   or one previous 5M candle
#
# REAL TRADING = DISABLED
#
# ============================================================

import os
import time
import sqlite3
import requests
import traceback

import pandas as pd

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

MAIN_INTERVAL = "1h"
ENTRY_INTERVAL = "5m"

SL_PCT = 0.01
TP_PCT = 0.01
RR = 1.0

REAL_TRADING = False

MAX_HOLD_HOURS = 48

MAIN_LOOKBACK = 500
ENTRY_LOOKBACK = 1500

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.10
CANDLE_CHUNK = 1900

# PERIODIC REPORT EVERY 15 MINUTES
PERIODIC_REPORT_SECONDS = 15 * 60

DB_FILE = "kraken_pattern_live_v52.db"


# ============================================================
# SIGNAL FRESHNESS
# ============================================================

# 0 = only latest closed 5M candle
# 1 = latest closed 5M candle + one previous candle
#
# With value 1:
# Entry 10:30 -> accepted
# Entry 10:25 -> accepted
# Entry 10:20 or older -> rejected

MAX_ENTRY_AGE_CANDLES = 1


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
]


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
# TIME
# ============================================================

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


def utc_now():
    return datetime.now(timezone.utc)


def format_time(ts):

    if ts is None:
        return "-"

    dt = datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc,
    )

    return dt.astimezone(
        TEHRAN_TZ
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram token missing")
        return

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat id missing")
        return

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if not r.ok:

            print(
                "Telegram error:",
                r.status_code,
                r.text,
            )

    except Exception as e:

        print(
            "Telegram exception:",
            e,
        )


# ============================================================
# KRAKEN REQUEST
# ============================================================

def kraken_get(
    path,
    params=None,
):

    url = BASE_URL + path

    r = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    r.raise_for_status()

    return r.json()


# ============================================================
# CONTRACTS
# ============================================================

def get_contracts():

    data = kraken_get(
        "/derivatives/api/v3/instruments"
    )

    return data.get(
        "instruments",
        []
    )


def build_contract_map():

    contracts = get_contracts()

    result = {}

    for c in contracts:

        symbol = c.get(
            "symbol"
        )

        if not symbol:
            continue

        result[symbol] = c

    return result


# ============================================================
# ASSET -> CONTRACT
# ============================================================

def find_contract_for_asset(
    asset,
    contract_map,
):

    candidates = [
        f"PF_{asset}USD",
        f"PI_{asset}USD",
    ]

    for symbol in candidates:

        if symbol in contract_map:
            return contract_map[symbol]

    for symbol, contract in contract_map.items():

        if (
            symbol.startswith(
                f"PF_{asset}"
            )
            and symbol.endswith("USD")
        ):
            return contract

    return None


# ============================================================
# CANDLES
# ============================================================

def fetch_recent_candles(
    contract,
    interval,
    lookback,
):

    symbol = contract.get(
        "symbol"
    )

    if not symbol:
        return pd.DataFrame()

    interval_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "1d": 86400,
    }.get(interval)

    if interval_seconds is None:
        raise ValueError(
            f"Unsupported interval: {interval}"
        )

    now_ts = int(
        utc_now().timestamp()
    )

    end_ts = now_ts

    start_ts = (
        now_ts
        - (
            lookback
            * interval_seconds
        )
        - interval_seconds
    )

    rows = []

    current_start = start_ts

    while current_start < end_ts:

        current_end = min(
            current_start
            + (
                CANDLE_CHUNK
                * interval_seconds
            ),
            end_ts,
        )

        params = {
            "symbol": symbol,
            "resolution": interval,
            "from": current_start,
            "to": current_end,
        }

        try:

            data = kraken_get(
                "/derivatives/api/v3/ohlc",
                params=params,
            )

        except Exception as e:

            print(
                f"OHLC error {symbol}: {e}"
            )

            break

        candles = data.get(
            "candles",
            []
        )

        rows.extend(candles)

        current_start = (
            current_end
            + interval_seconds
        )

        time.sleep(
            REQUEST_SLEEP
        )

    if not rows:
        return pd.DataFrame()

    parsed = []

    for c in rows:

        try:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                )

                o = c.get("open")
                h = c.get("high")
                l = c.get("low")
                close = c.get("close")
                volume = c.get(
                    "volume",
                    0,
                )

            else:

                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                close = c[4]

                volume = (
                    c[5]
                    if len(c) > 5
                    else 0
                )

            ts = int(
                float(ts)
            )

            if ts > 10_000_000_000:
                ts //= 1000

            parsed.append(
                [
                    ts,
                    float(o),
                    float(h),
                    float(l),
                    float(close),
                    float(volume),
                ]
            )

        except Exception:

            continue

    if not parsed:
        return pd.DataFrame()

    df = pd.DataFrame(
        parsed,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df = (
        df
        .drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values(
            "timestamp"
        )
        .reset_index(drop=True)
    )

    # ========================================================
    # CLOSED CANDLES ONLY
    # ========================================================

    df = df[
        df["timestamp"] <= now_ts
    ]

    df = df[
        (
            df["timestamp"]
            + interval_seconds
        )
        <= now_ts
    ]

    return df.tail(
        lookback
    ).reset_index(
        drop=True
    )


# ============================================================
# PIVOTS
# ============================================================

def pivot_highs(df):

    highs = df["high"].values

    result = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT,
    ):

        left = highs[
            i - PIVOT_LEFT:i
        ]

        right = highs[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            highs[i] > left.max()
            and highs[i] >= right.max()
        ):

            result.append(i)

    return result


def pivot_lows(df):

    lows = df["low"].values

    result = []

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT,
    ):

        left = lows[
            i - PIVOT_LEFT:i
        ]

        right = lows[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            lows[i] < left.min()
            and lows[i] <= right.min()
        ):

            result.append(i)

    return result


# ============================================================
# DOUBLE TOP / BOTTOM
# ============================================================

def detect_double_patterns(df):

    patterns = []

    highs = pivot_highs(df)
    lows = pivot_lows(df)

    # --------------------------------------------------------
    # DOUBLE TOP
    # --------------------------------------------------------

    for a in range(
        len(highs)
    ):

        for b in range(
            a + 1,
            len(highs),
        ):

            i = highs[a]
            j = highs[b]

            separation = j - i

            if separation < DOUBLE_MIN_SEPARATION:
                continue

            if separation > DOUBLE_MAX_SEPARATION:
                break

            p1 = df.iloc[i]["high"]
            p2 = df.iloc[j]["high"]

            diff = (
                abs(p1 - p2)
                / max(p1, p2)
            )

            if diff > DOUBLE_TOLERANCE:
                continue

            between = df.iloc[
                i:j + 1
            ]

            neckline = between[
                "low"
            ].min()

            patterns.append(
                {
                    "pattern": "DOUBLE_TOP",
                    "direction": "SHORT",
                    "start": i,
                    "end": j,
                    "breakout_level": neckline,
                    "upper_level": max(
                        p1,
                        p2,
                    ),
                }
            )

    # --------------------------------------------------------
    # DOUBLE BOTTOM
    # --------------------------------------------------------

    for a in range(
        len(lows)
    ):

        for b in range(
            a + 1,
            len(lows),
        ):

            i = lows[a]
            j = lows[b]

            separation = j - i

            if separation < DOUBLE_MIN_SEPARATION:
                continue

            if separation > DOUBLE_MAX_SEPARATION:
                break

            p1 = df.iloc[i]["low"]
            p2 = df.iloc[j]["low"]

            diff = (
                abs(p1 - p2)
                / max(
                    min(p1, p2),
                    1e-12,
                )
            )

            if diff > DOUBLE_TOLERANCE:
                continue

            between = df.iloc[
                i:j + 1
            ]

            neckline = between[
                "high"
            ].max()

            patterns.append(
                {
                    "pattern": "DOUBLE_BOTTOM",
                    "direction": "LONG",
                    "start": i,
                    "end": j,
                    "breakout_level": neckline,
                    "lower_level": min(
                        p1,
                        p2,
                    ),
                }
            )

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(df):

    patterns = []

    highs = pivot_highs(df)

    if len(highs) >= 3:

        for k in range(
            len(highs) - 2
        ):

            left = highs[k]
            head = highs[k + 1]
            right = highs[k + 2]

            sep1 = head - left
            sep2 = right - head

            if (
                sep1 < HS_MIN_SEPARATION
                or sep2 < HS_MIN_SEPARATION
            ):
                continue

            if (
                sep1 > HS_MAX_SEPARATION
                or sep2 > HS_MAX_SEPARATION
            ):
                continue

            lh = df.iloc[left]["high"]
            hh = df.iloc[head]["high"]
            rh = df.iloc[right]["high"]

            if not (
                hh > lh
                and hh > rh
            ):
                continue

            shoulder_diff = (
                abs(lh - rh)
                / max(lh, rh)
            )

            if (
                shoulder_diff
                > DOUBLE_TOLERANCE
            ):
                continue

            middle = df.iloc[
                left:right + 1
            ]

            neckline = middle[
                "low"
            ].min()

            patterns.append(
                {
                    "pattern": "HEAD_SHOULDERS",
                    "direction": "SHORT",
                    "start": left,
                    "end": right,
                    "breakout_level": neckline,
                    "upper_level": hh,
                }
            )

    lows = pivot_lows(df)

    if len(lows) >= 3:

        for k in range(
            len(lows) - 2
        ):

            left = lows[k]
            head = lows[k + 1]
            right = lows[k + 2]

            sep1 = head - left
            sep2 = right - head

            if (
                sep1 < HS_MIN_SEPARATION
                or sep2 < HS_MIN_SEPARATION
            ):
                continue

            if (
                sep1 > HS_MAX_SEPARATION
                or sep2 > HS_MAX_SEPARATION
            ):
                continue

            ll = df.iloc[left]["low"]
            hl = df.iloc[head]["low"]
            rl = df.iloc[right]["low"]

            if not (
                hl < ll
                and hl < rl
            ):
                continue

            shoulder_diff = (
                abs(ll - rl)
                / max(
                    min(ll, rl),
                    1e-12,
                )
            )

            if (
                shoulder_diff
                > DOUBLE_TOLERANCE
            ):
                continue

            middle = df.iloc[
                left:right + 1
            ]

            neckline = middle[
                "high"
            ].max()

            patterns.append(
                {
                    "pattern":
                        "INVERSE_HEAD_SHOULDERS",
                    "direction": "LONG",
                    "start": left,
                    "end": right,
                    "breakout_level": neckline,
                    "lower_level": hl,
                }
            )

    return patterns


# ============================================================
# FLAGS
# ============================================================

def detect_flags(df):

    patterns = []

    if len(df) < (
        FLAG_IMPULSE_LOOKBACK
        + FLAG_CONSOLIDATION_BARS
        + 5
    ):

        return patterns

    start_min = (
        FLAG_IMPULSE_LOOKBACK
        + FLAG_CONSOLIDATION_BARS
    )

    for end in range(
        start_min,
        len(df),
    ):

        impulse_start = (
            end
            - FLAG_CONSOLIDATION_BARS
            - FLAG_IMPULSE_LOOKBACK
        )

        impulse_end = (
            end
            - FLAG_CONSOLIDATION_BARS
        )

        if impulse_start < 0:
            continue

        impulse = df.iloc[
            impulse_start:
            impulse_end
        ]

        if len(impulse) < 5:
            continue

        impulse_start_close = float(
            impulse.iloc[0]["close"]
        )

        impulse_end_close = float(
            impulse.iloc[-1]["close"]
        )

        if impulse_start_close <= 0:
            continue

        impulse_return = (
            impulse_end_close
            / impulse_start_close
            - 1
        )

        consolidation = df.iloc[
            impulse_end:end
        ]

        if len(consolidation) < 5:
            continue

        if (
            abs(impulse_return)
            < FLAG_MIN_IMPULSE
        ):
            continue

        consolidation_high = float(
            consolidation["high"].max()
        )

        consolidation_low = float(
            consolidation["low"].min()
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
            continue

        consolidation_range = (
            consolidation_high
            - consolidation_low
        )

        if (
            consolidation_range
            / impulse_range
            > FLAG_MAX_RANGE_TO_IMPULSE
        ):
            continue

        # ----------------------------------------------------
        # LONG FLAG
        # ----------------------------------------------------

        if impulse_return > 0:

            retrace = (
                impulse_end_close
                - consolidation_low
            ) / max(
                impulse_end_close
                - impulse_low,
                1e-12,
            )

            if (
                retrace
                > FLAG_MAX_RETRACE
            ):
                continue

            direction_moves = 0.0
            total_moves = 0.0

            closes = consolidation[
                "close"
            ].values

            for i in range(
                1,
                len(closes),
            ):

                diff = (
                    closes[i]
                    - closes[i - 1]
                )

                total_moves += abs(
                    diff
                )

                direction_moves += diff

            efficiency = (
                abs(direction_moves)
                / max(
                    total_moves,
                    1e-12,
                )
            )

            if (
                efficiency
                < FLAG_MIN_DIRECTIONAL_EFFICIENCY
            ):
                continue

            patterns.append(
                {
                    "pattern": "BULL_FLAG",
                    "direction": "LONG",
                    "start": impulse_start,
                    "end": end - 1,
                    "breakout_level":
                        consolidation_high,
                    "upper_level":
                        consolidation_high,
                    "lower_level":
                        consolidation_low,
                }
            )

        # ----------------------------------------------------
        # SHORT FLAG
        # ----------------------------------------------------

        else:

            retrace = (
                consolidation_high
                - impulse_end_close
            ) / max(
                impulse_high
                - impulse_end_close,
                1e-12,
            )

            if (
                retrace
                > FLAG_MAX_RETRACE
            ):
                continue

            direction_moves = 0.0
            total_moves = 0.0

            closes = consolidation[
                "close"
            ].values

            for i in range(
                1,
                len(closes),
            ):

                diff = (
                    closes[i]
                    - closes[i - 1]
                )

                total_moves += abs(
                    diff
                )

                direction_moves += diff

            efficiency = (
                abs(direction_moves)
                / max(
                    total_moves,
                    1e-12,
                )
            )

            if (
                efficiency
                < FLAG_MIN_DIRECTIONAL_EFFICIENCY
            ):
                continue

            patterns.append(
                {
                    "pattern": "BEAR_FLAG",
                    "direction": "SHORT",
                    "start": impulse_start,
                    "end": end - 1,
                    "breakout_level":
                        consolidation_low,
                    "upper_level":
                        consolidation_high,
                    "lower_level":
                        consolidation_low,
                }
            )

    return patterns


# ============================================================
# ALL PATTERNS
# ============================================================

def detect_patterns(df):

    patterns = []

    patterns.extend(
        detect_double_patterns(df)
    )

    patterns.extend(
        detect_head_shoulders(df)
    )

    patterns.extend(
        detect_flags(df)
    )

    return patterns


# ============================================================
# BREAKOUT
# ============================================================

def breakout_signal(
    df,
    pattern,
    i,
):

    if i <= pattern["end"]:
        return False

    row = df.iloc[i]

    level = pattern[
        "breakout_level"
    ]

    close = float(
        row["close"]
    )

    if (
        pattern["direction"]
        == "LONG"
    ):

        return (
            close
            > level
            * (
                1
                + BREAKOUT_BUFFER
            )
        )

    return (
        close
        < level
        * (
            1
            - BREAKOUT_BUFFER
        )
    )


# ============================================================
# RETEST + CONFIRMATION
# ============================================================

def find_entry(
    df,
    breakout_time,
    direction,
    level,
):

    indices = df.index[
        df["timestamp"]
        > breakout_time
    ].tolist()

    if not indices:
        return None

    first_index = indices[0]

    retest_end = min(
        first_index
        + RETEST_MAX_BARS,
        len(df),
    )

    retest_index = None

    for i in range(
        first_index,
        retest_end,
    ):

        row = df.iloc[i]

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        if direction == "LONG":

            if low <= level:

                retest_index = i
                break

        else:

            if high >= level:

                retest_index = i
                break

    if retest_index is None:
        return None

    confirm_end = min(
        retest_index
        + 1
        + CONFIRM_MAX_BARS,
        len(df),
    )

    for i in range(
        retest_index + 1,
        confirm_end,
    ):

        row = df.iloc[i]

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

        rng = h - l

        if rng <= 0:
            continue

        body = abs(
            c - o
        )

        body_ratio = (
            body / rng
        )

        if (
            body_ratio
            < MIN_BODY_RATIO
        ):
            continue

        if direction == "LONG":

            close_position = (
                c - l
            ) / rng

            if (
                close_position
                < MIN_CLOSE_POSITION
            ):
                continue

            if c <= level:
                continue

        else:

            close_position = (
                h - c
            ) / rng

            if (
                close_position
                < MIN_CLOSE_POSITION
            ):
                continue

            if c >= level:
                continue

        return {
            "entry_time": int(
                row["timestamp"]
            ),
            "entry_price": c,
            "retest_time": int(
                df.iloc[
                    retest_index
                ]["timestamp"]
            ),
            "confirm_time": int(
                row["timestamp"]
            ),
        }

    return None


# ============================================================
# LIVE ENTRY GENERATOR
# ============================================================

def generate_live_entries(
    asset,
    contract,
    df_1h,
    df_5m,
):

    patterns = detect_patterns(
        df_1h
    )

    if not patterns:
        return []

    entries = []

    consumed_events = set()

    recent_start_ts = int(
        df_1h.iloc[
            max(
                0,
                len(df_1h) - 180,
            )
        ]["timestamp"]
    )

    for p in patterns:

        pattern_end_ts = int(
            df_1h.iloc[
                p["end"]
            ]["timestamp"]
        )

        if (
            pattern_end_ts
            < recent_start_ts
        ):
            continue

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

            breakout_time = int(
                df_1h.iloc[
                    i
                ]["timestamp"]
            )

            latest_5m_ts = int(
                df_5m.iloc[-1][
                    "timestamp"
                ]
            )

            max_retest_seconds = (
                (
                    RETEST_MAX_BARS
                    + CONFIRM_MAX_BARS
                    + 5
                )
                * 300
            )

            if (
                breakout_time
                <
                latest_5m_ts
                - max_retest_seconds
            ):
                continue

            event_key = (
                asset,
                i,
                p["direction"],
            )

            if (
                event_key
                in consumed_events
            ):
                continue

            consumed_events.add(
                event_key
            )

            breakout_i = i

            break

        if breakout_i is None:
            continue

        breakout_time = int(
            df_1h.iloc[
                breakout_i
            ]["timestamp"]
        )

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

        entry = find_entry(
            df_5m,
            breakout_time,
            p["direction"],
            level,
        )

        if entry is None:
            continue

        # ====================================================
        # FRESHNESS FILTER
        # ====================================================

        latest_5m_ts = int(
            df_5m.iloc[-1][
                "timestamp"
            ]
        )

        entry_age_seconds = (
            latest_5m_ts
            - int(
                entry[
                    "entry_time"
                ]
            )
        )

        max_entry_age_seconds = (
            MAX_ENTRY_AGE_CANDLES
            * 300
        )

        if (
            entry_age_seconds
            > max_entry_age_seconds
        ):

            print(
                f"STALE SIGNAL SKIPPED: "
                f"{asset} "
                f"{p['direction']} "
                f"Entry="
                f"{format_time(entry['entry_time'])} "
                f"| Latest 5M="
                f"{format_time(latest_5m_ts)} "
                f"| Age="
                f"{entry_age_seconds // 60}m "
                f"| Max="
                f"{max_entry_age_seconds // 60}m"
            )

            continue

        # ====================================================
        # SIGNAL KEY
        # ====================================================

        signal_key = (
            f"{asset}|"
            f"{p['pattern']}|"
            f"{p['direction']}|"
            f"{breakout_time}|"
            f"{entry['entry_time']}"
        )

        entries.append(
            {
                "signal_key":
                    signal_key,

                "asset":
                    asset,

                "contract":
                    contract.get(
                        "symbol"
                    ),

                "pattern":
                    p["pattern"],

                "direction":
                    p["direction"],

                "breakout_time":
                    breakout_time,

                "retest_time":
                    entry[
                        "retest_time"
                    ],

                "confirm_time":
                    entry[
                        "confirm_time"
                    ],

                "entry_time":
                    entry[
                        "entry_time"
                    ],

                "entry_price":
                    entry[
                        "entry_price"
                    ],

                "sl_price": (
                    entry[
                        "entry_price"
                    ]
                    * (
                        1 - SL_PCT
                    )
                    if p[
                        "direction"
                    ] == "LONG"
                    else
                    entry[
                        "entry_price"
                    ]
                    * (
                        1 + SL_PCT
                    )
                ),

                "tp_price": (
                    entry[
                        "entry_price"
                    ]
                    * (
                        1 + TP_PCT
                    )
                    if p[
                        "direction"
                    ] == "LONG"
                    else
                    entry[
                        "entry_price"
                    ]
                    * (
                        1 - TP_PCT
                    )
                ),

                "detected_at":
                    int(
                        utc_now().timestamp()
                    ),
            }
        )

    return entries


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    return sqlite3.connect(
        DB_FILE
    )


def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            signal_key TEXT UNIQUE,

            asset TEXT,
            contract TEXT,

            pattern TEXT,
            direction TEXT,

            breakout_time INTEGER,
            retest_time INTEGER,
            confirm_time INTEGER,
            entry_time INTEGER,

            entry_price REAL,
            sl_price REAL,
            tp_price REAL,

            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,

            status TEXT,

            detected_at INTEGER,

            notified_new INTEGER DEFAULT 0,
            notified_close INTEGER DEFAULT 0
        )
        """
    )

    conn.commit()

    conn.close()


# ============================================================
# DB HELPERS
# ============================================================

def get_trade_by_signal_key(
    signal_key
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE signal_key = ?
        """,
        (
            signal_key,
        ),
    )

    row = cur.fetchone()

    conn.close()

    return row


def get_trade_by_id(
    trade_id
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (
            trade_id,
        ),
    )

    row = cur.fetchone()

    conn.close()

    return row


def get_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    )

    rows = cur.fetchall()

    conn.close()

    return rows


def insert_trade(
    trade
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        INSERT OR IGNORE INTO trades (
            signal_key,
            asset,
            contract,
            pattern,
            direction,
            breakout_time,
            retest_time,
            confirm_time,
            entry_time,
            entry_price,
            sl_price,
            tp_price,
            exit_time,
            exit_price,
            exit_reason,
            status,
            detected_at,
            notified_new,
            notified_close
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, NULL, NULL, NULL,
            'OPEN', ?, 0, 0
        )
        """,
        (
            trade["signal_key"],
            trade["asset"],
            trade["contract"],
            trade["pattern"],
            trade["direction"],
            trade["breakout_time"],
            trade["retest_time"],
            trade["confirm_time"],
            trade["entry_time"],
            trade["entry_price"],
            trade["sl_price"],
            trade["tp_price"],
            trade["detected_at"],
        ),
    )

    inserted = (
        cur.rowcount == 1
    )

    conn.commit()

    conn.close()

    return inserted


# ============================================================
# KRAKEN PRICE
# ============================================================

def get_kraken_ticker(
    contract
):

    symbol = contract.get(
        "symbol"
    )

    if not symbol:
        return None

    try:

        data = kraken_get(
            "/derivatives/api/v3/tickers"
        )

        tickers = data.get(
            "tickers",
            []
        )

        for t in tickers:

            if (
                t.get("symbol")
                == symbol
            ):

                price = (
                    t.get("last")
                    or t.get("lastPrice")
                )

                if price is not None:

                    return float(
                        price
                    )

    except Exception as e:

        print(
            "Ticker error:",
            symbol,
            e,
        )

    return None


# ============================================================
# LBANK PRICE
# ============================================================

def get_lbank_price(
    asset
):

    try:

        symbol = (
            f"{asset.lower()}usdt"
        )

        url = (
            "https://lbkperp.lbank.com"
            "/cfd/openApi/v1/ticker"
        )

        r = requests.get(
            url,
            params={
                "symbol": symbol
            },
            timeout=10,
        )

        if not r.ok:
            return None

        data = r.json()

        return float(
            data["data"]["last"]
        )

    except Exception:

        return None


def get_lbank_snapshot():

    result = {}

    for asset in ASSETS:

        price = get_lbank_price(
            asset
        )

        if price is not None:

            result[asset] = price

        time.sleep(
            0.05
        )

    return result


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(
    trade,
    current_price=None,
):

    direction = trade[
        "direction"
    ]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    entry = float(
        trade["entry_price"]
    )

    sl = float(
        trade["sl_price"]
    )

    tp = float(
        trade["tp_price"]
    )

    sl_pct = (
        abs(sl - entry)
        / entry
        * 100
    )

    tp_pct = (
        abs(tp - entry)
        / entry
        * 100
    )

    current_text = ""

    if current_price is not None:

        if direction == "LONG":

            current_pct = (
                current_price
                / entry
                - 1
            ) * 100

        else:

            current_pct = (
                1
                - current_price
                / entry
            ) * 100

        current_text = (
            f"💵 Current: "
            f"{current_price:.8g} "
            f"({current_pct:+.2f}%)\n"
        )

    return (
        f"{emoji} "
        f"<b>{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{entry:.8g}\n"
        f"🛑 SL: "
        f"{sl:.8g} "
        f"(-{sl_pct:.2f}%)\n"
        f"🎯 TP: "
        f"{tp:.8g} "
        f"(+{tp_pct:.2f}%)\n"
        f"⚙️ RR: "
        f"{RR:.2f}\n"
        f"🕐 Entry time: "
        f"{format_time(trade['entry_time'])}\n"
        f"{current_text}"
        f"🧪 Real trading: "
        f"{'ENABLED' if REAL_TRADING else 'DISABLED'}"
    )


# ============================================================
# NEW SIGNAL ALERT
# ============================================================

def send_new_signal_alert(
    trade,
    display_price=None,
):

    detected_at = trade.get(
        "detected_at"
    )

    if detected_at is None:

        detected_at = int(
            utc_now().timestamp()
        )

    signal_text = format_signal(
        trade,
        display_price,
    )

    text = (
        "<b>🚨 NEW SIGNAL</b>\n"
        f"🕐 Detected: "
        f"{format_time(detected_at)}\n\n"
        f"{signal_text}"
    )

    send_telegram(
        text
    )


# ============================================================
# CLOSE ALERT
# ============================================================

def send_close_alert(
    trade,
):

    direction = trade[
        "direction"
    ]

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        trade["exit_price"]
    )

    if direction == "LONG":

        pnl_pct = (
            exit_price
            / entry
            - 1
        ) * 100

    else:

        pnl_pct = (
            1
            - exit_price
            / entry
        ) * 100

    emoji = (
        "🟢"
        if pnl_pct > 0
        else "🔴"
    )

    text = (
        "<b>🔔 TRADE CLOSED</b>\n\n"
        f"{trade['asset']} "
        f"{direction}\n"
        f"Pattern: "
        f"{trade['pattern']}\n"
        f"Entry: "
        f"{entry:.8g}\n"
        f"Exit: "
        f"{exit_price:.8g}\n"
        f"{emoji} PnL: "
        f"{pnl_pct:+.2f}%\n"
        f"Reason: "
        f"{trade['exit_reason']}\n"
        f"🕐 Exit: "
        f"{format_time(trade['exit_time'])}"
    )

    send_telegram(
        text
    )


# ============================================================
# ROW -> TRADE
# ============================================================

def row_to_trade(
    row
):

    if row is None:
        return None

    return {
        "id": row[0],
        "signal_key": row[1],
        "asset": row[2],
        "contract": row[3],
        "pattern": row[4],
        "direction": row[5],
        "breakout_time": row[6],
        "retest_time": row[7],
        "confirm_time": row[8],
        "entry_time": row[9],
        "entry_price": row[10],
        "sl_price": row[11],
        "tp_price": row[12],
        "exit_time": row[13],
        "exit_price": row[14],
        "exit_reason": row[15],
        "status": row[16],
        "detected_at": row[17],
        "notified_new": row[18],
        "notified_close": row[19],
    }


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_time,
    exit_price,
    exit_reason,
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        UPDATE trades
        SET
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            status = 'CLOSED'
        WHERE id = ?
          AND status = 'OPEN'
        """,
        (
            exit_time,
            exit_price,
            exit_reason,
            trade_id,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# NOTIFICATION FLAGS
# ============================================================

def mark_new_notified(
    trade_id
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        UPDATE trades
        SET notified_new = 1
        WHERE id = ?
        """,
        (
            trade_id,
        ),
    )

    conn.commit()

    conn.close()


def mark_close_notified(
    trade_id
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        UPDATE trades
        SET notified_close = 1
        WHERE id = ?
        """,
        (
            trade_id,
        ),
    )

    conn.commit()

    conn.close()


# ============================================================
# RESOLVE TRADE FROM CANDLES
# ============================================================

def resolve_open_trade_from_candles(
    trade,
    df_5m,
):

    entry_time = int(
        trade["entry_time"]
    )

    direction = trade[
        "direction"
    ]

    sl = float(
        trade["sl_price"]
    )

    tp = float(
        trade["tp_price"]
    )

    df = df_5m[
        df_5m["timestamp"]
        > entry_time
    ].copy()

    if df.empty:
        return None

    for _, row in df.iterrows():

        ts = int(
            row["timestamp"]
        )

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        close = float(
            row["close"]
        )

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

        if hit_sl and hit_tp:

            return {
                "exit_time": ts,
                "exit_price": sl,
                "exit_reason": "SL",
            }

        if hit_sl:

            return {
                "exit_time": ts,
                "exit_price": sl,
                "exit_reason": "SL",
            }

        if hit_tp:

            return {
                "exit_time": ts,
                "exit_price": tp,
                "exit_reason": "TP",
            }

        elapsed_hours = (
            ts - entry_time
        ) / 3600

        if (
            elapsed_hours
            >= MAX_HOLD_HOURS
        ):

            return {
                "exit_time": ts,
                "exit_price": close,
                "exit_reason": "TIME",
            }

    return None


# ============================================================
# HISTORICAL CLOSE RECONCILIATION
# ============================================================

def update_open_trades_from_history(
    contract_map,
):

    open_rows = (
        get_open_trades()
    )

    if not open_rows:
        return

    cache = {}

    for row in open_rows:

        trade = row_to_trade(
            row
        )

        asset = trade[
            "asset"
        ]

        contract = (
            contract_map.get(
                trade["contract"]
            )
        )

        if contract is None:
            continue

        if asset not in cache:

            cache[asset] = (
                fetch_recent_candles(
                    contract,
                    ENTRY_INTERVAL,
                    ENTRY_LOOKBACK,
                )
            )

        df_5m = cache[
            asset
        ]

        if df_5m.empty:
            continue

        result = (
            resolve_open_trade_from_candles(
                trade,
                df_5m,
            )
        )

        if result is None:
            continue

        close_trade(
            trade["id"],
            result["exit_time"],
            result["exit_price"],
            result["exit_reason"],
        )

        updated = get_trade_by_id(
            trade["id"]
        )

        if updated:

            updated_trade = (
                row_to_trade(
                    updated
                )
            )

            if not updated_trade[
                "notified_close"
            ]:

                send_close_alert(
                    updated_trade
                )

                mark_close_notified(
                    updated_trade["id"]
                )


# ============================================================
# CURRENT PRICE EXITS
# ============================================================

def process_current_price_exits(
    prices
):

    open_rows = (
        get_open_trades()
    )

    if not open_rows:
        return

    now_ts = int(
        utc_now().timestamp()
    )

    for row in open_rows:

        trade = row_to_trade(
            row
        )

        asset = trade[
            "asset"
        ]

        if asset not in prices:
            continue

        current = float(
            prices[asset]
        )

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        exit_price = None
        reason = None

        if direction == "LONG":

            if current <= sl:

                exit_price = sl
                reason = "SL"

            elif current >= tp:

                exit_price = tp
                reason = "TP"

        else:

            if current >= sl:

                exit_price = sl
                reason = "SL"

            elif current <= tp:

                exit_price = tp
                reason = "TP"

        if exit_price is None:

            elapsed_hours = (
                now_ts
                - int(
                    trade["entry_time"]
                )
            ) / 3600

            if (
                elapsed_hours
                >= MAX_HOLD_HOURS
            ):

                exit_price = current
                reason = "TIME"

        if exit_price is None:
            continue

        close_trade(
            trade["id"],
            now_ts,
            exit_price,
            reason,
        )

        updated = get_trade_by_id(
            trade["id"]
        )

        if updated:

            updated_trade = (
                row_to_trade(
                    updated
                )
            )

            if not updated_trade[
                "notified_close"
            ]:

                send_close_alert(
                    updated_trade
                )

                mark_close_notified(
                    updated_trade["id"]
                )


# ============================================================
# PERFORMANCE
# ============================================================

def performance_stats():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN status = 'CLOSED'
                    THEN 1
                    ELSE 0
                END
            )
        FROM trades
        """
    )

    total, closed = (
        cur.fetchone()
    )

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
          AND (
              (
                  direction = 'LONG'
                  AND exit_price > entry_price
              )
              OR
              (
                  direction = 'SHORT'
                  AND exit_price < entry_price
              )
          )
        """
    )

    wins = cur.fetchone()[0]

    cur.execute(
        """
        SELECT
            SUM(
                CASE
                    WHEN direction = 'LONG'
                    THEN (
                        exit_price
                        / entry_price
                        - 1
                    ) * 100

                    WHEN direction = 'SHORT'
                    THEN (
                        1
                        - exit_price
                        / entry_price
                    ) * 100

                    ELSE 0
                END
            )
        FROM trades
        WHERE status = 'CLOSED'
        """
    )

    net_pnl = (
        cur.fetchone()[0]
        or 0
    )

    conn.close()

    closed = closed or 0
    wins = wins or 0

    losses = (
        closed - wins
    )

    wr = (
        wins / closed * 100
        if closed
        else 0
    )

    return {
        "total": total or 0,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "net": net_pnl,
    }


# ============================================================
# PERIODIC REPORT
# ============================================================

def send_periodic_report():

    stats = performance_stats()

    open_rows = (
        get_open_trades()
    )

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    text = (
        "<b>📊 KRAKEN PATTERN SCANNER</b>\n\n"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    if open_rows:

        text += (
            f"<b>🟢 OPEN TRADES: "
            f"{len(open_rows)}</b>\n\n"
        )

        for row in open_rows:

            trade = row_to_trade(
                row
            )

            # ------------------------------------------------
            # Current LBank price
            # ------------------------------------------------

            current_price = (
                get_lbank_price(
                    trade["asset"]
                )
            )

            entry = float(
                trade["entry_price"]
            )

            sl = float(
                trade["sl_price"]
            )

            tp = float(
                trade["tp_price"]
            )

            # ------------------------------------------------
            # Current PnL
            # ------------------------------------------------

            if current_price is not None:

                if trade["direction"] == "LONG":

                    current_pnl = (
                        current_price
                        / entry
                        - 1
                    ) * 100

                else:

                    current_pnl = (
                        1
                        - current_price
                        / entry
                    ) * 100

                current_text = (
                    f"💵 Current: "
                    f"{current_price:.8g} "
                    f"({current_pnl:+.2f}%)\n"
                )

            else:

                current_text = (
                    "💵 Current: -\n"
                )

            # ------------------------------------------------
            # SL / TP percentages
            # ------------------------------------------------

            if trade["direction"] == "LONG":

                sl_pct = (
                    (sl - entry)
                    / entry
                    * 100
                )

                tp_pct = (
                    (tp - entry)
                    / entry
                    * 100
                )

            else:

                sl_pct = (
                    (entry - sl)
                    / entry
                    * 100
                )

                tp_pct = (
                    (entry - tp)
                    / entry
                    * 100
                )

            emoji = (
                "🟢"
                if trade["direction"] == "LONG"
                else "🔴"
            )

            # ------------------------------------------------
            # Trade block
            # ------------------------------------------------

            text += (
                f"{emoji} "
                f"<b>{trade['asset']} "
                f"{trade['direction']}</b>\n"
                f"📌 Pattern: "
                f"{trade['pattern']}\n"
                f"💰 Entry: "
                f"{entry:.8g}\n"
                f"{current_text}"
                f"🛑 SL: "
                f"{sl:.8g} "
                f"({sl_pct:+.2f}%)\n"
                f"🎯 TP: "
                f"{tp:.8g} "
                f"({tp_pct:+.2f}%)\n"
                f"⚙️ RR: "
                f"{RR:.2f}\n"
                f"🕐 Entry: "
                f"{format_time(trade['entry_time'])}\n\n"
            )

    else:

        text += (
            "<b>🟢 OPEN TRADES: 0</b>\n\n"
        )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    text += (
        f"📦 Total: "
        f"{stats['total']}\n"
        f"🔒 Closed: "
        f"{stats['closed']}\n"
        f"✅ Wins: "
        f"{stats['wins']}\n"
        f"❌ Losses: "
        f"{stats['losses']}\n"
        f"📈 WR: "
        f"{stats['wr']:.2f}%\n"
        f"💰 Net PnL: "
        f"{stats['net']:+.2f}%\n\n"
        f"⚙️ TP "
        f"{TP_PCT * 100:.2f}%"
        f" | SL "
        f"{SL_PCT * 100:.2f}%"
        f" | RR "
        f"{RR:.2f}\n"
        f"🧪 Real trading: "
        f"{'ENABLED' if REAL_TRADING else 'DISABLED'}"
    )

    send_telegram(
        text
    )


# ============================================================
# SCAN NEW SIGNALS
# ============================================================

def scan_new_signals(
    contract_map
):

    all_entries = []

    for asset in ASSETS:

        contract = (
            find_contract_for_asset(
                asset,
                contract_map,
            )
        )

        if contract is None:

            print(
                f"No contract for {asset}"
            )

            continue

        try:

            df_1h = (
                fetch_recent_candles(
                    contract,
                    MAIN_INTERVAL,
                    MAIN_LOOKBACK,
                )
            )

            df_5m = (
                fetch_recent_candles(
                    contract,
                    ENTRY_INTERVAL,
                    ENTRY_LOOKBACK,
                )
            )

            if (
                df_1h.empty
                or df_5m.empty
            ):
                continue

            entries = (
                generate_live_entries(
                    asset,
                    contract,
                    df_1h,
                    df_5m,
                )
            )

            all_entries.extend(
                entries
            )

        except Exception as e:

            print(
                f"Scan error "
                f"{asset}: {e}"
            )

            traceback.print_exc()

    return all_entries


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        "KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER"
    )

    print(
        "VERSION 5.4"
    )

    print(
        "=================================================="
    )

    print(
        f"REAL_TRADING = "
        f"{REAL_TRADING}"
    )

    print(
        f"MAX_ENTRY_AGE_CANDLES = "
        f"{MAX_ENTRY_AGE_CANDLES}"
    )

    print(
        f"PERIODIC_REPORT_SECONDS = "
        f"{PERIODIC_REPORT_SECONDS}"
    )

    init_db()

    # --------------------------------------------------------
    # LBANK DISPLAY PRICE
    # --------------------------------------------------------

    print(
        "Fetching LBank prices..."
    )

    lbank_prices = (
        get_lbank_snapshot()
    )

    # --------------------------------------------------------
    # CONTRACTS
    # --------------------------------------------------------

    print(
        "Building Kraken universe..."
    )

    contract_map = (
        build_contract_map()
    )

    # --------------------------------------------------------
    # HISTORICAL CLOSE RECONCILIATION
    # --------------------------------------------------------

    print(
        "Reconciling historical closes..."
    )

    update_open_trades_from_history(
        contract_map
    )

    # --------------------------------------------------------
    # NEW SIGNAL SCAN
    # --------------------------------------------------------

    print(
        "Scanning new signals..."
    )

    entries = scan_new_signals(
        contract_map
    )

    new_count = 0

    for trade in entries:

        inserted = insert_trade(
            trade
        )

        if not inserted:
            continue

        new_count += 1

        display_price = (
            lbank_prices.get(
                trade["asset"]
            )
        )

        print(
            "NEW SIGNAL:",
            trade["asset"],
            trade["direction"],
            trade["pattern"],
            "Entry=",
            format_time(
                trade["entry_time"]
            ),
            "Detected=",
            format_time(
                trade["detected_at"]
            ),
        )

        # ----------------------------------------------------
        # IMMEDIATE NEW SIGNAL ALERT
        # ----------------------------------------------------

        send_new_signal_alert(
            trade,
            display_price,
        )

        inserted_row = (
            get_trade_by_signal_key(
                trade["signal_key"]
            )
        )

        if inserted_row:

            inserted_trade = (
                row_to_trade(
                    inserted_row
                )
            )

            mark_new_notified(
                inserted_trade["id"]
            )

    print(
        f"New signals inserted: "
        f"{new_count}"
    )

    # --------------------------------------------------------
    # CURRENT PRICE EXITS
    # --------------------------------------------------------

    print(
        "Processing current-price exits..."
    )

    process_current_price_exits(
        lbank_prices
    )

    # --------------------------------------------------------
    # PERIODIC REPORT
    # --------------------------------------------------------

    report_file = (
        "last_report_timestamp.txt"
    )

    now_ts = int(
        utc_now().timestamp()
    )

    last_report = 0

    try:

        if os.path.exists(
            report_file
        ):

            with open(
                report_file,
                "r",
                encoding="utf-8",
            ) as f:

                last_report = int(
                    f.read().strip()
                )

    except Exception:

        last_report = 0

    if (
        now_ts
        - last_report
        >= PERIODIC_REPORT_SECONDS
    ):

        print(
            "Sending periodic report..."
        )

        send_periodic_report()

        try:

            with open(
                report_file,
                "w",
                encoding="utf-8",
            ) as f:

                f.write(
                    str(now_ts)
                )

        except Exception as e:

            print(
                "Report timestamp error:",
                e,
            )

    print(
        "Scan completed."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "FATAL ERROR:",
            e,
        )

        traceback.print_exc()
