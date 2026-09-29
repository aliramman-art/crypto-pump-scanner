# ============================================================
# NDS M30 ONLY LIVE SCANNER
# VERSION 4.6.0
# ============================================================
#
# PAPER ONLY
#
# NO M1
# NO 123F
# NO REAL ORDERS
#
# POSITIVE / SHORT:
#
# START -> H1 -> L1 -> H2 -> L2 -> H3
#
# START = lowest point of the complete Hook
# H2 > H1
# L2 < L1
# H3 > H2
# H3 = confirmation
#
# TP = 86.4% retracement from H3 toward START
#
# NEGATIVE / LONG:
#
# START -> L1 -> H1 -> L2 -> H2 -> L3
#
# START = highest point of the complete Hook
# L2 < L1
# H2 > H1
# L3 < L2
# L3 = confirmation
#
# TP = 86.4% retracement from L3 toward START
#
# PAPER TRADE:
#
# SHORT:
# Entry = H3
# TP    = 86.4%
# SL    = Entry + 0.5 * TP_DISTANCE
#
# LONG:
# Entry = L3
# TP    = 86.4%
# SL    = Entry - 0.5 * TP_DISTANCE
#
# TP ALREADY TOUCHED:
# If TP was touched after H3/L3 confirmation,
# the Hook is rejected and no new trade is opened.
#
# ============================================================

import os
import time
import sqlite3
import math
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np
import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.patches import Rectangle


# ============================================================
# CONFIG
# ============================================================

VERSION = "4.6.0"

DB_FILE = os.getenv(
    "NDS_DB_FILE",
    "nds_m30_m1_v44.db"
)

CHART_DIR = os.getenv(
    "NDS_CHART_DIR",
    "nds_charts"
)

BASE_URL = os.getenv(
    "KRAKEN_BASE_URL",
    "https://futures.kraken.com/api/charts/v1/trade"
)

INTERVAL = "30m"

M30_COUNT = int(
    os.getenv("M30_COUNT", "320")
)

PIVOT_LEFT = int(
    os.getenv("PIVOT_LEFT", "2")
)

PIVOT_RIGHT = int(
    os.getenv("PIVOT_RIGHT", "2")
)

MIN_SWING_PCT = float(
    os.getenv("MIN_SWING_PCT", "0.0005")
)

TP_RETRACE = 0.864

MIN_TP_DISTANCE_PCT = float(
    os.getenv("MIN_TP_DISTANCE_PCT", "0.30")
)

MAX_TP_DISTANCE_PCT = float(
    os.getenv("MAX_TP_DISTANCE_PCT", "5.00")
)

SL_DISTANCE_MULTIPLIER = 0.50

REQUEST_TIMEOUT = int(
    os.getenv("REQUEST_TIMEOUT", "20")
)

CHART_CANDLES = int(
    os.getenv("CHART_CANDLES", "180")
)

MAX_CHARTS_PER_SCAN = int(
    os.getenv("MAX_CHARTS_PER_SCAN", "10")
)

REPORT_OPEN_TRADES = True

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN")
    or os.getenv("TELEGRAM_TOKEN")
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID")
    or os.getenv("TELEGRAM_CHAT")
)


# ============================================================
# DEFAULT ASSETS
# ============================================================

DEFAULT_SYMBOLS = [
    "PI_XBTUSD",
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_AVAXUSD",
    "PF_LINKUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",
    "PF_BCHUSD",
    "PF_TRXUSD",
    "PF_ATOMUSD",
    "PF_NEARUSD",
    "PF_FILUSD",
    "PF_ALGOUSD",
    "PF_APTUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_SUIUSD",
    "PF_INJUSD",
    "PF_SEIUSD",
    "PF_TIAUSD",
    "PF_PEPEUSD",
    "PF_WIFUSD",
    "PF_FLOKIUSD",
    "PF_BONKUSD",
    "PF_SHIBUSD",
    "PF_LDOUSD",
    "PF_AAVEUSD",
    "PF_UNIUSD",
    "PF_MKRUSD",
    "PF_ETCUSD",
    "PF_XLMUSD",
    "PF_XTZUSD",
    "PF_HBARUSD",
    "PF_EOSUSD",
    "PF_RUNEUSD",
]


def get_symbols():
    raw = os.getenv("TARGET_SYMBOLS", "").strip()

    if not raw:
        return DEFAULT_SYMBOLS

    return [
        x.strip()
        for x in raw.split(",")
        if x.strip()
    ]


SYMBOLS = get_symbols()


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

DIAG = {
    "requests": 0,
    "data_ok": 0,
    "empty": 0,
    "short_data": 0,
    "errors": 0,

    "pivot_highs": 0,
    "pivot_lows": 0,
    "alternating_pivots": 0,
    "six_point_sequences": 0,

    "positive_candidates": 0,
    "positive_valid": 0,
    "positive_start_rejected": 0,
    "positive_h2_rejected": 0,
    "positive_l2_rejected": 0,
    "positive_h3_rejected": 0,

    "negative_candidates": 0,
    "negative_valid": 0,
    "negative_start_rejected": 0,
    "negative_l2_rejected": 0,
    "negative_h2_rejected": 0,
    "negative_l3_rejected": 0,

    "tp_distance_valid": 0,
    "tp_distance_rejected": 0,
    "tp_already_touched": 0,

    "confirmed_hooks": 0,
    "new_signals": 0,
    "charts": 0,

    "opened_trades": 0,
    "open_trades": 0,
    "tp_hits": 0,
    "sl_hits": 0,
}


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_iso():
    return utc_now().isoformat()


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except Exception:
        return default


def fmt_price(value):
    value = safe_float(value)

    if value is None:
        return "N/A"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:.3f}"

    if abs(value) >= 1:
        return f"{value:.4f}"

    if abs(value) >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def fmt_pct(value):
    value = safe_float(value, 0.0)
    return f"{value:+.2f}%"


def price_pct(entry, price, direction):
    entry = safe_float(entry)
    price = safe_float(price)

    if entry is None or price is None or entry == 0:
        return 0.0

    if direction == "SHORT":
        return ((entry - price) / entry) * 100.0

    return ((price - entry) / entry) * 100.0


