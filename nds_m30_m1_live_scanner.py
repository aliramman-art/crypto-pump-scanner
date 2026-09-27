# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 3.0.0
# ============================================================
#
# PAPER ONLY
#
# NDS STRUCTURE
#
# M30:
#
# POSITIVE HOOK:
#   H1 -> L1 -> H2 -> L2 -> H3(33)
#                                      |
#                                      | ENTRY WINDOW OPENS
#                                      v
#                                  M1 123F
#                                      |
#                                      v
#                                    SHORT
#
# WINDOW ENDS:
#   when next L3 is confirmed on M30
#
# NEGATIVE HOOK:
#   L1 -> H1 -> L2 -> H2 -> L3(33)
#                                      |
#                                      | ENTRY WINDOW OPENS
#                                      v
#                                  M1 123F
#                                      |
#                                      v
#                                    LONG
#
# WINDOW ENDS:
#   when next H3 is confirmed on M30
#
# M1 POSITIVE 123F:
#   L1 -> H2 -> L3
#   L3 > L1
#   F = CLOSED CANDLE ABOVE H2
#   => SHORT
#
# M1 NEGATIVE 123F:
#   H1 -> L2 -> H3
#   H3 < H1
#   F = CLOSED CANDLE BELOW L2
#   => LONG
#
# ============================================================

import os
import time
import json
import math
import sqlite3
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.dates as mdates


# ============================================================
# CONFIG
# ============================================================

VERSION = "3.0.0"

PAPER_ONLY = True

KRAKEN_CHART_BASE = "https://futures.kraken.com/api/charts/v1"
KRAKEN_REST_BASE = "https://futures.kraken.com/derivatives/api/v3"

DB_FILE = "nds_m30_m1_v30.db"
CHART_DIR = "nds_charts"

# ------------------------------------------------------------
# ASSET CONFIG
# ------------------------------------------------------------

TARGET_ASSETS = 100

# We dynamically discover Kraken Futures contracts.
# No hard-coded 40-symbol list.

ONLY_LINEAR_PERPETUALS = True

# ------------------------------------------------------------
# TIMEFRAMES
# ------------------------------------------------------------

M30_INTERVAL = "30m"
M1_INTERVAL = "1m"

M30_CANDLES = 320
M1_CANDLES = 1500

# M30 structure scan every 5 minutes.
M1 entry scan can run every minute for active hooks.
M30_SCAN_INTERVAL_SECONDS = 300
SCAN_INTERVAL_SECONDS = 60

# ------------------------------------------------------------
# PIVOTS
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# A pivot is confirmed only after PIVOT_RIGHT candles.
#
# M30:
#   right=2 => approximately 60 minutes confirmation delay
#
# M1:
#   right=2 => approximately 2 minutes confirmation delay

# ------------------------------------------------------------
# M30 HOOK
# ------------------------------------------------------------

HOOK_MIN_RANGE_PCT = 0.20
HOOK_TOLERANCE_PCT = 2.0

# ------------------------------------------------------------
# M1 123F
# ------------------------------------------------------------

M1_MIN_SWING_PCT = 0.07

# F candle must break the pivot by this percentage.
F_BREAK_BUFFER_PCT = 0.02

# Maximum distance from 123 structure.
M1_MAX_STRUCTURE_DISTANCE_PCT = 3.0

# ------------------------------------------------------------
# SL
# ------------------------------------------------------------

SL_BUFFER_PCT = 0.15

# ------------------------------------------------------------
# TRADES
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_MINUTES = 60

# ------------------------------------------------------------
# TP
# ------------------------------------------------------------

TP1_R = 1.0
TP2_R = 2.0
TP3_R = 3.0

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 15

MAX_WORKERS = 8

USER_AGENT = f"NDS-M30-M1-Scanner/{VERSION}"

# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

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
# DEBUG
# ------------------------------------------------------------

DEBUG = False


# ============================================================
# GLOBALS
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
})

LAST_M30_SCAN = 0

ACTIVE_ASSETS = []

SEEN_HOOKS = {}

# Prevent repeated F signals permanently.
PROCESSED_F_KEYS = set()


# ============================================================
# LOGGING
# ============================================================

def log(msg):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"[{now}] {msg}", flush=True)


def debug(msg):
    if DEBUG:
        log(f"DEBUG: {msg}")


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def ts_now():
    return int(time.time())


def ms_to_datetime(ms):
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


def iso_time(ms):
    return ms_to_datetime(ms).strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None, retries=3):
    last_error = None

    for attempt in range(retries):
        try:
            r = SESSION.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT,
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:
            last_error = e

            if attempt < retries - 1:
                time.sleep(1.0 * (attempt + 1))

    raise RuntimeError(
        f"HTTP failed: {url} | {last_error}"
    )


# ============================================================
# DATABASE
# ============================================================

def db():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30,
        check_same_thread=False,
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():
    conn = db()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            entry REAL NOT NULL,
            sl REAL NOT NULL,

            tp1 REAL NOT NULL,
            tp2 REAL NOT NULL,
            tp3 REAL NOT NULL,

            final_tp REAL,

            risk REAL NOT NULL,

            hook_direction TEXT NOT NULL,

            m30_h1_time INTEGER,
            m30_l1_time INTEGER,
            m30_h2_time INTEGER,
            m30_l2_time INTEGER,
            m30_h3_time INTEGER,

            m30_trigger_time INTEGER,

            m1_p1_time INTEGER,
            m1_p2_time INTEGER,
            m1_p3_time INTEGER,

            m1_f_time INTEGER,

            status TEXT DEFAULT 'OPEN',

            result TEXT,

            pnl_pct REAL,

            opened_at INTEGER NOT NULL,
            closed_at INTEGER,

            close_price REAL,
            close_reason TEXT,

            chart_path TEXT,

            UNIQUE(symbol, side, m1_f_time)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time INTEGER,
            assets_scanned INTEGER,
            active_hooks INTEGER,
            new_signals INTEGER,
            open_trades INTEGER,
            errors INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS processed_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE,
            created_at INTEGER
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# DB HELPERS
# ============================================================

