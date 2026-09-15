# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.6
# ============================================================
#
# FIX:
# - Correct Kraken Futures candle endpoint
# - Correct tick_type in candle URL
# - Correct resolutions: 5m / 15m / 1h
# - Correct candle timestamp handling (milliseconds)
#
# STRATEGY:
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE + PRICE REACTION
# 5M  = BREAKOUT / PULLBACK / CONFIRMATION
# VOL = RVOL
#
# CLOSED CANDLES ONLY
# PAPER TRADING ONLY
# ============================================================

import os
import time
import math
import sqlite3
from datetime import datetime, timezone

import requests
import numpy as np


# ============================================================
# CONFIG
# ============================================================

VERSION = "v7.6"

BASE_URL = "https://futures.kraken.com"

TICKERS_URL = f"{BASE_URL}/derivatives/api/v3/tickers"

# IMPORTANT:
# Kraken Futures Charts API requires:
# /api/charts/v1/{tick_type}/{symbol}/{resolution}
CHARTS_URL = f"{BASE_URL}/api/charts/v1"

# trade candles are used for strategy analysis
CANDLE_TICK_TYPE = "trade"

DB_FILE = "volume_khat_100_v76.db"

TOP_N = 100

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

PAPER_TRADING = True

REQUEST_TIMEOUT = 15

# ------------------------------------------------------------
# Timeframes
# ------------------------------------------------------------

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

TF_SECONDS = {
    TF_1H: 3600,
    TF_15M: 900,
    TF_5M: 300,
}

# ------------------------------------------------------------
# 1H TREND
# ------------------------------------------------------------

SMA_FAST = 20
SMA_SLOW = 50

MIN_TREND_SCORE = 3

# ------------------------------------------------------------
# SUPPORT / RESISTANCE
# ------------------------------------------------------------

SR_LOOKBACK_15M = 80
SR_LOOKBACK_1H = 80

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

SR_ZONE_TOLERANCE_PCT = 0.35

# ------------------------------------------------------------
# 5M STRUCTURE
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 6

PULLBACK_TOLERANCE_PCT = 0.50

REJECTION_DISTANCE_PCT = 0.80

MIN_BODY_PCT = 0.15

WICK_BODY_RATIO = 1.00

# ------------------------------------------------------------
# RVOL
# ------------------------------------------------------------

RVOL_LOOKBACK = 20

RVOL_NORMAL = 1.50
RVOL_STRONG = 2.20
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MIN_SL_PCT = 0.50
MAX_SL_PCT = 1.50

MIN_RR = 1.00

MAX_TP_PCT = 3.00

ATR_PERIOD = 14
ATR_MULTIPLIER = 1.20

ATR_BUFFER_PCT = 0.20

# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

MIN_SCORE = 9

# Max:
# Trend      = 5
# 15M setup  = 4
# 5M confirm = 4
# Volume     = 2
# TOTAL      = 15

# ------------------------------------------------------------
# COOLDOWN
# ------------------------------------------------------------

COOLDOWN_MINUTES = 15

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/7.6",
    "Accept": "application/json",
})


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "scanned": 0,

    "trend_long": 0,
    "trend_short": 0,
    "trend_neutral": 0,

    "data_fail": 0,
    "sr_fail": 0,

    "setup_pass": 0,
    "setup_fail": 0,

    "confirm_pass": 0,
    "confirm_fail": 0,

    "rvol_normal": 0,
    "rvol_strong": 0,
    "rvol_very_strong": 0,

    "score_checked": 0,
    "score_pass": 0,
    "score_fail": 0,

    "sl_pass": 0,
    "sl_fail": 0,

    "tp_pass": 0,
    "tp_fail": 0,

    "rr_pass": 0,
    "rr_fail": 0,

    "errors": 0,
}


# ============================================================
# UTILS
# ============================================================

def now_ts():
    return int(time.time())


def utc_now_str():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def pct_change(a, b):
    if a == 0:
        return 0.0
    return abs((b - a) / a) * 100.0


def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def fmt_price(x):
    if x is None:
        return "N/A"

    x = float(x)

    if x >= 1000:
        return f"{x:.2f}"
    if x >= 1:
        return f"{x:.5f}"
    if x >= 0.01:
        return f"{x:.6f}"

    return f"{x:.8f}"


def fmt_pct(x):
    if x is None:
        return "N/A"
    return f"{x:+.2f}%"


