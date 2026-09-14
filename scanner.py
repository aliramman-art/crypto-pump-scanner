# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.5
# ============================================================
#
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE + REACTION / BREAKOUT / RETEST
# 5M  = BREAKOUT / PULLBACK / REJECTION + CONFIRMATION
# VOL = RVOL SCORE
#
# v7.5
# ------------------------------------------------------------
# IMPORTANT DESIGN CHANGE:
#
# v7.4 required:
# 5M breakout -> exact pullback -> confirmation
#
# v7.5:
# Three valid 5M paths:
#   A) BREAKOUT + CONFIRMATION
#   B) PULLBACK + CONFIRMATION
#   C) REJECTION + CONFIRMATION
#
# RVOL is score-based, not mandatory.
#
# PAPER TRADING ONLY
# ============================================================

import os
import time
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import numpy as np


# ============================================================
# CONFIG
# ============================================================

VERSION = "v7.5"

DB_FILE = "volume_khat_100_v75.db"

TOP_N = 100
MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

PAPER_TRADING = True

KRAKEN_BASE = "https://futures.kraken.com"

REQUEST_TIMEOUT = 15

INTERVAL_1H = 60
INTERVAL_15M = 15
INTERVAL_5M = 5

# ------------------------------------------------------------
# Trend
# ------------------------------------------------------------

SMA_FAST = 20
SMA_SLOW = 50

MIN_TREND_SCORE = 3

# ------------------------------------------------------------
# S/R
# ------------------------------------------------------------

SR_LOOKBACK_15M = 80
SR_LOOKBACK_1H = 80

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

ZONE_TOLERANCE_PCT = 0.35

# ------------------------------------------------------------
# 5M
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 6

PULLBACK_TOLERANCE_PCT = 0.50

REJECTION_DISTANCE_PCT = 0.80

MIN_BODY_PCT = 0.15
WICK_BODY_RATIO = 1.00

# ------------------------------------------------------------
# Volume
# ------------------------------------------------------------

RVOL_LOOKBACK = 20

RVOL_NORMAL = 1.50
RVOL_STRONG = 2.20
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# Risk
# ------------------------------------------------------------

MIN_SL_PCT = 0.50
MAX_SL_PCT = 1.50

MIN_RR = 1.00
MAX_TP_PCT = 3.00

ATR_PERIOD = 14
ATR_MULTIPLIER = 1.20
ATR_BUFFER = 0.20

# ------------------------------------------------------------
# Score
# ------------------------------------------------------------

MIN_SCORE = 9

# Trend       = 0..5
# 15M setup   = 0..4
# 5M confirm  = 0..4
# Volume      = 0..2
#
# TOTAL       = 15

# ------------------------------------------------------------
# Cooldown
# ------------------------------------------------------------

COOLDOWN_MINUTES = 15


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "VOLUME-KHAT-100-v7.5"
})


# ============================================================
# HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_str():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def pct_change(a, b):
    if a == 0:
        return 0.0
    return ((b - a) / a) * 100.0


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


# ============================================================
# API
# ============================================================

def api_get(url, params=None):
    try:
        r = session.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        return data

    except Exception as e:
        print(f"[API ERROR] {url} -> {e}")
        return None


# ============================================================
# TICKERS
# ============================================================

def get_tickers():
    url = f"{KRAKEN_BASE}/derivatives/api/v3/tickers"

    data = api_get(url)

    if not data:
        return []

    result = data.get("tickers", [])

    if not isinstance(result, list):
        return []

    return result


