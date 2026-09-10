# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v2.7 FIXED
# ============================================================
#
# 1H  = Trend + Static Support/Resistance
# 15M = Dynamic Support/Resistance + RVOL + Setup
# 5M  = Entry Confirmation
#
# SETUPS:
#   BREAKOUT
#   HIGH-VOLUME REJECTION
#
# RISK:
#   Structural SL
#   Static S/R TP
#   Minimum RR 1:2
#   Maximum SL 0.80%
#
# TRADE MANAGEMENT:
#   MAX 3 OPEN TOTAL
#   0 -> up to 3 new
#   1 -> up to 2
#   2 -> up to 1
#   3 -> 0
#
# IMPORTANT FIX:
#   SL/TP is checked between reports using CLOSED 5M candle:
#
#   LONG:
#       Low  <= SL -> LOSS
#       High >= TP -> WIN
#
#   SHORT:
#       High >= SL -> LOSS
#       Low  <= TP -> WIN
#
#   If both SL and TP are touched in the same candle:
#       SL is considered first -> LOSS
#
# SYMBOL FIX:
#   Kraken raw symbols such as PF_DOTUSD are preserved
#   for API candle requests.
#   Normalized symbols are used only for matching.
#
# PNL FIX:
#   Closed PNL is calculated from actual SL/TP exit price.
#
# REPORT FIX:
#   Trades closed in the current scan appear in the same report.
#
# PAPER TRACKING ONLY
# REAL TRADING DISABLED
#
# GitHub Actions:
#   one scan / execution
#   no while True
# ============================================================

import os
import time
import math
import sqlite3
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

TOP_N = 100

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

OHLCV_LIMIT = 150

RVOL_PERIOD = 20
RVOL_ABNORMAL = 2.0
RVOL_STRONG = 3.0

MIN_RR = 2.0
MAX_SL_PCT = 0.0080
MIN_SIGNAL_SCORE = 9

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

SWING_LEFT = 2
SWING_RIGHT = 2

ATR_PERIOD = 14
ATR_BUFFER_MULT = 0.15

COOLDOWN_CANDLES = 3

DB_FILE = "volume_khat_100.db"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

REAL_TRADING = False

REQUEST_TIMEOUT = 20
MAX_WORKERS = 10

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Volume-Khat-100/2.7-Fixed"
})


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def format_utc():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "").strip()

        x = float(value)

        if not math.isfinite(x):
            return default

        return x

    except Exception:
        return default


def normalize_symbol(symbol):
    """
    Used ONLY for comparison/matching.

    IMPORTANT:
    This must NEVER be used as the Kraken API candle symbol.

    Example:
        PF_DOTUSD -> PFDOTUSD
    """

    if symbol is None:
        return ""

    return str(symbol).upper().replace("/", "").replace("-", "").replace("_", "")


def is_usd_perpetual_symbol(symbol):
    s = normalize_symbol(symbol)

    return (
        s.endswith("USD")
        or s.endswith("USDT")
    )


def is_explicitly_non_perpetual(item):
    if not isinstance(item, dict):
        return False

    keys = [
        "type",
        "contractType",
        "contract_type",
        "instrumentType",
        "instrument_type",
        "maturity",
        "expiration",
    ]

    values = []

    for key in keys:
        value = item.get(key)

        if value is not None:
            values.append(str(value).lower())

    text = " ".join(values)

    if "perpetual" in text:
        return False

    bad_words = [
        "future",
        "futures",
        "fixedmaturity",
        "fixed_maturity",
        "inverse",
    ]

    return any(word in text for word in bad_words)


# ============================================================
# HTTP
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
        print(f"[HTTP ERROR] {url} -> {e}")
        return None


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_instruments():

    url = f"{BASE}/derivatives/api/v3/instruments"

    data = http_get(url)

    if not data:
        return []

    rows = []

    if isinstance(data, dict):

        for key in ["instruments", "data", "result"]:

            value = data.get(key)

            if isinstance(value, list):
                rows = value
                break

            if isinstance(value, dict):
                rows = list(value.values())
                break

    elif isinstance(data, list):
        rows = data

    result = []

    for item in rows:

        if not isinstance(item, dict):
            continue

        raw_symbol = (
            item.get("symbol")
            or item.get("instrument")
            or item.get("ticker")
            or item.get("code")
        )

        if not raw_symbol:
            continue

        raw_symbol = str(raw_symbol).strip()

        if item.get("tradeable") is False:
            continue

        if item.get("tradable") is False:
            continue

        if is_explicitly_non_perpetual(item):
            continue

        if not is_usd_perpetual_symbol(raw_symbol):
            continue

        result.append({
            "symbol": raw_symbol,
            "norm_symbol": normalize_symbol(raw_symbol),
            "raw": item
        })

    return result


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    url = f"{BASE}/derivatives/api/v3/tickers"

    data = http_get(url)

    if not data:
        return []

    rows = []

    if isinstance(data, dict):

        for key in ["tickers", "data", "result"]:

            value = data.get(key)

            if isinstance(value, list):
                rows = value
                break

            if isinstance(value, dict):
                rows = []

                for k, v in value.items():

                    if isinstance(v, dict):

                        item = dict(v)

                        if not item.get("symbol"):
                            item["symbol"] = k

                        rows.append(item)

                break

    elif isinstance(data, list):
        rows = data

    return rows


# ============================================================
# TOP MARKET DISCOVERY
# ============================================================

