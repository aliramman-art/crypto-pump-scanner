# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.3.2
# ============================================================
#
# PAPER ONLY
#
# M30:
#   POSITIVE / SHORT:
#       H1 -> L1 -> H2 -> L2 -> H3
#       H2 > H1
#       L2 < L1
#       H3 > H2
#
#   NEGATIVE / LONG:
#       L1 -> H1 -> L2 -> H2 -> L3
#       L2 < L1
#       H2 > H1
#       L3 < L2
#
# M1 123F:
#
#   SHORT:
#       1 -> 2 -> 3 -> F
#       ALL ARE HIGHS
#       1 < 2 < 3 < F
#
#   LONG:
#       1 -> 2 -> 3 -> F
#       ALL ARE HIGHS
#       1 > 2 > 3 > F
#
#   IMPORTANT:
#       M1 intermediate LOWS do NOT have to make
#       higher lows / lower lows.
#
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import numpy as np
import pandas as pd

import matplotlib
matplotlib.use("Agg")

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.3.2"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v43.db"
CHART_DIR = "nds_charts"

KRAKEN_CHART_BASE = "https://futures.kraken.com/api/charts/v1"
KRAKEN_REST_BASE = "https://futures.kraken.com/derivatives/api/v3"

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)

TARGET_ASSETS = 100
ASSET_REFRESH_SECONDS = 3600

M30_INTERVAL = "30m"
M1_INTERVAL = "1m"

M30_CANDLES = 320
M1_CANDLES = 1500

M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60

REPORT_INTERVAL_SECONDS = 900

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

M30_MIN_HOOK_RANGE_PCT = 0.20

M1_MIN_SWING_PCT = 0.07
M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

MAX_HOOK_AGE_SECONDS = 6 * 3600
M1_F_MAX_AGE_SECONDS = 5 * 60

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_SECONDS = 60 * 60

SL_BUFFER_PCT = 0.15

ENABLE_CHARTS = True
CHART_CANDLES = 240
ACTIVE_HOOK_CHART_CANDLES = 120

HTTP_TIMEOUT = 15
REQUEST_SLEEP = 0.05
ERROR_SLEEP = 10

RUN_DURATION_SECONDS = 12 * 60

REQUEST_SESSION = requests.Session()

latest_price_map = {}

signals_since_last_report = 0


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_ts():
    return int(time.time())


