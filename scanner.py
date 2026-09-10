# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v2.7 FIXED
# ============================================================
#
# PAPER TRADING ONLY
#
# MARKET:
#   Kraken Futures
#   TOP 100 USD PERPETUAL MARKETS
#
# MTF:
#   1H  = Trend + Static S/R
#   15M = Dynamic S/R + RVOL + Setup
#   5M  = Entry Confirmation
#
# SETUPS:
#   BREAKOUT
#   HIGH-VOLUME REJECTION
#
# RISK:
#   Structural SL
#   Static S/R TP
#   Minimum RR = 2.0
#   Maximum SL = 0.80%
#
# TRADE MANAGEMENT:
#   Maximum OPEN trades = 3
#   Paper tracking only
#
# EXIT FIX:
#   Every run checks the latest CLOSED 5M candle.
#
#   LONG:
#       Low  <= SL  -> LOSS
#       High >= TP  -> WIN
#
#   SHORT:
#       High >= SL  -> LOSS
#       Low  <= TP  -> WIN
#
#   If both SL and TP are touched in the same candle:
#       SL has priority (conservative policy)
#
# IMPORTANT:
#   Raw Kraken symbols are preserved for API requests.
#   Normalized symbols are used only for matching.
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

MAX_WORKERS = 10


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Mozilla/5.0 "
            "KRAKEN-VOLUME-KHAT-100"
    }
)


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def format_time():
    return utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# SYMBOL HELPERS
# ============================================================

def normalize_symbol(symbol):
    """
    Used ONLY for matching.

    IMPORTANT:
    Do NOT use normalized symbol for Kraken chart API.
    """

    if symbol is None:
        return ""

    return (
        str(symbol)
        .upper()
        .replace("/", "")
        .replace("-", "")
        .replace("_", "")
    )


def is_usd_perpetual_symbol(symbol):
    """
    Conservative symbol check.
    """

    s = normalize_symbol(symbol)

    if not s:
        return False

    return (
        s.endswith("USD")
        or s.endswith("USDT")
    )


def is_explicitly_non_perpetual(item):
    """
    Reject instruments which clearly represent
    dated/fixed maturity futures.
    """

    if not isinstance(item, dict):
        return False

    values = []

    for key in (
        "type",
        "contractType",
        "instrumentType",
        "maturity",
        "expiration",
        "underlying",
    ):
        value = item.get(key)

        if value is not None:
            values.append(
                str(value).lower()
            )

    text = " ".join(values)

    if "perpetual" in text:
        return False

    bad_words = (
        "future",
        "futures",
        "fixedmaturity",
        "fixed_maturity",
        "dated",
        "inverse",
    )

    for word in bad_words:
        if word in text:
            return True

    return False


# ============================================================
# HTTP GET
# ============================================================

def http_get(url, params=None):
    try:
        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:
        print(
            f"[HTTP ERROR] {url} -> {exc}"
        )

        return None


# ============================================================
# PARSE GENERIC LIST
# ============================================================

def extract_list(payload):
    if payload is None:
        return []

    if isinstance(payload, list):
        return payload

    if isinstance(payload, dict):

        for key in (
            "instruments",
            "tickers",
            "data",
            "result",
            "results",
        ):
            value = payload.get(key)

            if isinstance(value, list):
                return value

        # Sometimes result can be nested
        for value in payload.values():

            if isinstance(value, list):
                return value

    return []


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    url = (
        f"{BASE}"
        "/derivatives/api/v3/instruments"
    )

    payload = http_get(url)

    rows = extract_list(payload)

    instruments = []

    for item in rows:

        if not isinstance(item, dict):
            continue

        raw_symbol = (
            item.get("symbol")
            or item.get("instrument")
            or item.get("id")
        )

        if not raw_symbol:
            continue

        instruments.append(
            {
                "symbol": str(raw_symbol),
                "norm_symbol":
                    normalize_symbol(raw_symbol),
                "raw": item,
            }
        )

    return instruments


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    url = (
        f"{BASE}"
        "/derivatives/api/v3/tickers"
    )

    payload = http_get(url)

    rows = extract_list(payload)

    tickers = []

    if rows:
        for item in rows:

            if not isinstance(item, dict):
                continue

            symbol = (
                item.get("symbol")
                or item.get("instrument")
                or item.get("pair")
            )

            if symbol:
                tickers.append(
                    {
                        "symbol": str(symbol),
                        "raw": item,
                    }
                )

    elif isinstance(payload, dict):

        for key, value in payload.items():

            if isinstance(value, dict):

                symbol = (
                    value.get("symbol")
                    or value.get("instrument")
                    or key
                )

                tickers.append(
                    {
                        "symbol": str(symbol),
                        "raw": value,
                    }
                )

    return tickers


# ============================================================
# NUMBER PARSER
# ============================================================

def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        x = float(value)

        if not math.isfinite(x):
            return default

        return x

    except Exception:
        return default


# ============================================================
# TOP MARKET DISCOVERY
# ============================================================