def get_top_symbols():
    tickers = get_tickers()

    rows = []

    for t in tickers:

        symbol = (
            t.get("symbol")
            or t.get("pair")
            or t.get("product_id")
        )

        if not symbol:
            continue

        symbol = str(symbol)

        if not symbol.endswith("USD"):
            continue

        if "PI_" not in symbol and "PF_" not in symbol:
            continue

        volume = (
            safe_float(t.get("vol24h"))
            or safe_float(t.get("volume24h"))
            or safe_float(t.get("volume"))
            or 0
        )

        last = (
            safe_float(t.get("last"))
            or safe_float(t.get("markPrice"))
            or safe_float(t.get("price"))
        )

        if not last:
            continue

        rows.append({
            "symbol": symbol,
            "volume": volume,
            "price": last
        })

    rows.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return rows[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, interval, limit=200):

    url = (
        f"{KRAKEN_BASE}/api/charts/v1/"
        f"market/{symbol}/{interval}"
    )

    data = api_get(
        url,
        params={
            "from": int(time.time()) - (limit * interval * 60 * 2),
            "to": int(time.time())
        }
    )

    if not data:
        return []

    candles = data.get("candles", data)

    if not isinstance(candles, list):
        return []

    parsed = []

    for c in candles:

        try:
            ts = (
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

            cl = (
                c.get("close")
                or c.get("c")
            )

            v = (
                c.get("volume")
                or c.get("v")
                or 0
            )

            if None in (ts, o, h, l, cl):
                continue

            parsed.append({
                "time": int(float(ts)),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(cl),
                "volume": float(v)
            })

        except Exception:
            continue

    parsed.sort(key=lambda x: x["time"])

    # --------------------------------------------------------
    # Kraken normally returns the currently forming candle.
    # Remove it so all calculations use CLOSED candles only.
    # --------------------------------------------------------

    if len(parsed) > 3:

        current_ts = int(time.time())

        candle_seconds = interval * 60

        last = parsed[-1]

        if last["time"] + candle_seconds > current_ts:
            parsed = parsed[:-1]

    return parsed[-limit:]


# ============================================================
# INDICATORS
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return float(
        np.mean(values[-period:])
    )


def atr(candles, period=ATR_PERIOD):

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


def rvol(candles):

    if len(candles) < RVOL_LOOKBACK + 1:
        return 0.0

    current_volume = candles[-1]["volume"]

    previous = [
        x["volume"]
        for x in candles[-RVOL_LOOKBACK-1:-1]
        if x["volume"] > 0
    ]

    if not previous:
        return 0.0

    avg_volume = np.mean(previous)

    if avg_volume <= 0:
        return 0.0

    return current_volume / avg_volume


# ============================================================
# CANDLE STRUCTURE
# ============================================================

def candle_body_pct(c):

    if c["close"] == 0:
        return 0

    return abs(
        c["close"] - c["open"]
    ) / c["close"] * 100


def candle_direction(c):

    if c["close"] > c["open"]:
        return "LONG"

    if c["close"] < c["open"]:
        return "SHORT"

    return "NEUTRAL"


def candle_wicks(c):

    body = abs(
        c["close"] - c["open"]
    )

    upper = c["high"] - max(
        c["open"],
        c["close"]
    )

    lower = min(
        c["open"],
        c["close"]
    ) - c["low"]

    return body, upper, lower


# ============================================================
# PIVOTS
# ============================================================

def pivot_highs(candles):

    levels = []

    start = PIVOT_LEFT
    end = len(candles) - PIVOT_RIGHT

    for i in range(start, end):

        h = candles[i]["high"]

        left = [
            candles[j]["high"]
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + 1 + PIVOT_RIGHT
            )
        ]

        if all(h >= x for x in left + right):
            levels.append(h)

    return levels


def pivot_lows(candles):

    levels = []

    start = PIVOT_LEFT
    end = len(candles) - PIVOT_RIGHT

    for i in range(start, end):

        l = candles[i]["low"]

        left = [
            candles[j]["low"]
            for j in range(
                i - PIVOT_LEFT,
                i
            )
        ]

        right = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + 1 + PIVOT_RIGHT
            )
        ]

        if all(l <= x for x in left + right):
            levels.append(l)

    return levels


# ============================================================
# ZONE CLUSTERING
# ============================================================

def cluster_levels(levels):

    if not levels:
        return []

    levels = sorted(levels)

    zones = []

    current = [levels[0]]

    for level in levels[1:]:

        base = np.mean(current)

        distance = abs(
            level - base
        ) / base * 100

        if distance <= ZONE_TOLERANCE_PCT:
            current.append(level)

        else:
            zones.append(
                float(np.mean(current))
            )

            current = [level]

    if current:
        zones.append(
            float(np.mean(current))
        )

    return zones


def get_sr_zones(c15, c1h):

    lows_15 = pivot_lows(
        c15[-SR_LOOKBACK_15M:]
    )

    highs_15 = pivot_highs(
        c15[-SR_LOOKBACK_15M:]
    )

    lows_1h = pivot_lows(
        c1h[-SR_LOOKBACK_1H:]
    )

    highs_1h = pivot_highs(
        c1h[-SR_LOOKBACK_1H:]
    )

    supports = cluster_levels(
        lows_15 + lows_1h
    )

    resistances = cluster_levels(
        highs_15 + highs_1h
    )

    return supports, resistances


# ============================================================
# 1H TREND
# ============================================================

