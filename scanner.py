# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v5.1
# ============================================================
#
# 1H  = MARKET STRUCTURE / TREND
# 15M = VALID SUPPORT / RESISTANCE + SETUP
# 5M  = FRESH BREAKOUT + PULLBACK + CONFIRMATION
#
# CLOSED CANDLES ONLY
#
# v5.1
# ------------------------------------------------------------
# MAJOR CHANGES
#
# 1) TP is NO LONGER fixed at 1R
# 2) TP comes from VALID SUPPORT / RESISTANCE ZONES
# 3) LONG  -> next valid resistance
# 4) SHORT -> next valid support
# 5) TP is placed slightly before the S/R zone
# 6) If TP distance <= SL distance -> REJECT
# 7) RR must be > 1.00
# 8) 15M and 1H S/R are clustered into zones
# 9) 1H levels are stronger fallback levels
# 10) 5M breakout must be fresh and linked to 15M setup
# 11) Current price + SL/TP % + S/R shown in report
# 12) CLOSED CANDLE exit detection using Kraken 5M high/low
# 13) Same-candle SL + TP -> AMBIGUOUS
# 14) PAPER TRADING ONLY
#
# ============================================================

import os
import time
import math
import sqlite3
import requests

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


# ============================================================
# CONFIG
# ============================================================

PAPER_TRADING = True

DB_FILE = "volume_khat_100.db"

KRAKEN_BASE = "https://futures.kraken.com"

TICKERS_URL = (
    KRAKEN_BASE +
    "/derivatives/api/v3/tickers"
)

CHART_URL = (
    KRAKEN_BASE +
    "/api/charts/v1/trade"
)

INSTRUMENTS_URL = (
    KRAKEN_BASE +
    "/derivatives/api/v3/instruments"
)

TOP_N = 100

# ------------------------------------------------------------
# TIMEFRAMES
# ------------------------------------------------------------

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

CANDLE_LIMIT_1H = 180
CANDLE_LIMIT_15M = 240
CANDLE_LIMIT_5M = 240

# ------------------------------------------------------------
# TREND
# ------------------------------------------------------------

SMA_FAST = 20
SMA_SLOW = 50

TREND_SWING = 3

# ------------------------------------------------------------
# SUPPORT / RESISTANCE
# ------------------------------------------------------------

SR_SWING = 3

SR_LOOKBACK_15M = 120
SR_LOOKBACK_1H = 120

# Two levels closer than this are treated as one zone
SR_CLUSTER_PCT = 0.0030       # 0.30%

# TP is placed slightly before the actual zone
TP_ZONE_BUFFER_PCT = 0.0010    # 0.10%

# A TP must provide strictly more reward than risk
MIN_TP_RR = 1.0

# ------------------------------------------------------------
# 5M STRUCTURE
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 5
BREAKOUT_MAX_AGE = 5

PULLBACK_MAX_AGE = 5

PULLBACK_TOLERANCE_PCT = 0.0035

CONFIRM_BODY_MIN_PCT = 0.10

# ------------------------------------------------------------
# RVOL
# ------------------------------------------------------------

RVOL_PERIOD = 20

RVOL_NORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# SL
# ------------------------------------------------------------

ATR_PERIOD = 14

SL_ATR_MULTIPLIER = 0.20

# Minimum structural buffer
SL_MIN_BUFFER_PCT = 0.0015

MIN_SL_PCT = 0.50
MAX_SL_PCT = 1.50

# ------------------------------------------------------------
# MARKET DIVERGENCE
# ------------------------------------------------------------

MAX_KRAKEN_ENTRY_DIVERGENCE_PCT = 0.75

# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

MIN_SCORE = 9

# ------------------------------------------------------------
# TRADING LIMITS
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3

# ------------------------------------------------------------
# TIME EXIT
# ------------------------------------------------------------

TIME_EXIT_ENABLED = True

MAX_HOLD_MINUTES = 120

# Emergency profit exit.
# This is NOT the TP.
TIME_PROFIT_EXIT_PCT = 1.50

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

HTTP_TIMEOUT = 15

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 VOLUME-KHAT-100"
    }
)

# ------------------------------------------------------------
# TIMEZONE
# ------------------------------------------------------------

try:
    IRAN_TZ = ZoneInfo("Asia/Tehran")
except Exception:
    IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================
# RUNTIME DIAGNOSTICS
# ============================================================

DIAG = {
    "scanned": 0,

    "trend_long": 0,
    "trend_short": 0,
    "trend_neutral": 0,

    "setup_long": 0,
    "setup_short": 0,
    "setup_failed": 0,

    "five_break_failed": 0,
    "five_pullback_failed": 0,
    "five_confirmation_failed": 0,

    "sr_no_target": 0,
    "tp_le_sl": 0,
    "rr_failed": 0,

    "sl_too_small": 0,
    "sl_too_large": 0,

    "divergence_failed": 0,

    "score_failed": 0,
    "cooldown": 0,

    "signals": 0,

    "errors": 0,
}


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iran():
    return datetime.now(IRAN_TZ)


def format_time(dt=None):
    if dt is None:
        dt = now_iran()

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)

    return dt.astimezone(IRAN_TZ).strftime(
        "%Y/%m/%d %H:%M:%S"
    )


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def pct_distance(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if a == 0:
        return 0.0

    return abs(a - b) / abs(a) * 100.0


def pct_from_entry(entry, price):
    if entry == 0:
        return 0.0

    return (price - entry) / entry * 100.0


def calculate_rr(entry, sl, tp):
    risk = abs(entry - sl)
    reward = abs(tp - entry)

    if risk <= 0:
        return 0.0

    return reward / risk


def normalize_symbol(symbol):
    if not symbol:
        return ""

    return str(symbol).upper().replace("-", "")


def is_pf_usd(symbol):
    s = normalize_symbol(symbol)

    return (
        s.startswith("PF_")
        and s.endswith("USD")
    )


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

            symbol TEXT NOT NULL,
            side TEXT NOT NULL,

            setup TEXT,

            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            rr REAL,

            status TEXT DEFAULT 'OPEN',
            result TEXT,

            pnl REAL,

            entry_time TEXT,
            exit_time TEXT,

            exit REAL,
            exit_reason TEXT,

            duration_minutes REAL,

            closed_reported INTEGER DEFAULT 0,

            created_at TEXT DEFAULT CURRENT_TIMESTAMP
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

    conn.close()


def get_open_trades():
    conn = db_connect()

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    ).fetchall()

    conn.close()

    return rows


