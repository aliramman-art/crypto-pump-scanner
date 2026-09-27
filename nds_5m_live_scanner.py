# ============================================================
# NDS M30 -> M1 LIVE SCANNER
# VERSION 2.0.0
# ============================================================
#
# PAPER ONLY
#
# NDS STRUCTURE
#
# M30 POSITIVE HOOK:
#
#       11H
#        |
#        |       33H
#        |      /
#        |  22L
#        | /
#        |
#   H1 -> L1 -> H2 -> L2 -> H3
#
# When M30 33H is CONFIRMED:
#
#     OPEN M1 ENTRY WINDOW
#
#     M1 positive 123F
#
#        L1
#         \
#          H2 -------- F
#         /
#        L3
#
#     F BREAKS ABOVE H2
#             |
#             v
#           SHORT
#
# Entry window remains open until the NEXT M30
# third trough / opposite pivot is formed and confirmed.
#
#
# M30 NEGATIVE HOOK:
#
#       11L
#        |
#        |       33L
#        |      /
#        |  22H
#        | /
#        |
#   L1 -> H1 -> L2 -> H2 -> L3
#
# When M30 33L is CONFIRMED:
#
#     OPEN M1 ENTRY WINDOW
#
#     M1 negative 123F
#
#        H1
#         \
#          L2 -------- F
#         /
#        H3
#
#     F BREAKS BELOW L2
#             |
#             v
#            LONG
#
# Entry window closes when the NEXT M30
# third peak / opposite pivot is formed and confirmed.
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
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "2.0.0"

PAPER_ONLY = True

DB_FILE = "nds_m30_m1_v20.db"
CHART_DIR = "nds_charts"

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1"

M30_INTERVAL = "30m"
M1_INTERVAL = "1m"

M30_COUNT = 260
M1_COUNT = 1200

MIN_M30_CANDLES = 50
MIN_M1_CANDLES = 100

REQUEST_RETRIES = 3
REQUEST_DELAY = 1.0
REQUEST_TIMEOUT = 20

# ------------------------------------------------------------
# Pivot
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MIN_SWING_PCT = 0.0010

# ------------------------------------------------------------
# M30 Hook
# ------------------------------------------------------------

# 11 / 22 / 33 must have meaningful swings.
HOOK_MIN_RANGE = 0.0020

# 86.4% validation tolerance
HOOK_864_TOL = 0.020

# ------------------------------------------------------------
# M1 123F
# ------------------------------------------------------------

M1_123_MIN_SWING = 0.0007
M1_123_MAX_DISTANCE_PCT = 0.030

# F breakout buffer
F_BREAK_BUFFER = 0.0002

# ------------------------------------------------------------
# Trading
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3

SIGNAL_COOLDOWN_MIN = 60

TP1_R = 1.0
TP2_R = 2.0
TP3_R = 3.0

USE_864_FINAL_TARGET = True
FINAL_864_MULTIPLIER = 0.864

# Structural SL buffer
SL_BUFFER_PCT = 0.0015

# ------------------------------------------------------------
# Scanner
# ------------------------------------------------------------

SCAN_INTERVAL_SECONDS = 60

# ------------------------------------------------------------
# Telegram
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


# ============================================================
# SYMBOLS
# ============================================================

SYMBOLS = [
    "PF_AAVEUSD",
    "PF_ADAUSD",
    "PF_ALGOUSD",
    "PF_APTUSD",
    "PF_ARBUSD",
    "PF_ATOMUSD",
    "PF_AVAXUSD",
    "PF_BCHUSD",
    "PF_BONKUSD",
    "PF_BTCUSD",
    "PF_DOGEUSD",
    "PF_DOTUSD",
    "PF_ENSUSD",
    "PF_ETCUSD",
    "PF_ETHUSD",
    "PF_FETUSD",
    "PF_FILUSD",
    "PF_FLOKIUSD",
    "PF_GALAUSD",
    "PF_HBARUSD",
    "PF_IMXUSD",
    "PF_INJUSD",
    "PF_JASMYUSD",
    "PF_LINKUSD",
    "PF_LDOUSD",
    "PF_LTCUSD",
    "PF_MANAUSD",
    "PF_NEARUSD",
    "PF_OPUSD",
    "PF_PEPEUSD",
    "PF_POLUSD",
    "PF_SEIUSD",
    "PF_SHIBUSD",
    "PF_SOLUSD",
    "PF_SUIUSD",
    "PF_TAOUSD",
    "PF_THETAUSD",
    "PF_TRXUSD",
    "PF_XLMUSD",
    "PF_XRPUSD",
]


# ============================================================
# GLOBAL SESSION
# ============================================================

SESSION = requests.Session()

os.makedirs(CHART_DIR, exist_ok=True)


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def ts_to_dt(ts):
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def fmt_time(ts):
    try:
        return ts_to_dt(ts).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return str(ts)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

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

    try:
        r = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if r.status_code != 200:
            print("Telegram error:", r.text[:500])
            return False

        return True

    except Exception as e:
        print("Telegram exception:", e)
        return False