def trend_1h(candles):

    if len(candles) < SMA_SLOW + 5:
        return {
            "direction": "NEUTRAL",
            "score": 0
        }

    closes = [
        x["close"]
        for x in candles
    ]

    fast = sma(
        closes,
        SMA_FAST
    )

    slow = sma(
        closes,
        SMA_SLOW
    )

    price = closes[-1]

    score_long = 0
    score_short = 0

    # Price vs MA
    if price > fast:
        score_long += 1

    if price < fast:
        score_short += 1

    if price > slow:
        score_long += 1

    if price < slow:
        score_short += 1

    # MA relationship
    if fast > slow:
        score_long += 1

    if fast < slow:
        score_short += 1

    # MA slope
    previous_fast = sma(
        closes[:-3],
        SMA_FAST
    )

    previous_slow = sma(
        closes[:-3],
        SMA_SLOW
    )

    if previous_fast is not None:

        if fast > previous_fast:
            score_long += 1

        if fast < previous_fast:
            score_short += 1

    if previous_slow is not None:

        if slow > previous_slow:
            score_long += 1

        if slow < previous_slow:
            score_short += 1

    # Recent structure
    recent = candles[-6:]

    if recent[-1]["close"] > recent[0]["close"]:
        score_long += 1

    if recent[-1]["close"] < recent[0]["close"]:
        score_short += 1

    if score_long >= score_short:
        direction = "LONG"
        score = score_long

    else:
        direction = "SHORT"
        score = score_short

    # Avoid weak trend
    if score < MIN_TREND_SCORE:
        direction = "NEUTRAL"

    return {
        "direction": direction,
        "score": int(score)
    }


# ============================================================
# 15M SETUP
# ============================================================

def detect_15m_setup(
    candles,
    supports,
    resistances,
    direction
):

    if len(candles) < 10:
        return {
            "valid": False,
            "score": 0,
            "level": None,
            "type": None
        }

    current = candles[-1]
    previous = candles[-2]

    price = current["close"]

    best_score = 0
    best_type = None
    best_level = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # Support reaction
        for support in supports:

            distance = abs(
                current["low"] - support
            ) / support * 100

            if distance <= REJECTION_DISTANCE_PCT:

                bullish = (
                    current["close"] >
                    current["open"]
                )

                if bullish:

                    score = 3

                    if current["close"] > support:
                        score += 1

                    if score > best_score:
                        best_score = score
                        best_type = "SUPPORT_REACTION"
                        best_level = support

        # Resistance breakout
        for resistance in resistances:

            crossed = (
                previous["close"] <= resistance
                and current["close"] > resistance
            )

            if crossed:

                score = 3

                if current["close"] > current["open"]:
                    score += 1

                if score > best_score:
                    best_score = score
                    best_type = "RESISTANCE_BREAKOUT"
                    best_level = resistance

        # Resistance retest
        for resistance in resistances:

            distance = abs(
                current["low"] - resistance
            ) / resistance * 100

            if (
                distance <= PULLBACK_TOLERANCE_PCT
                and current["close"] > resistance
            ):

                score = 3

                if current["close"] > current["open"]:
                    score += 1

                if score > best_score:
                    best_score = score
                    best_type = "RESISTANCE_RETEST"
                    best_level = resistance

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        # Resistance reaction
        for resistance in resistances:

            distance = abs(
                current["high"] - resistance
            ) / resistance * 100

            if distance <= REJECTION_DISTANCE_PCT:

                bearish = (
                    current["close"] <
                    current["open"]
                )

                if bearish:

                    score = 3

                    if current["close"] < resistance:
                        score += 1

                    if score > best_score:
                        best_score = score
                        best_type = "RESISTANCE_REACTION"
                        best_level = resistance

        # Support breakdown
        for support in supports:

            crossed = (
                previous["close"] >= support
                and current["close"] < support
            )

            if crossed:

                score = 3

                if current["close"] < current["open"]:
                    score += 1

                if score > best_score:
                    best_score = score
                    best_type = "SUPPORT_BREAKOUT"
                    best_level = support

        # Support retest
        for support in supports:

            distance = abs(
                current["high"] - support
            ) / support * 100

            if (
                distance <= PULLBACK_TOLERANCE_PCT
                and current["close"] < support
            ):

                score = 3

                if current["close"] < current["open"]:
                    score += 1

                if score > best_score:
                    best_score = score
                    best_type = "SUPPORT_RETEST"
                    best_level = support

    return {
        "valid": best_score >= 3,
        "score": min(best_score, 4),
        "level": best_level,
        "type": best_type
    }


# ============================================================
# 5M BREAKOUT DETECTION
# ============================================================

