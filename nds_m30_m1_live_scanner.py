# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.0
# ============================================================
#
# PAPER ONLY
#
# NDS STRUCTURE
#
# M30:
#
# SHORT HOOK:
#
#       H3
#      /  \
#     H2   \
#    /      \
#   H1       \____ 86.4%
#  / \             \
# /   \             \
# S     L1           ...
#        \
#         L2
#
# Exact logical sequence:
#
# Hook Start -> H1 -> L1 -> H2 -> L2 -> H3
#
# Requirements:
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# Then:
#   H3 -> correction -> 86.4%
#
# Only AFTER the 86.4% correction is reached:
#   HOOK = CONFIRMED
#
# M1:
#
# SHORT:
#   1 -> correction -> 2 -> correction -> 3 -> correction -> F
#   1 < 2 < 3 < F
#
# LONG is mirrored:
#   1 > 2 > 3 > F
#
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.4.0"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v43.db"

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

KRAKEN_API_URL = (
    "https://futures.kraken.com/derivatives/api/v3"
)

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

M30_INTERVAL = "30m"
M1_INTERVAL = "1m"

M30_CANDLES = 320
M1_CANDLES = 1500

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# Minimum total hook movement
M30_MIN_HOOK_RANGE_PCT = 0.20

# Minimum M1 swing
M1_MIN_SWING_PCT = 0.07

# Maximum distance between H3 and M1 structure
M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

# F must be fresh
MAX_F_AGE_SECONDS = 5 * 60

# Hook H3 can remain eligible this long
MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

# 86.4% retracement
RETRACEMENT_864 = 0.864

# Signal / trade control
MAX_OPEN_TRADES = 3
SIGNAL_COOLDOWN_SECONDS = 60 * 60

# Keep zero because the NDS definition says SL is the
# previous valid M30 peak/low itself.
SL_BUFFER_PCT = 0.0

# Runtime
M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60
REPORT_SECONDS = 900

RUN_SECONDS = 12 * 60

# Charts
CHARTS_ENABLED = True
CHART_CANDLES_M30 = 180
CHART_CANDLES_M1 = 240

CHART_DIR = "nds_charts"

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": "NDS-M30-M1-Scanner/4.4.0"
}


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "m30_requests": 0,
    "m30_data_ok": 0,
    "m30_short_data": 0,
    "m30_pivot_highs": 0,
    "m30_pivot_lows": 0,

    "m30_hook_candidates": 0,
    "m30_incomplete_hooks": 0,
    "m30_confirmed_hooks": 0,

    "m1_requests": 0,
    "m1_data_ok": 0,
    "m1_short_data": 0,

    "m1_123f_short": 0,
    "m1_123f_long": 0,

    "new_signals": 0,

    "charts_created": 0,
    "telegram_photos": 0,

    "errors": 0,
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def utc_now_ts():
    return int(time.time())


def utc_string(ts=None):
    if ts is None:
        ts = utc_now_ts()

    return datetime.fromtimestamp(
        ts,
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)

    except Exception:
        return default


def pct_change(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if a is None or b is None or a == 0:
        return 0.0

    return abs((b - a) / a) * 100.0


def format_price(value):
    value = safe_float(value)

    if value is None:
        return "-"

    if abs(value) >= 1000:
        return f"{value:.2f}"

    if abs(value) >= 1:
        return f"{value:.4f}"

    if abs(value) >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def format_pct(value):
    value = safe_float(value, 0.0)

    sign = "+" if value > 0 else ""

    return f"{sign}{value:.2f}%"


def get_side_name(side):
    return "LONG" if side.upper() == "LONG" else "SHORT"


def normalize_symbol(symbol):
    if not symbol:
        return ""

    return str(symbol).upper().strip()


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


def ensure_column(conn, table, column, definition):
    cur = conn.cursor()

    cur.execute(
        f"PRAGMA table_info({table})"
    )

    columns = {
        row["name"]
        for row in cur.fetchall()
    }

    if column not in columns:
        cur.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )

    conn.commit()


