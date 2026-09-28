# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.3
# ============================================================
#
# PAPER ONLY
#
# FINAL NDS LOGIC
#
# M30:
#
# SHORT:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
#   H3 = HOOK CONFIRMATION / M1 ACTIVATOR
#   NOT ENTRY
#
# LONG:
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
#   L3 = HOOK CONFIRMATION / M1 ACTIVATOR
#   NOT ENTRY
#
# ------------------------------------------------------------
#
# AFTER M30 H3/L3:
#
#   Search M1:
#
#       1 -> 2 -> 3 -> F
#
#   F = REAL ENTRY
#
# ------------------------------------------------------------
#
# TP:
#
#   TP is the 86.4% retracement level calculated on M30.
#
#   SHORT:
#       TP = H3 - 0.864 * (H3 - START)
#
#   LONG:
#       TP = L3 + 0.864 * (START - L3)
#
# IMPORTANT:
#
#   86.4% is NOT ENTRY.
#   H3/L3 is NOT ENTRY.
#   F is the REAL ENTRY.
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

VERSION = "4.4.3"

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
M1_CANDLES = 1800

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# Hook start window before H1/L1
HOOK_START_LOOKBACK_HOURS = int(
    os.getenv(
        "NDS_HOOK_START_LOOKBACK_HOURS",
        "6"
    )
)

HOOK_START_LOOKBACK_M30 = (
    HOOK_START_LOOKBACK_HOURS * 2
)

# Minimum Hook movement
M30_MIN_HOOK_RANGE_PCT = 0.20

# M1 minimum swing
M1_MIN_SWING_PCT = 0.07

# M1 structure maximum distance from M30 H3/L3
M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

# F freshness
MAX_F_AGE_SECONDS = 5 * 60

# Hook lifetime
MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

# NDS retracement
RETRACEMENT_864 = 0.864

# Trade
MAX_OPEN_TRADES = 3
SIGNAL_COOLDOWN_SECONDS = 60 * 60

# Scanner
M30_SCAN_SECONDS = 300
REPORT_SECONDS = 900

RUN_SECONDS = 12 * 60

# Charts
CHARTS_ENABLED = True

CHART_CANDLES_M30 = 180
CHART_CANDLES_M1 = 300

CHART_DIR = "nds_charts"

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": f"NDS-M30-M1-Scanner/{VERSION}"
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
    "m30_structure_valid": 0,
    "m30_incomplete_hooks": 0,
    "m30_retracement_tracking": 0,
    "m30_864_reached": 0,
    "m30_confirmed_hooks": 0,
    "m30_confirmed_this_cycle": 0,

    "hook_start_tests": 0,
    "hook_start_selected": 0,

    "m1_symbols_selected": 0,
    "m1_requests": 0,
    "m1_data_ok": 0,
    "m1_short_data": 0,

    "m1_123f_candidates": 0,
    "m1_123f_short": 0,
    "m1_123f_long": 0,

    "new_signals": 0,

    "open_trades": 0,
    "closed": 0,
    "wins": 0,
    "losses": 0,
    "pnl": 0.0,

    "charts_created": 0,
    "telegram_photos": 0,

    "errors": 0,
}


# ============================================================
# GENERAL
# ============================================================

def utc_now_ts():
    return int(time.time())


def utc_string(ts=None):
    if ts is None:
        ts = utc_now_ts()

    return datetime.fromtimestamp(
        ts,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)

    except Exception:
        return default


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


def normalize_symbol(symbol):
    return str(symbol or "").upper().strip()


