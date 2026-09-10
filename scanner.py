# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v3.0
# ============================================================
#
# FEATURES
# ------------------------------------------------------------
# Kraken Futures TOP 100 USD perpetual markets
#
# 1H  = Trend + Static S/R
# 15M = Dynamic S/R + RVOL + Setup
# 5M  = Entry confirmation
#
# SETUPS:
#   - BREAKOUT
#   - HIGH-VOLUME REJECTION
#
# RISK:
#   - Minimum RR 1:2
#   - Maximum SL 0.80%
#   - Structural SL + ATR buffer
#   - TP = nearest valid S/R
#
# TRADE STATE:
#   - Maximum 3 OPEN trades
#   - One OPEN trade per symbol
#   - Cooldown 3 candles
#   - SQLite persistent database
#
# EXIT TRACKING:
#   - CLOSED 5M candle
#   - SL/TP detection
#   - SL-first if both touched
#
# TELEGRAM:
#   - Clean compact report
#   - Current price for OPEN trades
#   - Unrealized PnL %
#   - Closed-trade statistics
#   - CLOSED shown only once
#   - Diagnostic scan counters
#
# MARKET DISCOVERY:
#   - Primary: instruments endpoint
#   - Fallback: tickers endpoint
#   - PF_...USD perpetual symbols
#
# REAL TRADING:
#   FALSE
#
# ============================================================

import os
import math
import sqlite3
import traceback
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE + "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE + "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE + "/api/charts/v1/trade"
)


# ============================================================
# SCANNER SETTINGS
# ============================================================

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

REQUEST_TIMEOUT = 20

REAL_TRADING = False


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


# ============================================================
# HTTP SESSION
# ============================================================

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; KRAKEN-VOLUME-KHAT/3.0)"
)

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
})


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_str():
    return now_utc().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        if isinstance(value, bool):
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except Exception:

        return default


