# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.2
# ============================================================
#
# PAPER ONLY
#
# M30:
#   START -> H1 -> L1 -> H2 -> L2 -> H3
#   or
#   START -> L1 -> H1 -> L2 -> H2 -> L3
#
# IMPORTANT:
#
# The 3rd peak/valley is the END of the main move.
#
# After the 3rd peak/valley:
#       correction starts
#
# Hook is NOT confirmed at the 3rd point.
#
# Hook becomes CONFIRMED only when correction reaches:
#       86.4% of the START -> 3rd-point move
#
# ONLY CONFIRMED HOOKS are sent to M1.
#
# M1:
#       1 -> 2 -> 3 -> F
#
# Charts:
#       M30 START / 1 / 2 / 3 / 86.4%
#       M1 1 / 2 / 3 / F
#       Entry / SL / TP
#
# PAPER ONLY
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


# ============================================================
# VERSION / CONFIG
# ============================================================

VERSION = "4.4.2"

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

HOOK_START_LOOKBACK_HOURS = int(
    os.getenv(
        "NDS_HOOK_START_LOOKBACK_HOURS",
        "6"
    )
)

HOOK_START_LOOKBACK_M30 = (
    HOOK_START_LOOKBACK_HOURS * 2
)

M30_MIN_HOOK_RANGE_PCT = 0.20

M1_MIN_SWING_PCT = 0.07

M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

MAX_F_AGE_SECONDS = 5 * 60

MAX_HOOK_AGE_SECONDS = 6 * 60 * 60

RETRACEMENT_864 = 0.864

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_SECONDS = 60 * 60

M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60
REPORT_SECONDS = 900

RUN_SECONDS = 12 * 60

CHARTS_ENABLED = True

CHART_CANDLES_M30 = 180
CHART_CANDLES_M1 = 240

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

    "hook_start_tests": 0,
    "hook_start_selected": 0,

    "m30_hook_candidates": 0,
    "m30_structure_valid": 0,
    "m30_incomplete_hooks": 0,

    "m30_retracement_tracking": 0,
    "m30_retracement_864_reached": 0,

    "m30_confirmed_hooks": 0,

    "m1_symbols_selected": 0,
    "m1_requests": 0,
    "m1_data_ok": 0,
    "m1_short_data": 0,

    "m1_123f_candidates": 0,
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

    hook_columns = [
        ("hook_start_time", "INTEGER"),
        ("hook_start", "REAL"),
        ("retracement_864", "REAL"),
        ("confirmed_time", "INTEGER"),
        ("strategy_version", "TEXT"),
        ("hook_start_lookback_hours", "INTEGER"),
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
            assets.remove("PF_LDOUSD")
            assets.insert(0, "PF_LDOUSD")

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

        if isinstance(item, dict):

            ts = (
                item.get("time")
                or item.get("timestamp")
                or item.get("ts")
            )

            op = item.get("open") or item.get("o")
            hi = item.get("high") or item.get("h")
            lo = item.get("low") or item.get("l")
            cl = item.get("close") or item.get("c")
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

        if None in (op, hi, lo, cl):
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

        candles = parse_candles(
            response.json()
        )

        if len(candles) > limit:
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

    duration = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
    }.get(interval, 60)

    closed = [
        c for c in candles
        if c["time"] + duration <= now
    ]

    return closed[-limit:]


# ============================================================
# PIVOTS
# ============================================================

def find_pivot_highs(candles):
    result = []

    for i in range(
        PIVOT_LEFT,
        len(candles) - PIVOT_RIGHT
    ):

        p = candles[i]["high"]

        left_ok = all(
            candles[j]["high"] < p
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        )

        right_ok = all(
            candles[j]["high"] < p
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        )

        if left_ok and right_ok:
            result.append({
                "index": i,
                "time": candles[i]["time"],
                "price": p,
                "type": "H",
            })

    return result


def find_pivot_lows(candles):
    result = []

    for i in range(
        PIVOT_LEFT,
        len(candles) - PIVOT_RIGHT
    ):

        p = candles[i]["low"]

        left_ok = all(
            candles[j]["low"] > p
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        )

        right_ok = all(
            candles[j]["low"] > p
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        )

        if left_ok and right_ok:
            result.append({
                "index": i,
                "time": candles[i]["time"],
                "price": p,
                "type": "L",
            })

    return result


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
# HOOK START
# ============================================================

def select_hook_start_low(
    candles,
    h1_index
):
    DIAG["hook_start_tests"] += 1

    start = max(
        0,
        h1_index - HOOK_START_LOOKBACK_M30
    )

    window = candles[
        start:h1_index
    ]

    if not window:
        return None

    low_candle = min(
        window,
        key=lambda x: (
            x["low"],
            x["time"]
        )
    )

    DIAG["hook_start_selected"] += 1

    return {
        "index": candles.index(
            low_candle
        ),
        "time": low_candle["time"],
        "price": low_candle["low"],
        "type": "L",
    }


