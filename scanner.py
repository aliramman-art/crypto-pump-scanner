# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.0
# ============================================================
#
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE ZONE + PRICE REACTION
# 5M  = FRESH BREAKOUT + PULLBACK + CONFIRMATION
# VOL = RVOL CONFIRMATION
#
# TP   = NEAREST VALID SUPPORT / RESISTANCE
# RR   = STRICTLY > 1.00
#
# CLOSED CANDLES ONLY
#
# EXIT = HISTORICAL 5M HIGH / LOW
# TIME EXIT = DISABLED
#
# STATISTICS = RESET ONCE AT v7.0 START
#
# REAL TRADING = DISABLED
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

VERSION = "v7.0"

KRAKEN_BASE = "https://futures.kraken.com"

TICKERS_URL = f"{KRAKEN_BASE}/derivatives/api/v3/tickers"
CHART_URL = f"{KRAKEN_BASE}/api/charts/v1/trade"

DB_FILE = "volume_khat_100.db"

PAPER_TRADING = True

TOP_N = 100

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3

# ------------------------------------------------------------
# TIMEFRAMES
# ------------------------------------------------------------

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

LOOKBACK_1H = 180
LOOKBACK_15M = 220
LOOKBACK_5M = 250

# ------------------------------------------------------------
# MOVING AVERAGES
# ------------------------------------------------------------

SMA_FAST = 20
SMA_SLOW = 50

# ------------------------------------------------------------
# TREND SCORE
# ------------------------------------------------------------

MIN_TREND_SCORE = 7

# ------------------------------------------------------------
# SUPPORT / RESISTANCE
# ------------------------------------------------------------

PIVOT_SWING = 3

ZONE_TOLERANCE_PCT = 0.30

MIN_ZONE_TOUCHES = 1

TP_ZONE_BUFFER_PCT = 0.10

# ------------------------------------------------------------
# VOLUME
# ------------------------------------------------------------

RVOL_PERIOD = 20

RVOL_NORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# BREAKOUT
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 5

BREAKOUT_MAX_AGE = 3

BREAKOUT_BUFFER_PCT = 0.05

# ------------------------------------------------------------
# CANDLE CONFIRMATION
# ------------------------------------------------------------

MIN_BODY_PCT = 0.20

WICK_BODY_RATIO = 1.15

# ------------------------------------------------------------
# SL
# ------------------------------------------------------------

ATR_PERIOD = 14

ATR_SL_MULTIPLIER = 0.20

MIN_SL_PCT = 0.50

MAX_SL_PCT = 1.50

SL_PRICE_BUFFER_PCT = 0.15

# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

MIN_SCORE = 10

# Max roughly 15:
#
# Trend              0-5
# 15M setup          0-4
# 5M trigger         0-4
# Volume             0-2
#
# ------------------------------------------------------------

# ------------------------------------------------------------
# EXIT
# ------------------------------------------------------------

TIME_EXIT_ENABLED = False

# ------------------------------------------------------------
# ONE-TIME RESET
# ------------------------------------------------------------

RESET_KEY = "VOLUME_KHAT_V7_RESET_DONE"

# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "VOLUME-KHAT-100/7.0",
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
    """
    Kraken may return seconds or milliseconds.
    """
    value = safe_float(value)

    if value > 10_000_000_000:
        value /= 1000.0

    return value


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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    conn.commit()

    # --------------------------------------------------------
    # Migration
    # --------------------------------------------------------

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


def get_meta(key):

    conn = db()

    row = conn.execute(
        "SELECT value FROM system_meta WHERE key=?",
        (key,)
    ).fetchone()

    conn.close()

    return row["value"] if row else None


def set_meta(key, value):

    conn = db()

    conn.execute(
        """
        INSERT INTO system_meta(key,value)
        VALUES(?,?)
        ON CONFLICT(key)
        DO UPDATE SET value=excluded.value
        """,
        (key, str(value))
    )

    conn.commit()
    conn.close()