def get_last_trade(symbol):
    conn = db_connect()

    row = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,)
    ).fetchone()

    conn.close()

    return row


def insert_trade(
    symbol,
    side,
    setup,
    entry,
    sl,
    tp,
    rr,
):
    conn = db_connect()

    entry_time = now_utc().isoformat()

    conn.execute(
        """
        INSERT INTO trades
        (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            status,
            entry_time,
            closed_reported
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN', ?, 0)
        """,
        (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            entry_time,
        )
    )

    conn.commit()

    conn.close()


def close_trade(
    trade_id,
    exit_price,
    pnl,
    result,
    exit_reason,
    duration_minutes,
):
    conn = db_connect()

    conn.execute(
        """
        UPDATE trades

        SET
            status = 'CLOSED',
            exit = ?,
            exit_time = ?,
            pnl = ?,
            result = ?,
            exit_reason = ?,
            duration_minutes = ?,
            closed_reported = 0

        WHERE id = ?
        """,
        (
            exit_price,
            now_utc().isoformat(),
            pnl,
            result,
            exit_reason,
            duration_minutes,
            trade_id,
        )
    )

    conn.commit()

    conn.close()


# ============================================================
# KRAKEN API
# ============================================================

def http_get(url, params=None):
    response = SESSION.get(
        url,
        params=params,
        timeout=HTTP_TIMEOUT,
    )

    response.raise_for_status()

    return response.json()


def fetch_ohlcv(symbol, interval, limit=200):
    """
    Kraken Futures chart endpoint.

    Returns list of dictionaries:
    {
        time,
        open,
        high,
        low,
        close,
        volume
    }
    """

    try:
        url = (
            f"{CHART_URL}/"
            f"{symbol}/"
            f"{interval}"
        )

        end = int(time.time())

        # Approximate starting timestamp.
        minutes = {
            "5m": 5,
            "15m": 15,
            "1h": 60,
        }.get(interval, 5)

        start = end - (
            limit * minutes * 60
        )

        params = {
            "from": start,
            "to": end,
        }

        data = http_get(
            url,
            params=params,
        )

        candles = (
            data.get("candles")
            or data.get("data")
            or []
        )

        result = []

        for c in candles:

            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("ts")
                )

                o = c.get("open")
                h = c.get("high")
                l = c.get("low")
                cl = (
                    c.get("close")
                    or c.get("last")
                )
                v = (
                    c.get("volume")
                    or c.get("vol")
                    or 0
                )

            elif isinstance(c, (list, tuple)):

                if len(c) < 6:
                    continue

                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                cl = c[4]
                v = c[5]

            else:
                continue

            try:

                ts = safe_float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000.0

                result.append(
                    {
                        "time": int(ts),

                        "open": safe_float(o),
                        "high": safe_float(h),
                        "low": safe_float(l),
                        "close": safe_float(cl),
                        "volume": safe_float(v),
                    }
                )

            except Exception:
                continue

        result.sort(
            key=lambda x: x["time"]
        )

        # Closed candles only.
        # The latest candle may still be forming.
        if len(result) > 1:
            result = result[:-1]

        return result[-limit:]

    except Exception as exc:

        DIAG["errors"] += 1

        print(
            f"OHLCV ERROR "
            f"{symbol} {interval}: {exc}"
        )

        return []


def get_all_tickers():
    try:

        data = http_get(
            TICKERS_URL
        )

        tickers = (
            data.get("tickers")
            or data.get("data")
            or []
        )

        return tickers

    except Exception as exc:

        DIAG["errors"] += 1

        print(
            f"TICKER ERROR: {exc}"
        )

        return []


def get_current_price(symbol):
    """
    Current Kraken Futures market price.
    """

    tickers = get_all_tickers()

    target = normalize_symbol(symbol)

    for ticker in tickers:

        tsymbol = normalize_symbol(
            ticker.get("symbol")
        )

        if tsymbol != target:
            continue

        price = (
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice")
            or ticker.get("indexPrice")
        )

        if price is not None:
            return safe_float(price)

    return 0.0


def get_top_markets(limit=TOP_N):
    tickers = get_all_tickers()

    markets = []

    for ticker in tickers:

        symbol = ticker.get("symbol")

        if not is_pf_usd(symbol):
            continue

        volume = (
            ticker.get("vol24h")
            or ticker.get("volume24h")
            or ticker.get("volume")
            or ticker.get("volumeQuote")
            or 0
        )

        price = (
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice")
            or 0
        )

        markets.append(
            {
                "symbol": normalize_symbol(symbol),
                "volume": safe_float(volume),
                "price": safe_float(price),
            }
        )

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True,
    )

    return markets[:limit]


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body_pct(c):
    if c["open"] == 0:
        return 0.0

    return (
        abs(c["close"] - c["open"])
        / c["open"]
        * 100.0
    )


def candle_range(c):
    return max(
        0.0,
        c["high"] - c["low"]
    )


def is_bull(c):
    return c["close"] > c["open"]


def is_bear(c):
    return c["close"] < c["open"]


def sma(candles, period):
    if len(candles) < period:
        return None

    values = [
        c["close"]
        for c in candles[-period:]
    ]

    return sum(values) / len(values)


def calculate_atr(candles, period=14):
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
            ),
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(trs[-period:]) / period


def calculate_rvol(candles, period=20):
    if len(candles) < period + 1:
        return 0.0

    current_volume = candles[-1]["volume"]

    previous = [
        c["volume"]
        for c in candles[-period - 1:-1]
    ]

    if not previous:
        return 0.0

    avg_volume = (
        sum(previous)
        / len(previous)
    )

    if avg_volume <= 0:
        return 0.0

    return current_volume / avg_volume


# ============================================================
# PIVOTS
# ============================================================

def pivot_high(candles, index, swing=3):
    if index < swing:
        return False

    if index + swing >= len(candles):
        return False

    level = candles[index]["high"]

    for j in range(
        index - swing,
        index + swing + 1
    ):

        if j == index:
            continue

        if candles[j]["high"] >= level:
            return False

    return True