def normalize_symbol(symbol):

    if not symbol:
        return ""

    return (
        str(symbol)
        .upper()
        .replace("_", "")
        .replace("-", "")
        .replace("/", "")
        .strip()
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    params=None,
    timeout=REQUEST_TIMEOUT
):

    response = SESSION.get(
        url,
        params=params,
        timeout=timeout
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# GENERIC LIST EXTRACTION
# ============================================================

def extract_list(data, keys):

    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        return []

    for key in keys:

        value = data.get(key)

        if isinstance(value, list):
            return value

    return []


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_instruments():

    try:

        data = http_get(
            INSTRUMENTS_URL
        )

        instruments = extract_list(
            data,
            [
                "instruments",
                "result",
                "data"
            ]
        )

        print(
            f"[INSTRUMENTS] Received "
            f"{len(instruments)} instruments."
        )

        return instruments

    except Exception as exc:

        print(
            "[INSTRUMENTS ERROR]",
            repr(exc)
        )

        return []


# ============================================================
# KRAKEN TICKERS
# ============================================================

def get_tickers():

    try:

        data = http_get(
            TICKERS_URL
        )

        tickers = extract_list(
            data,
            [
                "tickers",
                "result",
                "data"
            ]
        )

        print(
            f"[TICKERS] Received "
            f"{len(tickers)} tickers."
        )

        return tickers

    except Exception as exc:

        print(
            "[TICKERS ERROR]",
            repr(exc)
        )

        return []


# ============================================================
# TICKER INDEX
# ============================================================

def build_ticker_index(tickers):

    index = {}

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        symbol = ticker.get("symbol")

        if not symbol:
            continue

        raw = str(symbol).upper()

        index[raw] = ticker

        index[
            normalize_symbol(raw)
        ] = ticker

    return index


# ============================================================
# TICKER VOLUME
# ============================================================

def ticker_volume_24h(ticker):

    if not isinstance(ticker, dict):
        return 0.0

    candidates = [
        "volume24h",
        "volume_24h",
        "volume",
        "vol24h",
        "volumeQuote24h",
        "quoteVolume24h",
    ]

    for key in candidates:

        if key not in ticker:
            continue

        value = safe_float(
            ticker.get(key)
        )

        if value > 0:
            return value

    return 0.0


# ============================================================
# TICKER PRICE
# ============================================================

def ticker_price(ticker):

    if not isinstance(ticker, dict):
        return 0.0

    candidates = [
        "last",
        "lastPrice",
        "price",
        "markPrice",
        "indexPrice",
    ]

    for key in candidates:

        if key not in ticker:
            continue

        value = safe_float(
            ticker.get(key)
        )

        if value > 0:
            return value

    return 0.0


# ============================================================
# PERPETUAL FILTER
# ============================================================

def is_pf_usd_symbol(symbol):

    if not symbol:
        return False

    symbol = str(symbol).upper().strip()

    if not symbol.startswith("PF_"):
        return False

    if not symbol.endswith("USD"):
        return False

    return True


# ============================================================
# INSTRUMENT FILTER
# ============================================================

def instrument_is_pf_usd(inst):

    if not isinstance(inst, dict):
        return False

    symbol = str(
        inst.get("symbol", "")
    ).upper()

    return is_pf_usd_symbol(symbol)


# ============================================================
# MARKET DISCOVERY
# ============================================================

def discover_top_markets():

    print(
        "[MARKETS] Discovering "
        "Kraken Futures PF_*USD perpetuals..."
    )

    instruments = get_instruments()

    instrument_symbols = []

    for inst in instruments:

        if not instrument_is_pf_usd(inst):
            continue

        symbol = str(
            inst.get("symbol", "")
        ).upper()

        if symbol:
            instrument_symbols.append(
                symbol
            )

    print(
        "[MARKETS] Instrument PF_*USD "
        f"candidates: {len(instrument_symbols)}"
    )

    tickers = get_tickers()

    ticker_index = build_ticker_index(
        tickers
    )

    ranked = []

    for symbol in instrument_symbols:

        ticker = ticker_index.get(
            symbol
        )

        if ticker is None:

            ticker = ticker_index.get(
                normalize_symbol(symbol)
            )

        volume = ticker_volume_24h(
            ticker
        )

        price = ticker_price(
            ticker
        )

        ranked.append({
            "symbol": symbol,
            "volume": volume,
            "price": price,
        })

    if not ranked:

        print(
            "[MARKETS] Instrument discovery "
            "returned zero PF_*USD markets."
        )

        print(
            "[MARKETS] Using ticker fallback..."
        )

        fallback_symbols = set()

        for ticker in tickers:

            if not isinstance(
                ticker,
                dict
            ):
                continue

            symbol = str(
                ticker.get(
                    "symbol",
                    ""
                )
            ).upper()

            if not is_pf_usd_symbol(
                symbol
            ):
                continue

            fallback_symbols.add(
                symbol
            )

        print(
            "[MARKETS] Ticker fallback "
            f"found {len(fallback_symbols)} "
            "PF_*USD symbols."
        )

        for symbol in fallback_symbols:

            ticker = ticker_index.get(
                symbol
            )

            volume = ticker_volume_24h(
                ticker
            )

            price = ticker_price(
                ticker
            )

            ranked.append({
                "symbol": symbol,
                "volume": volume,
                "price": price,
            })

    unique = {}

    for item in ranked:

        symbol = item["symbol"]

        if symbol not in unique:
            unique[symbol] = item

    ranked = list(
        unique.values()
    )

    ranked.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    selected = ranked[:TOP_N]

    print(
        f"[MARKETS] Selected "
        f"{len(selected)}"
    )

    if selected:

        print(
            "[MARKETS] TOP symbols:"
        )

        for i, market in enumerate(
            selected[:15],
            start=1
        ):

            print(
                f"   {i:02d}. "
                f"{market['symbol']} "
                f"VOL={market['volume']:.2f}"
            )

    else:

        print(
            "[MARKETS ERROR] "
            "No PF_*USD markets found."
        )

    return selected


# ============================================================
# LIVE PRICE MAP
# ============================================================

def get_live_price_map():

    tickers = get_tickers()

    result = {}

    for ticker in tickers:

        if not isinstance(
            ticker,
            dict
        ):
            continue

        symbol = ticker.get(
            "symbol"
        )

        if not symbol:
            continue

        price = ticker_price(
            ticker
        )

        if price <= 0:
            continue

        raw = str(
            symbol
        ).upper()

        result[raw] = price

        result[
            normalize_symbol(raw)
        ] = price

    return result


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle_rows(data):

    rows = extract_list(
        data,
        [
            "candles",
            "data",
            "result"
        ]
    )

    if not rows:
        return []

    output = []

    for row in rows:

        if isinstance(
            row,
            dict
        ):

            timestamp = (
                row.get("time")
                or row.get("timestamp")
                or row.get("ts")
            )

            open_price = (
                row.get("open")
                if row.get("open") is not None
                else row.get("o")
            )

            high = (
                row.get("high")
                if row.get("high") is not None
                else row.get("h")
            )

            low = (
                row.get("low")
                if row.get("low") is not None
                else row.get("l")
            )

            close = (
                row.get("close")
                if row.get("close") is not None
                else row.get("c")
            )

            volume = (
                row.get("volume")
                if row.get("volume") is not None
                else row.get("v")
            )

            output.append([
                timestamp,
                open_price,
                high,
                low,
                close,
                volume
            ])

        elif isinstance(
            row,
            (list, tuple)
        ):

            if len(row) >= 6:

                output.append(
                    list(row[:6])
                )

    return output


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit=OHLCV_LIMIT
):

    url = (
        f"{CHART_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    try:

        data = http_get(
            url
        )

        rows = parse_candle_rows(
            data
        )

        if not rows:

            return pd.DataFrame(
                columns=[
                    "timestamp",
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                ]
            )

        df = pd.DataFrame(
            rows,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df = df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ]
        )

        df = df.sort_values(
            "timestamp"
        )

        df = df.drop_duplicates(
            subset=["timestamp"]
        )

        # Remove latest unfinished candle
        if len(df) > 1:
            df = df.iloc[:-1]

        if limit:
            df = df.tail(limit)

        return df.reset_index(
            drop=True
        )

    except Exception as exc:

        print(
            f"[CANDLES ERROR] "
            f"{symbol} "
            f"{resolution}: "
            f"{exc}"
        )

        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )


# ============================================================
# ATR
# ============================================================

def add_atr(
    df,
    period=ATR_PERIOD
):

    if df.empty:
        return df

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    df["atr"] = (
        tr
        .rolling(
            period,
            min_periods=1
        )
        .mean()
    )

    return df


# ============================================================
# RVOL
# ============================================================

def add_rvol(
    df,
    period=RVOL_PERIOD
):

    if df.empty:
        return df

    baseline = (
        df["volume"]
        .rolling(
            period,
            min_periods=5
        )
        .mean()
        .shift(1)
    )

    df["rvol"] = (
        df["volume"]
        /
        baseline.replace(
            0,
            np.nan
        )
    )

    df["rvol"] = (
        df["rvol"]
        .replace(
            [np.inf, -np.inf],
            np.nan
        )
        .fillna(0)
    )

    return df


# ============================================================
# SWINGS
# ============================================================

