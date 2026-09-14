# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.4
# ============================================================
#
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE ZONE + PRICE REACTION
# 5M  = FRESH BREAKOUT + PULLBACK + CONFIRMATION
# VOL = RVOL CONFIRMATION
#
# v7.4 CHANGES
# ------------------------------------------------------------
# - More practical 5M sequence detection
# - Breakout can be up to 4 closed candles old
# - Pullback can occur after breakout
# - Confirmation can occur within next 3 candles
# - Latest CLOSED candle must be confirmation
# - RVOL >= 1.70
# - TP = nearest valid S/R
# - RR > 1.00
# - SL = 0.50% to 1.50%
# - MAX TP distance = 3.00%
# - MAX OPEN = 3
# - CLOSED CANDLES ONLY
# - OPEN trades are preserved
# - SQLite persistence handled by GitHub Actions
# - Real trading DISABLED
#
# TELEGRAM:
#   NEW SIGNALS
#   OPEN TRADES
#   STATS
#
# Direction emoji:
#   LONG  = 🟢
#   SHORT = 🔴
# ============================================================

import os
import time
import sqlite3
from datetime import datetime, timezone

import numpy as np
import requests


# ============================================================
# VERSION
# ============================================================

VERSION = "v7.4"


# ============================================================
# KRAKEN
# ============================================================

KRAKEN_BASE = "https://futures.kraken.com"

TICKERS_URL = (
    f"{KRAKEN_BASE}/derivatives/api/v3/tickers"
)

CHART_URL = (
    f"{KRAKEN_BASE}/api/charts/v1/trade"
)


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "volume_khat_100_v74.db"


# ============================================================
# MODE
# ============================================================

PAPER_TRADING = True


# ============================================================
# UNIVERSE
# ============================================================

TOP_N = 100

MAX_OPEN_TRADES = 3

MAX_NEW_SIGNALS = 3


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
# TREND
# ============================================================

SMA_FAST = 20
SMA_SLOW = 50

MIN_TREND_SCORE = 6


# ============================================================
# S/R
# ============================================================

PIVOT_SWING = 3

ZONE_TOLERANCE_PCT = 0.30

TP_ZONE_BUFFER_PCT = 0.10


# ============================================================
# RVOL
# ============================================================

RVOL_PERIOD = 20

RVOL_NORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00


# ============================================================
# 5M BREAKOUT
# ============================================================

BREAKOUT_MAX_AGE = 4

BREAKOUT_BUFFER_PCT = 0.05


# ============================================================
# 5M PULLBACK
# ============================================================

PULLBACK_TOLERANCE_PCT = 0.35


# ============================================================
# 5M CONFIRMATION
# ============================================================

MAX_CONFIRMATION_BARS = 3

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
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "VOLUME-KHAT-100/7.4",
        "Accept": "application/json",
    }
)

TICKER_CACHE = {}


# ============================================================
# TIME
# ============================================================

def now_utc():

    return datetime.now(timezone.utc)


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(value, default=0.0):

    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# TIMESTAMP
# ============================================================

def normalize_ts(value):

    value = safe_float(value)

    if value > 10_000_000_000:
        value /= 1000.0

    return value


# ============================================================
# PRICE FORMAT
# ============================================================

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
# PERCENT DISTANCE
# ============================================================

def pct_distance(a, b):

    a = safe_float(a)
    b = safe_float(b)

    if a <= 0:
        return 0.0

    return abs(b - a) / a * 100.0


# ============================================================
# DATABASE CONNECTION
# ============================================================

def db():

    conn = sqlite3.connect(DB_FILE)

    conn.row_factory = sqlite3.Row

    return conn


# ============================================================
# INIT DATABASE
# ============================================================

def init_db():

    conn = db()

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            side TEXT NOT NULL,

            setup TEXT,

            entry REAL NOT NULL,

            sl REAL NOT NULL,

            tp REAL NOT NULL,

            rr REAL,

            status TEXT NOT NULL,

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

    conn.close()


# ============================================================
# HTTP GET
# ============================================================

def get_json(
    url,
    params=None,
    timeout=20
):

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

        data = get_json(
            TICKERS_URL
        )

        tickers = data.get(
            "tickers",
            []
        )

        result = {}

        for ticker in tickers:

            symbol = ticker.get(
                "symbol"
            )

            if symbol:
                result[symbol] = ticker

        TICKER_CACHE = result

        return result

    except Exception as e:

        print(
            f"❌ Ticker error: {e}"
        )

        return TICKER_CACHE


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    ticker = TICKER_CACHE.get(
        symbol
    )

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
        x[0]
        for x in candidates[:TOP_N]
    ]


