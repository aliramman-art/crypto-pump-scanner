# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 5.9.6
# ============================================================
#
# IMPORTANT:
# - PAPER ONLY
# - REAL_TRADING = False
# - Existing DB is preserved
# - 40 Kraken Futures assets
# - Closed candles only
# - No lookahead
#
# STRATEGY:
# 1H Pattern
#     ↓
# 1H Closed Candle Breakout
#     ↓
# First 5M Retest
#     ↓
# 5M Confirmation
#     ↓
# Entry = Confirmation Candle Close
#
# FRESHNESS:
# - Old confirmations are NOT entered
# - Confirmation freshness is checked BEFORE find_entry()
# - Only confirmations <= MAX_ENTRY_AGE_MINUTES are eligible
#
# PERIODIC REPORT FIX:
# - Periodic report is sent every 15 minutes
# - Report is sent even when there are ZERO open trades
# - Last report time is preserved in scanner_meta
# - If Telegram sending fails, last report time is NOT updated
#
# ============================================================

import os
import time
import json
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

VERSION = "5.9.6"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_BASE = "https://futures.kraken.com"

TICKER_ENDPOINT = "/derivatives/api/v3/tickers"

CHART_ENDPOINT = "/api/charts/v1/trade/{contract}/{resolution}"

REQUEST_TIMEOUT = 15

# ------------------------------------------------------------
# Universe
# ------------------------------------------------------------

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

# Exact Kraken Futures contract mapping
CONTRACT_MAP = {
    "XBT": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "LTC": "PF_LTCUSD",
    "DOGE": "PF_DOGEUSD",
    "ADA": "PF_ADAUSD",
    "LINK": "PF_LINKUSD",
    "AVAX": "PF_AVAXUSD",
    "DOT": "PF_DOTUSD",
    "BNB": "PF_BNBUSD",
    "TRX": "PF_TRXUSD",
    "UNI": "PF_UNIUSD",
    "AAVE": "PF_AAVEUSD",
    "SUI": "PF_SUIUSD",
    "NEAR": "PF_NEARUSD",
    "ATOM": "PF_ATOMUSD",
    "FIL": "PF_FILUSD",
    "ARB": "PF_ARBUSD",
    "OP": "PF_OPUSD",
    "BCH": "PF_BCHUSD",
    "ETC": "PF_ETCUSD",
    "XMR": "PF_XMRUSD",
    "XLM": "PF_XLMUSD",
    "ALGO": "PF_ALGOUSD",
    "ICP": "PF_ICPUSD",
    "INJ": "PF_INJUSD",
    "TIA": "PF_TIAUSD",
    "SEI": "PF_SEIUSD",
    "RUNE": "PF_RUNEUSD",
    "CRV": "PF_CRVUSD",
    "HBAR": "PF_HBARUSD",
    "HYPE": "PF_HYPEUSD",
    "ENA": "PF_ENAUSD",
    "FET": "PF_FETUSD",
    "KAS": "PF_KASUSD",
    "STX": "PF_STXUSD",
    "JUP": "PF_JUPUSD",
    "PEPE": "PF_PEPEUSD",
    "WIF": "PF_WIFUSD",
}

# ------------------------------------------------------------
# Strategy settings
# ------------------------------------------------------------

TP_PCT = 0.01
SL_PCT = 0.01

RR = 1.0

MAX_HOLD_HOURS = 48

PATTERN_LOOKBACK = 240
RECENT_PATTERN_HOURS = 48

BREAKOUT_LOOKAHEAD = 24
RETEST_LOOKAHEAD = 24
CONFIRMATION_LOOKAHEAD = 6

MAX_ENTRY_AGE_MINUTES = 10

PERIODIC_REPORT_SECONDS = 900

HTTP_RETRIES = 3


# ============================================================
# RUNTIME STATS
# ============================================================

STATS = {
    "patterns": 0,
    "recent_patterns": 0,
    "breakouts": 0,
    "old_breakouts": 0,
    "retests": 0,
    "confirmations": 0,
    "valid_entries": 0,
    "unique_entries": 0,
    "new_signals": 0,
    "closed_live": 0,
    "reconciled_closes": 0,
}


# ============================================================
# TIME HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def utc_now():
    return datetime.now(timezone.utc)


def fmt_utc(ts):
    if not ts:
        return "-"

    try:
        return datetime.fromtimestamp(
            int(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

    except Exception:
        return "-"


def duration_text(start_ts, end_ts=None):
    if not start_ts:
        return "-"

    if end_ts is None:
        end_ts = now_ts()

    try:
        seconds = max(
            0,
            int(end_ts) - int(start_ts)
        )

    except Exception:
        return "-"

    minutes = seconds // 60
    hours = minutes // 60
    minutes = minutes % 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 Kraken-Pattern-Scanner"
})


def http_get(url, params=None):
    last_error = None

    for attempt in range(HTTP_RETRIES):

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

            if attempt < HTTP_RETRIES - 1:
                time.sleep(
                    0.7 * (attempt + 1)
                )

    raise last_error


# ============================================================
# KRAKEN TICKERS
# ============================================================

def get_live_prices():

    url = (
        KRAKEN_BASE
        + TICKER_ENDPOINT
    )

    data = http_get(url)

    prices = {}

    rows = data.get(
        "tickers",
        []
    )

    for row in rows:

        symbol = (
            row.get("symbol")
            or row.get("pair")
        )

        if not symbol:
            continue

        last = row.get("last")

        try:
            last = float(last)

        except Exception:
            continue

        prices[symbol] = last

    return prices


