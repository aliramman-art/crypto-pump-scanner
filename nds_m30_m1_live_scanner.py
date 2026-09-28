# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.3.4
# ============================================================
#
# PAPER ONLY
#
# FIXES v4.3.4
# ------------------------------------------------------------
# 1. Robust timestamp normalization:
#       seconds
#       milliseconds
#       microseconds
#
# 2. Correct closed-candle filtering
#
# 3. M30 Hook search starts from newest structure
#
# 4. Strict NDS M30 structure:
#
# SHORT:
#   H1 -> L1 -> H2 -> L2 -> H3
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# LONG:
#   L1 -> H1 -> L2 -> H2 -> L3
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
# 5. M1 123F:
#
# SHORT:
#   1 < 2 < 3 < F
#
# LONG:
#   1 > 2 > 3 > F
#
# All M1 points are HIGH pivots.
#
# 6. PAPER ONLY - NO REAL ORDERS
# ============================================================

import os
import time
import json
import math
import sqlite3
import traceback
from datetime import datetime, timezone
from pathlib import Path

import requests
import pandas as pd
import numpy as np


# ============================================================
# VERSION / CONFIG
# ============================================================

VERSION = "4.3.4"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v43.db"
CHART_DIR = Path("nds_charts")

KRAKEN_CHART_BASE = "https://futures.kraken.com/api/charts/v1"
KRAKEN_API_BASE = "https://futures.kraken.com/derivatives/api/v3"

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
    or ""
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
    or ""
)

TARGET_ASSETS = 100

M30_CANDLES = 320
M1_CANDLES = 1500

M30_INTERVAL = "30m"
M1_INTERVAL = "1m"

M30_SECONDS = 30 * 60
M1_SECONDS = 60

M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60
REPORT_SECONDS = 900

RUN_DURATION_SECONDS = 12 * 60

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

M30_MIN_HOOK_RANGE_PCT = 0.20

M1_MIN_SWING_PCT = 0.07
M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
MAX_F_AGE_SECONDS = 5 * 60

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_SECONDS = 60 * 60

SL_BUFFER_PCT = 0.15

CHARTS_ENABLED = True
CHART_CANDLES = 240
ACTIVE_HOOK_CHART_CANDLES = 120

REQUEST_TIMEOUT = 20

USER_AGENT = "NDS-M30-M1-Scanner/4.3.4"


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