# ============================================================
# PARSE CANDLES
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
                    "volume": v
                }
            )

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit
):

    try:

        url = (
            f"{CHART_URL}/"
            f"{symbol}/"
            f"{resolution}"
        )

        data = get_json(url)

        candles = parse_candles(
            data
        )

        if len(candles) < 10:
            return []

        # Last API candle is treated as open.
        # Therefore remove it.
        candles = candles[:-1]

        return candles[-limit:]

    except Exception as e:

        print(
            f"❌ Candle error "
            f"{symbol} {resolution}: {e}"
        )

        return []


# ============================================================
# SMA
# ============================================================

def sma(values, period):

    if len(values) < period:
        return None

    return float(
        np.mean(
            values[-period:]
        )
    )


# ============================================================
# ATR
# ============================================================

def atr(
    candles,
    period=ATR_PERIOD
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        current = candles[i]

        previous = candles[i - 1]

        tr = max(
            current["high"]
            - current["low"],

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
        np.mean(
            trs[-period:]
        )
    )


# ============================================================
# RVOL
# ============================================================

def rvol(
    candles,
    period=RVOL_PERIOD
):

    if len(candles) < period + 1:
        return 0.0

    current_volume = (
        candles[-1]["volume"]
    )

    previous = [
        c["volume"]
        for c in candles[
            -period - 1:-1
        ]
    ]

    average = np.mean(
        previous
    )

    if average <= 0:
        return 0.0

    return (
        current_volume
        / average
    )


# ============================================================
# PIVOT HIGHS
# ============================================================

def pivot_highs(
    candles,
    swing=PIVOT_SWING
):

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

        if (
            high >= max(left)
            and high >= max(right)
        ):

            levels.append(
                {
                    "price": high,
                    "time":
                        candles[i]["time"]
                }
            )

    return levels


# ============================================================
# PIVOT LOWS
# ============================================================

def pivot_lows(
    candles,
    swing=PIVOT_SWING
):

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

        if (
            low <= min(left)
            and low <= min(right)
        ):

            levels.append(
                {
                    "price": low,
                    "time":
                        candles[i]["time"]
                }
            )

    return levels


# ============================================================
# BUILD ZONE
# ============================================================

def build_zone(
    levels,
    zone_type
):

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
        "type": zone_type,
        "last_time": max(
            x["time"]
            for x in levels
        )
    }


# ============================================================
# CLUSTER LEVELS
# ============================================================

def cluster_levels(
    levels,
    zone_type
):

    if not levels:
        return []

    levels = sorted(
        levels,
        key=lambda x: x["price"]
    )

    zones = []

    current = [
        levels[0]
    ]

    for level in levels[1:]:

        center = np.mean(
            [
                x["price"]
                for x in current
            ]
        )

        distance = (
            abs(
                level["price"]
                - center
            )
            / center
            * 100
        )

        if (
            distance
            <= ZONE_TOLERANCE_PCT
        ):

            current.append(level)

        else:

            zones.append(
                build_zone(
                    current,
                    zone_type
                )
            )

            current = [
                level
            ]

    zones.append(
        build_zone(
            current,
            zone_type
        )
    )

    return zones


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def build_sr_zones(
    candles_15m,
    candles_1h
):

    resistance_levels = (
        pivot_highs(candles_15m)
        + pivot_highs(candles_1h)
    )

    support_levels = (
        pivot_lows(candles_15m)
        + pivot_lows(candles_1h)
    )

    support_zones = cluster_levels(
        support_levels,
        "SUPPORT"
    )

    resistance_zones = cluster_levels(
        resistance_levels,
        "RESISTANCE"
    )

    return (
        support_zones,
        resistance_zones
    )


# ============================================================
# 1H STRUCTURE
# ============================================================

def get_structure(candles):

    if len(candles) < 10:
        return None

    recent = candles[-1]

    previous = candles[-6:-1]

    previous_high = max(
        c["high"]
        for c in previous
    )

    previous_low = min(
        c["low"]
        for c in previous
    )

    return {
        "HH":
            recent["high"]
            > previous_high,

        "HL":
            recent["low"]
            > previous_low,

        "LH":
            recent["high"]
            < previous_high,

        "LL":
            recent["low"]
            < previous_low
    }


# ============================================================
# 1H TREND
# ============================================================