def target_pct(entry, target):
    entry = safe_float(entry)
    target = safe_float(target)

    if entry is None or target is None or entry == 0:
        return 0.0

    return abs(target - entry) / entry * 100.0


def stop_pct(entry, sl):
    entry = safe_float(entry)
    sl = safe_float(sl)

    if entry is None or sl is None or entry == 0:
        return 0.0

    return abs(sl - entry) / entry * 100.0


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)

    conn.execute("""
        PRAGMA journal_mode=WAL
    """)

    return conn


def init_db():
    os.makedirs(CHART_DIR, exist_ok=True)

    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS hooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,

            start_time TEXT NOT NULL,
            h1_time TEXT,
            l1_time TEXT,
            h2_time TEXT,
            l2_time TEXT,
            confirm_time TEXT NOT NULL,

            start_price REAL NOT NULL,
            h1_price REAL,
            l1_price REAL,
            h2_price REAL,
            l2_price REAL,
            confirm_price REAL NOT NULL,

            tp REAL NOT NULL,
            sl REAL NOT NULL,

            tp_distance_pct REAL NOT NULL,

            status TEXT DEFAULT 'NEW',

            created_at TEXT NOT NULL,

            UNIQUE (
                symbol,
                direction,
                start_time,
                confirm_time
            )
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            hook_id INTEGER,

            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,

            entry REAL NOT NULL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,

            entry_time TEXT NOT NULL,

            current_price REAL,
            current_pct REAL,

            tp_pct REAL,
            sl_pct REAL,

            status TEXT DEFAULT 'OPEN',

            exit_price REAL,
            exit_time TEXT,

            pnl_pct REAL,

            last_update TEXT NOT NULL
        )
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_hooks_symbol
        ON hooks(symbol)
    """)

    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON paper_trades(status)
    """)

    conn.commit()
    conn.close()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_message(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=REQUEST_TIMEOUT,
        )

        return response.ok

    except Exception:
        return False


def telegram_send_photo(path, caption=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    if not os.path.exists(path):
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
                    "caption": caption or "",
                    "parse_mode": "HTML",
                },
                files={
                    "photo": photo
                },
                timeout=REQUEST_TIMEOUT,
            )

        return response.ok

    except Exception:
        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def parse_candle_response(payload):
    rows = None

    if isinstance(payload, dict):

        if "candles" in payload:
            rows = payload["candles"]

        elif "data" in payload:
            data = payload["data"]

            if isinstance(data, dict):
                rows = (
                    data.get("candles")
                    or data.get("data")
                    or data.get("result")
                )

            elif isinstance(data, list):
                rows = data

        elif "result" in payload:
            result = payload["result"]

            if isinstance(result, dict):
                rows = (
                    result.get("candles")
                    or result.get("data")
                )

            elif isinstance(result, list):
                rows = result

    elif isinstance(payload, list):
        rows = payload

    if not rows:
        return pd.DataFrame()

    parsed = []

    for row in rows:

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

            else:
                if len(row) < 5:
                    continue

                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]

                v = row[5] if len(row) > 5 else 0

            ts = safe_float(ts)

            if ts is None:
                continue

            # Kraken may return seconds or milliseconds.
            if ts > 10_000_000_000:
                ts = ts / 1000.0

            parsed.append({
                "timestamp": ts,
                "open": safe_float(o),
                "high": safe_float(h),
                "low": safe_float(l),
                "close": safe_float(c),
                "volume": safe_float(v, 0.0),
            })

        except Exception:
            continue

    if not parsed:
        return pd.DataFrame()

    df = pd.DataFrame(parsed)

    df = df.dropna(
        subset=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.sort_values("timestamp")
    df = df.drop_duplicates("timestamp")

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True,
    )

    df = df.reset_index(drop=True)

    return df


def fetch_m30(symbol):
    DIAG["requests"] += 1

    url = (
        f"{BASE_URL}/{symbol}/{INTERVAL}"
    )

    try:
        response = requests.get(
            url,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        payload = response.json()

        df = parse_candle_response(payload)

        if df.empty:
            DIAG["empty"] += 1
            return pd.DataFrame()

        # Remove currently forming candle.
        if len(df) >= 2:
            df = df.iloc[:-1].copy()

        if len(df) < M30_COUNT:
            DIAG["short_data"] += 1
            return df.reset_index(drop=True)

        df = df.tail(M30_COUNT).reset_index(drop=True)

        DIAG["data_ok"] += 1

        return df

    except Exception:
        DIAG["errors"] += 1
        return pd.DataFrame()


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):
    highs = []
    lows = []

    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    if len(df) < left + right + 1:
        return highs, lows

    for i in range(left, len(df) - right):

        high = float(df.iloc[i]["high"])
        low = float(df.iloc[i]["low"])

        left_highs = [
            float(x)
            for x in df.iloc[
                i - left:i
            ]["high"]
        ]

        right_highs = [
            float(x)
            for x in df.iloc[
                i + 1:i + right + 1
            ]["high"]
        ]

        left_lows = [
            float(x)
            for x in df.iloc[
                i - left:i
            ]["low"]
        ]

        right_lows = [
            float(x)
            for x in df.iloc[
                i + 1:i + right + 1
            ]["low"]
        ]

        if (
            high >= max(left_highs)
            and high >= max(right_highs)
            and (
                high > max(
                    left_highs + right_highs
                )
                or (
                    high == max(
                        left_highs + right_highs
                    )
                    and i > 0
                )
            )
        ):
            highs.append({
                "index": i,
                "price": high,
                "time": df.iloc[i]["datetime"],
                "type": "H",
            })

        if (
            low <= min(left_lows)
            and low <= min(right_lows)
            and (
                low < min(
                    left_lows + right_lows
                )
                or (
                    low == min(
                        left_lows + right_lows
                    )
                    and i > 0
                )
            )
        ):
            lows.append({
                "index": i,
                "price": low,
                "time": df.iloc[i]["datetime"],
                "type": "L",
            })

    DIAG["pivot_highs"] += len(highs)
    DIAG["pivot_lows"] += len(lows)

    return highs, lows


# ============================================================
# ALTERNATING PIVOTS
# ============================================================

