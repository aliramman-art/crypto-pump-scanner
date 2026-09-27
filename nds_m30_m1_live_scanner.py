# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.4.0
# ============================================================
#
# PAPER ONLY
#
# ============================================================
# RUNTIME MODEL
# ============================================================
#
# GitHub Actions:
#   Run every 15 minutes
#
# Python:
#   Run for 12 minutes
#   Then exit cleanly
#
# M30 scan:
#   Every 5 minutes
#
# M1 scan:
#   Every 1 minute
#
# Report:
#   Every 15 minutes
#
# ============================================================
# NEW IN VERSION 4.4.0
# ============================================================
#
# 1. M30 Hook charts are generated.
#
# 2. M30 Hook points are displayed:
#
#    LONG / NEGATIVE HOOK:
#
#       L1
#        \
#         H1
#          \
#           L2
#            \
#             H2
#              \
#               L3
#
#    SHORT / POSITIVE HOOK:
#
#       H1
#        \
#         L1
#          \
#           H2
#            \
#             L2
#              \
#               H3
#
# 3. Hook points are labeled:
#       H1 / L1 / H2 / L2 / H3 / L3
#
# 4. Entry / SL / TP are shown on chart.
#
# 5. M30 chart is sent to Telegram using sendPhoto.
#
# 6. New signal:
#       -> send M30 Hook chart
#
# 7. No new signal:
#       -> send active M30 Hook charts
#
# 8. Open trade report now contains:
#
#       Current
#       P/L
#       SL distance %
#       TP distance %
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
# VERSION / MODE
# ============================================================

VERSION = "4.4.0"

PAPER_ONLY = True


# ============================================================
# DATABASE / CHARTS
# ============================================================

DB_FILE = "nds_m30_m1_v43.db"

CHART_DIR = "nds_charts"

os.makedirs(CHART_DIR, exist_ok=True)


# ============================================================
# KRAKEN
# ============================================================

KRAKEN_CHART_BASE = (
    "https://futures.kraken.com/api/charts/v1"
)

KRAKEN_REST_BASE = (
    "https://futures.kraken.com/derivatives/api/v3"
)


# ============================================================
# TELEGRAM
# ============================================================

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


# ============================================================
# ASSET SETTINGS
# ============================================================

TARGET_ASSETS = 100

ASSET_REFRESH_SECONDS = 3600

M30_INTERVAL = "30m"

M1_INTERVAL = "1m"

M30_CANDLES = 320

M1_CANDLES = 1500


# ============================================================
# SCAN INTERVALS
# ============================================================

M30_SCAN_SECONDS = 300

M1_SCAN_SECONDS = 60

REPORT_INTERVAL_SECONDS = 900


# ============================================================
# STRUCTURE SETTINGS
# ============================================================

PIVOT_LEFT = 2

PIVOT_RIGHT = 2

M30_MIN_HOOK_RANGE_PCT = 0.20

M1_MIN_SWING_PCT = 0.07

M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

MAX_HOOK_AGE_SECONDS = 6 * 3600

M1_F_MAX_AGE_SECONDS = 5 * 60


# ============================================================
# TRADE SETTINGS
# ============================================================

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_SECONDS = 60 * 60

SL_BUFFER_PCT = 0.15


# ============================================================
# CHART SETTINGS
# ============================================================

ENABLE_CHARTS = True

CHART_CANDLES = 160

ACTIVE_HOOK_CHART_CANDLES = 120

CHART_DPI = 130


# ============================================================
# TELEGRAM CHART SETTINGS
# ============================================================

SEND_ACTIVE_HOOK_CHARTS = True

MAX_ACTIVE_HOOK_CHARTS_PER_REPORT = 20


# ============================================================
# HTTP SETTINGS
# ============================================================

HTTP_TIMEOUT = 15

REQUEST_SLEEP = 0.05

ERROR_SLEEP = 10


# ============================================================
# RUNTIME
# ============================================================

RUN_DURATION_SECONDS = 12 * 60


# ============================================================
# GLOBAL STATE
# ============================================================

session = requests.Session()

assets = []

last_asset_refresh = 0

last_m30_scan = 0

last_m1_scan = 0

last_report = 0


last_m30_result = {
    "assets": 0,
    "new_long": 0,
    "new_short": 0,
}


last_m1_result = {
    "new_long": 0,
    "new_short": 0,
}


# ============================================================
# NEW SIGNAL STATE
# ============================================================

# Used by report logic:
#
# If a new signal appeared since the previous report,
# its M30 chart has already been sent.
#
# If no new signal appeared, active Hook charts are sent.

new_signal_since_report = False


# ============================================================
# TIME
# ============================================================

def now_ts():

    return int(time.time())


def utc_now_text():

    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# DATABASE
# ============================================================

