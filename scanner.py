# ============================================================
# VOLUME-KHAT 100 v1.0
# ============================================================
#
# Strategy:
#   TOP 100 USD perpetual Futures
#
#   1H  = Trend + Static S/R
#   15M = Dynamic S/R + Abnormal Volume + Setup
#   5M  = Entry Confirmation
#
# Dynamic S/R:
#   - Swing highs/lows
#   - At least 2 pivots
#   - Linear dynamic support/resistance
#
# Abnormal Volume:
#   RVOL = current closed candle volume / SMA(volume, 20)
#   RVOL >= 2.0
#
# Entry:
#   BREAKOUT + RETEST
#   or
#   HIGH VOLUME REJECTION
#
# Static S/R:
#   1H + 15M pivot clustering
#   Used ONLY for SL / TP
#
# Risk:
#   Minimum RR = 1:2
#
# Tracking:
#   - Open signals
#   - Live PnL
#   - TP / SL detection
#   - WIN / LOSS
#   - Win rate
#   - Long / Short statistics
#   - Breakout / Rejection statistics
#   - Daily statistics
#
# IMPORTANT:
#   This version DOES NOT place real orders.
#   It is a signal + paper-trade tracking engine.
#
# ============================================================

import os
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_BASE = "https://futures.kraken.com"

TOP_N = 100

SCAN_INTERVAL_SECONDS = 300

HTTP_TIMEOUT = 15

MAX_WORKERS = 8

# Candle sizes
COUNT_1H = 180
COUNT_15M = 220
COUNT_5M = 160

# Volume
RVOL_PERIOD = 20
RVOL_MIN = 2.0
RVOL_STRONG = 3.0

# Pivot
PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# Dynamic S/R
MIN_DYNAMIC_TOUCHES = 2

# Price proximity
MAX_DYNAMIC_DISTANCE_PCT = 1.0

# Breakout
BREAKOUT_MIN_PCT = 0.15
MIN_BODY_RATIO = 0.50

# Retest
RETEST_TOLERANCE_PCT = 0.35

# Static cluster
STATIC_CLUSTER_PCT = 0.30

# SL buffer
ATR_SL_BUFFER = 0.20

# RR
MIN_RR = 2.0
STRONG_RR = 3.0

# Score
MIN_SCORE = 9

# Cooldown for same symbol
SYMBOL_COOLDOWN_MINUTES = 30

# Telegram
TELEGRAM_POLL_SECONDS = 2

# Database
DB_FILE = "volume_khat_100.db"

# Logging
VERBOSE = True


# ============================================================
# GLOBAL SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Volume-Khat-100/1.0",
    "Accept": "application/json"
})


# ============================================================
# HELPERS
# ============================================================

def log(msg):
    if VERBOSE:
        print(f"[{utc_now()}] {msg}", flush=True)


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def utc_dt():
    return datetime.now(timezone.utc)


def safe_float(value, default=0.0):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def pct_distance(a, b):
    if b == 0:
        return 999.0
    return abs(a - b) / abs(b) * 100.0


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not BOT_TOKEN or not CHAT_ID:
        print("\n" + text + "\n")
        return False

    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    try:
        r = SESSION.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": text
            },
            timeout=HTTP_TIMEOUT
        )

        if r.status_code != 200:
            log(f"Telegram error: {r.text}")

        return r.status_code == 200

    except Exception as e:
        log(f"Telegram exception: {e}")
        return False


def telegram_send_long(text):
    max_len = 3900

    if len(text) <= max_len:
        telegram_send(text)
        return

    parts = []

    while len(text) > max_len:
        cut = text.rfind("\n", 0, max_len)

        if cut <= 0:
            cut = max_len

        parts.append(text[:cut])
        text = text[cut:]

    parts.append(text)

    for p in parts:
        telegram_send(p)


# ============================================================
# KRAKEN API
# ============================================================

def api_get(url, params=None, retries=3):

    last_error = None

    for attempt in range(retries):

        try:

            r = SESSION.get(
                url,
                params=params,
                timeout=HTTP_TIMEOUT
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:

            last_error = e

            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))

    raise last_error


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    url = f"{KRAKEN_BASE}/derivatives/api/v3/instruments"

    data = api_get(url)

    instruments = data.get("instruments", [])

    result = []

    for item in instruments:

        symbol = item.get("symbol", "")

        if not symbol:
            continue

        typ = str(item.get("type", "")).lower()

        tags = item.get("tags", [])
        if not isinstance(tags, list):
            tags = []

        tags_text = " ".join(
            str(x).lower()
            for x in tags
        )

        # Try to identify perpetual USD futures.
        is_perpetual = (
            "perpetual" in typ
            or "perpetual" in tags_text
            or symbol.startswith("PI_")
            or symbol.startswith("PF_")
        )

        if not is_perpetual:
            continue

        # Exclude indices / non-tradable references
        if "index" in typ:
            continue

        result.append(item)

    return result


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    url = f"{KRAKEN_BASE}/derivatives/api/v3/tickers"

    data = api_get(url)

    tickers = data.get("tickers", [])

    result = {}

    for t in tickers:

        symbol = t.get("symbol")

        if not symbol:
            continue

        last = safe_float(
            t.get("last")
            or t.get("lastPrice")
            or t.get("markPrice")
        )

        volume = safe_float(
            t.get("volumeQuote")
            or t.get("volume24hQuote")
            or t.get("volume24h")
        )

        result[symbol] = {
            "symbol": symbol,
            "last": last,
            "volume": volume,
            "raw": t
        }

    return result


# ============================================================
# TOP 100
# ============================================================

