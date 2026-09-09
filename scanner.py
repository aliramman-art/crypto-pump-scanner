# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v2.5
# ============================================================
#
# VOLUME-KHAT 100
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
#   Minimum RR = 1:2
#   Maximum SL distance = 0.80%
#
# SIGNALS:
#   Maximum 3 NEW signals per scan
#   Ranked by score
#
# TRADING:
#   PAPER TRACKING ONLY
#   REAL TRADING DISABLED
#
# GITHUB ACTIONS:
#   One scan per execution
#   No while True
#
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


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Volume-Khat-100/2.5"
})


# ============================================================
# UTILS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        x = float(value)

        if not math.isfinite(x):
            return default

        return x

    except Exception:
        return default


def normalize_symbol(symbol):
    if not symbol:
        return ""

    s = str(symbol).upper().strip()

    # Kraken may sometimes return separators
    s = s.replace("/", "")
    s = s.replace("-", "")
    s = s.replace("_", "")

    return s


def is_usd_perpetual_symbol(symbol):
    s = normalize_symbol(symbol)

    if not s:
        return False

    # Main Kraken Futures perpetual naming
    if s.endswith("USD"):
        return True

    if s.endswith("USDT"):
        return True

    return False


def is_explicitly_non_perpetual(item):
    """
    Reject only when Kraken explicitly tells us
    that this is not a perpetual contract.
    """

    if not isinstance(item, dict):
        return False

    values = []

    for key in (
        "type",
        "contractType",
        "contract_type",
        "instrumentType",
        "instrument_type",
    ):
        value = item.get(key)

        if value is not None:
            values.append(str(value).lower())

    for value in values:

        if "perpetual" in value:
            return False

        if value in {
            "future",
            "futures",
            "fixedmaturity",
            "fixed-maturity",
            "inverse",
        }:
            return True

    return False


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(f"HTTP ERROR: {url}")
        print(f"ERROR: {e}")

        return None


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_instruments():

    url = f"{BASE}/derivatives/api/v3/instruments"

    data = http_get(url)

    if not data:
        return []

    instruments = []

    if isinstance(data, dict):

        for key in (
            "instruments",
            "data",
            "result",
            "contracts",
        ):

            value = data.get(key)

            if isinstance(value, list):

                instruments = value
                break

    elif isinstance(data, list):

        instruments = data

    print(f"Kraken raw instruments: {len(instruments)}")

    result = []

    for item in instruments:

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("instrument")
            or item.get("ticker")
            or item.get("code")
        )

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        # Explicitly disabled instrument
        tradeable = item.get("tradeable")

        if tradeable is False:
            continue

        # Explicitly non-perpetual
        if is_explicitly_non_perpetual(item):
            continue

        # Only USD / USDT contracts
        if not is_usd_perpetual_symbol(symbol):
            continue

        result.append({
            "symbol": symbol,
            "raw": item
        })

    # Remove duplicates
    unique = {}

    for item in result:
        unique[item["symbol"]] = item

    result = list(unique.values())

    print(f"Kraken usable instruments: {len(result)}")

    return result


# ============================================================
# KRAKEN TICKERS
# ============================================================

def get_tickers():

    url = f"{BASE}/derivatives/api/v3/tickers"

    data = http_get(url)

    if not data:
        return []

    tickers = []

    if isinstance(data, dict):

        for key in (
            "tickers",
            "data",
            "result",
        ):

            value = data.get(key)

            if isinstance(value, list):

                tickers = value
                break

            if isinstance(value, dict):

                tickers = []

                for symbol, row in value.items():

                    if isinstance(row, dict):

                        row = dict(row)

                        row.setdefault("symbol", symbol)

                        tickers.append(row)

                break

    elif isinstance(data, list):

        tickers = data

    print(f"Kraken raw tickers: {len(tickers)}")

    return tickers


# ============================================================
# TOP MARKET DISCOVERY
# ============================================================