def db():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time INTEGER,
            run_type TEXT,
            configured INTEGER DEFAULT 0,
            assets INTEGER DEFAULT 0,
            new_long INTEGER DEFAULT 0,
            new_short INTEGER DEFAULT 0,
            error TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            signal_time INTEGER NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'OPEN',
            exit_time INTEGER,
            exit_price REAL,
            pnl_pct REAL,
            exit_reason TEXT,
            hook_time INTEGER,
            f_time INTEGER,
            hook_target REAL,
            chart_path TEXT,
            created_at INTEGER NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            event_key TEXT NOT NULL,
            event_time INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(symbol, side, event_key)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            hook_time INTEGER NOT NULL,
            h1 REAL,
            l1 REAL,
            h2 REAL,
            l2 REAL,
            h3 REAL,
            l3 REAL,
            target REAL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at INTEGER NOT NULL,
            invalidated_at INTEGER
        )
    """)

    conn.commit()

    conn.close()


# ============================================================
# DATABASE MIGRATION
# ============================================================

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
        row[1]
        for row in cur.fetchall()
    }

    if column not in columns:

        cur.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


def migrate_db():

    conn = db()

    migrations = [

        (
            "scanner_runs",
            "configured",
            "INTEGER DEFAULT 0"
        ),

        (
            "signals",
            "hook_time",
            "INTEGER"
        ),

        (
            "signals",
            "f_time",
            "INTEGER"
        ),

        (
            "signals",
            "hook_target",
            "REAL"
        ),

        (
            "signals",
            "chart_path",
            "TEXT"
        ),

        (
            "signals",
            "exit_reason",
            "TEXT"
        ),

        (
            "hooks",
            "h1",
            "REAL"
        ),

        (
            "hooks",
            "l1",
            "REAL"
        ),

        (
            "hooks",
            "h2",
            "REAL"
        ),

        (
            "hooks",
            "l2",
            "REAL"
        ),

        (
            "hooks",
            "h3",
            "REAL"
        ),

        (
            "hooks",
            "l3",
            "REAL"
        ),

        (
            "hooks",
            "target",
            "REAL"
        ),

        (
            "hooks",
            "status",
            "TEXT DEFAULT 'ACTIVE'"
        ),

        (
            "hooks",
            "invalidated_at",
            "INTEGER"
        ),

    ]

    for table, column, definition in migrations:

        ensure_column(
            conn,
            table,
            column,
            definition
        )

    conn.commit()

    conn.close()


# ============================================================
# TELEGRAM TEXT
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
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

        response = session.post(
            url,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )

        return response.ok

    except Exception:

        return False


# ============================================================
# TELEGRAM PHOTO
# ============================================================

def send_telegram_photo(
    image_path,
    caption=""
):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    if not image_path:
        return False

    if not os.path.exists(image_path):
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:

        with open(
            image_path,
            "rb"
        ) as photo:

            files = {
                "photo": photo
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            response = session.post(
                url,
                data=data,
                files=files,
                timeout=HTTP_TIMEOUT,
            )

        return response.ok

    except Exception as exc:

        print(
            f"[TELEGRAM PHOTO ERROR] {exc}"
        )

        return False


# ============================================================
# KRAKEN CANDLES
# ============================================================

def fetch_candles(
    symbol,
    interval,
    count
):

    url = (
        f"{KRAKEN_CHART_BASE}/trade/"
        f"{symbol}/{interval}"
    )

    try:

        response = session.get(
            url,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        rows = data.get("candles")

        if rows is None:

            rows = data.get("data")

        if not rows:

            return pd.DataFrame()

        normalized = []

        for row in rows:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("t")
                )

                o = row.get(
                    "open",
                    row.get("o")
                )

                h = row.get(
                    "high",
                    row.get("h")
                )

                l = row.get(
                    "low",
                    row.get("l")
                )

                c = row.get(
                    "close",
                    row.get("c")
                )

                v = row.get(
                    "volume",
                    row.get("v", 0)
                )

            else:

                if len(row) < 5:
                    continue

                ts = row[0]

                o = row[1]

                h = row[2]

                l = row[3]

                c = row[4]

                v = (
                    row[5]
                    if len(row) > 5
                    else 0
                )

            try:

                ts = float(ts)

                if ts > 10_000_000_000:

                    ts /= 1000

                normalized.append({

                    "time": int(ts),

                    "open": float(o),

                    "high": float(h),

                    "low": float(l),

                    "close": float(c),

                    "volume": float(v),

                })

            except Exception:

                continue

        if not normalized:

            return pd.DataFrame()

        df = pd.DataFrame(
            normalized
        )

        df = df.sort_values(
            "time"
        )

        df = df.drop_duplicates(
            "time"
        )

        df = df.tail(
            count
        )

        df = df.reset_index(
            drop=True
        )

        return df

    except Exception:

        return pd.DataFrame()


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    symbol,
    interval,
    count
):

    df = fetch_candles(
        symbol,
        interval,
        count + 3
    )

    if df.empty:

        return df

    if len(df) < 3:

        return pd.DataFrame()

    # Remove newest forming candle.

    return (
        df.iloc[:-1]
        .tail(count)
        .reset_index(drop=True)
    )


# ============================================================
# CURRENT PRICE
# ============================================================

def fetch_current_price(symbol):

    url = (
        f"{KRAKEN_REST_BASE}/tickers"
    )

    try:

        response = session.get(
            url,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get(
            "tickers",
            []
        )

        for ticker in tickers:

            pair = (
                ticker.get("symbol")
                or ticker.get("pair")
                or ticker.get("instrument")
            )

            if pair != symbol:

                continue

            price = (
                ticker.get("last")
                or ticker.get("lastPrice")
                or ticker.get("price")
            )

            if price is not None:

                return float(price)

    except Exception:

        pass

    return None


def fetch_price_map(symbols):

    result = {}

    for symbol in symbols:

        price = fetch_current_price(
            symbol
        )

        if price is not None:

            result[symbol] = price

        time.sleep(
            REQUEST_SLEEP
        )

    return result


# ============================================================
# ASSET DISCOVERY
# ============================================================

def discover_assets():

    url = (
        f"{KRAKEN_REST_BASE}/instruments"
    )

    try:

        response = session.get(
            url,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        instruments = data.get(
            "instruments",
            []
        )

        candidates = []

        for item in instruments:

            symbol = (
                item.get("symbol")
                or item.get("instrument")
                or item.get("pair")
            )

            if not symbol:

                continue

            symbol = str(symbol)

            if not symbol.startswith(
                "PF_"
            ):

                continue

            if not symbol.endswith(
                "USD"
            ):

                continue

            if symbol in {
                "PF_SNXXUSD",
            }:

                continue

            candidates.append(
                symbol
            )

        def volume_key(symbol):

            return 0

        candidates = sorted(
            set(candidates),
            key=volume_key,
            reverse=True
        )

        return candidates[
            :TARGET_ASSETS
        ]

    except Exception:

        return []


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(
    df,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT
):

    if df.empty:

        return []

    highs = df[
        "high"
    ].values

    lows = df[
        "low"
    ].values

    times = df[
        "time"
    ].values

    pivots = []

    start = left

    end = len(df) - right

    for i in range(
        start,
        end
    ):

        high_window = highs[
            i - left:
            i + right + 1
        ]

        low_window = lows[
            i - left:
            i + right + 1
        ]

        is_high = (
            highs[i]
            ==
            np.max(high_window)
            and
            highs[i]
            >
            np.max(
                np.delete(
                    high_window,
                    left
                )
            )
        )

        is_low = (
            lows[i]
            ==
            np.min(low_window)
            and
            lows[i]
            <
            np.min(
                np.delete(
                    low_window,
                    left
                )
            )
        )

        if is_high:

            pivots.append({

                "type": "H",

                "price": float(
                    highs[i]
                ),

                "time": int(
                    times[i]
                ),

                "index": i,

            })

        if is_low:

            pivots.append({

                "type": "L",

                "price": float(
                    lows[i]
                ),

                "time": int(
                    times[i]
                ),

                "index": i,

            })

    pivots.sort(
        key=lambda x: x["time"]
    )

    return pivots


# ============================================================
# COMBINE SAME-TYPE PIVOTS
# ============================================================

def combined_pivots(pivots):

    if not pivots:

        return []

    result = []

    for p in pivots:

        if not result:

            result.append(p)

            continue

        last = result[-1]

        if last["type"] != p["type"]:

            result.append(p)

            continue

        if p["type"] == "H":

            if p["price"] >= last["price"]:

                result[-1] = p

        else:

            if p["price"] <= last["price"]:

                result[-1] = p

    return result


# ============================================================
# M30 POSITIVE HOOK
# ============================================================

def detect_m30_positive_hook(df):

    pivots = combined_pivots(
        detect_pivots(df)
    )

    if len(pivots) < 5:

        return None

    for i in range(
        len(pivots) - 5,
        -1,
        -1
    ):

        p = pivots[
            i:i + 5
        ]

        types = [
            x["type"]
            for x in p
        ]

        if types != [
            "H",
            "L",
            "H",
            "L",
            "H"
        ]:

            continue

        h1 = p[0]["price"]

        l1 = p[1]["price"]

        h2 = p[2]["price"]

        l2 = p[3]["price"]

        h3 = p[4]["price"]

        if not (
            h2 > h1
            and
            h3 > h2
        ):

            continue

        range_pct = (
            (h3 - l2)
            / l2
            * 100
        )

        if (
            range_pct
            < M30_MIN_HOOK_RANGE_PCT
        ):

            continue

        return {

            "direction": "SHORT",

            "hook_time": p[4]["time"],

            "h1": h1,

            "l1": l1,

            "h2": h2,

            "l2": l2,

            "h3": h3,

            "l3": None,

            "target": l2,

            "pivots": p,

        }

    return None


# ============================================================
# M30 NEGATIVE HOOK
# ============================================================

def detect_m30_negative_hook(df):

    pivots = combined_pivots(
        detect_pivots(df)
    )

    if len(pivots) < 5:

        return None

    for i in range(
        len(pivots) - 5,
        -1,
        -1
    ):

        p = pivots[
            i:i + 5
        ]

        types = [
            x["type"]
            for x in p
        ]

        if types != [
            "L",
            "H",
            "L",
            "H",
            "L"
        ]:

            continue

        l1 = p[0]["price"]

        h1 = p[1]["price"]

        l2 = p[2]["price"]

        h2 = p[3]["price"]

        l3 = p[4]["price"]

        if not (
            l2 < l1
            and
            l3 < l2
        ):

            continue

        range_pct = (
            (h2 - l3)
            / l3
            * 100
        )

        if (
            range_pct
            < M30_MIN_HOOK_RANGE_PCT
        ):

            continue

        return {

            "direction": "LONG",

            "hook_time": p[4]["time"],

            "h1": h1,

            "l1": l1,

            "h2": h2,

            "l2": l2,

            "h3": None,

            "l3": l3,

            "target": h2,

            "pivots": p,

        }

    return None


# ============================================================
# HOOK VALIDATION
# ============================================================

def hook_is_expired(hook):

    return (
        now_ts()
        - int(hook["hook_time"])
        >
        MAX_HOOK_AGE_SECONDS
    )


def hook_is_invalidated(
    hook,
    pivots
):

    hook_time = int(
        hook["hook_time"]
    )

    future = [
        p
        for p in pivots
        if p["time"] > hook_time
    ]

    if not future:

        return False

    if hook["direction"] == "SHORT":

        return any(
            p["type"] == "L"
            for p in future
        )

    else:

        return any(
            p["type"] == "H"
            for p in future
        )


# ============================================================
# SAVE HOOK
# ============================================================

def save_hook(
    symbol,
    hook
):

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        UPDATE hooks
        SET status = 'EXPIRED',
            invalidated_at = ?
        WHERE symbol = ?
          AND status = 'ACTIVE'
          AND (
                side != ?
                OR hook_time != ?
              )
    """, (
        now_ts(),
        symbol,
        hook["direction"],
        hook["hook_time"],
    ))

    cur.execute("""
        SELECT id
        FROM hooks
        WHERE symbol = ?
          AND side = ?
          AND hook_time = ?
        LIMIT 1
    """, (
        symbol,
        hook["direction"],
        hook["hook_time"],
    ))

    if cur.fetchone() is None:

        cur.execute("""
            INSERT INTO hooks (
                symbol,
                side,
                hook_time,
                h1,
                l1,
                h2,
                l2,
                h3,
                l3,
                target,
                status,
                created_at
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, 'ACTIVE', ?
            )
        """, (
            symbol,
            hook["direction"],
            hook["hook_time"],
            hook.get("h1"),
            hook.get("l1"),
            hook.get("h2"),
            hook.get("l2"),
            hook.get("h3"),
            hook.get("l3"),
            hook["target"],
            now_ts(),
        ))

    conn.commit()

    conn.close()