def pct_distance(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if a is None or b is None or a == 0:
        return 0.0

    return abs(
        (b - a) / a
    ) * 100.0


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


def ensure_column(
    conn,
    table,
    column,
    definition
):
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

    hook_columns = [
        ("hook_start_time", "INTEGER"),
        ("hook_start", "REAL"),
        ("retracement_864", "REAL"),
        ("confirmed_time", "INTEGER"),
        ("previous_valid_peak", "REAL"),
        ("previous_valid_low", "REAL"),
        ("strategy_version", "TEXT"),
        ("hook_start_lookback_hours", "INTEGER"),
        ("hook_start_window_low", "REAL"),
        ("hook_start_window_high", "REAL"),
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
        ("f_index", "INTEGER"),
    ]

    for column, definition in signal_columns:
        ensure_column(
            conn,
            "signals",
            column,
            definition
        )

    conn.close()


def deactivate_legacy_hooks():
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
        """, (
            VERSION,
        ))

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


def send_telegram_photo(
    path,
    caption=""
):
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
# KRAKEN
# ============================================================

def discover_assets():

    try:

        response = requests.get(
            f"{KRAKEN_API_URL}/instruments",
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

            symbol = normalize_symbol(
                item.get("symbol")
                or item.get("instrument")
                or ""
            )

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            if symbol == "PF_SNXXUSD":
                continue

            assets.append(symbol)

        assets = sorted(
            set(assets)
        )

        if "PF_LDOUSD" in assets:

            assets.remove(
                "PF_LDOUSD"
            )

            assets.insert(
                0,
                "PF_LDOUSD"
            )

        return assets[:TARGET_ASSETS]

    except Exception:
        DIAG["errors"] += 1
        return []


def parse_candles(data):

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

        try:

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

            elif isinstance(
                item,
                (list, tuple)
            ):

                if len(item) < 5:
                    continue

                ts = item[0]
                op = item[1]
                hi = item[2]
                lo = item[3]
                cl = item[4]

                vol = (
                    item[5]
                    if len(item) > 5
                    else 0
                )

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

        except Exception:
            continue

    rows.sort(
        key=lambda x: x["time"]
    )

    return rows


def fetch_candles(
    symbol,
    interval,
    limit
):

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


def get_closed_candles(
    symbol,
    interval,
    limit
):

    candles = fetch_candles(
        symbol,
        interval,
        limit + 5
    )

    if not candles:
        return []

    now = utc_now_ts()

    durations = {
        "1m": 60,
        "30m": 1800,
    }

    duration = durations.get(
        interval,
        60
    )

    closed = []

    for candle in candles:

        if (
            candle["time"]
            + duration
            <= now
        ):
            closed.append(candle)

    return closed[-limit:]


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(
    candles,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT
):

    result = []

    if len(candles) < (
        left + right + 1
    ):
        return result

    for i in range(
        left,
        len(candles) - right
    ):

        price = candles[i]["high"]

        valid = True

        for j in range(
            i - left,
            i
        ):

            if candles[j]["high"] >= price:
                valid = False
                break

        if not valid:
            continue

        for j in range(
            i + 1,
            i + right + 1
        ):

            if candles[j]["high"] >= price:
                valid = False
                break

        if valid:

            result.append({
                "index": i,
                "time": candles[i]["time"],
                "price": price,
                "type": "H",
            })

    return result


def find_pivot_lows(
    candles,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT
):

    result = []

    if len(candles) < (
        left + right + 1
    ):
        return result

    for i in range(
        left,
        len(candles) - right
    ):

        price = candles[i]["low"]

        valid = True

        for j in range(
            i - left,
            i
        ):

            if candles[j]["low"] <= price:
                valid = False
                break

        if not valid:
            continue

        for j in range(
            i + 1,
            i + right + 1
        ):

            if candles[j]["low"] <= price:
                valid = False
                break

        if valid:

            result.append({
                "index": i,
                "time": candles[i]["time"],
                "price": price,
                "type": "L",
            })

    return result


def merge_pivots(
    highs,
    lows
):

    result = (
        list(highs)
        + list(lows)
    )

    result.sort(
        key=lambda x: (
            x["time"],
            0 if x["type"] == "L" else 1
        )
    )

    return result


# ============================================================
# HOOK START
# ============================================================

def select_hook_start_low(
    candles,
    h1
):

    DIAG["hook_start_tests"] += 1

    if not candles:
        return None

    index = h1["index"]

    start = max(
        0,
        index - HOOK_START_LOOKBACK_M30
    )

    window = candles[
        start:index
    ]

    if not window:
        return None

    selected = min(
        window,
        key=lambda c: (
            c["low"],
            c["time"]
        )
    )

    DIAG["hook_start_selected"] += 1

    return {
        "time": selected["time"],
        "price": selected["low"],
        "type": "L",
        "window_low": min(
            c["low"]
            for c in window
        ),
        "window_high": max(
            c["high"]
            for c in window
        ),
    }


def select_hook_start_high(
    candles,
    l1
):

    DIAG["hook_start_tests"] += 1

    if not candles:
        return None

    index = l1["index"]

    start = max(
        0,
        index - HOOK_START_LOOKBACK_M30
    )

    window = candles[
        start:index
    ]

    if not window:
        return None

    selected = max(
        window,
        key=lambda c: (
            c["high"],
            -c["time"]
        )
    )

    DIAG["hook_start_selected"] += 1

    return {
        "time": selected["time"],
        "price": selected["high"],
        "type": "H",
        "window_low": min(
            c["low"]
            for c in window
        ),
        "window_high": max(
            c["high"]
            for c in window
        ),
    }


# ============================================================
# 86.4 CALCULATION
# ============================================================

def calc_short_tp(
    start,
    h3
):

    return (
        h3
        - RETRACEMENT_864
        * (h3 - start)
    )


def calc_long_tp(
    start,
    l3
):

    return (
        l3
        + RETRACEMENT_864
        * (start - l3)
    )


# ============================================================
# HOOK STRUCTURE DETECTION
# ============================================================

def detect_short_hooks(
    candles,
    highs,
    lows
):

    hooks = []

    for h1 in highs:

        h1_index = h1["index"]

        start = select_hook_start_low(
            candles,
            h1
        )

        if not start:
            continue

        after_h1 = [
            p for p in lows
            if p["time"] > h1["time"]
        ]

        l1_candidates = [
            p for p in after_h1
            if p["index"] > h1_index
        ]

        if not l1_candidates:
            continue

        for l1 in l1_candidates:

            h2_candidates = [
                p for p in highs
                if p["time"] > l1["time"]
                and p["price"] > h1["price"]
            ]

            if not h2_candidates:
                continue

            h2 = h2_candidates[0]

            l2_candidates = [
                p for p in lows
                if p["time"] > h2["time"]
                and p["price"] < l1["price"]
            ]

            if not l2_candidates:
                continue

            l2 = l2_candidates[0]

            h3_candidates = [
                p for p in highs
                if p["time"] > l2["time"]
                and p["price"] > h2["price"]
            ]

            if not h3_candidates:
                continue

            h3 = h3_candidates[0]

            DIAG["m30_hook_candidates"] += 1

            movement = pct_distance(
                start["price"],
                h3["price"]
            )

            if movement < M30_MIN_HOOK_RANGE_PCT:
                continue

            DIAG["m30_structure_valid"] += 1

            tp = calc_short_tp(
                start["price"],
                h3["price"]
            )

            correction_started = False
            reached = False

            for candle in candles:

                if candle["time"] <= h3["time"]:
                    continue

                correction_started = True

                if candle["low"] <= tp:
                    reached = True
                    break

            if reached:

                DIAG["m30_864_reached"] += 1

                hooks.append({
                    "side": "SHORT",
                    "hook_start_time": start["time"],
                    "hook_start": start["price"],

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

                    "l3_time": None,
                    "l3": None,

                    "hook_time": h3["time"],

                    "target": tp,

                    "window_low": start["price"],
                    "window_high": h3["price"],
                })

                continue

            DIAG["m30_retracement_tracking"] += 1

            # The important change:
            # H3 itself activates M1.
            #
            # We DO NOT wait for 86.4 to activate M1.
            #
            if correction_started:

                hooks.append({
                    "side": "SHORT",
                    "hook_start_time": start["time"],
                    "hook_start": start["price"],

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

                    "l3_time": None,
                    "l3": None,

                    "hook_time": h3["time"],

                    "target": tp,

                    "window_low": start["price"],
                    "window_high": h3["price"],
                })

    return hooks


def detect_long_hooks(
    candles,
    highs,
    lows
):

    hooks = []

    for l1 in lows:

        l1_index = l1["index"]

        start = select_hook_start_high(
            candles,
            l1
        )

        if not start:
            continue

        h1_candidates = [
            p for p in highs
            if p["time"] > l1["time"]
        ]

        if not h1_candidates:
            continue

        h1 = h1_candidates[0]

        l2_candidates = [
            p for p in lows
            if p["time"] > h1["time"]
            and p["price"] < l1["price"]
        ]

        if not l2_candidates:
            continue

        l2 = l2_candidates[0]

        h2_candidates = [
            p for p in highs
            if p["time"] > l2["time"]
            and p["price"] > h1["price"]
        ]

        if not h2_candidates:
            continue

        h2 = h2_candidates[0]

        l3_candidates = [
            p for p in lows
            if p["time"] > h2["time"]
            and p["price"] < l2["price"]
        ]

        if not l3_candidates:
            continue

        l3 = l3_candidates[0]

        DIAG["m30_hook_candidates"] += 1

        movement = pct_distance(
            start["price"],
            l3["price"]
        )

        if movement < M30_MIN_HOOK_RANGE_PCT:
            continue

        DIAG["m30_structure_valid"] += 1

        tp = calc_long_tp(
            start["price"],
            l3["price"]
        )

        correction_started = False
        reached = False

        for candle in candles:

            if candle["time"] <= l3["time"]:
                continue

            correction_started = True

            if candle["high"] >= tp:
                reached = True
                break

        if reached:
            DIAG["m30_864_reached"] += 1

        else:
            DIAG["m30_retracement_tracking"] += 1

        # IMPORTANT:
        # L3 activates M1 regardless of 86.4.
        hooks.append({
            "side": "LONG",

            "hook_start_time": start["time"],
            "hook_start": start["price"],

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

            "h3_time": None,
            "h3": None,

            "hook_time": l3["time"],

            "target": tp,

            "window_low": l3["price"],
            "window_high": start["price"],
        })

    return hooks


# ============================================================
# DEDUP HOOKS
# ============================================================

def dedup_hooks(hooks):

    result = {}

    for hook in hooks:

        symbol = hook["symbol"]

        current = result.get(symbol)

        if current is None:
            result[symbol] = hook
            continue

        if hook["hook_time"] > current["hook_time"]:
            result[symbol] = hook

    return list(
        result.values()
    )


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(
    symbol,
    hook
):

    conn = db_connect()

    try:

        strategy = VERSION

        cur = conn.cursor()

        cur.execute("""
            SELECT id
            FROM hooks
            WHERE symbol = ?
              AND side = ?
              AND hook_time = ?
              AND strategy_version = ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            symbol,
            hook["side"],
            hook["hook_time"],
            strategy,
        ))

        row = cur.fetchone()

        now = utc_now_ts()

        if row:

            hook_id = row["id"]

            cur.execute("""
                UPDATE hooks
                SET active = 1,
                    invalidated = 0,
                    confirmed_time = ?,
                    target = ?,
                    retracement_864 = ?,
                    hook_start_time = ?,
                    hook_start = ?
                WHERE id = ?
            """, (
                now,
                hook["target"],
                hook["target"],
                hook["hook_start_time"],
                hook["hook_start"],
                hook_id,
            ))

        else:

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

                    strategy_version,
                    hook_start_lookback_hours
                )
                VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?,
                    1, 0, ?,
                    ?, ?, ?, ?,
                    ?, ?
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

                now,

                hook["hook_start_time"],
                hook["hook_start"],
                hook["target"],
                now,

                strategy,
                HOOK_START_LOOKBACK_HOURS,
            ))

            hook_id = cur.lastrowid

        conn.commit()

        return hook_id

    finally:
        conn.close()


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(symbol):

    DIAG["m30_requests"] += 1

    candles = get_closed_candles(
        symbol,
        M30_INTERVAL,
        M30_CANDLES
    )

    if not candles:
        DIAG["m30_short_data"] += 1
        return None

    if len(candles) < 50:
        DIAG["m30_short_data"] += 1
        return None

    DIAG["m30_data_ok"] += 1

    highs = find_pivot_highs(
        candles
    )

    lows = find_pivot_lows(
        candles
    )

    DIAG["m30_pivot_highs"] += len(highs)
    DIAG["m30_pivot_lows"] += len(lows)

    short_hooks = detect_short_hooks(
        candles,
        highs,
        lows
    )

    long_hooks = detect_long_hooks(
        candles,
        highs,
        lows
    )

    hooks = (
        short_hooks
        + long_hooks
    )

    if not hooks:
        return None

    hooks.sort(
        key=lambda x: x["hook_time"],
        reverse=True
    )

    hook = hooks[0]

    hook["symbol"] = symbol

    # H3/L3 itself is the confirmation.
    DIAG["m30_confirmed_hooks"] += 1

    hook_id = save_hook(
        symbol,
        hook
    )

    hook["id"] = hook_id

    return hook