def get_top_100():

    instruments = get_instruments()

    tickers = get_tickers()

    markets = []

    for inst in instruments:

        symbol = inst.get("symbol")

        if symbol not in tickers:
            continue

        ticker = tickers[symbol]

        last = ticker["last"]

        volume = ticker["volume"]

        if last <= 0:
            continue

        markets.append({
            "symbol": symbol,
            "last": last,
            "volume": volume,
            "instrument": inst
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, count):

    url = (
        f"{KRAKEN_BASE}/api/charts/v1/"
        f"trade/{symbol}/{resolution}"
    )

    data = api_get(
        url,
        params={
            "count": count
        }
    )

    candles = data.get("candles", [])

    if not candles:
        return pd.DataFrame()

    rows = []

    for c in candles:

        rows.append({
            "time": int(c.get("time", 0)),
            "open": safe_float(c.get("open")),
            "high": safe_float(c.get("high")),
            "low": safe_float(c.get("low")),
            "close": safe_float(c.get("close")),
            "volume": safe_float(c.get("volume"))
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.sort_values("time").reset_index(drop=True)

    # Remove duplicated timestamps
    df = df.drop_duplicates(
        subset=["time"],
        keep="last"
    ).reset_index(drop=True)

    # Drop incomplete final candle.
    # We deliberately use closed candles only.
    now_ms = int(time.time() * 1000)

    resolution_ms = {
        "5m": 5 * 60 * 1000,
        "15m": 15 * 60 * 1000,
        "30m": 30 * 60 * 1000,
        "1h": 60 * 60 * 1000
    }.get(resolution, 0)

    if resolution_ms > 0 and len(df) > 0:

        last_time = int(df.iloc[-1]["time"])

        if now_ms < last_time + resolution_ms:
            df = df.iloc[:-1].copy()

    return df.reset_index(drop=True)


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=14):

    if len(df) < period + 2:
        return 0.0

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs()
        ],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    value = atr.iloc[-1]

    return safe_float(value)


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    df,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT
):

    highs = []
    lows = []

    if len(df) < left + right + 5:
        return highs, lows

    for i in range(left, len(df) - right):

        high = df.iloc[i]["high"]
        low = df.iloc[i]["low"]

        left_highs = df.iloc[
            i-left:i
        ]["high"]

        right_highs = df.iloc[
            i+1:i+1+right
        ]["high"]

        left_lows = df.iloc[
            i-left:i
        ]["low"]

        right_lows = df.iloc[
            i+1:i+1+right
        ]["low"]

        if high >= left_highs.max() and high >= right_highs.max():
            highs.append({
                "index": i,
                "price": float(high),
                "time": int(df.iloc[i]["time"])
            })

        if low <= left_lows.min() and low <= right_lows.min():
            lows.append({
                "index": i,
                "price": float(low),
                "time": int(df.iloc[i]["time"])
            })

    return highs, lows


# ============================================================
# DYNAMIC LINE
# ============================================================

def line_from_last_two(points):

    if len(points) < 2:
        return None

    p1 = points[-2]
    p2 = points[-1]

    x1 = p1["index"]
    x2 = p2["index"]

    y1 = p1["price"]
    y2 = p2["price"]

    if x2 == x1:
        return None

    slope = (y2 - y1) / (x2 - x1)

    return {
        "p1": p1,
        "p2": p2,
        "slope": slope
    }


def project_line(line, index):

    if not line:
        return None

    p1 = line["p1"]

    return (
        p1["price"]
        + line["slope"] * (index - p1["index"])
    )


def count_line_touches(
    df,
    line,
    side,
    tolerance_pct=0.35
):

    if not line:
        return 0

    count = 0

    for i in range(len(df)):

        level = project_line(line, i)

        if level is None or level <= 0:
            continue

        if side == "support":

            price = df.iloc[i]["low"]

        else:

            price = df.iloc[i]["high"]

        distance = abs(price - level) / level * 100

        if distance <= tolerance_pct:
            count += 1

    return count


def get_dynamic_sr(df):

    highs, lows = find_pivots(df)

    # Use recent pivots only
    highs = highs[-8:]
    lows = lows[-8:]

    resistance_line = line_from_last_two(highs)
    support_line = line_from_last_two(lows)

    resistance_touches = count_line_touches(
        df,
        resistance_line,
        "resistance"
    )

    support_touches = count_line_touches(
        df,
        support_line,
        "support"
    )

    idx = len(df) - 1

    resistance = project_line(
        resistance_line,
        idx
    )

    support = project_line(
        support_line,
        idx
    )

    if resistance is not None and resistance <= 0:
        resistance = None

    if support is not None and support <= 0:
        support = None

    return {
        "support": support,
        "resistance": resistance,
        "support_touches": support_touches,
        "resistance_touches": resistance_touches,
        "support_line": support_line,
        "resistance_line": resistance_line
    }


# ============================================================
# STATIC LEVEL CLUSTERING
# ============================================================

def cluster_levels(
    levels,
    cluster_pct=STATIC_CLUSTER_PCT
):

    if not levels:
        return []

    levels = sorted(
        float(x)
        for x in levels
        if safe_float(x) > 0
    )

    clusters = []

    current = [levels[0]]

    for level in levels[1:]:

        center = np.mean(current)

        distance = abs(level - center) / center * 100

        if distance <= cluster_pct:
            current.append(level)

        else:
            clusters.append(current)
            current = [level]

    clusters.append(current)

    result = []

    for cluster in clusters:

        center = float(np.mean(cluster))

        result.append({
            "price": center,
            "touches": len(cluster),
            "min": min(cluster),
            "max": max(cluster)
        })

    return result


def get_static_levels(df_1h, df_15m, current_price):

    all_levels = []

    h1_highs, h1_lows = find_pivots(df_1h)
    m15_highs, m15_lows = find_pivots(df_15m)

    for p in h1_highs:
        all_levels.append({
            "price": p["price"],
            "tf": "1H",
            "type": "resistance"
        })

    for p in h1_lows:
        all_levels.append({
            "price": p["price"],
            "tf": "1H",
            "type": "support"
        })

    for p in m15_highs:
        all_levels.append({
            "price": p["price"],
            "tf": "15M",
            "type": "resistance"
        })

    for p in m15_lows:
        all_levels.append({
            "price": p["price"],
            "tf": "15M",
            "type": "support"
        })

    raw_prices = [
        x["price"]
        for x in all_levels
    ]

    clusters = cluster_levels(raw_prices)

    supports = []
    resistances = []

    for c in clusters:

        price = c["price"]

        # classify relative to current price
        if price < current_price:
            supports.append(c)

        elif price > current_price:
            resistances.append(c)

    supports.sort(
        key=lambda x: x["price"],
        reverse=True
    )

    resistances.sort(
        key=lambda x: x["price"]
    )

    return supports, resistances


