# ============================================================
# VOLUME-KHAT 100 v1.1
# ============================================================
#
# KRAKEN FUTURES TOP 100
#
# STRATEGY:
#
# 1H  = Trend + Static Support / Resistance
# 15M = Dynamic Support / Resistance + RVOL + Setup
# 5M  = Entry Confirmation
#
# SETUPS:
#   - BREAKOUT
#   - HIGH VOLUME REJECTION
#
# RISK:
#   Minimum RR = 1:2
#
# IMPORTANT:
#   This version performs ONE complete scan per execution.
#   Designed for GitHub Actions every 5 minutes.
#
# REAL TRADING:
#   DISABLED
#
# ============================================================

import os
import sys
import time
import math
import sqlite3
import traceback
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIGURATION
# ============================================================

VERSION = "VOLUME-KHAT 100 v1.1"

TOP_N = 100

TIMEFRAME_1H = "1h"
TIMEFRAME_15M = "15m"
TIMEFRAME_5M = "5m"

CANDLE_LIMIT_1H = 180
CANDLE_LIMIT_15M = 180
CANDLE_LIMIT_5M = 120

RVOL_PERIOD = 20

RVOL_MIN = 2.0
RVOL_STRONG = 3.0

SWING_LEFT = 2
SWING_RIGHT = 2

DYNAMIC_PROXIMITY_MAX = 0.010
DYNAMIC_PROXIMITY_STRONG = 0.005

BREAKOUT_MIN_PCT = 0.0015

MIN_BODY_RATIO = 0.45
BREAKOUT_BODY_RATIO = 0.50

STATIC_CLUSTER_PCT = 0.0030

ATR_PERIOD = 14
ATR_SL_BUFFER = 0.20

MIN_RR = 2.0

MIN_SCORE = 9
COUNTER_TREND_MIN_SCORE = 12

MAX_SIGNALS_TELEGRAM = 5

REQUEST_TIMEOUT = 15

MAX_WORKERS = 8

DB_FILE = "volume_khat_100.db"

REAL_TRADING = False


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# KRAKEN FUTURES API
# ============================================================

INSTRUMENTS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

CANDLES_URL = (
    "https://futures.kraken.com/api/charts/v1/trade/{symbol}/{resolution}"
)


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/1.1"
})


# ============================================================
# STATISTICS
# ============================================================

stats = {
    "scanned": 0,
    "data_ok": 0,

    "trend_bull": 0,
    "trend_bear": 0,
    "trend_neutral": 0,

    "dynamic_sr": 0,
    "rvol_2": 0,
    "rvol_3": 0,

    "breakout": 0,
    "rejection": 0,

    "five_min_confirmation": 0,
    "retest": 0,

    "static_sr": 0,
    "rr_valid": 0,

    "qualified": 0
}


# ============================================================
# LOGGING
# ============================================================

def log(message=""):
    print(message, flush=True)


# ============================================================
# TELEGRAM SEND
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        log("Telegram configuration missing.")
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

        response = SESSION.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:
            return True

        log(
            f"Telegram error: "
            f"{response.status_code} "
            f"{response.text[:300]}"
        )

    except Exception as exc:

        log(f"Telegram exception: {exc}")

    return False


# ============================================================
# SAFE REQUEST
# ============================================================

def safe_get(url, params=None, retries=3):

    last_error = None

    for attempt in range(retries):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as exc:

            last_error = exc

            if attempt < retries - 1:
                time.sleep(1.5 * (attempt + 1))

    log(
        f"Request failed: {url} | "
        f"{last_error}"
    )

    return None


# ============================================================
# GET INSTRUMENTS
# ============================================================

def get_instruments():

    data = safe_get(INSTRUMENTS_URL)

    if not data:
        return []

    if isinstance(data, dict):

        for key in (
            "instruments",
            "data",
            "result"
        ):

            if isinstance(data.get(key), list):
                return data[key]

    if isinstance(data, list):
        return data

    return []


# ============================================================
# PERPETUAL DETECTION
# ============================================================

def is_perpetual(item):

    text = str(item).lower()

    if "perpetual" in text:
        return True

    symbol = str(
        item.get("symbol", "")
    ).lower()

    typ = str(
        item.get("type", "")
    ).lower()

    contract = str(
        item.get("contractType", "")
    ).lower()

    return (
        "perpetual" in symbol
        or "perpetual" in typ
        or "perpetual" in contract
    )


