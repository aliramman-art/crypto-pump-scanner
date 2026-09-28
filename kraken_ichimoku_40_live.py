# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 4.3.4
# ============================================================
#
# PAPER ONLY
#
# FIXES v4.3.4
# ------------------------------------------------------------
# 1) Fixed M30 "Short data" problem.
# 2) Fetch more candles than the analysis lookback.
# 3) Closed-candle count is checked independently.
# 4) Real candle counts are included in diagnostics.
# 5) M30 pivot diagnostics added.
# 6) M1 pivot / 123F diagnostics preserved.
# 7) Entry rejection diagnostics preserved.
#
# NDS STRUCTURE
#
# 30M SHORT:
#   H1 -> L1 -> H2 -> L2 -> H3
#
#   H2 > H1
#   L2 < L1
#   H3 > H2
#
# 30M LONG:
#   L1 -> H1 -> L2 -> H2 -> L3
#
#   L2 < L1
#   H2 > H1
#   L3 < L2
#
# 1M SHORT 123F:
#   HIGH 1 < HIGH 2 < HIGH 3 < HIGH F
#
# 1M LONG 123F:
#   HIGH 1 > HIGH 2 > HIGH 3 > HIGH F
#
# M1 intermediate lows are NOT used for 123F validation.
#
# ============================================================

import os
import sys
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests

# matplotlib is only required for charts
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    CHARTS_AVAILABLE = True
except Exception:
    CHARTS_AVAILABLE = False


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.3.4"

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

REQUEST_TIMEOUT = 20

ASSET_LIMIT = 100

# ------------------------------------------------------------
# IMPORTANT v4.3.4
# ------------------------------------------------------------
# We fetch MORE candles than we actually analyze.
#
# The previous version effectively treated the analysis
# lookback as the required API response size.
#
# This caused all 100 assets to become "Short data".
# ------------------------------------------------------------

M30_FETCH_LIMIT = 400
M30_LOOKBACK = 300
M30_MIN_CLOSED = 50

M1_FETCH_LIMIT = 1600
M1_LOOKBACK = 1500
M1_MIN_CLOSED = 100

M30_SCAN_SECONDS = 300
M1_SCAN_SECONDS = 60
REPORT_SECONDS = 900
RUNTIME_SECONDS = 12 * 60

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

M30_MIN_HOOK_RANGE = 0.0020       # 0.20%
M1_MIN_SWING = 0.0007            # 0.07%
M1_MAX_STRUCTURE_DISTANCE = 0.03  # 3%

MAX_HOOK_AGE_SECONDS = 6 * 60 * 60
M1_F_MAX_AGE_SECONDS = 5 * 60

MAX_OPEN_TRADES = 3
SIGNAL_COOLDOWN_SECONDS = 60 * 60

SL_BUFFER = 0.0015               # 0.15%

CHARTS_ENABLED = True

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "NDS-M30-M1-Scanner/4.3.4",
        "Accept": "application/json",
    }
)

latest_price_map = {}

signals_since_last_report = 0


# ============================================================
# DIAGNOSTICS
# ============================================================

diagnostics = {
    "m30_requests": 0,
    "m30_data_ok": 0,
    "m30_data_empty": 0,
    "m30_data_short": 0,
    "m30_min_candles": 0,
    "m30_max_candles": 0,
    "m30_total_candles": 0,
    "m30_pivots_high": 0,
    "m30_pivots_low": 0,

    "m30_short_candidates": 0,
    "m30_long_candidates": 0,
    "m30_short_saved": 0,
    "m30_long_saved": 0,
    "m30_expired": 0,

    "m1_hook_scans": 0,
    "m1_requests": 0,
    "m1_data_ok": 0,
    "m1_data_empty": 0,
    "m1_data_short": 0,
    "m1_min_candles": 0,
    "m1_max_candles": 0,
    "m1_total_candles": 0,
    "m1_pivots_high": 0,
    "m1_pivots_low": 0,

    "m1_123f_candidates": 0,
    "m1_f_too_old": 0,
    "m1_distance_rejected": 0,
    "m1_swing_rejected": 0,
    "m1_no_structure": 0,

    "hook_invalidated": 0,
    "max_open_trades": 0,
    "cooldown": 0,
    "level_error": 0,
    "duplicate_signal": 0,

    "signals_created": 0,
    "errors": 0,
}


def reset_diagnostics():
    for key in diagnostics:
        diagnostics[key] = 0


def inc_diag(key, value=1):
    if key in diagnostics:
        diagnostics[key] += value


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
        tz=timezone.utc
    ).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "").strip()

        return float(value)

    except Exception:
        return default


def pct_change(a, b):
    if a in (None, 0) or b is None:
        return 0.0

    return (b - a) / a


def html_escape(value):
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_enabled():
    return bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID)


def send_telegram(message):
    if not telegram_enabled():
        return False

    try:
        url = (
            f"https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}/sendMessage"
        )

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception as exc:
        print(f"Telegram error: {exc}")
        return False


# ============================================================
# KRAKEN API
# ============================================================

def get_json(url, params=None):
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        print(f"HTTP error: {url} -> {exc}")
        return None


def parse_candle_row(row):
    """
    Supports several Kraken candle formats.

    Dict:
        time/open/high/low/close/volume

    List:
        [time, open, high, low, close, ...]
    """

    try:
        if isinstance(row, dict):
            ts = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
            )

            o = (
                row.get("open")
                or row.get("o")
            )

            h = (
                row.get("high")
                or row.get("h")
            )

            l = (
                row.get("low")
                or row.get("l")
            )

            c = (
                row.get("close")
                or row.get("c")
            )

            v = (
                row.get("volume")
                or row.get("v")
                or 0
            )

        elif isinstance(row, (list, tuple)):
            if len(row) < 5:
                return None

            ts = row[0]
            o = row[1]
            h = row[2]
            l = row[3]
            c = row[4]
            v = row[5] if len(row) > 5 else 0

        else:
            return None

        ts = safe_float(ts)
        o = safe_float(o)
        h = safe_float(h)
        l = safe_float(l)
        c = safe_float(c)
        v = safe_float(v, 0.0)

        if ts is None:
            return None

        # Kraken may return milliseconds.
        if ts > 10_000_000_000:
            ts /= 1000.0

        if any(x is None for x in (o, h, l, c)):
            return None

        return {
            "time": int(ts),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": v,
        }

    except Exception:
        return None