def get_open_trades():
    conn = db()

    rows = conn.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY opened_at ASC
    """).fetchall()

    conn.close()

    return rows


def count_open_trades():
    conn = db()

    n = conn.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """).fetchone()[0]

    conn.close()

    return int(n)


def already_processed(event_key):
    conn = db()

    row = conn.execute("""
        SELECT 1
        FROM processed_events
        WHERE event_key = ?
        LIMIT 1
    """, (event_key,)).fetchone()

    conn.close()

    return row is not None


def mark_processed(event_key):
    conn = db()

    conn.execute("""
        INSERT OR IGNORE INTO processed_events
        (event_key, created_at)
        VALUES (?, ?)
    """, (
        event_key,
        ts_now(),
    ))

    conn.commit()
    conn.close()


# ============================================================
# KRAKEN INSTRUMENT DISCOVERY
# ============================================================

NON_CRYPTO_PREFIXES = (
    "PF_XAU",
    "PF_XAG",
    "PF_XCU",
    "PF_WTI",
    "PF_US",
    "PF_NQ",
    "PF_SP",
    "PF_DJ",
    "PF_NIK",
    "PF_AAPL",
    "PF_AMZN",
    "PF_GOOG",
    "PF_META",
    "PF_MSFT",
    "PF_NVDA",
    "PF_TSLA",
    "PF_QQQ",
)


def is_probably_crypto(instrument):
    symbol = str(
        instrument.get("symbol", "")
    ).upper()

    if not symbol.startswith("PF_"):
        return False

    if not symbol.endswith("USD"):
        return False

    if symbol.startswith(NON_CRYPTO_PREFIXES):
        return False

    category = str(
        instrument.get("category", "")
    ).lower()

    tags = instrument.get("tags", [])

    if isinstance(tags, list):
        tags_text = " ".join(
            str(x).lower() for x in tags
        )
    else:
        tags_text = str(tags).lower()

    # If Kraken explicitly identifies it as non-crypto,
    # reject it.
    if any(x in category for x in (
        "equity",
        "commodity",
        "index",
        "forex",
        "fx",
        "metal",
    )):
        return False

    if any(x in tags_text for x in (
        "equity",
        "commodity",
        "index",
        "forex",
        "metal",
    )):
        return False

    return True


def fetch_instruments():
    data = http_get(
        f"{KRAKEN_REST_BASE}/instruments"
    )

    instruments = data.get(
        "instruments",
        []
    )

    result = []

    for item in instruments:

        symbol = str(
            item.get("symbol", "")
        ).upper()

        tradeable = item.get(
            "tradeable",
            True
        )

        if not tradeable:
            continue

        if not is_probably_crypto(item):
            continue

        result.append(item)

    return result


# ============================================================
# TICKER / VOLUME
# ============================================================

def fetch_all_tickers():
    try:
        data = http_get(
            f"{KRAKEN_REST_BASE}/tickers"
        )

        tickers = data.get(
            "tickers",
            []
        )

        return tickers

    except Exception as e:
        log(f"Ticker fetch error: {e}")
        return []


def ticker_volume(item):
    possible = (
        "vol24h",
        "volume24h",
        "volume",
        "vol",
        "volume24Hour",
    )

    for key in possible:
        value = item.get(key)

        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    return 0.0


def choose_top_assets(instruments, tickers):
    ticker_map = {}

    for t in tickers:

        symbol = str(
            t.get("symbol", "")
        ).upper()

        if symbol:
            ticker_map[symbol] = t

    ranked = []

    for instrument in instruments:

        symbol = str(
            instrument.get("symbol", "")
        ).upper()

        ticker = ticker_map.get(
            symbol,
            {}
        )

        volume = ticker_volume(ticker)

        ranked.append(
            (
                volume,
                symbol,
            )
        )

    ranked.sort(
        key=lambda x: x[0],
        reverse=True
    )

    selected = [
        symbol
        for _, symbol in ranked[:TARGET_ASSETS]
    ]

    # Fallback if ticker volume was unavailable.
    if len(selected) < TARGET_ASSETS:
        symbols = sorted(
            str(x.get("symbol", "")).upper()
            for x in instruments
        )

        for symbol in symbols:
            if symbol not in selected:
                selected.append(symbol)

            if len(selected) >= TARGET_ASSETS:
                break

    return selected[:TARGET_ASSETS]


def refresh_asset_list(force=False):
    global ACTIVE_ASSETS

    try:

        instruments = fetch_instruments()

        if not instruments:
            raise RuntimeError(
                "No tradeable PF crypto instruments returned"
            )

        tickers = fetch_all_tickers()

        selected = choose_top_assets(
            instruments,
            tickers,
        )

        if not selected:
            raise RuntimeError(
                "Unable to select assets"
            )

        ACTIVE_ASSETS = selected

        log(
            f"ASSETS: {len(ACTIVE_ASSETS)} selected"
        )

        return ACTIVE_ASSETS

    except Exception as e:

        log(
            f"Asset discovery error: {e}"
        )

        # Keep previous list if Kraken temporarily fails.
        if ACTIVE_ASSETS:
            return ACTIVE_ASSETS

        raise


# ============================================================
# CANDLES
# ============================================================

def normalize_candles(raw):
    candles = raw.get(
        "candles",
        []
    )

    result = []

    for c in candles:

        try:
            t = int(c["time"])

            o = float(c["open"])
            h = float(c["high"])
            l = float(c["low"])
            close = float(c["close"])

            volume = float(
                c.get("volume", 0)
            )

            result.append({
                "time": t,
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": volume,
            })

        except Exception:
            continue

    result.sort(
        key=lambda x: x["time"]
    )

    return result