# ============================================================
# SYMBOL EXTRACTION
# ============================================================

def get_symbol(item):

    for key in (
        "symbol",
        "ticker",
        "instrumentName"
    ):

        value = item.get(key)

        if value:
            return str(value)

    return None


# ============================================================
# TICKER VOLUME
# ============================================================

def get_volume_value(ticker):

    possible_keys = [
        "volume24h",
        "volume",
        "vol24h",
        "volumeQuote",
        "quoteVolume",
        "volume_usd"
    ]

    for key in possible_keys:

        value = ticker.get(key)

        if value is None:
            continue

        try:
            value = float(value)

            if math.isfinite(value):
                return abs(value)

        except Exception:
            pass

    return 0.0


# ============================================================
# GET TOP 100
# ============================================================

def get_top_100():

    instruments = get_instruments()

    ticker_data = safe_get(TICKERS_URL)

    if not instruments or not ticker_data:
        return []

    if isinstance(ticker_data, dict):

        tickers = (
            ticker_data.get("tickers")
            or ticker_data.get("data")
            or ticker_data.get("result")
            or []
        )

    elif isinstance(ticker_data, list):

        tickers = ticker_data

    else:

        tickers = []

    ticker_map = {}

    for ticker in tickers:

        symbol = str(
            ticker.get("symbol")
            or ticker.get("instrument")
            or ticker.get("ticker")
            or ""
        )

        if symbol:
            ticker_map[symbol] = ticker

    markets = []

    for item in instruments:

        if not is_perpetual(item):
            continue

        symbol = get_symbol(item)

        if not symbol:
            continue

        ticker = ticker_map.get(symbol)

        if ticker is None:

            # Try common symbol variants
            for key, value in ticker_map.items():

                if (
                    key == symbol
                    or key.upper() == symbol.upper()
                ):

                    ticker = value
                    break

        if ticker is None:
            continue

        volume = get_volume_value(ticker)

        if volume <= 0:
            continue

        markets.append({
            "symbol": symbol,
            "volume": volume
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, limit):

    url = CANDLES_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = safe_get(
        url,
        params={
            "count": limit
        }
    )

    if not data:
        return None

    rows = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "result"
        ):

            if isinstance(data.get(key), list):
                rows = data[key]
                break

    elif isinstance(data, list):

        rows = data

    if not rows:
        return None

    parsed = []

    for row in rows:

        if isinstance(row, dict):

            try:

                parsed.append({
                    "time": row.get("time"),
                    "open": float(row.get("open")),
                    "high": float(row.get("high")),
                    "low": float(row.get("low")),
                    "close": float(row.get("close")),
                    "volume": float(row.get("volume", 0))
                })

            except Exception:
                continue

        elif isinstance(row, (list, tuple)):

            if len(row) < 6:
                continue

            try:

                parsed.append({
                    "time": row[0],
                    "open": float(row[1]),
                    "high": float(row[2]),
                    "low": float(row[3]),
                    "close": float(row[4]),
                    "volume": float(row[5])
                })

            except Exception:
                continue

    if len(parsed) < 30:
        return None

    df = pd.DataFrame(parsed)

    df["time"] = pd.to_numeric(
        df["time"],
        errors="coerce"
    )

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df = df.sort_values("time")
    df = df.drop_duplicates("time")

    # ========================================================
    # REMOVE CURRENT / INCOMPLETE CANDLE
    # ========================================================

    if len(df) > 1:
        df = df.iloc[:-1].copy()

    if len(df) < 30:
        return None

    df.reset_index(
        drop=True,
        inplace=True
    )

    return df


# ============================================================
# EMA TREND
# ============================================================

def get_trend(df):

    if len(df) < 60:
        return "NEUTRAL"

    close = df["close"]

    ema20 = close.ewm(
        span=20,
        adjust=False
    ).mean()

    ema50 = close.ewm(
        span=50,
        adjust=False
    ).mean()

    last_close = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])

    if (
        last_close > last_ema20
        and last_ema20 > last_ema50
    ):
        return "BULL"

    if (
        last_close < last_ema20
        and last_ema20 < last_ema50
    ):
        return "BEAR"

    return "NEUTRAL"


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=ATR_PERIOD):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(
        period
    ).mean()

    value = atr.iloc[-1]

    if pd.isna(value):
        return float(
            (high.iloc[-1] - low.iloc[-1])
        )

    return float(value)