def discover_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    if not instruments:
        print("[MARKETS] No instruments received.")
        return []

    if not tickers:
        print("[MARKETS] No tickers received.")
        return []

    instrument_map = {}

    for item in instruments:

        raw = item["raw"]

        symbol = item["symbol"]

        norm = item["norm_symbol"]

        if not norm:
            continue

        if not is_usd_perpetual_symbol(symbol):
            continue

        if is_explicitly_non_perpetual(raw):
            continue

        if norm not in instrument_map:

            instrument_map[norm] = {
                "api_symbol": symbol,
                "instrument": raw,
            }

    markets = []

    seen = set()

    for ticker in tickers:

        raw_ticker = ticker["raw"]

        ticker_symbol = ticker["symbol"]

        norm = normalize_symbol(
            ticker_symbol
        )

        if not norm:
            continue

        inst = instrument_map.get(norm)

        if not inst:
            continue

        volume = 0.0

        for key in (
            "volume24h",
            "volume24H",
            "vol24h",
            "volume",
            "turnover24h",
            "turnover",
        ):

            if key in raw_ticker:

                volume = safe_float(
                    raw_ticker.get(key)
                )

                if volume > 0:
                    break

        last = 0.0

        for key in (
            "last",
            "lastPrice",
            "markPrice",
            "price",
        ):

            if key in raw_ticker:

                last = safe_float(
                    raw_ticker.get(key)
                )

                if last > 0:
                    break

        if volume <= 0:
            continue

        if last <= 0:
            continue

        api_symbol = inst["api_symbol"]

        api_norm = normalize_symbol(
            api_symbol
        )

        if api_norm in seen:
            continue

        seen.add(api_norm)

        markets.append(
            {
                "symbol": api_symbol,
                "norm_symbol": api_norm,
                "volume": volume,
                "last": last,
            }
        )

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True,
    )

    markets = markets[:TOP_N]

    print(
        f"[MARKETS] Selected {len(markets)}"
    )

    return markets


# ============================================================
# LIVE PRICE MAP
# ============================================================

