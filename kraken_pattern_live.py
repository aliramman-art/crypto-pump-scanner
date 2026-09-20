# ============================================================
# KRAKEN FUTURES PATTERN LIVE SIGNAL SCANNER
# VERSION 6.1.0
# ============================================================
#
# PAPER ONLY
# REAL_TRADING = False
#
# STRATEGY
#
# 1H:
#   Pattern Detection
#       ↓
#   1H Neckline Breakout
#       ↓
#
# 15M:
#   Same Pattern Detection
#       ↓
#   15M Pattern Breakout
#       ↓
#   15M Neckline Retest
#       ↓
#   15M Confirmation
#       ↓
#   ENTRY
#
# TP / SL:
#   Natural target and invalidation of the 15M pattern
#
# NO:
#   Fixed 1% TP
#   Fixed 1% SL
#   Artificial RR filter
#
# Telegram:
#   NEW SIGNAL
#   CLOSE
#   PERIODIC REPORT
#   PERFORMANCE
#   15M CHART WITH:
#       Pattern points
#       Neckline
#       Breakout
#       Retest
#       Entry
#       TP
#       SL
#
# Existing DB is preserved:
#   kraken_pattern_live_v52.db
# ============================================================

import os
import sqlite3
import time
import math
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

VERSION = "6.1.0"

REAL_TRADING = False

DB_FILE = "kraken_pattern_live_v52.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_INSTRUMENTS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/instruments"
)

KRAKEN_TICKERS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

KRAKEN_CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

HTTP_RETRIES = 3
HTTP_TIMEOUT = 20

MAX_HOLD_HOURS = 48

PATTERN_LOOKBACK_1H = 240
PATTERN_LOOKBACK_15M = 240

RECENT_PATTERN_HOURS_1H = 72
RECENT_PATTERN_HOURS_15M = 36

BREAKOUT_LOOKAHEAD_1H = 24
BREAKOUT_LOOKAHEAD_15M = 32

RETEST_LOOKAHEAD_15M = 16

CONFIRMATION_LOOKAHEAD_15M = 4

MAX_ENTRY_AGE_MINUTES = 20

PERIODIC_REPORT_SECONDS = 900

MAX_SIGNALS_PER_SCAN = 1

# Pattern quality filters
DOUBLE_LEVEL_TOLERANCE = 0.0075
MIN_PATTERN_DEPTH_PCT = 0.004
MIN_PATTERN_SEPARATION_BARS = 2

HS_SHOULDER_TOLERANCE = 0.015
HS_MIN_HEAD_ADVANTAGE_PCT = 0.003
MIN_HS_DEPTH_PCT = 0.004

# Small technical buffer for natural invalidation.
# This is NOT a fixed stop-loss percentage.
INVALIDATION_BUFFER = 0.001

# Maximum chart candles
CHART_CANDLES = 80


# ============================================================
# ASSETS
# ============================================================

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


CONTRACT_MAP = {
    "XBT": "PF_XBTUSD",
    "ETH": "PF_ETHUSD",
    "SOL": "PF_SOLUSD",
    "XRP": "PF_XRPUSD",
    "LTC": "PF_LTCUSD",
    "DOGE": "PF_DOGEUSD",
    "ADA": "PF_ADAUSD",
    "LINK": "PF_LINKUSD",
    "AVAX": "PF_AVAXUSD",
    "DOT": "PF_DOTUSD",
    "BNB": "PF_BNBUSD",
    "TRX": "PF_TRXUSD",
    "UNI": "PF_UNIUSD",
    "AAVE": "PF_AAVEUSD",
    "SUI": "PF_SUIUSD",
    "NEAR": "PF_NEARUSD",
    "ATOM": "PF_ATOMUSD",
    "FIL": "PF_FILUSD",
    "ARB": "PF_ARBUSD",
    "OP": "PF_OPUSD",
    "BCH": "PF_BCHUSD",
    "ETC": "PF_ETCUSD",
    "XMR": "PF_XMRUSD",
    "XLM": "PF_XLMUSD",
    "ALGO": "PF_ALGOUSD",
    "ICP": "PF_ICPUSD",
    "INJ": "PF_INJUSD",
    "TIA": "PF_TIAUSD",
    "SEI": "PF_SEIUSD",
    "RUNE": "PF_RUNEUSD",
    "CRV": "PF_CRVUSD",
    "HBAR": "PF_HBARUSD",
    "HYPE": "PF_HYPEUSD",
    "ENA": "PF_ENAUSD",
    "FET": "PF_FETUSD",
    "KAS": "PF_KASUSD",
    "STX": "PF_STXUSD",
    "JUP": "PF_JUPUSD",
    "PEPE": "PF_PEPEUSD",
    "WIF": "PF_WIFUSD",
}


# ============================================================
# GLOBALS
# ============================================================

CONTRACTS = {}
LIVE_PRICES = {}

STATS = {
    "scans": 0,
    "candidates": 0,
    "unique_entries": 0,
    "signals_sent": 0,
    "closed_trades": 0,
    "errors": 0,
}


# ============================================================
# TIME
# ============================================================

TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def now_ts():
    return int(time.time())


def utc_now():
    return datetime.now(timezone.utc)


def format_time(ts):
    if ts is None:
        return "-"

    try:
        if isinstance(ts, str):
            dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        else:
            dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)

        tehran = dt.astimezone(TEHRAN_TZ)

        return tehran.strftime("%Y-%m-%d %H:%M")

    except Exception:
        return "-"


def duration_text(start_ts, end_ts=None):
    if not start_ts:
        return "-"

    try:
        if end_ts is None:
            end_ts = now_ts()

        seconds = max(0, int(float(end_ts) - float(start_ts)))

        minutes = seconds // 60
        hours = minutes // 60
        minutes %= 60

        if hours > 0:
            return f"{hours}h {minutes}m"

        return f"{minutes}m"

    except Exception:
        return "-"


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()