def recent_breakout(
    candles,
    level,
    direction
):

    if level is None:
        return False

    if len(candles) < BREAKOUT_LOOKBACK + 2:
        return False

    recent = candles[-BREAKOUT_LOOKBACK:]

    if direction == "LONG":

        for c in recent[:-1]:

            if c["close"] > level:
                return True

    if direction == "SHORT":

        for c in recent[:-1]:

            if c["close"] < level:
                return True

    return False


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirmation_5m(
    candles,
    setup_level,
    direction
):

    if len(candles) < 5:
        return {
            "valid": False,
            "score": 0,
            "type": None
        }

    current = candles[-1]
    previous = candles[-2]

    body, upper, lower = candle_wicks(
        current
    )

    body_pct = candle_body_pct(
        current
    )

    if body_pct < MIN_BODY_PCT:
        return {
            "valid": False,
            "score": 0,
            "type": "SMALL_BODY"
        }

    score = 0
    pattern = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        bullish = (
            current["close"] >
            current["open"]
        )

        if not bullish:
            return {
                "valid": False,
                "score": 0,
                "type": "NOT_BULLISH"
            }

        # Breakout confirmation
        if (
            setup_level is not None
            and current["close"] > setup_level
            and current["close"] > previous["high"]
        ):

            score = 4
            pattern = "BREAKOUT_CONFIRMATION"

        # Pullback confirmation
        elif (
            setup_level is not None
            and current["low"] <=
                setup_level *
                (1 + PULLBACK_TOLERANCE_PCT / 100)
            and current["close"] > setup_level
        ):

            score = 3
            pattern = "PULLBACK_CONFIRMATION"

        # Bullish rejection
        elif (
            lower >= body * WICK_BODY_RATIO
            and current["close"] >
                previous["close"]
        ):

            score = 3
            pattern = "BULLISH_REJECTION"

        # Momentum continuation
        elif current["close"] > previous["high"]:

            score = 3
            pattern = "MOMENTUM_CONFIRMATION"

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        bearish = (
            current["close"] <
            current["open"]
        )

        if not bearish:
            return {
                "valid": False,
                "score": 0,
                "type": "NOT_BEARISH"
            }

        # Breakdown confirmation
        if (
            setup_level is not None
            and current["close"] < setup_level
            and current["close"] < previous["low"]
        ):

            score = 4
            pattern = "BREAKDOWN_CONFIRMATION"

        # Pullback confirmation
        elif (
            setup_level is not None
            and current["high"] >=
                setup_level *
                (1 - PULLBACK_TOLERANCE_PCT / 100)
            and current["close"] < setup_level
        ):

            score = 3
            pattern = "PULLBACK_CONFIRMATION"

        # Bearish rejection
        elif (
            upper >= body * WICK_BODY_RATIO
            and current["close"] <
                previous["close"]
        ):

            score = 3
            pattern = "BEARISH_REJECTION"

        # Momentum continuation
        elif current["close"] < previous["low"]:

            score = 3
            pattern = "MOMENTUM_CONFIRMATION"

    return {
        "valid": score >= 3,
        "score": score,
        "type": pattern
    }


# ============================================================
# VOLUME SCORE
# ============================================================

def volume_score(candles):

    value = rvol(candles)

    if value >= RVOL_VERY_STRONG:
        return 2, value

    if value >= RVOL_NORMAL:
        return 1, value

    return 0, value


# ============================================================
# STOP LOSS
# ============================================================

def calculate_sl(
    candles,
    entry,
    direction
):

    a = atr(candles)

    if a is None:
        return None

    raw_distance = (
        a * ATR_MULTIPLIER
    )

    distance = (
        raw_distance +
        a * ATR_BUFFER
    )

    sl_pct = (
        distance /
        entry *
        100
    )

    sl_pct = clamp(
        sl_pct,
        MIN_SL_PCT,
        MAX_SL_PCT
    )

    if direction == "LONG":
        sl = entry * (
            1 - sl_pct / 100
        )

    else:
        sl = entry * (
            1 + sl_pct / 100
        )

    return {
        "price": sl,
        "pct": sl_pct
    }


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_tp(
    entry,
    sl,
    direction,
    supports,
    resistances
):

    candidates = []

    if direction == "LONG":

        for level in resistances:

            if level > entry:

                distance_pct = (
                    level - entry
                ) / entry * 100

                if (
                    0 < distance_pct <=
                    MAX_TP_PCT
                ):
                    candidates.append(level)

    else:

        for level in supports:

            if level < entry:

                distance_pct = (
                    entry - level
                ) / entry * 100

                if (
                    0 < distance_pct <=
                    MAX_TP_PCT
                ):
                    candidates.append(level)

    if not candidates:
        return None

    if direction == "LONG":
        tp = min(candidates)

        risk = entry - sl
        reward = tp - entry

    else:
        tp = max(candidates)

        risk = sl - entry
        reward = entry - tp

    if risk <= 0:
        return None

    rr = reward / risk

    if rr < MIN_RR:
        return None

    tp_pct = abs(
        tp - entry
    ) / entry * 100

    return {
        "price": tp,
        "pct": tp_pct,
        "rr": rr
    }


