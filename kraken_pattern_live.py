# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.9
# ============================================================
#
# V5.9:
#
# - V5.8 STRATEGY FULLY PRESERVED
# - FIXED DATABASE INSERT FOR REQUIRED "symbol" COLUMN
# - EXISTING DATABASE PRESERVED
# - DATABASE MIGRATION CHECK FOR "symbol"
# - DUPLICATE SIGNAL_KEYS WITHIN SAME SCAN ARE REMOVED
# - SAME SIGNAL IS INSERTED/PROCESSED ONLY ONCE PER RUN
# - TELEGRAM NEW SIGNAL LOGIC PRESERVED
# - REAL TRADING DISABLED
#
# STRATEGY:
#
# 1H Pattern
# ↓
# 1H Closed Candle Breakout
# ↓
# First 5M Retest
# ↓
# 5M Confirmation
# ↓
# Entry
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

PERIODIC_REPORT_SECONDS = 15 * 60

# KEEP ORIGINAL DATABASE
DB_FILE = "kraken_pattern_live_v52.db"


# ============================================================
# SIGNAL FRESHNESS
# ============================================================

MAX_ENTRY_AGE_CANDLES = 2


# ============================================================
# ASSETS
# ============================================================

ASSETS = [

    # ORIGINAL 20

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

    # NEW 20

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

        print(
            "Telegram token missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat id missing"
        )

        return False

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
                "Telegram HTTP error:",
                r.status_code,
                r.text,
            )

            return False

        try:

            result = r.json()

        except ValueError:

            print(
                "Telegram invalid JSON response:",
                r.text,
            )

            return False

        if not result.get(
            "ok",
            False,
        ):

            print(
                "Telegram API error:",
                result,
            )

            return False

        print(
            "Telegram message sent successfully."
        )

        return True

    except Exception as e:

        print(
            "Telegram exception:",
            e,
        )

        return False


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
        headers={
            "Accept": "application/json",
        },
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

            return contract_map[
                symbol
            ]

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
        "12h": 43200,
        "1d": 86400,
        "1w": 604800,
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
            "from": current_start,
            "to": current_end,
        }

        path = (
            "/api/charts/v1/trade/"
            f"{symbol}/"
            f"{interval}"
        )

        try:

            data = kraken_get(
                path,
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

        if candles:
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

    df = df[
        df["timestamp"] <= now_ts
    ]

    df = df[
        (
            df["timestamp"]
            + interval_seconds
        ) <= now_ts
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

    # DOUBLE TOP

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

    # DOUBLE BOTTOM

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

        # LONG FLAG

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

        # SHORT FLAG

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
    diagnostics=None,
    latest_5m_ts=None,
):

    indices = df.index[
        df["timestamp"]
        > breakout_time
    ].tolist()

    if not indices:

        if diagnostics is not None:
            diagnostics["no_retest"] += 1

        return None

    first_index = indices[0]

    retest_end = min(
        first_index
        + RETEST_MAX_BARS,
        len(df),
    )

    retest_index = None

    # FIRST RETEST ONLY

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

        if diagnostics is not None:
            diagnostics["no_retest"] += 1

        return None

    if diagnostics is not None:
        diagnostics["retests_found"] += 1

    # CONFIRMATION AFTER RETEST

    confirm_end = min(
        retest_index
        + 1
        + CONFIRM_MAX_BARS,
        len(df),
    )

    # FRESHNESS WINDOW

    max_entry_age_seconds = (
        MAX_ENTRY_AGE_CANDLES
        * 300
    )

    minimum_fresh_entry_ts = None

    if latest_5m_ts is not None:

        minimum_fresh_entry_ts = (
            int(latest_5m_ts)
            - max_entry_age_seconds
        )

    for i in range(
        retest_index + 1,
        confirm_end,
    ):

        row = df.iloc[i]

        entry_timestamp = int(
            row["timestamp"]
        )

        if (
            minimum_fresh_entry_ts is not None
            and entry_timestamp
            < minimum_fresh_entry_ts
        ):

            continue

        if (
            latest_5m_ts is not None
            and entry_timestamp
            > int(latest_5m_ts)
        ):

            continue

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

        if diagnostics is not None:

            diagnostics[
                "confirmations_found"
            ] += 1

        return {
            "entry_time":
                entry_timestamp,

            "entry_price":
                c,

            "retest_time":
                int(
                    df.iloc[
                        retest_index
                    ]["timestamp"]
                ),

            "confirm_time":
                entry_timestamp,
        }

    if diagnostics is not None:

        diagnostics[
            "no_confirmation"
        ] += 1

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

    diagnostics = {
        "patterns_total": len(patterns),
        "recent_patterns": 0,
        "breakouts_found": 0,
        "old_breakouts": 0,
        "retests_found": 0,
        "no_retest": 0,
        "confirmations_found": 0,
        "no_confirmation": 0,
        "stale_entries": 0,
        "valid_entries": 0,
    }

    if not patterns:

        print(
            f"[DIAG] {asset} | "
            f"Patterns=0"
        )

        return [], diagnostics

    entries = []

    recent_start_ts = int(
        df_1h.iloc[
            max(
                0,
                len(df_1h) - 180,
            )
        ]["timestamp"]
    )

    latest_5m_ts = int(
        df_5m.iloc[-1][
            "timestamp"
        ]
    )

    max_entry_age_seconds = (
        MAX_ENTRY_AGE_CANDLES
        * 300
    )

    maximum_entry_search_seconds = (
        (
            RETEST_MAX_BARS
            + CONFIRM_MAX_BARS
            + MAX_ENTRY_AGE_CANDLES
        )
        * 300
    )

    earliest_possible_breakout_time = (
        latest_5m_ts
        - maximum_entry_search_seconds
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

        diagnostics[
            "recent_patterns"
        ] += 1

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

        search_end = min(
            len(df_1h),
            p["end"] + 200,
        )

        breakout_candidates = []

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

            breakout_open_time = int(
                df_1h.iloc[i][
                    "timestamp"
                ]
            )

            breakout_close_time = (
                breakout_open_time
                + 3600
            )

            breakout_candidates.append(
                (
                    i,
                    breakout_open_time,
                    breakout_close_time,
                )
            )

        diagnostics[
            "breakouts_found"
        ] += len(
            breakout_candidates
        )

        breakout_candidates.reverse()

        for (
            i,
            breakout_open_time,
            breakout_close_time,
        ) in breakout_candidates:

            if (
                breakout_close_time
                < earliest_possible_breakout_time
            ):

                diagnostics[
                    "old_breakouts"
                ] += 1

                continue

            entry = find_entry(
                df_5m,
                breakout_close_time,
                p["direction"],
                level,
                diagnostics,
                latest_5m_ts,
            )

            if entry is None:
                continue

            entry_age_seconds = (
                latest_5m_ts
                - int(
                    entry[
                        "entry_time"
                    ]
                )
            )

            if (
                entry_age_seconds
                > max_entry_age_seconds
            ):

                diagnostics[
                    "stale_entries"
                ] += 1

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

            signal_key = (
                f"{asset}|"
                f"{p['pattern']}|"
                f"{p['direction']}|"
                f"{breakout_close_time}|"
                f"{entry['entry_time']}"
            )

            diagnostics[
                "valid_entries"
            ] += 1

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

                    "symbol":
                        contract.get(
                            "symbol"
                        ),

                    "pattern":
                        p["pattern"],

                    "direction":
                        p["direction"],

                    "breakout_time":
                        breakout_close_time,

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

            break

    no_entry_total = (
        diagnostics["no_retest"]
        + diagnostics["no_confirmation"]
    )

    print(
        f"[DIAG] {asset} | "
        f"Patterns={diagnostics['patterns_total']} | "
        f"Recent={diagnostics['recent_patterns']} | "
        f"Breakouts={diagnostics['breakouts_found']} | "
        f"OldBreakouts={diagnostics['old_breakouts']} | "
        f"Retests={diagnostics['retests_found']} | "
        f"Confirmations={diagnostics['confirmations_found']} | "
        f"NoEntry={no_entry_total} | "
        f"Stale={diagnostics['stale_entries']} | "
        f"Valid={diagnostics['valid_entries']}"
    )

    return entries, diagnostics


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            signal_key TEXT UNIQUE,

            asset TEXT,
            symbol TEXT,
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

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    existing_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    # --------------------------------------------------------
    # SYMBOL MIGRATION
    # --------------------------------------------------------

    if "symbol" not in existing_columns:

        print(
            "Migrating old DB: adding "
            "'symbol' column..."
        )

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN symbol TEXT
            """
        )

    if "contract" not in existing_columns:

        print(
            "Migrating old DB: adding "
            "'contract' column..."
        )

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN contract TEXT
            """
        )

    if "exit_reason" not in existing_columns:

        print(
            "Migrating old DB: adding "
            "'exit_reason' column..."
        )

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN exit_reason TEXT
            """
        )

    if "confirm_time" not in existing_columns:

        print(
            "Migrating old DB: adding "
            "'confirm_time' column..."
        )

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN confirm_time INTEGER
            """
        )

    if "notified_new" not in existing_columns:

        print(
            "Migrating old DB: adding "
            "'notified_new' column..."
        )

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN notified_new INTEGER DEFAULT 0
            """
        )

    if "notified_close" not in existing_columns:

        print(
            "Migrating old DB: adding "
            "'notified_close' column..."
        )

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN notified_close INTEGER DEFAULT 0
            """
        )

    # --------------------------------------------------------
    # BACKFILL SYMBOL FROM CONTRACT WHERE POSSIBLE
    # --------------------------------------------------------

    try:

        cur.execute(
            """
            UPDATE trades
            SET symbol = contract
            WHERE (
                symbol IS NULL
                OR symbol = ''
            )
            AND contract IS NOT NULL
            """
        )

        updated_symbol_rows = (
            cur.rowcount
        )

        if updated_symbol_rows:

            print(
                "Backfilled symbol from contract: "
                f"{updated_symbol_rows} rows"
            )

    except Exception as e:

        print(
            "Symbol backfill warning:",
            e,
        )

    conn.commit()

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    final_columns = {
        row["name"]
        for row in cur.fetchall()
    }

    required_columns = {
        "signal_key",
        "asset",
        "symbol",
        "contract",
        "pattern",
        "direction",
        "breakout_time",
        "retest_time",
        "confirm_time",
        "entry_time",
        "entry_price",
        "sl_price",
        "tp_price",
        "exit_time",
        "exit_price",
        "exit_reason",
        "status",
        "detected_at",
        "notified_new",
        "notified_close",
    }

    missing_columns = (
        required_columns
        - final_columns
    )

    if missing_columns:

        conn.close()

        raise RuntimeError(
            "Database migration incomplete. "
            f"Missing columns: "
            f"{sorted(missing_columns)}"
        )

    print(
        "Database schema verified."
    )

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