def reset_database_once():

    if get_meta(RESET_KEY) == "1":
        return False

    conn = db()

    conn.execute("DELETE FROM trades")

    conn.commit()
    conn.close()

    set_meta(RESET_KEY, "1")

    return True


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

                o = item.get("open", item.get("o"))
                h = item.get("high", item.get("h"))
                l = item.get("low", item.get("l"))
                c = item.get("close", item.get("c"))
                v = item.get("volume", item.get("v", 0))

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

        # ----------------------------------------------------
        # CLOSED CANDLES ONLY
        #
        # Last Kraken candle can still be forming.
        # ----------------------------------------------------

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

        if (
            c["high"]
            >= zone["low"]
        ):

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

        if (
            c["low"]
            <= zone["high"]
        ):

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

        # ----------------------------------------------------
        # 1. SUPPORT HOLD
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 2. RESISTANCE BREAK / HOLD
        # ----------------------------------------------------

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

    # ========================================================
    # SHORT
    # ========================================================

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

    # --------------------------------------------------------
    # Structure
    # --------------------------------------------------------

    if structure["HH"]:
        score_long += 2

    if structure["HL"]:
        score_long += 1

    if structure["LH"]:
        score_short += 2

    if structure["LL"]:
        score_short += 1

    # --------------------------------------------------------
    # SMA alignment
    # --------------------------------------------------------

    if sma20 > sma50:
        score_long += 2

    if sma20 < sma50:
        score_short += 2

    # --------------------------------------------------------
    # Price vs SMA20
    # --------------------------------------------------------

    if price > sma20:
        score_long += 1

    if price < sma20:
        score_short += 1

    # --------------------------------------------------------
    # SMA slope
    # --------------------------------------------------------

    if old_sma20 is not None:

        if sma20 > old_sma20:
            score_long += 1

        if sma20 < old_sma20:
            score_short += 1

    # --------------------------------------------------------
    # Normalize to 7+
    #
    # Max is 7 here.
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # If score reaches 5/6 but not 7, do not force a direction.
    # --------------------------------------------------------

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

    later = candles[
        idx + 1:
    ]

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

def confirmation_after_pullback(
    candles,
    side,
    pullback
):

    idx = pullback["index"]

    if idx >= len(candles) - 1:
        return None

    confirmation = candles[-1]

    if side == "LONG":

        bullish = (
            confirmation["close"]
            > confirmation["open"]
        )

        higher_close = (
            confirmation["close"]
            > candles[idx]["high"]
        )

        return (
            bullish
            and higher_close
        )

    bearish = (
        confirmation["close"]
        < confirmation["open"]
    )

    lower_close = (
        confirmation["close"]
        < candles[idx]["low"]
    )

    return (
        bearish
        and lower_close
    )


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
# TP
# ============================================================

def select_nearest_tp(
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
            z
            for z in resistance_zones
            if z["center"] > entry
        ]

        candidates.sort(
            key=lambda z: z["center"]
        )

        if not candidates:
            return None

        # ----------------------------------------------------
        # IMPORTANT:
        #
        # ONLY the nearest valid resistance is considered.
        #
        # We DO NOT skip it just to manufacture RR.
        # ----------------------------------------------------

        zone = candidates[0]

        tp = (
            zone["low"]
            * (
                1
                - TP_ZONE_BUFFER_PCT
                / 100
            )
        )

        reward = tp - entry

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
            return None

        zone = candidates[0]

        tp = (
            zone["high"]
            * (
                1
                + TP_ZONE_BUFFER_PCT
                / 100
            )
        )

        reward = entry - tp

    rr = reward / risk

    if reward <= risk:
        return None

    if rr <= 1.0:
        return None

    return {
        "tp": tp,
        "rr": rr,
        "zone": zone,
        "reward": reward,
        "risk": risk
    }


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

        # ----------------------------------------------------
        # Both hit in same candle.
        #
        # Candle order is unknown.
        # Conservative handling = SL first.
        # ----------------------------------------------------

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

    confirmed = confirmation_after_pullback(
        candles_5m,
        side,
        pullback
    )

    if not confirmed:
        return None, "CONFIRMATION_FAILED"

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

    tp_data = select_nearest_tp(
        side,
        entry,
        sl,
        support_zones,
        resistance_zones
    )

    if tp_data is None:
        return None, "NEAREST_SR_RR_FAILED"

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    trend_score = min(
        trend["score"],
        7
    )

    setup_score = setup["score"]

    trigger_score = 0

    if breakout:
        trigger_score += 1

    if pullback:
        trigger_score += 1

    if confirmed:
        trigger_score += 1

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
        "entry_time": candles_5m[-1]["time"]
    }, None


# ============================================================
# REPORT HELPERS
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


