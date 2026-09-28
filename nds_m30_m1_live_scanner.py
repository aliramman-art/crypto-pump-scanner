# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.3.5
# ============================================================
#
# PAPER ONLY
#
# M30:
#   SHORT HOOK:
#       H1 -> L1 -> H2 -> L2 -> H3
#       H2 > H1
#       L2 < L1
#       H3 > H2
#
#   LONG HOOK:
#       L1 -> H1 -> L2 -> H2 -> L3
#       L2 < L1
#       H2 > H1
#       L3 < L2
#
# M1:
#   123F uses HIGH pivots only.
#
#   SHORT:
#       1 < 2 < 3 < F
#
#   LONG:
#       1 > 2 > 3 > F
#
# PAPER ONLY
# ============================================================

import os
import time
import sqlite3
import traceback
from pathlib import Path
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.3.5"

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

REQUEST_TIMEOUT = 20

USER_AGENT = f"NDS-M30-M1-Scanner/{VERSION}"


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAGNOSTICS = {
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


def safe_int(value):
    try:
        if value is None:
            return None
        return int(float(value))
    except Exception:
        return None


def safe_float(value):
    try:
        if value is None:
            return None
        x = float(value)
        if not np.isfinite(x):
            return None
        return x
    except Exception:
        return None


def normalize_timestamp(value):
    """
    Kraken timestamps can appear in:
      seconds
      milliseconds
      microseconds

    Important:
      microseconds must be checked BEFORE milliseconds.
    """
    ts = safe_int(value)

    if ts is None:
        return None

    if ts > 100_000_000_000_000:
        ts //= 1_000_000

    elif ts > 100_000_000_000:
        ts //= 1_000

    return ts


def percent_change(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if a is None or b is None or a == 0:
        return None

    return ((b - a) / a) * 100.0


def age_seconds(ts):
    if ts is None:
        return None

    return max(0, now_ts() - int(ts))


def format_price(value):
    value = safe_float(value)

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def format_duration(seconds):
    seconds = int(max(0, seconds))

    d = seconds // 86400
    seconds %= 86400

    h = seconds // 3600
    seconds %= 3600

    m = seconds // 60

    if d:
        return f"{d}d {h}h"

    if h:
        return f"{h}h {m}m"

    return f"{m}m"


def row_to_dict(row):
    """
    Fix for sqlite3.Row:
    sqlite3.Row does not have .get()
    """
    if row is None:
        return None

    if isinstance(row, dict):
        return row

    if isinstance(row, sqlite3.Row):
        return {key: row[key] for key in row.keys()}

    try:
        return dict(row)
    except Exception:
        return row


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(text):
    if not telegram_enabled():
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        }

        r = requests.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        return r.ok

    except Exception as e:
        DIAGNOSTICS["errors"] += 1
        print("Telegram error:", e)
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn, table):
    rows = conn.execute(
        f"PRAGMA table_info({table})"
    ).fetchall()

    return {
        row["name"]
        for row in rows
    }


def ensure_column(conn, table, column, definition):
    cols = table_columns(conn, table)

    if column not in cols:
        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


def init_db():
    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at INTEGER,
            finished_at INTEGER,
            configured INTEGER DEFAULT 0,
            assets_scanned INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            hook_time INTEGER,
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
            status TEXT,
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
        "INTEGER DEFAULT 0",
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

    ensure_column(
        conn,
        "hooks",
        "l3_time",
        "INTEGER",
    )

    ensure_column(
        conn,
        "hooks",
        "l3",
        "REAL",
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

        candidates = []

        for item in tickers:

            if not isinstance(item, dict):
                continue

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
                item.get("volume")
                or item.get("volume24h")
                or item.get("vol24h")
                or 0
            )

            volume = safe_float(volume) or 0

            candidates.append(
                (symbol, volume)
            )

        candidates.sort(
            key=lambda x: x[1],
            reverse=True,
        )

        selected = [
            symbol
            for symbol, _ in candidates[:TARGET_ASSETS]
        ]

        # Ensure LDO if available.
        if "PF_LDOUSD" in {
            symbol for symbol, _ in candidates
        } and "PF_LDOUSD" not in selected:

            if len(selected) >= TARGET_ASSETS:
                selected[-1] = "PF_LDOUSD"
            else:
                selected.append("PF_LDOUSD")

        selected = list(dict.fromkeys(selected))

        print(
            f"Discovered {len(selected)} Kraken PF assets."
        )

        return selected

    except Exception as e:
        DIAGNOSTICS["errors"] += 1

        print(
            "Asset discovery error:",
            e,
        )

        return []


# ============================================================
# KRAKEN CANDLE PARSER
# ============================================================