# ============================================================
# LOAD ACTIVE HOOKS
# ============================================================

def load_active_hooks():

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM hooks
        WHERE status = 'ACTIVE'
        ORDER BY hook_time DESC
    """)

    rows = cur.fetchall()

    conn.close()

    hooks = []

    for row in rows:

        hook = dict(row)

        if (
            now_ts()
            - int(hook["hook_time"])
            >
            MAX_HOOK_AGE_SECONDS
        ):

            conn2 = db()

            conn2.execute("""
                UPDATE hooks
                SET status = 'EXPIRED'
                WHERE id = ?
            """, (
                hook["id"],
            ))

            conn2.commit()

            conn2.close()

            continue

        hooks.append(hook)

    return hooks


# ============================================================
# M1 123F LONG
# ============================================================

def detect_m1_long_123f(
    df,
    hook
):

    if df.empty:

        return None

    pivots = combined_pivots(
        detect_pivots(df)
    )

    if len(pivots) < 2:

        return None

    hook_time = int(
        hook["hook_time"]
    )

    future = [
        p
        for p in pivots
        if p["time"] > hook_time
    ]

    if len(future) < 2:

        return None

    for i in range(
        len(future) - 1,
        0,
        -1
    ):

        f = future[i]

        if f["type"] != "L":

            continue

        h_candidates = [
            p
            for p in future[:i]
            if p["type"] == "H"
        ]

        if not h_candidates:

            continue

        h3 = h_candidates[-1]

        h_index = future.index(
            h3
        )

        l_candidates = [
            p
            for p in future[:h_index]
            if p["type"] == "L"
        ]

        if not l_candidates:

            continue

        l3 = l_candidates[-1]

        if not (
            l3["time"]
            <
            h3["time"]
            <
            f["time"]
        ):

            continue

        if not (
            f["price"]
            <
            l3["price"]
        ):

            continue

        age = (
            now_ts()
            - f["time"]
        )

        if (
            age < 0
            or
            age > M1_F_MAX_AGE_SECONDS
        ):

            continue

        swing_pct = (
            (
                l3["price"]
                -
                f["price"]
            )
            /
            l3["price"]
            *
            100
        )

        if (
            swing_pct
            <
            M1_MIN_SWING_PCT
        ):

            continue

        distance_pct = (
            abs(
                f["price"]
                -
                l3["price"]
            )
            /
            l3["price"]
            *
            100
        )

        if (
            distance_pct
            >
            M1_MAX_STRUCTURE_DISTANCE_PCT
        ):

            continue

        return {

            "direction": "LONG",

            "f": f,

            "l3": l3,

            "h3": h3,

        }

    return None


# ============================================================
# M1 123F SHORT
# ============================================================

def detect_m1_short_123f(
    df,
    hook
):

    if df.empty:

        return None

    pivots = combined_pivots(
        detect_pivots(df)
    )

    if len(pivots) < 2:

        return None

    hook_time = int(
        hook["hook_time"]
    )

    future = [
        p
        for p in pivots
        if p["time"] > hook_time
    ]

    if len(future) < 2:

        return None

    for i in range(
        len(future) - 1,
        0,
        -1
    ):

        f = future[i]

        if f["type"] != "H":

            continue

        l_candidates = [
            p
            for p in future[:i]
            if p["type"] == "L"
        ]

        if not l_candidates:

            continue

        l3 = l_candidates[-1]

        l_index = future.index(
            l3
        )

        h_candidates = [
            p
            for p in future[:l_index]
            if p["type"] == "H"
        ]

        if not h_candidates:

            continue

        h3 = h_candidates[-1]

        if not (
            h3["time"]
            <
            l3["time"]
            <
            f["time"]
        ):

            continue

        if not (
            f["price"]
            >
            h3["price"]
        ):

            continue

        age = (
            now_ts()
            - f["time"]
        )

        if (
            age < 0
            or
            age > M1_F_MAX_AGE_SECONDS
        ):

            continue

        swing_pct = (
            (
                f["price"]
                -
                h3["price"]
            )
            /
            h3["price"]
            *
            100
        )

        if (
            swing_pct
            <
            M1_MIN_SWING_PCT
        ):

            continue

        distance_pct = (
            abs(
                f["price"]
                -
                h3["price"]
            )
            /
            h3["price"]
            *
            100
        )

        if (
            distance_pct
            >
            M1_MAX_STRUCTURE_DISTANCE_PCT
        ):

            continue

        return {

            "direction": "SHORT",

            "f": f,

            "h3": h3,

            "l3": l3,

        }

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    direction,
    current_price,
    hook,
    setup
):

    entry = float(
        current_price
    )

    if direction == "SHORT":

        highs = [

            hook.get("h1"),

            hook.get("h2"),

            hook.get("h3"),

            setup["h3"]["price"],

        ]

        highs = [
            x
            for x in highs
            if x is not None
        ]

        if not highs:

            return None

        sl_base = max(highs)

        sl = (
            sl_base
            *
            (
                1
                +
                SL_BUFFER_PCT / 100
            )
        )

        tp = float(
            hook["target"]
        )

        if not (
            tp
            <
            entry
            <
            sl
        ):

            return None

        return {

            "entry": entry,

            "sl": sl,

            "tp": tp,

        }

    else:

        lows = [

            hook.get("l1"),

            hook.get("l2"),

            hook.get("l3"),

            setup["l3"]["price"],

        ]

        lows = [
            x
            for x in lows
            if x is not None
        ]

        if not lows:

            return None

        sl_base = min(lows)

        sl = (
            sl_base
            *
            (
                1
                -
                SL_BUFFER_PCT / 100
            )
        )

        tp = float(
            hook["target"]
        )

        if not (
            sl
            <
            entry
            <
            tp
        ):

            return None

        return {

            "entry": entry,

            "sl": sl,

            "tp": tp,

        }


# ============================================================
# OPEN TRADES COUNT
# ============================================================

def open_trade_count():

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """)

    count = cur.fetchone()[0]

    conn.close()

    return int(count)