def remove_current_candle(candles, interval_seconds):
    if not candles:
        return candles

    now_ms = int(time.time() * 1000)

    last = candles[-1]

    # Candle start time.
    candle_age = (
        now_ms - last["time"]
    ) / 1000

    if candle_age < interval_seconds:
        return candles[:-1]

    return candles


def fetch_candles(
    symbol,
    interval,
    limit,
):
    url = (
        f"{KRAKEN_CHART_BASE}/trade/"
        f"{symbol}/{interval}"
    )

    data = http_get(url)

    candles = normalize_candles(
        data
    )

    if interval == "30m":
        candles = remove_current_candle(
            candles,
            1800,
        )

    elif interval == "1m":
        candles = remove_current_candle(
            candles,
            60,
        )

    if limit:
        candles = candles[-limit:]

    return candles


# ============================================================
# PIVOTS
# ============================================================

def find_raw_pivots(candles):
    pivots = []

    if len(candles) < (
        PIVOT_LEFT + PIVOT_RIGHT + 1
    ):
        return pivots

    start = PIVOT_LEFT

    end = (
        len(candles)
        - PIVOT_RIGHT
    )

    for i in range(start, end):

        high = candles[i]["high"]
        low = candles[i]["low"]

        left_highs = [
            candles[j]["high"]
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        left_lows = [
            candles[j]["low"]
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + PIVOT_RIGHT + 1
            )
        ]

        is_high = (
            high >= max(left_highs)
            and
            high >= max(right_highs)
        )

        is_low = (
            low <= min(left_lows)
            and
            low <= min(right_lows)
        )

        if is_high and not is_low:

            pivots.append({
                "type": "H",
                "index": i,
                "time": candles[i]["time"],
                "price": high,
            })

        elif is_low and not is_high:

            pivots.append({
                "type": "L",
                "index": i,
                "time": candles[i]["time"],
                "price": low,
            })

    return pivots


def alternate_pivots(raw_pivots):
    if not raw_pivots:
        return []

    result = [
        raw_pivots[0]
    ]

    for p in raw_pivots[1:]:

        last = result[-1]

        if p["type"] != last["type"]:
            result.append(p)

        else:
            # If two same-type pivots are consecutive,
            # keep the more extreme one.
            if p["type"] == "H":

                if p["price"] > last["price"]:
                    result[-1] = p

            else:

                if p["price"] < last["price"]:
                    result[-1] = p

    return result


def find_pivots(candles):
    return alternate_pivots(
        find_raw_pivots(candles)
    )


# ============================================================
# M30 HOOK DETECTION
# ============================================================

def pct_distance(a, b):
    if b == 0:
        return 999

    return abs(
        (a - b) / b
    ) * 100


def positive_hook_from_sequence(seq):
    if len(seq) < 5:
        return None

    p = seq[-5:]

    expected = [
        "H",
        "L",
        "H",
        "L",
        "H",
    ]

    if [x["type"] for x in p] != expected:
        return None

    h1, l1, h2, l2, h3 = p

    # Structural higher-high / higher-low.
    if not (
        h2["price"] > h1["price"]
        and
        l2["price"] > l1["price"]
        and
        h3["price"] > h2["price"]
    ):
        return None

    hook_range = (
        h3["price"]
        - l1["price"]
    )

    if hook_range <= 0:
        return None

    range_pct = (
        hook_range
        / l1["price"]
        * 100
    )

    if range_pct < HOOK_MIN_RANGE_PCT:
        return None

    # H3 must be the latest pivot.
    # The next L closes the entry window.
    return {
        "direction": "POSITIVE",
        "h1": h1,
        "l1": l1,
        "h2": h2,
        "l2": l2,
        "h3": h3,
        "trigger_time": h3["time"],
        "trigger_price": h3["price"],
    }


def negative_hook_from_sequence(seq):
    if len(seq) < 5:
        return None

    p = seq[-5:]

    expected = [
        "L",
        "H",
        "L",
        "H",
        "L",
    ]

    if [x["type"] for x in p] != expected:
        return None

    l1, h1, l2, h2, l3 = p

    if not (
        l2["price"] < l1["price"]
        and
        h2["price"] < h1["price"]
        and
        l3["price"] < l2["price"]
    ):
        return None

    hook_range = (
        h1["price"]
        - l3["price"]
    )

    if hook_range <= 0:
        return None

    range_pct = (
        hook_range
        / h1["price"]
        * 100
    )

    if range_pct < HOOK_MIN_RANGE_PCT:
        return None

    return {
        "direction": "NEGATIVE",
        "l1": l1,
        "h1": h1,
        "l2": l2,
        "h2": h2,
        "l3": l3,
        "trigger_time": l3["time"],
        "trigger_price": l3["price"],
    }


def detect_active_m30_hook(candles):
    pivots = find_pivots(candles)

    if len(pivots) < 5:
        return None

    # We search from newest to oldest.
    # The active hook MUST end at the latest pivot.
    for i in range(
        len(pivots) - 1,
        3,
        -1
    ):

        seq = pivots[
            i - 4:i + 1
        ]

        hook = (
            positive_hook_from_sequence(seq)
        )

        if hook:

            # If another L exists after H3,
            # the window is already closed.
            later = pivots[
                i + 1:
            ]

            if any(
                x["type"] == "L"
                for x in later
            ):
                continue

            return hook

        hook = (
            negative_hook_from_sequence(seq)
        )

        if hook:

            # If another H exists after L3,
            # the window is already closed.
            later = pivots[
                i + 1:
            ]

            if any(
                x["type"] == "H"
                for x in later
            ):
                continue

            return hook

    return None


# ============================================================
# M30 HOOK BOUNDARY
# ============================================================

