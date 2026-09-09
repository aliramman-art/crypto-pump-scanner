# ============================================================
# VOLUME-KHAT 100 v2.0
# KRAKEN FUTURES TOP 100 VOLUME + S/R + RVOL + PRICE ACTION
# ============================================================
#
# 1H  = Trend + Static S/R
# 15M = Dynamic S/R + RVOL + Setup
# 5M  = Entry Confirmation
#
# SETUPS:
#   - BREAKOUT
#   - HIGH VOLUME REJECTION
#
# FEATURES:
#   - TOP 100 USD PERPETUAL FUTURES
#   - CLOSED CANDLES ONLY
#   - RVOL
#   - Dynamic Support / Resistance
#   - Static Support / Resistance
#   - Breakout / Rejection
#   - 5M Confirmation
#   - Retest
#   - Structural SL
#   - Static S/R TP
#   - Minimum RR 1:2
#   - Score
#   - TOP 3
#   - SQLite trade tracking
#   - Telegram report
#   - Pipeline diagnostics
#   - ONE SHOT RUN FOR GITHUB ACTIONS
#
# REAL TRADING = DISABLED
# ============================================================

import os
import time
import math
import sqlite3
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

TOP_N = 100

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

LIMIT_1H = 180
LIMIT_15M = 180
LIMIT_5M = 120

RVOL_PERIOD = 20
RVOL_MIN = 2.0
RVOL_STRONG = 3.0

SWING_LEFT = 2
SWING_RIGHT = 2

DYNAMIC_MAX_DISTANCE = 0.010
DYNAMIC_STRONG_DISTANCE = 0.005

BREAKOUT_MIN_DISTANCE = 0.0015

MIN_BODY_BREAKOUT = 0.50
MIN_BODY_CONFIRM = 0.45

STATIC_CLUSTER_DISTANCE = 0.003

ATR_PERIOD = 14
SL_ATR_BUFFER = 0.20

MIN_RR = 2.0

MIN_SCORE = 9
COUNTER_TREND_MIN_SCORE = 12

MAX_SIGNALS = 5
TOP_SIGNALS = 3

DB_FILE = "volume_khat_100.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

REAL_TRADING = False

MAX_TELEGRAM_LENGTH = 3900

TIMEOUT = 20

BASE_FUTURES = "https://futures.kraken.com"
INSTRUMENTS_URL = BASE_FUTURES + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE_FUTURES + "/derivatives/api/v3/tickers"

CHART_URL = BASE_FUTURES + "/api/charts/v1/trade/{symbol}/{resolution}"


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Volume-Khat-100/2.0"
})


# ============================================================
# GLOBAL DIAGNOSTICS
# ============================================================

DIAG = {
    "instruments": 0,
    "tickers": 0,
    "perpetual": 0,
    "usd_perpetual": 0,
    "ranked": 0,
    "scanned": 0,
    "data_ok": 0,
    "bull": 0,
    "bear": 0,
    "neutral": 0,
    "dynamic_sr": 0,
    "rvol2": 0,
    "rvol3": 0,
    "breakout": 0,
    "rejection": 0,
    "confirmation": 0,
    "retest": 0,
    "static_sr": 0,
    "rr2": 0,
    "qualified": 0,
}


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def pct(a, b):
    if b == 0:
        return 0.0
    return (a - b) / b * 100.0


def safe_float(x, default=0.0):
    try:
        return float(x)
    except Exception:
        return default


def normalize_symbol(symbol):
    return str(symbol or "").strip().upper()


def clean_symbol(symbol):
    s = normalize_symbol(symbol)
    return s.replace("PF_", "").replace("PI_", "")


def is_perpetual_symbol(symbol):
    s = normalize_symbol(symbol)

    return (
        s.startswith("PF_")
        or s.startswith("PI_")
        or s.startswith("P_")
    )


def is_usd_symbol(symbol):
    s = normalize_symbol(symbol)

    return (
        "USD" in s
        or "USDT" in s
    )


# ============================================================
# API
# ============================================================