def discover_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    if not tickers:
        print("[DISCOVERY] No tickers received")
        return []

    instrument_map = {}

    for inst in instruments:

        instrument_map[
            inst["norm_symbol"]
        ] = inst["symbol"]

    markets = []

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        raw_symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
            or ticker.get("ticker")
            or ticker.get("code")
        )

        if not raw_symbol:
            continue

        raw_symbol = str(raw_symbol).strip()

        norm = normalize_symbol(raw_symbol)

        if not is_usd_perpetual_symbol(raw_symbol):
            continue

        # ----------------------------------------------------
        # Prefer exact raw symbol from instruments
        # ----------------------------------------------------

        api_symbol = instrument_map.get(norm)

        if not api_symbol:
            api_symbol = raw_symbol

        volume = (
            safe_float(ticker.get("volume24h"))
            or safe_float(ticker.get("volume"))
            or safe_float(ticker.get("vol24h"))
            or safe_float(ticker.get("volume_24h"))
            or 0.0
        )

        last = (
            safe_float(ticker.get("last"))
            or safe_float(ticker.get("lastPrice"))
            or safe_float(ticker.get("markPrice"))
            or safe_float(ticker.get("price"))
        )

        markets.append({
            "symbol": api_symbol,
            "norm_symbol": normalize_symbol(api_symbol),
            "volume": volume,
            "last": last,
            "ticker": ticker
        })

    # --------------------------------------------------------
    # Remove duplicates
    # --------------------------------------------------------

    unique = {}

    for market in markets:

        key = market["norm_symbol"]

        if key not in unique:
            unique[key] = market

        else:
            if market["volume"] > unique[key]["volume"]:
                unique[key] = market

    markets = list(unique.values())

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    markets = markets[:TOP_N]

    print(
        f"[DISCOVERY] Markets found: {len(markets)}"
    )

    for m in markets[:5]:

        print(
            f"  {m['symbol']} "
            f"volume={m['volume']:.2f} "
            f"last={m['last']}"
        )

    return markets


# ============================================================
# LIVE PRICE MAP
# ============================================================

def get_live_price_map(symbols=None):

    tickers = get_tickers()

    if not tickers:
        return {}

    wanted = None

    if symbols is not None:

        wanted = {
            normalize_symbol(s)
            for s in symbols
        }

    price_map = {}

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        raw_symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
            or ticker.get("ticker")
            or ticker.get("code")
        )

        if not raw_symbol:
            continue

        norm = normalize_symbol(raw_symbol)

        if wanted is not None and norm not in wanted:
            continue

        price = (
            safe_float(ticker.get("last"))
            or safe_float(ticker.get("lastPrice"))
            or safe_float(ticker.get("markPrice"))
            or safe_float(ticker.get("price"))
        )

        if price is not None:
            price_map[norm] = price

    return price_map


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, limit=OHLCV_LIMIT):

    # IMPORTANT:
    # symbol is the RAW Kraken symbol.
    # Example: PF_DOTUSD
    #
    # DO NOT normalize it here.

    url = (
        f"{BASE}/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    data = http_get(url)

    if not data:
        return None

    rows = []

    if isinstance(data, dict):

        for key in ["candles", "data", "result"]:

            value = data.get(key)

            if isinstance(value, list):
                rows = value
                break

            if isinstance(value, dict):
                rows = list(value.values())
                break

    elif isinstance(data, list):

        rows = data

    parsed = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("ts")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

                v = (
                    row.get("volume")
                    or row.get("v")
                )

            elif isinstance(row, (list, tuple)):

                if len(row) < 6:
                    continue

                ts = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]
                v = row[5]

            else:
                continue

            ts = safe_float(ts)
            o = safe_float(o)
            h = safe_float(h)
            l = safe_float(l)
            c = safe_float(c)
            v = safe_float(v)

            if None in [ts, o, h, l, c, v]:
                continue

            parsed.append({
                "timestamp": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v
            })

        except Exception:
            continue

    if len(parsed) < 30:
        return None

    df = pd.DataFrame(parsed)

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    # --------------------------------------------------------
    # Remove currently forming candle.
    # The final row is not used for signals/exits.
    # --------------------------------------------------------

    if len(df) > 1:
        df = df.iloc[:-1].copy()

    if len(df) > limit:
        df = df.tail(limit).copy()

    df = df.reset_index(drop=True)

    if len(df) < 30:
        return None

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=ATR_PERIOD):

    if df is None or len(df) < period + 1:
        return None

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    value = safe_float(
        atr.iloc[-1]
    )

    return value


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    period=RVOL_PERIOD
):

    if df is None or len(df) < period + 1:
        return 0.0

    current_volume = safe_float(
        df["volume"].iloc[-1],
        0.0
    )

    previous = df["volume"].iloc[
        -(period + 1):-1
    ]

    avg_volume = safe_float(
        previous.mean(),
        0.0
    )

    if avg_volume <= 0:
        return 0.0

    return current_volume / avg_volume


# ============================================================
# TREND
# ============================================================

def get_trend(df):

    if df is None or len(df) < 50:
        return "NEUTRAL"

    close = df["close"]

    sma20 = close.rolling(20).mean()
    sma50 = close.rolling(50).mean()

    last = safe_float(close.iloc[-1])
    s20 = safe_float(sma20.iloc[-1])
    s50 = safe_float(sma50.iloc[-1])

    if None in [last, s20, s50]:
        return "NEUTRAL"

    if last > s20 > s50:
        return "LONG"

    if last < s20 < s50:
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# SWINGS
# ============================================================