def discover_top_markets():

    print()
    print("==========================================")
    print("MARKET DISCOVERY")
    print("==========================================")

    instruments = get_instruments()
    tickers = get_tickers()

    instrument_symbols = set()

    for item in instruments:

        symbol = normalize_symbol(item.get("symbol"))

        if symbol:
            instrument_symbols.add(symbol)

    print(
        f"Instrument symbols available: "
        f"{len(instrument_symbols)}"
    )

    markets = {}

    # --------------------------------------------------------
    # FIRST PASS
    # Use instrument list + ticker list
    # --------------------------------------------------------

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
            or ticker.get("ticker")
        )

        symbol = normalize_symbol(symbol)

        if not symbol:
            continue

        if not is_usd_perpetual_symbol(symbol):
            continue

        # If instrument list exists, prefer symbols from it.
        # But do not reject everything if Kraken metadata
        # does not line up perfectly.
        if instrument_symbols:

            if symbol not in instrument_symbols:

                # Accept if it clearly looks like USD/USDT
                if not is_usd_perpetual_symbol(symbol):
                    continue

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

        if last is None or last <= 0:
            continue

        if volume < 0:
            volume = 0.0

        current = markets.get(symbol)

        row = {
            "symbol": symbol,
            "volume": volume,
            "last": last
        }

        if current is None:
            markets[symbol] = row

        else:

            if volume > current["volume"]:
                markets[symbol] = row

    # --------------------------------------------------------
    # FALLBACK
    #
    # If instruments filtering fails, use ticker universe
    # directly.
    # --------------------------------------------------------

    if not markets:

        print("Primary discovery returned 0 markets.")
        print("Activating ticker-only fallback...")

        for ticker in tickers:

            if not isinstance(ticker, dict):
                continue

            symbol = (
                ticker.get("symbol")
                or ticker.get("instrument")
                or ticker.get("ticker")
            )

            symbol = normalize_symbol(symbol)

            if not is_usd_perpetual_symbol(symbol):
                continue

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

            if last is None or last <= 0:
                continue

            markets[symbol] = {
                "symbol": symbol,
                "volume": volume,
                "last": last
            }

    markets = list(markets.values())

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    markets = markets[:TOP_N]

    print(
        f"Usable ticker markets: {len(markets)}"
    )

    print(
        f"TOP markets selected: {len(markets)}"
    )

    if markets:

        print("Top 5:")

        for i, market in enumerate(
            markets[:5],
            start=1
        ):

            print(
                f"{i}. "
                f"{market['symbol']} | "
                f"Vol24h={market['volume']:.2f} | "
                f"Last={market['last']}"
            )

    print()

    return markets


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, limit=OHLCV_LIMIT):

    url = (
        f"{BASE}/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    data = http_get(url)

    if not data:
        return None

    rows = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "result",
        ):

            value = data.get(key)

            if isinstance(value, list):

                rows = value
                break

    elif isinstance(data, list):

        rows = data

    if not rows:
        return None

    parsed = []

    for row in rows:

        if isinstance(row, dict):

            timestamp = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
            )

            open_price = (
                row.get("open")
                or row.get("o")
            )

            high = (
                row.get("high")
                or row.get("h")
            )

            low = (
                row.get("low")
                or row.get("l")
            )

            close = (
                row.get("close")
                or row.get("c")
            )

            volume = (
                row.get("volume")
                or row.get("v")
                or 0
            )

        elif isinstance(row, (list, tuple)):

            if len(row) < 5:
                continue

            timestamp = row[0]
            open_price = row[1]
            high = row[2]
            low = row[3]
            close = row[4]

            volume = (
                row[5]
                if len(row) > 5
                else 0
            )

        else:
            continue

        timestamp = safe_float(timestamp)
        open_price = safe_float(open_price)
        high = safe_float(high)
        low = safe_float(low)
        close = safe_float(close)
        volume = safe_float(volume, 0)

        if None in (
            timestamp,
            open_price,
            high,
            low,
            close
        ):
            continue

        parsed.append([
            timestamp,
            open_price,
            high,
            low,
            close,
            volume
        ])

    if len(parsed) < 30:
        return None

    df = pd.DataFrame(
        parsed,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    # Sort chronologically
    df = df.sort_values(
        "timestamp"
    ).drop_duplicates(
        subset=["timestamp"]
    )

    # Drop current unfinished candle
    if len(df) > 1:
        df = df.iloc[:-1]

    if limit:
        df = df.tail(limit)

    df = df.reset_index(drop=True)

    return df


# ============================================================
# INDICATORS
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD
):

    if df is None or len(df) < period + 2:
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


def calculate_rvol(
    df,
    period=RVOL_PERIOD
):

    if df is None:
        return 0.0

    if len(df) < period + 1:
        return 0.0

    current_volume = safe_float(
        df["volume"].iloc[-1],
        0.0
    )

    baseline = df["volume"].iloc[
        -period - 1:-1
    ].mean()

    baseline = safe_float(
        baseline,
        0.0
    )

    if baseline <= 0:
        return 0.0

    return current_volume / baseline


# ============================================================
# TREND
# ============================================================

def get_trend(df):

    if df is None or len(df) < 60:
        return "NEUTRAL"

    close = df["close"]

    sma20 = close.rolling(20).mean().iloc[-1]

    sma50 = close.rolling(50).mean().iloc[-1]

    last = close.iloc[-1]

    if (
        last > sma20
        and sma20 > sma50
    ):
        return "LONG"

    if (
        last < sma20
        and sma20 < sma50
    ):
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# SWINGS
# ============================================================