def build_alternating_pivots(highs, lows):
    pivots = sorted(
        highs + lows,
        key=lambda x: x["index"]
    )

    if not pivots:
        return []

    result = []

    for p in pivots:

        if not result:
            result.append(p)
            continue

        prev = result[-1]

        if p["type"] != prev["type"]:
            result.append(p)
            continue

        # Same type:
        # retain the more extreme pivot.
        if p["type"] == "H":
            if p["price"] >= prev["price"]:
                result[-1] = p

        else:
            if p["price"] <= prev["price"]:
                result[-1] = p

    filtered = []

    for p in result:

        if not filtered:
            filtered.append(p)
            continue

        prev = filtered[-1]

        if prev["price"] == 0:
            continue

        swing_pct = (
            abs(p["price"] - prev["price"])
            / abs(prev["price"])
        )

        if swing_pct >= MIN_SWING_PCT:
            filtered.append(p)

    DIAG["alternating_pivots"] += len(filtered)

    return filtered


# ============================================================
# START VALIDATION
# ============================================================

def valid_short_start(points):
    """
    Positive Hook:
    START must be the LOWEST point of the whole Hook.
    """

    start = points[0]["price"]

    rest = [
        p["price"]
        for p in points[1:]
    ]

    return start < min(rest)


def valid_long_start(points):
    """
    Negative Hook:
    START must be the HIGHEST point of the whole Hook.
    """

    start = points[0]["price"]

    rest = [
        p["price"]
        for p in points[1:]
    ]

    return start > max(rest)


# ============================================================
# TP / SL
# ============================================================

def calculate_short_levels(start, h3):
    movement = h3 - start

    if movement <= 0:
        return None

    tp = h3 - (
        TP_RETRACE * movement
    )

    distance = abs(h3 - tp)

    sl = h3 + (
        distance * SL_DISTANCE_MULTIPLIER
    )

    return tp, sl, distance


def calculate_long_levels(start, l3):
    movement = start - l3

    if movement <= 0:
        return None

    tp = l3 + (
        TP_RETRACE * movement
    )

    distance = abs(l3 - tp)

    sl = l3 - (
        distance * SL_DISTANCE_MULTIPLIER
    )

    return tp, sl, distance


# ============================================================
# TP DISTANCE FILTER
# ============================================================

def valid_tp_distance(entry, tp):
    distance_pct = target_pct(
        entry,
        tp
    )

    return (
        MIN_TP_DISTANCE_PCT
        <= distance_pct
        <= MAX_TP_DISTANCE_PCT
    )


# ============================================================
# HISTORICAL TP TOUCH
# ============================================================

def short_tp_was_touched(
    df,
    confirm_index,
    tp
):
    start_index = confirm_index + 1

    if start_index >= len(df):
        return False

    future = df.iloc[
        start_index:
    ]

    for _, candle in future.iterrows():

        low = float(candle["low"])

        if low <= tp:
            return True

    return False


def long_tp_was_touched(
    df,
    confirm_index,
    tp
):
    start_index = confirm_index + 1

    if start_index >= len(df):
        return False

    future = df.iloc[
        start_index:
    ]

    for _, candle in future.iterrows():

        high = float(candle["high"])

        if high >= tp:
            return True

    return False


# ============================================================
# HOOK OBJECT
# ============================================================

def make_hook(
    symbol,
    direction,
    points,
    df
):
    confirm = points[-1]

    if direction == "SHORT":

        start = points[0]
        h1 = points[1]
        l1 = points[2]
        h2 = points[3]
        l2 = points[4]
        h3 = points[5]

        levels = calculate_short_levels(
            start["price"],
            h3["price"]
        )

        if not levels:
            return None

        tp, sl, distance = levels

        if not valid_tp_distance(
            h3["price"],
            tp
        ):
            DIAG["tp_distance_rejected"] += 1
            return None

        DIAG["tp_distance_valid"] += 1

        if short_tp_was_touched(
            df,
            h3["index"],
            tp
        ):
            DIAG["tp_already_touched"] += 1
            return None

        return {
            "symbol": symbol,
            "direction": "SHORT",

            "start": start,
            "h1": h1,
            "l1": l1,
            "h2": h2,
            "l2": l2,
            "h3": h3,

            "confirm": h3,

            "start_price": start["price"],
            "h1_price": h1["price"],
            "l1_price": l1["price"],
            "h2_price": h2["price"],
            "l2_price": l2["price"],
            "confirm_price": h3["price"],

            "tp": tp,
            "sl": sl,

            "tp_distance": distance,

            "tp_distance_pct": target_pct(
                h3["price"],
                tp
            ),

            "confirm_index": h3["index"],
        }

    else:

        start = points[0]
        l1 = points[1]
        h1 = points[2]
        l2 = points[3]
        h2 = points[4]
        l3 = points[5]

        levels = calculate_long_levels(
            start["price"],
            l3["price"]
        )

        if not levels:
            return None

        tp, sl, distance = levels

        if not valid_tp_distance(
            l3["price"],
            tp
        ):
            DIAG["tp_distance_rejected"] += 1
            return None

        DIAG["tp_distance_valid"] += 1

        if long_tp_was_touched(
            df,
            l3["index"],
            tp
        ):
            DIAG["tp_already_touched"] += 1
            return None

        return {
            "symbol": symbol,
            "direction": "LONG",

            "start": start,
            "l1": l1,
            "h1": h1,
            "l2": l2,
            "h2": h2,
            "l3": l3,

            "confirm": l3,

            "start_price": start["price"],
            "l1_price": l1["price"],
            "h1_price": h1["price"],
            "l2_price": l2["price"],
            "h2_price": h2["price"],
            "confirm_price": l3["price"],

            "tp": tp,
            "sl": sl,

            "tp_distance": distance,

            "tp_distance_pct": target_pct(
                l3["price"],
                tp
            ),

            "confirm_index": l3["index"],
        }


# ============================================================
# BUILD HOOKS
# ============================================================