def init_db():
    conn = db_connect()

    cur = conn.cursor()

    # --------------------------------------------------------
    # scanner_runs
    # --------------------------------------------------------

    cur.execute("""
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

    # --------------------------------------------------------
    # hooks
    # --------------------------------------------------------

    cur.execute("""
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

    # --------------------------------------------------------
    # signals
    # --------------------------------------------------------

    cur.execute("""
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

    conn.commit()

    # --------------------------------------------------------
    # migrations
    # --------------------------------------------------------

    hook_columns = [
        ("hook_start_time", "INTEGER"),
        ("hook_start", "REAL"),
        ("retracement_864", "REAL"),
        ("confirmed_time", "INTEGER"),
        ("previous_valid_peak", "REAL"),
        ("previous_valid_low", "REAL"),
        ("strategy_version", "TEXT"),
    ]

    for column, definition in hook_columns:
        ensure_column(
            conn,
            "hooks",
            column,
            definition
        )

    signal_columns = [
        ("strategy_version", "TEXT"),
        ("entry_time", "INTEGER"),
        ("last_price", "REAL"),
        ("last_pnl_pct", "REAL"),
    ]

    for column, definition in signal_columns:
        ensure_column(
            conn,
            "signals",
            column,
            definition
        )

    conn.close()


# ============================================================
# LEGACY HOOK HANDLING
# ============================================================

def deactivate_legacy_hooks():
    """
    Old versions did not require the 86.4% confirmation.
    Therefore old active hooks cannot safely be treated as
    confirmed hooks by the new detector.
    """

    conn = db_connect()

    try:
        conn.execute("""
            UPDATE hooks
            SET active = 0,
                invalidated = 1
            WHERE active = 1
              AND (
                    strategy_version IS NULL
                    OR strategy_version != ?
                  )
        """, (VERSION,))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(
        TELEGRAM_BOT_TOKEN
        and TELEGRAM_CHAT_ID
    )


def send_telegram(message):
    if not telegram_enabled():
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception:
        DIAG["errors"] += 1
        return False


def send_telegram_photo(path, caption=""):
    if not telegram_enabled():
        return False

    if not path or not os.path.exists(path):
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
        )

        with open(path, "rb") as photo:
            response = requests.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "parse_mode": "HTML",
                },
                files={
                    "photo": photo
                },
                timeout=REQUEST_TIMEOUT,
            )

        if response.ok:
            DIAG["telegram_photos"] += 1

        return response.ok

    except Exception:
        DIAG["errors"] += 1
        return False


# ============================================================
# KRAKEN API
# ============================================================

def discover_assets():
    """
    Get PF_*USD perpetual contracts.
    """

    try:
        url = (
            f"{KRAKEN_API_URL}/instruments"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        instruments = data.get(
            "instruments",
            []
        )

        assets = []

        for item in instruments:
            symbol = (
                item.get("symbol")
                or item.get("instrument")
                or ""
            )

            symbol = normalize_symbol(symbol)

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            if symbol == "PF_SNXXUSD":
                continue

            assets.append(symbol)

        # User requested LDO.
        if "PF_LDOUSD" in assets:
            assets.remove("PF_LDOUSD")
            assets.insert(0, "PF_LDOUSD")

        assets = sorted(
            set(assets)
        )

        if "PF_LDOUSD" in assets:
            assets.remove("PF_LDOUSD")
            assets.insert(0, "PF_LDOUSD")

        return assets[:TARGET_ASSETS]

    except Exception:
        DIAG["errors"] += 1
        return []


def parse_candles(data):
    """
    Kraken chart endpoint formats can vary slightly.
    Normalize them to:

    {
        time,
        open,
        high,
        low,
        close,
        volume
    }
    """

    rows = []

    if not isinstance(data, dict):
        return rows

    raw = (
        data.get("candles")
        or data.get("data")
        or []
    )

    if not isinstance(raw, list):
        return rows

    for item in raw:

        if isinstance(item, dict):

            ts = (
                item.get("time")
                or item.get("timestamp")
                or item.get("ts")
            )

            op = (
                item.get("open")
                or item.get("o")
            )

            hi = (
                item.get("high")
                or item.get("h")
            )

            lo = (
                item.get("low")
                or item.get("l")
            )

            cl = (
                item.get("close")
                or item.get("c")
            )

            vol = (
                item.get("volume")
                or item.get("v")
                or 0
            )

        elif isinstance(item, (list, tuple)):

            if len(item) < 5:
                continue

            ts = item[0]
            op = item[1]
            hi = item[2]
            lo = item[3]
            cl = item[4]

            vol = item[5] if len(item) > 5 else 0

        else:
            continue

        ts = safe_float(ts)
        op = safe_float(op)
        hi = safe_float(hi)
        lo = safe_float(lo)
        cl = safe_float(cl)
        vol = safe_float(vol, 0)

        if ts is None:
            continue

        if ts > 10_000_000_000:
            ts /= 1000.0

        if None in (
            op,
            hi,
            lo,
            cl
        ):
            continue

        rows.append({
            "time": int(ts),
            "open": op,
            "high": hi,
            "low": lo,
            "close": cl,
            "volume": vol,
        })

    rows.sort(
        key=lambda x: x["time"]
    )

    return rows


def fetch_candles(symbol, interval, limit):
    try:
        url = (
            f"{KRAKEN_CHART_URL}/"
            f"{symbol}/"
            f"{interval}"
        )

        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        candles = parse_candles(data)

        if limit and len(candles) > limit:
            candles = candles[-limit:]

        return candles

    except Exception:
        DIAG["errors"] += 1
        return []


def get_closed_candles(symbol, interval, limit):
    candles = fetch_candles(
        symbol,
        interval,
        limit + 5
    )

    if not candles:
        return []

    now = utc_now_ts()

    if interval == "1m":
        duration = 60
    elif interval == "5m":
        duration = 300
    elif interval == "15m":
        duration = 900
    elif interval == "30m":
        duration = 1800
    elif interval == "1h":
        duration = 3600
    else:
        duration = 60

    closed = []

    for candle in candles:

        if candle["time"] + duration <= now:
            closed.append(candle)

    return closed[-limit:]


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(candles, left=PIVOT_LEFT, right=PIVOT_RIGHT):
    pivots = []

    if len(candles) < left + right + 1:
        return pivots

    for i in range(left, len(candles) - right):

        price = candles[i]["high"]

        is_pivot = True

        for j in range(i - left, i):
            if candles[j]["high"] >= price:
                is_pivot = False
                break

        if not is_pivot:
            continue

        for j in range(i + 1, i + right + 1):
            if candles[j]["high"] >= price:
                is_pivot = False
                break

        if is_pivot:
            pivots.append({
                "index": i,
                "time": candles[i]["time"],
                "price": price,
                "type": "H",
            })

    return pivots


def find_pivot_lows(candles, left=PIVOT_LEFT, right=PIVOT_RIGHT):
    pivots = []

    if len(candles) < left + right + 1:
        return pivots

    for i in range(left, len(candles) - right):

        price = candles[i]["low"]

        is_pivot = True

        for j in range(i - left, i):
            if candles[j]["low"] <= price:
                is_pivot = False
                break

        if not is_pivot:
            continue

        for j in range(i + 1, i + right + 1):
            if candles[j]["low"] <= price:
                is_pivot = False
                break

        if is_pivot:
            pivots.append({
                "index": i,
                "time": candles[i]["time"],
                "price": price,
                "type": "L",
            })

    return pivots


def merge_pivots(highs, lows):
    result = list(highs) + list(lows)

    result.sort(
        key=lambda x: (
            x["time"],
            0 if x["type"] == "L" else 1
        )
    )

    return result


# ============================================================
# PIVOT SEQUENCE HELPERS
# ============================================================

def previous_pivot_before(pivots, timestamp, pivot_type=None):
    candidates = []

    for p in pivots:

        if p["time"] >= timestamp:
            continue

        if pivot_type and p["type"] != pivot_type:
            continue

        candidates.append(p)

    if not candidates:
        return None

    return candidates[-1]


def next_pivot_after(pivots, timestamp, pivot_type=None):
    for p in pivots:

        if p["time"] <= timestamp:
            continue

        if pivot_type and p["type"] != pivot_type:
            continue

        return p

    return None


def get_pivots_between(
    pivots,
    start_time,
    end_time,
    pivot_type=None
):
    result = []

    for p in pivots:

        if p["time"] <= start_time:
            continue

        if p["time"] >= end_time:
            continue

        if pivot_type and p["type"] != pivot_type:
            continue

        result.append(p)

    return result


def choose_nearest_low_before(
    lows,
    timestamp
):
    candidates = [
        p for p in lows
        if p["time"] < timestamp
    ]

    if not candidates:
        return None

    return candidates[-1]


def choose_nearest_high_before(
    highs,
    timestamp
):
    candidates = [
        p for p in highs
        if p["time"] < timestamp
    ]

    if not candidates:
        return None

    return candidates[-1]


# ============================================================
# 86.4% RETRACEMENT
# ============================================================

def calculate_864_short(hook_start, h3):
    """
    Bullish move:
        Hook Start -> H3

    86.4% downward retracement:

        H3 - 0.864 * (H3 - Start)
    """

    return (
        h3
        - RETRACEMENT_864 * (h3 - hook_start)
    )


def calculate_864_long(hook_start, l3):
    """
    Bearish move:
        Hook Start -> L3

    86.4% upward retracement:

        L3 + 0.864 * (Start - L3)
    """

    return (
        l3
        + RETRACEMENT_864 * (hook_start - l3)
    )


def reached_864_short(candles, h3_time, target):
    """
    After H3, price must actually reach/cross the 86.4%
    retracement level.

    Exact equality is not required because market prices
    rarely have the courtesy to land on our decimal.
    """

    for candle in candles:

        if candle["time"] <= h3_time:
            continue

        if candle["low"] <= target:
            return candle

    return None


def reached_864_long(candles, l3_time, target):

    for candle in candles:

        if candle["time"] <= l3_time:
            continue

        if candle["high"] >= target:
            return candle

    return None


# ============================================================
# M30 SHORT HOOK DETECTION
# ============================================================

def detect_m30_short_hook(candles):
    """
    Strict structure:

    Hook Start -> H1 -> L1 -> H2 -> L2 -> H3

    Conditions:

        H2 > H1
        L2 < L1
        H3 > H2

    Then:

        H3 -> 86.4% retracement

    Only then confirmed.
    """

    if len(candles) < 80:
        return None

    highs = find_pivot_highs(candles)
    lows = find_pivot_lows(candles)

    DIAG["m30_pivot_highs"] += len(highs)
    DIAG["m30_pivot_lows"] += len(lows)

    if len(highs) < 3 or len(lows) < 2:
        return None

    pivots = merge_pivots(
        highs,
        lows
    )

    best = None

    # --------------------------------------------------------
    # H1
    # --------------------------------------------------------

    for h1_index, h1 in enumerate(highs):

        # Hook Start = nearest valid low before H1
        hook_start = choose_nearest_low_before(
            lows,
            h1["time"]
        )

        if not hook_start:
            continue

        # Previous valid peak before Hook Start
        previous_peak = choose_nearest_high_before(
            highs,
            hook_start["time"]
        )

        # Need L1 after H1
        l1_candidates = [
            p for p in lows
            if p["time"] > h1["time"]
        ]

        if not l1_candidates:
            continue

        l1 = l1_candidates[0]

        # There should not be another HIGH before L1.
        between_h1_l1 = get_pivots_between(
            pivots,
            h1["time"],
            l1["time"]
        )

        if any(
            p["type"] == "H"
            for p in between_h1_l1
        ):
            continue

        # ----------------------------------------------------
        # H2
        # ----------------------------------------------------

        h2_candidates = [
            p for p in highs
            if p["time"] > l1["time"]
        ]

        if not h2_candidates:
            continue

        h2 = h2_candidates[0]

        # No extra LOW between L1 and H2
        between_l1_h2 = get_pivots_between(
            pivots,
            l1["time"],
            h2["time"]
        )

        if any(
            p["type"] == "L"
            for p in between_l1_h2
        ):
            continue

        if h2["price"] <= h1["price"]:
            continue

        # ----------------------------------------------------
        # L2
        # ----------------------------------------------------

        l2_candidates = [
            p for p in lows
            if p["time"] > h2["time"]
        ]

        if not l2_candidates:
            continue

        l2 = l2_candidates[0]

        between_h2_l2 = get_pivots_between(
            pivots,
            h2["time"],
            l2["time"]
        )

        if any(
            p["type"] == "H"
            for p in between_h2_l2
        ):
            continue

        if l2["price"] >= l1["price"]:
            continue

        # ----------------------------------------------------
        # H3
        # ----------------------------------------------------

        h3_candidates = [
            p for p in highs
            if p["time"] > l2["time"]
        ]

        if not h3_candidates:
            continue

        h3 = h3_candidates[0]

        between_l2_h3 = get_pivots_between(
            pivots,
            l2["time"],
            h3["time"]
        )

        if any(
            p["type"] == "L"
            for p in between_l2_h3
        ):
            continue

        if h3["price"] <= h2["price"]:
            continue

        # ----------------------------------------------------
        # Hook total movement
        # ----------------------------------------------------

        movement_pct = pct_change(
            hook_start["price"],
            h3["price"]
        )

        if movement_pct < M30_MIN_HOOK_RANGE_PCT:
            continue

        DIAG["m30_hook_candidates"] += 1

        # ----------------------------------------------------
        # 86.4%
        # ----------------------------------------------------

        retracement_864 = calculate_864_short(
            hook_start["price"],
            h3["price"]
        )

        # H3 must be followed by actual correction.
        correction = reached_864_short(
            candles,
            h3["time"],
            retracement_864
        )

        if correction is None:

            DIAG["m30_incomplete_hooks"] += 1

            # H3 exists but Hook is NOT confirmed.
            continue

        # If correction goes beyond Hook Start before the
        # confirmation, reject the structure.
        exceeded_start = False

        for candle in candles:

            if candle["time"] <= h3["time"]:
                continue

            if candle["time"] > correction["time"]:
                break

            if candle["low"] < hook_start["price"]:
                exceeded_start = True
                break

        if exceeded_start:
            continue

        # ----------------------------------------------------
        # L3 = correction low reaching 86.4
        # ----------------------------------------------------

        l3 = {
            "time": correction["time"],
            "price": correction["low"],
            "type": "L"
        }

        candidate = {
            "side": "SHORT",

            "hook_start_time": hook_start["time"],
            "hook_start": hook_start["price"],

            "h1_time": h1["time"],
            "h1": h1["price"],

            "l1_time": l1["time"],
            "l1": l1["price"],

            "h2_time": h2["time"],
            "h2": h2["price"],

            "l2_time": l2["time"],
            "l2": l2["price"],

            "h3_time": h3["time"],
            "h3": h3["price"],

            "l3_time": l3["time"],
            "l3": l3["price"],

            "target": retracement_864,

            "retracement_864": retracement_864,

            "previous_valid_peak": (
                previous_peak["price"]
                if previous_peak
                else h1["price"]
            ),

            "previous_valid_low": hook_start["price"],

            "confirmed_time": correction["time"],

            "hook_time": h3["time"],
        }

        # Prefer latest H3 structure.
        if (
            best is None
            or candidate["h3_time"] > best["h3_time"]
        ):
            best = candidate

    if best:
        DIAG["m30_confirmed_hooks"] += 1

    return best


# ============================================================
# M30 LONG HOOK DETECTION
# ============================================================

def detect_m30_long_hook(candles):
    """
    Mirror structure:

    Hook Start -> L1 -> H1 -> L2 -> H2 -> L3

    Conditions:

        L2 < L1
        H2 > H1
        L3 < L2

    Then:

        L3 -> 86.4% upward retracement
    """

    if len(candles) < 80:
        return None

    highs = find_pivot_highs(candles)
    lows = find_pivot_lows(candles)

    pivots = merge_pivots(
        highs,
        lows
    )

    if len(highs) < 2 or len(lows) < 3:
        return None

    best = None

    for l1 in lows:

        hook_start = choose_nearest_high_before(
            highs,
            l1["time"]
        )

        if not hook_start:
            continue

        previous_low = choose_nearest_low_before(
            lows,
            hook_start["time"]
        )

        h1_candidates = [
            p for p in highs
            if p["time"] > l1["time"]
        ]

        if not h1_candidates:
            continue

        h1 = h1_candidates[0]

        between_l1_h1 = get_pivots_between(
            pivots,
            l1["time"],
            h1["time"]
        )

        if any(
            p["type"] == "L"
            for p in between_l1_h1
        ):
            continue

        l2_candidates = [
            p for p in lows
            if p["time"] > h1["time"]
        ]

        if not l2_candidates:
            continue

        l2 = l2_candidates[0]

        between_h1_l2 = get_pivots_between(
            pivots,
            h1["time"],
            l2["time"]
        )

        if any(
            p["type"] == "H"
            for p in between_h1_l2
        ):
            continue

        if l2["price"] >= l1["price"]:
            continue

        h2_candidates = [
            p for p in highs
            if p["time"] > l2["time"]
        ]

        if not h2_candidates:
            continue

        h2 = h2_candidates[0]

        between_l2_h2 = get_pivots_between(
            pivots,
            l2["time"],
            h2["time"]
        )

        if any(
            p["type"] == "L"
            for p in between_l2_h2
        ):
            continue

        if h2["price"] <= h1["price"]:
            continue

        l3_candidates = [
            p for p in lows
            if p["time"] > h2["time"]
        ]

        if not l3_candidates:
            continue

        l3 = l3_candidates[0]

        between_h2_l3 = get_pivots_between(
            pivots,
            h2["time"],
            l3["time"]
        )

        if any(
            p["type"] == "H"
            for p in between_h2_l3
        ):
            continue

        if l3["price"] >= l2["price"]:
            continue

        movement_pct = pct_change(
            hook_start["price"],
            l3["price"]
        )

        if movement_pct < M30_MIN_HOOK_RANGE_PCT:
            continue

        DIAG["m30_hook_candidates"] += 1

        retracement_864 = calculate_864_long(
            hook_start["price"],
            l3["price"]
        )

        correction = reached_864_long(
            candles,
            l3["time"],
            retracement_864
        )

        if correction is None:

            DIAG["m30_incomplete_hooks"] += 1

            continue

        exceeded_start = False

        for candle in candles:

            if candle["time"] <= l3["time"]:
                continue

            if candle["time"] > correction["time"]:
                break

            if candle["high"] > hook_start["price"]:
                exceeded_start = True
                break

        if exceeded_start:
            continue

        h3 = {
            "time": correction["time"],
            "price": correction["high"],
            "type": "H"
        }

        candidate = {
            "side": "LONG",

            "hook_start_time": hook_start["time"],
            "hook_start": hook_start["price"],

            "l1_time": l1["time"],
            "l1": l1["price"],

            "h1_time": h1["time"],
            "h1": h1["price"],

            "l2_time": l2["time"],
            "l2": l2["price"],

            "h2_time": h2["time"],
            "h2": h2["price"],

            "l3_time": l3["time"],
            "l3": l3["price"],

            "h3_time": h3["time"],
            "h3": h3["price"],

            "target": retracement_864,

            "retracement_864": retracement_864,

            "previous_valid_peak": hook_start["price"],

            "previous_valid_low": (
                previous_low["price"]
                if previous_low
                else l1["price"]
            ),

            "confirmed_time": correction["time"],

            "hook_time": l3["time"],
        }

        if (
            best is None
            or candidate["l3_time"] > best["l3_time"]
        ):
            best = candidate

    if best:
        DIAG["m30_confirmed_hooks"] += 1

    return best


# ============================================================
# MAIN M30 DETECTOR
# ============================================================

def detect_m30_hook(candles):
    short_hook = detect_m30_short_hook(
        candles
    )

    long_hook = detect_m30_long_hook(
        candles
    )

    candidates = [
        x for x in (
            short_hook,
            long_hook
        )
        if x
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda x: x["confirmed_time"]
    )


# ============================================================
# HOOK DATABASE
# ============================================================

def hook_event_key(hook):
    return (
        f'{hook["side"]}_'
        f'{hook["hook_start_time"]}_'
        f'{hook["h3_time"]}'
    )


def save_hook(symbol, hook):
    conn = db_connect()

    try:
        key = hook_event_key(hook)

        existing = conn.execute("""
            SELECT *
            FROM hooks
            WHERE symbol = ?
              AND side = ?
              AND hook_start_time = ?
              AND h3_time = ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            symbol,
            hook["side"],
            hook["hook_start_time"],
            hook["h3_time"],
        )).fetchone()

        if existing:
            return existing["id"]

        # One active confirmed Hook per symbol.
        conn.execute("""
            UPDATE hooks
            SET active = 0
            WHERE symbol = ?
              AND active = 1
        """, (symbol,))

        cur = conn.cursor()

        cur.execute("""
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

                created_at,

                hook_start_time,
                hook_start,

                retracement_864,
                confirmed_time,

                previous_valid_peak,
                previous_valid_low,

                strategy_version
            )
            VALUES (
                ?, ?,

                ?,

                ?, ?, ?,

                ?, ?, ?,

                ?, ?, ?,

                ?, ?, ?,

                ?,

                1,
                0,

                ?,

                ?, ?,

                ?, ?,

                ?, ?,

                ?
            )
        """, (
            symbol,
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

            utc_now_ts(),

            hook["hook_start_time"],
            hook["hook_start"],

            hook["retracement_864"],
            hook["confirmed_time"],

            hook.get("previous_valid_peak"),
            hook.get("previous_valid_low"),

            VERSION,
        ))

        conn.commit()

        return cur.lastrowid

    finally:
        conn.close()