def select_hook_start_high(
    candles,
    l1_index
):
    DIAG["hook_start_tests"] += 1

    start = max(
        0,
        l1_index - HOOK_START_LOOKBACK_M30
    )

    window = candles[
        start:l1_index
    ]

    if not window:
        return None

    high_candle = max(
        window,
        key=lambda x: (
            x["high"],
            -x["time"]
        )
    )

    DIAG["hook_start_selected"] += 1

    return {
        "index": candles.index(
            high_candle
        ),
        "time": high_candle["time"],
        "price": high_candle["high"],
        "type": "H",
    }


# ============================================================
# 86.4%
# ============================================================

def calc_864_short(
    start,
    h3
):
    return (
        h3
        - RETRACEMENT_864 * (
            h3 - start
        )
    )


def calc_864_long(
    start,
    l3
):
    return (
        l3
        + RETRACEMENT_864 * (
            start - l3
        )
    )


# ============================================================
# M30 SHORT
#
# START -> H1 -> L1 -> H2 -> L2 -> H3
#
# H2 > H1
# L2 < L1
# H3 > H2
#
# Then correction from H3 to 86.4%
# ============================================================

def detect_short_hook(
    candles,
    pivots
):
    highs = [
        p for p in pivots
        if p["type"] == "H"
    ]

    lows = [
        p for p in pivots
        if p["type"] == "L"
    ]

    if len(highs) < 3 or len(lows) < 2:
        return None

    for h1 in reversed(highs):

        h1_index = h1["index"]

        start = select_hook_start_low(
            candles,
            h1_index
        )

        if not start:
            continue

        if start["time"] >= h1["time"]:
            continue

        l1s = [
            x for x in lows
            if h1["time"] < x["time"]
        ]

        if not l1s:
            continue

        l1 = l1s[0]

        h2s = [
            x for x in highs
            if x["time"] > l1["time"]
            and x["price"] > h1["price"]
        ]

        if not h2s:
            continue

        h2 = h2s[0]

        l2s = [
            x for x in lows
            if x["time"] > h2["time"]
            and x["price"] < l1["price"]
        ]

        if not l2s:
            continue

        l2 = l2s[0]

        h3s = [
            x for x in highs
            if x["time"] > l2["time"]
            and x["price"] > h2["price"]
        ]

        if not h3s:
            continue

        h3 = h3s[0]

        move_pct = pct_change(
            start["price"],
            h3["price"]
        )

        if move_pct < M30_MIN_HOOK_RANGE_PCT:
            continue

        DIAG["m30_hook_candidates"] += 1
        DIAG["m30_structure_valid"] += 1

        target = calc_864_short(
            start["price"],
            h3["price"]
        )

        DIAG["m30_retracement_tracking"] += 1

        h3_index = h3["index"]

        for i in range(
            h3_index + 1,
            len(candles)
        ):
            candle = candles[i]

            if candle["low"] <= target:

                DIAG[
                    "m30_retracement_864_reached"
                ] += 1

                DIAG[
                    "m30_confirmed_hooks"
                ] += 1

                return {
                    "symbol": None,
                    "side": "SHORT",

                    "hook_start": start["price"],
                    "hook_start_time": start["time"],

                    "h1": h1["price"],
                    "h1_time": h1["time"],

                    "l1": l1["price"],
                    "l1_time": l1["time"],

                    "h2": h2["price"],
                    "h2_time": h2["time"],

                    "l2": l2["price"],
                    "l2_time": l2["time"],

                    "h3": h3["price"],
                    "h3_time": h3["time"],

                    "target": target,

                    "confirmed_time": candle["time"],

                    "hook_time": h3["time"],
                }

        DIAG["m30_incomplete_hooks"] += 1

    return None


# ============================================================
# M30 LONG
#
# START -> L1 -> H1 -> L2 -> H2 -> L3
#
# L2 < L1
# H2 > H1
# L3 < L2
#
# Then correction from L3 to 86.4%
# ============================================================

