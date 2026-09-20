# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.9.3
# ============================================================
#
# BASED ON V5.9.2
#
# FIXES:
# - FIXED ENTRY LOGIC
# - CONFIRMATION CANDLE IS THE ENTRY CANDLE
# - NO LOOKAHEAD
# - CLOSED CANDLES ONLY
# - FIXED KRAKEN CANDLE INTERVALS
# - FIXED EXACT CONTRACT MAPPING
# - PRESERVES EXISTING DATABASE
# - SAFE DATABASE MIGRATION
# - updated_at ALWAYS PROVIDED
#
# STRATEGY:
# 1H Pattern
# -> 1H CLOSED-CANDLE BREAKOUT
# -> FIRST 5M RETEST
# -> 5M CONFIRMATION
# -> ENTRY AT CONFIRMATION CLOSE
#
# RULES:
# - CLOSED CANDLES ONLY
# - NO LOOKAHEAD
# - FIRST RETEST ONLY
# - CONFIRMATION AFTER RETEST
# - CONFIRMATION CANDLE = ENTRY CANDLE
# - ENTRY CANDLE NOT USED FOR TP/SL
# - SAME CANDLE TP+SL => SL FIRST
#
# PAPER ONLY
# REAL_TRADING MUST REMAIN FALSE
# ============================================================

import os
import time
import sqlite3
import traceback
import requests
import pandas as pd

from datetime import datetime, timezone
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.9.3"

BASE_URL = "https://futures.kraken.com"

DB_FILE = "kraken_pattern_live_v52.db"

REAL_TRADING = False

# IMPORTANT:
# Kraken charts endpoint expects interval strings.
INTERVAL_1H = "1h"
INTERVAL_5M = "5m"

SL_PCT = 0.01
TP_PCT = 0.01
RR = 1.0

MAX_HOLD_HOURS = 48

MAX_ENTRY_AGE_CANDLES = 2
MAX_ENTRY_AGE_MINUTES = 10

REQUEST_TIMEOUT = 20
REQUEST_SLEEP = 0.10

PERIODIC_REPORT_SECONDS = 900

TEHRAN_TZ = ZoneInfo("Asia/Tehran")


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
# PATTERN CONFIG
# ============================================================

PATTERN_LOOKBACK = 220

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

PATTERN_TOLERANCE = 0.012

MIN_PATTERN_DISTANCE = 3
MAX_PATTERN_DISTANCE = 80

BREAKOUT_LOOKAHEAD = 20
RETEST_LOOKAHEAD = 12
CONFIRMATION_LOOKAHEAD = 6


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def now_ts():
    return int(utc_now().timestamp())


def fmt_utc(ts):
    if not ts:
        return "-"

    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M UTC")


def fmt_tehran(ts):
    if not ts:
        return "-"

    return datetime.fromtimestamp(
        int(ts),
        tz=timezone.utc
    ).astimezone(TEHRAN_TZ).strftime(
        "%Y-%m-%d %H:%M"
    )


def duration_text(start_ts, end_ts=None):
    if not start_ts:
        return "-"

    if end_ts is None:
        end_ts = now_ts()

    seconds = max(
        0,
        int(end_ts) - int(start_ts)
    )

    hours = seconds // 3600
    minutes = (seconds % 3600) // 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        print(
            "[TELEGRAM] Token/chat ID not configured."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if r.ok:
            return True

        print(
            "[TELEGRAM ERROR]",
            r.status_code,
            r.text
        )

    except Exception as e:

        print(
            "[TELEGRAM EXCEPTION]",
            e
        )

    return False


# ============================================================
# KRAKEN REQUEST
# ============================================================

def kraken_get(path, params=None):

    url = BASE_URL + path

    try:

        r = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:

        print(
            f"[KRAKEN ERROR] {path}: {e}"
        )

        return None


# ============================================================
# CONTRACT DISCOVERY
# ============================================================

def get_contracts():

    data = kraken_get(
        "/derivatives/api/v3/instruments"
    )

    if not data:
        return []

    return data.get(
        "instruments",
        []
    )


def build_contract_map():

    instruments = get_contracts()

    print(
        f"Kraken contracts loaded: "
        f"{len(instruments)}"
    )

    instrument_symbols = set()

    for item in instruments:

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper()

        if symbol:
            instrument_symbols.add(
                symbol
            )

    contract_map = {}

    for asset in ASSETS:

        wanted = (
            f"PF_{asset}USD"
        ).upper()

        if wanted in instrument_symbols:

            contract_map[asset] = wanted

    print(
        f"Available assets: "
        f"{len(contract_map)}/{len(ASSETS)}"
    )

    for asset in ASSETS:

        if asset in contract_map:

            print(
                f"  {asset:<6} -> "
                f"{contract_map[asset]}"
            )

        else:

            print(
                f"  {asset:<6} -> NOT FOUND"
            )

    return contract_map


# ============================================================
# CANDLE FETCH
# ============================================================