# ============================================================
# TREND
# ============================================================

def get_trend(df):

    if len(df) < 60:
        return "NEUTRAL"

    close = df["close"]

    ema20 = close.ewm(
        span=20,
        adjust=False
    ).mean()

    ema50 = close.ewm(
        span=50,
        adjust=False
    ).mean()

    last = close.iloc[-1]

    e20 = ema20.iloc[-1]
    e50 = ema50.iloc[-1]

    if last > e20 > e50:
        return "BULLISH"

    if last < e20 < e50:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# VOLUME
# ============================================================

def calculate_rvol(df):

    if len(df) < RVOL_PERIOD + 2:
        return 0.0

    # Current CLOSED candle compared with previous volume average.
    previous_avg = (
        df["volume"]
        .shift(1)
        .rolling(RVOL_PERIOD)
        .mean()
        .iloc[-1]
    )

    current_volume = df["volume"].iloc[-1]

    if previous_avg <= 0:
        return 0.0

    return current_volume / previous_avg


# ============================================================
# CANDLE STRUCTURE
# ============================================================

def candle_structure(candle):

    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])

    candle_range = h - l

    if candle_range <= 0:
        return {
            "body_ratio": 0,
            "upper_wick": 0,
            "lower_wick": 0,
            "bullish": False,
            "bearish": False
        }

    body = abs(c - o)

    body_ratio = body / candle_range

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    return {
        "body_ratio": body_ratio,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "bullish": c > o,
        "bearish": c < o
    }


# ============================================================
# BREAKOUT / REJECTION
# ============================================================

def detect_setup(
    df,
    dynamic,
    rvol
):

    if len(df) < 5:
        return None

    candle = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(candle["close"])
    high = float(candle["high"])
    low = float(candle["low"])

    prev_close = float(prev["close"])

    structure = candle_structure(candle)

    resistance = dynamic["resistance"]
    support = dynamic["support"]

    # --------------------------------------------------------
    # LONG BREAKOUT
    # --------------------------------------------------------

    if (
        resistance
        and resistance > 0
        and close > resistance
        and prev_close <= resistance
        and rvol >= RVOL_MIN
    ):

        breakout_distance = (
            (close - resistance)
            / resistance
            * 100
        )

        if (
            breakout_distance >= BREAKOUT_MIN_PCT
            and structure["body_ratio"] >= MIN_BODY_RATIO
            and structure["bullish"]
        ):

            return {
                "setup": "BREAKOUT_LONG",
                "direction": "LONG",
                "level": resistance,
                "rvol": rvol
            }

    # --------------------------------------------------------
    # SHORT BREAKOUT
    # --------------------------------------------------------

    if (
        support
        and support > 0
        and close < support
        and prev_close >= support
        and rvol >= RVOL_MIN
    ):

        breakout_distance = (
            (support - close)
            / support
            * 100
        )

        if (
            breakout_distance >= BREAKOUT_MIN_PCT
            and structure["body_ratio"] >= MIN_BODY_RATIO
            and structure["bearish"]
        ):

            return {
                "setup": "BREAKOUT_SHORT",
                "direction": "SHORT",
                "level": support,
                "rvol": rvol
            }

    # --------------------------------------------------------
    # HIGH VOLUME REJECTION SHORT
    # --------------------------------------------------------

    if (
        resistance
        and resistance > 0
        and rvol >= RVOL_MIN
        and high >= resistance
        and close < resistance
        and structure["bearish"]
    ):

        wick_ratio = (
            structure["upper_wick"]
            / max(
                high - low,
                1e-12
            )
        )

        if wick_ratio >= 0.25:

            return {
                "setup": "REJECTION_SHORT",
                "direction": "SHORT",
                "level": resistance,
                "rvol": rvol
            }

    # --------------------------------------------------------
    # HIGH VOLUME REJECTION LONG
    # --------------------------------------------------------

    if (
        support
        and support > 0
        and rvol >= RVOL_MIN
        and low <= support
        and close > support
        and structure["bullish"]
    ):

        wick_ratio = (
            structure["lower_wick"]
            / max(
                high - low,
                1e-12
            )
        )

        if wick_ratio >= 0.25:

            return {
                "setup": "REJECTION_LONG",
                "direction": "LONG",
                "level": support,
                "rvol": rvol
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df,
    direction,
    setup,
    level
):

    if len(df) < 10:
        return False

    last = df.iloc[-1]
    prev = df.iloc[-2]

    close = float(last["close"])
    prev_close = float(prev["close"])

    structure = candle_structure(last)

    if direction == "LONG":

        # Breakout confirmation
        if setup == "BREAKOUT_LONG":

            return (
                close > level
                and structure["bullish"]
                and structure["body_ratio"] >= 0.45
            )

        # Rejection confirmation
        if setup == "REJECTION_LONG":

            return (
                close > level
                and structure["bullish"]
                and close > prev_close
            )

    if direction == "SHORT":

        if setup == "BREAKOUT_SHORT":

            return (
                close < level
                and structure["bearish"]
                and structure["body_ratio"] >= 0.45
            )

        if setup == "REJECTION_SHORT":

            return (
                close < level
                and structure["bearish"]
                and close < prev_close
            )

    return False


# ============================================================
# RETEST
# ============================================================