def detect_long_hook(
    candles,
    pivots
):
    highs = [
        p for p in pivots
        if p["type"] == "H"
    ]

    lows = [
        p for p in pivots
        if p["type"] == "L"
    ]

    if len(highs) < 2 or len(lows) < 3:
        return None

    for l1 in reversed(lows):

        l1_index = l1["index"]

        start = select_hook_start_high(
            candles,
            l1_index
        )

        if not start:
            continue

        if start["time"] >= l1["time"]:
            continue

        h1s = [
            x for x in highs
            if x["time"] > l1["time"]
        ]

        if not h1s:
            continue

        h1 = h1s[0]

        l2s = [
            x for x in lows
            if x["time"] > h1["time"]
            and x["price"] < l1["price"]
        ]

        if not l2s:
            continue

        l2 = l2s[0]

        h2s = [
            x for x in highs
            if x["time"] > l2["time"]
            and x["price"] > h1["price"]
        ]

        if not h2s:
            continue

        h2 = h2s[0]

        l3s = [
            x for x in lows
            if x["time"] > h2["time"]
            and x["price"] < l2["price"]
        ]

        if not l3s:
            continue

        l3 = l3s[0]

        move_pct = pct_change(
            start["price"],
            l3["price"]
        )

        if move_pct < M30_MIN_HOOK_RANGE_PCT:
            continue

        DIAG["m30_hook_candidates"] += 1
        DIAG["m30_structure_valid"] += 1

        target = calc_864_long(
            start["price"],
            l3["price"]
        )

        DIAG["m30_retracement_tracking"] += 1

        l3_index = l3["index"]

        for i in range(
            l3_index + 1,
            len(candles)
        ):
            candle = candles[i]

            if candle["high"] >= target:

                DIAG[
                    "m30_retracement_864_reached"
                ] += 1

                DIAG[
                    "m30_confirmed_hooks"
                ] += 1

                return {
                    "symbol": None,
                    "side": "LONG",

                    "hook_start": start["price"],
                    "hook_start_time": start["time"],

                    "l1": l1["price"],
                    "l1_time": l1["time"],

                    "h1": h1["price"],
                    "h1_time": h1["time"],

                    "l2": l2["price"],
                    "l2_time": l2["time"],

                    "h2": h2["price"],
                    "h2_time": h2["time"],

                    "l3": l3["price"],
                    "l3_time": l3["time"],

                    "target": target,

                    "confirmed_time": candle["time"],

                    "hook_time": l3["time"],
                }

        DIAG["m30_incomplete_hooks"] += 1

    return None


# ============================================================
# M30 DETECTOR
# ============================================================

def detect_m30_hook(
    symbol,
    candles
):
    highs = find_pivot_highs(candles)
    lows = find_pivot_lows(candles)

    DIAG["m30_pivot_highs"] += len(highs)
    DIAG["m30_pivot_lows"] += len(lows)

    pivots = merge_pivots(
        highs,
        lows
    )

    short_hook = detect_short_hook(
        candles,
        pivots
    )

    if short_hook:
        short_hook["symbol"] = symbol
        return short_hook

    long_hook = detect_long_hook(
        candles,
        pivots
    )

    if long_hook:
        long_hook["symbol"] = symbol
        return long_hook

    return None


# ============================================================
# SAVE / REACTIVATE HOOK
# ============================================================

def save_hook(hook):
    conn = db_connect()

    try:
        now = utc_now_ts()

        existing = conn.execute("""
            SELECT *
            FROM hooks
            WHERE symbol = ?
              AND side = ?
              AND hook_start_time = ?
              AND hook_time = ?
              AND strategy_version = ?
            ORDER BY id DESC
            LIMIT 1
        """, (
            hook["symbol"],
            hook["side"],
            hook["hook_start_time"],
            hook["hook_time"],
            VERSION,
        )).fetchone()

        if existing:

            conn.execute("""
                UPDATE hooks
                SET active = 1,
                    invalidated = 0,
                    confirmed_time = ?,
                    target = ?
                WHERE id = ?
            """, (
                hook["confirmed_time"],
                hook["target"],
                existing["id"],
            ))

            conn.commit()

            return int(existing["id"])

        cur = conn.execute("""
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
                ?, ?, ?, ?, ?, ?
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

            now,

            hook["hook_start_time"],
            hook["hook_start"],
            RETRACEMENT_864,
            hook["confirmed_time"],
            VERSION,
            HOOK_START_LOOKBACK_HOURS,
        ))

        conn.commit()

        return int(cur.lastrowid)

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
              AND strategy_version = ?
            ORDER BY confirmed_time DESC
        """, (VERSION,)).fetchall()

        return rows

    finally:
        conn.close()