# ============================================================
# DATABASE
# ============================================================

def init_db():

    conn = sqlite3.connect(DB_FILE)

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            entry_time INTEGER NOT NULL,
            exit_time INTEGER,
            exit_price REAL,
            exit_reason TEXT,
            pnl_pct REAL,
            score INTEGER,
            setup_type TEXT,
            confirmation_type TEXT,
            rvol REAL,
            closed_reported INTEGER DEFAULT 0
        )
    """)

    conn.commit()
    conn.close()


def db_connection():
    return sqlite3.connect(DB_FILE)


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connection()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time,
            score,
            setup_type,
            confirmation_type,
            rvol
        FROM trades
        WHERE exit_time IS NULL
        ORDER BY entry_time ASC
    """)

    rows = cur.fetchall()

    conn.close()

    columns = [
        "id",
        "symbol",
        "side",
        "entry",
        "sl",
        "tp",
        "entry_time",
        "score",
        "setup_type",
        "confirmation_type",
        "rvol"
    ]

    return [
        dict(zip(columns, row))
        for row in rows
    ]


def insert_trade(trade):

    conn = db_connection()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO trades (
            symbol,
            side,
            entry,
            sl,
            tp,
            entry_time,
            score,
            setup_type,
            confirmation_type,
            rvol
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        trade["symbol"],
        trade["side"],
        trade["entry"],
        trade["sl"],
        trade["tp"],
        trade["entry_time"],
        trade["score"],
        trade["setup_type"],
        trade["confirmation_type"],
        trade["rvol"]
    ))

    conn.commit()

    conn.close()


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    exit_price,
    exit_reason,
    exit_time,
    pnl_pct
):

    conn = db_connection()

    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET
            exit_time = ?,
            exit_price = ?,
            exit_reason = ?,
            pnl_pct = ?,
            closed_reported = 0
        WHERE id = ?
          AND exit_time IS NULL
    """, (
        exit_time,
        exit_price,
        exit_reason,
        pnl_pct,
        trade_id
    ))

    conn.commit()

    conn.close()


# ============================================================
# COOLDOWN
# ============================================================

def recently_traded(symbol):

    conn = db_connection()

    cur = conn.cursor()

    cur.execute("""
        SELECT exit_time
        FROM trades
        WHERE symbol = ?
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return False

    exit_time = row[0]

    elapsed = (
        int(time.time()) -
        int(exit_time)
    ) / 60

    return elapsed < COOLDOWN_MINUTES


# ============================================================
# PROCESS OPEN TRADES
# ============================================================

def process_open_trades():

    trades = get_open_trades()

    closed = []

    for trade in trades:

        candles = get_candles(
            trade["symbol"],
            INTERVAL_5M,
            200
        )

        if not candles:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # Never allow the entry candle to close the trade.
        # ----------------------------------------------------

        candles = [
            c for c in candles
            if c["time"] > trade["entry_time"]
        ]

        if not candles:
            continue

        exit_price = None
        reason = None

        for c in candles:

            hit_sl = False
            hit_tp = False

            if trade["side"] == "LONG":

                hit_sl = (
                    c["low"] <= trade["sl"]
                )

                hit_tp = (
                    c["high"] >= trade["tp"]
                )

            else:

                hit_sl = (
                    c["high"] >= trade["sl"]
                )

                hit_tp = (
                    c["low"] <= trade["tp"]
                )

            # ------------------------------------------------
            # Conservative rule:
            # If both happen in same candle,
            # SL is assumed first.
            # ------------------------------------------------

            if hit_sl:

                exit_price = trade["sl"]
                reason = "SL"
                exit_time = c["time"]
                break

            if hit_tp:

                exit_price = trade["tp"]
                reason = "TP"
                exit_time = c["time"]
                break

        if exit_price is not None:

            if trade["side"] == "LONG":

                pnl_pct = (
                    exit_price -
                    trade["entry"]
                ) / trade["entry"] * 100

            else:

                pnl_pct = (
                    trade["entry"] -
                    exit_price
                ) / trade["entry"] * 100

            close_trade(
                trade["id"],
                exit_price,
                reason,
                exit_time,
                pnl_pct
            )

            closed.append({
                "symbol": trade["symbol"],
                "side": trade["side"],
                "reason": reason,
                "pnl_pct": pnl_pct
            })

    return closed


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connection()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE exit_time IS NOT NULL
    """)

    closed = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE exit_time IS NOT NULL
          AND pnl_pct > 0
    """)

    wins = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE exit_time IS NOT NULL
          AND pnl_pct <= 0
    """)

    losses = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(pnl_pct), 0)
        FROM trades
        WHERE exit_time IS NOT NULL
    """)

    net = cur.fetchone()[0]

    conn.close()

    wr = (
        wins / closed * 100
        if closed
        else 0
    )

    return {
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "net": net
    }


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    tickers = get_tickers()

    for t in tickers:

        s = (
            t.get("symbol")
            or t.get("pair")
            or t.get("product_id")
        )

        if s != symbol:
            continue

        price = (
            safe_float(t.get("last"))
            or safe_float(t.get("markPrice"))
            or safe_float(t.get("price"))
        )

        if price:
            return price

    return None