def get_active_hook(symbol):
    conn = db_connect()

    try:
        return conn.execute("""
            SELECT *
            FROM hooks
            WHERE symbol = ?
              AND active = 1
              AND invalidated = 0
            ORDER BY id DESC
            LIMIT 1
        """, (symbol,)).fetchone()

    finally:
        conn.close()


def get_hook_by_id(hook_id):
    conn = db_connect()

    try:
        return conn.execute("""
            SELECT *
            FROM hooks
            WHERE id = ?
            LIMIT 1
        """, (hook_id,)).fetchone()

    finally:
        conn.close()


def deactivate_hook(hook_id, invalidated=1):
    conn = db_connect()

    try:
        conn.execute("""
            UPDATE hooks
            SET active = 0,
                invalidated = ?
            WHERE id = ?
        """, (
            invalidated,
            hook_id
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# HOOK VALIDITY
# ============================================================

def hook_is_expired(hook):
    if not hook:
        return True

    hook_time = hook["hook_time"]

    if not hook_time:
        return True

    return (
        utc_now_ts() - hook_time
        > MAX_HOOK_AGE_SECONDS
    )


def hook_is_invalidated(symbol, hook):
    """
    Do NOT invalidate the hook merely because a lower low
    appears after H3. The lower low is exactly what is needed
    to reach the 86.4% correction.

    Here we only invalidate if price goes beyond Hook Start
    after the Hook was confirmed.
    """

    if not hook:
        return True

    if hook_is_expired(hook):
        return True

    candles = get_closed_candles(
        symbol,
        M30_INTERVAL,
        40
    )

    if not candles:
        return False

    start = safe_float(
        hook["hook_start"]
    )

    if start is None:
        return False

    side = hook["side"]

    for candle in candles:

        if candle["time"] <= hook["hook_time"]:
            continue

        if side == "SHORT":

            if candle["close"] < start:
                return True

        else:

            if candle["close"] > start:
                return True

    return False


# ============================================================
# M1 123F
# ============================================================

def build_alternating_pivots(
    highs,
    lows,
    start_time,
    end_time
):
    pivots = [
        p for p in merge_pivots(
            highs,
            lows
        )
        if p["time"] > start_time
        and p["time"] <= end_time
    ]

    # Remove same-type consecutive pivots.
    result = []

    for p in pivots:

        if not result:
            result.append(p)
            continue

        if p["type"] != result[-1]["type"]:
            result.append(p)

        else:
            # Keep the stronger pivot.
            if p["type"] == "H":

                if p["price"] > result[-1]["price"]:
                    result[-1] = p

            else:

                if p["price"] < result[-1]["price"]:
                    result[-1] = p

    return result


def detect_m1_123f(symbol, hook):
    """
    SHORT:

        H1 -> L -> H2 -> L -> H3 -> L -> F

        1 < 2 < 3 < F

    LONG:

        L1 -> H -> L2 -> H -> L3 -> H -> F

        1 > 2 > 3 > F
    """

    candles = get_closed_candles(
        symbol,
        M1_INTERVAL,
        M1_CANDLES
    )

    DIAG["m1_requests"] += 1

    if len(candles) < 100:
        DIAG["m1_short_data"] += 1
        return None

    DIAG["m1_data_ok"] += 1

    hook_time = hook["hook_time"]

    now = utc_now_ts()

    if (
        now - hook_time
        > MAX_HOOK_AGE_SECONDS
    ):
        return None

    highs = find_pivot_highs(
        candles
    )

    lows = find_pivot_lows(
        candles
    )

    side = hook["side"]

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if side == "SHORT":

        pivots = build_alternating_pivots(
            highs,
            lows,
            hook_time,
            now
        )

        # Need:
        # H -> L -> H -> L -> H -> L -> H
        #
        # The final H is F.

        for i in range(
            0,
            len(pivots) - 6
        ):

            seq = pivots[i:i + 7]

            types = [
                p["type"]
                for p in seq
            ]

            if types != [
                "H",
                "L",
                "H",
                "L",
                "H",
                "L",
                "H"
            ]:
                continue

            p1 = seq[0]
            correction1 = seq[1]

            p2 = seq[2]
            correction2 = seq[3]

            p3 = seq[4]
            correction3 = seq[5]

            f = seq[6]

            # Strict increasing tops
            if not (
                p1["price"]
                < p2["price"]
                < p3["price"]
                < f["price"]
            ):
                continue

            # Every correction must actually be below
            # its preceding high.
            if not (
                correction1["price"] < p1["price"]
                and
                correction2["price"] < p2["price"]
                and
                correction3["price"] < p3["price"]
            ):
                continue

            # Minimum swing
            if (
                pct_change(
                    p1["price"],
                    p2["price"]
                ) < M1_MIN_SWING_PCT
            ):
                continue

            if (
                pct_change(
                    p2["price"],
                    p3["price"]
                ) < M1_MIN_SWING_PCT
            ):
                continue

            if (
                pct_change(
                    p3["price"],
                    f["price"]
                ) < M1_MIN_SWING_PCT
            ):
                continue

            # Distance from Hook H3
            distance = pct_change(
                hook["h3"],
                f["price"]
            )

            if (
                distance
                > M1_MAX_STRUCTURE_DISTANCE_PCT
            ):
                continue

            f_age = now - f["time"]

            if f_age > MAX_F_AGE_SECONDS:
                continue

            DIAG["m1_123f_short"] += 1

            return {
                "side": "SHORT",

                "p1": p1,
                "c1": correction1,

                "p2": p2,
                "c2": correction2,

                "p3": p3,
                "c3": correction3,

                "f": f,

                "f_time": f["time"],
                "f_price": f["price"],
            }

        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    pivots = build_alternating_pivots(
        highs,
        lows,
        hook_time,
        now
    )

    # L -> H -> L -> H -> L -> H -> L

    for i in range(
        0,
        len(pivots) - 6
    ):

        seq = pivots[i:i + 7]

        types = [
            p["type"]
            for p in seq
        ]

        if types != [
            "L",
            "H",
            "L",
            "H",
            "L",
            "H",
            "L"
        ]:
            continue

        p1 = seq[0]
        correction1 = seq[1]

        p2 = seq[2]
        correction2 = seq[3]

        p3 = seq[4]
        correction3 = seq[5]

        f = seq[6]

        if not (
            p1["price"]
            > p2["price"]
            > p3["price"]
            > f["price"]
        ):
            continue

        if not (
            correction1["price"] > p1["price"]
            and
            correction2["price"] > p2["price"]
            and
            correction3["price"] > p3["price"]
        ):
            continue

        if (
            pct_change(
                p1["price"],
                p2["price"]
            ) < M1_MIN_SWING_PCT
        ):
            continue

        if (
            pct_change(
                p2["price"],
                p3["price"]
            ) < M1_MIN_SWING_PCT
        ):
            continue

        if (
            pct_change(
                p3["price"],
                f["price"]
            ) < M1_MIN_SWING_PCT
        ):
            continue

        distance = pct_change(
            hook["h3"],
            f["price"]
        )

        if (
            distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        f_age = now - f["time"]

        if f_age > MAX_F_AGE_SECONDS:
            continue

        DIAG["m1_123f_long"] += 1

        return {
            "side": "LONG",

            "p1": p1,
            "c1": correction1,

            "p2": p2,
            "c2": correction2,

            "p3": p3,
            "c3": correction3,

            "f": f,

            "f_time": f["time"],
            "f_price": f["price"],
        }

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):
    try:
        url = (
            f"{KRAKEN_API_URL}/tickers"
        )

        response = requests.get(
            url,
            params={
                "symbol": symbol
            },
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            []
        )

        if isinstance(tickers, dict):
            tickers = [tickers]

        for ticker in tickers:

            tsymbol = normalize_symbol(
                ticker.get("symbol")
                or ticker.get("pair")
                or ticker.get("instrument")
            )

            if tsymbol != symbol:
                continue

            price = (
                ticker.get("last")
                or ticker.get("lastPrice")
                or ticker.get("price")
            )

            return safe_float(price)

        # Fallback
        if tickers:

            ticker = tickers[0]

            price = (
                ticker.get("last")
                or ticker.get("lastPrice")
                or ticker.get("price")
            )

            return safe_float(price)

    except Exception:
        DIAG["errors"] += 1

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    hook,
    f_price
):
    side = hook["side"]

    entry = safe_float(
        f_price
    )

    if entry is None:
        return None

    if side == "SHORT":

        sl = safe_float(
            hook["previous_valid_peak"]
        )

        tp = safe_float(
            hook["retracement_864"]
        )

        if sl is None:
            sl = hook["h3"]

        if tp is None:
            tp = hook["target"]

        # SL must remain above entry
        if sl <= entry:
            sl = hook["h3"]

        # TP must remain below entry
        if tp >= entry:
            return None

    else:

        sl = safe_float(
            hook["previous_valid_low"]
        )

        tp = safe_float(
            hook["retracement_864"]
        )

        if sl is None:
            sl = hook["l3"]

        if tp is None:
            tp = hook["target"]

        if sl >= entry:
            sl = hook["l3"]

        if tp <= entry:
            return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
    }


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