def build_hooks(symbol, df):

    highs, lows = detect_pivots(df)

    pivots = build_alternating_pivots(
        highs,
        lows
    )

    hooks = []

    if len(pivots) < 6:
        return hooks

    for i in range(
        0,
        len(pivots) - 5
    ):

        points = pivots[
            i:i + 6
        ]

        if len(points) != 6:
            continue

        DIAG["six_point_sequences"] += 1

        types = [
            p["type"]
            for p in points
        ]

        # ====================================================
        # POSITIVE / SHORT
        # H1 L1 H2 L2 H3
        # START is independent LOW point.
        # ====================================================

        if (
            types[1:] ==
            ["H", "L", "H", "L", "H"]
        ):

            DIAG["positive_candidates"] += 1

            if not valid_short_start(points):
                DIAG["positive_start_rejected"] += 1
            else:

                h1 = points[1]
                l1 = points[2]
                h2 = points[3]
                l2 = points[4]
                h3 = points[5]

                if h2["price"] <= h1["price"]:
                    DIAG["positive_h2_rejected"] += 1

                elif l2["price"] >= l1["price"]:
                    DIAG["positive_l2_rejected"] += 1

                elif h3["price"] <= h2["price"]:
                    DIAG["positive_h3_rejected"] += 1

                else:

                    DIAG["positive_valid"] += 1

                    hook = make_hook(
                        symbol,
                        "SHORT",
                        points,
                        df
                    )

                    if hook:
                        hooks.append(hook)

        # ====================================================
        # NEGATIVE / LONG
        # L1 H1 L2 H2 L3
        # START is independent HIGH point.
        # ====================================================

        if (
            types[1:] ==
            ["L", "H", "L", "H", "L"]
        ):

            DIAG["negative_candidates"] += 1

            if not valid_long_start(points):
                DIAG["negative_start_rejected"] += 1

            else:

                l1 = points[1]
                h1 = points[2]
                l2 = points[3]
                h2 = points[4]
                l3 = points[5]

                if l2["price"] >= l1["price"]:
                    DIAG["negative_l2_rejected"] += 1

                elif h2["price"] <= h1["price"]:
                    DIAG["negative_h2_rejected"] += 1

                elif l3["price"] >= l2["price"]:
                    DIAG["negative_l3_rejected"] += 1

                else:

                    DIAG["negative_valid"] += 1

                    hook = make_hook(
                        symbol,
                        "LONG",
                        points,
                        df
                    )

                    if hook:
                        hooks.append(hook)

    return hooks


# ============================================================
# HOOK DATABASE
# ============================================================

def hook_exists(hook):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM hooks
        WHERE symbol = ?
          AND direction = ?
          AND start_time = ?
          AND confirm_time = ?
        LIMIT 1
        """,
        (
            hook["symbol"],
            hook["direction"],
            hook["start"]["time"].isoformat(),
            hook["confirm"]["time"].isoformat(),
        )
    ).fetchone()

    conn.close()

    return row is not None


def save_hook(hook):
    conn = db_connect()

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO hooks (
            symbol,
            direction,

            start_time,
            h1_time,
            l1_time,
            h2_time,
            l2_time,
            confirm_time,

            start_price,
            h1_price,
            l1_price,
            h2_price,
            l2_price,
            confirm_price,

            tp,
            sl,
            tp_distance_pct,

            status,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?,
            ?, ?, ?,
            'NEW', ?
        )
        """,
        (
            hook["symbol"],
            hook["direction"],

            hook["start"]["time"].isoformat(),

            hook["h1"]["time"].isoformat()
            if "h1" in hook else None,

            hook["l1"]["time"].isoformat()
            if "l1" in hook else None,

            hook["h2"]["time"].isoformat()
            if "h2" in hook else None,

            hook["l2"]["time"].isoformat()
            if "l2" in hook else None,

            hook["confirm"]["time"].isoformat(),

            hook["start_price"],
            hook.get("h1_price"),
            hook.get("l1_price"),
            hook.get("h2_price"),
            hook.get("l2_price"),
            hook["confirm_price"],

            hook["tp"],
            hook["sl"],
            hook["tp_distance_pct"],

            utc_iso(),
        )
    )

    conn.commit()

    new_id = cursor.lastrowid

    conn.close()

    return new_id if new_id else None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(df):
    if df.empty:
        return None

    return safe_float(
        df.iloc[-1]["close"]
    )


# ============================================================
# PAPER TRADE
# ============================================================

def open_paper_trade(
    hook_id,
    hook
):
    conn = db_connect()

    # Never open a second trade from the same Hook.
    existing = conn.execute(
        """
        SELECT id
        FROM paper_trades
        WHERE hook_id = ?
        LIMIT 1
        """,
        (hook_id,)
    ).fetchone()

    if existing:
        conn.close()
        return False

    entry = hook["confirm_price"]

    cursor = conn.execute(
        """
        INSERT INTO paper_trades (
            hook_id,
            symbol,
            direction,
            entry,
            tp,
            sl,
            entry_time,
            current_price,
            current_pct,
            tp_pct,
            sl_pct,
            status,
            last_update
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, 'OPEN', ?
        )
        """,
        (
            hook_id,
            hook["symbol"],
            hook["direction"],

            entry,
            hook["tp"],
            hook["sl"],

            hook["confirm"]["time"].isoformat(),

            entry,

            0.0,

            target_pct(
                entry,
                hook["tp"]
            ),

            stop_pct(
                entry,
                hook["sl"]
            ),

            utc_iso(),
        )
    )

    conn.commit()

    trade_id = cursor.lastrowid

    conn.close()

    DIAG["opened_trades"] += 1

    return trade_id


# ============================================================
# CHECK OPEN TRADES
# ============================================================