# ============================================================
# SWING HIGHS / LOWS
# ============================================================

def find_swings(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    highs = []
    lows = []

    high = df["high"].values
    low = df["low"].values

    for i in range(
        left,
        len(df) - right
    ):

        current_high = high[i]

        left_highs = high[
            i-left:i
        ]

        right_highs = high[
            i+1:i+right+1
        ]

        if (
            current_high > left_highs.max()
            and current_high > right_highs.max()
        ):
            highs.append(
                (i, float(current_high))
            )

        current_low = low[i]

        left_lows = low[
            i-left:i
        ]

        right_lows = low[
            i+1:i+right+1
        ]

        if (
            current_low < left_lows.min()
            and current_low < right_lows.min()
        ):
            lows.append(
                (i, float(current_low))
            )

    return highs, lows


# ============================================================
# DYNAMIC LINE
# ============================================================

def project_line(p1, p2, x):

    x1, y1 = p1
    x2, y2 = p2

    if x2 == x1:
        return y2

    slope = (
        (y2 - y1)
        / float(x2 - x1)
    )

    return y2 + slope * (x - x2)


# ============================================================
# DYNAMIC S/R
# ============================================================

def dynamic_levels(df):

    highs, lows = find_swings(df)

    result = {
        "resistance": None,
        "support": None,
        "resistance_touches": 0,
        "support_touches": 0
    }

    n = len(df)

    # --------------------------------------------------------
    # RESISTANCE
    # --------------------------------------------------------

    if len(highs) >= 2:

        p1 = highs[-2]
        p2 = highs[-1]

        resistance = project_line(
            p1,
            p2,
            n - 1
        )

        if resistance > 0:

            touches = 0

            for _, price in highs:

                if abs(
                    price - resistance
                ) / resistance <= 0.01:

                    touches += 1

            result["resistance"] = float(
                resistance
            )

            result[
                "resistance_touches"
            ] = max(touches, 2)

    # --------------------------------------------------------
    # SUPPORT
    # --------------------------------------------------------

    if len(lows) >= 2:

        p1 = lows[-2]
        p2 = lows[-1]

        support = project_line(
            p1,
            p2,
            n - 1
        )

        if support > 0:

            touches = 0

            for _, price in lows:

                if abs(
                    price - support
                ) / support <= 0.01:

                    touches += 1

            result["support"] = float(
                support
            )

            result[
                "support_touches"
            ] = max(touches, 2)

    return result


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(df):

    if len(df) < RVOL_PERIOD + 2:
        return 0.0

    volumes = df["volume"]

    current_volume = float(
        volumes.iloc[-1]
    )

    # Previous 20 CLOSED candles.
    baseline = volumes.iloc[
        -(RVOL_PERIOD + 1):-1
    ]

    average_volume = float(
        baseline.mean()
    )

    if average_volume <= 0:
        return 0.0

    return (
        current_volume
        / average_volume
    )


# ============================================================
# CANDLE INFORMATION
# ============================================================

def candle_info(row):

    open_price = float(row["open"])
    high = float(row["high"])
    low = float(row["low"])
    close = float(row["close"])

    candle_range = high - low

    if candle_range <= 0:
        return {
            "bull": False,
            "bear": False,
            "body_ratio": 0.0,
            "upper_wick": 0.0,
            "lower_wick": 0.0
        }

    body = abs(
        close - open_price
    )

    upper_wick = (
        high
        - max(open_price, close)
    )

    lower_wick = (
        min(open_price, close)
        - low
    )

    return {
        "bull": close > open_price,
        "bear": close < open_price,
        "body_ratio": body / candle_range,
        "upper_wick": upper_wick / candle_range,
        "lower_wick": lower_wick / candle_range
    }


# ============================================================
# BREAKOUT DETECTION
# ============================================================

def detect_breakout(
    df,
    dynamic
):

    if len(df) < 3:
        return None

    previous = df.iloc[-2]
    current = df.iloc[-1]

    candle = candle_info(current)

    close = float(current["close"])
    previous_close = float(previous["close"])

    resistance = dynamic["resistance"]
    support = dynamic["support"]

    # --------------------------------------------------------
    # LONG BREAKOUT
    # --------------------------------------------------------

    if resistance:

        distance = (
            close - resistance
        ) / resistance

        if (
            previous_close <= resistance
            and close > resistance
            and distance >= BREAKOUT_MIN_PCT
            and candle["bull"]
            and candle["body_ratio"]
            >= BREAKOUT_BODY_RATIO
        ):

            return {
                "setup": "BREAKOUT",
                "direction": "LONG",
                "level": resistance,
                "distance": distance
            }

    # --------------------------------------------------------
    # SHORT BREAKOUT
    # --------------------------------------------------------

    if support:

        distance = (
            support - close
        ) / support

        if (
            previous_close >= support
            and close < support
            and distance >= BREAKOUT_MIN_PCT
            and candle["bear"]
            and candle["body_ratio"]
            >= BREAKOUT_BODY_RATIO
        ):

            return {
                "setup": "BREAKOUT",
                "direction": "SHORT",
                "level": support,
                "distance": distance
            }

    return None


# ============================================================
# REJECTION DETECTION
# ============================================================

def detect_rejection(
    df,
    dynamic
):

    if len(df) < 2:
        return None

    current = df.iloc[-1]

    candle = candle_info(current)

    high = float(current["high"])
    low = float(current["low"])
    close = float(current["close"])

    resistance = dynamic["resistance"]
    support = dynamic["support"]

    # --------------------------------------------------------
    # BEARISH REJECTION AT RESISTANCE
    # --------------------------------------------------------

    if resistance:

        distance = abs(
            high - resistance
        ) / resistance

        if (
            distance <= DYNAMIC_PROXIMITY_MAX
            and high >= resistance
            and close < resistance
            and candle["upper_wick"] >= 0.35
            and candle["bear"]
        ):

            return {
                "setup": "REJECTION",
                "direction": "SHORT",
                "level": resistance,
                "distance": distance
            }

    # --------------------------------------------------------
    # BULLISH REJECTION AT SUPPORT
    # --------------------------------------------------------

    if support:

        distance = abs(
            low - support
        ) / support

        if (
            distance <= DYNAMIC_PROXIMITY_MAX
            and low <= support
            and close > support
            and candle["lower_wick"] >= 0.35
            and candle["bull"]
        ):

            return {
                "setup": "REJECTION",
                "direction": "LONG",
                "level": support,
                "distance": distance
            }

    return None


# ============================================================
# RETEST DETECTION
# ============================================================

def detect_retest(
    df,
    level,
    direction
):

    if len(df) < 6:
        return False

    recent = df.iloc[-6:-1]

    tolerance = 0.004

    if direction == "LONG":

        touched = (
            (
                recent["low"] <=
                level * (1 + tolerance)
            )
            &
            (
                recent["high"] >=
                level * (1 - tolerance)
            )
        )

        return bool(touched.any())

    if direction == "SHORT":

        touched = (
            (
                recent["high"] >=
                level * (1 - tolerance)
            )
            &
            (
                recent["low"] <=
                level * (1 + tolerance)
            )
        )

        return bool(touched.any())

    return False


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df,
    direction
):

    if len(df) < 3:
        return False

    current = df.iloc[-1]

    candle = candle_info(current)

    if candle["body_ratio"] < MIN_BODY_RATIO:
        return False

    if direction == "LONG":

        return candle["bull"]

    if direction == "SHORT":

        return candle["bear"]

    return False


