# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.3 FIXED
# ============================================================
#
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE ZONE + PRICE REACTION
# 5M  = FRESH BREAKOUT + PULLBACK + CONFIRMATION
# VOL = RVOL CONFIRMATION
#
# TP:
#   LONG  -> nearest valid resistance, then next resistance
#   SHORT -> nearest valid support, then next support
#
# TP requirements:
#   RR > MIN_TP_RR
#   TP distance <= MAX_TP_DISTANCE_PCT
#
# EXIT:
#   Historical 5M HIGH / LOW
#   Time exit disabled
#
# IMPORTANT v7.3 FIX:
#   OPEN trades are NEVER removed merely because a new scan
#   has no signal.
#
#   An OPEN trade can become CLOSED only when:
#       SL is hit
#       OR
#       TP is hit
#
#   Database persistence between GitHub Actions runs is handled
#   by main.yml.
#
# REAL TRADING = DISABLED
# ============================================================

import os
import time
import sqlite3
from datetime import datetime, timezone

import requests
import numpy as np


# ============================================================
# CONFIG
# ============================================================

VERSION = "v7.3"

KRAKEN_BASE = "https://futures.kraken.com"

TICKERS_URL = f"{KRAKEN_BASE}/derivatives/api/v3/tickers"
CHART_URL = f"{KRAKEN_BASE}/api/charts/v1/trade"

DB_FILE = "volume_khat_100_v73.db"

PAPER_TRADING = True

TOP_N = 100

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3


# ============================================================
# TIMEFRAMES
# ============================================================

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

LOOKBACK_1H = 180
LOOKBACK_15M = 220
LOOKBACK_5M = 250


# ============================================================
# MOVING AVERAGES
# ============================================================

SMA_FAST = 20
SMA_SLOW = 50


# ============================================================
# TREND
# ============================================================

MIN_TREND_SCORE = 7


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

PIVOT_SWING = 3

ZONE_TOLERANCE_PCT = 0.30

MIN_ZONE_TOUCHES = 1

TP_ZONE_BUFFER_PCT = 0.10


# ============================================================
# VOLUME
# ============================================================

RVOL_PERIOD = 20

RVOL_NORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00


# ============================================================
# BREAKOUT
# ============================================================

BREAKOUT_LOOKBACK = 5

BREAKOUT_MAX_AGE = 3

BREAKOUT_BUFFER_PCT = 0.05


# ============================================================
# CONFIRMATION
# ============================================================

MIN_BODY_PCT = 0.20

WICK_BODY_RATIO = 1.15


# ============================================================
# SL
# ============================================================

ATR_PERIOD = 14

ATR_SL_MULTIPLIER = 0.20

MIN_SL_PCT = 0.50

MAX_SL_PCT = 1.50


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 10


# ============================================================
# TP
# ============================================================

MIN_TP_RR = 1.00

MAX_TP_DISTANCE_PCT = 3.00


# ============================================================
# EXIT
# ============================================================

TIME_EXIT_ENABLED = False


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "VOLUME-KHAT-100/7.3",
        "Accept": "application/json",
    }
)

TICKER_CACHE = {}


# ============================================================
# UTILITIES
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def normalize_ts(value):
    value = safe_float(value)

    if value > 10_000_000_000:
        value /= 1000.0

    return value


def format_price(value):

    value = safe_float(value)

    if value >= 100:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def pct_distance(a, b):

    a = safe_float(a)
    b = safe_float(b)

    if a <= 0:
        return 0.0

    return abs(b - a) / a * 100.0


# ============================================================
# DATABASE
# ============================================================

