# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.3.0
# ============================================================
#
# PAPER ONLY
#
# IMPORTANT STRUCTURE RULE
#
# M30:
#   LONG / NEGATIVE HOOK:
#       L1 -> H1 -> L2 -> H2 -> L3
#       L2 < L1
#       L3 < L2
#
#   SHORT / POSITIVE HOOK:
#       H1 -> L1 -> H2 -> L2 -> H3
#       H2 > H1
#       H3 > H2
#
# M1:
#   123F structure is DIFFERENT.
#
#   LONG:
#       L1 -> H1 -> L2 -> H2 -> L3 -> F
#       NO REQUIREMENT:
#           L2 < L1
#           L3 < L2
#
#   SHORT:
#       H1 -> L1 -> H2 -> L2 -> H3 -> F
#       NO REQUIREMENT:
#           H2 > H1
#           H3 > H2
#
# F must be a confirmed M1 pivot.
#
# Entry is current market price after F confirmation.
#
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.3.0"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v43.db"
CHART_DIR = "nds_charts"

KRAKEN_CHART_BASE = "https://futures.kraken.com/api/charts/v1"
KRAKEN_REST_BASE = "https://futures.kraken.com/derivatives/api/v3"

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

# ------------------------------------------------------------
# ASSETS
# ------------------------------------------------------------

TARGET_ASSETS = 100
ASSET_REFRESH_SECONDS = 3600

# ------------------------------------------------------------
# TIMEFRAMES
# ------------------------------------------------------------

M30_TIMEFRAME = "30m"
M1_TIMEFRAME = "1m"

M30_CANDLES = 320
M1_CANDLES = 1500

M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60

# F must be fresh.
M1_F_MAX_AGE_MINUTES = 5

# ------------------------------------------------------------
# PIVOTS
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# ------------------------------------------------------------
# M30 HOOK
# ------------------------------------------------------------

HOOK_MIN_RANGE_PCT = 0.20

# ------------------------------------------------------------
# M1 STRUCTURE
# ------------------------------------------------------------

M1_MIN_SWING_PCT = 0.07

# These are now used only as minimum structure counts.
M1_MIN_HIGH_COUNT = 4
M1_MIN_LOW_COUNT = 4

M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

# ------------------------------------------------------------
# HOOK AGE
# ------------------------------------------------------------

# Prevent an old hook from remaining active indefinitely.
MAX_HOOK_AGE_HOURS = 6

# ------------------------------------------------------------
# TRADES
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_MINUTES = 60

SL_BUFFER_PCT = 0.15

# ------------------------------------------------------------
# REPORT
# ------------------------------------------------------------

REPORT_INTERVAL_SECONDS = 900

# ------------------------------------------------------------
# CHARTS
# ------------------------------------------------------------

CHARTS_ENABLED = True

CHART_CANDLES = 240
ACTIVE_HOOK_CHART_CANDLES = 100

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 15
REQUEST_SLEEP = 0.05
ERROR_SLEEP = 10

SESSION = requests.Session()

os.makedirs(CHART_DIR, exist_ok=True)


# ============================================================
# TIME HELPERS
# ============================================================

def now_ts():
    return time.time()


def utc_now():
    return datetime.now(timezone.utc)


def fmt_time(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return "N/A"


def pct_change(a, b):
    if a is None or b is None or a == 0:
        return 0.0
    return abs((b - a) / a) * 100.0


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at REAL,
            finished_at REAL,
            scan_type TEXT,
            assets INTEGER DEFAULT 0,
            active_hooks INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            errors INTEGER DEFAULT 0
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            hook_type TEXT,

            hook_time REAL,
            f_time REAL,

            entry REAL,
            sl REAL,

            tp1 REAL,
            tp2 REAL,
            tp3 REAL,

            status TEXT DEFAULT 'OPEN',

            current_price REAL,
            pnl_pct REAL DEFAULT 0,

            result TEXT,

            opened_at REAL,
            closed_at REAL,

            exit_price REAL,
            exit_reason TEXT,

            created_at REAL,

            UNIQUE(symbol, side, f_time)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            event_key TEXT,
            created_at REAL,
            UNIQUE(symbol, event_key)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT UNIQUE,

            hook_type TEXT,
            hook_time REAL,

            direction TEXT,

            h3_time REAL,
            l3_time REAL,

            target_price REAL,

            active INTEGER DEFAULT 1,

            updated_at REAL
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# SCANNER RUN LOG
# ============================================================

def start_scan_run(scan_type):
    started = now_ts()

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO scanner_runs (
            started_at,
            scan_type
        )
        VALUES (?, ?)
    """, (
        started,
        scan_type
    ))

    run_id = cur.lastrowid

    conn.commit()
    conn.close()

    return run_id, started


def finish_scan_run(
    run_id,
    started,
    scan_type,
    assets,
    active_hooks,
    new_signals,
    errors
):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        UPDATE scanner_runs
        SET
            finished_at = ?,
            assets = ?,
            active_hooks = ?,
            new_signals = ?,
            errors = ?
        WHERE id = ?
    """, (
        now_ts(),
        assets,
        active_hooks,
        new_signals,
        errors,
        run_id
    ))

    conn.commit()
    conn.close()


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# KRAKEN CANDLES
# ============================================================

def fetch_candles(symbol, resolution, count):
    url = f"{KRAKEN_CHART_BASE}/trade/{symbol}/{resolution}"

    data = http_get(
        url,
        params={
            "last": count
        }
    )

    candles = None

    if isinstance(data, dict):
        if "candles" in data:
            candles = data["candles"]
        elif "data" in data:
            candles = data["data"]

    if candles is None:
        raise ValueError(
            f"No candle data returned for {symbol} {resolution}"
        )

    rows = []

    for c in candles:
        try:
            if isinstance(c, dict):
                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("t")
                )

                o = c.get("open", c.get("o"))
                h = c.get("high", c.get("h"))
                l = c.get("low", c.get("l"))
                close = c.get("close", c.get("c"))
                volume = c.get("volume", c.get("v", 0))

            else:
                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                close = c[4]
                volume = c[5] if len(c) > 5 else 0

            ts = float(ts)

            # Kraken can return milliseconds in some responses.
            if ts > 10_000_000_000:
                ts /= 1000.0

            rows.append({
                "time": ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(close),
                "volume": float(volume or 0)
            })

        except Exception:
            continue

    if not rows:
        raise ValueError(
            f"Could not parse candles for {symbol} {resolution}"
        )

    df = pd.DataFrame(rows)

    df = df.sort_values("time")
    df = df.drop_duplicates("time")
    df = df.reset_index(drop=True)

    return df