def fetch_candles(
    contract,
    resolution,
    limit=300
):

    path = (
        f"/api/charts/v1/trade/"
        f"{contract}/{resolution}"
    )

    data = kraken_get(
        path,
        params={
            "count": limit
        }
    )

    if not data:
        return pd.DataFrame()

    rows = None

    if isinstance(data, dict):

        for key in [
            "candles",
            "data",
            "results",
            "ohlc"
        ]:

            value = data.get(key)

            if isinstance(value, list):

                rows = value
                break

    elif isinstance(data, list):

        rows = data

    if not rows:
        return pd.DataFrame()

    parsed = []

    for row in rows:

        if isinstance(row, dict):

            ts = (
                row.get("time")
                or row.get("timestamp")
                or row.get("t")
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

            if (
                ts is not None
                and op is not None
                and hi is not None
                and lo is not None
                and cl is not None
            ):

                parsed.append([
                    float(ts),
                    float(op),
                    float(hi),
                    float(lo),
                    float(cl),
                    float(vol),
                ])

        elif isinstance(
            row,
            (list, tuple)
        ):

            if len(row) >= 5:

                parsed.append([
                    float(row[0]),
                    float(row[1]),
                    float(row[2]),
                    float(row[3]),
                    float(row[4]),
                    float(row[5])
                    if len(row) > 5
                    else 0,
                ])

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
        ]
    )

    if (
        df["timestamp"].max()
        > 10_000_000_000
    ):

        df["timestamp"] = (
            df["timestamp"] / 1000
        )

    df["timestamp"] = (
        df["timestamp"]
        .astype(int)
    )

    df = (
        df.sort_values("timestamp")
        .drop_duplicates(
            "timestamp"
        )
        .reset_index(drop=True)
    )

    return df


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def closed_candles(
    df,
    interval_minutes
):

    if df.empty:
        return df

    current = now_ts()

    candle_seconds = (
        interval_minutes * 60
    )

    mask = (
        df["timestamp"]
        + candle_seconds
        <= current
    )

    return (
        df.loc[mask]
        .reset_index(drop=True)
    )


# ============================================================
# PIVOTS
# ============================================================

def pivot_highs(df):

    highs = []

    h = df["high"].values

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left = h[
            i - PIVOT_LEFT:i
        ]

        right = h[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            h[i] >= max(left)
            and h[i] >= max(right)
        ):

            highs.append(i)

    return highs


def pivot_lows(df):

    lows = []

    l = df["low"].values

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left = l[
            i - PIVOT_LEFT:i
        ]

        right = l[
            i + 1:
            i + 1 + PIVOT_RIGHT
        ]

        if (
            l[i] <= min(left)
            and l[i] <= min(right)
        ):

            lows.append(i)

    return lows


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(df):

    patterns = []

    highs = pivot_highs(df)

    if len(highs) < 2:
        return patterns

    for a in range(
        len(highs) - 2,
        -1,
        -1
    ):

        i1 = highs[a]

        for b in range(
            a + 1,
            len(highs)
        ):

            i2 = highs[b]

            distance = i2 - i1

            if (
                distance
                < MIN_PATTERN_DISTANCE
            ):
                continue

            if (
                distance
                > MAX_PATTERN_DISTANCE
            ):
                continue

            p1 = float(
                df.iloc[i1]["high"]
            )

            p2 = float(
                df.iloc[i2]["high"]
            )

            if p1 == 0:
                continue

            if (
                abs(p1 - p2) / p1
                > PATTERN_TOLERANCE
            ):
                continue

            between = df.iloc[
                i1:i2 + 1
            ]

            neckline = float(
                between["low"].min()
            )

            patterns.append({
                "pattern": "DOUBLE_TOP",
                "direction": "SHORT",
                "left_index": i1,
                "right_index": i2,
                "pattern_time": int(
                    df.iloc[i2]["timestamp"]
                ),
                "level": neckline,
                "high1": p1,
                "high2": p2,
            })

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(df):

    patterns = []

    lows = pivot_lows(df)

    if len(lows) < 2:
        return patterns

    for a in range(
        len(lows) - 2,
        -1,
        -1
    ):

        i1 = lows[a]

        for b in range(
            a + 1,
            len(lows)
        ):

            i2 = lows[b]

            distance = i2 - i1

            if (
                distance
                < MIN_PATTERN_DISTANCE
            ):
                continue

            if (
                distance
                > MAX_PATTERN_DISTANCE
            ):
                continue

            p1 = float(
                df.iloc[i1]["low"]
            )

            p2 = float(
                df.iloc[i2]["low"]
            )

            if p1 == 0:
                continue

            if (
                abs(p1 - p2) / p1
                > PATTERN_TOLERANCE
            ):
                continue

            between = df.iloc[
                i1:i2 + 1
            ]

            neckline = float(
                between["high"].max()
            )

            patterns.append({
                "pattern": "DOUBLE_BOTTOM",
                "direction": "LONG",
                "left_index": i1,
                "right_index": i2,
                "pattern_time": int(
                    df.iloc[i2]["timestamp"]
                ),
                "level": neckline,
                "low1": p1,
                "low2": p2,
            })

    return patterns


# ============================================================
# HEAD AND SHOULDERS
# ============================================================

def detect_head_shoulders(df):

    patterns = []

    highs = pivot_highs(df)

    if len(highs) < 3:
        return patterns

    for x in range(
        len(highs) - 3,
        -1,
        -1
    ):

        i1 = highs[x]
        i2 = highs[x + 1]
        i3 = highs[x + 2]

        if not (
            MIN_PATTERN_DISTANCE
            <= i2 - i1
            <= MAX_PATTERN_DISTANCE
        ):
            continue

        if not (
            MIN_PATTERN_DISTANCE
            <= i3 - i2
            <= MAX_PATTERN_DISTANCE
        ):
            continue

        left = float(
            df.iloc[i1]["high"]
        )

        head = float(
            df.iloc[i2]["high"]
        )

        right = float(
            df.iloc[i3]["high"]
        )

        if (
            head <= left
            or head <= right
        ):
            continue

        if left == 0:
            continue

        if (
            abs(left - right) / left
            > PATTERN_TOLERANCE * 1.5
        ):
            continue

        section1 = df.iloc[
            i1:i2 + 1
        ]

        section2 = df.iloc[
            i2:i3 + 1
        ]

        neckline = (
            float(section1["low"].min())
            + float(section2["low"].min())
        ) / 2

        patterns.append({
            "pattern":
                "HEAD_AND_SHOULDERS",
            "direction": "SHORT",
            "left_index": i1,
            "head_index": i2,
            "right_index": i3,
            "pattern_time": int(
                df.iloc[i3]["timestamp"]
            ),
            "level": neckline,
        })

    return patterns