def find_swing_highs(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    if df is None:
        return []

    highs = df["high"].values

    result = []

    for i in range(left, len(df) - right):

        current = highs[i]

        left_values = highs[
            i-left:i
        ]

        right_values = highs[
            i+1:i+right+1
        ]

        if (
            current > left_values.max()
            and current > right_values.max()
        ):
            result.append(float(current))

    return result


def find_swing_lows(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    if df is None:
        return []

    lows = df["low"].values

    result = []

    for i in range(left, len(df) - right):

        current = lows[i]

        left_values = lows[
            i-left:i
        ]

        right_values = lows[
            i+1:i+right+1
        ]

        if (
            current < left_values.min()
            and current < right_values.min()
        ):
            result.append(float(current))

    return result


# ============================================================
# LEVEL HELPERS
# ============================================================

def closest_below(levels, price):

    values = [
        x for x in levels
        if x < price
    ]

    if not values:
        return None

    return max(values)


def closest_above(levels, price):

    values = [
        x for x in levels
        if x > price
    ]

    if not values:
        return None

    return min(values)


def cluster_levels(
    levels,
    tolerance=0.002
):

    if not levels:
        return []

    levels = sorted(
        float(x)
        for x in levels
        if x is not None
    )

    clusters = []

    for level in levels:

        if not clusters:

            clusters.append([level])
            continue

        center = sum(
            clusters[-1]
        ) / len(clusters[-1])

        if center == 0:
            clusters[-1].append(level)
            continue

        if abs(level - center) / center <= tolerance:

            clusters[-1].append(level)

        else:

            clusters.append([level])

    return [
        sum(cluster) / len(cluster)
        for cluster in clusters
    ]


# ============================================================
# STATIC S/R
# ============================================================

def get_static_levels(df_1h):

    if df_1h is None:
        return [], []

    highs = find_swing_highs(df_1h)
    lows = find_swing_lows(df_1h)

    resistance = cluster_levels(highs)
    support = cluster_levels(lows)

    return support, resistance


# ============================================================
# DYNAMIC S/R
# ============================================================

def get_dynamic_levels(df_15m):

    if df_15m is None:
        return [], []

    recent = df_15m.tail(40)

    highs = find_swing_highs(recent)
    lows = find_swing_lows(recent)

    resistance = cluster_levels(highs)
    support = cluster_levels(lows)

    return support, resistance


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(
    df_15m,
    rvol
):

    if df_15m is None or len(df_15m) < 12:
        return None

    current = df_15m.iloc[-1]

    previous = df_15m.iloc[-10:-2]

    previous_high = safe_float(
        previous["high"].max()
    )

    previous_low = safe_float(
        previous["low"].min()
    )

    close = safe_float(
        current["close"]
    )

    open_price = safe_float(
        current["open"]
    )

    if None in [
        previous_high,
        previous_low,
        close,
        open_price
    ]:
        return None

    # --------------------------------------------------------
    # LONG BREAKOUT
    # --------------------------------------------------------

    if (
        close > previous_high
        and close > open_price
        and rvol >= RVOL_ABNORMAL
    ):

        return "LONG"

    # --------------------------------------------------------
    # SHORT BREAKOUT
    # --------------------------------------------------------

    if (
        close < previous_low
        and close < open_price
        and rvol >= RVOL_ABNORMAL
    ):

        return "SHORT"

    return None


# ============================================================
# HIGH-VOLUME REJECTION
# ============================================================

def detect_rejection(
    df_15m,
    rvol
):

    if df_15m is None or len(df_15m) < 2:
        return None

    if rvol < RVOL_ABNORMAL:
        return None

    candle = df_15m.iloc[-1]

    o = safe_float(candle["open"])
    h = safe_float(candle["high"])
    l = safe_float(candle["low"])
    c = safe_float(candle["close"])

    if None in [o, h, l, c]:
        return None

    total_range = h - l

    if total_range <= 0:
        return None

    upper_wick = h - max(o, c)
    lower_wick = min(o, c) - l

    upper_ratio = upper_wick / total_range
    lower_ratio = lower_wick / total_range

    # --------------------------------------------------------
    # Bearish upper rejection -> SHORT
    # --------------------------------------------------------

    if (
        upper_ratio >= 0.45
        and c < o
    ):

        return "SHORT"

    # --------------------------------------------------------
    # Bullish lower rejection -> LONG
    # --------------------------------------------------------

    if (
        lower_ratio >= 0.45
        and c > o
    ):

        return "LONG"

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df_5m,
    side
):

    if df_5m is None or len(df_5m) < 3:
        return False

    current = df_5m.iloc[-1]
    previous = df_5m.iloc[-2]

    o = safe_float(current["open"])
    c = safe_float(current["close"])

    prev_c = safe_float(
        previous["close"]
    )

    if None in [o, c, prev_c]:
        return False

    if side == "LONG":

        return (
            c > o
            and c > prev_c
        )

    if side == "SHORT":

        return (
            c < o
            and c < prev_c
        )

    return False


# ============================================================
# TRADE CALCULATION
# ============================================================

def calculate_trade(
    side,
    entry,
    atr,
    static_support,
    static_resistance,
    dynamic_support,
    dynamic_resistance
):

    if entry is None or entry <= 0:
        return None

    if atr is None or atr <= 0:
        return None

    supports = (
        static_support
        + dynamic_support
    )

    resistances = (
        static_resistance
        + dynamic_resistance
    )

    buffer = atr * ATR_BUFFER_MULT

    # ========================================================
    # LONG
    # ========================================================

    if side == "LONG":

        support_candidates = [
            x for x in supports
            if x < entry
        ]

        resistance_candidates = [
            x for x in static_resistance
            if x > entry
        ]

        if not support_candidates:
            return None

        if not resistance_candidates:
            return None

        support = max(
            support_candidates
        )

        resistance = min(
            resistance_candidates
        )

        sl = support - buffer
        tp = resistance

        if sl <= 0:
            return None

        if tp <= entry:
            return None

        risk = entry - sl
        reward = tp - entry

    # ========================================================
    # SHORT
    # ========================================================

    elif side == "SHORT":

        resistance_candidates = [
            x for x in resistances
            if x > entry
        ]

        support_candidates = [
            x for x in static_support
            if x < entry
        ]

        if not resistance_candidates:
            return None

        if not support_candidates:
            return None

        resistance = min(
            resistance_candidates
        )

        support = max(
            support_candidates
        )

        sl = resistance + buffer
        tp = support

        if sl <= entry:
            return None

        if tp >= entry:
            return None

        risk = sl - entry
        reward = entry - tp

    else:
        return None

    if risk <= 0 or reward <= 0:
        return None

    sl_pct = risk / entry
    rr = reward / risk

    # --------------------------------------------------------
    # Maximum SL
    # --------------------------------------------------------

    if sl_pct > MAX_SL_PCT:
        return None

    # --------------------------------------------------------
    # Minimum RR
    # --------------------------------------------------------

    if rr < MIN_RR:
        return None

    return {
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "rr": float(rr),
        "sl_pct": float(sl_pct)
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    side,
    trend,
    setup,
    rvol,
    confirmed_5m,
    close_direction,
    rr
):

    score = 0

    # Trend
    if trend == side:
        score += 3

    elif trend == "NEUTRAL":
        score += 1

    # Setup
    if setup == "BREAKOUT":
        score += 3

    elif setup == "REJECTION":
        score += 2

    # RVOL
    if rvol >= RVOL_STRONG:
        score += 3

    elif rvol >= RVOL_ABNORMAL:
        score += 2

    # 5M
    if confirmed_5m:
        score += 2

    # 15M direction
    if close_direction:
        score += 1

    # RR
    if rr >= 4:
        score += 2

    elif rr >= 3:
        score += 1

    return int(score)


# ============================================================
# DATABASE
# ============================================================

def get_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = get_db()

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
            closed_at TEXT,
            exit_reason TEXT,
            closed_reported INTEGER DEFAULT 0
        )
    """)

    # --------------------------------------------------------
    # Migration for old DBs
    # --------------------------------------------------------

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    columns = {
        row[1]
        for row in cur.fetchall()
    }

    if "exit_reason" not in columns:

        cur.execute("""
            ALTER TABLE trades
            ADD COLUMN exit_reason TEXT
        """)

    if "closed_reported" not in columns:

        cur.execute("""
            ALTER TABLE trades
            ADD COLUMN closed_reported INTEGER DEFAULT 0
        """)

    conn.commit()
    conn.close()


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
    """).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def has_open_symbol(symbol):

    norm = normalize_symbol(symbol)

    conn = get_db()

    rows = conn.execute("""
        SELECT symbol
        FROM trades
        WHERE status='OPEN'
    """).fetchall()

    conn.close()

    for row in rows:

        if normalize_symbol(row["symbol"]) == norm:
            return True

    return False