# ============================================================
# CLOSED CANDLES
# ============================================================

def timeframe_seconds(resolution):
    if resolution == "1m":
        return 60

    if resolution == "5m":
        return 300

    if resolution == "15m":
        return 900

    if resolution == "30m":
        return 1800

    if resolution == "1h":
        return 3600

    return 60


def get_closed_candles(df, resolution):
    if df is None or df.empty:
        return df

    tf = timeframe_seconds(resolution)
    current = now_ts()

    mask = (
        df["time"] + tf
    ) <= current

    return df.loc[mask].copy().reset_index(drop=True)


# ============================================================
# TICKERS
# ============================================================

def fetch_all_tickers():
    url = f"{KRAKEN_REST_BASE}/tickers"

    data = http_get(url)

    if isinstance(data, dict):
        if "tickers" in data:
            return data["tickers"]

        if "result" in data:
            return data["result"]

    return []


def ticker_symbol(item):
    if isinstance(item, dict):
        return (
            item.get("symbol")
            or item.get("pair")
            or item.get("instrument")
        )

    return None


def ticker_volume(item):
    if not isinstance(item, dict):
        return 0.0

    for key in (
        "volumeQuote",
        "vol24hQuote",
        "volume24h",
        "vol24h",
        "volume"
    ):
        value = item.get(key)

        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    return 0.0


def discover_assets():
    tickers = fetch_all_tickers()

    candidates = []

    for item in tickers:
        symbol = ticker_symbol(item)

        if not symbol:
            continue

        symbol = str(symbol).upper()

        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        volume = ticker_volume(item)

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

    assets = [
        symbol
        for symbol, volume in candidates[:TARGET_ASSETS]
    ]

    return assets


# ============================================================
# CURRENT PRICE
# ============================================================

def fetch_current_price(symbol):
    tickers = fetch_all_tickers()

    for item in tickers:
        if ticker_symbol(item) == symbol:
            if isinstance(item, dict):
                for key in (
                    "markPrice",
                    "last",
                    "bid",
                    "ask"
                ):
                    value = item.get(key)

                    try:
                        if value is not None:
                            return float(value)
                    except Exception:
                        pass

    return None


# ============================================================
# BULK PRICE CACHE
# ============================================================