DIAG = {
    "m30_requests": 0,
    "m30_data_ok": 0,
    "m30_empty": 0,
    "m30_short": 0,

    "pivot_highs": 0,
    "pivot_lows": 0,

    "short_hooks": 0,
    "long_hooks": 0,
    "hooks_saved": 0,

    "m1_requests": 0,
    "m1_data_ok": 0,
    "m1_empty": 0,
    "m1_short": 0,

    "m1_123f_short": 0,
    "m1_123f_long": 0,

    "signals": 0,
    "errors": 0,

    "assets_scanned": 0,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def utc_now_string():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_int(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return int(value)

        if isinstance(value, float) and math.isnan(value):
            return default

        return int(float(value))

    except Exception:
        return default


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.strip()

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except Exception:
        return default


# ============================================================
# CRITICAL TIMESTAMP FIX
# ============================================================

def normalize_timestamp(value):
    """
    Normalize timestamps returned by APIs.

    Supports:
        seconds       ~ 1.7e9
        milliseconds  ~ 1.7e12
        microseconds  ~ 1.7e15

    IMPORTANT:
    Microseconds must be checked BEFORE milliseconds.
    """

    ts = safe_int(value)

    if ts is None:
        return None

    # Microseconds
    if ts > 100_000_000_000_000:
        ts //= 1_000_000

    # Milliseconds
    elif ts > 100_000_000_000:
        ts //= 1_000

    return ts


def percent_change(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if a is None or b is None or a == 0:
        return 0.0

    return ((b - a) / a) * 100.0


def age_seconds(ts):
    ts = safe_int(ts)

    if ts is None:
        return 10**12

    return max(0, now_ts() - ts)


def format_price(value):
    value = safe_float(value)

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(text):
    if not telegram_enabled():
        print(text)
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        r = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if r.status_code != 200:
            print("Telegram error:", r.status_code, r.text[:500])
            return False

        return True

    except Exception as e:
        print("Telegram exception:", e)
        return False


# ============================================================
# SQLITE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table):
    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {row["name"] for row in rows}


def ensure_column(conn, table, column, definition):
    columns = table_columns(conn, table)

    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def init_db():

    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at INTEGER,
            finished_at INTEGER,
            configured INTEGER DEFAULT 1,
            assets_scanned INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            hook_time INTEGER NOT NULL,

            h1_time INTEGER,
            h2_time INTEGER,
            h3_time INTEGER,

            l1_time INTEGER,
            l2_time INTEGER,
            l3_time INTEGER,

            h1 REAL,
            h2 REAL,
            h3 REAL,

            l1 REAL,
            l2 REAL,
            l3 REAL,

            target REAL,

            active INTEGER DEFAULT 1,
            invalidated INTEGER DEFAULT 0,

            created_at INTEGER
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            event_key TEXT UNIQUE,

            symbol TEXT,
            side TEXT,

            hook_id INTEGER,
            hook_time INTEGER,
            f_time INTEGER,

            entry REAL,
            sl REAL,
            tp REAL,

            f_price REAL,
            hook_target REAL,

            status TEXT DEFAULT 'OPEN',

            created_at INTEGER,
            closed_at INTEGER,

            close_price REAL,
            pnl_pct REAL,
            exit_reason TEXT,

            chart_path TEXT
        )
    """)

    ensure_column(
        conn,
        "scanner_runs",
        "configured",
        "INTEGER DEFAULT 1",
    )

    ensure_column(
        conn,
        "signals",
        "chart_path",
        "TEXT",
    )

    ensure_column(
        conn,
        "signals",
        "hook_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "signals",
        "f_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "signals",
        "hook_target",
        "REAL",
    )

    ensure_column(
        conn,
        "signals",
        "created_at",
        "INTEGER",
    )

    conn.commit()
    conn.close()


# ============================================================
# ASSET DISCOVERY
# ============================================================

def discover_assets():

    try:

        url = f"{KRAKEN_API_BASE}/tickers"

        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        r.raise_for_status()

        data = r.json()

        tickers = data.get("tickers", [])

        assets = []

        for item in tickers:

            symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if not symbol:
                continue

            symbol = str(symbol).upper()

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            if symbol == "PF_SNXXUSD":
                continue

            volume = (
                safe_float(item.get("volume"))
                or safe_float(item.get("volume24h"))
                or safe_float(item.get("vol24h"))
                or 0
            )

            assets.append(
                {
                    "symbol": symbol,
                    "volume": volume,
                }
            )

        assets.sort(
            key=lambda x: x["volume"],
            reverse=True,
        )

        result = [
            x["symbol"]
            for x in assets[:TARGET_ASSETS]
        ]

        # Ensure LDO is included if available.
        if "PF_LDOUSD" in [x["symbol"] for x in assets]:
            if "PF_LDOUSD" not in result:

                if len(result) >= TARGET_ASSETS:
                    result[-1] = "PF_LDOUSD"
                else:
                    result.append("PF_LDOUSD")

        print(
            f"Discovered {len(result)} PF assets"
        )

        return result

    except Exception as e:

        print("Asset discovery error:", e)

        DIAG["errors"] += 1

        return []


# ============================================================
# KRAKEN CANDLE FETCH
# ============================================================

def parse_candle_row(row):

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

        return [
            normalize_timestamp(ts),
            safe_float(op),
            safe_float(hi),
            safe_float(lo),
            safe_float(cl),
            safe_float(vol, 0),
        ]

    if isinstance(row, (list, tuple)):

        if len(row) < 5:
            return None

        ts = normalize_timestamp(row[0])

        op = safe_float(row[1])
        hi = safe_float(row[2])
        lo = safe_float(row[3])
        cl = safe_float(row[4])

        vol = (
            safe_float(row[5], 0)
            if len(row) > 5
            else 0
        )

        return [
            ts,
            op,
            hi,
            lo,
            cl,
            vol,
        ]

    return None


def fetch_candles(symbol, interval, count):

    is_m30 = interval == M30_INTERVAL

    if is_m30:
        DIAG["m30_requests"] += 1
    else:
        DIAG["m1_requests"] += 1

    url = (
        f"{KRAKEN_CHART_BASE}/trade/"
        f"{symbol}/{interval}"
    )

    try:

        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        if r.status_code != 200:

            print(
                f"{symbol} {interval} HTTP "
                f"{r.status_code}"
            )

            if is_m30:
                DIAG["m30_empty"] += 1
            else:
                DIAG["m1_empty"] += 1

            return pd.DataFrame()

        payload = r.json()

        rows = (
            payload.get("candles")
            if isinstance(payload, dict)
            else payload
        )

        if rows is None:
            rows = []

        parsed = []

        for row in rows:

            item = parse_candle_row(row)

            if item is None:
                continue

            ts, op, hi, lo, cl, vol = item

            if ts is None:
                continue

            if any(
                x is None
                for x in [op, hi, lo, cl]
            ):
                continue

            parsed.append(
                {
                    "time": ts,
                    "open": op,
                    "high": hi,
                    "low": lo,
                    "close": cl,
                    "volume": vol,
                }
            )

        if not parsed:

            if is_m30:
                DIAG["m30_empty"] += 1
            else:
                DIAG["m1_empty"] += 1

            return pd.DataFrame()

        df = pd.DataFrame(parsed)

        df = df.drop_duplicates(
            subset=["time"],
            keep="last",
        )

        df = df.sort_values("time")

        df = df.tail(count).reset_index(drop=True)

        if is_m30:

            DIAG["m30_data_ok"] += 1

            if len(df) < 50:
                DIAG["m30_short"] += 1

        else:

            DIAG["m1_data_ok"] += 1

            if len(df) < 100:
                DIAG["m1_short"] += 1

        return df

    except Exception as e:

        print(
            f"Fetch error {symbol} {interval}: {e}"
        )

        DIAG["errors"] += 1

        if is_m30:
            DIAG["m30_empty"] += 1
        else:
            DIAG["m1_empty"] += 1

        return pd.DataFrame()


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(df, interval_seconds):

    if df is None or df.empty:
        return pd.DataFrame()

    current = now_ts()

    result = df[
        (df["time"] + interval_seconds) <= current
    ].copy()

    result = result.sort_values("time")
    result = result.reset_index(drop=True)

    return result


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):

    highs = []
    lows = []

    if df is None or len(df) < (
        PIVOT_LEFT + PIVOT_RIGHT + 1
    ):
        return highs, lows

    high_values = df["high"].to_numpy()
    low_values = df["low"].to_numpy()
    times = df["time"].to_numpy()

    start = PIVOT_LEFT
    end = len(df) - PIVOT_RIGHT

    for i in range(start, end):

        center_high = high_values[i]

        left_highs = high_values[
            i - PIVOT_LEFT:i
        ]

        right_highs = high_values[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        if (
            center_high > np.max(left_highs)
            and center_high >= np.max(right_highs)
        ):
            highs.append(
                {
                    "index": i,
                    "time": int(times[i]),
                    "price": float(center_high),
                }
            )

        center_low = low_values[i]

        left_lows = low_values[
            i - PIVOT_LEFT:i
        ]

        right_lows = low_values[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        if (
            center_low < np.min(left_lows)
            and center_low <= np.min(right_lows)
        ):
            lows.append(
                {
                    "index": i,
                    "time": int(times[i]),
                    "price": float(center_low),
                }
            )

    return highs, lows


# ============================================================
# M30 HOOK DETECTION
# ============================================================

def detect_m30_hook(df, symbol):

    if df is None or df.empty:
        return None

    highs, lows = detect_pivots(df)

    DIAG["pivot_highs"] += len(highs)
    DIAG["pivot_lows"] += len(lows)

    if len(highs) < 3 or len(lows) < 2:
        return None

    # --------------------------------------------------------
    # SHORT
    #
    # H1 -> L1 -> H2 -> L2 -> H3
    #
    # H2 > H1
    # L2 < L1
    # H3 > H2
    # --------------------------------------------------------

    short_matches = []

    for h1 in highs:

        later_lows_1 = [
            x for x in lows
            if x["time"] > h1["time"]
        ]

        for l1 in later_lows_1:

            later_h2 = [
                x for x in highs
                if x["time"] > l1["time"]
                and x["price"] > h1["price"]
            ]

            for h2 in later_h2:

                later_l2 = [
                    x for x in lows
                    if x["time"] > h2["time"]
                    and x["price"] < l1["price"]
                ]

                for l2 in later_l2:

                    later_h3 = [
                        x for x in highs
                        if x["time"] > l2["time"]
                        and x["price"] > h2["price"]
                    ]

                    for h3 in later_h3:

                        hook_range = (
                            (h3["price"] - l2["price"])
                            / h3["price"]
                        ) * 100

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        short_matches.append(
                            {
                                "symbol": symbol,
                                "side": "SHORT",

                                "hook_time": h3["time"],

                                "h1_time": h1["time"],
                                "h2_time": h2["time"],
                                "h3_time": h3["time"],

                                "l1_time": l1["time"],
                                "l2_time": l2["time"],

                                "h1": h1["price"],
                                "h2": h2["price"],
                                "h3": h3["price"],

                                "l1": l1["price"],
                                "l2": l2["price"],

                                "target": l2["price"],

                                "hook_range": hook_range,
                            }
                        )

    # --------------------------------------------------------
    # LONG
    #
    # L1 -> H1 -> L2 -> H2 -> L3
    #
    # L2 < L1
    # H2 > H1
    # L3 < L2
    # --------------------------------------------------------

    long_matches = []

    for l1 in lows:

        later_highs_1 = [
            x for x in highs
            if x["time"] > l1["time"]
        ]

        for h1 in later_highs_1:

            later_l2 = [
                x for x in lows
                if x["time"] > h1["time"]
                and x["price"] < l1["price"]
            ]

            for l2 in later_l2:

                later_h2 = [
                    x for x in highs
                    if x["time"] > l2["time"]
                    and x["price"] > h1["price"]
                ]

                for h2 in later_h2:

                    later_l3 = [
                        x for x in lows
                        if x["time"] > h2["time"]
                        and x["price"] < l2["price"]
                    ]

                    for l3 in later_l3:

                        hook_range = (
                            (h2["price"] - l3["price"])
                            / h2["price"]
                        ) * 100

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        long_matches.append(
                            {
                                "symbol": symbol,
                                "side": "LONG",

                                "hook_time": l3["time"],

                                "l1_time": l1["time"],
                                "l2_time": l2["time"],
                                "l3_time": l3["time"],

                                "h1_time": h1["time"],
                                "h2_time": h2["time"],

                                "l1": l1["price"],
                                "l2": l2["price"],
                                "l3": l3["price"],

                                "h1": h1["price"],
                                "h2": h2["price"],

                                "target": h2["price"],

                                "hook_range": hook_range,
                            }
                        )

    # --------------------------------------------------------
    # IMPORTANT:
    # Use the NEWEST valid structure.
    # --------------------------------------------------------

    if short_matches:
        DIAG["short_hooks"] += 1

    if long_matches:
        DIAG["long_hooks"] += 1

    candidates = []

    candidates.extend(short_matches)
    candidates.extend(long_matches)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["hook_time"],
        reverse=True,
    )

    return candidates[0]


# ============================================================
# HOOK VALIDITY
# ============================================================

def hook_is_expired(hook):

    return (
        now_ts() - safe_int(hook["hook_time"], 0)
        > MAX_HOOK_AGE_SECONDS
    )


def hook_is_invalidated(hook, df):

    if df is None or df.empty:
        return False

    highs, lows = detect_pivots(df)

    hook_time = safe_int(hook["hook_time"])

    if hook_time is None:
        return True

    if hook["side"] == "SHORT":

        # After SHORT hook, a new pivot low
        # invalidates the active hook.
        for low in lows:

            if low["time"] > hook_time:
                return True

    else:

        # After LONG hook, a new pivot high
        # invalidates the active hook.
        for high in highs:

            if high["time"] > hook_time:
                return True

    return False


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(hook):

    conn = db_connect()

    try:

        symbol = hook["symbol"]

        # Only one active hook per symbol.
        conn.execute("""
            UPDATE hooks
            SET active = 0
            WHERE symbol = ?
              AND active = 1
        """, (symbol,))

        conn.execute("""
            INSERT INTO hooks (
                symbol,
                side,
                hook_time,

                h1_time,
                h2_time,
                h3_time,

                l1_time,
                l2_time,
                l3_time,

                h1,
                h2,
                h3,

                l1,
                l2,
                l3,

                target,

                active,
                invalidated,
                created_at
            )
            VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?,
                1,
                0,
                ?
            )
        """, (
            hook["symbol"],
            hook["side"],
            hook["hook_time"],

            hook.get("h1_time"),
            hook.get("h2_time"),
            hook.get("h3_time"),

            hook.get("l1_time"),
            hook.get("l2_time"),
            hook.get("l3_time"),

            hook.get("h1"),
            hook.get("h2"),
            hook.get("h3"),

            hook.get("l1"),
            hook.get("l2"),
            hook.get("l3"),

            hook["target"],

            now_ts(),
        ))

        conn.commit()

        DIAG["hooks_saved"] += 1

        return True

    except Exception as e:

        conn.rollback()

        print("save_hook error:", e)

        DIAG["errors"] += 1

        return False

    finally:

        conn.close()


# ============================================================
# ACTIVE HOOKS
# ============================================================

def get_active_hooks():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM hooks
        WHERE active = 1
          AND invalidated = 0
        ORDER BY hook_time DESC
    """).fetchall()

    conn.close()

    return rows