def pivot_low(candles, index, swing=3):
    if index < swing:
        return False

    if index + swing >= len(candles):
        return False

    level = candles[index]["low"]

    for j in range(
        index - swing,
        index + swing + 1
    ):

        if j == index:
            continue

        if candles[j]["low"] <= level:
            return False

    return True


# ============================================================
# SUPPORT / RESISTANCE ZONES
# ============================================================

def extract_sr_levels(
    candles,
    swing=3,
    lookback=120,
):
    """
    Returns raw confirmed pivot levels.

    resistance:
        confirmed pivot highs

    support:
        confirmed pivot lows
    """

    if len(candles) < swing * 2 + 10:
        return {
            "support": [],
            "resistance": [],
        }

    start = max(
        swing,
        len(candles) - lookback
    )

    end = len(candles) - swing

    supports = []
    resistances = []

    for i in range(start, end):

        if pivot_high(
            candles,
            i,
            swing,
        ):
            resistances.append(
                {
                    "price":
                        candles[i]["high"],

                    "index": i,

                    "time":
                        candles[i]["time"],

                    "type":
                        "RESISTANCE",
                }
            )

        if pivot_low(
            candles,
            i,
            swing,
        ):
            supports.append(
                {
                    "price":
                        candles[i]["low"],

                    "index": i,

                    "time":
                        candles[i]["time"],

                    "type":
                        "SUPPORT",
                }
            )

    return {
        "support": supports,
        "resistance": resistances,
    }


def cluster_levels(
    levels,
    tolerance_pct=SR_CLUSTER_PCT,
):
    """
    Converts multiple nearby pivot lines into zones.

    Example:
        100.00
        100.15
        100.25

    may become:

        100.00 - 100.25
    """

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

        base = (
            sum(
                x["price"]
                for x in current
            )
            / len(current)
        )

        if base == 0:
            current.append(level)
            continue

        distance = (
            abs(
                level["price"] - base
            )
            / base
        )

        if distance <= tolerance_pct:

            current.append(level)

        else:

            zones.append(
                build_zone(current)
            )

            current = [level]

    if current:
        zones.append(
            build_zone(current)
        )

    return zones


def build_zone(levels):
    prices = [
        x["price"]
        for x in levels
    ]

    strongest = max(
        levels,
        key=lambda x: x["index"]
    )

    return {
        "low": min(prices),
        "high": max(prices),

        "mid": sum(prices)
        / len(prices),

        "touches": len(levels),

        "latest_index":
            strongest["index"],

        "latest_time":
            strongest["time"],
    }


def get_sr_zones(candles, swing=3, lookback=120):
    raw = extract_sr_levels(
        candles,
        swing=swing,
        lookback=lookback,
    )

    support = cluster_levels(
        raw["support"]
    )

    resistance = cluster_levels(
        raw["resistance"]
    )

    return {
        "support": support,
        "resistance": resistance,
    }


def zone_strength(zone, timeframe):
    score = 0

    if timeframe == "1H":
        score += 3
    else:
        score += 2

    if zone["touches"] >= 2:
        score += 1

    if zone["touches"] >= 3:
        score += 1

    return score


def format_zone(zone):
    return (
        f"{zone['low']:.8g}"
        f" - "
        f"{zone['high']:.8g}"
    )


def select_support_zones(
    candles_15m,
    candles_1h,
):
    sr15 = get_sr_zones(
        candles_15m,
        swing=SR_SWING,
        lookback=SR_LOOKBACK_15M,
    )

    sr1h = get_sr_zones(
        candles_1h,
        swing=SR_SWING,
        lookback=SR_LOOKBACK_1H,
    )

    supports = []

    for zone in sr15["support"]:
        supports.append(
            {
                **zone,
                "timeframe": "15M",
                "strength":
                    zone_strength(
                        zone,
                        "15M",
                    ),
            }
        )

    for zone in sr1h["support"]:
        supports.append(
            {
                **zone,
                "timeframe": "1H",
                "strength":
                    zone_strength(
                        zone,
                        "1H",
                    ),
            }
        )

    return supports


def select_resistance_zones(
    candles_15m,
    candles_1h,
):
    sr15 = get_sr_zones(
        candles_15m,
        swing=SR_SWING,
        lookback=SR_LOOKBACK_15M,
    )

    sr1h = get_sr_zones(
        candles_1h,
        swing=SR_SWING,
        lookback=SR_LOOKBACK_1H,
    )

    resistances = []

    for zone in sr15["resistance"]:
        resistances.append(
            {
                **zone,
                "timeframe": "15M",
                "strength":
                    zone_strength(
                        zone,
                        "15M",
                    ),
            }
        )

    for zone in sr1h["resistance"]:
        resistances.append(
            {
                **zone,
                "timeframe": "1H",
                "strength":
                    zone_strength(
                        zone,
                        "1H",
                    ),
            }
        )

    return resistances


# ============================================================
# 1H MARKET STRUCTURE
# ============================================================

def recent_pivots(candles, swing=3):
    highs = []
    lows = []

    start = swing
    end = len(candles) - swing

    for i in range(start, end):

        if pivot_high(
            candles,
            i,
            swing,
        ):
            highs.append(
                candles[i]["high"]
            )

        if pivot_low(
            candles,
            i,
            swing,
        ):
            lows.append(
                candles[i]["low"]
            )

    return highs, lows