def count_open_trades():
    conn = db_connect()

    try:
        row = conn.execute("""
            SELECT COUNT(*) AS c
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()

        return int(
            row["c"]
        )

    finally:
        conn.close()


def recent_signal_exists(symbol, side):
    cutoff = (
        utc_now_ts()
        - SIGNAL_COOLDOWN_SECONDS
    )

    conn = db_connect()

    try:
        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE symbol = ?
              AND side = ?
              AND created_at >= ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            symbol,
            side,
            cutoff,
        )).fetchone()

        return row is not None

    finally:
        conn.close()


def save_signal(
    symbol,
    hook,
    flag,
    levels,
    chart_path=None
):
    event_key = (
        f"{symbol}_"
        f"{hook['side']}_"
        f"{hook['hook_start_time']}_"
        f"{flag['f_time']}"
    )

    if signal_exists(event_key):
        return None

    if count_open_trades() >= MAX_OPEN_TRADES:
        return None

    if recent_signal_exists(
        symbol,
        hook["side"]
    ):
        return None

    conn = db_connect()

    try:
        cur = conn.cursor()

        now = utc_now_ts()

        cur.execute("""
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
                closed_at,

                close_price,
                pnl_pct,

                exit_reason,

                chart_path,

                strategy_version,
                entry_time,

                last_price,
                last_pnl_pct
            )
            VALUES (
                ?, ?, ?,
                ?, ?,
                ?,
                ?, ?, ?,
                ?, ?,
                'OPEN',
                ?, NULL,
                NULL, NULL,
                NULL,
                ?,
                ?, ?,
                NULL, NULL
            )
        """, (
            event_key,

            symbol,
            hook["side"],

            hook["id"],
            hook["hook_time"],

            flag["f_time"],

            levels["entry"],
            levels["sl"],
            levels["tp"],

            flag["f_price"],
            hook["retracement_864"],

            now,

            chart_path,

            VERSION,
            now,
        ))

        conn.commit()

        DIAG["new_signals"] += 1

        return cur.lastrowid

    finally:
        conn.close()


# ============================================================
# PNL
# ============================================================

def calculate_pnl(side, entry, current):
    if not entry:
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


def monitor_open_trades():
    conn = db_connect()

    try:
        trades = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY id
        """).fetchall()

        for trade in trades:

            current = get_current_price(
                trade["symbol"]
            )

            if current is None:
                continue

            pnl = calculate_pnl(
                trade["side"],
                trade["entry"],
                current
            )

            conn.execute("""
                UPDATE signals
                SET last_price = ?,
                    last_pnl_pct = ?
                WHERE id = ?
            """, (
                current,
                pnl,
                trade["id"],
            ))

            hit_tp = False
            hit_sl = False

            if trade["side"] == "LONG":

                hit_tp = current >= trade["tp"]
                hit_sl = current <= trade["sl"]

            else:

                hit_tp = current <= trade["tp"]
                hit_sl = current >= trade["sl"]

            if hit_tp or hit_sl:

                if hit_tp:
                    reason = "TP"
                else:
                    reason = "SL"

                conn.execute("""
                    UPDATE signals
                    SET status = 'CLOSED',
                        closed_at = ?,
                        close_price = ?,
                        pnl_pct = ?,
                        exit_reason = ?
                    WHERE id = ?
                """, (
                    utc_now_ts(),
                    current,
                    pnl,
                    reason,
                    trade["id"],
                ))

                message = (
                    f"🔔 <b>NDS TRADE CLOSED</b>\n\n"
                    f"<b>{trade['symbol']}</b>\n"
                    f"Side: <b>{trade['side']}</b>\n"
                    f"Exit: <b>{reason}</b>\n\n"
                    f"Entry: {format_price(trade['entry'])}\n"
                    f"Close: {format_price(current)}\n"
                    f"P/L: <b>{format_pct(pnl)}</b>\n\n"
                    f"Time: {utc_string()}"
                )

                send_telegram(message)

        conn.commit()

    except Exception:
        DIAG["errors"] += 1

    finally:
        conn.close()