def http_get(url, params=None):
    last_error = None

    for attempt in range(HTTP_RETRIES):
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

            if attempt < HTTP_RETRIES - 1:
                time.sleep(1.5 * (attempt + 1))

    raise last_error


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send_text(text):
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
        r = SESSION.post(
            url,
            json=payload,
            timeout=20,
        )

        r.raise_for_status()

        return True

    except Exception as e:
        print("Telegram text error:", e)
        return False


def telegram_send_photo(photo_path, caption=""):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendPhoto"
    )

    try:
        with open(photo_path, "rb") as f:
            files = {
                "photo": f
            }

            data = {
                "chat_id": TELEGRAM_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            }

            r = SESSION.post(
                url,
                data=data,
                files=files,
                timeout=30,
            )

        r.raise_for_status()

        return True

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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            asset TEXT,
            direction TEXT,
            pattern TEXT,
            signal_key TEXT UNIQUE,

            pattern_time TEXT,
            breakout_time TEXT,
            retest_time TEXT,
            confirmation_time TEXT,
            entry_time TEXT,

            entry_price REAL,
            sl_price REAL,
            tp_price REAL,
            current_price REAL,

            status TEXT,

            exit_time TEXT,
            exit_price REAL,
            exit_reason TEXT,

            created_at INTEGER,
            updated_at INTEGER
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS scanner_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    conn.commit()

    # --------------------------------------------------------
    # Migration for existing DB
    # --------------------------------------------------------

    cur.execute("PRAGMA table_info(trades)")

    columns = {
        row["name"]
        for row in cur.fetchall()
    }

    required_columns = {
        "symbol": "TEXT",
        "asset": "TEXT",
        "direction": "TEXT",
        "pattern": "TEXT",
        "signal_key": "TEXT",
        "pattern_time": "TEXT",
        "breakout_time": "TEXT",
        "retest_time": "TEXT",
        "confirmation_time": "TEXT",
        "entry_time": "TEXT",
        "entry_price": "REAL",
        "sl_price": "REAL",
        "tp_price": "REAL",
        "current_price": "REAL",
        "status": "TEXT",
        "exit_time": "TEXT",
        "exit_price": "REAL",
        "exit_reason": "TEXT",
        "created_at": "INTEGER",
        "updated_at": "INTEGER",
    }

    for name, dtype in required_columns.items():

        if name not in columns:

            try:
                cur.execute(
                    f"ALTER TABLE trades ADD COLUMN {name} {dtype}"
                )
            except Exception as e:
                print(
                    f"Could not add column {name}: {e}"
                )

    # Existing rows
    try:
        cur.execute(
            """
            UPDATE trades
            SET symbol = asset
            WHERE symbol IS NULL
            """
        )
    except Exception:
        pass

    # Unique index
    try:
        cur.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_trades_signal_key
            ON trades(signal_key)
            """
        )
    except Exception:
        pass

    conn.commit()
    conn.close()


# ============================================================
# META
# ============================================================

def get_meta(key):
    conn = db_connect()

    row = conn.execute(
        "SELECT value FROM scanner_meta WHERE key=?",
        (key,),
    ).fetchone()

    conn.close()

    return row["value"] if row else None


def set_meta(key, value):
    conn = db_connect()

    conn.execute(
        """
        INSERT INTO scanner_meta(key, value)
        VALUES(?, ?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, str(value)),
    )

    conn.commit()
    conn.close()


# ============================================================
# KRAKEN
# ============================================================

def load_contracts():

    global CONTRACTS

    data = http_get(KRAKEN_INSTRUMENTS_URL)

    instruments = data.get("instruments", [])

    found = {}

    for item in instruments:

        symbol = item.get("symbol") or item.get("contract")

        if not symbol:
            continue

        found[symbol] = item

    CONTRACTS = found

    print(
        f"Loaded {len(CONTRACTS)} Kraken instruments"
    )


def get_contract(asset):

    if asset in CONTRACT_MAP:
        return CONTRACT_MAP[asset]

    return f"PF_{asset}USD"


def load_prices():

    global LIVE_PRICES

    data = http_get(KRAKEN_TICKERS_URL)

    tickers = data.get("tickers", [])

    prices = {}

    for t in tickers:

        symbol = t.get("symbol")

        if not symbol:
            continue

        price = (
            t.get("last")
            or t.get("lastPrice")
            or t.get("markPrice")
        )

        try:
            prices[symbol] = float(price)
        except Exception:
            continue

    LIVE_PRICES = prices


def get_live_price(asset):

    contract = get_contract(asset)

    price = LIVE_PRICES.get(contract)

    if price is not None:
        return float(price)

    return None


# ============================================================
# CANDLES
# ============================================================

def get_candles(asset, interval, limit=300):

    contract = get_contract(asset)

    url = (
        f"{KRAKEN_CHART_URL}/"
        f"{contract}/{interval}"
    )

    data = http_get(url)

    candles = data.get("candles", data)

    if not isinstance(candles, list):
        return pd.DataFrame()

    rows = []

    for c in candles:

        try:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("t")
                )

                op = (
                    c.get("open")
                    or c.get("o")
                )

                hi = (
                    c.get("high")
                    or c.get("h")
                )

                lo = (
                    c.get("low")
                    or c.get("l")
                )

                cl = (
                    c.get("close")
                    or c.get("c")
                )

            else:

                if len(c) < 5:
                    continue

                ts = c[0]
                op = c[1]
                hi = c[2]
                lo = c[3]
                cl = c[4]

            rows.append(
                {
                    "time": int(float(ts)),
                    "open": float(op),
                    "high": float(hi),
                    "low": float(lo),
                    "close": float(cl),
                }
            )

        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = df.drop_duplicates("time")

    df = df.sort_values("time")

    # --------------------------------------------------------
    # Remove currently open candle.
    # --------------------------------------------------------

    now = now_ts()

    interval_seconds = {
        "1h": 3600,
        "15m": 900,
        "5m": 300,
    }.get(interval, 900)

    df = df[
        (df["time"] + interval_seconds) <= now
    ]

    if limit:
        df = df.tail(limit)

    df = df.reset_index(drop=True)

    return df