def find_hook_boundary(
    m30_candles,
    hook,
):
    pivots = find_pivots(
        m30_candles
    )

    trigger = hook[
        "trigger_time"
    ]

    later = [
        p for p in pivots
        if p["time"] > trigger
    ]

    if hook["direction"] == "POSITIVE":

        for p in later:

            if p["type"] == "L":
                return p

    else:

        for p in later:

            if p["type"] == "H":
                return p

    return None


# ============================================================
# M1 123F
# ============================================================

def positive_123f(
    candles,
    start_time,
    boundary_time=None,
):
    """
    Positive 123F:

        L1 -> H2 -> L3

        L3 > L1

        F = closed candle above H2

    Used for SHORT after positive M30 Hook.
    """

    pivots = find_pivots(
        candles
    )

    pivots = [
        p for p in pivots
        if p["time"] > start_time
    ]

    if boundary_time is not None:

        pivots = [
            p for p in pivots
            if p["time"] < boundary_time
        ]

    if len(pivots) < 3:
        return None

    for i in range(
        0,
        len(pivots) - 2
    ):

        p1 = pivots[i]
        p2 = pivots[i + 1]
        p3 = pivots[i + 2]

        if not (
            p1["type"] == "L"
            and
            p2["type"] == "H"
            and
            p3["type"] == "L"
        ):
            continue

        if p3["price"] <= p1["price"]:
            continue

        swing_pct = (
            (p2["price"] - p1["price"])
            / p1["price"]
            * 100
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        # Search F after P3.
        after_p3 = [
            c for c in candles
            if c["time"] > p3["time"]
        ]

        for c in after_p3:

            if boundary_time is not None:
                if c["time"] >= boundary_time:
                    break

            break_price = (
                p2["price"]
                * (
                    1
                    +
                    F_BREAK_BUFFER_PCT
                    / 100
                )
            )

            if c["close"] > break_price:

                distance_pct = (
                    (c["close"] - p1["price"])
                    / p1["price"]
                    * 100
                )

                if (
                    distance_pct
                    >
                    M1_MAX_STRUCTURE_DISTANCE_PCT
                ):
                    continue

                return {
                    "direction": "POSITIVE",
                    "p1": p1,
                    "p2": p2,
                    "p3": p3,
                    "f": {
                        "time": c["time"],
                        "price": c["close"],
                        "high": c["high"],
                        "low": c["low"],
                    },
                }

    return None


def negative_123f(
    candles,
    start_time,
    boundary_time=None,
):
    """
    Negative 123F:

        H1 -> L2 -> H3

        H3 < H1

        F = closed candle below L2

    Used for LONG after negative M30 Hook.
    """

    pivots = find_pivots(
        candles
    )

    pivots = [
        p for p in pivots
        if p["time"] > start_time
    ]

    if boundary_time is not None:

        pivots = [
            p for p in pivots
            if p["time"] < boundary_time
        ]

    if len(pivots) < 3:
        return None

    for i in range(
        0,
        len(pivots) - 2
    ):

        p1 = pivots[i]
        p2 = pivots[i + 1]
        p3 = pivots[i + 2]

        if not (
            p1["type"] == "H"
            and
            p2["type"] == "L"
            and
            p3["type"] == "H"
        ):
            continue

        if p3["price"] >= p1["price"]:
            continue

        swing_pct = (
            (p1["price"] - p2["price"])
            / p1["price"]
            * 100
        )

        if swing_pct < M1_MIN_SWING_PCT:
            continue

        after_p3 = [
            c for c in candles
            if c["time"] > p3["time"]
        ]

        for c in after_p3:

            if boundary_time is not None:
                if c["time"] >= boundary_time:
                    break

            break_price = (
                p2["price"]
                * (
                    1
                    -
                    F_BREAK_BUFFER_PCT
                    / 100
                )
            )

            if c["close"] < break_price:

                distance_pct = (
                    (p1["price"] - c["close"])
                    / p1["price"]
                    * 100
                )

                if (
                    distance_pct
                    >
                    M1_MAX_STRUCTURE_DISTANCE_PCT
                ):
                    continue

                return {
                    "direction": "NEGATIVE",
                    "p1": p1,
                    "p2": p2,
                    "p3": p3,
                    "f": {
                        "time": c["time"],
                        "price": c["close"],
                        "high": c["high"],
                        "low": c["low"],
                    },
                }

    return None


# ============================================================
# SIGNAL LEVELS
# ============================================================

def build_trade_levels(
    direction,
    pattern,
):
    entry = float(
        pattern["f"]["price"]
    )

    p1 = pattern["p1"]
    p2 = pattern["p2"]
    p3 = pattern["p3"]

    if direction == "SHORT":

        structure_sl = max(
            p1["price"],
            p2["price"],
            p3["price"],
        )

        sl = (
            structure_sl
            * (
                1
                +
                SL_BUFFER_PCT / 100
            )
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

    else:

        structure_sl = min(
            p1["price"],
            p2["price"],
            p3["price"],
        )

        sl = (
            structure_sl
            * (
                1
                -
                SL_BUFFER_PCT / 100
            )
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

    return {
        "entry": entry,
        "sl": sl,
        "risk": risk,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
    }


# ============================================================
# SIGNAL DUPLICATE CONTROL
# ============================================================

def signal_exists(
    symbol,
    side,
    f_time,
):
    conn = db()

    row = conn.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND m1_f_time = ?
        LIMIT 1
    """, (
        symbol,
        side,
        f_time,
    )).fetchone()

    conn.close()

    return row is not None


def recent_signal_exists(
    symbol,
    side,
):
    cutoff = (
        ts_now()
        -
        SIGNAL_COOLDOWN_MINUTES * 60
    )

    conn = db()

    row = conn.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND opened_at >= ?
        LIMIT 1
    """, (
        symbol,
        side,
        cutoff,
    )).fetchone()

    conn.close()

    return row is not None


# ============================================================
# CHART
# ============================================================

def save_signal_chart(
    symbol,
    m1_candles,
    hook,
    pattern,
    levels,
):
    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    f_time = pattern[
        "f"
    ]["time"]

    filename = (
        f"{symbol}_"
        f"{f_time}_"
        f"{pattern['direction']}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename,
    )

    candles = [
        c for c in m1_candles
        if c["time"] >= (
            f_time - 60 * 60 * 4 * 1000
        )
    ]

    if len(candles) > 300:
        candles = candles[-300:]

    if not candles:
        return None

    x = [
        ms_to_datetime(c["time"])
        for c in candles
    ]

    fig, ax = plt.subplots(
        figsize=(15, 8)
    )

    # Candle width.
    width = (
        0.65
        / 1440
    )

    for c, xx in zip(candles, x):

        o = c["open"]
        h = c["high"]
        l = c["low"]
        cl = c["close"]

        if cl >= o:
            face = "green"
        else:
            face = "red"

        ax.plot(
            [xx, xx],
            [l, h],
            linewidth=0.8,
            color="black",
        )

        ax.bar(
            xx,
            cl - o,
            bottom=o,
            width=width,
            color=face,
            alpha=0.7,
        )

    # 123 points.
    pts = [
        pattern["p1"],
        pattern["p2"],
        pattern["p3"],
    ]

    px = [
        ms_to_datetime(p["time"])
        for p in pts
    ]

    py = [
        p["price"]
        for p in pts
    ]

    ax.plot(
        px,
        py,
        linewidth=2,
        marker="o",
        color="blue",
    )

    labels = [
        "P1",
        "P2",
        "P3",
    ]

    for p, label in zip(pts, labels):

        ax.annotate(
            label,
            (
                ms_to_datetime(
                    p["time"]
                ),
                p["price"],
            ),
            xytext=(5, 8),
            textcoords="offset points",
            fontsize=10,
        )

    # F
    f = pattern["f"]

    fx = ms_to_datetime(
        f["time"]
    )

    ax.scatter(
        [fx],
        [f["price"]],
        s=80,
        color="purple",
        zorder=10,
    )

    ax.annotate(
        "F / ENTRY",
        (
            fx,
            f["price"],
        ),
        xytext=(8, -15),
        textcoords="offset points",
        fontsize=10,
        fontweight="bold",
    )

    # Levels
    ax.axhline(
        levels["entry"],
        linestyle="-",
        linewidth=1.5,
        color="blue",
        label="ENTRY",
    )

    ax.axhline(
        levels["sl"],
        linestyle="--",
        linewidth=1.5,
        color="red",
        label="SL",
    )

    ax.axhline(
        levels["tp1"],
        linestyle="--",
        linewidth=1,
        color="green",
        label="TP1",
    )

    ax.axhline(
        levels["tp2"],
        linestyle="--",
        linewidth=1,
        color="green",
        label="TP2",
    )

    ax.axhline(
        levels["tp3"],
        linestyle="--",
        linewidth=1,
        color="green",
        label="TP3",
    )

    # M30 trigger
    trigger_price = hook[
        "trigger_price"
    ]

    ax.axhline(
        trigger_price,
        linestyle=":",
        linewidth=1.2,
        color="orange",
        label="M30 33",
    )

    direction = pattern[
        "direction"
    ]

    ax.set_title(
        f"NDS M30 Hook -> M1 123F | "
        f"{symbol} | {direction}"
    )

    ax.set_xlabel(
        "UTC"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.xaxis.set_major_formatter(
        mdates.DateFormatter(
            "%H:%M"
        )
    )

    ax.grid(
        alpha=0.2
    )

    ax.legend(
        loc="best"
    )

    fig.autofmt_xdate()

    plt.tight_layout()

    fig.savefig(
        path,
        dpi=150,
    )

    plt.close(fig)

    return path


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text,
    image_path=None,
):
    if not (
        TELEGRAM_BOT_TOKEN
        and
        TELEGRAM_CHAT_ID
    ):
        return False

    try:

        if image_path and os.path.exists(
            image_path
        ):

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendPhoto"
            )

            with open(
                image_path,
                "rb"
            ) as f:

                r = requests.post(
                    url,
                    data={
                        "chat_id":
                            TELEGRAM_CHAT_ID,
                        "caption":
                            text,
                    },
                    files={
                        "photo": f
                    },
                    timeout=20,
                )

        else:

            url = (
                "https://api.telegram.org/"
                f"bot{TELEGRAM_BOT_TOKEN}/"
                "sendMessage"
            )

            r = requests.post(
                url,
                data={
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text":
                        text,
                    "parse_mode":
                        "HTML",
                },
                timeout=20,
            )

        return r.ok

    except Exception as e:

        log(
            f"Telegram error: {e}"
        )

        return False


# ============================================================
# SIGNAL INSERT
# ============================================================

def insert_signal(
    symbol,
    side,
    hook,
    pattern,
    levels,
    chart_path,
):
    f_time = pattern[
        "f"
    ]["time"]

    if signal_exists(
        symbol,
        side,
        f_time,
    ):
        return False

    if recent_signal_exists(
        symbol,
        side,
    ):
        return False

    if count_open_trades() >= MAX_OPEN_TRADES:
        return False

    conn = db()

    try:

        conn.execute("""
            INSERT INTO signals (
                symbol,
                side,
                entry,
                sl,
                tp1,
                tp2,
                tp3,
                final_tp,
                risk,
                hook_direction,

                m30_h1_time,
                m30_l1_time,
                m30_h2_time,
                m30_l2_time,
                m30_h3_time,

                m30_trigger_time,

                m1_p1_time,
                m1_p2_time,
                m1_p3_time,

                m1_f_time,

                status,
                opened_at,
                chart_path
            )
            VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                NULL,
                ?, ?,

                ?, ?, ?, ?, ?,

                ?,

                ?, ?, ?,

                ?,

                'OPEN',
                ?,
                ?
            )
        """, (

            symbol,
            side,

            levels["entry"],
            levels["sl"],
            levels["tp1"],
            levels["tp2"],
            levels["tp3"],

            levels["risk"],

            hook["direction"],

            hook["h1"]["time"],
            hook["l1"]["time"],
            hook["h2"]["time"],
            hook["l2"]["time"],
            hook["h3"]["time"],

            hook["trigger_time"],

            pattern["p1"]["time"],
            pattern["p2"]["time"],
            pattern["p3"]["time"],

            pattern["f"]["time"],

            ts_now(),

            chart_path,
        ))

        conn.commit()

    except sqlite3.IntegrityError:

        conn.rollback()
        conn.close()

        return False

    except Exception:

        conn.rollback()
        conn.close()

        raise

    conn.close()

    return True


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_message(
    symbol,
    side,
    hook,
    pattern,
    levels,
):
    emoji = (
        "🟢"
        if side == "LONG"
        else
        "🔴"
    )

    return f"""
{emoji} <b>NDS {side}</b>

<b>{symbol}</b>

Entry:
<code>{levels['entry']:.8g}</code>

SL:
<code>{levels['sl']:.8g}</code>

TP1:
<code>{levels['tp1']:.8g}</code>

TP2:
<code>{levels['tp2']:.8g}</code>

TP3:
<code>{levels['tp3']:.8g}</code>

M30:
<b>{hook['direction']} HOOK</b>

M30 33:
<code>{hook['trigger_price']:.8g}</code>

M1:
<b>123F confirmed</b>

F:
<code>{pattern['f']['price']:.8g}</code>

Mode:
<b>PAPER ONLY</b>
""".strip()


# ============================================================
# PROCESS SINGLE SYMBOL
# ============================================================

def process_symbol(symbol):
    result = {
        "symbol": symbol,
        "hook": None,
        "signal": None,
        "error": None,
    }

    try:

        m30 = fetch_candles(
            symbol,
            M30_INTERVAL,
            M30_CANDLES,
        )

        if len(m30) < 20:
            return result

        hook = detect_active_m30_hook(
            m30
        )

        result["hook"] = hook

        if not hook:
            return result

        boundary = find_hook_boundary(
            m30,
            hook,
        )

        boundary_time = None

        if boundary:
            boundary_time = boundary[
                "time"
            ]

        # ----------------------------------------------------
        # M1
        # ----------------------------------------------------

        m1 = fetch_candles(
            symbol,
            M1_INTERVAL,
            M1_CANDLES,
        )

        if len(m1) < 50:
            return result

        if hook["direction"] == "POSITIVE":

            pattern = positive_123f(
                m1,
                hook["trigger_time"],
                boundary_time,
            )

            if not pattern:
                return result

            side = "SHORT"

        else:

            pattern = negative_123f(
                m1,
                hook["trigger_time"],
                boundary_time,
            )

            if not pattern:
                return result

            side = "LONG"

        f_time = pattern[
            "f"
        ]["time"]

        event_key = (
            f"{symbol}|"
            f"{side}|"
            f"{f_time}"
        )

        if (
            event_key
            in
            PROCESSED_F_KEYS
        ):
            return result

        if already_processed(
            event_key
        ):
            PROCESSED_F_KEYS.add(
                event_key
            )
            return result

        levels = build_trade_levels(
            side,
            pattern,
        )

        if not levels:
            return result

        chart_path = save_signal_chart(
            symbol,
            m1,
            hook,
            pattern,
            levels,
        )

        inserted = insert_signal(
            symbol,
            side,
            hook,
            pattern,
            levels,
            chart_path,
        )

        if not inserted:
            mark_processed(
                event_key
            )

            PROCESSED_F_KEYS.add(
                event_key
            )

            return result

        mark_processed(
            event_key
        )

        PROCESSED_F_KEYS.add(
            event_key
        )

        result["signal"] = {
            "side": side,
            "hook": hook,
            "pattern": pattern,
            "levels": levels,
            "chart": chart_path,
        }

        return result

    except Exception as e:

        result["error"] = str(e)

        return result


# ============================================================
# EXIT / TRADE MANAGEMENT
# ============================================================

def check_trade_exit(
    trade,
    current_price,
):
    side = trade["side"]

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp1 = float(
        trade["tp1"]
    )

    tp2 = float(
        trade["tp2"]
    )

    tp3 = float(
        trade["tp3"]
    )

    if side == "LONG":

        if current_price <= sl:
            return (
                "SL",
                sl,
            )

        if current_price >= tp3:
            return (
                "TP3",
                tp3,
            )

        if current_price >= tp2:
            return (
                "TP2",
                tp2,
            )

        if current_price >= tp1:
            return (
                "TP1",
                tp1,
            )

    else:

        if current_price >= sl:
            return (
                "SL",
                sl,
            )

        if current_price <= tp3:
            return (
                "TP3",
                tp3,
            )

        if current_price <= tp2:
            return (
                "TP2",
                tp2,
            )

        if current_price <= tp1:
            return (
                "TP1",
                tp1,
            )

    return None


def calculate_pnl(
    side,
    entry,
    close,
):
    if side == "LONG":

        return (
            close - entry
        ) / entry * 100

    return (
        entry - close
    ) / entry * 100


def close_trade(
    trade,
    close_price,
    reason,
):
    pnl = calculate_pnl(
        trade["side"],
        float(trade["entry"]),
        close_price,
    )

    conn = db()

    conn.execute("""
        UPDATE signals
        SET
            status = 'CLOSED',
            result = ?,
            pnl_pct = ?,
            closed_at = ?,
            close_price = ?,
            close_reason = ?
        WHERE id = ?
          AND status = 'OPEN'
    """, (
        "WIN"
        if pnl > 0
        else
        "LOSS",

        pnl,

        ts_now(),

        close_price,

        reason,

        trade["id"],
    ))

    conn.commit()
    conn.close()

    emoji = (
        "🟢"
        if pnl > 0
        else
        "🔴"
    )

    message = (
        f"{emoji} <b>NDS CLOSED</b>\n\n"
        f"<b>{trade['symbol']}</b> "
        f"{trade['side']}\n\n"
        f"Close: <code>{close_price:.8g}</code>\n"
        f"Reason: <b>{reason}</b>\n"
        f"PnL: <b>{pnl:+.2f}%</b>"
    )

    telegram_send(
        message
    )


def fetch_last_price(symbol):
    url = (
        f"{KRAKEN_REST_BASE}/tickers/"
        f"{symbol}"
    )

    try:

        data = http_get(
            url
        )

        ticker = data.get(
            "ticker",
            data,
        )

        for key in (
            "last",
            "lastPrice",
            "price",
        ):

            if key in ticker:

                try:
                    return float(
                        ticker[key]
                    )
                except Exception:
                    pass

    except Exception as e:

        debug(
            f"Ticker {symbol}: {e}"
        )

    return None


def manage_open_trades():
    trades = get_open_trades()

    if not trades:
        return 0

    closed = 0

    # Cache prices by symbol.
    prices = {}

    for trade in trades:

        symbol = trade["symbol"]

        if symbol not in prices:

            prices[symbol] = (
                fetch_last_price(
                    symbol
                )
            )

    for trade in trades:

        price = prices.get(
            trade["symbol"]
        )

        if price is None:
            continue

        result = check_trade_exit(
            trade,
            price,
        )

        if result:

            reason, close_price = result

            close_trade(
                trade,
                close_price,
                reason,
            )

            closed += 1

    return closed


# ============================================================
# PERFORMANCE
# ============================================================

def performance():
    conn = db()

    row = conn.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                    WHEN result = 'LOSS'
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
    """).fetchone()

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

    winrate = (
        wins / total * 100
        if total
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "winrate": winrate,
    }


# ============================================================
# REPORT
# ============================================================

def report(
    scanned,
    active_hooks,
    new_signals,
    errors,
):
    open_count = count_open_trades()

    perf = performance()

    text = f"""
📊 <b>NDS M30 → M1 REPORT</b>

Assets: <b>{scanned}</b>
Active Hooks: <b>{active_hooks}</b>
New Signals: <b>{new_signals}</b>
Open Trades: <b>{open_count}</b>

Closed: {perf['total']}
Wins: {perf['wins']}
Losses: {perf['losses']}
Win Rate: {perf['winrate']:.1f}%
PnL: {perf['pnl']:+.2f}%

Errors: {errors}

Mode: <b>PAPER ONLY</b>
Version: <b>{VERSION}</b>
""".strip()

    log(
        text.replace(
            "<b>", ""
        ).replace(
            "</b>", ""
        )
    )


# ============================================================
# RUN SCAN
# ============================================================

def scan_all_assets():
    global LAST_M30_SCAN

    now = time.time()

    # Refresh list periodically.
    if (
        not ACTIVE_ASSETS
        or
        now - LAST_M30_SCAN
        >= 3600
    ):
        refresh_asset_list()

    # M30 scan controls which symbols have active hooks.
    # This prevents 100 M1 requests every minute.
    if (
        now - LAST_M30_SCAN
        >= M30_SCAN_INTERVAL_SECONDS
        or
        LAST_M30_SCAN == 0
    ):

        LAST_M30_SCAN = now

        active = []

        log(
            f"Scanning M30 structure on "
            f"{len(ACTIVE_ASSETS)} assets..."
        )

        def get_hook(symbol):

            try:

                m30 = fetch_candles(
                    symbol,
                    M30_INTERVAL,
                    M30_CANDLES,
                )

                hook = detect_active_m30_hook(
                    m30
                )

                return symbol, hook, None

            except Exception as e:

                return symbol, None, str(e)

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            futures = [
                executor.submit(
                    get_hook,
                    symbol
                )
                for symbol in ACTIVE_ASSETS
            ]

            for future in as_completed(
                futures
            ):

                symbol, hook, error = (
                    future.result()
                )

                if hook:

                    active.append(
                        symbol
                    )

        log(
            f"Active M30 Hooks: "
            f"{len(active)}"
        )

    else:

        active = []

        # On non-M30 cycles we only need symbols
        # that currently have an active hook.
        for symbol in ACTIVE_ASSETS:

            # We intentionally check M30 again here
            # only if the list was not built.
            #
            # To avoid stale hook state, use DB-style
            # memory below.
            pass

    # --------------------------------------------------------
    # Rebuild active hook list every cycle from M30.
    #
    # Since M1 is the expensive/high-frequency part,
    # only symbols with active M30 structure are allowed
    # to reach M1.
    # --------------------------------------------------------

    active_symbols = []

    if (
        now - LAST_M30_SCAN
        <= 1
    ):
        # M30 scan just ran.
        # Re-read M30 for the active list would be wasteful,
        # so use the local result.
        active_symbols = active

    else:
        # Use a lighter M30 scan to maintain correct state.
        # This still happens once per 5 min.
        #
        # If not due, we derive active hooks from fresh M30
        # only for symbols that were active previously.
        #
        # Since active is local, use a persisted in-memory
        # structure.
        active_symbols = [
            s for s in ACTIVE_ASSETS
            if s in ACTIVE_HOOK_SYMBOLS
        ]

    return active_symbols


# ============================================================
# ACTIVE HOOK MEMORY
# ============================================================

ACTIVE_HOOK_SYMBOLS = set()


def refresh_active_hooks():
    global LAST_M30_SCAN
    global ACTIVE_HOOK_SYMBOLS

    now = time.time()

    if (
        LAST_M30_SCAN != 0
        and
        now - LAST_M30_SCAN
        <
        M30_SCAN_INTERVAL_SECONDS
    ):
        return

    LAST_M30_SCAN = now

    active = set()

    errors = 0

    log(
        f"M30 scan: "
        f"{len(ACTIVE_ASSETS)} assets"
    )

    def worker(symbol):

        try:

            m30 = fetch_candles(
                symbol,
                M30_INTERVAL,
                M30_CANDLES,
            )

            hook = detect_active_m30_hook(
                m30
            )

            return symbol, bool(hook), None

        except Exception as e:

            return symbol, False, str(e)

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                worker,
                symbol
            )
            for symbol in ACTIVE_ASSETS
        ]

        for future in as_completed(
            futures
        ):

            symbol, is_active, error = (
                future.result()
            )

            if is_active:
                active.add(symbol)

            if error:
                errors += 1

    ACTIVE_HOOK_SYMBOLS = active

    log(
        f"M30 active hooks: "
        f"{len(active)} | errors: {errors}"
    )


# ============================================================
# M1 ACTIVE-HOOK SCAN
# ============================================================

def scan_active_hooks():
    symbols = list(
        ACTIVE_HOOK_SYMBOLS
    )

    if not symbols:
        return 0, 0

    new_signals = 0
    errors = 0

    def worker(symbol):

        try:

            result = process_symbol(
                symbol
            )

            return result

        except Exception as e:

            return {
                "symbol": symbol,
                "error": str(e),
                "signal": None,
            }

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = [
            executor.submit(
                worker,
                symbol
            )
            for symbol in symbols
        ]

        for future in as_completed(
            futures
        ):

            result = future.result()

            if result.get(
                "error"
            ):
                errors += 1

                debug(
                    f"{result['symbol']}: "
                    f"{result['error']}"
                )

            signal = result.get(
                "signal"
            )

            if signal:

                new_signals += 1

                symbol = result[
                    "symbol"
                ]

                message = signal_message(
                    symbol,
                    signal["side"],
                    signal["hook"],
                    signal["pattern"],
                    signal["levels"],
                )

                telegram_send(
                    message,
                    signal["chart"],
                )

                log(
                    f"NEW {signal['side']} "
                    f"{symbol} "
                    f"@ "
                    f"{signal['levels']['entry']}"
                )

    return new_signals, errors


# ============================================================
# RUN LOG
# ============================================================

def save_run(
    scanned,
    active,
    new_signals,
    open_trades,
    errors,
):
    conn = db()

    conn.execute("""
        INSERT INTO scanner_runs (
            run_time,
            assets_scanned,
            active_hooks,
            new_signals,
            open_trades,
            errors
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        ts_now(),
        scanned,
        active,
        new_signals,
        open_trades,
        errors,
    ))

    conn.commit()
    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    global ACTIVE_ASSETS

    if not PAPER_ONLY:
        raise RuntimeError(
            "SAFETY ERROR: "
            "This scanner is PAPER ONLY."
        )

    os.makedirs(
        CHART_DIR,
        exist_ok=True
    )

    init_db()

    log("=" * 70)
    log(
        f"NDS M30 -> M1 SCANNER "
        f"VERSION {VERSION}"
    )
    log(
        "MODE: PAPER ONLY"
    )
    log(
        "TARGET ASSETS: 100"
    )
    log("=" * 70)

    # Initial asset discovery.
    refresh_asset_list(
        force=True
    )

    log(
        "Selected assets:"
    )

    log(
        ", ".join(
            ACTIVE_ASSETS
        )
    )

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    last_report = 0

    while True:

        cycle_start = time.time()

        try:

            # ------------------------------------------------
            # Refresh assets once per hour.
            # ------------------------------------------------

            if (
                time.time()
                -
                getattr(
                    refresh_asset_list,
                    "_last",
                    0
                )
                >= 3600
            ):

                try:

                    refresh_asset_list()

                finally:

                    refresh_asset_list._last = (
                        time.time()
                    )

            # ------------------------------------------------
            # M30 Hook state
            # ------------------------------------------------

            refresh_active_hooks()

            # ------------------------------------------------
            # Manage open paper trades
            # ------------------------------------------------

            closed = manage_open_trades()

            if closed:
                log(
                    f"Closed trades: {closed}"
                )

            # ------------------------------------------------
            # M1 entry scan
            # ------------------------------------------------

            new_signals, errors = (
                scan_active_hooks()
            )

            # ------------------------------------------------
            # Report every 5 minutes
            # ------------------------------------------------

            if (
                time.time()
                -
                last_report
                >= 300
            ):

                last_report = time.time()

                report(
                    scanned=len(
                        ACTIVE_ASSETS
                    ),
                    active_hooks=len(
                        ACTIVE_HOOK_SYMBOLS
                    ),
                    new_signals=new_signals,
                    errors=errors,
                )

                save_run(
                    scanned=len(
                        ACTIVE_ASSETS
                    ),
                    active=len(
                        ACTIVE_HOOK_SYMBOLS
                    ),
                    new_signals=new_signals,
                    open_trades=count_open_trades(),
                    errors=errors,
                )

        except KeyboardInterrupt:

            log(
                "Scanner stopped."
            )

            break

        except Exception as e:

            log(
                "MAIN LOOP ERROR:"
            )

            log(
                str(e)
            )

            traceback.print_exc()

        elapsed = (
            time.time()
            -
            cycle_start
        )

        sleep_for = max(
            1,
            SCAN_INTERVAL_SECONDS
            -
            elapsed
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