def extract_candles(data):
    if not data:
        return []

    rows = None

    if isinstance(data, dict):

        if isinstance(data.get("candles"), list):
            rows = data["candles"]

        elif isinstance(data.get("data"), list):
            rows = data["data"]

        elif isinstance(data.get("result"), dict):

            result = data["result"]

            if isinstance(result.get("candles"), list):
                rows = result["candles"]

            elif isinstance(result.get("data"), list):
                rows = result["data"]

    elif isinstance(data, list):
        rows = data

    if not rows:
        return []

    candles = []

    for row in rows:
        candle = parse_candle_row(row)

        if candle:
            candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    # Remove exact duplicates.
    clean = []
    seen = set()

    for candle in candles:
        ts = candle["time"]

        if ts in seen:
            continue

        seen.add(ts)
        clean.append(candle)

    return clean


def fetch_ohlcv(symbol, interval, limit):
    """
    Kraken chart endpoint.

    interval:
        30m
        1m
    """

    url = (
        f"{KRAKEN_CHART_BASE}/trade/"
        f"{symbol}/{interval}"
    )

    data = get_json(
        url,
        params={
            "count": limit,
        },
    )

    candles = extract_candles(data)

    return candles


def fetch_current_price(symbol):
    try:
        url = f"{KRAKEN_REST_BASE}/tickers"

        data = get_json(url)

        if not data:
            return None

        tickers = data.get("tickers")

        if isinstance(tickers, list):

            for item in tickers:

                item_symbol = (
                    item.get("symbol")
                    or item.get("pair")
                    or item.get("instrument")
                )

                if item_symbol != symbol:
                    continue

                price = (
                    item.get("last")
                    or item.get("lastPrice")
                    or item.get("price")
                    or item.get("markPrice")
                )

                price = safe_float(price)

                if price is not None:
                    latest_price_map[symbol] = price

                return price

        elif isinstance(tickers, dict):

            item = tickers.get(symbol)

            if isinstance(item, dict):

                price = (
                    item.get("last")
                    or item.get("lastPrice")
                    or item.get("price")
                    or item.get("markPrice")
                )

                price = safe_float(price)

                if price is not None:
                    latest_price_map[symbol] = price

                return price

    except Exception:
        pass

    return latest_price_map.get(symbol)


# ============================================================
# ASSETS
# ============================================================

def fetch_assets():
    """
    Returns PF_*USD contracts sorted by available volume data.

    Falls back to alphabetical order if ticker volume cannot
    be obtained.
    """

    instruments_url = (
        f"{KRAKEN_REST_BASE}/instruments"
    )

    data = get_json(instruments_url)

    instruments = []

    if isinstance(data, dict):
        instruments = data.get("instruments") or []

    candidates = []

    for item in instruments:

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("instrument")
            or item.get("contract")
        )

        if not symbol:
            continue

        symbol = str(symbol)

        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        candidates.append(symbol)

    candidates = list(dict.fromkeys(candidates))

    # Try ticker volume ranking.
    volume_map = {}

    try:
        tickers_data = get_json(
            f"{KRAKEN_REST_BASE}/tickers"
        )

        tickers = (
            tickers_data.get("tickers", [])
            if isinstance(tickers_data, dict)
            else []
        )

        if isinstance(tickers, list):

            for item in tickers:

                if not isinstance(item, dict):
                    continue

                symbol = (
                    item.get("symbol")
                    or item.get("pair")
                    or item.get("instrument")
                )

                if symbol not in candidates:
                    continue

                volume = (
                    item.get("vol24h")
                    or item.get("volume24h")
                    or item.get("volume")
                    or item.get("vol")
                )

                volume = safe_float(volume, 0)

                volume_map[symbol] = volume

        elif isinstance(tickers, dict):

            for symbol in candidates:

                item = tickers.get(symbol)

                if not isinstance(item, dict):
                    continue

                volume = (
                    item.get("vol24h")
                    or item.get("volume24h")
                    or item.get("volume")
                    or item.get("vol")
                )

                volume_map[symbol] = safe_float(
                    volume,
                    0
                )

    except Exception:
        pass

    if volume_map:
        candidates.sort(
            key=lambda x: volume_map.get(x, 0),
            reverse=True,
        )
    else:
        candidates.sort()

    return candidates[:ASSET_LIMIT]


# ============================================================
# SQLITE
# ============================================================

def db_connect():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    return conn


def table_columns(conn, table):
    try:
        rows = conn.execute(
            f"PRAGMA table_info({table})"
        ).fetchall()

        return {
            row["name"]
            for row in rows
        }

    except Exception:
        return set()


def add_column_if_missing(
    conn,
    table,
    column,
    definition,
):
    columns = table_columns(conn, table)

    if column not in columns:

        conn.execute(
            f"ALTER TABLE {table} "
            f"ADD COLUMN {column} {definition}"
        )