# ============================================================
# LOCAL EXTREMA
# ============================================================

def is_local_high(df, i, left=2, right=2):

    if i < left or i + right >= len(df):
        return False

    value = df.iloc[i]["high"]

    left_values = df.iloc[
        i-left:i
    ]["high"]

    right_values = df.iloc[
        i+1:i+right+1
    ]["high"]

    return (
        value >= left_values.max()
        and value >= right_values.max()
    )


def is_local_low(df, i, left=2, right=2):

    if i < left or i + right >= len(df):
        return False

    value = df.iloc[i]["low"]

    left_values = df.iloc[
        i-left:i
    ]["low"]

    right_values = df.iloc[
        i+1:i+right+1
    ]["low"]

    return (
        value <= left_values.min()
        and value <= right_values.min()
    )


def pivot_highs(df):

    result = []

    for i in range(len(df)):

        if is_local_high(df, i):
            result.append(i)

    return result


def pivot_lows(df):

    result = []

    for i in range(len(df)):

        if is_local_low(df, i):
            result.append(i)

    return result


# ============================================================
# PATTERN DETECTION
# ============================================================

def pct_diff(a, b):

    if a == 0:
        return 999

    return abs(a - b) / abs(a)


def make_pattern(
    name,
    direction,
    points,
    neckline,
    target,
    stop,
    pattern_time,
    quality,
):

    return {
        "pattern": name,
        "direction": direction,
        "points": points,
        "neckline": float(neckline),
        "target": float(target),
        "stop": float(stop),
        "pattern_time": int(pattern_time),
        "quality": float(quality),
    }


def detect_double_top(df, latest_only=False):

    highs = pivot_highs(df)
    lows = pivot_lows(df)

    patterns = []

    for a in range(len(highs)):

        i1 = highs[a]

        for b in range(a + 1, len(highs)):

            i2 = highs[b]

            if i2 - i1 < MIN_PATTERN_SEPARATION_BARS:
                continue

            h1 = float(df.iloc[i1]["high"])
            h2 = float(df.iloc[i2]["high"])

            if pct_diff(h1, h2) > DOUBLE_LEVEL_TOLERANCE:
                continue

            between_lows = [
                x for x in lows
                if i1 < x < i2
            ]

            if not between_lows:
                continue

            valley_i = min(
                between_lows,
                key=lambda x: df.iloc[x]["low"]
            )

            valley = float(
                df.iloc[valley_i]["low"]
            )

            peak = max(h1, h2)

            depth = (peak - valley) / peak

            if depth < MIN_PATTERN_DEPTH_PCT:
                continue

            neckline = valley

            height = peak - neckline

            target = neckline - height

            stop = max(h1, h2) * (
                1 + INVALIDATION_BUFFER
            )

            quality = (
                depth * 100
                + (1 - pct_diff(h1, h2)) * 2
            )

            patterns.append(
                make_pattern(
                    "Double Top",
                    "SHORT",
                    {
                        "left": i1,
                        "valley": valley_i,
                        "right": i2,
                    },
                    neckline,
                    target,
                    stop,
                    int(df.iloc[i2]["time"]),
                    quality,
                )
            )

    return patterns


def detect_double_bottom(df, latest_only=False):

    lows = pivot_lows(df)
    highs = pivot_highs(df)

    patterns = []

    for a in range(len(lows)):

        i1 = lows[a]

        for b in range(a + 1, len(lows)):

            i2 = lows[b]

            if i2 - i1 < MIN_PATTERN_SEPARATION_BARS:
                continue

            l1 = float(df.iloc[i1]["low"])
            l2 = float(df.iloc[i2]["low"])

            if pct_diff(l1, l2) > DOUBLE_LEVEL_TOLERANCE:
                continue

            between_highs = [
                x for x in highs
                if i1 < x < i2
            ]

            if not between_highs:
                continue

            valley_i = max(
                between_highs,
                key=lambda x: df.iloc[x]["high"]
            )

            peak = float(
                df.iloc[valley_i]["high"]
            )

            bottom = min(l1, l2)

            depth = (peak - bottom) / peak

            if depth < MIN_PATTERN_DEPTH_PCT:
                continue

            neckline = peak

            height = neckline - bottom

            target = neckline + height

            stop = min(l1, l2) * (
                1 - INVALIDATION_BUFFER
            )

            quality = (
                depth * 100
                + (1 - pct_diff(l1, l2)) * 2
            )

            patterns.append(
                make_pattern(
                    "Double Bottom",
                    "LONG",
                    {
                        "left": i1,
                        "valley": valley_i,
                        "right": i2,
                    },
                    neckline,
                    target,
                    stop,
                    int(df.iloc[i2]["time"]),
                    quality,
                )
            )

    return patterns


def detect_head_shoulders(df):

    highs = pivot_highs(df)
    lows = pivot_lows(df)

    patterns = []

    for a in range(len(highs)):

        left = highs[a]

        for b in range(a + 1, len(highs)):

            head = highs[b]

            if head - left < MIN_PATTERN_SEPARATION_BARS:
                continue

            for c in range(b + 1, len(highs)):

                right = highs[c]

                if right - head < MIN_PATTERN_SEPARATION_BARS:
                    continue

                left_h = float(
                    df.iloc[left]["high"]
                )

                head_h = float(
                    df.iloc[head]["high"]
                )

                right_h = float(
                    df.iloc[right]["high"]
                )

                shoulder_diff = pct_diff(
                    left_h,
                    right_h,
                )

                if shoulder_diff > HS_SHOULDER_TOLERANCE:
                    continue

                advantage = (
                    head_h
                    - max(left_h, right_h)
                ) / max(left_h, right_h)

                if advantage < HS_MIN_HEAD_ADVANTAGE_PCT:
                    continue

                lows_between = [
                    x for x in lows
                    if left < x < head
                ]

                lows_after = [
                    x for x in lows
                    if head < x < right
                ]

                if not lows_between or not lows_after:
                    continue

                neckline_left = min(
                    lows_between,
                    key=lambda x: df.iloc[x]["low"]
                )

                neckline_right = min(
                    lows_after,
                    key=lambda x: df.iloc[x]["low"]
                )

                nl1 = float(
                    df.iloc[neckline_left]["low"]
                )

                nl2 = float(
                    df.iloc[neckline_right]["low"]
                )

                neckline = (nl1 + nl2) / 2

                depth = (
                    head_h - neckline
                ) / head_h

                if depth < MIN_HS_DEPTH_PCT:
                    continue

                height = head_h - neckline

                target = neckline - height

                stop = head_h * (
                    1 + INVALIDATION_BUFFER
                )

                quality = (
                    depth * 100
                    + (1 - shoulder_diff) * 2
                )

                patterns.append(
                    make_pattern(
                        "Head & Shoulders",
                        "SHORT",
                        {
                            "left_shoulder": left,
                            "head": head,
                            "right_shoulder": right,
                            "neckline_left": neckline_left,
                            "neckline_right": neckline_right,
                        },
                        neckline,
                        target,
                        stop,
                        int(df.iloc[right]["time"]),
                        quality,
                    )
                )

    return patterns


