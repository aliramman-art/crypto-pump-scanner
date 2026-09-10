# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v2.8
# ============================================================
#
# FIXED:
#   - Kraken Futures Derivatives API v3
#   - Correct instrument discovery
#   - PF_ USD perpetual markets
#   - Raw Kraken symbols preserved
#   - 24H volume ranking
#   - CLOSED 5M candle SL/TP tracking
#   - SL-first when SL and TP touch same candle
#   - Maximum 3 OPEN trades
#   - Structural SL
#   - Static S/R TP
#   - Minimum RR 1:2
#   - Maximum SL 0.80%
#   - SQLite persistent state
#   - Telegram reporting
#   - Telegram diagnostics
#   - PAPER TRADING ONLY
#
# ============================================================

import os
import time
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

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

REAL_TRADING = False

REQUEST_TIMEOUT = 20

USER_AGENT = (
    "Mozilla/5.0 "
    "(compatible; VOLUME-KHAT/2.8)"
)


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "application/json",
    }
)


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
# SYMBOL HELPERS
# ============================================================

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


def is_usd_perpetual(instrument):
    """
    Kraken Futures perpetual contracts are normally
    represented as PF_XXXXUSD.

    We intentionally inspect the instrument metadata
    instead of guessing only from the symbol.
    """

    if not isinstance(instrument, dict):
        return False

    symbol = str(
        instrument.get("symbol", "")
    ).upper()

    if not symbol:
        return False

    # Explicit PF_ perpetual convention
    if not symbol.startswith("PF_"):
        return False

    # Must end in USD
    if not symbol.endswith("USD"):
        return False

    # Reject obvious inverse/non-perpetual contracts
    instrument_type = str(
        instrument.get("type", "")
    ).lower()

    if "inverse" in instrument_type:
        return False

    # Some API responses expose tags
    tags = instrument.get("tags", [])

    if isinstance(tags, str):
        tags = [tags]

    tags_lower = {
        str(x).lower()
        for x in tags
    }

    # If type/tags explicitly say fixed maturity,
    # reject it.
    if (
        "fixed" in instrument_type
        or "fixed_maturity" in instrument_type
        or "dated" in instrument_type
    ):
        return False

    if (
        "fixed" in tags_lower
        or "fixed-maturity" in tags_lower
        or "dated" in tags_lower
    ):
        return False

    return True


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None, timeout=REQUEST_TIMEOUT):
    response = SESSION.get(
        url,
        params=params,
        timeout=timeout,
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# GENERIC JSON HELPERS
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
    """
    Correct Kraken Futures Derivatives API v3 endpoint.
    """

    try:
        data = http_get(
            INSTRUMENTS_URL
        )

        instruments = extract_list(
            data,
            [
                "instruments",
                "result",
                "data",
            ],
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
    """
    Correct Kraken Futures Derivatives API v3
    ticker endpoint.
    """

    try:
        data = http_get(
            TICKERS_URL
        )

        tickers = extract_list(
            data,
            [
                "tickers",
                "result",
                "data",
            ],
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
# NUMBER PARSING
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

        normalized = normalize_symbol(
            raw
        )

        index[raw] = ticker
        index[normalized] = ticker

    return index


# ============================================================
# VOLUME EXTRACTION
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
# PRICE EXTRACTION
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
# MARKET DISCOVERY
# ============================================================

def discover_top_markets():
    """
    Discover Kraken Futures USD perpetual markets.

    Important:
    Raw symbol such as PF_CRVUSD is preserved.
    It must be passed unchanged to Kraken chart API.
    """

    print(
        "[MARKETS] Discovering Kraken Futures "
        "USD perpetual markets..."
    )

    instruments = get_instruments()

    if not instruments:
        print(
            "[MARKETS] No instruments received."
        )

        return []

    perpetuals = []

    for inst in instruments:

        if not is_usd_perpetual(inst):
            continue

        symbol = str(
            inst.get("symbol", "")
        ).upper()

        if not symbol:
            continue

        perpetuals.append(
            {
                "symbol": symbol,
                "instrument": inst,
            }
        )

    print(
        f"[MARKETS] USD perpetual candidates: "
        f"{len(perpetuals)}"
    )

    if not perpetuals:
        print(
            "[MARKETS ERROR] No PF_ USD perpetual "
            "markets detected."
        )

        # Diagnostic sample
        sample = []

        for inst in instruments[:20]:

            if isinstance(inst, dict):

                sample.append(
                    (
                        inst.get("symbol"),
                        inst.get("type"),
                        inst.get("tags"),
                    )
                )

        print(
            "[MARKETS DEBUG] Sample instruments:"
        )

        for item in sample:
            print(
                "   ",
                item
            )

        return []

    tickers = get_tickers()

    ticker_index = build_ticker_index(
        tickers
    )

    ranked = []

    for item in perpetuals:

        symbol = item["symbol"]

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

        ranked.append(
            {
                "symbol": symbol,
                "volume": volume,
                "price": price,
                "instrument": item["instrument"],
            }
        )

    # Sort by 24h volume descending
    ranked.sort(
        key=lambda x: x["volume"],
        reverse=True,
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

        preview = selected[:10]

        for i, market in enumerate(
            preview,
            start=1,
        ):

            print(
                f"   {i:02d}. "
                f"{market['symbol']} "
                f"VOL={market['volume']:.2f}"
            )

    return selected


# ============================================================
# LIVE PRICE MAP
# ============================================================

def get_live_price_map():
    tickers = get_tickers()

    result = {}

    for ticker in tickers:

        if not isinstance(ticker, dict):
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

        result[
            str(symbol).upper()
        ] = price

        result[
            normalize_symbol(symbol)
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
            "result",
        ],
    )

    if not rows:
        return []

    output = []

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
            )

            output.append(
                [
                    timestamp,
                    open_price,
                    high,
                    low,
                    close,
                    volume,
                ]
            )

        elif isinstance(row, (list, tuple)):

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
    limit=OHLCV_LIMIT,
):
    """
    Kraken Futures charts endpoint.

    Raw PF_ symbol is required.
    """

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
            ],
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
                errors="coerce",
            )

        df = df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close",
            ]
        )

        df = df.sort_values(
            "timestamp"
        )

        df = df.drop_duplicates(
            subset=["timestamp"]
        )

        # Remove latest candle because it may still
        # be forming.
        if len(df) > 1:
            df = df.iloc[:-1]

        if limit:
            df = df.tail(limit)

        df = df.reset_index(
            drop=True
        )

        return df

    except Exception as exc:

        print(
            f"[CANDLES ERROR] "
            f"{symbol} {resolution}: "
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
    period=ATR_PERIOD,
):
    if df.empty:
        return df

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = (
        close.shift(1)
    )

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
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = (
        tr.rolling(
            period,
            min_periods=1,
        ).mean()
    )

    return df