def deactivate_hook(hook_id, invalidated=0):

    conn = db_connect()

    conn.execute("""
        UPDATE hooks
        SET active = 0,
            invalidated = ?
        WHERE id = ?
    """, (
        invalidated,
        hook_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# M1 123F DETECTION
# ============================================================

def detect_123f(df, hook):

    if df is None or df.empty:
        return None

    highs, lows = detect_pivots(df)

    # The user's NDS interpretation:
    # 1,2,3,F are ALL HIGH pivots.

    if len(highs) < 4:
        return None

    hook_time = safe_int(
        hook["hook_time"],
        0,
    )

    # Only structures after the Hook.
    candidates = [
        x for x in highs
        if x["time"] > hook_time
    ]

    if len(candidates) < 4:
        return None

    # Search newest F first.
    for i in range(len(candidates) - 1, -1, -1):

        f = candidates[i]

        if (
            now_ts() - f["time"]
            > MAX_F_AGE_SECONDS
        ):
            continue

        before_f = candidates[:i]

        if len(before_f) < 3:
            continue

        # Search the most recent 3-point sequence.
        for j in range(
            len(before_f) - 1,
            1,
            -1
        ):

            p3 = before_f[j]
            p2 = before_f[j - 1]
            p1 = before_f[j - 2]

            # ------------------------------------------------
            # SHORT
            #
            # 1 < 2 < 3 < F
            # ------------------------------------------------

            if hook["side"] == "SHORT":

                if not (
                    p1["price"]
                    < p2["price"]
                    < p3["price"]
                    < f["price"]
                ):
                    continue

                distance = (
                    abs(
                        f["price"]
                        - hook["h3"]
                    )
                    / hook["h3"]
                ) * 100

                if (
                    distance
                    > M1_MAX_STRUCTURE_DISTANCE_PCT
                ):
                    continue

                swing = (
                    (f["price"] - p1["price"])
                    / p1["price"]
                ) * 100

                if swing < M1_MIN_SWING_PCT:
                    continue

                DIAG["m1_123f_short"] += 1

                return {
                    "side": "SHORT",

                    "p1_time": p1["time"],
                    "p2_time": p2["time"],
                    "p3_time": p3["time"],
                    "f_time": f["time"],

                    "p1": p1["price"],
                    "p2": p2["price"],
                    "p3": p3["price"],
                    "f": f["price"],

                    "distance_pct": distance,
                    "swing_pct": swing,
                }

            # ------------------------------------------------
            # LONG
            #
            # 1 > 2 > 3 > F
            # ------------------------------------------------

            else:

                if not (
                    p1["price"]
                    > p2["price"]
                    > p3["price"]
                    > f["price"]
                ):
                    continue

                distance = (
                    abs(
                        f["price"]
                        - hook["l3"]
                    )
                    / hook["l3"]
                ) * 100

                if (
                    distance
                    > M1_MAX_STRUCTURE_DISTANCE_PCT
                ):
                    continue

                swing = (
                    (p1["price"] - f["price"])
                    / p1["price"]
                ) * 100

                if swing < M1_MIN_SWING_PCT:
                    continue

                DIAG["m1_123f_long"] += 1

                return {
                    "side": "LONG",

                    "p1_time": p1["time"],
                    "p2_time": p2["time"],
                    "p3_time": p3["time"],
                    "f_time": f["time"],

                    "p1": p1["price"],
                    "p2": p2["price"],
                    "p3": p3["price"],
                    "f": f["price"],

                    "distance_pct": distance,
                    "swing_pct": swing,
                }

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def fetch_current_price(symbol):

    try:

        url = f"{KRAKEN_API_BASE}/tickers"

        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": USER_AGENT},
        )

        r.raise_for_status()

        data = r.json()

        tickers = data.get("tickers", [])

        for item in tickers:

            item_symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if str(item_symbol).upper() != symbol:
                continue

            price = (
                safe_float(item.get("last"))
                or safe_float(item.get("lastPrice"))
                or safe_float(item.get("markPrice"))
                or safe_float(item.get("price"))
            )

            if price is not None:
                return price

    except Exception as e:

        print(
            f"Price error {symbol}: {e}"
        )

    return None


# ============================================================
# SIGNAL LEVELS
# ============================================================

def calculate_trade_levels(
    hook,
    f_structure,
):

    side = hook["side"]

    f_price = f_structure["f"]

    if side == "LONG":

        lows = [
            hook.get("l1"),
            hook.get("l2"),
            hook.get("l3"),
            f_price,
        ]

        lows = [
            x for x in lows
            if safe_float(x) is not None
        ]

        if not lows:
            return None

        lowest = min(lows)

        sl = lowest * (
            1 - SL_BUFFER_PCT / 100
        )

        tp = safe_float(
            hook["target"]
        )

        entry = f_price

        if tp is None:
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    # SHORT

    highs = [
        hook.get("h1"),
        hook.get("h2"),
        hook.get("h3"),
        f_price,
    ]

    highs = [
        x for x in highs
        if safe_float(x) is not None
    ]

    if not highs:
        return None

    highest = max(highs)

    sl = highest * (
        1 + SL_BUFFER_PCT / 100
    )

    tp = safe_float(
        hook["target"]
    )

    entry = f_price

    if tp is None:
        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


# ============================================================
# SIGNAL DATABASE HELPERS
# ============================================================

def signal_exists(event_key):

    conn = db_connect()

    row = conn.execute("""
        SELECT id
        FROM signals
        WHERE event_key = ?
        LIMIT 1
    """, (event_key,)).fetchone()

    conn.close()

    return row is not None


def recent_signal_for_symbol(symbol, side):

    conn = db_connect()

    row = conn.execute("""
        SELECT *
        FROM signals
        WHERE symbol = ?
          AND side = ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (
        symbol,
        side,
    )).fetchone()

    conn.close()

    if row is None:
        return None

    created = safe_int(
        row["created_at"],
        0,
    )

    if (
        now_ts() - created
        < SIGNAL_COOLDOWN_SECONDS
    ):
        return row

    return None


def count_open_trades():

    conn = db_connect()

    row = conn.execute("""
        SELECT COUNT(*) AS c
        FROM signals
        WHERE status = 'OPEN'
    """).fetchone()

    conn.close()

    return int(row["c"])


def insert_signal(
    event_key,
    hook,
    f_structure,
    levels,
    chart_path=None,
):

    conn = db_connect()

    try:

        conn.execute("""
            INSERT INTO signals (
                event_key,

                symbol,
                side,

                hook_id,
                hook_time,
                f_time,

                entry,
                sl,
                tp,

                f_price,
                hook_target,

                status,

                created_at,
                chart_path
            )
            VALUES (
                ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                'OPEN',
                ?, ?
            )
        """, (
            event_key,

            hook["symbol"],
            hook["side"],

            hook["id"],
            hook["hook_time"],
            f_structure["f_time"],

            levels["entry"],
            levels["sl"],
            levels["tp"],

            f_structure["f"],
            hook["target"],

            now_ts(),
            chart_path,
        ))

        conn.commit()

        return True

    except sqlite3.IntegrityError:
        conn.rollback()
        return False

    except Exception as e:

        conn.rollback()

        print(
            "insert_signal error:",
            e,
        )

        DIAG["errors"] += 1

        return False

    finally:
        conn.close()


# ============================================================
# PNL / TRADE MONITOR
# ============================================================

def calculate_pnl(side, entry, current):

    if side == "LONG":

        return (
            (current - entry)
            / entry
        ) * 100

    return (
        (entry - current)
        / entry
    ) * 100


def monitor_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY created_at ASC
    """).fetchall()

    for trade in rows:

        symbol = trade["symbol"]

        current = fetch_current_price(
            symbol
        )

        if current is None:
            continue

        side = trade["side"]

        entry = safe_float(
            trade["entry"]
        )

        sl = safe_float(
            trade["sl"]
        )

        tp = safe_float(
            trade["tp"]
        )

        if (
            entry is None
            or sl is None
            or tp is None
        ):
            continue

        pnl = calculate_pnl(
            side,
            entry,
            current,
        )

        exit_reason = None

        if side == "LONG":

            if current <= sl:
                exit_reason = "SL"

            elif current >= tp:
                exit_reason = "TP"

        else:

            if current >= sl:
                exit_reason = "SL"

            elif current <= tp:
                exit_reason = "TP"

        if exit_reason:

            conn.execute("""
                UPDATE signals
                SET status = 'CLOSED',
                    closed_at = ?,
                    close_price = ?,
                    pnl_pct = ?,
                    exit_reason = ?
                WHERE id = ?
            """, (
                now_ts(),
                current,
                pnl,
                exit_reason,
                trade["id"],
            ))

            message = (
                f"🚨 <b>NDS TRADE CLOSED</b>\n\n"
                f"<b>{symbol}</b>\n"
                f"{'🟢 LONG' if side == 'LONG' else '🔴 SHORT'}\n\n"
                f"Entry: <code>{format_price(entry)}</code>\n"
                f"Close: <code>{format_price(current)}</code>\n"
                f"PnL: <b>{pnl:+.2f}%</b>\n"
                f"Reason: <b>{exit_reason}</b>"
            )

            send_telegram(message)

    conn.commit()
    conn.close()


# ============================================================
# CHART
# ============================================================

def make_chart(
    symbol,
    hook,
    f_structure=None,
    entry=None,
    sl=None,
    tp=None,
    df=None,
    filename_prefix="signal",
):

    if not CHARTS_ENABLED:
        return None

    try:

        import matplotlib

        matplotlib.use("Agg")

        import matplotlib.pyplot as plt

        if df is None or df.empty:
            return None

        plot_df = df.tail(
            CHART_CANDLES
        ).copy()

        if plot_df.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(14, 7)
        )

        x = np.arange(
            len(plot_df)
        )

        width = 0.65

        for i, row in enumerate(
            plot_df.itertuples()
        ):

            o = row.open
            h = row.high
            l = row.low
            c = row.close

            ax.vlines(
                i,
                l,
                h,
                linewidth=1,
            )

            bottom = min(o, c)
            height = abs(c - o)

            if height == 0:
                height = max(
                    (h - l) * 0.01,
                    1e-12,
                )

            rect = plt.Rectangle(
                (
                    i - width / 2,
                    bottom,
                ),
                width,
                height,
                fill=False,
                linewidth=1,
            )

            ax.add_patch(rect)

        # ----------------------------------------------------
        # Hook points
        # ----------------------------------------------------

        point_specs = []

        if hook["side"] == "SHORT":

            point_specs = [
                (
                    hook.get("h1_time"),
                    hook.get("h1"),
                    "H1",
                ),
                (
                    hook.get("l1_time"),
                    hook.get("l1"),
                    "L1",
                ),
                (
                    hook.get("h2_time"),
                    hook.get("h2"),
                    "H2",
                ),
                (
                    hook.get("l2_time"),
                    hook.get("l2"),
                    "L2",
                ),
                (
                    hook.get("h3_time"),
                    hook.get("h3"),
                    "H3",
                ),
            ]

        else:

            point_specs = [
                (
                    hook.get("l1_time"),
                    hook.get("l1"),
                    "L1",
                ),
                (
                    hook.get("h1_time"),
                    hook.get("h1"),
                    "H1",
                ),
                (
                    hook.get("l2_time"),
                    hook.get("l2"),
                    "L2",
                ),
                (
                    hook.get("h2_time"),
                    hook.get("h2"),
                    "H2",
                ),
                (
                    hook.get("l3_time"),
                    hook.get("l3"),
                    "L3",
                ),
            ]

        time_to_x = {
            int(t): i
            for i, t in enumerate(
                plot_df["time"]
            )
        }

        for ts, price, label in point_specs:

            if ts is None or price is None:
                continue

            ts = safe_int(ts)

            if ts not in time_to_x:
                continue

            xi = time_to_x[ts]

            ax.scatter(
                xi,
                price,
                s=50,
                zorder=5,
            )

            ax.annotate(
                label,
                (
                    xi,
                    price,
                ),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=9,
            )

        # ----------------------------------------------------
        # M1 123F
        # ----------------------------------------------------

        if f_structure:

            for key in [
                "p1",
                "p2",
                "p3",
                "f",
            ]:

                ts_key = (
                    "f_time"
                    if key == "f"
                    else f"{key}_time"
                )

                ts = f_structure.get(
                    ts_key
                )

                price = f_structure.get(
                    key
                )

                if ts is None or price is None:
                    continue

                if ts not in time_to_x:
                    continue

                xi = time_to_x[ts]

                ax.scatter(
                    xi,
                    price,
                    s=65,
                    zorder=6,
                )

                ax.annotate(
                    key.upper(),
                    (
                        xi,
                        price,
                    ),
                    xytext=(0, -15),
                    textcoords="offset points",
                    ha="center",
                    fontsize=9,
                )

        # ----------------------------------------------------
        # Levels
        # ----------------------------------------------------

        if entry is not None:
            ax.axhline(
                entry,
                linestyle="--",
                linewidth=1,
                label="ENTRY",
            )

        if sl is not None:
            ax.axhline(
                sl,
                linestyle="--",
                linewidth=1,
                label="SL",
            )

        if tp is not None:
            ax.axhline(
                tp,
                linestyle="--",
                linewidth=1,
                label="TP",
            )

        ax.set_title(
            f"NDS {symbol} "
            f"{hook['side']} "
            f"v{VERSION}"
        )

        ax.set_xlabel(
            "Closed M30 candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            True,
            alpha=0.2,
        )

        ax.legend()

        CHART_DIR.mkdir(
            parents=True,
            exist_ok=True,
        )

        safe_symbol = (
            symbol
            .replace("/", "_")
            .replace(":", "_")
        )

        path = (
            CHART_DIR
            / f"{filename_prefix}_{safe_symbol}_"
              f"{hook['side']}_{hook['hook_time']}.png"
        )

        fig.tight_layout()

        fig.savefig(
            path,
            dpi=130,
        )

        plt.close(fig)

        return str(path)

    except Exception as e:

        print(
            "Chart error:",
            e,
        )

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def send_new_signal(
    hook,
    f_structure,
    levels,
):

    symbol = hook["symbol"]
    side = hook["side"]

    direction = (
        "🟢 LONG"
        if side == "LONG"
        else "🔴 SHORT"
    )

    message = (
        f"🚨 <b>NDS NEW SIGNAL</b>\n\n"
        f"<b>{symbol}</b>\n"
        f"{direction}\n\n"
        f"Entry: <code>"
        f"{format_price(levels['entry'])}"
        f"</code>\n"
        f"SL: <code>"
        f"{format_price(levels['sl'])}"
        f"</code>\n"
        f"TP: <code>"
        f"{format_price(levels['tp'])}"
        f"</code>\n\n"
        f"Hook: <b>{hook['hook_range']:.2f}%</b>\n"
        f"123F swing: "
        f"<b>{f_structure['swing_pct']:.2f}%</b>\n"
        f"F age: "
        f"<b>{age_seconds(f_structure['f_time']) // 60}m</b>\n\n"
        f"<i>PAPER ONLY</i>"
    )

    send_telegram(message)


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(assets):

    print(
        f"\n[{utc_now_string()}] "
        f"M30 scan: {len(assets)} assets"
    )

    DIAG["assets_scanned"] = len(assets)

    for symbol in assets:

        try:

            df = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES,
            )

            if df.empty:
                continue

            closed = get_closed_candles(
                df,
                M30_SECONDS,
            )

            if len(closed) < 30:
                DIAG["m30_short"] += 1
                continue

            hook = detect_m30_hook(
                closed,
                symbol,
            )

            if hook is None:
                continue

            if (
                now_ts()
                - hook["hook_time"]
                > MAX_HOOK_AGE_SECONDS
            ):
                continue

            # ------------------------------------------------
            # Check whether the exact hook is already active.
            # ------------------------------------------------

            conn = db_connect()

            existing = conn.execute("""
                SELECT *
                FROM hooks
                WHERE symbol = ?
                  AND side = ?
                  AND hook_time = ?
                LIMIT 1
            """, (
                symbol,
                hook["side"],
                hook["hook_time"],
            )).fetchone()

            conn.close()

            if existing is not None:
                continue

            save_hook(hook)

        except Exception as e:

            DIAG["errors"] += 1

            print(
                f"M30 scan error {symbol}:",
                e,
            )


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(assets=None):

    hooks = get_active_hooks()

    if not hooks:
        return

    print(
        f"\n[{utc_now_string()}] "
        f"M1 scan: {len(hooks)} active hooks"
    )

    for hook in hooks:

        symbol = hook["symbol"]

        try:

            # ------------------------------------------------
            # Hook age
            # ------------------------------------------------

            if hook_is_expired(hook):

                deactivate_hook(
                    hook["id"],
                    invalidated=0,
                )

                continue

            # ------------------------------------------------
            # Refresh M30 to verify Hook validity.
            # ------------------------------------------------

            m30 = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES,
            )

            if m30.empty:
                continue

            m30_closed = get_closed_candles(
                m30,
                M30_SECONDS,
            )

            if hook_is_invalidated(
                hook,
                m30_closed,
            ):

                deactivate_hook(
                    hook["id"],
                    invalidated=1,
                )

                continue

            # ------------------------------------------------
            # M1
            # ------------------------------------------------

            m1 = fetch_candles(
                symbol,
                M1_INTERVAL,
                M1_CANDLES,
            )

            if m1.empty:
                continue

            m1_closed = get_closed_candles(
                m1,
                M1_SECONDS,
            )

            if len(m1_closed) < 30:
                DIAG["m1_short"] += 1
                continue

            f_structure = detect_123f(
                m1_closed,
                hook,
            )

            if f_structure is None:
                continue

            # ------------------------------------------------
            # Maximum open trades
            # ------------------------------------------------

            if (
                count_open_trades()
                >= MAX_OPEN_TRADES
            ):
                continue

            # ------------------------------------------------
            # Event key
            # ------------------------------------------------

            event_key = (
                f"{symbol}|"
                f"{hook['side']}|"
                f"{hook['hook_time']}|"
                f"{f_structure['f_time']}"
            )

            if signal_exists(event_key):
                continue

            # ------------------------------------------------
            # Cooldown
            # ------------------------------------------------

            recent = recent_signal_for_symbol(
                symbol,
                hook["side"],
            )

            if recent is not None:
                continue

            # ------------------------------------------------
            # Levels
            # ------------------------------------------------

            levels = calculate_trade_levels(
                hook,
                f_structure,
            )

            if levels is None:
                continue

            # ------------------------------------------------
            # Validate directional TP
            # ------------------------------------------------

            entry = levels["entry"]
            sl = levels["sl"]
            tp = levels["tp"]

            if hook["side"] == "LONG":

                if not (
                    sl < entry < tp
                ):
                    continue

            else:

                if not (
                    tp < entry < sl
                ):
                    continue

            # ------------------------------------------------
            # Chart
            # ------------------------------------------------

            chart_path = make_chart(
                symbol=symbol,
                hook=hook,
                f_structure=f_structure,
                entry=entry,
                sl=sl,
                tp=tp,
                df=m30_closed,
                filename_prefix="signal",
            )

            # ------------------------------------------------
            # Insert
            # ------------------------------------------------

            inserted = insert_signal(
                event_key=event_key,
                hook=hook,
                f_structure=f_structure,
                levels=levels,
                chart_path=chart_path,
            )

            if not inserted:
                continue

            DIAG["signals"] += 1

            send_new_signal(
                hook,
                f_structure,
                levels,
            )

        except Exception as e:

            DIAG["errors"] += 1

            print(
                f"M1 scan error {symbol}:",
                e,
            )

            traceback.print_exc()


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY created_at ASC
    """).fetchall()

    conn.close()

    return rows


def get_closed_stats():

    conn = db_connect()

    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN pnl_pct > 0
                    THEN 1 ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN pnl_pct <= 0
                    THEN 1 ELSE 0
                END
            ) AS losses,
            COALESCE(
                SUM(pnl_pct),
                0
            ) AS pnl
        FROM signals
        WHERE status = 'CLOSED'
    """).fetchone()

    conn.close()

    return row


