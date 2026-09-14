# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.3
# ============================================================
#
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE ZONE + PRICE REACTION
# 5M  = FRESH BREAKOUT + PULLBACK + CONFIRMATION
# VOL = RVOL CONFIRMATION
#
# TP v7.3
# ------------------------------------------------------------
# LONG:
#   Nearest Resistance
#       ↓
#   RR sufficient? YES → TP
#       ↓ NO
#   Next Resistance
#       ↓
#   RR sufficient? YES → TP
#       ↓ NO
#   Next Resistance
#
# SHORT:
#   Nearest Support
#       ↓
#   RR sufficient? YES → TP
#       ↓ NO
#   Next Support
#
# TP must satisfy BOTH:
#   RR > MIN_TP_RR
#   TP distance <= MAX_TP_DISTANCE_PCT
#
# ENTRY FILTERS UNCHANGED
# ------------------------------------------------------------
# 1H Trend + Trend Strength
# 15M Support / Resistance + Reaction
# 5M Breakout + Pullback + Confirmation
# RVOL
# Score
# SL
#
# CLOSED CANDLES ONLY
#
# EXIT = HISTORICAL 5M HIGH / LOW
# TIME EXIT = DISABLED
#
# STATISTICS
# ------------------------------------------------------------
# v7.3 uses a separate database.
# Previous v7.2 statistics remain untouched.
#
# TELEGRAM REPORT
# ------------------------------------------------------------
# Only:
# - New Signals
# - Open Trades
# - Performance
#
# Internal calculations remain active but are NOT displayed:
# - Support / Resistance levels
# - Setup Zone
# - Breakout details
# - Pullback details
# - Confirmation details
# - RVOL
# - Diagnostics
# - Rejection reason
# - Scanned count
# - Individual filter states
# - Full strategy settings
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

# ------------------------------------------------------------
# IMPORTANT:
# v7.3 uses a separate DB.
# v7.2 database remains untouched.
# ------------------------------------------------------------

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
# TREND SCORE
# ============================================================

MIN_TREND_SCORE = 7


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

PIVOT_SWING = 3

ZONE_TOLERANCE_PCT = 0.30

MIN_ZONE_TOUCHES = 1

TP_ZONE_BUFFER_PCT = 0.10

SR_REPORT_LIMIT = 3


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
# CANDLE CONFIRMATION
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

SL_PRICE_BUFFER_PCT = 0.15


# ============================================================
# SCORE
# ============================================================
#
# Trend       0-7
# Setup       0-4
# Trigger     0-3
# Volume      0-2
#
# MAX = 16
#
# ============================================================

MIN_SCORE = 10


# ============================================================
# TP v7.3
# ============================================================

# Minimum acceptable reward/risk.
# TP must be STRICTLY greater than this value.

MIN_TP_RR = 1.00

# Maximum allowed TP distance from entry.
#
# This prevents the scanner from selecting a very distant
# S/R level merely to manufacture an acceptable RR.
#
# TP must satisfy:
#
#     RR > MIN_TP_RR
#     AND
#     TP distance <= MAX_TP_DISTANCE_PCT
#
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