def get_live_price_map(symbols=None):

    tickers = get_tickers()

    wanted = None

    if symbols:

        wanted = {
            normalize_symbol(x)
            for x in symbols
        }

    price_map = {}

    for ticker in tickers:

        raw = ticker["raw"]

        symbol = ticker["symbol"]

        norm = normalize_symbol(symbol)

        if wanted is not None:

            if norm not in wanted:
                continue

        price = 0.0

        for key in (
            "last",
            "lastPrice",
            "markPrice",
            "price",
        ):

            if key in raw:

                price = safe_float(
                    raw.get(key)
                )

                if price > 0:
                    break

        if price > 0:
            price_map[norm] = price

    return price_map


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle_rows(payload):

    if payload is None:
        return []

    rows = extract_list(payload)

    if not rows and isinstance(
        payload,
        dict
    ):

        for key in (
            "candles",
            "ohlc",
            "data",
            "result",
        ):

            value = payload.get(key)

            if isinstance(value, list):
                rows = value
                break

    parsed = []

    for row in rows:

        try:

            if isinstance(row, dict):

                timestamp = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("ts")
                )

                open_ = (
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

            elif isinstance(row, (list, tuple)):

                if len(row) < 6:
                    continue

                timestamp = row[0]
                open_ = row[1]
                high = row[2]
                low = row[3]
                close = row[4]
                volume = row[5]

            else:
                continue

            timestamp = safe_float(
                timestamp
            )

            open_ = safe_float(open_)
            high = safe_float(high)
            low = safe_float(low)
            close = safe_float(close)
            volume = safe_float(volume)

            if timestamp <= 0:
                continue

            if min(
                open_,
                high,
                low,
                close
            ) <= 0:
                continue

            parsed.append(
                [
                    timestamp,
                    open_,
                    high,
                    low,
                    close,
                    volume,
                ]
            )

        except Exception:
            continue

    return parsed


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit=OHLCV_LIMIT,
):
    """
    IMPORTANT:
    symbol MUST be the raw Kraken symbol.

    Example:
        PF_DOTUSD

    NOT:
        PFDOTUSD
    """

    if not symbol:
        return pd.DataFrame()

    url = (
        f"{BASE}"
        f"/api/charts/v1/trade/"
        f"{symbol}/"
        f"{resolution}"
    )

    payload = http_get(url)

    rows = parse_candle_rows(
        payload
    )

    if len(rows) < 30:
        return pd.DataFrame()

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

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(drop=True)

    # --------------------------------------------------------
    # REMOVE LAST CANDLE
    # The latest candle can still be forming.
    # --------------------------------------------------------

    if len(df) >= 2:
        df = df.iloc[:-1].copy()

    if len(df) > limit:
        df = df.tail(limit).copy()

    df = df.reset_index(
        drop=True
    )

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD,
):

    if df.empty:
        return 0.0

    previous_close = (
        df["close"].shift(1)
    )

    tr1 = (
        df["high"]
        - df["low"]
    )

    tr2 = (
        df["high"]
        - previous_close
    ).abs()

    tr3 = (
        df["low"]
        - previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    atr = (
        tr.rolling(
            period
        ).mean()
    )

    value = (
        atr.iloc[-1]
        if len(atr)
        else 0
    )

    return safe_float(value)


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(
    df,
    period=RVOL_PERIOD,
):

    if len(df) <= period:
        return 0.0

    current_volume = safe_float(
        df["volume"].iloc[-1]
    )

    previous = df[
        "volume"
    ].iloc[
        -(period + 1):-1
    ]

    average_volume = safe_float(
        previous.mean()
    )

    if average_volume <= 0:
        return 0.0

    return (
        current_volume
        / average_volume
    )


# ============================================================
# SWING HIGHS
# ============================================================

def get_swing_highs(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT,
):

    levels = []

    if len(df) < (
        left + right + 1
    ):
        return levels

    highs = df[
        "high"
    ].values

    for i in range(
        left,
        len(df) - right
    ):

        center = highs[i]

        left_values = highs[
            i - left:i
        ]

        right_values = highs[
            i + 1:i + right + 1
        ]

        if (
            center >= left_values.max()
            and
            center >= right_values.max()
        ):
            levels.append(
                float(center)
            )

    return levels


# ============================================================
# SWING LOWS
# ============================================================

def get_swing_lows(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT,
):

    levels = []

    if len(df) < (
        left + right + 1
    ):
        return levels

    lows = df[
        "low"
    ].values

    for i in range(
        left,
        len(df) - right
    ):

        center = lows[i]

        left_values = lows[
            i - left:i
        ]

        right_values = lows[
            i + 1:i + right + 1
        ]

        if (
            center <= left_values.min()
            and
            center <= right_values.min()
        ):
            levels.append(
                float(center)
            )

    return levels


# ============================================================
# CLUSTER LEVELS
# ============================================================

def cluster_levels(
    levels,
    tolerance=0.002,
):

    if not levels:
        return []

    sorted_levels = sorted(
        float(x)
        for x in levels
        if safe_float(x) > 0
    )

    if not sorted_levels:
        return []

    clusters = []

    current = [
        sorted_levels[0]
    ]

    for level in sorted_levels[1:]:

        center = (
            sum(current)
            / len(current)
        )

        if center <= 0:
            current.append(level)
            continue

        distance = (
            abs(level - center)
            / center
        )

        if distance <= tolerance:

            current.append(level)

        else:

            clusters.append(
                sum(current)
                / len(current)
            )

            current = [level]

    if current:

        clusters.append(
            sum(current)
            / len(current)
        )

    return clusters


# ============================================================
# STATIC S/R
# ============================================================

def build_static_sr(
    df_1h,
    price,
):

    highs = get_swing_highs(
        df_1h
    )

    lows = get_swing_lows(
        df_1h
    )

    resistance = cluster_levels(
        highs
    )

    support = cluster_levels(
        lows
    )

    resistance = [
        x
        for x in resistance
        if x > price
    ]

    support = [
        x
        for x in support
        if x < price
    ]

    resistance.sort()

    support.sort(
        reverse=True
    )

    return support, resistance


# ============================================================
# DYNAMIC S/R
# ============================================================

def build_dynamic_sr(
    df_15m,
    price,
):

    recent = df_15m.tail(40)

    highs = get_swing_highs(
        recent
    )

    lows = get_swing_lows(
        recent
    )

    resistance = cluster_levels(
        highs
    )

    support = cluster_levels(
        lows
    )

    resistance = [
        x
        for x in resistance
        if x > price
    ]

    support = [
        x
        for x in support
        if x < price
    ]

    resistance.sort()

    support.sort(
        reverse=True
    )

    return support, resistance


# ============================================================
# TREND
# ============================================================

def determine_trend(
    df_1h
):

    if len(df_1h) < 60:
        return "NEUTRAL"

    close = df_1h[
        "close"
    ]

    sma20 = (
        close.rolling(20).mean()
    )

    sma50 = (
        close.rolling(50).mean()
    )

    last_close = safe_float(
        close.iloc[-1]
    )

    last_sma20 = safe_float(
        sma20.iloc[-1]
    )

    last_sma50 = safe_float(
        sma50.iloc[-1]
    )

    if (
        last_close > last_sma20
        and
        last_sma20 > last_sma50
    ):
        return "LONG"

    if (
        last_close < last_sma20
        and
        last_sma20 < last_sma50
    ):
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# BREAKOUT DETECTION
# ============================================================

def detect_breakout(
    df_15m,
    rvol,
):

    if len(df_15m) < 20:
        return None

    current = df_15m.iloc[-1]

    previous_window = (
        df_15m.iloc[-10:-1]
    )

    if previous_window.empty:
        return None

    resistance = safe_float(
        previous_window[
            "high"
        ].max()
    )

    support = safe_float(
        previous_window[
            "low"
        ].min()
    )

    bullish = (
        current["close"]
        > current["open"]
    )

    bearish = (
        current["close"]
        < current["open"]
    )

    close_price = safe_float(
        current["close"]
    )

    if (
        close_price > resistance
        and bullish
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "side": "LONG",
            "setup": "BREAKOUT",
            "level": resistance,
        }

    if (
        close_price < support
        and bearish
        and rvol >= RVOL_ABNORMAL
    ):

        return {
            "side": "SHORT",
            "setup": "BREAKOUT",
            "level": support,
        }

    return None


# ============================================================
# HIGH VOLUME REJECTION
# ============================================================

def detect_rejection(
    df_15m,
    rvol,
):

    if len(df_15m) < 2:
        return None

    if rvol < RVOL_ABNORMAL:
        return None

    current = df_15m.iloc[-1]

    open_price = safe_float(
        current["open"]
    )

    high = safe_float(
        current["high"]
    )

    low = safe_float(
        current["low"]
    )

    close = safe_float(
        current["close"]
    )

    candle_range = (
        high - low
    )

    if candle_range <= 0:
        return None

    upper_wick = (
        high
        - max(
            open_price,
            close
        )
    )

    lower_wick = (
        min(
            open_price,
            close
        )
        - low
    )

    upper_ratio = (
        upper_wick
        / candle_range
    )

    lower_ratio = (
        lower_wick
        / candle_range
    )

    if (
        upper_ratio >= 0.45
        and close < open_price
    ):

        return {
            "side": "SHORT",
            "setup":
                "HIGH-VOLUME REJECTION",
            "level": high,
        }

    if (
        lower_ratio >= 0.45
        and close > open_price
    ):

        return {
            "side": "LONG",
            "setup":
                "HIGH-VOLUME REJECTION",
            "level": low,
        }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df_5m,
    side,
):

    if len(df_5m) < 3:
        return False

    current = df_5m.iloc[-1]

    previous = df_5m.iloc[-2]

    current_close = safe_float(
        current["close"]
    )

    current_open = safe_float(
        current["open"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    if side == "LONG":

        return (
            current_close > current_open
            and
            current_close > previous_close
        )

    if side == "SHORT":

        return (
            current_close < current_open
            and
            current_close < previous_close
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
    atr,
):

    entry = safe_float(entry)
    atr = safe_float(atr)

    if entry <= 0:
        return None

    if atr <= 0:
        return None

    all_supports = (
        list(static_support)
        + list(dynamic_support)
    )

    all_resistances = (
        list(static_resistance)
        + list(dynamic_resistance)
    )

    if side == "LONG":

        supports = [
            x
            for x in all_supports
            if x < entry
        ]

        resistances = [
            x
            for x in static_resistance
            if x > entry
        ]

        if not supports:
            return None

        if not resistances:
            return None

        nearest_support = max(
            supports
        )

        tp = min(
            resistances
        )

        sl = (
            nearest_support
            - atr * ATR_BUFFER_MULT
        )

        if sl <= 0:
            return None

        if not (
            sl < entry < tp
        ):
            return None

        risk = (
            entry - sl
        )

        reward = (
            tp - entry
        )

    elif side == "SHORT":

        resistances = [
            x
            for x in all_resistances
            if x > entry
        ]

        supports = [
            x
            for x in static_support
            if x < entry
        ]

        if not resistances:
            return None

        if not supports:
            return None

        nearest_resistance = min(
            resistances
        )

        tp = max(
            supports
        )

        sl = (
            nearest_resistance
            + atr * ATR_BUFFER_MULT
        )

        if sl <= 0:
            return None

        if not (
            tp < entry < sl
        ):
            return None

        risk = (
            sl - entry
        )

        reward = (
            entry - tp
        )

    else:
        return None

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    rr = (
        reward / risk
    )

    if rr < MIN_RR:
        return None

    sl_pct = (
        risk / entry
    )

    if sl_pct > MAX_SL_PCT:
        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "sl_pct": sl_pct,
    }


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    side,
    trend,
    setup,
    rvol,
    confirmation,
    rr,
    df_15m,
):

    score = 0

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if trend == side:
        score += 3

    elif trend == "NEUTRAL":
        score += 1

    # --------------------------------------------------------
    # SETUP
    # --------------------------------------------------------

    if setup == "BREAKOUT":
        score += 3

    elif setup == "HIGH-VOLUME REJECTION":
        score += 2

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    if rvol >= RVOL_STRONG:
        score += 3

    elif rvol >= RVOL_ABNORMAL:
        score += 2

    # --------------------------------------------------------
    # 5M CONFIRMATION
    # --------------------------------------------------------

    if confirmation:
        score += 2

    # --------------------------------------------------------
    # 15M DIRECTION
    # --------------------------------------------------------

    if len(df_15m) >= 1:

        candle = df_15m.iloc[-1]

        if side == "LONG":

            if (
                candle["close"]
                > candle["open"]
            ):
                score += 1

        elif side == "SHORT":

            if (
                candle["close"]
                < candle["open"]
            ):
                score += 1

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    if rr >= 4.0:
        score += 2

    elif rr >= 3.0:
        score += 1

    return int(score)


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

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            setup TEXT NOT NULL,
            entry REAL NOT NULL,
            sl REAL NOT NULL,
            tp REAL NOT NULL,
            rr REAL NOT NULL,
            score INTEGER NOT NULL,
            rvol REAL NOT NULL,
            trend TEXT NOT NULL,
            status TEXT NOT NULL,
            result TEXT,
            pnl REAL,
            created_at TEXT NOT NULL,
            closed_at TEXT,
            exit_reason TEXT,
            closed_reported INTEGER DEFAULT 0
        )
        """
    )

    # --------------------------------------------------------
    # ADD MISSING COLUMNS FOR OLD DATABASE
    # --------------------------------------------------------

    columns = set()

    try:

        cur.execute(
            "PRAGMA table_info(trades)"
        )

        for row in cur.fetchall():

            columns.add(
                row["name"]
            )

    except Exception:
        pass

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
# GET OPEN TRADES
# ============================================================

def get_open_trades():

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

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# OPEN SYMBOL CHECK
# ============================================================

def has_open_symbol(
    symbol
):

    target = normalize_symbol(
        symbol
    )

    for trade in get_open_trades():

        if (
            normalize_symbol(
                trade["symbol"]
            )
            == target
        ):
            return True

    return False


# ============================================================
# ENFORCE MAX OPEN TRADES
# ============================================================

def enforce_max_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute(
        """
        SELECT id
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
        """
    )

    rows = cur.fetchall()

    if len(rows) <= MAX_OPEN_TRADES:

        conn.close()

        return 0

    extras = rows[
        MAX_OPEN_TRADES:
    ]

    removed = 0

    for row in extras:

        cur.execute(
            """
            UPDATE trades
            SET
                status = 'REMOVED',
                result = 'REMOVED',
                pnl = 0,
                exit_reason =
                    'OPEN_LIMIT_CLEANUP',
                closed_at = ?,
                closed_reported = 1
            WHERE id = ?
            AND status = 'OPEN'
            """,
            (
                iso_now(),
                row["id"],
            ),
        )

        removed += (
            cur.rowcount
        )

    conn.commit()

    conn.close()

    return removed


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(
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
):

    if has_open_symbol(symbol):
        return False

    conn = db_connect()

    cur = conn.cursor()

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
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, 'OPEN', NULL, NULL, ?, NULL,
            NULL, 0
        )
        """,
        (
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
            iso_now(),
        ),
    )

    conn.commit()

    conn.close()

    return True