# ============================================================
# STATIC SUPPORT / RESISTANCE
# ============================================================

def collect_levels(df):

    highs, lows = find_swings(df)

    levels = []

    for _, price in highs:
        levels.append(float(price))

    for _, price in lows:
        levels.append(float(price))

    return levels


def cluster_levels(
    levels,
    cluster_pct=STATIC_CLUSTER_PCT
):

    if not levels:
        return []

    levels = sorted(levels)

    clusters = []

    current_cluster = [
        levels[0]
    ]

    for price in levels[1:]:

        center = np.mean(
            current_cluster
        )

        if (
            abs(price - center)
            / center
            <= cluster_pct
        ):

            current_cluster.append(
                price
            )

        else:

            clusters.append(
                current_cluster
            )

            current_cluster = [
                price
            ]

    clusters.append(
        current_cluster
    )

    result = []

    for cluster in clusters:

        result.append({
            "price": float(
                np.mean(cluster)
            ),
            "strength": len(cluster)
        })

    return result


def build_static_sr(
    df_1h,
    df_15m,
    entry
):

    levels = []

    levels.extend(
        collect_levels(df_1h)
    )

    levels.extend(
        collect_levels(df_15m)
    )

    zones = cluster_levels(
        levels
    )

    supports = []
    resistances = []

    for zone in zones:

        price = zone["price"]

        if price < entry:
            supports.append(zone)

        elif price > entry:
            resistances.append(zone)

    supports.sort(
        key=lambda x: x["price"],
        reverse=True
    )

    resistances.sort(
        key=lambda x: x["price"]
    )

    support = (
        supports[0]
        if supports
        else None
    )

    resistance = (
        resistances[0]
        if resistances
        else None
    )

    return {
        "support": support,
        "resistance": resistance
    }