# ============================================================
# ENFORCE MAX OPEN
# ============================================================

def enforce_max_open_trades():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
    """).fetchall()

    if len(rows) <= MAX_OPEN_TRADES:

        conn.close()
        return []

    extras = rows[MAX_OPEN_TRADES:]

    removed = []

    for row in extras:

        conn.execute("""
            UPDATE trades
            SET
                status='REMOVED',
                result='REMOVED',
                pnl=0,
                closed_at=?,
                exit_reason='OPEN_LIMIT_CLEANUP',
                closed_reported=1
            WHERE id=?
        """, (
            iso_now(),
            row["id"]
        ))

        removed.append(
            row["id"]
        )

    conn.commit()
    conn.close()

    print(
        f"[DB] Removed {len(removed)} "
        f"extra OPEN trades"
    )

    return removed


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(
    symbol,
    side,
    setup,
    trade,
    score,
    rvol,
    trend
):

    conn = get_db()

    # Final protection against duplicate open symbol
    existing = conn.execute("""
        SELECT id
        FROM trades
        WHERE status='OPEN'
          AND symbol=?
    """, (
        symbol,
    )).fetchone()

    if existing:

        conn.close()
        return None

    cur = conn.execute("""
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
            closed_at,
            exit_reason,
            closed_reported
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN',
                NULL, NULL, ?, NULL, NULL, 0)
    """, (
        symbol,
        side,
        setup,
        trade["entry"],
        trade["sl"],
        trade["tp"],
        trade["rr"],
        score,
        rvol,
        trend,
        iso_now()
    ))

    conn.commit()

    trade_id = cur.lastrowid

    conn.close()

    return trade_id


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    exit_price
):

    if entry is None or entry == 0:
        return 0.0

    if side == "LONG":

        return (
            (exit_price - entry)
            / entry
            * 100
        )

    if side == "SHORT":

        return (
            (entry - exit_price)
            / entry
            * 100
        )

    return 0.0


# ============================================================
# UPDATE OPEN TRADES USING CLOSED 5M CANDLES
# ============================================================

def update_open_trades():

    open_trades = get_open_trades()

    if not open_trades:
        return []

    newly_closed = []

    conn = get_db()

    for trade in open_trades:

        trade_id = trade["id"]
        symbol = trade["symbol"]
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

        if None in [entry, sl, tp]:
            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # Use RAW DB symbol for Kraken API.
        # Do NOT normalize it.
        # ----------------------------------------------------

        df = get_candles(
            symbol,
            TF_5M,
            limit=50
        )

        if df is None or len(df) < 2:
            print(
                f"[EXIT] No 5M data for {symbol}"
            )
            continue

        candle = df.iloc[-1]

        high = safe_float(
            candle["high"]
        )

        low = safe_float(
            candle["low"]
        )

        if high is None or low is None:
            continue

        result = None
        exit_price = None
        exit_reason = None

        # ====================================================
        # LONG
        # ====================================================

        if side == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            # ------------------------------------------------
            # Both hit:
            # Conservative policy = SL first
            # ------------------------------------------------

            if hit_sl:

                result = "LOSS"
                exit_price = sl
                exit_reason = "SL"

            elif hit_tp:

                result = "WIN"
                exit_price = tp
                exit_reason = "TP"

        # ====================================================
        # SHORT
        # ====================================================

        elif side == "SHORT":

            hit_sl = high >= sl
            hit_tp = low <= tp

            # ------------------------------------------------
            # Both hit:
            # Conservative policy = SL first
            # ------------------------------------------------

            if hit_sl:

                result = "LOSS"
                exit_price = sl
                exit_reason = "SL"

            elif hit_tp:

                result = "WIN"
                exit_price = tp
                exit_reason = "TP"

        if result is None:
            continue

        pnl = calculate_pnl(
            side,
            entry,
            exit_price
        )

        conn.execute("""
            UPDATE trades
            SET
                status='CLOSED',
                result=?,
                pnl=?,
                closed_at=?,
                exit_reason=?,
                closed_reported=0
            WHERE id=?
              AND status='OPEN'
        """, (
            result,
            pnl,
            iso_now(),
            exit_reason,
            trade_id
        ))

        newly_closed.append(
            trade_id
        )

        print(
            f"[EXIT] {symbol} {side} "
            f"{result} {exit_reason} "
            f"exit={exit_price} "
            f"pnl={pnl:.2f}%"
        )

    conn.commit()
    conn.close()

    return newly_closed


# ============================================================
# CLOSED REPORT QUEUE
# ============================================================

def get_unreported_closed():

    conn = get_db()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status='CLOSED'
          AND closed_reported=0
        ORDER BY id ASC
    """).fetchall()

    conn.close()

    return [dict(row) for row in rows]