def update_open_trades(price_map):
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT
            id,
            hook_id,
            symbol,
            direction,
            entry,
            tp,
            sl
        FROM paper_trades
        WHERE status = 'OPEN'
        ORDER BY id
        """
    ).fetchall()

    for row in rows:

        (
            trade_id,
            hook_id,
            symbol,
            direction,
            entry,
            tp,
            sl,
        ) = row

        current = price_map.get(symbol)

        if current is None:
            continue

        current_pct = price_pct(
            entry,
            current,
            direction
        )

        tp_pct = target_pct(
            entry,
            tp
        )

        sl_pct = stop_pct(
            entry,
            sl
        )

        exit_reason = None

        # ----------------------------------------------------
        # Conservative assumption:
        # if current price has crossed TP/SL,
        # mark the corresponding level hit.
        # ----------------------------------------------------

        if direction == "SHORT":

            if current <= tp:
                exit_reason = "TP"

            elif current >= sl:
                exit_reason = "SL"

        else:

            if current >= tp:
                exit_reason = "TP"

            elif current <= sl:
                exit_reason = "SL"

        if exit_reason:

            pnl_pct = price_pct(
                entry,
                current,
                direction
            )

            conn.execute(
                """
                UPDATE paper_trades
                SET
                    current_price = ?,
                    current_pct = ?,
                    tp_pct = ?,
                    sl_pct = ?,
                    status = ?,
                    exit_price = ?,
                    exit_time = ?,
                    pnl_pct = ?,
                    last_update = ?
                WHERE id = ?
                """,
                (
                    current,
                    current_pct,
                    tp_pct,
                    sl_pct,

                    exit_reason,

                    current,
                    utc_iso(),

                    pnl_pct,

                    utc_iso(),

                    trade_id,
                )
            )

            if exit_reason == "TP":
                DIAG["tp_hits"] += 1
            else:
                DIAG["sl_hits"] += 1

            send_trade_close_message(
                trade_id=trade_id,
                symbol=symbol,
                direction=direction,
                entry=entry,
                exit_price=current,
                tp=tp,
                sl=sl,
                reason=exit_reason,
                pnl_pct=pnl_pct,
            )

        else:

            conn.execute(
                """
                UPDATE paper_trades
                SET
                    current_price = ?,
                    current_pct = ?,
                    tp_pct = ?,
                    sl_pct = ?,
                    last_update = ?
                WHERE id = ?
                """,
                (
                    current,
                    current_pct,
                    tp_pct,
                    sl_pct,
                    utc_iso(),
                    trade_id,
                )
            )

    conn.commit()
    conn.close()


# ============================================================
# TRADE OPEN MESSAGE
# ============================================================

def send_trade_open_message(
    hook_id,
    hook,
    current
):
    direction = hook["direction"]
    entry = hook["confirm_price"]
    tp = hook["tp"]
    sl = hook["sl"]

    current_pct = price_pct(
        entry,
        current,
        direction
    )

    tp_pct = target_pct(
        entry,
        tp
    )

    sl_pct = stop_pct(
        entry,
        sl
    )

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    text = (
        f"{emoji} <b>{direction} PAPER TRADE</b>\n\n"

        f"<b>{hook['symbol']}</b>\n"

        f"Entry: <b>{fmt_price(entry)}</b>\n"

        f"Current: <b>{fmt_price(current)}</b> "
        f"({fmt_pct(current_pct)})\n\n"

        f"TP 86.4%: <b>{fmt_price(tp)}</b> "
        f"({fmt_pct(tp_pct)})\n"

        f"SL: <b>{fmt_price(sl)}</b> "
        f"({fmt_pct(-sl_pct)})\n\n"

        f"Hook: <b>{direction}</b>\n"
        f"TP distance: <b>{hook['tp_distance_pct']:.2f}%</b>\n"
        f"SL distance: <b>{sl_pct:.2f}%</b>\n\n"

        f"Entry = H3/L3 confirmation\n"
        f"TP = 86.4%\n"
        f"SL = 50% of TP distance\n"
        f"Paper only"
    )

    telegram_send_message(text)


# ============================================================
# TRADE CLOSE MESSAGE
# ============================================================

def send_trade_close_message(
    trade_id,
    symbol,
    direction,
    entry,
    exit_price,
    tp,
    sl,
    reason,
    pnl_pct,
):
    if reason == "TP":
        title = "✅ TP HIT"
    else:
        title = "❌ SL HIT"

    text = (
        f"<b>{title}</b>\n\n"

        f"{symbol} {direction}\n"
        f"Entry: <b>{fmt_price(entry)}</b>\n"
        f"Exit: <b>{fmt_price(exit_price)}</b>\n"
        f"TP: <b>{fmt_price(tp)}</b>\n"
        f"SL: <b>{fmt_price(sl)}</b>\n\n"

        f"PnL: <b>{fmt_pct(pnl_pct)}</b>\n"
        f"Paper trade #{trade_id}"
    )

    telegram_send_message(text)


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT
            id,
            symbol,
            direction,
            entry,
            tp,
            sl,
            current_price,
            current_pct,
            tp_pct,
            sl_pct,
            entry_time
        FROM paper_trades
        WHERE status = 'OPEN'
        ORDER BY entry_time DESC
        """
    ).fetchall()

    conn.close()

    return rows


def send_open_trades_report():
    if not REPORT_OPEN_TRADES:
        return

    rows = get_open_trades()

    DIAG["open_trades"] = len(rows)

    if not rows:
        telegram_send_message(
            "<b>📊 OPEN TRADES</b>\n\n"
            "No open paper trades."
        )
        return

    lines = [
        "<b>📊 OPEN TRADES</b>",
        ""
    ]

    for row in rows:

        (
            trade_id,
            symbol,
            direction,
            entry,
            tp,
            sl,
            current,
            current_pct,
            tp_pct,
            sl_pct,
            entry_time,
        ) = row

        emoji = (
            "🔴"
            if direction == "SHORT"
            else "🟢"
        )

        lines.append(
            f"{emoji} <b>{symbol} {direction}</b>"
        )

        lines.append(
            f"Entry: {fmt_price(entry)}"
        )

        lines.append(
            f"Current: {fmt_price(current)} "
            f"({fmt_pct(current_pct)})"
        )

        lines.append(
            f"TP: {fmt_price(tp)} "
            f"({fmt_pct(tp_pct)})"
        )

        lines.append(
            f"SL: {fmt_price(sl)} "
            f"({fmt_pct(-sl_pct)})"
        )

        lines.append("")

    telegram_send_message(
        "\n".join(lines)
    )


# ============================================================
# CHART HELPERS
# ============================================================