def parse_candle_row(row):

    if isinstance(row, dict):

        timestamp = (
            row.get("time")
            if row.get("time") is not None
            else row.get("timestamp")
        )

        if timestamp is None:
            timestamp = row.get("t")

        o = (
            row.get("open")
            if row.get("open") is not None
            else row.get("o")
        )

        h = (
            row.get("high")
            if row.get("high") is not None
            else row.get("h")
        )

        l = (
            row.get("low")
            if row.get("low") is not None
            else row.get("l")
        )

        c = (
            row.get("close")
            if row.get("close") is not None
            else row.get("c")
        )

        v = (
            row.get("volume")
            if row.get("volume") is not None
            else row.get("v")
        )

    elif isinstance(row, (list, tuple)):

        if len(row) < 5:
            return None

        timestamp = row[0]
        o = row[1]
        h = row[2]
        l = row[3]
        c = row[4]

        v = row[5] if len(row) > 5 else None

    else:
        return None

    timestamp = normalize_timestamp(timestamp)

    o = safe_float(o)
    h = safe_float(h)
    l = safe_float(l)
    c = safe_float(c)
    v = safe_float(v)

    if None in (
        timestamp,
        o,
        h,
        l,
        c,
    ):
        return None

    return {
        "time": timestamp,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": v or 0.0,
    }


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(
    symbol,
    interval,
    limit,
):
    is_m30 = interval == M30_INTERVAL

    if is_m30:
        DIAGNOSTICS["m30_requests"] += 1
    else:
        DIAGNOSTICS["m1_requests"] += 1

    try:

        url = (
            f"{KRAKEN_CHART_BASE}"
            f"/trade/{symbol}/{interval}"
        )

        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT
            },
        )

        r.raise_for_status()

        data = r.json()

        raw = data.get("candles")

        if raw is None:
            raw = data.get("data")

        if raw is None:
            raw = []

        parsed = []

        for row in raw:

            candle = parse_candle_row(row)

            if candle is not None:
                parsed.append(candle)

        if not parsed:

            if is_m30:
                DIAGNOSTICS["m30_empty"] += 1
            else:
                DIAGNOSTICS["m1_empty"] += 1

            return pd.DataFrame(
                columns=[
                    "time",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            )

        df = pd.DataFrame(parsed)

        df = (
            df.drop_duplicates(
                subset=["time"],
                keep="last",
            )
            .sort_values("time")
            .reset_index(drop=True)
        )

        df = df.tail(limit).reset_index(drop=True)

        minimum = 50 if is_m30 else 100

        if len(df) < minimum:

            if is_m30:
                DIAGNOSTICS["m30_short"] += 1
            else:
                DIAGNOSTICS["m1_short"] += 1

        else:

            if is_m30:
                DIAGNOSTICS["m30_data_ok"] += 1
            else:
                DIAGNOSTICS["m1_data_ok"] += 1

        return df

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            f"Candle fetch error "
            f"{symbol} {interval}: {e}"
        )

        return pd.DataFrame(
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    df,
    interval_seconds,
):
    if df is None or df.empty:
        return df

    current = now_ts()

    result = df[
        (df["time"] + interval_seconds) <= current
    ].copy()

    return (
        result
        .sort_values("time")
        .reset_index(drop=True)
    )


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(
    df,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT,
):
    highs = []
    lows = []

    if df is None or len(df) < left + right + 1:
        return highs, lows

    highs_array = df["high"].values
    lows_array = df["low"].values

    for i in range(
        left,
        len(df) - right,
    ):

        high = highs_array[i]
        low = lows_array[i]

        left_highs = highs_array[
            i - left:i
        ]

        right_highs = highs_array[
            i + 1:i + 1 + right
        ]

        left_lows = lows_array[
            i - left:i
        ]

        right_lows = lows_array[
            i + 1:i + 1 + right
        ]

        if (
            high > np.max(left_highs)
            and high >= np.max(right_highs)
        ):
            highs.append({
                "time": int(df.iloc[i]["time"]),
                "price": float(high),
                "index": i,
            })

        if (
            low < np.min(left_lows)
            and low <= np.min(right_lows)
        ):
            lows.append({
                "time": int(df.iloc[i]["time"]),
                "price": float(low),
                "index": i,
            })

    return highs, lows


# ============================================================
# M30 HOOK DETECTION
# ============================================================

def detect_m30_hook(df):

    highs, lows = detect_pivots(df)

    DIAGNOSTICS["pivot_highs"] += len(highs)
    DIAGNOSTICS["pivot_lows"] += len(lows)

    if len(highs) < 3 or len(lows) < 2:
        return None

    candidates = []

    # --------------------------------------------------------
    # SHORT
    #
    # H1 -> L1 -> H2 -> L2 -> H3
    #
    # H2 > H1
    # L2 < L1
    # H3 > H2
    # --------------------------------------------------------

    for h1 in highs:

        for l1 in lows:

            if l1["time"] <= h1["time"]:
                continue

            for h2 in highs:

                if h2["time"] <= l1["time"]:
                    continue

                if h2["price"] <= h1["price"]:
                    continue

                for l2 in lows:

                    if l2["time"] <= h2["time"]:
                        continue

                    if l2["price"] >= l1["price"]:
                        continue

                    for h3 in highs:

                        if h3["time"] <= l2["time"]:
                            continue

                        if h3["price"] <= h2["price"]:
                            continue

                        hook_range = (
                            (h3["price"] - l2["price"])
                            / h3["price"]
                        ) * 100.0

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        candidates.append({
                            "side": "SHORT",

                            "hook_time": h3["time"],

                            "h1_time": h1["time"],
                            "h2_time": h2["time"],
                            "h3_time": h3["time"],

                            "l1_time": l1["time"],
                            "l2_time": l2["time"],
                            "l3_time": None,

                            "h1": h1["price"],
                            "h2": h2["price"],
                            "h3": h3["price"],

                            "l1": l1["price"],
                            "l2": l2["price"],
                            "l3": None,

                            "target": l2["price"],
                            "hook_range": hook_range,
                        })

    # --------------------------------------------------------
    # LONG
    #
    # L1 -> H1 -> L2 -> H2 -> L3
    #
    # L2 < L1
    # H2 > H1
    # L3 < L2
    # --------------------------------------------------------

    for l1 in lows:

        for h1 in highs:

            if h1["time"] <= l1["time"]:
                continue

            for l2 in lows:

                if l2["time"] <= h1["time"]:
                    continue

                if l2["price"] >= l1["price"]:
                    continue

                for h2 in highs:

                    if h2["time"] <= l2["time"]:
                        continue

                    if h2["price"] <= h1["price"]:
                        continue

                    for l3 in lows:

                        if l3["time"] <= h2["time"]:
                            continue

                        if l3["price"] >= l2["price"]:
                            continue

                        hook_range = (
                            (h2["price"] - l3["price"])
                            / h2["price"]
                        ) * 100.0

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        candidates.append({
                            "side": "LONG",

                            "hook_time": l3["time"],

                            "h1_time": h1["time"],
                            "h2_time": h2["time"],
                            "h3_time": None,

                            "l1_time": l1["time"],
                            "l2_time": l2["time"],
                            "l3_time": l3["time"],

                            "h1": h1["price"],
                            "h2": h2["price"],
                            "h3": None,

                            "l1": l1["price"],
                            "l2": l2["price"],
                            "l3": l3["price"],

                            "target": h2["price"],
                            "hook_range": hook_range,
                        })

    if not candidates:
        return None

    short_count = sum(
        1
        for x in candidates
        if x["side"] == "SHORT"
    )

    long_count = sum(
        1
        for x in candidates
        if x["side"] == "LONG"
    )

    DIAGNOSTICS["short_hooks"] += short_count
    DIAGNOSTICS["long_hooks"] += long_count

    candidates.sort(
        key=lambda x: x["hook_time"],
        reverse=True,
    )

    return candidates[0]