# ============================================================
# COOLDOWN
# ============================================================

def signal_on_cooldown(
    symbol,
    side
):

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT signal_time
        FROM signals
        WHERE symbol = ?
          AND side = ?
        ORDER BY signal_time DESC
        LIMIT 1
    """, (
        symbol,
        side,
    ))

    row = cur.fetchone()

    conn.close()

    if not row:

        return False

    return (
        now_ts()
        -
        int(row["signal_time"])
        <
        SIGNAL_COOLDOWN_SECONDS
    )


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    symbol,
    side,
    levels,
    hook,
    setup,
    chart_path=None
):

    if (
        open_trade_count()
        >= MAX_OPEN_TRADES
    ):

        return None

    if signal_on_cooldown(
        symbol,
        side
    ):

        return None

    f_time = int(
        setup["f"]["time"]
    )

    event_key = str(
        f_time
    )

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM processed_events
        WHERE symbol = ?
          AND side = ?
          AND event_key = ?
        LIMIT 1
    """, (
        symbol,
        side,
        event_key,
    ))

    if cur.fetchone() is not None:

        conn.close()

        return None

    signal_time = now_ts()

    cur.execute("""
        INSERT INTO signals (
            symbol,
            side,
            signal_time,
            entry,
            sl,
            tp,
            status,
            hook_time,
            f_time,
            hook_target,
            chart_path,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?,
            'OPEN', ?, ?, ?, ?, ?
        )
    """, (
        symbol,
        side,
        signal_time,
        levels["entry"],
        levels["sl"],
        levels["tp"],
        hook["hook_time"],
        f_time,
        hook["target"],
        chart_path,
        signal_time,
    ))

    cur.execute("""
        INSERT OR IGNORE INTO processed_events (
            symbol,
            side,
            event_key,
            event_time,
            created_at
        )
        VALUES (?, ?, ?, ?, ?)
    """, (
        symbol,
        side,
        event_key,
        f_time,
        signal_time,
    ))

    signal_id = cur.lastrowid

    conn.commit()

    conn.close()

    return signal_id


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:

        return "-"

    value = float(value)

    if value >= 1000:

        return f"{value:,.2f}"

    if value >= 1:

        return f"{value:.5f}"

    if value >= 0.01:

        return f"{value:.6f}"

    return f"{value:.10f}"


# ============================================================
# PERCENT DISTANCE
# ============================================================

def target_distance_pct(
    side,
    current,
    target
):

    if current is None:

        return 0.0

    current = float(
        current
    )

    target = float(
        target
    )

    if current == 0:

        return 0.0

    if side == "LONG":

        return (
            (target - current)
            /
            current
            *
            100
        )

    return (
        (current - target)
        /
        current
        *
        100
    )


def sl_distance_pct(
    side,
    current,
    sl
):

    if current is None:

        return 0.0

    current = float(
        current
    )

    sl = float(
        sl
    )

    if current == 0:

        return 0.0

    if side == "LONG":

        return (
            (sl - current)
            /
            current
            *
            100
        )

    return (
        (current - sl)
        /
        current
        *
        100
    )


# ============================================================
# TRADE PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    current
):

    if side == "LONG":

        return (
            (current - entry)
            /
            entry
            *
            100
        )

    return (
        (entry - current)
        /
        entry
        *
        100
    )


# ============================================================
# M30 HOOK CHART
# ============================================================