def candle_plot(
    ax,
    df
):
    for i, row in df.iterrows():

        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        if c >= o:
            face = "white"
        else:
            face = "black"

        height = abs(c - o)

        if height == 0:
            height = max(
                abs(h - l) * 0.001,
                abs(c) * 0.00001
            )

        rect = Rectangle(
            (
                i - 0.32,
                min(o, c)
            ),
            0.64,
            height,
            facecolor=face,
            edgecolor="black",
            linewidth=0.8,
            zorder=3,
        )

        ax.add_patch(rect)

        ax.plot(
            [i, i],
            [l, h],
            color="black",
            linewidth=0.8,
            zorder=2,
        )


def hook_point_label(
    ax,
    x,
    y,
    label,
    above=True,
    confirmed=False
):
    offset = (
        14
        if above
        else -18
    )

    va = (
        "bottom"
        if above
        else "top"
    )

    extra = "\nCONFIRMED" if confirmed else ""

    ax.annotate(
        f"{label}\n{fmt_price(y)}{extra}",
        xy=(x, y),
        xytext=(0, offset),
        textcoords="offset points",
        ha="center",
        va=va,
        fontsize=9,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="white",
            edgecolor="black",
            alpha=0.95,
        ),
        arrowprops=dict(
            arrowstyle="-",
            linewidth=0.8,
        ),
        zorder=10,
    )


# ============================================================
# HOOK CHART
# ============================================================

