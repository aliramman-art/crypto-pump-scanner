# ============================================================
# KRAKEN FUTURES TRENDLINE BREAK LIVE SCANNER
# VERSION 7.0.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# 1H:
#   Valid Pivot High/Low
#        ↓
#   Valid Trendline
#        ↓
#   Closed Candle Trendline Break
#        ↓
# 5M:
#   Valid Pivot High/Low
#        ↓
#   Valid Trendline
#        ↓
#   Closed Candle Trendline Break
#        +
#   Acceptable Volume / RVOL
#        ↓
#   ENTRY
#
# TP:
#   Nearest confirmed swing level ahead in price
#
# SL:
#   Just beyond previous confirmed swing
#
# IMPORTANT:
# - Closed candles only
# - No lookahead
# - Existing DB preserved
# - Existing open trades preserved
# - LONG and SHORT are independent
# - Same-direction duplicate open trades are blocked
# - PAPER ONLY
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta

import requests
import pandas as pd
import numpy as np

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# CONFIG
# ============================================================

VERSION = "7.0.0"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

KRAKEN_TICKER_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REQUEST_TIMEOUT = 20

# ------------------------------------------------------------
# 40 ASSETS
# ------------------------------------------------------------

ASSETS = [
    "XBT",
    "ETH",
    "SOL",
    "XRP",
    "LTC",
    "DOGE",
    "ADA",
    "LINK",
    "AVAX",
    "DOT",
    "BNB",
    "TRX",
    "UNI",
    "AAVE",
    "SUI",
    "NEAR",
    "ATOM",
    "FIL",
    "ARB",
    "OP",
    "BCH",
    "ETC",
    "XMR",
    "XLM",
    "ALGO",
    "ICP",
    "INJ",
    "TIA",
    "SEI",
    "RUNE",
    "CRV",
    "HBAR",
    "HYPE",
    "ENA",
    "FET",
    "KAS",
    "STX",
    "JUP",
    "PEPE",
    "WIF",
]

CONTRACTS = {
    asset: f"PF_{asset}USD"
    for asset in ASSETS
}

# ------------------------------------------------------------
# TIMEFRAMES
# ------------------------------------------------------------

TF_1H = 60
TF_5M = 5

INTERVAL_1H_SECONDS = 60 * 60
INTERVAL_5M_SECONDS = 5 * 60

# ------------------------------------------------------------
# DATA
# ------------------------------------------------------------

CANDLES_1H = 240
CANDLES_5M = 300

# ------------------------------------------------------------
# PIVOTS
# ------------------------------------------------------------

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# Minimum bars between two anchor pivots
MIN_PIVOT_SEPARATION_1H = 4
MIN_PIVOT_SEPARATION_5M = 5

# Minimum price movement required for a meaningful swing
MIN_SWING_PCT_1H = 0.003
MIN_SWING_PCT_5M = 0.0015

# ------------------------------------------------------------
# TRENDLINES
# ------------------------------------------------------------

# Maximum number of recent pivots used for trendline search
MAX_TRENDLINE_PIVOTS = 12

# How close a third pivot / candle needs to be to the line
TRENDLINE_TOUCH_TOLERANCE = 0.0035

# Break must exceed line by this amount
TRENDLINE_BREAK_BUFFER = 0.0005

# Maximum number of candles after second anchor in which
# breakout is accepted
BREAKOUT_LOOKAHEAD_1H = 72
BREAKOUT_LOOKAHEAD_5M = 72

# ------------------------------------------------------------
# VOLUME
# ------------------------------------------------------------

RVOL_LOOKBACK = 20

# Minimum relative volume for 5M breakout
MIN_RVOL = 1.30

# Breakout candle must also have meaningful body
MIN_BREAK_BODY_PCT = 0.0008

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MIN_RR = 1.50

# SL buffer beyond previous swing
SL_BUFFER_PCT = 0.0015

# Ignore levels that are unrealistically close
MIN_SL_DISTANCE_PCT = 0.0015
MIN_TP_DISTANCE_PCT = 0.0020

# ------------------------------------------------------------
# SIGNAL CONTROL
# ------------------------------------------------------------

MAX_SIGNALS_PER_SCAN = 1

MAX_HOLD_HOURS = 48

# periodic report
PERIODIC_REPORT_SECONDS = 900

# chart
CHART_CANDLES = 120

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 Kraken Trendline Scanner"
})


# ============================================================
# TIME HELPERS
# ============================================================

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def now_ts():
    return int(time.time())


def format_tehran(ts):
    try:
        dt = datetime.fromtimestamp(ts, tz=TEHRAN_TZ)
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def utc_string(ts):
    try:
        return datetime.fromtimestamp(
            ts,
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def pct(a, b):
    if b == 0:
        return 0.0
    return ((a - b) / b) * 100.0


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, payload=None, files=None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/{method}"
    )

    try:
        if files:
            r = SESSION.post(
                url,
                data=payload,
                files=files,
                timeout=REQUEST_TIMEOUT
            )
        else:
            r = SESSION.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT
            )

        return r.ok

    except Exception:
        return False


def send_telegram(text):
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    return telegram_request(
        "sendMessage",
        payload
    )