# ============================================================
# CHART CANDLE DRAWING
# ============================================================

def draw_candles(ax, candles):
    if not candles:
        return

    width = 0.65

    for i, candle in enumerate(candles):

        o = candle["open"]
        h = candle["high"]
        l = candle["low"]
        c = candle["close"]

        if c >= o:
            body_bottom = o
            body_height = c - o
            face = "white"
            edge = "green"
        else:
            body_bottom = c
            body_height = o - c
            face = "red"
            edge = "red"

        ax.vlines(
            i,
            l,
            h,
            linewidth=0.8,
            color="black"
        )

        rect = Rectangle(
            (
                i - width / 2,
                body_bottom
            ),
            width,
            max(
                body_height,
                abs(o) * 0.000001
            ),
            facecolor=face,
            edgecolor=edge,
            linewidth=0.8
        )

        ax.add_patch(rect)


def candle_index_map(candles):
    return {
        candle["time"]: i
        for i, candle in enumerate(candles)
    }


def nearest_candle_index(candles, timestamp):
    if not candles:
        return None

    best_i = 0
    best_distance = abs(
        candles[0]["time"]
        - timestamp
    )

    for i, candle in enumerate(candles[1:], start=1):

        distance = abs(
            candle["time"]
            - timestamp
        )

        if distance < best_distance:
            best_distance = distance
            best_i = i

    return best_i