# ============================================================
# RVOL
# ============================================================

def add_rvol(
    df,
    period=RVOL_PERIOD,
):
    if df.empty:
        return df

    baseline = (
        df["volume"]
        .rolling(
            period,
            min_periods=5,
        )
        .mean()
        .shift(1)
    )

    df["rvol"] = (
        df["volume"] /
        baseline.replace(
            0,
            np.nan,
        )
    )

    df["rvol"] = (
        df["rvol"]
        .replace(
            [np.inf, -np.inf],
            np.nan,
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
    right=SWING_RIGHT,
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
        len(values) - right,
    ):

        current = values[i]

        left_values = values[
            i - left:i
        ]

        right_values = values[
            i + 1:i + right + 1
        ]

        if (
            current >= left_values.max()
            and
            current >= right_values.max()
        ):
            swings.append(
                (
                    i,
                    float(current),
                )
            )

    return swings


def find_swing_lows(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT,
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
        len(values) - right,
    ):

        current = values[i]

        left_values = values[
            i - left:i
        ]

        right_values = values[
            i + 1:i + right + 1
        ]

        if (
            current <= left_values.min()
            and
            current <= right_values.min()
        ):
            swings.append(
                (
                    i,
                    float(current),
                )
            )

    return swings


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def get_recent_swing_levels(
    df,
    max_levels=8,
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


def nearest_support(
    price,
    levels,
):
    candidates = [
        x
        for x in levels
        if x < price
    ]

    if not candidates:
        return None

    return max(candidates)


def nearest_resistance(
    price,
    levels,
):
    candidates = [
        x
        for x in levels
        if x > price
    ]

    if not candidates:
        return None

    return min(candidates)


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

    sma50 = (
        close
        .rolling(50)
        .mean()
        .iloc[-1]
    ) if len(df) >= 50 else (
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
# BREAKOUT DETECTION
# ============================================================

def detect_breakout(
    df,
    trend,
):
    if len(df) < 25:
        return None

    support, resistance = (
        get_recent_swing_levels(
            df
        )
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

    if trend == "UP" and resistance:

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

    if trend == "DOWN" and support:

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
    trend,
):
    if len(df) < 25:
        return None

    support, resistance = (
        get_recent_swing_levels(
            df
        )
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
        high - max(
            open_price,
            close,
        )
    )

    lower_wick = (
        min(
            open_price,
            close,
        ) - low
    )

    # High volume rejection from resistance
    if (
        trend == "DOWN"
        and resistance
        and rvol >= RVOL_ABNORMAL
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
            and bearish_close
            and strong_upper_wick
        ):
            return {
                "side": "SHORT",
                "setup":
                    "HIGH-VOLUME REJECTION",
                "level":
                    resistance_level,
                "rvol": rvol,
            }

    # High volume rejection from support
    if (
        trend == "UP"
        and support
        and rvol >= RVOL_ABNORMAL
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
            and bullish_close
            and strong_lower_wick
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
    side,
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
            and body_ratio >= 0.35
        )

    if side == "SHORT":

        return (
            close < open_price
            and body_ratio >= 0.35
        )

    return False


# ============================================================
# TRADE CALCULATION
# ============================================================

def calculate_trade(
    side,
    entry,
    df15,
    df1h,
):
    support15, resistance15 = (
        get_recent_swing_levels(
            df15
        )
    )

    support1h, resistance1h = (
        get_recent_swing_levels(
            df1h
        )
    )

    atr = safe_float(
        df15.iloc[-1].get("atr")
    )

    if atr <= 0:
        atr = entry * 0.002

    atr_buffer = (
        atr * ATR_BUFFER_MULT
    )

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
            - atr_buffer
        )

        tp_candidates = [
            x
            for x in (
                resistance1h
                + resistance15
            )
            if x > entry
        ]

        if not tp_candidates:
            return None

        tp = min(
            tp_candidates
        )

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
            + atr_buffer
        )

        tp_candidates = [
            x
            for x in (
                support1h
                + support15
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

        risk = entry - sl
        reward = tp - entry

    else:

        risk = sl - entry
        reward = entry - tp

    if risk <= 0 or reward <= 0:
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
# SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    rvol,
    confirmed,
):
    score = 0

    if trend in (
        "UP",
        "DOWN",
    ):
        score += 3

    if setup == "BREAKOUT":
        score += 4

    elif setup == "HIGH-VOLUME REJECTION":
        score += 4

    if rvol >= RVOL_STRONG:
        score += 4

    elif rvol >= RVOL_ABNORMAL:
        score += 3

    if confirmed:
        score += 4

    return min(
        score,
        15,
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
        """
    )

    conn.commit()

    # Add columns if an older DB exists
    columns = set()

    cur.execute(
        "PRAGMA table_info(trades)"
    )

    for row in cur.fetchall():
        columns.add(
            row["name"]
        )

    if "exit_reason" not in columns:

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN exit_reason TEXT
            """
        )

    if "closed_reported" not in columns:

        cur.execute(
            """
            ALTER TABLE trades
            ADD COLUMN closed_reported INTEGER
            DEFAULT 0
            """
        )

    conn.commit()

    conn.close()


# ============================================================
# OPEN COUNT
# ============================================================

def get_open_count():
    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
        """
    )

    count = int(
        cur.fetchone()[0]
    )

    conn.close()

    return count


# ============================================================
# MAX OPEN TRADE ENFORCEMENT
# ============================================================

def enforce_max_open_trades():
    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    )

    rows = cur.fetchall()

    excess = max(
        0,
        len(rows) - MAX_OPEN_TRADES,
    )

    if excess <= 0:
        conn.close()
        return

    print(
        f"[OPEN LIMIT] Removing "
        f"{excess} excess OPEN trades."
    )

    for row in rows[
        :excess
    ]:

        cur.execute(
            """
            UPDATE trades
            SET
                status='CLOSED',
                result='REMOVED',
                pnl=0,
                exit_time=?,
                exit_reason='OPEN_LIMIT_CLEANUP',
                closed_reported=1
            WHERE id=?
            """,
            (
                now_str(),
                row["id"],
            ),
        )

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
    score,
):
    conn = db_connect()

    cur = conn.cursor()

    # One open trade per symbol
    cur.execute(
        """
        SELECT id
        FROM trades
        WHERE symbol=?
          AND status='OPEN'
        LIMIT 1
        """,
        (symbol,),
    )

    existing = cur.fetchone()

    if existing:
        conn.close()
        return False

    # Cooldown
    cur.execute(
        """
        SELECT entry_time
        FROM trades
        WHERE symbol=?
        ORDER BY id DESC
        LIMIT 1
        """,
        (symbol,),
    )

    previous = cur.fetchone()

    if previous and previous["entry_time"]:

        try:

            previous_time = datetime.strptime(
                previous["entry_time"],
                "%Y-%m-%d %H:%M:%S UTC",
            ).replace(
                tzinfo=timezone.utc
            )

            elapsed = (
                now_utc()
                - previous_time
            ).total_seconds()

            candle_seconds = (
                5 * 60
            )

            if (
                elapsed
                <
                candle_seconds
                * COOLDOWN_CANDLES
            ):
                conn.close()
                return False

        except Exception:
            pass

    cur.execute(
        """
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
            ?, ?, ?, ?, ?, ?, ?, ?, 'OPEN',
            NULL, 0, ?, NULL, NULL, 0, ?
        )
        """,
        (
            symbol,
            trade["side"],
            setup,
            trade["entry"],
            trade["sl"],
            trade["tp"],
            trade["rr"],
            score,
            now_str(),
            now_str(),
        ),
    )

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
# PNL
# ============================================================

def calculate_pnl(
    side,
    entry,
    exit_price,
):
    if entry <= 0:
        return 0.0

    if side == "LONG":

        return (
            (
                exit_price
                - entry
            )
            / entry
        ) * 100.0

    if side == "SHORT":

        return (
            (
                entry
                - exit_price
            )
            / entry
        ) * 100.0

    return 0.0


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():
    """
    Check the latest CLOSED 5M candle.

    LONG:
        Low <= SL => LOSS
        High >= TP => WIN

    SHORT:
        High >= SL => LOSS
        Low <= TP => WIN

    If both SL and TP are touched in the same
    candle, SL is treated as first.
    """

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        """
    )

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
            5,
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

            # Conservative SL first
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

            # Conservative SL first
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
            exit_price,
        )

        cur.execute(
            """
            UPDATE trades
            SET
                status='CLOSED',
                result=?,
                pnl=?,
                exit_time=?,
                exit_reason=?,
                closed_reported=0
            WHERE id=?
            """,
            (
                result,
                pnl,
                now_str(),
                exit_reason,
                trade["id"],
            ),
        )

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
            f"[EXIT] Newly closed: "
            f"{newly_closed}"
        )

    return newly_closed