def detect_inverse_head_shoulders(df):

    lows = pivot_lows(df)
    highs = pivot_highs(df)

    patterns = []

    for a in range(len(lows)):

        left = lows[a]

        for b in range(a + 1, len(lows)):

            head = lows[b]

            if head - left < MIN_PATTERN_SEPARATION_BARS:
                continue

            for c in range(b + 1, len(lows)):

                right = lows[c]

                if right - head < MIN_PATTERN_SEPARATION_BARS:
                    continue

                left_l = float(
                    df.iloc[left]["low"]
                )

                head_l = float(
                    df.iloc[head]["low"]
                )

                right_l = float(
                    df.iloc[right]["low"]
                )

                shoulder_diff = pct_diff(
                    left_l,
                    right_l,
                )

                if shoulder_diff > HS_SHOULDER_TOLERANCE:
                    continue

                advantage = (
                    min(left_l, right_l)
                    - head_l
                ) / min(left_l, right_l)

                if advantage < HS_MIN_HEAD_ADVANTAGE_PCT:
                    continue

                highs_between = [
                    x for x in highs
                    if left < x < head
                ]

                highs_after = [
                    x for x in highs
                    if head < x < right
                ]

                if not highs_between or not highs_after:
                    continue

                neckline_left = max(
                    highs_between,
                    key=lambda x: df.iloc[x]["high"]
                )

                neckline_right = max(
                    highs_after,
                    key=lambda x: df.iloc[x]["high"]
                )

                nl1 = float(
                    df.iloc[neckline_left]["high"]
                )

                nl2 = float(
                    df.iloc[neckline_right]["high"]
                )

                neckline = (nl1 + nl2) / 2

                depth = (
                    neckline - head_l
                ) / neckline

                if depth < MIN_HS_DEPTH_PCT:
                    continue

                height = neckline - head_l

                target = neckline + height

                stop = head_l * (
                    1 - INVALIDATION_BUFFER
                )

                quality = (
                    depth * 100
                    + (1 - shoulder_diff) * 2
                )

                patterns.append(
                    make_pattern(
                        "Inverse Head & Shoulders",
                        "LONG",
                        {
                            "left_shoulder": left,
                            "head": head,
                            "right_shoulder": right,
                            "neckline_left": neckline_left,
                            "neckline_right": neckline_right,
                        },
                        neckline,
                        target,
                        stop,
                        int(df.iloc[right]["time"]),
                        quality,
                    )
                )

    return patterns


def detect_patterns(df):

    if df is None or len(df) < 20:
        return []

    patterns = []

    patterns.extend(
        detect_double_top(df)
    )

    patterns.extend(
        detect_double_bottom(df)
    )

    patterns.extend(
        detect_head_shoulders(df)
    )

    patterns.extend(
        detect_inverse_head_shoulders(df)
    )

    patterns.sort(
        key=lambda x: (
            x["pattern_time"],
            x["quality"],
        ),
        reverse=True,
    )

    return patterns


# ============================================================
# PATTERN MATCHING
# ============================================================

def pattern_direction(pattern_name):

    if pattern_name in (
        "Double Bottom",
        "Inverse Head & Shoulders",
    ):
        return "LONG"

    return "SHORT"


def pattern_matches(p1, p2):

    return (
        p1["pattern"] == p2["pattern"]
        and p1["direction"] == p2["direction"]
    )


# ============================================================
# 1H BREAKOUT
# ============================================================

def find_1h_breakout(df, pattern):

    start_time = pattern["pattern_time"]

    eligible = df[
        df["time"] > start_time
    ].copy()

    eligible = eligible.head(
        BREAKOUT_LOOKAHEAD_1H
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            if row["close"] > neckline:

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    else:

        for _, row in eligible.iterrows():

            if row["close"] < neckline:

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    return None


# ============================================================
# 15M PATTERN
# ============================================================

def find_15m_same_pattern(
    df15,
    pattern_name,
    direction,
    after_time,
):

    patterns = detect_patterns(df15)

    candidates = []

    for p in patterns:

        if p["pattern"] != pattern_name:
            continue

        if p["direction"] != direction:
            continue

        if p["pattern_time"] <= after_time:
            continue

        age = now_ts() - p["pattern_time"]

        if age > RECENT_PATTERN_HOURS_15M * 3600:
            continue

        candidates.append(p)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x["pattern_time"],
            x["quality"],
        ),
        reverse=True,
    )

    return candidates[0]


# ============================================================
# 15M BREAKOUT
# ============================================================