def mark_closed_reported(ids):

    if not ids:
        return

    conn = get_db()

    placeholders = ",".join(
        "?"
        for _ in ids
    )

    conn.execute(
        f"""
        UPDATE trades
        SET closed_reported=1
        WHERE id IN ({placeholders})
        """,
        tuple(ids)
    )

    conn.commit()
    conn.close()


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = get_db()

    open_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
    """).fetchone()[0]

    closed_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
    """).fetchone()[0]

    wins = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
          AND result='WIN'
    """).fetchone()[0]

    losses = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status='CLOSED'
          AND result='LOSS'
    """).fetchone()[0]

    net_pnl = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status='CLOSED'
    """).fetchone()[0]

    long_pnl = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status='CLOSED'
          AND side='LONG'
    """).fetchone()[0]

    short_pnl = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status='CLOSED'
          AND side='SHORT'
    """).fetchone()[0]

    breakout_pnl = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status='CLOSED'
          AND setup='BREAKOUT'
    """).fetchone()[0]

    rejection_pnl = conn.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status='CLOSED'
          AND setup='REJECTION'
    """).fetchone()[0]

    conn.close()

    total_decisions = wins + losses

    win_rate = (
        wins / total_decisions * 100
        if total_decisions > 0
        else 0.0
    )

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": safe_float(
            net_pnl,
            0.0
        ),
        "long_pnl": safe_float(
            long_pnl,
            0.0
        ),
        "short_pnl": safe_float(
            short_pnl,
            0.0
        ),
        "breakout_pnl": safe_float(
            breakout_pnl,
            0.0
        ),
        "rejection_pnl": safe_float(
            rejection_pnl,
            0.0
        )
    }


# ============================================================
# LIVE OPEN TRADE DISPLAY
# ============================================================

def live_trade_pnl(
    trade,
    price
):

    entry = safe_float(
        trade["entry"]
    )

    if price is None or entry is None:
        return None

    if trade["side"] == "LONG":

        return (
            (price - entry)
            / entry
            * 100
        )

    if trade["side"] == "SHORT":

        return (
            (entry - price)
            / entry
            * 100
        )

    return None


# ============================================================
# MARKET SCAN
# ============================================================

def scan_market(market):

    symbol = market["symbol"]

    result = {
        "symbol": symbol,
        "data": False,
        "candidate": False,
        "trade": None,
        "score": 0,
        "side": None,
        "setup": None,
        "rvol": 0.0,
        "trend": "NEUTRAL"
    }

    try:

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        df_1h = get_candles(
            symbol,
            TF_1H,
            OHLCV_LIMIT
        )

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        df_15m = get_candles(
            symbol,
            TF_15M,
            OHLCV_LIMIT
        )

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df_5m = get_candles(
            symbol,
            TF_5M,
            OHLCV_LIMIT
        )

        if (
            df_1h is None
            or df_15m is None
            or df_5m is None
        ):
            return result

        result["data"] = True

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        trend = get_trend(
            df_1h
        )

        result["trend"] = trend

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            df_15m
        )

        result["rvol"] = rvol

        # ----------------------------------------------------
        # STATIC / DYNAMIC LEVELS
        # ----------------------------------------------------

        static_support, static_resistance = (
            get_static_levels(df_1h)
        )

        dynamic_support, dynamic_resistance = (
            get_dynamic_levels(df_15m)
        )

        # ----------------------------------------------------
        # SETUPS
        # ----------------------------------------------------

        breakout_side = detect_breakout(
            df_15m,
            rvol
        )

        rejection_side = detect_rejection(
            df_15m,
            rvol
        )

        setup = None
        side = None

        # Prefer breakout
        if breakout_side:

            setup = "BREAKOUT"
            side = breakout_side

        elif rejection_side:

            setup = "REJECTION"
            side = rejection_side

        else:

            return result

        # ----------------------------------------------------
        # TREND FILTER
        # ----------------------------------------------------

        if trend != "NEUTRAL":

            if side != trend:
                return result

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        confirmed = confirm_5m(
            df_5m,
            side
        )

        if not confirmed:
            return result

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = safe_float(
            df_5m["close"].iloc[-1]
        )

        if entry is None or entry <= 0:
            return result

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = calculate_atr(
            df_15m
        )

        if atr is None:
            return result

        # ----------------------------------------------------
        # TRADE
        # ----------------------------------------------------

        trade = calculate_trade(
            side=side,
            entry=entry,
            atr=atr,
            static_support=static_support,
            static_resistance=static_resistance,
            dynamic_support=dynamic_support,
            dynamic_resistance=dynamic_resistance
        )

        if trade is None:
            return result

        # ----------------------------------------------------
        # 15M CLOSE DIRECTION
        # ----------------------------------------------------

        c15 = safe_float(
            df_15m["close"].iloc[-1]
        )

        o15 = safe_float(
            df_15m["open"].iloc[-1]
        )

        close_direction = False

        if side == "LONG":

            close_direction = (
                c15 is not None
                and o15 is not None
                and c15 > o15
            )

        elif side == "SHORT":

            close_direction = (
                c15 is not None
                and o15 is not None
                and c15 < o15
            )

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            side=side,
            trend=trend,
            setup=setup,
            rvol=rvol,
            confirmed_5m=confirmed,
            close_direction=close_direction,
            rr=trade["rr"]
        )

        result.update({
            "candidate": score >= MIN_SIGNAL_SCORE,
            "trade": trade,
            "score": score,
            "side": side,
            "setup": setup
        })

        return result

    except Exception as e:

        print(
            f"[SCAN ERROR] {symbol}: {e}"
        )

        return result


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "[TELEGRAM] BOT TOKEN missing"
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[TELEGRAM] CHAT ID missing"
        )
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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
            timeout=REQUEST_TIMEOUT
        )

        if r.ok:

            print(
                "[TELEGRAM] Sent"
            )

            return True

        print(
            "[TELEGRAM ERROR]",
            r.text
        )

        return False

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


# ============================================================
# REPORT FORMAT
# ============================================================

def build_report(
    stats,
    open_trades,
    closed_trades,
    new_selected,
    scan_stats,
    duration,
    price_map
):

    lines = []

    lines.append(
        "🤖 VOLUME-KHAT 100"
    )

    lines.append(
        "📡 VOLUME-KHAT 100 v2.7 FIXED"
    )

    lines.append(
        f"🕐 {format_utc()}"
    )

    lines.append(
        "⏱ TOP 100 | 5M CLOSED"
    )

    lines.append(
        "💻 Real trading: DISABLED"
    )

    lines.append("")

    # ========================================================
    # CAPACITY
    # ========================================================

    open_count = stats["open"]

    available = max(
        0,
        MAX_OPEN_TRADES - open_count
    )

    lines.append(
        "📦 TRADE CAPACITY"
    )

    lines.append(
        f"Open: {open_count}/{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"Available slots: {available}"
    )

    lines.append(
        f"New selected: {new_selected}"
    )

    if available == 0:

        lines.append(
            "🔒 OPEN LIMIT REACHED"
        )

    lines.append("")

    # ========================================================
    # STRATEGY
    # ========================================================

    lines.append(
        "🎯 STRATEGY"
    )

    lines.append(
        "1H Trend + Static S/R"
    )

    lines.append(
        "15M Dynamic S/R + RVOL + Setup"
    )

    lines.append(
        "5M Entry Confirmation"
    )

    lines.append(
        "BREAKOUT / HIGH-VOLUME REJECTION"
    )

    lines.append(
        "RR >= 1:2 | SL <= 0.80%"
    )

    lines.append(
        "MAX 3 OPEN"
    )

    lines.append("")

    # ========================================================
    # OPEN SIGNALS
    # ========================================================

    lines.append(
        "📂 OPEN SIGNALS"
    )

    if not open_trades:

        lines.append(
            "None"
        )

    else:

        for trade in open_trades:

            symbol = trade["symbol"]

            price = price_map.get(
                normalize_symbol(symbol)
            )

            pnl = live_trade_pnl(
                trade,
                price
            )

            emoji = (
                "🟢"
                if trade["side"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} {symbol} "
                f"{trade['side']}"
            )

            lines.append(
                f"📌 {trade['setup']} | "
                f"⭐ {trade['score']} | "
                f"RR 1:{trade['rr']:.2f}"
            )

            lines.append(
                f"Entry {trade['entry']:.8g}, "
                f"Live {price if price is not None else 'N/A'}"
            )

            lines.append(
                f"SL {trade['sl']:.8g}, "
                f"TP {trade['tp']:.8g}"
            )

            if pnl is not None:

                sign = (
                    "+"
                    if pnl >= 0
                    else ""
                )

                lines.append(
                    f"Live PnL {sign}{pnl:.2f}%"
                )

            else:

                lines.append(
                    "Live PnL N/A"
                )

            lines.append("")

    # ========================================================
    # CLOSED SINCE PREVIOUS REPORT
    # ========================================================

    lines.append(
        "📕 CLOSED SINCE PREVIOUS REPORT"
    )

    if not closed_trades:

        lines.append(
            "None"
        )

    else:

        for trade in closed_trades:

            result = trade["result"]

            emoji = (
                "🟢"
                if result == "WIN"
                else "🔴"
            )

            pnl = safe_float(
                trade["pnl"],
                0.0
            )

            sign = (
                "+"
                if pnl >= 0
                else ""
            )

            lines.append(
                f"{emoji} {trade['symbol']} "
                f"{trade['side']} "
                f"{result}"
            )

            lines.append(
                f"📌 {trade['setup']} | "
                f"Exit: {trade['exit_reason']}"
            )

            lines.append(
                f"Entry {trade['entry']:.8g} | "
                f"Exit {(
                    trade['tp']
                    if trade['exit_reason'] == 'TP'
                    else trade['sl']
                ):.8g}"
            )

            lines.append(
                f"PnL {sign}{pnl:.2f}%"
            )

            lines.append("")

    # ========================================================
    # STATS
    # ========================================================

    lines.append(
        "📈 STATS"
    )

    lines.append(
        f"Open {stats['open']}/{MAX_OPEN_TRADES}, "
        f"Closed {stats['closed']}, "
        f"Wins {stats['wins']}, "
        f"Losses {stats['losses']}, "
        f"Win Rate {stats['win_rate']:.1f}%"
    )

    net_sign = (
        "+"
        if stats["net_pnl"] >= 0
        else ""
    )

    lines.append(
        f"Net PnL {net_sign}"
        f"{stats['net_pnl']:.2f}%"
    )

    long_sign = (
        "+"
        if stats["long_pnl"] >= 0
        else ""
    )

    short_sign = (
        "+"
        if stats["short_pnl"] >= 0
        else ""
    )

    lines.append(
        f"LONG {long_sign}"
        f"{stats['long_pnl']:.2f}%"
    )

    lines.append(
        f"SHORT {short_sign}"
        f"{stats['short_pnl']:.2f}%"
    )

    breakout_sign = (
        "+"
        if stats["breakout_pnl"] >= 0
        else ""
    )

    rejection_sign = (
        "+"
        if stats["rejection_pnl"] >= 0
        else ""
    )

    lines.append(
        f"BREAKOUT {breakout_sign}"
        f"{stats['breakout_pnl']:.2f}%"
    )

    lines.append(
        f"REJECTION {rejection_sign}"
        f"{stats['rejection_pnl']:.2f}%"
    )

    lines.append("")

    # ========================================================
    # SCAN FUNNEL
    # ========================================================

    lines.append(
        "📊 SCAN"
    )

    lines.append(
        f"Markets {scan_stats['markets']}"
    )

    lines.append(
        f"Data {scan_stats['data']}"
    )

    lines.append(
        f"RVOL>=2 {scan_stats['rvol']}"
    )

    lines.append(
        f"BO {scan_stats['breakout']}"
    )

    lines.append(
        f"REJ {scan_stats['rejection']}"
    )

    lines.append(
        f"5M Confirm {scan_stats['confirm']}"
    )

    lines.append(
        f"RR>=1:2 {scan_stats['rr']}"
    )

    lines.append(
        f"SL<=0.80% {scan_stats['sl']}"
    )

    lines.append(
        f"Qualified {scan_stats['qualified']}"
    )

    lines.append(
        f"New selected {new_selected}/{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"⏱ {duration:.1f}s"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print("=" * 60)
    print("KRAKEN FUTURES VOLUME-KHAT 100 v2.7 FIXED")
    print("=" * 60)

    init_db()

    # --------------------------------------------------------
    # STEP 1
    # Close existing trades FIRST
    # using latest CLOSED 5M candle.
    # --------------------------------------------------------

    newly_closed_ids = update_open_trades()

    if newly_closed_ids:

        print(
            f"[EXIT] Newly closed: "
            f"{len(newly_closed_ids)}"
        )

    # --------------------------------------------------------
    # STEP 2
    # Enforce max open.
    # --------------------------------------------------------

    enforce_max_open_trades()

    # --------------------------------------------------------
    # STEP 3
    # Discover TOP 100.
    # --------------------------------------------------------

    markets = discover_top_markets()

    if not markets:

        duration = (
            time.time()
            - start_time
        )

        stats = get_stats()

        report = build_report(
            stats=stats,
            open_trades=get_open_trades(),
            closed_trades=get_unreported_closed(),
            new_selected=0,
            scan_stats={
                "markets": 0,
                "data": 0,
                "rvol": 0,
                "breakout": 0,
                "rejection": 0,
                "confirm": 0,
                "rr": 0,
                "sl": 0,
                "qualified": 0
            },
            duration=duration,
            price_map={}
        )

        send_telegram(
            report
        )

        return

    # --------------------------------------------------------
    # STEP 4
    # Live prices
    # --------------------------------------------------------

    market_symbols = [
        m["symbol"]
        for m in markets
    ]

    price_map = get_live_price_map(
        market_symbols
    )

    # --------------------------------------------------------
    # STEP 5
    # Scan
    # --------------------------------------------------------

    scan_stats = {
        "markets": len(markets),
        "data": 0,
        "rvol": 0,
        "breakout": 0,
        "rejection": 0,
        "confirm": 0,
        "rr": 0,
        "sl": 0,
        "qualified": 0
    }

    candidates = []

    for market in markets:

        result = scan_market(
            market
        )

        if result["data"]:

            scan_stats["data"] += 1

        if result["rvol"] >= RVOL_ABNORMAL:

            scan_stats["rvol"] += 1

        if result["setup"] == "BREAKOUT":

            scan_stats["breakout"] += 1

        elif result["setup"] == "REJECTION":

            scan_stats["rejection"] += 1

        if result["trade"] is not None:

            trade = result["trade"]

            scan_stats["rr"] += 1
            scan_stats["sl"] += 1

        if result["candidate"]:

            scan_stats["qualified"] += 1

            candidates.append(
                result
            )

    # --------------------------------------------------------
    # 5M confirmation count is calculated from candidates
    # and valid setup flow.
    # --------------------------------------------------------

    scan_stats["confirm"] = sum(
        1
        for c in candidates
        if c["trade"] is not None
    )

    # --------------------------------------------------------
    # STEP 6
    # Rank candidates
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["trade"]["rr"]
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # STEP 7
    # Capacity
    # --------------------------------------------------------

    open_trades = get_open_trades()

    open_count = len(
        open_trades
    )

    capacity = max(
        0,
        MAX_OPEN_TRADES - open_count
    )

    capacity = min(
        capacity,
        MAX_NEW_SIGNALS
    )

    selected = []

    # --------------------------------------------------------
    # STEP 8
    # Select signals
    # --------------------------------------------------------

    for candidate in candidates:

        if len(selected) >= capacity:
            break

        symbol = candidate["symbol"]

        if has_open_symbol(symbol):
            continue

        selected.append(
            candidate
        )

    # --------------------------------------------------------
    # STEP 9
    # Save selected
    # --------------------------------------------------------

    saved_count = 0

    for candidate in selected:

        symbol = candidate["symbol"]

        trade = candidate["trade"]

        trade_id = save_signal(
            symbol=symbol,
            side=candidate["side"],
            setup=candidate["setup"],
            trade=trade,
            score=candidate["score"],
            rvol=candidate["rvol"],
            trend=candidate["trend"]
        )

        if trade_id:

            saved_count += 1

            print(
                f"[NEW] {symbol} "
                f"{candidate['side']} "
                f"{candidate['setup']} "
                f"score={candidate['score']} "
                f"RR={trade['rr']:.2f}"
            )

    # --------------------------------------------------------
    # STEP 10
    # Final max-open enforcement
    # --------------------------------------------------------

    enforce_max_open_trades()

    # --------------------------------------------------------
    # STEP 11
    # Refresh live price map for report
    # --------------------------------------------------------

    final_open = get_open_trades()

    final_symbols = [
        x["symbol"]
        for x in final_open
    ]

    if final_symbols:

        fresh_prices = get_live_price_map(
            final_symbols
        )

        if fresh_prices:

            price_map.update(
                fresh_prices
            )

    # --------------------------------------------------------
    # STEP 12
    # CLOSED REPORT
    #
    # IMPORTANT:
    # Current-run closures ARE included.
    # No longer hidden.
    # --------------------------------------------------------

    closed_for_report = (
        get_unreported_closed()
    )

    # --------------------------------------------------------
    # STEP 13
    # Final stats
    # --------------------------------------------------------

    stats = get_stats()

    duration = (
        time.time()
        - start_time
    )

    # --------------------------------------------------------
    # STEP 14
    # Build report
    # --------------------------------------------------------

    report = build_report(
        stats=stats,
        open_trades=final_open,
        closed_trades=closed_for_report,
        new_selected=saved_count,
        scan_stats=scan_stats,
        duration=duration,
        price_map=price_map
    )

    print("")
    print(report)
    print("")

    # --------------------------------------------------------
    # STEP 15
    # Telegram
    # --------------------------------------------------------

    telegram_ok = send_telegram(
        report
    )

    # --------------------------------------------------------
    # STEP 16
    # Mark closed trades reported
    # ONLY after successful Telegram.
    #
    # Therefore:
    # Telegram failure -> closure remains queued
    # next successful report -> shown again
    # --------------------------------------------------------

    if telegram_ok:

        closed_ids = [
            x["id"]
            for x in closed_for_report
        ]

        mark_closed_reported(
            closed_ids
        )

    print("=" * 60)

    print(
        f"FINISHED | "
        f"Markets={len(markets)} | "
        f"Data={scan_stats['data']} | "
        f"Qualified={scan_stats['qualified']} | "
        f"New={saved_count} | "
        f"Open={stats['open']} | "
        f"Closed={stats['closed']}"
    )

    print("=" * 60)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