# ============================================================
# REPORT
# ============================================================

def diagnostic_text():

    return (
        "\n"
        f"🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_now_string()}\n\n"

        f"<b>ASSETS</b>\n"
        f"Scanned: <b>{DIAG['assets_scanned']}</b>\n\n"

        f"<b>30M DATA</b>\n"
        f"Requests: {DIAG['m30_requests']}\n"
        f"Data OK: {DIAG['m30_data_ok']}\n"
        f"Empty: {DIAG['m30_empty']}\n"
        f"Short data: {DIAG['m30_short']}\n"
        f"Pivot Highs: {DIAG['pivot_highs']}\n"
        f"Pivot Lows: {DIAG['pivot_lows']}\n\n"

        f"<b>30M HOOK DETECTION</b>\n"
        f"SHORT: {DIAG['short_hooks']}\n"
        f"LONG: {DIAG['long_hooks']}\n"
        f"Saved: {DIAG['hooks_saved']}\n\n"

        f"<b>M1 DATA</b>\n"
        f"Requests: {DIAG['m1_requests']}\n"
        f"Data OK: {DIAG['m1_data_ok']}\n"
        f"Empty: {DIAG['m1_empty']}\n"
        f"Short data: {DIAG['m1_short']}\n\n"

        f"<b>M1 123F</b>\n"
        f"SHORT: {DIAG['m1_123f_short']}\n"
        f"LONG: {DIAG['m1_123f_long']}\n\n"

        f"Signals: <b>{DIAG['signals']}</b>\n"
        f"Errors: {DIAG['errors']}\n"
    )