def find_15m_breakout(df15, pattern):

    eligible = df15[
        df15["time"] > pattern["pattern_time"]
    ].copy()

    eligible = eligible.head(
        BREAKOUT_LOOKAHEAD_15M
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            if row["close"] > neckline:

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    else:

        for _, row in eligible.iterrows():

            if row["close"] < neckline:

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    return None


# ============================================================
# 15M RETEST
# ============================================================

def find_15m_retest(
    df15,
    pattern,
    breakout,
):

    eligible = df15[
        df15["time"] > breakout["time"]
    ].copy()

    eligible = eligible.head(
        RETEST_LOOKAHEAD_15M
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            # Retest from above.
            if (
                row["low"] <= neckline
                and row["high"] >= neckline
            ):

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    else:

        for _, row in eligible.iterrows():

            # Retest from below.
            if (
                row["high"] >= neckline
                and row["low"] <= neckline
            ):

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    return None


# ============================================================
# 15M CONFIRMATION
# ============================================================

def find_15m_confirmation(
    df15,
    pattern,
    retest,
):

    eligible = df15[
        df15["time"] > retest["time"]
    ].copy()

    eligible = eligible.head(
        CONFIRMATION_LOOKAHEAD_15M
    )

    if eligible.empty:
        return None

    neckline = pattern["neckline"]

    if pattern["direction"] == "LONG":

        for _, row in eligible.iterrows():

            # Confirmation must close back above neckline
            # and be bullish.
            if (
                row["close"] > neckline
                and row["close"] > row["open"]
            ):

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    else:

        for _, row in eligible.iterrows():

            # Confirmation must close back below neckline
            # and be bearish.
            if (
                row["close"] < neckline
                and row["close"] < row["open"]
            ):

                return {
                    "time": int(row["time"]),
                    "price": float(row["close"]),
                    "index": int(row.name),
                }

    return None


# ============================================================
# NATURAL TP / SL
# ============================================================

def natural_tp_sl(pattern):

    target = float(pattern["target"])
    stop = float(pattern["stop"])

    return target, stop


# ============================================================
# SIGNAL KEY
# ============================================================

def build_signal_key(
    asset,
    p1h,
    breakout1h,
    p15,
    breakout15,
    retest15,
    confirmation15,
):

    return (
        f"{asset}|"
        f"{p1h['pattern']}|"
        f"{p1h['pattern_time']}|"
        f"{breakout1h['time']}|"
        f"{p15['pattern_time']}|"
        f"{breakout15['time']}|"
        f"{retest15['time']}|"
        f"{confirmation15['time']}"
    )


# ============================================================
# DB HELPERS
# ============================================================

def signal_exists(signal_key):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE signal_key=?
        LIMIT 1
        """,
        (signal_key,),
    ).fetchone()

    conn.close()

    return row is not None


def open_trade_exists(asset, direction):

    conn = db_connect()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE asset=?
          AND direction=?
          AND status='OPEN'
        LIMIT 1
        """,
        (asset, direction),
    ).fetchone()

    conn.close()

    return row is not None


def insert_trade(signal):

    conn = db_connect()

    now = now_ts()

    columns = [
        "symbol",
        "asset",
        "direction",
        "pattern",
        "signal_key",
        "pattern_time",
        "breakout_time",
        "retest_time",
        "confirmation_time",
        "entry_time",
        "entry_price",
        "sl_price",
        "tp_price",
        "current_price",
        "status",
        "created_at",
        "updated_at",
    ]

    values = [
        signal["symbol"],
        signal["asset"],
        signal["direction"],
        signal["pattern"],
        signal["signal_key"],
        signal["pattern_time"],
        signal["breakout_time"],
        signal["retest_time"],
        signal["confirmation_time"],
        signal["entry_time"],
        signal["entry_price"],
        signal["sl_price"],
        signal["tp_price"],
        signal["entry_price"],
        "OPEN",
        now,
        now,
    ]

    placeholders = ",".join(
        ["?"] * len(columns)
    )

    sql = f"""
        INSERT OR IGNORE INTO trades
        ({",".join(columns)})
        VALUES ({placeholders})
    """

    cur = conn.execute(
        sql,
        values,
    )

    inserted = cur.rowcount == 1

    conn.commit()
    conn.close()

    return inserted


# ============================================================
# BUILD SIGNAL
# ============================================================

def build_candidate(asset):

    try:

        df1h = get_candles(
            asset,
            "1h",
            PATTERN_LOOKBACK_1H,
        )

        if len(df1h) < 30:
            return None

        df15 = get_candles(
            asset,
            "15m",
            PATTERN_LOOKBACK_15M,
        )

        if len(df15) < 40:
            return None

        patterns1h = detect_patterns(df1h)

        if not patterns1h:
            return None

        # ----------------------------------------------------
        # Most recent valid 1H patterns first
        # ----------------------------------------------------

        patterns1h = [
            p for p in patterns1h
            if (
                now_ts() - p["pattern_time"]
                <= RECENT_PATTERN_HOURS_1H * 3600
            )
        ]

        patterns1h.sort(
            key=lambda x: (
                x["pattern_time"],
                x["quality"],
            ),
            reverse=True,
        )

        for p1h in patterns1h:

            breakout1h = find_1h_breakout(
                df1h,
                p1h,
            )

            if breakout1h is None:
                continue

            p15 = find_15m_same_pattern(
                df15,
                p1h["pattern"],
                p1h["direction"],
                breakout1h["time"],
            )

            if p15 is None:
                continue

            breakout15 = find_15m_breakout(
                df15,
                p15,
            )

            if breakout15 is None:
                continue

            retest15 = find_15m_retest(
                df15,
                p15,
                breakout15,
            )

            if retest15 is None:
                continue

            confirmation15 = find_15m_confirmation(
                df15,
                p15,
                retest15,
            )

            if confirmation15 is None:
                continue

            entry_price = float(
                confirmation15["price"]
            )

            tp_price, sl_price = natural_tp_sl(
                p15
            )

            # ------------------------------------------------
            # Sanity check only.
            # This does NOT impose an RR filter.
            # ------------------------------------------------

            if p15["direction"] == "LONG":

                if not (
                    sl_price < entry_price
                    and tp_price > entry_price
                ):
                    continue

            else:

                if not (
                    sl_price > entry_price
                    and tp_price < entry_price
                ):
                    continue

            signal_key = build_signal_key(
                asset,
                p1h,
                breakout1h,
                p15,
                breakout15,
                retest15,
                confirmation15,
            )

            if signal_exists(signal_key):
                continue

            if open_trade_exists(
                asset,
                p15["direction"],
            ):
                continue

            candidate = {
                "asset": asset,
                "symbol": get_contract(asset),
                "direction": p15["direction"],
                "pattern": p15["pattern"],

                "signal_key": signal_key,

                "pattern_time": p15["pattern_time"],

                "breakout_time": breakout15["time"],

                "retest_time": retest15["time"],

                "confirmation_time":
                    confirmation15["time"],

                "entry_time":
                    confirmation15["time"],

                "entry_price": entry_price,

                "sl_price": sl_price,

                "tp_price": tp_price,

                "pattern_1h": p1h,

                "pattern_15m": p15,

                "breakout_1h": breakout1h,

                "breakout_15m": breakout15,

                "retest_15m": retest15,

                "confirmation_15m":
                    confirmation15,

                "score": (
                    p1h["quality"]
                    + p15["quality"]
                ),
            }

            return candidate

    except Exception as e:

        print(
            f"Candidate error {asset}: {e}"
        )

        traceback.print_exc()

    return None


# ============================================================
# CHART
# ============================================================

def create_15m_chart(
    asset,
    df15,
    signal,
):

    p = signal["pattern_15m"]

    start_time = min(
        p["pattern_time"],
        signal["breakout_15m"]["time"],
    )

    chart_df = df15[
        df15["time"] >= (
            start_time - 30 * 900
        )
    ].tail(CHART_CANDLES).copy()

    if chart_df.empty:
        return None

    fig, ax = plt.subplots(
        figsize=(13, 7)
    )

    x = np.arange(len(chart_df))

    ax.plot(
        x,
        chart_df["close"].values,
        linewidth=1.4,
        label="15M Close",
    )

    # --------------------------------------------------------
    # Pattern points
    # --------------------------------------------------------

    points = p["points"]

    point_labels = {
        "left": "L",
        "valley": "V",
        "right": "R",
        "left_shoulder": "LS",
        "head": "H",
        "right_shoulder": "RS",
        "neckline_left": "NL",
        "neckline_right": "NR",
    }

    for key, idx in points.items():

        if idx >= len(df15):
            continue

        ts = int(df15.iloc[idx]["time"])

        matches = np.where(
            chart_df["time"].values == ts
        )[0]

        if len(matches) == 0:
            continue

        x_pos = matches[0]

        if "shoulder" in key or key in (
            "left",
            "head",
            "right",
        ):
            y = (
                chart_df.iloc[x_pos]["high"]
            )
        else:
            y = (
                chart_df.iloc[x_pos]["low"]
            )

        label = point_labels.get(
            key,
            key,
        )

        ax.scatter(
            x_pos,
            y,
            s=60,
            zorder=5,
        )

        ax.annotate(
            label,
            (x_pos, y),
            xytext=(0, 10),
            textcoords="offset points",
            ha="center",
            fontsize=10,
            fontweight="bold",
        )

    # --------------------------------------------------------
    # Neckline
    # --------------------------------------------------------

    neckline = p["neckline"]

    ax.axhline(
        neckline,
        linestyle="--",
        linewidth=1.2,
        label=f"Neckline {neckline:.8g}",
    )

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    def mark_event(event, label):

        ts = event["time"]

        matches = np.where(
            chart_df["time"].values == ts
        )[0]

        if len(matches) == 0:
            return

        xx = matches[0]
        yy = event["price"]

        ax.scatter(
            xx,
            yy,
            s=70,
            zorder=6,
        )

        ax.annotate(
            label,
            (xx, yy),
            xytext=(0, -18),
            textcoords="offset points",
            ha="center",
            fontsize=9,
        )

    mark_event(
        signal["breakout_15m"],
        "BREAKOUT",
    )

    mark_event(
        signal["retest_15m"],
        "RETEST",
    )

    mark_event(
        signal["confirmation_15m"],
        "ENTRY",
    )

    # --------------------------------------------------------
    # TP / SL
    # --------------------------------------------------------

    ax.axhline(
        signal["tp_price"],
        linestyle=":",
        linewidth=1.5,
        label=f"TP {signal['tp_price']:.8g}",
    )

    ax.axhline(
        signal["sl_price"],
        linestyle=":",
        linewidth=1.5,
        label=f"SL {signal['sl_price']:.8g}",
    )

    ax.set_title(
        f"{asset} | 15M {signal['pattern']} | "
        f"{signal['direction']}"
    )

    ax.set_xlabel(
        "15M Closed Candles"
    )

    ax.set_ylabel(
        "Price"
    )

    ax.grid(
        alpha=0.20
    )

    ax.legend(
        loc="best",
        fontsize=8,
    )

    plt.tight_layout()

    filename = (
        f"chart_{asset}_"
        f"{signal['confirmation_time']}.png"
    )

    path = os.path.join(
        "/tmp",
        filename,
    )

    fig.savefig(
        path,
        dpi=140,
        bbox_inches="tight",
    )

    plt.close(fig)

    return path


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def signal_caption(signal):

    emoji = (
        "🟢"
        if signal["direction"] == "LONG"
        else "🔴"
    )

    entry = signal["entry_price"]
    tp = signal["tp_price"]
    sl = signal["sl_price"]

    if entry != 0:

        tp_pct = (
            (tp - entry) / entry * 100
        )

        sl_pct = (
            (sl - entry) / entry * 100
        )

    else:

        tp_pct = 0
        sl_pct = 0

    return (
        f"<b>{emoji} NEW SIGNAL</b>\n\n"
        f"<b>{signal['asset']} "
        f"{signal['direction']}</b>\n"
        f"📌 Pattern: "
        f"{signal['pattern']}\n\n"
        f"💰 Entry: "
        f"{entry:.10g}\n"
        f"🎯 TP: "
        f"{tp:.10g} "
        f"({tp_pct:+.2f}%)\n"
        f"🛑 SL: "
        f"{sl:.10g} "
        f"({sl_pct:+.2f}%)\n\n"
        f"🕐 1H Pattern: "
        f"{format_time(signal['pattern_1h']['pattern_time'])}\n"
        f"💥 1H Breakout: "
        f"{format_time(signal['breakout_1h']['time'])}\n"
        f"🔹 15M Pattern: "
        f"{format_time(signal['pattern_15m']['pattern_time'])}\n"
        f"💥 15M Breakout: "
        f"{format_time(signal['breakout_15m']['time'])}\n"
        f"🔄 15M Retest: "
        f"{format_time(signal['retest_15m']['time'])}\n"
        f"✅ Confirmation: "
        f"{format_time(signal['confirmation_15m']['time'])}\n\n"
        f"⚙️ TP/SL: Natural Pattern Levels\n"
        f"📊 Timeframe: 15M"
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    reason,
):

    conn = db_connect()

    now = now_ts()

    conn.execute(
        """
        UPDATE trades
        SET
            status='CLOSED',
            exit_time=?,
            exit_price=?,
            exit_reason=?,
            current_price=?,
            updated_at=?
        WHERE id=?
        """,
        (
            now,
            exit_price,
            reason,
            exit_price,
            now,
            trade_id,
        ),
    )

    conn.commit()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE id=?
        """,
        (trade_id,),
    ).fetchone()

    conn.close()

    return row


def check_trade_exit(trade):

    asset = trade["asset"]

    price = get_live_price(asset)

    if price is None:
        return None

    entry = float(
        trade["entry_price"]
    )

    tp = float(
        trade["tp_price"]
    )

    sl = float(
        trade["sl_price"]
    )

    direction = trade["direction"]

    exit_reason = None

    if direction == "LONG":

        if price >= tp:
            exit_reason = "TP"

        elif price <= sl:
            exit_reason = "SL"

    else:

        if price <= tp:
            exit_reason = "TP"

        elif price >= sl:
            exit_reason = "SL"

    # --------------------------------------------------------
    # Maximum holding time
    # --------------------------------------------------------

    if exit_reason is None:

        entry_time = trade["entry_time"]

        try:
            if isinstance(entry_time, str):
                dt = datetime.fromisoformat(
                    entry_time.replace(
                        "Z",
                        "+00:00",
                    )
                )

                entry_ts = dt.timestamp()

            else:
                entry_ts = float(
                    entry_time
                )

        except Exception:
            entry_ts = None

        if entry_ts:

            if (
                now_ts() - entry_ts
                >= MAX_HOLD_HOURS * 3600
            ):
                exit_reason = "TIME"

    return {
        "price": price,
        "reason": exit_reason,
    }


# ============================================================
# OPEN TRADE RECONCILIATION
# ============================================================

def reconcile_open_trades():

    conn = db_connect()

    trades = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    for trade in trades:

        try:

            result = check_trade_exit(
                trade
            )

            if result is None:
                continue

            price = result["price"]
            reason = result["reason"]

            # Update current price even if still open.
            conn2 = db_connect()

            conn2.execute(
                """
                UPDATE trades
                SET
                    current_price=?,
                    updated_at=?
                WHERE id=?
                """,
                (
                    price,
                    now_ts(),
                    trade["id"],
                ),
            )

            conn2.commit()
            conn2.close()

            if reason is None:
                continue

            closed = close_trade(
                trade["id"],
                price,
                reason,
            )

            if closed:

                send_close_message(
                    closed
                )

                STATS["closed_trades"] += 1

        except Exception as e:

            print(
                "Reconcile error:",
                e,
            )

            STATS["errors"] += 1


# ============================================================
# CLOSE MESSAGE
# ============================================================

def send_close_message(trade):

    entry = float(
        trade["entry_price"]
    )

    exit_price = float(
        trade["exit_price"]
    )

    direction = trade["direction"]

    if direction == "LONG":

        pnl_pct = (
            (exit_price - entry)
            / entry
            * 100
        )

    else:

        pnl_pct = (
            (entry - exit_price)
            / entry
            * 100
        )

    reason = trade["exit_reason"]

    if reason == "TP":
        icon = "🎯"
    elif reason == "SL":
        icon = "🛑"
    else:
        icon = "⏱️"

    text = (
        f"<b>{icon} CLOSED TRADE</b>\n\n"
        f"<b>{trade['asset']} "
        f"{direction}</b>\n"
        f"📌 Pattern: "
        f"{trade['pattern']}\n"
        f"💰 Entry: "
        f"{entry:.10g}\n"
        f"💵 Exit: "
        f"{exit_price:.10g}\n"
        f"📊 Result: "
        f"{pnl_pct:+.2f}%\n"
        f"📌 Reason: "
        f"{reason}\n"
        f"🕐 Entry: "
        f"{format_time(trade['entry_time'])}\n"
        f"🕐 Exit: "
        f"{format_time(trade['exit_time'])}\n"
        f"⏱ Duration: "
        f"{duration_text("
        f"trade['created_at'], "
        f"trade['exit_time']"
        f")}"
    )

    if telegram_send_text(text):
        STATS["signals_sent"] += 1


# ============================================================
# OPEN TRADES REPORT
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY entry_time DESC
        """
    ).fetchall()

    conn.close()

    return rows