def detect_swings(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    highs = []
    lows = []

    if df is None:
        return highs, lows

    if len(df) < left + right + 5:
        return highs, lows

    for i in range(
        left,
        len(df) - right
    ):

        high = df["high"].iloc[i]

        low = df["low"].iloc[i]

        left_highs = df["high"].iloc[
            i - left:i
        ]

        right_highs = df["high"].iloc[
            i + 1:i + right + 1
        ]

        left_lows = df["low"].iloc[
            i - left:i
        ]

        right_lows = df["low"].iloc[
            i + 1:i + right + 1
        ]

        if (
            high > left_highs.max()
            and high > right_highs.max()
        ):
            highs.append(
                float(high)
            )

        if (
            low < left_lows.min()
            and low < right_lows.min()
        ):
            lows.append(
                float(low)
            )

    return highs, lows


# ============================================================
# LEVEL HELPERS
# ============================================================

def closest_below(
    levels,
    price
):

    valid = [
        x for x in levels
        if x < price
    ]

    if not valid:
        return None

    return max(valid)


def closest_above(
    levels,
    price
):

    valid = [
        x for x in levels
        if x > price
    ]

    if not valid:
        return None

    return min(valid)


def cluster_levels(
    levels,
    tolerance=0.002
):

    if not levels:
        return []

    levels = sorted(
        float(x)
        for x in levels
        if safe_float(x) is not None
        and x > 0
    )

    clusters = []

    for level in levels:

        if not clusters:

            clusters.append([
                level
            ])

            continue

        avg = np.mean(
            clusters[-1]
        )

        if abs(level - avg) / avg <= tolerance:

            clusters[-1].append(level)

        else:

            clusters.append([
                level
            ])

    result = []

    for cluster in clusters:

        result.append(
            float(
                np.mean(cluster)
            )
        )

    return result


# ============================================================
# STATIC S/R
# ============================================================

def get_static_sr(df):

    highs, lows = detect_swings(df)

    resistance = cluster_levels(
        highs
    )

    support = cluster_levels(
        lows
    )

    return support, resistance


# ============================================================
# DYNAMIC S/R
# ============================================================

def get_dynamic_sr(df):

    if df is None or len(df) < 20:
        return [], []

    recent = df.tail(40)

    highs, lows = detect_swings(
        recent
    )

    support = cluster_levels(
        lows,
        tolerance=0.003
    )

    resistance = cluster_levels(
        highs,
        tolerance=0.003
    )

    return support, resistance


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(
    df,
    rvol
):

    if df is None or len(df) < 10:
        return None

    previous = df.iloc[-2]

    current = df.iloc[-1]

    recent_high = df["high"].iloc[
        -8:-2
    ].max()

    recent_low = df["low"].iloc[
        -8:-2
    ].min()

    # Bullish breakout
    if (
        current["close"] > recent_high
        and current["close"] > current["open"]
        and rvol >= RVOL_ABNORMAL
    ):

        return "LONG"

    # Bearish breakout
    if (
        current["close"] < recent_low
        and current["close"] < current["open"]
        and rvol >= RVOL_ABNORMAL
    ):

        return "SHORT"

    return None


# ============================================================
# HIGH VOLUME REJECTION
# ============================================================

def detect_rejection(
    df,
    rvol
):

    if df is None or len(df) < 10:
        return None

    if rvol < RVOL_ABNORMAL:
        return None

    candle = df.iloc[-1]

    high = float(candle["high"])

    low = float(candle["low"])

    open_price = float(candle["open"])

    close = float(candle["close"])

    total_range = high - low

    if total_range <= 0:
        return None

    upper_wick = (
        high - max(
            open_price,
            close
        )
    )

    lower_wick = (
        min(
            open_price,
            close
        ) - low
    )

    # Bearish rejection
    if (
        upper_wick / total_range >= 0.45
        and close < open_price
    ):

        return "SHORT"

    # Bullish rejection
    if (
        lower_wick / total_range >= 0.45
        and close > open_price
    ):

        return "LONG"

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df,
    side
):

    if df is None or len(df) < 5:
        return False

    candle = df.iloc[-1]

    previous = df.iloc[-2]

    close = float(candle["close"])

    open_price = float(candle["open"])

    prev_close = float(
        previous["close"]
    )

    if side == "LONG":

        bullish = (
            close > open_price
        )

        momentum = (
            close > prev_close
        )

        return (
            bullish
            and momentum
        )

    if side == "SHORT":

        bearish = (
            close < open_price
        )

        momentum = (
            close < prev_close
        )

        return (
            bearish
            and momentum
        )

    return False


# ============================================================
# RETEST
# ============================================================

def detect_retest(
    df,
    level,
    side
):

    if (
        df is None
        or level is None
        or len(df) < 5
    ):
        return False

    candle = df.iloc[-1]

    tolerance = (
        abs(level) * 0.002
    )

    if side == "LONG":

        touched = (
            candle["low"]
            <= level + tolerance
        )

        recovered = (
            candle["close"]
            > level
        )

        return (
            touched
            and recovered
        )

    if side == "SHORT":

        touched = (
            candle["high"]
            >= level - tolerance
        )

        rejected = (
            candle["close"]
            < level
        )

        return (
            touched
            and rejected
        )

    return False


# ============================================================
# TRADE CALCULATION
# ============================================================