def sr_text(zones, limit=4):

    if not zones:
        return "None"

    zones = sorted(
        zones,
        key=lambda z: z["center"]
    )

    output = []

    for z in zones[:limit]:

        output.append(
            f"{format_price(z['low'])}"
            f"-"
            f"{format_price(z['high'])}"
            f" ({z['touches']} touch)"
        )

    return " | ".join(output)


# ============================================================
# REPORT
# ============================================================

def build_report(
    signals,
    closed_trades,
    diagnostics
):

    perf = get_performance()

    lines = []

    lines.append(
        f"📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {now_utc().strftime('%Y/%m/%d %H:%M:%S UTC')}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED | TOP 100"
    )

    lines.append(
        "🧠 1H TREND + 15M S/R ZONE + "
        "5M BREAKOUT/PULLBACK"
    )

    lines.append(
        f"🛠 Strategy: {VERSION}"
    )

    lines.append(
        "💡 TP = NEAREST VALID SUPPORT/RESISTANCE"
    )

    lines.append(
        "💡 TP distance MUST be > SL distance"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("🎯 NEW SIGNALS")

    if not signals:

        lines.append("None")

    else:

        for s in signals:

            sl_pct = pct_distance(
                s["entry"],
                s["sl"]
            )

            tp_pct = pct_distance(
                s["entry"],
                s["tp"]
            )

            lines.append(
                f"{'🟢' if s['side']=='LONG' else '🔴'} "
                f"{s['symbol']} {s['side']}"
            )

            lines.append(
                f"Entry: {format_price(s['entry'])}"
            )

            lines.append(
                f"SL: {format_price(s['sl'])} "
                f"({sl_pct:.2f}%)"
            )

            lines.append(
                f"TP: {format_price(s['tp'])} "
                f"({tp_pct:.2f}%)"
            )

            lines.append(
                f"RR: {s['rr']:.2f}"
            )

            lines.append(
                f"RVOL: {s['rv']:.2f}"
            )

            lines.append(
                f"Score: {s['score']}"
            )

            lines.append(
                f"Setup: {s['setup']}"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    lines.append(
        "📂 OPEN TRADES "
        f"({perf['open']}/{MAX_OPEN_TRADES})"
    )

    open_trades = get_open_trades()

    if not open_trades:

        lines.append("None")

    else:

        for trade in open_trades:

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
                f"RR: {safe_float(trade['rr']):.2f}"
            )

            lines.append(
                f"Setup: {trade['setup']}"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    lines.append("📈 PERFORMANCE")

    lines.append(
        f"Open:   {perf['open']}"
    )

    lines.append(
        f"Closed: {perf['closed']}"
    )

    lines.append(
        f"Wins:   {perf['wins']}"
    )

    lines.append(
        f"Losses: {perf['losses']}"
    )

    lines.append(
        f"Win Rate: {perf['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: {perf['net_pnl']:+.2f}%"
    )

    lines.append(
        f"Closed This Run: {closed_trades}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("🔎 FILTER DIAGNOSTICS")

    for key, value in diagnostics.items():

        label = key.replace(
            "_",
            " "
        )

        lines.append(
            f"{label}: {value}"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("⚙️ SETTINGS")

    lines.append(
        f"1H Trend Score: >= {MIN_TREND_SCORE}/7"
    )

    lines.append(
        "15M: SUPPORT/RESISTANCE ZONES + "
        "PRICE REACTION"
    )

    lines.append(
        "5M: FRESH BREAKOUT + PULLBACK + CONFIRMATION"
    )

    lines.append(
        f"RVOL: >= {RVOL_NORMAL:.2f}"
    )

    lines.append(
        f"SL: {MIN_SL_PCT:.2f}% - "
        f"{MAX_SL_PCT:.2f}%"
    )

    lines.append(
        "TP: NEAREST VALID S/R"
    )

    lines.append(
        "RR: > 1.00 ONLY"
    )

    lines.append(
        "Exit: HISTORICAL 5M HIGH/LOW"
    )

    lines.append(
        "Time Exit: DISABLED"
    )

    lines.append(
        "Statistics: RESET AT v7.0 START"
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

    # Telegram safe chunks

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

    reset_happened = (
        reset_database_once()
    )

    if reset_happened:

        print(
            "🔄 Statistics reset "
            "for VOLUME-KHAT v7.0"
        )

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

        "RVOL Failed": 0,

        "Score Failed": 0,

        "No Valid S/R Target": 0,
        "Nearest S/R RR <= 1": 0,

        "SL Failed": 0,

        "Cooldown": 0,
        "Already Open": 0,

        "Errors": 0
    }

    signals = []

    closed_trades = 0

    # --------------------------------------------------------
    # Tickers / Top 100
    # --------------------------------------------------------

    symbols = get_top_symbols()

    diagnostics["Scanned"] = len(symbols)

    # --------------------------------------------------------
    # Historical exits first
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Refresh open trades after exits
    # --------------------------------------------------------

    open_trades = get_open_trades()

    open_symbols = {
        trade["symbol"]
        for trade in open_trades
    }

    # --------------------------------------------------------
    # Scan market
    # --------------------------------------------------------

    for symbol in symbols:

        if len(signals) >= MAX_NEW_SIGNALS:
            break

        if symbol in open_symbols:

            diagnostics["Already Open"] += 1

            continue

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

            # ------------------------------------------------
            # 1H
            # ------------------------------------------------

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

            # ------------------------------------------------
            # S/R
            # ------------------------------------------------

            support_zones, resistance_zones = (
                build_sr_zones(
                    candles_15m,
                    candles_1h
                )
            )

            # ------------------------------------------------
            # 15M
            # ------------------------------------------------

            setup = detect_15m_setup(
                side,
                candles_15m,
                support_zones,
                resistance_zones
            )

            if not setup:

                diagnostics["15M Failed"] += 1

                continue

            if side == "LONG":

                diagnostics[
                    "15M LONG Setup"
                ] += 1

            else:

                diagnostics[
                    "15M SHORT Setup"
                ] += 1

            # ------------------------------------------------
            # 5M breakout
            # ------------------------------------------------

            breakout = find_recent_breakout(
                candles_5m,
                side,
                setup["zone"]
            )

            if not breakout:

                diagnostics[
                    "5M Breakout Failed"
                ] += 1

                continue

            diagnostics[
                "5M Breakout Pass"
            ] += 1

            # ------------------------------------------------
            # Pullback
            # ------------------------------------------------

            pullback = detect_pullback(
                candles_5m,
                side,
                breakout
            )

            if not pullback:

                diagnostics[
                    "5M Pullback Failed"
                ] += 1

                continue

            diagnostics[
                "5M Pullback Pass"
            ] += 1

            # ------------------------------------------------
            # Confirmation
            # ------------------------------------------------

            confirmed = (
                confirmation_after_pullback(
                    candles_5m,
                    side,
                    pullback
                )
            )

            if not confirmed:

                diagnostics[
                    "5M Confirmation Failed"
                ] += 1

                continue

            diagnostics[
                "5M Confirmation Pass"
            ] += 1

            # ------------------------------------------------
            # Build signal
            # ------------------------------------------------

            signal, reason = build_signal(
                symbol,
                side,
                trend,
                setup,
                candles_5m,
                support_zones,
                resistance_zones
            )

            if signal is None:

                if reason == "RVOL_FAILED":

                    diagnostics[
                        "RVOL Failed"
                    ] += 1

                elif reason == "SL_FAILED":

                    diagnostics[
                        "SL Failed"
                    ] += 1

                elif reason == "NEAREST_SR_RR_FAILED":

                    diagnostics[
                        "Nearest S/R RR <= 1"
                    ] += 1

                elif reason == "SCORE_FAILED":

                    diagnostics[
                        "Score Failed"
                    ] += 1

                elif reason == "BREAKOUT_FAILED":

                    diagnostics[
                        "5M Breakout Failed"
                    ] += 1

                elif reason == "PULLBACK_FAILED":

                    diagnostics[
                        "5M Pullback Failed"
                    ] += 1

                elif reason == "CONFIRMATION_FAILED":

                    diagnostics[
                        "5M Confirmation Failed"
                    ] += 1

                continue

            # ------------------------------------------------
            # Max open protection
            # ------------------------------------------------

            if (
                len(
                    get_open_trades()
                )
                >= MAX_OPEN_TRADES
            ):

                break

            save_trade(signal)

            signals.append(signal)

            open_symbols.add(symbol)

        except Exception as e:

            diagnostics["Errors"] += 1

            print(
                f"❌ Scan error "
                f"{symbol}: {e}"
            )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report = build_report(
        signals,
        closed_trades,
        diagnostics
    )

    print(report)

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

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

        # Try to notify Telegram

        try:

            send_telegram(
                "❌ VOLUME-KHAT 100\n"
                f"Fatal scanner error:\n{e}"
            )

        except Exception:
            pass

        raise