# ============================================================
# CHART LABEL HELPERS
# ============================================================

def annotate_point(
    ax,
    x,
    y,
    label,
    direction="up",
    color=None,
    fontsize=9
):
    if direction == "up":
        xytext = (0, 22)
        va = "bottom"
    else:
        xytext = (0, -22)
        va = "top"

    kwargs = {
        "xy": (x, y),
        "xytext": xytext,
        "textcoords": "offset points",
        "ha": "center",
        "va": va,
        "fontsize": fontsize,
        "fontweight": "bold",
        "arrowprops": {
            "arrowstyle": "->",
            "linewidth": 0.8,
        },
        "bbox": {
            "boxstyle": "round,pad=0.25",
            "alpha": 0.85,
        }
    }

    if color:
        kwargs["color"] = color

    ax.annotate(
        label,
        **kwargs
    )


def add_trade_box(
    ax,
    entry,
    sl,
    tp,
    side
):
    """
    Dedicated right-side box.
    This avoids Entry / SL / TP labels colliding
    with each other or with candles.
    """

    pnl_text = (
        f"ENTRY  {format_price(entry)}\n"
        f"SL     {format_price(sl)}\n"
        f"TP     {format_price(tp)}"
    )

    ax.text(
        1.015,
        0.72,
        pnl_text,
        transform=ax.transAxes,
        ha="left",
        va="top",
        fontsize=10,
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.45",
            "facecolor": "white",
            "edgecolor": "black",
            "alpha": 0.95,
        }
    )

    # Separate line labels
    ax.text(
        1.015,
        0.48,
        f"ENTRY\n{format_price(entry)}",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=9,
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.3",
            "facecolor": "white",
            "alpha": 0.9,
        }
    )

    ax.text(
        1.015,
        0.33,
        f"SL\n{format_price(sl)}",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=9,
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.3",
            "facecolor": "white",
            "alpha": 0.9,
        }
    )

    ax.text(
        1.015,
        0.18,
        f"TP\n{format_price(tp)}",
        transform=ax.transAxes,
        ha="left",
        va="center",
        fontsize=9,
        fontweight="bold",
        bbox={
            "boxstyle": "round,pad=0.3",
            "facecolor": "white",
            "alpha": 0.9,
        }
    )


# ============================================================
# M30 HOOK CHART
# ============================================================