def utc_text(ts=None):
    if ts is None:
        ts = now_ts()

    return datetime.fromtimestamp(
        ts,
        timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_price(value):
    if value is None:
        return "-"

    try:
        value = float(value)
    except Exception:
        return "-"

    if value == 0:
        return "0"

    av = abs(value)

    if av >= 1000:
        return f"{value:,.2f}"

    if av >= 100:
        return f"{value:.3f}"

    if av >= 1:
        return f"{value:.5f}"

    if av >= 0.01:
        return f"{value:.6f}"

    if av >= 0.0001:
        return f"{value:.8f}"

    return f"{value:.10f}"


def fmt_pct(value):
    if value is None:
        return "-"

    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "-"


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def safe_int(value, default=None):
    try:
        return int(value)
    except Exception:
        return default


def clean_symbol(symbol):
    return str(symbol).replace("/", "_")


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
    cur = conn.execute(f"PRAGMA table_info({table})")
    columns = {row["name"] for row in cur.fetchall()}

    if column not in columns:
        conn.execute(
            f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
        )


def migrate_db():
    conn = db()

    try:
        # ----------------------------------------------------
        # scanner_runs
        # ----------------------------------------------------
        ensure_column(
            conn,
            "scanner_runs",
            "configured",
            "INTEGER DEFAULT 1"
        )

        # ----------------------------------------------------
        # signals
        # ----------------------------------------------------
        signal_columns = [
            ("chart_path", "TEXT"),
            ("hook_time", "INTEGER"),
            ("f_time", "INTEGER"),
            ("hook_target", "REAL"),
            ("created_at", "INTEGER"),
        ]

        for column, definition in signal_columns:
            ensure_column(
                conn,
                "signals",
                column,
                definition
            )

        # ----------------------------------------------------
        # hooks
        # ----------------------------------------------------
        hook_time_columns = [
            ("h1_time", "INTEGER"),
            ("l1_time", "INTEGER"),
            ("h2_time", "INTEGER"),
            ("l2_time", "INTEGER"),
            ("h3_time", "INTEGER"),
            ("l3_time", "INTEGER"),
        ]

        for column, definition in hook_time_columns:
            ensure_column(
                conn,
                "hooks",
                column,
                definition
            )

        conn.commit()

    finally:
        conn.close()


def init_db():
    conn = db()

    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scanner_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                started_at INTEGER,
                finished_at INTEGER,
                assets_scanned INTEGER DEFAULT 0,
                new_long INTEGER DEFAULT 0,
                new_short INTEGER DEFAULT 0,
                configured INTEGER DEFAULT 1
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                signal_time INTEGER NOT NULL,
                entry REAL NOT NULL,
                sl REAL NOT NULL,
                tp REAL NOT NULL,
                status TEXT DEFAULT 'OPEN',
                exit_time INTEGER,
                exit_price REAL,
                pnl_pct REAL,
                exit_reason TEXT,
                hook_time INTEGER,
                f_time INTEGER,
                hook_target REAL,
                chart_path TEXT,
                created_at INTEGER
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS processed_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT UNIQUE,
                symbol TEXT,
                event_type TEXT,
                event_time INTEGER,
                created_at INTEGER
            )
        """)

        conn.execute("""
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

                h1_time INTEGER,
                l1_time INTEGER,
                h2_time INTEGER,
                l2_time INTEGER,
                h3_time INTEGER,
                l3_time INTEGER,

                status TEXT DEFAULT 'ACTIVE',
                invalidated_at INTEGER,
                created_at INTEGER
            )
        """)

        conn.commit()

    finally:
        conn.close()

    migrate_db()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

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

    try:
        response = REQUEST_SESSION.post(
            url,
            data=payload,
            timeout=HTTP_TIMEOUT
        )

        return response.ok

    except Exception as exc:
        print("Telegram text error:", exc)
        return False


def send_telegram_photo(photo_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not photo_path or not os.path.exists(photo_path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(photo_path, "rb") as photo:
            response = REQUEST_SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption[:1024],
                    "parse_mode": "HTML",
                },
                files={
                    "photo": photo
                },
                timeout=HTTP_TIMEOUT
            )

        return response.ok

    except Exception as exc:
        print("Telegram photo error:", exc)
        return False


# ============================================================
# KRAKEN API
# ============================================================

def fetch_candles(symbol, interval, count):
    url = (
        f"{KRAKEN_CHART_BASE}/trade/"
        f"{symbol}/{interval}"
    )

    try:
        response = REQUEST_SESSION.get(
            url,
            timeout=HTTP_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        candles = data.get("candles", [])

        if not candles:
            return pd.DataFrame()

        rows = []

        for item in candles:
            if isinstance(item, dict):
                ts = (
                    item.get("time")
                    or item.get("timestamp")
                )

                rows.append({
                    "time": safe_int(ts),
                    "open": safe_float(item.get("open")),
                    "high": safe_float(item.get("high")),
                    "low": safe_float(item.get("low")),
                    "close": safe_float(item.get("close")),
                    "volume": safe_float(
                        item.get("volume"),
                        0
                    ),
                })

            elif isinstance(item, list) and len(item) >= 5:
                rows.append({
                    "time": safe_int(item[0]),
                    "open": safe_float(item[1]),
                    "high": safe_float(item[2]),
                    "low": safe_float(item[3]),
                    "close": safe_float(item[4]),
                    "volume": safe_float(
                        item[5] if len(item) > 5 else 0,
                        0
                    ),
                })

        df = pd.DataFrame(rows)

        if df.empty:
            return df

        df = df.dropna(
            subset=[
                "time",
                "open",
                "high",
                "low",
                "close"
            ]
        )

        df["time"] = df["time"].astype(int)

        df = (
            df
            .drop_duplicates("time")
            .sort_values("time")
            .tail(count)
            .reset_index(drop=True)
        )

        return df

    except Exception as exc:
        print(
            f"fetch_candles error "
            f"{symbol} {interval}: {exc}"
        )
        return pd.DataFrame()


def get_closed_candles(df, interval_seconds):
    if df is None or df.empty:
        return pd.DataFrame()

    current = now_ts()

    out = df[
        (df["time"] + interval_seconds) <= current
    ].copy()

    return out.reset_index(drop=True)


def fetch_current_price(symbol):
    url = (
        f"{KRAKEN_REST_BASE}/tickers"
    )

    try:
        response = REQUEST_SESSION.get(
            url,
            timeout=HTTP_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        tickers = data.get("tickers", [])

        for item in tickers:
            if item.get("symbol") == symbol:
                value = (
                    item.get("last")
                    or item.get("lastPrice")
                )

                return safe_float(value)

    except Exception as exc:
        print(
            f"fetch_current_price error "
            f"{symbol}: {exc}"
        )

    return None


def fetch_price_map(symbols):
    result = {}

    if not symbols:
        return result

    url = (
        f"{KRAKEN_REST_BASE}/tickers"
    )

    try:
        response = REQUEST_SESSION.get(
            url,
            timeout=HTTP_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        wanted = set(symbols)

        for item in data.get("tickers", []):
            symbol = item.get("symbol")

            if symbol not in wanted:
                continue

            value = (
                item.get("last")
                or item.get("lastPrice")
            )

            value = safe_float(value)

            if value is not None:
                result[symbol] = value

    except Exception as exc:
        print("fetch_price_map error:", exc)

    return result


def discover_assets():
    url = (
        f"{KRAKEN_REST_BASE}/tickers"
    )

    try:
        response = REQUEST_SESSION.get(
            url,
            timeout=HTTP_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        candidates = []

        for item in data.get("tickers", []):
            symbol = item.get("symbol")

            if not symbol:
                continue

            if not symbol.startswith("PF_"):
                continue

            if not symbol.endswith("USD"):
                continue

            volume = (
                item.get("volumeQuote")
                or item.get("volume")
                or item.get("vol24h")
                or 0
            )

            volume = safe_float(volume, 0)

            candidates.append(
                (symbol, volume)
            )

        candidates.sort(
            key=lambda x: x[1],
            reverse=True
        )

        assets = [
            x[0]
            for x in candidates[:TARGET_ASSETS]
        ]

        print(
            f"Discovered assets: {len(assets)}"
        )

        return assets

    except Exception as exc:
        print("discover_assets error:", exc)
        return []


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

    high_values = df["high"].values
    low_values = df["low"].values

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):
        left_h = high_values[
            i - PIVOT_LEFT:i
        ]

        right_h = high_values[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        center_h = high_values[i]

        if (
            center_h > np.max(left_h)
            and center_h >= np.max(right_h)
        ):
            highs.append({
                "index": i,
                "time": int(df.iloc[i]["time"]),
                "price": float(center_h),
                "type": "H",
            })

        left_l = low_values[
            i - PIVOT_LEFT:i
        ]

        right_l = low_values[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        center_l = low_values[i]

        if (
            center_l < np.min(left_l)
            and center_l <= np.min(right_l)
        ):
            lows.append({
                "index": i,
                "time": int(df.iloc[i]["time"]),
                "price": float(center_l),
                "type": "L",
            })

    return highs, lows


def combined_pivots(df):
    highs, lows = detect_pivots(df)

    pivots = highs + lows

    pivots.sort(
        key=lambda x: x["index"]
    )

    return pivots


# ============================================================
# M30 HOOKS
# ============================================================

def detect_m30_positive_hook(df):
    """
    POSITIVE HOOK / SHORT

        H1
         \
          L1
            \
             H2
               \
                L2
                  \
                   H3

    Conditions:

        H2 > H1
        L2 < L1
        H3 > H2
    """

    if df is None or len(df) < 20:
        return None

    highs, lows = detect_pivots(df)

    if len(highs) < 3 or len(lows) < 2:
        return None

    pivots = combined_pivots(df)

    for i in range(len(pivots)):
        if pivots[i]["type"] != "H":
            continue

        H1 = pivots[i]

        for j in range(i + 1, len(pivots)):
            if pivots[j]["type"] != "L":
                continue

            L1 = pivots[j]

            for k in range(j + 1, len(pivots)):
                if pivots[k]["type"] != "H":
                    continue

                H2 = pivots[k]

                if H2["price"] <= H1["price"]:
                    continue

                for m in range(k + 1, len(pivots)):
                    if pivots[m]["type"] != "L":
                        continue

                    L2 = pivots[m]

                    if L2["price"] >= L1["price"]:
                        continue

                    for n in range(m + 1, len(pivots)):
                        if pivots[n]["type"] != "H":
                            continue

                        H3 = pivots[n]

                        if H3["price"] <= H2["price"]:
                            continue

                        hook_range = (
                            H3["price"] - L2["price"]
                        ) / H3["price"] * 100

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        return {
                            "side": "SHORT",
                            "hook_time": H3["time"],
                            "h1": H1["price"],
                            "l1": L1["price"],
                            "h2": H2["price"],
                            "l2": L2["price"],
                            "h3": H3["price"],
                            "l3": None,
                            "target": L2["price"],
                            "pivots": [
                                H1,
                                L1,
                                H2,
                                L2,
                                H3,
                            ],
                        }

    return None


def detect_m30_negative_hook(df):
    """
    NEGATIVE HOOK / LONG

        L1
         \
          H1
            \
             L2
               \
                H2
                  \
                   L3

    Conditions:

        L2 < L1
        H2 > H1
        L3 < L2
    """

    if df is None or len(df) < 20:
        return None

    highs, lows = detect_pivots(df)

    if len(highs) < 2 or len(lows) < 3:
        return None

    pivots = combined_pivots(df)

    for i in range(len(pivots)):
        if pivots[i]["type"] != "L":
            continue

        L1 = pivots[i]

        for j in range(i + 1, len(pivots)):
            if pivots[j]["type"] != "H":
                continue

            H1 = pivots[j]

            for k in range(j + 1, len(pivots)):
                if pivots[k]["type"] != "L":
                    continue

                L2 = pivots[k]

                if L2["price"] >= L1["price"]:
                    continue

                for m in range(k + 1, len(pivots)):
                    if pivots[m]["type"] != "H":
                        continue

                    H2 = pivots[m]

                    if H2["price"] <= H1["price"]:
                        continue

                    for n in range(m + 1, len(pivots)):
                        if pivots[n]["type"] != "L":
                            continue

                        L3 = pivots[n]

                        if L3["price"] >= L2["price"]:
                            continue

                        hook_range = (
                            H2["price"] - L3["price"]
                        ) / H2["price"] * 100

                        if (
                            hook_range
                            < M30_MIN_HOOK_RANGE_PCT
                        ):
                            continue

                        return {
                            "side": "LONG",
                            "hook_time": L3["time"],
                            "h1": H1["price"],
                            "l1": L1["price"],
                            "h2": H2["price"],
                            "l2": L2["price"],
                            "h3": None,
                            "l3": L3["price"],
                            "target": H2["price"],
                            "pivots": [
                                L1,
                                H1,
                                L2,
                                H2,
                                L3,
                            ],
                        }

    return None


def hook_is_expired(hook):
    return (
        now_ts() - int(hook["hook_time"])
        > MAX_HOOK_AGE_SECONDS
    )


def hook_is_invalidated(hook, df):
    if df is None or df.empty:
        return False

    hook_time = int(hook["hook_time"])

    future = df[
        df["time"] > hook_time
    ]

    if future.empty:
        return False

    side = hook["side"]

    if side == "SHORT":
        # After H3, a new confirmed HIGH is not
        # the invalidation we use here.
        #
        # A new LOW can invalidate the hook because
        # the expected M1 short sequence has already
        # been overtaken.
        _, lows = detect_pivots(
            df
        )

        for p in lows:
            if p["time"] > hook_time:
                return True

    else:
        highs, _ = detect_pivots(
            df
        )

        for p in highs:
            if p["time"] > hook_time:
                return True

    return False


# ============================================================
# HOOK DATABASE
# ============================================================

def save_hook(symbol, hook):
    conn = db()

    try:
        # Only one active Hook per symbol.
        conn.execute("""
            UPDATE hooks
            SET status = 'EXPIRED'
            WHERE symbol = ?
              AND status = 'ACTIVE'
        """, (symbol,))

        times = {
            "h1_time": None,
            "l1_time": None,
            "h2_time": None,
            "l2_time": None,
            "h3_time": None,
            "l3_time": None,
        }

        if hook.get("pivots"):
            labels = (
                ["h1_time", "l1_time",
                 "h2_time", "l2_time",
                 "h3_time"]
                if hook["side"] == "SHORT"
                else
                ["l1_time", "h1_time",
                 "l2_time", "h2_time",
                 "l3_time"]
            )

            for label, pivot in zip(
                labels,
                hook["pivots"]
            ):
                times[label] = pivot["time"]

        conn.execute("""
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
                h1_time,
                l1_time,
                h2_time,
                l2_time,
                h3_time,
                l3_time,
                status,
                created_at
            )
            VALUES (
                ?,?,?,?,?,?,?,?,?,?,
                ?,?,?,?,?,?,
                'ACTIVE',?
            )
        """, (
            symbol,
            hook["side"],
            hook["hook_time"],
            hook.get("h1"),
            hook.get("l1"),
            hook.get("h2"),
            hook.get("l2"),
            hook.get("h3"),
            hook.get("l3"),
            hook["target"],
            times["h1_time"],
            times["l1_time"],
            times["h2_time"],
            times["l2_time"],
            times["h3_time"],
            times["l3_time"],
            now_ts(),
        ))

        conn.commit()

    finally:
        conn.close()


def load_active_hooks():
    conn = db()

    try:
        rows = conn.execute("""
            SELECT *
            FROM hooks
            WHERE status = 'ACTIVE'
            ORDER BY hook_time ASC
        """).fetchall()

        result = []

        for row in rows:
            hook = dict(row)

            if hook_is_expired(hook):
                conn.execute("""
                    UPDATE hooks
                    SET status = 'EXPIRED'
                    WHERE id = ?
                """, (hook["id"],))

                continue

            result.append(hook)

        conn.commit()

        return result

    finally:
        conn.close()


# ============================================================
# M1 123F
# ============================================================

def find_m1_highs_after(df, start_time):
    highs, _ = detect_pivots(df)

    return [
        p for p in highs
        if p["time"] > start_time
    ]


def detect_m1_short_123f(df, hook):
    """
    M1 SHORT 123F

    ALL 1,2,3,F ARE HIGHS.

        1 < 2 < 3 < F

    Intermediate lows are irrelevant.
    """

    if df is None or df.empty:
        return None

    highs = find_m1_highs_after(
        df,
        hook["hook_time"]
    )

    if len(highs) < 4:
        return None

    # Search from the newest possible 4-high sequence.
    for i in range(len(highs) - 4, -1, -1):

        p1 = highs[i]
        p2 = highs[i + 1]
        p3 = highs[i + 2]
        pf = highs[i + 3]

        if not (
            p2["price"] > p1["price"]
            and
            p3["price"] > p2["price"]
            and
            pf["price"] > p3["price"]
        ):
            continue

        age = now_ts() - pf["time"]

        if age < 0 or age > M1_F_MAX_AGE_SECONDS:
            continue

        structure_distance = (
            abs(pf["price"] - hook["h3"])
            / hook["h3"] * 100
            if hook.get("h3")
            else 0
        )

        if (
            structure_distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        swing_pct = (
            (pf["price"] - p1["price"])
            / p1["price"] * 100
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        return {
            "side": "SHORT",
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "f": pf,
            "f_time": pf["time"],
        }

    return None


def detect_m1_long_123f(df, hook):
    """
    M1 LONG 123F

    ALL 1,2,3,F ARE HIGHS.

        1 > 2 > 3 > F

    Intermediate lows are irrelevant.
    """

    if df is None or df.empty:
        return None

    highs = find_m1_highs_after(
        df,
        hook["hook_time"]
    )

    if len(highs) < 4:
        return None

    for i in range(len(highs) - 4, -1, -1):

        p1 = highs[i]
        p2 = highs[i + 1]
        p3 = highs[i + 2]
        pf = highs[i + 3]

        if not (
            p2["price"] < p1["price"]
            and
            p3["price"] < p2["price"]
            and
            pf["price"] < p3["price"]
        ):
            continue

        age = now_ts() - pf["time"]

        if age < 0 or age > M1_F_MAX_AGE_SECONDS:
            continue

        structure_distance = (
            abs(pf["price"] - hook["l3"])
            / hook["l3"] * 100
            if hook.get("l3")
            else 0
        )

        if (
            structure_distance
            > M1_MAX_STRUCTURE_DISTANCE_PCT
        ):
            continue

        swing_pct = (
            (p1["price"] - pf["price"])
            / p1["price"] * 100
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        return {
            "side": "LONG",
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "f": pf,
            "f_time": pf["time"],
        }

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    side,
    hook,
    m1_setup,
    current_price
):
    if current_price is None:
        return None

    if side == "LONG":
        candidates = [
            hook.get("l1"),
            hook.get("l2"),
            hook.get("l3"),
            m1_setup["f"]["price"],
        ]

        lows = [
            x for x in candidates
            if x is not None
        ]

        if not lows:
            return None

        base_sl = min(lows)

        sl = (
            base_sl
            * (1 - SL_BUFFER_PCT / 100)
        )

        tp = hook["target"]

        return {
            "entry": current_price,
            "sl": sl,
            "tp": tp,
        }

    candidates = [
        hook.get("h1"),
        hook.get("h2"),
        hook.get("h3"),
        m1_setup["f"]["price"],
    ]

    highs = [
        x for x in candidates
        if x is not None
    ]

    if not highs:
        return None

    base_sl = max(highs)

    sl = (
        base_sl
        * (1 + SL_BUFFER_PCT / 100)
    )

    tp = hook["target"]

    return {
        "entry": current_price,
        "sl": sl,
        "tp": tp,
    }


# ============================================================
# SIGNAL DATABASE
# ============================================================

def open_trade_count():
    conn = db()

    try:
        row = conn.execute("""
            SELECT COUNT(*) AS c
            FROM signals
            WHERE status = 'OPEN'
        """).fetchone()

        return int(row["c"])

    finally:
        conn.close()


def signal_on_cooldown(symbol, side):
    conn = db()

    try:
        row = conn.execute("""
            SELECT signal_time
            FROM signals
            WHERE symbol = ?
              AND side = ?
            ORDER BY signal_time DESC
            LIMIT 1
        """, (
            symbol,
            side
        )).fetchone()

        if not row:
            return False

        return (
            now_ts() - int(row["signal_time"])
            < SIGNAL_COOLDOWN_SECONDS
        )

    finally:
        conn.close()


def insert_signal(
    symbol,
    side,
    levels,
    hook,
    m1_setup,
    chart_path
):
    conn = db()

    try:
        event_key = (
            f"{symbol}|{side}|"
            f"{hook['hook_time']}|"
            f"{m1_setup['f_time']}"
        )

        existing = conn.execute("""
            SELECT id
            FROM processed_events
            WHERE event_key = ?
        """, (event_key,)).fetchone()

        if existing:
            return None

        signal_time = now_ts()

        cur = conn.execute("""
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
                ?,?,?,?,?,?,
                'OPEN',
                ?,?,?,?,?,?
            )
        """, (
            symbol,
            side,
            signal_time,
            levels["entry"],
            levels["sl"],
            levels["tp"],
            hook["hook_time"],
            m1_setup["f_time"],
            hook["target"],
            chart_path,
            signal_time,
        ))

        conn.execute("""
            INSERT INTO processed_events (
                event_key,
                symbol,
                event_type,
                event_time,
                created_at
            )
            VALUES (
                ?,?,?,?,?
            )
        """, (
            event_key,
            symbol,
            "SIGNAL",
            signal_time,
            signal_time,
        ))

        conn.commit()

        return cur.lastrowid

    finally:
        conn.close()


# ============================================================
# PERCENTAGE CALCULATIONS
# ============================================================

def calculate_pnl(side, entry, current):
    if not entry or not current:
        return None

    if side == "LONG":
        return (
            (current - entry)
            / entry
            * 100
        )

    return (
        (entry - current)
        / entry
        * 100
    )


def level_distance_pct(
    side,
    current,
    level,
    level_type
):
    if current is None or level is None:
        return None

    if side == "LONG":

        if level_type == "TP":
            return (
                (level - current)
                / current
                * 100
            )

        return (
            (level - current)
            / current
            * 100
        )

    # SHORT

    if level_type == "TP":
        return (
            (current - level)
            / current
            * 100
        )

    return (
        (current - level)
        / current
        * 100
    )


def target_distance_pct(
    side,
    current,
    target
):
    if current is None or target is None:
        return None

    if side == "LONG":
        return (
            (target - current)
            / current
            * 100
        )

    return (
        (current - target)
        / current
        * 100
    )


# ============================================================
# CHART HELPERS
# ============================================================

def get_hook_points(hook, df=None):
    """
    Returns exact M30 Hook points.

    Existing old DB rows may not have pivot timestamps.
    In that case, the current M30 structure is reconstructed.
    """

    side = hook["side"]

    if side == "SHORT":

        points = [
            ("H1", hook.get("h1"), hook.get("h1_time")),
            ("L1", hook.get("l1"), hook.get("l1_time")),
            ("H2", hook.get("h2"), hook.get("h2_time")),
            ("L2", hook.get("l2"), hook.get("l2_time")),
            ("H3", hook.get("h3"), hook.get("h3_time")),
        ]

    else:

        points = [
            ("L1", hook.get("l1"), hook.get("l1_time")),
            ("H1", hook.get("h1"), hook.get("h1_time")),
            ("L2", hook.get("l2"), hook.get("l2_time")),
            ("H2", hook.get("h2"), hook.get("h2_time")),
            ("L3", hook.get("l3"), hook.get("l3_time")),
        ]

    # Old database fallback
    if any(
        x[2] is None
        for x in points
    ) and df is not None:

        if side == "SHORT":
            detected = detect_m30_positive_hook(df)
        else:
            detected = detect_m30_negative_hook(df)

        if detected:
            points = []

            labels = (
                ["H1", "L1", "H2", "L2", "H3"]
                if side == "SHORT"
                else ["L1", "H1", "L2", "H2", "L3"]
            )

            for label, pivot in zip(
                labels,
                detected["pivots"]
            ):
                points.append((
                    label,
                    pivot["price"],
                    pivot["time"]
                ))

    return points


def save_m30_hook_chart(
    symbol,
    hook,
    df,
    current_price=None,
    levels=None,
    context="HOOK"
):
    if not ENABLE_CHARTS:
        return None

    if df is None or df.empty:
        return None

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    plot_df = df.tail(
        CHART_CANDLES
        if levels
        else ACTIVE_HOOK_CHART_CANDLES
    ).copy()

    if plot_df.empty:
        return None

    points = get_hook_points(
        hook,
        df
    )

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    # --------------------------------------------------------
    # Candlesticks
    # --------------------------------------------------------

    candle_width = 0.65

    for i, row in plot_df.iterrows():

        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        if c >= o:
            candle_color = "green"
        else:
            candle_color = "red"

        ax.vlines(
            i,
            l,
            h,
            color=candle_color,
            linewidth=1
        )

        body_low = min(o, c)
        body_height = abs(c - o)

        if body_height <= 0:
            body_height = max(
                abs(h - l) * 0.01,
                1e-12
            )

        rect = Rectangle(
            (
                i - candle_width / 2,
                body_low
            ),
            candle_width,
            body_height,
            facecolor=candle_color,
            edgecolor=candle_color,
            alpha=0.75
        )

        ax.add_patch(rect)

    # --------------------------------------------------------
    # Hook points
    # --------------------------------------------------------

    x_values = []
    y_values = []
    labels = []

    time_to_x = {
        int(row["time"]): i
        for i, (_, row) in enumerate(
            plot_df.iterrows()
        )
    }

    for label, price, ts in points:

        if price is None:
            continue

        x = None

        if ts is not None:
            ts = int(ts)

            if ts in time_to_x:
                x = time_to_x[ts]
            else:
                # nearest candle
                nearest = min(
                    time_to_x.keys(),
                    key=lambda t: abs(t - ts)
                )

                if abs(nearest - ts) <= 30 * 60 * 2:
                    x = time_to_x[nearest]

        if x is None:
            continue

        x_values.append(x)
        y_values.append(float(price))
        labels.append(label)

    if x_values:
        ax.plot(
            x_values,
            y_values,
            linestyle="-",
            linewidth=2,
            marker="o",
            markersize=6,
            label="NDS Hook"
        )

        y_range = (
            max(plot_df["high"])
            - min(plot_df["low"])
        )

        offset = y_range * 0.025

        for x, y, label in zip(
            x_values,
            y_values,
            labels
        ):
            ax.annotate(
                label,
                (
                    x,
                    y
                ),
                xytext=(
                    0,
                    offset if label.startswith("H")
                    else -offset
                ),
                textcoords="offset points",
                ha="center",
                fontsize=11,
                fontweight="bold"
            )

    # --------------------------------------------------------
    # Current price
    # --------------------------------------------------------

    if current_price is not None:
        ax.axhline(
            current_price,
            linestyle="--",
            linewidth=1.2,
            label=(
                f"Current "
                f"{fmt_price(current_price)}"
            )
        )

    # --------------------------------------------------------
    # Trade levels
    # --------------------------------------------------------

    if levels:

        entry = levels.get("entry")
        sl = levels.get("sl")
        tp = levels.get("tp")

        if entry is not None:
            ax.axhline(
                entry,
                linestyle="-.",
                linewidth=1.3,
                label=f"Entry {fmt_price(entry)}"
            )

        if sl is not None:
            ax.axhline(
                sl,
                linestyle="--",
                linewidth=1.3,
                label=f"SL {fmt_price(sl)}"
            )

        if tp is not None:
            ax.axhline(
                tp,
                linestyle="--",
                linewidth=1.3,
                label=f"TP {fmt_price(tp)}"
            )

    else:

        target = hook.get("target")

        if target is not None:
            ax.axhline(
                target,
                linestyle="--",
                linewidth=1.3,
                label=f"Target {fmt_price(target)}"
            )

    # --------------------------------------------------------
    # X axis
    # --------------------------------------------------------

    tick_count = min(
        10,
        len(plot_df)
    )

    if tick_count > 1:
        indexes = np.linspace(
            0,
            len(plot_df) - 1,
            tick_count
        ).astype(int)

        ax.set_xticks(indexes)

        labels_x = []

        for idx in indexes:
            ts = int(
                plot_df.iloc[idx]["time"]
            )

            labels_x.append(
                datetime.fromtimestamp(
                    ts,
                    timezone.utc
                ).strftime("%m-%d %H:%M")
            )

        ax.set_xticklabels(
            labels_x,
            rotation=35,
            ha="right"
        )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    side = hook["side"]

    ax.set_title(
        f"NDS M30 | {symbol} | "
        f"{side} | {context}\n"
        f"Hook Points Marked"
    )

    ax.set_ylabel("Price")
    ax.set_xlabel("M30")

    ax.grid(
        True,
        alpha=0.25
    )

    ax.legend(
        loc="best",
        fontsize=9
    )

    fig.tight_layout()

    filename = (
        f"{clean_symbol(symbol)}_"
        f"{side}_"
        f"{hook['hook_time']}_"
        f"{context.lower()}_m30.png"
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

    plt.close(fig)

    return path


# ============================================================
# SIGNAL TELEGRAM
# ============================================================

def send_signal_message(
    symbol,
    side,
    levels,
    hook,
    m1_setup,
    chart_path,
    current_price
):
    entry = levels["entry"]
    sl = levels["sl"]
    tp = levels["tp"]

    sl_pct = level_distance_pct(
        side,
        current_price,
        sl,
        "SL"
    )

    tp_pct = level_distance_pct(
        side,
        current_price,
        tp,
        "TP"
    )

    pnl = calculate_pnl(
        side,
        entry,
        current_price
    )

    direction = (
        "🟢 LONG"
        if side == "LONG"
        else "🔴 SHORT"
    )

    caption = (
        f"<b>NDS M30 → M1 SIGNAL</b>\n"
        f"{direction} <b>{symbol}</b>\n\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"SL: <b>{fmt_price(sl)}</b> "
        f"({fmt_pct(sl_pct)})\n"
        f"TP: <b>{fmt_price(tp)}</b> "
        f"({fmt_pct(tp_pct)})\n"
        f"Current: <b>{fmt_price(current_price)}</b>\n"
        f"P/L: <b>{fmt_pct(pnl)}</b>\n\n"
        f"M1: 1 → 2 → 3 → F\n"
        f"F: {fmt_price(m1_setup['f']['price'])}\n"
        f"F age: "
        f"{max(0, now_ts() - m1_setup['f_time'])}s\n\n"
        f"<b>M30 Hook points are marked "
        f"on chart.</b>"
    )

    if chart_path:
        if send_telegram_photo(
            chart_path,
            caption
        ):
            return True

    return send_telegram(caption)


# ============================================================
# M30 SCAN
# ============================================================

def scan_m30(assets):
    new_hooks = 0

    for symbol in assets:

        try:
            df = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES
            )

            if df.empty:
                continue

            df = get_closed_candles(
                df,
                30 * 60
            )

            if len(df) < 30:
                continue

            # Positive / SHORT
            hook = detect_m30_positive_hook(
                df
            )

            if hook:

                if not hook_is_expired(
                    hook
                ):
                    save_hook(
                        symbol,
                        hook
                    )

                    new_hooks += 1

                    print(
                        f"M30 SHORT HOOK "
                        f"{symbol} "
                        f"H1={fmt_price(hook['h1'])} "
                        f"H2={fmt_price(hook['h2'])} "
                        f"H3={fmt_price(hook['h3'])} "
                        f"L1={fmt_price(hook['l1'])} "
                        f"L2={fmt_price(hook['l2'])}"
                    )

                continue

            # Negative / LONG
            hook = detect_m30_negative_hook(
                df
            )

            if hook:

                if not hook_is_expired(
                    hook
                ):
                    save_hook(
                        symbol,
                        hook
                    )

                    new_hooks += 1

                    print(
                        f"M30 LONG HOOK "
                        f"{symbol} "
                        f"L1={fmt_price(hook['l1'])} "
                        f"L2={fmt_price(hook['l2'])} "
                        f"L3={fmt_price(hook['l3'])} "
                        f"H1={fmt_price(hook['h1'])} "
                        f"H2={fmt_price(hook['h2'])}"
                    )

        except Exception as exc:
            print(
                f"M30 scan error "
                f"{symbol}: {exc}"
            )

        time.sleep(REQUEST_SLEEP)

    return new_hooks


# ============================================================
# M1 SCAN
# ============================================================

def scan_m1(assets):
    global signals_since_last_report

    new_long = 0
    new_short = 0

    hooks = load_active_hooks()

    hook_symbols = {
        h["symbol"]
        for h in hooks
    }

    if not hook_symbols:
        return 0, 0

    price_map = fetch_price_map(
        list(hook_symbols)
    )

    for hook in hooks:

        symbol = hook["symbol"]
        side = hook["side"]

        try:

            df = fetch_candles(
                symbol,
                M1_INTERVAL,
                M1_CANDLES
            )

            if df.empty:
                continue

            df = get_closed_candles(
                df,
                60
            )

            if len(df) < 20:
                continue

            current_price = (
                price_map.get(symbol)
                or fetch_current_price(symbol)
            )

            if current_price is None:
                continue

            # ------------------------------------------------
            # Check Hook invalidation
            # ------------------------------------------------

            if hook_is_invalidated(
                hook,
                fetch_candles(
                    symbol,
                    M30_INTERVAL,
                    M30_CANDLES
                )
            ):
                conn = db()

                try:
                    conn.execute("""
                        UPDATE hooks
                        SET status = 'INVALID',
                            invalidated_at = ?
                        WHERE id = ?
                    """, (
                        now_ts(),
                        hook["id"]
                    ))

                    conn.commit()

                finally:
                    conn.close()

                continue

            # ------------------------------------------------
            # M1 123F
            # ------------------------------------------------

            if side == "SHORT":
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

            if open_trade_count() >= MAX_OPEN_TRADES:
                continue

            if signal_on_cooldown(
                symbol,
                side
            ):
                continue

            levels = calculate_trade_levels(
                side,
                hook,
                setup,
                current_price
            )

            if not levels:
                continue

            # ------------------------------------------------
            # M30 signal chart
            # ------------------------------------------------

            chart_path = None

            if ENABLE_CHARTS:

                m30_df = fetch_candles(
                    symbol,
                    M30_INTERVAL,
                    M30_CANDLES
                )

                if not m30_df.empty:

                    m30_df = get_closed_candles(
                        m30_df,
                        30 * 60
                    )

                    chart_path = save_m30_hook_chart(
                        symbol,
                        hook,
                        m30_df,
                        current_price=current_price,
                        levels=levels,
                        context="NEW SIGNAL"
                    )

            signal_id = insert_signal(
                symbol,
                side,
                levels,
                hook,
                setup,
                chart_path
            )

            if signal_id is None:
                continue

            signals_since_last_report += 1

            if side == "LONG":
                new_long += 1
            else:
                new_short += 1

            send_signal_message(
                symbol,
                side,
                levels,
                hook,
                setup,
                chart_path,
                current_price
            )

            print(
                f"NEW {side} "
                f"{symbol} "
                f"Entry={fmt_price(levels['entry'])} "
                f"SL={fmt_price(levels['sl'])} "
                f"TP={fmt_price(levels['tp'])}"
            )

        except Exception as exc:
            print(
                f"M1 scan error "
                f"{symbol}: {exc}"
            )
            traceback.print_exc()

        time.sleep(REQUEST_SLEEP)

    return new_long, new_short


# ============================================================
# OPEN TRADE MANAGEMENT
# ============================================================

def close_trade(
    signal,
    exit_price,
    reason
):
    side = signal["side"]

    pnl = calculate_pnl(
        side,
        signal["entry"],
        exit_price
    )

    conn = db()

    try:
        conn.execute("""
            UPDATE signals
            SET status = 'CLOSED',
                exit_time = ?,
                exit_price = ?,
                pnl_pct = ?,
                exit_reason = ?
            WHERE id = ?
        """, (
            now_ts(),
            exit_price,
            pnl,
            reason,
            signal["id"]
        ))

        conn.commit()

    finally:
        conn.close()

    direction = (
        "🟢 LONG"
        if side == "LONG"
        else "🔴 SHORT"
    )

    text = (
        f"<b>NDS TRADE CLOSED</b>\n"
        f"{direction} <b>{signal['symbol']}</b>\n\n"
        f"Entry: {fmt_price(signal['entry'])}\n"
        f"Exit: <b>{fmt_price(exit_price)}</b>\n"
        f"Reason: <b>{reason}</b>\n"
        f"P/L: <b>{fmt_pct(pnl)}</b>"
    )

    send_telegram(text)


def manage_open_trades(price_map):
    conn = db()

    try:
        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
        """).fetchall()

    finally:
        conn.close()

    for row in rows:

        signal = dict(row)

        symbol = signal["symbol"]

        current = (
            price_map.get(symbol)
            or fetch_current_price(symbol)
        )

        if current is None:
            continue

        side = signal["side"]

        if side == "LONG":

            if current <= signal["sl"]:
                close_trade(
                    signal,
                    current,
                    "SL"
                )

            elif current >= signal["tp"]:
                close_trade(
                    signal,
                    current,
                    "TP"
                )

        else:

            if current >= signal["sl"]:
                close_trade(
                    signal,
                    current,
                    "SL"
                )

            elif current <= signal["tp"]:
                close_trade(
                    signal,
                    current,
                    "TP"
                )