# ============================================================
# INVERSE HEAD AND SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(
    df
):

    patterns = []

    lows = pivot_lows(df)

    if len(lows) < 3:
        return patterns

    for x in range(
        len(lows) - 3,
        -1,
        -1
    ):

        i1 = lows[x]
        i2 = lows[x + 1]
        i3 = lows[x + 2]

        if not (
            MIN_PATTERN_DISTANCE
            <= i2 - i1
            <= MAX_PATTERN_DISTANCE
        ):
            continue

        if not (
            MIN_PATTERN_DISTANCE
            <= i3 - i2
            <= MAX_PATTERN_DISTANCE
        ):
            continue

        left = float(
            df.iloc[i1]["low"]
        )

        head = float(
            df.iloc[i2]["low"]
        )

        right = float(
            df.iloc[i3]["low"]
        )

        if (
            head >= left
            or head >= right
        ):
            continue

        if left == 0:
            continue

        if (
            abs(left - right) / left
            > PATTERN_TOLERANCE * 1.5
        ):
            continue

        section1 = df.iloc[
            i1:i2 + 1
        ]

        section2 = df.iloc[
            i2:i3 + 1
        ]

        neckline = (
            float(section1["high"].max())
            + float(section2["high"].max())
        ) / 2

        patterns.append({
            "pattern":
                "INVERSE_HEAD_AND_SHOULDERS",
            "direction": "LONG",
            "left_index": i1,
            "head_index": i2,
            "right_index": i3,
            "pattern_time": int(
                df.iloc[i3]["timestamp"]
            ),
            "level": neckline,
        })

    return patterns


# ============================================================
# FLAG DETECTION
# ============================================================

def detect_flags(df):

    patterns = []

    if len(df) < 30:
        return patterns

    closes = df["close"]

    for i in range(
        20,
        len(df) - 1
    ):

        base = float(
            closes.iloc[i - 20]
        )

        current = float(
            closes.iloc[i]
        )

        if base == 0:
            continue

        move = (
            current - base
        ) / base

        recent = df.iloc[
            i - 5:i + 1
        ]

        range_pct = (
            float(recent["high"].max())
            - float(recent["low"].min())
        ) / current

        if move >= 0.025:

            if range_pct < 0.03:

                patterns.append({
                    "pattern": "BULL_FLAG",
                    "direction": "LONG",
                    "pattern_time": int(
                        df.iloc[i]["timestamp"]
                    ),
                    "level": float(
                        recent["high"].max()
                    ),
                    "left_index": i - 20,
                    "right_index": i,
                })

        elif move <= -0.025:

            if range_pct < 0.03:

                patterns.append({
                    "pattern": "BEAR_FLAG",
                    "direction": "SHORT",
                    "pattern_time": int(
                        df.iloc[i]["timestamp"]
                    ),
                    "level": float(
                        recent["low"].min()
                    ),
                    "left_index": i - 20,
                    "right_index": i,
                })

    return patterns


# ============================================================
# PATTERN DETECTION
# ============================================================