def get_trend(candles):
    if len(candles) < 70:
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Not enough candles",
        }

    close = candles[-1]["close"]

    fast = sma(
        candles,
        SMA_FAST
    )

    slow = sma(
        candles,
        SMA_SLOW
    )

    highs, lows = recent_pivots(
        candles,
        TREND_SWING,
    )

    if (
        fast is None
        or slow is None
        or len(highs) < 2
        or len(lows) < 2
    ):
        return {
            "side": "NEUTRAL",
            "score": 0,
            "reason": "Insufficient structure",
        }

    higher_high = (
        highs[-1] > highs[-2]
    )

    higher_low = (
        lows[-1] > lows[-2]
    )

    lower_high = (
        highs[-1] < highs[-2]
    )

    lower_low = (
        lows[-1] < lows[-2]
    )

    long_ok = (
        higher_high
        and higher_low
        and close > fast
        and fast > slow
    )

    short_ok = (
        lower_high
        and lower_low
        and close < fast
        and fast < slow
    )

    if long_ok:

        DIAG["trend_long"] += 1

        return {
            "side": "LONG",
            "score": 4,
            "reason":
                "HH + HL + price > SMA20 > SMA50",
        }

    if short_ok:

        DIAG["trend_short"] += 1

        return {
            "side": "SHORT",
            "score": 4,
            "reason":
                "LH + LL + price < SMA20 < SMA50",
        }

    DIAG["trend_neutral"] += 1

    return {
        "side": "NEUTRAL",
        "score": 0,
        "reason": "No clean 1H structure",
    }


# ============================================================
# 15M SETUP
# ============================================================

def detect_15m_setup(
    candles,
    side,
):
    """
    Setup is tied to a recent valid 15M S/R area.

    LONG:
        price breaks / rejects above support or near resistance.

    SHORT:
        price breaks / rejects below resistance or near support.

    The setup level is used later by the 5M confirmation.
    """

    if len(candles) < 60:
        return None

    current = candles[-1]

    zones = get_sr_zones(
        candles,
        swing=SR_SWING,
        lookback=SR_LOOKBACK_15M,
    )

    if side == "LONG":

        # Prefer recent support that price is holding above.
        candidates = []

        for zone in zones["support"]:

            if zone["high"] >= current["close"]:
                continue

            distance = (
                current["close"]
                - zone["high"]
            ) / current["close"]

            if distance <= 0.02:
                candidates.append(
                    (
                        distance,
                        zone
                    )
                )

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x[0]
        )

        distance, zone = candidates[0]

        DIAG["setup_long"] += 1

        return {
            "side": "LONG",

            "level":
                zone["high"],

            "zone":
                zone,

            "type":
                "SUPPORT_HOLD",

            "score": 3,
        }

    if side == "SHORT":

        candidates = []

        for zone in zones["resistance"]:

            if zone["low"] <= current["close"]:
                continue

            distance = (
                zone["low"]
                - current["close"]
            ) / current["close"]

            if distance <= 0.02:
                candidates.append(
                    (
                        distance,
                        zone
                    )
                )

        if not candidates:
            return None

        candidates.sort(
            key=lambda x: x[0]
        )

        distance, zone = candidates[0]

        DIAG["setup_short"] += 1

        return {
            "side": "SHORT",

            "level":
                zone["low"],

            "zone":
                zone,

            "type":
                "RESISTANCE_HOLD",

            "score": 3,
        }

    return None


# ============================================================
# 5M BREAKOUT / PULLBACK / CONFIRMATION
# ============================================================

def detect_5m_breakout(
    candles,
    side,
    setup,
):
    """
    Find the MOST RECENT valid breakout.

    This intentionally scans backwards so an old breakout
    cannot become a fake 'fresh' breakout.
    """

    if len(candles) < 40:
        return None

    last_index = len(candles) - 1

    search_start = max(
        10,
        last_index - BREAKOUT_LOOKBACK - 5
    )

    setup_level = setup["level"]

    for i in range(
        last_index - 1,
        search_start - 1,
        -1
    ):

        c = candles[i]

        previous = candles[
            max(0, i - BREAKOUT_LOOKBACK):i
        ]

        if not previous:
            continue

        previous_high = max(
            x["high"]
            for x in previous
        )

        previous_low = min(
            x["low"]
            for x in previous
        )

        body_pct = candle_body_pct(c)

        if body_pct < CONFIRM_BODY_MIN_PCT:
            continue

        if side == "LONG":

            broke_structure = (
                c["close"]
                > previous_high
            )

            linked_to_setup = (
                c["close"]
                >= setup_level
                * (1 - PULLBACK_TOLERANCE_PCT)
            )

            if (
                broke_structure
                and linked_to_setup
                and is_bull(c)
            ):
                return {
                    "index": i,
                    "level": previous_high,
                    "time": c["time"],
                }

        elif side == "SHORT":

            broke_structure = (
                c["close"]
                < previous_low
            )

            linked_to_setup = (
                c["close"]
                <= setup_level
                * (1 + PULLBACK_TOLERANCE_PCT)
            )

            if (
                broke_structure
                and linked_to_setup
                and is_bear(c)
            ):
                return {
                    "index": i,
                    "level": previous_low,
                    "time": c["time"],
                }

    DIAG["five_break_failed"] += 1

    return None


def detect_pullback(
    candles,
    side,
    breakout,
):
    break_index = breakout["index"]
    break_level = breakout["level"]

    last_index = len(candles) - 1

    if (
        last_index
        - break_index
        > PULLBACK_MAX_AGE
    ):
        DIAG["five_pullback_failed"] += 1
        return None

    for i in range(
        break_index + 1,
        last_index + 1
    ):

        c = candles[i]

        tolerance = (
            break_level
            * PULLBACK_TOLERANCE_PCT
        )

        if side == "LONG":

            touched = (
                c["low"]
                <= break_level + tolerance
                and
                c["low"]
                >= break_level - tolerance
            )

            if touched:

                return {
                    "index": i,
                    "level": break_level,
                    "candle": c,
                }

        elif side == "SHORT":

            touched = (
                c["high"]
                >= break_level - tolerance
                and
                c["high"]
                <= break_level + tolerance
            )

            if touched:

                return {
                    "index": i,
                    "level": break_level,
                    "candle": c,
                }

    DIAG["five_pullback_failed"] += 1

    return None


def confirm_5m_structure(
    candles,
    side,
    setup,
):
    breakout = detect_5m_breakout(
        candles,
        side,
        setup,
    )

    if breakout is None:
        return None

    pullback = detect_pullback(
        candles,
        side,
        breakout,
    )

    if pullback is None:
        return None

    confirmation = candles[-1]

    body_pct = candle_body_pct(
        confirmation
    )

    if body_pct < CONFIRM_BODY_MIN_PCT:

        DIAG[
            "five_confirmation_failed"
        ] += 1

        return None

    if side == "LONG":

        if not is_bull(confirmation):
            DIAG[
                "five_confirmation_failed"
            ] += 1
            return None

        if (
            confirmation["close"]
            <= pullback["level"]
        ):
            DIAG[
                "five_confirmation_failed"
            ] += 1
            return None

    elif side == "SHORT":

        if not is_bear(confirmation):
            DIAG[
                "five_confirmation_failed"
            ] += 1
            return None

        if (
            confirmation["close"]
            >= pullback["level"]
        ):
            DIAG[
                "five_confirmation_failed"
            ] += 1
            return None

    return {
        "breakout": breakout,
        "pullback": pullback,
        "confirmation": confirmation,

        "score": 4,
    }