def ts_to_text(ts):
    try:
        return datetime.fromtimestamp(
            float(ts),
            tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    except Exception:
        return ""


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def pct_distance(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if a <= 0:
        return 0.0

    return abs(b - a) / a * 100.0


def clamp(value, low, high):
    return max(low, min(high, value))


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
# KRAKEN API
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

        for t in tickers:

            symbol = t.get("symbol")

            if not symbol:
                continue

            result[symbol] = t

        TICKER_CACHE = result

        return result

    except Exception as e:

        print(f"❌ Ticker error: {e}")

        return TICKER_CACHE


def get_current_price(symbol):

    ticker = TICKER_CACHE.get(symbol)

    if not ticker:
        return None

    for key in ("last", "lastPrice", "price"):

        if key in ticker:

            price = safe_float(ticker[key])

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
            "volume",
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
# ZONES
# ============================================================

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


def build_sr_zones(candles_15m, candles_1h):

    resistance = (
        pivot_highs(candles_15m)
        + pivot_highs(candles_1h)
    )

    support = (
        pivot_lows(candles_15m)
        + pivot_lows(candles_1h)
    )

    resistance_zones = cluster_levels(
        resistance
    )

    support_zones = cluster_levels(
        support
    )

    return support_zones, resistance_zones


# ============================================================
# S/R INTERNAL HELPERS
# ============================================================

def sort_supports_below_price(
    support_zones,
    price
):

    return sorted(
        [
            z
            for z in support_zones
            if z["center"] < price
        ],
        key=lambda z: z["center"],
        reverse=True
    )


def sort_resistances_above_price(
    resistance_zones,
    price
):

    return sorted(
        [
            z
            for z in resistance_zones
            if z["center"] > price
        ],
        key=lambda z: z["center"]
    )


def nearest_support(
    support_zones,
    price
):

    candidates = sort_supports_below_price(
        support_zones,
        price
    )

    return candidates[0] if candidates else None


def nearest_resistance(
    resistance_zones,
    price
):

    candidates = sort_resistances_above_price(
        resistance_zones,
        price
    )

    return candidates[0] if candidates else None


def nearest_supports(
    support_zones,
    price,
    limit=SR_REPORT_LIMIT
):

    return sort_supports_below_price(
        support_zones,
        price
    )[:limit]


def nearest_resistances(
    resistance_zones,
    price,
    limit=SR_REPORT_LIMIT
):

    return sort_resistances_above_price(
        resistance_zones,
        price
    )[:limit]


def zone_text(zone):

    if not zone:
        return "None"

    return (
        f"{format_price(zone['low'])}"
        f" - "
        f"{format_price(zone['high'])}"
        f" | C:{format_price(zone['center'])}"
        f" | T:{zone['touches']}"
    )


def sr_list_text(
    zones,
    price,
    side,
    limit=SR_REPORT_LIMIT
):

    if side == "SUPPORT":

        selected = nearest_supports(
            zones,
            price,
            limit
        )

    else:

        selected = nearest_resistances(
            zones,
            price,
            limit
        )

    if not selected:
        return "None"

    return " | ".join(
        zone_text(z)
        for z in selected
    )


def contextual_sr_text(
    support_zones,
    resistance_zones,
    price
):

    support = nearest_support(
        support_zones,
        price
    )

    resistance = nearest_resistance(
        resistance_zones,
        price
    )

    return support, resistance


# ============================================================
# PRICE REACTION
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


def is_inside_or_near_zone(
    price,
    zone,
    tolerance_pct=0.20
):

    tolerance = (
        price
        * tolerance_pct
        / 100
    )

    return (
        zone["low"] - tolerance
        <= price
        <= zone["high"] + tolerance
    )


def resistance_reaction(candles, zone):

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
        last["close"]
        <= zone["high"]
    )

    return (
        touched
        and rejected
        and failed_above
    )


def support_reaction(candles, zone):

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
        last["close"]
        >= zone["low"]
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

    previous_below = (
        previous["close"]
        <= zone["high"]
    )

    current_above = (
        current["close"]
        > zone["high"]
    )

    return (
        previous_below
        and current_above
    )


def support_break_hold(
    candles,
    zone
):

    if len(candles) < 4:
        return False

    previous = candles[-2]
    current = candles[-1]

    previous_above = (
        previous["close"]
        >= zone["low"]
    )

    current_below = (
        current["close"]
        < zone["low"]
    )

    return (
        previous_above
        and current_below
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
                    "setup": "RESISTANCE_BREAK_HOLD",
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
                "setup": "SUPPORT_BREAK_HOLD",
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

    hh = recent_high > previous_high
    hl = recent_low > previous_low

    lh = recent_high < previous_high
    ll = recent_low < previous_low

    return {
        "HH": hh,
        "HL": hl,
        "LH": lh,
        "LL": ll,
    }


def trend_1h(candles):

    if len(candles) < SMA_SLOW + 10:

        return {
            "side": None,
            "score": 0,
            "reason": "INSUFFICIENT_DATA"
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
            "score": 0,
            "reason": "NO_STRUCTURE"
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
            "score": score_long,
            "sma20": sma20,
            "sma50": sma50,
            "structure": structure
        }

    if score_short >= MIN_TREND_SCORE:

        return {
            "side": "SHORT",
            "score": score_short,
            "sma20": sma20,
            "sma50": sma50,
            "structure": structure
        }

    return {
        "side": None,
        "score": max(
            score_long,
            score_short
        ),
        "sma20": sma20,
        "sma50": sma50,
        "structure": structure
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

    for i in range(
        start,
        end
    ):

        c = candles[i]

        if side == "LONG":

            level = zone["high"]

            broken = (
                c["close"]
                > level
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
                < level
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
                c["close"]
                >= level
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
                c["close"]
                <= level
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
            "reason": "WAITING_NEXT_CANDLE"
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

        if not bullish and not higher_close:

            return {
                "status": "FAIL",
                "passed": False,
                "reason":
                    "CONFIRMATION_NOT_BULLISH_"
                    "AND_CLOSE_NOT_ABOVE_PULLBACK_HIGH"
            }

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
            "reason": "CONFIRMATION_PASSED"
        }

    lower_close = (
        confirmation["close"]
        < pullback_candle["low"]
    )

    if not bearish and not lower_close:

        return {
            "status": "FAIL",
            "passed": False,
            "reason":
                "CONFIRMATION_NOT_BEARISH_"
                "AND_CLOSE_NOT_BELOW_PULLBACK_LOW"
        }

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
        "reason": "CONFIRMATION_PASSED"
    }


def confirmation_after_pullback(
    candles,
    side,
    pullback
):

    result = evaluate_confirmation(
        candles,
        side,
        pullback
    )

    return result["passed"]


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
        current_atr = entry * 0.005

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
    """
    v7.3 multi-level TP selection.

    LONG:
        Nearest resistance
            -> RR sufficient?
            -> distance acceptable?
        If not, next resistance.

    SHORT:
        Nearest support
            -> RR sufficient?
            -> distance acceptable?
        If not, next support.

    IMPORTANT:
    Entry filters are NOT changed here.
    This function only determines the final TP.
    """

    risk = abs(
        entry - sl
    )

    if risk <= 0:

        return None, "INVALID_RISK"

    # ========================================================
    # LONG
    # ========================================================

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

            # ----------------------------------------------
            # RR not enough -> try next resistance
            # ----------------------------------------------

            if rr <= MIN_TP_RR:
                continue

            # ----------------------------------------------
            # TP too far -> all later resistances are farther
            # ----------------------------------------------

            if distance_pct > MAX_TP_DISTANCE_PCT:
                break

            return {
                "tp": tp,
                "rr": rr,
                "zone": zone,
                "reward": reward,
                "risk": risk,
                "distance_pct": distance_pct
            }, None

    # ========================================================
    # SHORT
    # ========================================================

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

            # ----------------------------------------------
            # RR not enough -> try next support
            # ----------------------------------------------

            if rr <= MIN_TP_RR:
                continue

            # ----------------------------------------------
            # TP too far -> all later supports are farther
            # ----------------------------------------------

            if distance_pct > MAX_TP_DISTANCE_PCT:
                break

            return {
                "tp": tp,
                "rr": rr,
                "zone": zone,
                "reward": reward,
                "risk": risk,
                "distance_pct": distance_pct
            }, None

    return None, "NO_VALID_TP"


# ============================================================
# COOLDOWN
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
        ORDER BY exit_time DESC
        LIMIT 1
        """,
        (symbol,)
    ).fetchone()

    conn.close()

    if not row or not row["exit_time"]:
        return False

    latest_exit = row["exit_time"]

    now = time.time()

    candle_seconds = 5 * 60

    return (
        now - latest_exit
        <
        COOLDOWN_CANDLES
        * candle_seconds
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
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN',
                NULL, NULL, ?, NULL, NULL,
                NULL, NULL, 0, ?)
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

def process_open_trade(
    trade,
    candles_5m
):

    entry_time = safe_float(
        trade["entry_time"]
    )

    relevant = [
        c
        for c in candles_5m
        if c["time"] >= entry_time
    ]

    if not relevant:
        return None

    side = trade["side"]

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    for candle in relevant:

        sl_hit = False
        tp_hit = False

        if side == "LONG":

            sl_hit = (
                candle["low"]
                <= sl
            )

            tp_hit = (
                candle["high"]
                >= tp
            )

        else:

            sl_hit = (
                candle["high"]
                >= sl
            )

            tp_hit = (
                candle["low"]
                <= tp
            )

        if not sl_hit and not tp_hit:
            continue

        if sl_hit and tp_hit:

            exit_price = sl
            result = "LOSS"

            reason = (
                "AMBIGUOUS_SAME_CANDLE_SL_TP"
            )

        elif sl_hit:

            exit_price = sl
            result = "LOSS"

            reason = (
                "HISTORICAL_5M_SL"
            )

        else:

            exit_price = tp
            result = "WIN"

            reason = (
                "HISTORICAL_5M_TP"
            )

        if side == "LONG":

            pnl_pct = (
                exit_price
                - trade["entry"]
            ) / trade["entry"] * 100

        else:

            pnl_pct = (
                trade["entry"]
                - exit_price
            ) / trade["entry"] * 100

        exit_time = candle["time"]

        duration = (
            exit_time
            - entry_time
        ) / 60

        conn = db()

        conn.execute(
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
            """,
            (
                result,
                pnl_pct,
                exit_time,
                exit_price,
                reason,
                duration,
                trade["id"]
            )
        )

        conn.commit()
        conn.close()

        return {
            "result": result,
            "pnl": pnl_pct,
            "reason": reason
        }

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
                THEN 1 ELSE 0
                END
            ) AS wins,
            SUM(
                CASE
                WHEN result='LOSS'
                THEN 1 ELSE 0
                END
            ) AS losses,
            COALESCE(SUM(pnl),0)
            AS net_pnl
        FROM trades
        WHERE status='CLOSED'
        """
    ).fetchone()

    conn.close()

    total = closed["total"] or 0
    wins = closed["wins"] or 0
    losses = closed["losses"] or 0
    net = closed["net_pnl"] or 0.0

    wr = (
        wins / total * 100
        if total
        else 0.0
    )

    return {
        "open": open_count,
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
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

    setup_zone = setup["zone"]

    breakout = find_recent_breakout(
        candles_5m,
        side,
        setup_zone
    )

    if not breakout:
        return None, "BREAKOUT_FAILED"

    pullback = detect_pullback(
        candles_5m,
        side,
        breakout
    )

    if not pullback:
        return None, "PULLBACK_FAILED"

    confirmation = evaluate_confirmation(
        candles_5m,
        side,
        pullback
    )

    if confirmation["status"] == "WAIT":
        return None, "WAITING_NEXT_CANDLE"

    if not confirmation["passed"]:
        return None, confirmation["reason"]

    rv = rvol(
        candles_5m,
        RVOL_PERIOD
    )

    if rv < RVOL_NORMAL:
        return None, "RVOL_FAILED"

    entry = candles_5m[-1]["close"]

    sl = calculate_sl(
        side,
        entry,
        candles_5m,
        setup_zone
    )

    if sl is None:
        return None, "SL_FAILED"

    tp_data, tp_reason = select_tp(
        side,
        entry,
        sl,
        support_zones,
        resistance_zones
    )

    if tp_data is None:
        return None, tp_reason

    trend_score = min(
        trend["score"],
        7
    )

    setup_score = setup["score"]

    trigger_score = 3

    volume_score = 0

    if rv >= RVOL_NORMAL:
        volume_score += 1

    if rv >= RVOL_STRONG:
        volume_score += 1

    score = (
        trend_score
        + min(setup_score, 4)
        + min(trigger_score, 3)
        + min(volume_score, 2)
    )

    if score < MIN_SCORE:
        return None, "SCORE_FAILED"

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
        "sr_zone": tp_data["zone"],
        "setup_zone": setup_zone,
        "support_zones": support_zones,
        "resistance_zones": resistance_zones,
        "entry_time": candles_5m[-1]["time"]
    }, None


# ============================================================
# TOP CANDIDATE
# ============================================================

def candidate_stage_rank(stage):

    ranks = {
        "TREND": 1,
        "SETUP": 2,
        "BREAKOUT": 3,
        "PULLBACK": 4,
        "CONFIRMATION": 5,
        "FINAL_FILTER": 6,
        "SIGNAL": 7,
    }

    return ranks.get(stage, 0)


def make_candidate(
    symbol,
    side,
    stage,
    reason,
    trend=None,
    setup=None,
    breakout=None,
    pullback=None,
    confirmation_status="NONE",
    confirmation_reason=None,
    rv=0.0,
    score=None,
    entry=None,
    sl=None,
    tp_data=None,
    support_zones=None,
    resistance_zones=None
):

    support_zones = support_zones or []
    resistance_zones = resistance_zones or []

    setup_zone = (
        setup["zone"]
        if setup
        else None
    )

    candidate = {
        "symbol": symbol,
        "side": side,
        "stage": stage,
        "reason": reason,

        "trend_score": (
            min(trend["score"], 7)
            if trend
            else 0
        ),

        "setup": (
            setup["setup"]
            if setup
            else None
        ),

        "setup_score": (
            setup["score"]
            if setup
            else 0
        ),

        "setup_zone": setup_zone,

        "breakout": bool(breakout),

        "breakout_level": (
            breakout["level"]
            if breakout
            else None
        ),

        "pullback": bool(pullback),

        "confirmation": (
            confirmation_status == "PASS"
        ),

        "confirmation_status":
            confirmation_status,

        "confirmation_reason":
            confirmation_reason,

        "rv": rv,

        "score": score,

        "entry": entry,
        "sl": sl,

        "tp": (
            tp_data["tp"]
            if tp_data
            else None
        ),

        "rr": (
            tp_data["rr"]
            if tp_data
            else None
        ),

        "tp_zone": (
            tp_data["zone"]
            if tp_data
            else None
        ),

        "support_zones": support_zones,
        "resistance_zones": resistance_zones,
    }

    return candidate


def candidate_current_score(candidate):

    score = candidate["trend_score"]

    score += min(
        candidate["setup_score"],
        4
    )

    if candidate["breakout"]:
        score += 1

    if candidate["pullback"]:
        score += 1

    if candidate["confirmation"]:
        score += 1

    if candidate["rv"] >= RVOL_NORMAL:
        score += 1

    if candidate["rv"] >= RVOL_STRONG:
        score += 1

    return score


def candidate_rank(candidate):

    stage_rank = candidate_stage_rank(
        candidate["stage"]
    )

    score = (
        candidate["score"]
        if candidate["score"] is not None
        else candidate_current_score(candidate)
    )

    return (
        stage_rank,
        score,
        candidate["trend_score"],
        candidate["rv"]
    )


def update_top_candidate(
    current,
    candidate
):

    if candidate is None:
        return current

    if current is None:
        return candidate

    if candidate_rank(candidate) > candidate_rank(current):
        return candidate

    return current


# ============================================================
# REPORT
# ============================================================
#
# IMPORTANT:
# Internal candidate diagnostics remain available to the
# scanner, but are NOT displayed in Telegram.
# ============================================================

def build_report(
    signals,
    closed_trades,
    diagnostics,
    top_candidate
):

    perf = get_performance()

    lines = []

    lines.append(
        f"📊 VOLUME-KHAT 100 {VERSION}"
    )

    lines.append(
        f"🕐 "
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

        lines.append(
            "None"
        )

    else:

        for index, s in enumerate(signals):

            sl_pct = pct_distance(
                s["entry"],
                s["sl"]
            )

            tp_pct = pct_distance(
                s["entry"],
                s["tp"]
            )

            lines.append(
                f"{'🟢' if s['side'] == 'LONG' else '🔴'} "
                f"{s['symbol']} {s['side']}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(s['entry'])}"
            )

            lines.append(
                f"SL: "
                f"{format_price(s['sl'])} "
                f"({sl_pct:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{format_price(s['tp'])} "
                f"({tp_pct:.2f}%)"
            )

            lines.append(
                f"RR: {s['rr']:.2f}"
            )

            lines.append(
                f"Score: {s['score']}/16"
            )

            if index < len(signals) - 1:

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

    open_trades = get_open_trades()

    if not open_trades:

        lines.append(
            "None"
        )

    else:

        for index, trade in enumerate(open_trades):

            current = get_current_price(
                trade["symbol"]
            )

            if current is None:
                current = trade["entry"]

            if trade["side"] == "LONG":

                pnl = (
                    current
                    - trade["entry"]
                ) / trade["entry"] * 100

            else:

                pnl = (
                    trade["entry"]
                    - current
                ) / trade["entry"] * 100

            emoji = (
                "🟢"
                if pnl >= 0
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
                f"{format_price(current)}"
            )

            lines.append(
                f"P&L: {pnl:+.2f}%"
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

            if index < len(open_trades) - 1:

                lines.append(
                    "━━━━━━━━━━━━━━━━━━"
                )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # PERFORMANCE
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

    # ========================================================
    # REAL TRADING STATUS
    # ========================================================

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

    chunks = []

    max_length = 3900

    for i in range(
        0,
        len(text),
        max_length
    ):

        chunks.append(
            text[
                i:i + max_length
            ]
        )

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
                    "❌ Telegram HTTP "
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

        "15M LONG Setup": 0,
        "15M SHORT Setup": 0,
        "15M Failed": 0,

        "5M Breakout Pass": 0,
        "5M Breakout Failed": 0,

        "5M Pullback Pass": 0,
        "5M Pullback Failed": 0,

        "5M Confirmation Pass": 0,
        "5M Confirmation Failed": 0,

        "Confirmation Waiting": 0,

        "RVOL Failed": 0,

        "Score Failed": 0,

        "No Valid S/R Target": 0,

        "No Valid TP": 0,

        "SL Failed": 0,

        "Cooldown": 0,
        "Already Open": 0,

        "Errors": 0
    }

    signals = []

    closed_trades = 0

    top_candidate = None

    # ========================================================
    # TICKERS / TOP 100
    # ========================================================

    symbols = get_top_symbols()

    diagnostics["Scanned"] = len(symbols)

    # ========================================================
    # HISTORICAL EXITS FIRST
    # ========================================================

    open_trades = get_open_trades()

    for trade in open_trades:

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
            closed_trades += 1

    # ========================================================
    # REFRESH OPEN TRADES
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
        # Existing open trade
        # ----------------------------------------------------

        if symbol in open_symbols:

            diagnostics["Already Open"] += 1

            continue

        # ----------------------------------------------------
        # Cooldown
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

            current_price = candles_5m[-1]["close"]

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
            # INITIAL CANDIDATE
            # =================================================

            candidate = make_candidate(
                symbol=symbol,
                side=side,
                stage="TREND",
                reason="SETUP_FAILED",
                trend=trend,
                rv=rv,
                support_zones=support_zones,
                resistance_zones=resistance_zones,
                entry=current_price
            )

            top_candidate = update_top_candidate(
                top_candidate,
                candidate
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

                candidate["stage"] = "TREND"
                candidate["reason"] = "SETUP_FAILED"

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            if side == "LONG":

                diagnostics[
                    "15M LONG Setup"
                ] += 1

            else:

                diagnostics[
                    "15M SHORT Setup"
                ] += 1

            # =================================================
            # CANDIDATE AFTER SETUP
            # =================================================

            candidate = make_candidate(
                symbol=symbol,
                side=side,
                stage="SETUP",
                reason="BREAKOUT_FAILED",
                trend=trend,
                setup=setup,
                rv=rv,
                support_zones=support_zones,
                resistance_zones=resistance_zones,
                entry=current_price
            )

            top_candidate = update_top_candidate(
                top_candidate,
                candidate
            )

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
                    "5M Breakout Failed"
                ] += 1

                candidate["stage"] = "SETUP"
                candidate["reason"] = "BREAKOUT_FAILED"

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            diagnostics[
                "5M Breakout Pass"
            ] += 1

            # =================================================
            # CANDIDATE AFTER BREAKOUT
            # =================================================

            candidate = make_candidate(
                symbol=symbol,
                side=side,
                stage="BREAKOUT",
                reason="PULLBACK_FAILED",
                trend=trend,
                setup=setup,
                breakout=breakout,
                rv=rv,
                support_zones=support_zones,
                resistance_zones=resistance_zones,
                entry=current_price
            )

            top_candidate = update_top_candidate(
                top_candidate,
                candidate
            )

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
                    "5M Pullback Failed"
                ] += 1

                candidate["stage"] = "BREAKOUT"
                candidate["reason"] = "PULLBACK_FAILED"

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            diagnostics[
                "5M Pullback Pass"
            ] += 1

            # =================================================
            # CONFIRMATION
            # =================================================

            confirmation = evaluate_confirmation(
                candles_5m,
                side,
                pullback
            )

            confirmation_status = (
                confirmation["status"]
            )

            confirmation_reason = (
                confirmation["reason"]
            )

            # -------------------------------------------------
            # WAITING
            # -------------------------------------------------

            if confirmation_status == "WAIT":

                diagnostics[
                    "Confirmation Waiting"
                ] += 1

                candidate = make_candidate(
                    symbol=symbol,
                    side=side,
                    stage="PULLBACK",
                    reason="WAITING_NEXT_CANDLE",
                    trend=trend,
                    setup=setup,
                    breakout=breakout,
                    pullback=pullback,
                    confirmation_status="WAIT",
                    confirmation_reason=confirmation_reason,
                    rv=rv,
                    support_zones=support_zones,
                    resistance_zones=resistance_zones,
                    entry=current_price
                )

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            # -------------------------------------------------
            # CONFIRMATION FAIL
            # -------------------------------------------------

            if not confirmation["passed"]:

                diagnostics[
                    "5M Confirmation Failed"
                ] += 1

                candidate = make_candidate(
                    symbol=symbol,
                    side=side,
                    stage="PULLBACK",
                    reason=confirmation_reason,
                    trend=trend,
                    setup=setup,
                    breakout=breakout,
                    pullback=pullback,
                    confirmation_status="FAIL",
                    confirmation_reason=confirmation_reason,
                    rv=rv,
                    support_zones=support_zones,
                    resistance_zones=resistance_zones,
                    entry=current_price
                )

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            # =================================================
            # CONFIRMATION PASS
            # =================================================

            diagnostics[
                "5M Confirmation Pass"
            ] += 1

            # =================================================
            # ENTRY
            # =================================================

            entry = candles_5m[-1]["close"]

            # =================================================
            # RVOL FILTER
            # =================================================

            if rv < RVOL_NORMAL:

                diagnostics[
                    "RVOL Failed"
                ] += 1

                candidate = make_candidate(
                    symbol=symbol,
                    side=side,
                    stage="CONFIRMATION",
                    reason="RVOL_FAILED",
                    trend=trend,
                    setup=setup,
                    breakout=breakout,
                    pullback=pullback,
                    confirmation_status="PASS",
                    confirmation_reason="CONFIRMATION_PASSED",
                    rv=rv,
                    support_zones=support_zones,
                    resistance_zones=resistance_zones,
                    entry=entry
                )

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

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

                candidate = make_candidate(
                    symbol=symbol,
                    side=side,
                    stage="FINAL_FILTER",
                    reason="SL_FAILED",
                    trend=trend,
                    setup=setup,
                    breakout=breakout,
                    pullback=pullback,
                    confirmation_status="PASS",
                    confirmation_reason="CONFIRMATION_PASSED",
                    rv=rv,
                    support_zones=support_zones,
                    resistance_zones=resistance_zones,
                    entry=entry
                )

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            # =================================================
            # TP v7.3
            # =================================================

            tp_data, tp_reason = select_tp(
                side,
                entry,
                sl,
                support_zones,
                resistance_zones
            )

            if tp_data is None:

                if tp_reason == "NO_VALID_SR_TARGET":

                    diagnostics[
                        "No Valid S/R Target"
                    ] += 1

                else:

                    diagnostics[
                        "No Valid TP"
                    ] += 1

                candidate = make_candidate(
                    symbol=symbol,
                    side=side,
                    stage="FINAL_FILTER",
                    reason=tp_reason,
                    trend=trend,
                    setup=setup,
                    breakout=breakout,
                    pullback=pullback,
                    confirmation_status="PASS",
                    confirmation_reason="CONFIRMATION_PASSED",
                    rv=rv,
                    support_zones=support_zones,
                    resistance_zones=resistance_zones,
                    entry=entry,
                    sl=sl
                )

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

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
                + min(volume_score, 2)
            )

            candidate = make_candidate(
                symbol=symbol,
                side=side,
                stage="FINAL_FILTER",
                reason="SCORE_FAILED",
                trend=trend,
                setup=setup,
                breakout=breakout,
                pullback=pullback,
                confirmation_status="PASS",
                confirmation_reason="CONFIRMATION_PASSED",
                rv=rv,
                score=score,
                entry=entry,
                sl=sl,
                tp_data=tp_data,
                support_zones=support_zones,
                resistance_zones=resistance_zones
            )

            # =================================================
            # SCORE FAIL
            # =================================================

            if score < MIN_SCORE:

                diagnostics[
                    "Score Failed"
                ] += 1

                top_candidate = update_top_candidate(
                    top_candidate,
                    candidate
                )

                continue

            # =================================================
            # VALID SIGNAL
            # =================================================

            candidate["stage"] = "SIGNAL"
            candidate["reason"] = "SIGNAL_CREATED"

            top_candidate = update_top_candidate(
                top_candidate,
                candidate
            )

            # =================================================
            # MAX NEW SIGNALS
            # =================================================

            if len(signals) >= MAX_NEW_SIGNALS:

                continue

            # =================================================
            # MAX OPEN TRADES
            # =================================================

            if current_open_count >= MAX_OPEN_TRADES:

                continue

            # =================================================
            # BUILD SIGNAL
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
                "trend_score": trend_score,
                "sr_zone": tp_data["zone"],
                "setup_zone": setup["zone"],
                "support_zones": support_zones,
                "resistance_zones": resistance_zones,
                "entry_time": candles_5m[-1]["time"]
            }

            save_trade(signal)

            signals.append(signal)

            open_symbols.add(symbol)

            current_open_count += 1

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
        signals,
        closed_trades,
        diagnostics,
        top_candidate
    )

    print(report)

    # ========================================================
    # TELEGRAM
    # ========================================================

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