# ============================================================
# CALCULATE PNL
# ============================================================

def calculate_pnl(
    trade,
    exit_price,
):

    entry = safe_float(
        trade["entry"]
    )

    exit_price = safe_float(
        exit_price
    )

    if entry <= 0:
        return 0.0

    if trade["side"] == "LONG":

        return (
            (exit_price - entry)
            / entry
        ) * 100.0

    if trade["side"] == "SHORT":

        return (
            (entry - exit_price)
            / entry
        ) * 100.0

    return 0.0


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    open_trades = get_open_trades()

    if not open_trades:
        return []

    newly_closed_ids = []

    conn = db_connect()

    cur = conn.cursor()

    for trade in open_trades:

        symbol = trade["symbol"]

        try:

            # ------------------------------------------------
            # IMPORTANT:
            # Use RAW symbol from DB.
            # ------------------------------------------------

            df = get_candles(
                symbol,
                TF_5M,
                50,
            )

            if df.empty:
                continue

            if len(df) < 2:
                continue

            # ------------------------------------------------
            # Latest CLOSED candle
            # ------------------------------------------------

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

            exit_price = None
            result = None
            exit_reason = None

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if trade["side"] == "LONG":

                # Conservative policy:
                # SL first when both are touched.

                if low <= sl:

                    exit_price = sl
                    result = "LOSS"
                    exit_reason = "SL"

                elif high >= tp:

                    exit_price = tp
                    result = "WIN"
                    exit_reason = "TP"

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            elif trade["side"] == "SHORT":

                # Conservative policy:
                # SL first when both are touched.

                if high >= sl:

                    exit_price = sl
                    result = "LOSS"
                    exit_reason = "SL"

                elif low <= tp:

                    exit_price = tp
                    result = "WIN"
                    exit_reason = "TP"

            # ------------------------------------------------
            # NO EXIT
            # ------------------------------------------------

            if (
                exit_price is None
                or result is None
            ):
                continue

            pnl = calculate_pnl(
                trade,
                exit_price,
            )

            cur.execute(
                """
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = ?,
                    pnl = ?,
                    closed_at = ?,
                    exit_reason = ?,
                    closed_reported = 0
                WHERE id = ?
                AND status = 'OPEN'
                """,
                (
                    result,
                    pnl,
                    iso_now(),
                    exit_reason,
                    trade["id"],
                ),
            )

            if cur.rowcount > 0:

                newly_closed_ids.append(
                    trade["id"]
                )

                print(
                    "[EXIT] "
                    f"{symbol} "
                    f"{trade['side']} "
                    f"-> {result} "
                    f"@ {exit_price:.8g} "
                    f"({exit_reason}) "
                    f"PnL={pnl:.3f}%"
                )

        except Exception as exc:

            print(
                f"[EXIT ERROR] "
                f"{symbol}: {exc}"
            )

    conn.commit()

    conn.close()

    return newly_closed_ids


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
        WHERE status = 'CLOSED'
        AND COALESCE(
            closed_reported,
            0
        ) = 0
        ORDER BY id ASC
        """
    )

    rows = cur.fetchall()

    conn.close()

    return [
        dict(row)
        for row in rows
    ]


# ============================================================
# MARK CLOSED AS REPORTED
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
            SET closed_reported = 1
            WHERE id = ?
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

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
        """
    )

    open_count = int(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        """
    )

    closed_count = int(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # WINS
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'WIN'
        """
    )

    wins = int(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # LOSSES
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'LOSS'
        """
    )

    losses = int(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # PNL
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            COALESCE(
                SUM(pnl),
                0
            )
        FROM trades
        WHERE status = 'CLOSED'
        """
    )

    net_pnl = safe_float(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # LONG PNL
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            COALESCE(
                SUM(pnl),
                0
            )
        FROM trades
        WHERE status = 'CLOSED'
        AND side = 'LONG'
        """
    )

    long_pnl = safe_float(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # SHORT PNL
    # --------------------------------------------------------

    cur.execute(
        """
        SELECT
            COALESCE(
                SUM(pnl),
                0
            )
        FROM trades
        WHERE status = 'CLOSED'
        AND side = 'SHORT'
        """
    )

    short_pnl = safe_float(
        cur.fetchone()[0]
    )

    # --------------------------------------------------------
    # WIN RATE
    # --------------------------------------------------------

    if (
        wins + losses
    ) > 0:

        win_rate = (
            wins
            /
            (wins + losses)
        ) * 100

    else:

        win_rate = 0.0

    conn.close()

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "net_pnl": net_pnl,
        "long_pnl": long_pnl,
        "short_pnl": short_pnl,
        "win_rate": win_rate,
    }


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    market,
    stats,
):

    symbol = market[
        "symbol"
    ]

    stats["markets"] += 1

    try:

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        df_1h = get_candles(
            symbol,
            TF_1H,
            OHLCV_LIMIT,
        )

        if df_1h.empty:
            stats["data"] += 1
            return None

        if len(df_1h) < 60:
            stats["data"] += 1
            return None

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        df_15m = get_candles(
            symbol,
            TF_15M,
            OHLCV_LIMIT,
        )

        if df_15m.empty:
            stats["data"] += 1
            return None

        if len(df_15m) < 30:
            stats["data"] += 1
            return None

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df_5m = get_candles(
            symbol,
            TF_5M,
            OHLCV_LIMIT,
        )

        if df_5m.empty:
            stats["data"] += 1
            return None

        if len(df_5m) < 30:
            stats["data"] += 1
            return None

        stats["data_ok"] += 1

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        entry = safe_float(
            df_5m[
                "close"
            ].iloc[-1]
        )

        if entry <= 0:
            return None

        # ----------------------------------------------------
        # TREND
        # ----------------------------------------------------

        trend = determine_trend(
            df_1h
        )

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = calculate_atr(
            df_15m
        )

        if atr <= 0:
            return None

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            df_15m
        )

        # ----------------------------------------------------
        # S/R
        # ----------------------------------------------------

        static_support, static_resistance = (
            build_static_sr(
                df_1h,
                entry,
            )
        )

        dynamic_support, dynamic_resistance = (
            build_dynamic_sr(
                df_15m,
                entry,
            )
        )

        stats["sr"] += 1

        # ----------------------------------------------------
        # SETUP
        # ----------------------------------------------------

        setup = detect_breakout(
            df_15m,
            rvol,
        )

        if setup is None:

            setup = detect_rejection(
                df_15m,
                rvol,
            )

        if setup is None:
            stats["setup"] += 1
            return None

        stats["setup_ok"] += 1

        side = setup[
            "side"
        ]

        setup_name = setup[
            "setup"
        ]

        # ----------------------------------------------------
        # TREND FILTER
        #
        # NEUTRAL is allowed.
        # Opposite trend is rejected.
        # ----------------------------------------------------

        if (
            trend != "NEUTRAL"
            and
            trend != side
        ):

            stats[
                "trend_reject"
            ] += 1

            return None

        stats["trend_ok"] += 1

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        confirmation = confirm_5m(
            df_5m,
            side,
        )

        if not confirmation:

            stats[
                "confirm_reject"
            ] += 1

            return None

        stats["confirm_ok"] += 1

        # ----------------------------------------------------
        # TRADE
        # ----------------------------------------------------

        trade_calc = calculate_trade(
            side=side,
            entry=entry,
            static_support=
                static_support,
            static_resistance=
                static_resistance,
            dynamic_support=
                dynamic_support,
            dynamic_resistance=
                dynamic_resistance,
            atr=atr,
        )

        if trade_calc is None:

            stats[
                "risk_reject"
            ] += 1

            return None

        stats["risk_ok"] += 1

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score = calculate_score(
            side=side,
            trend=trend,
            setup=setup_name,
            rvol=rvol,
            confirmation=confirmation,
            rr=trade_calc["rr"],
            df_15m=df_15m,
        )

        if score < MIN_SIGNAL_SCORE:

            stats[
                "score_reject"
            ] += 1

            return None

        stats["score_ok"] += 1

        return {
            "symbol": symbol,
            "side": side,
            "setup": setup_name,
            "entry":
                trade_calc["entry"],
            "sl":
                trade_calc["sl"],
            "tp":
                trade_calc["tp"],
            "rr":
                trade_calc["rr"],
            "score": score,
            "rvol": rvol,
            "trend": trend,
            "confirmation":
                confirmation,
        }

    except Exception as exc:

        print(
            f"[SCAN ERROR] "
            f"{symbol}: {exc}"
        )

        stats["errors"] += 1

        return None


# ============================================================
# SCAN ALL MARKETS
# ============================================================

def scan_all_markets(
    markets
):

    stats = {
        "markets": 0,
        "data": 0,
        "data_ok": 0,
        "sr": 0,
        "setup": 0,
        "setup_ok": 0,
        "trend_reject": 0,
        "trend_ok": 0,
        "confirm_reject": 0,
        "confirm_ok": 0,
        "risk_reject": 0,
        "risk_ok": 0,
        "score_reject": 0,
        "score_ok": 0,
        "errors": 0,
    }

    candidates = []

    for market in markets:

        result = scan_market(
            market,
            stats,
        )

        if result is not None:

            candidates.append(
                result
            )

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rvol"],
            x["rr"],
        ),
        reverse=True,
    )

    return candidates, stats


# ============================================================
# FORMAT PNL
# ============================================================

def format_pnl(
    value
):

    value = safe_float(
        value
    )

    if value > 0:

        return f"+{value:.2f}%"

    return f"{value:.2f}%"


# ============================================================
# BUILD REPORT
# ============================================================

def build_report(
    stats,
    scan_stats,
    markets_count,
    candidates_count,
    closed_for_report,
    live_prices,
    duration,
):

    lines = []

    lines.append(
        "📊 CRYPTO VOLUME-KHAT 100"
    )

    lines.append(
        "🛠 v2.7 FIXED"
    )

    lines.append(
        f"🕐 {format_time()}"
    )

    lines.append(
        "⏱ 5M CLOSED | TOP 100"
    )

    lines.append(
        "🤖 PAPER TRADING ONLY"
    )

    lines.append(
        ""
    )

    # ========================================================
    # CAPACITY
    # ========================================================

    open_count = scan_stats.get(
        "open_count",
        0
    )

    capacity = max(
        0,
        MAX_OPEN_TRADES
        - open_count
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📌 CAPACITY"
    )

    lines.append(
        f"OPEN: {open_count}/{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"FREE SLOTS: {capacity}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # STRATEGY
    # ========================================================

    lines.append(
        "🎯 STRATEGY"
    )

    lines.append(
        "1H: Trend + Static S/R"
    )

    lines.append(
        "15M: Dynamic S/R + RVOL"
    )

    lines.append(
        "SETUP: BREAKOUT / HIGH-VOLUME REJECTION"
    )

    lines.append(
        "5M: Entry Confirmation"
    )

    lines.append(
        "RR ≥ 2 | SL ≤ 0.80%"
    )

    lines.append(
        ""
    )

    # ========================================================
    # OPEN TRADES
    # ========================================================

    open_trades = get_open_trades()

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔵 OPEN TRADES"
    )

    if not open_trades:

        lines.append(
            "None"
        )

    else:

        for trade in open_trades:

            symbol = trade[
                "symbol"
            ]

            live = live_prices.get(
                normalize_symbol(
                    symbol
                )
            )

            live_pnl = None

            if live:

                live_pnl = calculate_pnl(
                    trade,
                    live,
                )

            lines.append(
                ""
            )

            lines.append(
                f"{trade['side']} "
                f"{symbol}"
            )

            lines.append(
                f"Entry {trade['entry']:.8g} | "
                f"SL {trade['sl']:.8g} | "
                f"TP {trade['tp']:.8g}"
            )

            lines.append(
                f"RR {trade['rr']:.2f} | "
                f"Score {trade['score']} | "
                f"RVOL {trade['rvol']:.2f}"
            )

            if live:

                lines.append(
                    f"Live {live:.8g} | "
                    f"PnL {format_pnl(live_pnl)}"
                )

            else:

                lines.append(
                    "Live price unavailable"
                )

    # ========================================================
    # CLOSED SINCE PREVIOUS REPORT
    # ========================================================

    lines.append(
        ""
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔴 CLOSED SINCE PREVIOUS REPORT"
    )

    if not closed_for_report:

        lines.append(
            "None"
        )

    else:

        for trade in closed_for_report:

            # ------------------------------------------------
            # IMPORTANT:
            # Do NOT put a multiline expression directly
            # inside an f-string.
            #
            # This fixes the previous SyntaxError.
            # ------------------------------------------------

            exit_price = (
                trade["tp"]
                if trade["exit_reason"] == "TP"
                else trade["sl"]
            )

            result_icon = (
                "✅"
                if trade["result"] == "WIN"
                else "❌"
            )

            lines.append(
                ""
            )

            lines.append(
                f"{result_icon} "
                f"{trade['side']} "
                f"{trade['symbol']} "
                f"{trade['result']}"
            )

            lines.append(
                f"Entry {trade['entry']:.8g} | "
                f"Exit {exit_price:.8g}"
            )

            lines.append(
                f"Reason {trade['exit_reason']} | "
                f"PnL {format_pnl(trade['pnl'])}"
            )

            lines.append(
                f"RR {trade['rr']:.2f} | "
                f"Score {trade['score']}"
            )

    # ========================================================
    # STATS
    # ========================================================

    lines.append(
        ""
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"OPEN: {scan_stats.get('open_count', 0)}"
    )

    lines.append(
        f"CLOSED: {scan_stats.get('closed_count', 0)}"
    )

    lines.append(
        f"WINS: {scan_stats.get('wins', 0)}"
    )

    lines.append(
        f"LOSSES: {scan_stats.get('losses', 0)}"
    )

    lines.append(
        f"WIN RATE: "
        f"{scan_stats.get('win_rate', 0):.1f}%"
    )

    lines.append(
        f"NET PNL: "
        f"{format_pnl(scan_stats.get('net_pnl', 0))}"
    )

    lines.append(
        f"LONG PNL: "
        f"{format_pnl(scan_stats.get('long_pnl', 0))}"
    )

    lines.append(
        f"SHORT PNL: "
        f"{format_pnl(scan_stats.get('short_pnl', 0))}"
    )

    # ========================================================
    # SCAN FUNNEL
    # ========================================================

    lines.append(
        ""
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔎 SCAN FUNNEL"
    )

    lines.append(
        f"Markets: "
        f"{scan_stats.get('markets', 0)}"
    )

    lines.append(
        f"Data OK: "
        f"{scan_stats.get('data_ok', 0)}"
    )

    lines.append(
        f"S/R Checked: "
        f"{scan_stats.get('sr', 0)}"
    )

    lines.append(
        f"Setup OK: "
        f"{scan_stats.get('setup_ok', 0)}"
    )

    lines.append(
        f"Trend OK: "
        f"{scan_stats.get('trend_ok', 0)}"
    )

    lines.append(
        f"5M Confirm OK: "
        f"{scan_stats.get('confirm_ok', 0)}"
    )

    lines.append(
        f"Risk OK: "
        f"{scan_stats.get('risk_ok', 0)}"
    )

    lines.append(
        f"Score OK: "
        f"{scan_stats.get('score_ok', 0)}"
    )

    lines.append(
        f"Candidates: "
        f"{candidates_count}"
    )

    lines.append(
        f"Errors: "
        f"{scan_stats.get('errors', 0)}"
    )

    # ========================================================
    # DURATION
    # ========================================================

    lines.append(
        ""
    )

    lines.append(
        f"⏱ Scan duration: "
        f"{duration:.1f}s"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    return "\n".join(
        lines
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    text
):

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
        "text": text,
        "disable_web_page_preview":
            True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        if data.get("ok"):

            print(
                "[TELEGRAM] Sent."
            )

            return True

        print(
            "[TELEGRAM] API error:",
            data,
        )

        return False

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    print(
        "=================================================="
    )

    print(
        "KRAKEN FUTURES VOLUME-KHAT 100 v2.7 FIXED"
    )

    print(
        "PAPER TRADING ONLY"
    )

    print(
        "=================================================="
    )

    # ========================================================
    # DATABASE
    # ========================================================

    init_db()

    # ========================================================
    # FIRST:
    # CHECK EXISTING OPEN TRADES
    # ========================================================

    print(
        "[STEP 1] Checking existing trades..."
    )

    closed_ids = (
        update_open_trades()
    )

    if closed_ids:

        print(
            "[EXIT] Newly closed:",
            closed_ids,
        )

    # ========================================================
    # CLEAN OPEN LIMIT
    # ========================================================

    removed = (
        enforce_max_open_trades()
    )

    if removed:

        print(
            f"[CLEANUP] "
            f"Removed {removed} "
            f"extra OPEN trades."
        )

    # ========================================================
    # CURRENT STATS
    # ========================================================

    stats = get_stats()

    print(
        f"[STATS] "
        f"OPEN={stats['open']} "
        f"CLOSED={stats['closed']} "
        f"W={stats['wins']} "
        f"L={stats['losses']} "
        f"PnL={stats['net_pnl']:.3f}%"
    )

    # ========================================================
    # DISCOVER MARKETS
    # ========================================================

    print(
        "[STEP 2] Discovering TOP 100 markets..."
    )

    markets = (
        discover_top_markets()
    )

    if not markets:

        print(
            "[FATAL] "
            "No markets found."
        )

        return

    # ========================================================
    # LIVE PRICES
    # ========================================================

    print(
        "[STEP 3] Getting live prices..."
    )

    market_symbols = [
        x["symbol"]
        for x in markets
    ]

    live_prices = (
        get_live_price_map(
            market_symbols
        )
    )

    # ========================================================
    # SCAN
    # ========================================================

    print(
        "[STEP 4] Scanning markets..."
    )

    candidates, scan_stats = (
        scan_all_markets(
            markets
        )
    )

    # ========================================================
    # CURRENT OPEN COUNT
    # ========================================================

    open_trades = (
        get_open_trades()
    )

    open_count = len(
        open_trades
    )

    scan_stats[
        "open_count"
    ] = open_count

    scan_stats[
        "closed_count"
    ] = stats["closed"]

    scan_stats[
        "wins"
    ] = stats["wins"]

    scan_stats[
        "losses"
    ] = stats["losses"]

    scan_stats[
        "net_pnl"
    ] = stats["net_pnl"]

    scan_stats[
        "long_pnl"
    ] = stats["long_pnl"]

    scan_stats[
        "short_pnl"
    ] = stats["short_pnl"]

    scan_stats[
        "win_rate"
    ] = stats["win_rate"]

    # ========================================================
    # CAPACITY
    # ========================================================

    capacity = max(
        0,
        MAX_OPEN_TRADES
        - open_count
    )

    capacity = min(
        capacity,
        MAX_NEW_SIGNALS
    )

    print(
        f"[CAPACITY] "
        f"Open={open_count}/"
        f"{MAX_OPEN_TRADES} "
        f"Free={capacity}"
    )

    # ========================================================
    # SAVE BEST SIGNALS
    # ========================================================

    saved = 0

    if capacity > 0:

        for candidate in candidates:

            if saved >= capacity:
                break

            symbol = candidate[
                "symbol"
            ]

            if has_open_symbol(
                symbol
            ):
                continue

            success = save_signal(
                symbol=
                    candidate["symbol"],
                side=
                    candidate["side"],
                setup=
                    candidate["setup"],
                entry=
                    candidate["entry"],
                sl=
                    candidate["sl"],
                tp=
                    candidate["tp"],
                rr=
                    candidate["rr"],
                score=
                    candidate["score"],
                rvol=
                    candidate["rvol"],
                trend=
                    candidate["trend"],
            )

            if success:

                saved += 1

                print(
                    "[NEW SIGNAL] "
                    f"{candidate['side']} "
                    f"{candidate['symbol']} "
                    f"{candidate['setup']} "
                    f"Entry={candidate['entry']:.8g} "
                    f"SL={candidate['sl']:.8g} "
                    f"TP={candidate['tp']:.8g} "
                    f"RR={candidate['rr']:.2f} "
                    f"Score={candidate['score']} "
                    f"RVOL={candidate['rvol']:.2f}"
                )

    # ========================================================
    # FINAL OPEN LIMIT CHECK
    # ========================================================

    enforce_max_open_trades()

    # ========================================================
    # REFRESH LIVE PRICES
    # ========================================================

    final_open = (
        get_open_trades()
    )

    final_symbols = [
        x["symbol"]
        for x in final_open
    ]

    if final_symbols:

        live_prices = (
            get_live_price_map(
                final_symbols
            )
        )

    else:

        live_prices = {}

    # ========================================================
    # IMPORTANT:
    # GET CLOSED TRADES AFTER UPDATE
    #
    # This includes trades closed in THIS run.
    # ========================================================

    closed_for_report = (
        get_unreported_closed()
    )

    # ========================================================
    # FINAL STATS
    # ========================================================

    final_stats = (
        get_stats()
    )

    scan_stats[
        "open_count"
    ] = final_stats["open"]

    scan_stats[
        "closed_count"
    ] = final_stats["closed"]

    scan_stats[
        "wins"
    ] = final_stats["wins"]

    scan_stats[
        "losses"
    ] = final_stats["losses"]

    scan_stats[
        "net_pnl"
    ] = final_stats["net_pnl"]

    scan_stats[
        "long_pnl"
    ] = final_stats["long_pnl"]

    scan_stats[
        "short_pnl"
    ] = final_stats["short_pnl"]

    scan_stats[
        "win_rate"
    ] = final_stats["win_rate"]

    # ========================================================
    # REPORT
    # ========================================================

    duration = (
        time.time()
        - start_time
    )

    report = build_report(
        stats=final_stats,
        scan_stats=scan_stats,
        markets_count=
            len(markets),
        candidates_count=
            len(candidates),
        closed_for_report=
            closed_for_report,
        live_prices=
            live_prices,
        duration=duration,
    )

    print(
        ""
    )

    print(
        report
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    telegram_ok = (
        send_telegram(
            report
        )
    )

    # ========================================================
    # MARK CLOSED AS REPORTED
    #
    # Only after successful Telegram send.
    # ========================================================

    if telegram_ok:

        ids = [
            x["id"]
            for x in closed_for_report
        ]

        mark_closed_reported(
            ids
        )

    # ========================================================
    # FINAL
    # ========================================================

    print(
        ""
    )

    print(
        "=================================================="
    )

    print(
        f"Markets scanned: "
        f"{len(markets)}"
    )

    print(
        f"Candidates: "
        f"{len(candidates)}"
    )

    print(
        f"New signals saved: "
        f"{saved}"
    )

    print(
        f"Closed reported: "
        f"{len(closed_for_report)}"
    )

    print(
        f"Duration: "
        f"{duration:.1f}s"
    )

    print(
        "=================================================="
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "[STOP] Interrupted."
        )

    except Exception as exc:

        print(
            f"[FATAL ERROR] {exc}"
        )

        raise