# ============================================================
# M1 PIVOTS
# ============================================================

def find_m1_pivots(candles):

    highs = find_pivot_highs(
        candles,
        2,
        2
    )

    lows = find_pivot_lows(
        candles,
        2,
        2
    )

    return merge_pivots(
        highs,
        lows
    )


# ============================================================
# M1 123F
# ============================================================

def detect_m1_123f(
    candles,
    hook
):

    if len(candles) < 30:
        return None

    pivots = find_m1_pivots(
        candles
    )

    if len(pivots) < 7:
        return None

    DIAG["m1_123f_candidates"] += 1

    side = hook["side"]

    hook_price = (
        hook["h3"]
        if side == "SHORT"
        else hook["l3"]
    )

    hook_time = hook["hook_time"]

    # Only pivots AFTER H3/L3
    pivots = [
        p for p in pivots
        if p["time"] > hook_time
    ]

    if len(pivots) < 7:
        return None

    # --------------------------------------------------------
    # SHORT
    #
    # 1 = High
    # 2 = High
    # 3 = High
    # F = High
    #
    # 1 < 2 < 3 < F
    # --------------------------------------------------------

    if side == "SHORT":

        highs = [
            p for p in pivots
            if p["type"] == "H"
        ]

        if len(highs) < 4:
            return None

        candidate = highs[-4:]

        p1, p2, p3, f = candidate

        if not (
            p1["price"]
            < p2["price"]
            < p3["price"]
            < f["price"]
        ):
            return None

        if f["time"] <= p3["time"]:
            return None

        if (
            utc_now_ts()
            - f["time"]
            > MAX_F_AGE_SECONDS
        ):
            return None

        # Structure should remain reasonably close
        # to the M30 Hook area.
        distance = pct_distance(
            hook_price,
            f["price"]
        )

        if distance > M1_MAX_STRUCTURE_DISTANCE_PCT:
            return None

        # Each structural leg must have enough movement.
        if pct_distance(
            p1["price"],
            p2["price"]
        ) < M1_MIN_SWING_PCT:
            return None

        if pct_distance(
            p2["price"],
            p3["price"]
        ) < M1_MIN_SWING_PCT:
            return None

        if pct_distance(
            p3["price"],
            f["price"]
        ) < M1_MIN_SWING_PCT:
            return None

        DIAG["m1_123f_short"] += 1

        return {
            "side": "SHORT",
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "f": f,
        }

    # --------------------------------------------------------
    # LONG
    #
    # 1 = Low
    # 2 = Low
    # 3 = Low
    # F = Low
    #
    # 1 > 2 > 3 > F
    # --------------------------------------------------------

    lows = [
        p for p in pivots
        if p["type"] == "L"
    ]

    if len(lows) < 4:
        return None

    candidate = lows[-4:]

    p1, p2, p3, f = candidate

    if not (
        p1["price"]
        > p2["price"]
        > p3["price"]
        > f["price"]
    ):
        return None

    if f["time"] <= p3["time"]:
        return None

    if (
        utc_now_ts()
        - f["time"]
        > MAX_F_AGE_SECONDS
    ):
        return None

    distance = pct_distance(
        hook_price,
        f["price"]
    )

    if distance > M1_MAX_STRUCTURE_DISTANCE_PCT:
        return None

    if pct_distance(
        p1["price"],
        p2["price"]
    ) < M1_MIN_SWING_PCT:
        return None

    if pct_distance(
        p2["price"],
        p3["price"]
    ) < M1_MIN_SWING_PCT:
        return None

    if pct_distance(
        p3["price"],
        f["price"]
    ) < M1_MIN_SWING_PCT:
        return None

    DIAG["m1_123f_long"] += 1

    return {
        "side": "LONG",
        "p1": p1,
        "p2": p2,
        "p3": p3,
        "f": f,
    }