# ============================================================
# REPORT
# ============================================================

def get_report_data():
    conn = db()

    try:
        open_trades = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'OPEN'
            ORDER BY signal_time DESC
        """).fetchall()

        closed = conn.execute("""
            SELECT *
            FROM signals
            WHERE status = 'CLOSED'
            ORDER BY exit_time DESC
        """).fetchall()

        hooks = conn.execute("""
            SELECT *
            FROM hooks
            WHERE status = 'ACTIVE'
            ORDER BY hook_time DESC
        """).fetchall()

        return {
            "open_trades": [
                dict(x)
                for x in open_trades
            ],
            "closed": [
                dict(x)
                for x in closed
            ],
            "hooks": [
                dict(x)
                for x in hooks
            ],
        }

    finally:
        conn.close()


def send_active_hook_charts(
    hooks,
    price_map
):
    for hook in hooks:

        symbol = hook["symbol"]

        try:
            current = (
                price_map.get(symbol)
                or fetch_current_price(symbol)
            )

            m30_df = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES
            )

            if m30_df.empty:
                continue

            m30_df = get_closed_candles(
                m30_df,
                30 * 60
            )

            chart_path = save_m30_hook_chart(
                symbol,
                hook,
                m30_df,
                current_price=current,
                levels=None,
                context="ACTIVE HOOK"
            )

            if not chart_path:
                continue

            target_pct = target_distance_pct(
                hook["side"],
                current,
                hook["target"]
            )

            direction = (
                "🟢 LONG"
                if hook["side"] == "LONG"
                else "🔴 SHORT"
            )

            points = get_hook_points(
                hook,
                m30_df
            )

            point_text = " → ".join(
                p[0]
                for p in points
            )

            caption = (
                f"<b>NDS ACTIVE M30 HOOK</b>\n"
                f"{direction} <b>{symbol}</b>\n\n"
                f"Points: <b>{point_text}</b>\n"
                f"Current: "
                f"<b>{fmt_price(current)}</b>\n"
                f"Target: "
                f"<b>{fmt_price(hook['target'])}</b> "
                f"({fmt_pct(target_pct)})\n"
                f"Hook age: "
                f"{max(0, now_ts() - hook['hook_time']) // 60}m"
            )

            send_telegram_photo(
                chart_path,
                caption
            )

            time.sleep(0.25)

        except Exception as exc:
            print(
                f"Active hook chart error "
                f"{symbol}: {exc}"
            )


def send_report(
    assets_scanned,
    new_long,
    new_short
):
    global signals_since_last_report

    data = get_report_data()

    open_trades = data["open_trades"]
    closed = data["closed"]
    hooks = data["hooks"]

    # --------------------------------------------------------
    # Current prices
    # --------------------------------------------------------

    symbols = [
        x["symbol"]
        for x in open_trades
    ]

    symbols += [
        x["symbol"]
        for x in hooks
        if x["symbol"] not in symbols
    ]

    price_map = fetch_price_map(
        list(set(symbols))
    )

    latest_price_map.update(
        price_map
    )

    # --------------------------------------------------------
    # Closed statistics
    # --------------------------------------------------------

    wins = sum(
        1
        for x in closed
        if x["pnl_pct"] is not None
        and x["pnl_pct"] > 0
    )

    losses = sum(
        1
        for x in closed
        if x["pnl_pct"] is not None
        and x["pnl_pct"] <= 0
    )

    total_pnl = sum(
        float(x["pnl_pct"] or 0)
        for x in closed
    )

    text = (
        f"<b>📊 NDS M30 → M1 REPORT</b>\n"
        f"Version: <b>{VERSION}</b>\n\n"
        f"Assets scanned: <b>{assets_scanned}</b>\n"
        f"🟢 New LONG: <b>{new_long}</b>\n"
        f"🔴 New SHORT: <b>{new_short}</b>\n"
        f"Active Hooks: <b>{len(hooks)}</b>\n\n"
        f"<b>OPEN TRADES: {len(open_trades)}</b>\n"
    )

    if not open_trades:
        text += "None\n"

    for trade in open_trades:

        symbol = trade["symbol"]
        side = trade["side"]

        current = price_map.get(
            symbol
        )

        if current is None:
            current = fetch_current_price(
                symbol
            )

        pnl = calculate_pnl(
            side,
            trade["entry"],
            current
        )

        sl_pct = level_distance_pct(
            side,
            current,
            trade["sl"],
            "SL"
        )

        tp_pct = level_distance_pct(
            side,
            current,
            trade["tp"],
            "TP"
        )

        direction = (
            "🟢 LONG"
            if side == "LONG"
            else "🔴 SHORT"
        )

        text += (
            f"\n{direction} "
            f"<b>{symbol}</b>\n"
            f"Entry: {fmt_price(trade['entry'])}\n"
            f"Current: "
            f"<b>{fmt_price(current)}</b> | "
            f"P/L: <b>{fmt_pct(pnl)}</b>\n"
            f"SL: {fmt_price(trade['sl'])} "
            f"({fmt_pct(sl_pct)})\n"
            f"TP: {fmt_price(trade['tp'])} "
            f"({fmt_pct(tp_pct)})\n"
        )

    text += (
        f"\n<b>CLOSED TRADES: {len(closed)}</b>\n"
        f"Wins: {wins} | Losses: {losses}\n"
        f"Total P/L: <b>{fmt_pct(total_pnl)}</b>\n"
    )

    # --------------------------------------------------------
    # Send report
    # --------------------------------------------------------

    send_telegram(text)

    # --------------------------------------------------------
    # IMPORTANT:
    # If there was NO NEW SIGNAL since the previous report,
    # send charts of ACTIVE M30 Hooks.
    # --------------------------------------------------------

    if signals_since_last_report == 0:
        if hooks:
            send_active_hook_charts(
                hooks,
                price_map
            )

    signals_since_last_report = 0


# ============================================================
# ERROR
# ============================================================

def log_error(message):
    print(message)

    try:
        send_telegram(
            f"<b>🚨 NDS SCANNER ERROR</b>\n\n"
            f"<code>{message[:3000]}</code>\n\n"
            f"Version: {VERSION}"
        )
    except Exception:
        pass


# ============================================================
# MAIN
# ============================================================

def main():
    global latest_price_map

    if not PAPER_ONLY:
        raise RuntimeError(
            "PAPER_ONLY must remain True."
        )

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    init_db()

    assets = discover_assets()

    if not assets:
        raise RuntimeError(
            "No Kraken PF assets discovered."
        )

    start_time = time.time()

    last_asset_refresh = 0

    last_m30_scan = 0
    last_m1_scan = 0
    last_report = 0

    total_new_long = 0
    total_new_short = 0

    # --------------------------------------------------------
    # Immediate first M30 scan
    # --------------------------------------------------------

    print(
        f"Starting NDS scanner {VERSION}"
    )

    print(
        f"Assets: {len(assets)}"
    )

    try:
        new_hooks = scan_m30(
            assets
        )

        print(
            f"Initial M30 hooks: {new_hooks}"
        )

    except Exception as exc:
        log_error(
            f"Initial M30 scan failed: {exc}"
        )

    # --------------------------------------------------------
    # Immediate first M1 scan
    # --------------------------------------------------------

    try:
        new_long, new_short = scan_m1(
            assets
        )

        total_new_long += new_long
        total_new_short += new_short

    except Exception as exc:
        log_error(
            f"Initial M1 scan failed: {exc}"
        )

    last_m30_scan = time.time()
    last_m1_scan = time.time()
    last_report = time.time()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while (
        time.time() - start_time
        < RUN_DURATION_SECONDS
    ):

        try:

            current_time = time.time()

            # ------------------------------------------------
            # Refresh assets
            # ------------------------------------------------

            if (
                current_time
                - last_asset_refresh
                >= ASSET_REFRESH_SECONDS
            ):

                new_assets = discover_assets()

                if new_assets:
                    assets = new_assets

                last_asset_refresh = current_time

            # ------------------------------------------------
            # Prices
            # ------------------------------------------------

            price_map = fetch_price_map(
                assets
            )

            latest_price_map = price_map

            # ------------------------------------------------
            # Manage open trades
            # ------------------------------------------------

            manage_open_trades(
                price_map
            )

            # ------------------------------------------------
            # M30
            # ------------------------------------------------

            if (
                current_time
                - last_m30_scan
                >= M30_SCAN_SECONDS
            ):

                scan_m30(
                    assets
                )

                last_m30_scan = current_time

            # ------------------------------------------------
            # M1
            # ------------------------------------------------

            if (
                current_time
                - last_m1_scan
                >= M1_SCAN_SECONDS
            ):

                new_long, new_short = scan_m1(
                    assets
                )

                total_new_long += new_long
                total_new_short += new_short

                last_m1_scan = current_time

            # ------------------------------------------------
            # Report
            # ------------------------------------------------

            if (
                current_time
                - last_report
                >= REPORT_INTERVAL_SECONDS
            ):

                send_report(
                    len(assets),
                    total_new_long,
                    total_new_short
                )

                total_new_long = 0
                total_new_short = 0

                last_report = current_time

            time.sleep(1)

        except Exception as exc:

            log_error(
                f"MAIN LOOP ERROR: {exc}"
            )

            traceback.print_exc()

            time.sleep(
                ERROR_SLEEP
            )

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    try:

        send_report(
            len(assets),
            total_new_long,
            total_new_short
        )

    except Exception as exc:

        log_error(
            f"Final report error: {exc}"
        )

    print(
        f"NDS scanner {VERSION} finished."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