# ============================================================
# HOOK VALIDITY
# ============================================================

def hook_is_expired(hook):
    hook_time = safe_int(
        hook.get("hook_time")
    )

    if hook_time is None:
        return True

    return (
        now_ts() - hook_time
        > MAX_HOOK_AGE_SECONDS
    )


def hook_is_invalidated(
    symbol,
    hook,
):
    try:

        df = fetch_candles(
            symbol,
            M30_INTERVAL,
            M30_CANDLES,
        )

        closed = get_closed_candles(
            df,
            M30_SECONDS,
        )

        if len(closed) < 30:
            return False

        highs, lows = detect_pivots(closed)

        hook_time = safe_int(
            hook.get("hook_time")
        )

        if hook_time is None:
            return True

        side = hook.get("side")

        # Aggressive invalidation exactly according
        # to current scanner logic.
        if side == "SHORT":

            for pivot in lows:

                if pivot["time"] > hook_time:
                    return True

        elif side == "LONG":

            for pivot in highs:

                if pivot["time"] > hook_time:
                    return True

        return False

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            f"Hook invalidation error "
            f"{symbol}: {e}"
        )

        return False


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(
    symbol,
    hook,
):
    conn = db_connect()

    try:

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
                ?, 1, 0, ?
            )
        """, (
            symbol,
            hook.get("side"),
            hook.get("hook_time"),

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

            hook.get("target"),
            now_ts(),
        ))

        conn.commit()

        DIAGNOSTICS["hooks_saved"] += 1

        return conn.execute(
            "SELECT last_insert_rowid()"
        ).fetchone()[0]

    finally:
        conn.close()


# ============================================================
# ACTIVE HOOKS
# ============================================================

def get_active_hooks():
    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM hooks
            WHERE active = 1
              AND invalidated = 0
            ORDER BY hook_time DESC
        """).fetchall()

        # IMPORTANT:
        # sqlite3.Row -> dict
        # so .get() works everywhere.
        return [
            row_to_dict(row)
            for row in rows
        ]

    finally:
        conn.close()


def deactivate_hook(hook_id):
    conn = db_connect()

    try:

        conn.execute("""
            UPDATE hooks
            SET active = 0
            WHERE id = ?
        """, (hook_id,))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# M1 123F
# ============================================================