# ============================================================
# P&L
# ============================================================

def live_pnl(trade, current):

    if current is None:
        return 0

    if trade["side"] == "LONG":

        return (
            current -
            trade["entry"]
        ) / trade["entry"] * 100

    return (
        trade["entry"] -
        current
    ) / trade["entry"] * 100


# ============================================================
# EVALUATE SYMBOL
# ============================================================

def evaluate_symbol(
    symbol,
    diagnostics
):

    try:

        c1h = get_candles(
            symbol,
            INTERVAL_1H,
            150
        )

        c15 = get_candles(
            symbol,
            INTERVAL_15M,
            150
        )

        c5 = get_candles(
            symbol,
            INTERVAL_5M,
            150
        )

        if (
            len(c1h) < 70
            or len(c15) < 50
            or len(c5) < 50
        ):

            diagnostics["data_fail"] += 1

            return None

        trend = trend_1h(c1h)

        if trend["direction"] == "NEUTRAL":

            diagnostics["trend_neutral"] += 1

            return None

        diagnostics[
            f"trend_{trend['direction'].lower()}"
        ] += 1

        supports, resistances = get_sr_zones(
            c15,
            c1h
        )

        if not supports and not resistances:

            diagnostics["sr_fail"] += 1

            return None

        setup = detect_15m_setup(
            c15,
            supports,
            resistances,
            trend["direction"]
        )

        if not setup["valid"]:

            diagnostics["setup_fail"] += 1

            return None

        diagnostics["setup_pass"] += 1

        confirmation = confirmation_5m(
            c5,
            setup["level"],
            trend["direction"]
        )

        if not confirmation["valid"]:

            diagnostics[
                "confirmation_fail"
            ] += 1

            return None

        diagnostics[
            "confirmation_pass"
        ] += 1

        volume_points, rv = volume_score(
            c5
        )

        if rv >= RVOL_NORMAL:
            diagnostics["rvol_pass"] += 1
        else:
            diagnostics["rvol_soft"] += 1

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        trend_points = clamp(
            trend["score"],
            0,
            5
        )

        setup_points = clamp(
            setup["score"],
            0,
            4
        )

        confirmation_points = clamp(
            confirmation["score"],
            0,
            4
        )

        score = (
            trend_points
            + setup_points
            + confirmation_points
            + volume_points
        )

        diagnostics["score_checked"] += 1

        if score < MIN_SCORE:

            diagnostics["score_fail"] += 1

            return None

        diagnostics["score_pass"] += 1

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = c5[-1]["close"]

        sl_data = calculate_sl(
            c5,
            entry,
            trend["direction"]
        )

        if not sl_data:

            diagnostics["sl_fail"] += 1

            return None

        if not (
            MIN_SL_PCT
            <= sl_data["pct"]
            <= MAX_SL_PCT
        ):

            diagnostics["sl_fail"] += 1

            return None

        diagnostics["sl_pass"] += 1

        tp_data = calculate_tp(
            entry,
            sl_data["price"],
            trend["direction"],
            supports,
            resistances
        )

        if not tp_data:

            diagnostics["tp_fail"] += 1

            return None

        diagnostics["tp_pass"] += 1

        if tp_data["rr"] < MIN_RR:

            diagnostics["rr_fail"] += 1

            return None

        diagnostics["rr_pass"] += 1

        return {
            "symbol": symbol,
            "side": trend["direction"],
            "entry": entry,
            "sl": sl_data["price"],
            "sl_pct": sl_data["pct"],
            "tp": tp_data["price"],
            "tp_pct": tp_data["pct"],
            "rr": tp_data["rr"],
            "score": int(score),
            "trend_score": int(trend_points),
            "setup_score": int(setup_points),
            "confirmation_score": int(
                confirmation_points
            ),
            "rvol": rv,
            "setup_type": setup["type"],
            "confirmation_type": confirmation["type"],
            "entry_time": c5[-1]["time"]
        }

    except Exception as e:

        diagnostics["errors"] += 1

        print(
            f"[ERROR] {symbol}: {e}"
        )

        traceback.print_exc()

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")

    if not token or not chat_id:
        print("[TELEGRAM] credentials missing")
        return

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": text
    }

    try:

        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        print(
            f"[TELEGRAM] {r.status_code}"
        )

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(t):

    emoji = (
        "🟢"
        if t["side"] == "LONG"
        else "🔴"
    )

    return (
        f"{emoji} {t['symbol']} "
        f"{t['side']}\n"
        f"Entry: {t['entry']:.8g}\n"
        f"SL: {t['sl']:.8g} "
        f"(-{t['sl_pct']:.2f}%)\n"
        f"TP: {t['tp']:.8g} "
        f"(+{t['tp_pct']:.2f}%)\n"
        f"RR: 1:{t['rr']:.2f}\n"
        f"Score: {t['score']}/15\n"
        f"Setup: {t['setup_type']}\n"
        f"5M: {t['confirmation_type']}\n"
        f"RVOL: {t['rvol']:.2f}"
    )