def send_report():

    open_trades = get_open_trades()

    stats = get_closed_stats()

    total = int(
        stats["total"] or 0
    )

    wins = int(
        stats["wins"] or 0
    )

    losses = int(
        stats["losses"] or 0
    )

    total_pnl = float(
        stats["pnl"] or 0
    )

    lines = []

    lines.append(
        f"📊 <b>NDS M30→M1 REPORT</b>"
    )

    lines.append(
        f"Version: <b>{VERSION}</b>"
    )

    lines.append("")

    lines.append(
        f"Assets scanned: "
        f"<b>{DIAG['assets_scanned']}</b>"
    )

    lines.append(
        f"New signals: "
        f"<b>{DIAG['signals']}</b>"
    )

    lines.append(
        f"Open trades: "
        f"<b>{len(open_trades)}</b>"
    )

    lines.append("")

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    if open_trades:

        lines.append(
            "🔓 <b>OPEN TRADES</b>"
        )

        for trade in open_trades:

            current = fetch_current_price(
                trade["symbol"]
            )

            entry = safe_float(
                trade["entry"]
            )

            if current is None:
                current_text = "-"
                pnl_text = "-"
            else:
                current_text = format_price(
                    current
                )

                pnl = calculate_pnl(
                    trade["side"],
                    entry,
                    current,
                )

                pnl_text = (
                    f"{pnl:+.2f}%"
                )

            direction = (
                "🟢 LONG"
                if trade["side"] == "LONG"
                else "🔴 SHORT"
            )

            duration = (
                now_ts()
                - safe_int(
                    trade["created_at"],
                    now_ts(),
                )
            )

            hours = duration // 3600
            minutes = (
                duration % 3600
            ) // 60

            lines.append(
                f"\n<b>{trade['symbol']}</b> "
                f"{direction}\n"
                f"Entry: "
                f"<code>{format_price(trade['entry'])}</code>\n"
                f"Current: "
                f"<b>{current_text}</b>\n"
                f"P/L: "
                f"<b>{pnl_text}</b>\n"
                f"TP: "
                f"<code>{format_price(trade['tp'])}</code>\n"
                f"SL: "
                f"<code>{format_price(trade['sl'])}</code>\n"
                f"Duration: "
                f"{hours}h {minutes}m"
            )

    else:

        lines.append(
            "🔓 <b>OPEN TRADES: 0</b>"
        )

    lines.append("")

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    lines.append(
        "📕 <b>CLOSED TRADES</b>"
    )

    lines.append(
        f"Closed: <b>{total}</b>"
    )

    lines.append(
        f"Wins: <b>{wins}</b>"
    )

    lines.append(
        f"Losses: <b>{losses}</b>"
    )

    if total:
        win_rate = (
            wins / total
        ) * 100

        lines.append(
            f"Win rate: "
            f"<b>{win_rate:.1f}%</b>"
        )

    lines.append(
        f"PnL: <b>{total_pnl:+.2f}%</b>"
    )

    lines.append("")

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    lines.append(
        diagnostic_text()
    )

    message = "\n".join(lines)

    send_telegram(message)

    # Reset per-report diagnostics.
    reset_diagnostics()