def draw_m30_hook(
    ax,
    candles,
    hook
):
    draw_candles(
        ax,
        candles
    )

    idx = candle_index_map(
        candles
    )

    def x_for(ts):
        if ts in idx:
            return idx[ts]

        return nearest_candle_index(
            candles,
            ts
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if hook["side"] == "SHORT":

        points = [
            (
                "START",
                hook["hook_start_time"],
                hook["hook_start"],
                "down"
            ),
            (
                "H1",
                hook["h1_time"],
                hook["h1"],
                "up"
            ),
            (
                "L1",
                hook["l1_time"],
                hook["l1"],
                "down"
            ),
            (
                "H2",
                hook["h2_time"],
                hook["h2"],
                "up"
            ),
            (
                "L2",
                hook["l2_time"],
                hook["l2"],
                "down"
            ),
            (
                "H3",
                hook["h3_time"],
                hook["h3"],
                "up"
            ),
            (
                "86.4%",
                hook["confirmed_time"],
                hook["retracement_864"],
                "down"
            ),
        ]

    else:

        points = [
            (
                "START",
                hook["hook_start_time"],
                hook["hook_start"],
                "up"
            ),
            (
                "L1",
                hook["l1_time"],
                hook["l1"],
                "down"
            ),
            (
                "H1",
                hook["h1_time"],
                hook["h1"],
                "up"
            ),
            (
                "L2",
                hook["l2_time"],
                hook["l2"],
                "down"
            ),
            (
                "H2",
                hook["h2_time"],
                hook["h2"],
                "up"
            ),
            (
                "L3",
                hook["l3_time"],
                hook["l3"],
                "down"
            ),
            (
                "86.4%",
                hook["confirmed_time"],
                hook["retracement_864"],
                "up"
            ),
        ]

    xy = []

    for label, ts, price, direction in points:

        x = x_for(ts)

        if x is None:
            continue

        xy.append(
            (x, price)
        )

        if label == "H3":
            annotate_point(
                ax,
                x,
                price,
                "H3 ★",
                "up",
                color="darkred",
                fontsize=11
            )

        else:
            annotate_point(
                ax,
                x,
                price,
                label,
                direction,
                fontsize=8
            )

    if len(xy) >= 2:

        xs = [
            p[0]
            for p in xy
        ]

        ys = [
            p[1]
            for p in xy
        ]

        ax.plot(
            xs,
            ys,
            linewidth=2.0,
            linestyle="-",
            marker="o",
            markersize=4,
            color="purple"
        )

    # 86.4 horizontal line
    target = hook["retracement_864"]

    ax.axhline(
        target,
        linestyle="--",
        linewidth=1.5,
        color="orange"
    )

    ax.text(
        len(candles) - 1,
        target,
        f"  86.4% = {format_price(target)}",
        va="bottom",
        fontsize=9,
        fontweight="bold",
        color="darkorange"
    )

    # Previous valid SL level
    if hook["side"] == "SHORT":
        sl_level = hook["previous_valid_peak"]
    else:
        sl_level = hook["previous_valid_low"]

    if sl_level:

        ax.axhline(
            sl_level,
            linestyle=":",
            linewidth=1.3,
            color="black"
        )

        ax.text(
            len(candles) - 1,
            sl_level,
            f"  Previous Valid SL = "
            f"{format_price(sl_level)}",
            va="bottom",
            fontsize=8,
            fontweight="bold"
        )


# ============================================================
# M1 123F CHART
# ============================================================

def draw_m1_123f(
    ax,
    candles,
    flag,
    levels
):
    draw_candles(
        ax,
        candles
    )

    idx = candle_index_map(
        candles
    )

    def x_for(ts):
        if ts in idx:
            return idx[ts]

        return nearest_candle_index(
            candles,
            ts
        )

    labels = [
        ("1", flag["p1"]),
        ("2", flag["p2"]),
        ("3", flag["p3"]),
        ("F", flag["f"]),
    ]

    xs = []
    ys = []

    for label, point in labels:

        x = x_for(
            point["time"]
        )

        if x is None:
            continue

        xs.append(x)
        ys.append(point["price"])

        if label == "F":

            direction = (
                "up"
                if flag["side"] == "SHORT"
                else "down"
            )

            annotate_point(
                ax,
                x,
                point["price"],
                "F ★",
                direction,
                color="darkred",
                fontsize=11
            )

        else:

            direction = (
                "up"
                if flag["side"] == "SHORT"
                else "down"
            )

            annotate_point(
                ax,
                x,
                point["price"],
                label,
                direction,
                fontsize=10
            )

    # Draw 1-2-3-F path
    if len(xs) >= 2:

        ax.plot(
            xs,
            ys,
            linewidth=2.0,
            marker="o",
            markersize=5,
            color="blue"
        )

    # --------------------------------------------------------
    # Entry / SL / TP
    # --------------------------------------------------------

    entry = levels["entry"]
    sl = levels["sl"]
    tp = levels["tp"]

    ax.axhline(
        entry,
        linestyle="-",
        linewidth=1.6,
        color="blue"
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=1.6,
        color="red"
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=1.6,
        color="green"
    )

    # Separate labels at right
    add_trade_box(
        ax,
        entry,
        sl,
        tp,
        flag["side"]
    )


# ============================================================
# FULL SIGNAL CHART
# ============================================================

def create_signal_chart(
    symbol,
    hook,
    flag,
    levels
):
    if not CHARTS_ENABLED:
        return None

    try:
        os.makedirs(
            CHART_DIR,
            exist_ok=True
        )

        m30 = get_closed_candles(
            symbol,
            M30_INTERVAL,
            CHART_CANDLES_M30
        )

        m1 = get_closed_candles(
            symbol,
            M1_INTERVAL,
            CHART_CANDLES_M1
        )

        if len(m30) < 30 or len(m1) < 50:
            return None

        # Make sure Hook + F are visible.
        m30_start = hook["hook_start_time"]

        m30 = [
            c for c in m30
            if c["time"] >=
            min(
                m30_start,
                hook["hook_time"]
            ) - 1800 * 30
        ]

        m1_start = hook["hook_time"]

        m1 = [
            c for c in m1
            if c["time"] >=
            m1_start - 60 * 180
        ]

        fig, axes = plt.subplots(
            2,
            1,
            figsize=(19, 13),
            gridspec_kw={
                "height_ratios": [1.15, 1]
            }
        )

        ax_m30 = axes[0]
        ax_m1 = axes[1]

        # ----------------------------------------------------
        # M30
        # ----------------------------------------------------

        draw_m30_hook(
            ax_m30,
            m30,
            hook
        )

        ax_m30.set_title(
            f"NDS M30 CONFIRMED HOOK | "
            f"{symbol} | {hook['side']}\n"
            f"Hook Start → H1 → L1 → H2 → L2 → H3 "
            f"→ 86.4%",
            fontsize=14,
            fontweight="bold"
        )

        ax_m30.set_ylabel(
            "Price"
        )

        ax_m30.grid(
            alpha=0.2
        )

        # ----------------------------------------------------
        # M1
        # ----------------------------------------------------

        draw_m1_123f(
            ax_m1,
            m1,
            flag,
            levels
        )

        ax_m1.set_title(
            f"NDS M1 1-2-3-F | "
            f"{symbol} | {flag['side']}",
            fontsize=13,
            fontweight="bold"
        )

        ax_m1.set_ylabel(
            "Price"
        )

        ax_m1.set_xlabel(
            "Closed candles"
        )

        ax_m1.grid(
            alpha=0.2
        )

        # ----------------------------------------------------
        # Global
        # ----------------------------------------------------

        fig.suptitle(
            f"NDS SIGNAL | {symbol} | "
            f"{hook['side']} | VERSION {VERSION}",
            fontsize=16,
            fontweight="bold"
        )

        fig.tight_layout(
            rect=[
                0,
                0,
                1,
                0.97
            ]
        )

        filename = (
            f"{symbol}_"
            f"{hook['side']}_"
            f"{hook['hook_time']}_"
            f"{flag['f_time']}.png"
        )

        path = os.path.join(
            CHART_DIR,
            filename
        )

        fig.savefig(
            path,
            dpi=150,
            bbox_inches="tight"
        )

        plt.close(
            fig
        )

        DIAG["charts_created"] += 1

        return path

    except Exception:
        DIAG["errors"] += 1

        try:
            plt.close("all")
        except Exception:
            pass

        return None


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(
    symbol,
    hook,
    flag,
    levels
):
    entry = levels["entry"]
    sl = levels["sl"]
    tp = levels["tp"]

    if hook["side"] == "SHORT":

        sl_distance = (
            (sl - entry)
            / entry
            * 100
        )

        tp_distance = (
            (entry - tp)
            / entry
            * 100
        )

    else:

        sl_distance = (
            (entry - sl)
            / entry
            * 100
        )

        tp_distance = (
            (tp - entry)
            / entry
            * 100
        )

    message = (
        f"🚨 <b>NDS NEW SIGNAL</b>\n\n"

        f"<b>{symbol}</b>\n"
        f"Side: <b>{hook['side']}</b>\n\n"

        f"<b>M30 CONFIRMED HOOK</b>\n"
        f"Hook Start: {format_price(hook['hook_start'])}\n"
        f"H3: {format_price(hook['h3'])}\n"
        f"86.4%: {format_price(hook['retracement_864'])}\n\n"

        f"<b>M1 1-2-3-F</b>\n"
        f"1: {format_price(flag['p1']['price'])}\n"
        f"2: {format_price(flag['p2']['price'])}\n"
        f"3: {format_price(flag['p3']['price'])}\n"
        f"F: {format_price(flag['f_price'])}\n\n"

        f"<b>TRADE</b>\n"
        f"Entry: <b>{format_price(entry)}</b>\n"
        f"SL: <b>{format_price(sl)}</b> "
        f"({sl_distance:.2f}%)\n"
        f"TP: <b>{format_price(tp)}</b> "
        f"({tp_distance:.2f}%)\n\n"

        f"Time: {utc_string()}\n"
        f"Mode: <b>PAPER ONLY</b>"
    )

    return message


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def open_trades_text():
    conn = db_connect()

    try:
        trades = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY id DESC
        """).fetchall()

    finally:
        conn.close()

    if not trades:
        return (
            "<b>OPEN TRADES</b>: 0"
        )

    lines = [
        f"<b>OPEN TRADES</b>: "
        f"{len(trades)}"
    ]

    for trade in trades:

        current = (
            get_current_price(
                trade["symbol"]
            )
            or trade["last_price"]
            or trade["entry"]
        )

        pnl = calculate_pnl(
            trade["side"],
            trade["entry"],
            current
        )

        if trade["side"] == "LONG":

            tp_distance = (
                (trade["tp"] - current)
                / current
                * 100
            )

            sl_distance = (
                (current - trade["sl"])
                / current
                * 100
            )

        else:

            tp_distance = (
                (current - trade["tp"])
                / current
                * 100
            )

            sl_distance = (
                (trade["sl"] - current)
                / current
                * 100
            )

        lines.extend([
            "",
            f"<b>{trade['symbol']}</b> "
            f"{trade['side']}",
            f"Entry: {format_price(trade['entry'])}",
            f"Current: <b>{format_price(current)}</b>",
            f"P/L: <b>{format_pct(pnl)}</b>",
            f"TP: {format_price(trade['tp'])} "
            f"({tp_distance:.2f}%)",
            f"SL: {format_price(trade['sl'])} "
            f"({sl_distance:.2f}%)",
        ])

    return "\n".join(lines)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_text():
    conn = db_connect()

    try:
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

    finally:
        conn.close()

    total = int(
        row["total"] or 0
    )

    wins = int(
        row["wins"] or 0
    )

    losses = int(
        row["losses"] or 0
    )

    pnl = float(
        row["pnl"] or 0
    )

    return (
        f"<b>PERFORMANCE</b>\n"
        f"Closed: {total}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"PnL: <b>{format_pct(pnl)}</b>"
    )


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def diagnostic_report():
    return (
        f"🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_string()}\n\n"

        f"<b>M30</b>\n"
        f"Requests: {DIAG['m30_requests']}\n"
        f"Data OK: {DIAG['m30_data_ok']}\n"
        f"Short: {DIAG['m30_short_data']}\n"
        f"Pivot Highs: {DIAG['m30_pivot_highs']}\n"
        f"Pivot Lows: {DIAG['m30_pivot_lows']}\n"
        f"Hook Candidates: {DIAG['m30_hook_candidates']}\n"
        f"Incomplete Hooks: {DIAG['m30_incomplete_hooks']}\n"
        f"Confirmed Hooks: {DIAG['m30_confirmed_hooks']}\n\n"

        f"<b>M1</b>\n"
        f"Requests: {DIAG['m1_requests']}\n"
        f"Data OK: {DIAG['m1_data_ok']}\n"
        f"Short: {DIAG['m1_short_data']}\n"
        f"123F SHORT: {DIAG['m1_123f_short']}\n"
        f"123F LONG: {DIAG['m1_123f_long']}\n\n"

        f"Signals: {DIAG['new_signals']}\n"
        f"Charts: {DIAG['charts_created']}\n"
        f"Telegram Photos: {DIAG['telegram_photos']}\n"
        f"Errors: {DIAG['errors']}"
    )


# ============================================================
# SCAN M30
# ============================================================

def scan_m30(symbol):
    try:

        candles = get_closed_candles(
            symbol,
            M30_INTERVAL,
            M30_CANDLES
        )

        DIAG["m30_requests"] += 1

        if len(candles) < 100:

            DIAG["m30_short_data"] += 1

            return None

        DIAG["m30_data_ok"] += 1

        hook = detect_m30_hook(
            candles
        )

        if not hook:
            return None

        hook_id = save_hook(
            symbol,
            hook
        )

        hook["id"] = hook_id

        return hook

    except Exception:
        DIAG["errors"] += 1
        return None


# ============================================================
# SCAN M1
# ============================================================

def scan_m1(symbol):
    try:

        hook = get_active_hook(
            symbol
        )

        if not hook:
            return None

        if hook_is_expired(
            hook
        ):

            deactivate_hook(
                hook["id"],
                invalidated=1
            )

            return None

        if hook_is_invalidated(
            symbol,
            hook
        ):

            deactivate_hook(
                hook["id"],
                invalidated=1
            )

            return None

        flag = detect_m1_123f(
            symbol,
            hook
        )

        if not flag:
            return None

        levels = calculate_trade_levels(
            hook,
            flag["f_price"]
        )

        if not levels:
            return None

        chart_path = create_signal_chart(
            symbol,
            hook,
            flag,
            levels
        )

        signal_id = save_signal(
            symbol,
            hook,
            flag,
            levels,
            chart_path
        )

        if signal_id is None:
            return None

        message = build_signal_message(
            symbol,
            hook,
            flag,
            levels
        )

        send_telegram(
            message
        )

        if chart_path:
            send_telegram_photo(
                chart_path,
                caption=(
                    f"NDS {symbol} "
                    f"{hook['side']} | "
                    f"M30 Hook + M1 123F"
                )
            )

        # Hook is consumed by this signal.
        deactivate_hook(
            hook["id"],
            invalidated=0
        )

        return signal_id

    except Exception:
        DIAG["errors"] += 1

        return None


# ============================================================
# SCANNER REPORT
# ============================================================

def scanner_report(
    assets,
    new_signals
):
    open_count = count_open_trades()

    return (
        f"📊 <b>NDS M30 → M1 REPORT</b>\n"
        f"Version: <b>{VERSION}</b>\n\n"

        f"Assets scanned: <b>{len(assets)}</b>\n"
        f"New signals: <b>{new_signals}</b>\n"
        f"Open trades: <b>{open_count}</b>\n\n"

        f"{open_trades_text()}\n\n"
        f"{performance_text()}"
    )


# ============================================================
# RUN DATABASE
# ============================================================

def start_run():
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO scanner_runs (
                started_at,
                configured
            )
            VALUES (?, ?)
        """, (
            utc_now_ts(),
            1
        ))

        conn.commit()

        return cur.lastrowid

    finally:
        conn.close()