# ============================================================
# UNREPORTED CLOSED TRADES
# ============================================================

def get_unreported_closed():
    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE status='CLOSED'
          AND closed_reported=0
        ORDER BY id ASC
        """
    )

    rows = cur.fetchall()

    conn.close()

    return rows


# ============================================================
# MARK REPORTED
# ============================================================

def mark_closed_reported(
    trade_ids,
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
            (trade_id,),
        )

    conn.commit()

    conn.close()


# ============================================================
# STATS
# ============================================================

def get_stats():
    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
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
                        THEN pnl
                        ELSE 0
                    END
                ),
                0
            ) AS pnl

        FROM trades
        """
    )

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

    win_rate = (
        wins
        / total_decided
        * 100
        if total_decided
        else 0.0
    )

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "win_rate": win_rate,
    }


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(market):
    symbol = market["symbol"]

    try:

        df1h = get_candles(
            symbol,
            TF_1H,
            OHLCV_LIMIT,
        )

        df15 = get_candles(
            symbol,
            TF_15M,
            OHLCV_LIMIT,
        )

        df5 = get_candles(
            symbol,
            TF_5M,
            OHLCV_LIMIT,
        )

        if (
            df1h.empty
            or df15.empty
            or df5.empty
        ):
            return None

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

        trend = determine_trend(
            df1h
        )

        if trend == "NEUTRAL":
            return None

        setup = detect_breakout(
            df15,
            trend,
        )

        if setup is None:

            setup = detect_rejection(
                df15,
                trend,
            )

        if setup is None:
            return None

        side = setup["side"]

        confirmed = confirm_5m(
            df5,
            side,
        )

        if not confirmed:
            return None

        entry = safe_float(
            df5.iloc[-1]["close"]
        )

        if entry <= 0:
            return None

        trade = calculate_trade(
            side,
            entry,
            df15,
            df1h,
        )

        if trade is None:
            return None

        score = calculate_score(
            trend,
            setup["setup"],
            setup["rvol"],
            confirmed,
        )

        if score < MIN_SIGNAL_SCORE:
            return None

        trade["symbol"] = symbol
        trade["setup"] = setup["setup"]
        trade["trend"] = trend
        trade["rvol"] = setup["rvol"]
        trade["score"] = score

        return trade

    except Exception as exc:

        print(
            f"[SCAN ERROR] "
            f"{symbol}: {exc}"
        )

        return None