def detect_m1_123f(
    df,
    hook,
):
    highs, lows = detect_pivots(df)

    if len(highs) < 4:
        return None

    hook_time = safe_int(
        hook.get("hook_time")
    )

    side = hook.get("side")

    if hook_time is None:
        return None

    candidates = [
        x
        for x in highs
        if x["time"] > hook_time
    ]

    if len(candidates) < 4:
        return None

    # Newest F first.
    candidates = sorted(
        candidates,
        key=lambda x: x["time"],
        reverse=True,
    )

    for f in candidates:

        f_age = age_seconds(
            f["time"]
        )

        if f_age is None:
            continue

        if f_age > MAX_F_AGE_SECONDS:
            continue

        before_f = [
            x
            for x in candidates
            if x["time"] < f["time"]
        ]

        if len(before_f) < 3:
            continue

        # Newest 3 pivots before F.
        points = before_f[-3:]

        if len(points) != 3:
            continue

        p1, p2, p3 = points

        # ----------------------------------------------------
        # SHORT
        #
        # 1 < 2 < 3 < F
        # ----------------------------------------------------

        if side == "SHORT":

            if not (
                p1["price"]
                < p2["price"]
                < p3["price"]
                < f["price"]
            ):
                continue

            hook_h3 = safe_float(
                hook.get("h3")
            )

            if hook_h3 is None:
                continue

            distance = (
                abs(f["price"] - hook_h3)
                / hook_h3
            ) * 100.0

            if (
                distance
                > M1_MAX_STRUCTURE_DISTANCE_PCT
            ):
                continue

            swing = (
                (f["price"] - p1["price"])
                / p1["price"]
            ) * 100.0

            if swing < M1_MIN_SWING_PCT:
                continue

            DIAGNOSTICS["m1_123f_short"] += 1

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

                "swing_pct": swing,
                "f_age": f_age,
            }

        # ----------------------------------------------------
        # LONG
        #
        # 1 > 2 > 3 > F
        # ----------------------------------------------------

        if side == "LONG":

            if not (
                p1["price"]
                > p2["price"]
                > p3["price"]
                > f["price"]
            ):
                continue

            hook_l3 = safe_float(
                hook.get("l3")
            )

            if hook_l3 is None:
                continue

            distance = (
                abs(f["price"] - hook_l3)
                / hook_l3
            ) * 100.0

            if (
                distance
                > M1_MAX_STRUCTURE_DISTANCE_PCT
            ):
                continue

            swing = (
                (p1["price"] - f["price"])
                / p1["price"]
            ) * 100.0

            if swing < M1_MIN_SWING_PCT:
                continue

            DIAGNOSTICS["m1_123f_long"] += 1

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

                "swing_pct": swing,
                "f_age": f_age,
            }

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    try:

        url = f"{KRAKEN_API_BASE}/tickers"

        r = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={
                "User-Agent": USER_AGENT
            },
        )

        r.raise_for_status()

        data = r.json()

        tickers = data.get(
            "tickers",
            []
        )

        for item in tickers:

            if not isinstance(item, dict):
                continue

            item_symbol = (
                item.get("symbol")
                or item.get("pair")
                or item.get("instrument")
            )

            if str(item_symbol).upper() != symbol.upper():
                continue

            for key in (
                "last",
                "lastPrice",
                "markPrice",
                "price",
            ):

                price = safe_float(
                    item.get(key)
                )

                if price is not None:
                    return price

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            f"Price error {symbol}: {e}"
        )

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    hook,
    f_structure,
):
    side = hook.get("side")

    entry = safe_float(
        f_structure.get("f")
    )

    if entry is None:
        return None

    if side == "LONG":

        lows = [
            hook.get("l1"),
            hook.get("l2"),
            hook.get("l3"),
            f_structure.get("f"),
        ]

        lows = [
            safe_float(x)
            for x in lows
            if safe_float(x) is not None
        ]

        tp = safe_float(
            hook.get("target")
        )

        if not lows or tp is None:
            return None

        lowest = min(lows)

        sl = lowest * (
            1.0 - SL_BUFFER_PCT / 100.0
        )

        if not (
            sl < entry < tp
        ):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    if side == "SHORT":

        highs = [
            hook.get("h1"),
            hook.get("h2"),
            hook.get("h3"),
            f_structure.get("f"),
        ]

        highs = [
            safe_float(x)
            for x in highs
            if safe_float(x) is not None
        ]

        tp = safe_float(
            hook.get("target")
        )

        if not highs or tp is None:
            return None

        highest = max(highs)

        sl = highest * (
            1.0 + SL_BUFFER_PCT / 100.0
        )

        if not (
            tp < entry < sl
        ):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    return None


# ============================================================
# SIGNAL DATABASE
# ============================================================

def signal_exists(event_key):
    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE event_key = ?
            LIMIT 1
        """, (event_key,)).fetchone()

        return row is not None

    finally:
        conn.close()


def recent_signal_for_symbol(
    symbol,
    seconds=SIGNAL_COOLDOWN_SECONDS,
):
    conn = db_connect()

    try:

        cutoff = now_ts() - seconds

        return conn.execute("""
            SELECT *
            FROM signals
            WHERE symbol = ?
              AND created_at >= ?
            ORDER BY created_at DESC
            LIMIT 1
        """, (
            symbol,
            cutoff,
        )).fetchone()

    finally:
        conn.close()


def count_open_trades():
    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT COUNT(*)
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()

        return int(row[0])

    finally:
        conn.close()


def insert_signal(
    event_key,
    symbol,
    side,
    hook,
    f_structure,
    levels,
    chart_path,
):
    conn = db_connect()

    try:

        cursor = conn.execute("""
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
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, 'OPEN', ?, ?
            )
        """, (
            event_key,
            symbol,
            side,
            hook.get("id"),
            hook.get("hook_time"),
            f_structure.get("f_time"),
            levels.get("entry"),
            levels.get("sl"),
            levels.get("tp"),
            f_structure.get("f"),
            hook.get("target"),
            now_ts(),
            chart_path,
        ))

        conn.commit()

        return cursor.lastrowid

    except sqlite3.IntegrityError:
        return None

    finally:
        conn.close()


# ============================================================
# TRADE MONITOR
# ============================================================

def get_open_trades():
    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY created_at ASC
        """).fetchall()

        # IMPORTANT:
        # Convert sqlite3.Row to dict.
        return [
            row_to_dict(row)
            for row in rows
        ]

    finally:
        conn.close()


def close_trade(
    trade_id,
    close_price,
    pnl_pct,
    reason,
):
    conn = db_connect()

    try:

        conn.execute("""
            UPDATE signals
            SET
                status = 'CLOSED',
                closed_at = ?,
                close_price = ?,
                pnl_pct = ?,
                exit_reason = ?
            WHERE id = ?
        """, (
            now_ts(),
            close_price,
            pnl_pct,
            reason,
            trade_id,
        ))

        conn.commit()

    finally:
        conn.close()


def monitor_open_trades():

    trades = get_open_trades()

    for trade in trades:

        try:

            symbol = trade["symbol"]
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

            if None in (
                entry,
                sl,
                tp,
            ):
                continue

            current = get_current_price(
                symbol
            )

            if current is None:
                continue

            if side == "LONG":

                pnl_pct = (
                    (current - entry)
                    / entry
                ) * 100.0

                if current >= tp:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "TP",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🟢 LONG\n"
                        f"Entry: {format_price(entry)}\n"
                        f"Close: {format_price(current)}\n"
                        f"P/L: {pnl_pct:+.2f}%\n"
                        f"Reason: TP"
                    )

                    continue

                if current <= sl:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "SL",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🟢 LONG\n"
                        f"Entry: {format_price(entry)}\n"
                        f"Close: {format_price(current)}\n"
                        f"P/L: {pnl_pct:+.2f}%\n"
                        f"Reason: SL"
                    )

                    continue

            elif side == "SHORT":

                pnl_pct = (
                    (entry - current)
                    / entry
                ) * 100.0

                if current <= tp:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "TP",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🔴 SHORT\n"
                        f"Entry: {format_price(entry)}\n"
                        f"Close: {format_price(current)}\n"
                        f"P/L: {pnl_pct:+.2f}%\n"
                        f"Reason: TP"
                    )

                    continue

                if current >= sl:

                    close_trade(
                        trade["id"],
                        current,
                        pnl_pct,
                        "SL",
                    )

                    send_telegram(
                        "🚨 NDS TRADE CLOSED\n\n"
                        f"{symbol} 🔴 SHORT\n"
                        f"Entry: {format_price(entry)}\n"
                        f"Close: {format_price(current)}\n"
                        f"P/L: {pnl_pct:+.2f}%\n"
                        f"Reason: SL"
                    )

                    continue

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                "Trade monitor error:",
                e,
            )