# ============================================================
# HTTP GET
# ============================================================

def http_get(url, params=None):
    try:
        r = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:
        DIAG["errors"] += 1

        print(
            f"[API ERROR] {url}"
            f" -> {e}"
        )

        return None


# ============================================================
# TOP 100
# ============================================================

def get_top_symbols():

    data = http_get(TICKERS_URL)

    if not data:
        return []

    tickers = data.get("tickers", [])

    if not isinstance(tickers, list):
        return []

    rows = []

    for item in tickers:

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("pair")
            or item.get("product_id")
        )

        if not symbol:
            continue

        symbol = str(symbol)

        # Futures perpetuals
        if not symbol.endswith("USD"):
            continue

        if "PI_" not in symbol and "PF_" not in symbol:
            continue

        volume = (
            item.get("vol24h")
            or item.get("volume24h")
            or item.get("volume")
        )

        price = (
            item.get("last")
            or item.get("markPrice")
            or item.get("price")
        )

        volume = safe_float(volume)
        price = safe_float(price)

        if volume is None or volume <= 0:
            continue

        rows.append({
            "symbol": symbol,
            "volume": volume,
            "price": price,
        })

    rows.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    result = rows[:TOP_N]

    print(f"[TOP] {len(result)} symbols")

    return result


# ============================================================
# CANDLE API
# ============================================================

def get_candles(symbol, resolution, count=150):

    if resolution not in {
        "1m",
        "5m",
        "15m",
        "30m",
        "1h",
        "4h",
        "12h",
        "1d",
        "1w",
    }:
        print(
            f"[CANDLE ERROR] Unsupported resolution: "
            f"{resolution}"
        )
        DIAG["errors"] += 1
        return []

    interval_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
        "12h": 43200,
        "1d": 86400,
        "1w": 604800,
    }[resolution]

    end_ts = int(time.time())

    # Small buffer so Kraken definitely returns enough candles.
    from_ts = end_ts - (
        interval_seconds * (count + 10)
    )

    url = (
        f"{CHARTS_URL}/"
        f"{CANDLE_TICK_TYPE}/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "from": from_ts,
        "to": end_ts,
    }

    data = http_get(url, params=params)

    if not data:
        return []

    raw = data.get("candles")

    if raw is None:
        # Defensive fallback for alternate response wrappers.
        if isinstance(data.get("result"), dict):
            raw = data["result"].get("candles")

    if not isinstance(raw, list):
        print(
            f"[CANDLE ERROR] {symbol} {resolution}"
            f" -> no candles in response"
        )
        return []

    candles = []

    for c in raw:

        if not isinstance(c, dict):
            continue

        t = (
            c.get("time")
            or c.get("timestamp")
            or c.get("t")
        )

        o = (
            c.get("open")
            or c.get("o")
        )

        h = (
            c.get("high")
            or c.get("h")
        )

        l = (
            c.get("low")
            or c.get("l")
        )

        close = (
            c.get("close")
            or c.get("c")
        )

        volume = (
            c.get("volume")
            or c.get("v")
        )

        try:
            t = int(t)

            # Kraken returns candle time in milliseconds.
            # Normalize to seconds internally.
            if t > 10_000_000_000:
                t = t // 1000

            o = float(o)
            h = float(h)
            l = float(l)
            close = float(close)
            volume = float(volume)

        except Exception:
            continue

        candles.append({
            "time": t,
            "open": o,
            "high": h,
            "low": l,
            "close": close,
            "volume": volume,
        })

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    current_ts = int(time.time())

    closed = []

    for c in candles:

        candle_end = (
            c["time"] + interval_seconds
        )

        if candle_end <= current_ts:
            closed.append(c)

    # Keep enough history
    if len(closed) > count:
        closed = closed[-count:]

    return closed