def api_get(url, params=None):
    try:
        r = SESSION.get(
            url,
            params=params,
            timeout=TIMEOUT
        )

        r.raise_for_status()

        return r.json()

    except Exception as e:
        print(f"API ERROR: {url} -> {e}")
        return {}


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    data = api_get(INSTRUMENTS_URL)

    if not data:
        return []

    instruments = data.get("instruments", [])

    if isinstance(instruments, dict):
        instruments = list(instruments.values())

    if not isinstance(instruments, list):
        return []

    DIAG["instruments"] = len(instruments)

    return instruments


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = api_get(TICKERS_URL)

    if not data:
        return []

    tickers = data.get("tickers", [])

    if isinstance(tickers, dict):
        tickers = list(tickers.values())

    if not isinstance(tickers, list):
        return []

    DIAG["tickers"] = len(tickers)

    return tickers


# ============================================================
# INSTRUMENT PERPETUAL CHECK
# ============================================================

def instrument_is_perpetual(inst):

    if not isinstance(inst, dict):
        return False

    symbol = normalize_symbol(inst.get("symbol"))

    text = " ".join(
        str(inst.get(k, ""))
        for k in [
            "type",
            "contractType",
            "category",
            "tags",
            "tradeable",
            "status"
        ]
    ).lower()

    if "perpetual" in text:
        return True

    if is_perpetual_symbol(symbol):
        return True

    return False


# ============================================================
# USD CHECK
# ============================================================

def instrument_is_usd(inst):

    if not isinstance(inst, dict):
        return False

    symbol = normalize_symbol(inst.get("symbol"))

    fields = [
        "quoteCurrency",
        "quoteAsset",
        "settleCurrency",
        "settlementCurrency",
        "underlying",
        "symbol"
    ]

    values = [
        str(inst.get(k, "")).upper()
        for k in fields
    ]

    text = " ".join(values)

    if "USD" in text or "USDT" in text:
        return True

    return is_usd_symbol(symbol)


# ============================================================
# VOLUME EXTRACTION
# ============================================================

def ticker_volume(ticker):

    keys = [
        "vol24h",
        "volume24h",
        "volumeQuote",
        "quoteVolume",
        "turnover24h",
        "volume"
    ]

    for k in keys:
        value = safe_float(ticker.get(k), 0)

        if value > 0:
            return value

    return 0.0


# ============================================================
# TOP 100 MARKETS
# ============================================================

def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    instrument_map = {}

    for inst in instruments:

        symbol = normalize_symbol(inst.get("symbol"))

        if symbol:
            instrument_map[symbol] = inst

    candidates = []

    for ticker in tickers:

        symbol = normalize_symbol(ticker.get("symbol"))

        if not symbol:
            continue

        inst = instrument_map.get(symbol)

        perpetual = False
        usd = False

        if inst:
            perpetual = instrument_is_perpetual(inst)
            usd = instrument_is_usd(inst)

        # Fallback for Kraken symbols
        if not perpetual:
            perpetual = is_perpetual_symbol(symbol)

        if not usd:
            usd = is_usd_symbol(symbol)

        if not perpetual:
            continue

        DIAG["perpetual"] += 1

        if not usd:
            continue

        DIAG["usd_perpetual"] += 1

        volume = ticker_volume(ticker)

        if volume <= 0:
            continue

        last_price = safe_float(
            ticker.get("last"),
            safe_float(ticker.get("markPrice"), 0)
        )

        candidates.append({
            "symbol": symbol,
            "volume": volume,
            "price": last_price
        })

    candidates.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    ranked = candidates[:TOP_N]

    DIAG["ranked"] = len(ranked)

    print("=" * 60)
    print("KRAKEN MARKET DISCOVERY")
    print("=" * 60)
    print(f"Instruments       : {DIAG['instruments']}")
    print(f"Tickers           : {DIAG['tickers']}")
    print(f"Perpetual         : {DIAG['perpetual']}")
    print(f"USD Perpetual     : {DIAG['usd_perpetual']}")
    print(f"TOP {TOP_N} Ranked      : {DIAG['ranked']}")

    if ranked:
        print("\nTOP MARKETS:")
        for i, m in enumerate(ranked[:10], 1):
            print(
                f"{i:02d}. "
                f"{m['symbol']} "
                f"VOL={m['volume']:.2f}"
            )

    print("=" * 60)

    return ranked


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, limit):

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = api_get(url)

    if not data:
        return None

    candles = data.get("candles", [])

    if not isinstance(candles, list):
        return None

    rows = []

    for c in candles:

        if not isinstance(c, dict):
            continue

        t = (
            c.get("time")
            or c.get("timestamp")
            or c.get("t")
        )

        o = c.get("open", c.get("o"))
        h = c.get("high", c.get("h"))
        l = c.get("low", c.get("l"))
        close = c.get("close", c.get("c"))
        v = c.get("volume", c.get("v"))

        try:
            rows.append({
                "time": float(t),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(close),
                "volume": float(v)
            })
        except Exception:
            continue

    if len(rows) < 30:
        return None

    df = pd.DataFrame(rows)

    df = df.sort_values("time").drop_duplicates(
        subset=["time"]
    )

    # Closed candles only
    if len(df) > 2:
        df = df.iloc[:-1].copy()

    if len(df) > limit:
        df = df.iloc[-limit:].copy()

    return df.reset_index(drop=True)