# ============================================================
# M30 SIGNAL CHART
# ============================================================

def draw_candle(
    ax,
    x,
    candle,
    width=0.65,
):
    o = candle["open"]
    h = candle["high"]
    l = candle["low"]
    c = candle["close"]

    ax.vlines(
        x,
        l,
        h,
        linewidth=0.8,
    )

    body_low = min(o, c)
    body_height = abs(c - o)

    if body_height == 0:
        body_height = max(
            abs(h - l) * 0.002,
            1e-12,
        )

    rect = Rectangle(
        (
            x - width / 2,
            body_low,
        ),
        width,
        body_height,
        fill=False,
        linewidth=1.0,
    )

    ax.add_patch(rect)


def create_signal_chart(
    symbol,
    hook,
    f_structure,
    levels,
):
    if not CHARTS_ENABLED:
        return None

    try:

        df = fetch_candles(
            symbol,
            M30_INTERVAL,
            M30_CANDLES,
        )

        df = get_closed_candles(
            df,
            M30_SECONDS,
        )

        if df.empty:
            return None

        df = df.tail(
            CHART_CANDLES
        ).reset_index(drop=True)

        if df.empty:
            return None

        fig, ax = plt.subplots(
            figsize=(16, 9)
        )

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for i, row in df.iterrows():

            draw_candle(
                ax,
                i,
                row,
            )

        time_to_x = {
            int(row["time"]): i
            for i, row in df.iterrows()
        }

        # ----------------------------------------------------
        # Helper to plot point
        # ----------------------------------------------------

        def plot_point(
            label,
            ts,
            price,
            marker="o",
            size=90,
        ):
            if ts is None or price is None:
                return

            ts = safe_int(ts)
            price = safe_float(price)

            if ts is None or price is None:
                return

            # If exact pivot candle is outside chart,
            # find nearest visible candle.
            if ts in time_to_x:
                x = time_to_x[ts]
            else:
                nearest = min(
                    time_to_x.keys(),
                    key=lambda t: abs(t - ts),
                )
                x = time_to_x[nearest]

            ax.scatter(
                [x],
                [price],
                s=size,
                marker=marker,
                zorder=10,
            )

            ax.annotate(
                f"{label}\n{format_price(price)}",
                (
                    x,
                    price,
                ),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=9,
                fontweight="bold",
            )

        # ----------------------------------------------------
        # M30 Hook points
        # ----------------------------------------------------

        side = hook.get("side")

        plot_point(
            "H1",
            hook.get("h1_time"),
            hook.get("h1"),
            marker="^",
        )

        plot_point(
            "H2",
            hook.get("h2_time"),
            hook.get("h2"),
            marker="^",
        )

        plot_point(
            "H3",
            hook.get("h3_time"),
            hook.get("h3"),
            marker="^",
        )

        plot_point(
            "L1",
            hook.get("l1_time"),
            hook.get("l1"),
            marker="v",
        )

        plot_point(
            "L2",
            hook.get("l2_time"),
            hook.get("l2"),
            marker="v",
        )

        plot_point(
            "L3",
            hook.get("l3_time"),
            hook.get("l3"),
            marker="v",
        )

        # ----------------------------------------------------
        # Draw Hook connection
        # ----------------------------------------------------

        hook_points = []

        if side == "SHORT":

            sequence = [
                (
                    hook.get("h1_time"),
                    hook.get("h1"),
                ),
                (
                    hook.get("l1_time"),
                    hook.get("l1"),
                ),
                (
                    hook.get("h2_time"),
                    hook.get("h2"),
                ),
                (
                    hook.get("l2_time"),
                    hook.get("l2"),
                ),
                (
                    hook.get("h3_time"),
                    hook.get("h3"),
                ),
            ]

        else:

            sequence = [
                (
                    hook.get("l1_time"),
                    hook.get("l1"),
                ),
                (
                    hook.get("h1_time"),
                    hook.get("h1"),
                ),
                (
                    hook.get("l2_time"),
                    hook.get("l2"),
                ),
                (
                    hook.get("h2_time"),
                    hook.get("h2"),
                ),
                (
                    hook.get("l3_time"),
                    hook.get("l3"),
                ),
            ]

        for ts, price in sequence:

            if ts is None or price is None:
                continue

            ts = safe_int(ts)

            if ts not in time_to_x:
                nearest = min(
                    time_to_x.keys(),
                    key=lambda t: abs(t - ts),
                )
                x = time_to_x[nearest]
            else:
                x = time_to_x[ts]

            hook_points.append(
                (x, price)
            )

        if len(hook_points) >= 2:

            ax.plot(
                [p[0] for p in hook_points],
                [p[1] for p in hook_points],
                linewidth=2.0,
                linestyle="-",
                label="M30 Hook",
            )

        # ----------------------------------------------------
        # M1 123F points
        #
        # They are mapped to the M30 chart by time.
        # ----------------------------------------------------

        m1_points = [
            (
                "1",
                f_structure.get("p1_time"),
                f_structure.get("p1"),
            ),
            (
                "2",
                f_structure.get("p2_time"),
                f_structure.get("p2"),
            ),
            (
                "3",
                f_structure.get("p3_time"),
                f_structure.get("p3"),
            ),
            (
                "F",
                f_structure.get("f_time"),
                f_structure.get("f"),
            ),
        ]

        for label, ts, price in m1_points:

            if ts is None or price is None:
                continue

            plot_point(
                label,
                ts,
                price,
                marker="D",
                size=65,
            )

        # ----------------------------------------------------
        # Entry / SL / TP
        # ----------------------------------------------------

        entry = levels["entry"]
        sl = levels["sl"]
        tp = levels["tp"]

        ax.axhline(
            entry,
            linestyle="--",
            linewidth=1.5,
            label=(
                f"ENTRY {format_price(entry)}"
            ),
        )

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1.5,
            label=(
                f"SL {format_price(sl)}"
            ),
        )

        ax.axhline(
            tp,
            linestyle="--",
            linewidth=1.5,
            label=(
                f"TP {format_price(tp)}"
            ),
        )

        # ----------------------------------------------------
        # Formatting
        # ----------------------------------------------------

        direction = (
            "LONG 🟢"
            if side == "LONG"
            else "SHORT 🔴"
        )

        hook_range = safe_float(
            hook.get("hook_range")
        ) or 0

        swing = safe_float(
            f_structure.get("swing_pct")
        ) or 0

        title = (
            f"NDS M30 → M1 SIGNAL | "
            f"{symbol} | {direction}\n"
            f"M30 Hook: {hook_range:.2f}% | "
            f"M1 123F Swing: {swing:.2f}%"
        )

        ax.set_title(
            title,
            fontsize=14,
            fontweight="bold",
        )

        ax.set_ylabel(
            "Price"
        )

        ax.set_xlabel(
            "M30 candles"
        )

        ax.grid(
            True,
            alpha=0.25,
        )

        ax.legend(
            loc="best",
            fontsize=9,
        )

        plt.tight_layout()

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
            / (
                f"signal_"
                f"{safe_symbol}_"
                f"{side}_"
                f"{hook.get('hook_time')}.png"
            )
        )

        fig.savefig(
            path,
            dpi=150,
            bbox_inches="tight",
        )

        plt.close(fig)

        return str(path)

    except Exception as e:

        DIAGNOSTICS["errors"] += 1

        print(
            "Chart error:",
            e,
        )

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def send_new_signal(
    symbol,
    hook,
    f_structure,
    levels,
):
    side = hook.get("side")

    direction = (
        "🟢 LONG"
        if side == "LONG"
        else "🔴 SHORT"
    )

    hook_range = (
        safe_float(
            hook.get("hook_range")
        )
        or 0
    )

    swing = (
        safe_float(
            f_structure.get("swing_pct")
        )
        or 0
    )

    f_age = (
        safe_int(
            f_structure.get("f_age")
        )
        or 0
    )

    text = (
        "🚨 NDS NEW SIGNAL\n\n"
        f"{symbol} {direction}\n\n"

        f"Entry: {format_price(levels['entry'])}\n"
        f"SL: {format_price(levels['sl'])}\n"
        f"TP: {format_price(levels['tp'])}\n\n"

        f"M30 Hook: {hook_range:.2f}%\n"
        f"M1 123F Swing: {swing:.2f}%\n"
        f"F Age: {f_age // 60}m\n\n"

        "PAPER ONLY"
    )

    send_telegram(text)


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(assets):

    print(
        f"[{utc_now_string()}] "
        f"M30 scan: {len(assets)} assets"
    )

    for symbol in assets:

        try:

            df = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES,
            )

            closed = get_closed_candles(
                df,
                M30_SECONDS,
            )

            if len(closed) < 30:
                continue

            hook = detect_m30_hook(
                closed
            )

            if hook is None:
                continue

            if hook_is_expired(
                hook
            ):
                continue

            conn = db_connect()

            try:

                existing = conn.execute("""
                    SELECT id
                    FROM hooks
                    WHERE symbol = ?
                      AND side = ?
                      AND hook_time = ?
                    LIMIT 1
                """, (
                    symbol,
                    hook.get("side"),
                    hook.get("hook_time"),
                )).fetchone()

            finally:
                conn.close()

            if existing is not None:
                continue

            save_hook(
                symbol,
                hook,
            )

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                f"M30 scan error "
                f"{symbol}: {e}"
            )


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(assets):

    hooks = get_active_hooks()

    if not hooks:
        return

    for hook in hooks:

        try:

            symbol = hook.get(
                "symbol"
            )

            hook_id = hook.get(
                "id"
            )

            if hook_is_expired(
                hook
            ):

                deactivate_hook(
                    hook_id
                )

                continue

            if hook_is_invalidated(
                symbol,
                hook,
            ):

                deactivate_hook(
                    hook_id
                )

                continue

            df = fetch_candles(
                symbol,
                M1_INTERVAL,
                M1_CANDLES,
            )

            closed = get_closed_candles(
                df,
                M1_SECONDS,
            )

            if len(closed) < 30:
                continue

            f_structure = detect_m1_123f(
                closed,
                hook,
            )

            if f_structure is None:
                continue

            if count_open_trades() >= MAX_OPEN_TRADES:
                continue

            event_key = (
                f"{symbol}|"
                f"{hook.get('side')}|"
                f"{hook.get('hook_time')}|"
                f"{f_structure.get('f_time')}"
            )

            if signal_exists(
                event_key
            ):
                continue

            recent = recent_signal_for_symbol(
                symbol
            )

            if recent is not None:
                continue

            levels = calculate_trade_levels(
                hook,
                f_structure,
            )

            if levels is None:
                continue

            # ------------------------------------------------
            # Final directional validation
            # ------------------------------------------------

            if hook.get("side") == "LONG":

                if not (
                    levels["sl"]
                    < levels["entry"]
                    < levels["tp"]
                ):
                    continue

            elif hook.get("side") == "SHORT":

                if not (
                    levels["tp"]
                    < levels["entry"]
                    < levels["sl"]
                ):
                    continue

            else:
                continue

            # ------------------------------------------------
            # Create full M30 signal chart
            # ------------------------------------------------

            chart_path = create_signal_chart(
                symbol,
                hook,
                f_structure,
                levels,
            )

            signal_id = insert_signal(
                event_key,
                symbol,
                hook.get("side"),
                hook,
                f_structure,
                levels,
                chart_path,
            )

            if signal_id is None:
                continue

            DIAGNOSTICS["signals"] += 1

            send_new_signal(
                symbol,
                hook,
                f_structure,
                levels,
            )

            print(
                f"NEW SIGNAL: "
                f"{symbol} "
                f"{hook.get('side')} "
                f"Entry={format_price(levels['entry'])}"
            )

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                f"M1 scan error "
                f"{hook.get('symbol')}: {e}"
            )

            traceback.print_exc()


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def open_trades_text():

    trades = get_open_trades()

    if not trades:
        return "📂 OPEN TRADES: 0"

    lines = [
        f"📂 OPEN TRADES: {len(trades)}"
    ]

    for trade in trades:

        try:

            symbol = trade["symbol"]
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

            created_at = safe_int(
                trade["created_at"]
            )

            current = get_current_price(
                symbol
            )

            if None in (
                entry,
                sl,
                tp,
            ):
                continue

            if current is None:
                current = entry

            # ------------------------------------------------
            # Current P/L
            # ------------------------------------------------

            if side == "LONG":

                pnl_pct = (
                    (current - entry)
                    / entry
                ) * 100.0

                # Distance from CURRENT to TP / SL.
                tp_pct = (
                    (tp - current)
                    / current
                ) * 100.0

                sl_pct = (
                    (sl - current)
                    / current
                ) * 100.0

                icon = "🟢"

            else:

                pnl_pct = (
                    (entry - current)
                    / entry
                ) * 100.0

                # For SHORT, positive means price
                # still has this percentage to fall
                # toward TP.
                tp_pct = (
                    (current - tp)
                    / current
                ) * 100.0

                # Negative means price must rise
                # this percentage to reach SL.
                sl_pct = (
                    (current - sl)
                    / current
                ) * 100.0

                icon = "🔴"

            duration = (
                format_duration(
                    now_ts() - created_at
                )
                if created_at
                else "-"
            )

            lines.append("")

            lines.append(
                f"{icon} {symbol} {side}"
            )

            lines.append(
                f"Entry: {format_price(entry)}"
            )

            lines.append(
                f"Current: **{format_price(current)}** "
                f"({pnl_pct:+.2f}%)"
            )

            lines.append(
                f"TP: {format_price(tp)} "
                f"({tp_pct:+.2f}%)"
            )

            lines.append(
                f"SL: {format_price(sl)} "
                f"({sl_pct:+.2f}%)"
            )

            lines.append(
                f"Duration: {duration}"
            )

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                "Open trade report error:",
                e,
            )

    return "\n".join(lines)