def open_trades_text():

    trades = get_open_trades()

    if not trades:
        return (
            "<b>📂 OPEN TRADES</b>\n"
            "No open trades."
        )

    lines = [
        f"<b>📂 OPEN TRADES: "
        f"{len(trades)}</b>\n"
    ]

    for trade in trades:

        price = (
            get_live_price(
                trade["asset"]
            )
        )

        if price is None:
            price = trade["current_price"]

        entry = float(
            trade["entry_price"]
        )

        if trade["direction"] == "LONG":

            pnl = (
                (price - entry)
                / entry
                * 100
            )

        else:

            pnl = (
                (entry - price)
                / entry
                * 100
            )

        emoji = (
            "🟢"
            if trade["direction"] == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} "
            f"<b>{trade['asset']} "
            f"{trade['direction']}</b>\n"
            f"📌 {trade['pattern']}\n"
            f"💰 Entry: {entry:.10g}\n"
            f"💵 Current: {price:.10g} "
            f"({pnl:+.2f}%)\n"
            f"🎯 TP: {float(trade['tp_price']):.10g}\n"
            f"🛑 SL: {float(trade['sl_price']):.10g}\n"
            f"⏱ Duration: "
            f"{duration_text(trade['created_at'])}\n"
        )

    return "\n".join(lines)