def calculate_trade(
    side,
    entry,
    static_support,
    static_resistance,
    dynamic_support,
    dynamic_resistance,
    atr
):

    if entry is None or entry <= 0:
        return None

    if atr is None or atr <= 0:
        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        support_candidates = []

        for level in (
            static_support
            + dynamic_support
        ):

            if level < entry:
                support_candidates.append(
                    level
                )

        if not support_candidates:
            return None

        # Closest structural support
        structural_support = max(
            support_candidates
        )

        sl = (
            structural_support
            - atr * ATR_BUFFER_MULT
        )

        # TP = next valid STATIC resistance
        resistance_candidates = [
            x for x in static_resistance
            if x > entry
        ]

        if not resistance_candidates:
            return None

        tp = min(
            resistance_candidates
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif side == "SHORT":

        resistance_candidates = []

        for level in (
            static_resistance
            + dynamic_resistance
        ):

            if level > entry:
                resistance_candidates.append(
                    level
                )

        if not resistance_candidates:
            return None

        structural_resistance = min(
            resistance_candidates
        )

        sl = (
            structural_resistance
            + atr * ATR_BUFFER_MULT
        )

        # TP = next valid STATIC support
        support_candidates = [
            x for x in static_support
            if x < entry
        ]

        if not support_candidates:
            return None

        tp = max(
            support_candidates
        )

    else:
        return None

    if sl <= 0 or tp <= 0:
        return None

    # Safety direction check
    if side == "LONG":

        if sl >= entry:
            return None

        if tp <= entry:
            return None

    else:

        if sl <= entry:
            return None

        if tp >= entry:
            return None

    risk = abs(
        entry - sl
    )

    reward = abs(
        tp - entry
    )

    if risk <= 0:
        return None

    rr = reward / risk

    # --------------------------------------------------------
    # HARD SL LIMIT
    #
    # Structural SL is preserved.
    # Signal is rejected if structural SL
    # exceeds 0.80%.
    # --------------------------------------------------------

    sl_pct = (
        risk / entry
    )

    if sl_pct > MAX_SL_PCT:

        return None

    # --------------------------------------------------------
    # MINIMUM RR
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
    setup,
    trend,
    rvol,
    df15,
    df5,
    rr
):

    score = 0

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    if trend == side:

        score += 3

    elif trend == "NEUTRAL":

        score += 1

    # --------------------------------------------------------
    # Setup
    # --------------------------------------------------------

    if setup == "BREAKOUT":

        score += 3

    elif setup == "REJECTION":

        score += 2

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    if rvol >= RVOL_STRONG:

        score += 3

    elif rvol >= RVOL_ABNORMAL:

        score += 2

    # --------------------------------------------------------
    # 5M candle
    # --------------------------------------------------------

    if df5 is not None and len(df5) >= 2:

        candle = df5.iloc[-1]

        if side == "LONG":

            if candle["close"] > candle["open"]:
                score += 2

        elif side == "SHORT":

            if candle["close"] < candle["open"]:
                score += 2

    # --------------------------------------------------------
    # 15M momentum
    # --------------------------------------------------------

    if df15 is not None and len(df15) >= 3:

        c1 = df15["close"].iloc[-1]

        c2 = df15["close"].iloc[-2]

        if side == "LONG" and c1 > c2:
            score += 1

        if side == "SHORT" and c1 < c2:
            score += 1

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    if rr >= 4:
        score += 2

    elif rr >= 3:
        score += 1

    return int(
        min(score, 15)
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    return conn


def init_db():

    conn = db_connect()

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
    # Migration
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
# OPEN CHECK
# ============================================================

def has_open_symbol(symbol):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM trades
        WHERE symbol = ?
        AND status = 'OPEN'
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()

    conn.close()

    return row is not None


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(signal):

    conn = db_connect()

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
            closed_at,
            exit_reason,
            closed_reported
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
            'OPEN',
            NULL,
            NULL,
            ?,
            NULL,
            NULL,
            0
        )
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
        signal["created_at"]
    ))

    conn.commit()

    trade_id = cur.lastrowid

    conn.close()

    return trade_id


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(
    price_map
):

    newly_closed_ids = []

    conn = db_connect()

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

    current_time = iso_now()

    for (
        trade_id,
        symbol,
        side,
        entry,
        sl,
        tp
    ) in rows:

        price = price_map.get(
            symbol
        )

        if price is None:
            continue

        result = None
        reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "LONG":

            if price >= tp:

                result = "WIN"
                reason = "TP"

                pnl = (
                    (tp - entry)
                    / entry
                    * 100
                )

            elif price <= sl:

                result = "LOSS"
                reason = "SL"

                pnl = (
                    (sl - entry)
                    / entry
                    * 100
                )

            else:
                continue

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if price <= tp:

                result = "WIN"
                reason = "TP"

                pnl = (
                    (entry - tp)
                    / entry
                    * 100
                )

            elif price >= sl:

                result = "LOSS"
                reason = "SL"

                pnl = (
                    (entry - sl)
                    / entry
                    * 100
                )

            else:
                continue

        cur.execute("""
            UPDATE trades
            SET
                status = 'CLOSED',
                result = ?,
                pnl = ?,
                closed_at = ?,
                exit_reason = ?,
                closed_reported = 0
            WHERE id = ?
        """, (
            result,
            pnl,
            current_time,
            reason,
            trade_id
        ))

        newly_closed_ids.append(
            trade_id
        )

    conn.commit()

    conn.close()

    return newly_closed_ids