def detect_retest(
    df,
    level,
    direction
):

    if len(df) < 5:
        return False

    recent = df.iloc[-5:]

    tolerance = level * (
        RETEST_TOLERANCE_PCT / 100
    )

    if direction == "LONG":

        touched = (
            recent["low"] <= level + tolerance
        ).any()

        closes_above = (
            recent["close"] > level
        ).any()

        return bool(
            touched and closes_above
        )

    if direction == "SHORT":

        touched = (
            recent["high"] >= level - tolerance
        ).any()

        closes_below = (
            recent["close"] < level
        ).any()

        return bool(
            touched and closes_below
        )

    return False


# ============================================================
# ENTRY
# ============================================================

def calculate_entry(
    df_5m,
    setup,
    level
):

    if df_5m.empty:
        return None

    close = float(
        df_5m.iloc[-1]["close"]
    )

    # Conservative entry uses latest confirmed 5M close.
    return close


# ============================================================
# SL / TP
# ============================================================

def calculate_trade_levels(
    direction,
    entry,
    supports,
    resistances,
    atr
):

    if entry <= 0:
        return None

    buffer = atr * ATR_SL_BUFFER

    if buffer <= 0:
        buffer = entry * 0.002

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        candidate_supports = [
            s["price"]
            for s in supports
            if s["price"] < entry
        ]

        if not candidate_supports:
            return None

        sl_support = max(
            candidate_supports
        )

        sl = sl_support - buffer

        if sl >= entry:
            return None

        risk = entry - sl

        candidate_resistances = [
            r["price"]
            for r in resistances
            if r["price"] > entry
        ]

        candidate_resistances.sort()

        tp = None

        for resistance in candidate_resistances:

            reward = resistance - entry

            if reward / risk >= MIN_RR:

                tp = resistance
                break

        if tp is None:
            return None

        reward = tp - entry

        rr = reward / risk

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk_pct": risk / entry * 100,
            "reward_pct": reward / entry * 100,
            "rr": rr
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        candidate_resistances = [
            r["price"]
            for r in resistances
            if r["price"] > entry
        ]

        if not candidate_resistances:
            return None

        sl_resistance = min(
            candidate_resistances
        )

        sl = sl_resistance + buffer

        if sl <= entry:
            return None

        risk = sl - entry

        candidate_supports = [
            s["price"]
            for s in supports
            if s["price"] < entry
        ]

        candidate_supports.sort(
            reverse=True
        )

        tp = None

        for support in candidate_supports:

            reward = entry - support

            if reward / risk >= MIN_RR:

                tp = support
                break

        if tp is None:
            return None

        reward = entry - tp

        rr = reward / risk

        return {
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk_pct": risk / entry * 100,
            "reward_pct": reward / entry * 100,
            "rr": rr
        }

    return None


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    rvol,
    dynamic,
    setup,
    rr,
    proximity_pct,
    confirmed_5m,
    retest
):

    score = 0

    # RVOL
    if rvol >= RVOL_MIN:
        score += 2

    if rvol >= RVOL_STRONG:
        score += 1

    # Dynamic S/R
    if (
        dynamic["support_touches"] >= 2
        or dynamic["resistance_touches"] >= 2
    ):
        score += 2

    # Proximity
    if proximity_pct <= 0.5:
        score += 2

    elif proximity_pct <= 1.0:
        score += 1

    # Setup
    if setup:

        if "BREAKOUT" in setup:
            score += 2

        elif "REJECTION" in setup:
            score += 2

    # Retest
    if retest:
        score += 2

    # 5M
    if confirmed_5m:
        score += 2

    # Trend
    if (
        (trend == "BULLISH" and "LONG" in setup)
        or
        (trend == "BEARISH" and "SHORT" in setup)
    ):
        score += 2

    # RR
    if rr >= STRONG_RR:
        score += 2

    return score


# ============================================================
# DATABASE
# ============================================================