def save_m30_hook_chart(
    symbol,
    df,
    hook,
    current_price=None,
    entry=None,
    sl=None,
    tp=None,
    prefix="hook"
):

    if not ENABLE_CHARTS:

        return None

    if df.empty:

        return None

    try:

        hook_time = int(
            hook["hook_time"]
        )

        chart_file = (
            f"{symbol}_"
            f"{hook['direction']}_"
            f"{prefix}_"
            f"{hook_time}.png"
        )

        path = os.path.join(
            CHART_DIR,
            chart_file
        )

        plot_df = df.tail(
            CHART_CANDLES
        ).copy()

        fig, ax = plt.subplots(
            figsize=(16, 9)
        )

        x = np.arange(
            len(plot_df)
        )

        # ----------------------------------------------------
        # CANDLESTICKS
        # ----------------------------------------------------

        candle_width = 0.65

        for i, row in plot_df.iterrows():

            o = float(row["open"])

            h = float(row["high"])

            l = float(row["low"])

            c = float(row["close"])

            # Wick

            ax.plot(
                [i, i],
                [l, h],
                linewidth=1.0,
            )

            # Body

            body_low = min(
                o,
                c
            )

            body_height = abs(
                c - o
            )

            if body_height == 0:

                body_height = (
                    max(
                        abs(c) * 0.0001,
                        1e-12
                    )
                )

            rect = plt.Rectangle(
                (
                    i - candle_width / 2,
                    body_low
                ),
                candle_width,
                body_height,
                fill=False,
                linewidth=1.0,
            )

            ax.add_patch(rect)

        # ----------------------------------------------------
        # TIME MAP
        # ----------------------------------------------------

        time_to_x = {

            int(t): i

            for i, t in enumerate(
                plot_df["time"]
            )

        }

        # ----------------------------------------------------
        # HOOK POINTS
        # ----------------------------------------------------

        hook_points = []

        point_names = [

            ("h1", "H1"),

            ("l1", "L1"),

            ("h2", "H2"),

            ("l2", "L2"),

            ("h3", "H3"),

            ("l3", "L3"),

        ]

        for key, label in point_names:

            price = hook.get(key)

            if price is None:

                continue

            # Find actual pivot time.

            point_time = None

            for p in hook.get(
                "pivots",
                []
            ):

                if (
                    p["type"]
                    ==
                    label[0]
                    and
                    abs(
                        p["price"]
                        -
                        float(price)
                    )
                    <
                    max(
                        abs(
                            float(price)
                        )
                        *
                        0.000001,
                        1e-12
                    )
                ):

                    point_time = p[
                        "time"
                    ]

            if point_time is None:

                continue

            if point_time not in time_to_x:

                continue

            px = time_to_x[
                point_time
            ]

            py = float(price)

            hook_points.append(
                (
                    px,
                    py,
                    label
                )
            )

        # ----------------------------------------------------
        # FALLBACK FROM STORED HOOK
        # ----------------------------------------------------

        if not hook_points:

            for p in hook.get(
                "pivots",
                []
            ):

                if p["time"] not in time_to_x:

                    continue

                hook_points.append(
                    (
                        time_to_x[
                            p["time"]
                        ],
                        float(
                            p["price"]
                        ),
                        p["type"]
                    )
                )

        # ----------------------------------------------------
        # DRAW HOOK PATH
        # ----------------------------------------------------

        if hook_points:

            hook_points = sorted(
                hook_points,
                key=lambda z: z[0]
            )

            path_x = [
                p[0]
                for p in hook_points
            ]

            path_y = [
                p[1]
                for p in hook_points
            ]

            ax.plot(
                path_x,
                path_y,
                linewidth=2.2,
                marker="o",
                markersize=6,
                label="M30 Hook",
                zorder=8,
            )

            for px, py, label in hook_points:

                ax.annotate(
                    label,
                    (
                        px,
                        py
                    ),
                    xytext=(
                        6,
                        10
                    ),
                    textcoords="offset points",
                    fontsize=10,
                    fontweight="bold",
                    zorder=10,
                )

        # ----------------------------------------------------
        # HOOK TARGET
        # ----------------------------------------------------

        target = hook.get(
            "target"
        )

        if target is not None:

            ax.axhline(
                float(target),
                linestyle=":",
                linewidth=1.5,
                label=(
                    "Hook Target "
                    f"{fmt_price(target)}"
                ),
            )

        # ----------------------------------------------------
        # CURRENT PRICE
        # ----------------------------------------------------

        if current_price is not None:

            ax.axhline(
                float(current_price),
                linestyle="-.",
                linewidth=1.2,
                label=(
                    "Current "
                    f"{fmt_price(current_price)}"
                ),
            )

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        if entry is not None:

            ax.axhline(
                float(entry),
                linestyle="--",
                linewidth=1.2,
                label=(
                    "Entry "
                    f"{fmt_price(entry)}"
                ),
            )

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        if sl is not None:

            ax.axhline(
                float(sl),
                linestyle="--",
                linewidth=1.2,
                label=(
                    "SL "
                    f"{fmt_price(sl)}"
                ),
            )

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        if tp is not None:

            ax.axhline(
                float(tp),
                linestyle="--",
                linewidth=1.2,
                label=(
                    "TP "
                    f"{fmt_price(tp)}"
                ),
            )

        # ----------------------------------------------------
        # HOOK AGE
        # ----------------------------------------------------

        hook_age_min = max(
            0,
            (
                now_ts()
                -
                hook_time
            )
            // 60
        )

        direction = hook[
            "direction"
        ]

        ax.set_title(
            (
                f"NDS M30 "
                f"{direction} HOOK | "
                f"{symbol}\n"
                f"Hook age: "
                f"{hook_age_min}m"
            )
        )

        ax.set_xlabel(
            "M30 candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.20
        )

        ax.legend(
            loc="best",
            fontsize=8
        )

        fig.tight_layout()

        fig.savefig(
            path,
            dpi=CHART_DPI,
            bbox_inches="tight"
        )

        plt.close(fig)

        return path

    except Exception as exc:

        print(
            f"[M30 CHART ERROR] "
            f"{symbol}: {exc}"
        )

        try:

            plt.close(
                "all"
            )

        except Exception:

            pass

        return None


# ============================================================
# SIGNAL M30 CHART
# ============================================================

def save_signal_m30_chart(
    symbol,
    hook,
    levels
):

    df = get_closed_candles(
        symbol,
        M30_INTERVAL,
        M30_CANDLES
    )

    if df.empty:

        return None

    # Reconstruct pivot list for chart labels.
    #
    # Stored DB hook does not contain pivot timestamps,
    # therefore find the exact structure again from M30.

    positive = detect_m30_positive_hook(
        df
    )

    negative = detect_m30_negative_hook(
        df
    )

    selected = None

    if (
        hook["side"] == "SHORT"
        and
        positive is not None
    ):

        if (
            int(
                positive["hook_time"]
            )
            ==
            int(
                hook["hook_time"]
            )
        ):

            selected = positive

    if (
        hook["side"] == "LONG"
        and
        negative is not None
    ):

        if (
            int(
                negative["hook_time"]
            )
            ==
            int(
                hook["hook_time"]
            )
        ):

            selected = negative

    if selected is None:

        selected = {

            "direction": hook["side"],

            "hook_time": hook[
                "hook_time"
            ],

            "h1": hook["h1"],

            "l1": hook["l1"],

            "h2": hook["h2"],

            "l2": hook["l2"],

            "h3": hook["h3"],

            "l3": hook["l3"],

            "target": hook["target"],

            "pivots": [],

        }

    # Current price = Entry for the signal
    # at creation time.

    path = save_m30_hook_chart(

        symbol,

        df,

        selected,

        current_price=levels[
            "entry"
        ],

        entry=levels[
            "entry"
        ],

        sl=levels[
            "sl"
        ],

        tp=levels[
            "tp"
        ],

        prefix="signal"

    )

    return path


# ============================================================
# ACTIVE HOOK M30 CHART
# ============================================================