# ============================================================
# ATR
# ============================================================

def atr(df, period=14):

    if len(df) < period + 2:
        return 0.0

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)

    value = tr.rolling(period).mean().iloc[-1]

    return safe_float(value)


# ============================================================
# EMA TREND
# ============================================================

def trend_1h(df):

    if len(df) < 50:
        return "NEUTRAL"

    ema20 = df["close"].ewm(
        span=20,
        adjust=False
    ).mean().iloc[-1]

    ema50 = df["close"].ewm(
        span=50,
        adjust=False
    ).mean().iloc[-1]

    close = df["close"].iloc[-1]

    if close > ema20 > ema50:
        return "BULL"

    if close < ema20 < ema50:
        return "BEAR"

    return "NEUTRAL"


# ============================================================
# SWINGS
# ============================================================

def find_swings(df):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    left = SWING_LEFT
    right = SWING_RIGHT

    for i in range(left, len(df) - right):

        if h[i] > max(
            h[i-left:i]
        ) and h[i] > min(
            h[i+1:i+right+1]
        ):
            highs.append(
                (i, float(h[i]))
            )

        if l[i] < min(
            l[i-left:i]
        ) and l[i] < max(
            l[i+1:i+right+1]
        ):
            lows.append(
                (i, float(l[i]))
            )

    return highs, lows


# ============================================================
# BETTER SWING DETECTION
# ============================================================

def detect_swings(df):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    for i in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT
    ):

        left_h = h[
            i-SWING_LEFT:i
        ]

        right_h = h[
            i+1:i+SWING_RIGHT+1
        ]

        left_l = l[
            i-SWING_LEFT:i
        ]

        right_l = l[
            i+1:i+SWING_RIGHT+1
        ]

        if h[i] > max(left_h) and h[i] > max(right_h):
            highs.append(
                (i, float(h[i]))
            )

        if l[i] < min(left_l) and l[i] < min(right_l):
            lows.append(
                (i, float(l[i]))
            )

    return highs, lows


# ============================================================
# LINE PROJECTION
# ============================================================

def projected_level(points, current_index):

    if len(points) < 2:
        return None

    p1 = points[-2]
    p2 = points[-1]

    x1, y1 = p1
    x2, y2 = p2

    if x2 == x1:
        return y2

    slope = (y2 - y1) / (x2 - x1)

    return y2 + slope * (
        current_index - x2
    )


# ============================================================
# DYNAMIC S/R
# ============================================================

def dynamic_sr(df):

    highs, lows = detect_swings(df)

    idx = len(df) - 1

    resistance = projected_level(
        highs,
        idx
    )

    support = projected_level(
        lows,
        idx
    )

    close = float(df["close"].iloc[-1])

    result = {
        "support": support,
        "resistance": resistance,
        "support_touches": len(lows),
        "resistance_touches": len(highs)
    }

    if support is not None:
        if abs(close - support) / close <= DYNAMIC_MAX_DISTANCE:
            result["support_near"] = True
        else:
            result["support_near"] = False
    else:
        result["support_near"] = False

    if resistance is not None:
        if abs(close - resistance) / close <= DYNAMIC_MAX_DISTANCE:
            result["resistance_near"] = True
        else:
            result["resistance_near"] = False
    else:
        result["resistance_near"] = False

    return result


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(df):

    if len(df) < RVOL_PERIOD + 2:
        return 0.0

    current_volume = float(
        df["volume"].iloc[-1]
    )

    baseline = float(
        df["volume"]
        .iloc[-RVOL_PERIOD-1:-1]
        .mean()
    )

    if baseline <= 0:
        return 0.0

    return current_volume / baseline