def plot_hook_chart(
    df,
    hook,
    current_price
):
    symbol = hook["symbol"]
    direction = hook["direction"]

    confirm_index = hook["confirm_index"]

    start_index = hook["start"]["index"]

    left_index = max(
        0,
        start_index - 35
    )

    right_index = min(
        len(df) - 1,
        max(
            confirm_index + 35,
            len(df) - 1
        )
    )

    chart_df = df.iloc[
        left_index:right_index + 1
    ].copy()

    chart_df = chart_df.reset_index(
        drop=True
    )

    original_to_chart = {
        int(original_index): i
        for i, original_index
        in enumerate(
            range(
                left_index,
                right_index + 1
            )
        )
    }

    fig, ax = plt.subplots(
        figsize=(18, 10)
    )

    candle_plot(
        ax,
        chart_df
    )

    # --------------------------------------------------------
    # Hook points
    # --------------------------------------------------------

    if direction == "SHORT":

        point_specs = [
            ("START", hook["start"], False),
            ("H1", hook["h1"], True),
            ("L1", hook["l1"], False),
            ("H2", hook["h2"], True),
            ("L2", hook["l2"], False),
            ("H3", hook["h3"], True),
        ]

    else:

        point_specs = [
            ("START", hook["start"], True),
            ("L1", hook["l1"], False),
            ("H1", hook["h1"], True),
            ("L2", hook["l2"], False),
            ("H2", hook["h2"], True),
            ("L3", hook["l3"], False),
        ]

    hook_x = []
    hook_y = []

    for label, point, above in point_specs:

        original_index = int(
            point["index"]
        )

        x = original_to_chart.get(
            original_index
        )

        if x is None:
            continue

        y = float(point["price"])

        hook_x.append(x)
        hook_y.append(y)

        ax.scatter(
            [x],
            [y],
            s=115,
            facecolors="white",
            edgecolors="black",
            linewidths=2.2,
            zorder=8,
        )

        confirmed = (
            label == "H3"
            or label == "L3"
        )

        hook_point_label(
            ax,
            x,
            y,
            label,
            above=above,
            confirmed=confirmed,
        )

    # --------------------------------------------------------
    # Connect Hook points
    # --------------------------------------------------------

    if len(hook_x) >= 2:

        ax.plot(
            hook_x,
            hook_y,
            linewidth=2.2,
            linestyle="-",
            color="black",
            zorder=6,
        )

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = hook["confirm_price"]

    ax.axhline(
        entry,
        linestyle=":",
        linewidth=1.5,
        color="black",
        zorder=1,
    )

    ax.annotate(
        f"ENTRY\n{fmt_price(entry)}",
        xy=(
            len(chart_df) - 1,
            entry
        ),
        xytext=(-8, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        fontsize=9,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.3",
            facecolor="white",
            edgecolor="black",
        ),
    )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    tp = hook["tp"]

    tp_pct = target_pct(
        entry,
        tp
    )

    ax.axhline(
        tp,
        linestyle="--",
        linewidth=2.0,
        color="green",
        zorder=1,
    )

    ax.annotate(
        f"TP 86.4%\n"
        f"{fmt_price(tp)}\n"
        f"(+{tp_pct:.2f}%)",
        xy=(
            len(chart_df) - 1,
            tp
        ),
        xytext=(-8, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        fontsize=10,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="white",
            edgecolor="green",
        ),
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    sl = hook["sl"]

    sl_pct = stop_pct(
        entry,
        sl
    )

    ax.axhline(
        sl,
        linestyle="--",
        linewidth=2.0,
        color="red",
        zorder=1,
    )

    ax.annotate(
        f"SL\n"
        f"{fmt_price(sl)}\n"
        f"(-{sl_pct:.2f}%)",
        xy=(
            len(chart_df) - 1,
            sl
        ),
        xytext=(-8, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        fontsize=10,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="white",
            edgecolor="red",
        ),
    )

    # --------------------------------------------------------
    # CURRENT
    # --------------------------------------------------------

    current_pct = price_pct(
        entry,
        current_price,
        direction
    )

    ax.axhline(
        current_price,
        linestyle="-.",
        linewidth=1.5,
        color="blue",
        zorder=1,
    )

    ax.annotate(
        f"CURRENT\n"
        f"{fmt_price(current_price)}\n"
        f"({current_pct:+.2f}%)",
        xy=(
            len(chart_df) - 1,
            current_price
        ),
        xytext=(-8, 0),
        textcoords="offset points",
        ha="right",
        va="center",
        fontsize=9,
        fontweight="bold",
        bbox=dict(
            boxstyle="round,pad=0.35",
            facecolor="white",
            edgecolor="blue",
        ),
    )

    # --------------------------------------------------------
    # Confirmation vertical line
    # --------------------------------------------------------

    if confirm_index in original_to_chart:

        cx = original_to_chart[
            confirm_index
        ]

        ax.axvline(
            cx,
            linestyle=":",
            linewidth=1.5,
            color="black",
            alpha=0.8,
        )

    # --------------------------------------------------------
    # Title
    # --------------------------------------------------------

    hook_name = (
        "POSITIVE HOOK / SHORT"
        if direction == "SHORT"
        else "NEGATIVE HOOK / LONG"
    )

    ax.set_title(
        f"NDS M30 | {symbol} | {hook_name}\n"
        f"START → Hook → "
        f"{'H3' if direction == 'SHORT' else 'L3'} CONFIRMED | "
        f"TP 86.4% | "
        f"TP Distance: {hook['tp_distance_pct']:.2f}% | "
        f"SL = 50% TP Distance",
        fontsize=14,
        fontweight="bold",
        pad=20,
    )

    ax.set_ylabel(
        "Price",
        fontsize=11,
    )

    ax.set_xlabel(
        "M30 Candles",
        fontsize=11,
    )

    # --------------------------------------------------------
    # X labels
    # --------------------------------------------------------

    step = max(
        1,
        len(chart_df) // 10
    )

    ticks = list(
        range(
            0,
            len(chart_df),
            step
        )
    )

    labels = [
        chart_df.iloc[i]["datetime"].strftime(
            "%m-%d\n%H:%M"
        )
        for i in ticks
    ]

    ax.set_xticks(ticks)
    ax.set_xticklabels(
        labels,
        rotation=0,
        fontsize=8,
    )

    # --------------------------------------------------------
    # Y limits
    # --------------------------------------------------------

    important_prices = [
        hook["start_price"],
        hook["confirm_price"],
        hook["tp"],
        hook["sl"],
        current_price,
    ]

    important_prices.extend(
        [
            p["price"]
            for _, p, _ in point_specs
        ]
    )

    low_price = min(
        important_prices
    )

    high_price = max(
        important_prices
    )

    spread = high_price - low_price

    if spread <= 0:
        spread = max(
            high_price * 0.01,
            1.0
        )

    margin = spread * 0.15

    ax.set_ylim(
        low_price - margin,
        high_price + margin,
    )

    ax.grid(
        alpha=0.18,
        linestyle="--",
    )

    fig.tight_layout()

    safe_symbol = (
        symbol
        .replace("/", "_")
        .replace(":", "_")
    )

    filename = (
        f"{safe_symbol}_"
        f"{direction}_"
        f"{hook['confirm']['time'].strftime('%Y%m%d_%H%M%S')}.png"
    )

    path = os.path.join(
        CHART_DIR,
        filename
    )

    fig.savefig(
        path,
        dpi=150,
        bbox_inches="tight",
    )

    plt.close(fig)

    return path


# ============================================================
# HOOK TELEGRAM MESSAGE
# ============================================================

def build_hook_message(
    hook,
    current
):
    direction = hook["direction"]

    emoji = (
        "🔴"
        if direction == "SHORT"
        else "🟢"
    )

    entry = hook["confirm_price"]
    tp = hook["tp"]
    sl = hook["sl"]

    current_pct = price_pct(
        entry,
        current,
        direction
    )

    tp_pct = target_pct(
        entry,
        tp
    )

    sl_pct = stop_pct(
        entry,
        sl
    )

    if direction == "SHORT":

        hook_text = (
            f"START {fmt_price(hook['start_price'])}"
            f" → "
            f"H1 {fmt_price(hook['h1_price'])}"
            f" → "
            f"L1 {fmt_price(hook['l1_price'])}"
            f" → "
            f"H2 {fmt_price(hook['h2_price'])}"
            f" → "
            f"L2 {fmt_price(hook['l2_price'])}"
            f" → "
            f"H3 {fmt_price(hook['h3_price'])}"
        )

    else:

        hook_text = (
            f"START {fmt_price(hook['start_price'])}"
            f" → "
            f"L1 {fmt_price(hook['l1_price'])}"
            f" → "
            f"H1 {fmt_price(hook['h1_price'])}"
            f" → "
            f"L2 {fmt_price(hook['l2_price'])}"
            f" → "
            f"H2 {fmt_price(hook['h2_price'])}"
            f" → "
            f"L3 {fmt_price(hook['l3_price'])}"
        )

    return (
        f"{emoji} <b>NDS {direction}</b>\n\n"

        f"<b>{hook['symbol']}</b>\n\n"

        f"<b>HOOK</b>\n"
        f"{hook_text}\n\n"

        f"<b>ENTRY</b>: "
        f"{fmt_price(entry)}\n"

        f"<b>TP 86.4%</b>: "
        f"{fmt_price(tp)} "
        f"(+{tp_pct:.2f}%)\n"

        f"<b>SL</b>: "
        f"{fmt_price(sl)} "
        f"(-{sl_pct:.2f}%)\n\n"

        f"<b>CURRENT</b>: "
        f"{fmt_price(current)} "
        f"({current_pct:+.2f}%)\n\n"

        f"TP distance: "
        f"<b>{hook['tp_distance_pct']:.2f}%</b>\n"

        f"SL = 50% of TP distance\n"
        f"TP was not previously touched\n\n"

        f"<b>M30 ONLY</b>\n"
        f"<b>PAPER ONLY</b>"
    )


# ============================================================
# DIAGNOSTIC
# ============================================================

def send_diagnostic():
    DIAG["open_trades"] = len(
        get_open_trades()
    )

    text = (
        f"🔎 <b>NDS DIAGNOSTIC</b>\n"
        f"Version: <b>{VERSION}</b>\n"
        f"Time: {utc_iso()}\n\n"

        f"<b>M30</b>\n"
        f"Requests: {DIAG['requests']}\n"
        f"Data OK: {DIAG['data_ok']}\n"
        f"Empty: {DIAG['empty']}\n"
        f"Short data: {DIAG['short_data']}\n"
        f"Errors: {DIAG['errors']}\n\n"

        f"<b>PIVOTS</b>\n"
        f"Pivot Highs: {DIAG['pivot_highs']}\n"
        f"Pivot Lows: {DIAG['pivot_lows']}\n"
        f"Alternating Pivots: "
        f"{DIAG['alternating_pivots']}\n"
        f"6-point Sequences: "
        f"{DIAG['six_point_sequences']}\n\n"

        f"<b>POSITIVE / SHORT</b>\n"
        f"Candidates: "
        f"{DIAG['positive_candidates']}\n"
        f"Structure Valid: "
        f"{DIAG['positive_valid']}\n"
        f"START rejected: "
        f"{DIAG['positive_start_rejected']}\n"
        f"H2 rejected: "
        f"{DIAG['positive_h2_rejected']}\n"
        f"L2 rejected: "
        f"{DIAG['positive_l2_rejected']}\n"
        f"H3 rejected: "
        f"{DIAG['positive_h3_rejected']}\n\n"

        f"<b>NEGATIVE / LONG</b>\n"
        f"Candidates: "
        f"{DIAG['negative_candidates']}\n"
        f"Structure Valid: "
        f"{DIAG['negative_valid']}\n"
        f"START rejected: "
        f"{DIAG['negative_start_rejected']}\n"
        f"L2 rejected: "
        f"{DIAG['negative_l2_rejected']}\n"
        f"H2 rejected: "
        f"{DIAG['negative_h2_rejected']}\n"
        f"L3 rejected: "
        f"{DIAG['negative_l3_rejected']}\n\n"

        f"<b>TP FILTER</b>\n"
        f"Valid distance: "
        f"{DIAG['tp_distance_valid']}\n"
        f"Distance rejected: "
        f"{DIAG['tp_distance_rejected']}\n"
        f"TP already touched: "
        f"{DIAG['tp_already_touched']}\n\n"

        f"<b>TRADES</b>\n"
        f"Confirmed Hooks: "
        f"{DIAG['confirmed_hooks']}\n"
        f"New Signals: "
        f"{DIAG['new_signals']}\n"
        f"Charts: "
        f"{DIAG['charts']}\n"
        f"Opened Trades: "
        f"{DIAG['opened_trades']}\n"
        f"Open Trades: "
        f"{DIAG['open_trades']}\n"
        f"TP Hits: "
        f"{DIAG['tp_hits']}\n"
        f"SL Hits: "
        f"{DIAG['sl_hits']}\n\n"

        f"<b>TP = 86.4%</b>\n"
        f"TP distance: "
        f"{MIN_TP_DISTANCE_PCT:.2f}%"
        f" → "
        f"{MAX_TP_DISTANCE_PCT:.2f}%\n"
        f"SL = 50% TP distance\n\n"

        f"<b>M30 ONLY</b>\n"
        f"<b>NO M1 / NO 123F</b>\n"
        f"<b>PAPER ONLY</b>"
    )

    telegram_send_message(text)


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol,
    price_map,
    chart_counter
):
    df = fetch_m30(symbol)

    if df.empty:
        return chart_counter

    current = get_current_price(df)

    if current is None:
        return chart_counter

    price_map[symbol] = current

    hooks = build_hooks(
        symbol,
        df
    )

    if not hooks:
        return chart_counter

    # Most recent confirmed Hook only.
    hooks = sorted(
        hooks,
        key=lambda h: h["confirm"]["index"]
    )

    hook = hooks[-1]

    DIAG["confirmed_hooks"] += 1

    # --------------------------------------------------------
    # Existing Hook?
    # --------------------------------------------------------

    existing = hook_exists(
        hook
    )

    if existing:
        return chart_counter

    # --------------------------------------------------------
    # Save Hook
    # --------------------------------------------------------

    hook_id = save_hook(
        hook
    )

    if not hook_id:
        return chart_counter

    DIAG["new_signals"] += 1

    # --------------------------------------------------------
    # Open Paper Trade
    # --------------------------------------------------------

    trade_id = open_paper_trade(
        hook_id,
        hook
    )

    if trade_id:

        send_trade_open_message(
            hook_id,
            hook,
            current
        )

    # --------------------------------------------------------
    # Chart
    # --------------------------------------------------------

    if chart_counter < MAX_CHARTS_PER_SCAN:

        try:

            chart_path = plot_hook_chart(
                df,
                hook,
                current
            )

            if chart_path:

                DIAG["charts"] += 1

                caption = build_hook_message(
                    hook,
                    current
                )

                telegram_send_photo(
                    chart_path,
                    caption
                )

                chart_counter += 1

        except Exception:
            traceback.print_exc()

    return chart_counter


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    init_db()

    price_map = {}

    chart_counter = 0

    for symbol in SYMBOLS:

        try:

            chart_counter = process_symbol(
                symbol,
                price_map,
                chart_counter
            )

        except Exception:

            traceback.print_exc()

    # --------------------------------------------------------
    # Update ALL open trades after obtaining current prices.
    # --------------------------------------------------------

    if price_map:

        update_open_trades(
            price_map
        )

    # --------------------------------------------------------
    # Report open trades.
    # --------------------------------------------------------

    send_open_trades_report()

    # --------------------------------------------------------
    # Diagnostic.
    # --------------------------------------------------------

    send_diagnostic()