# ============================================================
# SL / TP
# ============================================================

def calculate_trade_levels(
    entry,
    direction,
    static_sr,
    atr
):

    if atr <= 0:
        return None

    buffer = atr * ATR_SL_BUFFER

    support = static_sr["support"]
    resistance = static_sr["resistance"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if not support:
            return None

        sl = (
            support["price"]
            - buffer
        )

        if sl >= entry:
            return None

        risk = entry - sl

        candidate_tps = []

        if resistance:
            candidate_tps.append(
                resistance["price"]
            )

        for tp in candidate_tps:

            reward = tp - entry

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                return {
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": rr,
                    "risk_pct": (
                        risk / entry * 100
                    )
                }

        return None

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if not resistance:
            return None

        sl = (
            resistance["price"]
            + buffer
        )

        if sl <= entry:
            return None

        risk = sl - entry

        candidate_tps = []

        if support:
            candidate_tps.append(
                support["price"]
            )

        for tp in candidate_tps:

            reward = entry - tp

            if reward <= 0:
                continue

            rr = reward / risk

            if rr >= MIN_RR:

                return {
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "rr": rr,
                    "risk_pct": (
                        risk / entry * 100
                    )
                }

        return None

    return None


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    direction,
    trend,
    rvol,
    dynamic,
    setup,
    retest,
    confirmed_5m,
    trade
):

    score = 0

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    if rvol >= 2:
        score += 2

    if rvol >= 3:
        score += 1

    # --------------------------------------------------------
    # DYNAMIC S/R
    # --------------------------------------------------------

    touches = 0

    if setup["direction"] == "LONG":

        touches = dynamic[
            "support_touches"
        ]

    else:

        touches = dynamic[
            "resistance_touches"
        ]

    if touches >= 2:
        score += 2

    # --------------------------------------------------------
    # PROXIMITY
    # --------------------------------------------------------

    proximity = setup.get(
        "distance",
        999
    )

    if proximity <= 0.005:
        score += 2

    elif proximity <= 0.010:
        score += 1

    # --------------------------------------------------------
    # SETUP
    # --------------------------------------------------------

    if setup["setup"] in (
        "BREAKOUT",
        "REJECTION"
    ):

        score += 2

    # --------------------------------------------------------
    # RETEST
    # --------------------------------------------------------

    if retest:
        score += 2

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    if confirmed_5m:
        score += 2

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    aligned = (
        (
            direction == "LONG"
            and trend == "BULL"
        )
        or
        (
            direction == "SHORT"
            and trend == "BEAR"
        )
    )

    if aligned:
        score += 2

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    rr = trade["rr"]

    if rr >= 3:
        score += 2

    return score, aligned


# ============================================================
# ANALYZE ONE MARKET
# ============================================================