# ============================================================
# CANDLE INFORMATION
# ============================================================

def candle_info(row):

    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    rng = max(h - l, 1e-12)
    body = abs(c - o)

    body_ratio = body / rng

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    bullish = c > o
    bearish = c < o

    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "range": rng,
        "body": body,
        "body_ratio": body_ratio,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "bullish": bullish,
        "bearish": bearish
    }


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(df, sr):

    if len(df) < 3:
        return None

    current = candle_info(df.iloc[-1])
    previous = candle_info(df.iloc[-2])

    close = current["close"]

    resistance = sr.get("resistance")
    support = sr.get("support")

    # LONG BREAKOUT
    if resistance is not None:

        distance = (
            close - resistance
        ) / resistance

        if (
            close > resistance
            and previous["close"] <= resistance
            and distance >= BREAKOUT_MIN_DISTANCE
            and current["bullish"]
            and current["body_ratio"] >= MIN_BODY_BREAKOUT
        ):
            return {
                "type": "BREAKOUT",
                "side": "LONG",
                "level": resistance
            }

    # SHORT BREAKOUT
    if support is not None:

        distance = (
            support - close
        ) / support

        if (
            close < support
            and previous["close"] >= support
            and distance >= BREAKOUT_MIN_DISTANCE
            and current["bearish"]
            and current["body_ratio"] >= MIN_BODY_BREAKOUT
        ):
            return {
                "type": "BREAKOUT",
                "side": "SHORT",
                "level": support
            }

    return None


# ============================================================
# REJECTION
# ============================================================