# ============================================================
# CANDLE DOWNLOAD
# ============================================================

def parse_candle_payload(data):

    rows = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "chart",
            "result",
            "history",
        ):

            value = data.get(key)

            if isinstance(value, list):

                rows = value

                break

            if isinstance(value, dict):

                for subkey in (
                    "candles",
                    "data",
                    "history",
                ):

                    sub = value.get(
                        subkey
                    )

                    if isinstance(
                        sub,
                        list
                    ):

                        rows = sub

                        break

                if rows is not None:
                    break

    elif isinstance(data, list):

        rows = data

    if not rows:
        return pd.DataFrame()

    parsed = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("ts")
                )

                op = row.get("open")
                hi = row.get("high")
                lo = row.get("low")
                cl = row.get("close")
                vol = row.get(
                    "volume",
                    0
                )

            else:

                if len(row) < 5:
                    continue

                ts = row[0]
                op = row[1]
                hi = row[2]
                lo = row[3]
                cl = row[4]

                vol = (
                    row[5]
                    if len(row) > 5
                    else 0
                )

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000.0

            parsed.append({
                "timestamp": int(ts),
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(
                    vol or 0
                ),
            })

        except Exception:
            continue

    if not parsed:
        return pd.DataFrame()

    df = pd.DataFrame(
        parsed
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    return df


def get_candles(
    contract,
    resolution,
    count
):

    url = (
        KRAKEN_BASE
        + CHART_ENDPOINT.format(
            contract=contract,
            resolution=resolution,
        )
    )

    data = http_get(
        url,
        params={
            "count": count
        }
    )

    df = parse_candle_payload(
        data
    )

    if df.empty:
        return df

    current = now_ts()

    interval_seconds = (
        3600
        if resolution == "1h"
        else 300
    )

    df = df[
        df["timestamp"]
        + interval_seconds
        <= current
    ].copy()

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    return df


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


def table_columns(
    conn,
    table_name
):

    rows = conn.execute(
        f"PRAGMA table_info({table_name})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def add_column_if_missing(
    conn,
    table_name,
    column_name,
    column_type,
    default_sql=None
):

    columns = table_columns(
        conn,
        table_name
    )

    if column_name in columns:
        return

    sql = (
        f"ALTER TABLE {table_name} "
        f"ADD COLUMN {column_name} "
        f"{column_type}"
    )

    if default_sql is not None:
        sql += (
            f" DEFAULT {default_sql}"
        )

    conn.execute(sql)


def init_db():

    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT,
            contract TEXT,
            direction TEXT,
            pattern TEXT,
            entry_price REAL,
            sl REAL,
            tp REAL,
            current_price REAL,
            entry_time INTEGER,
            exit_price REAL,
            exit_time INTEGER,
            exit_reason TEXT,
            status TEXT,
            signal_key TEXT UNIQUE,
            created_at INTEGER,
            updated_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    required_columns = [

        ("asset", "TEXT", None),

        ("contract", "TEXT", None),

        ("direction", "TEXT", None),

        ("pattern", "TEXT", None),

        ("entry_price", "REAL", None),

        ("sl", "REAL", None),

        ("tp", "REAL", None),

        ("current_price", "REAL", None),

        ("entry_time", "INTEGER", None),

        ("exit_price", "REAL", None),

        ("exit_time", "INTEGER", None),

        ("exit_reason", "TEXT", None),

        ("status", "TEXT", "'OPEN'"),

        ("signal_key", "TEXT", None),

        ("created_at", "INTEGER", None),

        ("updated_at", "INTEGER", None),
    ]

    for name, typ, default in required_columns:

        try:

            add_column_if_missing(
                conn,
                "trades",
                name,
                typ,
                default
            )

        except Exception:
            pass

    current = now_ts()

    try:

        conn.execute("""
            UPDATE trades
            SET created_at =
                COALESCE(
                    created_at,
                    entry_time,
                    ?
                )
            WHERE created_at IS NULL
        """, (current,))

    except Exception:
        pass

    try:

        conn.execute("""
            UPDATE trades
            SET updated_at =
                COALESCE(
                    updated_at,
                    created_at,
                    entry_time,
                    ?
                )
            WHERE updated_at IS NULL
        """, (current,))

    except Exception:
        pass

    conn.commit()

    print(
        "Database schema verified."
    )

    return conn


# ============================================================
# DB HELPERS
# ============================================================

def signal_exists(
    conn,
    signal_key
):

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE signal_key = ?
        LIMIT 1
        """,
        (signal_key,)
    ).fetchone()

    return row is not None


def open_trade_exists(
    conn,
    asset,
    direction
):

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE asset = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (
            asset,
            direction,
        )
    ).fetchone()

    return row is not None


def insert_trade(
    conn,
    trade
):

    columns = table_columns(
        conn,
        "trades"
    )

    now = now_ts()

    values = {
        "asset": trade["asset"],
        "contract": trade["contract"],
        "direction": trade["direction"],
        "pattern": trade["pattern"],
        "entry_price": trade["entry_price"],
        "sl": trade["sl"],
        "tp": trade["tp"],
        "current_price": trade["entry_price"],
        "entry_time": trade["entry_time"],
        "exit_price": None,
        "exit_time": None,
        "exit_reason": None,
        "status": "OPEN",
        "signal_key": trade["signal_key"],
        "created_at": now,
        "updated_at": now,
    }

    usable = {
        key: value
        for key, value in values.items()
        if key in columns
    }

    names = list(
        usable.keys()
    )

    placeholders = ",".join(
        ["?"] * len(names)
    )

    sql = (
        f"INSERT OR IGNORE INTO trades "
        f"({','.join(names)}) "
        f"VALUES ({placeholders})"
    )

    cursor = conn.execute(
        sql,
        [
            usable[name]
            for name in names
        ]
    )

    conn.commit()

    return cursor.rowcount


# ============================================================
# PATTERN HELPERS
# ============================================================

def local_highs(
    df,
    distance=3
):

    result = []

    if len(df) < (
        distance * 2 + 1
    ):
        return result

    highs = df[
        "high"
    ].values

    for i in range(
        distance,
        len(df) - distance
    ):

        left = highs[
            i - distance:i
        ]

        right = highs[
            i + 1:
            i + distance + 1
        ]

        if (
            highs[i] >= left.max()
            and
            highs[i] >= right.max()
        ):

            result.append(i)

    return result


def local_lows(
    df,
    distance=3
):

    result = []

    if len(df) < (
        distance * 2 + 1
    ):
        return result

    lows = df[
        "low"
    ].values

    for i in range(
        distance,
        len(df) - distance
    ):

        left = lows[
            i - distance:i
        ]

        right = lows[
            i + 1:
            i + distance + 1
        ]

        if (
            lows[i] <= left.min()
            and
            lows[i] <= right.min()
        ):

            result.append(i)

    return result


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(df):

    highs = local_highs(
        df,
        distance=3
    )

    if len(highs) < 2:
        return []

    patterns = []

    for a, b in zip(
        highs[:-1],
        highs[1:]
    ):

        if b <= a:
            continue

        p1 = float(
            df.iloc[a]["high"]
        )

        p2 = float(
            df.iloc[b]["high"]
        )

        avg = (
            p1 + p2
        ) / 2

        if avg <= 0:
            continue

        diff = (
            abs(p1 - p2)
            / avg
        )

        if diff > 0.015:
            continue

        valley = df.iloc[
            a:b + 1
        ]

        if valley.empty:
            continue

        neckline = float(
            valley["low"].min()
        )

        if neckline >= min(
            p1,
            p2
        ):
            continue

        patterns.append({
            "pattern": "Double Top",
            "direction": "SHORT",
            "pattern_time": int(
                df.iloc[b]["timestamp"]
            ),
            "neckline": neckline,
        })

    return patterns


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(df):

    lows = local_lows(
        df,
        distance=3
    )

    if len(lows) < 2:
        return []

    patterns = []

    for a, b in zip(
        lows[:-1],
        lows[1:]
    ):

        if b <= a:
            continue

        p1 = float(
            df.iloc[a]["low"]
        )

        p2 = float(
            df.iloc[b]["low"]
        )

        avg = (
            p1 + p2
        ) / 2

        if avg <= 0:
            continue

        diff = (
            abs(p1 - p2)
            / avg
        )

        if diff > 0.015:
            continue

        valley = df.iloc[
            a:b + 1
        ]

        neckline = float(
            valley["high"].max()
        )

        if neckline <= max(
            p1,
            p2
        ):
            continue

        patterns.append({
            "pattern": "Double Bottom",
            "direction": "LONG",
            "pattern_time": int(
                df.iloc[b]["timestamp"]
            ),
            "neckline": neckline,
        })

    return patterns


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(df):

    highs = local_highs(
        df,
        distance=3
    )

    if len(highs) < 3:
        return []

    patterns = []

    for i in range(
        len(highs) - 2
    ):

        a, b, c = highs[
            i:i + 3
        ]

        if not (
            a < b < c
        ):
            continue

        left = float(
            df.iloc[a]["high"]
        )

        head = float(
            df.iloc[b]["high"]
        )

        right = float(
            df.iloc[c]["high"]
        )

        if (
            head <= left
            or
            head <= right
        ):
            continue

        shoulder_avg = (
            left + right
        ) / 2

        if shoulder_avg <= 0:
            continue

        shoulder_diff = (
            abs(left - right)
            / shoulder_avg
        )

        if shoulder_diff > 0.03:
            continue

        neckline_zone = df.iloc[
            a:c + 1
        ]

        neckline = float(
            neckline_zone["low"].min()
        )

        patterns.append({
            "pattern":
                "Head & Shoulders",
            "direction": "SHORT",
            "pattern_time": int(
                df.iloc[c]["timestamp"]
            ),
            "neckline": neckline,
        })

    return patterns


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(df):

    lows = local_lows(
        df,
        distance=3
    )

    if len(lows) < 3:
        return []

    patterns = []

    for i in range(
        len(lows) - 2
    ):

        a, b, c = lows[
            i:i + 3
        ]

        if not (
            a < b < c
        ):
            continue

        left = float(
            df.iloc[a]["low"]
        )

        head = float(
            df.iloc[b]["low"]
        )

        right = float(
            df.iloc[c]["low"]
        )

        if (
            head >= left
            or
            head >= right
        ):
            continue

        shoulder_avg = (
            left + right
        ) / 2

        if shoulder_avg <= 0:
            continue

        shoulder_diff = (
            abs(left - right)
            / shoulder_avg
        )

        if shoulder_diff > 0.03:
            continue

        neckline_zone = df.iloc[
            a:c + 1
        ]

        neckline = float(
            neckline_zone["high"].max()
        )

        patterns.append({
            "pattern":
                "Inverse Head & Shoulders",
            "direction": "LONG",
            "pattern_time": int(
                df.iloc[c]["timestamp"]
            ),
            "neckline": neckline,
        })

    return patterns


# ============================================================
# FLAG PATTERNS
# ============================================================

def detect_flags(df):

    patterns = []

    if len(df) < 30:
        return patterns

    for i in range(
        20,
        len(df)
    ):

        window = df.iloc[
            i - 20:i + 1
        ]

        first = float(
            window.iloc[0]["close"]
        )

        last = float(
            window.iloc[-1]["close"]
        )

        if first <= 0:
            continue

        move = (
            last - first
        ) / first

        if move > 0.04:

            consolidation = (
                window.iloc[-6:]
            )

            ch = (
                float(
                    consolidation[
                        "high"
                    ].max()
                )
                -
                float(
                    consolidation[
                        "low"
                    ].min()
                )
            ) / last

            if ch < 0.025:

                patterns.append({
                    "pattern": "Bull Flag",
                    "direction": "LONG",
                    "pattern_time": int(
                        window.iloc[-1][
                            "timestamp"
                        ]
                    ),
                    "neckline": float(
                        consolidation[
                            "high"
                        ].max()
                    ),
                })

        if move < -0.04:

            consolidation = (
                window.iloc[-6:]
            )

            ch = (
                float(
                    consolidation[
                        "high"
                    ].max()
                )
                -
                float(
                    consolidation[
                        "low"
                    ].min()
                )
            ) / last

            if ch < 0.025:

                patterns.append({
                    "pattern": "Bear Flag",
                    "direction": "SHORT",
                    "pattern_time": int(
                        window.iloc[-1][
                            "timestamp"
                        ]
                    ),
                    "neckline": float(
                        consolidation[
                            "low"
                        ].min()
                    ),
                })

    return patterns


# ============================================================
# ALL PATTERNS
# ============================================================

def detect_patterns(df):

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

    patterns.sort(
        key=lambda x:
        x["pattern_time"]
    )

    return patterns


# ============================================================
# RECENT PATTERNS
# ============================================================

def filter_recent_patterns(
    patterns
):

    cutoff = (
        now_ts()
        -
        RECENT_PATTERN_HOURS * 3600
    )

    return [
        p
        for p in patterns
        if p["pattern_time"] >= cutoff
    ]


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(
    df1h,
    pattern
):

    pattern_time = (
        pattern["pattern_time"]
    )

    neckline = float(
        pattern["neckline"]
    )

    direction = (
        pattern["direction"]
    )

    candidates = df1h[
        df1h["timestamp"]
        > pattern_time
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

            if close > neckline:

                return {
                    "breakout_time": int(
                        row["timestamp"]
                    ),
                    "breakout_price": close,
                }

        else:

            if close < neckline:

                return {
                    "breakout_time": int(
                        row["timestamp"]
                    ),
                    "breakout_price": close,
                }

    return None


# ============================================================
# FIRST RETEST
# ============================================================

def find_first_retest(
    df5,
    breakout,
    direction,
    neckline
):

    breakout_time = int(
        breakout["breakout_time"]
    )

    candidates = df5[
        df5["timestamp"]
        > breakout_time
    ].copy()

    if candidates.empty:
        return None

    candidates = candidates.head(
        RETEST_LOOKAHEAD
    )

    for _, row in candidates.iterrows():

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

            touched = (
                low <= neckline
            )

            held = (
                close >= neckline
            )

            if touched and held:

                return {
                    "retest_time": int(
                        row["timestamp"]
                    ),
                    "retest_price": close,
                }

        else:

            touched = (
                high >= neckline
            )

            held = (
                close <= neckline
            )

            if touched and held:

                return {
                    "retest_time": int(
                        row["timestamp"]
                    ),
                    "retest_price": close,
                }

    return None


# ============================================================
# FRESH CONFIRMATION
# ============================================================

def find_confirmation(
    df5,
    retest,
    direction
):

    retest_time = int(
        retest["retest_time"]
    )

    current_time = now_ts()

    freshness_seconds = (
        MAX_ENTRY_AGE_MINUTES * 60
    )

    candidates = df5[
        (df5["timestamp"] > retest_time)
        &
        (df5["timestamp"] <= current_time)
        &
        (
            (
                current_time
                -
                df5["timestamp"]
            )
            <= freshness_seconds
        )
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

def find_entry(
    df5,
    confirmation,
    direction
):

    confirm_time = int(
        confirmation["confirm_time"]
    )

    rows = df5[
        df5["timestamp"]
        == confirm_time
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

    if entry_price <= 0:
        return None

    if direction == "LONG":

        tp = (
            entry_price
            * (1.0 + TP_PCT)
        )

        sl = (
            entry_price
            * (1.0 - SL_PCT)
        )

    else:

        tp = (
            entry_price
            * (1.0 - TP_PCT)
        )

        sl = (
            entry_price
            * (1.0 + SL_PCT)
        )

    return {
        "entry_time": entry_time,
        "entry_price": entry_price,
        "tp": tp,
        "sl": sl,
    }


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_signal(
    asset,
    contract,
    df1h,
    df5,
    pattern
):

    breakout = find_breakout(
        df1h,
        pattern
    )

    if breakout is None:

        return None, {
            "breakout": False,
            "retest": False,
            "confirmation": False,
            "entry": False,
        }

    STATS["breakouts"] += 1

    retest = find_first_retest(
        df5,
        breakout,
        pattern["direction"],
        pattern["neckline"]
    )

    if retest is None:

        return None, {
            "breakout": True,
            "retest": False,
            "confirmation": False,
            "entry": False,
        }

    STATS["retests"] += 1

    confirmation = find_confirmation(
        df5,
        retest,
        pattern["direction"]
    )

    if confirmation is None:

        return None, {
            "breakout": True,
            "retest": True,
            "confirmation": False,
            "entry": False,
        }

    STATS["confirmations"] += 1

    entry = find_entry(
        df5,
        confirmation,
        pattern["direction"]
    )

    if entry is None:

        return None, {
            "breakout": True,
            "retest": True,
            "confirmation": True,
            "entry": False,
        }

    STATS["valid_entries"] += 1

    signal_key = (
        f"{asset}|"
        f"{pattern['pattern']}|"
        f"{pattern['direction']}|"
        f"{breakout['breakout_time']}|"
        f"{entry['entry_time']}"
    )

    signal = {
        "asset": asset,
        "contract": contract,
        "direction": pattern["direction"],
        "pattern": pattern["pattern"],
        "pattern_time": pattern["pattern_time"],
        "breakout_time": breakout["breakout_time"],
        "retest_time": retest["retest_time"],
        "confirm_time": confirmation["confirm_time"],
        "entry_time": entry["entry_time"],
        "entry_price": entry["entry_price"],
        "tp": entry["tp"],
        "sl": entry["sl"],
        "signal_key": signal_key,
    }

    return signal, {
        "breakout": True,
        "retest": True,
        "confirmation": True,
        "entry": True,
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "Telegram token is missing."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "Telegram chat ID is missing."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    try:

        response = SESSION.post(
            url,
            data={
                "chat_id":
                    TELEGRAM_CHAT_ID,

                "text":
                    message,

                "parse_mode":
                    "HTML",

                "disable_web_page_preview":
                    True,
            },
            timeout=15,
        )

        if not response.ok:

            print(
                "Telegram HTTP error: "
                f"{response.status_code} "
                f"{response.text[:500]}"
            )

            return False

        return True

    except Exception as exc:

        print(
            f"Telegram error: {exc}"
        )

        return False


# ============================================================
# MESSAGE HELPERS
# ============================================================

def price_text(value):

    if value is None:
        return "-"

    try:

        value = float(value)

        if value >= 1000:
            return f"{value:.2f}"

        if value >= 1:
            return f"{value:.5f}"

        if value >= 0.01:
            return f"{value:.6f}"

        return f"{value:.8f}"

    except Exception:
        return str(value)


def current_pct(
    direction,
    entry,
    current
):

    try:

        entry = float(entry)
        current = float(current)

        if entry == 0:
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

    except Exception:
        return 0.0


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def new_signal_message(
    trade
):

    if trade["direction"] == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    return (
        "🚨 <b>NEW SIGNAL</b>\n\n"
        f"{emoji} <b>{trade['asset']} "
        f"{trade['direction']}</b>\n"
        f"📌 Pattern: {trade['pattern']}\n"
        f"💰 Entry: "
        f"{price_text(trade['entry_price'])}\n"
        f"🛑 SL: "
        f"{price_text(trade['sl'])}\n"
        f"🎯 TP: "
        f"{price_text(trade['tp'])}\n"
        f"⚙️ RR: {RR:.1f}\n"
        f"🕐 Entry: "
        f"{fmt_utc(trade['entry_time'])}"
    )


# ============================================================
# CLOSE MESSAGE
# ============================================================

def close_message(
    trade
):

    direction = trade[
        "direction"
    ]

    if direction == "LONG":
        emoji = "🟢"
    else:
        emoji = "🔴"

    pct = current_pct(
        direction,
        trade["entry_price"],
        trade["exit_price"]
    )

    trade_duration = duration_text(
        trade["entry_time"],
        trade["exit_time"]
    )

    return (
        "📕 <b>TRADE CLOSED</b>\n\n"
        f"{emoji} <b>{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{price_text(trade['entry_price'])}\n"
        f"🏁 Exit: "
        f"{price_text(trade['exit_price'])}\n"
        f"📊 Result: "
        f"{pct:+.2f}%\n"
        f"📍 Reason: "
        f"{trade['exit_reason']}\n"
        f"⏱ Duration: "
        f"{trade_duration}\n"
        f"🕐 Exit: "
        f"{fmt_utc(trade['exit_time'])}"
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    conn,
    trade_id,
    exit_price,
    exit_reason
):

    exit_time = now_ts()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (trade_id,)
    ).fetchone()

    if row is None:
        return None

    conn.execute(
        """
        UPDATE trades
        SET
            exit_price = ?,
            exit_time = ?,
            exit_reason = ?,
            status = 'CLOSED',
            current_price = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            exit_price,
            exit_time,
            exit_reason,
            exit_price,
            exit_time,
            trade_id,
        )
    )

    conn.commit()

    return conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id = ?
        """,
        (trade_id,)
    ).fetchone()


# ============================================================
# LIVE PRICE CLOSE
# ============================================================

def reconcile_open_trades(
    conn,
    prices
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
        """
    ).fetchall()

    for trade in rows:

        contract = trade[
            "contract"
        ]

        current = prices.get(
            contract
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
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        exit_reason = None

        if direction == "LONG":

            if current <= sl:
                exit_reason = "SL"

            elif current >= tp:
                exit_reason = "TP"

        else:

            if current >= sl:
                exit_reason = "SL"

            elif current <= tp:
                exit_reason = "TP"

        if exit_reason is None:

            entry_time = int(
                trade["entry_time"]
            )

            if (
                now_ts()
                -
                entry_time
                >= MAX_HOLD_HOURS * 3600
            ):

                exit_reason = "TIME"

        conn.execute(
            """
            UPDATE trades
            SET
                current_price = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                current,
                now_ts(),
                trade["id"],
            )
        )

        conn.commit()

        if exit_reason is None:
            continue

        closed = close_trade(
            conn,
            trade["id"],
            current,
            exit_reason
        )

        if closed is None:
            continue

        STATS[
            "closed_live"
        ] += 1

        telegram_send(
            close_message(
                closed
            )
        )


# ============================================================
# HISTORICAL RECONCILIATION
# ============================================================

def historical_reconcile(
    conn
):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        """
    ).fetchall()

    for trade in rows:

        contract = trade[
            "contract"
        ]

        try:

            df5 = get_candles(
                contract,
                "5m",
                700
            )

        except Exception:

            continue

        if df5.empty:
            continue

        entry_time = int(
            trade["entry_time"]
        )

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        candles = df5[
            df5["timestamp"]
            >= entry_time
        ].copy()

        if candles.empty:
            continue

        found = None

        for _, row in candles.iterrows():

            high = float(
                row["high"]
            )

            low = float(
                row["low"]
            )

            if direction == "LONG":

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp
                )

                if hit_sl:

                    found = (
                        sl,
                        "SL",
                        int(
                            row["timestamp"]
                        )
                    )

                    break

                if hit_tp:

                    found = (
                        tp,
                        "TP",
                        int(
                            row["timestamp"]
                        )
                    )

                    break

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp
                )

                if hit_sl:

                    found = (
                        sl,
                        "SL",
                        int(
                            row["timestamp"]
                        )
                    )

                    break

                if hit_tp:

                    found = (
                        tp,
                        "TP",
                        int(
                            row["timestamp"]
                        )
                    )

                    break

        if found is None:

            if (
                now_ts()
                -
                entry_time
                >= MAX_HOLD_HOURS * 3600
            ):

                found = (
                    float(
                        candles.iloc[-1][
                            "close"
                        ]
                    ),
                    "TIME",
                    int(
                        candles.iloc[-1][
                            "timestamp"
                        ]
                    )
                )

        if found is None:
            continue

        exit_price, reason, exit_time = (
            found
        )

        closed = close_trade(
            conn,
            trade["id"],
            exit_price,
            reason
        )

        if closed is None:
            continue

        conn.execute(
            """
            UPDATE trades
            SET
                exit_time = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                exit_time,
                now_ts(),
                trade["id"],
            )
        )

        conn.commit()

        STATS[
            "reconciled_closes"
        ] += 1

        telegram_send(
            close_message(
                conn.execute(
                    """
                    SELECT *
                    FROM trades
                    WHERE id = ?
                    """,
                    (trade["id"],)
                ).fetchone()
            )
        )


# ============================================================
# OPEN TRADE REPORT
# ============================================================

def periodic_report(
    conn,
    prices
):
    """
    IMPORTANT:
    This function ALWAYS builds a periodic report,
    including when there are ZERO open trades.
    """

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
        """
    ).fetchall()

    lines = [
        "📊 <b>KRAKEN PATTERN SCANNER</b>",
        "",
        f"<b>🟢 OPEN TRADES: "
        f"{len(rows)}</b>",
        "",
    ]

    # --------------------------------------------------------
    # ZERO OPEN TRADES
    # --------------------------------------------------------

    if not rows:

        lines.append(
            "No open trades."
        )

        message = "\n".join(
            lines
        )

        sent = telegram_send(
            message
        )

        if sent:
            print(
                "Periodic report sent "
                "(0 open trades)."
            )

        else:
            print(
                "Periodic report failed "
                "(0 open trades)."
            )

        return sent

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    for trade in rows:

        contract = trade[
            "contract"
        ]

        current = prices.get(
            contract
        )

        if current is None:
            current = trade[
                "current_price"
            ]

        try:

            current = float(
                current
            )

        except Exception:

            current = float(
                trade["entry_price"]
            )

        pct = current_pct(
            trade["direction"],
            trade["entry_price"],
            current
        )

        if trade["direction"] == "LONG":
            emoji = "🟢"
        else:
            emoji = "🔴"

        trade_duration = duration_text(
            trade["entry_time"]
        )

        lines.extend([
            f"{emoji} <b>{trade['asset']} "
            f"{trade['direction']}</b>",

            f"📌 Pattern: "
            f"{trade['pattern']}",

            f"💰 Entry: "
            f"{price_text(trade['entry_price'])}",

            f"💵 Current: "
            f"{price_text(current)} "
            f"({pct:+.2f}%)",

            f"🛑 SL: "
            f"{price_text(trade['sl'])}",

            f"🎯 TP: "
            f"{price_text(trade['tp'])}",

            f"⚙️ RR: {RR:.1f}",

            f"⏱ Duration: "
            f"{trade_duration}",

            "",
        ])

        conn.execute(
            """
            UPDATE trades
            SET
                current_price = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                current,
                now_ts(),
                trade["id"],
            )
        )

    conn.commit()

    message = "\n".join(
        lines
    )

    sent = telegram_send(
        message
    )

    if sent:

        print(
            f"Periodic report sent "
            f"({len(rows)} open trades)."
        )

    else:

        print(
            "Periodic report failed."
        )

    return sent


# ============================================================
# PERIODIC REPORT STATE
# ============================================================

def get_meta(
    conn,
    key
):

    row = conn.execute(
        """
        SELECT value
        FROM scanner_meta
        WHERE key = ?
        """,
        (key,)
    ).fetchone()

    if row is None:
        return None

    return row["value"]


def set_meta(
    conn,
    key,
    value
):

    conn.execute(
        """
        INSERT INTO scanner_meta(
            key,
            value
        )
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET
            value = excluded.value
        """,
        (
            key,
            str(value),
        )
    )

    conn.commit()


# ============================================================
# ASSET SCAN
# ============================================================

def scan_asset(
    conn,
    asset,
    contract
):

    result = {
        "patterns": 0,
        "recent": 0,
        "breakouts": 0,
        "old": 0,
        "retests": 0,
        "confirmations": 0,
        "valid": 0,
    }

    df1h = get_candles(
        contract,
        "1h",
        PATTERN_LOOKBACK
    )

    if df1h.empty:
        return result

    df5 = get_candles(
        contract,
        "5m",
        500
    )

    if df5.empty:
        return result

    patterns = detect_patterns(
        df1h
    )

    result[
        "patterns"
    ] = len(patterns)

    recent_patterns = (
        filter_recent_patterns(
            patterns
        )
    )

    result[
        "recent"
    ] = len(
        recent_patterns
    )

    recent_patterns = sorted(
        recent_patterns,
        key=lambda x:
            x["pattern_time"],
        reverse=True
    )

    for pattern in recent_patterns:

        signal, chain = build_signal(
            asset,
            contract,
            df1h,
            df5,
            pattern
        )

        if chain["breakout"]:
            result[
                "breakouts"
            ] += 1

        if chain["retest"]:
            result[
                "retests"
            ] += 1

        if chain["confirmation"]:
            result[
                "confirmations"
            ] += 1

        if chain["entry"]:
            result[
                "valid"
            ] += 1

        if signal is None:
            continue

        if signal_exists(
            conn,
            signal["signal_key"]
        ):
            continue

        STATS[
            "unique_entries"
        ] += 1

        if open_trade_exists(
            conn,
            signal["asset"],
            signal["direction"]
        ):
            continue

        inserted = insert_trade(
            conn,
            signal
        )

        if inserted <= 0:
            continue

        STATS[
            "new_signals"
        ] += 1

        telegram_send(
            new_signal_message(
                signal
            )
        )

        break

    return result


# ============================================================
# RESET STATS
# ============================================================

def reset_stats():

    for key in STATS:
        STATS[key] = 0


# ============================================================
# PRINT CONFIG
# ============================================================

def print_config():

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
        f"ASSETS = "
        f"{len(ASSETS)}"
    )

    print(
        f"MAX_ENTRY_AGE_MINUTES = "
        f"{MAX_ENTRY_AGE_MINUTES}"
    )

    print(
        f"PERIODIC_REPORT_SECONDS = "
        f"{PERIODIC_REPORT_SECONDS}"
    )

    print(
        "ENTRY MODE = "
        "CONFIRMATION CANDLE CLOSE"
    )

    print(
        "FRESHNESS = "
        "CHECKED BEFORE ENTRY"
    )

    print(
        "PERIODIC REPORT = "
        "EVERY 15 MINUTES INCLUDING ZERO OPEN TRADES"
    )

    print(
        "TP = 1.00%"
    )

    print(
        "SL = 1.00%"
    )

    print(
        "RR = 1.0"
    )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    reset_stats()

    print_config()

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    conn = init_db()

    # --------------------------------------------------------
    # Kraken contracts
    # --------------------------------------------------------

    try:

        ticker_data = http_get(
            KRAKEN_BASE
            + TICKER_ENDPOINT
        )

        available_contracts = set()

        for row in ticker_data.get(
            "tickers",
            []
        ):

            symbol = (
                row.get("symbol")
                or row.get("pair")
            )

            if symbol:
                available_contracts.add(
                    symbol
                )

        print(
            f"Kraken contracts loaded: "
            f"{len(available_contracts)}"
        )

        available_assets = [
            asset
            for asset in ASSETS
            if CONTRACT_MAP.get(
                asset
            ) in available_contracts
        ]

        print(
            f"Available assets: "
            f"{len(available_assets)}/"
            f"{len(ASSETS)}"
        )

    except Exception as exc:

        print(
            f"Ticker loading failed: "
            f"{exc}"
        )

        available_contracts = set(
            CONTRACT_MAP.values()
        )

        available_assets = (
            ASSETS.copy()
        )

    # --------------------------------------------------------
    # Prices
    # --------------------------------------------------------

    try:

        prices = get_live_prices()

    except Exception as exc:

        print(
            f"Live price error: "
            f"{exc}"
        )

        prices = {}

    # --------------------------------------------------------
    # Live reconciliation
    # --------------------------------------------------------

    try:

        reconcile_open_trades(
            conn,
            prices
        )

    except Exception as exc:

        print(
            f"Live reconciliation error: "
            f"{exc}"
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # Historical reconciliation
    # --------------------------------------------------------

    try:

        historical_reconcile(
            conn
        )

    except Exception as exc:

        print(
            f"Historical reconciliation error: "
            f"{exc}"
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # Scan assets
    # --------------------------------------------------------

    total_patterns = 0
    total_recent = 0
    total_breakouts = 0
    total_retests = 0
    total_confirmations = 0
    total_valid = 0

    for asset in available_assets:

        contract = CONTRACT_MAP.get(
            asset
        )

        if not contract:
            continue

        try:

            result = scan_asset(
                conn,
                asset,
                contract
            )

            total_patterns += (
                result["patterns"]
            )

            total_recent += (
                result["recent"]
            )

            total_breakouts += (
                result["breakouts"]
            )

            total_retests += (
                result["retests"]
            )

            total_confirmations += (
                result["confirmations"]
            )

            total_valid += (
                result["valid"]
            )

            print(
                f"{asset} "
                f"patterns="
                f"{result['patterns']} "
                f"recent="
                f"{result['recent']} "
                f"breakouts="
                f"{result['breakouts']} "
                f"old=0 "
                f"retests="
                f"{result['retests']} "
                f"conf="
                f"{result['confirmations']} "
                f"valid="
                f"{result['valid']}"
            )

        except Exception as exc:

            print(
                f"{asset} ERROR: "
                f"{exc}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Update stats
    # --------------------------------------------------------

    STATS[
        "patterns"
    ] = total_patterns

    STATS[
        "recent_patterns"
    ] = total_recent

    STATS[
        "breakouts"
    ] = total_breakouts

    STATS[
        "retests"
    ] = total_retests

    STATS[
        "confirmations"
    ] = total_confirmations

    STATS[
        "valid_entries"
    ] = total_valid

    # --------------------------------------------------------
    # PERIODIC REPORT
    # --------------------------------------------------------

    try:

        last_report = get_meta(
            conn,
            "last_periodic_report"
        )

        current = now_ts()

        if last_report is None:

            should_report = True

        else:

            try:

                last_report_ts = int(
                    last_report
                )

                should_report = (
                    current
                    -
                    last_report_ts
                    >=
                    PERIODIC_REPORT_SECONDS
                )

            except Exception:

                should_report = True

        if should_report:

            print(
                "Periodic report is due."
            )

            try:

                report_prices = (
                    get_live_prices()
                )

            except Exception as exc:

                print(
                    f"Report price refresh "
                    f"failed: {exc}"
                )

                report_prices = prices

            sent = periodic_report(
                conn,
                report_prices
            )

            # ------------------------------------------------
            # IMPORTANT:
            # Only save the timestamp when Telegram
            # successfully accepted the message.
            # ------------------------------------------------

            if sent:

                set_meta(
                    conn,
                    "last_periodic_report",
                    now_ts()
                )

                print(
                    "Periodic report timestamp "
                    "saved."
                )

            else:

                print(
                    "Periodic report was NOT "
                    "marked as sent."
                )

        else:

            remaining = (
                PERIODIC_REPORT_SECONDS
                -
                (
                    current
                    -
                    int(last_report)
                )
            )

            remaining = max(
                0,
                remaining
            )

            print(
                "Periodic report not due. "
                f"Approximately "
                f"{remaining // 60}m "
                f"{remaining % 60}s remaining."
            )

    except Exception as exc:

        print(
            f"Periodic report error: "
            f"{exc}"
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # Final statistics
    # --------------------------------------------------------

    elapsed = (
        time.time()
        -
        started
    )

    print()

    print("=" * 70)

    print(
        "SCAN SUMMARY"
    )

    print("=" * 70)

    print(
        f"Patterns: "
        f"{total_patterns}"
    )

    print(
        f"Recent patterns: "
        f"{total_recent}"
    )

    print(
        f"Breakouts: "
        f"{total_breakouts}"
    )

    print(
        "Old breakouts: 0"
    )

    print(
        f"Retests: "
        f"{total_retests}"
    )

    print(
        f"Confirmations: "
        f"{total_confirmations}"
    )

    print(
        f"Valid entries: "
        f"{total_valid}"
    )

    print(
        f"Unique entries this scan: "
        f"{STATS['unique_entries']}"
    )

    print(
        f"New signals inserted: "
        f"{STATS['new_signals']}"
    )

    print(
        f"Trades closed by live price: "
        f"{STATS['closed_live']}"
    )

    print(
        f"Historical reconciled closes: "
        f"{STATS['reconciled_closes']}"
    )

    print(
        f"Scan completed in "
        f"{elapsed:.1f}s"
    )

    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "Scanner stopped."
        )

    except Exception as exc:

        print(
            f"FATAL ERROR: {exc}"
        )

        traceback.print_exc()

        raise