def db():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db()

    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            setup TEXT,

            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,

            status TEXT,
            result TEXT,
            pnl REAL,

            entry_time REAL,
            exit_time REAL,

            exit REAL,
            exit_reason TEXT,

            duration_minutes REAL,

            closed_reported INTEGER DEFAULT 0,
            created_at REAL
        )
        """
    )

    conn.commit()

    columns = {
        row["name"]
        for row in cur.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()
    }

    required_columns = {
        "setup": "TEXT",
        "rr": "REAL",
        "exit_reason": "TEXT",
        "duration_minutes": "REAL",
        "closed_reported": "INTEGER DEFAULT 0",
        "created_at": "REAL",
    }

    for name, definition in required_columns.items():

        if name not in columns:

            cur.execute(
                f"ALTER TABLE trades ADD COLUMN {name} {definition}"
            )

    conn.commit()
    conn.close()


# ============================================================
# API
# ============================================================

def get_json(url, params=None, timeout=20):

    response = SESSION.get(
        url,
        params=params,
        timeout=timeout
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# TICKERS
# ============================================================

def get_all_tickers():

    global TICKER_CACHE

    try:

        data = get_json(TICKERS_URL)

        tickers = data.get("tickers", [])

        result = {}

        for ticker in tickers:

            symbol = ticker.get("symbol")

            if not symbol:
                continue

            result[symbol] = ticker

        TICKER_CACHE = result

        return result

    except Exception as e:

        print(f"❌ Ticker error: {e}")

        return TICKER_CACHE


def get_current_price(symbol):

    ticker = TICKER_CACHE.get(symbol)

    if not ticker:
        return None

    for key in (
        "last",
        "lastPrice",
        "price"
    ):

        if key in ticker:

            price = safe_float(
                ticker[key]
            )

            if price > 0:
                return price

    return None


# ============================================================
# TOP 100
# ============================================================

def get_top_symbols():

    tickers = get_all_tickers()

    candidates = []

    for symbol, ticker in tickers.items():

        if not symbol.startswith("PF_"):
            continue

        if not symbol.endswith("USD"):
            continue

        volume = 0.0

        for key in (
            "vol24h",
            "volume24h",
            "volume"
        ):

            if key in ticker:

                volume = safe_float(
                    ticker[key]
                )

                if volume > 0:
                    break

        candidates.append(
            (
                symbol,
                volume
            )
        )

    candidates.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        symbol
        for symbol, _ in candidates[:TOP_N]
    ]


# ============================================================
# CANDLES
# ============================================================

def parse_candles(data):

    raw = None

    if isinstance(data, dict):

        raw = (
            data.get("candles")
            or data.get("data")
            or data.get("result")
        )

    elif isinstance(data, list):

        raw = data

    if not raw:
        return []

    candles = []

    for item in raw:

        try:

            if isinstance(item, dict):

                ts = (
                    item.get("time")
                    or item.get("timestamp")
                    or item.get("t")
                )

                o = item.get(
                    "open",
                    item.get("o")
                )

                h = item.get(
                    "high",
                    item.get("h")
                )

                l = item.get(
                    "low",
                    item.get("l")
                )

                c = item.get(
                    "close",
                    item.get("c")
                )

                v = item.get(
                    "volume",
                    item.get("v", 0)
                )

            else:

                if len(item) < 6:
                    continue

                ts, o, h, l, c, v = item[:6]

            ts = normalize_ts(ts)

            o = safe_float(o)
            h = safe_float(h)
            l = safe_float(l)
            c = safe_float(c)
            v = safe_float(v)

            if min(o, h, l, c) <= 0:
                continue

            candles.append(
                {
                    "time": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": c,
                    "volume": v,
                }
            )

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


def get_candles(symbol, resolution, limit):

    try:

        url = (
            f"{CHART_URL}/"
            f"{symbol}/"
            f"{resolution}"
        )

        data = get_json(url)

        candles = parse_candles(data)

        if len(candles) < 10:
            return []

        # CLOSED CANDLES ONLY
        candles = candles[:-1]

        return candles[-limit:]

    except Exception as e:

        print(
            f"❌ Candle error "
            f"{symbol} {resolution}: {e}"
        )

        return []


# ============================================================
# INDICATORS
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return float(
        np.mean(values[-period:])
    )


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
            abs(
                current["high"]
                - previous["close"]
            ),
            abs(
                current["low"]
                - previous["close"]
            )
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return float(
        np.mean(trs[-period:])
    )


def rvol(candles, period=20):

    if len(candles) < period + 1:
        return 0.0

    current = candles[-1]["volume"]

    previous = [
        c["volume"]
        for c in candles[-period - 1:-1]
    ]

    avg = np.mean(previous)

    if avg <= 0:
        return 0.0

    return current / avg


# ============================================================
# PIVOTS
# ============================================================

def pivot_highs(candles, swing=PIVOT_SWING):

    levels = []

    for i in range(
        swing,
        len(candles) - swing
    ):

        high = candles[i]["high"]

        left = [
            candles[j]["high"]
            for j in range(
                i - swing,
                i
            )
        ]

        right = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + swing + 1
            )
        ]

        if high >= max(left) and high >= max(right):

            levels.append(
                {
                    "price": high,
                    "time": candles[i]["time"],
                    "type": "RESISTANCE"
                }
            )

    return levels


def pivot_lows(candles, swing=PIVOT_SWING):

    levels = []

    for i in range(
        swing,
        len(candles) - swing
    ):

        low = candles[i]["low"]

        left = [
            candles[j]["low"]
            for j in range(
                i - swing,
                i
            )
        ]

        right = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + swing + 1
            )
        ]

        if low <= min(left) and low <= min(right):

            levels.append(
                {
                    "price": low,
                    "time": candles[i]["time"],
                    "type": "SUPPORT"
                }
            )

    return levels


# ============================================================
# S/R ZONES
# ============================================================

def build_zone(levels):

    prices = [
        x["price"]
        for x in levels
    ]

    return {
        "low": min(prices),
        "high": max(prices),
        "center": float(
            np.mean(prices)
        ),
        "touches": len(levels),
        "type": levels[0]["type"],
        "last_time": max(
            x["time"]
            for x in levels
        )
    }


def cluster_levels(levels):

    if not levels:
        return []

    levels = sorted(
        levels,
        key=lambda x: x["price"]
    )

    zones = []

    current = [levels[0]]

    for level in levels[1:]:

        center = np.mean(
            [
                x["price"]
                for x in current
            ]
        )

        distance = (
            abs(level["price"] - center)
            / center
            * 100
        )

        if distance <= ZONE_TOLERANCE_PCT:

            current.append(level)

        else:

            zones.append(
                build_zone(current)
            )

            current = [level]

    zones.append(
        build_zone(current)
    )

    return zones


def build_sr_zones(
    candles_15m,
    candles_1h
):

    resistance = (
        pivot_highs(candles_15m)
        + pivot_highs(candles_1h)
    )

    support = (
        pivot_lows(candles_15m)
        + pivot_lows(candles_1h)
    )

    return (
        cluster_levels(resistance)
        and None
    ) if False else (
        cluster_levels(support),
        cluster_levels(resistance)
    )


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(c):

    return abs(
        c["close"] - c["open"]
    )


def upper_wick(c):

    return (
        c["high"]
        - max(c["open"], c["close"])
    )


def lower_wick(c):

    return (
        min(c["open"], c["close"])
        - c["low"]
    )


# ============================================================
# PRICE REACTION
# ============================================================

def resistance_reaction(
    candles,
    zone
):

    if len(candles) < 3:
        return False

    recent = candles[-3:]

    touched = False
    rejected = False

    for c in recent:

        if c["high"] >= zone["low"]:
            touched = True

        body = candle_body(c)

        if body <= 0:
            continue

        upper = upper_wick(c)

        bearish = (
            c["close"] < c["open"]
        )

        close_low = (
            (
                c["close"]
                - c["low"]
            )
            /
            max(
                c["high"]
                - c["low"],
                1e-12
            )
            <= 0.45
        )

        if (
            bearish
            and upper / body >= WICK_BODY_RATIO
            and close_low
        ):

            rejected = True

    last = candles[-1]

    failed_above = (
        last["close"] <= zone["high"]
    )

    return (
        touched
        and rejected
        and failed_above
    )


def support_reaction(
    candles,
    zone
):

    if len(candles) < 3:
        return False

    recent = candles[-3:]

    touched = False
    rejected = False

    for c in recent:

        if c["low"] <= zone["high"]:
            touched = True

        body = candle_body(c)

        if body <= 0:
            continue

        lower = lower_wick(c)

        bullish = (
            c["close"] > c["open"]
        )

        close_high = (
            (
                c["close"]
                - c["low"]
            )
            /
            max(
                c["high"]
                - c["low"],
                1e-12
            )
            >= 0.55
        )

        if (
            bullish
            and lower / body >= WICK_BODY_RATIO
            and close_high
        ):

            rejected = True

    last = candles[-1]

    failed_below = (
        last["close"] >= zone["low"]
    )

    return (
        touched
        and rejected
        and failed_below
    )


# ============================================================
# 15M SETUP
# ============================================================

def resistance_break_hold(
    candles,
    zone
):

    if len(candles) < 4:
        return False

    previous = candles[-2]
    current = candles[-1]

    return (
        previous["close"] <= zone["high"]
        and current["close"] > zone["high"]
    )


def support_break_hold(
    candles,
    zone
):

    if len(candles) < 4:
        return False

    previous = candles[-2]
    current = candles[-1]

    return (
        previous["close"] >= zone["low"]
        and current["close"] < zone["low"]
    )


def detect_15m_setup(
    side,
    candles_15m,
    support_zones,
    resistance_zones
):

    if side == "LONG":

        for zone in support_zones:

            if support_reaction(
                candles_15m,
                zone
            ):

                return {
                    "setup": "SUPPORT_HOLD",
                    "zone": zone,
                    "score": 4
                }

        for zone in resistance_zones:

            if resistance_break_hold(
                candles_15m,
                zone
            ):

                return {
                    "setup":
                        "RESISTANCE_BREAK_HOLD",
                    "zone": zone,
                    "score": 4
                }

        return None

    for zone in resistance_zones:

        if resistance_reaction(
            candles_15m,
            zone
        ):

            return {
                "setup": "RESISTANCE_HOLD",
                "zone": zone,
                "score": 4
            }

    for zone in support_zones:

        if support_break_hold(
            candles_15m,
            zone
        ):

            return {
                "setup":
                    "SUPPORT_BREAK_HOLD",
                "zone": zone,
                "score": 4
            }

    return None


# ============================================================
# 1H TREND
# ============================================================

def get_structure(candles):

    highs = [
        c["high"]
        for c in candles[-10:]
    ]

    lows = [
        c["low"]
        for c in candles[-10:]
    ]

    if len(highs) < 6:
        return None

    recent_high = highs[-1]

    previous_high = max(
        highs[-6:-1]
    )

    recent_low = lows[-1]

    previous_low = min(
        lows[-6:-1]
    )

    return {
        "HH": recent_high > previous_high,
        "HL": recent_low > previous_low,
        "LH": recent_high < previous_high,
        "LL": recent_low < previous_low,
    }


def trend_1h(candles):

    if len(candles) < SMA_SLOW + 10:

        return {
            "side": None,
            "score": 0
        }

    closes = [
        c["close"]
        for c in candles
    ]

    price = closes[-1]

    sma20 = sma(
        closes,
        SMA_FAST
    )

    sma50 = sma(
        closes,
        SMA_SLOW
    )

    old_sma20 = sma(
        closes[:-5],
        SMA_FAST
    )

    structure = get_structure(
        candles
    )

    if not structure:

        return {
            "side": None,
            "score": 0
        }

    score_long = 0
    score_short = 0

    if structure["HH"]:
        score_long += 2

    if structure["HL"]:
        score_long += 1

    if structure["LH"]:
        score_short += 2

    if structure["LL"]:
        score_short += 1

    if sma20 > sma50:
        score_long += 2

    if sma20 < sma50:
        score_short += 2

    if price > sma20:
        score_long += 1

    if price < sma20:
        score_short += 1

    if old_sma20 is not None:

        if sma20 > old_sma20:
            score_long += 1

        if sma20 < old_sma20:
            score_short += 1

    if score_long >= MIN_TREND_SCORE:

        return {
            "side": "LONG",
            "score": score_long
        }

    if score_short >= MIN_TREND_SCORE:

        return {
            "side": "SHORT",
            "score": score_short
        }

    return {
        "side": None,
        "score": max(
            score_long,
            score_short
        )
    }


# ============================================================
# 5M BREAKOUT
# ============================================================

def find_recent_breakout(
    candles,
    side,
    zone
):

    if len(candles) < BREAKOUT_LOOKBACK + 5:
        return None

    start = max(
        1,
        len(candles)
        - BREAKOUT_MAX_AGE
        - 2
    )

    end = len(candles) - 1

    for i in range(start, end):

        c = candles[i]

        if side == "LONG":

            level = zone["high"]

            broken = (
                c["close"]
                >
                level
                * (
                    1
                    + BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

        else:

            level = zone["low"]

            broken = (
                c["close"]
                <
                level
                * (
                    1
                    - BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

        if broken:

            return {
                "index": i,
                "level": level,
                "candle": c
            }

    return None


# ============================================================
# PULLBACK
# ============================================================

def detect_pullback(
    candles,
    side,
    breakout
):

    idx = breakout["index"]

    level = breakout["level"]

    if idx >= len(candles) - 1:
        return None

    later = candles[idx + 1:]

    for i, c in enumerate(later):

        if side == "LONG":

            touched = (
                c["low"]
                <= level
                * (
                    1
                    + ZONE_TOLERANCE_PCT
                    / 100
                )
            )

            held = (
                c["close"] >= level
            )

        else:

            touched = (
                c["high"]
                >= level
                * (
                    1
                    - ZONE_TOLERANCE_PCT
                    / 100
                )
            )

            held = (
                c["close"] <= level
            )

        if touched and held:

            return {
                "index": idx + 1 + i,
                "candle": c
            }

    return None


# ============================================================
# CONFIRMATION
# ============================================================

def evaluate_confirmation(
    candles,
    side,
    pullback
):

    idx = pullback["index"]

    if idx >= len(candles) - 1:

        return {
            "status": "WAIT",
            "passed": False,
            "reason":
                "WAITING_NEXT_CANDLE"
        }

    confirmation = candles[-1]

    pullback_candle = candles[idx]

    bullish = (
        confirmation["close"]
        > confirmation["open"]
    )

    bearish = (
        confirmation["close"]
        < confirmation["open"]
    )

    if side == "LONG":

        higher_close = (
            confirmation["close"]
            > pullback_candle["high"]
        )

        if not bullish:

            return {
                "status": "FAIL",
                "passed": False,
                "reason":
                    "CONFIRMATION_CANDLE_NOT_BULLISH"
            }

        if not higher_close:

            return {
                "status": "FAIL",
                "passed": False,
                "reason":
                    "CLOSE_DID_NOT_BREAK_PULLBACK_HIGH"
            }

        return {
            "status": "PASS",
            "passed": True,
            "reason":
                "CONFIRMATION_PASSED"
        }

    lower_close = (
        confirmation["close"]
        < pullback_candle["low"]
    )

    if not bearish:

        return {
            "status": "FAIL",
            "passed": False,
            "reason":
                "CONFIRMATION_CANDLE_NOT_BEARISH"
        }

    if not lower_close:

        return {
            "status": "FAIL",
            "passed": False,
            "reason":
                "CLOSE_DID_NOT_BREAK_PULLBACK_LOW"
        }

    return {
        "status": "PASS",
        "passed": True,
        "reason":
            "CONFIRMATION_PASSED"
    }


# ============================================================
# SL
# ============================================================

def calculate_sl(
    side,
    entry,
    candles_5m,
    setup_zone
):

    current_atr = atr(
        candles_5m,
        ATR_PERIOD
    )

    if current_atr is None:

        current_atr = (
            entry * 0.005
        )

    structural_buffer = (
        current_atr
        * ATR_SL_MULTIPLIER
    )

    if side == "LONG":

        structural_sl = (
            setup_zone["low"]
            - structural_buffer
        )

        min_sl = (
            entry
            * (
                1
                - MIN_SL_PCT
                / 100
            )
        )

        max_sl = (
            entry
            * (
                1
                - MAX_SL_PCT
                / 100
            )
        )

        sl = structural_sl

        if sl > min_sl:
            sl = min_sl

        if sl < max_sl:
            sl = max_sl

    else:

        structural_sl = (
            setup_zone["high"]
            + structural_buffer
        )

        min_sl = (
            entry
            * (
                1
                + MIN_SL_PCT
                / 100
            )
        )

        max_sl = (
            entry
            * (
                1
                + MAX_SL_PCT
                / 100
            )
        )

        sl = structural_sl

        if sl < min_sl:
            sl = min_sl

        if sl > max_sl:
            sl = max_sl

    distance_pct = pct_distance(
        entry,
        sl
    )

    if (
        distance_pct < MIN_SL_PCT
        or distance_pct > MAX_SL_PCT
    ):

        return None

    return sl


# ============================================================
# TP v7.3
# ============================================================

def select_tp(
    side,
    entry,
    sl,
    support_zones,
    resistance_zones
):

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        return None, "INVALID_RISK"

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        candidates = [
            z
            for z in resistance_zones
            if z["center"] > entry
        ]

        candidates.sort(
            key=lambda z: z["center"]
        )

        if not candidates:
            return None, "NO_VALID_SR_TARGET"

        for zone in candidates:

            tp = (
                zone["low"]
                * (
                    1
                    - TP_ZONE_BUFFER_PCT
                    / 100
                )
            )

            reward = tp - entry

            if reward <= 0:
                continue

            rr = reward / risk

            distance_pct = (
                reward
                / entry
                * 100
            )

            if rr <= MIN_TP_RR:
                continue

            if distance_pct > MAX_TP_DISTANCE_PCT:
                break

            return {
                "tp": tp,
                "rr": rr,
                "zone": zone,
                "reward": reward,
                "risk": risk,
                "distance_pct":
                    distance_pct
            }, None

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        candidates = [
            z
            for z in support_zones
            if z["center"] < entry
        ]

        candidates.sort(
            key=lambda z: z["center"],
            reverse=True
        )

        if not candidates:
            return None, "NO_VALID_SR_TARGET"

        for zone in candidates:

            tp = (
                zone["high"]
                * (
                    1
                    + TP_ZONE_BUFFER_PCT
                    / 100
                )
            )

            reward = entry - tp

            if reward <= 0:
                continue

            rr = reward / risk

            distance_pct = (
                reward
                / entry
                * 100
            )

            if rr <= MIN_TP_RR:
                continue

            if distance_pct > MAX_TP_DISTANCE_PCT:
                break

            return {
                "tp": tp,
                "rr": rr,
                "zone": zone,
                "reward": reward,
                "risk": risk,
                "distance_pct":
                    distance_pct
            }, None

    return None, "NO_VALID_TP"


# ============================================================
# OPEN TRADE / COOLDOWN
# ============================================================

def has_open_trade(symbol):

    conn = db()

    row = conn.execute(
        """
        SELECT id
        FROM trades
        WHERE symbol=?
          AND status='OPEN'
        LIMIT 1
        """,
        (symbol,)
    ).fetchone()

    conn.close()

    return row is not None


def in_cooldown(symbol):

    conn = db()

    row = conn.execute(
        """
        SELECT exit_time
        FROM trades
        WHERE symbol=?
          AND status='CLOSED'
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
        """,
        (symbol,)
    ).fetchone()

    conn.close()

    if not row:
        return False

    latest_exit = safe_float(
        row["exit_time"]
    )

    if latest_exit <= 0:
        return False

    return (
        time.time()
        - latest_exit
        <
        COOLDOWN_CANDLES * 5 * 60
    )


# ============================================================
# SAVE TRADE
# ============================================================

def save_trade(signal):

    conn = db()

    conn.execute(
        """
        INSERT INTO trades (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit,
            exit_reason,
            duration_minutes,
            closed_reported,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            'OPEN',
            NULL,
            NULL,
            ?,
            NULL,
            NULL,
            NULL,
            NULL,
            0,
            ?
        )
        """,
        (
            signal["symbol"],
            signal["side"],
            signal["setup"],
            signal["entry"],
            signal["sl"],
            signal["tp"],
            signal["rr"],
            signal["entry_time"],
            time.time()
        )
    )

    conn.commit()
    conn.close()


# ============================================================
# HISTORICAL EXIT ENGINE
# ============================================================
#
# CRITICAL RULE:
#
# If SL/TP is NOT hit:
#       return None
#
# The database row remains:
#       status='OPEN'
#
# There is NO code here that deletes an open trade.
#
# ============================================================

def process_open_trade(
    trade,
    candles_5m
):

    entry_time = safe_float(
        trade["entry_time"]
    )

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    if (
        entry <= 0
        or sl <= 0
        or tp <= 0
    ):
        return None

    relevant = [
        c
        for c in candles_5m
        if c["time"] >= entry_time
    ]

    if not relevant:
        return None

    side = trade["side"]

    for candle in relevant:

        high = safe_float(
            candle["high"]
        )

        low = safe_float(
            candle["low"]
        )

        if high <= 0 or low <= 0:
            continue

        if side == "LONG":

            sl_hit = low <= sl
            tp_hit = high >= tp

        else:

            sl_hit = high >= sl
            tp_hit = low <= tp

        # ====================================================
        # NOTHING HIT
        # ====================================================

        if not sl_hit and not tp_hit:

            # DO NOT CLOSE.
            # DO NOT DELETE.
            # KEEP OPEN.
            continue

        # ====================================================
        # BOTH HIT IN SAME CANDLE
        # ====================================================

        if sl_hit and tp_hit:

            exit_price = sl

            result = "LOSS"

            reason = (
                "AMBIGUOUS_SAME_CANDLE_SL_TP"
            )

        # ====================================================
        # SL
        # ====================================================

        elif sl_hit:

            exit_price = sl

            result = "LOSS"

            reason = (
                "HISTORICAL_5M_SL"
            )

        # ====================================================
        # TP
        # ====================================================

        else:

            exit_price = tp

            result = "WIN"

            reason = (
                "HISTORICAL_5M_TP"
            )

        # ====================================================
        # PNL
        # ====================================================

        if side == "LONG":

            pnl_pct = (
                exit_price - entry
            ) / entry * 100.0

        else:

            pnl_pct = (
                entry - exit_price
            ) / entry * 100.0

        exit_time = safe_float(
            candle["time"]
        )

        duration_minutes = (
            exit_time - entry_time
        ) / 60.0

        # ====================================================
        # CLOSE ATOMICALLY
        # ====================================================

        conn = db()

        cursor = conn.execute(
            """
            UPDATE trades
            SET
                status='CLOSED',
                result=?,
                pnl=?,
                exit_time=?,
                exit=?,
                exit_reason=?,
                duration_minutes=?
            WHERE id=?
              AND status='OPEN'
            """,
            (
                result,
                pnl_pct,
                exit_time,
                exit_price,
                reason,
                duration_minutes,
                trade["id"]
            )
        )

        conn.commit()

        updated = cursor.rowcount

        conn.close()

        if updated != 1:
            return None

        return {
            "result": result,
            "pnl": pnl_pct,
            "reason": reason,
            "exit_time": exit_time
        }

    # ========================================================
    # CRITICAL:
    #
    # NO SL
    # NO TP
    #
    # TRADE REMAINS OPEN
    # ========================================================

    return None


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    conn = db()

    open_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
        """
    ).fetchone()[0]

    closed = conn.execute(
        """
        SELECT
            COUNT(*) AS total,

            SUM(
                CASE
                    WHEN result='WIN'
                    THEN 1
                    ELSE 0
                END
            ) AS wins,

            SUM(
                CASE
                    WHEN result='LOSS'
                    THEN 1
                    ELSE 0
                END
            ) AS losses,

            COALESCE(
                SUM(pnl),
                0
            ) AS net_pnl

        FROM trades

        WHERE status='CLOSED'
        """
    ).fetchone()

    conn.close()

    total = closed["total"] or 0

    wins = closed["wins"] or 0

    losses = closed["losses"] or 0

    net = closed["net_pnl"] or 0.0

    win_rate = (
        wins / total * 100
        if total
        else 0.0
    )

    return {
        "open": open_count,
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net
    }


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# SIGNAL GENERATION
# ============================================================