def detect_rejection(df, sr, rvol):

    if rvol < RVOL_MIN:
        return None

    c = candle_info(df.iloc[-1])

    close = c["close"]
    body = max(c["body"], 1e-12)

    resistance = sr.get("resistance")
    support = sr.get("support")

    # RESISTANCE REJECTION -> SHORT
    if resistance is not None:

        distance = abs(
            close - resistance
        ) / close

        if (
            distance <= DYNAMIC_MAX_DISTANCE
            and c["upper_wick"] >= body * 1.2
            and close < resistance
        ):
            return {
                "type": "REJECTION",
                "side": "SHORT",
                "level": resistance
            }

    # SUPPORT REJECTION -> LONG
    if support is not None:

        distance = abs(
            close - support
        ) / close

        if (
            distance <= DYNAMIC_MAX_DISTANCE
            and c["lower_wick"] >= body * 1.2
            and close > support
        ):
            return {
                "type": "REJECTION",
                "side": "LONG",
                "level": support
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(df, side):

    if len(df) < 3:
        return False

    c = candle_info(df.iloc[-1])

    if c["body_ratio"] < MIN_BODY_CONFIRM:
        return False

    if side == "LONG":
        return c["bullish"]

    if side == "SHORT":
        return c["bearish"]

    return False


# ============================================================
# RETEST
# ============================================================

def detect_retest(df, level, side):

    if level is None or len(df) < 4:
        return False

    recent = df.iloc[-3:]

    tolerance = 0.004

    for _, row in recent.iterrows():

        high = float(row["high"])
        low = float(row["low"])
        close = float(row["close"])

        if side == "LONG":

            touched = (
                abs(low - level) / level
                <= tolerance
            )

            held = close > level

            if touched and held:
                return True

        elif side == "SHORT":

            touched = (
                abs(high - level) / level
                <= tolerance
            )

            held = close < level

            if touched and held:
                return True

    return False


# ============================================================
# STATIC S/R
# ============================================================

def cluster_levels(levels):

    if not levels:
        return []

    levels = sorted(
        [x for x in levels if x > 0]
    )

    clusters = []

    for level in levels:

        if not clusters:
            clusters.append(
                [level]
            )
            continue

        center = np.mean(
            clusters[-1]
        )

        if (
            abs(level - center)
            / center
            <= STATIC_CLUSTER_DISTANCE
        ):
            clusters[-1].append(level)
        else:
            clusters.append(
                [level]
            )

    return [
        float(np.mean(c))
        for c in clusters
    ]


def static_sr(df_1h, df_15m):

    h1_highs, h1_lows = detect_swings(
        df_1h
    )

    m15_highs, m15_lows = detect_swings(
        df_15m
    )

    resistances = (
        [x[1] for x in h1_highs]
        + [x[1] for x in m15_highs]
    )

    supports = (
        [x[1] for x in h1_lows]
        + [x[1] for x in m15_lows]
    )

    resistances = cluster_levels(
        resistances
    )

    supports = cluster_levels(
        supports
    )

    price = float(
        df_15m["close"].iloc[-1]
    )

    supports_below = [
        x for x in supports
        if x < price
    ]

    resistances_above = [
        x for x in resistances
        if x > price
    ]

    supports_below.sort(
        reverse=True
    )

    resistances_above.sort()

    return {
        "supports": supports_below,
        "resistances": resistances_above
    }


# ============================================================
# SL / TP
# ============================================================

def calculate_trade(
    side,
    entry,
    df_15m,
    static_levels,
    dynamic_level
):

    atr_value = atr(
        df_15m,
        ATR_PERIOD
    )

    if atr_value <= 0:
        return None

    buffer = (
        atr_value *
        SL_ATR_BUFFER
    )

    supports = static_levels[
        "supports"
    ]

    resistances = static_levels[
        "resistances"
    ]

    if side == "LONG":

        # SL based on nearest static support
        if supports:
            sl_base = supports[0]
        else:
            sl_base = entry - atr_value

        sl = sl_base - buffer

        if sl >= entry:
            sl = entry - atr_value

        risk = entry - sl

        possible_tp = [
            x for x in resistances
            if x > entry
        ]

        # choose nearest level satisfying RR
        tp = None

        for level in possible_tp:
            rr = (
                level - entry
            ) / risk

            if rr >= MIN_RR:
                tp = level
                break

        if tp is None:
            return None

    else:

        if resistances:
            sl_base = resistances[0]
        else:
            sl_base = entry + atr_value

        sl = sl_base + buffer

        if sl <= entry:
            sl = entry + atr_value

        risk = sl - entry

        possible_tp = [
            x for x in supports
            if x < entry
        ]

        tp = None

        for level in possible_tp:

            rr = (
                entry - level
            ) / risk

            if rr >= MIN_RR:
                tp = level
                break

        if tp is None:
            return None

    if risk <= 0:
        return None

    if side == "LONG":
        rr = (tp - entry) / risk
    else:
        rr = (entry - tp) / risk

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "atr": atr_value
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    setup,
    rvol,
    dynamic,
    retest,
    confirmation,
    trend,
    rr,
    side
):

    score = 0

    # RVOL
    if rvol >= RVOL_MIN:
        score += 2

    if rvol >= RVOL_STRONG:
        score += 1

    # Dynamic S/R
    touches = max(
        dynamic.get(
            "support_touches",
            0
        ),
        dynamic.get(
            "resistance_touches",
            0
        )
    )

    if touches >= 2:
        score += 2

    # Proximity
    level = setup.get("level")

    if level:

        distance = abs(
            dynamic_price(setup, level)
        )

    # Setup
    score += 2

    # Retest
    if retest:
        score += 2

    # Confirmation
    if confirmation:
        score += 2

    # Trend
    if (
        (side == "LONG" and trend == "BULL")
        or
        (side == "SHORT" and trend == "BEAR")
    ):
        score += 2

    # RR
    if rr >= 3:
        score += 2

    return score


def dynamic_price(setup, level):

    # helper used only for scoring
    return 0.0


# ============================================================
# SQLITE
# ============================================================