def fetch_price_map():
    result = {}

    try:
        tickers = fetch_all_tickers()
    except Exception:
        return result

    for item in tickers:
        symbol = ticker_symbol(item)

        if not symbol:
            continue

        if not isinstance(item, dict):
            continue

        for key in (
            "markPrice",
            "last",
            "bid",
            "ask"
        ):
            try:
                value = item.get(key)

                if value is not None:
                    result[str(symbol).upper()] = float(value)
                    break
            except Exception:
                continue

    return result


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(df):
    highs = []
    lows = []

    if df is None or len(df) < (
        PIVOT_LEFT + PIVOT_RIGHT + 1
    ):
        return highs, lows

    high_values = df["high"].values
    low_values = df["low"].values
    times = df["time"].values

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):
        h = high_values[i]
        l = low_values[i]

        left_highs = high_values[
            i - PIVOT_LEFT:i
        ]

        right_highs = high_values[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        left_lows = low_values[
            i - PIVOT_LEFT:i
        ]

        right_lows = low_values[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        if (
            h > np.max(left_highs)
            and h > np.max(right_highs)
        ):
            highs.append({
                "type": "H",
                "time": float(times[i]),
                "price": float(h),
                "index": i
            })

        if (
            l < np.min(left_lows)
            and l < np.min(right_lows)
        ):
            lows.append({
                "type": "L",
                "time": float(times[i]),
                "price": float(l),
                "index": i
            })

    return highs, lows


def combined_pivots(highs, lows):
    pivots = list(highs) + list(lows)

    pivots.sort(
        key=lambda x: x["time"]
    )

    cleaned = []

    for p in pivots:
        if not cleaned:
            cleaned.append(p)
            continue

        previous = cleaned[-1]

        if previous["type"] != p["type"]:
            cleaned.append(p)
            continue

        # Consecutive same-type pivots.
        if p["type"] == "H":
            if p["price"] > previous["price"]:
                cleaned[-1] = p
        else:
            if p["price"] < previous["price"]:
                cleaned[-1] = p

    return cleaned


# ============================================================
# M30 POSITIVE HOOK
# SHORT
# ============================================================

def detect_m30_positive_hook(pivots):
    if len(pivots) < 5:
        return None

    latest = None

    for i in range(len(pivots) - 4):
        h1 = pivots[i]
        l1 = pivots[i + 1]
        h2 = pivots[i + 2]
        l2 = pivots[i + 3]
        h3 = pivots[i + 4]

        if [
            h1["type"],
            l1["type"],
            h2["type"],
            l2["type"],
            h3["type"]
        ] != ["H", "L", "H", "L", "H"]:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # M30 SHORT requires higher highs.
        # ----------------------------------------------------

        if not (
            h2["price"] > h1["price"]
            and h3["price"] > h2["price"]
        ):
            continue

        hook_range = pct_change(
            l2["price"],
            h3["price"]
        )

        if hook_range < HOOK_MIN_RANGE_PCT:
            continue

        latest = {
            "hook_type": "POSITIVE",
            "direction": "SHORT",

            "h1": h1,
            "l1": l1,
            "h2": h2,
            "l2": l2,
            "h3": h3,

            "hook_time": h3["time"],

            "h3_time": h3["time"],
            "l3_time": None,

            "target_price": l2["price"]
        }

    return latest


# ============================================================
# M30 NEGATIVE HOOK
# LONG
# ============================================================

def detect_m30_negative_hook(pivots):
    if len(pivots) < 5:
        return None

    latest = None

    for i in range(len(pivots) - 4):
        l1 = pivots[i]
        h1 = pivots[i + 1]
        l2 = pivots[i + 2]
        h2 = pivots[i + 3]
        l3 = pivots[i + 4]

        if [
            l1["type"],
            h1["type"],
            l2["type"],
            h2["type"],
            l3["type"]
        ] != ["L", "H", "L", "H", "L"]:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # M30 LONG requires lower lows.
        # ----------------------------------------------------

        if not (
            l2["price"] < l1["price"]
            and l3["price"] < l2["price"]
        ):
            continue

        hook_range = pct_change(
            h2["price"],
            l3["price"]
        )

        if hook_range < HOOK_MIN_RANGE_PCT:
            continue

        latest = {
            "hook_type": "NEGATIVE",
            "direction": "LONG",

            "l1": l1,
            "h1": h1,
            "l2": l2,
            "h2": h2,
            "l3": l3,

            "hook_time": l3["time"],

            "h3_time": None,
            "l3_time": l3["time"],

            "target_price": h2["price"]
        }

    return latest


# ============================================================
# M30 ACTIVE HOOK
# ============================================================

def hook_is_expired(hook):
    if not hook:
        return True

    age = (
        now_ts() - float(hook["hook_time"])
    ) / 3600.0

    return age > MAX_HOOK_AGE_HOURS


def hook_invalidated_by_pivot(hook, pivots):
    if not hook:
        return True

    hook_time = hook["hook_time"]

    if hook["direction"] == "SHORT":

        # After H3, a new LOW pivot invalidates
        # the active positive hook.

        for p in pivots:
            if (
                p["time"] > hook_time
                and p["type"] == "L"
            ):
                return True

    else:

        # After L3, a new HIGH pivot invalidates
        # the active negative hook.

        for p in pivots:
            if (
                p["time"] > hook_time
                and p["type"] == "H"
            ):
                return True

    return False


def determine_active_hook(df):
    closed = get_closed_candles(
        df,
        M30_TIMEFRAME
    )

    if closed is None or closed.empty:
        return None

    highs, lows = find_pivots(closed)

    pivots = combined_pivots(
        highs,
        lows
    )

    positive = detect_m30_positive_hook(
        pivots
    )

    negative = detect_m30_negative_hook(
        pivots
    )

    candidates = []

    if positive:
        candidates.append(positive)

    if negative:
        candidates.append(negative)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["hook_time"],
        reverse=True
    )

    hook = candidates[0]

    if hook_is_expired(hook):
        return None

    if hook_invalidated_by_pivot(
        hook,
        pivots
    ):
        return None

    return hook


# ============================================================
# M1 STRUCTURE HELPERS
# ============================================================

def get_recent_pivots_after(
    pivots,
    after_time
):
    return [
        p for p in pivots
        if p["time"] > after_time
    ]


# ============================================================
# M1 SHORT 123F
#
# IMPORTANT:
# H2 > H1 and H3 > H2 are NOT REQUIRED.
#
# Only alternating structure and F > H3 are required.
# ============================================================

def detect_m1_short_123f(
    df,
    hook
):
    if df is None or df.empty or not hook:
        return None

    closed = get_closed_candles(
        df,
        M1_TIMEFRAME
    )

    if closed.empty:
        return None

    highs, lows = find_pivots(closed)

    pivots = combined_pivots(
        highs,
        lows
    )

    candidates = []

    for i in range(len(pivots) - 5):
        h1 = pivots[i]
        l1 = pivots[i + 1]
        h2 = pivots[i + 2]
        l2 = pivots[i + 3]
        h3 = pivots[i + 4]
        f = pivots[i + 5]

        if [
            h1["type"],
            l1["type"],
            h2["type"],
            l2["type"],
            h3["type"],
            f["type"]
        ] != [
            "H",
            "L",
            "H",
            "L",
            "H",
            "H"
        ]:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # We DO NOT require:
        #
        # H2 > H1
        # H3 > H2
        #
        # M1 123F is allowed to have non-higher highs.
        # ----------------------------------------------------

        if f["price"] <= h3["price"]:
            continue

        if f["time"] <= hook["hook_time"]:
            continue

        age_minutes = (
            now_ts() - f["time"]
        ) / 60.0

        if age_minutes > M1_F_MAX_AGE_MINUTES:
            continue

        swing_pct = pct_change(
            h1["price"],
            f["price"]
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        structure_distance = pct_change(
            h3["price"],
            f["price"]
        )

        if (
            structure_distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        candidates.append({
            "pattern": "123F_SHORT",

            "h1": h1,
            "l1": l1,
            "h2": h2,
            "l2": l2,
            "h3": h3,
            "f": f,

            "f_time": f["time"],
            "f_price": f["price"],

            "direction": "SHORT"
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["f_time"],
        reverse=True
    )

    return candidates[0]


# ============================================================
# M1 LONG 123F
#
# IMPORTANT:
# L2 < L1 and L3 < L2 are NOT REQUIRED.
#
# Only alternating structure and F < L3 are required.
# ============================================================

def detect_m1_long_123f(
    df,
    hook
):
    if df is None or df.empty or not hook:
        return None

    closed = get_closed_candles(
        df,
        M1_TIMEFRAME
    )

    if closed.empty:
        return None

    highs, lows = find_pivots(closed)

    pivots = combined_pivots(
        highs,
        lows
    )

    candidates = []

    for i in range(len(pivots) - 5):
        l1 = pivots[i]
        h1 = pivots[i + 1]
        l2 = pivots[i + 2]
        h2 = pivots[i + 3]
        l3 = pivots[i + 4]
        f = pivots[i + 5]

        if [
            l1["type"],
            h1["type"],
            l2["type"],
            h2["type"],
            l3["type"],
            f["type"]
        ] != [
            "L",
            "H",
            "L",
            "H",
            "L",
            "L"
        ]:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # We DO NOT require:
        #
        # L2 < L1
        # L3 < L2
        #
        # M1 123F is allowed to have non-lower lows.
        # ----------------------------------------------------

        if f["price"] >= l3["price"]:
            continue

        if f["time"] <= hook["hook_time"]:
            continue

        age_minutes = (
            now_ts() - f["time"]
        ) / 60.0

        if age_minutes > M1_F_MAX_AGE_MINUTES:
            continue

        swing_pct = pct_change(
            l1["price"],
            f["price"]
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        structure_distance = pct_change(
            l3["price"],
            f["price"]
        )

        if (
            structure_distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        candidates.append({
            "pattern": "123F_LONG",

            "l1": l1,
            "h1": h1,
            "l2": l2,
            "h2": h2,
            "l3": l3,
            "f": f,

            "f_time": f["time"],
            "f_price": f["price"],

            "direction": "LONG"
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["f_time"],
        reverse=True
    )

    return candidates[0]


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    hook,
    setup,
    current_price
):
    if not hook or not setup:
        return None

    if current_price is None:
        return None

    entry = float(current_price)

    side = setup["direction"]

    if side == "SHORT":

        highs = [
            setup["h1"]["price"],
            setup["h2"]["price"],
            setup["h3"]["price"]
        ]

        base_sl = max(highs)

        sl = (
            base_sl
            * (1.0 + SL_BUFFER_PCT / 100.0)
        )

        tp = float(
            hook["target_price"]
        )

        if not (
            tp < entry
            and sl > entry
        ):
            return None

    else:

        lows = [
            setup["l1"]["price"],
            setup["l2"]["price"],
            setup["l3"]["price"]
        ]

        base_sl = min(lows)

        sl = (
            base_sl
            * (1.0 - SL_BUFFER_PCT / 100.0)
        )

        tp = float(
            hook["target_price"]
        )

        if not (
            tp > entry
            and sl < entry
        ):
            return None

    return {
        "entry": entry,
        "sl": sl,

        "tp1": tp,
        "tp2": tp,
        "tp3": tp
    }


# ============================================================
# DATABASE HOOK STORAGE
# ============================================================

def save_active_hook(
    symbol,
    hook
):
    if not hook:
        return

    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT INTO hooks (
            symbol,
            hook_type,
            hook_time,
            direction,
            h3_time,
            l3_time,
            target_price,
            active,
            updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(symbol)
        DO UPDATE SET
            hook_type = excluded.hook_type,
            hook_time = excluded.hook_time,
            direction = excluded.direction,
            h3_time = excluded.h3_time,
            l3_time = excluded.l3_time,
            target_price = excluded.target_price,
            active = 1,
            updated_at = excluded.updated_at
    """, (
        symbol,
        hook["hook_type"],
        hook["hook_time"],
        hook["direction"],
        hook.get("h3_time"),
        hook.get("l3_time"),
        hook["target_price"],
        now_ts()
    ))

    conn.commit()
    conn.close()


def deactivate_hook(symbol):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        UPDATE hooks
        SET active = 0,
            updated_at = ?
        WHERE symbol = ?
    """, (
        now_ts(),
        symbol
    ))

    conn.commit()
    conn.close()


def load_active_hooks():
    conn = db_connect()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT *
        FROM hooks
        WHERE active = 1
    """).fetchall()

    conn.close()

    return rows


# ============================================================
# PROCESSED EVENTS
# ============================================================

def event_processed(
    symbol,
    event_key
):
    conn = db_connect()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT 1
        FROM processed_events
        WHERE symbol = ?
          AND event_key = ?
        LIMIT 1
    """, (
        symbol,
        event_key
    )).fetchone()

    conn.close()

    return row is not None


def mark_event_processed(
    symbol,
    event_key
):
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        INSERT OR IGNORE INTO processed_events (
            symbol,
            event_key,
            created_at
        )
        VALUES (?, ?, ?)
    """, (
        symbol,
        event_key,
        now_ts()
    ))

    conn.commit()
    conn.close()


# ============================================================
# SIGNAL DUPLICATE / COOLDOWN
# ============================================================

def cooldown_active(
    symbol,
    side
):
    conn = db_connect()
    cur = conn.cursor()

    cutoff = (
        now_ts()
        - SIGNAL_COOLDOWN_MINUTES * 60
    )

    row = cur.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND created_at >= ?
        ORDER BY created_at DESC
        LIMIT 1
    """, (
        symbol,
        side,
        cutoff
    )).fetchone()

    conn.close()

    return row is not None


def open_trade_count():
    conn = db_connect()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """).fetchone()

    conn.close()

    return int(row[0])


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    symbol,
    hook,
    setup,
    levels
):
    if open_trade_count() >= MAX_OPEN_TRADES:
        return None

    side = setup["direction"]

    if cooldown_active(
        symbol,
        side
    ):
        return None

    f_time = setup["f_time"]

    conn = db_connect()
    cur = conn.cursor()

    try:
        cur.execute("""
            INSERT INTO signals (
                symbol,
                side,
                hook_type,
                hook_time,
                f_time,
                entry,
                sl,
                tp1,
                tp2,
                tp3,
                status,
                current_price,
                pnl_pct,
                opened_at,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN',
                    ?, 0, ?, ?)
        """, (
            symbol,
            side,
            hook["hook_type"],
            hook["hook_time"],
            f_time,

            levels["entry"],
            levels["sl"],

            levels["tp1"],
            levels["tp2"],
            levels["tp3"],

            levels["entry"],
            now_ts(),
            now_ts()
        ))

        signal_id = cur.lastrowid

        conn.commit()

    except sqlite3.IntegrityError:
        conn.rollback()
        signal_id = None

    finally:
        conn.close()

    return signal_id


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(text, image_path=None):
    if not telegram_enabled():
        return False

    try:
        if image_path and os.path.exists(image_path):

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendPhoto"
            )

            with open(
                image_path,
                "rb"
            ) as photo:

                response = SESSION.post(
                    url,
                    data={
                        "chat_id": TELEGRAM_CHAT_ID,
                        "caption": text
                    },
                    files={
                        "photo": photo
                    },
                    timeout=HTTP_TIMEOUT
                )

        else:

            url = (
                f"https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            )

            response = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "HTML"
                },
                timeout=HTTP_TIMEOUT
            )

        return response.ok

    except Exception as e:
        print(
            "Telegram error:",
            repr(e)
        )
        return False


# ============================================================
# HOOK MESSAGE
# ============================================================

def active_hook_message(
    symbol,
    hook
):
    if hook["direction"] == "SHORT":

        return (
            f"🪝 <b>NDS M30 HOOK</b>\n"
            f"🔴 {symbol} SHORT\n"
            f"Structure: H1-L1-H2-L2-H3\n"
            f"Higher Highs: YES\n"
            f"H3: {hook['h3']['price']:.8g}\n"
            f"Target: {hook['target_price']:.8g}\n"
            f"Age: "
            f"{(now_ts()-hook['hook_time'])/3600:.1f}h"
        )

    return (
        f"🪝 <b>NDS M30 HOOK</b>\n"
        f"🟢 {symbol} LONG\n"
        f"Structure: L1-H1-L2-H2-L3\n"
        f"Lower Lows: YES\n"
        f"L3: {hook['l3']['price']:.8g}\n"
        f"Target: {hook['target_price']:.8g}\n"
        f"Age: "
        f"{(now_ts()-hook['hook_time'])/3600:.1f}h"
    )


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    symbol,
    hook,
    setup,
    levels
):
    side = setup["direction"]

    emoji = (
        "🟢"
        if side == "LONG"
        else
        "🔴"
    )

    return (
        f"🚨 <b>NDS M1 SIGNAL</b>\n"
        f"{emoji} <b>{symbol} {side}</b>\n\n"
        f"M30 Hook: "
        f"{hook['hook_type']}\n"
        f"M1: 123F\n\n"
        f"<b>Entry:</b> {levels['entry']:.8g}\n"
        f"<b>SL:</b> {levels['sl']:.8g}\n"
        f"<b>TP:</b> {levels['tp1']:.8g}\n\n"
        f"F: {setup['f_price']:.8g}\n"
        f"F time: {fmt_time(setup['f_time'])}"
    )


# ============================================================
# CHART HELPERS
# ============================================================

def draw_candles(
    ax,
    df
):
    width = 0.65

    for i, row in df.iterrows():

        o = row["open"]
        h = row["high"]
        l = row["low"]
        c = row["close"]

        ax.vlines(
            i,
            l,
            h,
            linewidth=0.8
        )

        lower = min(o, c)
        height = abs(c - o)

        if height == 0:
            height = max(
                (h - l) * 0.01,
                1e-12
            )

        rect = plt.Rectangle(
            (
                i - width / 2,
                lower
            ),
            width,
            height,
            fill=False,
            linewidth=0.8
        )

        ax.add_patch(rect)


def pivot_index_map(df):
    return {
        float(t): i
        for i, t in enumerate(df["time"])
    }


def mark_point(
    ax,
    df,
    point,
    label
):
    mapping = pivot_index_map(df)

    t = float(point["time"])

    if t not in mapping:
        return

    x = mapping[t]
    y = point["price"]

    ax.scatter(
        [x],
        [y],
        s=35
    )

    ax.annotate(
        label,
        (
            x,
            y
        ),
        xytext=(4, 6),
        textcoords="offset points",
        fontsize=8
    )


# ============================================================
# ACTIVE HOOK CHART
# ============================================================

def save_active_hook_chart(
    symbol,
    hook,
    df
):
    if not CHARTS_ENABLED:
        return None

    try:
        closed = get_closed_candles(
            df,
            M30_TIMEFRAME
        )

        if closed.empty:
            return None

        closed = closed.tail(
            ACTIVE_HOOK_CHART_CANDLES
        ).reset_index(drop=True)

        fig, ax = plt.subplots(
            figsize=(14, 7)
        )

        draw_candles(
            ax,
            closed
        )

        if hook["direction"] == "SHORT":

            points = [
                ("1", hook["h1"]),
                ("2", hook["l1"]),
                ("3", hook["h2"]),
                ("4", hook["l2"]),
                ("5", hook["h3"])
            ]

            line_points = [
                hook["h1"],
                hook["h2"],
                hook["h3"]
            ]

            label = "M30 POSITIVE HOOK → SHORT"

        else:

            points = [
                ("1", hook["l1"]),
                ("2", hook["h1"]),
                ("3", hook["l2"]),
                ("4", hook["h2"]),
                ("5", hook["l3"])
            ]

            line_points = [
                hook["l1"],
                hook["l2"],
                hook["l3"]
            ]

            label = "M30 NEGATIVE HOOK → LONG"

        for number, point in points:
            mark_point(
                ax,
                closed,
                point,
                number
            )

        mapping = pivot_index_map(
            closed
        )

        xs = []
        ys = []

        for p in line_points:
            if float(p["time"]) in mapping:
                xs.append(
                    mapping[float(p["time"])]
                )
                ys.append(
                    p["price"]
                )

        if len(xs) >= 2:
            ax.plot(
                xs,
                ys,
                linewidth=1.2
            )

        target = hook["target_price"]

        ax.axhline(
            target,
            linestyle="--",
            linewidth=1
        )

        ax.text(
            0.01,
            0.98,
            f"Target: {target:.8g}",
            transform=ax.transAxes,
            verticalalignment="top"
        )

        ax.set_title(
            f"{symbol} | {label}"
        )

        ax.grid(
            alpha=0.15
        )

        path = os.path.join(
            CHART_DIR,
            f"{symbol}_hook_{int(hook['hook_time'])}.png"
        )

        fig.tight_layout()
        fig.savefig(path, dpi=140)
        plt.close(fig)

        return path

    except Exception as e:
        print(
            "Hook chart error:",
            symbol,
            repr(e)
        )
        return None


# ============================================================
# SIGNAL CHART
# ============================================================

def save_signal_chart(
    symbol,
    setup,
    levels,
    df
):
    if not CHARTS_ENABLED:
        return None

    try:
        closed = get_closed_candles(
            df,
            M1_TIMEFRAME
        )

        if closed.empty:
            return None

        closed = closed.tail(
            CHART_CANDLES
        ).reset_index(drop=True)

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        draw_candles(
            ax,
            closed
        )

        if setup["direction"] == "SHORT":

            points = [
                ("1", setup["h1"]),
                ("2", setup["l1"]),
                ("3", setup["h2"]),
                ("4", setup["l2"]),
                ("5", setup["h3"]),
                ("F", setup["f"])
            ]

            structure_points = [
                setup["h1"],
                setup["h2"],
                setup["h3"],
                setup["f"]
            ]

        else:

            points = [
                ("1", setup["l1"]),
                ("2", setup["h1"]),
                ("3", setup["l2"]),
                ("4", setup["h2"]),
                ("5", setup["l3"]),
                ("F", setup["f"])
            ]

            structure_points = [
                setup["l1"],
                setup["l2"],
                setup["l3"],
                setup["f"]
            ]

        for label, point in points:
            mark_point(
                ax,
                closed,
                point,
                label
            )

        mapping = pivot_index_map(
            closed
        )

        xs = []
        ys = []

        for p in structure_points:

            t = float(p["time"])

            if t in mapping:
                xs.append(
                    mapping[t]
                )
                ys.append(
                    p["price"]
                )

        if len(xs) >= 2:
            ax.plot(
                xs,
                ys,
                linewidth=1.3
            )

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        ax.axhline(
            levels["entry"],
            linestyle="-",
            linewidth=1.2
        )

        ax.axhline(
            levels["sl"],
            linestyle="--",
            linewidth=1.2
        )

        ax.axhline(
            levels["tp1"],
            linestyle="--",
            linewidth=1.2
        )

        x_text = max(
            len(closed) - 30,
            1
        )

        ax.text(
            x_text,
            levels["entry"],
            f" ENTRY {levels['entry']:.8g}",
            fontsize=8
        )

        ax.text(
            x_text,
            levels["sl"],
            f" SL {levels['sl']:.8g}",
            fontsize=8
        )

        ax.text(
            x_text,
            levels["tp1"],
            f" TP {levels['tp1']:.8g}",
            fontsize=8
        )

        ax.set_title(
            f"{symbol} | M1 123F → "
            f"{setup['direction']}"
        )

        ax.grid(
            alpha=0.15
        )

        path = os.path.join(
            CHART_DIR,
            f"{symbol}_signal_{int(setup['f_time'])}.png"
        )

        fig.tight_layout()
        fig.savefig(
            path,
            dpi=140
        )

        plt.close(fig)

        return path

    except Exception as e:
        print(
            "Signal chart error:",
            symbol,
            repr(e)
        )

        return None


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(
    assets,
    notify_hooks=True
):
    run_id, started = start_scan_run(
        "M30"
    )

    errors = 0
    active_count = 0

    for symbol in assets:

        try:
            df = fetch_candles(
                symbol,
                M30_TIMEFRAME,
                M30_CANDLES
            )

            hook = determine_active_hook(
                df
            )

            if hook is None:

                deactivate_hook(
                    symbol
                )

                continue

            active_count += 1

            # Event is based on direction + hook time.
            event_key = (
                f"M30_HOOK:"
                f"{hook['direction']}:"
                f"{int(hook['hook_time'])}"
            )

            previous_exists = event_processed(
                symbol,
                event_key
            )

            save_active_hook(
                symbol,
                hook
            )

            if (
                notify_hooks
                and not previous_exists
            ):

                chart = save_active_hook_chart(
                    symbol,
                    hook,
                    df
                )

                send_telegram(
                    active_hook_message(
                        symbol,
                        hook
                    ),
                    chart
                )

                mark_event_processed(
                    symbol,
                    event_key
                )

            time.sleep(
                REQUEST_SLEEP
            )

        except Exception as e:

            errors += 1

            print(
                f"M30 error {symbol}:",
                repr(e)
            )

            time.sleep(
                REQUEST_SLEEP
            )

    finish_scan_run(
        run_id,
        started,
        "M30",
        len(assets),
        active_count,
        0,
        errors
    )

    return active_count


# ============================================================
# BUILD HOOK OBJECT FROM DB
# ============================================================

def reconstruct_hook_from_db(
    row,
    m30_df=None
):
    """
    The DB keeps the key metadata.
    When possible, rebuild the exact pivot structure
    from the current M30 candles.
    """

    if m30_df is None:
        return None

    try:
        closed = get_closed_candles(
            m30_df,
            M30_TIMEFRAME
        )

        highs, lows = find_pivots(
            closed
        )

        pivots = combined_pivots(
            highs,
            lows
        )

        hook_time = float(
            row["hook_time"]
        )

        candidates = [
            p
            for p in pivots
            if abs(
                p["time"] - hook_time
            ) < 1
        ]

        if not candidates:
            return None

        if row["direction"] == "SHORT":
            hook = detect_m30_positive_hook(
                pivots
            )
        else:
            hook = detect_m30_negative_hook(
                pivots
            )

        if not hook:
            return None

        if abs(
            hook["hook_time"] - hook_time
        ) > 1:
            return None

        return hook

    except Exception:
        return None


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(
    assets,
    price_map
):
    run_id, started = start_scan_run(
        "M1"
    )

    errors = 0
    new_signals = 0

    # --------------------------------------------------------
    # Load active hooks ONCE.
    #
    # M30 is NOT refetched for every M1 symbol.
    # --------------------------------------------------------

    active_rows = load_active_hooks()

    active_by_symbol = {
        row["symbol"]: row
        for row in active_rows
    }

    # --------------------------------------------------------
    # Expire old hooks before M1 processing.
    # --------------------------------------------------------

    for symbol, row in active_by_symbol.items():

        age_hours = (
            now_ts()
            - float(row["hook_time"])
        ) / 3600.0

        if age_hours > MAX_HOOK_AGE_HOURS:

            deactivate_hook(
                symbol
            )

            del active_by_symbol[
                symbol
            ]

    # --------------------------------------------------------
    # Global M30 refresh is already done by scan_m30.
    #
    # M1 does not request M30 again.
    # --------------------------------------------------------

    for symbol in assets:

        if symbol not in active_by_symbol:
            continue

        row = active_by_symbol[
            symbol
        ]

        try:

            current_price = price_map.get(
                symbol
            )

            if current_price is None:
                continue

            # ------------------------------------------------
            # M1 candles only.
            # ------------------------------------------------

            df = fetch_candles(
                symbol,
                M1_TIMEFRAME,
                M1_CANDLES
            )

            # ------------------------------------------------
            # Reconstruct M30 hook metadata from DB.
            #
            # This does NOT make an HTTP M30 request.
            # ------------------------------------------------

            hook = None

            # We can rebuild the exact hook from the
            # last M30 data only if cached data exists.
            #
            # To keep M1 independent from M30 HTTP calls,
            # use the DB values directly for target and
            # direction, while M1 detection uses hook_time.
            #
            # Construct a lightweight hook object.
            # ------------------------------------------------

            hook = {
                "hook_type": row["hook_type"],
                "hook_time": float(row["hook_time"]),
                "direction": row["direction"],
                "target_price": float(row["target_price"]),

                "h1": None,
                "l1": None,
                "h2": None,
                "l2": None,
                "h3": None,
                "l3": None
            }

            if row["direction"] == "SHORT":

                setup = detect_m1_short_123f(
                    df,
                    hook
                )

            else:

                setup = detect_m1_long_123f(
                    df,
                    hook
                )

            if not setup:
                continue

            # ------------------------------------------------
            # One event per confirmed F.
            # ------------------------------------------------

            event_key = (
                f"M1_F:"
                f"{setup['direction']}:"
                f"{int(setup['f_time'])}"
            )

            if event_processed(
                symbol,
                event_key
            ):
                continue

            # ------------------------------------------------
            # Calculate levels using CURRENT price.
            # ------------------------------------------------

            levels = calculate_trade_levels(
                hook,
                setup,
                current_price
            )

            if not levels:
                continue

            signal_id = insert_signal(
                symbol,
                hook,
                setup,
                levels
            )

            if signal_id is None:
                continue

            new_signals += 1

            mark_event_processed(
                symbol,
                event_key
            )

            chart = save_signal_chart(
                symbol,
                setup,
                levels,
                df
            )

            send_telegram(
                signal_message(
                    symbol,
                    hook,
                    setup,
                    levels
                ),
                chart
            )

            time.sleep(
                REQUEST_SLEEP
            )

        except Exception as e:

            errors += 1

            print(
                f"M1 error {symbol}:",
                repr(e)
            )

            time.sleep(
                REQUEST_SLEEP
            )

    finish_scan_run(
        run_id,
        started,
        "M1",
        len(assets),
        len(active_by_symbol),
        new_signals,
        errors
    )

    return new_signals


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def calculate_pnl(
    side,
    entry,
    current
):
    if not entry or not current:
        return 0.0

    if side == "LONG":
        return (
            (current - entry)
            / entry
            * 100.0
        )

    return (
        (entry - current)
        / entry
        * 100.0
    )


def close_signal(
    signal_id,
    current_price,
    reason
):
    conn = db_connect()
    cur = conn.cursor()

    row = cur.execute("""
        SELECT *
        FROM signals
        WHERE id = ?
          AND status = 'OPEN'
    """, (
        signal_id,
    )).fetchone()

    if not row:
        conn.close()
        return

    pnl = calculate_pnl(
        row["side"],
        row["entry"],
        current_price
    )

    result = (
        "WIN"
        if pnl > 0
        else "LOSS"
    )

    cur.execute("""
        UPDATE signals
        SET
            status = 'CLOSED',
            current_price = ?,
            pnl_pct = ?,
            result = ?,
            closed_at = ?,
            exit_price = ?,
            exit_reason = ?
        WHERE id = ?
    """, (
        current_price,
        pnl,
        result,
        now_ts(),
        current_price,
        reason,
        signal_id
    ))

    conn.commit()
    conn.close()

    emoji = (
        "🟢"
        if pnl > 0
        else "🔴"
    )

    send_telegram(
        f"🔔 <b>NDS TRADE CLOSED</b>\n"
        f"{emoji} {row['symbol']} "
        f"{row['side']}\n"
        f"Entry: {row['entry']:.8g}\n"
        f"Exit: {current_price:.8g}\n"
        f"P/L: <b>{pnl:+.2f}%</b>\n"
        f"Reason: {reason}"
    )


def manage_open_trades(price_map):
    conn = db_connect()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
    """).fetchall()

    conn.close()

    for row in rows:

        symbol = row["symbol"]

        current = price_map.get(
            symbol
        )

        if current is None:
            continue

        side = row["side"]

        pnl = calculate_pnl(
            side,
            row["entry"],
            current
        )

        should_close = False
        reason = None

        if side == "LONG":

            if current <= row["sl"]:
                should_close = True
                reason = "SL"

            elif current >= row["tp1"]:
                should_close = True
                reason = "TP"

        else:

            if current >= row["sl"]:
                should_close = True
                reason = "SL"

            elif current <= row["tp1"]:
                should_close = True
                reason = "TP"

        if should_close:

            close_signal(
                row["id"],
                current,
                reason
            )

        else:

            conn = db_connect()
            cur = conn.cursor()

            cur.execute("""
                UPDATE signals
                SET
                    current_price = ?,
                    pnl_pct = ?
                WHERE id = ?
                  AND status = 'OPEN'
            """, (
                current,
                pnl,
                row["id"]
            ))

            conn.commit()
            conn.close()


# ============================================================
# REPORT DATA
# ============================================================

def get_report_stats():
    conn = db_connect()
    cur = conn.cursor()

    open_count = cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """).fetchone()[0]

    closed_count = cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    wins = cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
          AND result = 'WIN'
    """).fetchone()[0]

    losses = cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
          AND result = 'LOSS'
    """).fetchone()[0]

    pnl = cur.execute("""
        SELECT COALESCE(SUM(pnl_pct), 0)
        FROM signals
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    conn.close()

    return {
        "open": int(open_count),
        "closed": int(closed_count),
        "wins": int(wins),
        "losses": int(losses),
        "pnl": float(pnl or 0)
    }


def get_open_trades():
    conn = db_connect()
    cur = conn.cursor()

    rows = cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY opened_at DESC
    """).fetchall()

    conn.close()

    return rows


# ============================================================
# REPORT
# ============================================================

def send_report(
    assets_count,
    new_signals_interval
):
    stats = get_report_stats()

    hooks = load_active_hooks()

    text = (
        f"📊 <b>NDS M30→M1 REPORT</b>\n\n"
        f"Assets scanned: <b>{assets_count}</b>\n"
        f"Active Hooks: <b>{len(hooks)}</b>\n"
        f"New Signals: <b>{new_signals_interval}</b>\n\n"
        f"OPEN: <b>{stats['open']}</b>\n"
        f"Closed: {stats['closed']}\n"
        f"Wins: {stats['wins']}\n"
        f"Losses: {stats['losses']}\n"
        f"Total P/L: <b>{stats['pnl']:+.2f}%</b>"
    )

    open_trades = get_open_trades()

    if open_trades:

        text += "\n\n<b>OPEN TRADES</b>"

        for row in open_trades:

            current = row["current_price"]

            if current is None:
                current = row["entry"]

            text += (
                f"\n\n{row['symbol']} "
                f"{row['side']}\n"
                f"Entry: {row['entry']:.8g}\n"
                f"Current: {current:.8g}\n"
                f"P/L: "
                f"<b>{row['pnl_pct']:+.2f}%</b>\n"
                f"SL: {row['sl']:.8g}\n"
                f"TP: {row['tp1']:.8g}"
            )

    send_telegram(
        text
    )


# ============================================================
# STARTUP MESSAGE
# ============================================================

def send_startup():
    send_telegram(
        f"🚀 <b>NDS M30→M1 SCANNER</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Mode: <b>PAPER ONLY</b>\n\n"
        f"M30 LONG: Lower Lows required\n"
        f"M30 SHORT: Higher Highs required\n"
        f"M1 123F: No LL/HH requirement\n"
        f"Entry: Current price after F"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    if not PAPER_ONLY:
        raise RuntimeError(
            "PAPER_ONLY must remain True."
        )

    init_db()

    print("=" * 60)
    print(
        f"NDS M30 -> M1 LIVE SCANNER "
        f"VERSION {VERSION}"
    )
    print("PAPER ONLY")
    print("=" * 60)

    send_startup()

    assets = []

    last_asset_refresh = 0

    last_m30_scan = 0
    last_m1_scan = 0
    last_report = now_ts()

    report_new_signals = 0

    while True:

        try:

            current_time = now_ts()

            # =================================================
            # ASSET REFRESH
            # =================================================

            if (
                not assets
                or current_time - last_asset_refresh
                >= ASSET_REFRESH_SECONDS
            ):

                try:

                    new_assets = discover_assets()

                    if new_assets:
                        assets = new_assets
                        last_asset_refresh = current_time

                        print(
                            f"Assets refreshed: "
                            f"{len(assets)}"
                        )

                except Exception as e:

                    print(
                        "Asset refresh error:",
                        repr(e)
                    )

            # =================================================
            # M30
            # =================================================

            if (
                assets
                and (
                    current_time - last_m30_scan
                    >= M30_SCAN_SECONDS
                )
            ):

                print(
                    f"[{fmt_time(current_time)}] "
                    f"M30 scan..."
                )

                active_count = scan_m30(
                    assets,
                    notify_hooks=True
                )

                print(
                    f"M30 active hooks: "
                    f"{active_count}"
                )

                last_m30_scan = current_time

            # =================================================
            # PRICE MAP
            # =================================================

            price_map = {}

            try:
                price_map = fetch_price_map()
            except Exception as e:
                print(
                    "Price map error:",
                    repr(e)
                )

            # =================================================
            # MANAGE OPEN TRADES
            # =================================================

            if price_map:
                manage_open_trades(
                    price_map
                )

            # =================================================
            # M1
            # =================================================

            if (
                assets
                and (
                    current_time - last_m1_scan
                    >= M1_SCAN_SECONDS
                )
            ):

                print(
                    f"[{fmt_time(current_time)}] "
                    f"M1 scan..."
                )

                new_count = scan_m1(
                    assets,
                    price_map
                )

                report_new_signals += (
                    new_count
                )

                print(
                    f"M1 new signals: "
                    f"{new_count}"
                )

                last_m1_scan = current_time

            # =================================================
            # REPORT
            # =================================================

            if (
                current_time - last_report
                >= REPORT_INTERVAL_SECONDS
            ):

                send_report(
                    len(assets),
                    report_new_signals
                )

                report_new_signals = 0
                last_report = current_time

            # =================================================
            # LOOP
            # =================================================

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "Scanner stopped."
            )

            break

        except Exception as e:

            print(
                "MAIN LOOP ERROR:",
                repr(e)
            )

            traceback.print_exc()

            time.sleep(
                ERROR_SLEEP
            )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