def analyze_market(market):

    symbol = market["symbol"]

    local = {
        "symbol": symbol,
        "qualified": False
    }

    try:

        df_1h = get_candles(
            symbol,
            TIMEFRAME_1H,
            CANDLE_LIMIT_1H
        )

        df_15m = get_candles(
            symbol,
            TIMEFRAME_15M,
            CANDLE_LIMIT_15M
        )

        df_5m = get_candles(
            symbol,
            TIMEFRAME_5M,
            CANDLE_LIMIT_5M
        )

        if (
            df_1h is None
            or df_15m is None
            or df_5m is None
        ):

            return local

        local["data_ok"] = True

        # ----------------------------------------------------
        # 1H TREND
        # ----------------------------------------------------

        trend = get_trend(df_1h)

        local["trend"] = trend

        # ----------------------------------------------------
        # DYNAMIC S/R
        # ----------------------------------------------------

        dynamic = dynamic_levels(
            df_15m
        )

        has_dynamic = (
            dynamic["resistance"] is not None
            or dynamic["support"] is not None
        )

        if not has_dynamic:
            return local

        local["dynamic_sr"] = True

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            df_15m
        )

        local["rvol"] = rvol

        if rvol < RVOL_MIN:
            return local

        local["rvol_pass"] = True

        # ----------------------------------------------------
        # SETUP
        # ----------------------------------------------------

        setup = detect_breakout(
            df_15m,
            dynamic
        )

        if setup is None:

            setup = detect_rejection(
                df_15m,
                dynamic
            )

        if setup is None:
            return local

        local["setup"] = setup["setup"]
        local["direction"] = setup["direction"]
        local["level"] = setup["level"]

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        confirmed = confirm_5m(
            df_5m,
            setup["direction"]
        )

        if not confirmed:
            return local

        local["confirmed_5m"] = True

        # ----------------------------------------------------
        # RETEST
        # ----------------------------------------------------

        retest = False

        if setup["setup"] == "BREAKOUT":

            retest = detect_retest(
                df_5m,
                setup["level"],
                setup["direction"]
            )

            if not retest:
                return local

        else:

            # Rejection does not require retest.
            retest = False

        local["retest"] = retest

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = float(
            df_5m["close"].iloc[-1]
        )

        # ----------------------------------------------------
        # STATIC S/R
        # ----------------------------------------------------

        static_sr = build_static_sr(
            df_1h,
            df_15m,
            entry
        )

        if (
            static_sr["support"] is None
            or static_sr["resistance"] is None
        ):

            return local

        local["static_sr"] = True

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = calculate_atr(
            df_15m
        )

        # ----------------------------------------------------
        # TRADE LEVELS
        # ----------------------------------------------------

        trade = calculate_trade_levels(
            entry,
            setup["direction"],
            static_sr,
            atr
        )

        if trade is None:
            return local

        local["rr"] = trade["rr"]

        if trade["rr"] < MIN_RR:
            return local

        local["rr_pass"] = True

        # ----------------------------------------------------
        # SCORE
        # ----------------------------------------------------

        score, aligned = calculate_score(
            setup["direction"],
            trend,
            rvol,
            dynamic,
            setup,
            retest,
            confirmed,
            trade
        )

        local["score"] = score
        local["trend_aligned"] = aligned

        # Counter-trend requires stronger score.

        if aligned:

            if score < MIN_SCORE:
                return local

        else:

            if score < COUNTER_TREND_MIN_SCORE:
                return local

        # ----------------------------------------------------
        # QUALIFIED
        # ----------------------------------------------------

        local["qualified"] = True

        local["entry"] = entry
        local["sl"] = trade["sl"]
        local["tp"] = trade["tp"]
        local["rr"] = trade["rr"]
        local["risk_pct"] = trade["risk_pct"]

        local["support"] = (
            static_sr["support"]["price"]
        )

        local["resistance"] = (
            static_sr["resistance"]["price"]
        )

        local["atr"] = atr

        return local

    except Exception as exc:

        local["error"] = str(exc)

        log(
            f"[ERROR] {symbol}: {exc}"
        )

        return local


# ============================================================
# DATABASE
# ============================================================