# ============================================================
# RESET DIAGNOSTICS
# ============================================================

def reset_diagnostics():
    for key in DIAG:
        DIAG[key] = 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    print("=" * 64)
    print(
        f"NDS M30 ONLY LIVE SCANNER "
        f"VERSION {VERSION}"
    )
    print("=" * 64)

    print(
        f"Symbols: {len(SYMBOLS)}"
    )

    print(
        f"Interval: {INTERVAL}"
    )

    print(
        f"TP Retracement: "
        f"{TP_RETRACE * 100:.1f}%"
    )

    print(
        f"TP Distance Filter: "
        f"{MIN_TP_DISTANCE_PCT:.2f}%"
        f" -> "
        f"{MAX_TP_DISTANCE_PCT:.2f}%"
    )

    print(
        f"SL: "
        f"{SL_DISTANCE_MULTIPLIER * 100:.0f}% "
        f"of TP distance"
    )

    print(
        "M1: DISABLED"
    )

    print(
        "123F: DISABLED"
    )

    print(
        "REAL TRADING: DISABLED"
    )

    print("=" * 64)

    try:

        reset_diagnostics()

        run_scan()

    except KeyboardInterrupt:

        print(
            "\nScanner stopped."
        )

    except Exception:

        traceback.print_exc()

        telegram_send_message(
            f"⚠️ <b>NDS SCANNER ERROR</b>\n"
            f"Version: {VERSION}\n"
            f"{traceback.format_exc()[-2500:]}"
        )