def detect_patterns(df):

    if df.empty:
        return []

    df = (
        df.tail(PATTERN_LOOKBACK)
        .reset_index(drop=True)
    )

    patterns = []

    patterns.extend(
        detect_double_top(df)
    )

    patterns.extend(
        detect_double_bottom(df)
    )

    patterns.extend(
        detect_head_shoulders(df)
    )

    patterns.extend(
        detect_inverse_head_shoulders(df)
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
    pattern
):

    if df.empty:
        return None

    level = pattern.get(
        "level"
    )

    if level is None:
        return None

    direction = pattern[
        "direction"
    ]

    pattern_time = pattern[
        "pattern_time"
    ]

    candidates = df[
        df["timestamp"] > pattern_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        BREAKOUT_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

        close = float(
            row["close"]
        )

        if direction == "LONG":

            if close > level:

                return {
                    "breakout_time": int(
                        row["timestamp"]
                    ),
                    "breakout_price": close,
                    "level": level,
                }

        else:

            if close < level:

                return {
                    "breakout_time": int(
                        row["timestamp"]
                    ),
                    "breakout_price": close,
                    "level": level,
                }

    return None


# ============================================================
# FIRST 5M RETEST
# ============================================================

def find_first_retest(
    df5,
    breakout,
    direction
):

    if df5.empty:
        return None

    level = float(
        breakout["level"]
    )

    breakout_time = int(
        breakout["breakout_time"]
    )

    candidates = df5[
        df5["timestamp"] > breakout_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        RETEST_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

        low = float(
            row["low"]
        )

        high = float(
            row["high"]
        )

        touched = (
            low <= level <= high
        )

        if touched:

            return {
                "retest_time": int(
                    row["timestamp"]
                ),
                "retest_price": level,
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def find_confirmation(
    df5,
    retest,
    direction
):

    if df5.empty:
        return None

    retest_time = int(
        retest["retest_time"]
    )

    candidates = df5[
        df5["timestamp"] > retest_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        CONFIRMATION_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

        op = float(
            row["open"]
        )

        cl = float(
            row["close"]
        )

        if direction == "LONG":

            if cl > op:

                return {
                    "confirm_time": int(
                        row["timestamp"]
                    ),
                    "confirm_price": cl,
                }

        else:

            if cl < op:

                return {
                    "confirm_time": int(
                        row["timestamp"]
                    ),
                    "confirm_price": cl,
                }

    return None


# ============================================================
# ENTRY
# ============================================================
#
# IMPORTANT FIX:
#
# The confirmation candle itself is the entry candle.
#
# Previous version:
# confirmation -> NEXT 5M candle -> entry
#
# That caused many valid confirmations to produce no entry,
# especially when confirmation was the latest closed candle.
#
# New version:
# retest -> confirmation candle closes -> ENTRY
#
# No lookahead.
# TP/SL are calculated from entry price only.
# ============================================================

def find_entry(
    df5,
    confirmation,
    direction
):

    if df5.empty:
        return None

    confirm_time = int(
        confirmation["confirm_time"]
    )

    confirm_price = float(
        confirmation["confirm_price"]
    )

    # Find the exact confirmation candle.
    rows = df5[
        df5["timestamp"] == confirm_time
    ]

    if rows.empty:
        return None

    row = rows.iloc[0]

    entry_time = int(
        row["timestamp"]
    )

    entry_price = float(
        row["close"]
    )

    # Safety check:
    # confirmation and entry must be the same
    # closed candle.
    if entry_time != confirm_time:
        return None

    # Use BOTH configured limits.
    candle_age_limit = (
        MAX_ENTRY_AGE_CANDLES * 5
    )

    max_age = min(
        candle_age_limit,
        MAX_ENTRY_AGE_MINUTES
    )

    age_minutes = (
        now_ts() - entry_time
    ) / 60

    if age_minutes < 0:
        return None

    if age_minutes > max_age:
        return None

    # Sanity check against confirmation price.
    # This does not alter the strategy.
    if entry_price <= 0:
        return None

    if direction == "LONG":

        sl = entry_price * (
            1 - SL_PCT
        )

        tp = entry_price * (
            1 + TP_PCT
        )

    else:

        sl = entry_price * (
            1 + SL_PCT
        )

        tp = entry_price * (
            1 - TP_PCT
        )

    return {
        "entry_time": entry_time,
        "entry_price": entry_price,
        "sl_price": sl,
        "tp_price": tp,
    }


# ============================================================
# GENERATE LIVE ENTRIES
# ============================================================

def generate_live_entries(
    asset,
    contract
):

    df1h = fetch_candles(
        contract,
        INTERVAL_1H,
        350
    )

    if df1h.empty:

        return [], {
            "patterns": 0,
            "recent": 0,
            "breakouts": 0,
            "old_breakouts": 0,
            "retests": 0,
            "confirmations": 0,
            "valid": 0,
            "stale": 0,
        }

    df1h = closed_candles(
        df1h,
        60
    )

    if len(df1h) < 50:

        return [], {
            "patterns": 0,
            "recent": 0,
            "breakouts": 0,
            "old_breakouts": 0,
            "retests": 0,
            "confirmations": 0,
            "valid": 0,
            "stale": 0,
        }

    patterns = detect_patterns(
        df1h
    )

    recent_patterns = []

    cutoff = (
        now_ts()
        - 48 * 3600
    )

    for pattern in patterns:

        if (
            pattern["pattern_time"]
            < cutoff
        ):
            continue

        recent_patterns.append(
            pattern
        )

    df5 = fetch_candles(
        contract,
        INTERVAL_5M,
        500
    )

    if df5.empty:

        return [], {
            "patterns": len(patterns),
            "recent": len(recent_patterns),
            "breakouts": 0,
            "old_breakouts": 0,
            "retests": 0,
            "confirmations": 0,
            "valid": 0,
            "stale": 0,
        }

    df5 = closed_candles(
        df5,
        5
    )

    entries = []

    stats = {
        "patterns": len(patterns),
        "recent": len(recent_patterns),
        "breakouts": 0,
        "old_breakouts": 0,
        "retests": 0,
        "confirmations": 0,
        "valid": 0,
        "stale": 0,
    }

    for pattern in recent_patterns:

        breakout = breakout_signal(
            df1h,
            pattern
        )

        if not breakout:
            continue

        stats["breakouts"] += 1

        breakout_age = (
            now_ts()
            - breakout["breakout_time"]
        ) / 3600

        if breakout_age > 48:

            stats[
                "old_breakouts"
            ] += 1

            continue

        retest = find_first_retest(
            df5,
            breakout,
            pattern["direction"]
        )

        if not retest:
            continue

        stats["retests"] += 1

        confirmation = find_confirmation(
            df5,
            retest,
            pattern["direction"]
        )

        if not confirmation:
            continue

        stats[
            "confirmations"
        ] += 1

        entry = find_entry(
            df5,
            confirmation,
            pattern["direction"]
        )

        if not entry:

            stats["stale"] += 1

            continue

        stats["valid"] += 1

        entries.append({
            "asset": asset,
            "symbol": contract,
            "contract": contract,
            "pattern":
                pattern["pattern"],
            "direction":
                pattern["direction"],
            "breakout_time":
                breakout["breakout_time"],
            "retest_time":
                retest["retest_time"],
            "confirm_time":
                confirmation["confirm_time"],
            "entry_time":
                entry["entry_time"],
            "entry_price":
                entry["entry_price"],
            "sl_price":
                entry["sl_price"],
            "tp_price":
                entry["tp_price"],
        })

    return entries, stats


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
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
            created_at INTEGER,
            updated_at INTEGER,
            notified_new INTEGER DEFAULT 0,
            notified_close INTEGER DEFAULT 0
        )
    """)

    conn.commit()

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    existing_columns = {
        row[1]
        for row in cur.fetchall()
    }

    migrations = {

        "contract": """
            ALTER TABLE trades
            ADD COLUMN contract TEXT
        """,

        "exit_reason": """
            ALTER TABLE trades
            ADD COLUMN exit_reason TEXT
        """,

        "created_at": """
            ALTER TABLE trades
            ADD COLUMN created_at INTEGER
        """,

        "updated_at": """
            ALTER TABLE trades
            ADD COLUMN updated_at INTEGER
        """,

        "notified_new": """
            ALTER TABLE trades
            ADD COLUMN notified_new INTEGER
            DEFAULT 0
        """,

        "notified_close": """
            ALTER TABLE trades
            ADD COLUMN notified_close INTEGER
            DEFAULT 0
        """,
    }

    for column, sql in migrations.items():

        if column not in existing_columns:

            print(
                f"Migrating old DB: "
                f"adding '{column}'..."
            )

            try:

                cur.execute(sql)
                conn.commit()

            except sqlite3.OperationalError as e:

                print(
                    f"[MIGRATION ERROR] "
                    f"{column}: {e}"
                )

    # Re-read schema
    cur.execute(
        "PRAGMA table_info(trades)"
    )

    final_columns = {
        row[1]
        for row in cur.fetchall()
    }

    # Backfill created_at
    if "created_at" in final_columns:

        cur.execute("""
            UPDATE trades
            SET created_at =
                COALESCE(
                    created_at,
                    detected_at,
                    CAST(
                        strftime(
                            '%s',
                            'now'
                        ) AS INTEGER
                    )
                )
            WHERE created_at IS NULL
        """)

    # Backfill updated_at
    if "updated_at" in final_columns:

        cur.execute("""
            UPDATE trades
            SET updated_at =
                COALESCE(
                    updated_at,
                    created_at,
                    detected_at,
                    CAST(
                        strftime(
                            '%s',
                            'now'
                        ) AS INTEGER
                    )
                )
            WHERE updated_at IS NULL
        """)

    # Backfill contract
    if "contract" in final_columns:

        cur.execute("""
            UPDATE trades
            SET contract = symbol
            WHERE contract IS NULL
        """)

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
        "created_at",
        "updated_at",
        "notified_new",
        "notified_close",
    }

    missing = (
        required_columns
        - final_columns
    )

    if missing:

        conn.close()

        raise RuntimeError(
            "Database schema is missing "
            f"required columns: "
            f"{sorted(missing)}"
        )

    conn.commit()
    conn.close()

    print(
        "Database schema verified."
    )


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(trade):

    conn = get_db()
    cur = conn.cursor()

    now_value = now_ts()

    created_at = trade.get(
        "created_at",
        now_value
    )

    if created_at is None:
        created_at = now_value

    updated_at = trade.get(
        "updated_at",
        created_at
    )

    if updated_at is None:
        updated_at = created_at

    signal_key = (
        f"{trade['asset']}|"
        f"{trade['pattern']}|"
        f"{trade['direction']}|"
        f"{trade['breakout_time']}|"
        f"{trade['entry_time']}"
    )

    cur.execute("""
        SELECT id
        FROM trades
        WHERE signal_key = ?
        LIMIT 1
    """, (
        signal_key,
    ))

    if cur.fetchone():

        print(
            f"[DUPLICATE DB] "
            f"{signal_key}"
        )

        conn.close()

        return False

    try:

        cur.execute("""
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
                created_at,
                updated_at,
                notified_new,
                notified_close
            )
            VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?, ?,
                NULL, NULL, NULL,
                'OPEN',
                ?, ?, ?,
                0, 0
            )
        """, (
            signal_key,
            trade["asset"],
            trade["symbol"],
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
            trade.get(
                "detected_at",
                now_value
            ),
            created_at,
            updated_at,
        ))

        conn.commit()

        inserted = (
            cur.rowcount == 1
        )

        if inserted:

            print(
                f"[INSERTED] "
                f"{signal_key}"
            )

        return inserted

    except sqlite3.IntegrityError as e:

        conn.rollback()

        print(
            "[INSERT INTEGRITY ERROR]"
        )

        print(
            f"Signal: {signal_key}"
        )

        print(
            f"Error: {e}"
        )

        return False

    except Exception as e:

        conn.rollback()

        print(
            "[INSERT ERROR]",
            e
        )

        return False

    finally:

        conn.close()


# ============================================================
# LIVE PRICES
# ============================================================

def get_live_prices():

    data = kraken_get(
        "/derivatives/api/v3/tickers"
    )

    prices = {}

    if not data:
        return prices

    tickers = data.get(
        "tickers",
        []
    )

    for ticker in tickers:

        symbol = str(
            ticker.get(
                "symbol",
                ""
            )
        )

        last = ticker.get(
            "last"
        )

        if last is None:
            continue

        try:

            prices[symbol] = float(
                last
            )

        except Exception:
            pass

    return prices


# ============================================================
# ROW -> DICT
# ============================================================

def row_to_trade(row):

    if isinstance(
        row,
        sqlite3.Row
    ):

        data = dict(row)

        data.setdefault(
            "contract",
            data.get("symbol")
        )

        data.setdefault(
            "created_at",
            data.get("detected_at")
        )

        data.setdefault(
            "updated_at",
            data.get("created_at")
        )

        data.setdefault(
            "notified_new",
            0
        )

        data.setdefault(
            "notified_close",
            0
        )

        return data

    try:
        return dict(row)

    except Exception:
        return {}


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return [
        row_to_trade(row)
        for row in rows
    ]


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_reason,
    exit_time=None
):

    if exit_time is None:
        exit_time = now_ts()

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            status = 'CLOSED',
            updated_at = ?
        WHERE
            id = ?
            AND status = 'OPEN'
    """, (
        exit_time,
        exit_price,
        exit_reason,
        exit_time,
        trade_id,
    ))

    conn.commit()

    changed = (
        cur.rowcount == 1
    )

    conn.close()

    return changed