# ============================================================
# SMA
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return float(
        np.mean(values[-period:])
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return float(
        np.mean(trs[-period:])
    )


# ============================================================
# 1H TREND
# ============================================================

def trend_1h(candles):

    if len(candles) < 60:
        return "NEUTRAL", 0

    closes = np.array(
        [x["close"] for x in candles],
        dtype=float
    )

    highs = np.array(
        [x["high"] for x in candles],
        dtype=float
    )

    lows = np.array(
        [x["low"] for x in candles],
        dtype=float
    )

    price = closes[-1]

    sma20 = sma(closes, SMA_FAST)
    sma50 = sma(closes, SMA_SLOW)

    if sma20 is None or sma50 is None:
        return "NEUTRAL", 0

    score_long = 0
    score_short = 0

    # --------------------------------------------------------
    # Price vs SMA20
    # --------------------------------------------------------

    if price > sma20:
        score_long += 1
    elif price < sma20:
        score_short += 1

    # --------------------------------------------------------
    # SMA20 vs SMA50
    # --------------------------------------------------------

    if sma20 > sma50:
        score_long += 1
    elif sma20 < sma50:
        score_short += 1

    # --------------------------------------------------------
    # SMA20 slope
    # --------------------------------------------------------

    if len(closes) >= 25:

        old_sma20 = sma(
            closes[:-5],
            SMA_FAST
        )

        if old_sma20 is not None:

            if sma20 > old_sma20:
                score_long += 1
            elif sma20 < old_sma20:
                score_short += 1

    # --------------------------------------------------------
    # Recent structure
    # --------------------------------------------------------

    recent_highs = highs[-6:]
    recent_lows = lows[-6:]

    if recent_highs[-1] >= recent_highs[0]:
        score_long += 1

    if recent_lows[-1] >= recent_lows[0]:
        score_long += 1

    if recent_highs[-1] <= recent_highs[0]:
        score_short += 1

    if recent_lows[-1] <= recent_lows[0]:
        score_short += 1

    # --------------------------------------------------------
    # Normalize maximum to 5
    # --------------------------------------------------------

    score_long = min(score_long, 5)
    score_short = min(score_short, 5)

    if score_long >= MIN_TREND_SCORE and score_long > score_short:
        return "LONG", score_long

    if score_short >= MIN_TREND_SCORE and score_short > score_long:
        return "SHORT", score_short

    return "NEUTRAL", max(
        score_long,
        score_short
    )


# ============================================================
# PIVOTS
# ============================================================

def pivot_high(candles, i):

    if i < PIVOT_LEFT:
        return False

    if i + PIVOT_RIGHT >= len(candles):
        return False

    h = candles[i]["high"]

    for j in range(
        i - PIVOT_LEFT,
        i + PIVOT_RIGHT + 1
    ):
        if j == i:
            continue

        if candles[j]["high"] >= h:
            return False

    return True


def pivot_low(candles, i):

    if i < PIVOT_LEFT:
        return False

    if i + PIVOT_RIGHT >= len(candles):
        return False

    l = candles[i]["low"]

    for j in range(
        i - PIVOT_LEFT,
        i + PIVOT_RIGHT + 1
    ):
        if j == i:
            continue

        if candles[j]["low"] <= l:
            return False

    return True


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def build_sr(candles):

    if len(candles) < 20:
        return {
            "supports": [],
            "resistances": [],
        }

    supports = []
    resistances = []

    start = max(
        0,
        len(candles) - SR_LOOKBACK_15M
    )

    for i in range(
        start,
        len(candles) - PIVOT_RIGHT
    ):

        if pivot_low(candles, i):
            supports.append(
                candles[i]["low"]
            )

        if pivot_high(candles, i):
            resistances.append(
                candles[i]["high"]
            )

    return {
        "supports": sorted(set(supports)),
        "resistances": sorted(set(resistances)),
    }


def nearest_support(price, supports):

    candidates = [
        x for x in supports
        if x < price
    ]

    if not candidates:
        return None

    return max(candidates)


def nearest_resistance(price, resistances):

    candidates = [
        x for x in resistances
        if x > price
    ]

    if not candidates:
        return None

    return min(candidates)


def near_level(price, level):

    if level is None or price == 0:
        return False

    distance = abs(
        (price - level) / price
    ) * 100

    return (
        distance <= SR_ZONE_TOLERANCE_PCT
    )


# ============================================================
# 15M SETUP
# ============================================================

def setup_15m(candles, direction, sr):

    if len(candles) < 10:
        return False, 0, "insufficient"

    last = candles[-1]
    prev = candles[-2]

    price = last["close"]

    supports = sr["supports"]
    resistances = sr["resistances"]

    score = 0
    reason = []

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        support = nearest_support(
            price,
            supports
        )

        resistance = nearest_resistance(
            price,
            resistances
        )

        # Support reaction
        if support is not None:

            if (
                near_level(last["low"], support)
                and last["close"] > last["open"]
            ):
                score += 2
                reason.append("support_reaction")

        # Resistance breakout
        if resistance is not None:

            if (
                prev["close"] <= resistance
                and last["close"] > resistance
            ):
                score += 2
                reason.append("resistance_breakout")

        # Retest after breakout
        if resistance is not None:

            if (
                abs(last["low"] - resistance)
                / price
                * 100
                <= REJECTION_DISTANCE_PCT
                and last["close"] > resistance
            ):
                score += 2
                reason.append("resistance_retest")

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        resistance = nearest_resistance(
            price,
            resistances
        )

        support = nearest_support(
            price,
            supports
        )

        # Resistance rejection
        if resistance is not None:

            if (
                near_level(last["high"], resistance)
                and last["close"] < last["open"]
            ):
                score += 2
                reason.append("resistance_rejection")

        # Support breakdown
        if support is not None:

            if (
                prev["close"] >= support
                and last["close"] < support
            ):
                score += 2
                reason.append("support_breakdown")

        # Retest after breakdown
        if support is not None:

            if (
                abs(last["high"] - support)
                / price
                * 100
                <= REJECTION_DISTANCE_PCT
                and last["close"] < support
            ):
                score += 2
                reason.append("support_retest")

    score = min(score, 4)

    if score >= 3:
        return True, score, ",".join(reason)

    return False, score, ",".join(reason)


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirmation_5m(candles, direction):

    if len(candles) < BREAKOUT_LOOKBACK + 3:
        return False, 0, "insufficient"

    last = candles[-1]
    prev = candles[-2]

    recent = candles[
        -(BREAKOUT_LOOKBACK + 1):-1
    ]

    recent_high = max(
        x["high"] for x in recent
    )

    recent_low = min(
        x["low"] for x in recent
    )

    body = abs(
        last["close"] - last["open"]
    )

    candle_range = (
        last["high"] - last["low"]
    )

    if candle_range <= 0:
        return False, 0, "zero_range"

    body_pct = (
        body / last["close"] * 100
    )

    if body_pct < MIN_BODY_PCT:
        return False, 0, "small_body"

    upper_wick = (
        last["high"]
        - max(last["open"], last["close"])
    )

    lower_wick = (
        min(last["open"], last["close"])
        - last["low"]
    )

    score = 0
    reasons = []

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # Fresh breakout
        if last["close"] > recent_high:
            score += 2
            reasons.append("breakout")

        # Bullish candle
        if last["close"] > last["open"]:
            score += 1
            reasons.append("bullish")

        # Higher close
        if last["close"] > prev["close"]:
            score += 1
            reasons.append("momentum")

        # Bullish rejection
        if (
            lower_wick >= body * WICK_BODY_RATIO
            and last["close"] > last["open"]
        ):
            score += 1
            reasons.append("bullish_rejection")

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        # Fresh breakdown
        if last["close"] < recent_low:
            score += 2
            reasons.append("breakdown")

        # Bearish candle
        if last["close"] < last["open"]:
            score += 1
            reasons.append("bearish")

        # Lower close
        if last["close"] < prev["close"]:
            score += 1
            reasons.append("momentum")

        # Bearish rejection
        if (
            upper_wick >= body * WICK_BODY_RATIO
            and last["close"] < last["open"]
        ):
            score += 1
            reasons.append("bearish_rejection")

    score = min(score, 4)

    if score >= 3:
        return True, score, ",".join(reasons)

    return False, score, ",".join(reasons)


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(candles):

    if len(candles) < RVOL_LOOKBACK + 1:
        return 0.0

    current_volume = candles[-1]["volume"]

    historical = [
        x["volume"]
        for x in candles[
            -(RVOL_LOOKBACK + 1):-1
        ]
    ]

    avg_volume = np.mean(historical)

    if avg_volume <= 0:
        return 0.0

    return float(
        current_volume / avg_volume
    )


def volume_score(rvol):

    if rvol >= RVOL_VERY_STRONG:
        DIAG["rvol_very_strong"] += 1
        return 2

    if rvol >= RVOL_STRONG:
        DIAG["rvol_strong"] += 1
        return 2

    if rvol >= RVOL_NORMAL:
        DIAG["rvol_normal"] += 1
        return 1

    return 0


# ============================================================
# TP / SL
# ============================================================

def calculate_trade_levels(
    entry,
    direction,
    candles_5m,
    sr
):

    atr = calculate_atr(
        candles_5m,
        ATR_PERIOD
    )

    if atr is None or entry <= 0:
        return None

    atr_pct = (
        atr / entry
    ) * 100

    raw_sl_pct = (
        atr_pct * ATR_MULTIPLIER
        + ATR_BUFFER_PCT
    )

    sl_pct = max(
        MIN_SL_PCT,
        min(
            raw_sl_pct,
            MAX_SL_PCT
        )
    )

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    if direction == "LONG":

        sl = entry * (
            1 - sl_pct / 100
        )

        tp = nearest_resistance(
            entry,
            sr["resistances"]
        )

    else:

        sl = entry * (
            1 + sl_pct / 100
        )

        tp = nearest_support(
            entry,
            sr["supports"]
        )

    DIAG["sl_pass"] += 1

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    if tp is None:

        DIAG["tp_fail"] += 1

        return None

    tp_pct = abs(
        (tp - entry) / entry
    ) * 100

    if tp_pct <= 0:
        DIAG["tp_fail"] += 1
        return None

    if tp_pct > MAX_TP_PCT:
        DIAG["tp_fail"] += 1
        return None

    DIAG["tp_pass"] += 1

    risk_pct = abs(
        (entry - sl) / entry
    ) * 100

    reward_pct = tp_pct

    if risk_pct <= 0:
        DIAG["rr_fail"] += 1
        return None

    rr = reward_pct / risk_pct

    if rr < MIN_RR:
        DIAG["rr_fail"] += 1
        return None

    DIAG["rr_pass"] += 1

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "sl_pct": risk_pct,
        "tp_pct": reward_pct,
        "rr": rr,
    }


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db_connect()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,

            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,

            sl_pct REAL,
            tp_pct REAL,
            rr REAL,

            entry_time INTEGER NOT NULL,

            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,

            pnl_pct REAL,

            status TEXT NOT NULL DEFAULT 'OPEN',

            created_at INTEGER NOT NULL,

            closed_reported INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
    """).fetchall()

    conn.close()

    return rows


def count_open_trades():

    return len(
        get_open_trades()
    )


# ============================================================
# COOLDOWN
# ============================================================

def recently_closed(symbol):

    conn = db_connect()

    row = conn.execute("""
        SELECT exit_time
        FROM trades
        WHERE symbol = ?
          AND status = 'CLOSED'
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
    """, (symbol,)).fetchone()

    conn.close()

    if not row:
        return False

    elapsed = (
        time.time()
        - row["exit_time"]
    )

    return (
        elapsed
        < COOLDOWN_MINUTES * 60
    )


# ============================================================
# INSERT TRADE
# ============================================================

def insert_trade(signal):

    conn = db_connect()

    conn.execute("""
        INSERT INTO trades (
            symbol,
            direction,
            entry,
            sl,
            tp,
            sl_pct,
            tp_pct,
            rr,
            entry_time,
            created_at,
            status,
            closed_reported
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN', 0)
    """, (
        signal["symbol"],
        signal["direction"],
        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["sl_pct"],
        signal["tp_pct"],
        signal["rr"],
        signal["entry_time"],
        int(time.time()),
    ))

    conn.commit()
    conn.close()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def process_open_trades():

    open_trades = get_open_trades()

    if not open_trades:
        return

    conn = db_connect()

    for trade in open_trades:

        symbol = trade["symbol"]

        candles = get_candles(
            symbol,
            TF_5M,
            10
        )

        if len(candles) < 2:
            continue

        latest = candles[-1]

        candle_time = latest["time"]

        # Do not evaluate a candle before entry.
        if candle_time <= trade["entry_time"]:
            continue

        high = latest["high"]
        low = latest["low"]

        exit_price = None
        exit_reason = None

        if trade["direction"] == "LONG":

            hit_sl = low <= trade["sl"]
            hit_tp = high >= trade["tp"]

            # Conservative:
            # if both happen in the same candle,
            # SL is considered first.
            if hit_sl:
                exit_price = trade["sl"]
                exit_reason = "SL"

            elif hit_tp:
                exit_price = trade["tp"]
                exit_reason = "TP"

        else:

            hit_sl = high >= trade["sl"]
            hit_tp = low <= trade["tp"]

            if hit_sl:
                exit_price = trade["sl"]
                exit_reason = "SL"

            elif hit_tp:
                exit_price = trade["tp"]
                exit_reason = "TP"

        if exit_price is None:
            continue

        if trade["direction"] == "LONG":

            pnl_pct = (
                (exit_price - trade["entry"])
                / trade["entry"]
            ) * 100

        else:

            pnl_pct = (
                (trade["entry"] - exit_price)
                / trade["entry"]
            ) * 100

        conn.execute("""
            UPDATE trades
            SET
                status = 'CLOSED',
                exit_time = ?,
                exit_price = ?,
                exit_reason = ?,
                pnl_pct = ?
            WHERE id = ?
        """, (
            candle_time,
            exit_price,
            exit_reason,
            pnl_pct,
            trade["id"],
        ))

        print(
            f"[CLOSE] {symbol} "
            f"{trade['direction']} "
            f"{exit_reason} "
            f"PnL={pnl_pct:+.2f}%"
        )

    conn.commit()
    conn.close()


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connect()

    closed = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'CLOSED'
    """).fetchall()

    conn.close()

    total = len(closed)

    wins = sum(
        1 for x in closed
        if (x["pnl_pct"] or 0) > 0
    )

    losses = sum(
        1 for x in closed
        if (x["pnl_pct"] or 0) <= 0
    )

    net = sum(
        x["pnl_pct"] or 0
        for x in closed
    )

    wr = (
        wins / total * 100
        if total > 0
        else 0
    )

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "net_pnl": net,
    }