# ============================================================
# SL
# ============================================================

def calculate_sl(
    candles,
    side,
    entry,
    hook
):

    pivots = find_m1_pivots(
        candles
    )

    if side == "SHORT":

        candidates = [
            p["price"]
            for p in pivots
            if p["type"] == "H"
            and p["price"] > entry
        ]

        if candidates:
            return min(candidates)

        return entry * 1.01

    candidates = [
        p["price"]
        for p in pivots
        if p["type"] == "L"
        and p["price"] < entry
    ]

    if candidates:
        return max(candidates)

    return entry * 0.99


# ============================================================
# SIGNAL DB
# ============================================================

def signal_exists(
    symbol,
    side,
    f_time
):

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE symbol = ?
              AND side = ?
              AND f_time = ?
            LIMIT 1
        """, (
            symbol,
            side,
            f_time,
        )).fetchone()

        return row is not None

    finally:
        conn.close()


def open_trade_count():

    conn = db_connect()

    try:

        row = conn.execute("""
            SELECT COUNT(*)
            AS n
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()

        return int(
            row["n"]
        )

    finally:
        conn.close()


def create_signal(
    symbol,
    hook,
    structure,
    candles
):

    f = structure["f"]

    side = structure["side"]

    entry = f["price"]

    tp = hook["target"]

    if side == "SHORT":

        if tp >= entry:
            return None

    else:

        if tp <= entry:
            return None

    sl = calculate_sl(
        candles,
        side,
        entry,
        hook
    )

    event_key = (
        f"{symbol}:"
        f"{side}:"
        f"{f['time']}"
    )

    if signal_exists(
        symbol,
        side,
        f["time"]
    ):
        return None

    if open_trade_count() >= MAX_OPEN_TRADES:
        return None

    conn = db_connect()

    try:

        now = utc_now_ts()

        cur = conn.cursor()

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

                strategy_version,
                entry_time,
                last_price,
                last_pnl_pct,

                f_index
            )
            VALUES (
                ?, ?, ?,
                ?, ?,
                ?,
                ?, ?, ?,
                ?, ?,
                'OPEN',
                ?,
                ?, ?, ?, ?,
                ?
            )
        """, (
            event_key,
            symbol,
            side,

            hook["id"],
            hook["hook_time"],

            f["time"],

            entry,
            sl,
            tp,

            f["price"],
            hook["target"],

            now,

            VERSION,
            f["time"],
            entry,
            0.0,

            f["index"],
        ))

        signal_id = cur.lastrowid

        conn.commit()

    finally:
        conn.close()

    DIAG["new_signals"] += 1

    return {
        "id": signal_id,
        "symbol": symbol,
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "f": f,
        "hook": hook,
    }


# ============================================================
# CHART
# ============================================================

def candle_width(candles):

    if len(candles) < 2:
        return 0.0005

    diffs = [
        candles[i]["time"]
        - candles[i - 1]["time"]
        for i in range(1, len(candles))
    ]

    diffs = [
        x for x in diffs
        if x > 0
    ]

    if not diffs:
        return 60

    return min(diffs) * 0.65


def draw_candles(
    ax,
    candles
):

    width = candle_width(
        candles
    )

    for c in candles:

        x = c["time"]

        color = (
            "green"
            if c["close"] >= c["open"]
            else "red"
        )

        ax.plot(
            [x, x],
            [c["low"], c["high"]],
            color=color,
            linewidth=0.7,
        )

        bottom = min(
            c["open"],
            c["close"]
        )

        height = abs(
            c["close"]
            - c["open"]
        )

        if height == 0:
            height = max(
                c["close"] * 0.00001,
                0.00000001
            )

        rect = Rectangle(
            (
                x - width / 2,
                bottom
            ),
            width,
            height,
            facecolor=color,
            edgecolor=color,
            alpha=0.75,
        )

        ax.add_patch(rect)


def annotate_point(
    ax,
    point,
    label,
    offset
):

    if not point:
        return

    ax.scatter(
        [point["time"]],
        [point["price"]],
        s=42,
        zorder=8,
    )

    ax.annotate(
        label,
        (
            point["time"],
            point["price"]
        ),
        xytext=(0, offset),
        textcoords="offset points",
        ha="center",
        fontsize=9,
        fontweight="bold",
    )


def draw_full_chart(
    symbol,
    hook,
    structure,
    m30,
    m1,
    signal
):

    if not CHARTS_ENABLED:
        return None

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    path = os.path.join(
        CHART_DIR,
        (
            f"{symbol}_"
            f"{hook['side']}_"
            f"{signal['f']['time']}.png"
        )
    )

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(16, 11),
        gridspec_kw={
            "height_ratios": [1.35, 1]
        }
    )

    ax30 = axes[0]
    ax1 = axes[1]

    # --------------------------------------------------------
    # M30
    # --------------------------------------------------------

    chart30 = m30[
        -CHART_CANDLES_M30:
    ]

    draw_candles(
        ax30,
        chart30
    )

    ax30.set_title(
        f"{symbol} | M30 NDS HOOK | "
        f"{hook['side']}"
    )

    ax30.grid(
        alpha=0.20
    )

    start_point = {
        "time": hook["hook_start_time"],
        "price": hook["hook_start"],
    }

    annotate_point(
        ax30,
        start_point,
        "START",
        -18
    )

    if hook["side"] == "SHORT":

        points = [
            ("H1", hook["h1_time"], hook["h1"]),
            ("L1", hook["l1_time"], hook["l1"]),
            ("H2", hook["h2_time"], hook["h2"]),
            ("L2", hook["l2_time"], hook["l2"]),
            ("H3", hook["h3_time"], hook["h3"]),
        ]

    else:

        points = [
            ("L1", hook["l1_time"], hook["l1"]),
            ("H1", hook["h1_time"], hook["h1"]),
            ("L2", hook["l2_time"], hook["l2"]),
            ("H2", hook["h2_time"], hook["h2"]),
            ("L3", hook["l3_time"], hook["l3"]),
        ]

    for label, ts, price in points:

        if ts is None or price is None:
            continue

        annotate_point(
            ax30,
            {
                "time": ts,
                "price": price
            },
            label,
            12 if label.startswith("H")
            else -18
        )

    hook_price = (
        hook["h3"]
        if hook["side"] == "SHORT"
        else hook["l3"]
    )

    hook_time = hook["hook_time"]

    ax30.scatter(
        [hook_time],
        [hook_price],
        s=100,
        marker="*",
        zorder=10,
    )

    ax30.annotate(
        "TRADE ACTIVATOR\nH3/L3",
        (
            hook_time,
            hook_price
        ),
        xytext=(0, 28),
        textcoords="offset points",
        ha="center",
        fontsize=10,
        fontweight="bold",
    )

    # 86.4 TP
    ax30.axhline(
        hook["target"],
        linestyle="--",
        linewidth=1.6,
    )

    ax30.text(
        chart30[-1]["time"],
        hook["target"],
        "  86.4% FINAL TP",
        va="center",
        fontsize=10,
        fontweight="bold",
    )

    # Entry projected on M30
    ax30.axhline(
        signal["entry"],
        linestyle=":",
        linewidth=1.2,
    )

    ax30.text(
        chart30[-1]["time"],
        signal["entry"],
        "  M1 F ENTRY",
        va="center",
        fontsize=10,
        fontweight="bold",
    )

    # --------------------------------------------------------
    # M1
    # --------------------------------------------------------

    chart1 = m1[
        -CHART_CANDLES_M1:
    ]

    draw_candles(
        ax1,
        chart1
    )

    ax1.set_title(
        f"{symbol} | M1 123F ENTRY"
    )

    ax1.grid(
        alpha=0.20
    )

    annotate_point(
        ax1,
        structure["p1"],
        "1",
        12
    )

    annotate_point(
        ax1,
        structure["p2"],
        "2",
        -18
    )

    annotate_point(
        ax1,
        structure["p3"],
        "3",
        12
    )

    annotate_point(
        ax1,
        structure["f"],
        "F = ENTRY",
        -25
    )

    ax1.axhline(
        signal["entry"],
        linestyle=":",
        linewidth=1.5,
    )

    ax1.axhline(
        signal["tp"],
        linestyle="--",
        linewidth=1.5,
    )

    ax1.axhline(
        signal["sl"],
        linestyle="--",
        linewidth=1.2,
    )

    last_time = chart1[-1]["time"]

    ax1.text(
        last_time,
        signal["entry"],
        "  ENTRY",
        va="center",
        fontsize=10,
        fontweight="bold",
    )

    ax1.text(
        last_time,
        signal["tp"],
        "  TP 86.4%",
        va="center",
        fontsize=10,
        fontweight="bold",
    )

    ax1.text(
        last_time,
        signal["sl"],
        "  SL",
        va="center",
        fontsize=10,
        fontweight="bold",
    )

    fig.text(
        0.5,
        0.015,
        (
            f"{hook['side']} | "
            f"M30 H3/L3 = ACTIVATOR | "
            f"M1 F = ENTRY | "
            f"86.4% = FINAL TP"
        ),
        ha="center",
        fontsize=11,
        fontweight="bold",
    )

    plt.tight_layout(
        rect=[0, 0.04, 1, 1]
    )

    fig.savefig(
        path,
        dpi=150,
        bbox_inches="tight"
    )

    plt.close(fig)

    DIAG["charts_created"] += 1

    return path


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

def send_signal_alert(
    signal
):

    hook = signal["hook"]

    structure = signal["f"]

    side = signal["side"]

    emoji = (
        "🔴"
        if side == "SHORT"
        else "🟢"
    )

    message = (
        f"{emoji} <b>NDS {side}</b>\n\n"
        f"<b>{signal['symbol']}</b>\n"
        f"M30 Hook: "
        f"<b>{hook['h3'] if side == 'SHORT' else hook['l3']:.6f}</b>\n"
        f"M1 F Entry: "
        f"<b>{signal['entry']:.6f}</b>\n"
        f"TP 86.4%: "
        f"<b>{signal['tp']:.6f}</b>\n"
        f"SL: "
        f"<b>{signal['sl']:.6f}</b>\n\n"
        f"M1 Structure: "
        f"<b>1 → 2 → 3 → F</b>\n"
        f"F = Entry\n"
        f"86.4% M30 = Final TP"
    )

    send_telegram(
        message
    )


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(
    hook
):

    symbol = hook["symbol"]

    DIAG["m1_symbols_selected"] += 1
    DIAG["m1_requests"] += 1

    candles = get_closed_candles(
        symbol,
        M1_INTERVAL,
        M1_CANDLES
    )

    if not candles:
        DIAG["m1_short_data"] += 1
        return None

    if len(candles) < 100:
        DIAG["m1_short_data"] += 1
        return None

    DIAG["m1_data_ok"] += 1

    structure = detect_m1_123f(
        candles,
        hook
    )

    if not structure:
        return None

    signal = create_signal(
        symbol,
        hook,
        structure,
        candles
    )

    if not signal:
        return None

    chart_m30 = get_closed_candles(
        symbol,
        M30_INTERVAL,
        M30_CANDLES
    )

    chart_path = draw_full_chart(
        symbol,
        hook,
        structure,
        chart_m30,
        candles,
        signal
    )

    if chart_path:

        conn = db_connect()

        try:

            conn.execute("""
                UPDATE signals
                SET chart_path = ?
                WHERE id = ?
            """, (
                chart_path,
                signal["id"],
            ))

            conn.commit()

        finally:
            conn.close()

        signal["chart_path"] = chart_path

    send_signal_alert(
        signal
    )

    if signal.get("chart_path"):
        send_telegram_photo(
            signal["chart_path"],
            (
                f"NDS {signal['side']} | "
                f"{symbol}\n"
                f"M1 F Entry: "
                f"{format_price(signal['entry'])}\n"
                f"TP 86.4%: "
                f"{format_price(signal['tp'])}"
            )
        )

    return signal


# ============================================================
# OPEN TRADE MONITOR
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

        return rows

    finally:
        conn.close()


def calculate_trade_pnl(
    side,
    entry,
    current
):

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

    rows = get_open_trades()

    DIAG["open_trades"] = len(rows)

    for row in rows:

        symbol = row["symbol"]

        candles = fetch_candles(
            symbol,
            M1_INTERVAL,
            10
        )

        if not candles:
            continue

        current = candles[-1]["close"]

        side = row["side"]

        entry = row["entry"]
        tp = row["tp"]
        sl = row["sl"]

        pnl = calculate_trade_pnl(
            side,
            entry,
            current
        )

        close_reason = None

        if side == "LONG":

            if current >= tp:
                close_reason = "TP_86.4"

            elif current <= sl:
                close_reason = "SL"

        else:

            if current <= tp:
                close_reason = "TP_86.4"

            elif current >= sl:
                close_reason = "SL"

        conn = db_connect()

        try:

            conn.execute("""
                UPDATE signals
                SET last_price = ?,
                    last_pnl_pct = ?
                WHERE id = ?
            """, (
                current,
                pnl,
                row["id"],
            ))

            conn.commit()

        finally:
            conn.close()

        if not close_reason:
            continue

        close_trade(
            row,
            current,
            pnl,
            close_reason
        )


def close_trade(
    row,
    price,
    pnl,
    reason
):

    conn = db_connect()

    try:

        now = utc_now_ts()

        conn.execute("""
            UPDATE signals
            SET status = 'CLOSED',
                closed_at = ?,
                close_price = ?,
                pnl_pct = ?,
                last_price = ?,
                last_pnl_pct = ?,
                exit_reason = ?
            WHERE id = ?
        """, (
            now,
            price,
            pnl,
            price,
            pnl,
            reason,
            row["id"],
        ))

        conn.commit()

    finally:
        conn.close()

    DIAG["closed"] += 1

    if pnl >= 0:
        DIAG["wins"] += 1
    else:
        DIAG["losses"] += 1

    DIAG["pnl"] += pnl

    emoji = (
        "✅"
        if pnl >= 0
        else "❌"
    )

    send_telegram(
        (
            f"{emoji} <b>NDS CLOSED</b>\n\n"
            f"<b>{row['symbol']}</b>\n"
            f"{row['side']}\n"
            f"Entry: {format_price(row['entry'])}\n"
            f"Close: {format_price(price)}\n"
            f"PnL: <b>{format_pct(pnl)}</b>\n"
            f"Reason: {reason}"
        )
    )


# ============================================================
# REPORT
# ============================================================

def report():

    open_count = open_trade_count()

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

    DIAG["open_trades"] = open_count
    DIAG["closed"] = total
    DIAG["wins"] = wins
    DIAG["losses"] = losses
    DIAG["pnl"] = pnl

    text = (
        "🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_string()}\n\n"

        "<b>M30</b>\n"
        f"Requests: {DIAG['m30_requests']}\n"
        f"Data OK: {DIAG['m30_data_ok']}\n"
        f"Short data: {DIAG['m30_short_data']}\n"
        f"Pivot Highs: {DIAG['m30_pivot_highs']}\n"
        f"Pivot Lows: {DIAG['m30_pivot_lows']}\n"
        f"Hook Candidates: "
        f"{DIAG['m30_hook_candidates']}\n"
        f"Structure Valid: "
        f"{DIAG['m30_structure_valid']}\n"
        f"Retracement Tracking: "
        f"{DIAG['m30_retracement_tracking']}\n"
        f"86.4% Reached: "
        f"{DIAG['m30_864_reached']}\n"
        f"Confirmed Hooks: "
        f"{DIAG['m30_confirmed_hooks']}\n"
        f"Confirmed This Cycle: "
        f"{DIAG['m30_confirmed_this_cycle']}\n\n"

        "<b>M1 PIPELINE</b>\n"
        f"Symbols Selected: "
        f"{DIAG['m1_symbols_selected']}\n"
        f"Requests: "
        f"{DIAG['m1_requests']}\n"
        f"Data OK: "
        f"{DIAG['m1_data_ok']}\n"
        f"Short data: "
        f"{DIAG['m1_short_data']}\n"
        f"123F Candidates: "
        f"{DIAG['m1_123f_candidates']}\n"
        f"123F SHORT: "
        f"{DIAG['m1_123f_short']}\n"
        f"123F LONG: "
        f"{DIAG['m1_123f_long']}\n\n"

        "<b>TRADES</b>\n"
        f"New Signals: "
        f"{DIAG['new_signals']}\n"
        f"Open Trades: "
        f"{open_count}\n"
        f"Closed: {total}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"PnL: <b>{format_pct(pnl)}</b>\n\n"

        f"Charts: {DIAG['charts_created']}\n"
        f"Errors: {DIAG['errors']}"
    )

    send_telegram(
        text
    )

    print()
    print(
        "=================================================="
    )
    print(
        f"NDS DIAGNOSTIC {VERSION}"
    )
    print(
        utc_string()
    )
    print(
        "=================================================="
    )

    print()
    print("M30")
    print(
        f"Requests: {DIAG['m30_requests']}"
    )
    print(
        f"Data OK: {DIAG['m30_data_ok']}"
    )
    print(
        f"Short data: {DIAG['m30_short_data']}"
    )
    print(
        f"Pivot Highs: {DIAG['m30_pivot_highs']}"
    )
    print(
        f"Pivot Lows: {DIAG['m30_pivot_lows']}"
    )
    print(
        f"Hook Candidates: "
        f"{DIAG['m30_hook_candidates']}"
    )
    print(
        f"Structure Valid: "
        f"{DIAG['m30_structure_valid']}"
    )
    print(
        f"Retracement Tracking: "
        f"{DIAG['m30_retracement_tracking']}"
    )
    print(
        f"86.4% Reached: "
        f"{DIAG['m30_864_reached']}"
    )
    print(
        f"Confirmed Hooks: "
        f"{DIAG['m30_confirmed_hooks']}"
    )
    print(
        f"Confirmed This Cycle: "
        f"{DIAG['m30_confirmed_this_cycle']}"
    )

    print()
    print("M1 PIPELINE")
    print(
        f"Symbols Selected: "
        f"{DIAG['m1_symbols_selected']}"
    )
    print(
        f"Requests: "
        f"{DIAG['m1_requests']}"
    )
    print(
        f"Data OK: "
        f"{DIAG['m1_data_ok']}"
    )
    print(
        f"Short data: "
        f"{DIAG['m1_short_data']}"
    )
    print(
        f"123F Candidates: "
        f"{DIAG['m1_123f_candidates']}"
    )
    print(
        f"123F SHORT: "
        f"{DIAG['m1_123f_short']}"
    )
    print(
        f"123F LONG: "
        f"{DIAG['m1_123f_long']}"
    )

    print()
    print("TRADES")
    print(
        f"New Signals: "
        f"{DIAG['new_signals']}"
    )
    print(
        f"Open Trades: "
        f"{open_count}"
    )
    print(
        f"Closed: {total}"
    )
    print(
        f"Wins: {wins}"
    )
    print(
        f"Losses: {losses}"
    )
    print(
        f"PnL: {format_pct(pnl)}"
    )

    print()
    print(
        f"Charts: {DIAG['charts_created']}"
    )
    print(
        f"Errors: {DIAG['errors']}"
    )


# ============================================================
# SCANNER RUN
# ============================================================

def run_cycle(
    assets
):

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # The M1 queue is built directly from THIS M30 scan.
    #
    # We do NOT select one global active DB Hook.
    #
    # One latest confirmed Hook per symbol.
    # --------------------------------------------------------

    confirmed_hooks_current = {}

    for symbol in assets:

        try:

            hook = scan_m30(
                symbol
            )

            if not hook:
                continue

            # Keep only newest Hook for this symbol.
            previous = confirmed_hooks_current.get(
                symbol
            )

            if (
                previous is None
                or hook["hook_time"]
                > previous["hook_time"]
            ):

                confirmed_hooks_current[
                    symbol
                ] = hook

        except Exception:

            DIAG["errors"] += 1

            traceback.print_exc()

    DIAG["m30_confirmed_this_cycle"] = len(
        confirmed_hooks_current
    )

    # --------------------------------------------------------
    # M1
    #
    # Every confirmed M30 H3/L3 enters M1 queue.
    #
    # H3/L3 is NOT entry.
    # F is entry.
    # --------------------------------------------------------

    for symbol, hook in (
        confirmed_hooks_current.items()
    ):

        try:

            scan_m1(
                hook
            )

        except Exception:

            DIAG["errors"] += 1

            traceback.print_exc()

    # --------------------------------------------------------
    # Monitor already open paper trades.
    # --------------------------------------------------------

    try:

        monitor_open_trades()

    except Exception:

        DIAG["errors"] += 1

        traceback.print_exc()


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        f"Starting NDS M30 -> M1 "
        f"Scanner {VERSION}"
    )

    print(
        "PAPER_ONLY:",
        PAPER_ONLY
    )

    init_db()

    deactivate_legacy_hooks()

    assets = discover_assets()

    if not assets:

        print(
            "No Kraken assets found."
        )

        return

    print(
        f"Assets discovered: "
        f"{len(assets)}"
    )

    started = time.time()

    last_report = 0

    while (
        time.time()
        - started
        < RUN_SECONDS
    ):

        cycle_start = time.time()

        # Reset cycle-only diagnostics.
        DIAG[
            "m30_confirmed_this_cycle"
        ] = 0

        DIAG[
            "m1_symbols_selected"
        ] = 0

        DIAG[
            "m1_requests"
        ] = 0

        DIAG[
            "m1_data_ok"
        ] = 0

        DIAG[
            "m1_short_data"
        ] = 0

        DIAG[
            "m1_123f_candidates"
        ] = 0

        DIAG[
            "m1_123f_short"
        ] = 0

        DIAG[
            "m1_123f_long"
        ] = 0

        DIAG[
            "new_signals"
        ] = 0

        DIAG[
            "charts_created"
        ] = 0

        print()
        print(
            f"--- SCAN "
            f"{utc_string()} ---"
        )

        run_cycle(
            assets
        )

        now = time.time()

        if (
            now - last_report
            >= REPORT_SECONDS
        ):

            report()

            last_report = now

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_for = max(
            5,
            M30_SCAN_SECONDS
            - elapsed
        )

        remaining = (
            RUN_SECONDS
            - (
                time.time()
                - started
            )
        )

        if remaining <= 0:
            break

        time.sleep(
            min(
                sleep_for,
                remaining
            )
        )

    report()

    print()
    print(
        "NDS scanner finished."
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "Stopped."
        )

    except Exception:

        DIAG["errors"] += 1

        traceback.print_exc()

        try:
            report()
        except Exception:
            pass