def send_telegram_photo(path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    try:
        with open(path, "rb") as f:
            files = {
                "photo": f
            }

            payload = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            return telegram_request(
                "sendPhoto",
                payload,
                files
            )

    except Exception:
        return False


# ============================================================
# DATABASE
# ============================================================

def db_connect():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_column(conn, table, column, definition):
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

        conn.commit()


def init_db():
    conn = db_connect()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            asset TEXT NOT NULL,
            direction TEXT NOT NULL,
            pattern TEXT,
            entry REAL NOT NULL,
            current_price REAL,
            tp REAL NOT NULL,
            sl REAL NOT NULL,
            rr REAL,
            entry_timestamp INTEGER NOT NULL,
            exit_timestamp INTEGER,
            status TEXT NOT NULL DEFAULT 'OPEN',
            pnl_pct REAL,
            exit_reason TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_key TEXT UNIQUE,
            asset TEXT,
            direction TEXT,
            pattern TEXT,
            entry REAL,
            tp REAL,
            sl REAL,
            rr REAL,
            signal_timestamp INTEGER,
            created_at INTEGER
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    # --------------------------------------------------------
    # Preserve old DB and migrate missing columns
    # --------------------------------------------------------

    ensure_column(
        conn,
        "trades",
        "pattern",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "current_price",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "tp",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "sl",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "rr",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "entry_timestamp",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "exit_timestamp",
        "INTEGER"
    )

    ensure_column(
        conn,
        "trades",
        "status",
        "TEXT"
    )

    ensure_column(
        conn,
        "trades",
        "pnl_pct",
        "REAL"
    )

    ensure_column(
        conn,
        "trades",
        "exit_reason",
        "TEXT"
    )

    conn.commit()
    conn.close()


# ============================================================
# DATABASE HELPERS
# ============================================================

def has_signal(signal_key):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM signals
        WHERE signal_key = ?
        LIMIT 1
        """,
        (signal_key,)
    ).fetchone()

    conn.close()

    return row is not None


def has_open_trade(asset, direction):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT 1
        FROM trades
        WHERE asset = ?
          AND direction = ?
          AND status = 'OPEN'
        LIMIT 1
        """,
        (asset, direction)
    ).fetchone()

    conn.close()

    return row is not None


def insert_signal_and_trade(
    signal_key,
    asset,
    direction,
    pattern,
    entry,
    tp,
    sl,
    rr,
    signal_timestamp
):
    conn = db_connect()

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT OR IGNORE INTO signals
            (
                signal_key,
                asset,
                direction,
                pattern,
                entry,
                tp,
                sl,
                rr,
                signal_timestamp,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            signal_key,
            asset,
            direction,
            pattern,
            entry,
            tp,
            sl,
            rr,
            signal_timestamp,
            now_ts()
        ))

        if cur.rowcount == 0:
            conn.rollback()
            return False

        cur.execute("""
            INSERT INTO trades
            (
                asset,
                direction,
                pattern,
                entry,
                current_price,
                tp,
                sl,
                rr,
                entry_timestamp,
                status
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN')
        """, (
            asset,
            direction,
            pattern,
            entry,
            entry,
            tp,
            sl,
            rr,
            signal_timestamp
        ))

        conn.commit()
        return True

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