# ============================================================
# INSERT TRADE
# WITH EXACT SKIP DIAGNOSTICS
# ============================================================

def insert_trade(
    trade
):

    conn = db_connect()

    cur = conn.cursor()

    try:

        # ----------------------------------------------------
        # CHECK EXACT SIGNAL KEY FIRST
        # ----------------------------------------------------

        cur.execute(
            """
            SELECT
                id,
                signal_key,
                asset,
                symbol,
                contract,
                direction,
                pattern,
                status,
                entry_time,
                exit_time
            FROM trades
            WHERE signal_key = ?
            """,
            (
                trade["signal_key"],
            ),
        )

        existing = cur.fetchone()

        if existing is not None:

            print(
                "=================================================="
            )

            print(
                "[VALID BUT NOT INSERTED]"
            )

            print(
                f"Asset: "
                f"{trade['asset']}"
            )

            print(
                f"Direction: "
                f"{trade['direction']}"
            )

            print(
                f"Pattern: "
                f"{trade['pattern']}"
            )

            print(
                f"Entry: "
                f"{trade['entry_price']}"
            )

            print(
                f"Entry time: "
                f"{format_time(trade['entry_time'])}"
            )

            print(
                f"Signal key: "
                f"{trade['signal_key']}"
            )

            print(
                "[SKIP REASON] "
                "Duplicate signal_key already exists in DB"
            )

            print(
                f"Existing DB ID: "
                f"{existing['id']}"
            )

            print(
                f"Existing symbol: "
                f"{existing['symbol']}"
            )

            print(
                f"Existing contract: "
                f"{existing['contract']}"
            )

            print(
                f"Existing status: "
                f"{existing['status']}"
            )

            print(
                f"Existing entry time: "
                f"{format_time(existing['entry_time'])}"
            )

            if existing["exit_time"]:

                print(
                    f"Existing exit time: "
                    f"{format_time(existing['exit_time'])}"
                )

            else:

                print(
                    "Existing exit time: OPEN"
                )

            print(
                "=================================================="
            )

            conn.close()

            return False

        # ----------------------------------------------------
        # RESOLVE SYMBOL
        # ----------------------------------------------------

        symbol = (
            trade.get("symbol")
            or trade.get("contract")
        )

        if not symbol:

            print(
                "=================================================="
            )

            print(
                "[INSERT ERROR] Missing symbol/contract"
            )

            print(
                f"Asset: {trade.get('asset')}"
            )

            print(
                f"Signal key: "
                f"{trade.get('signal_key')}"
            )

            print(
                "=================================================="
            )

            conn.close()

            return False

        # ----------------------------------------------------
        # INSERT
        # ----------------------------------------------------

        cur.execute(
            """
            INSERT INTO trades (
                signal_key,
                asset,
                symbol,
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
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, NULL, NULL, NULL,
                'OPEN', ?, 0, 0
            )
            """,
            (
                trade["signal_key"],
                trade["asset"],
                symbol,
                trade.get(
                    "contract"
                ) or symbol,
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

        conn.commit()

        print(
            "=================================================="
        )

        print(
            "[NEW SIGNAL INSERTED]"
        )

        print(
            f"Asset: "
            f"{trade['asset']}"
        )

        print(
            f"Symbol: "
            f"{symbol}"
        )

        print(
            f"Direction: "
            f"{trade['direction']}"
        )

        print(
            f"Pattern: "
            f"{trade['pattern']}"
        )

        print(
            f"Entry: "
            f"{trade['entry_price']}"
        )

        print(
            f"Entry time: "
            f"{format_time(trade['entry_time'])}"
        )

        print(
            f"Signal key: "
            f"{trade['signal_key']}"
        )

        print(
            "=================================================="
        )

        conn.close()

        return True

    except sqlite3.IntegrityError as e:

        conn.rollback()

        print(
            "=================================================="
        )

        print(
            "[INSERT INTEGRITY ERROR]"
        )

        print(
            f"Asset: "
            f"{trade.get('asset')}"
        )

        print(
            f"Direction: "
            f"{trade.get('direction')}"
        )

        print(
            f"Pattern: "
            f"{trade.get('pattern')}"
        )

        print(
            f"Signal key: "
            f"{trade.get('signal_key')}"
        )

        print(
            f"Symbol: "
            f"{trade.get('symbol')}"
        )

        print(
            f"Contract: "
            f"{trade.get('contract')}"
        )

        print(
            f"Error: {e}"
        )

        print(
            "=================================================="
        )

        conn.close()

        return False

    except Exception as e:

        conn.rollback()

        print(
            "=================================================="
        )

        print(
            "[INSERT ERROR]"
        )

        print(
            f"Asset: "
            f"{trade.get('asset')}"
        )

        print(
            f"Direction: "
            f"{trade.get('direction')}"
        )

        print(
            f"Pattern: "
            f"{trade.get('pattern')}"
        )

        print(
            f"Signal key: "
            f"{trade.get('signal_key')}"
        )

        print(
            f"Error: {e}"
        )

        traceback.print_exc()

        print(
            "=================================================="
        )

        conn.close()

        return False


# ============================================================
# KRAKEN LIVE PRICE
# ============================================================

def get_kraken_live_prices(
    contract_map
):

    try:

        data = kraken_get(
            "/derivatives/api/v3/tickers"
        )

        tickers = data.get(
            "tickers",
            []
        )

        ticker_by_symbol = {}

        for ticker in tickers:

            symbol = ticker.get(
                "symbol"
            )

            if not symbol:
                continue

            last = ticker.get(
                "last"
            )

            if last is None:
                continue

            try:

                last = float(
                    last
                )

            except (
                TypeError,
                ValueError,
            ):

                continue

            if last <= 0:
                continue

            ticker_by_symbol[
                symbol
            ] = last

        prices = {}

        for asset in ASSETS:

            contract = (
                find_contract_for_asset(
                    asset,
                    contract_map,
                )
            )

            if contract is None:
                continue

            symbol = contract.get(
                "symbol"
            )

            if not symbol:
                continue

            last = ticker_by_symbol.get(
                symbol
            )

            if last is None:
                continue

            prices[asset] = last

        return prices

    except Exception as e:

        print(
            "Kraken live ticker error:",
            e,
        )

        return {}


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

        current_price = float(
            current_price
        )

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

    return send_telegram(
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

    return send_telegram(
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

    try:

        if isinstance(
            row,
            sqlite3.Row
        ):

            data = dict(row)

        elif hasattr(
            row,
            "keys"
        ):

            data = {
                key: row[key]
                for key in row.keys()
            }

        else:

            return {
                "id": row[0],
                "signal_key": row[1],
                "asset": row[2],
                "symbol": row[3],
                "contract": row[4],
                "pattern": row[5],
                "direction": row[6],
                "breakout_time": row[7],
                "retest_time": row[8],
                "confirm_time": row[9],
                "entry_time": row[10],
                "entry_price": row[11],
                "sl_price": row[12],
                "tp_price": row[13],
                "exit_time": row[14],
                "exit_price": row[15],
                "exit_reason": row[16],
                "status": row[17],
                "detected_at": row[18],
                "notified_new": row[19],
                "notified_close": row[20],
            }

    except Exception as e:

        print(
            "row_to_trade conversion error:",
            e,
        )

        raise

    return {
        "id":
            data.get("id"),

        "signal_key":
            data.get(
                "signal_key",
                "",
            ),

        "asset":
            data.get(
                "asset",
                "",
            ),

        "symbol":
            data.get(
                "symbol"
            ),

        "contract":
            data.get(
                "contract"
            ),

        "pattern":
            data.get(
                "pattern",
                "",
            ),

        "direction":
            data.get(
                "direction",
                "",
            ),

        "breakout_time":
            data.get(
                "breakout_time"
            ),

        "retest_time":
            data.get(
                "retest_time"
            ),

        "confirm_time":
            data.get(
                "confirm_time"
            ),

        "entry_time":
            data.get(
                "entry_time"
            ),

        "entry_price":
            data.get(
                "entry_price"
            ),

        "sl_price":
            data.get(
                "sl_price"
            ),

        "tp_price":
            data.get(
                "tp_price"
            ),

        "exit_time":
            data.get(
                "exit_time"
            ),

        "exit_price":
            data.get(
                "exit_price"
            ),

        "exit_reason":
            data.get(
                "exit_reason"
            ),

        "status":
            data.get(
                "status",
                "",
            ),

        "detected_at":
            data.get(
                "detected_at"
            ),

        "notified_new":
            data.get(
                "notified_new",
                0,
            ),

        "notified_close":
            data.get(
                "notified_close",
                0,
            ),
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

        contract_key = trade.get(
            "contract"
        )

        contract = None

        if contract_key:

            contract = contract_map.get(
                contract_key
            )

        if contract is None:

            contract = (
                find_contract_for_asset(
                    asset,
                    contract_map,
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

                sent = send_close_alert(
                    updated_trade
                )

                if sent:

                    mark_close_notified(
                        updated_trade["id"]
                    )


# ============================================================
# CURRENT KRAKEN PRICE EXITS
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

        current = prices.get(
            asset
        )

        if current is None:
            continue

        current = float(
            current
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

                sent = send_close_alert(
                    updated_trade
                )

                if sent:

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

def send_periodic_report(
    kraken_prices=None
):

    stats = performance_stats()

    open_rows = (
        get_open_trades()
    )

    if kraken_prices is None:
        kraken_prices = {}

    text = (
        "<b>📊 KRAKEN PATTERN SCANNER</b>\n\n"
    )

    if open_rows:

        text += (
            f"<b>🟢 OPEN TRADES: "
            f"{len(open_rows)}</b>\n\n"
        )

        for row in open_rows:

            trade = row_to_trade(
                row
            )

            current_price = (
                kraken_prices.get(
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

            if current_price is not None:

                current_price = float(
                    current_price
                )

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

    return send_telegram(
        text
    )


# ============================================================
# SCAN NEW SIGNALS
# ============================================================

def scan_new_signals(
    contract_map
):

    all_entries = []

    # --------------------------------------------------------
    # PREVENT SAME SIGNAL KEY FROM BEING ADDED MULTIPLE TIMES
    # DURING THE SAME SCAN
    # --------------------------------------------------------

    seen_signal_keys = set()

    total_diag = {
        "patterns_total": 0,
        "recent_patterns": 0,
        "breakouts_found": 0,
        "old_breakouts": 0,
        "retests_found": 0,
        "no_retest": 0,
        "confirmations_found": 0,
        "no_confirmation": 0,
        "stale_entries": 0,
        "valid_entries": 0,
    }

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

                print(
                    f"No candle data for "
                    f"{asset}"
                )

                continue

            print(
                f"OHLC loaded: "
                f"{asset} "
                f"1H={len(df_1h)} "
                f"5M={len(df_5m)}"
            )

            entries, diagnostics = (
                generate_live_entries(
                    asset,
                    contract,
                    df_1h,
                    df_5m,
                )
            )

            # ------------------------------------------------
            # DEDUPLICATE SIGNALS WITHIN THIS RUN
            # ------------------------------------------------

            for trade in entries:

                signal_key = trade.get(
                    "signal_key"
                )

                if not signal_key:
                    continue

                if signal_key in seen_signal_keys:

                    print(
                        "[DUPLICATE IN SAME SCAN] "
                        f"{signal_key}"
                    )

                    continue

                seen_signal_keys.add(
                    signal_key
                )

                all_entries.append(
                    trade
                )

            for key in total_diag:

                total_diag[key] += (
                    diagnostics.get(
                        key,
                        0
                    )
                )

        except Exception as e:

            print(
                f"Scan error "
                f"{asset}: {e}"
            )

            traceback.print_exc()

    no_entry_total = (
        total_diag["no_retest"]
        + total_diag["no_confirmation"]
    )

    print(
        "=================================================="
    )

    print(
        "[GLOBAL DIAGNOSTICS]"
    )

    print(
        f"Assets scanned: "
        f"{len(ASSETS)}"
    )

    print(
        f"Patterns detected: "
        f"{total_diag['patterns_total']}"
    )

    print(
        f"Recent patterns: "
        f"{total_diag['recent_patterns']}"
    )

    print(
        f"Breakouts found: "
        f"{total_diag['breakouts_found']}"
    )

    print(
        f"Old breakouts: "
        f"{total_diag['old_breakouts']}"
    )

    print(
        f"Retests found: "
        f"{total_diag['retests_found']}"
    )

    print(
        f"Confirmations found: "
        f"{total_diag['confirmations_found']}"
    )

    print(
        f"No valid Retest/Confirmation: "
        f"{no_entry_total}"
    )

    print(
        f"Stale entries: "
        f"{total_diag['stale_entries']}"
    )

    print(
        f"Valid entries: "
        f"{total_diag['valid_entries']}"
    )

    print(
        f"Unique entries this scan: "
        f"{len(all_entries)}"
    )

    print(
        "=================================================="
    )

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
        "VERSION 5.9"
    )

    print(
        "=================================================="
    )

    print(
        f"REAL_TRADING = "
        f"{REAL_TRADING}"
    )

    print(
        f"ASSETS = "
        f"{len(ASSETS)}"
    )

    print(
        f"MAX_ENTRY_AGE_CANDLES = "
        f"{MAX_ENTRY_AGE_CANDLES}"
    )

    print(
        f"MAX ENTRY AGE = "
        f"{MAX_ENTRY_AGE_CANDLES * 5} MINUTES"
    )

    print(
        f"PERIODIC_REPORT_SECONDS = "
        f"{PERIODIC_REPORT_SECONDS}"
    )

    # DATABASE

    init_db()

    # CONTRACTS

    print(
        "Building Kraken universe..."
    )

    contract_map = (
        build_contract_map()
    )

    print(
        f"Kraken contracts loaded: "
        f"{len(contract_map)}"
    )

    # CHECK UNIVERSE

    print(
        "Checking requested 40-asset universe..."
    )

    available_assets = []

    missing_assets = []

    for asset in ASSETS:

        contract = (
            find_contract_for_asset(
                asset,
                contract_map,
            )
        )

        if contract is None:

            missing_assets.append(
                asset
            )

        else:

            available_assets.append(
                asset
            )

    print(
        f"Requested assets: "
        f"{len(ASSETS)}"
    )

    print(
        f"Available contracts: "
        f"{len(available_assets)}"
    )

    if missing_assets:

        print(
            "WARNING - Missing contracts:"
        )

        print(
            ", ".join(
                missing_assets
            )
        )

    # LIVE PRICES

    print(
        "Fetching Kraken live prices..."
    )

    kraken_prices = (
        get_kraken_live_prices(
            contract_map
        )
    )

    print(
        f"Kraken live prices loaded: "
        f"{len(kraken_prices)}"
    )

    # HISTORICAL CLOSES

    print(
        "Reconciling historical closes..."
    )

    update_open_trades_from_history(
        contract_map
    )

    # NEW SIGNAL SCAN

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

            print(
                f"[NO NEW INSERT] "
                f"{trade['asset']} "
                f"{trade['direction']} "
                f"| {trade['pattern']} "
                f"| Entry={trade['entry_price']} "
                f"| EntryTime="
                f"{format_time(trade['entry_time'])}"
            )

            continue

        new_count += 1

        # NEW SIGNAL CURRENT PRICE

        display_price = (
            kraken_prices.get(
                trade["asset"]
            )
        )

        print(
            "NEW SIGNAL:",
            trade["asset"],
            trade["direction"],
            trade["pattern"],
            "Entry=",
            trade["entry_price"],
            "EntryTime=",
            format_time(
                trade["entry_time"]
            ),
            "Current=",
            display_price,
            "Detected=",
            format_time(
                trade["detected_at"]
            ),
        )

        # IMMEDIATE TELEGRAM

        sent = send_new_signal_alert(
            trade,
            display_price,
        )

        inserted_row = (
            get_trade_by_signal_key(
                trade["signal_key"]
            )
        )

        if inserted_row and sent:

            inserted_trade = (
                row_to_trade(
                    inserted_row
                )
            )

            mark_new_notified(
                inserted_trade["id"]
            )

        elif inserted_row and not sent:

            print(
                "NEW SIGNAL Telegram alert "
                "failed. Notification flag "
                "was NOT updated."
            )

    print(
        f"New signals inserted: "
        f"{new_count}"
    )

    # CURRENT PRICE EXITS

    print(
        "Processing Kraken current-price exits..."
    )

    process_current_price_exits(
        kraken_prices
    )

    # PERIODIC REPORT

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

        report_sent = (
            send_periodic_report(
                kraken_prices
            )
        )

        if report_sent:

            try:

                with open(
                    report_file,
                    "w",
                    encoding="utf-8",
                ) as f:

                    f.write(
                        str(now_ts)
                    )

                print(
                    "Periodic report sent "
                    "and timestamp updated."
                )

            except Exception as e:

                print(
                    "Report timestamp error:",
                    e,
                )

        else:

            print(
                "Periodic report was NOT sent. "
                "Timestamp was NOT updated; "
                "it will be retried on the next run."
            )

    else:

        remaining = (
            PERIODIC_REPORT_SECONDS
            - (
                now_ts
                - last_report
            )
        )

        print(
            f"Periodic report not due yet. "
            f"Remaining: "
            f"{max(0, remaining)} seconds."
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