def find_swing_highs(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    if len(df) < (
        left + right + 1
    ):
        return []

    values = (
        df["high"]
        .to_numpy()
    )

    swings = []

    for i in range(
        left,
        len(values) - right
    ):

        current = values[i]

        left_values = (
            values[i-left:i]
        )

        right_values = (
            values[i+1:i+right+1]
        )

        if (
            current >= left_values.max()
            and
            current >= right_values.max()
        ):

            swings.append(
                (
                    i,
                    float(current)
                )
            )

    return swings


def find_swing_lows(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    if len(df) < (
        left + right + 1
    ):
        return []

    values = (
        df["low"]
        .to_numpy()
    )

    swings = []

    for i in range(
        left,
        len(values) - right
    ):

        current = values[i]

        left_values = (
            values[i-left:i]
        )

        right_values = (
            values[i+1:i+right+1]
        )

        if (
            current <= left_values.min()
            and
            current <= right_values.min()
        ):

            swings.append(
                (
                    i,
                    float(current)
                )
            )

    return swings


# ============================================================
# S/R LEVELS
# ============================================================

def get_recent_swing_levels(
    df,
    max_levels=8
):

    highs = find_swing_highs(
        df
    )

    lows = find_swing_lows(
        df
    )

    resistance = [
        x[1]
        for x in highs[-max_levels:]
    ]

    support = [
        x[1]
        for x in lows[-max_levels:]
    ]

    return support, resistance


# ============================================================
# TREND
# ============================================================

def determine_trend(df):

    if len(df) < 20:
        return "NEUTRAL"

    close = df["close"]

    sma20 = (
        close
        .rolling(20)
        .mean()
        .iloc[-1]
    )

    if len(df) >= 50:

        sma50 = (
            close
            .rolling(50)
            .mean()
            .iloc[-1]
        )

    else:

        sma50 = (
            close
            .rolling(20)
            .mean()
            .iloc[-1]
        )

    last = float(
        close.iloc[-1]
    )

    if (
        last > sma20
        and
        sma20 >= sma50
    ):
        return "UP"

    if (
        last < sma20
        and
        sma20 <= sma50
    ):
        return "DOWN"

    return "NEUTRAL"


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(
    df,
    trend
):

    if len(df) < 25:
        return None

    support, resistance = (
        get_recent_swing_levels(df)
    )

    last = df.iloc[-1]
    previous = df.iloc[-2]

    close = float(
        last["close"]
    )

    previous_close = float(
        previous["close"]
    )

    rvol = safe_float(
        last.get("rvol")
    )

    # LONG breakout
    if (
        trend == "UP"
        and resistance
    ):

        level = max(
            resistance
        )

        if (
            previous_close <= level
            and
            close > level
            and
            rvol >= RVOL_ABNORMAL
        ):

            return {
                "side": "LONG",
                "setup": "BREAKOUT",
                "level": level,
                "rvol": rvol,
            }

    # SHORT breakout
    if (
        trend == "DOWN"
        and support
    ):

        level = min(
            support
        )

        if (
            previous_close >= level
            and
            close < level
            and
            rvol >= RVOL_ABNORMAL
        ):

            return {
                "side": "SHORT",
                "setup": "BREAKOUT",
                "level": level,
                "rvol": rvol,
            }

    return None


# ============================================================
# HIGH VOLUME REJECTION
# ============================================================

def detect_rejection(
    df,
    trend
):

    if len(df) < 25:
        return None

    support, resistance = (
        get_recent_swing_levels(df)
    )

    last = df.iloc[-1]

    high = float(
        last["high"]
    )

    low = float(
        last["low"]
    )

    open_price = float(
        last["open"]
    )

    close = float(
        last["close"]
    )

    rvol = safe_float(
        last.get("rvol")
    )

    candle_range = (
        high - low
    )

    if candle_range <= 0:
        return None

    upper_wick = (
        high
        -
        max(
            open_price,
            close
        )
    )

    lower_wick = (
        min(
            open_price,
            close
        )
        -
        low
    )

    # SHORT rejection
    if (
        trend == "DOWN"
        and
        resistance
        and
        rvol >= RVOL_ABNORMAL
    ):

        resistance_level = max(
            resistance
        )

        touched = (
            high >= resistance_level
        )

        bearish_close = (
            close < open_price
        )

        strong_upper_wick = (
            upper_wick
            >= candle_range * 0.35
        )

        if (
            touched
            and
            bearish_close
            and
            strong_upper_wick
        ):

            return {
                "side": "SHORT",
                "setup":
                    "HIGH-VOLUME REJECTION",
                "level":
                    resistance_level,
                "rvol": rvol,
            }

    # LONG rejection
    if (
        trend == "UP"
        and
        support
        and
        rvol >= RVOL_ABNORMAL
    ):

        support_level = min(
            support
        )

        touched = (
            low <= support_level
        )

        bullish_close = (
            close > open_price
        )

        strong_lower_wick = (
            lower_wick
            >= candle_range * 0.35
        )

        if (
            touched
            and
            bullish_close
            and
            strong_lower_wick
        ):

            return {
                "side": "LONG",
                "setup":
                    "HIGH-VOLUME REJECTION",
                "level":
                    support_level,
                "rvol": rvol,
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df,
    side
):

    if len(df) < 5:
        return False

    last = df.iloc[-1]

    open_price = float(
        last["open"]
    )

    close = float(
        last["close"]
    )

    high = float(
        last["high"]
    )

    low = float(
        last["low"]
    )

    candle_range = (
        high - low
    )

    if candle_range <= 0:
        return False

    body = abs(
        close - open_price
    )

    body_ratio = (
        body / candle_range
    )

    if side == "LONG":

        return (
            close > open_price
            and
            body_ratio >= 0.35
        )

    if side == "SHORT":

        return (
            close < open_price
            and
            body_ratio >= 0.35
        )

    return False


# ============================================================
# TRADE CALCULATION
# ============================================================

def calculate_trade(
    side,
    entry,
    df15,
    df1h
):

    support15, resistance15 = (
        get_recent_swing_levels(df15)
    )

    support1h, resistance1h = (
        get_recent_swing_levels(df1h)
    )

    atr = safe_float(
        df15.iloc[-1].get("atr")
    )

    if atr <= 0:

        atr = (
            entry * 0.002
        )

    atr_buffer = (
        atr * ATR_BUFFER_MULT
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        swing_candidates = [
            x
            for x in support15
            if x < entry
        ]

        if not swing_candidates:

            swing_candidates = [
                x
                for x in support1h
                if x < entry
            ]

        if not swing_candidates:
            return None

        swing_low = max(
            swing_candidates
        )

        sl = (
            swing_low
            -
            atr_buffer
        )

        tp_candidates = [
            x
            for x in (
                resistance1h
                +
                resistance15
            )
            if x > entry
        ]

        if not tp_candidates:
            return None

        tp = min(
            tp_candidates
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif side == "SHORT":

        swing_candidates = [
            x
            for x in resistance15
            if x > entry
        ]

        if not swing_candidates:

            swing_candidates = [
                x
                for x in resistance1h
                if x > entry
            ]

        if not swing_candidates:
            return None

        swing_high = min(
            swing_candidates
        )

        sl = (
            swing_high
            +
            atr_buffer
        )

        tp_candidates = [
            x
            for x in (
                support1h
                +
                support15
            )
            if x < entry
        ]

        if not tp_candidates:
            return None

        tp = max(
            tp_candidates
        )

    else:

        return None

    if side == "LONG":

        risk = (
            entry - sl
        )

        reward = (
            tp - entry
        )

    else:

        risk = (
            sl - entry
        )

        reward = (
            entry - tp
        )

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    sl_pct = (
        risk / entry
    )

    rr = (
        reward / risk
    )

    if sl_pct > MAX_SL_PCT:
        return None

    if rr < MIN_RR:
        return None

    return {
        "side": side,
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "rr": float(rr),
        "sl_pct": float(sl_pct),
    }


# ============================================================
# SIGNAL SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    rvol,
    confirmed
):

    score = 0

    if trend in (
        "UP",
        "DOWN"
    ):
        score += 3

    if setup in (
        "BREAKOUT",
        "HIGH-VOLUME REJECTION"
    ):
        score += 4

    if rvol >= RVOL_STRONG:

        score += 4

    elif rvol >= RVOL_ABNORMAL:

        score += 3

    if confirmed:

        score += 4

    return min(
        score,
        15
    )


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE
    )

    conn.row_factory = (
        sqlite3.Row
    )

    return conn


# ============================================================
# INIT DATABASE
# ============================================================

def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            setup TEXT,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            rr REAL,
            score REAL,
            status TEXT NOT NULL,
            result TEXT,
            pnl REAL DEFAULT 0,
            entry_time TEXT,
            exit_time TEXT,
            exit_reason TEXT,
            closed_reported INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    conn.commit()

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    columns = {
        row["name"]
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
            ADD COLUMN closed_reported
            INTEGER DEFAULT 0
        """)

    conn.commit()

    conn.close()


# ============================================================
# OPEN COUNT
# ============================================================

def get_open_count():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status='OPEN'
    """)

    count = int(
        cur.fetchone()[0]
    )

    conn.close()

    return count


# ============================================================
# MAX OPEN CLEANUP
# ============================================================

def enforce_max_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    excess = max(
        0,
        len(rows) - MAX_OPEN_TRADES
    )

    if excess <= 0:

        conn.close()

        return

    print(
        f"[OPEN LIMIT] "
        f"Removing {excess} excess OPEN trades."
    )

    for row in rows[:excess]:

        cur.execute("""
            UPDATE trades
            SET
                status='CLOSED',
                result='REMOVED',
                pnl=0,
                exit_time=?,
                exit_reason='OPEN_LIMIT_CLEANUP',
                closed_reported=1
            WHERE id=?
        """, (
            now_str(),
            row["id"]
        ))

        print(
            f"[REMOVED] "
            f"{row['symbol']} "
            f"{row['side']} "
            f"ID={row['id']}"
        )

    conn.commit()

    conn.close()


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(
    symbol,
    trade,
    setup,
    score
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT id
        FROM trades
        WHERE symbol=?
          AND status='OPEN'
        LIMIT 1
    """, (
        symbol,
    ))

    existing = cur.fetchone()

    if existing:

        conn.close()

        return False

    cur.execute("""
        SELECT entry_time
        FROM trades
        WHERE symbol=?
        ORDER BY id DESC
        LIMIT 1
    """, (
        symbol,
    ))

    previous = cur.fetchone()

    if previous and previous["entry_time"]:

        try:

            previous_time = (
                datetime.strptime(
                    previous["entry_time"],
                    "%Y-%m-%d %H:%M:%S UTC"
                )
                .replace(
                    tzinfo=timezone.utc
                )
            )

            elapsed = (
                now_utc()
                -
                previous_time
            ).total_seconds()

            candle_seconds = (
                5 * 60
            )

            if (
                elapsed
                <
                candle_seconds
                *
                COOLDOWN_CANDLES
            ):

                conn.close()

                return False

        except Exception:

            pass

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
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason,
            closed_reported,
            created_at
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            'OPEN',
            NULL,
            0,
            ?,
            NULL,
            NULL,
            0,
            ?
        )
    """, (
        symbol,
        trade["side"],
        setup,
        trade["entry"],
        trade["sl"],
        trade["tp"],
        trade["rr"],
        score,
        now_str(),
        now_str()
    ))

    conn.commit()

    trade_id = cur.lastrowid

    conn.close()

    print(
        f"[OPEN] #{trade_id} "
        f"{symbol} "
        f"{trade['side']} "
        f"Entry={trade['entry']:.8g} "
        f"SL={trade['sl']:.8g} "
        f"TP={trade['tp']:.8g} "
        f"RR={trade['rr']:.2f}"
    )

    return True


# ============================================================
# CLOSED PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    exit_price
):

    if entry <= 0:
        return 0.0

    if side == "LONG":

        return (
            (exit_price - entry)
            /
            entry
        ) * 100.0

    if side == "SHORT":

        return (
            (entry - exit_price)
            /
            entry
        ) * 100.0

    return 0.0


# ============================================================
# LIVE / UNREALIZED PNL
# ============================================================

def calculate_unrealized_pnl(
    side,
    entry,
    current
):

    if entry <= 0:
        return 0.0

    if current <= 0:
        return 0.0

    if side == "LONG":

        return (
            (current - entry)
            /
            entry
        ) * 100.0

    if side == "SHORT":

        return (
            (entry - current)
            /
            entry
        ) * 100.0

    return 0.0


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
    """)

    trades = cur.fetchall()

    if not trades:

        conn.close()

        return []

    newly_closed = []

    for trade in trades:

        symbol = trade["symbol"]

        df = get_candles(
            symbol,
            TF_5M,
            5
        )

        if df.empty:
            continue

        candle = df.iloc[-1]

        high = safe_float(
            candle["high"]
        )

        low = safe_float(
            candle["low"]
        )

        sl = safe_float(
            trade["sl"]
        )

        tp = safe_float(
            trade["tp"]
        )

        entry = safe_float(
            trade["entry"]
        )

        side = trade["side"]

        result = None
        exit_reason = None
        exit_price = None

        if side == "LONG":

            sl_hit = (
                low <= sl
            )

            tp_hit = (
                high >= tp
            )

            # SL-first if both touched
            if sl_hit:

                result = "LOSS"
                exit_reason = "SL"
                exit_price = sl

            elif tp_hit:

                result = "WIN"
                exit_reason = "TP"
                exit_price = tp

        elif side == "SHORT":

            sl_hit = (
                high >= sl
            )

            tp_hit = (
                low <= tp
            )

            # SL-first if both touched
            if sl_hit:

                result = "LOSS"
                exit_reason = "SL"
                exit_price = sl

            elif tp_hit:

                result = "WIN"
                exit_reason = "TP"
                exit_price = tp

        if result is None:
            continue

        pnl = calculate_pnl(
            side,
            entry,
            exit_price
        )

        cur.execute("""
            UPDATE trades
            SET
                status='CLOSED',
                result=?,
                pnl=?,
                exit_time=?,
                exit_reason=?,
                closed_reported=0
            WHERE id=?
        """, (
            result,
            pnl,
            now_str(),
            exit_reason,
            trade["id"]
        ))

        newly_closed.append(
            trade["id"]
        )

        print(
            f"[EXIT] "
            f"{symbol} "
            f"{side} -> "
            f"{result} "
            f"@ {exit_price:.8g} "
            f"({exit_reason}) "
            f"PnL={pnl:.3f}%"
        )

    conn.commit()

    conn.close()

    if newly_closed:

        print(
            "[EXIT] Newly closed:",
            newly_closed
        )

    return newly_closed


# ============================================================
# UNREPORTED CLOSED
# ============================================================

def get_unreported_closed():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM trades
        WHERE status='CLOSED'
          AND closed_reported=0
          AND result IN ('WIN', 'LOSS')
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# MARK REPORTED
# ============================================================

def mark_closed_reported(
    trade_ids
):

    if not trade_ids:
        return

    conn = db_connect()

    cur = conn.cursor()

    for trade_id in trade_ids:

        cur.execute(
            """
            UPDATE trades
            SET closed_reported=1
            WHERE id=?
            """,
            (trade_id,)
        )

    conn.commit()

    conn.close()


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(
                CASE
                    WHEN status='OPEN'
                    THEN 1
                END
            ) AS open_count,

            COUNT(
                CASE
                    WHEN status='CLOSED'
                    AND result IN ('WIN','LOSS')
                    THEN 1
                END
            ) AS closed_count,

            COUNT(
                CASE
                    WHEN result='WIN'
                    THEN 1
                END
            ) AS wins,

            COUNT(
                CASE
                    WHEN result='LOSS'
                    THEN 1
                END
            ) AS losses,

            COALESCE(
                SUM(
                    CASE
                        WHEN status='CLOSED'
                        AND result IN ('WIN','LOSS')
                        THEN pnl
                        ELSE 0
                    END
                ),
                0
            ) AS pnl

        FROM trades
    """)

    row = cur.fetchone()

    conn.close()

    open_count = int(
        row["open_count"] or 0
    )

    closed_count = int(
        row["closed_count"] or 0
    )

    wins = int(
        row["wins"] or 0
    )

    losses = int(
        row["losses"] or 0
    )

    pnl = safe_float(
        row["pnl"]
    )

    total_decided = (
        wins + losses
    )

    if total_decided:

        win_rate = (
            wins
            /
            total_decided
        ) * 100

    else:

        win_rate = 0.0

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "win_rate": win_rate,
    }