def save_active_hook_chart(
    hook
):

    symbol = hook[
        "symbol"
    ]

    df = get_closed_candles(
        symbol,
        M30_INTERVAL,
        ACTIVE_HOOK_CHART_CANDLES
    )

    if df.empty:

        return None

    positive = None

    negative = None

    if hook["side"] == "SHORT":

        positive = (
            detect_m30_positive_hook(
                df
            )
        )

    else:

        negative = (
            detect_m30_negative_hook(
                df
            )
        )

    selected = (
        positive
        if hook["side"] == "SHORT"
        else negative
    )

    if selected is None:

        selected = {

            "direction": hook[
                "side"
            ],

            "hook_time": hook[
                "hook_time"
            ],

            "h1": hook["h1"],

            "l1": hook["l1"],

            "h2": hook["h2"],

            "l2": hook["l2"],

            "h3": hook["h3"],

            "l3": hook["l3"],

            "target": hook[
                "target"
            ],

            "pivots": [],

        }

    current = fetch_current_price(
        symbol
    )

    return save_m30_hook_chart(

        symbol,

        df,

        selected,

        current_price=current,

        prefix="active"

    )


# ============================================================
# SIGNAL TELEGRAM
# ============================================================

def send_signal_message(
    symbol,
    side,
    levels,
    setup,
    chart_path=None
):

    global new_signal_since_report

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    entry = float(
        levels["entry"]
    )

    sl = float(
        levels["sl"]
    )

    tp = float(
        levels["tp"]
    )

    sl_pct = sl_distance_pct(
        side,
        entry,
        sl
    )

    tp_pct = target_distance_pct(
        side,
        entry,
        tp
    )

    text = (
        f"🚨 <b>NDS {side}</b>\n\n"

        f"{emoji} <b>{symbol}</b>\n"

        f"TF: M30 → M1\n\n"

        f"Entry: "
        f"<b>{fmt_price(entry)}</b>\n"

        f"SL: "
        f"{fmt_price(sl)} "
        f"({sl_pct:+.2f}%)\n"

        f"TP: "
        f"{fmt_price(tp)} "
        f"({tp_pct:+.2f}%)\n\n"

        f"F: "
        f"{fmt_price(setup['f']['price'])}\n"

        f"F age: "
        f"{max(0, now_ts() - setup['f']['time']) // 60}m\n\n"

        f"Mode: PAPER ONLY"
    )

    if chart_path:

        success = send_telegram_photo(
            chart_path,
            text
        )

        if not success:

            send_telegram(
                text
            )

    else:

        send_telegram(
            text
        )

    new_signal_since_report = True


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30():

    global last_m30_result

    if not assets:

        return

    new_long = 0

    new_short = 0

    scanned = 0

    for symbol in assets:

        try:

            df = get_closed_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES
            )

            if df.empty:

                continue

            scanned += 1

            pivots = combined_pivots(
                detect_pivots(df)
            )

            positive = (
                detect_m30_positive_hook(
                    df
                )
            )

            negative = (
                detect_m30_negative_hook(
                    df
                )
            )

            selected = []

            if positive is not None:

                selected.append(
                    positive
                )

            if negative is not None:

                selected.append(
                    negative
                )

            for hook in selected:

                if hook_is_expired(
                    hook
                ):

                    continue

                if hook_is_invalidated(
                    hook,
                    pivots
                ):

                    continue

                save_hook(
                    symbol,
                    hook
                )

            time.sleep(
                REQUEST_SLEEP
            )

        except Exception as exc:

            print(
                f"[M30 ERROR] "
                f"{symbol}: {exc}"
            )

    last_m30_result = {

        "assets": scanned,

        "new_long": new_long,

        "new_short": new_short,

    }

    conn = db()

    conn.execute("""
        INSERT INTO scanner_runs (
            run_time,
            run_type,
            configured,
            assets,
            new_long,
            new_short,
            error
        )
        VALUES (
            ?, 'M30', 1, ?, ?, ?, NULL
        )
    """, (
        now_ts(),
        scanned,
        new_long,
        new_short,
    ))

    conn.commit()

    conn.close()


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1():

    global last_m1_result

    hooks = load_active_hooks()

    new_long = 0

    new_short = 0

    for hook in hooks:

        symbol = hook[
            "symbol"
        ]

        try:

            df = get_closed_candles(
                symbol,
                M1_INTERVAL,
                M1_CANDLES
            )

            if df.empty:

                continue

            direction = hook[
                "side"
            ]

            if direction == "LONG":

                setup = (
                    detect_m1_long_123f(
                        df,
                        hook
                    )
                )

            else:

                setup = (
                    detect_m1_short_123f(
                        df,
                        hook
                    )
                )

            if setup is None:

                continue

            current_price = (
                fetch_current_price(
                    symbol
                )
            )

            if current_price is None:

                continue

            levels = (
                calculate_trade_levels(
                    direction,
                    current_price,
                    hook,
                    setup
                )
            )

            if levels is None:

                continue

            # ------------------------------------------------
            # M30 SIGNAL CHART
            # ------------------------------------------------

            chart_path = (
                save_signal_m30_chart(
                    symbol,
                    hook,
                    levels
                )
            )

            signal_id = insert_signal(
                symbol,
                direction,
                levels,
                hook,
                setup,
                chart_path
            )

            if signal_id is None:

                continue

            if direction == "LONG":

                new_long += 1

            else:

                new_short += 1

            # ------------------------------------------------
            # TELEGRAM
            # ------------------------------------------------

            send_signal_message(
                symbol,
                direction,
                levels,
                setup,
                chart_path
            )

            print(
                f"[NEW {direction}] "
                f"{symbol} "
                f"entry={levels['entry']} "
                f"sl={levels['sl']} "
                f"tp={levels['tp']}"
            )

            time.sleep(
                REQUEST_SLEEP
            )

        except Exception as exc:

            print(
                f"[M1 ERROR] "
                f"{symbol}: {exc}"
            )

    last_m1_result = {

        "new_long": new_long,

        "new_short": new_short,

    }

    conn = db()

    conn.execute("""
        INSERT INTO scanner_runs (
            run_time,
            run_type,
            configured,
            assets,
            new_long,
            new_short,
            error
        )
        VALUES (
            ?, 'M1', 1, ?, ?, ?, NULL
        )
    """, (
        now_ts(),
        len(hooks),
        new_long,
        new_short,
    ))

    conn.commit()

    conn.close()


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    signal_id,
    price,
    reason
):

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM signals
        WHERE id = ?
          AND status = 'OPEN'
    """, (
        signal_id,
    ))

    row = cur.fetchone()

    if row is None:

        conn.close()

        return

    pnl = calculate_pnl(
        row["side"],
        row["entry"],
        price
    )

    cur.execute("""
        UPDATE signals
        SET status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            pnl_pct = ?,
            exit_reason = ?
        WHERE id = ?
    """, (
        now_ts(),
        price,
        pnl,
        reason,
        signal_id,
    ))

    conn.commit()

    conn.close()

    emoji = (
        "🟢"
        if pnl >= 0
        else "🔴"
    )

    text = (

        f"📕 <b>NDS TRADE CLOSED</b>\n\n"

        f"{emoji} "
        f"{row['symbol']} "
        f"{row['side']}\n"

        f"Entry: "
        f"{fmt_price(row['entry'])}\n"

        f"Exit: "
        f"{fmt_price(price)}\n"

        f"P/L: "
        f"<b>{pnl:+.2f}%</b>\n"

        f"Reason: {reason}"

    )

    send_telegram(
        text
    )


# ============================================================
# MANAGE OPEN TRADES
# ============================================================

def manage_open_trades(
    price_map
):

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
    """)

    trades = cur.fetchall()

    conn.close()

    for trade in trades:

        symbol = trade[
            "symbol"
        ]

        price = price_map.get(
            symbol
        )

        if price is None:

            continue

        side = trade[
            "side"
        ]

        if side == "LONG":

            if price <= trade["sl"]:

                close_trade(
                    trade["id"],
                    trade["sl"],
                    "SL"
                )

            elif price >= trade["tp"]:

                close_trade(
                    trade["id"],
                    trade["tp"],
                    "TP"
                )

        else:

            if price >= trade["sl"]:

                close_trade(
                    trade["id"],
                    trade["sl"],
                    "SL"
                )

            elif price <= trade["tp"]:

                close_trade(
                    trade["id"],
                    trade["tp"],
                    "TP"
                )