# ============================================================
# BUILD SIGNAL
# ============================================================

def analyze_symbol(item):

    symbol = item["symbol"]

    DIAG["scanned"] += 1

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    candles_1h = get_candles(
        symbol,
        TF_1H,
        120
    )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    candles_15m = get_candles(
        symbol,
        TF_15M,
        150
    )

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    candles_5m = get_candles(
        symbol,
        TF_5M,
        150
    )

    if (
        len(candles_1h) < 60
        or len(candles_15m) < 30
        or len(candles_5m) < 30
    ):
        DIAG["data_fail"] += 1
        return None

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    direction, trend_score = trend_1h(
        candles_1h
    )

    if direction == "LONG":
        DIAG["trend_long"] += 1

    elif direction == "SHORT":
        DIAG["trend_short"] += 1

    else:
        DIAG["trend_neutral"] += 1
        return None

    # --------------------------------------------------------
    # S/R
    # --------------------------------------------------------

    sr_15m = build_sr(
        candles_15m
    )

    sr_1h = build_sr(
        candles_1h
    )

    if (
        not sr_15m["supports"]
        and not sr_15m["resistances"]
    ):
        DIAG["sr_fail"] += 1
        return None

    # Merge S/R
    supports = (
        sr_15m["supports"]
        + sr_1h["supports"]
    )

    resistances = (
        sr_15m["resistances"]
        + sr_1h["resistances"]
    )

    sr = {
        "supports": sorted(set(supports)),
        "resistances": sorted(set(resistances)),
    }

    # --------------------------------------------------------
    # 15M SETUP
    # --------------------------------------------------------

    setup_ok, setup_score, setup_reason = setup_15m(
        candles_15m,
        direction,
        sr
    )

    if setup_ok:
        DIAG["setup_pass"] += 1
    else:
        DIAG["setup_fail"] += 1
        return None

    # --------------------------------------------------------
    # 5M CONFIRMATION
    # --------------------------------------------------------

    confirm_ok, confirm_score, confirm_reason = confirmation_5m(
        candles_5m,
        direction
    )

    if confirm_ok:
        DIAG["confirm_pass"] += 1
    else:
        DIAG["confirm_fail"] += 1
        return None

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    rvol = calculate_rvol(
        candles_5m
    )

    vol_score = volume_score(
        rvol
    )

    # --------------------------------------------------------
    # TOTAL SCORE
    # --------------------------------------------------------

    DIAG["score_checked"] += 1

    total_score = (
        trend_score
        + setup_score
        + confirm_score
        + vol_score
    )

    if total_score < MIN_SCORE:
        DIAG["score_fail"] += 1
        return None

    DIAG["score_pass"] += 1

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = candles_5m[-1]["close"]

    # --------------------------------------------------------
    # SL / TP
    # --------------------------------------------------------

    levels = calculate_trade_levels(
        entry,
        direction,
        candles_5m,
        sr
    )

    if levels is None:
        return None

    # --------------------------------------------------------
    # COOLDOWN
    # --------------------------------------------------------

    if recently_closed(symbol):
        return None

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    return {
        "symbol": symbol,
        "direction": direction,

        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],

        "sl_pct": levels["sl_pct"],
        "tp_pct": levels["tp_pct"],
        "rr": levels["rr"],

        "score": total_score,
        "trend_score": trend_score,
        "setup_score": setup_score,
        "confirm_score": confirm_score,
        "vol_score": vol_score,

        "rvol": rvol,

        "setup_reason": setup_reason,
        "confirm_reason": confirm_reason,

        "entry_time": candles_5m[-1]["time"],
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:
        print("[TELEGRAM] Missing credentials")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text,
    }

    try:

        r = SESSION.post(
            url,
            json=payload,
            timeout=15
        )

        r.raise_for_status()

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )


# ============================================================
# TELEGRAM REPORT
# ============================================================

def build_report(
    new_signals
):

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {utc_now_str()}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED | TOP 100"
    )

    lines.append(
        f"🛠 Strategy: {VERSION}"
    )

    lines.append(
        "🔒 Real trading: DISABLED"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append("")
    lines.append("🎯 NEW SIGNALS")

    if not new_signals:

        lines.append("None")

    else:

        for s in new_signals:

            icon = (
                "🟢"
                if s["direction"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{icon} {s['symbol']} "
                f"{s['direction']}"
            )

            lines.append(
                f"Entry: {fmt_price(s['entry'])}"
            )

            lines.append(
                f"SL: {fmt_price(s['sl'])} "
                f"({s['sl_pct']:.2f}%)"
            )

            lines.append(
                f"TP: {fmt_price(s['tp'])} "
                f"({s['tp_pct']:.2f}%)"
            )

            lines.append(
                f"RR: 1:{s['rr']:.2f}"
            )

            lines.append(
                f"Score: {s['score']}/15"
            )

            lines.append(
                f"RVOL: {s['rvol']:.2f}"
            )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    open_trades = get_open_trades()

    lines.append("")
    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append("None")

    else:

        for trade in open_trades:

            # live price
            data = get_top_symbols()

            live_price = None

            for x in data:
                if x["symbol"] == trade["symbol"]:
                    live_price = x["price"]
                    break

            if live_price is None:
                live_price = trade["entry"]

            if trade["direction"] == "LONG":

                pnl = (
                    (live_price - trade["entry"])
                    / trade["entry"]
                ) * 100

                icon = "🟢"

            else:

                pnl = (
                    (trade["entry"] - live_price)
                    / trade["entry"]
                ) * 100

                icon = "🔴"

            lines.append(
                f"{icon} {trade['symbol']} "
                f"{trade['direction']}"
            )

            lines.append(
                f"Entry: {fmt_price(trade['entry'])}"
            )

            lines.append(
                f"Now: {fmt_price(live_price)} "
                f"({pnl:+.2f}%)"
            )

            lines.append(
                f"SL: {fmt_price(trade['sl'])} "
                f"({trade['sl_pct']:.2f}%)"
            )

            lines.append(
                f"TP: {fmt_price(trade['tp'])} "
                f"({trade['tp_pct']:.2f}%)"
            )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    stats = get_stats()

    lines.append("")
    lines.append("📈 STATS")

    lines.append(
        f"Open: {len(open_trades)}"
    )

    lines.append(
        f"Closed: {stats['closed']}"
    )

    lines.append(
        f"Wins: {stats['wins']}"
    )

    lines.append(
        f"Losses: {stats['losses']}"
    )

    lines.append(
        f"Win Rate: {stats['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: {stats['net_pnl']:+.2f}%"
    )

    return "\n".join(lines)


# ============================================================
# DIAGNOSTICS
# ============================================================

def print_diagnostics():

    print("")
    print("============================================================")
    print("DIAGNOSTICS")
    print("============================================================")

    print(
        f"Scanned: {DIAG['scanned']}"
    )

    print(
        f"1H LONG: {DIAG['trend_long']}"
    )

    print(
        f"1H SHORT: {DIAG['trend_short']}"
    )

    print(
        f"1H NEUTRAL: {DIAG['trend_neutral']}"
    )

    print(
        f"Data fail: {DIAG['data_fail']}"
    )

    print(
        f"S/R fail: {DIAG['sr_fail']}"
    )

    print(
        f"15M setup PASS: {DIAG['setup_pass']}"
    )

    print(
        f"15M setup FAIL: {DIAG['setup_fail']}"
    )

    print(
        f"5M confirmation PASS: "
        f"{DIAG['confirm_pass']}"
    )

    print(
        f"5M confirmation FAIL: "
        f"{DIAG['confirm_fail']}"
    )

    print(
        f"RVOL >= {RVOL_NORMAL}: "
        f"{DIAG['rvol_normal']}"
    )

    print(
        f"RVOL >= {RVOL_STRONG}: "
        f"{DIAG['rvol_strong']}"
    )

    print(
        f"RVOL >= {RVOL_VERY_STRONG}: "
        f"{DIAG['rvol_very_strong']}"
    )

    print(
        f"Score checked: "
        f"{DIAG['score_checked']}"
    )

    print(
        f"Score >= {MIN_SCORE}: "
        f"{DIAG['score_pass']}"
    )

    print(
        f"Score < {MIN_SCORE}: "
        f"{DIAG['score_fail']}"
    )

    print(
        f"SL pass: {DIAG['sl_pass']}"
    )

    print(
        f"SL fail: {DIAG['sl_fail']}"
    )

    print(
        f"TP pass: {DIAG['tp_pass']}"
    )

    print(
        f"TP fail: {DIAG['tp_fail']}"
    )

    print(
        f"RR pass: {DIAG['rr_pass']}"
    )

    print(
        f"RR fail: {DIAG['rr_fail']}"
    )

    print(
        f"Errors: {DIAG['errors']}"
    )

    print("============================================================")


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)
    print(
        f"KRAKEN FUTURES VOLUME-KHAT 100 {VERSION}"
    )
    print(
        utc_now_str()
    )
    print("=" * 60)

    print(
        "SYSTEM: GitHub Actions"
    )

    print(
        "EXCHANGE: Kraken Futures"
    )

    print(
        "MARKETS: TOP 100 USD PERPETUAL"
    )

    print(
        "TIMEFRAME: 5M CLOSED CANDLES"
    )

    print(
        "REAL TRADING: DISABLED"
    )

    print(
        f"DATABASE: {DB_FILE}"
    )

    print(
        "DATABASE RESET: DISABLED"
    )

    print("=" * 60)

    init_db()

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    open_before = count_open_trades()

    print(
        f"[OPEN] "
        f"{open_before}/{MAX_OPEN_TRADES}"
    )

    # --------------------------------------------------------
    # FIRST PROCESS EXISTING TRADES
    # --------------------------------------------------------

    process_open_trades()

    # --------------------------------------------------------
    # TOP 100
    # --------------------------------------------------------

    symbols = get_top_symbols()

    if not symbols:

        print(
            "[FATAL] No symbols received."
        )

        print_diagnostics()

        return

    # --------------------------------------------------------
    # AVAILABLE SLOTS
    # --------------------------------------------------------

    open_count = count_open_trades()

    slots = max(
        0,
        MAX_OPEN_TRADES - open_count
    )

    print(
        f"[SLOTS] {slots}"
    )

    candidates = []

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    for item in symbols:

        try:

            signal = analyze_symbol(
                item
            )

            if signal:
                candidates.append(signal)

        except Exception as e:

            DIAG["errors"] += 1

            print(
                f"[SCAN ERROR] "
                f"{item.get('symbol')} "
                f"-> {e}"
            )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["rr"],
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # CREATE NEW TRADES
    # --------------------------------------------------------

    new_signals = []

    if slots > 0:

        for signal in candidates:

            if len(new_signals) >= min(
                slots,
                MAX_NEW_SIGNALS
            ):
                break

            # Double-check symbol isn't already open.
            existing = db_connect()

            row = existing.execute("""
                SELECT id
                FROM trades
                WHERE symbol = ?
                  AND status = 'OPEN'
                LIMIT 1
            """, (
                signal["symbol"],
            )).fetchone()

            existing.close()

            if row:
                continue

            insert_trade(
                signal
            )

            new_signals.append(
                signal
            )

            print(
                f"[SIGNAL] "
                f"{signal['symbol']} "
                f"{signal['direction']} "
                f"score={signal['score']} "
                f"RVOL={signal['rvol']:.2f} "
                f"RR={signal['rr']:.2f}"
            )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        new_signals
    )

    print("")
    print(report)
    print("")

    telegram_send(
        report
    )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    print_diagnostics()


if __name__ == "__main__":
    main()