def telegram_send_photo(photo_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not os.path.exists(photo_path):
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(photo_path, "rb") as f:
            files = {
                "photo": f,
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            r = SESSION.post(
                url,
                files=files,
                data=data,
                timeout=REQUEST_TIMEOUT,
            )

        return r.status_code == 200

    except Exception as e:
        print("Telegram photo error:", e)
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            signal_time REAL NOT NULL,

            entry REAL NOT NULL,
            sl REAL NOT NULL,

            tp1 REAL,
            tp2 REAL,
            tp3 REAL,

            final_tp REAL,

            status TEXT DEFAULT 'OPEN',

            exit_time REAL,
            exit_price REAL,
            exit_reason TEXT,

            pnl_pct REAL,

            m30_direction TEXT,

            m30_11_time REAL,
            m30_22_time REAL,
            m30_33_time REAL,

            m30_33_price REAL,

            m1_p1_time REAL,
            m1_p1_price REAL,

            m1_p2_time REAL,
            m1_p2_price REAL,

            m1_p3_time REAL,
            m1_p3_price REAL,

            m1_f_time REAL,
            m1_f_price REAL,

            chart_path TEXT,

            created_at REAL DEFAULT (
                CAST(strftime('%s','now') AS REAL)
            )
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_time REAL NOT NULL,
            scanned INTEGER DEFAULT 0,
            new_signals INTEGER DEFAULT 0,
            open_trades INTEGER DEFAULT 0,
            closed_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            pnl_pct REAL DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def count_open_trades(conn):
    row = conn.execute("""
        SELECT COUNT(*)
        FROM signals
        WHERE status = 'OPEN'
    """).fetchone()

    return int(row[0])


def recent_signal_exists(conn, symbol, side, cooldown_min):
    cutoff = now_utc().timestamp() - cooldown_min * 60

    row = conn.execute("""
        SELECT id
        FROM signals
        WHERE symbol = ?
          AND side = ?
          AND signal_time >= ?
        ORDER BY signal_time DESC
        LIMIT 1
    """, (
        symbol,
        side,
        cutoff,
    )).fetchone()

    return row is not None


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_candles(symbol, interval, count):
    url = (
        f"{KRAKEN_BASE}/trade/"
        f"{symbol}/"
        f"{interval}"
    )

    params = {
        "count": count,
    }

    last_error = None

    for attempt in range(REQUEST_RETRIES):

        try:
            r = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            r.raise_for_status()

            data = r.json()

            if isinstance(data, dict):
                rows = (
                    data.get("candles")
                    or data.get("data")
                    or data.get("result")
                    or []
                )
            else:
                rows = data

            if not rows:
                raise ValueError(
                    f"No candle data for {symbol} {interval}"
                )

            parsed = []

            for row in rows:

                if isinstance(row, dict):

                    ts = (
                        row.get("time")
                        or row.get("timestamp")
                        or row.get("ts")
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

                elif isinstance(row, (list, tuple)):

                    if len(row) < 5:
                        continue

                    ts = row[0]
                    op = row[1]
                    hi = row[2]
                    lo = row[3]
                    cl = row[4]

                    vol = row[5] if len(row) > 5 else 0

                else:
                    continue

                try:
                    parsed.append({
                        "timestamp": float(ts),
                        "open": float(op),
                        "high": float(hi),
                        "low": float(lo),
                        "close": float(cl),
                        "volume": float(vol),
                    })
                except Exception:
                    continue

            if not parsed:
                raise ValueError(
                    f"Could not parse candles {symbol} {interval}"
                )

            df = pd.DataFrame(parsed)

            df = df.drop_duplicates(
                subset=["timestamp"]
            )

            df = df.sort_values(
                "timestamp"
            ).reset_index(drop=True)

            # ------------------------------------------------
            # Remove currently forming candle
            # ------------------------------------------------

            interval_seconds = {
                "1m": 60,
                "5m": 300,
                "15m": 900,
                "30m": 1800,
                "1h": 3600,
            }.get(interval, 60)

            current_ts = now_utc().timestamp()

            if len(df):

                last_ts = float(
                    df.iloc[-1]["timestamp"]
                )

                if (
                    last_ts + interval_seconds
                    > current_ts
                ):
                    df = df.iloc[:-1].copy()

            if len(df) < 5:
                raise ValueError(
                    f"Not enough closed candles "
                    f"{symbol} {interval}"
                )

            return df.reset_index(drop=True)

        except Exception as e:
            last_error = e

            if attempt < REQUEST_RETRIES - 1:
                time.sleep(REQUEST_DELAY)

    raise last_error


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    df,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT,
    min_swing=MIN_SWING_PCT,
):
    """
    Returns alternating confirmed pivots.

    type:
        H = swing high
        L = swing low
    """

    if len(df) < left + right + 5:
        return []

    raw = []

    highs = df["high"].values
    lows = df["low"].values

    for i in range(left, len(df) - right):

        h = highs[i]
        l = lows[i]

        left_highs = highs[i - left:i]
        right_highs = highs[i + 1:i + right + 1]

        left_lows = lows[i - left:i]
        right_lows = lows[i + 1:i + right + 1]

        is_high = (
            h >= np.max(left_highs)
            and h > np.max(right_highs)
        )

        is_low = (
            l <= np.min(left_lows)
            and l < np.min(right_lows)
        )

        if is_high:
            raw.append({
                "index": i,
                "timestamp": float(
                    df.iloc[i]["timestamp"]
                ),
                "price": float(h),
                "type": "H",
            })

        if is_low:
            raw.append({
                "index": i,
                "timestamp": float(
                    df.iloc[i]["timestamp"]
                ),
                "price": float(l),
                "type": "L",
            })

    raw.sort(key=lambda x: x["index"])

    # --------------------------------------------------------
    # Make pivots alternate H/L
    # --------------------------------------------------------

    pivots = []

    for p in raw:

        if not pivots:
            pivots.append(p)
            continue

        last = pivots[-1]

        if p["type"] == last["type"]:

            if p["type"] == "H":
                if p["price"] > last["price"]:
                    pivots[-1] = p

            else:
                if p["price"] < last["price"]:
                    pivots[-1] = p

            continue

        # meaningful swing
        base = last["price"]

        if base == 0:
            continue

        swing = abs(
            p["price"] - base
        ) / base

        if swing >= min_swing:
            pivots.append(p)

    return pivots


# ============================================================
# PIVOT HELPERS
# ============================================================

def pivot_sequence_is_valid(seq):
    if len(seq) < 3:
        return False

    for i in range(1, len(seq)):
        if seq[i]["type"] == seq[i - 1]["type"]:
            return False

    return True


def pct_distance(a, b):
    if not a or not b:
        return 999

    return abs(a - b) / abs(b)


def is_price_above(price, level):
    return price > level * (
        1.0 + F_BREAK_BUFFER
    )


def is_price_below(price, level):
    return price < level * (
        1.0 - F_BREAK_BUFFER
    )


# ============================================================
# 86.4 VALIDATION
# ============================================================

def validate_864(p1, p2, p3):
    """
    Basic 86.4% relationship check.

    This is deliberately tolerant because the main NDS
    structure is pivot based, not Fibonacci based.
    """

    try:

        a = abs(
            p2["price"] - p1["price"]
        )

        b = abs(
            p3["price"] - p2["price"]
        )

        if a <= 0:
            return False

        ratio = b / a

        return (
            abs(ratio - FINAL_864_MULTIPLIER)
            <= HOOK_864_TOL
        )

    except Exception:
        return False


# ============================================================
# M30 ACTIVE POSITIVE HOOK
# ============================================================

def detect_positive_m30_hook(
    df,
    pivots,
):
    """
    Positive Hook:

        H1 -> L1 -> H2 -> L2 -> H3

    H3 is the confirmed third peak.

    After H3:
        M1 positive 123F -> SHORT

    Window closes at next M30 LOW.
    """

    if len(pivots) < 5:
        return None

    # Check every possible recent 5-pivot structure.
    for i in range(
        len(pivots) - 5,
        -1,
        -1
    ):

        seq = pivots[i:i + 5]

        types = [p["type"] for p in seq]

        if types != ["H", "L", "H", "L", "H"]:
            continue

        h1, l1, h2, l2, h3 = seq

        # ----------------------------------------------------
        # Structural requirements
        # ----------------------------------------------------

        if h2["price"] <= h1["price"]:
            continue

        if h3["price"] <= h2["price"]:
            continue

        if l2["price"] <= l1["price"]:
            continue

        # Hook range
        hook_range = (
            h3["price"] - l1["price"]
        ) / l1["price"]

        if hook_range < HOOK_MIN_RANGE:
            continue

        # H3 must be confirmed.
        # find_pivots only returns confirmed pivots.
        trigger_time = h3["timestamp"]

        # ----------------------------------------------------
        # Find the next M30 low.
        # This is the boundary of the entry window.
        # ----------------------------------------------------

        boundary = None

        for p in pivots:

            if p["index"] <= h3["index"]:
                continue

            if p["type"] == "L":
                boundary = p
                break

        # If boundary already exists, the window is closed.
        if boundary is not None:
            continue

        return {
            "direction": "POSITIVE",
            "trade_side": "SHORT",

            "p11": h1,
            "p22": l1,
            "p33": h3,

            "second_high": h2,
            "second_low": l2,

            "trigger_time": trigger_time,
            "boundary": None,

            "active": True,
        }

    return None


# ============================================================
# M30 ACTIVE NEGATIVE HOOK
# ============================================================

def detect_negative_m30_hook(
    df,
    pivots,
):
    """
    Negative Hook:

        L1 -> H1 -> L2 -> H2 -> L3

    L3 is the confirmed third trough.

    After L3:
        M1 negative 123F -> LONG

    Window closes at next M30 HIGH.
    """

    if len(pivots) < 5:
        return None

    for i in range(
        len(pivots) - 5,
        -1,
        -1
    ):

        seq = pivots[i:i + 5]

        types = [p["type"] for p in seq]

        if types != ["L", "H", "L", "H", "L"]:
            continue

        l1, h1, l2, h2, l3 = seq

        # ----------------------------------------------------
        # Structural requirements
        # ----------------------------------------------------

        if l2["price"] >= l1["price"]:
            continue

        if l3["price"] >= l2["price"]:
            continue

        if h2["price"] <= h1["price"]:
            continue

        hook_range = (
            h1["price"] - l3["price"]
        ) / h1["price"]

        if hook_range < HOOK_MIN_RANGE:
            continue

        trigger_time = l3["timestamp"]

        # ----------------------------------------------------
        # Find next M30 high.
        # ----------------------------------------------------

        boundary = None

        for p in pivots:

            if p["index"] <= l3["index"]:
                continue

            if p["type"] == "H":
                boundary = p
                break

        if boundary is not None:
            continue

        return {
            "direction": "NEGATIVE",
            "trade_side": "LONG",

            "p11": l1,
            "p22": h1,
            "p33": l3,

            "second_low": l2,
            "second_high": h2,

            "trigger_time": trigger_time,
            "boundary": None,

            "active": True,
        }

    return None


# ============================================================
# M30 HOOK DETECTOR
# ============================================================

def detect_active_m30_hook(df):
    pivots = find_pivots(
        df,
        left=PIVOT_LEFT,
        right=PIVOT_RIGHT,
        min_swing=MIN_SWING_PCT,
    )

    if len(pivots) < 5:
        return None

    positive = detect_positive_m30_hook(
        df,
        pivots,
    )

    negative = detect_negative_m30_hook(
        df,
        pivots,
    )

    if positive and negative:

        if (
            positive["trigger_time"]
            >= negative["trigger_time"]
        ):
            return positive

        return negative

    return positive or negative


# ============================================================
# M1 POSITIVE 123F
# ============================================================

def find_positive_123f(
    df,
    start_time,
    end_time=None,
):
    """
    Positive 123:

        L1 -> H2 -> L3

    Requirements:
        L3 > L1

    F:
        candle closes above H2

    Trade mapping in NDS:
        positive 123F -> SHORT
    """

    work = df[
        df["timestamp"] >= start_time
    ].copy()

    if end_time is not None:
        work = work[
            work["timestamp"] < end_time
        ]

    if len(work) < 10:
        return None

    pivots = find_pivots(
        work,
        left=PIVOT_LEFT,
        right=PIVOT_RIGHT,
        min_swing=M1_123_MIN_SWING,
    )

    if len(pivots) < 3:
        return None

    # --------------------------------------------------------
    # Search from oldest to newest.
    # First confirmed valid F is returned.
    # --------------------------------------------------------

    for i in range(
        len(pivots) - 2
    ):

        p1 = pivots[i]
        p2 = pivots[i + 1]
        p3 = pivots[i + 2]

        if (
            p1["type"] != "L"
            or p2["type"] != "H"
            or p3["type"] != "L"
        ):
            continue

        # L3 must be higher than L1
        if p3["price"] <= p1["price"]:
            continue

        # reasonable formation size
        if (
            pct_distance(
                p3["price"],
                p1["price"],
            )
            > M1_123_MAX_DISTANCE_PCT
        ):
            continue

        # ----------------------------------------------------
        # F = close above point 2
        # ----------------------------------------------------

        after_p3 = work[
            work["timestamp"]
            > p3["timestamp"]
        ]

        for _, candle in after_p3.iterrows():

            close = float(candle["close"])

            if is_price_above(
                close,
                p2["price"],
            ):

                return {
                    "type": "POSITIVE_123F",

                    "p1": p1,
                    "p2": p2,
                    "p3": p3,

                    "f_time": float(
                        candle["timestamp"]
                    ),

                    "f_price": close,

                    "side": "SHORT",
                }

    return None


# ============================================================
# M1 NEGATIVE 123F
# ============================================================

def find_negative_123f(
    df,
    start_time,
    end_time=None,
):
    """
    Negative 123:

        H1 -> L2 -> H3

    Requirements:
        H3 < H1

    F:
        candle closes below L2

    Trade mapping:
        negative 123F -> LONG
    """

    work = df[
        df["timestamp"] >= start_time
    ].copy()

    if end_time is not None:
        work = work[
            work["timestamp"] < end_time
        ]

    if len(work) < 10:
        return None

    pivots = find_pivots(
        work,
        left=PIVOT_LEFT,
        right=PIVOT_RIGHT,
        min_swing=M1_123_MIN_SWING,
    )

    if len(pivots) < 3:
        return None

    for i in range(
        len(pivots) - 2
    ):

        p1 = pivots[i]
        p2 = pivots[i + 1]
        p3 = pivots[i + 2]

        if (
            p1["type"] != "H"
            or p2["type"] != "L"
            or p3["type"] != "H"
        ):
            continue

        # H3 must be lower than H1
        if p3["price"] >= p1["price"]:
            continue

        if (
            pct_distance(
                p3["price"],
                p1["price"],
            )
            > M1_123_MAX_DISTANCE_PCT
        ):
            continue

        # ----------------------------------------------------
        # F = close below point 2
        # ----------------------------------------------------

        after_p3 = work[
            work["timestamp"]
            > p3["timestamp"]
        ]

        for _, candle in after_p3.iterrows():

            close = float(candle["close"])

            if is_price_below(
                close,
                p2["price"],
            ):

                return {
                    "type": "NEGATIVE_123F",

                    "p1": p1,
                    "p2": p2,
                    "p3": p3,

                    "f_time": float(
                        candle["timestamp"]
                    ),

                    "f_price": close,

                    "side": "LONG",
                }

    return None


# ============================================================
# FIND M1 ENTRY
# ============================================================

def find_m1_entry(
    m1_df,
    hook,
):
    start_time = hook["trigger_time"]

    boundary = hook.get("boundary")

    end_time = None

    if boundary:
        end_time = boundary["timestamp"]

    if hook["trade_side"] == "SHORT":

        return find_positive_123f(
            m1_df,
            start_time,
            end_time,
        )

    return find_negative_123f(
        m1_df,
        start_time,
        end_time,
    )


# ============================================================
# PRICE
# ============================================================

def get_current_price(symbol):
    try:

        df = fetch_candles(
            symbol,
            M1_INTERVAL,
            5,
        )

        return float(
            df.iloc[-1]["close"]
        )

    except Exception:
        return None


# ============================================================
# SL / TP
# ============================================================

def build_trade_levels(
    side,
    entry,
    setup,
):
    """
    Structural SL based on the 123 pattern.

    SHORT:
        SL above 123 point 2

    LONG:
        SL below 123 point 2
    """

    p1 = setup["p1"]
    p2 = setup["p2"]
    p3 = setup["p3"]

    if side == "SHORT":

        structure_high = max(
            p1["price"],
            p2["price"],
            p3["price"],
        )

        sl = (
            structure_high
            * (1.0 + SL_BUFFER_PCT)
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp1 = entry - risk * TP1_R
        tp2 = entry - risk * TP2_R
        tp3 = entry - risk * TP3_R

        final_tp = (
            entry - risk
            * FINAL_864_MULTIPLIER
        )

        return {
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "final_tp": final_tp,
            "risk": risk,
        }

    else:

        structure_low = min(
            p1["price"],
            p2["price"],
            p3["price"],
        )

        sl = (
            structure_low
            * (1.0 - SL_BUFFER_PCT)
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp1 = entry + risk * TP1_R
        tp2 = entry + risk * TP2_R
        tp3 = entry + risk * TP3_R

        final_tp = (
            entry + risk
            * FINAL_864_MULTIPLIER
        )

        return {
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "final_tp": final_tp,
            "risk": risk,
        }


# ============================================================
# CHART
# ============================================================

def save_signal_chart(
    symbol,
    m1_df,
    m30_df,
    hook,
    setup,
    levels,
):
    try:

        # ----------------------------------------------------
        # Focus around signal
        # ----------------------------------------------------

        signal_time = setup["f_time"]

        m1 = m1_df.copy()

        m1 = m1[
            m1["timestamp"]
            >= signal_time - 30 * 60
        ].copy()

        if len(m1) > 150:
            m1 = m1.tail(150)

        if len(m1) < 5:
            return None

        fig, ax = plt.subplots(
            figsize=(15, 8)
        )

        x = np.arange(len(m1))

        # Candles
        candle_width = 0.65

        for i, row in m1.iterrows():

            idx = m1.index.get_loc(i)

            op = row["open"]
            hi = row["high"]
            lo = row["low"]
            cl = row["close"]

            ax.vlines(
                idx,
                lo,
                hi,
                linewidth=1,
            )

            if cl >= op:
                bottom = op
                height = cl - op
            else:
                bottom = cl
                height = op - cl

            if height == 0:
                height = max(
                    abs(cl) * 0.00005,
                    1e-12,
                )

            rect = plt.Rectangle(
                (
                    idx - candle_width / 2,
                    bottom,
                ),
                candle_width,
                height,
                fill=False,
                linewidth=1,
            )

            ax.add_patch(rect)

        # ----------------------------------------------------
        # 123 points
        # ----------------------------------------------------

        p1 = setup["p1"]
        p2 = setup["p2"]
        p3 = setup["p3"]

        def map_time(ts):
            arr = m1["timestamp"].values

            if len(arr) == 0:
                return None

            return int(
                np.argmin(
                    np.abs(arr - ts)
                )
            )

        i1 = map_time(
            p1["timestamp"]
        )

        i2 = map_time(
            p2["timestamp"]
        )

        i3 = map_time(
            p3["timestamp"]
        )

        iF = map_time(
            setup["f_time"]
        )

        ax.plot(
            [i1, i2, i3],
            [
                p1["price"],
                p2["price"],
                p3["price"],
            ],
            linewidth=2,
        )

        ax.scatter(
            [i1, i2, i3],
            [
                p1["price"],
                p2["price"],
                p3["price"],
            ],
            s=60,
            zorder=5,
        )

        ax.annotate(
            "1",
            (i1, p1["price"]),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=11,
        )

        ax.annotate(
            "2",
            (i2, p2["price"]),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=11,
        )

        ax.annotate(
            "3",
            (i3, p3["price"]),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=11,
        )

        # ----------------------------------------------------
        # F
        # ----------------------------------------------------

        ax.scatter(
            iF,
            setup["f_price"],
            s=100,
            marker="*",
            zorder=10,
        )

        ax.annotate(
            "F",
            (
                iF,
                setup["f_price"],
            ),
            xytext=(0, 15),
            textcoords="offset points",
            ha="center",
            fontsize=13,
            fontweight="bold",
        )

        # ----------------------------------------------------
        # Entry
        # ----------------------------------------------------

        entry = levels["entry"]
        sl = levels["sl"]

        ax.axhline(
            entry,
            linestyle="--",
            linewidth=1.5,
        )

        ax.axhline(
            sl,
            linestyle="--",
            linewidth=1.5,
        )

        ax.axhline(
            levels["tp1"],
            linestyle=":",
            linewidth=1,
        )

        ax.axhline(
            levels["tp2"],
            linestyle=":",
            linewidth=1,
        )

        ax.axhline(
            levels["tp3"],
            linestyle=":",
            linewidth=1,
        )

        ax.annotate(
            f"ENTRY {entry:.8g}",
            (
                len(m1) - 1,
                entry,
            ),
            xytext=(-100, 0),
            textcoords="offset points",
        )

        ax.annotate(
            f"SL {sl:.8g}",
            (
                len(m1) - 1,
                sl,
            ),
            xytext=(-100, 0),
            textcoords="offset points",
        )

        # ----------------------------------------------------
        # M30 Hook reference
        # ----------------------------------------------------

        hook_price = hook["p33"]["price"]

        ax.axhline(
            hook_price,
            linestyle="-.",
            linewidth=1,
        )

        ax.annotate(
            "M30 33",
            (
                len(m1) - 1,
                hook_price,
            ),
            xytext=(-100, 0),
            textcoords="offset points",
        )

        ax.set_title(
            f"NDS M30 -> M1 | {symbol} | "
            f"{hook['direction']} Hook -> "
            f"{setup['type']} -> {setup['side']}"
        )

        ax.set_xlabel(
            "M1 candles"
        )

        ax.set_ylabel(
            "Price"
        )

        ax.grid(
            alpha=0.2
        )

        fig.tight_layout()

        safe_symbol = (
            symbol.replace("/", "_")
            .replace(":", "_")
        )

        path = os.path.join(
            CHART_DIR,
            f"{safe_symbol}_{int(signal_time)}.png"
        )

        fig.savefig(
            path,
            dpi=150,
            bbox_inches="tight",
        )

        plt.close(fig)

        return path

    except Exception as e:
        print(
            "Chart error:",
            symbol,
            e,
        )

        return None


# ============================================================
# INSERT SIGNAL
# ============================================================

def insert_signal(
    conn,
    symbol,
    side,
    hook,
    setup,
    levels,
    chart_path,
):
    cur = conn.cursor()

    signal_time = setup["f_time"]

    cur.execute("""
        INSERT INTO signals (
            symbol,
            side,
            signal_time,

            entry,
            sl,

            tp1,
            tp2,
            tp3,

            final_tp,

            status,

            m30_direction,

            m30_11_time,
            m30_22_time,
            m30_33_time,

            m30_33_price,

            m1_p1_time,
            m1_p1_price,

            m1_p2_time,
            m1_p2_price,

            m1_p3_time,
            m1_p3_price,

            m1_f_time,
            m1_f_price,

            chart_path
        )
        VALUES (
            ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?,
            'OPEN',
            ?,
            ?, ?, ?,
            ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?, ?,
            ?
        )
    """, (
        symbol,
        side,
        signal_time,

        levels["entry"],
        levels["sl"],

        levels["tp1"],
        levels["tp2"],
        levels["tp3"],

        levels["final_tp"],

        hook["direction"],

        hook["p11"]["timestamp"],
        hook["p22"]["timestamp"],
        hook["p33"]["timestamp"],

        hook["p33"]["price"],

        setup["p1"]["timestamp"],
        setup["p1"]["price"],

        setup["p2"]["timestamp"],
        setup["p2"]["price"],

        setup["p3"]["timestamp"],
        setup["p3"]["price"],

        setup["f_time"],
        setup["f_price"],

        chart_path,
    ))

    conn.commit()

    return cur.lastrowid


# ============================================================
# FORMAT SIGNAL
# ============================================================

def signal_message(
    symbol,
    side,
    hook,
    setup,
    levels,
):
    emoji = "🟢" if side == "LONG" else "🔴"

    return (
        f"🚨 <b>NDS M30 → M1 SIGNAL</b>\n\n"
        f"{emoji} <b>{side}</b>\n"
        f"<b>{symbol}</b>\n\n"

        f"<b>M30 Hook:</b> "
        f"{hook['direction']}\n"

        f"<b>M30 33:</b> "
        f"{hook['p33']['price']:.8g}\n\n"

        f"<b>M1 Pattern:</b> "
        f"{setup['type']}\n"

        f"<b>F:</b> "
        f"{setup['f_price']:.8g}\n\n"

        f"<b>Entry:</b> "
        f"{levels['entry']:.8g}\n"

        f"<b>SL:</b> "
        f"{levels['sl']:.8g}\n"

        f"<b>TP1:</b> "
        f"{levels['tp1']:.8g}\n"

        f"<b>TP2:</b> "
        f"{levels['tp2']:.8g}\n"

        f"<b>TP3:</b> "
        f"{levels['tp3']:.8g}\n\n"

        f"<b>Window:</b> "
        f"M30 33 → opposite 3\n\n"

        f"<i>PAPER ONLY</i>"
    )


# ============================================================
# OPEN TRADE MANAGEMENT
# ============================================================

def calculate_pnl(side, entry, current):
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


def check_trade_exit(
    trade,
    current,
):
    side = trade["side"]

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp1 = (
        float(trade["tp1"])
        if trade["tp1"] is not None
        else None
    )

    tp2 = (
        float(trade["tp2"])
        if trade["tp2"] is not None
        else None
    )

    tp3 = (
        float(trade["tp3"])
        if trade["tp3"] is not None
        else None
    )

    final_tp = (
        float(trade["final_tp"])
        if trade["final_tp"] is not None
        else None
    )

    if side == "LONG":

        if current <= sl:
            return (
                "SL",
                sl,
            )

        if final_tp and current >= final_tp:
            return (
                "FINAL_TP",
                final_tp,
            )

        if tp3 and current >= tp3:
            return (
                "TP3",
                tp3,
            )

        if tp2 and current >= tp2:
            return (
                "TP2",
                tp2,
            )

        if tp1 and current >= tp1:
            return (
                "TP1",
                tp1,
            )

    else:

        if current >= sl:
            return (
                "SL",
                sl,
            )

        if final_tp and current <= final_tp:
            return (
                "FINAL_TP",
                final_tp,
            )

        if tp3 and current <= tp3:
            return (
                "TP3",
                tp3,
            )

        if tp2 and current <= tp2:
            return (
                "TP2",
                tp2,
            )

        if tp1 and current <= tp1:
            return (
                "TP1",
                tp1,
            )

    return None


def close_trade(
    conn,
    trade_id,
    exit_price,
    reason,
):
    row = conn.execute("""
        SELECT *
        FROM signals
        WHERE id = ?
    """, (
        trade_id,
    )).fetchone()

    if not row:
        return

    side = row["side"]
    entry = float(row["entry"])

    pnl = calculate_pnl(
        side,
        entry,
        exit_price,
    )

    conn.execute("""
        UPDATE signals
        SET
            status = 'CLOSED',
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl_pct = ?
        WHERE id = ?
    """, (
        now_utc().timestamp(),
        exit_price,
        reason,
        pnl,
        trade_id,
    ))

    conn.commit()

    emoji = "🟢" if pnl >= 0 else "🔴"

    message = (
        f"📌 <b>NDS TRADE CLOSED</b>\n\n"
        f"{row['symbol']}\n"
        f"{side}\n\n"
        f"Entry: <b>{entry:.8g}</b>\n"
        f"Exit: <b>{exit_price:.8g}</b>\n"
        f"Reason: <b>{reason}</b>\n\n"
        f"{emoji} P/L: <b>{pnl:+.2f}%</b>"
    )

    telegram_send(message)


def manage_open_trades(conn):
    rows = conn.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time ASC
    """).fetchall()

    for trade in rows:

        try:

            current = get_current_price(
                trade["symbol"]
            )

            if current is None:
                continue

            result = check_trade_exit(
                trade,
                current,
            )

            if result:

                reason, exit_price = result

                close_trade(
                    conn,
                    trade["id"],
                    exit_price,
                    reason,
                )

        except Exception as e:

            print(
                "Trade management error:",
                trade["symbol"],
                e,
            )


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def get_open_trades_report(conn):
    rows = conn.execute("""
        SELECT *
        FROM signals
        WHERE status = 'OPEN'
        ORDER BY signal_time DESC
    """).fetchall()

    if not rows:
        return "Open Trades: <b>0</b>"

    lines = [
        f"Open Trades: <b>{len(rows)}</b>"
    ]

    for row in rows:

        current = get_current_price(
            row["symbol"]
        )

        if current is None:
            current = row["entry"]

        pnl = calculate_pnl(
            row["side"],
            float(row["entry"]),
            float(current),
        )

        emoji = "🟢" if row["side"] == "LONG" else "🔴"

        lines.append(
            f"\n{emoji} <b>{row['symbol']}</b> "
            f"{row['side']}\n"
            f"Entry: {row['entry']:.8g}\n"
            f"Current: <b>{current:.8g}</b>\n"
            f"P/L: <b>{pnl:+.2f}%</b>"
        )

    return "\n".join(lines)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_report(conn):
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

    total = int(row["total"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    pnl = float(row["pnl"] or 0)

    return (
        f"Closed: <b>{total}</b>\n"
        f"Wins: <b>{wins}</b>\n"
        f"Losses: <b>{losses}</b>\n"
        f"PnL: <b>{pnl:+.2f}%</b>"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    conn,
    symbol,
):
    try:

        # ----------------------------------------------------
        # M30
        # ----------------------------------------------------

        m30 = fetch_candles(
            symbol,
            M30_INTERVAL,
            M30_COUNT,
        )

        if len(m30) < MIN_M30_CANDLES:
            return None

        hook = detect_active_m30_hook(
            m30
        )

        if not hook:
            return None

        # ----------------------------------------------------
        # M1
        # ----------------------------------------------------

        m1 = fetch_candles(
            symbol,
            M1_INTERVAL,
            M1_COUNT,
        )

        if len(m1) < MIN_M1_CANDLES:
            return None

        # ----------------------------------------------------
        # M1 entry only AFTER M30 33
        # ----------------------------------------------------

        setup = find_m1_entry(
            m1,
            hook,
        )

        if not setup:
            return None

        # ----------------------------------------------------
        # Cooldown
        # ----------------------------------------------------

        side = setup["side"]

        if recent_signal_exists(
            conn,
            symbol,
            side,
            SIGNAL_COOLDOWN_MIN,
        ):
            return None

        # ----------------------------------------------------
        # Maximum open trades
        # ----------------------------------------------------

        if (
            count_open_trades(conn)
            >= MAX_OPEN_TRADES
        ):
            return None

        # ----------------------------------------------------
        # Levels
        # ----------------------------------------------------

        levels = build_trade_levels(
            side,
            setup["f_price"],
            setup,
        )

        if not levels:
            return None

        # ----------------------------------------------------
        # Chart
        # ----------------------------------------------------

        chart_path = save_signal_chart(
            symbol,
            m1,
            m30,
            hook,
            setup,
            levels,
        )

        # ----------------------------------------------------
        # DB
        # ----------------------------------------------------

        signal_id = insert_signal(
            conn,
            symbol,
            side,
            hook,
            setup,
            levels,
            chart_path,
        )

        # ----------------------------------------------------
        # Telegram
        # ----------------------------------------------------

        message = signal_message(
            symbol,
            side,
            hook,
            setup,
            levels,
        )

        telegram_send(message)

        if chart_path:
            telegram_send_photo(
                chart_path,
                caption=(
                    f"NDS {symbol} "
                    f"{side} | "
                    f"M30 {hook['direction']} "
                    f"Hook → M1 123F"
                ),
            )

        print(
            f"NEW SIGNAL #{signal_id}: "
            f"{symbol} {side} "
            f"F={setup['f_price']}"
        )

        return {
            "symbol": symbol,
            "side": side,
            "signal_id": signal_id,
        }

    except Exception as e:

        print(
            f"Process error {symbol}:",
            e,
        )

        traceback.print_exc()

        return None


# ============================================================
# RUN REPORT
# ============================================================

def save_run(
    conn,
    scanned,
    new_signals,
):
    closed_row = conn.execute("""
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

    open_count = count_open_trades(
        conn
    )

    conn.execute("""
        INSERT INTO scanner_runs (
            run_time,
            scanned,
            new_signals,
            open_trades,
            closed_trades,
            wins,
            losses,
            pnl_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        now_utc().timestamp(),
        scanned,
        new_signals,
        open_count,
        int(
            closed_row["total"] or 0
        ),
        int(
            closed_row["wins"] or 0
        ),
        int(
            closed_row["losses"] or 0
        ),
        float(
            closed_row["pnl"] or 0
        ),
    ))

    conn.commit()


# ============================================================
# TELEGRAM REPORT
# ============================================================

def send_report(
    conn,
    scanned,
    new_signals,
):
    open_count = count_open_trades(
        conn
    )

    report = (
        f"📊 <b>NDS M30 → M1 REPORT</b>\n\n"
        f"Assets scanned: <b>{scanned}</b>\n"
        f"New signals: <b>{new_signals}</b>\n"
        f"Open trades: <b>{open_count}</b>\n\n"
        f"{performance_report(conn)}"
    )

    telegram_send(report)


# ============================================================
# RUN ONCE
# ============================================================

def run_once():
    print()
    print("=" * 70)
    print(
        f"NDS M30 -> M1 SCANNER "
        f"v{VERSION}"
    )
    print(
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )
    print("=" * 70)

    conn = db_connect()

    # --------------------------------------------------------
    # Manage old trades first
    # --------------------------------------------------------

    manage_open_trades(
        conn
    )

    scanned = 0
    new_signals = 0

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for symbol in SYMBOLS:

        scanned += 1

        print(
            f"[{scanned}/{len(SYMBOLS)}] "
            f"{symbol}"
        )

        result = process_symbol(
            conn,
            symbol,
        )

        if result:
            new_signals += 1

    # --------------------------------------------------------
    # Save run
    # --------------------------------------------------------

    save_run(
        conn,
        scanned,
        new_signals,
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    send_report(
        conn,
        scanned,
        new_signals,
    )

    conn.close()

    print()
    print(
        f"Scanned: {scanned}"
    )

    print(
        f"New signals: {new_signals}"
    )

    print("=" * 70)


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    init_db()

    print(
        f"NDS M30 -> M1 Scanner "
        f"v{VERSION}"
    )

    print(
        "PAPER_ONLY =",
        PAPER_ONLY,
    )

    if not PAPER_ONLY:
        raise RuntimeError(
            "This scanner is PAPER ONLY. "
            "PAPER_ONLY must remain True."
        )

    while True:

        started = time.time()

        try:
            run_once()

        except KeyboardInterrupt:
            print(
                "\nScanner stopped."
            )
            break

        except Exception as e:

            print(
                "FATAL SCANNER ERROR:",
                e,
            )

            traceback.print_exc()

            telegram_send(
                "🚨 <b>NDS SCANNER ERROR</b>\n\n"
                f"<code>{str(e)[:1500]}</code>\n\n"
                f"Version: {VERSION}"
            )

        elapsed = time.time() - started

        sleep_for = max(
            5,
            SCAN_INTERVAL_SECONDS - elapsed,
        )

        print(
            f"Next scan in "
            f"{sleep_for:.1f}s"
        )

        time.sleep(
            sleep_for
        )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