# ============================================================
# REPORT DATA
# ============================================================

def get_report_data():

    conn = db()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """)

    open_count = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                    WHEN pnl_pct > 0
                    THEN 1
                    ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                    WHEN pnl_pct <= 0
                    THEN 1
                    ELSE 0
                END
            ) AS losses,

            COALESCE(
                SUM(pnl_pct),
                0
            ) AS pnl

        FROM signals

        WHERE status = 'CLOSED'
    """)

    stats = cur.fetchone()

    cur.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time DESC
    """)

    open_trades = cur.fetchall()

    cur.execute("""
        SELECT COUNT(*)
        FROM hooks
        WHERE status = 'ACTIVE'
    """)

    active_hooks = cur.fetchone()[0]

    conn.close()

    return (
        open_count,
        stats,
        open_trades,
        active_hooks,
    )


# ============================================================
# OPEN TRADE TEXT
# ============================================================

def format_open_trade(
    trade,
    current
):

    side = trade[
        "side"
    ]

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp = float(
        trade["tp"]
    )

    pnl = calculate_pnl(
        side,
        entry,
        current
    )

    sl_pct = sl_distance_pct(
        side,
        current,
        sl
    )

    tp_pct = target_distance_pct(
        side,
        current,
        tp
    )

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
    )

    return (

        f"\n"
        f"{emoji} "
        f"<b>{trade['symbol']}</b> "
        f"{side}\n"

        f"Entry: "
        f"{fmt_price(entry)}\n"

        f"Current: "
        f"<b>{fmt_price(current)}</b>\n"

        f"P/L: "
        f"<b>{pnl:+.2f}%</b>\n"

        f"SL: "
        f"{fmt_price(sl)} "
        f"({sl_pct:+.2f}%)\n"

        f"TP: "
        f"{fmt_price(tp)} "
        f"({tp_pct:+.2f}%)\n"

    )


# ============================================================
# ACTIVE HOOK TEXT
# ============================================================

def format_active_hook(
    hook,
    current=None
):

    side = hook[
        "side"
    ]

    target = hook[
        "target"
    ]

    hook_age = max(
        0,
        (
            now_ts()
            -
            int(
                hook["hook_time"]
            )
        )
        // 60
    )

    text = (

        f"\n"
        f"{'🟢' if side == 'LONG' else '🔴'} "
        f"<b>{hook['symbol']}</b> "
        f"{side}\n"

        f"Hook target: "
        f"{fmt_price(target)}\n"

        f"Hook age: "
        f"{hook_age}m"

    )

    if current is not None:

        distance = (
            target_distance_pct(
                side,
                current,
                target
            )
        )

        text += (

            f"\nCurrent: "
            f"<b>{fmt_price(current)}</b>\n"

            f"Target distance: "
            f"<b>{distance:+.2f}%</b>"

        )

    return text


# ============================================================
# SEND ACTIVE HOOK CHARTS
# ============================================================

def send_active_hook_charts():

    if not SEND_ACTIVE_HOOK_CHARTS:

        return 0

    hooks = load_active_hooks()

    if not hooks:

        return 0

    sent = 0

    # Newest hooks first.

    hooks = hooks[
        :MAX_ACTIVE_HOOK_CHARTS_PER_REPORT
    ]

    for hook in hooks:

        try:

            symbol = hook[
                "symbol"
            ]

            current = (
                fetch_current_price(
                    symbol
                )
            )

            chart_path = (
                save_active_hook_chart(
                    hook
                )
            )

            caption = (

                f"📌 <b>ACTIVE NDS HOOK</b>\n\n"

                f"{'🟢' if hook['side'] == 'LONG' else '🔴'} "
                f"<b>{symbol}</b> "
                f"{hook['side']}\n"

                f"TF: M30\n\n"

                f"Target: "
                f"<b>{fmt_price(hook['target'])}</b>\n"

            )

            if current is not None:

                distance = (
                    target_distance_pct(
                        hook["side"],
                        current,
                        hook["target"]
                    )
                )

                caption += (

                    f"Current: "
                    f"<b>{fmt_price(current)}</b>\n"

                    f"Target distance: "
                    f"<b>{distance:+.2f}%</b>\n"

                )

            caption += (

                f"Hook age: "
                f"{max(0, now_ts() - int(hook['hook_time'])) // 60}m\n\n"

                f"Mode: PAPER ONLY"

            )

            if chart_path:

                if send_telegram_photo(
                    chart_path,
                    caption
                ):

                    sent += 1

            time.sleep(
                REQUEST_SLEEP
            )

        except Exception as exc:

            print(
                f"[ACTIVE HOOK CHART ERROR] "
                f"{hook.get('symbol')}: "
                f"{exc}"
            )

    return sent


# ============================================================
# REPORT
# ============================================================

def send_report(
    final_report=False
):

    global new_signal_since_report

    (
        open_count,
        stats,
        open_trades,
        active_hooks,
    ) = get_report_data()

    total = int(
        stats["total"] or 0
    )

    wins = int(
        stats["wins"] or 0
    )

    losses = int(
        stats["losses"] or 0
    )

    pnl = float(
        stats["pnl"] or 0
    )

    mode_text = (
        "FINAL RUN REPORT"
        if final_report
        else "REPORT"
    )

    text = (

        f"📊 <b>NDS M30 → M1 "
        f"{mode_text}</b>\n\n"

        f"Version: {VERSION}\n"

        f"Assets: {len(assets)}\n"

        f"Active hooks: "
        f"{active_hooks}\n\n"

        f"🟢 New LONG: "
        f"{last_m1_result['new_long']}\n"

        f"🔴 New SHORT: "
        f"{last_m1_result['new_short']}\n\n"

        f"📌 Open trades: "
        f"{open_count}\n\n"

        f"Closed: {total}\n"

        f"Wins: {wins}\n"

        f"Losses: {losses}\n"

        f"Total P/L: "
        f"<b>{pnl:+.2f}%</b>\n"

    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    if open_trades:

        text += (
            "\n<b>OPEN TRADES</b>\n"
        )

        symbols = [
            trade["symbol"]
            for trade in open_trades
        ]

        price_map = fetch_price_map(
            symbols
        )

        for trade in open_trades:

            current = price_map.get(
                trade["symbol"]
            )

            if current is None:

                current = float(
                    trade["entry"]
                )

            text += format_open_trade(
                trade,
                current
            )

    # --------------------------------------------------------
    # ACTIVE HOOKS
    # --------------------------------------------------------

    if active_hooks:

        text += (
            "\n<b>ACTIVE HOOKS</b>\n"
        )

        hooks = load_active_hooks()

        for hook in hooks[:20]:

            current = fetch_current_price(
                hook["symbol"]
            )

            text += format_active_hook(
                hook,
                current
            )

            time.sleep(
                REQUEST_SLEEP
            )

    text += (

        "\n\n"
        f"⏱ {utc_now_text()}\n"

        f"Mode: PAPER ONLY"

    )

    send_telegram(
        text
    )

    # --------------------------------------------------------
    # CHART POLICY
    # --------------------------------------------------------
    #
    # If there was no new signal since the last report,
    # send active M30 Hook charts.
    #
    # New signal charts are already sent immediately
    # from send_signal_message().
    #

    if (
        not new_signal_since_report
        and
        SEND_ACTIVE_HOOK_CHARTS
    ):

        print(
            "[REPORT] "
            "No new signal -> "
            "sending active Hook charts"
        )

        send_active_hook_charts()

    else:

        print(
            "[REPORT] "
            "New signal exists -> "
            "active Hook charts skipped"
        )

    new_signal_since_report = False


# ============================================================
# ACTIVE HOOK CONSOLE REPORT
# ============================================================

def print_active_hooks():

    hooks = load_active_hooks()

    print(
        f"[HOOKS] Active: "
        f"{len(hooks)}"
    )

    for hook in hooks:

        print(

            f"  "
            f"{hook['symbol']} "
            f"{hook['side']} "

            f"target="
            f"{hook['target']} "

            f"age="
            f"{(now_ts() - hook['hook_time']) // 60}m"

        )


# ============================================================
# REFRESH ASSETS
# ============================================================

def refresh_assets():

    global assets

    global last_asset_refresh

    if (
        assets
        and
        now_ts()
        -
        last_asset_refresh
        <
        ASSET_REFRESH_SECONDS
    ):

        return

    discovered = discover_assets()

    if discovered:

        assets = discovered

        last_asset_refresh = now_ts()

        print(
            f"[ASSETS] "
            f"Loaded {len(assets)} assets"
        )

    else:

        print(
            "[ASSETS] "
            "Could not refresh assets"
        )


# ============================================================
# SCANNER RUN LOG
# ============================================================

def log_error(
    run_type,
    error
):

    conn = db()

    conn.execute("""
        INSERT INTO scanner_runs (
            run_time,
            run_type,
            configured,
            assets,
            new_long,
            new_short,
            error
        )
        VALUES (
            ?, ?, 1, ?, 0, 0, ?
        )
    """, (
        now_ts(),
        run_type,
        len(assets),
        str(error),
    ))

    conn.commit()

    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():

    global last_m30_scan

    global last_m1_scan

    global last_report

    global new_signal_since_report

    print(
        "=" * 70
    )

    print(
        f"NDS M30 -> M1 LIVE SCANNER "
        f"VERSION {VERSION}"
    )

    print(
        "=" * 70
    )

    print(
        "MODE: PAPER ONLY"
    )

    print(
        f"Runtime: "
        f"{RUN_DURATION_SECONDS // 60} minutes"
    )

    print(
        "M30 scan: every "
        f"{M30_SCAN_SECONDS // 60} minutes"
    )

    print(
        "M1 scan: every "
        f"{M1_SCAN_SECONDS} seconds"
    )

    print(
        "Report: every "
        f"{REPORT_INTERVAL_SECONDS // 60} minutes"
    )

    print(
        "M30 Hook charts: ENABLED"
    )

    print(
        "Telegram sendPhoto: ENABLED"
    )

    print(
        "=" * 70
    )

    init_db()

    migrate_db()

    run_started_at = now_ts()

    last_asset_refresh = 0

    last_m30_scan = 0

    last_m1_scan = 0

    last_report = now_ts()

    new_signal_since_report = False

    while (
        now_ts()
        -
        run_started_at
        <
        RUN_DURATION_SECONDS
    ):

        try:

            current_time = now_ts()

            # ------------------------------------------------
            # ASSETS
            # ------------------------------------------------

            refresh_assets()

            # ------------------------------------------------
            # M30
            # ------------------------------------------------

            if (
                current_time
                -
                last_m30_scan
                >=
                M30_SCAN_SECONDS
            ):

                print(
                    "\n[M30] "
                    "Scan started"
                )

                scan_m30()

                last_m30_scan = (
                    current_time
                )

                print(
                    "[M30] "
                    "Scan finished"
                )

                print_active_hooks()

            # ------------------------------------------------
            # PRICE MAP
            # ------------------------------------------------

            price_map = {}

            if assets:

                price_map = fetch_price_map(
                    assets
                )

            # ------------------------------------------------
            # MANAGE TRADES
            # ------------------------------------------------

            if price_map:

                manage_open_trades(
                    price_map
                )

            # ------------------------------------------------
            # M1
            # ------------------------------------------------

            if (
                current_time
                -
                last_m1_scan
                >=
                M1_SCAN_SECONDS
            ):

                print(
                    "\n[M1] "
                    "Scan started"
                )

                scan_m1()

                last_m1_scan = (
                    current_time
                )

                print(
                    "[M1] "
                    "Scan finished"
                )

            # ------------------------------------------------
            # PERIODIC REPORT
            # ------------------------------------------------

            if (
                current_time
                -
                last_report
                >=
                REPORT_INTERVAL_SECONDS
            ):

                send_report()

                last_report = (
                    current_time
                )

            # ------------------------------------------------
            # RUNTIME
            # ------------------------------------------------

            elapsed = (
                now_ts()
                -
                run_started_at
            )

            remaining = max(
                0,
                RUN_DURATION_SECONDS
                -
                elapsed
            )

            print(

                f"[RUN] "
                f"Elapsed "
                f"{elapsed // 60}m "
                f"{elapsed % 60}s | "

                f"Remaining "
                f"{remaining // 60}m "
                f"{remaining % 60}s"

            )

            time.sleep(1)

        except KeyboardInterrupt:

            print(
                "\n[STOP] "
                "Keyboard interrupt"
            )

            break

        except Exception as exc:

            print(
                "\n[MAIN ERROR]"
            )

            print(
                str(exc)
            )

            traceback.print_exc()

            log_error(
                "MAIN",
                exc
            )

            time.sleep(
                ERROR_SLEEP
            )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    print(
        "\n"
        +
        "=" * 70
    )

    print(
        "[RUN COMPLETE]"
    )

    print(
        "Sending final report..."
    )

    try:

        send_report(
            final_report=True
        )

    except Exception as exc:

        print(
            f"[FINAL REPORT ERROR] "
            f"{exc}"
        )

    print(
        "Scanner exiting cleanly."
    )

    print(
        "=" * 70
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