def build_signal(
    symbol,
    side,
    trend,
    setup,
    candles_5m,
    support_zones,
    resistance_zones
):

    breakout = find_recent_breakout(
        candles_5m,
        side,
        setup["zone"]
    )

    if not breakout:
        return None

    pullback = detect_pullback(
        candles_5m,
        side,
        breakout
    )

    if not pullback:
        return None

    confirmation = evaluate_confirmation(
        candles_5m,
        side,
        pullback
    )

    if not confirmation["passed"]:
        return None

    rv = rvol(
        candles_5m,
        RVOL_PERIOD
    )

    if rv < RVOL_NORMAL:
        return None

    entry = candles_5m[-1]["close"]

    sl = calculate_sl(
        side,
        entry,
        candles_5m,
        setup["zone"]
    )

    if sl is None:
        return None

    tp_data, _ = select_tp(
        side,
        entry,
        sl,
        support_zones,
        resistance_zones
    )

    if tp_data is None:
        return None

    trend_score = min(
        trend["score"],
        7
    )

    setup_score = min(
        setup["score"],
        4
    )

    trigger_score = 3

    volume_score = 0

    if rv >= RVOL_NORMAL:
        volume_score += 1

    if rv >= RVOL_STRONG:
        volume_score += 1

    score = (
        trend_score
        + setup_score
        + trigger_score
        + volume_score
    )

    if score < MIN_SCORE:
        return None

    return {
        "symbol": symbol,
        "side": side,
        "setup": setup["setup"],
        "entry": entry,
        "sl": sl,
        "tp": tp_data["tp"],
        "rr": tp_data["rr"],
        "rv": rv,
        "score": score,
        "trend_score": trend_score,
        "entry_time":
            candles_5m[-1]["time"]
    }