def trend_1h(candles):

    if len(candles) < (
        SMA_SLOW + 10
    ):

        return {
            "side": None,
            "score": 0
        }

    closes = [
        c["close"]
        for c in candles
    ]

    price = closes[-1]

    fast = sma(
        closes,
        SMA_FAST
    )

    slow = sma(
        closes,
        SMA_SLOW
    )

    old_fast = sma(
        closes[:-5],
        SMA_FAST
    )

    structure = get_structure(
        candles
    )

    if (
        fast is None
        or slow is None
        or structure is None
    ):

        return {
            "side": None,
            "score": 0
        }

    long_score = 0
    short_score = 0

    # Structure
    if structure["HH"]:
        long_score += 2

    if structure["HL"]:
        long_score += 1

    if structure["LH"]:
        short_score += 2

    if structure["LL"]:
        short_score += 1

    # Moving average alignment
    if fast > slow:
        long_score += 2

    elif fast < slow:
        short_score += 2

    # Price location
    if price > fast:
        long_score += 1

    elif price < fast:
        short_score += 1

    # MA slope
    if old_fast is not None:

        if fast > old_fast:
            long_score += 1

        elif fast < old_fast:
            short_score += 1

    if (
        long_score >= MIN_TREND_SCORE
        and long_score > short_score
    ):

        return {
            "side": "LONG",
            "score": long_score
        }

    if (
        short_score >= MIN_TREND_SCORE
        and short_score > long_score
    ):

        return {
            "side": "SHORT",
            "score": short_score
        }

    return {
        "side": None,
        "score": max(
            long_score,
            short_score
        )
    }


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(candle):

    return abs(
        candle["close"]
        - candle["open"]
    )


def candle_range(candle):

    return (
        candle["high"]
        - candle["low"]
    )


def upper_wick(candle):

    return (
        candle["high"]
        - max(
            candle["open"],
            candle["close"]
        )
    )


def lower_wick(candle):

    return (
        min(
            candle["open"],
            candle["close"]
        )
        - candle["low"]
    )


# ============================================================
# 15M SUPPORT REACTION
# ============================================================

def support_reaction(
    candles,
    zone
):

    recent = candles[-3:]

    touched = False

    bullish_rejection = False

    for candle in recent:

        if (
            candle["low"]
            <= zone["high"]
            * (
                1
                + ZONE_TOLERANCE_PCT
                / 100
            )
        ):

            touched = True

        body = candle_body(
            candle
        )

        if body <= 0:
            continue

        lower = lower_wick(
            candle
        )

        bullish = (
            candle["close"]
            > candle["open"]
        )

        if (
            bullish
            and lower / body
            >= WICK_BODY_RATIO
        ):

            bullish_rejection = True

    return (
        touched
        and bullish_rejection
    )


# ============================================================
# 15M RESISTANCE REACTION
# ============================================================

def resistance_reaction(
    candles,
    zone
):

    recent = candles[-3:]

    touched = False

    bearish_rejection = False

    for candle in recent:

        if (
            candle["high"]
            >= zone["low"]
            * (
                1
                - ZONE_TOLERANCE_PCT
                / 100
            )
        ):

            touched = True

        body = candle_body(
            candle
        )

        if body <= 0:
            continue

        upper = upper_wick(
            candle
        )

        bearish = (
            candle["close"]
            < candle["open"]
        )

        if (
            bearish
            and upper / body
            >= WICK_BODY_RATIO
        ):

            bearish_rejection = True

    return (
        touched
        and bearish_rejection
    )


# ============================================================
# 15M BREAK
# ============================================================

def resistance_break(
    candles,
    zone
):

    previous = candles[-2]
    current = candles[-1]

    return (
        previous["close"]
        <= zone["high"]
        and
        current["close"]
        >
        zone["high"]
        * (
            1
            + BREAKOUT_BUFFER_PCT
            / 100
        )
    )


def support_break(
    candles,
    zone
):

    previous = candles[-2]
    current = candles[-1]

    return (
        previous["close"]
        >= zone["low"]
        and
        current["close"]
        <
        zone["low"]
        * (
            1
            - BREAKOUT_BUFFER_PCT
            / 100
        )
    )


# ============================================================
# 15M SETUP
# ============================================================