def init_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            direction TEXT,
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
            pnl_pct REAL
        )
    """)

    conn.commit()

    conn.close()


def save_signal(signal):

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO signals (
            timestamp,
            symbol,
            direction,
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
            pnl_pct
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now(
            timezone.utc
        ).isoformat(),

        signal["symbol"],
        signal["direction"],
        signal["setup"],
        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["rr"],
        signal["score"],
        signal["rvol"],
        signal["trend"],

        "OPEN",
        None,
        0.0
    ))

    conn.commit()

    conn.close()


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 100:
        return f"{value:,.3f}"

    if value >= 1:
        return f"{value:,.4f}"

    if value >= 0.01:
        return f"{value:,.5f}"

    return f"{value:.8f}"


# ============================================================
# SIGNAL TEXT
# ============================================================

def signal_text(signal):

    direction = signal["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return (
        f"{emoji} {direction} | "
        f"{signal['symbol']}\n"
        f"Setup: {signal['setup']}\n"
        f"Score: {signal['score']}\n"
        f"RVOL: {signal['rvol']:.2f}x\n"
        f"1H Trend: {signal['trend']}\n"
        f"Entry: {fmt_price(signal['entry'])}\n"
        f"SL: {fmt_price(signal['sl'])}\n"
        f"TP: {fmt_price(signal['tp'])}\n"
        f"RR: 1:{signal['rr']:.2f}\n"
        f"Risk: {signal['risk_pct']:.2f}%"
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    markets,
    results,
    duration
):

    qualified = [
        r for r in results
        if r.get("qualified")
    ]

    # --------------------------------------------------------
    # COUNT FILTERS
    # --------------------------------------------------------

    scanned = len(markets)

    data_ok = sum(
        1 for r in results
        if r.get("data_ok")
    )

    trend_bull = sum(
        1 for r in results
        if r.get("trend") == "BULL"
    )

    trend_bear = sum(
        1 for r in results
        if r.get("trend") == "BEAR"
    )

    trend_neutral = sum(
        1 for r in results
        if r.get("trend") == "NEUTRAL"
    )

    dynamic_count = sum(
        1 for r in results
        if r.get("dynamic_sr")
    )

    rvol_2 = sum(
        1 for r in results
        if r.get("rvol", 0) >= 2
    )

    rvol_3 = sum(
        1 for r in results
        if r.get("rvol", 0) >= 3
    )

    breakout = sum(
        1 for r in results
        if r.get("setup") == "BREAKOUT"
    )

    rejection = sum(
        1 for r in results
        if r.get("setup") == "REJECTION"
    )

    confirmation = sum(
        1 for r in results
        if r.get("confirmed_5m")
    )

    retest = sum(
        1 for r in results
        if r.get("retest")
    )

    static_sr = sum(
        1 for r in results
        if r.get("static_sr")
    )

    rr_valid = sum(
        1 for r in results
        if r.get("rr_pass")
    )

    qualified_count = len(
        qualified
    )

    # --------------------------------------------------------
    # UPDATE GLOBAL STATS
    # --------------------------------------------------------

    stats["scanned"] = scanned
    stats["data_ok"] = data_ok

    stats["trend_bull"] = trend_bull
    stats["trend_bear"] = trend_bear
    stats["trend_neutral"] = trend_neutral

    stats["dynamic_sr"] = dynamic_count
    stats["rvol_2"] = rvol_2
    stats["rvol_3"] = rvol_3

    stats["breakout"] = breakout
    stats["rejection"] = rejection

    stats["five_min_confirmation"] = confirmation
    stats["retest"] = retest

    stats["static_sr"] = static_sr
    stats["rr_valid"] = rr_valid

    stats["qualified"] = qualified_count

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    now = datetime.now(
        timezone.utc
    )

    timestamp = now.strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    lines = []

    lines.append(
        "🤖 VOLUME-KHAT 100"
    )

    lines.append(
        f"📡 {VERSION}"
    )

    lines.append(
        f"🕐 {timestamp}"
    )

    lines.append(
        f"⏱ TOP {TOP_N} | 5M CLOSED"
    )

    lines.append(
        "Real trading: DISABLED"
    )

    lines.append("")

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 SCAN PIPELINE"
    )

    lines.append(
        f"Scanned: {scanned}"
    )

    lines.append(
        f"Data OK: {data_ok}"
    )

    lines.append(
        ""
        f"1H Trend:"
    )

    lines.append(
        f"🟢 Bull: {trend_bull}"
    )

    lines.append(
        f"🔴 Bear: {trend_bear}"
    )

    lines.append(
        f"⚪ Neutral: {trend_neutral}"
    )

    lines.append("")

    lines.append(
        f"Dynamic S/R: {dynamic_count}"
    )

    lines.append(
        f"RVOL ≥ 2: {rvol_2}"
    )

    lines.append(
        f"RVOL ≥ 3: {rvol_3}"
    )

    lines.append("")

    lines.append(
        f"Breakout: {breakout}"
    )

    lines.append(
        f"Rejection: {rejection}"
    )

    lines.append("")

    lines.append(
        f"5M Confirmation: {confirmation}"
    )

    lines.append(
        f"Retest: {retest}"
    )

    lines.append(
        f"Static S/R: {static_sr}"
    )

    lines.append(
        f"RR ≥ 1:2: {rr_valid}"
    )

    lines.append("")

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🎯 Qualified: {qualified_count}"
    )

    lines.append(
        f"⏱ Scan time: {duration:.1f}s"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # QUALIFIED SIGNALS
    # --------------------------------------------------------

    if qualified:

        qualified.sort(
            key=lambda x: (
                x.get("score", 0),
                x.get("rr", 0),
                x.get("rvol", 0)
            ),
            reverse=True
        )

        lines.append("")
        lines.append(
            "🔥 TOP SIGNALS"
        )

        lines.append("")

        for signal in qualified[
            :MAX_SIGNALS_TELEGRAM
        ]:

            lines.append(
                signal_text(signal)
            )

            lines.append(
                "──────────────"
            )

    else:

        lines.append("")

        lines.append(
            "❌ No valid setup"
        )

    return "\n".join(lines)


# ============================================================
# SAVE QUALIFIED SIGNALS
# ============================================================

def save_qualified_signals(
    results
):

    qualified = [
        r for r in results
        if r.get("qualified")
    ]

    qualified.sort(
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    for signal in qualified[
        :MAX_SIGNALS_TELEGRAM
    ]:

        save_signal(signal)


# ============================================================
# MAIN SCAN
# ============================================================

def scan():

    start_time = time.time()

    log("")
    log(
        "============================================================"
    )
    log(
        f"🤖 {VERSION}"
    )
    log(
        "============================================================"
    )

    log(
        "Scanner started."
    )

    log(
        "Real trading: DISABLED"
    )

    log(
        f"Exchange: Kraken Futures"
    )

    log(
        f"Markets: TOP {TOP_N}"
    )

    log(
        "1H: Trend + Static S/R"
    )

    log(
        "15M: Dynamic S/R + RVOL + Setup"
    )

    log(
        "5M: Entry Confirmation"
    )

    log(
        "Minimum RVOL: 2.0"
    )

    log(
        "Minimum RR: 1:2"
    )

    log("")

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    markets = get_top_100()

    if not markets:

        log(
            "ERROR: Could not obtain TOP 100 markets."
        )

        telegram_send(
            "❌ VOLUME-KHAT 100\n"
            "Kraken market data unavailable."
        )

        return 1

    log(
        f"TOP {len(markets)} markets loaded."
    )

    # --------------------------------------------------------
    # ANALYSIS
    # --------------------------------------------------------

    results = []

    completed = 0

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                analyze_market,
                market
            ): market
            for market in markets
        }

        for future in as_completed(
            futures
        ):

            try:

                result = future.result()

                results.append(
                    result
                )

            except Exception as exc:

                market = futures[future]

                log(
                    f"Worker error "
                    f"{market['symbol']}: "
                    f"{exc}"
                )

            completed += 1

            if (
                completed % 20 == 0
                or completed == len(markets)
            ):

                log(
                    f"Progress: "
                    f"{completed}/{len(markets)}"
                )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    duration = (
        time.time()
        - start_time
    )

    report = build_report(
        markets,
        results,
        duration
    )

    log("")
    log(report)

    # --------------------------------------------------------
    # SAVE SIGNALS
    # --------------------------------------------------------

    save_qualified_signals(
        results
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    telegram_send(
        report
    )

    log("")
    log(
        "============================================================"
    )

    log(
        "SCAN COMPLETE"
    )

    log(
        f"Scanned: {stats['scanned']}"
    )

    log(
        f"Qualified: {stats['qualified']}"
    )

    log(
        f"Duration: {duration:.1f}s"
    )

    log(
        "============================================================"
    )

    return 0


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        exit_code = scan()

        sys.exit(
            exit_code
        )

    except KeyboardInterrupt:

        log(
            "Scanner interrupted."
        )

        sys.exit(130)

    except Exception as exc:

        log("")
        log(
            "============================================================"
        )

        log(
            "FATAL ERROR"
        )

        log(
            str(exc)
        )

        log("")
        traceback.print_exc()

        log(
            "============================================================"
        )

        telegram_send(
            "🚨 VOLUME-KHAT 100\n"
            "Fatal scanner error:\n"
            f"{str(exc)[:800]}"
        )

        sys.exit(1)