def get_open_trades():
    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_timestamp ASC
    """).fetchall()

    conn.close()

    return rows


def update_trade_price(trade_id, price):
    conn = db_connect()

    conn.execute("""
        UPDATE trades
        SET current_price = ?
        WHERE id = ?
    """, (
        price,
        trade_id
    ))

    conn.commit()
    conn.close()


def close_trade(
    trade_id,
    exit_price,
    exit_reason,
    exit_timestamp
):
    conn = db_connect()

    row = conn.execute("""
        SELECT entry, direction
        FROM trades
        WHERE id = ?
    """, (trade_id,)).fetchone()

    if not row:
        conn.close()
        return

    entry = float(row["entry"])
    direction = row["direction"]

    if direction == "LONG":
        pnl = pct(exit_price, entry)
    else:
        pnl = pct(entry, exit_price)

    conn.execute("""
        UPDATE trades
        SET
            current_price = ?,
            exit_timestamp = ?,
            status = 'CLOSED',
            pnl_pct = ?,
            exit_reason = ?
        WHERE id = ?
    """, (
        exit_price,
        exit_timestamp,
        pnl,
        exit_reason,
        trade_id
    ))

    conn.commit()
    conn.close()


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance_start():
    conn = db_connect()

    row = conn.execute("""
        SELECT value
        FROM scanner_meta
        WHERE key = 'performance_start'
    """).fetchone()

    if row:
        conn.close()
        return int(row["value"])

    ts = now_ts()

    conn.execute("""
        INSERT INTO scanner_meta
        (key, value)
        VALUES ('performance_start', ?)
    """, (str(ts),))

    conn.commit()
    conn.close()

    return ts


def performance_summary():
    start_ts = get_performance_start()

    conn = db_connect()

    rows = conn.execute("""
        SELECT pnl_pct, exit_reason
        FROM trades
        WHERE status = 'CLOSED'
          AND exit_timestamp >= ?
        ORDER BY exit_timestamp ASC
    """, (start_ts,)).fetchall()

    conn.close()

    if not rows:
        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "winrate": 0,
            "total": 0,
        }

    pnls = [
        float(r["pnl_pct"] or 0)
        for r in rows
    ]

    wins = sum(
        1
        for x in pnls
        if x > 0
    )

    losses = sum(
        1
        for x in pnls
        if x <= 0
    )

    total = sum(pnls)

    return {
        "trades": len(pnls),
        "wins": wins,
        "losses": losses,
        "winrate": (
            wins / len(pnls) * 100
            if pnls else 0
        ),
        "total": total,
    }


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_klines(contract, interval, candles):
    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/{interval}"
    )

    try:
        r = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:
            return None

        data = r.json()

    except Exception:
        return None

    rows = None

    if isinstance(data, dict):
        if "candles" in data:
            rows = data["candles"]

        elif "data" in data:
            rows = data["data"]

        elif "result" in data:
            rows = data["result"]

    elif isinstance(data, list):
        rows = data

    if not rows:
        return None

    parsed = []

    for row in rows:
        try:
            if isinstance(row, dict):
                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("t")
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
                if len(row) < 6:
                    continue

                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]
                v = row[5]

            ts = float(ts)

            if ts > 10_000_000_000:
                ts /= 1000

            parsed.append({
                "timestamp": int(ts),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            })

        except Exception:
            continue

    if not parsed:
        return None

    df = pd.DataFrame(parsed)

    df = (
        df
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    interval_seconds = (
        INTERVAL_1H_SECONDS
        if interval == TF_1H
        else INTERVAL_5M_SECONDS
    )

    current = now_ts()

    df = df[
        (df["timestamp"] + interval_seconds) <= current
    ].copy()

    if len(df) < 50:
        return None

    return df.tail(candles).reset_index(drop=True)


def fetch_tickers():
    try:
        r = SESSION.get(
            KRAKEN_TICKER_URL,
            timeout=REQUEST_TIMEOUT
        )

        if not r.ok:
            return {}

        data = r.json()

    except Exception:
        return {}

    rows = []

    if isinstance(data, dict):
        rows = (
            data.get("tickers")
            or data.get("data")
            or data.get("result")
            or []
        )

    prices = {}

    for row in rows:
        if not isinstance(row, dict):
            continue

        symbol = (
            row.get("symbol")
            or row.get("instrument")
            or row.get("pair")
        )

        if not symbol:
            continue

        value = (
            row.get("last")
            or row.get("lastPrice")
            or row.get("price")
        )

        if value is None:
            continue

        try:
            prices[symbol] = float(value)
        except Exception:
            pass

    return prices


def get_current_price(contract, prices):
    if contract in prices:
        return prices[contract]

    # Try normalized forms
    contract_upper = contract.upper()

    for key, value in prices.items():
        if str(key).upper() == contract_upper:
            return value

    return None


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df, left=3, right=3):
    highs = []
    lows = []

    if len(df) < left + right + 5:
        return highs, lows

    for i in range(left, len(df) - right):

        h = float(df.iloc[i]["high"])
        l = float(df.iloc[i]["low"])

        left_highs = df.iloc[
            i - left:i
        ]["high"].values

        right_highs = df.iloc[
            i + 1:i + right + 1
        ]["high"].values

        left_lows = df.iloc[
            i - left:i
        ]["low"].values

        right_lows = df.iloc[
            i + 1:i + right + 1
        ]["low"].values

        if h >= max(left_highs) and h >= max(right_highs):
            highs.append({
                "index": i,
                "timestamp": int(
                    df.iloc[i]["timestamp"]
                ),
                "price": h,
                "type": "HIGH",
            })

        if l <= min(left_lows) and l <= min(right_lows):
            lows.append({
                "index": i,
                "timestamp": int(
                    df.iloc[i]["timestamp"]
                ),
                "price": l,
                "type": "LOW",
            })

    return highs, lows


# ============================================================
# VALID SWINGS
# ============================================================

def filter_valid_pivots(
    pivots,
    min_separation,
    min_swing_pct
):
    if not pivots:
        return []

    pivots = sorted(
        pivots,
        key=lambda x: x["index"]
    )

    result = []

    for p in pivots:
        if not result:
            result.append(p)
            continue

        prev = result[-1]

        if (
            p["index"] - prev["index"]
            < min_separation
        ):
            continue

        movement = abs(
            pct(
                p["price"],
                prev["price"]
            )
        ) / 100

        if movement < min_swing_pct:
            continue

        result.append(p)

    return result


# ============================================================
# TRENDLINE MATH
# ============================================================

def line_value(p1, p2, index):
    if p2["index"] == p1["index"]:
        return None

    slope = (
        p2["price"] - p1["price"]
    ) / (
        p2["index"] - p1["index"]
    )

    return (
        p1["price"]
        + slope * (
            index - p1["index"]
        )
    )


def line_slope(p1, p2):
    if p2["index"] == p1["index"]:
        return 0

    return (
        p2["price"] - p1["price"]
    ) / (
        p2["index"] - p1["index"]
    )


# ============================================================
# TRENDLINE VALIDATION
# ============================================================

def validate_downtrend_line(
    df,
    pivots,
    p1,
    p2
):
    if p2["index"] <= p1["index"]:
        return None

    if p2["price"] >= p1["price"]:
        return None

    slope = line_slope(p1, p2)

    if slope >= 0:
        return None

    touches = 0

    for p in pivots:
        if p["index"] < p1["index"]:
            continue

        if p["index"] > p2["index"]:
            continue

        lv = line_value(
            p1,
            p2,
            p["index"]
        )

        if lv is None:
            continue

        distance = abs(
            p["price"] - lv
        ) / max(
            abs(lv),
            1e-12
        )

        if distance <= TRENDLINE_TOUCH_TOLERANCE:
            touches += 1

    # Two anchors are mandatory.
    if touches < 2:
        return None

    # Check that candles between anchors did not
    # strongly invalidate the line.
    for i in range(
        p1["index"] + 1,
        p2["index"]
    ):
        lv = line_value(
            p1,
            p2,
            i
        )

        if lv is None:
            continue

        high = float(
            df.iloc[i]["high"]
        )

        # A downtrend resistance line should not be
        # repeatedly violated before the breakout.
        if high > lv * (
            1 + TRENDLINE_BREAK_BUFFER
        ):
            return None

    return {
        "direction": "LONG",
        "type": "DOWN_TRENDLINE",
        "p1": p1,
        "p2": p2,
        "slope": slope,
    }


def validate_uptrend_line(
    df,
    pivots,
    p1,
    p2
):
    if p2["index"] <= p1["index"]:
        return None

    if p2["price"] <= p1["price"]:
        return None

    slope = line_slope(p1, p2)

    if slope <= 0:
        return None

    touches = 0

    for p in pivots:
        if p["index"] < p1["index"]:
            continue

        if p["index"] > p2["index"]:
            continue

        lv = line_value(
            p1,
            p2,
            p["index"]
        )

        if lv is None:
            continue

        distance = abs(
            p["price"] - lv
        ) / max(
            abs(lv),
            1e-12
        )

        if distance <= TRENDLINE_TOUCH_TOLERANCE:
            touches += 1

    if touches < 2:
        return None

    for i in range(
        p1["index"] + 1,
        p2["index"]
    ):
        lv = line_value(
            p1,
            p2,
            i
        )

        if lv is None:
            continue

        low = float(
            df.iloc[i]["low"]
        )

        if low < lv * (
            1 - TRENDLINE_BREAK_BUFFER
        ):
            return None

    return {
        "direction": "SHORT",
        "type": "UP_TRENDLINE",
        "p1": p1,
        "p2": p2,
        "slope": slope,
    }


# ============================================================
# FIND BEST VALID TRENDLINE
# ============================================================

def find_valid_trendlines(
    df,
    pivot_highs,
    pivot_lows
):
    lines = []

    highs = pivot_highs[
        -MAX_TRENDLINE_PIVOTS:
    ]

    lows = pivot_lows[
        -MAX_TRENDLINE_PIVOTS:
    ]

    # --------------------------------------------------------
    # LONG = break of descending resistance
    # --------------------------------------------------------

    for i in range(len(highs)):
        for j in range(i + 1, len(highs)):

            p1 = highs[i]
            p2 = highs[j]

            line = validate_downtrend_line(
                df,
                highs,
                p1,
                p2
            )

            if line:
                lines.append(line)

    # --------------------------------------------------------
    # SHORT = break of ascending support
    # --------------------------------------------------------

    for i in range(len(lows)):
        for j in range(i + 1, len(lows)):

            p1 = lows[i]
            p2 = lows[j]

            line = validate_uptrend_line(
                df,
                lows,
                p1,
                p2
            )

            if line:
                lines.append(line)

    # Newer second anchor first
    lines.sort(
        key=lambda x: x["p2"]["index"],
        reverse=True
    )

    return lines


# ============================================================
# FIND TRENDLINE BREAK
# ============================================================

def find_breakout_after_line(
    df,
    line,
    start_index,
    max_bars
):
    p2_index = line["p2"]["index"]

    start = max(
        start_index,
        p2_index + 1
    )

    end = min(
        len(df) - 1,
        start + max_bars
    )

    for i in range(start, end + 1):

        candle = df.iloc[i]

        lv = line_value(
            line["p1"],
            line["p2"],
            i
        )

        if lv is None or lv <= 0:
            continue

        close = float(candle["close"])
        open_price = float(candle["open"])

        body_pct = abs(
            close - open_price
        ) / max(
            abs(open_price),
            1e-12
        )

        if line["direction"] == "LONG":

            if (
                close >
                lv * (
                    1 + TRENDLINE_BREAK_BUFFER
                )
            ):
                return {
                    "index": i,
                    "timestamp": int(
                        candle["timestamp"]
                    ),
                    "price": close,
                    "line_price": lv,
                    "direction": "LONG",
                    "body_pct": body_pct,
                }

        else:

            if (
                close <
                lv * (
                    1 - TRENDLINE_BREAK_BUFFER
                )
            ):
                return {
                    "index": i,
                    "timestamp": int(
                        candle["timestamp"]
                    ),
                    "price": close,
                    "line_price": lv,
                    "direction": "SHORT",
                    "body_pct": body_pct,
                }

    return None


# ============================================================
# 1H SETUP
# ============================================================

def find_1h_break_setup(df):
    highs, lows = detect_pivots(
        df,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    highs = filter_valid_pivots(
        highs,
        MIN_PIVOT_SEPARATION_1H,
        MIN_SWING_PCT_1H
    )

    lows = filter_valid_pivots(
        lows,
        MIN_PIVOT_SEPARATION_1H,
        MIN_SWING_PCT_1H
    )

    lines = find_valid_trendlines(
        df,
        highs,
        lows
    )

    candidates = []

    for line in lines:

        breakout = find_breakout_after_line(
            df,
            line,
            line["p2"]["index"] + 1,
            BREAKOUT_LOOKAHEAD_1H
        )

        if not breakout:
            continue

        candidates.append({
            "line": line,
            "breakout": breakout,
            "highs": highs,
            "lows": lows,
        })

    if not candidates:
        return None

    # Most recent valid breakout
    candidates.sort(
        key=lambda x: x["breakout"]["timestamp"],
        reverse=True
    )

    return candidates[0]


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    index,
    lookback=20
):
    if index < lookback:
        return 0.0

    previous = df.iloc[
        index - lookback:index
    ]["volume"].astype(float)

    avg = previous.mean()

    if avg <= 0:
        return 0.0

    current_volume = float(
        df.iloc[index]["volume"]
    )

    return current_volume / avg


# ============================================================
# 5M SETUP
# ============================================================

def find_5m_break_setup(
    df,
    after_timestamp,
    direction
):
    highs, lows = detect_pivots(
        df,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    highs = filter_valid_pivots(
        highs,
        MIN_PIVOT_SEPARATION_5M,
        MIN_SWING_PCT_5M
    )

    lows = filter_valid_pivots(
        lows,
        MIN_PIVOT_SEPARATION_5M,
        MIN_SWING_PCT_5M
    )

    lines = find_valid_trendlines(
        df,
        highs,
        lows
    )

    candidates = []

    for line in lines:

        if line["direction"] != direction:
            continue

        # Trendline itself must exist before 1H breakout.
        if (
            int(
                df.iloc[
                    line["p2"]["index"]
                ]["timestamp"]
            )
            >= after_timestamp
        ):
            continue

        # Start after the 1H breakout.
        indices = df.index[
            df["timestamp"] > after_timestamp
        ].tolist()

        if not indices:
            continue

        start_index = indices[0]

        breakout = find_breakout_after_line(
            df,
            line,
            start_index,
            BREAKOUT_LOOKAHEAD_5M
        )

        if not breakout:
            continue

        idx = breakout["index"]

        rvol = calculate_rvol(
            df,
            idx,
            RVOL_LOOKBACK
        )

        if rvol < MIN_RVOL:
            continue

        if breakout["body_pct"] < MIN_BREAK_BODY_PCT:
            continue

        candidates.append({
            "line": line,
            "breakout": breakout,
            "rvol": rvol,
            "highs": highs,
            "lows": lows,
        })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["breakout"]["timestamp"],
            x["rvol"]
        ),
        reverse=True
    )

    return candidates[0]


# ============================================================
# STRUCTURAL TP / SL
# ============================================================

def choose_levels(
    df,
    setup,
    direction
):
    breakout = setup["breakout"]
    entry = float(breakout["price"])

    highs = setup["highs"]
    lows = setup["lows"]

    break_index = breakout["index"]

    if direction == "LONG":

        # ----------------------------------------------------
        # Previous confirmed swing low
        # ----------------------------------------------------

        previous_lows = [
            p for p in lows
            if p["index"] < break_index
        ]

        if not previous_lows:
            return None

        previous_low = previous_lows[-1]

        sl = (
            previous_low["price"]
            * (1 - SL_BUFFER_PCT)
        )

        # ----------------------------------------------------
        # Nearest confirmed resistance above entry.
        # This uses already confirmed pivots only.
        # ----------------------------------------------------

        future_price_highs = [
            p for p in highs
            if p["index"] < break_index
            and p["price"] > entry
        ]

        if not future_price_highs:
            return None

        future_price_highs.sort(
            key=lambda p: p["price"]
        )

        target = future_price_highs[0]

        tp = target["price"]

        risk = entry - sl
        reward = tp - entry

        if risk <= 0 or reward <= 0:
            return None

        sl_distance_pct = (
            abs(entry - sl)
            / entry
        )

        tp_distance_pct = (
            abs(tp - entry)
            / entry
        )

        if sl_distance_pct < MIN_SL_DISTANCE_PCT:
            return None

        if tp_distance_pct < MIN_TP_DISTANCE_PCT:
            return None

        rr = reward / risk

        if rr < MIN_RR:
            return None

        return {
            "entry": entry,
            "tp": tp,
            "sl": sl,
            "rr": rr,
            "previous_swing": previous_low,
            "target_swing": target,
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    previous_highs = [
        p for p in highs
        if p["index"] < break_index
    ]

    if not previous_highs:
        return None

    previous_high = previous_highs[-1]

    sl = (
        previous_high["price"]
        * (1 + SL_BUFFER_PCT)
    )

    future_price_lows = [
        p for p in lows
        if p["index"] < break_index
        and p["price"] < entry
    ]

    if not future_price_lows:
        return None

    future_price_lows.sort(
        key=lambda p: p["price"],
        reverse=True
    )

    target = future_price_lows[0]

    tp = target["price"]

    risk = sl - entry
    reward = entry - tp

    if risk <= 0 or reward <= 0:
        return None

    sl_distance_pct = (
        abs(sl - entry)
        / entry
    )

    tp_distance_pct = (
        abs(entry - tp)
        / entry
    )

    if sl_distance_pct < MIN_SL_DISTANCE_PCT:
        return None

    if tp_distance_pct < MIN_TP_DISTANCE_PCT:
        return None

    rr = reward / risk

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "previous_swing": previous_high,
        "target_swing": target,
    }


# ============================================================
# ANALYZE ASSET
# ============================================================

def analyze_asset(asset):
    contract = CONTRACTS[asset]

    df1h = fetch_klines(
        contract,
        TF_1H,
        CANDLES_1H
    )

    if df1h is None:
        return None, "NO_1H_DATA"

    setup_1h = find_1h_break_setup(
        df1h
    )

    if not setup_1h:
        return None, "NO_1H_BREAK"

    direction = (
        setup_1h["breakout"]["direction"]
    )

    one_h_break_ts = (
        setup_1h["breakout"]["timestamp"]
    )

    df5m = fetch_klines(
        contract,
        TF_5M,
        CANDLES_5M
    )

    if df5m is None:
        return None, "NO_5M_DATA"

    setup_5m = find_5m_break_setup(
        df5m,
        one_h_break_ts,
        direction
    )

    if not setup_5m:
        return None, "NO_5M_VOLUME_BREAK"

    levels = choose_levels(
        df5m,
        setup_5m,
        direction
    )

    if not levels:
        return None, "NO_VALID_TP_SL"

    entry = levels["entry"]
    tp = levels["tp"]
    sl = levels["sl"]
    rr = levels["rr"]

    signal_timestamp = (
        setup_5m["breakout"]["timestamp"]
    )

    signal_key = (
        f"{asset}|"
        f"{direction}|"
        f"{signal_timestamp}"
    )

    if has_signal(signal_key):
        return None, "DUPLICATE"

    if has_open_trade(
        asset,
        direction
    ):
        return None, "OPEN_SAME_DIRECTION"

    return {
        "asset": asset,
        "contract": contract,
        "direction": direction,
        "pattern": (
            "1H Trendline Break + "
            "5M Trendline Break"
        ),
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "rr": rr,
        "rvol": setup_5m["rvol"],
        "signal_timestamp": signal_timestamp,
        "signal_key": signal_key,
        "one_h_break_timestamp": one_h_break_ts,
        "one_h_line": setup_1h["line"],
        "five_m_line": setup_5m["line"],
        "five_m_breakout": setup_5m["breakout"],
        "previous_swing": levels["previous_swing"],
        "target_swing": levels["target_swing"],
        "df5m": df5m,
    }, "VALID"


# ============================================================
# CHART
# ============================================================

def create_signal_chart(signal):
    df = signal["df5m"].copy()

    df = df.tail(
        CHART_CANDLES
    ).reset_index(drop=True)

    if len(df) < 20:
        return None

    path = (
        f"signal_"
        f"{signal['asset']}_"
        f"{signal['direction']}_"
        f"{signal['signal_timestamp']}.png"
    )

    fig, ax = plt.subplots(
        figsize=(12, 6)
    )

    x = np.arange(len(df))

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    for i, row in df.iterrows():

        o = float(row["open"])
        h = float(row["high"])
        l = float(row["low"])
        c = float(row["close"])

        if c >= o:
            face = "white"
            edge = "black"
        else:
            face = "black"
            edge = "black"

        ax.plot(
            [i, i],
            [l, h],
            linewidth=0.8,
            color="black"
        )

        bottom = min(o, c)
        height = max(
            abs(c - o),
            max(c, o) * 0.0001
        )

        rect = plt.Rectangle(
            (i - 0.3, bottom),
            0.6,
            height,
            facecolor=face,
            edgecolor=edge,
            linewidth=0.8
        )

        ax.add_patch(rect)

    # --------------------------------------------------------
    # TP / SL / ENTRY
    # --------------------------------------------------------

    ax.axhline(
        signal["entry"],
        linestyle="--",
        linewidth=1,
        label=f"ENTRY {signal['entry']:.8g}"
    )

    ax.axhline(
        signal["tp"],
        linestyle="--",
        linewidth=1,
        label=f"TP {signal['tp']:.8g}"
    )

    ax.axhline(
        signal["sl"],
        linestyle="--",
        linewidth=1,
        label=f"SL {signal['sl']:.8g}"
    )

    # --------------------------------------------------------
    # 5M trendline
    # --------------------------------------------------------

    line = signal["five_m_line"]

    visible_indices = []

    for i in range(len(df)):

        original_ts = int(
            df.iloc[i]["timestamp"]
        )

        # convert local chart index to global
        matching = np.where(
            signal["df5m"]["timestamp"].values
            == original_ts
        )[0]

        if len(matching):
            global_i = int(
                matching[0]
            )

            if (
                global_i >= line["p1"]["index"]
            ):
                visible_indices.append(
                    (i, global_i)
                )

    if visible_indices:
        xs = [
            item[0]
            for item in visible_indices
        ]

        ys = [
            line_value(
                line["p1"],
                line["p2"],
                item[1]
            )
            for item in visible_indices
        ]

        ax.plot(
            xs,
            ys,
            linestyle=":",
            linewidth=1.5,
            label="5M Trendline"
        )

    # --------------------------------------------------------
    # Breakout marker
    # --------------------------------------------------------

    break_ts = signal[
        "five_m_breakout"
    ]["timestamp"]

    break_positions = np.where(
        df["timestamp"].values
        == break_ts
    )[0]

    if len(break_positions):
        bx = int(
            break_positions[0]
        )

        by = float(
            df.iloc[bx]["close"]
        )

        ax.scatter(
            [bx],
            [by],
            s=50,
            marker="o",
            label="5M BREAK"
        )

    # --------------------------------------------------------
    # Target / previous swing
    # --------------------------------------------------------

    ps = signal["previous_swing"]
    ts = signal["target_swing"]

    for p, label in [
        (ps, "PREVIOUS SWING"),
        (ts, "TARGET SWING"),
    ]:
        positions = np.where(
            df["timestamp"].values
            == p["timestamp"]
        )[0]

        if len(positions):
            px = int(positions[0])

            ax.scatter(
                [px],
                [p["price"]],
                s=40,
                marker="x",
                label=label
            )

    ax.set_title(
        f"KRAKEN {signal['asset']} "
        f"{signal['direction']} | "
        f"1H → 5M Trendline Break | "
        f"RVOL {signal['rvol']:.2f}"
    )

    ax.grid(
        True,
        alpha=0.2
    )

    ax.legend(
        loc="best",
        fontsize=8
    )

    ax.set_xlabel(
        "5M Closed Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    plt.tight_layout()

    try:
        fig.savefig(
            path,
            dpi=140
        )
    finally:
        plt.close(fig)

    return path


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(signal):
    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = signal["entry"]
    tp = signal["tp"]
    sl = signal["sl"]

    tp_pct = (
        pct(tp, entry)
        if direction == "LONG"
        else pct(entry, tp)
    )

    sl_pct = (
        pct(sl, entry)
        if direction == "LONG"
        else pct(entry, sl)
    )

    one_h_ts = signal[
        "one_h_break_timestamp"
    ]

    five_m_ts = signal[
        "signal_timestamp"
    ]

    return (
        f"{emoji} <b>{direction} "
        f"{signal['asset']}</b>\n\n"

        f"📌 Strategy: "
        f"1H Trendline → 5M Trendline\n"

        f"🕐 1H Break: "
        f"{format_tehran(one_h_ts)}\n"

        f"🕐 5M Break: "
        f"{format_tehran(five_m_ts)}\n\n"

        f"💰 Entry: "
        f"<code>{entry:.8g}</code>\n"

        f"🎯 TP: "
        f"<code>{tp:.8g}</code> "
        f"({tp_pct:+.2f}%)\n"

        f"🛑 SL: "
        f"<code>{sl:.8g}</code> "
        f"({sl_pct:+.2f}%)\n\n"

        f"⚙️ RR: "
        f"<b>1:{signal['rr']:.2f}</b>\n"

        f"📊 RVOL: "
        f"<b>{signal['rvol']:.2f}x</b>\n\n"

        f"Mode: PAPER ONLY"
    )


# ============================================================
# CLOSE OPEN TRADES
# ============================================================

def reconcile_open_trades(prices):
    rows = get_open_trades()

    closed_count = 0

    for trade in rows:

        asset = trade["asset"]
        direction = trade["direction"]

        contract = CONTRACTS.get(asset)

        if not contract:
            continue

        current = get_current_price(
            contract,
            prices
        )

        if current is None:
            continue

        trade_id = trade["id"]

        entry = float(
            trade["entry"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        entry_ts = int(
            trade["entry_timestamp"]
        )

        update_trade_price(
            trade_id,
            current
        )

        age_hours = (
            now_ts() - entry_ts
        ) / 3600.0

        exit_reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            if current >= tp:
                exit_reason = "TP"

            elif current <= sl:
                exit_reason = "SL"

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if current <= tp:
                exit_reason = "TP"

            elif current >= sl:
                exit_reason = "SL"

        # ----------------------------------------------------
        # TIME EXIT
        # ----------------------------------------------------

        if (
            exit_reason is None
            and age_hours >= MAX_HOLD_HOURS
        ):
            exit_reason = "TIME"

        if exit_reason is None:
            continue

        close_trade(
            trade_id,
            current,
            exit_reason,
            now_ts()
        )

        closed_count += 1

        if direction == "LONG":
            pnl = pct(
                current,
                entry
            )
        else:
            pnl = pct(
                entry,
                current
            )

        emoji = (
            "🟢"
            if pnl >= 0
            else "🔴"
        )

        message = (
            f"{emoji} <b>CLOSE "
            f"{asset} {direction}</b>\n\n"

            f"📌 Reason: "
            f"<b>{exit_reason}</b>\n"

            f"💰 Entry: "
            f"<code>{entry:.8g}</code>\n"

            f"💵 Exit: "
            f"<code>{current:.8g}</code>\n"

            f"📊 PnL: "
            f"<b>{pnl:+.2f}%</b>\n"

            f"🕐 Exit: "
            f"{format_tehran(now_ts())}\n"

            f"Mode: PAPER ONLY"
        )

        send_telegram(
            message
        )

    return closed_count


# ============================================================
# OPEN TRADES TELEGRAM
# ============================================================

def build_open_trades_message(prices):
    rows = get_open_trades()

    if not rows:
        return (
            "📊 <b>KRAKEN PATTERN SCANNER</b>\n\n"
            "🟢 <b>OPEN TRADES: 0</b>"
        )

    lines = [
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>",
        "",
        f"🟢 <b>OPEN TRADES: {len(rows)}</b>",
        ""
    ]

    for trade in rows:

        asset = trade["asset"]
        direction = trade["direction"]

        current = get_current_price(
            CONTRACTS[asset],
            prices
        )

        if current is None:
            current = float(
                trade["current_price"]
                or trade["entry"]
            )

        entry = float(
            trade["entry"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        if direction == "LONG":
            current_pnl = pct(
                current,
                entry
            )
            tp_pnl = pct(
                tp,
                entry
            )
            sl_pnl = pct(
                sl,
                entry
            )
        else:
            current_pnl = pct(
                entry,
                current
            )
            tp_pnl = pct(
                entry,
                tp
            )
            sl_pnl = pct(
                entry,
                sl
            )

        age_hours = (
            now_ts()
            - int(trade["entry_timestamp"])
        ) / 3600.0

        emoji = (
            "🟢"
            if direction == "LONG"
            else "🔴"
        )

        lines.extend([
            (
                f"{emoji} <b>{asset} "
                f"{direction}</b>"
            ),
            (
                f"📌 "
                f"{trade['pattern'] or 'Trendline Break'}"
            ),
            (
                f"💰 Entry: "
                f"<code>{entry:.8g}</code>"
            ),
            (
                f"💵 Current: "
                f"<code>{current:.8g}</code> "
                f"({current_pnl:+.2f}%)"
            ),
            (
                f"🛑 SL: "
                f"<code>{sl:.8g}</code> "
                f"({sl_pnl:+.2f}%)"
            ),
            (
                f"🎯 TP: "
                f"<code>{tp:.8g}</code> "
                f"({tp_pnl:+.2f}%)"
            ),
            (
                f"⏱ Duration: "
                f"{age_hours:.1f}h"
            ),
            ""
        ])

    return "\n".join(lines)


# ============================================================
# PERFORMANCE TELEGRAM
# ============================================================

def build_performance_message():
    p = performance_summary()

    return (
        "📈 <b>PERFORMANCE</b>\n\n"

        f"Trades: <b>{p['trades']}</b>\n"
        f"Wins: <b>{p['wins']}</b>\n"
        f"Losses: <b>{p['losses']}</b>\n"
        f"Win Rate: <b>{p['winrate']:.2f}%</b>\n"
        f"Net PnL: <b>{p['total']:+.2f}%</b>"
    )


# ============================================================
# SCAN SUMMARY
# ============================================================

def build_scan_summary(stats):
    return (
        "📊 <b>KRAKEN TRENDLINE SCANNER</b>\n\n"

        f"Version: <b>{VERSION}</b>\n"
        f"Mode: <b>PAPER ONLY</b>\n"
        f"Assets: <b>{stats['assets']}/40</b>\n\n"

        f"1H Valid Trendlines: "
        f"<b>{stats['trendlines_1h']}</b>\n"

        f"1H Breakouts: "
        f"<b>{stats['breaks_1h']}</b>\n"

        f"5M Trendlines: "
        f"<b>{stats['trendlines_5m']}</b>\n"

        f"5M Breakouts: "
        f"<b>{stats['breaks_5m']}</b>\n"

        f"Volume Accepted: "
        f"<b>{stats['volume_ok']}</b>\n"

        f"Valid TP/SL: "
        f"<b>{stats['valid_levels']}</b>\n\n"

        f"New Signals: "
        f"<b>{stats['new_signals']}</b>\n"

        f"Closed Trades: "
        f"<b>{stats['closed']}</b>\n"

        f"Time: "
        f"{format_tehran(now_ts())}"
    )


# ============================================================
# PERIODIC REPORT
# ============================================================

def should_send_periodic_report():
    conn = db_connect()

    row = conn.execute("""
        SELECT value
        FROM scanner_meta
        WHERE key = 'last_periodic_report'
    """).fetchone()

    if not row:
        conn.execute("""
            INSERT OR REPLACE INTO scanner_meta
            (key, value)
            VALUES ('last_periodic_report', ?)
        """, (str(now_ts()),))

        conn.commit()
        conn.close()

        return True

    last = int(row["value"])

    if (
        now_ts() - last
        < PERIODIC_REPORT_SECONDS
    ):
        conn.close()
        return False

    conn.execute("""
        UPDATE scanner_meta
        SET value = ?
        WHERE key = 'last_periodic_report'
    """, (str(now_ts()),))

    conn.commit()
    conn.close()

    return True


# ============================================================
# MAIN SCAN
# ============================================================

def scan():
    init_db()

    prices = fetch_tickers()

    stats = {
        "assets": 0,
        "trendlines_1h": 0,
        "breaks_1h": 0,
        "trendlines_5m": 0,
        "breaks_5m": 0,
        "volume_ok": 0,
        "valid_levels": 0,
        "new_signals": 0,
        "closed": 0,
    }

    # --------------------------------------------------------
    # Reconcile existing trades first
    # --------------------------------------------------------

    stats["closed"] = (
        reconcile_open_trades(
            prices
        )
    )

    candidates = []

    # --------------------------------------------------------
    # Scan all 40 assets
    # --------------------------------------------------------

    for asset in ASSETS:

        stats["assets"] += 1

        try:
            signal, reason = analyze_asset(
                asset
            )

            if signal is None:
                continue

            stats["breaks_1h"] += 1
            stats["breaks_5m"] += 1
            stats["volume_ok"] += 1
            stats["valid_levels"] += 1

            candidates.append(
                signal
            )

        except Exception as e:
            print(
                f"[ERROR] {asset}: {e}"
            )
            traceback.print_exc()

    # --------------------------------------------------------
    # Prefer highest RR, then higher RVOL
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    sent = 0

    for signal in candidates:

        if sent >= MAX_SIGNALS_PER_SCAN:
            break

        inserted = insert_signal_and_trade(
            signal["signal_key"],
            signal["asset"],
            signal["direction"],
            signal["pattern"],
            signal["entry"],
            signal["tp"],
            signal["sl"],
            signal["rr"],
            signal["signal_timestamp"]
        )

        if not inserted:
            continue

        stats["new_signals"] += 1
        sent += 1

        message = build_signal_message(
            signal
        )

        # ----------------------------------------------------
        # Normal signal message only
        # ----------------------------------------------------

        send_telegram(
            message
        )

        # ----------------------------------------------------
        # Chart
        # ----------------------------------------------------

        chart_path = None

        try:
            chart_path = create_signal_chart(
                signal
            )

            if chart_path:
                send_telegram_photo(
                    chart_path,
                    caption=(
                        f"{signal['asset']} "
                        f"{signal['direction']} | "
                        f"5M Trendline Break"
                    )
                )

        except Exception as e:
            print(
                f"[CHART ERROR] "
                f"{signal['asset']}: {e}"
            )

        finally:
            if chart_path:
                try:
                    os.remove(
                        chart_path
                    )
                except Exception:
                    pass

    # --------------------------------------------------------
    # Console report
    # --------------------------------------------------------

    summary = build_scan_summary(
        stats
    )

    print(summary)

    # --------------------------------------------------------
    # Periodic Telegram report
    # --------------------------------------------------------

    if should_send_periodic_report():

        open_message = (
            build_open_trades_message(
                prices
            )
        )

        performance_message = (
            build_performance_message()
        )

        send_telegram(
            open_message
        )

        send_telegram(
            performance_message
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    print("=" * 70)
    print(
        f"KRAKEN FUTURES TRENDLINE "
        f"SCANNER v{VERSION}"
    )
    print("=" * 70)

    print(
        f"REAL_TRADING = {REAL_TRADING}"
    )

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    try:
        scan()

    except Exception as e:
        print(
            "[FATAL ERROR]",
            e
        )

        traceback.print_exc()

        # Do not crash silently.
        send_telegram(
            "⚠️ <b>SCANNER ERROR</b>\n\n"
            f"<code>{str(e)[:1000]}</code>"
        )

        raise