class Database:

    def __init__(self, filename=DB_FILE):

        self.conn = sqlite3.connect(
            filename,
            check_same_thread=False
        )

        self.conn.row_factory = sqlite3.Row

        self.create_tables()

    def create_tables(self):

        cur = self.conn.cursor()

        cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            direction TEXT NOT NULL,

            setup TEXT NOT NULL,

            score REAL NOT NULL,

            rvol REAL NOT NULL,

            trend TEXT NOT NULL,

            entry REAL NOT NULL,

            sl REAL NOT NULL,

            tp REAL NOT NULL,

            rr REAL NOT NULL,

            risk_pct REAL NOT NULL,

            reward_pct REAL NOT NULL,

            current_price REAL,

            pnl_pct REAL DEFAULT 0,

            max_profit_pct REAL DEFAULT 0,

            max_drawdown_pct REAL DEFAULT 0,

            status TEXT NOT NULL,

            result TEXT,

            opened_at TEXT NOT NULL,

            closed_at TEXT,

            exit_price REAL,

            close_reason TEXT

        )
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_status
        ON trades(status)
        """)

        cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_trade_symbol
        ON trades(symbol)
        """)

        self.conn.commit()

    # --------------------------------------------------------

    def has_open_trade(self, symbol):

        cur = self.conn.cursor()

        cur.execute("""
        SELECT id
        FROM trades
        WHERE symbol = ?
        AND status = 'OPEN'
        LIMIT 1
        """, (symbol,))

        return cur.fetchone() is not None

    # --------------------------------------------------------

    def last_trade_time(self, symbol):

        cur = self.conn.cursor()

        cur.execute("""
        SELECT opened_at
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """, (symbol,))

        row = cur.fetchone()

        if not row:
            return None

        try:
            return datetime.fromisoformat(
                row["opened_at"]
            )
        except Exception:
            return None

    # --------------------------------------------------------

    def insert_trade(self, trade):

        cur = self.conn.cursor()

        cur.execute("""
        INSERT INTO trades (
            symbol,
            direction,
            setup,
            score,
            rvol,
            trend,
            entry,
            sl,
            tp,
            rr,
            risk_pct,
            reward_pct,
            current_price,
            pnl_pct,
            max_profit_pct,
            max_drawdown_pct,
            status,
            result,
            opened_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            trade["symbol"],
            trade["direction"],
            trade["setup"],
            trade["score"],
            trade["rvol"],
            trade["trend"],
            trade["entry"],
            trade["sl"],
            trade["tp"],
            trade["rr"],
            trade["risk_pct"],
            trade["reward_pct"],
            trade["entry"],
            0,
            0,
            0,
            "OPEN",
            None,
            trade["opened_at"]
        ))

        self.conn.commit()

        return cur.lastrowid

    # --------------------------------------------------------

    def get_open_trades(self):

        cur = self.conn.cursor()

        cur.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id DESC
        """)

        return cur.fetchall()

    # --------------------------------------------------------

    def update_open_trade(
        self,
        trade_id,
        current_price,
        pnl_pct,
        max_profit_pct,
        max_drawdown_pct
    ):

        cur = self.conn.cursor()

        cur.execute("""
        UPDATE trades

        SET
            current_price = ?,
            pnl_pct = ?,
            max_profit_pct = ?,
            max_drawdown_pct = ?

        WHERE id = ?
        """, (
            current_price,
            pnl_pct,
            max_profit_pct,
            max_drawdown_pct,
            trade_id
        ))

        self.conn.commit()

    # --------------------------------------------------------

    def close_trade(
        self,
        trade_id,
        exit_price,
        pnl_pct,
        result,
        reason
    ):

        cur = self.conn.cursor()

        cur.execute("""
        UPDATE trades

        SET
            current_price = ?,
            pnl_pct = ?,
            status = 'CLOSED',
            result = ?,
            exit_price = ?,
            close_reason = ?,
            closed_at = ?

        WHERE id = ?
        """, (
            exit_price,
            pnl_pct,
            result,
            exit_price,
            reason,
            utc_dt().isoformat(),
            trade_id
        ))

        self.conn.commit()

    # --------------------------------------------------------

    def stats(self):

        cur = self.conn.cursor()

        cur.execute("""
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                WHEN result = 'WIN' THEN 1
                ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                WHEN result = 'LOSS' THEN 1
                ELSE 0
                END
            ) AS losses,

            COALESCE(
                SUM(pnl_pct),
                0
            ) AS net_pnl,

            COALESCE(
                AVG(
                    CASE
                    WHEN result = 'WIN'
                    THEN pnl_pct
                    END
                ),
                0
            ) AS avg_win,

            COALESCE(
                AVG(
                    CASE
                    WHEN result = 'LOSS'
                    THEN pnl_pct
                    END
                ),
                0
            ) AS avg_loss

        FROM trades
        WHERE status = 'CLOSED'
        """)

        row = cur.fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0

        win_rate = (
            wins / total * 100
            if total > 0
            else 0
        )

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "net_pnl": row["net_pnl"] or 0,
            "avg_win": row["avg_win"] or 0,
            "avg_loss": row["avg_loss"] or 0
        }

    # --------------------------------------------------------

    def directional_stats(self, direction):

        cur = self.conn.cursor()

        cur.execute("""
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

        FROM trades

        WHERE
            status = 'CLOSED'
            AND direction = ?
        """, (direction,))

        row = cur.fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (
                wins / total * 100
                if total else 0
            ),
            "pnl": row["pnl"] or 0
        }

    # --------------------------------------------------------

    def setup_stats(self, setup):

        cur = self.conn.cursor()

        cur.execute("""
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

        FROM trades

        WHERE
            status = 'CLOSED'
            AND setup = ?
        """, (setup,))

        row = cur.fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (
                wins / total * 100
                if total else 0
            ),
            "pnl": row["pnl"] or 0
        }

    # --------------------------------------------------------

    def today_stats(self):

        cur = self.conn.cursor()

        today = (
            datetime.now(timezone.utc)
            .date()
            .isoformat()
        )

        cur.execute("""
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

        FROM trades

        WHERE
            status = 'CLOSED'
            AND substr(closed_at, 1, 10) = ?
        """, (today,))

        row = cur.fetchone()

        total = row["total"] or 0
        wins = row["wins"] or 0
        losses = row["losses"] or 0

        return {
            "total": total,
            "wins": wins,
            "losses": losses,
            "win_rate": (
                wins / total * 100
                if total else 0
            ),
            "pnl": row["pnl"] or 0
        }


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    direction,
    entry,
    current
):

    if entry <= 0:
        return 0

    if direction == "LONG":

        return (
            (current - entry)
            / entry
            * 100
        )

    if direction == "SHORT":

        return (
            (entry - current)
            / entry
            * 100
        )

    return 0


# ============================================================
# TRACK OPEN TRADES
# ============================================================

def update_open_trades(db, tickers):

    opened = db.get_open_trades()

    if not opened:
        return []

    closed_messages = []

    for trade in opened:

        symbol = trade["symbol"]

        ticker = tickers.get(symbol)

        if not ticker:
            continue

        current = ticker["last"]

        if current <= 0:
            continue

        direction = trade["direction"]

        entry = trade["entry"]
        sl = trade["sl"]
        tp = trade["tp"]

        pnl = calculate_pnl(
            direction,
            entry,
            current
        )

        max_profit = max(
            trade["max_profit_pct"] or 0,
            pnl
        )

        max_drawdown = min(
            trade["max_drawdown_pct"] or 0,
            pnl
        )

        # ----------------------------------------------------
        # Update
        # ----------------------------------------------------

        db.update_open_trade(
            trade["id"],
            current,
            pnl,
            max_profit,
            max_drawdown
        )

        # ----------------------------------------------------
        # TP / SL
        # ----------------------------------------------------

        result = None
        reason = None

        if direction == "LONG":

            if current >= tp:
                result = "WIN"
                reason = "TP HIT"

            elif current <= sl:
                result = "LOSS"
                reason = "SL HIT"

        elif direction == "SHORT":

            if current <= tp:
                result = "WIN"
                reason = "TP HIT"

            elif current >= sl:
                result = "LOSS"
                reason = "SL HIT"

        if result:

            db.close_trade(
                trade["id"],
                current,
                pnl,
                result,
                reason
            )

            emoji = "🟢" if result == "WIN" else "🔴"

            msg = (
                f"{emoji} TRADE CLOSED\n\n"
                f"{symbol}\n"
                f"{direction}\n"
                f"Setup: {trade['setup']}\n\n"
                f"Entry: {entry:.8g}\n"
                f"Exit: {current:.8g}\n"
                f"SL: {sl:.8g}\n"
                f"TP: {tp:.8g}\n\n"
                f"PnL: {pnl:+.2f}%\n"
                f"Result: {result}\n"
                f"Reason: {reason}"
            )

            closed_messages.append(msg)

    return closed_messages