def detect_15m_setup(
    side,
    candles,
    support_zones,
    resistance_zones
):

    if side == "LONG":

        # First preference:
        # support reaction

        for zone in sorted(
            support_zones,
            key=lambda z:
                abs(
                    z["center"]
                    - candles[-1]["close"]
                )
        ):

            if support_reaction(
                candles,
                zone
            ):

                return {
                    "setup":
                        "SUPPORT_REACTION",
                    "zone": zone,
                    "score": 4
                }

        # Second:
        # resistance breakout/hold

        for zone in sorted(
            resistance_zones,
            key=lambda z:
                abs(
                    z["center"]
                    - candles[-1]["close"]
                )
        ):

            if resistance_break(
                candles,
                zone
            ):

                return {
                    "setup":
                        "RESISTANCE_BREAK",
                    "zone": zone,
                    "score": 4
                }

    else:

        # Resistance reaction

        for zone in sorted(
            resistance_zones,
            key=lambda z:
                abs(
                    z["center"]
                    - candles[-1]["close"]
                )
        ):

            if resistance_reaction(
                candles,
                zone
            ):

                return {
                    "setup":
                        "RESISTANCE_REACTION",
                    "zone": zone,
                    "score": 4
                }

        # Support breakdown

        for zone in sorted(
            support_zones,
            key=lambda z:
                abs(
                    z["center"]
                    - candles[-1]["close"]
                )
        ):

            if support_break(
                candles,
                zone
            ):

                return {
                    "setup":
                        "SUPPORT_BREAK",
                    "zone": zone,
                    "score": 4
                }

    return None


# ============================================================
# 5M BREAKOUT
# ============================================================
#
# Find a fresh breakout among the last few CLOSED candles.
#
# The breakout does NOT need to be the latest candle.
# ============================================================