# ============================================================
# CLOSED STATS
# ============================================================

def get_closed_stats():

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT
                COUNT(*) AS closed,
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

        return {
            "closed": int(
                row["closed"] or 0
            ),
            "wins": int(
                row["wins"] or 0
            ),
            "losses": int(
                row["losses"] or 0
            ),
            "pnl": float(
                row["pnl"] or 0
            ),
        }

    finally:
        conn.close()


# ============================================================
# DIAGNOSTIC TEXT
# ============================================================

def diagnostic_text():

    return (
        "\n"
        "🔎 DIAGNOSTIC\n"
        f"M30 requests: {DIAGNOSTICS['m30_requests']}\n"
        f"M30 OK: {DIAGNOSTICS['m30_data_ok']}\n"
        f"M30 empty: {DIAGNOSTICS['m30_empty']}\n"
        f"M30 short: {DIAGNOSTICS['m30_short']}\n"
        f"Pivot highs: {DIAGNOSTICS['pivot_highs']}\n"
        f"Pivot lows: {DIAGNOSTICS['pivot_lows']}\n"
        f"SHORT hooks: {DIAGNOSTICS['short_hooks']}\n"
        f"LONG hooks: {DIAGNOSTICS['long_hooks']}\n"
        f"Hooks saved: {DIAGNOSTICS['hooks_saved']}\n"
        f"M1 requests: {DIAGNOSTICS['m1_requests']}\n"
        f"M1 OK: {DIAGNOSTICS['m1_data_ok']}\n"
        f"M1 empty: {DIAGNOSTICS['m1_empty']}\n"
        f"M1 short: {DIAGNOSTICS['m1_short']}\n"
        f"M1 123F SHORT: "
        f"{DIAGNOSTICS['m1_123f_short']}\n"
        f"M1 123F LONG: "
        f"{DIAGNOSTICS['m1_123f_long']}\n"
        f"Signals: {DIAGNOSTICS['signals']}\n"
        f"Errors: {DIAGNOSTICS['errors']}"
    )