def deactivate_old_hooks():
    cutoff = utc_now_ts() - MAX_HOOK_AGE_SECONDS

    conn = db_connect()

    try:
        conn.execute("""
            UPDATE hooks
            SET active = 0,
                invalidated = 1
            WHERE active = 1
              AND strategy_version = ?
              AND hook_time < ?
        """, (
            VERSION,
            cutoff,
        ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# M1 PIVOTS
# ============================================================

def m1_pivots(candles):
    highs = find_pivot_highs(candles)
    lows = find_pivot_lows(candles)

    return merge_pivots(
        highs,
        lows
    )


def min_swing_ok(a, b):
    return pct_change(
        a,
        b
    ) >= M1_MIN_SWING_PCT


# ============================================================
# M1 123F
# ============================================================

def detect_m1_123f(
    hook,
    candles
):
    if not candles:
        return None

    DIAG["m1_123f_candidates"] += 1

    pivots = m1_pivots(candles)

    if len(pivots) < 7:
        return None

    now = utc_now_ts()

    side = hook["side"]

    # --------------------------------------------------------
    # SHORT
    #
    # 1 = High
    # 2 = Low
    # 3 = High
    # F = Low
    #
    # Current price progression:
    #     1 < 2 < 3 < F
    #
    # --------------------------------------------------------

    if side == "SHORT":

        for i in range(
            len(pivots) - 7,
            -1,
            -1
        ):

            seq = pivots[
                i:i + 7
            ]

            types = [
                p["type"]
                for p in seq
            ]

            expected = [
                "H",
                "L",
                "H",
                "L",
                "H",
                "L",
                "H",
            ]

            if types != expected:
                continue

            p1 = seq[0]
            p2 = seq[2]
            p3 = seq[4]
            f = seq[6]

            if not (
                p1["price"]
                < p2["price"]
                < p3["price"]
                < f["price"]
            ):
                continue

            if not min_swing_ok(
                p1["price"],
                p2["price"]
            ):
                continue

            if not min_swing_ok(
                p2["price"],
                p3["price"]
            ):
                continue

            if not min_swing_ok(
                p3["price"],
                f["price"]
            ):
                continue

            m30_ref = hook["h3"]

            if pct_change(
                m30_ref,
                f["price"]
            ) > M1_MAX_STRUCTURE_DISTANCE_PCT:
                continue

            if now - f["time"] > MAX_F_AGE_SECONDS:
                continue

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
    # 2 = High
    # 3 = Low
    # F = High
    #
    # 1 > 2 > 3 > F
    # --------------------------------------------------------

    if side == "LONG":

        for i in range(
            len(pivots) - 7,
            -1,
            -1
        ):

            seq = pivots[
                i:i + 7
            ]

            types = [
                p["type"]
                for p in seq
            ]

            expected = [
                "L",
                "H",
                "L",
                "H",
                "L",
                "H",
                "L",
            ]

            if types != expected:
                continue

            p1 = seq[0]
            p2 = seq[2]
            p3 = seq[4]
            f = seq[6]

            if not (
                p1["price"]
                > p2["price"]
                > p3["price"]
                > f["price"]
            ):
                continue

            if not min_swing_ok(
                p1["price"],
                p2["price"]
            ):
                continue

            if not min_swing_ok(
                p2["price"],
                p3["price"]
            ):
                continue

            if not min_swing_ok(
                p3["price"],
                f["price"]
            ):
                continue

            m30_ref = hook["l3"]

            if pct_change(
                m30_ref,
                f["price"]
            ) > M1_MAX_STRUCTURE_DISTANCE_PCT:
                continue

            if now - f["time"] > MAX_F_AGE_SECONDS:
                continue

            DIAG["m1_123f_long"] += 1

            return {
                "side": "LONG",

                "p1": p1,
                "p2": p2,
                "p3": p3,
                "f": f,
            }

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    side,
    candles,
    f
):
    price = f["price"]

    pivots = m1_pivots(candles)

    before = [
        p for p in pivots
        if p["time"] < f["time"]
    ]

    if side == "SHORT":

        lows = [
            p["price"]
            for p in before
            if p["type"] == "L"
            and p["price"] < price
        ]

        highs = [
            p["price"]
            for p in before
            if p["type"] == "H"
            and p["price"] > price
        ]

        if not lows or not highs:
            return None

        tp = max(lows)
        sl = min(highs)

    else:

        highs = [
            p["price"]
            for p in before
            if p["type"] == "H"
            and p["price"] > price
        ]

        lows = [
            p["price"]
            for p in before
            if p["type"] == "L"
            and p["price"] < price
        ]

        if not highs or not lows:
            return None

        tp = min(highs)
        sl = max(lows)

    if side == "SHORT":

        if not (
            tp < price < sl
        ):
            return None

    else:

        if not (
            sl < price < tp
        ):
            return None

    return {
        "entry": price,
        "sl": sl,
        "tp": tp,
    }


# ============================================================
# CHART HELPERS
# ============================================================

def candle_plot(
    ax,
    candles
):
    for i, c in enumerate(candles):

        o = c["open"]
        h = c["high"]
        l = c["low"]
        cl = c["close"]

        body_low = min(o, cl)
        body_high = max(o, cl)
        body_height = max(
            body_high - body_low,
            abs(o) * 0.000001
        )

        ax.vlines(
            i,
            l,
            h,
            linewidth=0.8
        )

        ax.add_patch(
            plt.Rectangle(
                (
                    i - 0.32,
                    body_low
                ),
                0.64,
                body_height,
                fill=False,
                linewidth=0.8
            )
        )


def time_to_index(
    candles,
    timestamp
):
    if timestamp is None:
        return None

    best = None
    best_distance = None

    for i, c in enumerate(candles):

        d = abs(
            c["time"] - timestamp
        )

        if (
            best_distance is None
            or d < best_distance
        ):
            best = i
            best_distance = d

    return best


# ============================================================
# M30 CHART
# ============================================================

def draw_m30_chart(
    symbol,
    candles,
    hook
):
    if not CHARTS_ENABLED:
        return None

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    candles = candles[
        -CHART_CANDLES_M30:
    ]

    if not candles:
        return None

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    candle_plot(
        ax,
        candles
    )

    points = []

    labels = []

    if hook["side"] == "SHORT":

        raw_points = [
            (
                "START",
                hook["hook_start_time"],
                hook["hook_start"]
            ),
            (
                "H1",
                hook["h1_time"],
                hook["h1"]
            ),
            (
                "L1",
                hook["l1_time"],
                hook["l1"]
            ),
            (
                "H2",
                hook["h2_time"],
                hook["h2"]
            ),
            (
                "L2",
                hook["l2_time"],
                hook["l2"]
            ),
            (
                "H3",
                hook["h3_time"],
                hook["h3"]
            ),
            (
                "86.4%",
                hook["confirmed_time"],
                hook["target"]
            ),
        ]

    else:

        raw_points = [
            (
                "START",
                hook["hook_start_time"],
                hook["hook_start"]
            ),
            (
                "L1",
                hook["l1_time"],
                hook["l1"]
            ),
            (
                "H1",
                hook["h1_time"],
                hook["h1"]
            ),
            (
                "L2",
                hook["l2_time"],
                hook["l2"]
            ),
            (
                "H2",
                hook["h2_time"],
                hook["h2"]
            ),
            (
                "L3",
                hook["l3_time"],
                hook["l3"]
            ),
            (
                "86.4%",
                hook["confirmed_time"],
                hook["target"]
            ),
        ]

    for label, ts, price in raw_points:

        idx = time_to_index(
            candles,
            ts
        )

        if idx is None:
            continue

        points.append(
            (idx, price)
        )

        labels.append(
            (
                label,
                idx,
                price
            )
        )

    if len(points) >= 2:

        xs = [
            p[0]
            for p in points
        ]

        ys = [
            p[1]
            for p in points
        ]

        ax.plot(
            xs,
            ys,
            linewidth=1.5
        )

    for label, x, y in labels:

        ax.scatter(
            [x],
            [y],
            s=55,
            zorder=5
        )

        ax.annotate(
            label,
            (
                x,
                y
            ),
            xytext=(
                5,
                8
            ),
            textcoords="offset points",
            fontsize=9,
            fontweight="bold"
        )

    ax.axhline(
        hook["target"],
        linestyle="--",
        linewidth=1
    )

    ax.set_title(
        f"NDS M30 HOOK | {symbol} | "
        f"{hook['side']} | 86.4% CONFIRMED"
    )

    ax.set_xlabel(
        "M30 candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.2
    )

    fig.tight_layout()

    path = os.path.join(
        CHART_DIR,
        (
            f"{symbol}_M30_"
            f"{hook['side']}_"
            f"{hook['confirmed_time']}.png"
        )
    )

    fig.savefig(
        path,
        dpi=140
    )

    plt.close(fig)

    return path


# ============================================================
# FULL M30 + M1 CHART
# ============================================================

def draw_full_chart(
    symbol,
    m30_candles,
    m1_candles,
    hook,
    structure,
    levels
):
    if not CHARTS_ENABLED:
        return None

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    m30 = m30_candles[
        -CHART_CANDLES_M30:
    ]

    m1 = m1_candles[
        -CHART_CANDLES_M1:
    ]

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(16, 12)
    )

    ax30 = axes[0]
    ax1 = axes[1]

    # --------------------------------------------------------
    # M30
    # --------------------------------------------------------

    candle_plot(
        ax30,
        m30
    )

    if hook["side"] == "SHORT":

        m30_points = [
            (
                "START",
                hook["hook_start_time"],
                hook["hook_start"]
            ),
            (
                "H1",
                hook["h1_time"],
                hook["h1"]
            ),
            (
                "L1",
                hook["l1_time"],
                hook["l1"]
            ),
            (
                "H2",
                hook["h2_time"],
                hook["h2"]
            ),
            (
                "L2",
                hook["l2_time"],
                hook["l2"]
            ),
            (
                "H3",
                hook["h3_time"],
                hook["h3"]
            ),
            (
                "86.4%",
                hook["confirmed_time"],
                hook["target"]
            ),
        ]

    else:

        m30_points = [
            (
                "START",
                hook["hook_start_time"],
                hook["hook_start"]
            ),
            (
                "L1",
                hook["l1_time"],
                hook["l1"]
            ),
            (
                "H1",
                hook["h1_time"],
                hook["h1"]
            ),
            (
                "L2",
                hook["l2_time"],
                hook["l2"]
            ),
            (
                "H2",
                hook["h2_time"],
                hook["h2"]
            ),
            (
                "L3",
                hook["l3_time"],
                hook["l3"]
            ),
            (
                "86.4%",
                hook["confirmed_time"],
                hook["target"]
            ),
        ]

    m30_xy = []

    for label, ts, price in m30_points:

        x = time_to_index(
            m30,
            ts
        )

        if x is None:
            continue

        m30_xy.append(
            (x, price)
        )

        ax30.scatter(
            [x],
            [price],
            s=65,
            zorder=5
        )

        ax30.annotate(
            label,
            (x, price),
            xytext=(
                5,
                8
            ),
            textcoords="offset points",
            fontsize=9,
            fontweight="bold"
        )

    if len(m30_xy) > 1:

        ax30.plot(
            [p[0] for p in m30_xy],
            [p[1] for p in m30_xy],
            linewidth=1.5
        )

    ax30.axhline(
        hook["target"],
        linestyle="--",
        linewidth=1
    )

    ax30.set_title(
        f"{symbol} | M30 NDS HOOK | "
        f"{hook['side']} | 86.4% CONFIRMED"
    )

    ax30.grid(
        alpha=0.2
    )

    # --------------------------------------------------------
    # M1
    # --------------------------------------------------------

    candle_plot(
        ax1,
        m1
    )

    m1_points = [
        (
            "1",
            structure["p1"]["time"],
            structure["p1"]["price"]
        ),
        (
            "2",
            structure["p2"]["time"],
            structure["p2"]["price"]
        ),
        (
            "3",
            structure["p3"]["time"],
            structure["p3"]["price"]
        ),
        (
            "F",
            structure["f"]["time"],
            structure["f"]["price"]
        ),
    ]

    m1_xy = []

    for label, ts, price in m1_points:

        x = time_to_index(
            m1,
            ts
        )

        if x is None:
            continue

        m1_xy.append(
            (x, price)
        )

        ax1.scatter(
            [x],
            [price],
            s=65,
            zorder=5
        )

        ax1.annotate(
            label,
            (x, price),
            xytext=(
                5,
                8
            ),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold"
        )

    if len(m1_xy) > 1:

        ax1.plot(
            [p[0] for p in m1_xy],
            [p[1] for p in m1_xy],
            linewidth=1.5
        )

    entry = levels["entry"]
    sl = levels["sl"]
    tp = levels["tp"]

    ax1.axhline(
        entry,
        linestyle="-",
        linewidth=1
    )

    ax1.axhline(
        sl,
        linestyle="--",
        linewidth=1
    )

    ax1.axhline(
        tp,
        linestyle="--",
        linewidth=1
    )

    ax1.text(
        0.01,
        entry,
        f" Entry {format_price(entry)}",
        transform=ax1.get_yaxis_transform(),
        fontsize=9,
        fontweight="bold"
    )

    ax1.text(
        0.01,
        sl,
        f" SL {format_price(sl)}",
        transform=ax1.get_yaxis_transform(),
        fontsize=9,
        fontweight="bold"
    )

    ax1.text(
        0.01,
        tp,
        f" TP {format_price(tp)}",
        transform=ax1.get_yaxis_transform(),
        fontsize=9,
        fontweight="bold"
    )

    ax1.set_title(
        f"{symbol} | M1 1-2-3-F | "
        f"{hook['side']}"
    )

    ax1.grid(
        alpha=0.2
    )

    fig.suptitle(
        f"NDS SIGNAL | {symbol} | "
        f"{hook['side']}",
        fontsize=14,
        fontweight="bold"
    )

    fig.tight_layout()

    path = os.path.join(
        CHART_DIR,
        (
            f"{symbol}_"
            f"{hook['side']}_"
            f"{structure['f']['time']}.png"
        )
    )

    fig.savefig(
        path,
        dpi=140
    )

    plt.close(fig)

    DIAG["charts_created"] += 1

    return path