# ============================================================
# SCAN ALL MARKETS
# ============================================================

def scan_all_markets(
    markets,
):
    signals = []

    print(
        f"[SCAN] Scanning "
        f"{len(markets)} markets..."
    )

    for index, market in enumerate(
        markets,
        start=1,
    ):

        symbol = market["symbol"]

        print(
            f"[SCAN] "
            f"{index}/{len(markets)} "
            f"{symbol}"
        )

        signal = scan_market(
            market
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
                f"Score={signal['score']}"
            )

    signals.sort(
        key=lambda x: x["score"],
        reverse=True,
    )

    return signals


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):
    """
    Telegram diagnostics intentionally verbose enough
    for GitHub Actions logs.
    """

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
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        print(
            f"[TELEGRAM] HTTP "
            f"{response.status_code}"
        )

        # Never print the bot token.
        response_text = (
            response.text[:1000]
        )

        print(
            f"[TELEGRAM] Response: "
            f"{response_text}"
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
            "[TELEGRAM] API returned "
            "ok=false."
        )

        return False

    except Exception as exc:

        print(
            "[TELEGRAM ERROR]",
            repr(exc)
        )

        return False


# ============================================================
# REPORT
# ============================================================

def build_report(
    markets,
    signals,
    closed_trades,
    stats,
    market_error=None,
):
    lines = []

    lines.append(
        "📊 CRYPTO VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {now_str()}"
    )

    lines.append(
        "⚡ KRAKEN FUTURES"
    )

    lines.append(
        "📌 TOP 100 USD PERPETUAL"
    )

    lines.append(
        "🤖 VOLUME-KHAT v2.8"
    )

    lines.append("")

    # --------------------------------------------------------
    # MARKET STATUS
    # --------------------------------------------------------

    if market_error:

        lines.append(
            "⚠️ MARKET DISCOVERY ERROR"
        )

        lines.append(
            str(market_error)[:500]
        )

    else:

        lines.append(
            f"🔎 Markets scanned: "
            f"{len(markets)}"
        )

    lines.append("")

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"OPEN: {stats['open']}/"
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"CLOSED: {stats['closed']}"
    )

    lines.append(
        f"WIN: {stats['wins']}"
    )

    lines.append(
        f"LOSS: {stats['losses']}"
    )

    lines.append(
        f"WIN RATE: "
        f"{stats['win_rate']:.1f}%"
    )

    lines.append(
        f"TOTAL PnL: "
        f"{stats['pnl']:+.3f}%"
    )

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    if closed_trades:

        lines.append("")

        lines.append(
            "🔔 NEWLY CLOSED"
        )

        for trade in closed_trades:

            exit_price = (
                trade["tp"]
                if trade["exit_reason"]
                == "TP"
                else trade["sl"]
            )

            result_icon = (
                "✅"
                if trade["result"]
                == "WIN"
                else "❌"
            )

            lines.append(
                f"{result_icon} "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"{trade['result']}"
            )

            lines.append(
                f"Entry "
                f"{trade['entry']:.8g} | "
                f"Exit "
                f"{exit_price:.8g}"
            )

            lines.append(
                f"Reason: "
                f"{trade['exit_reason']} | "
                f"PnL: "
                f"{trade['pnl']:+.3f}%"
            )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    if signals:

        lines.append("")

        lines.append(
            "🚨 NEW SIGNALS"
        )

        for signal in signals:

            lines.append(
                f"🔥 {signal['symbol']} "
                f"{signal['side']}"
            )

            lines.append(
                f"Setup: "
                f"{signal['setup']}"
            )

            lines.append(
                f"Trend: "
                f"{signal['trend']}"
            )

            lines.append(
                f"RVOL: "
                f"{signal['rvol']:.2f}"
            )

            lines.append(
                f"Score: "
                f"{signal['score']}/15"
            )

            lines.append(
                f"Entry: "
                f"{signal['entry']:.8g}"
            )

            lines.append(
                f"SL: "
                f"{signal['sl']:.8g}"
            )

            lines.append(
                f"TP: "
                f"{signal['tp']:.8g}"
            )

            lines.append(
                f"RR: "
                f"1:{signal['rr']:.2f}"
            )

            lines.append("")

    else:

        lines.append("")

        lines.append(
            "ℹ️ No new valid signals."
        )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT *
        FROM trades
        WHERE status='OPEN'
        ORDER BY id ASC
        """
    )

    open_trades = cur.fetchall()

    conn.close()

    if open_trades:

        lines.append("")

        lines.append(
            "📂 OPEN TRADES"
        )

        for trade in open_trades:

            lines.append(
                f"• {trade['symbol']} "
                f"{trade['side']}"
            )

            lines.append(
                f"Entry "
                f"{trade['entry']:.8g} | "
                f"SL "
                f"{trade['sl']:.8g} | "
                f"TP "
                f"{trade['tp']:.8g}"
            )

            lines.append(
                f"RR "
                f"1:{safe_float(trade['rr']):.2f}"
            )

    lines.append("")

    lines.append(
        "🧪 PAPER TRADING ONLY"
    )

    lines.append(
        "REAL_TRADING = FALSE"
    )

    report = "\n".join(
        lines
    )

    # Telegram max message length safety
    if len(report) > 3900:
        report = (
            report[:3850]
            + "\n\n...[TRUNCATED]"
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
        "KRAKEN FUTURES VOLUME-KHAT 100 v2.8"
    )

    print(
        "PAPER TRADING ONLY"
    )

    print(
        "=================================================="
    )

    # --------------------------------------------------------
    # ENV DIAGNOSTICS
    # --------------------------------------------------------

    print(
        "[ENV] Telegram token:",
        "SET"
        if TELEGRAM_BOT_TOKEN
        else "MISSING",
    )

    print(
        "[ENV] Telegram chat ID:",
        "SET"
        if TELEGRAM_CHAT_ID
        else "MISSING",
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # STEP 1
    # --------------------------------------------------------

    print(
        "[STEP 1] Checking existing trades..."
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
        "[STEP 2] Discovering TOP 100 markets..."
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
    # IMPORTANT:
    # Do NOT silently stop before Telegram.
    # --------------------------------------------------------

    if not markets:

        print(
            "[FATAL] No markets found."
        )

        # Get closed trades even when discovery fails
        closed_trades = (
            get_unreported_closed()
        )

        stats = get_stats()

        report = build_report(
            markets=[],
            signals=[],
            closed_trades=closed_trades,
            stats=stats,
            market_error=(
                market_error
                or
                "Kraken returned zero "
                "USD perpetual markets."
            ),
        )

        telegram_ok = send_telegram(
            report
        )

        if telegram_ok:

            mark_closed_reported(
                [
                    row["id"]
                    for row in closed_trades
                ]
            )

        print(
            "============================================================"
        )

        print(
            "SCANNER EXIT CODE: 0"
        )

        print(
            "============================================================"
        )

        return

    # --------------------------------------------------------
    # STEP 3
    # --------------------------------------------------------

    print(
        "[STEP 3] Scanning markets..."
    )

    open_count = get_open_count()

    slots_available = max(
        0,
        MAX_OPEN_TRADES
        - open_count,
    )

    print(
        f"[CAPACITY] "
        f"OPEN={open_count}/"
        f"{MAX_OPEN_TRADES} "
        f"AVAILABLE={slots_available}"
    )

    signals = []

    if slots_available > 0:

        all_signals = (
            scan_all_markets(
                markets
            )
        )

        signals = all_signals[
            :min(
                slots_available,
                MAX_NEW_SIGNALS,
            )
        ]

    else:

        print(
            "[CAPACITY] "
            "Maximum open trades reached."
        )

    # --------------------------------------------------------
    # STEP 4
    # --------------------------------------------------------

    print(
        "[STEP 4] Saving signals..."
    )

    saved_count = 0

    for signal in signals:

        saved = save_signal(
            signal["symbol"],
            signal,
            signal["setup"],
            signal["score"],
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
        "[STEP 5] Refreshing statistics..."
    )

    stats = get_stats()

    # --------------------------------------------------------
    # STEP 6
    # --------------------------------------------------------

    print(
        "[STEP 6] Preparing Telegram report..."
    )

    closed_trades = (
        get_unreported_closed()
    )

    report = build_report(
        markets=markets,
        signals=signals,
        closed_trades=closed_trades,
        stats=stats,
        market_error=None,
    )

    print(
        "------------------------------------------------------------"
    )

    print(
        report
    )

    print(
        "------------------------------------------------------------"
    )

    # --------------------------------------------------------
    # STEP 7
    # --------------------------------------------------------

    print(
        "[STEP 7] Sending Telegram..."
    )

    telegram_ok = send_telegram(
        report
    )

    if telegram_ok:

        mark_closed_reported(
            [
                row["id"]
                for row in closed_trades
            ]
        )

        print(
            "[REPORT] Telegram sent successfully."
        )

    else:

        print(
            "[REPORT] Telegram send FAILED."
        )

        print(
            "[REPORT] Closed trades remain "
            "unreported for the next run."
        )

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    final_stats = get_stats()

    print(
        "============================================================"
    )

    print(
        f"FINAL "
        f"OPEN={final_stats['open']} "
        f"CLOSED={final_stats['closed']} "
        f"W={final_stats['wins']} "
        f"L={final_stats['losses']} "
        f"WINRATE={final_stats['win_rate']:.1f}% "
        f"PnL={final_stats['pnl']:+.3f}%"
    )

    print(
        "SCANNER EXIT CODE: 0"
    )

    print(
        "============================================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