# ============================================================
# TELEGRAM REPORT
# ============================================================

def build_report(
    new_signals,
    open_trades,
    stats
):

    lines = []

    lines.append(
        f"📊 VOLUME-KHAT 100 v{VERSION}"
    )

    lines.append(
        f"🕐 {now_str()}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED | TOP 100"
    )

    lines.append("")

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append("🎯 NEW SIGNALS")

    if new_signals:

        for t in new_signals:

            lines.append(
                format_signal(t)
            )

            lines.append("")

    else:

        lines.append("None")

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})"
    )

    if open_trades:

        for trade in open_trades:

            current = get_current_price(
                trade["symbol"]
            )

            pnl = live_pnl(
                trade,
                current
            )

            emoji = (
                "🟢"
                if trade["side"] == "LONG"
                else "🔴"
            )

            current_text = (
                f"{current:.8g}"
                if current is not None
                else "N/A"
            )

            lines.append(
                f"{emoji} {trade['symbol']} "
                f"{trade['side']}"
            )

            lines.append(
                f"Entry: {trade['entry']:.8g}"
            )

            lines.append(
                f"Current: {current_text} "
                f"({pnl:+.2f}%)"
            )

            lines.append(
                f"SL: {trade['sl']:.8g}"
            )

            lines.append(
                f"TP: {trade['tp']:.8g}"
            )

            lines.append("")

    else:

        lines.append("None")

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    lines.append("📈 STATS")

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
        f"Net PnL: {stats['net']:+.2f}%"
    )

    lines.append("")

    lines.append(
        "Real trading: DISABLED"
    )

    return "\n".join(lines)


# ============================================================
# DIAGNOSTICS
# ============================================================