# ============================================================
# FORMAT OPEN TRADES
# ============================================================

def format_open_trades(db):

    trades = db.get_open_trades()

    if not trades:
        return (
            "📊 OPEN TRADES\n\n"
            "هیچ سیگنال بازی وجود ندارد."
        )

    lines = [
        "📊 OPEN TRADES",
        ""
    ]

    for t in trades:

        direction_emoji = (
            "🟢"
            if t["direction"] == "LONG"
            else "🔴"
        )

        pnl = t["pnl_pct"] or 0

        pnl_emoji = (
            "🟢"
            if pnl >= 0
            else "🔴"
        )

        lines.extend([
            f"{direction_emoji} {t['symbol']} "
            f"{t['direction']}",
            f"Setup: {t['setup']}",
            f"Entry: {t['entry']:.8g}",
            f"Current: {t['current_price']:.8g}",
            f"SL: {t['sl']:.8g}",
            f"TP: {t['tp']:.8g}",
            f"RR: 1:{t['rr']:.2f}",
            f"{pnl_emoji} PnL: {pnl:+.2f}%",
            f"Score: {t['score']:.0f}",
            ""
        ])

    return "\n".join(lines)


# ============================================================
# FORMAT STATS
# ============================================================

def format_stats(db):

    s = db.stats()

    long_s = db.directional_stats(
        "LONG"
    )

    short_s = db.directional_stats(
        "SHORT"
    )

    bo_long = db.setup_stats(
        "BREAKOUT_LONG"
    )

    bo_short = db.setup_stats(
        "BREAKOUT_SHORT"
    )

    rej_long = db.setup_stats(
        "REJECTION_LONG"
    )

    rej_short = db.setup_stats(
        "REJECTION_SHORT"
    )

    return (
        "📈 VOLUME-KHAT 100\n"
        "PERFORMANCE\n\n"

        f"Total: {s['total']}\n"
        f"🟢 Wins: {s['wins']}\n"
        f"🔴 Losses: {s['losses']}\n"
        f"Win Rate: {s['win_rate']:.2f}%\n\n"

        f"Net PnL: {s['net_pnl']:+.2f}%\n"
        f"Avg Win: {s['avg_win']:+.2f}%\n"
        f"Avg Loss: {s['avg_loss']:+.2f}%\n\n"

        "━━━━━━━━━━━━━━\n"
        "LONG\n"
        f"Trades: {long_s['total']}\n"
        f"W/L: {long_s['wins']}/{long_s['losses']}\n"
        f"Win Rate: {long_s['win_rate']:.2f}%\n"
        f"PnL: {long_s['pnl']:+.2f}%\n\n"

        "SHORT\n"
        f"Trades: {short_s['total']}\n"
        f"W/L: {short_s['wins']}/{short_s['losses']}\n"
        f"Win Rate: {short_s['win_rate']:.2f}%\n"
        f"PnL: {short_s['pnl']:+.2f}%\n\n"

        "━━━━━━━━━━━━━━\n"
        "BREAKOUT LONG\n"
        f"{bo_long['total']} trades | "
        f"{bo_long['win_rate']:.1f}% WR\n\n"

        "BREAKOUT SHORT\n"
        f"{bo_short['total']} trades | "
        f"{bo_short['win_rate']:.1f}% WR\n\n"

        "REJECTION LONG\n"
        f"{rej_long['total']} trades | "
        f"{rej_long['win_rate']:.1f}% WR\n\n"

        "REJECTION SHORT\n"
        f"{rej_short['total']} trades | "
        f"{rej_short['win_rate']:.1f}% WR"
    )


# ============================================================
# TODAY
# ============================================================

def format_today(db):

    s = db.today_stats()

    return (
        "📅 TODAY\n\n"
        f"Trades: {s['total']}\n"
        f"🟢 Wins: {s['wins']}\n"
        f"🔴 Losses: {s['losses']}\n"
        f"Win Rate: {s['win_rate']:.2f}%\n"
        f"Net PnL: {s['pnl']:+.2f}%"
    )


# ============================================================
# SIGNAL FORMAT
# ============================================================

def format_signal(t):

    direction_emoji = (
        "🟢"
        if t["direction"] == "LONG"
        else "🔴"
    )

    rr_icon = (
        "🔥"
        if t["rr"] >= 3
        else "✅"
    )

    return (
        f"{direction_emoji} "
        f"{t['symbol']} "
        f"{t['direction']}\n\n"

        f"SETUP: {t['setup']}\n"
        f"Score: {t['score']:.0f}\n"
        f"1H Trend: {t['trend']}\n\n"

        f"RVOL: {t['rvol']:.2f}x\n"
        f"Dynamic Level: {t['dynamic_level']:.8g}\n"
        f"5M Confirmation: "
        f"{'✅' if t['confirmed_5m'] else '❌'}\n"
        f"Retest: "
        f"{'✅' if t['retest'] else '❌'}\n\n"

        f"ENTRY: {t['entry']:.8g}\n"
        f"SL: {t['sl']:.8g}\n"
        f"TP: {t['tp']:.8g}\n\n"

        f"Risk: {t['risk_pct']:.2f}%\n"
        f"Reward: {t['reward_pct']:.2f}%\n"
        f"{rr_icon} RR: 1:{t['rr']:.2f}\n\n"

        f"STATUS: 🚀 OPEN"
    )