# ============================================================
# SIGNAL DATABASE
# ============================================================

def signal_exists(
    symbol,
    side,
    f_time
):
    event_key = (
        f"{VERSION}|"
        f"{symbol}|"
        f"{side}|"
        f"{f_time}"
    )

    conn = db_connect()

    try:
        row = conn.execute("""
            SELECT id
            FROM signals
            WHERE event_key = ?
            LIMIT 1
        """, (
            event_key,
        )).fetchone()

        return row is not None

    finally:
        conn.close()


def count_open_trades():
    conn = db_connect()

    try:
        row = conn.execute("""
            SELECT COUNT(*) AS n
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()

        return int(row["n"])

    finally:
        conn.close()


def save_signal(
    hook,
    structure,
    levels,
    chart_path
):
    f_time = structure["f"]["time"]

    event_key = (
        f"{VERSION}|"
        f"{hook['symbol']}|"
        f"{hook['side']}|"
        f"{f_time}"
    )

    conn = db_connect()

    try:

        if conn.execute("""
            SELECT id
            FROM signals
            WHERE event_key = ?
        """, (
            event_key,
        )).fetchone():

            return None

        now = utc_now_ts()

        cur = conn.execute("""
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
                chart_path
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                'OPEN', ?, ?, ?, ?
            )
        """, (
            event_key,
            hook["symbol"],
            hook["side"],
            hook["id"],
            hook["hook_time"],
            f_time,
            levels["entry"],
            levels["sl"],
            levels["tp"],
            structure["f"]["price"],
            hook["target"],
            now,
            VERSION,
            f_time,
            chart_path,
        ))

        conn.commit()

        return int(cur.lastrowid)

    finally:
        conn.close()


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

def signal_message(
    hook,
    structure,
    levels
):
    side_icon = (
        "🔴"
        if hook["side"] == "SHORT"
        else "🟢"
    )

    entry = levels["entry"]
    sl = levels["sl"]
    tp = levels["tp"]

    return (
        f"{side_icon} "
        f"<b>{hook['side']}</b>\n"
        f"<b>{hook['symbol']}</b>\n\n"
        f"M30 Hook: <b>86.4% CONFIRMED</b>\n"
        f"M1: <b>1 → 2 → 3 → F</b>\n\n"
        f"Entry: <b>{format_price(entry)}</b>\n"
        f"SL: <b>{format_price(sl)}</b>\n"
        f"TP: <b>{format_price(tp)}</b>\n\n"
        f"F: {format_price(structure['f']['price'])}\n"
        f"Time: {utc_string(structure['f']['time'])}"
    )


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(
    symbol
):
    DIAG["m30_requests"] += 1

    candles = get_closed_candles(
        symbol,
        M30_INTERVAL,
        M30_CANDLES
    )

    if not candles:
        DIAG["m30_short_data"] += 1
        return None

    if len(candles) < 80:
        DIAG["m30_short_data"] += 1
        return None

    DIAG["m30_data_ok"] += 1

    hook = detect_m30_hook(
        symbol,
        candles
    )

    if not hook:
        return None

    hook_id = save_hook(
        hook
    )

    hook["id"] = hook_id

    return {
        "hook": hook,
        "candles": candles,
    }


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(
    hook_row
):
    symbol = hook_row["symbol"]

    DIAG["m1_symbols_selected"] += 1

    # --------------------------------------------------------
    # IMPORTANT:
    # Request counter is incremented BEFORE network call.
    # This makes the diagnostic truthful.
    # --------------------------------------------------------

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

    hook = dict(hook_row)

    structure = detect_m1_123f(
        hook,
        candles
    )

    if not structure:
        return None

    levels = calculate_trade_levels(
        hook["side"],
        candles,
        structure["f"]
    )

    if not levels:
        return None

    if count_open_trades() >= MAX_OPEN_TRADES:
        return None

    if signal_exists(
        symbol,
        hook["side"],
        structure["f"]["time"]
    ):
        return None

    m30_candles = get_closed_candles(
        symbol,
        M30_INTERVAL,
        M30_CANDLES
    )

    if not m30_candles:
        return None

    chart_path = draw_full_chart(
        symbol,
        m30_candles,
        candles,
        hook,
        structure,
        levels
    )

    signal_id = save_signal(
        hook,
        structure,
        levels,
        chart_path
    )

    if signal_id is None:
        return None

    DIAG["new_signals"] += 1

    send_telegram(
        signal_message(
            hook,
            structure,
            levels
        )
    )

    if chart_path:
        send_telegram_photo(
            chart_path,
            (
                f"NDS {hook['side']} "
                f"{symbol} | "
                f"M30 86.4% + M1 123F"
            )
        )

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "side": hook["side"],
        "levels": levels,
    }


# ============================================================
# OPEN TRADE MONITOR
# ============================================================

def update_open_trades():
    conn = db_connect()

    try:

        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
        """).fetchall()

        for row in rows:

            candles = get_closed_candles(
                row["symbol"],
                M1_INTERVAL,
                5
            )

            if not candles:
                continue

            price = candles[-1]["close"]

            entry = row["entry"]
            sl = row["sl"]
            tp = row["tp"]

            side = row["side"]

            pnl = (
                (price - entry) / entry * 100
                if side == "LONG"
                else
                (entry - price) / entry * 100
            )

            reason = None

            if side == "LONG":

                if price <= sl:
                    reason = "SL"

                elif price >= tp:
                    reason = "TP"

            else:

                if price >= sl:
                    reason = "SL"

                elif price <= tp:
                    reason = "TP"

            if reason:

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

                send_telegram(
                    (
                        f"{'🟢' if pnl >= 0 else '🔴'} "
                        f"<b>{row['symbol']}</b> "
                        f"{side} CLOSED\n"
                        f"Exit: <b>{reason}</b>\n"
                        f"Price: <b>{format_price(price)}</b>\n"
                        f"P/L: <b>{format_pct(pnl)}</b>"
                    )
                )

            else:

                conn.execute("""
                    UPDATE signals
                    SET last_price = ?,
                        last_pnl_pct = ?
                    WHERE id = ?
                """, (
                    price,
                    pnl,
                    row["id"],
                ))

        conn.commit()

    finally:
        conn.close()