def reset_diagnostics():
    for key in DIAGNOSTICS:
        DIAGNOSTICS[key] = 0


# ============================================================
# REPORT
# ============================================================

def send_report(
    assets,
    new_signals=0,
):

    open_text = open_trades_text()

    stats = get_closed_stats()

    report = (
        "📊 NDS M30 → M1 REPORT\n\n"

        f"Version: {VERSION}\n"
        f"Time: {utc_now_string()}\n\n"

        f"Assets scanned: {len(assets)}\n"
        f"New signals: {new_signals}\n\n"

        f"{open_text}\n\n"

        "📈 PERFORMANCE\n"
        f"Closed: {stats['closed']}\n"
        f"Wins: {stats['wins']}\n"
        f"Losses: {stats['losses']}\n"
        f"PnL: {stats['pnl']:+.2f}%\n"

        f"{diagnostic_text()}\n\n"

        "PAPER ONLY"
    )

    send_telegram(
        report
    )

    print(report)

    reset_diagnostics()


# ============================================================
# RUN DATABASE
# ============================================================

def record_run_start():

    conn = db_connect()

    try:

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

        conn.commit()

        return cursor.lastrowid

    finally:
        conn.close()


def record_run_finish(
    run_id,
    assets_scanned,
    new_signals,
    errors,
):

    conn = db_connect()

    try:

        conn.execute("""
            UPDATE scanner_runs
            SET
                finished_at = ?,
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

    finally:
        conn.close()


# ============================================================
# STARTUP MESSAGE
# ============================================================

def send_startup():

    text = (
        "🚀 NDS M30 → M1 SCANNER\n\n"
        f"Version: {VERSION}\n"
        "Mode: PAPER ONLY\n\n"

        "M30 SHORT:\n"
        "H1 → L1 → H2 → L2 → H3\n"
        "H2 > H1\n"
        "L2 < L1\n"
        "H3 > H2\n\n"

        "M30 LONG:\n"
        "L1 → H1 → L2 → H2 → L3\n"
        "L2 < L1\n"
        "H2 > H1\n"
        "L3 < L2\n\n"

        "M1 SHORT 123F:\n"
        "1 < 2 < 3 < F\n\n"

        "M1 LONG 123F:\n"
        "1 > 2 > 3 > F\n\n"

        f"Max open trades: {MAX_OPEN_TRADES}\n"
        f"Hook max age: "
        f"{MAX_HOOK_AGE_SECONDS // 3600}h\n"
        f"F max age: "
        f"{MAX_F_AGE_SECONDS // 60}m\n\n"

        "Charts: ON\n"
        "PAPER ONLY"
    )

    send_telegram(
        text
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not PAPER_ONLY:

        raise RuntimeError(
            "SAFETY STOP: "
            "PAPER_ONLY must remain True."
        )

    init_db()

    CHART_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    run_id = record_run_start()

    print(
        "\n"
        "==================================================\n"
        f"NDS M30 -> M1 SCANNER {VERSION}\n"
        "PAPER ONLY\n"
        "==================================================\n"
    )

    send_startup()

    assets = discover_assets()

    DIAGNOSTICS["assets_scanned"] = len(
        assets
    )

    if not assets:

        record_run_finish(
            run_id,
            0,
            0,
            DIAGNOSTICS["errors"],
        )

        print(
            "No assets discovered."
        )

        return

    # --------------------------------------------------------
    # Initial scan
    # --------------------------------------------------------

    scan_m30(
        assets
    )

    scan_m1(
        assets
    )

    monitor_open_trades()

    send_report(
        assets,
        DIAGNOSTICS["signals"],
    )

    # --------------------------------------------------------
    # Loop
    # --------------------------------------------------------

    started = time.time()

    last_m30 = time.time()
    last_m1 = time.time()
    last_report = time.time()
    last_asset_refresh = time.time()

    total_new_signals = 0

    while (
        time.time() - started
        < RUN_DURATION_SECONDS
    ):

        try:

            current_time = time.time()

            # Refresh asset list hourly.
            if (
                current_time
                - last_asset_refresh
                >= 3600
            ):

                new_assets = discover_assets()

                if new_assets:
                    assets = new_assets

                last_asset_refresh = current_time

            # M30 every 5 minutes.
            if (
                current_time
                - last_m30
                >= M30_SCAN_SECONDS
            ):

                before = DIAGNOSTICS[
                    "signals"
                ]

                scan_m30(
                    assets
                )

                last_m30 = current_time

                after = DIAGNOSTICS[
                    "signals"
                ]

                total_new_signals += (
                    after - before
                )

            # M1 every minute.
            if (
                current_time
                - last_m1
                >= M1_SCAN_SECONDS
            ):

                before = DIAGNOSTICS[
                    "signals"
                ]

                scan_m1(
                    assets
                )

                last_m1 = current_time

                after = DIAGNOSTICS[
                    "signals"
                ]

                total_new_signals += (
                    after - before
                )

            # Monitor every loop.
            monitor_open_trades()

            # Report every 15 minutes.
            if (
                current_time
                - last_report
                >= REPORT_SECONDS
            ):

                send_report(
                    assets,
                    total_new_signals,
                )

                total_new_signals = 0
                last_report = current_time

            time.sleep(5)

        except KeyboardInterrupt:

            print(
                "Scanner interrupted."
            )

            break

        except Exception as e:

            DIAGNOSTICS["errors"] += 1

            print(
                "Main loop error:",
                e,
            )

            traceback.print_exc()

            time.sleep(5)

    # --------------------------------------------------------
    # Final monitor/report
    # --------------------------------------------------------

    monitor_open_trades()

    send_report(
        assets,
        total_new_signals,
    )

    record_run_finish(
        run_id,
        len(assets),
        DIAGNOSTICS["signals"],
        DIAGNOSTICS["errors"],
    )

    print(
        "\n"
        "==================================================\n"
        "NDS SCANNER FINISHED\n"
        "==================================================\n"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