# ============================================================
# ANALYZE ONE SYMBOL
# ============================================================

def analyze_symbol(
    market,
    db
):

    symbol = market["symbol"]

    # Already open
    if db.has_open_trade(symbol):
        return None

    # Cooldown
    last_time = db.last_trade_time(symbol)

    if last_time:

        try:

            age = (
                utc_dt()
                - last_time
            ).total_seconds() / 60

            if age < SYMBOL_COOLDOWN_MINUTES:
                return None

        except Exception:
            pass

    try:

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        df_1h = get_candles(
            symbol,
            "1h",
            COUNT_1H
        )

        if df_1h.empty or len(df_1h) < 70:
            return None

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        df_15m = get_candles(
            symbol,
            "15m",
            COUNT_15M
        )

        if df_15m.empty or len(df_15m) < 80:
            return None

        # ----------------------------------------------------
        # 1H TREND
        # ----------------------------------------------------

        trend = get_trend(df_1h)

        if trend == "NEUTRAL":
            return None

        # ----------------------------------------------------
        # 15M DYNAMIC S/R
        # ----------------------------------------------------

        dynamic = get_dynamic_sr(df_15m)

        current = float(
            df_15m.iloc[-1]["close"]
        )

        # Need valid dynamic levels
        if (
            dynamic["support"] is None
            and dynamic["resistance"] is None
        ):
            return None

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            df_15m
        )

        if rvol < RVOL_MIN:
            return None

        # ----------------------------------------------------
        # SETUP
        # ----------------------------------------------------

        setup = detect_setup(
            df_15m,
            dynamic,
            rvol
        )

        if not setup:
            return None

        direction = setup["direction"]

        level = setup["level"]

        proximity = pct_distance(
            current,
            level
        )

        # For actual setup, price can be slightly beyond
        # the level after a breakout.
        if proximity > 2.0:
            return None

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df_5m = get_candles(
            symbol,
            "5m",
            COUNT_5M
        )

        if df_5m.empty or len(df_5m) < 30:
            return None

        confirmed_5m = confirm_5m(
            df_5m,
            direction,
            setup["setup"],
            level
        )

        if not confirmed_5m:
            return None

        # ----------------------------------------------------
        # RETEST
        # ----------------------------------------------------

        retest = detect_retest(
            df_5m,
            level,
            direction
        )

        # For rejection we don't require retest.
        if (
            "REJECTION" not in setup["setup"]
            and not retest
        ):
            return None

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = calculate_entry(
            df_5m,
            setup["setup"],
            level
        )

        if entry is None:
            return None

        # ----------------------------------------------------
        # STATIC S/R
        # ----------------------------------------------------

        supports, resistances = get_static_levels(
            df_1h,
            df_15m,
            entry
        )

        if not supports or not resistances:
            return None

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = calculate_atr(
            df_15m
        )

        if atr <= 0:
            return None

        # ----------------------------------------------------
        # SL / TP
        # ----------------------------------------------------

        levels = calculate_trade_levels(
            direction,
            entry,
            supports,
            resistances,
            atr
        )

        if not levels:
            return None

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            trend,
            rvol,
            dynamic,
            setup["setup"],
            levels["rr"],
            proximity,
            confirmed_5m,
            retest
        )

        if score < MIN_SCORE:
            return None

        # Trend filter
        # A rejection can technically be counter-trend,
        # but we require stronger score for counter-trend.
        trend_aligned = (
            (trend == "BULLISH" and direction == "LONG")
            or
            (trend == "BEARISH" and direction == "SHORT")
        )

        if not trend_aligned and score < 12:
            return None

        trade = {
            "symbol": symbol,
            "direction": direction,
            "setup": setup["setup"],
            "score": score,
            "rvol": rvol,
            "trend": trend,
            "dynamic_level": level,
            "confirmed_5m": confirmed_5m,
            "retest": retest,
            "entry": levels["entry"],
            "sl": levels["sl"],
            "tp": levels["tp"],
            "rr": levels["rr"],
            "risk_pct": levels["risk_pct"],
            "reward_pct": levels["reward_pct"],
            "opened_at": utc_dt().isoformat()
        }

        return trade

    except Exception as e:

        log(
            f"{symbol} analysis error: {e}"
        )

        return None


# ============================================================
# SCAN TOP 100
# ============================================================

def scan_markets(db):

    log("Getting TOP 100 markets...")

    markets = get_top_100()

    if not markets:

        log("No markets returned.")

        return []

    log(
        f"Scanning {len(markets)} markets..."
    )

    signals = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_symbol,
                market,
                db
            ): market["symbol"]
            for market in markets
        }

        for future in as_completed(futures):

            symbol = futures[future]

            try:

                result = future.result()

                if result:
                    signals.append(result)

            except Exception as e:

                log(
                    f"{symbol} worker error: {e}"
                )

    # Sort strongest first
    signals.sort(
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    return signals


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_and_send_signal(
    db,
    trade
):

    trade_id = db.insert_trade(
        trade
    )

    message = format_signal(
        trade
    )

    message += (
        f"\n\n🆔 Trade ID: {trade_id}"
    )

    telegram_send_long(
        message
    )

    return trade_id


# ============================================================
# SCAN REPORT
# ============================================================

def scan_report(
    scanned,
    signals
):

    if not signals:

        return (
            "📡 VOLUME-KHAT 100\n\n"
            f"Scanned: {scanned}\n"
            "Qualified: 0\n\n"
            "❌ No valid setup"
        )

    lines = [
        "📡 VOLUME-KHAT 100",
        "",
        f"Scanned: {scanned}",
        f"Qualified: {len(signals)}",
        ""
    ]

    for i, s in enumerate(
        signals[:10],
        start=1
    ):

        emoji = (
            "🟢"
            if s["direction"] == "LONG"
            else "🔴"
        )

        lines.append(
            f"{i}. {emoji} "
            f"{s['symbol']} "
            f"{s['direction']} "
            f"| Score {s['score']:.0f} "
            f"| RVOL {s['rvol']:.2f} "
            f"| RR 1:{s['rr']:.2f}"
        )

    return "\n".join(lines)


# ============================================================
# COMMAND HANDLER
# ============================================================

def get_updates(offset=None):

    if not BOT_TOKEN:
        return []

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 1
    }

    if offset is not None:
        params["offset"] = offset

    try:

        r = SESSION.get(
            url,
            params=params,
            timeout=10
        )

        if r.status_code != 200:
            return []

        data = r.json()

        return data.get(
            "result",
            []
        )

    except Exception:
        return []