# ============================================================
# DIAGNOSTIC COUNTERS
# ============================================================

def new_diagnostics():

    return {
        "markets": 0,
        "data_ok": 0,
        "trend": 0,
        "setup": 0,
        "rvol": 0,
        "confirmation": 0,
        "trade_valid": 0,
        "score": 0,
        "final": 0,
        "data_failed": 0,
        "neutral": 0,
        "no_setup": 0,
        "no_confirmation": 0,
        "invalid_trade": 0,
        "low_score": 0,
    }


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    market,
    diagnostics=None
):

    symbol = market["symbol"]

    try:

        if diagnostics is not None:
            diagnostics["markets"] += 1

        df1h = get_candles(
            symbol,
            TF_1H,
            OHLCV_LIMIT
        )

        df15 = get_candles(
            symbol,
            TF_15M,
            OHLCV_LIMIT
        )

        df5 = get_candles(
            symbol,
            TF_5M,
            OHLCV_LIMIT
        )

        if (
            df1h.empty
            or
            df15.empty
            or
            df5.empty
        ):

            if diagnostics is not None:
                diagnostics["data_failed"] += 1

            return None

        if diagnostics is not None:
            diagnostics["data_ok"] += 1

        df1h = add_atr(
            df1h
        )

        df15 = add_atr(
            df15
        )

        df15 = add_rvol(
            df15
        )

        df5 = add_rvol(
            df5
        )

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        trend = determine_trend(
            df1h
        )

        if trend == "NEUTRAL":

            if diagnostics is not None:
                diagnostics["neutral"] += 1

            return None

        if diagnostics is not None:
            diagnostics["trend"] += 1

        # ----------------------------------------------------
        # SETUP
        # ----------------------------------------------------

        setup = detect_breakout(
            df15,
            trend
        )

        if setup is None:

            setup = detect_rejection(
                df15,
                trend
            )

        if setup is None:

            if diagnostics is not None:
                diagnostics["no_setup"] += 1

            return None

        if diagnostics is not None:
            diagnostics["setup"] += 1

        side = setup["side"]

        # ----------------------------------------------------
        # EXPLICIT RVOL DIAGNOSTIC
        # ----------------------------------------------------

        setup_rvol = safe_float(
            setup.get("rvol")
        )

        if (
            setup_rvol
            >= RVOL_ABNORMAL
        ):

            if diagnostics is not None:
                diagnostics["rvol"] += 1

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        confirmed = confirm_5m(
            df5,
            side
        )

        if not confirmed:

            if diagnostics is not None:
                diagnostics["no_confirmation"] += 1

            return None

        if diagnostics is not None:
            diagnostics["confirmation"] += 1

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = safe_float(
            df5.iloc[-1]["close"]
        )

        if entry <= 0:

            if diagnostics is not None:
                diagnostics["invalid_trade"] += 1

            return None

        # ----------------------------------------------------
        # SL / TP
        # ----------------------------------------------------

        trade = calculate_trade(
            side,
            entry,
            df15,
            df1h
        )

        if trade is None:

            if diagnostics is not None:
                diagnostics["invalid_trade"] += 1

            return None

        if diagnostics is not None:
            diagnostics["trade_valid"] += 1

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            trend,
            setup["setup"],
            setup["rvol"],
            confirmed
        )

        if score < MIN_SIGNAL_SCORE:

            if diagnostics is not None:
                diagnostics["low_score"] += 1

            return None

        if diagnostics is not None:
            diagnostics["score"] += 1

        trade["symbol"] = symbol

        trade["setup"] = (
            setup["setup"]
        )

        trade["trend"] = trend

        trade["rvol"] = (
            setup["rvol"]
        )

        trade["score"] = score

        if diagnostics is not None:
            diagnostics["final"] += 1

        return trade

    except Exception as exc:

        print(
            f"[SCAN ERROR] "
            f"{symbol}: {exc}"
        )

        if diagnostics is not None:
            diagnostics["data_failed"] += 1

        return None