def reset_diagnostics():

    DIAG["m30_requests"] = 0
    DIAG["m30_data_ok"] = 0
    DIAG["m30_empty"] = 0
    DIAG["m30_short"] = 0

    DIAG["pivot_highs"] = 0
    DIAG["pivot_lows"] = 0

    DIAG["short_hooks"] = 0
    DIAG["long_hooks"] = 0
    DIAG["hooks_saved"] = 0

    DIAG["m1_requests"] = 0
    DIAG["m1_data_ok"] = 0
    DIAG["m1_empty"] = 0
    DIAG["m1_short"] = 0

    DIAG["m1_123f_short"] = 0
    DIAG["m1_123f_long"] = 0

    DIAG["signals"] = 0
    DIAG["errors"] = 0

    DIAG["assets_scanned"] = 0


# ============================================================
# STARTUP DIAGNOSTIC
# ============================================================

def send_startup():

    message = (
        f"🚀 <b>NDS M30→M1 SCANNER</b>\n\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Mode: <b>PAPER ONLY</b>\n\n"

        f"M30 Hook:\n"
        f"• H/L structural sequence\n"
        f"• 3 directional highs/lows\n"
        f"• Min range "
        f"{M30_MIN_HOOK_RANGE_PCT:.2f}%\n"
        f"• Max age 6h\n\n"

        f"M1 Entry:\n"
        f"• 123F\n"
        f"• F age ≤ 5m\n"
        f"• Min swing "
        f"{M1_MIN_SWING_PCT:.2f}%\n"
        f"• Max distance "
        f"{M1_MAX_STRUCTURE_DISTANCE_PCT:.1f}%\n\n"

        f"Max open trades: "
        f"<b>{MAX_OPEN_TRADES}</b>"
    )

    send_telegram(message)