# ============================================================
# NOTIFICATION FLAGS
# ============================================================

def mark_new_notified(
    trade_id
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET notified_new = 1
        WHERE id = ?
    """, (
        trade_id,
    ))

    conn.commit()
    conn.close()


def mark_close_notified(
    trade_id
):

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET notified_close = 1
        WHERE id = ?
    """, (
        trade_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# PRICE FORMAT
# ============================================================

def price_text(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# PERCENT PNL
# ============================================================

def calc_pct(
    direction,
    entry,
    current
):

    if not entry:
        return 0.0

    if direction == "LONG":

        return (
            (current - entry)
            / entry
        ) * 100

    return (
        (entry - current)
        / entry
    ) * 100


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def new_signal_message(
    trade,
    current_price=None
):

    direction = trade[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
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

    current = (
        current_price
        if current_price is not None
        else entry
    )

    pct = calc_pct(
        direction,
        entry,
        current
    )

    return (
        "🚨 <b>NEW SIGNAL</b>\n\n"
        f"{emoji} <b>"
        f"{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{price_text(entry)}\n"
        f"💵 Current: "
        f"{price_text(current)} "
        f"({pct:+.2f}%)\n"
        f"🛑 SL: "
        f"{price_text(sl)}\n"
        f"🎯 TP: "
        f"{price_text(tp)}\n"
        f"⚙️ RR: {RR:.2f}\n"
        f"🕐 Entry: "
        f"{fmt_tehran(trade['entry_time'])}"
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def close_message(
    trade,
    current_price
):

    direction = trade[
        "direction"
    ]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        current_price
    )

    pct = calc_pct(
        direction,
        entry,
        exit_price
    )

    return (
        "🔔 <b>TRADE CLOSED</b>\n\n"
        f"{emoji} <b>"
        f"{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{price_text(entry)}\n"
        f"🏁 Exit: "
        f"{price_text(exit_price)}\n"
        f"📊 P&L: "
        f"{pct:+.2f}%\n"
        f"📍 Reason: "
        f"{trade['exit_reason']}\n"
        f"🕐 Entry: "
        f"{fmt_tehran(trade['entry_time'])}\n"
        f"🕐 Exit: "
        f"{fmt_tehran(trade['exit_time'])}\n"
        f"⏱ Duration: "
        f"{duration_text("
        f"trade['entry_time'], "
        f"trade['exit_time']"
        f")}"
    )


# ============================================================
# PROCESS NEW SIGNALS
# ============================================================

def process_new_signals(
    entries,
    live_prices
):

    inserted_count = 0

    seen_keys = set()

    for entry in entries:

        signal_key = (
            f"{entry['asset']}|"
            f"{entry['pattern']}|"
            f"{entry['direction']}|"
            f"{entry['breakout_time']}|"
            f"{entry['entry_time']}"
        )

        if signal_key in seen_keys:

            print(
                "[DUPLICATE IN SAME SCAN] "
                f"{signal_key}"
            )

            continue

        seen_keys.add(
            signal_key
        )

        ts = now_ts()

        entry["detected_at"] = ts
        entry["created_at"] = ts
        entry["updated_at"] = ts

        inserted = insert_trade(
            entry
        )

        if not inserted:
            continue

        inserted_count += 1

        current = live_prices.get(
            entry["contract"],
            entry["entry_price"]
        )

        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            SELECT *
            FROM trades
            WHERE signal_key = ?
            LIMIT 1
        """, (
            signal_key,
        ))

        row = cur.fetchone()

        conn.close()

        if row:

            trade = row_to_trade(
                row
            )

            message = (
                new_signal_message(
                    trade,
                    current
                )
            )

            if send_telegram(
                message
            ):

                mark_new_notified(
                    trade["id"]
                )

    return inserted_count


# ============================================================
# LIVE EXIT CHECK
# ============================================================

def process_open_trades(
    live_prices
):

    open_trades = (
        get_open_trades()
    )

    closed_count = 0

    for trade in open_trades:

        contract = (
            trade.get("contract")
            or trade.get("symbol")
        )

        current = live_prices.get(
            contract
        )

        if current is None:
            continue

        direction = trade[
            "direction"
        ]

        entry_time = int(
            trade["entry_time"]
        )

        sl = float(
            trade["sl_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        exit_reason = None
        exit_price = None

        # ----------------------------------------------------
        # SAME CANDLE TP + SL => SL FIRST
        # ----------------------------------------------------

        if direction == "LONG":

            if current <= sl:

                exit_price = sl
                exit_reason = "SL"

            elif current >= tp:

                exit_price = tp
                exit_reason = "TP"

        else:

            if current >= sl:

                exit_price = sl
                exit_reason = "SL"

            elif current <= tp:

                exit_price = tp
                exit_reason = "TP"

        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

        if exit_reason is None:

            age_hours = (
                now_ts()
                - entry_time
            ) / 3600

            if (
                age_hours
                >= MAX_HOLD_HOURS
            ):

                exit_price = current
                exit_reason = "TIME"

        if exit_reason is None:
            continue

        exit_time = now_ts()

        closed = close_trade(
            trade["id"],
            exit_price,
            exit_reason,
            exit_time
        )

        if not closed:
            continue

        closed_count += 1

        trade["exit_time"] = (
            exit_time
        )

        trade["exit_price"] = (
            exit_price
        )

        trade["exit_reason"] = (
            exit_reason
        )

        trade["status"] = "CLOSED"

        message = close_message(
            trade,
            exit_price
        )

        if send_telegram(
            message
        ):

            mark_close_notified(
                trade["id"]
            )

    return closed_count


# ============================================================
# HISTORICAL CLOSE RECONCILIATION
# ============================================================

def reconcile_open_trades(
    contract_map
):

    open_trades = (
        get_open_trades()
    )

    if not open_trades:
        return 0

    closed_count = 0

    for trade in open_trades:

        contract = (
            trade.get("contract")
            or trade.get("symbol")
        )

        if not contract:
            continue

        df5 = fetch_candles(
            contract,
            INTERVAL_5M,
            700
        )

        if df5.empty:
            continue

        df5 = closed_candles(
            df5,
            5
        )

        if df5.empty:
            continue

        entry_time = int(
            trade["entry_time"]
        )

        future = df5[
            df5["timestamp"]
            > entry_time
        ]

        if future.empty:
            continue

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl_price"]
        )

        tp = float(
            trade["tp_price"]
        )

        exit_time = None
        exit_price = None
        exit_reason = None

        for _, candle in (
            future.iterrows()
        ):

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            ts = int(
                candle["timestamp"]
            )

            if direction == "LONG":

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp
                )

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp
                )

            # Same candle => SL first
            if hit_sl:

                exit_time = ts
                exit_price = sl
                exit_reason = "SL"
                break

            if hit_tp:

                exit_time = ts
                exit_price = tp
                exit_reason = "TP"
                break

            if (
                ts - entry_time
                >= MAX_HOLD_HOURS * 3600
            ):

                exit_time = ts

                exit_price = float(
                    candle["close"]
                )

                exit_reason = "TIME"

                break

        if exit_reason is None:
            continue

        closed = close_trade(
            trade["id"],
            exit_price,
            exit_reason,
            exit_time
        )

        if not closed:
            continue

        closed_count += 1

        trade["exit_time"] = (
            exit_time
        )

        trade["exit_price"] = (
            exit_price
        )

        trade["exit_reason"] = (
            exit_reason
        )

        trade["status"] = "CLOSED"

        if int(
            trade.get(
                "notified_close",
                0
            ) or 0
        ) == 0:

            message = close_message(
                trade,
                exit_price
            )

            if send_telegram(
                message
            ):

                mark_close_notified(
                    trade["id"]
                )

    return closed_count


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN status='CLOSED'
                    THEN 1
                    ELSE 0
                END
            ) AS closed
        FROM trades
    """)

    row = cur.fetchone()

    total = int(
        row["total"] or 0
    )

    closed = int(
        row["closed"] or 0
    )

    cur.execute("""
        SELECT
            direction,
            exit_reason,
            entry_price,
            exit_price
        FROM trades
        WHERE status='CLOSED'
    """)

    rows = cur.fetchall()

    conn.close()

    wins = 0
    losses = 0
    total_pct = 0.0

    for row in rows:

        entry = float(
            row["entry_price"]
        )

        exit_price = float(
            row["exit_price"]
        )

        direction = row[
            "direction"
        ]

        pct = calc_pct(
            direction,
            entry,
            exit_price
        )

        total_pct += pct

        if pct > 0:
            wins += 1

        elif pct < 0:
            losses += 1

    winrate = (
        wins / closed * 100
        if closed > 0
        else 0
    )

    return {
        "total": total,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "winrate": winrate,
        "total_pct": total_pct,
    }


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report(
    live_prices
):

    open_trades = (
        get_open_trades()
    )

    perf = performance()

    lines = []

    lines.append(
        "📊 <b>KRAKEN PATTERN "
        "SCANNER</b>"
    )

    lines.append("")

    lines.append(
        f"🟢 <b>OPEN TRADES: "
        f"{len(open_trades)}</b>"
    )

    if open_trades:

        for trade in open_trades:

            direction = (
                trade["direction"]
            )

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            contract = (
                trade.get("contract")
                or trade.get("symbol")
            )

            current = live_prices.get(
                contract,
                float(
                    trade["entry_price"]
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

            pct = calc_pct(
                direction,
                entry,
                current
            )

            lines.append("")

            lines.append(
                f"{emoji} <b>"
                f"{trade['asset']} "
                f"{direction}</b>"
            )

            lines.append(
                f"📌 Pattern: "
                f"{trade['pattern']}"
            )

            lines.append(
                f"💰 Entry: "
                f"{price_text(entry)}"
            )

            lines.append(
                f"💵 Current: "
                f"{price_text(current)} "
                f"({pct:+.2f}%)"
            )

            lines.append(
                f"🛑 SL: "
                f"{price_text(sl)} "
                f"({calc_pct(direction, entry, sl):+.2f}%)"
            )

            lines.append(
                f"🎯 TP: "
                f"{price_text(tp)} "
                f"({calc_pct(direction, entry, tp):+.2f}%)"
            )

            lines.append(
                f"⚙️ RR: {RR:.2f}"
            )

            lines.append(
                f"⏱ Duration: "
                f"{duration_text("
                f"trade['entry_time']"
                f")}"
            )

    lines.append("")

    lines.append(
        "📈 <b>PERFORMANCE</b>"
    )

    lines.append(
        f"Trades: {perf['total']}"
    )

    lines.append(
        f"Closed: {perf['closed']}"
    )

    lines.append(
        f"Wins: {perf['wins']}"
    )

    lines.append(
        f"Losses: {perf['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{perf['winrate']:.2f}%"
    )

    lines.append(
        f"Total P&L: "
        f"{perf['total_pct']:+.2f}%"
    )

    send_telegram(
        "\n".join(lines)
    )


# ============================================================
# SCAN
# ============================================================

def scan():

    scan_start = time.time()

    print("=" * 70)

    print(
        "KRAKEN FUTURES PATTERN "
        "LIVE SIGNAL SCANNER"
    )

    print(
        f"VERSION {VERSION}"
    )

    print("=" * 70)

    print(
        f"REAL_TRADING = "
        f"{REAL_TRADING}"
    )

    print(
        f"ASSETS = {len(ASSETS)}"
    )

    print(
        f"MAX_ENTRY_AGE_CANDLES = "
        f"{MAX_ENTRY_AGE_CANDLES}"
    )

    print(
        f"MAX ENTRY AGE = "
        f"{MAX_ENTRY_AGE_MINUTES} "
        f"MINUTES"
    )

    print(
        f"PERIODIC_REPORT_SECONDS = "
        f"{PERIODIC_REPORT_SECONDS}"
    )

    print(
        "ENTRY MODE = "
        "CONFIRMATION CANDLE CLOSE"
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    init_db()

    contract_map = (
        build_contract_map()
    )

    all_entries = []

    total_stats = {
        "patterns": 0,
        "recent": 0,
        "breakouts": 0,
        "old_breakouts": 0,
        "retests": 0,
        "confirmations": 0,
        "valid": 0,
        "stale": 0,
    }

    # --------------------------------------------------------
    # SCAN ASSETS
    # --------------------------------------------------------

    for asset in ASSETS:

        contract = contract_map.get(
            asset
        )

        if not contract:

            print(
                f"[SKIP] {asset}: "
                "contract not found"
            )

            continue

        try:

            entries, stats = (
                generate_live_entries(
                    asset,
                    contract
                )
            )

            for key in total_stats:

                total_stats[key] += (
                    stats.get(
                        key,
                        0
                    )
                )

            print(
                f"{asset:<6} "
                f"patterns="
                f"{stats.get('patterns', 0)} "
                f"recent="
                f"{stats.get('recent', 0)} "
                f"breakouts="
                f"{stats.get('breakouts', 0)} "
                f"old="
                f"{stats.get('old_breakouts', 0)} "
                f"retests="
                f"{stats.get('retests', 0)} "
                f"conf="
                f"{stats.get('confirmations', 0)} "
                f"valid="
                f"{stats.get('valid', 0)}"
            )

            all_entries.extend(
                entries
            )

        except Exception as e:

            print(
                f"[ASSET ERROR] "
                f"{asset}: {e}"
            )

            traceback.print_exc()

        time.sleep(
            REQUEST_SLEEP
        )

    # --------------------------------------------------------
    # UNIQUE ENTRIES
    # --------------------------------------------------------

    unique_entries = []

    seen = set()

    for entry in all_entries:

        key = (
            f"{entry['asset']}|"
            f"{entry['pattern']}|"
            f"{entry['direction']}|"
            f"{entry['breakout_time']}|"
            f"{entry['entry_time']}"
        )

        if key in seen:

            print(
                "[DUPLICATE IN SAME SCAN] "
                f"{key}"
            )

            continue

        seen.add(key)

        unique_entries.append(
            entry
        )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    print("")

    print(
        f"Patterns: "
        f"{total_stats['patterns']}"
    )

    print(
        f"Recent patterns: "
        f"{total_stats['recent']}"
    )

    print(
        f"Breakouts: "
        f"{total_stats['breakouts']}"
    )

    print(
        f"Old breakouts: "
        f"{total_stats['old_breakouts']}"
    )

    print(
        f"Retests: "
        f"{total_stats['retests']}"
    )

    print(
        f"Confirmations: "
        f"{total_stats['confirmations']}"
    )

    print(
        f"Valid entries: "
        f"{len(all_entries)}"
    )

    print(
        f"Unique entries this scan: "
        f"{len(unique_entries)}"
    )

    # --------------------------------------------------------
    # LIVE PRICES
    # --------------------------------------------------------

    live_prices = (
        get_live_prices()
    )

    # --------------------------------------------------------
    # PROCESS NEW SIGNALS
    # --------------------------------------------------------

    inserted_count = (
        process_new_signals(
            unique_entries,
            live_prices
        )
    )

    print(
        f"New signals inserted: "
        f"{inserted_count}"
    )

    # --------------------------------------------------------
    # CURRENT PRICE EXITS
    # --------------------------------------------------------

    closed_count = (
        process_open_trades(
            live_prices
        )
    )

    print(
        f"Trades closed by live price: "
        f"{closed_count}"
    )

    # --------------------------------------------------------
    # HISTORICAL RECONCILIATION
    # --------------------------------------------------------

    reconciled = (
        reconcile_open_trades(
            contract_map
        )
    )

    print(
        f"Historical reconciled closes: "
        f"{reconciled}"
    )

    # --------------------------------------------------------
    # PERIODIC REPORT
    # --------------------------------------------------------

    conn = get_db()
    cur = conn.cursor()

    cur.execute("""
        SELECT MAX(updated_at)
        FROM trades
    """)

    last_update = (
        cur.fetchone()[0]
    )

    conn.close()

    should_report = False

    if last_update is None:

        should_report = True

    elif (
        now_ts() - int(last_update)
        >= PERIODIC_REPORT_SECONDS
    ):

        should_report = True

    if should_report:

        periodic_report(
            live_prices
        )

    elapsed = (
        time.time()
        - scan_start
    )

    print("")

    print(
        f"Scan completed in "
        f"{elapsed:.1f}s"
    )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    try:

        scan()

    except Exception as e:

        print(
            "[FATAL ERROR]"
        )

        print(e)

        traceback.print_exc()

        raise


if __name__ == "__main__":

    main()