def init_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,
            score INTEGER,
            rvol REAL,
            trend TEXT,
            status TEXT,
            result TEXT,
            pnl REAL,
            created_at TEXT,
            closed_at TEXT
        )
    """)

    conn.commit()
    conn.close()


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(signal):

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO trades (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            rvol,
            trend,
            status,
            result,
            pnl,
            created_at,
            closed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        signal["symbol"],
        signal["side"],
        signal["setup"],
        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["rr"],
        signal["score"],
        signal["rvol"],
        signal["trend"],
        "OPEN",
        "",
        0.0,
        now_utc().isoformat(),
        ""
    ))

    conn.commit()
    conn.close()


# ============================================================
# TRADE STATUS UPDATE
# ============================================================

def update_open_trades(price_map):

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp
        FROM trades
        WHERE status = 'OPEN'
    """)

    rows = cur.fetchall()

    for row in rows:

        (
            trade_id,
            symbol,
            side,
            entry,
            sl,
            tp
        ) = row

        price = price_map.get(
            symbol
        )

        if not price:
            continue

        result = None
        pnl = 0.0

        if side == "LONG":

            pnl = (
                price - entry
            ) / entry * 100

            if price >= tp:
                result = "WIN"

            elif price <= sl:
                result = "LOSS"

        else:

            pnl = (
                entry - price
            ) / entry * 100

            if price <= tp:
                result = "WIN"

            elif price >= sl:
                result = "LOSS"

        if result:

            cur.execute("""
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = ?,
                    pnl = ?,
                    closed_at = ?
                WHERE id = ?
            """, (
                result,
                pnl,
                now_utc().isoformat(),
                trade_id
            ))

    conn.commit()
    conn.close()


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN result='WIN'
                    THEN 1
                    ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN result='LOSS'
                    THEN 1
                    ELSE 0
                END
            ),
            COALESCE(SUM(pnl),0),
            SUM(
                CASE
                    WHEN status='OPEN'
                    THEN 1
                    ELSE 0
                END
            )
        FROM trades
    """)

    row = cur.fetchone()

    conn.close()

    total = row[0] or 0
    wins = row[1] or 0
    losses = row[2] or 0
    pnl = row[3] or 0
    open_count = row[4] or 0

    closed = wins + losses

    win_rate = (
        wins / closed * 100
        if closed
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "open": open_count,
        "win_rate": win_rate
    }


# ============================================================
# PROCESS ONE MARKET
# ============================================================

def process_market(market):

    symbol = market["symbol"]

    try:

        df1h = get_candles(
            symbol,
            TF_1H,
            LIMIT_1H
        )

        df15 = get_candles(
            symbol,
            TF_15M,
            LIMIT_15M
        )

        df5 = get_candles(
            symbol,
            TF_5M,
            LIMIT_5M
        )

        if (
            df1h is None
            or df15 is None
            or df5 is None
        ):
            return None

        DIAG["data_ok"] += 1

        trend = trend_1h(
            df1h
        )

        if trend == "BULL":
            DIAG["bull"] += 1
        elif trend == "BEAR":
            DIAG["bear"] += 1
        else:
            DIAG["neutral"] += 1

        dynamic = dynamic_sr(
            df15
        )

        if (
            dynamic.get("support")
            is not None
            or
            dynamic.get("resistance")
            is not None
        ):
            DIAG["dynamic_sr"] += 1

        rvol = calculate_rvol(
            df15
        )

        if rvol >= RVOL_MIN:
            DIAG["rvol2"] += 1

        if rvol >= RVOL_STRONG:
            DIAG["rvol3"] += 1

        breakout = detect_breakout(
            df15,
            dynamic
        )

        rejection = detect_rejection(
            df15,
            dynamic,
            rvol
        )

        setup = breakout or rejection

        if breakout:
            DIAG["breakout"] += 1

        if rejection:
            DIAG["rejection"] += 1

        if not setup:
            return None

        side = setup["side"]

        confirmation = confirm_5m(
            df5,
            side
        )

        if confirmation:
            DIAG["confirmation"] += 1

        if not confirmation:
            return None

        retest = False

        if setup["type"] == "BREAKOUT":

            retest = detect_retest(
                df5,
                setup["level"],
                side
            )

            if retest:
                DIAG["retest"] += 1

        static = static_sr(
            df1h,
            df15
        )

        if (
            static["supports"]
            or
            static["resistances"]
        ):
            DIAG["static_sr"] += 1

        entry = float(
            df5["close"].iloc[-1]
        )

        trade = calculate_trade(
            side,
            entry,
            df15,
            static,
            setup["level"]
        )

        if trade is None:
            return None

        if trade["rr"] >= MIN_RR:
            DIAG["rr2"] += 1

        # ====================================================
        # SCORE
        # ====================================================

        score = 0

        if rvol >= RVOL_MIN:
            score += 2

        if rvol >= RVOL_STRONG:
            score += 1

        touches = max(
            dynamic.get(
                "support_touches",
                0
            ),
            dynamic.get(
                "resistance_touches",
                0
            )
        )

        if touches >= 2:
            score += 2

        # Dynamic proximity
        level = setup.get("level")

        if level:

            distance = abs(
                entry - level
            ) / entry

            if distance <= DYNAMIC_STRONG_DISTANCE:
                score += 2

            elif distance <= DYNAMIC_MAX_DISTANCE:
                score += 1

        # Setup
        score += 2

        # Retest
        if retest:
            score += 2

        # 5M confirmation
        if confirmation:
            score += 2

        # 1H trend
        trend_aligned = (
            (
                side == "LONG"
                and trend == "BULL"
            )
            or
            (
                side == "SHORT"
                and trend == "BEAR"
            )
        )

        if trend_aligned:
            score += 2

        # RR
        if trade["rr"] >= 3:
            score += 2

        # Counter trend protection
        if (
            not trend_aligned
            and score < COUNTER_TREND_MIN_SCORE
        ):
            return None

        if score < MIN_SCORE:
            return None

        DIAG["qualified"] += 1

        return {
            "symbol": symbol,
            "display": clean_symbol(symbol),
            "side": side,
            "setup": setup["type"],
            "entry": trade["entry"],
            "sl": trade["sl"],
            "tp": trade["tp"],
            "rr": trade["rr"],
            "atr": trade["atr"],
            "score": score,
            "rvol": rvol,
            "trend": trend,
            "retest": retest,
            "confirmation": confirmation
        }

    except Exception as e:

        print(
            f"ERROR {symbol}: {e}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):
        print(
            "Telegram credentials missing."
        )
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }

    try:

        r = SESSION.post(
            url,
            json=payload,
            timeout=TIMEOUT
        )

        print(
            "Telegram:",
            r.status_code
        )

        return r.ok

    except Exception as e:

        print(
            "Telegram error:",
            e
        )

        return False


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal,
    rank
):

    if rank == 1:
        medal = "🥇"
    elif rank == 2:
        medal = "🥈"
    elif rank == 3:
        medal = "🥉"
    else:
        medal = "🎯"

    side_icon = (
        "🟢"
        if signal["side"] == "LONG"
        else "🔴"
    )

    retest = (
        "✅"
        if signal["retest"]
        else "❌"
    )

    return (
        f"{medal} TOP {rank}\n"
        f"🔥 {signal['display']}\n"
        f"{side_icon} {signal['side']}\n"
        f"📌 Setup: {signal['setup']}\n"
        f"⭐ Score: {signal['score']}\n"
        f"📊 RVOL: {signal['rvol']:.2f}x\n"
        f"📈 1H Trend: {signal['trend']}\n"
        f"🔁 Retest: {retest}\n"
        f"🎯 RR: 1:{signal['rr']:.2f}\n"
        f"💰 Entry: {signal['entry']:.8g}\n"
        f"🛑 SL: {signal['sl']:.8g}\n"
        f"🎯 TP: {signal['tp']:.8g}\n"
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    signals,
    scan_seconds
):

    stats = get_stats()

    signals = sorted(
        signals,
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    top3 = signals[
        :TOP_SIGNALS
    ]

    lines = []

    lines.append(
        "🤖 VOLUME-KHAT 100"
    )

    lines.append(
        "📡 VOLUME-KHAT 100 v2.0"
    )

    lines.append(
        f"🕐 {now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        "⏱ TOP 100 | 5M CLOSED"
    )

    lines.append(
        "💻 Real trading: DISABLED"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # TOP 3
    lines.append(
        "🏆 TOP 3 SIGNALS"
    )

    lines.append("")

    if top3:

        for i, signal in enumerate(
            top3,
            1
        ):

            lines.append(
                format_signal(
                    signal,
                    i
                )
            )

            if i < len(top3):
                lines.append(
                    "━━━━━━━━━━━━━━━━━━"
                )

    else:

        lines.append(
            "❌ TOP 3: NONE"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Pipeline
    lines.append(
        "📊 SCAN PIPELINES"
    )

    lines.append(
        f"Markets discovered: {DIAG['ranked']}"
    )

    lines.append(
        f"Scanned: {DIAG['scanned']}"
    )

    lines.append(
        f"Data OK: {DIAG['data_ok']}"
    )

    lines.append(
        "1H Trend:"
    )

    lines.append(
        f"🟢 Bull: {DIAG['bull']}"
    )

    lines.append(
        f"🔴 Bear: {DIAG['bear']}"
    )

    lines.append(
        f"⚪ Neutral: {DIAG['neutral']}"
    )

    lines.append(
        f"Dynamic S/R: {DIAG['dynamic_sr']}"
    )

    lines.append(
        f"RVOL ≥ 2: {DIAG['rvol2']}"
    )

    lines.append(
        f"RVOL ≥ 3: {DIAG['rvol3']}"
    )

    lines.append(
        f"Breakout: {DIAG['breakout']}"
    )

    lines.append(
        f"Rejection: {DIAG['rejection']}"
    )

    lines.append(
        f"5M Confirmation: {DIAG['confirmation']}"
    )

    lines.append(
        f"Retest: {DIAG['retest']}"
    )

    lines.append(
        f"Static S/R: {DIAG['static_sr']}"
    )

    lines.append(
        f"RR ≥ 1:2: {DIAG['rr2']}"
    )

    lines.append(
        f"🎯 Qualified: {DIAG['qualified']}"
    )

    lines.append(
        f"⏱ Scan time: {scan_seconds:.1f}s"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Stats
    lines.append(
        "📈 TRACKING STATS"
    )

    lines.append(
        f"Open: {stats['open']}"
    )

    lines.append(
        f"Closed: {stats['wins'] + stats['losses']}"
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
        f"Net PnL: {stats['pnl']:.2f}%"
    )

    if not signals:

        lines.append(
            "❌ No valid setup"
        )

    text = "\n".join(lines)

    if len(text) > MAX_TELEGRAM_LENGTH:

        text = text[
            :MAX_TELEGRAM_LENGTH - 20
        ]

        text += "\n...TRUNCATED"

    return text


# ============================================================
# MAIN
# ============================================================

def main():

    started = time.time()

    print()
    print("=" * 70)
    print("VOLUME-KHAT 100 v2.0")
    print("KRAKEN FUTURES")
    print("=" * 70)

    init_db()

    markets = get_top_markets()

    if not markets:

        report = (
            "🤖 VOLUME-KHAT 100\n"
            "❌ MARKET DISCOVERY FAILED\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"Instruments: {DIAG['instruments']}\n"
            f"Tickers: {DIAG['tickers']}\n"
            f"Perpetual: {DIAG['perpetual']}\n"
            f"USD Perpetual: {DIAG['usd_perpetual']}\n"
            f"Ranked: {DIAG['ranked']}\n"
        )

        print(report)

        telegram_send(report)

        return

    DIAG["scanned"] = len(markets)

    signals = []

    print(
        f"\nScanning {len(markets)} markets..."
    )

    # ========================================================
    # PARALLEL SCAN
    # ========================================================

    workers = min(
        8,
        max(2, len(markets))
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as executor:

        futures = {
            executor.submit(
                process_market,
                market
            ): market
            for market in markets
        }

        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

                if result:
                    signals.append(
                        result
                    )

            except Exception as e:

                market = futures[
                    future
                ]

                print(
                    "Worker error:",
                    market["symbol"],
                    e
                )

    # ========================================================
    # RANK
    # ========================================================

    signals.sort(
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    # Save TOP 5 only
    for signal in signals[
        :MAX_SIGNALS
    ]:

        save_signal(
            signal
        )

    # ========================================================
    # UPDATE OLD OPEN TRADES
    # ========================================================

    price_map = {}

    for market in markets:

        price = market.get(
            "price",
            0
        )

        if price > 0:
            price_map[
                market["symbol"]
            ] = price

    update_open_trades(
        price_map
    )

    # ========================================================
    # REPORT
    # ========================================================

    elapsed = (
        time.time() - started
    )

    report = build_report(
        signals,
        elapsed
    )

    print()
    print(report)
    print()

    telegram_send(
        report
    )

    print(
        "=" * 70
    )

    print(
        "SCAN FINISHED"
    )

    print(
        f"Markets: {len(markets)}"
    )

    print(
        f"Qualified: {len(signals)}"
    )

    print(
        f"TOP 3: {min(3, len(signals))}"
    )

    print(
        f"Time: {elapsed:.2f}s"
    )

    print(
        "=" * 70
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