def finish_run(
    run_id,
    assets_scanned,
    new_signals
):
    conn = db_connect()

    try:
        conn.execute("""
            UPDATE scanner_runs
            SET finished_at = ?,
                assets_scanned = ?,
                new_signals = ?,
                errors = ?
            WHERE id = ?
        """, (
            utc_now_ts(),
            assets_scanned,
            new_signals,
            DIAG["errors"],
            run_id
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    if not PAPER_ONLY:
        raise RuntimeError(
            "PAPER_ONLY must remain True."
        )

    init_db()

    deactivate_legacy_hooks()

    assets = discover_assets()

    if not assets:
        send_telegram(
            "🚨 <b>NDS ERROR</b>\n"
            "No Kraken PF_*USD assets discovered."
        )

        return

    start_time = time.time()

    last_m30_scan = 0
    last_m1_scan = 0
    last_report = 0

    total_new_signals = 0

    send_telegram(
        f"🟢 <b>NDS SCANNER STARTED</b>\n\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Assets: <b>{len(assets)}</b>\n"
        f"Mode: <b>PAPER ONLY</b>\n"
        f"M30 Hook: <b>86.4% CONFIRMED</b>\n"
        f"M1 Entry: <b>123F</b>"
    )

    while (
        time.time() - start_time
        < RUN_SECONDS
    ):

        now = time.time()

        # ----------------------------------------------------
        # M30
        # ----------------------------------------------------

        if (
            now - last_m30_scan
            >= M30_SCAN_SECONDS
        ):

            run_id = start_run()

            cycle_signals = 0

            for symbol in assets:

                hook = scan_m30(
                    symbol
                )

                if hook:
                    # Confirmed Hook exists.
                    # No signal is sent here because M1 123F
                    # still has to confirm the entry.
                    pass

            finish_run(
                run_id,
                len(assets),
                cycle_signals
            )

            last_m30_scan = now

        # ----------------------------------------------------
        # M1
        # ----------------------------------------------------

        if (
            now - last_m1_scan
            >= M1_SCAN_SECONDS
        ):

            for symbol in assets:

                signal_id = scan_m1(
                    symbol
                )

                if signal_id:
                    total_new_signals += 1

            last_m1_scan = now

        # ----------------------------------------------------
        # OPEN TRADES
        # ----------------------------------------------------

        monitor_open_trades()

        # ----------------------------------------------------
        # REPORT
        # ----------------------------------------------------

        if (
            now - last_report
            >= REPORT_SECONDS
        ):

            report = scanner_report(
                assets,
                total_new_signals
            )

            send_telegram(
                report
            )

            send_telegram(
                diagnostic_report()
            )

            last_report = now

        time.sleep(5)

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    send_telegram(
        scanner_report(
            assets,
            total_new_signals
        )
    )

    send_telegram(
        diagnostic_report()
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        send_telegram(
            "🛑 <b>NDS SCANNER STOPPED</b>"
        )

    except Exception as exc:

        DIAG["errors"] += 1

        error_text = (
            f"🚨 <b>NDS SCANNER ERROR</b>\n\n"
            f"Version: {VERSION}\n"
            f"<pre>{str(exc)[:3000]}</pre>"
        )

        send_telegram(
            error_text
        )

        traceback.print_exc()

        raise