def handle_command(
    db,
    text
):

    command = text.strip().lower()

    if command.startswith("/start"):

        return (
            "🤖 VOLUME-KHAT 100\n\n"
            "/scan - اسکن 100 ارز\n"
            "/active - سیگنال‌های باز\n"
            "/stats - آمار کلی\n"
            "/today - آمار امروز\n"
            "/help - راهنما"
        )

    if command.startswith("/help"):

        return (
            "📚 COMMANDS\n\n"
            "/scan\n"
            "اسکن دستی 100 ارز\n\n"
            "/active\n"
            "نمایش معاملات باز و PnL\n\n"
            "/stats\n"
            "آمار موفقیت و شکست\n\n"
            "/today\n"
            "آمار امروز"
        )

    if command.startswith("/active"):

        return format_open_trades(
            db
        )

    if command.startswith("/stats"):

        return format_stats(
            db
        )

    if command.startswith("/today"):

        return format_today(
            db
        )

    return None


def telegram_command_loop(db):

    if not BOT_TOKEN:
        return

    offset = None

    log("Telegram command listener started.")

    while True:

        updates = get_updates(
            offset
        )

        for update in updates:

            offset = (
                update["update_id"] + 1
            )

            message = update.get(
                "message",
                {}
            )

            text = message.get(
                "text",
                ""
            )

            if not text:
                continue

            # Optional security:
            # If CHAT_ID is configured,
            # only accept commands from it.
            incoming_chat = str(
                message.get(
                    "chat",
                    {}
                ).get(
                    "id",
                    ""
                )
            )

            if (
                CHAT_ID
                and incoming_chat
                and incoming_chat != str(CHAT_ID)
            ):
                continue

            if text.lower().startswith(
                "/scan"
            ):

                telegram_send(
                    "🔎 اسکن دستی شروع شد..."
                )

                try:

                    signals = scan_markets(
                        db
                    )

                    markets = get_top_100()

                    report = scan_report(
                        len(markets),
                        signals
                    )

                    telegram_send_long(
                        report
                    )

                    for signal in signals[:5]:

                        save_and_send_signal(
                            db,
                            signal
                        )

                except Exception as e:

                    telegram_send(
                        f"❌ Scan error:\n{e}"
                    )

                continue

            response = handle_command(
                db,
                text
            )

            if response:
                telegram_send_long(
                    response
                )


# ============================================================
# BACKGROUND TELEGRAM THREAD
# ============================================================

def start_telegram_listener(db):

    if not BOT_TOKEN:
        return None

    import threading

    thread = threading.Thread(
        target=telegram_command_loop,
        args=(db,),
        daemon=True
    )

    thread.start()

    return thread


# ============================================================
# MAIN SCANNER LOOP
# ============================================================

def main():

    print("=" * 60)
    print("VOLUME-KHAT 100 v1.0")
    print("=" * 60)

    print(
        "Mode: SIGNAL + PAPER TRACKING"
    )

    print(
        "Real trading: DISABLED"
    )

    print("=" * 60)

    db = Database()

    start_telegram_listener(
        db
    )

    # Initial notification
    telegram_send(
        "🤖 VOLUME-KHAT 100\n"
        "Scanner started.\n\n"
        "Real trading: DISABLED"
    )

    last_scan = 0

    while True:

        try:

            now = time.time()

            # ------------------------------------------------
            # LIVE OPEN TRADE UPDATE
            # ------------------------------------------------

            try:

                tickers = get_tickers()

                closed_messages = (
                    update_open_trades(
                        db,
                        tickers
                    )
                )

                for message in closed_messages:

                    telegram_send_long(
                        message
                    )

            except Exception as e:

                log(
                    f"Open trade update error: {e}"
                )

            # ------------------------------------------------
            # SCAN
            # ------------------------------------------------

            if (
                now - last_scan
                >= SCAN_INTERVAL_SECONDS
            ):

                last_scan = now

                log(
                    "Starting new scan..."
                )

                markets = []

                try:

                    markets = get_top_100()

                    signals = scan_markets(
                        db
                    )

                    # ------------------------------------------------
                    # Send report
                    # ------------------------------------------------

                    report = scan_report(
                        len(markets),
                        signals
                    )

                    telegram_send_long(
                        report
                    )

                    # ------------------------------------------------
                    # Save strongest signals
                    # ------------------------------------------------

                    saved = 0

                    for signal in signals[:5]:

                        if db.has_open_trade(
                            signal["symbol"]
                        ):
                            continue

                        save_and_send_signal(
                            db,
                            signal
                        )

                        saved += 1

                    log(
                        f"Scan complete | "
                        f"Markets={len(markets)} | "
                        f"Signals={len(signals)} | "
                        f"Saved={saved}"
                    )

                except Exception as e:

                    log(
                        f"Scan error: {e}"
                    )

                    traceback.print_exc()

                    telegram_send(
                        "❌ Scanner error:\n"
                        f"{str(e)[:1000]}"
                    )

            time.sleep(
                TELEGRAM_POLL_SECONDS
            )

        except KeyboardInterrupt:

            print(
                "\nScanner stopped."
            )

            break

        except Exception as e:

            log(
                f"MAIN LOOP ERROR: {e}"
            )

            traceback.print_exc()

            time.sleep(10)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