def init_db():

    conn = db_connect()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at INTEGER,
            finished_at INTEGER,
            assets_scanned INTEGER DEFAULT 0,
            configured INTEGER DEFAULT 1
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            current_price REAL,
            status TEXT DEFAULT 'OPEN',
            pnl_pct REAL DEFAULT 0,
            opened_at INTEGER,
            closed_at INTEGER,
            close_price REAL,
            exit_reason TEXT,
            hook_time INTEGER,
            f_time INTEGER,
            chart_path TEXT,
            created_at INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS processed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE,
            symbol TEXT,
            event_type TEXT,
            event_time INTEGER,
            created_at INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            status TEXT DEFAULT 'ACTIVE',

            h1 REAL,
            l1 REAL,
            h2 REAL,
            l2 REAL,
            h3 REAL,

            l3 REAL,

            hook_time INTEGER,

            created_at INTEGER,
            expired_at INTEGER
        )
        """
    )

    # --------------------------------------------------------
    # Migrations for older databases
    # --------------------------------------------------------

    migrations = [
        ("signals", "current_price", "REAL DEFAULT 0"),
        ("signals", "status", "TEXT DEFAULT 'OPEN'"),
        ("signals", "pnl_pct", "REAL DEFAULT 0"),
        ("signals", "opened_at", "INTEGER"),
        ("signals", "closed_at", "INTEGER"),
        ("signals", "close_price", "REAL"),
        ("signals", "exit_reason", "TEXT"),
        ("signals", "hook_time", "INTEGER"),
        ("signals", "f_time", "INTEGER"),
        ("signals", "chart_path", "TEXT"),
        ("signals", "created_at", "INTEGER"),

        ("hooks", "status", "TEXT DEFAULT 'ACTIVE'"),
        ("hooks", "h1", "REAL"),
        ("hooks", "l1", "REAL"),
        ("hooks", "h2", "REAL"),
        ("hooks", "l2", "REAL"),
        ("hooks", "h3", "REAL"),
        ("hooks", "l3", "REAL"),
        ("hooks", "hook_time", "INTEGER"),
        ("hooks", "created_at", "INTEGER"),
        ("hooks", "expired_at", "INTEGER"),

        ("scanner_runs", "configured", "INTEGER DEFAULT 1"),
    ]

    for table, column, definition in migrations:
        add_column_if_missing(
            conn,
            table,
            column,
            definition,
        )

    conn.commit()
    conn.close()


# ============================================================
# DB HELPERS
# ============================================================

def load_active_hooks():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM hooks
        WHERE status = 'ACTIVE'
        ORDER BY hook_time DESC
        """
    ).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def expire_hook(hook_id, reason=None):
    conn = db_connect()

    conn.execute(
        """
        UPDATE hooks
        SET status = 'EXPIRED',
            expired_at = ?
        WHERE id = ?
        """,
        (
            now_ts(),
            hook_id,
        ),
    )

    conn.commit()
    conn.close()

    inc_diag("m30_expired")