# ============================================================
# GET UNREPORTED CLOSED
# ============================================================

def get_unreported_closed():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
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
            exit_reason
        FROM trades
        WHERE status = 'CLOSED'
        AND closed_reported = 0
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    conn.close()

    columns = [
        "id",
        "symbol",
        "side",
        "setup",
        "entry",
        "sl",
        "tp",
        "rr",
        "score",
        "rvol",
        "trend",
        "status",
        "result",
        "pnl",
        "created_at",
        "closed_at",
        "exit_reason"
    ]

    return [
        dict(
            zip(columns, row)
        )
        for row in rows
    ]


# ============================================================
# MARK CLOSED REPORTED
# ============================================================

def mark_closed_reported(
    trade_ids
):

    if not trade_ids:
        return

    conn = db_connect()

    cur = conn.cursor()

    for trade_id in trade_ids:

        cur.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE id = ?
        """, (
            trade_id,
        ))

    conn.commit()

    conn.close()


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
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
            created_at
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id DESC
    """)

    rows = cur.fetchall()

    conn.close()

    columns = [
        "id",
        "symbol",
        "side",
        "setup",
        "entry",
        "sl",
        "tp",
        "rr",
        "score",
        "rvol",
        "trend",
        "created_at"
    ]

    return [
        dict(
            zip(columns, row)
        )
        for row in rows
    ]


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """)

    open_count = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
    """)

    closed_count = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'WIN'
    """)

    wins = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'LOSS'
    """)

    losses = cur.fetchone()[0]

    cur.execute("""
        SELECT
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status = 'CLOSED'
    """)

    net_pnl = (
        cur.fetchone()[0]
        or 0
    )

    # LONG
    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE side = 'LONG'
        AND status = 'CLOSED'
    """)

    long_closed, long_pnl = (
        cur.fetchone()
    )

    # SHORT
    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE side = 'SHORT'
        AND status = 'CLOSED'
    """)

    short_closed, short_pnl = (
        cur.fetchone()
    )

    # BREAKOUT
    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE setup = 'BREAKOUT'
        AND status = 'CLOSED'
    """)

    breakout_closed, breakout_pnl = (
        cur.fetchone()
    )

    # REJECTION
    cur.execute("""
        SELECT
            COUNT(*),
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE setup = 'REJECTION'
        AND status = 'CLOSED'
    """)

    rejection_closed, rejection_pnl = (
        cur.fetchone()
    )

    conn.close()

    win_rate = (
        wins / closed_count * 100
        if closed_count
        else 0
    )

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "long_closed": long_closed,
        "long_pnl": long_pnl,
        "short_closed": short_closed,
        "short_pnl": short_pnl,
        "breakout_closed": breakout_closed,
        "breakout_pnl": breakout_pnl,
        "rejection_closed": rejection_closed,
        "rejection_pnl": rejection_pnl
    }


# ============================================================
# PRICE MAP
# ============================================================

def build_price_map(
    markets
):

    result = {}

    for market in markets:

        symbol = market["symbol"]

        price = safe_float(
            market.get("last")
        )

        if price:

            result[symbol] = price

    return result


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.6f}".rstrip("0").rstrip(".")

    if value >= 0.01:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    return f"{value:.12f}".rstrip("0").rstrip(".")


# ============================================================
# NEW SIGNAL FORMAT
# ============================================================

def format_new_signal(
    signal
):

    icon = (
        "🟢"
        if signal["side"] == "LONG"
        else "🔴"
    )

    return (
        f"{icon} {signal['symbol']} "
        f"{signal['side']}\n"
        f"📌 {signal['setup']} | "
        f"⭐ {signal['score']} | "
        f"RVOL {signal['rvol']:.2f}x | "
        f"RR 1:{signal['rr']:.2f}\n"
        f"💰 Entry: {fmt_price(signal['entry'])}\n"
        f"🛑 SL: {fmt_price(signal['sl'])} "
        f"({signal['sl_pct'] * 100:.2f}%)\n"
        f"🎯 TP: {fmt_price(signal['tp'])}\n"
    )


# ============================================================
# OPEN FORMAT
# ============================================================

def format_open_trade(
    trade,
    current_price=None
):

    icon = (
        "🟢"
        if trade["side"] == "LONG"
        else "🔴"
    )

    pnl_text = ""

    if current_price is not None:

        if trade["side"] == "LONG":

            pnl = (
                current_price
                - trade["entry"]
            ) / trade["entry"] * 100

        else:

            pnl = (
                trade["entry"]
                - current_price
            ) / trade["entry"] * 100

        pnl_text = (
            f"\n📊 Live PnL: "
            f"{pnl:+.2f}%"
        )

    return (
        f"{icon} {trade['symbol']} "
        f"{trade['side']}\n"
        f"📌 {trade['setup']} | "
        f"⭐ {trade['score']} | "
        f"RR 1:{trade['rr']:.2f}\n"
        f"💰 Entry: {fmt_price(trade['entry'])}\n"
        f"🛑 SL: {fmt_price(trade['sl'])}\n"
        f"🎯 TP: {fmt_price(trade['tp'])}"
        f"{pnl_text}\n"
    )


# ============================================================
# CLOSED FORMAT
# ============================================================

def format_closed_trade(
    trade
):

    icon = (
        "✅"
        if trade["result"] == "WIN"
        else "❌"
    )

    reason = (
        trade["exit_reason"]
        or trade["result"]
        or "-"
    )

    return (
        f"{icon} {trade['symbol']} "
        f"{trade['side']} → {reason}\n"
        f"📊 PnL: "
        f"{trade['pnl']:+.2f}%\n"
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    new_signals,
    open_trades,
    closed_trades,
    stats,
    scan_stats,
    scan_seconds
):

    lines = []

    lines.append(
        "🤖 VOLUME-KHAT 100"
    )

    lines.append(
        "📡 VOLUME-KHAT 100 v2.5"
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

    # --------------------------------------------------------
    # STRATEGY
    # --------------------------------------------------------

    lines.append(
        "📐 STRATEGY"
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
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append(
        "🚨 NEW SIGNALS"
    )

    if new_signals:

        for signal in new_signals:

            lines.append(
                format_new_signal(
                    signal
                )
            )

    else:

        lines.append(
            "No qualified new signal."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    lines.append(
        "📂 OPEN SIGNALS"
    )

    if open_trades:

        price_map = scan_stats.get(
            "price_map",
            {}
        )

        for trade in open_trades:

            current_price = price_map.get(
                trade["symbol"]
            )

            lines.append(
                format_open_trade(
                    trade,
                    current_price
                )
            )

    else:

        lines.append(
            "No open signals."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    lines.append(
        "📌 CLOSED SINCE PREVIOUS REPORT"
    )

    if closed_trades:

        for trade in closed_trades:

            lines.append(
                format_closed_trade(
                    trade
                )
            )

    else:

        lines.append(
            "No newly closed signals."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    lines.append(
        "📈 STATS"
    )

    lines.append(
        f"Open: {stats['open']}"
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

    lines.append(
        ""
    )

    lines.append(
        "LONG: "
        f"{stats['long_closed']} closed | "
        f"{stats['long_pnl']:+.2f}%"
    )

    lines.append(
        "SHORT: "
        f"{stats['short_closed']} closed | "
        f"{stats['short_pnl']:+.2f}%"
    )

    lines.append(
        "BREAKOUT: "
        f"{stats['breakout_closed']} closed | "
        f"{stats['breakout_pnl']:+.2f}%"
    )

    lines.append(
        "REJECTION: "
        f"{stats['rejection_closed']} closed | "
        f"{stats['rejection_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    lines.append(
        "📊 SCAN"
    )

    lines.append(
        f"Markets: {scan_stats['markets']}"
    )

    lines.append(
        f"Data: {scan_stats['data']}"
    )

    lines.append(
        f"RVOL≥2: {scan_stats['rvol_abnormal']}"
    )

    lines.append(
        f"BO: {scan_stats['breakouts']}"
    )

    lines.append(
        f"REJ: {scan_stats['rejections']}"
    )

    lines.append(
        f"5M Confirm: {scan_stats['confirmations']}"
    )

    lines.append(
        f"RR≥1:2: {scan_stats['rr_pass']}"
    )

    lines.append(
        f"SL≤0.80%: {scan_stats['sl_pass']}"
    )

    lines.append(
        f"Qualified: {scan_stats['qualified']}"
    )

    lines.append(
        f"New selected: {len(new_signals)}"
    )

    lines.append(
        f"⏱ {scan_seconds:.1f}s"
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    text
):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "TELEGRAM_BOT_TOKEN not configured."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "TELEGRAM_CHAT_ID not configured."
        )
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        print(
            "Telegram report sent successfully."
        )

        return True

    except Exception as e:

        print(
            f"Telegram ERROR: {e}"
        )

        return False


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    market
):

    symbol = market["symbol"]

    result = {
        "symbol": symbol,
        "data": False,
        "rvol": 0.0,
        "setup": None,
        "side": None,
        "confirmed": False,
        "trade": None,
        "score": 0,
        "trend": "NEUTRAL"
    }

    try:

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        df1h = get_candles(
            symbol,
            TF_1H
        )

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        df15 = get_candles(
            symbol,
            TF_15M
        )

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df5 = get_candles(
            symbol,
            TF_5M
        )

        if (
            df1h is None
            or df15 is None
            or df5 is None
        ):
            return result

        if (
            len(df1h) < 60
            or len(df15) < 30
            or len(df5) < 10
        ):
            return result

        result["data"] = True

        # ----------------------------------------------------
        # Trend
        # ----------------------------------------------------

        trend = get_trend(
            df1h
        )

        result["trend"] = trend

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            df15
        )

        result["rvol"] = rvol

        # ----------------------------------------------------
        # Static S/R from 1H
        # ----------------------------------------------------

        static_support, static_resistance = (
            get_static_sr(
                df1h
            )
        )

        # ----------------------------------------------------
        # Dynamic S/R from 15M
        # ----------------------------------------------------

        dynamic_support, dynamic_resistance = (
            get_dynamic_sr(
                df15
            )
        )

        # ----------------------------------------------------
        # Setup
        # ----------------------------------------------------

        breakout_side = detect_breakout(
            df15,
            rvol
        )

        rejection_side = detect_rejection(
            df15,
            rvol
        )

        setup = None
        side = None

        # Prefer breakout when both occur
        if breakout_side:

            setup = "BREAKOUT"

            side = breakout_side

        elif rejection_side:

            setup = "REJECTION"

            side = rejection_side

        if not setup or not side:

            return result

        result["setup"] = setup

        result["side"] = side

        # ----------------------------------------------------
        # Trend filter
        #
        # Do not completely kill neutral setups.
        # Opposite trend is rejected.
        # ----------------------------------------------------

        if (
            trend != "NEUTRAL"
            and trend != side
        ):
            return result

        # ----------------------------------------------------
        # 5M confirmation
        # ----------------------------------------------------

        confirmed = confirm_5m(
            df5,
            side
        )

        if not confirmed:

            return result

        result["confirmed"] = True

        # ----------------------------------------------------
        # Entry
        # ----------------------------------------------------

        entry = safe_float(
            df5["close"].iloc[-1]
        )

        if entry is None:
            return result

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = calculate_atr(
            df15
        )

        if atr is None:
            return result

        # ----------------------------------------------------
        # Trade
        # ----------------------------------------------------

        trade = calculate_trade(
            side=side,
            entry=entry,
            static_support=static_support,
            static_resistance=static_resistance,
            dynamic_support=dynamic_support,
            dynamic_resistance=dynamic_resistance,
            atr=atr
        )

        if trade is None:

            return result

        result["trade"] = trade

        # ----------------------------------------------------
        # Score
        # ----------------------------------------------------

        score = calculate_score(
            side=side,
            setup=setup,
            trend=trend,
            rvol=rvol,
            df15=df15,
            df5=df5,
            rr=trade["rr"]
        )

        result["score"] = score

        return result

    except Exception as e:

        print(
            f"SCAN ERROR {symbol}: {e}"
        )

        return result


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print()
    print("============================================================")
    print("VOLUME-KHAT 100")
    print("============================================================")
    print()
    print("SYSTEM: GitHub Actions")
    print("EXCHANGE: Kraken Futures")
    print("MARKETS: TOP 100 USD PERPETUAL")
    print()
    print("============================================================")
    print("STRATEGY")
    print("============================================================")
    print()
    print("1H  = Trend + Static Support/Resistance")
    print("15M = Dynamic Support/Resistance + RVOL + Setup")
    print("5M  = Entry Confirmation")
    print()
    print("============================================================")
    print("VOLUME")
    print("============================================================")
    print()
    print("RVOL baseline: 20 candles")
    print("Abnormal Volume: RVOL >= 2.0")
    print("Strong Volume: RVOL >= 3.0")
    print()
    print("============================================================")
    print("SETUPS")
    print("============================================================")
    print()
    print("BREAKOUT")
    print("HIGH-VOLUME REJECTION")
    print()
    print("============================================================")
    print("RISK MANAGEMENT")
    print("============================================================")
    print()
    print("Minimum RR: 1:2")
    print("Maximum SL distance: 0.80%")
    print("SL: Structural + ATR buffer")
    print("TP: Next valid static S/R")
    print()
    print("============================================================")
    print("STARTING SCANNER")
    print("============================================================")
    print()

    init_db()

    # ========================================================
    # MARKET DISCOVERY
    # ========================================================

    markets = discover_top_markets()

    if not markets:

        print()
        print("ERROR: No markets found.")
        print(
            "Scanner stopped before market scanning."
        )

        error_report = (
            "🤖 VOLUME-KHAT 100\n"
            "❌ MARKET DISCOVERY ERROR\n\n"
            "Kraken returned 0 usable USD/USDT "
            "perpetual markets.\n\n"
            "Scanner did NOT perform the strategy scan."
        )

        send_telegram(
            error_report
        )

        return

    # ========================================================
    # PRICE MAP
    # ========================================================

    price_map = build_price_map(
        markets
    )

    # ========================================================
    # UPDATE OPEN TRADES
    #
    # IMPORTANT:
    # These trades are marked closed now,
    # but are NOT included in the current report.
    #
    # They will appear on the NEXT successful report.
    # ========================================================

    newly_closed_ids = (
        update_open_trades(
            price_map
        )
    )

    print(
        f"Newly closed this run: "
        f"{len(newly_closed_ids)}"
    )

    # ========================================================
    # SCAN
    # ========================================================

    scan_results = []

    data_count = 0

    rvol_abnormal = 0

    breakouts = 0

    rejections = 0

    confirmations = 0

    rr_pass = 0

    sl_pass = 0

    qualified = 0

    print()
    print(
        f"Scanning {len(markets)} markets..."
    )
    print()

    for index, market in enumerate(
        markets,
        start=1
    ):

        symbol = market["symbol"]

        print(
            f"[{index:03d}/{len(markets):03d}] "
            f"{symbol}"
        )

        result = scan_market(
            market
        )

        scan_results.append(
            result
        )

        if result["data"]:
            data_count += 1

        if result["rvol"] >= RVOL_ABNORMAL:
            rvol_abnormal += 1

        if result["setup"] == "BREAKOUT":
            breakouts += 1

        if result["setup"] == "REJECTION":
            rejections += 1

        if result["confirmed"]:
            confirmations += 1

        trade = result.get(
            "trade"
        )

        if trade:

            rr_pass += 1

            if (
                trade["sl_pct"]
                <= MAX_SL_PCT
            ):
                sl_pass += 1

        if (
            result["trade"] is not None
            and result["score"]
            >= MIN_SIGNAL_SCORE
        ):

            qualified += 1

    # ========================================================
    # CANDIDATES
    # ========================================================

    candidates = []

    for result in scan_results:

        trade = result.get(
            "trade"
        )

        if trade is None:
            continue

        if result["score"] < MIN_SIGNAL_SCORE:
            continue

        symbol = result["symbol"]

        # Do not create another open trade
        if has_open_symbol(symbol):
            continue

        signal = {
            "symbol": symbol,
            "side": result["side"],
            "setup": result["setup"],
            "entry": trade["entry"],
            "sl": trade["sl"],
            "tp": trade["tp"],
            "rr": trade["rr"],
            "sl_pct": trade["sl_pct"],
            "score": result["score"],
            "rvol": result["rvol"],
            "trend": result["trend"],
            "created_at": iso_now()
        }

        candidates.append(
            signal
        )

    # ========================================================
    # RANK
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["rr"]
        ),
        reverse=True
    )

    # ========================================================
    # MAX 3 NEW SIGNALS
    # ========================================================

    selected = candidates[
        :MAX_NEW_SIGNALS
    ]

    # ========================================================
    # SAVE
    # ========================================================

    new_signals = []

    for signal in selected:

        trade_id = save_signal(
            signal
        )

        signal["id"] = trade_id

        new_signals.append(
            signal
        )

    # ========================================================
    # CLOSED TRADES
    #
    # IMPORTANT:
    # Exclude trades closed in THIS run.
    # They will be reported NEXT run.
    # ========================================================

    pending_closed = (
        get_unreported_closed()
    )

    current_closed_set = set(
        newly_closed_ids
    )

    closed_for_report = [
        trade
        for trade in pending_closed
        if trade["id"]
        not in current_closed_set
    ]

    # ========================================================
    # OPEN TRADES AFTER NEW SIGNALS
    # ========================================================

    open_trades = get_open_trades()

    stats = get_stats()

    scan_seconds = (
        time.time()
        - start_time
    )

    scan_stats = {
        "markets": len(markets),
        "data": data_count,
        "rvol_abnormal": rvol_abnormal,
        "breakouts": breakouts,
        "rejections": rejections,
        "confirmations": confirmations,
        "rr_pass": rr_pass,
        "sl_pass": sl_pass,
        "qualified": qualified,
        "price_map": price_map
    }

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        new_signals=new_signals,
        open_trades=open_trades,
        closed_trades=closed_for_report,
        stats=stats,
        scan_stats=scan_stats,
        scan_seconds=scan_seconds
    )

    # Telegram limit safety
    if len(report) > 3900:

        report = report[
            :3850
        ]

        report += (
            "\n\n⚠️ Report truncated."
        )

    print()
    print(
        "============================================================"
    )
    print("FINAL REPORT")
    print(
        "============================================================"
    )
    print()
    print(report)
    print()

    # ========================================================
    # SEND TELEGRAM
    # ========================================================

    telegram_success = send_telegram(
        report
    )

    # ========================================================
    # ONLY MARK CLOSED TRADES AS REPORTED
    # IF TELEGRAM ACTUALLY SUCCEEDED
    # ========================================================

    if telegram_success:

        report_ids = [
            trade["id"]
            for trade in closed_for_report
        ]

        mark_closed_reported(
            report_ids
        )

        print(
            f"Marked "
            f"{len(report_ids)} closed trades "
            f"as reported."
        )

    else:

        print(
            "Telegram failed."
        )

        print(
            "Closed trades remain unreported "
            "for the next successful run."
        )

    # ========================================================
    # EXIT
    # ========================================================

    print()
    print(
        "============================================================"
    )

    print(
        f"SCANNER FINISHED IN "
        f"{scan_seconds:.2f}s"
    )

    print(
        f"Markets scanned: "
        f"{len(markets)}"
    )

    print(
        f"Qualified: "
        f"{qualified}"
    )

    print(
        f"New signals selected: "
        f"{len(new_signals)}"
    )

    print(
        f"Newly closed this run: "
        f"{len(newly_closed_ids)}"
    )

    print(
        "REAL TRADING: DISABLED"
    )

    print(
        "============================================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