# ============================================================
# REPORT
# ============================================================

def build_report(signals):

    perf = get_performance()

    open_trades = get_open_trades()

    lines = []

    lines.append(
        f"📊 VOLUME-KHAT 100 {VERSION}"
    )

    lines.append(
        "🕐 "
        f"{now_utc().strftime('%Y/%m/%d %H:%M:%S UTC')}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED | TOP 100"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if not signals:

        lines.append("None")

    else:

        for i, signal in enumerate(signals):

            sl_pct = pct_distance(
                signal["entry"],
                signal["sl"]
            )

            tp_pct = pct_distance(
                signal["entry"],
                signal["tp"]
            )

            emoji = (
                "🟢"
                if signal["side"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} "
                f"{signal['symbol']} "
                f"{signal['side']}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(signal['entry'])}"
            )

            lines.append(
                f"SL: "
                f"{format_price(signal['sl'])} "
                f"({sl_pct:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{format_price(signal['tp'])} "
                f"({tp_pct:.2f}%)"
            )

            lines.append(
                f"RR: {signal['rr']:.2f}"
            )

            lines.append(
                f"Score: "
                f"{signal['score']}/16"
            )

            if i < len(signals) - 1:

                lines.append(
                    "━━━━━━━━━━━━━━━━━━"
                )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # OPEN TRADES
    # ========================================================

    lines.append(
        "📂 OPEN TRADES "
        f"({perf['open']}/{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append("None")

    else:

        for i, trade in enumerate(open_trades):

            current = get_current_price(
                trade["symbol"]
            )

            if current is None:

                current = safe_float(
                    trade["entry"]
                )

            if trade["side"] == "LONG":

                pnl = (
                    current
                    - trade["entry"]
                ) / trade["entry"] * 100.0

            else:

                pnl = (
                    trade["entry"]
                    - current
                ) / trade["entry"] * 100.0

            # IMPORTANT:
            # Emoji represents SIDE, not PNL.
            emoji = (
                "🟢"
                if trade["side"] == "LONG"
                else "🔴"
            )

            sl_pct = pct_distance(
                trade["entry"],
                trade["sl"]
            )

            tp_pct = pct_distance(
                trade["entry"],
                trade["tp"]
            )

            lines.append(
                f"{emoji} "
                f"{trade['symbol']} "
                f"{trade['side']}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(trade['entry'])}"
            )

            lines.append(
                f"Current: "
                f"{format_price(current)} "
                f"({pnl:+.2f}%)"
            )

            lines.append(
                f"SL: "
                f"{format_price(trade['sl'])} "
                f"({sl_pct:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{format_price(trade['tp'])} "
                f"({tp_pct:.2f}%)"
            )

            lines.append(
                f"RR: "
                f"{safe_float(trade['rr']):.2f}"
            )

            if i < len(open_trades) - 1:

                lines.append(
                    "━━━━━━━━━━━━━━━━━━"
                )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # STATS
    # ========================================================

    lines.append(
        f"📈 STATS {VERSION}"
    )

    lines.append(
        f"Open: {perf['open']}"
    )

    lines.append(
        f"Closed: {perf['closed']}"
    )

    lines.append(
        f"Wins: {perf['wins']}"
    )

    lines.append(
        f"Losses: {perf['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{perf['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: "
        f"{perf['net_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "Real trading: "
        f"{'ENABLED' if not PAPER_TRADING else 'DISABLED'}"
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "❌ Telegram: "
            "TELEGRAM_BOT_TOKEN is EMPTY"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "❌ Telegram: "
            "TELEGRAM_CHAT_ID is EMPTY"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    max_length = 3900

    chunks = [
        text[i:i + max_length]
        for i in range(
            0,
            len(text),
            max_length
        )
    ]

    success = True

    for chunk in chunks:

        try:

            response = SESSION.post(
                url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": chunk
                },
                timeout=20
            )

            if response.status_code != 200:

                print(
                    f"❌ Telegram HTTP "
                    f"{response.status_code}"
                )

                print(
                    response.text[:500]
                )

                success = False

            else:

                print(
                    "📨 Telegram: HTTP 200"
                )

        except Exception as e:

            print(
                f"❌ Telegram error: {e}"
            )

            success = False

    return success


# ============================================================
# SCAN
# ============================================================

def scan():

    init_db()

    diagnostics = {
        "Scanned": 0,
        "1H LONG": 0,
        "1H SHORT": 0,
        "1H Neutral": 0,
        "15M Failed": 0,
        "Breakout Failed": 0,
        "Pullback Failed": 0,
        "Confirmation Failed": 0,
        "Confirmation Waiting": 0,
        "RVOL Failed": 0,
        "SL Failed": 0,
        "TP Failed": 0,
        "Score Failed": 0,
        "Cooldown": 0,
        "Already Open": 0,
        "Errors": 0
    }

    signals = []

    # ========================================================
    # GET TOP 100
    # ========================================================

    symbols = get_top_symbols()

    diagnostics["Scanned"] = len(symbols)

    # ========================================================
    # PROCESS EXISTING OPEN TRADES FIRST
    # ========================================================
    #
    # THIS IS THE MOST IMPORTANT PART.
    #
    # Existing trades are handled BEFORE looking for new
    # signals.
    #
    # They are never removed because there is no new signal.
    #
    # ========================================================

    existing_open_trades = get_open_trades()

    for trade in existing_open_trades:

        candles = get_candles(
            trade["symbol"],
            TF_5M,
            LOOKBACK_5M
        )

        if not candles:
            continue

        result = process_open_trade(
            trade,
            candles
        )

        if result:

            print(
                f"✅ CLOSED "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"{result['reason']} "
                f"PnL={result['pnl']:+.2f}%"
            )

    # ========================================================
    # RELOAD OPEN TRADES AFTER EXIT PROCESSING
    # ========================================================

    open_trades = get_open_trades()

    open_symbols = {
        trade["symbol"]
        for trade in open_trades
    }

    current_open_count = len(
        open_trades
    )

    # ========================================================
    # SCAN TOP 100
    # ========================================================

    for symbol in symbols:

        # ----------------------------------------------------
        # NEVER OPEN A SECOND TRADE ON SAME SYMBOL
        # ----------------------------------------------------

        if symbol in open_symbols:

            diagnostics["Already Open"] += 1

            continue

        # ----------------------------------------------------
        # COOLDOWN
        # ----------------------------------------------------

        if in_cooldown(symbol):

            diagnostics["Cooldown"] += 1

            continue

        try:

            candles_1h = get_candles(
                symbol,
                TF_1H,
                LOOKBACK_1H
            )

            candles_15m = get_candles(
                symbol,
                TF_15M,
                LOOKBACK_15M
            )

            candles_5m = get_candles(
                symbol,
                TF_5M,
                LOOKBACK_5M
            )

            if (
                not candles_1h
                or not candles_15m
                or not candles_5m
            ):

                diagnostics["Errors"] += 1

                continue

            # =================================================
            # 1H TREND
            # =================================================

            trend = trend_1h(
                candles_1h
            )

            side = trend["side"]

            if side == "LONG":

                diagnostics["1H LONG"] += 1

            elif side == "SHORT":

                diagnostics["1H SHORT"] += 1

            else:

                diagnostics["1H Neutral"] += 1

                continue

            # =================================================
            # RVOL
            # =================================================

            rv = rvol(
                candles_5m,
                RVOL_PERIOD
            )

            # =================================================
            # S/R
            # =================================================

            support_zones, resistance_zones = (
                build_sr_zones(
                    candles_15m,
                    candles_1h
                )
            )

            # =================================================
            # 15M SETUP
            # =================================================

            setup = detect_15m_setup(
                side,
                candles_15m,
                support_zones,
                resistance_zones
            )

            if not setup:

                diagnostics["15M Failed"] += 1

                continue

            # =================================================
            # BREAKOUT
            # =================================================

            breakout = find_recent_breakout(
                candles_5m,
                side,
                setup["zone"]
            )

            if not breakout:

                diagnostics[
                    "Breakout Failed"
                ] += 1

                continue

            # =================================================
            # PULLBACK
            # =================================================

            pullback = detect_pullback(
                candles_5m,
                side,
                breakout
            )

            if not pullback:

                diagnostics[
                    "Pullback Failed"
                ] += 1

                continue

            # =================================================
            # CONFIRMATION
            # =================================================

            confirmation = evaluate_confirmation(
                candles_5m,
                side,
                pullback
            )

            if confirmation["status"] == "WAIT":

                diagnostics[
                    "Confirmation Waiting"
                ] += 1

                continue

            if not confirmation["passed"]:

                diagnostics[
                    "Confirmation Failed"
                ] += 1

                continue

            # =================================================
            # RVOL
            # =================================================

            if rv < RVOL_NORMAL:

                diagnostics[
                    "RVOL Failed"
                ] += 1

                continue

            # =================================================
            # ENTRY
            # =================================================

            entry = candles_5m[-1]["close"]

            # =================================================
            # SL
            # =================================================

            sl = calculate_sl(
                side,
                entry,
                candles_5m,
                setup["zone"]
            )

            if sl is None:

                diagnostics[
                    "SL Failed"
                ] += 1

                continue

            # =================================================
            # TP
            # =================================================

            tp_data, tp_reason = select_tp(
                side,
                entry,
                sl,
                support_zones,
                resistance_zones
            )

            if tp_data is None:

                diagnostics[
                    "TP Failed"
                ] += 1

                continue

            # =================================================
            # SCORE
            # =================================================

            trend_score = min(
                trend["score"],
                7
            )

            setup_score = min(
                setup["score"],
                4
            )

            trigger_score = 3

            volume_score = 0

            if rv >= RVOL_NORMAL:
                volume_score += 1

            if rv >= RVOL_STRONG:
                volume_score += 1

            score = (
                trend_score
                + setup_score
                + trigger_score
                + volume_score
            )

            if score < MIN_SCORE:

                diagnostics[
                    "Score Failed"
                ] += 1

                continue

            # =================================================
            # MAX OPEN
            # =================================================

            if current_open_count >= MAX_OPEN_TRADES:

                continue

            # =================================================
            # MAX NEW SIGNALS
            # =================================================

            if len(signals) >= MAX_NEW_SIGNALS:

                continue

            # =================================================
            # CREATE SIGNAL
            # =================================================

            signal = {
                "symbol": symbol,
                "side": side,
                "setup": setup["setup"],
                "entry": entry,
                "sl": sl,
                "tp": tp_data["tp"],
                "rr": tp_data["rr"],
                "rv": rv,
                "score": score,
                "trend_score":
                    trend_score,
                "entry_time":
                    candles_5m[-1]["time"]
            }

            # =================================================
            # SAVE OPEN TRADE
            # =================================================

            save_trade(signal)

            signals.append(signal)

            open_symbols.add(symbol)

            current_open_count += 1

            print(
                f"🎯 NEW SIGNAL "
                f"{symbol} {side} "
                f"Entry={entry} "
                f"SL={sl} "
                f"TP={tp_data['tp']} "
                f"RR={tp_data['rr']:.2f}"
            )

        except Exception as e:

            diagnostics["Errors"] += 1

            print(
                f"❌ Scan error "
                f"{symbol}: {e}"
            )

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        signals
    )

    print(report)

    send_telegram(report)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except Exception as e:

        print(
            f"❌ FATAL ERROR: {e}"
        )

        try:

            send_telegram(
                f"❌ VOLUME-KHAT 100 {VERSION}\n"
                f"Fatal scanner error:\n{e}"
            )

        except Exception:
            pass

        raise