def save_hook(symbol, direction, structure, hook_time):

    conn = db_connect()

    # Preserve existing behavior:
    # one active hook per symbol.
    conn.execute(
        """
        UPDATE hooks
        SET status = 'EXPIRED',
            expired_at = ?
        WHERE symbol = ?
          AND status = 'ACTIVE'
        """,
        (
            now_ts(),
            symbol,
        ),
    )

    conn.execute(
        """
        INSERT INTO hooks (
            symbol,
            direction,
            status,
            h1,
            l1,
            h2,
            l2,
            h3,
            l3,
            hook_time,
            created_at
        )
        VALUES (?, ?, 'ACTIVE', ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            symbol,
            direction,
            structure.get("h1"),
            structure.get("l1"),
            structure.get("h2"),
            structure.get("l2"),
            structure.get("h3"),
            structure.get("l3"),
            hook_time,
            now_ts(),
        ),
    )

    conn.commit()
    conn.close()

    inc_diag(
        "m30_short_saved"
        if direction == "SHORT"
        else "m30_long_saved"
    )


def open_trade_count():
    conn = db_connect()

    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchone()

    conn.close()

    return int(row[0] or 0)


def recent_signal_for_symbol(symbol):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    conn.close()

    return dict(row) if row else None


def duplicate_open_signal(symbol, direction):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (
            symbol,
            direction,
        ),
    ).fetchone()

    conn.close()

    return row is not None


def signal_in_cooldown(symbol):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT created_at
        FROM signals
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,),
    ).fetchone()

    conn.close()

    if not row:
        return False

    created_at = row["created_at"] or 0

    return (
        now_ts() - int(created_at)
        < SIGNAL_COOLDOWN_SECONDS
    )


def insert_signal(
    symbol,
    direction,
    entry,
    sl,
    tp,
    current_price,
    hook_time,
    f_time,
    chart_path,
):
    global signals_since_last_report

    conn = db_connect()

    try:
        cur = conn.execute(
            """
            INSERT INTO signals (
                symbol,
                direction,
                entry,
                sl,
                tp,
                current_price,
                status,
                pnl_pct,
                opened_at,
                hook_time,
                f_time,
                chart_path,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, 'OPEN', 0, ?, ?, ?, ?, ?)
            """,
            (
                symbol,
                direction,
                entry,
                sl,
                tp,
                current_price,
                now_ts(),
                hook_time,
                f_time,
                chart_path,
                now_ts(),
            ),
        )

        conn.commit()

        signal_id = cur.lastrowid

        inc_diag("signals_created")

        signals_since_last_report += 1

        return signal_id

    except sqlite3.IntegrityError:
        inc_diag("duplicate_signal")
        return None

    finally:
        conn.close()


# ============================================================
# PIVOTS
# ============================================================

def pivot_highs(candles):
    pivots = []

    n = len(candles)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        value = candles[i]["high"]

        left = candles[
            i - PIVOT_LEFT:i
        ]

        right = candles[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        if all(
            value > x["high"]
            for x in left
        ) and all(
            value >= x["high"]
            for x in right
        ):
            pivots.append(
                {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": value,
                    "type": "HIGH",
                }
            )

    return pivots


def pivot_lows(candles):
    pivots = []

    n = len(candles)

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        value = candles[i]["low"]

        left = candles[
            i - PIVOT_LEFT:i
        ]

        right = candles[
            i + 1:i + 1 + PIVOT_RIGHT
        ]

        if all(
            value < x["low"]
            for x in left
        ) and all(
            value <= x["low"]
            for x in right
        ):
            pivots.append(
                {
                    "index": i,
                    "time": candles[i]["time"],
                    "price": value,
                    "type": "LOW",
                }
            )

    return pivots


# ============================================================
# M30 HOOK DETECTION
# ============================================================

def detect_short_hook(candles):
    """
    SHORT hook:

        H1 -> L1 -> H2 -> L2 -> H3

        H2 > H1
        L2 < L1
        H3 > H2
    """

    highs = pivot_highs(candles)
    lows = pivot_lows(candles)

    events = sorted(
        highs + lows,
        key=lambda x: x["index"],
    )

    if len(events) < 5:
        return None

    # Search newest qualifying structure first.
    for start in range(
        len(events) - 5,
        -1,
        -1,
    ):

        seq = events[
            start:start + 5
        ]

        if [x["type"] for x in seq] != [
            "HIGH",
            "LOW",
            "HIGH",
            "LOW",
            "HIGH",
        ]:
            continue

        h1, l1, h2, l2, h3 = seq

        if not (
            h2["price"] > h1["price"]
        ):
            continue

        if not (
            l2["price"] < l1["price"]
        ):
            continue

        if not (
            h3["price"] > h2["price"]
        ):
            continue

        if h1["price"] <= 0:
            continue

        hook_range = (
            h3["price"] - l2["price"]
        ) / h1["price"]

        if hook_range < M30_MIN_HOOK_RANGE:
            continue

        return {
            "direction": "SHORT",
            "h1": h1["price"],
            "l1": l1["price"],
            "h2": h2["price"],
            "l2": l2["price"],
            "h3": h3["price"],
            "hook_time": h3["time"],
        }

    return None


def detect_long_hook(candles):
    """
    LONG hook:

        L1 -> H1 -> L2 -> H2 -> L3

        L2 < L1
        H2 > H1
        L3 < L2
    """

    highs = pivot_highs(candles)
    lows = pivot_lows(candles)

    events = sorted(
        highs + lows,
        key=lambda x: x["index"],
    )

    if len(events) < 5:
        return None

    for start in range(
        len(events) - 5,
        -1,
        -1,
    ):

        seq = events[
            start:start + 5
        ]

        if [x["type"] for x in seq] != [
            "LOW",
            "HIGH",
            "LOW",
            "HIGH",
            "LOW",
        ]:
            continue

        l1, h1, l2, h2, l3 = seq

        if not (
            l2["price"] < l1["price"]
        ):
            continue

        if not (
            h2["price"] > h1["price"]
        ):
            continue

        if not (
            l3["price"] < l2["price"]
        ):
            continue

        if l1["price"] <= 0:
            continue

        hook_range = (
            h2["price"] - l3["price"]
        ) / l1["price"]

        if hook_range < M30_MIN_HOOK_RANGE:
            continue

        return {
            "direction": "LONG",
            "l1": l1["price"],
            "h1": h1["price"],
            "l2": l2["price"],
            "h2": h2["price"],
            "l3": l3["price"],
            "hook_time": l3["time"],
        }

    return None


# ============================================================
# HOOK VALIDATION
# ============================================================

def hook_is_invalidated(
    hook,
    candles,
):
    """
    SHORT:
        New confirmed LOW after hook invalidates.

    LONG:
        New confirmed HIGH after hook invalidates.
    """

    highs = pivot_highs(candles)
    lows = pivot_lows(candles)

    hook_time = hook["hook_time"]

    if hook["direction"] == "SHORT":

        for p in lows:

            if p["time"] > hook_time:
                inc_diag("hook_invalidated")
                return True

    else:

        for p in highs:

            if p["time"] > hook_time:
                inc_diag("hook_invalidated")
                return True

    return False


# ============================================================
# M1 123F
# ============================================================

def structure_distance(points):
    prices = [
        p["price"]
        for p in points
    ]

    low = min(prices)
    high = max(prices)

    if low <= 0:
        return 999

    return (
        high - low
    ) / low


def detect_m1_short_123f(
    candles,
    hook,
):
    """
    SHORT:

        HIGH 1 < HIGH 2 < HIGH 3 < HIGH F

    All four points are pivot HIGHS.
    """

    highs = pivot_highs(candles)

    hook_time = hook["hook_time"]

    highs = [
        x for x in highs
        if x["time"] > hook_time
    ]

    if len(highs) < 4:
        return None

    # Newest F first.
    for end in range(
        len(highs) - 1,
        2,
        -1,
    ):

        p1 = highs[end - 3]
        p2 = highs[end - 2]
        p3 = highs[end - 1]
        pf = highs[end]

        points = [
            p1,
            p2,
            p3,
            pf,
        ]

        # Exact 123F structure.
        if not (
            p1["price"]
            < p2["price"]
            < p3["price"]
            < pf["price"]
        ):
            continue

        inc_diag("m1_123f_candidates")

        # F freshness.
        if (
            now_ts() - pf["time"]
            > M1_F_MAX_AGE_SECONDS
        ):
            inc_diag("m1_f_too_old")
            continue

        distance = structure_distance(
            points
        )

        if distance > M1_MAX_STRUCTURE_DISTANCE:
            inc_diag("m1_distance_rejected")
            continue

        swing = (
            pf["price"] - p1["price"]
        ) / p1["price"]

        if swing < M1_MIN_SWING:
            inc_diag("m1_swing_rejected")
            continue

        return {
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "f": pf,
        }

    return None


def detect_m1_long_123f(
    candles,
    hook,
):
    """
    LONG:

        HIGH 1 > HIGH 2 > HIGH 3 > HIGH F

    All four points are pivot HIGHS.
    """

    highs = pivot_highs(candles)

    hook_time = hook["hook_time"]

    highs = [
        x for x in highs
        if x["time"] > hook_time
    ]

    if len(highs) < 4:
        return None

    for end in range(
        len(highs) - 1,
        2,
        -1,
    ):

        p1 = highs[end - 3]
        p2 = highs[end - 2]
        p3 = highs[end - 1]
        pf = highs[end]

        points = [
            p1,
            p2,
            p3,
            pf,
        ]

        if not (
            p1["price"]
            > p2["price"]
            > p3["price"]
            > pf["price"]
        ):
            continue

        inc_diag("m1_123f_candidates")

        if (
            now_ts() - pf["time"]
            > M1_F_MAX_AGE_SECONDS
        ):
            inc_diag("m1_f_too_old")
            continue

        distance = structure_distance(
            points
        )

        if distance > M1_MAX_STRUCTURE_DISTANCE:
            inc_diag("m1_distance_rejected")
            continue

        swing = (
            p1["price"] - pf["price"]
        ) / p1["price"]

        if swing < M1_MIN_SWING:
            inc_diag("m1_swing_rejected")
            continue

        return {
            "p1": p1,
            "p2": p2,
            "p3": p3,
            "f": pf,
        }

    return None


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    direction,
    candles,
    hook,
    current_price,
):
    if (
        current_price is None
        or current_price <= 0
    ):
        return None

    highs = pivot_highs(candles)
    lows = pivot_lows(candles)

    hook_time = hook["hook_time"]

    if direction == "SHORT":

        tp = safe_float(
            hook.get("l2")
        )

        if tp is None:
            tp = safe_float(
                hook.get("l1")
            )

        valid_highs = [
            x["price"]
            for x in highs
            if x["time"] <= hook_time
        ]

        if not valid_highs:
            return None

        sl_base = max(valid_highs)

        sl = (
            sl_base
            * (1 + SL_BUFFER)
        )

        entry = current_price

        if not (
            tp < entry < sl
        ):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }

    else:

        tp = safe_float(
            hook.get("h2")
        )

        if tp is None:
            tp = safe_float(
                hook.get("h1")
            )

        valid_lows = [
            x["price"]
            for x in lows
            if x["time"] <= hook_time
        ]

        if not valid_lows:
            return None

        sl_base = min(valid_lows)

        sl = (
            sl_base
            * (1 - SL_BUFFER)
        )

        entry = current_price

        if not (
            sl < entry < tp
        ):
            return None

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
        }


# ============================================================
# CHART
# ============================================================

def make_chart(
    symbol,
    direction,
    m1_candles,
    hook,
    setup,
    entry,
    sl,
    tp,
):
    if not CHARTS_ENABLED:
        return ""

    if not CHARTS_AVAILABLE:
        return ""

    try:
        os.makedirs(
            CHART_DIR,
            exist_ok=True,
        )

        candles = m1_candles[-250:]

        if not candles:
            return ""

        times = [
            datetime.fromtimestamp(
                x["time"],
                tz=timezone.utc,
            )
            for x in candles
        ]

        fig, ax = plt.subplots(
            figsize=(14, 7)
        )

        # ----------------------------------------------------
        # Candles
        # ----------------------------------------------------

        for i, candle in enumerate(candles):

            o = candle["open"]
            h = candle["high"]
            l = candle["low"]
            c = candle["close"]

            if c >= o:
                face = "white"
            else:
                face = "black"

            ax.vlines(
                i,
                l,
                h,
                linewidth=0.8,
            )

            body_low = min(o, c)
            body_high = max(o, c)

            ax.add_patch(
                plt.Rectangle(
                    (
                        i - 0.30,
                        body_low,
                    ),
                    0.60,
                    max(
                        body_high - body_low,
                        1e-12,
                    ),
                    fill=True,
                    facecolor=face,
                    edgecolor="black",
                    linewidth=0.8,
                )
            )

        # ----------------------------------------------------
        # 123F
        # ----------------------------------------------------

        labels = [
            ("1", setup["p1"]),
            ("2", setup["p2"]),
            ("3", setup["p3"]),
            ("F", setup["f"]),
        ]

        candle_index = {
            c["time"]: i
            for i, c in enumerate(candles)
        }

        for label, point in labels:

            idx = candle_index.get(
                point["time"]
            )

            if idx is None:
                continue

            ax.scatter(
                idx,
                point["price"],
                s=50,
            )

            ax.annotate(
                label,
                (
                    idx,
                    point["price"],
                ),
                xytext=(0, 10),
                textcoords="offset points",
                ha="center",
                fontsize=10,
                fontweight="bold",
            )

        # ----------------------------------------------------
        # Entry / SL / TP
        # ----------------------------------------------------

        ax.axhline(
            entry,
            linestyle="--",
            linewidth=1,
            label=f"Entry {entry:.8g}",
        )

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1,
            label=f"SL {sl:.8g}",
        )

        ax.axhline(
            tp,
            linestyle="--",
            linewidth=1,
            label=f"TP {tp:.8g}",
        )

        ax.set_title(
            f"NDS {symbol} | "
            f"{direction} | "
            f"123F"
        )

        ax.set_ylabel("Price")
        ax.set_xlabel("M1 candles")

        ax.grid(
            alpha=0.20
        )

        ax.legend()

        filename = (
            f"{symbol.replace('/', '_')}_"
            f"{direction}_"
            f"{now_ts()}.png"
        )

        path = os.path.join(
            CHART_DIR,
            filename,
        )

        fig.tight_layout()
        fig.savefig(
            path,
            dpi=130,
        )

        plt.close(fig)

        return path

    except Exception as exc:
        print(
            f"Chart error {symbol}: {exc}"
        )

        return ""


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def send_signal_message(
    symbol,
    direction,
    entry,
    sl,
    tp,
    current_price,
    hook,
    setup,
):
    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    side = (
        "SHORT"
        if direction == "SHORT"
        else "LONG"
    )

    msg = (
        f"<b>NDS {emoji} {side}</b>\n\n"
        f"<b>{html_escape(symbol)}</b>\n"
        f"Entry: <code>{entry:.8g}</code>\n"
        f"SL: <code>{sl:.8g}</code>\n"
        f"TP: <code>{tp:.8g}</code>\n"
        f"Current: <b>{current_price:.8g}</b>\n\n"
        f"30M Hook: <b>{side}</b>\n"
        f"1M Structure: <b>123F</b>\n"
        f"F: <code>{setup['f']['price']:.8g}</code>\n\n"
        f"<i>PAPER ONLY</i>"
    )

    send_telegram(msg)


# ============================================================
# M30 SCANNER
# ============================================================

def scan_m30(assets):
    print(
        f"[M30] scanning "
        f"{len(assets)} assets..."
    )

    for symbol in assets:

        inc_diag("m30_requests")

        try:
            candles = fetch_ohlcv(
                symbol,
                "30m",
                M30_FETCH_LIMIT,
            )

            if not candles:
                inc_diag("m30_data_empty")
                continue

            # Latest candle may still be open.
            closed = candles[:-1]

            count = len(closed)

            inc_diag("m30_data_ok")
            inc_diag(
                "m30_total_candles",
                count,
            )

            if diagnostics["m30_min_candles"] == 0:
                diagnostics["m30_min_candles"] = count
            else:
                diagnostics["m30_min_candles"] = min(
                    diagnostics["m30_min_candles"],
                    count,
                )

            diagnostics["m30_max_candles"] = max(
                diagnostics["m30_max_candles"],
                count,
            )

            # ------------------------------------------------
            # IMPORTANT v4.3.4
            #
            # We no longer require exactly M30_LOOKBACK
            # closed candles.
            #
            # We only require a sensible minimum.
            # ------------------------------------------------

            if count < M30_MIN_CLOSED:
                inc_diag("m30_data_short")
                continue

            work = closed[-M30_LOOKBACK:]

            highs = pivot_highs(work)
            lows = pivot_lows(work)

            inc_diag(
                "m30_pivots_high",
                len(highs),
            )

            inc_diag(
                "m30_pivots_low",
                len(lows),
            )

            # ------------------------------------------------
            # SHORT HOOK
            # ------------------------------------------------

            short_hook = detect_short_hook(
                work
            )

            if short_hook:

                age = (
                    now_ts()
                    - short_hook["hook_time"]
                )

                if age <= MAX_HOOK_AGE_SECONDS:

                    inc_diag(
                        "m30_short_candidates"
                    )

                    save_hook(
                        symbol,
                        "SHORT",
                        short_hook,
                        short_hook["hook_time"],
                    )

                else:
                    inc_diag("m30_expired")

                    # Preserve existing behavior.
                    continue

            # ------------------------------------------------
            # LONG HOOK
            # ------------------------------------------------

            long_hook = detect_long_hook(
                work
            )

            if long_hook:

                age = (
                    now_ts()
                    - long_hook["hook_time"]
                )

                if age <= MAX_HOOK_AGE_SECONDS:

                    inc_diag(
                        "m30_long_candidates"
                    )

                    save_hook(
                        symbol,
                        "LONG",
                        long_hook,
                        long_hook["hook_time"],
                    )

                else:
                    inc_diag("m30_expired")

        except Exception as exc:

            inc_diag("errors")

            print(
                f"[M30 ERROR] "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()


# ============================================================
# M1 SCANNER
# ============================================================

def scan_m1(assets):
    """
    Scan active M30 hooks and look for M1 123F.
    """

    hooks = load_active_hooks()

    if not hooks:
        return

    inc_diag(
        "m1_hook_scans",
        len(hooks),
    )

    print(
        f"[M1] active hooks: "
        f"{len(hooks)}"
    )

    for hook in hooks:

        symbol = hook["symbol"]

        inc_diag("m1_requests")

        try:
            candles = fetch_ohlcv(
                symbol,
                "1m",
                M1_FETCH_LIMIT,
            )

            if not candles:
                inc_diag("m1_data_empty")
                continue

            closed = candles[:-1]

            count = len(closed)

            inc_diag("m1_data_ok")
            inc_diag(
                "m1_total_candles",
                count,
            )

            if diagnostics["m1_min_candles"] == 0:
                diagnostics["m1_min_candles"] = count
            else:
                diagnostics["m1_min_candles"] = min(
                    diagnostics["m1_min_candles"],
                    count,
                )

            diagnostics["m1_max_candles"] = max(
                diagnostics["m1_max_candles"],
                count,
            )

            if count < M1_MIN_CLOSED:
                inc_diag("m1_data_short")
                continue

            work = closed[-M1_LOOKBACK:]

            highs = pivot_highs(work)
            lows = pivot_lows(work)

            inc_diag(
                "m1_pivots_high",
                len(highs),
            )

            inc_diag(
                "m1_pivots_low",
                len(lows),
            )

            # ------------------------------------------------
            # Hook invalidation
            # ------------------------------------------------

            if hook_is_invalidated(
                hook,
                work,
            ):
                continue

            direction = hook["direction"]

            # ------------------------------------------------
            # 123F
            # ------------------------------------------------

            if direction == "SHORT":

                setup = detect_m1_short_123f(
                    work,
                    hook,
                )

            else:

                setup = detect_m1_long_123f(
                    work,
                    hook,
                )

            if not setup:
                inc_diag("m1_no_structure")
                continue

            # ------------------------------------------------
            # Max open trades
            # ------------------------------------------------

            if (
                open_trade_count()
                >= MAX_OPEN_TRADES
            ):
                inc_diag("max_open_trades")
                continue

            # ------------------------------------------------
            # Cooldown
            # ------------------------------------------------

            if signal_in_cooldown(
                symbol
            ):
                inc_diag("cooldown")
                continue

            # ------------------------------------------------
            # Duplicate
            # ------------------------------------------------

            if duplicate_open_signal(
                symbol,
                direction,
            ):
                inc_diag("duplicate_signal")
                continue

            # ------------------------------------------------
            # Current price
            # ------------------------------------------------

            current_price = (
                fetch_current_price(symbol)
            )

            if (
                current_price is None
                or current_price <= 0
            ):
                inc_diag("level_error")
                continue

            # ------------------------------------------------
            # Levels
            # ------------------------------------------------

            levels = calculate_trade_levels(
                direction,
                work,
                hook,
                current_price,
            )

            if not levels:
                inc_diag("level_error")
                continue

            entry = levels["entry"]
            sl = levels["sl"]
            tp = levels["tp"]

            # ------------------------------------------------
            # Chart
            # ------------------------------------------------

            chart_path = make_chart(
                symbol,
                direction,
                work,
                hook,
                setup,
                entry,
                sl,
                tp,
            )

            # ------------------------------------------------
            # DB signal
            # ------------------------------------------------

            signal_id = insert_signal(
                symbol=symbol,
                direction=direction,
                entry=entry,
                sl=sl,
                tp=tp,
                current_price=current_price,
                hook_time=hook["hook_time"],
                f_time=setup["f"]["time"],
                chart_path=chart_path,
            )

            if not signal_id:
                continue

            # ------------------------------------------------
            # Telegram
            # ------------------------------------------------

            send_signal_message(
                symbol,
                direction,
                entry,
                sl,
                tp,
                current_price,
                hook,
                setup,
            )

            print(
                f"[SIGNAL] "
                f"{symbol} "
                f"{direction} "
                f"entry={entry}"
            )

        except Exception as exc:

            inc_diag("errors")

            print(
                f"[M1 ERROR] "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def calculate_pnl(
    direction,
    entry,
    current,
):
    if entry in (None, 0):
        return 0.0

    if direction == "LONG":
        return (
            (current - entry)
            / entry
        ) * 100.0

    return (
        (entry - current)
        / entry
    ) * 100.0


def manage_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    for row in rows:

        signal = dict(row)

        symbol = signal["symbol"]

        try:
            current = fetch_current_price(
                symbol
            )

            if (
                current is None
                or current <= 0
            ):
                continue

            direction = signal["direction"]

            pnl = calculate_pnl(
                direction,
                signal["entry"],
                current,
            )

            conn.execute(
                """
                UPDATE signals
                SET current_price = ?,
                    pnl_pct = ?
                WHERE id = ?
                """,
                (
                    current,
                    pnl,
                    signal["id"],
                ),
            )

            reason = None

            if direction == "LONG":

                if current <= signal["sl"]:
                    reason = "SL"

                elif current >= signal["tp"]:
                    reason = "TP"

            else:

                if current >= signal["sl"]:
                    reason = "SL"

                elif current <= signal["tp"]:
                    reason = "TP"

            if reason:

                conn.execute(
                    """
                    UPDATE signals
                    SET status = 'CLOSED',
                        closed_at = ?,
                        close_price = ?,
                        exit_reason = ?,
                        pnl_pct = ?
                    WHERE id = ?
                    """,
                    (
                        now_ts(),
                        current,
                        reason,
                        pnl,
                        signal["id"],
                    ),
                )

                emoji = (
                    "🟢"
                    if pnl >= 0
                    else "🔴"
                )

                message = (
                    f"<b>NDS TRADE CLOSED</b>\n\n"
                    f"<b>{html_escape(symbol)}</b>\n"
                    f"{direction}\n\n"
                    f"Exit: <code>{current:.8g}</code>\n"
                    f"Reason: <b>{reason}</b>\n"
                    f"P/L: {emoji} "
                    f"<b>{pnl:+.2f}%</b>"
                )

                send_telegram(message)

        except Exception as exc:

            inc_diag("errors")

            print(
                f"Trade management error "
                f"{symbol}: {exc}"
            )

    conn.commit()
    conn.close()


# ============================================================
# REPORT DATA
# ============================================================

def report_stats():
    conn = db_connect()

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
        """
    ).fetchone()[0]

    closed_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
          AND pnl_pct > 0
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'CLOSED'
          AND pnl_pct <= 0
        """
    ).fetchone()[0]

    pnl = conn.execute(
        """
        SELECT COALESCE(SUM(pnl_pct), 0)
        FROM signals
        WHERE status = 'CLOSED'
        """
    ).fetchone()[0]

    conn.close()

    return {
        "open": int(open_count or 0),
        "closed": int(closed_count or 0),
        "wins": int(wins or 0),
        "losses": int(losses or 0),
        "pnl": float(pnl or 0),
    }


# ============================================================
# NORMAL REPORT
# ============================================================

def send_report(assets_scanned):

    global signals_since_last_report

    hooks = load_active_hooks()
    stats = report_stats()

    new_long = 0
    new_short = 0

    conn = db_connect()

    since = (
        now_ts() - REPORT_SECONDS
    )

    rows = conn.execute(
        """
        SELECT direction, COUNT(*) AS c
        FROM signals
        WHERE created_at >= ?
        GROUP BY direction
        """,
        (since,),
    ).fetchall()

    conn.close()

    for row in rows:

        if row["direction"] == "LONG":
            new_long = row["c"]

        elif row["direction"] == "SHORT":
            new_short = row["c"]

    message = (
        f"<b>📊 NDS M30 → M1 REPORT</b>\n"
        f"Version: <b>{VERSION}</b>\n\n"

        f"Assets scanned: <b>"
        f"{assets_scanned}</b>\n"

        f"🟢 New LONG: <b>"
        f"{new_long}</b>\n"

        f"🔴 New SHORT: <b>"
        f"{new_short}</b>\n\n"

        f"Active Hooks: <b>"
        f"{len(hooks)}</b>\n"

        f"OPEN TRADES: <b>"
        f"{stats['open']}</b>\n"

        f"CLOSED TRADES: <b>"
        f"{stats['closed']}</b>\n"

        f"Wins: <b>{stats['wins']}</b> | "
        f"Losses: <b>{stats['losses']}</b>\n"

        f"Total P/L: <b>"
        f"{stats['pnl']:+.2f}%</b>"
    )

    send_telegram(message)


# ============================================================
# DIAGNOSTIC REPORT
# ============================================================

def send_diagnostic_report(
    assets_scanned
):
    hooks = load_active_hooks()

    stats = report_stats()

    d = diagnostics

    m30_min = (
        d["m30_min_candles"]
        if d["m30_min_candles"] > 0
        else 0
    )

    m30_max = d["m30_max_candles"]

    m30_avg = (
        d["m30_total_candles"]
        / d["m30_data_ok"]
        if d["m30_data_ok"] > 0
        else 0
    )

    m1_min = (
        d["m1_min_candles"]
        if d["m1_min_candles"] > 0
        else 0
    )

    m1_max = d["m1_max_candles"]

    m1_avg = (
        d["m1_total_candles"]
        / d["m1_data_ok"]
        if d["m1_data_ok"] > 0
        else 0
    )

    message = (
        f"<b>🔎 NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_text()}\n\n"

        f"<b>ASSETS</b>\n"
        f"Scanned: <b>{assets_scanned}</b>\n\n"

        f"<b>30M DATA</b>\n"
        f"Requests: {d['m30_requests']}\n"
        f"Data OK: {d['m30_data_ok']}\n"
        f"Empty: {d['m30_data_empty']}\n"
        f"Short data: {d['m30_data_short']}\n"
        f"Candles min/max/avg: "
        f"{m30_min}/"
        f"{m30_max}/"
        f"{m30_avg:.1f}\n"
        f"Pivot Highs: {d['m30_pivots_high']}\n"
        f"Pivot Lows: {d['m30_pivots_low']}\n\n"

        f"<b>30M HOOK DETECTION</b>\n"
        f"SHORT candidates: "
        f"<b>{d['m30_short_candidates']}</b>\n"
        f"LONG candidates: "
        f"<b>{d['m30_long_candidates']}</b>\n"
        f"SHORT saved: "
        f"{d['m30_short_saved']}\n"
        f"LONG saved: "
        f"{d['m30_long_saved']}\n"
        f"Expired: "
        f"{d['m30_expired']}\n\n"

        f"<b>ACTIVE HOOKS</b>\n"
        f"Current: <b>{len(hooks)}</b>\n\n"

        f"<b>1M DATA</b>\n"
        f"Hook scans: {d['m1_hook_scans']}\n"
        f"Requests: {d['m1_requests']}\n"
        f"Data OK: {d['m1_data_ok']}\n"
        f"Empty: {d['m1_data_empty']}\n"
        f"Short data: {d['m1_data_short']}\n"
        f"Candles min/max/avg: "
        f"{m1_min}/"
        f"{m1_max}/"
        f"{m1_avg:.1f}\n"
        f"Pivot Highs: {d['m1_pivots_high']}\n"
        f"Pivot Lows: {d['m1_pivots_low']}\n\n"

        f"<b>123F FILTERS</b>\n"
        f"123F candidates: "
        f"{d['m1_123f_candidates']}\n"
        f"F too old: "
        f"{d['m1_f_too_old']}\n"
        f"Structure distance rejected: "
        f"{d['m1_distance_rejected']}\n"
        f"Swing rejected: "
        f"{d['m1_swing_rejected']}\n"
        f"No 123F: "
        f"{d['m1_no_structure']}\n\n"

        f"<b>ENTRY FILTERS</b>\n"
        f"Hook invalidated: "
        f"{d['hook_invalidated']}\n"
        f"Max open trades: "
        f"{d['max_open_trades']}\n"
        f"Cooldown: "
        f"{d['cooldown']}\n"
        f"Level error: "
        f"{d['level_error']}\n"
        f"Duplicate signal: "
        f"{d['duplicate_signal']}\n\n"

        f"<b>RESULT</b>\n"
        f"Signals created: "
        f"<b>{d['signals_created']}</b>\n"
        f"Current OPEN trades: "
        f"<b>{stats['open']}</b>\n"
        f"Errors: "
        f"{d['errors']}"
    )

    send_telegram(message)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        f"NDS M30 -> M1 LIVE SCANNER "
        f"v{VERSION}"
    )
    print("=" * 70)

    if not PAPER_ONLY:
        raise RuntimeError(
            "PAPER_ONLY must remain True."
        )

    init_db()

    os.makedirs(
        CHART_DIR,
        exist_ok=True,
    )

    assets = fetch_assets()

    if not assets:
        print(
            "ERROR: No Kraken PF_*USD assets found."
        )

        send_telegram(
            f"<b>🚨 NDS SCANNER ERROR</b>\n"
            f"Version: {VERSION}\n"
            f"No PF_*USD assets found."
        )

        return

    print(
        f"Assets loaded: {len(assets)}"
    )

    # --------------------------------------------------------
    # Reset diagnostic period before first scan.
    # --------------------------------------------------------

    reset_diagnostics()

    start_time = now_ts()

    last_m30_scan = 0
    last_m1_scan = 0
    last_report = 0

    # --------------------------------------------------------
    # Immediate first scans
    # --------------------------------------------------------

    scan_m30(assets)

    scan_m1(assets)

    manage_open_trades()

    last_m30_scan = now_ts()
    last_m1_scan = now_ts()
    last_report = now_ts()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    while (
        now_ts() - start_time
        < RUNTIME_SECONDS
    ):

        current = now_ts()

        try:

            # -----------------------------------------------
            # M30
            # -----------------------------------------------

            if (
                current - last_m30_scan
                >= M30_SCAN_SECONDS
            ):

                scan_m30(assets)

                last_m30_scan = current

            # -----------------------------------------------
            # M1
            # -----------------------------------------------

            if (
                current - last_m1_scan
                >= M1_SCAN_SECONDS
            ):

                scan_m1(assets)

                last_m1_scan = current

            # -----------------------------------------------
            # Trade management
            # -----------------------------------------------

            manage_open_trades()

            # -----------------------------------------------
            # Report
            # -----------------------------------------------

            if (
                current - last_report
                >= REPORT_SECONDS
            ):

                send_report(
                    len(assets)
                )

                send_diagnostic_report(
                    len(assets)
                )

                reset_diagnostics()

                last_report = current

            time.sleep(5)

        except KeyboardInterrupt:
            break

        except Exception as exc:

            inc_diag("errors")

            print(
                f"MAIN LOOP ERROR: {exc}"
            )

            traceback.print_exc()

            time.sleep(5)

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    manage_open_trades()

    send_report(
        len(assets)
    )

    send_diagnostic_report(
        len(assets)
    )

    print("=" * 70)
    print(
        f"NDS SCANNER v{VERSION} "
        f"FINISHED"
    )
    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