# ============================================================
# REPORT
# ============================================================

def report():
    conn = db_connect()

    try:

        open_count = conn.execute("""
            SELECT COUNT(*)
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()[0]

        closed = conn.execute("""
            SELECT
                COUNT(*) AS n,
                SUM(
                    CASE
                        WHEN pnl_pct > 0
                        THEN 1 ELSE 0
                    END
                ) AS wins,
                SUM(
                    CASE
                        WHEN pnl_pct < 0
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

    message = (
        f"🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_string()}\n\n"

        f"<b>M30</b>\n"
        f"Requests: {DIAG['m30_requests']}\n"
        f"Data OK: {DIAG['m30_data_ok']}\n"
        f"Short data: {DIAG['m30_short_data']}\n"
        f"Pivot Highs: {DIAG['m30_pivot_highs']}\n"
        f"Pivot Lows: {DIAG['m30_pivot_lows']}\n"
        f"Hook Candidates: {DIAG['m30_hook_candidates']}\n"
        f"Structure Valid: {DIAG['m30_structure_valid']}\n"
        f"Incomplete: {DIAG['m30_incomplete_hooks']}\n"
        f"Retracement Tracking: "
        f"{DIAG['m30_retracement_tracking']}\n"
        f"86.4% Reached: "
        f"{DIAG['m30_retracement_864_reached']}\n"
        f"Confirmed Hooks: "
        f"{DIAG['m30_confirmed_hooks']}\n\n"

        f"<b>M1 PIPELINE</b>\n"
        f"Symbols Selected: "
        f"{DIAG['m1_symbols_selected']}\n"
        f"Requests: {DIAG['m1_requests']}\n"
        f"Data OK: {DIAG['m1_data_ok']}\n"
        f"Short data: {DIAG['m1_short_data']}\n"
        f"123F Candidates: "
        f"{DIAG['m1_123f_candidates']}\n"
        f"123F SHORT: "
        f"{DIAG['m1_123f_short']}\n"
        f"123F LONG: "
        f"{DIAG['m1_123f_long']}\n\n"

        f"<b>TRADES</b>\n"
        f"New Signals: {DIAG['new_signals']}\n"
        f"Open Trades: {open_count}\n"
        f"Closed: {closed['n'] or 0}\n"
        f"Wins: {closed['wins'] or 0}\n"
        f"Losses: {closed['losses'] or 0}\n"
        f"PnL: {format_pct(closed['pnl'] or 0)}\n\n"

        f"Charts: {DIAG['charts_created']}\n"
        f"Errors: {DIAG['errors']}"
    )

    print(
        "\n" +
        message.replace(
            "<b>",
            ""
        ).replace(
            "</b>",
            ""
        )
    )

    send_telegram(
        message
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

    deactivate_legacy_hooks()

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    assets = discover_assets()

    if not assets:
        print(
            "No Kraken assets found."
        )
        return

    print(
        f"NDS {VERSION} STARTED"
    )

    print(
        f"Assets: {len(assets)}"
    )

    start_time = time.time()

    last_m30_scan = 0
    last_m1_scan = 0
    last_report = 0

    # --------------------------------------------------------
    # MAIN LOOP
    # --------------------------------------------------------

    while (
        time.time() - start_time
        < RUN_SECONDS
    ):

        now = time.time()

        try:

            # =================================================
            # M30
            # =================================================

            if (
                now - last_m30_scan
                >= M30_SCAN_SECONDS
            ):

                confirmed_this_cycle = []

                for symbol in assets:

                    try:

                        result = scan_m30(
                            symbol
                        )

                        if result:

                            confirmed_this_cycle.append(
                                result["hook"]
                            )

                    except Exception:

                        DIAG["errors"] += 1

                        traceback.print_exc()

                last_m30_scan = now

                # ------------------------------------------------
                # IMPORTANT:
                #
                # Do NOT depend on scan_m1() rediscovering the
                # hook indirectly.
                #
                # All active confirmed hooks are explicitly
                # selected below.
                # ------------------------------------------------

                print(
                    "M30 scan completed | "
                    f"Confirmed this cycle: "
                    f"{len(confirmed_this_cycle)}"
                )

            # =================================================
            # EXPIRE OLD HOOKS
            # =================================================

            deactivate_old_hooks()

            # =================================================
            # M1
            # =================================================

            if (
                now - last_m1_scan
                >= M1_SCAN_SECONDS
            ):

                active_hooks = get_active_hooks()

                print(
                    "M1 selected hooks: "
                    f"{len(active_hooks)}"
                )

                for hook in active_hooks:

                    try:

                        scan_m1(
                            hook
                        )

                    except Exception:

                        DIAG["errors"] += 1

                        traceback.print_exc()

                update_open_trades()

                last_m1_scan = now

            # =================================================
            # REPORT
            # =================================================

            if (
                now - last_report
                >= REPORT_SECONDS
            ):

                report()

                last_report = now

            time.sleep(2)

        except KeyboardInterrupt:
            break

        except Exception:

            DIAG["errors"] += 1

            traceback.print_exc()

            time.sleep(5)

    # ========================================================
    # FINAL REPORT
    # ========================================================

    update_open_trades()

    report()

    print(
        "NDS scanner finished."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