def find_recent_breakout(
    candles,
    side,
    zone
):

    if len(candles) < 10:
        return None

    latest_index = (
        len(candles) - 1
    )

    first_index = max(
        2,
        latest_index
        - BREAKOUT_MAX_AGE
    )

    candidates = []

    for i in range(
        first_index,
        latest_index
    ):

        candle = candles[i]

        if side == "LONG":

            level = zone["high"]

            broken = (
                candle["close"]
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
                candle["close"]
                <
                level
                * (
                    1
                    - BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

        if broken:

            candidates.append(
                {
                    "index": i,
                    "level": level,
                    "candle": candle
                }
            )

    if not candidates:
        return None

    # Most recent breakout wins.
    return candidates[-1]


# ============================================================
# 5M PULLBACK
# ============================================================

def find_pullback(
    candles,
    side,
    breakout
):

    breakout_index = (
        breakout["index"]
    )

    level = breakout["level"]

    latest_index = (
        len(candles) - 1
    )

    if (
        breakout_index
        >= latest_index
    ):

        return None

    max_pullback_index = min(
        latest_index,
        breakout_index
        + MAX_CONFIRMATION_BARS
    )

    for i in range(
        breakout_index + 1,
        max_pullback_index + 1
    ):

        candle = candles[i]

        if side == "LONG":

            touched = (
                candle["low"]
                <= level
                * (
                    1
                    + PULLBACK_TOLERANCE_PCT
                    / 100
                )
            )

            held = (
                candle["close"]
                >= level
                * (
                    1
                    - PULLBACK_TOLERANCE_PCT
                    / 100
                )
            )

        else:

            touched = (
                candle["high"]
                >= level
                * (
                    1
                    - PULLBACK_TOLERANCE_PCT
                    / 100
                )
            )

            held = (
                candle["close"]
                <= level
                * (
                    1
                    + PULLBACK_TOLERANCE_PCT
                    / 100
                )
            )

        if touched and held:

            return {
                "index": i,
                "level": level,
                "candle": candle
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================
#
# IMPORTANT v7.4:
#
# Confirmation is checked AFTER pullback.
#
# The latest CLOSED candle must be confirmation.
#
# Therefore:
#
# breakout
#     ↓
# pullback
#     ↓
# one or more candles
#     ↓
# latest closed candle = confirmation
#
# This avoids requiring breakout/pullback/confirmation to
# happen in one absurdly tiny sequence.
# ============================================================

def confirmation_signal(
    candles,
    side,
    pullback
):

    pullback_index = (
        pullback["index"]
    )

    latest_index = (
        len(candles) - 1
    )

    bars_after_pullback = (
        latest_index
        - pullback_index
    )

    if bars_after_pullback < 1:

        return {
            "passed": False,
            "reason":
                "WAITING_CONFIRMATION"
        }

    if (
        bars_after_pullback
        > MAX_CONFIRMATION_BARS
    ):

        return {
            "passed": False,
            "reason":
                "CONFIRMATION_TOO_OLD"
        }

    confirmation = candles[
        latest_index
    ]

    previous = candles[
        latest_index - 1
    ]

    body = candle_body(
        confirmation
    )

    candle_range_value = candle_range(
        confirmation
    )

    if (
        candle_range_value <= 0
        or body <= 0
    ):

        return {
            "passed": False,
            "reason":
                "WEAK_CONFIRMATION_CANDLE"
        }

    body_pct = (
        body
        / confirmation["open"]
        * 100
    )

    if body_pct < MIN_BODY_PCT:

        return {
            "passed": False,
            "reason":
                "CONFIRMATION_BODY_TOO_SMALL"
        }

    if side == "LONG":

        bullish = (
            confirmation["close"]
            > confirmation["open"]
        )

        higher_close = (
            confirmation["close"]
            > previous["high"]
        )

        above_pullback = (
            confirmation["close"]
            > pullback["level"]
        )

        if not bullish:

            return {
                "passed": False,
                "reason":
                    "CONFIRMATION_NOT_BULLISH"
            }

        if not above_pullback:

            return {
                "passed": False,
                "reason":
                    "CONFIRMATION_BELOW_BREAKOUT_LEVEL"
            }

        if not higher_close:

            return {
                "passed": False,
                "reason":
                    "CONFIRMATION_NO_MOMENTUM"
            }

    else:

        bearish = (
            confirmation["close"]
            < confirmation["open"]
        )

        lower_close = (
            confirmation["close"]
            < previous["low"]
        )

        below_pullback = (
            confirmation["close"]
            < pullback["level"]
        )

        if not bearish:

            return {
                "passed": False,
                "reason":
                    "CONFIRMATION_NOT_BEARISH"
            }

        if not below_pullback:

            return {
                "passed": False,
                "reason":
                    "CONFIRMATION_ABOVE_BREAKOUT_LEVEL"
            }

        if not lower_close:

            return {
                "passed": False,
                "reason":
                    "CONFIRMATION_NO_MOMENTUM"
            }

    return {
        "passed": True,
        "reason":
            "CONFIRMATION_PASSED"
    }


# ============================================================
# 5M ENTRY STRUCTURE
# ============================================================

def find_5m_structure(
    candles,
    side,
    setup_zone
):

    breakout = find_recent_breakout(
        candles,
        side,
        setup_zone
    )

    if not breakout:

        return None, "NO_FRESH_BREAKOUT"

    pullback = find_pullback(
        candles,
        side,
        breakout
    )

    if not pullback:

        return None, "NO_PULLBACK"

    confirmation = confirmation_signal(
        candles,
        side,
        pullback
    )

    if not confirmation["passed"]:

        return None, confirmation["reason"]

    return {
        "breakout": breakout,
        "pullback": pullback,
        "confirmation": confirmation
    }, None


# ============================================================
# SL
# ============================================================

def calculate_sl(
    side,
    entry,
    candles,
    setup_zone
):

    current_atr = atr(
        candles,
        ATR_PERIOD
    )

    if current_atr is None:

        current_atr = (
            entry * 0.005
        )

    buffer = (
        current_atr
        * ATR_SL_MULTIPLIER
    )

    if side == "LONG":

        structural_sl = (
            setup_zone["low"]
            - buffer
        )

        minimum_sl = (
            entry
            * (
                1
                - MIN_SL_PCT / 100
            )
        )

        maximum_sl = (
            entry
            * (
                1
                - MAX_SL_PCT / 100
            )
        )

        sl = structural_sl

        if sl > minimum_sl:
            sl = minimum_sl

        if sl < maximum_sl:
            sl = maximum_sl

    else:

        structural_sl = (
            setup_zone["high"]
            + buffer
        )

        minimum_sl = (
            entry
            * (
                1
                + MIN_SL_PCT / 100
            )
        )

        maximum_sl = (
            entry
            * (
                1
                + MAX_SL_PCT / 100
            )
        )

        sl = structural_sl

        if sl < minimum_sl:
            sl = minimum_sl

        if sl > maximum_sl:
            sl = maximum_sl

    distance = pct_distance(
        entry,
        sl
    )

    if (
        distance < MIN_SL_PCT
        or distance > MAX_SL_PCT
    ):

        return None

    return sl


# ============================================================
# TP
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
        return None

    if side == "LONG":

        candidates = [
            zone
            for zone in resistance_zones
            if zone["center"] > entry
        ]

        candidates.sort(
            key=lambda z:
                z["center"]
        )

        for zone in candidates:

            tp = (
                zone["low"]
                * (
                    1
                    - TP_ZONE_BUFFER_PCT
                    / 100
                )
            )

            reward = (
                tp - entry
            )

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

            if (
                distance_pct
                > MAX_TP_DISTANCE_PCT
            ):
                break

            return {
                "tp": tp,
                "rr": rr,
                "distance_pct":
                    distance_pct,
                "zone": zone
            }

    else:

        candidates = [
            zone
            for zone in support_zones
            if zone["center"] < entry
        ]

        candidates.sort(
            key=lambda z:
                z["center"],
            reverse=True
        )

        for zone in candidates:

            tp = (
                zone["high"]
                * (
                    1
                    + TP_ZONE_BUFFER_PCT
                    / 100
                )
            )

            reward = (
                entry - tp
            )

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

            if (
                distance_pct
                > MAX_TP_DISTANCE_PCT
            ):
                break

            return {
                "tp": tp,
                "rr": rr,
                "distance_pct":
                    distance_pct,
                "zone": zone
            }

    return None


# ============================================================
# OPEN TRADE
# ============================================================

def get_open_trades():

    conn = db()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


# ============================================================
# HAS OPEN TRADE
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


# ============================================================
# COOLDOWN
# ============================================================

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

    exit_time = safe_float(
        row["exit_time"]
    )

    if exit_time <= 0:
        return False

    cooldown_seconds = (
        3 * 5 * 60
    )

    return (
        time.time()
        - exit_time
        < cooldown_seconds
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
            entry_time,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?,
            'OPEN',
            ?, ?
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
# PROCESS OPEN TRADE
# ============================================================

def process_open_trade(
    trade,
    candles
):

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    entry_time = safe_float(
        trade["entry_time"]
    )

    side = trade["side"]

    if (
        entry <= 0
        or sl <= 0
        or tp <= 0
    ):

        return None

    # IMPORTANT:
    # Trade enters at the CLOSE of signal candle.
    # Therefore the signal candle itself must NOT be used
    # to trigger SL/TP.
    relevant = [
        candle
        for candle in candles
        if candle["time"] > entry_time
    ]

    for candle in relevant:

        high = candle["high"]

        low = candle["low"]

        if side == "LONG":

            sl_hit = (
                low <= sl
            )

            tp_hit = (
                high >= tp
            )

        else:

            sl_hit = (
                high >= sl
            )

            tp_hit = (
                low <= tp
            )

        if not sl_hit and not tp_hit:

            continue

        # If both are touched inside the same 5M candle,
        # assume SL first. Conservative handling.
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

            pnl = (
                exit_price - entry
            ) / entry * 100

        else:

            pnl = (
                entry - exit_price
            ) / entry * 100

        exit_time = (
            candle["time"]
        )

        duration = (
            exit_time
            - entry_time
        ) / 60

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
                pnl,
                exit_time,
                exit_price,
                reason,
                duration,
                trade["id"]
            )
        )

        conn.commit()

        updated = cursor.rowcount

        conn.close()

        if updated == 1:

            print(
                f"✅ CLOSED "
                f"{trade['symbol']} "
                f"{side} "
                f"{result} "
                f"{pnl:+.2f}% "
                f"{reason}"
            )

            return {
                "result": result,
                "pnl": pnl,
                "reason": reason
            }

        return None

    # ========================================================
    # VERY IMPORTANT:
    #
    # Neither SL nor TP was hit.
    #
    # The trade remains OPEN.
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

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,

            COALESCE(
                SUM(
                    CASE
                        WHEN result='WIN'
                        THEN 1
                        ELSE 0
                    END
                ),
                0
            ) AS wins,

            COALESCE(
                SUM(
                    CASE
                        WHEN result='LOSS'
                        THEN 1
                        ELSE 0
                    END
                ),
                0
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

    total = row["total"]

    wins = row["wins"]

    losses = row["losses"]

    net_pnl = row["net_pnl"]

    win_rate = (
        wins / total * 100
        if total > 0
        else 0
    )

    return {
        "open": open_count,
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net_pnl
    }


# ============================================================
# BUILD SIGNAL
# ============================================================

def evaluate_symbol(
    symbol,
    diagnostics
):

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

        diagnostics["ERROR"] += 1

        return None

    # ========================================================
    # 1H TREND
    # ========================================================

    trend = trend_1h(
        candles_1h
    )

    side = trend["side"]

    if side is None:

        diagnostics["1H NEUTRAL"] += 1

        return None

    diagnostics[
        f"1H {side}"
    ] += 1

    # ========================================================
    # S/R
    # ========================================================

    (
        support_zones,
        resistance_zones
    ) = build_sr_zones(
        candles_15m,
        candles_1h
    )

    # ========================================================
    # 15M SETUP
    # ========================================================

    setup = detect_15m_setup(
        side,
        candles_15m,
        support_zones,
        resistance_zones
    )

    if not setup:

        diagnostics["15M SETUP FAIL"] += 1

        return None

    diagnostics["15M SETUP PASS"] += 1

    # ========================================================
    # 5M STRUCTURE
    # ========================================================

    structure, reason = find_5m_structure(
        candles_5m,
        side,
        setup["zone"]
    )

    if structure is None:

        diagnostics[
            f"5M {reason}"
        ] += 1

        return None

    diagnostics["5M CONFIRM PASS"] += 1

    # ========================================================
    # RVOL
    # ========================================================

    current_rvol = rvol(
        candles_5m,
        RVOL_PERIOD
    )

    if current_rvol < RVOL_NORMAL:

        diagnostics["RVOL FAIL"] += 1

        return None

    diagnostics["RVOL PASS"] += 1

    # ========================================================
    # ENTRY
    # ========================================================

    confirmation_candle = (
        candles_5m[-1]
    )

    entry = confirmation_candle[
        "close"
    ]

    # ========================================================
    # SL
    # ========================================================

    sl = calculate_sl(
        side,
        entry,
        candles_5m,
        setup["zone"]
    )

    if sl is None:

        diagnostics["SL FAIL"] += 1

        return None

    diagnostics["SL PASS"] += 1

    # ========================================================
    # TP
    # ========================================================

    tp_data = select_tp(
        side,
        entry,
        sl,
        support_zones,
        resistance_zones
    )

    if tp_data is None:

        diagnostics["TP FAIL"] += 1

        return None

    diagnostics["TP PASS"] += 1

    # ========================================================
    # SCORE
    # ========================================================

    trend_score = min(
        trend["score"],
        7
    )

    setup_score = min(
        setup["score"],
        4
    )

    confirmation_score = 3

    volume_score = 0

    if current_rvol >= RVOL_NORMAL:
        volume_score += 1

    if current_rvol >= RVOL_STRONG:
        volume_score += 1

    score = (
        trend_score
        + setup_score
        + confirmation_score
        + volume_score
    )

    if score < MIN_SCORE:

        diagnostics["SCORE FAIL"] += 1

        return None

    diagnostics["SCORE PASS"] += 1

    # ========================================================
    # SIGNAL
    # ========================================================

    return {
        "symbol": symbol,
        "side": side,
        "setup": setup["setup"],
        "entry": entry,
        "sl": sl,
        "tp": tp_data["tp"],
        "rr": tp_data["rr"],
        "rvol": current_rvol,
        "score": score,
        "entry_time":
            confirmation_candle["time"]
    }


# ============================================================
# REPORT
# ============================================================

def build_report(
    signals
):

    performance = get_performance()

    open_trades = get_open_trades()

    lines = []

    lines.append(
        f"📊 VOLUME-KHAT 100 {VERSION}"
    )

    lines.append(
        "🕐 "
        + now_utc().strftime(
            "%Y/%m/%d %H:%M:%S UTC"
        )
    )

    lines.append(
        "⚡ Kraken Futures | "
        "5M CLOSED | TOP 100"
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

        for index, signal in enumerate(
            signals
        ):

            emoji = (
                "🟢"
                if signal["side"] == "LONG"
                else "🔴"
            )

            sl_pct = pct_distance(
                signal["entry"],
                signal["sl"]
            )

            tp_pct = pct_distance(
                signal["entry"],
                signal["tp"]
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
                f"RR: "
                f"{signal['rr']:.2f}"
            )

            lines.append(
                f"Score: "
                f"{signal['score']}/16"
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
        f"({performance['open']}/"
        f"{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append("None")

    else:

        for index, trade in enumerate(
            open_trades
        ):

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
                ) / trade["entry"] * 100

                emoji = "🟢"

            else:

                pnl = (
                    trade["entry"]
                    - current
                ) / trade["entry"] * 100

                emoji = "🔴"

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
                f"P&L: "
                f"{pnl:+.2f}%"
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
    # STATS
    # ========================================================

    lines.append(
        f"📈 STATS {VERSION}"
    )

    lines.append(
        f"Open: "
        f"{performance['open']}"
    )

    lines.append(
        f"Closed: "
        f"{performance['closed']}"
    )

    lines.append(
        f"Wins: "
        f"{performance['wins']}"
    )

    lines.append(
        f"Losses: "
        f"{performance['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{performance['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: "
        f"{performance['net_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "Real trading: DISABLED"
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
            "❌ TELEGRAM_BOT_TOKEN missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "❌ TELEGRAM_CHAT_ID missing"
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
                    "chat_id":
                        TELEGRAM_CHAT_ID,
                    "text": chunk
                },
                timeout=20
            )

            if response.status_code != 200:

                print(
                    "❌ Telegram error "
                    f"{response.status_code}"
                )

                print(
                    response.text[:500]
                )

                success = False

            else:

                print(
                    "📨 Telegram sent"
                )

        except Exception as e:

            print(
                f"❌ Telegram exception: {e}"
            )

            success = False

    return success


# ============================================================
# DIAGNOSTICS
# ============================================================

def print_diagnostics(
    diagnostics,
    scanned
):

    print("")
    print(
        "=============================="
    )

    print(
        f"VOLUME-KHAT {VERSION} DIAGNOSTICS"
    )

    print(
        "=============================="
    )

    print(
        f"Scanned: {scanned}"
    )

    ordered = [
        "1H LONG",
        "1H SHORT",
        "1H NEUTRAL",
        "15M SETUP PASS",
        "15M SETUP FAIL",
        "5M CONFIRM PASS",
        "5M NO_FRESH_BREAKOUT",
        "5M NO_PULLBACK",
        "5M WAITING_CONFIRMATION",
        "5M CONFIRMATION_TOO_OLD",
        "5M CONFIRMATION_NOT_BULLISH",
        "5M CONFIRMATION_NOT_BEARISH",
        "5M CONFIRMATION_BELOW_BREAKOUT_LEVEL",
        "5M CONFIRMATION_ABOVE_BREAKOUT_LEVEL",
        "5M CONFIRMATION_NO_MOMENTUM",
        "5M WEAK_CONFIRMATION_CANDLE",
        "RVOL PASS",
        "RVOL FAIL",
        "SL PASS",
        "SL FAIL",
        "TP PASS",
        "TP FAIL",
        "SCORE PASS",
        "SCORE FAIL",
        "ERROR"
    ]

    for key in ordered:

        value = diagnostics.get(
            key,
            0
        )

        if value:

            print(
                f"{key}: {value}"
            )

    print(
        "=============================="
    )


# ============================================================
# SCAN
# ============================================================

def scan():

    init_db()

    symbols = get_top_symbols()

    diagnostics = {}

    def inc(key):

        diagnostics[key] = (
            diagnostics.get(key, 0)
            + 1
        )

    # ========================================================
    # EXISTING OPEN TRADES
    # ========================================================

    open_before = get_open_trades()

    print(
        f"Existing OPEN trades: "
        f"{len(open_before)}"
    )

    for trade in open_before:

        candles = get_candles(
            trade["symbol"],
            TF_5M,
            LOOKBACK_5M
        )

        if not candles:
            continue

        process_open_trade(
            trade,
            candles
        )

    # ========================================================
    # REFRESH OPEN TRADES
    # ========================================================

    open_trades = get_open_trades()

    open_symbols = {
        trade["symbol"]
        for trade in open_trades
    }

    open_count = len(
        open_trades
    )

    # ========================================================
    # SIGNALS
    # ========================================================

    signals = []

    for symbol in symbols:

        if (
            open_count
            >= MAX_OPEN_TRADES
        ):

            break

        if (
            len(signals)
            >= MAX_NEW_SIGNALS
        ):

            break

        if symbol in open_symbols:

            inc(
                "ALREADY OPEN"
            )

            continue

        if in_cooldown(symbol):

            inc(
                "COOLDOWN"
            )

            continue

        try:

            signal = evaluate_symbol(
                symbol,
                diagnostics
            )

            if signal is None:
                continue

            # =================================================
            # SAVE
            # =================================================

            save_trade(
                signal
            )

            signals.append(
                signal
            )

            open_symbols.add(
                symbol
            )

            open_count += 1

            print(
                "🎯 SIGNAL "
                f"{signal['symbol']} "
                f"{signal['side']} "
                f"Entry="
                f"{format_price(signal['entry'])} "
                f"SL="
                f"{format_price(signal['sl'])} "
                f"TP="
                f"{format_price(signal['tp'])} "
                f"RR="
                f"{signal['rr']:.2f} "
                f"RVOL="
                f"{signal['rvol']:.2f} "
                f"Score="
                f"{signal['score']}/16"
            )

        except Exception as e:

            inc("ERROR")

            print(
                f"❌ {symbol}: {e}"
            )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    print_diagnostics(
        diagnostics,
        len(symbols)
    )

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        signals
    )

    print("")
    print(report)

    send_telegram(
        report
    )


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
                f"❌ VOLUME-KHAT {VERSION}\n"
                f"Fatal scanner error:\n"
                f"{e}"
            )

        except Exception:
            pass

        raise