# ============================================================
# PERFORMANCE
# ============================================================

def performance_text():

    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        """
    ).fetchone()[0]

    closed = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchall()

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()[0]

    conn.close()

    wins = 0
    losses = 0
    total_pnl = 0.0

    for trade in closed:

        try:

            entry = float(
                trade["entry_price"]
            )

            exit_price = float(
                trade["exit_price"]
            )

            if trade["direction"] == "LONG":

                pnl = (
                    exit_price - entry
                ) / entry * 100

            else:

                pnl = (
                    entry - exit_price
                ) / entry * 100

            total_pnl += pnl

            if pnl > 0:
                wins += 1
            elif pnl < 0:
                losses += 1

        except Exception:
            continue

    closed_count = len(closed)

    if closed_count:

        win_rate = (
            wins / closed_count * 100
        )

    else:

        win_rate = 0

    return (
        "<b>📈 PERFORMANCE</b>\n\n"
        f"Total Trades: {total}\n"
        f"Closed: {closed_count}\n"
        f"Open: {open_count}\n"
        f"🟢 Wins: {wins}\n"
        f"🔴 Losses: {losses}\n"
        f"Win Rate: {win_rate:.2f}%\n"
        f"Net PnL: {total_pnl:+.2f}%"
    )


# ============================================================
# PERIODIC REPORT
# ============================================================

def periodic_report():

    last = get_meta(
        "last_periodic_report"
    )

    current = now_ts()

    if last:

        try:

            if (
                current - int(float(last))
                < PERIODIC_REPORT_SECONDS
            ):
                return

        except Exception:
            pass

    report = (
        f"<b>📊 KRAKEN PATTERN SCANNER</b>\n\n"
        f"Version: {VERSION}\n\n"
        f"{open_trades_text()}\n\n"
        f"{performance_text()}\n\n"
        f"<b>🔎 SCAN STATS</b>\n"
        f"Scans: {STATS['scans']}\n"
        f"Candidates: {STATS['candidates']}\n"
        f"New Entries: {STATS['unique_entries']}\n"
        f"Closed: {STATS['closed_trades']}\n"
    )

    if telegram_send_text(report):

        set_meta(
            "last_periodic_report",
            current,
        )


# ============================================================
# NEW SIGNAL
# ============================================================

def send_new_signal(
    signal,
    df15,
):

    caption = signal_caption(
        signal
    )

    chart = create_15m_chart(
        signal["asset"],
        df15,
        signal,
    )

    sent = False

    if chart and os.path.exists(chart):

        sent = telegram_send_photo(
            chart,
            caption,
        )

        try:
            os.remove(chart)
        except Exception:
            pass

    else:

        sent = telegram_send_text(
            caption
        )

    if sent:
        STATS["signals_sent"] += 1

    return sent


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    STATS["scans"] += 1

    load_prices()

    candidates = []

    for asset in ASSETS:

        try:

            candidate = build_candidate(
                asset
            )

            if candidate:

                candidates.append(
                    candidate
                )

                STATS["candidates"] += 1

        except Exception as e:

            print(
                f"Scan error {asset}: {e}"
            )

            STATS["errors"] += 1

    if not candidates:
        return

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    selected = []

    for candidate in candidates:

        if len(selected) >= MAX_SIGNALS_PER_SCAN:
            break

        if signal_exists(
            candidate["signal_key"]
        ):
            continue

        if open_trade_exists(
            candidate["asset"],
            candidate["direction"],
        ):
            continue

        selected.append(
            candidate
        )

    for signal in selected:

        inserted = insert_trade(
            signal
        )

        if not inserted:
            continue

        STATS["unique_entries"] += 1

        try:

            df15 = get_candles(
                signal["asset"],
                "15m",
                PATTERN_LOOKBACK_15M,
            )

            send_new_signal(
                signal,
                df15,
            )

        except Exception as e:

            print(
                "Signal notification error:",
                e,
            )

            # Database signal remains valid
            # even if Telegram fails.


# ============================================================
# STARTUP
# ============================================================

def startup_message():

    text = (
        f"<b>🚀 KRAKEN PATTERN SCANNER</b>\n\n"
        f"Version: {VERSION}\n"
        f"Mode: PAPER ONLY\n\n"
        f"<b>Strategy</b>\n"
        f"1H Pattern → 1H Breakout\n"
        f"→ 15M Same Pattern\n"
        f"→ 15M Breakout\n"
        f"→ 15M Retest\n"
        f"→ 15M Confirmation\n"
        f"→ Entry\n\n"
        f"🎯 TP: Natural 15M Pattern Target\n"
        f"🛑 SL: Natural 15M Pattern Invalidation\n\n"
        f"Fixed TP/SL: OFF\n"
        f"RR Filter: OFF"
    )

    telegram_send_text(text)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        f"KRAKEN FUTURES PATTERN LIVE SCANNER "
        f"VERSION {VERSION}"
    )
    print("=" * 70)

    print(
        "REAL_TRADING:",
        REAL_TRADING,
    )

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    init_db()

    try:
        load_contracts()
    except Exception as e:
        print(
            "Instrument loading warning:",
            e,
        )

    try:
        reconcile_open_trades()
    except Exception as e:
        print(
            "Startup reconciliation error:",
            e,
        )

    scan()

    periodic_report()

    print("=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)


if __name__ == "__main__":
    main()