# ============================================================
# INITIAL DATABASE RUN
# ============================================================

def record_run_start():

    conn = db_connect()

    cursor = conn.execute("""
        INSERT INTO scanner_runs (
            started_at,
            configured
        )
        VALUES (?, ?)
    """, (
        now_ts(),
        1,
    ))

    run_id = cursor.lastrowid

    conn.commit()
    conn.close()

    return run_id


def record_run_finish(
    run_id,
    assets_scanned,
    new_signals,
    errors,
):

    conn = db_connect()

    conn.execute("""
        UPDATE scanner_runs
        SET finished_at = ?,
            assets_scanned = ?,
            new_signals = ?,
            errors = ?
        WHERE id = ?
    """, (
        now_ts(),
        assets_scanned,
        new_signals,
        errors,
        run_id,
    ))

    conn.commit()
    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():

    if not PAPER_ONLY:
        raise RuntimeError(
            "REAL TRADING IS DISABLED. "
            "PAPER_ONLY must remain True."
        )

    print("=" * 70)
    print(
        f"NDS M30 -> M1 LIVE SCANNER "
        f"v{VERSION}"
    )
    print("PAPER ONLY")
    print("=" * 70)

    init_db()

    CHART_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_id = record_run_start()

    send_startup()

    assets = discover_assets()

    if not assets:

        print(
            "No assets discovered."
        )

        record_run_finish(
            run_id,
            0,
            0,
            DIAG["errors"],
        )

        return

    # ========================================================
    # INITIAL SCANS
    # ========================================================

    print(
        f"\nInitial M30 scan "
        f"({len(assets)} assets)"
    )

    scan_m30(assets)

    print(
        "\nInitial M1 scan"
    )

    scan_m1(assets)

    # Monitor any existing paper trades.
    monitor_open_trades()

    # Immediate report.
    send_report()

    # ========================================================
    # LOOP
    # ========================================================

    started = time.time()

    last_m30 = time.time()
    last_m1 = time.time()
    last_report = time.time()
    last_assets = time.time()

    while (
        time.time() - started
        < RUN_DURATION_SECONDS
    ):

        current = time.time()

        try:

            # ------------------------------------------------
            # Refresh assets every hour.
            # ------------------------------------------------

            if (
                current - last_assets
                >= 3600
            ):

                new_assets = (
                    discover_assets()
                )

                if new_assets:
                    assets = new_assets

                last_assets = current

            # ------------------------------------------------
            # M30
            # ------------------------------------------------

            if (
                current - last_m30
                >= M30_SCAN_SECONDS
            ):

                scan_m30(assets)

                last_m30 = current

            # ------------------------------------------------
            # M1
            # ------------------------------------------------

            if (
                current - last_m1
                >= M1_SCAN_SECONDS
            ):

                scan_m1(assets)

                last_m1 = current

            # ------------------------------------------------
            # Trade monitoring
            # ------------------------------------------------

            monitor_open_trades()

            # ------------------------------------------------
            # Report
            # ------------------------------------------------

            if (
                current - last_report
                >= REPORT_SECONDS
            ):

                send_report()

                last_report = current

        except Exception as e:

            DIAG["errors"] += 1

            print(
                "MAIN LOOP ERROR:",
                e,
            )

            traceback.print_exc()

        time.sleep(5)

    # ========================================================
    # FINAL REPORT
    # ========================================================

    try:

        monitor_open_trades()

        send_report()

    except Exception as e:

        print(
            "Final report error:",
            e,
        )

    record_run_finish(
        run_id,
        len(assets),
        DIAG["signals"],
        DIAG["errors"],
    )

    print(
        "\nScanner finished."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