# ============================================================
# STRUCTURAL SL
# ============================================================

def calculate_5m_structural_sl(
    candles,
    side,
    entry,
):
    atr = calculate_atr(
        candles,
        ATR_PERIOD,
    )

    if atr is None:
        return None

    recent = candles[-12:]

    if side == "LONG":

        structural_low = min(
            c["low"]
            for c in recent
        )

        buffer = max(
            atr * SL_ATR_MULTIPLIER,

            entry
            * SL_MIN_BUFFER_PCT,
        )

        sl = structural_low - buffer

        if sl >= entry:
            return None

        return sl

    if side == "SHORT":

        structural_high = max(
            c["high"]
            for c in recent
        )

        buffer = max(
            atr * SL_ATR_MULTIPLIER,

            entry
            * SL_MIN_BUFFER_PCT,
        )

        sl = structural_high + buffer

        if sl <= entry:
            return None

        return sl

    return None


# ============================================================
# TP FROM SUPPORT / RESISTANCE
# ============================================================

def select_tp_from_sr(
    side,
    entry,
    sl,
    supports,
    resistances,
):
    """
    IMPORTANT:

    TP is selected ONLY from a valid S/R zone.

    LONG:
        resistance above entry

    SHORT:
        support below entry

    The closest valid level that gives RR > 1
    is selected.

    We do NOT move TP arbitrarily farther away
    simply to manufacture a better RR.
    """

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    candidates = []

    if side == "LONG":

        for zone in resistances:

            # Zone must be above Entry.
            if zone["low"] <= entry:
                continue

            # TP slightly before resistance.
            tp = (
                zone["low"]
                * (1 - TP_ZONE_BUFFER_PCT)
            )

            if tp <= entry:
                continue

            reward = tp - entry

            rr = reward / risk

            if reward <= risk:
                continue

            if rr <= MIN_TP_RR:
                continue

            candidates.append(
                {
                    "tp": tp,
                    "rr": rr,
                    "zone": zone,
                    "source":
                        f"{zone['timeframe']} "
                        f"RESISTANCE",
                    "distance":
                        reward,
                }
            )

    elif side == "SHORT":

        for zone in supports:

            # Zone must be below Entry.
            if zone["high"] >= entry:
                continue

            # TP slightly before support.
            tp = (
                zone["high"]
                * (1 + TP_ZONE_BUFFER_PCT)
            )

            if tp >= entry:
                continue

            reward = entry - tp

            rr = reward / risk

            if reward <= risk:
                continue

            if rr <= MIN_TP_RR:
                continue

            candidates.append(
                {
                    "tp": tp,
                    "rr": rr,
                    "zone": zone,
                    "source":
                        f"{zone['timeframe']} "
                        f"SUPPORT",
                    "distance":
                        reward,
                }
            )

    if not candidates:

        DIAG["sr_no_target"] += 1

        return None

    # Nearest valid S/R target.
    candidates.sort(
        key=lambda x: (
            x["distance"],
            -x["zone"]["strength"],
        )
    )

    return candidates[0]


# ============================================================
# SUPPORT / RESISTANCE REPORT DATA
# ============================================================

def get_report_sr(
    entry,
    supports,
    resistances,
    max_each=3,
):
    support_levels = []

    resistance_levels = []

    for zone in supports:

        if zone["high"] < entry:

            support_levels.append(zone)

    for zone in resistances:

        if zone["low"] > entry:

            resistance_levels.append(zone)

    support_levels.sort(
        key=lambda z: entry - z["high"]
    )

    resistance_levels.sort(
        key=lambda z: z["low"] - entry
    )

    return (
        support_levels[:max_each],
        resistance_levels[:max_each],
    )


# ============================================================
# DIVERGENCE CHECK
# ============================================================

def check_price_divergence(
    kraken_price,
    entry,
):
    if kraken_price <= 0:
        return True

    divergence = (
        abs(
            kraken_price - entry
        )
        / entry
        * 100
    )

    return (
        divergence
        <= MAX_KRAKEN_ENTRY_DIVERGENCE_PCT
    )


# ============================================================
# SIGNAL BUILDER
# ============================================================

def build_signal(
    symbol,
    trend,
    setup,
    confirmation,
    candles_5m,
    candles_15m,
    candles_1h,
    entry,
):
    side = trend["side"]

    sl = calculate_5m_structural_sl(
        candles_5m,
        side,
        entry,
    )

    if sl is None:
        DIAG["sl_too_small"] += 1
        return None

    sl_pct = pct_distance(
        entry,
        sl,
    )

    if sl_pct < MIN_SL_PCT:

        DIAG["sl_too_small"] += 1

        return None

    if sl_pct > MAX_SL_PCT:

        DIAG["sl_too_large"] += 1

        return None

    supports = select_support_zones(
        candles_15m,
        candles_1h,
    )

    resistances = select_resistance_zones(
        candles_15m,
        candles_1h,
    )

    tp_data = select_tp_from_sr(
        side,
        entry,
        sl,
        supports,
        resistances,
    )

    if tp_data is None:
        return None

    tp = tp_data["tp"]

    reward = abs(tp - entry)
    risk = abs(entry - sl)

    # HARD RULE:
    # TP distance must be strictly greater than SL distance.
    if reward <= risk:

        DIAG["tp_le_sl"] += 1

        return None

    rr = calculate_rr(
        entry,
        sl,
        tp,
    )

    if rr <= 1.0:

        DIAG["rr_failed"] += 1

        return None

    score = (
        trend["score"]
        + setup["score"]
        + confirmation["score"]
    )

    if score < MIN_SCORE:

        DIAG["score_failed"] += 1

        return None

    support_report, resistance_report = (
        get_report_sr(
            entry,
            supports,
            resistances,
        )
    )

    return {
        "symbol": symbol,

        "side": side,

        "entry": entry,

        "current": entry,

        "sl": sl,

        "tp": tp,

        "sl_pct":
            pct_distance(
                entry,
                sl,
            ),

        "tp_pct":
            pct_distance(
                entry,
                tp,
            ),

        "rr": rr,

        "score": score,

        "setup":
            setup["type"],

        "tp_source":
            tp_data["source"],

        "tp_zone":
            tp_data["zone"],

        "support_zones":
            support_report,

        "resistance_zones":
            resistance_report,

        "trend_reason":
            trend["reason"],

        "confirmation":
            confirmation,
    }