# ============================================================
# SCAN ALL
# ============================================================

def scan_all_markets(
    markets,
    diagnostics=None
):

    signals = []

    print(
        f"[SCAN] Scanning "
        f"{len(markets)} markets..."
    )

    for index, market in enumerate(
        markets,
        start=1
    ):

        symbol = market[
            "symbol"
        ]

        print(
            f"[SCAN] "
            f"{index}/{len(markets)} "
            f"{symbol}"
        )

        signal = scan_market(
            market,
            diagnostics
        )

        if signal:

            signals.append(
                signal
            )

            print(
                f"[SIGNAL] "
                f"{symbol} "
                f"{signal['side']} "
                f"{signal['setup']} "
                f"Score="
                f"{signal['score']}"
            )

    signals.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return signals


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM] "
            "TELEGRAM_BOT_TOKEN missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM] "
            "TELEGRAM_CHAT_ID missing."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendMessage"
    )

    payload = {
        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            text,

        "disable_web_page_preview":
            True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        print(
            f"[TELEGRAM] HTTP "
            f"{response.status_code}"
        )

        print(
            "[TELEGRAM] Response:",
            response.text[:1000]
        )

        if not response.ok:
            return False

        try:

            data = response.json()

        except Exception:

            return False

        if data.get("ok"):

            print(
                "[TELEGRAM] Sent."
            )

            return True

        print(
            "[TELEGRAM] "
            "API returned ok=false."
        )

        return False

    except Exception as exc:

        print(
            "[TELEGRAM ERROR]",
            repr(exc)
        )

        return False


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    value = safe_float(
        value
    )

    if value <= 0:
        return "-"

    if value >= 1000:

        return f"{value:,.2f}"

    if value >= 1:

        return f"{value:.4f}"

    if value >= 0.01:

        return f"{value:.6f}"

    return f"{value:.8g}"


# ============================================================
# FORMAT PNL
# ============================================================

def fmt_pnl(value):

    value = safe_float(
        value
    )

    return f"{value:+.2f}%"


# ============================================================
# GET OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# BUILD DIAGNOSTIC SECTION
# ============================================================

def build_diagnostic_lines(
    diagnostics,
    signals
):

    lines = []

    lines.append(
        "🧪 DIAGNOSTIC"
    )

    lines.append(
        f"Markets {diagnostics['markets']} | "
        f"Data OK {diagnostics['data_ok']}"
    )

    lines.append(
        f"Trend {diagnostics['trend']} | "
        f"Setup {diagnostics['setup']}"
    )

    lines.append(
        f"Confirmation {diagnostics['confirmation']} | "
        f"Valid SL/TP {diagnostics['trade_valid']}"
    )

    lines.append(
        f"Score ≥ {MIN_SIGNAL_SCORE}: "
        f"{diagnostics['score']} | "
        f"Final: {len(signals)}"
    )

    lines.append(
        f"Neutral {diagnostics['neutral']} | "
        f"No Setup {diagnostics['no_setup']}"
    )

    lines.append(
        f"No 5M Confirm {diagnostics['no_confirmation']} | "
        f"Invalid SL/TP {diagnostics['invalid_trade']}"
    )

    lines.append(
        f"Low Score {diagnostics['low_score']} | "
        f"Data Errors {diagnostics['data_failed']}"
    )

    return lines


# ============================================================
# BUILD CLEAN REPORT
# ============================================================

def build_report(
    markets,
    signals,
    closed_trades,
    stats,
    diagnostics,
    market_error=None
):

    open_trades = get_open_trades()

    live_prices = {}

    if open_trades:

        live_prices = (
            get_live_price_map()
        )

    lines = []

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {now_str()}"
    )

    lines.append(
        "⚡ Kraken Futures | "
        "5M CLOSED"
    )

    # --------------------------------------------------------
    # MARKET STATUS
    # --------------------------------------------------------

    if market_error:

        lines.append(
            "⚠️ Market scan failed"
        )

    else:

        lines.append(
            f"🔎 Markets: "
            f"{len(markets)} | "
            f"Signals: "
            f"{len(signals)}"
        )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Open {stats['open']}/"
        f"{MAX_OPEN_TRADES} | "
        f"Closed {stats['closed']}"
    )

    lines.append(
        f"W {stats['wins']} | "
        f"L {stats['losses']} | "
        f"WR {stats['win_rate']:.1f}%"
    )

    lines.append(
        f"Realized PnL "
        f"{stats['pnl']:+.3f}%"
    )

    # --------------------------------------------------------
    # DIAGNOSTIC
    # --------------------------------------------------------

    if not market_error:

        lines.append("")

        lines.extend(
            build_diagnostic_lines(
                diagnostics,
                signals
            )
        )

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    # IMPORTANT:
    # Only unreported WIN/LOSS trades are passed here.
    # After successful Telegram delivery they are marked
    # closed_reported=1 and will never appear again.

    if closed_trades:

        lines.append("")

        lines.append(
            "🔔 CLOSED"
        )

        for trade in closed_trades:

            icon = (
                "✅"
                if trade["result"] == "WIN"
                else "❌"
            )

            if (
                trade["exit_reason"]
                == "TP"
            ):

                exit_price = (
                    trade["tp"]
                )

            else:

                exit_price = (
                    trade["sl"]
                )

            lines.append(
                f"{icon} "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"{trade['result']} "
                f"{trade['pnl']:+.2f}%"
            )

            lines.append(
                f"Entry "
                f"{fmt_price(trade['entry'])} "
                f"→ "
                f"Exit "
                f"{fmt_price(exit_price)}"
            )

    # --------------------------------------------------------
    # SIGNALS
    # --------------------------------------------------------

    if signals:

        lines.append("")

        lines.append(
            "🚨 NEW SIGNALS"
        )

        for signal in signals:

            lines.append(
                f"🔥 {signal['symbol']} "
                f"{signal['side']} "
                f"Score "
                f"{signal['score']}/15"
            )

            lines.append(
                f"Entry "
                f"{fmt_price(signal['entry'])} | "
                f"SL "
                f"{fmt_price(signal['sl'])} | "
                f"TP "
                f"{fmt_price(signal['tp'])}"
            )

            lines.append(
                f"RR 1:{signal['rr']:.2f} | "
                f"RVOL {signal['rvol']:.2f}"
            )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "📂 OPEN TRADES"
    )

    if open_trades:

        for trade in open_trades:

            symbol = trade[
                "symbol"
            ]

            side = trade[
                "side"
            ]

            entry = safe_float(
                trade["entry"]
            )

            current = (
                live_prices.get(
                    symbol,
                    0.0
                )
            )

            if current <= 0:

                current = (
                    live_prices.get(
                        normalize_symbol(
                            symbol
                        ),
                        0.0
                    )
                )

            unrealized = (
                calculate_unrealized_pnl(
                    side,
                    entry,
                    current
                )
            )

            if unrealized >= 0:

                pnl_icon = "🟢"

            else:

                pnl_icon = "🔴"

            lines.append(
                f"{pnl_icon} "
                f"{symbol} "
                f"{side} "
                f"PnL "
                f"{fmt_pnl(unrealized)}"
            )

            lines.append(
                f"Entry "
                f"{fmt_price(entry)} | "
                f"Now "
                f"{fmt_price(current)}"
            )

            lines.append(
                f"SL "
                f"{fmt_price(trade['sl'])} | "
                f"TP "
                f"{fmt_price(trade['tp'])} | "
                f"RR "
                f"1:{safe_float(trade['rr']):.2f}"
            )

    else:

        lines.append(
            "None"
        )

    # --------------------------------------------------------
    # ERROR
    # --------------------------------------------------------

    if market_error:

        lines.append("")

        lines.append(
            "⚠️ "
            + str(market_error)[:300]
        )

    # --------------------------------------------------------
    # FOOTER
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "🧪 PAPER TRADING"
    )

    # --------------------------------------------------------
    # SIZE PROTECTION
    # --------------------------------------------------------

    report = "\n".join(
        lines
    )

    if len(report) > 3900:

        report = (
            report[:3800]
            +
            "\n\n...[TRUNCATED]"
        )

    return report


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        "KRAKEN FUTURES "
        "VOLUME-KHAT 100 v3.0"
    )

    print(
        "PAPER TRADING ONLY"
    )

    print(
        "=================================================="
    )

    # --------------------------------------------------------
    # ENV
    # --------------------------------------------------------

    print(
        "[ENV] Telegram token:",
        "SET"
        if TELEGRAM_BOT_TOKEN
        else "MISSING"
    )

    print(
        "[ENV] Telegram chat ID:",
        "SET"
        if TELEGRAM_CHAT_ID
        else "MISSING"
    )

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    print(
        "[STEP 1] "
        "Checking existing trades..."
    )

    newly_closed_ids = (
        update_open_trades()
    )

    enforce_max_open_trades()

    stats = get_stats()

    print(
        f"[STATS] "
        f"OPEN={stats['open']} "
        f"CLOSED={stats['closed']} "
        f"W={stats['wins']} "
        f"L={stats['losses']} "
        f"PnL={stats['pnl']:.3f}%"
    )

    # --------------------------------------------------------
    # STEP 2
    # --------------------------------------------------------

    print(
        "[STEP 2] "
        "Discovering TOP 100 markets..."
    )

    markets = []

    market_error = None

    try:

        markets = (
            discover_top_markets()
        )

    except Exception as exc:

        market_error = (
            f"{type(exc).__name__}: "
            f"{exc}"
        )

        print(
            "[MARKETS FATAL]",
            market_error
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # NO MARKETS
    # --------------------------------------------------------

    if not markets:

        print(
            "[FATAL] "
            "No markets found."
        )

        closed_trades = (
            get_unreported_closed()
        )

        stats = get_stats()

        if market_error is None:

            market_error = (
                "Kraken returned "
                "zero PF_*USD markets."
            )

        diagnostics = new_diagnostics()

        report = build_report(
            markets=[],
            signals=[],
            closed_trades=
                closed_trades,
            stats=stats,
            diagnostics=
                diagnostics,
            market_error=
                market_error
        )

        print(
            "--------------------------------------------------"
        )

        print(report)

        print(
            "--------------------------------------------------"
        )

        telegram_ok = (
            send_telegram(
                report
            )
        )

        if telegram_ok:

            mark_closed_reported(
                [
                    row["id"]
                    for row
                    in closed_trades
                ]
            )

        print(
            "=================================================="
        )

        print(
            "SCANNER EXIT CODE: 0"
        )

        print(
            "=================================================="
        )

        return

    # --------------------------------------------------------
    # STEP 3
    # --------------------------------------------------------

    print(
        "[STEP 3] "
        "Scanning markets..."
    )

    open_count = (
        get_open_count()
    )

    slots_available = max(
        0,
        MAX_OPEN_TRADES
        -
        open_count
    )

    print(
        f"[CAPACITY] "
        f"OPEN={open_count}/"
        f"{MAX_OPEN_TRADES} "
        f"AVAILABLE={slots_available}"
    )

    signals = []

    diagnostics = new_diagnostics()

    # IMPORTANT:
    # We still scan the markets whenever capacity exists.
    # With 2/3 OPEN trades, one slot remains available.

    if slots_available > 0:

        all_signals = (
            scan_all_markets(
                markets,
                diagnostics
            )
        )

        signals = (
            all_signals[
                :min(
                    slots_available,
                    MAX_NEW_SIGNALS
                )
            ]
        )

    else:

        print(
            "[CAPACITY] "
            "Maximum open trades reached."
        )

    # --------------------------------------------------------
    # STEP 4
    # --------------------------------------------------------

    print(
        "[STEP 4] "
        "Saving signals..."
    )

    saved_count = 0

    for signal in signals:

        saved = save_signal(
            signal["symbol"],
            signal,
            signal["setup"],
            signal["score"]
        )

        if saved:

            saved_count += 1

    print(
        f"[SIGNALS] "
        f"Detected={len(signals)} "
        f"Saved={saved_count}"
    )

    # --------------------------------------------------------
    # STEP 5
    # --------------------------------------------------------

    print(
        "[STEP 5] "
        "Refreshing statistics..."
    )

    stats = get_stats()

    # --------------------------------------------------------
    # STEP 6
    # --------------------------------------------------------

    print(
        "[STEP 6] "
        "Preparing clean Telegram report..."
    )

    closed_trades = (
        get_unreported_closed()
    )

    report = build_report(
        markets=markets,
        signals=signals,
        closed_trades=
            closed_trades,
        stats=stats,
        diagnostics=
            diagnostics,
        market_error=None
    )

    print(
        "--------------------------------------------------"
    )

    print(report)

    print(
        "--------------------------------------------------"
    )

    # --------------------------------------------------------
    # STEP 7
    # --------------------------------------------------------

    print(
        "[STEP 7] "
        "Sending Telegram..."
    )

    telegram_ok = (
        send_telegram(
            report
        )
    )

    if telegram_ok:

        mark_closed_reported(
            [
                row["id"]
                for row
                in closed_trades
            ]
        )

        print(
            "[REPORT] "
            "Telegram sent successfully."
        )

    else:

        print(
            "[REPORT] "
            "Telegram send FAILED."
        )

        print(
            "[REPORT] "
            "Closed trades remain "
            "unreported."
        )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    final_stats = get_stats()

    print(
        "=================================================="
    )

    print(
        f"FINAL "
        f"OPEN={final_stats['open']} "
        f"CLOSED={final_stats['closed']} "
        f"W={final_stats['wins']} "
        f"L={final_stats['losses']} "
        f"WINRATE="
        f"{final_stats['win_rate']:.1f}% "
        f"PnL="
        f"{final_stats['pnl']:+.3f}%"
    )

    print(
        "DIAGNOSTIC "
        f"MARKETS={diagnostics['markets']} "
        f"TREND={diagnostics['trend']} "
        f"SETUP={diagnostics['setup']} "
        f"CONFIRM={diagnostics['confirmation']} "
        f"TRADE={diagnostics['trade_valid']} "
        f"SCORE={diagnostics['score']} "
        f"FINAL={diagnostics['final']}"
    )

    print(
        "SCANNER EXIT CODE: 0"
    )

    print(
        "=================================================="
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