def print_diagnostics(
    diagnostics,
    scanned
):

    print("")
    print("=" * 60)
    print(f"VOLUME-KHAT {VERSION} DIAGNOSTICS")
    print("=" * 60)

    print(
        f"Scanned: {scanned}"
    )

    print(
        f"1H LONG: "
        f"{diagnostics['trend_long']}"
    )

    print(
        f"1H SHORT: "
        f"{diagnostics['trend_short']}"
    )

    print(
        f"1H NEUTRAL: "
        f"{diagnostics['trend_neutral']}"
    )

    print(
        f"Data fail: "
        f"{diagnostics['data_fail']}"
    )

    print(
        f"S/R fail: "
        f"{diagnostics['sr_fail']}"
    )

    print(
        f"15M setup PASS: "
        f"{diagnostics['setup_pass']}"
    )

    print(
        f"15M setup FAIL: "
        f"{diagnostics['setup_fail']}"
    )

    print(
        f"5M confirmation PASS: "
        f"{diagnostics['confirmation_pass']}"
    )

    print(
        f"5M confirmation FAIL: "
        f"{diagnostics['confirmation_fail']}"
    )

    print(
        f"RVOL >= {RVOL_NORMAL}: "
        f"{diagnostics['rvol_pass']}"
    )

    print(
        f"RVOL soft: "
        f"{diagnostics['rvol_soft']}"
    )

    print(
        f"Score checked: "
        f"{diagnostics['score_checked']}"
    )

    print(
        f"Score >= {MIN_SCORE}: "
        f"{diagnostics['score_pass']}"
    )

    print(
        f"Score fail: "
        f"{diagnostics['score_fail']}"
    )

    print(
        f"SL pass: "
        f"{diagnostics['sl_pass']}"
    )

    print(
        f"SL fail: "
        f"{diagnostics['sl_fail']}"
    )

    print(
        f"TP pass: "
        f"{diagnostics['tp_pass']}"
    )

    print(
        f"TP fail: "
        f"{diagnostics['tp_fail']}"
    )

    print(
        f"RR pass: "
        f"{diagnostics['rr_pass']}"
    )

    print(
        f"RR fail: "
        f"{diagnostics['rr_fail']}"
    )

    print(
        f"Errors: "
        f"{diagnostics['errors']}"
    )

    print("=" * 60)


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print("=" * 60)
    print(
        f"KRAKEN FUTURES VOLUME-KHAT 100 v{VERSION}"
    )
    print(now_str())
    print("=" * 60)

    init_db()

    # --------------------------------------------------------
    # First process existing trades.
    # --------------------------------------------------------

    closed = process_open_trades()

    if closed:

        print(
            f"[CLOSED] {len(closed)} trades"
        )

        for t in closed:

            print(
                f"{t['symbol']} "
                f"{t['side']} "
                f"{t['reason']} "
                f"{t['pnl_pct']:+.2f}%"
            )

    # --------------------------------------------------------
    # Existing open trades
    # --------------------------------------------------------

    open_trades = get_open_trades()

    print(
        f"[OPEN] {len(open_trades)}/"
        f"{MAX_OPEN_TRADES}"
    )

    # --------------------------------------------------------
    # Top 100
    # --------------------------------------------------------

    symbols = get_top_symbols()

    print(
        f"[TOP] {len(symbols)} symbols"
    )

    diagnostics = {
        "trend_long": 0,
        "trend_short": 0,
        "trend_neutral": 0,

        "data_fail": 0,
        "sr_fail": 0,

        "setup_pass": 0,
        "setup_fail": 0,

        "confirmation_pass": 0,
        "confirmation_fail": 0,

        "rvol_pass": 0,
        "rvol_soft": 0,

        "score_checked": 0,
        "score_pass": 0,
        "score_fail": 0,

        "sl_pass": 0,
        "sl_fail": 0,

        "tp_pass": 0,
        "tp_fail": 0,

        "rr_pass": 0,
        "rr_fail": 0,

        "errors": 0
    }

    candidates = []

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for item in symbols:

        if len(open_trades) >= MAX_OPEN_TRADES:
            break

        symbol = item["symbol"]

        # Existing position on same symbol
        if any(
            x["symbol"] == symbol
            for x in open_trades
        ):
            continue

        if recently_traded(symbol):
            continue

        result = evaluate_symbol(
            symbol,
            diagnostics
        )

        if result:
            candidates.append(result)

    # --------------------------------------------------------
    # Rank candidates
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # Create new trades
    # --------------------------------------------------------

    new_signals = []

    slots = (
        MAX_OPEN_TRADES -
        len(open_trades)
    )

    slots = max(
        0,
        min(
            slots,
            MAX_NEW_SIGNALS
        )
    )

    for candidate in candidates[:slots]:

        # Double-check same symbol
        if any(
            x["symbol"] ==
            candidate["symbol"]
            for x in open_trades
        ):
            continue

        insert_trade(candidate)

        new_signals.append(
            candidate
        )

        open_trades.append({
            "id": None,
            "symbol": candidate["symbol"],
            "side": candidate["side"],
            "entry": candidate["entry"],
            "sl": candidate["sl"],
            "tp": candidate["tp"],
            "entry_time": candidate["entry_time"],
            "score": candidate["score"],
            "setup_type": candidate["setup_type"],
            "confirmation_type": candidate[
                "confirmation_type"
            ],
            "rvol": candidate["rvol"]
        })

    # --------------------------------------------------------
    # Stats
    # --------------------------------------------------------

    stats = get_stats()

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report = build_report(
        new_signals,
        open_trades,
        stats
    )

    print("")
    print(report)
    print("")

    print_diagnostics(
        diagnostics,
        len(symbols)
    )

    send_telegram(report)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            f"[FATAL ERROR] {e}"
        )

        traceback.print_exc()

        raise