# ============================================================
# COOLDOWN
# ============================================================

def is_in_cooldown(symbol):
    last = get_last_trade(symbol)

    if not last:
        return False

    # If last trade is still open, handled elsewhere.
    if last["status"] == "OPEN":
        return False

    exit_time = last["exit_time"]

    if not exit_time:
        return False

    try:

        dt = datetime.fromisoformat(
            exit_time
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        elapsed = (
            now_utc() - dt
        ).total_seconds()

        cooldown_seconds = (
            COOLDOWN_CANDLES
            * 5
            * 60
        )

        if elapsed < cooldown_seconds:

            DIAG["cooldown"] += 1

            return True

    except Exception:
        return False

    return False


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(
    trade,
    current_price,
):
    entry = safe_float(
        trade["entry"]
    )

    if entry <= 0:
        return 0.0

    if trade["side"] == "LONG":

        return (
            current_price - entry
        ) / entry * 100

    return (
        entry - current_price
    ) / entry * 100


# ============================================================
# EXIT ENGINE
# ============================================================

def process_open_trade(
    trade,
    candles_5m,
    current_price,
):
    if not candles_5m:
        return None

    side = trade["side"]

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    latest = candles_5m[-1]

    high = latest["high"]
    low = latest["low"]

    hit_sl = False
    hit_tp = False

    if side == "LONG":

        hit_sl = low <= sl
        hit_tp = high >= tp

    elif side == "SHORT":

        hit_sl = high >= sl
        hit_tp = low <= tp

    # --------------------------------------------------------
    # Both touched in the same closed candle.
    # We cannot know intrabar order.
    # --------------------------------------------------------

    if hit_sl and hit_tp:

        exit_price = current_price

        pnl = calculate_trade_pnl(
            trade,
            exit_price,
        )

        return {
            "exit_price": exit_price,
            "pnl": pnl,
            "result": "AMBIGUOUS",
            "reason":
                "SL_AND_TP_SAME_CANDLE",
        }

    if hit_sl:

        exit_price = sl

        pnl = calculate_trade_pnl(
            trade,
            exit_price,
        )

        return {
            "exit_price": exit_price,
            "pnl": pnl,
            "result": "LOSS",
            "reason": "SL_HIT",
        }

    if hit_tp:

        exit_price = tp

        pnl = calculate_trade_pnl(
            trade,
            exit_price,
        )

        return {
            "exit_price": exit_price,
            "pnl": pnl,
            "result": "WIN",
            "reason": "TP_HIT",
        }

    # --------------------------------------------------------
    # Time exit
    # --------------------------------------------------------

    if TIME_EXIT_ENABLED:

        try:

            entry_time = datetime.fromisoformat(
                trade["entry_time"]
            )

            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(
                    tzinfo=timezone.utc
                )

            age_minutes = (
                now_utc() - entry_time
            ).total_seconds() / 60.0

            pnl = calculate_trade_pnl(
                trade,
                current_price,
            )

            if (
                age_minutes
                >= MAX_HOLD_MINUTES
                and
                pnl >= TIME_PROFIT_EXIT_PCT
            ):

                return {
                    "exit_price":
                        current_price,

                    "pnl": pnl,

                    "result": "WIN",

                    "reason":
                        "TIME_PROFIT_EXIT",
                }

        except Exception:
            pass

    return None


def process_all_open_trades():
    open_trades = get_open_trades()

    if not open_trades:
        return []

    closed = []

    for trade in open_trades:

        symbol = trade["symbol"]

        current_price = get_current_price(
            symbol
        )

        if current_price <= 0:
            continue

        candles_5m = fetch_ohlcv(
            symbol,
            TF_5M,
            CANDLE_LIMIT_5M,
        )

        if not candles_5m:
            continue

        result = process_open_trade(
            trade,
            candles_5m,
            current_price,
        )

        if result is None:
            continue

        try:

            entry_time = datetime.fromisoformat(
                trade["entry_time"]
            )

            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(
                    tzinfo=timezone.utc
                )

            duration = (
                now_utc() - entry_time
            ).total_seconds() / 60

        except Exception:
            duration = 0

        close_trade(
            trade["id"],
            result["exit_price"],
            result["pnl"],
            result["result"],
            result["reason"],
            duration,
        )

        closed.append(
            {
                "trade": trade,
                **result,
            }
        )

    return closed


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():
    conn = db_connect()

    total = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()[0]

    wins = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'WIN'
        """
    ).fetchone()[0]

    losses = conn.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'LOSS'
        """
    ).fetchone()[0]

    pnl = conn.execute(
        """
        SELECT COALESCE(
            SUM(pnl),
            0
        )
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()[0]

    conn.close()

    wr = (
        wins / total * 100
        if total > 0
        else 0.0
    )

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "win_rate": wr,
        "pnl": safe_float(pnl),
    }


# ============================================================
# REPORT HELPERS
# ============================================================

def format_sr_report(
    zones,
    label,
):
    lines = []

    if not zones:

        lines.append(
            f"{label}: None"
        )

        return lines

    lines.append(
        f"{label}:"
    )

    for zone in zones:

        lines.append(
            "  "
            f"{zone['timeframe']}: "
            f"{format_zone(zone)} "
            f"({zone['touches']} touch)"
        )

    return lines


def format_signal(signal):
    side_icon = (
        "🟢"
        if signal["side"] == "LONG"
        else "🔴"
    )

    lines = []

    lines.append(
        f"{side_icon} "
        f"{signal['symbol']} "
        f"{signal['side']}"
    )

    lines.append("")

    lines.append(
        f"Current: "
        f"{signal['current']:.8g}"
    )

    lines.append(
        f"Entry:   "
        f"{signal['entry']:.8g}"
    )

    lines.append("")

    sl_sign = "-" if signal["side"] == "LONG" else "+"

    tp_sign = "+" if signal["side"] == "LONG" else "-"

    lines.append(
        f"🛑 SL: "
        f"{signal['sl']:.8g} "
        f"({sl_sign}{signal['sl_pct']:.2f}%)"
    )

    lines.append(
        f"🎯 TP: "
        f"{signal['tp']:.8g} "
        f"({tp_sign}{signal['tp_pct']:.2f}%)"
    )

    lines.append(
        f"📐 RR: "
        f"{signal['rr']:.2f}"
    )

    lines.append(
        f"🏆 Score: "
        f"{signal['score']}"
    )

    lines.append("")

    lines.extend(
        format_sr_report(
            signal["support_zones"],
            "📍 SUPPORT",
        )
    )

    lines.append("")

    lines.extend(
        format_sr_report(
            signal["resistance_zones"],
            "📍 RESISTANCE",
        )
    )

    lines.append("")

    lines.append(
        f"🎯 TP SOURCE: "
        f"{signal['tp_source']}"
    )

    lines.append(
        f"📦 TP ZONE: "
        f"{format_zone(signal['tp_zone'])}"
    )

    return "\n".join(lines)


def format_open_trade(trade):
    current = get_current_price(
        trade["symbol"]
    )

    pnl = calculate_trade_pnl(
        trade,
        current,
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

    sl_pct = pct_distance(
        entry,
        sl,
    )

    tp_pct = pct_distance(
        entry,
        tp,
    )

    rr = calculate_rr(
        entry,
        sl,
        tp,
    )

    icon = (
        "🟢"
        if trade["side"] == "LONG"
        else "🔴"
    )

    pnl_icon = (
        "+"
        if pnl >= 0
        else ""
    )

    lines = []

    lines.append(
        f"{icon} "
        f"{trade['symbol']} "
        f"{trade['side']}"
    )

    lines.append(
        f"Entry:   {entry:.8g}"
    )

    lines.append(
        f"Current: {current:.8g}"
    )

    lines.append(
        f"P&L:     "
        f"{pnl_icon}{pnl:.2f}%"
    )

    lines.append("")

    if trade["side"] == "LONG":

        lines.append(
            f"🛑 SL: "
            f"{sl:.8g} "
            f"(-{sl_pct:.2f}%)"
        )

        lines.append(
            f"🎯 TP: "
            f"{tp:.8g} "
            f"(+{tp_pct:.2f}%)"
        )

    else:

        lines.append(
            f"🛑 SL: "
            f"{sl:.8g} "
            f"(+{sl_pct:.2f}%)"
        )

        lines.append(
            f"🎯 TP: "
            f"{tp:.8g} "
            f"(-{tp_pct:.2f}%)"
        )

    lines.append(
        f"📐 RR: {rr:.2f}"
    )

    return "\n".join(lines)


# ============================================================
# DIAGNOSTICS REPORT
# ============================================================

def diagnostics_report():
    lines = []

    lines.append(
        "🔎 FILTER DIAGNOSTICS"
    )

    lines.append(
        f"Scanned: {DIAG['scanned']}"
    )

    lines.append("")

    lines.append("1H TREND")

    lines.append(
        f"🟢 LONG: "
        f"{DIAG['trend_long']}"
    )

    lines.append(
        f"🔴 SHORT: "
        f"{DIAG['trend_short']}"
    )

    lines.append(
        f"⚪ Neutral: "
        f"{DIAG['trend_neutral']}"
    )

    lines.append("")

    lines.append("15M S/R SETUP")

    lines.append(
        f"🟢 LONG: "
        f"{DIAG['setup_long']}"
    )

    lines.append(
        f"🔴 SHORT: "
        f"{DIAG['setup_short']}"
    )

    lines.append(
        f"❌ Failed: "
        f"{DIAG['setup_failed']}"
    )

    lines.append("")

    lines.append("5M STRUCTURE")

    lines.append(
        f"❌ Breakout: "
        f"{DIAG['five_break_failed']}"
    )

    lines.append(
        f"❌ Pullback: "
        f"{DIAG['five_pullback_failed']}"
    )

    lines.append(
        f"❌ Confirmation: "
        f"{DIAG['five_confirmation_failed']}"
    )

    lines.append("")

    lines.append("TP / SL")

    lines.append(
        f"❌ No valid S/R target: "
        f"{DIAG['sr_no_target']}"
    )

    lines.append(
        f"❌ TP <= SL distance: "
        f"{DIAG['tp_le_sl']}"
    )

    lines.append(
        f"❌ RR <= 1.0: "
        f"{DIAG['rr_failed']}"
    )

    lines.append(
        f"❌ SL < {MIN_SL_PCT:.2f}%: "
        f"{DIAG['sl_too_small']}"
    )

    lines.append(
        f"❌ SL > {MAX_SL_PCT:.2f}%: "
        f"{DIAG['sl_too_large']}"
    )

    lines.append("")

    lines.append(
        f"❌ Divergence: "
        f"{DIAG['divergence_failed']}"
    )

    lines.append(
        f"❌ Score < {MIN_SCORE}: "
        f"{DIAG['score_failed']}"
    )

    lines.append(
        f"⏳ Cooldown: "
        f"{DIAG['cooldown']}"
    )

    lines.append("")

    lines.append(
        f"🎯 Accepted: "
        f"{DIAG['signals']}"
    )

    lines.append(
        f"⚠️ Errors: "
        f"{DIAG['errors']}"
    )

    return "\n".join(lines)


# ============================================================
# FULL REPORT
# ============================================================

def build_report(
    signals,
    closed_trades,
):
    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {format_time()}"
    )

    lines.append(
        "⚡ Kraken Futures | "
        "5M CLOSED"
    )

    lines.append(
        "🧠 1H TREND + 15M S/R + "
        "5M BREAKOUT/PULLBACK"
    )

    lines.append("")

    lines.append(
        "💡 TP = VALID SUPPORT/RESISTANCE"
    )

    lines.append(
        "💡 TP distance must be > SL distance"
    )

    lines.append("")

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if signals:

        for signal in signals:

            lines.append("")

            lines.append(
                format_signal(signal)
            )

    else:

        lines.append(
            "None"
        )

    lines.append("")

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    if closed_trades:

        lines.append(
            "📕 RECENT CLOSED"
        )

        for item in closed_trades:

            trade = item["trade"]

            result = item["result"]

            pnl = item["pnl"]

            icon = (
                "🟢"
                if result == "WIN"
                else "🔴"
                if result == "LOSS"
                else "⚪"
            )

            lines.append(
                f"{icon} "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"{result} "
                f"{pnl:+.2f}% "
                f"({item['reason']})"
            )

        lines.append("")

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    open_trades = get_open_trades()

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/"
        f"{MAX_OPEN_TRADES})"
    )

    if open_trades:

        for trade in open_trades:

            lines.append("")

            lines.append(
                format_open_trade(
                    trade
                )
            )

    else:

        lines.append(
            "None"
        )

    lines.append("")

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    perf = get_performance()

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Open:   {len(open_trades)}"
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
        f"Win Rate: "
        f"{perf['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: "
        f"{perf['pnl']:+.2f}%"
    )

    lines.append("")

    lines.append(
        diagnostics_report()
    )

    lines.append("")

    lines.append(
        "⚙️ SETTINGS"
    )

    lines.append(
        f"Min RR: > {MIN_TP_RR:.2f}"
    )

    lines.append(
        f"SL Range: "
        f"{MIN_SL_PCT:.2f}% - "
        f"{MAX_SL_PCT:.2f}%"
    )

    lines.append(
        f"Max Open: "
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(
        "Real trading: DISABLED"
    )

    return "\n".join(lines)


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    market,
    open_symbols,
):
    symbol = market["symbol"]

    if symbol in open_symbols:
        return None

    if is_in_cooldown(symbol):
        return None

    DIAG["scanned"] += 1

    # --------------------------------------------------------
    # Load candles
    # --------------------------------------------------------

    candles_1h = fetch_ohlcv(
        symbol,
        TF_1H,
        CANDLE_LIMIT_1H,
    )

    candles_15m = fetch_ohlcv(
        symbol,
        TF_15M,
        CANDLE_LIMIT_15M,
    )

    candles_5m = fetch_ohlcv(
        symbol,
        TF_5M,
        CANDLE_LIMIT_5M,
    )

    if (
        len(candles_1h) < 70
        or len(candles_15m) < 60
        or len(candles_5m) < 40
    ):
        return None

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    rvol = calculate_rvol(
        candles_5m,
        RVOL_PERIOD,
    )

    if rvol < RVOL_NORMAL:
        return None

    # --------------------------------------------------------
    # 1H TREND
    # --------------------------------------------------------

    trend = get_trend(
        candles_1h
    )

    if trend["side"] == "NEUTRAL":
        return None

    side = trend["side"]

    # --------------------------------------------------------
    # 15M SETUP
    # --------------------------------------------------------

    setup = detect_15m_setup(
        candles_15m,
        side,
    )

    if setup is None:

        DIAG["setup_failed"] += 1

        return None

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    confirmation = confirm_5m_structure(
        candles_5m,
        side,
        setup,
    )

    if confirmation is None:
        return None

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    kraken_price = get_current_price(
        symbol
    )

    if kraken_price <= 0:
        return None

    entry = kraken_price

    # --------------------------------------------------------
    # Divergence
    # --------------------------------------------------------

    market_price = safe_float(
        market.get("price")
    )

    if market_price > 0:

        divergence = (
            abs(
                market_price
                - entry
            )
            / entry
            * 100
        )

        if (
            divergence
            > MAX_KRAKEN_ENTRY_DIVERGENCE_PCT
        ):

            DIAG[
                "divergence_failed"
            ] += 1

            return None

    # --------------------------------------------------------
    # Build SL + S/R TP
    # --------------------------------------------------------

    signal = build_signal(
        symbol,
        trend,
        setup,
        confirmation,
        candles_5m,
        candles_15m,
        candles_1h,
        entry,
    )

    if signal is None:
        return None

    signal["rvol"] = rvol

    DIAG["signals"] += 1

    return signal


# ============================================================
# MAIN SCANNER
# ============================================================

def scan():
    init_db()

    # --------------------------------------------------------
    # First process open trades.
    # --------------------------------------------------------

    closed_trades = (
        process_all_open_trades()
    )

    # --------------------------------------------------------
    # Refresh open trades AFTER exits.
    # --------------------------------------------------------

    open_trades = get_open_trades()

    open_symbols = {
        trade["symbol"]
        for trade in open_trades
    }

    available_slots = (
        MAX_OPEN_TRADES
        - len(open_trades)
    )

    if available_slots <= 0:

        print(
            build_report(
                [],
                closed_trades,
            )
        )

        return

    # --------------------------------------------------------
    # Top 100
    # --------------------------------------------------------

    markets = get_top_markets(
        TOP_N
    )

    if not markets:

        print(
            "No markets available."
        )

        return

    signals = []

    for market in markets:

        if len(signals) >= min(
            MAX_NEW_SIGNALS,
            available_slots,
        ):
            break

        try:

            signal = scan_market(
                market,
                open_symbols,
            )

            if signal is None:
                continue

            signals.append(signal)

        except Exception as exc:

            DIAG["errors"] += 1

            print(
                f"SCAN ERROR "
                f"{market.get('symbol')}: "
                f"{exc}"
            )

    # --------------------------------------------------------
    # Save signals
    # --------------------------------------------------------

    for signal in signals:

        insert_trade(
            symbol=signal["symbol"],
            side=signal["side"],
            setup=signal["setup"],
            entry=signal["entry"],
            sl=signal["sl"],
            tp=signal["tp"],
            rr=signal["rr"],
        )

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    print(
        build_report(
            signals,
            closed_trades,
        )
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except KeyboardInterrupt:

        print(
            "Scanner stopped."
        )

    except Exception as exc:

        DIAG["errors"] += 1

        print(
            f"FATAL ERROR: {exc}"
        )
