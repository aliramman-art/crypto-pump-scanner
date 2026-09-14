# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v6.0
# ============================================================
#
# 1H  = TREND
# 15M = SUPPORT / RESISTANCE SETUP
# 5M  = FRESH BREAKOUT + PULLBACK + CONFIRMATION
#
# CLOSED CANDLES ONLY
#
# v6.0
# ------------------------------------------------------------
# MAJOR FIXES
#
# - REAL TELEGRAM DELIVERY
# - TELEGRAM MESSAGE CHUNKING
# - NO MARKDOWN PARSE ERRORS
# - VALID SUPPORT / RESISTANCE TP ONLY
# - NEAREST S/R TARGET ONLY
# - NO ARTIFICIAL RR MANIPULATION
# - TP MUST BE STRICTLY GREATER THAN SL DISTANCE
# - FRESH 5M BREAKOUT
# - FRESH PULLBACK
# - LINKED 15M SETUP -> 5M TRIGGER
# - REAL DYNAMIC SCORE
# - STRUCTURAL SL
# - HISTORICAL 5M EXIT DETECTION
# - SAME-CANDLE SL/TP = AMBIGUOUS
# - TIME-PROFIT EXIT DISABLED BY DEFAULT
# - SQLITE SCHEMA MIGRATION
# - OPEN TRADE HISTORY PRESERVED
# - TOP 100 PF_*USD
# - PAPER TRADING ONLY
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

STRATEGY_VERSION = "v6.0"

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE_URL + "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE_URL + "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE_URL + "/api/charts/v1/trade/{symbol}/{resolution}"
)

DB_FILE = "volume_khat_100.db"

PAPER_TRADING = True

TOP_N = 100

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

# ------------------------------------------------------------
# Timeframes
# ------------------------------------------------------------

TF_5M = "5m"
TF_15M = "15m"
TF_1H = "1h"

RESOLUTION_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}

# ------------------------------------------------------------
# Signal
# ------------------------------------------------------------

ATR_PERIOD = 14

SR_LOOKBACK_15M = 80
SR_LOOKBACK_1H = 80

PIVOT_SWING = 3

# Maximum distance between pivot prices to cluster
SR_CLUSTER_PCT = 0.30

# TP is placed slightly before the S/R zone
TP_ZONE_BUFFER_PCT = 0.10

# ------------------------------------------------------------
# SL
# ------------------------------------------------------------

MIN_SL_PCT = 0.50
MAX_SL_PCT = 1.50

SL_ATR_MULTIPLIER = 0.20
SL_PRICE_BUFFER_PCT = 0.15

# ------------------------------------------------------------
# Breakout
# ------------------------------------------------------------

BREAKOUT_LOOKBACK = 5
BREAKOUT_MAX_AGE = 2

# Pullback
PULLBACK_MAX_AGE = 2
PULLBACK_ATR_DISTANCE = 0.50

# ------------------------------------------------------------
# RVOL
# ------------------------------------------------------------

RVOL_PERIOD = 20

RVOL_NORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00

# ------------------------------------------------------------
# Candle quality
# ------------------------------------------------------------

MIN_BODY_PCT = 0.20
MIN_BODY_ATR = 0.10

WICK_BODY_RATIO = 1.15

# ------------------------------------------------------------
# Score
# ------------------------------------------------------------

MIN_SCORE = 10

# Max:
#
# 1H trend       4
# 15M setup      4
# 5M trigger     5
# S/R quality    2
#
# Total = 15
# ------------------------------------------------------------

# ------------------------------------------------------------
# Cooldown
# ------------------------------------------------------------

COOLDOWN_CANDLES = 3

# ------------------------------------------------------------
# Time exit
# ------------------------------------------------------------

# Disabled for clean TP/SR performance evaluation.
TIME_EXIT_ENABLED = False

MAX_HOLD_MINUTES = 240

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

REQUEST_TIMEOUT = 20
RETRIES = 3
REQUEST_SLEEP = 0.08

# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_MAX_LENGTH = 3900


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/6.0"
})


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "scanned": 0,

    "trend_long": 0,
    "trend_short": 0,
    "trend_neutral": 0,

    "setup_long": 0,
    "setup_short": 0,
    "setup_failed": 0,

    "breakout_pass": 0,
    "breakout_failed": 0,

    "pullback_pass": 0,
    "pullback_failed": 0,

    "confirmation_pass": 0,
    "confirmation_failed": 0,

    "score_failed": 0,

    "sr_no_target": 0,
    "tp_le_sl": 0,

    "cooldown": 0,
    "already_open": 0,

    "errors": 0,
}


# ============================================================
# HELPERS
# ============================================================

def reset_diag():
    for k in DIAG:
        DIAG[k] = 0


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def normalize_timestamp(value):
    try:
        ts = float(value)

        if ts > 10_000_000_000:
            ts /= 1000.0

        return int(ts)
    except Exception:
        return 0


def display_symbol(symbol):
    symbol = str(symbol or "")

    if symbol.upper().startswith("PF_"):
        return symbol[3:]

    return symbol


def fmt_price(value):
    value = safe_float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.1:
        return f"{value:.6f}"

    if value >= 0.01:
        return f"{value:.7f}"

    return f"{value:.8f}"


def pct_distance(a, b):
    if a <= 0:
        return 0.0

    return abs(a - b) / a * 100.0


def round_price(price, tick_size=0.0):
    price = safe_float(price)

    if price <= 0:
        return 0.0

    tick_size = safe_float(tick_size)

    if tick_size > 0:
        return round(round(price / tick_size) * tick_size, 12)

    return round(price, 12)


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    last_error = None

    for attempt in range(1, RETRIES + 1):

        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            time.sleep(REQUEST_SLEEP)

            return data

        except Exception as e:

            last_error = e

            print(
                f"[WARN] GET failed "
                f"{attempt}/{RETRIES}: {e}"
            )

            if attempt < RETRIES:
                time.sleep(1.0 * attempt)

    raise RuntimeError(
        f"GET failed after {RETRIES} attempts: "
        f"{url} | {last_error}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def split_telegram_message(message):
    if len(message) <= TELEGRAM_MAX_LENGTH:
        return [message]

    chunks = []

    current = ""

    for line in message.splitlines(True):

        if len(current) + len(line) <= TELEGRAM_MAX_LENGTH:
            current += line
            continue

        if current:
            chunks.append(current.rstrip())
            current = ""

        while len(line) > TELEGRAM_MAX_LENGTH:

            chunks.append(
                line[:TELEGRAM_MAX_LENGTH]
            )

            line = line[TELEGRAM_MAX_LENGTH:]

        current = line

    if current:
        chunks.append(current.rstrip())

    return chunks


def send_telegram(message):
    token = os.getenv(
        "TELEGRAM_BOT_TOKEN",
        ""
    ).strip()

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID",
        ""
    ).strip()

    if not token:
        print("❌ Telegram: TELEGRAM_BOT_TOKEN missing")
        return False

    if not chat_id:
        print("❌ Telegram: TELEGRAM_CHAT_ID missing")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    chunks = split_telegram_message(message)

    try:

        for index, chunk in enumerate(chunks, 1):

            response = SESSION.post(
                url,
                data={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": "true",
                },
                timeout=REQUEST_TIMEOUT
            )

            if not response.ok:

                print(
                    f"❌ Telegram ERROR "
                    f"HTTP {response.status_code}: "
                    f"{response.text[:500]}"
                )

                return False

            print(
                f"📨 Telegram: "
                f"{index}/{len(chunks)} SENT"
            )

        print("✅ Telegram: SUCCESS")

        return True

    except Exception as e:

        print(
            f"❌ Telegram EXCEPTION: {e}"
        )

        return False


# ============================================================
# KRAKEN TICKERS
# ============================================================

def get_all_tickers():
    data = http_get(TICKERS_URL)

    if isinstance(data, dict):

        rows = (
            data.get("tickers")
            or data.get("data")
            or data.get("results")
            or []
        )

    else:
        rows = data

    result = {}

    if isinstance(rows, dict):

        for symbol, ticker in rows.items():
            if isinstance(ticker, dict):
                result[symbol] = ticker

    elif isinstance(rows, list):

        for ticker in rows:

            if not isinstance(ticker, dict):
                continue

            symbol = (
                ticker.get("symbol")
                or ticker.get("instrument")
            )

            if symbol:
                result[symbol] = ticker

    return result


def ticker_price(ticker):
    if not ticker:
        return 0.0

    for key in (
        "last",
        "lastPrice",
        "markPrice",
        "price",
        "bid",
        "ask",
    ):

        value = safe_float(
            ticker.get(key)
        )

        if value > 0:
            return value

    return 0.0


def ticker_volume(ticker):
    if not ticker:
        return 0.0

    for key in (
        "volume24h",
        "vol24h",
        "volume",
    ):

        value = safe_float(
            ticker.get(key)
        )

        if value > 0:
            return value

    return 0.0


def ticker_spread_pct(ticker):
    if not ticker:
        return 0.0

    bid = safe_float(
        ticker.get("bid")
    )

    ask = safe_float(
        ticker.get("ask")
    )

    if bid <= 0 or ask <= 0:
        return 0.0

    if ask < bid:
        return 0.0

    mid = (ask + bid) / 2.0

    return (
        (ask - bid) / mid * 100.0
    )


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():
    data = http_get(INSTRUMENTS_URL)

    if isinstance(data, dict):

        return (
            data.get("instruments")
            or data.get("data")
            or data.get("results")
            or []
        )

    return data if isinstance(data, list) else []


def instrument_tick_size(instrument):
    for key in (
        "tickSize",
        "tick_size",
        "priceIncrement",
        "price_increment",
        "quoteIncrement",
    ):

        value = safe_float(
            instrument.get(key)
        )

        if value > 0:
            return value

    return 0.0


def build_market_list(tickers=None):
    if tickers is None:
        tickers = get_all_tickers()

    instruments = get_instruments()

    markets = []

    for instrument in instruments:

        if not isinstance(instrument, dict):
            continue

        symbol = (
            instrument.get("symbol")
            or instrument.get("instrument")
        )

        if not symbol:
            continue

        symbol = str(symbol)

        if not symbol.upper().startswith("PF_"):
            continue

        if not symbol.upper().endswith("USD"):
            continue

        ticker = tickers.get(symbol)

        if not ticker:
            continue

        price = ticker_price(ticker)

        volume = ticker_volume(ticker)

        spread = ticker_spread_pct(ticker)

        if price <= 0:
            continue

        if spread > 0.80:
            continue

        markets.append({
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "spread_pct": spread,
            "tick_size": instrument_tick_size(
                instrument
            ),
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    markets = markets[:TOP_N]

    print(
        f"[INFO] TOP {len(markets)} markets loaded."
    )

    return markets


# ============================================================
# CANDLES
# ============================================================

def parse_candle(raw):

    if isinstance(raw, dict):

        return {
            "time": normalize_timestamp(
                raw.get("time")
                or raw.get("timestamp")
                or raw.get("t")
            ),
            "open": safe_float(
                raw.get("open") or raw.get("o")
            ),
            "high": safe_float(
                raw.get("high") or raw.get("h")
            ),
            "low": safe_float(
                raw.get("low") or raw.get("l")
            ),
            "close": safe_float(
                raw.get("close") or raw.get("c")
            ),
            "volume": safe_float(
                raw.get("volume") or raw.get("v")
            ),
        }

    if isinstance(raw, (list, tuple)) and len(raw) >= 5:

        return {
            "time": normalize_timestamp(raw[0]),
            "open": safe_float(raw[1]),
            "high": safe_float(raw[2]),
            "low": safe_float(raw[3]),
            "close": safe_float(raw[4]),
            "volume": (
                safe_float(raw[5])
                if len(raw) > 5
                else 0.0
            ),
        }

    return None


def get_candles(symbol, resolution, limit=250):

    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(
            f"Unsupported resolution: {resolution}"
        )

    data = http_get(
        CHART_URL.format(
            symbol=symbol,
            resolution=resolution
        ),
        {"count": limit}
    )

    if isinstance(data, dict):

        raw = (
            data.get("candles")
            or data.get("data")
            or data.get("results")
            or []
        )

    else:
        raw = data

    candles = []

    if isinstance(raw, list):

        for item in raw:

            candle = parse_candle(item)

            if candle is None:
                continue

            if candle["time"] <= 0:
                continue

            if candle["high"] <= 0:
                continue

            candles.append(candle)

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    if len(candles) >= 2:

        current_time = int(
            time.time()
        )

        candle_seconds = (
            RESOLUTION_SECONDS[resolution]
        )

        last = candles[-1]

        # If latest candle has not fully closed,
        # remove it.
        if (
            last["time"]
            + candle_seconds
            > current_time
        ):
            candles = candles[:-1]

    return candles


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_range(c):
    return max(
        safe_float(c["high"])
        - safe_float(c["low"]),
        1e-12
    )


def candle_body(c):
    return abs(
        safe_float(c["close"])
        - safe_float(c["open"])
    )


def upper_wick(c):
    return (
        safe_float(c["high"])
        - max(
            safe_float(c["open"]),
            safe_float(c["close"])
        )
    )


def lower_wick(c):
    return (
        min(
            safe_float(c["open"]),
            safe_float(c["close"])
        )
        - safe_float(c["low"])
    )


def is_bullish(c):
    return c["close"] > c["open"]


def is_bearish(c):
    return c["close"] < c["open"]


# ============================================================
# ATR / RVOL
# ============================================================

def atr(candles, period=ATR_PERIOD):

    if len(candles) < period + 1:
        return 0.0

    trs = []

    for i in range(1, len(candles)):

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
            ),
        )

        trs.append(tr)

    if len(trs) < period:
        return 0.0

    return float(
        np.mean(trs[-period:])
    )


def rvol(candles, period=RVOL_PERIOD):

    if len(candles) < period + 1:
        return 0.0

    volumes = [
        safe_float(c["volume"])
        for c in candles
    ]

    current = volumes[-1]

    previous = volumes[
        -period - 1:-1
    ]

    average = np.mean(previous)

    if average <= 0:
        return 0.0

    return current / average


# ============================================================
# SMA
# ============================================================

def sma(candles, period):

    if len(candles) < period:
        return 0.0

    values = [
        safe_float(c["close"])
        for c in candles[-period:]
    ]

    return float(
        np.mean(values)
    )


# ============================================================
# PIVOTS / S-R ZONES
# ============================================================

def find_pivots(candles, swing=PIVOT_SWING):

    supports = []
    resistances = []

    if len(candles) < swing * 2 + 1:
        return supports, resistances

    for i in range(
        swing,
        len(candles) - swing
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]

        left = candles[
            i - swing:i
        ]

        right = candles[
            i + 1:i + swing + 1
        ]

        if (
            high >= max(
                x["high"]
                for x in left
            )
            and
            high >= max(
                x["high"]
                for x in right
            )
        ):
            resistances.append(high)

        if (
            low <= min(
                x["low"]
                for x in left
            )
            and
            low <= min(
                x["low"]
                for x in right
            )
        ):
            supports.append(low)

    return supports, resistances


def cluster_levels(levels):

    if not levels:
        return []

    levels = sorted(
        float(x)
        for x in levels
        if safe_float(x) > 0
    )

    zones = []

    current = [levels[0]]

    for level in levels[1:]:

        center = np.mean(current)

        distance_pct = (
            abs(level - center)
            / center
            * 100.0
        )

        if distance_pct <= SR_CLUSTER_PCT:

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

    low = min(levels)
    high = max(levels)

    center = (
        low + high
    ) / 2.0

    return {
        "low": low,
        "high": high,
        "center": center,
        "touches": len(levels),
    }


def build_sr_zones(candles):

    supports, resistances = find_pivots(
        candles,
        PIVOT_SWING
    )

    return (
        cluster_levels(supports),
        cluster_levels(resistances)
    )


def merge_sr_zones(zones_a, zones_b):

    all_supports = []

    all_resistances = []

    for zone in zones_a:
        all_supports.append(
            zone["center"]
        )

    for zone in zones_b:
        all_resistances.append(
            zone["center"]
        )

    return (
        cluster_levels(all_supports),
        cluster_levels(all_resistances)
    )


# ============================================================
# S/R REPORT
# ============================================================

def sr_text(zones, direction, entry):

    if direction == "SUPPORT":

        valid = [
            z for z in zones
            if z["high"] < entry
        ]

        valid.sort(
            key=lambda z: z["center"],
            reverse=True
        )

    else:

        valid = [
            z for z in zones
            if z["low"] > entry
        ]

        valid.sort(
            key=lambda z: z["center"]
        )

    if not valid:
        return "None"

    output = []

    for zone in valid[:5]:

        output.append(
            f"{fmt_price(zone['low'])}-"
            f"{fmt_price(zone['high'])}"
        )

    return ", ".join(output)


# ============================================================
# 1H TREND
# ============================================================

def detect_1h_trend(candles):

    if len(candles) < 60:
        return "NEUTRAL", 0

    last = candles[-1]

    close = last["close"]

    sma20 = sma(candles, 20)
    sma50 = sma(candles, 50)

    recent = candles[-7:]

    highs = [
        x["high"]
        for x in recent
    ]

    lows = [
        x["low"]
        for x in recent
    ]

    prev_highs = [
        x["high"]
        for x in candles[-13:-7]
    ]

    prev_lows = [
        x["low"]
        for x in candles[-13:-7]
    ]

    higher_high = (
        max(highs)
        > max(prev_highs)
    )

    higher_low = (
        min(lows)
        > min(prev_lows)
    )

    lower_high = (
        max(highs)
        < max(prev_highs)
    )

    lower_low = (
        min(lows)
        < min(prev_lows)
    )

    long_score = 0
    short_score = 0

    if close > sma20:
        long_score += 1

    if sma20 > sma50:
        long_score += 1

    if higher_high:
        long_score += 1

    if higher_low:
        long_score += 1

    if close < sma20:
        short_score += 1

    if sma20 < sma50:
        short_score += 1

    if lower_high:
        short_score += 1

    if lower_low:
        short_score += 1

    if long_score >= 3:
        return "LONG", long_score

    if short_score >= 3:
        return "SHORT", short_score

    return "NEUTRAL", max(
        long_score,
        short_score
    )


# ============================================================
# 15M SETUP
# ============================================================

def zone_near_price(zone, price, tolerance_pct):

    if zone["low"] <= price <= zone["high"]:
        return True

    distance = min(
        abs(price - zone["low"]),
        abs(price - zone["high"])
    )

    return (
        distance / price * 100.0
        <= tolerance_pct
    )


def detect_15m_setup(
    side,
    candles,
    support_zones,
    resistance_zones
):

    if len(candles) < 20:
        return {
            "valid": False,
            "score": 0,
            "reason": "insufficient_data",
            "zone": None,
            "type": None,
        }

    current = candles[-1]
    previous = candles[-2]

    close = current["close"]

    atr_value = atr(candles)

    if atr_value <= 0:
        return {
            "valid": False,
            "score": 0,
            "reason": "invalid_atr",
            "zone": None,
            "type": None,
        }

    tolerance_pct = min(
        0.60,
        max(
            0.20,
            atr_value / close * 100.0
        )
    )

    if side == "LONG":

        # ----------------------------------------------------
        # LONG: support reaction
        # ----------------------------------------------------

        support_candidates = [
            z for z in support_zones
            if z["center"] <= close
        ]

        support_candidates.sort(
            key=lambda z:
            abs(close - z["center"])
        )

        for zone in support_candidates[:5]:

            near = zone_near_price(
                zone,
                current["low"],
                tolerance_pct
            )

            rejection = (
                current["close"]
                > current["open"]
                and
                lower_wick(current)
                >= candle_body(current)
                * 0.80
            )

            hold = (
                current["close"]
                >= zone["low"]
            )

            break_hold = (
                previous["close"]
                <= zone["high"]
                and
                current["close"]
                > zone["high"]
            )

            if near and hold and (
                rejection or break_hold
            ):

                score = 0

                score += 2

                if rejection:
                    score += 1

                if break_hold:
                    score += 1

                return {
                    "valid": True,
                    "score": score,
                    "reason": (
                        "SUPPORT_REACTION"
                        if rejection
                        else "SUPPORT_BREAK_HOLD"
                    ),
                    "zone": zone,
                    "type": (
                        "SUPPORT_REACTION"
                        if rejection
                        else "SUPPORT_BREAK_HOLD"
                    ),
                }

    else:

        # ----------------------------------------------------
        # SHORT: resistance rejection
        # ----------------------------------------------------

        resistance_candidates = [
            z for z in resistance_zones
            if z["center"] >= close
        ]

        resistance_candidates.sort(
            key=lambda z:
            abs(close - z["center"])
        )

        for zone in resistance_candidates[:5]:

            near = zone_near_price(
                zone,
                current["high"],
                tolerance_pct
            )

            rejection = (
                current["close"]
                < current["open"]
                and
                upper_wick(current)
                >= candle_body(current)
                * 0.80
            )

            hold = (
                current["close"]
                <= zone["high"]
            )

            break_hold = (
                previous["close"]
                >= zone["low"]
                and
                current["close"]
                < zone["low"]
            )

            if near and hold and (
                rejection or break_hold
            ):

                score = 0

                score += 2

                if rejection:
                    score += 1

                if break_hold:
                    score += 1

                return {
                    "valid": True,
                    "score": score,
                    "reason": (
                        "RESISTANCE_REJECTION"
                        if rejection
                        else "RESISTANCE_BREAK_HOLD"
                    ),
                    "zone": zone,
                    "type": (
                        "RESISTANCE_REJECTION"
                        if rejection
                        else "RESISTANCE_BREAK_HOLD"
                    ),
                }

    return {
        "valid": False,
        "score": 0,
        "reason": "no_valid_sr_setup",
        "zone": None,
        "type": None,
    }


# ============================================================
# 5M BREAKOUT
# ============================================================

def detect_5m_breakout(
    side,
    candles,
    setup
):

    result = {
        "valid": False,
        "index": None,
        "level": None,
        "age": None,
        "reason": "",
    }

    if not setup:
        result["reason"] = "no_15m_setup"
        return result

    if len(candles) < 20:
        result["reason"] = "insufficient_data"
        return result

    last_index = len(candles) - 1

    # Only recent closed candles.
    start = max(
        10,
        last_index - 10
    )

    for i in range(
        start,
        last_index
    ):

        candle = candles[i]

        previous = candles[
            max(0, i - BREAKOUT_LOOKBACK):i
        ]

        if len(previous) < BREAKOUT_LOOKBACK:
            continue

        previous_high = max(
            x["high"]
            for x in previous
        )

        previous_low = min(
            x["low"]
            for x in previous
        )

        if side == "LONG":

            breakout = (
                candle["close"]
                > previous_high
            )

        else:

            breakout = (
                candle["close"]
                < previous_low
            )

        if not breakout:
            continue

        age = last_index - i

        if age > BREAKOUT_MAX_AGE:
            continue

        result.update({
            "valid": True,
            "index": i,
            "level": (
                previous_high
                if side == "LONG"
                else previous_low
            ),
            "age": age,
            "reason": "FRESH_BREAKOUT",
        })

        return result

    result["reason"] = "no_fresh_breakout"

    return result


# ============================================================
# 5M PULLBACK
# ============================================================

def detect_5m_pullback(
    side,
    candles,
    breakout
):

    result = {
        "valid": False,
        "index": None,
        "reason": "",
    }

    if not breakout["valid"]:
        result["reason"] = "no_breakout"
        return result

    breakout_index = breakout["index"]

    last_index = len(candles) - 1

    pullback_start = breakout_index + 1

    pullback_end = last_index

    if pullback_start > pullback_end:
        result["reason"] = "no_pullback_window"
        return result

    # Pullback must be immediately after breakout.
    for i in range(
        pullback_start,
        pullback_end
    ):

        candle = candles[i]

        level = breakout["level"]

        if side == "LONG":

            touched = (
                candle["low"]
                <= level * 1.002
            )

            recovered = (
                candle["close"]
                >= level
            )

        else:

            touched = (
                candle["high"]
                >= level * 0.998
            )

            recovered = (
                candle["close"]
                <= level
            )

        if touched and recovered:

            age = (
                last_index - i
            )

            if age <= PULLBACK_MAX_AGE:

                result.update({
                    "valid": True,
                    "index": i,
                    "reason": "FRESH_PULLBACK",
                })

                return result

    result["reason"] = "no_fresh_pullback"

    return result


# ============================================================
# 5M CONFIRMATION
# ============================================================

def detect_5m_confirmation(
    side,
    candles,
    pullback
):

    result = {
        "valid": False,
        "score": 0,
        "reason": "",
        "patterns": [],
    }

    if not pullback["valid"]:
        result["reason"] = "no_pullback"
        return result

    trigger = candles[-1]

    previous = candles[-2]

    body = candle_body(trigger)

    rng = candle_range(trigger)

    if rng <= 0:
        result["reason"] = "invalid_range"
        return result

    score = 0

    patterns = []

    if side == "LONG":

        if not is_bullish(trigger):
            result["reason"] = "not_bullish"
            return result

        score += 1

        if trigger["close"] > previous["high"]:

            score += 2
            patterns.append(
                "BREAK_PREVIOUS_HIGH"
            )

        if (
            lower_wick(trigger)
            >= body * WICK_BODY_RATIO
        ):

            score += 1
            patterns.append(
                "LOWER_WICK_REJECTION"
            )

        if body / rng >= 0.30:

            score += 1
            patterns.append(
                "STRONG_BODY"
            )

    else:

        if not is_bearish(trigger):
            result["reason"] = "not_bearish"
            return result

        score += 1

        if trigger["close"] < previous["low"]:

            score += 2
            patterns.append(
                "BREAK_PREVIOUS_LOW"
            )

        if (
            upper_wick(trigger)
            >= body * WICK_BODY_RATIO
        ):

            score += 1
            patterns.append(
                "UPPER_WICK_REJECTION"
            )

        if body / rng >= 0.30:

            score += 1
            patterns.append(
                "STRONG_BODY"
            )

    if body <= 0:
        result["reason"] = "zero_body"
        return result

    if (
        body / trigger["close"] * 100.0
        < MIN_BODY_PCT
    ):

        result["reason"] = "body_too_small"
        return result

    if score < 3:

        result["reason"] = (
            f"confirmation_score_{score}"
        )

        result["score"] = score

        return result

    result.update({
        "valid": True,
        "score": score,
        "reason": "CONFIRMED",
        "patterns": patterns,
    })

    return result


# ============================================================
# SUPPORT / RESISTANCE TARGET
# ============================================================

def select_tp_from_sr(
    side,
    entry,
    sl,
    support_zones,
    resistance_zones
):

    risk = abs(entry - sl)

    if risk <= 0:
        return None

    if side == "LONG":

        candidates = [
            z for z in resistance_zones
            if z["low"] > entry
        ]

        candidates.sort(
            key=lambda z: z["low"]
        )

        if not candidates:
            return None

        # IMPORTANT:
        # Only the NEAREST valid resistance is allowed.
        zone = candidates[0]

        tp = (
            zone["low"]
            * (
                1.0
                - TP_ZONE_BUFFER_PCT / 100.0
            )
        )

        reward = tp - entry

    else:

        candidates = [
            z for z in support_zones
            if z["high"] < entry
        ]

        candidates.sort(
            key=lambda z: z["high"],
            reverse=True
        )

        if not candidates:
            return None

        # IMPORTANT:
        # Only the NEAREST valid support is allowed.
        zone = candidates[0]

        tp = (
            zone["high"]
            * (
                1.0
                + TP_ZONE_BUFFER_PCT / 100.0
            )
        )

        reward = entry - tp

    # --------------------------------------------------------
    # NO RR MANIPULATION
    # --------------------------------------------------------

    if reward <= risk:
        return {
            "valid": False,
            "reason": "NEAREST_SR_REWARD_LE_RISK",
            "zone": zone,
            "tp": tp,
            "risk": risk,
            "reward": reward,
            "rr": (
                reward / risk
                if risk > 0
                else 0
            ),
        }

    rr = reward / risk

    if rr <= 1.0:
        return {
            "valid": False,
            "reason": "RR_LE_1",
            "zone": zone,
            "tp": tp,
            "risk": risk,
            "reward": reward,
            "rr": rr,
        }

    return {
        "valid": True,
        "reason": "VALID_SR_TARGET",
        "zone": zone,
        "tp": tp,
        "risk": risk,
        "reward": reward,
        "rr": rr,
    }


# ============================================================
# STRUCTURAL SL
# ============================================================

def calculate_sl(
    side,
    entry,
    candles_5m,
    setup_zone,
    tick_size=0.0
):

    atr_value = atr(candles_5m)

    if atr_value <= 0:
        raise ValueError(
            "Invalid ATR for SL"
        )

    if side == "LONG":

        recent_low = min(
            x["low"]
            for x in candles_5m[-10:]
        )

        structural = (
            setup_zone["low"]
            if setup_zone
            else recent_low
        )

        sl = min(
            structural
            - atr_value
            * SL_ATR_MULTIPLIER,

            entry
            * (
                1.0
                - SL_PRICE_BUFFER_PCT / 100.0
            )
        )

        risk_pct = (
            (entry - sl)
            / entry
            * 100.0
        )

        if risk_pct < MIN_SL_PCT:

            sl = entry * (
                1.0
                - MIN_SL_PCT / 100.0
            )

        elif risk_pct > MAX_SL_PCT:

            sl = entry * (
                1.0
                - MAX_SL_PCT / 100.0
            )

    else:

        recent_high = max(
            x["high"]
            for x in candles_5m[-10:]
        )

        structural = (
            setup_zone["high"]
            if setup_zone
            else recent_high
        )

        sl = max(
            structural
            + atr_value
            * SL_ATR_MULTIPLIER,

            entry
            * (
                1.0
                + SL_PRICE_BUFFER_PCT / 100.0
            )
        )

        risk_pct = (
            (sl - entry)
            / entry
            * 100.0
        )

        if risk_pct < MIN_SL_PCT:

            sl = entry * (
                1.0
                + MIN_SL_PCT / 100.0
            )

        elif risk_pct > MAX_SL_PCT:

            sl = entry * (
                1.0
                + MAX_SL_PCT / 100.0
            )

    sl = round_price(
        sl,
        tick_size
    )

    risk = abs(
        entry - sl
    )

    risk_pct = (
        risk / entry * 100.0
    )

    if (
        risk_pct < MIN_SL_PCT
        or risk_pct > MAX_SL_PCT
    ):
        raise ValueError(
            "SL outside allowed range"
        )

    return sl, risk, risk_pct


# ============================================================
# COOLDOWN
# ============================================================

def is_in_cooldown(
    conn,
    symbol,
    side,
    candle_time
):

    cutoff = (
        candle_time
        - COOLDOWN_CANDLES
        * RESOLUTION_SECONDS["5m"]
    )

    row = conn.execute(
        """
        SELECT exit_time
        FROM trades
        WHERE symbol = ?
          AND side = ?
          AND status = 'CLOSED'
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
        """,
        (
            symbol,
            side,
        )
    ).fetchone()

    if not row:
        return False

    last_exit = safe_float(
        row[0]
    )

    if last_exit <= 0:
        return False

    return last_exit >= cutoff


# ============================================================
# DATABASE
# ============================================================

def get_db():
    return sqlite3.connect(
        DB_FILE,
        timeout=30
    )


def init_db():

    conn = get_db()

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
            pnl REAL DEFAULT 0,
            entry_time INTEGER,
            exit_time INTEGER,
            exit REAL,
            exit_reason TEXT,
            duration_minutes REAL,
            closed_reported INTEGER DEFAULT 0,
            created_at INTEGER
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS system_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )

    # --------------------------------------------------------
    # Migration for old DBs
    # --------------------------------------------------------

    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()
    }

    required = {
        "setup": "TEXT",
        "entry_time": "INTEGER",
        "exit_time": "INTEGER",
        "exit": "REAL",
        "exit_reason": "TEXT",
        "duration_minutes": "REAL",
        "closed_reported": "INTEGER DEFAULT 0",
        "created_at": "INTEGER",
    }

    for column, definition in required.items():

        if column not in columns:

            print(
                f"[DB MIGRATE] Adding {column}"
            )

            conn.execute(
                f"ALTER TABLE trades "
                f"ADD COLUMN {column} "
                f"{definition}"
            )

    conn.commit()

    conn.close()


# ============================================================
# DB HELPERS
# ============================================================

def get_open_trades(conn):

    rows = conn.execute(
        """
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id
        """
    ).fetchall()

    columns = [
        x[1]
        for x in conn.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()
    ]

    result = []

    for row in rows:

        result.append(
            dict(zip(columns, row))
        )

    return result


def insert_trade(
    conn,
    signal
):

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
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'OPEN',
                NULL, 0, ?, ?)
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
            int(time.time()),
        )
    )


# ============================================================
# EXIT DETECTION
# ============================================================

def determine_exit_for_trade(
    trade,
    candles
):

    entry_time = safe_float(
        trade.get("entry_time")
    )

    sl = safe_float(
        trade.get("sl")
    )

    tp = safe_float(
        trade.get("tp")
    )

    side = trade.get("side")

    if (
        entry_time <= 0
        or sl <= 0
        or tp <= 0
    ):
        return None

    # Only candles after entry.
    relevant = [
        c for c in candles
        if c["time"] > entry_time
    ]

    if not relevant:
        return None

    for candle in relevant:

        high = candle["high"]
        low = candle["low"]

        if side == "LONG":

            sl_hit = low <= sl
            tp_hit = high >= tp

        else:

            sl_hit = high >= sl
            tp_hit = low <= tp

        if sl_hit and tp_hit:

            return {
                "price": candle["close"],
                "reason": "AMBIGUOUS",
                "time": candle["time"],
            }

        if sl_hit:

            return {
                "price": sl,
                "reason": "SL",
                "time": candle["time"],
            }

        if tp_hit:

            return {
                "price": tp,
                "reason": "TP",
                "time": candle["time"],
            }

    # --------------------------------------------------------
    # Optional timeout
    # --------------------------------------------------------

    if TIME_EXIT_ENABLED:

        latest_time = relevant[-1]["time"]

        duration = (
            latest_time
            - entry_time
        ) / 60.0

        if duration >= MAX_HOLD_MINUTES:

            return {
                "price": relevant[-1]["close"],
                "reason": "TIMEOUT",
                "time": latest_time,
            }

    return None


def calculate_pnl(
    side,
    entry,
    exit_price
):

    entry = safe_float(entry)
    exit_price = safe_float(exit_price)

    if entry <= 0 or exit_price <= 0:
        return 0.0

    if side == "LONG":

        return (
            exit_price - entry
        ) / entry * 100.0

    return (
        entry - exit_price
    ) / entry * 100.0


def process_open_trades(
    conn
):

    open_trades = get_open_trades(conn)

    if not open_trades:
        return 0

    closed_count = 0

    for trade in open_trades:

        symbol = trade["symbol"]

        try:

            candles = get_candles(
                symbol,
                TF_5M,
                1000
            )

            exit_data = determine_exit_for_trade(
                trade,
                candles
            )

            if not exit_data:
                continue

            exit_price = safe_float(
                exit_data["price"]
            )

            reason = exit_data["reason"]

            pnl = calculate_pnl(
                trade["side"],
                trade["entry"],
                exit_price
            )

            entry_time = safe_float(
                trade["entry_time"]
            )

            exit_time = safe_float(
                exit_data["time"]
            )

            duration = (
                exit_time
                - entry_time
            ) / 60.0

            result = (
                "WIN"
                if pnl > 0
                else "LOSS"
                if pnl < 0
                else "FLAT"
            )

            conn.execute(
                """
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = ?,
                    pnl = ?,
                    exit_time = ?,
                    exit = ?,
                    exit_reason = ?,
                    duration_minutes = ?
                WHERE id = ?
                """,
                (
                    result,
                    pnl,
                    int(exit_time),
                    exit_price,
                    reason,
                    duration,
                    trade["id"],
                )
            )

            closed_count += 1

            print(
                f"[CLOSED] "
                f"{display_symbol(symbol)} "
                f"{trade['side']} "
                f"{reason} "
                f"P/L={pnl:+.2f}%"
            )

        except Exception as e:

            DIAG["errors"] += 1

            print(
                f"[EXIT ERROR] "
                f"{symbol}: {e}"
            )

    conn.commit()

    return closed_count


# ============================================================
# PERFORMANCE
# ============================================================

def performance(conn):

    row = conn.execute(
        """
        SELECT
            COUNT(*),
            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1 ELSE 0
                END
            ),
            SUM(
                CASE
                    WHEN result = 'LOSS'
                    THEN 1 ELSE 0
                END
            ),
            COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status = 'CLOSED'
        """
    ).fetchone()

    total = int(
        row[0] or 0
    )

    wins = int(
        row[1] or 0
    )

    losses = int(
        row[2] or 0
    )

    pnl = safe_float(
        row[3]
    )

    decided = wins + losses

    wr = (
        wins / decided * 100.0
        if decided > 0
        else 0.0
    )

    return {
        "closed": total,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "pnl": pnl,
    }


# ============================================================
# LIVE PNL
# ============================================================

def live_pnl(
    trade,
    current
):

    entry = safe_float(
        trade["entry"]
    )

    if entry <= 0 or current <= 0:
        return 0.0

    if trade["side"] == "LONG":

        return (
            current - entry
        ) / entry * 100.0

    return (
        entry - current
    ) / entry * 100.0


# ============================================================
# SIGNAL ANALYSIS
# ============================================================

def analyze_market(
    market,
    conn,
    tickers
):

    symbol = market["symbol"]

    # --------------------------------------------------------
    # Current price
    # --------------------------------------------------------

    ticker = tickers.get(symbol)

    entry = ticker_price(ticker)

    if entry <= 0:
        return None

    # --------------------------------------------------------
    # Candles
    # --------------------------------------------------------

    c1h = get_candles(
        symbol,
        TF_1H,
        150
    )

    c15 = get_candles(
        symbol,
        TF_15M,
        150
    )

    c5 = get_candles(
        symbol,
        TF_5M,
        150
    )

    if (
        len(c1h) < 60
        or len(c15) < 30
        or len(c5) < 30
    ):
        return None

    # --------------------------------------------------------
    # S/R
    # --------------------------------------------------------

    sup15, res15 = build_sr_zones(
        c15
    )

    sup1h, res1h = build_sr_zones(
        c1h
    )

    support_zones = cluster_levels(
        [
            z["center"]
            for z in sup15 + sup1h
        ]
    )

    resistance_zones = cluster_levels(
        [
            z["center"]
            for z in res15 + res1h
        ]
    )

    # --------------------------------------------------------
    # 1H trend
    # --------------------------------------------------------

    trend, trend_score = detect_1h_trend(
        c1h
    )

    if trend == "LONG":
        DIAG["trend_long"] += 1

    elif trend == "SHORT":
        DIAG["trend_short"] += 1

    else:
        DIAG["trend_neutral"] += 1

    if trend == "NEUTRAL":
        return None

    side = trend

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    rv = rvol(c5)

    if rv < RVOL_NORMAL:
        return None

    # --------------------------------------------------------
    # 15M setup
    # --------------------------------------------------------

    setup = detect_15m_setup(
        side,
        c15,
        support_zones,
        resistance_zones
    )

    if not setup["valid"]:

        DIAG["setup_failed"] += 1

        return None

    if side == "LONG":
        DIAG["setup_long"] += 1
    else:
        DIAG["setup_short"] += 1

    # --------------------------------------------------------
    # 5M breakout
    # --------------------------------------------------------

    breakout = detect_5m_breakout(
        side,
        c5,
        setup
    )

    if not breakout["valid"]:

        DIAG["breakout_failed"] += 1

        return None

    DIAG["breakout_pass"] += 1

    # --------------------------------------------------------
    # Pullback
    # --------------------------------------------------------

    pullback = detect_5m_pullback(
        side,
        c5,
        breakout
    )

    if not pullback["valid"]:

        DIAG["pullback_failed"] += 1

        return None

    DIAG["pullback_pass"] += 1

    # --------------------------------------------------------
    # Confirmation
    # --------------------------------------------------------

    confirmation = detect_5m_confirmation(
        side,
        c5,
        pullback
    )

    if not confirmation["valid"]:

        DIAG["confirmation_failed"] += 1

        return None

    DIAG["confirmation_pass"] += 1

    # --------------------------------------------------------
    # Score
    # --------------------------------------------------------

    score = 0

    # 1H = max 4
    score += min(
        4,
        trend_score
    )

    # 15M = max 4
    score += min(
        4,
        setup["score"]
    )

    # 5M = max 5
    score += min(
        5,
        confirmation["score"]
    )

    # RVOL quality
    if rv >= RVOL_VERY_STRONG:
        score += 2
    elif rv >= RVOL_STRONG:
        score += 1

    if score < MIN_SCORE:

        DIAG["score_failed"] += 1

        return None

    # --------------------------------------------------------
    # Structural SL
    # --------------------------------------------------------

    sl, risk, risk_pct = calculate_sl(
        side,
        entry,
        c5,
        setup["zone"],
        market.get("tick_size", 0.0)
    )

    # --------------------------------------------------------
    # TP from NEAREST S/R
    # --------------------------------------------------------

    target = select_tp_from_sr(
        side,
        entry,
        sl,
        support_zones,
        resistance_zones
    )

    if not target:

        DIAG["sr_no_target"] += 1

        return None

    if not target["valid"]:

        if target["reason"] == (
            "NEAREST_SR_REWARD_LE_RISK"
        ):
            DIAG["tp_le_sl"] += 1

        else:
            DIAG["sr_no_target"] += 1

        return None

    tp = round_price(
        target["tp"],
        market.get("tick_size", 0.0)
    )

    reward = abs(
        tp - entry
    )

    rr = (
        reward / risk
        if risk > 0
        else 0
    )

    # Final safety check
    if reward <= risk or rr <= 1.0:

        DIAG["tp_le_sl"] += 1

        return None

    trigger_time = c5[-1]["time"]

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    if is_in_cooldown(
        conn,
        symbol,
        side,
        trigger_time
    ):

        DIAG["cooldown"] += 1

        return None

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    return {
        "symbol": symbol,
        "side": side,

        "setup": setup["type"],

        "entry": entry,
        "sl": sl,
        "tp": tp,

        "risk": risk,
        "reward": reward,
        "risk_pct": risk_pct,
        "reward_pct": (
            reward / entry * 100.0
        ),

        "rr": rr,

        "score": score,

        "rvol": rv,

        "trigger_time": trigger_time,

        "support_zones": support_zones,
        "resistance_zones": resistance_zones,

        "tp_zone": target["zone"],

        "tp_source": (
            "RESISTANCE"
            if side == "LONG"
            else "SUPPORT"
        ),

        "patterns": confirmation[
            "patterns"
        ],
    }


# ============================================================
# OPEN TRADE LIMIT
# ============================================================

def select_signals(
    conn,
    candidates
):

    open_trades = get_open_trades(
        conn
    )

    available = (
        MAX_OPEN_TRADES
        - len(open_trades)
    )

    if available <= 0:
        return []

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"],
        ),
        reverse=True
    )

    selected = []

    used_symbols = set()

    for signal in candidates:

        if len(selected) >= min(
            available,
            MAX_NEW_SIGNALS
        ):
            break

        if signal["symbol"] in used_symbols:
            continue

        selected.append(signal)

        used_symbols.add(
            signal["symbol"]
        )

    return selected


# ============================================================
# REPORT
# ============================================================

def zone_report(
    zones,
    side
):

    if not zones:
        return "None"

    values = []

    for zone in zones:

        values.append(
            f"{fmt_price(zone['low'])}-"
            f"{fmt_price(zone['high'])}"
        )

    return ", ".join(
        values[:5]
    )


def build_report(
    conn,
    signals,
    closed_count,
    tickers,
    markets_count
):

    open_trades = get_open_trades(
        conn
    )

    stats = performance(
        conn
    )

    lines = [

        "📊 VOLUME-KHAT 100",

        f"🕐 {now_utc().strftime('%Y/%m/%d %H:%M:%S')} UTC",

        f"⚡ Kraken Futures | 5M CLOSED | TOP {TOP_N}",

        f"🧠 1H TREND + 15M S/R + "
        f"5M BREAKOUT/PULLBACK",

        f"🛠 Strategy: {STRATEGY_VERSION}",

        "💡 TP = NEAREST VALID SUPPORT/RESISTANCE",

        "💡 TP distance MUST be > SL distance",

        "━━━━━━━━━━━━━━━━━━",

        "🎯 NEW SIGNALS",
    ]

    if not signals:

        lines.append("None")

    else:

        for signal in signals:

            icon = (
                "🟢"
                if signal["side"] == "LONG"
                else "🔴"
            )

            lines += [

                "",

                f"{icon} "
                f"{display_symbol(signal['symbol'])} "
                f"{signal['side']}",

                f"Entry: {fmt_price(signal['entry'])}",

                f"SL: {fmt_price(signal['sl'])} "
                f"({signal['risk_pct']:.2f}%)",

                f"TP: {fmt_price(signal['tp'])} "
                f"({signal['reward_pct']:.2f}%)",

                f"RR: {signal['rr']:.2f}",

                f"Score: {signal['score']}/15",

                f"RVOL: {signal['rvol']:.2f}",

                f"Setup: {signal['setup']}",

                f"TP Source: "
                f"{signal['tp_source']}",

                f"Support: "
                f"{zone_report(signal['support_zones'], 'SUPPORT')}",

                f"Resistance: "
                f"{zone_report(signal['resistance_zones'], 'RESISTANCE')}",

                (
                    "Pattern: "
                    + ", ".join(
                        signal["patterns"]
                    )
                    if signal["patterns"]
                    else "Pattern: None"
                ),
            ]

    lines += [
        "",
        "━━━━━━━━━━━━━━━━━━",

        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})",
    ]

    if not open_trades:

        lines.append("None")

    else:

        for trade in open_trades:

            ticker = tickers.get(
                trade["symbol"]
            )

            current = ticker_price(
                ticker
            )

            pnl = live_pnl(
                trade,
                current
            )

            icon = (
                "🟢"
                if trade["side"] == "LONG"
                else "🔴"
            )

            lines += [

                "",

                f"{icon} "
                f"{display_symbol(trade['symbol'])} "
                f"{trade['side']}",

                f"Entry: "
                f"{fmt_price(trade['entry'])}",

                f"Current: "
                f"{fmt_price(current)}",

                f"P&L: "
                f"{pnl:+.2f}%",

                f"SL: "
                f"{fmt_price(trade['sl'])}",

                f"TP: "
                f"{fmt_price(trade['tp'])}",

                f"RR: "
                f"{safe_float(trade['rr']):.2f}",

                f"Setup: "
                f"{trade.get('setup') or 'N/A'}",
            ]

    lines += [

        "",
        "━━━━━━━━━━━━━━━━━━",

        "📈 PERFORMANCE",

        f"Open: {len(open_trades)}",

        f"Closed: {stats['closed']}",

        f"Wins: {stats['wins']}",

        f"Losses: {stats['losses']}",

        f"Win Rate: {stats['wr']:.1f}%",

        f"Net PnL: {stats['pnl']:+.2f}%",

        f"Closed This Run: {closed_count}",

        "",
        "━━━━━━━━━━━━━━━━━━",

        "🔎 FILTER DIAGNOSTICS",

        f"Scanned: {DIAG['scanned']}",

        f"1H LONG: {DIAG['trend_long']}",

        f"1H SHORT: {DIAG['trend_short']}",

        f"1H Neutral: {DIAG['trend_neutral']}",

        f"15M LONG Setup: {DIAG['setup_long']}",

        f"15M SHORT Setup: {DIAG['setup_short']}",

        f"15M Failed: {DIAG['setup_failed']}",

        f"5M Breakout Pass: {DIAG['breakout_pass']}",

        f"5M Breakout Failed: {DIAG['breakout_failed']}",

        f"5M Pullback Pass: {DIAG['pullback_pass']}",

        f"5M Pullback Failed: {DIAG['pullback_failed']}",

        f"5M Confirmation Pass: {DIAG['confirmation_pass']}",

        f"5M Confirmation Failed: {DIAG['confirmation_failed']}",

        f"Score Failed: {DIAG['score_failed']}",

        f"No Valid S/R Target: {DIAG['sr_no_target']}",

        f"Nearest S/R RR <= 1: {DIAG['tp_le_sl']}",

        f"Cooldown: {DIAG['cooldown']}",

        f"Already Open: {DIAG['already_open']}",

        f"Errors: {DIAG['errors']}",

        "",
        "━━━━━━━━━━━━━━━━━━",

        "⚙️ SETTINGS",

        f"SL: {MIN_SL_PCT:.2f}% - "
        f"{MAX_SL_PCT:.2f}%",

        "TP: NEAREST VALID S/R",

        "RR: > 1.00 ONLY",

        "Exit: HISTORICAL 5M HIGH/LOW",

        (
            "Time Exit: ENABLED"
            if TIME_EXIT_ENABLED
            else "Time Exit: DISABLED"
        ),

        "Real trading: DISABLED",

    ]

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def scan():

    reset_diag()

    print(
        "============================================================"
    )

    print(
        f"KRAKEN FUTURES VOLUME-KHAT 100 "
        f"{STRATEGY_VERSION}"
    )

    print(
        "============================================================"
    )

    print(
        "1H TREND -> 15M S/R -> "
        "5M BREAKOUT -> PULLBACK -> CONFIRMATION"
    )

    print(
        "CLOSED CANDLES ONLY"
    )

    print(
        "TP = NEAREST VALID S/R"
    )

    print(
        "RR MUST BE > 1.00"
    )

    print(
        "Real trading: DISABLED"
    )

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    init_db()

    conn = get_db()

    # --------------------------------------------------------
    # Exit existing trades first
    # --------------------------------------------------------

    print(
        "[STEP 1] Checking open trades..."
    )

    closed_count = process_open_trades(
        conn
    )

    # --------------------------------------------------------
    # Tickers
    # --------------------------------------------------------

    print(
        "[STEP 2] Loading Kraken tickers..."
    )

    tickers = get_all_tickers()

    if not tickers:

        raise RuntimeError(
            "No Kraken tickers available"
        )

    # --------------------------------------------------------
    # Markets
    # --------------------------------------------------------

    print(
        "[STEP 3] Building TOP 100..."
    )

    markets = build_market_list(
        tickers
    )

    if not markets:

        report = build_report(
            conn,
            [],
            closed_count,
            tickers,
            0
        )

        print(report)

        send_telegram(report)

        conn.close()

        return

    # --------------------------------------------------------
    # Open symbols
    # --------------------------------------------------------

    open_trades = get_open_trades(
        conn
    )

    open_symbols = {
        x["symbol"]
        for x in open_trades
    }

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    print(
        "[STEP 4] Scanning markets..."
    )

    candidates = []

    for index, market in enumerate(
        markets,
        1
    ):

        symbol = market["symbol"]

        DIAG["scanned"] += 1

        print(
            f"[{index:03d}/{len(markets):03d}] "
            f"{symbol}"
        )

        if symbol in open_symbols:

            DIAG["already_open"] += 1

            print(
                "    ⏭ OPEN"
            )

            continue

        try:

            signal = analyze_market(
                market,
                conn,
                tickers
            )

            if signal:

                candidates.append(
                    signal
                )

                print(
                    f"    ✅ "
                    f"{signal['side']} "
                    f"Score={signal['score']} "
                    f"Entry={fmt_price(signal['entry'])} "
                    f"SL={fmt_price(signal['sl'])} "
                    f"TP={fmt_price(signal['tp'])} "
                    f"RR={signal['rr']:.2f}"
                )

            else:

                print(
                    "    — NO SIGNAL"
                )

        except Exception as e:

            DIAG["errors"] += 1

            print(
                f"    ❌ ERROR: {e}"
            )

    # --------------------------------------------------------
    # Selection
    # --------------------------------------------------------

    print(
        "[STEP 5] Selecting trades..."
    )

    selected = select_signals(
        conn,
        candidates
    )

    # --------------------------------------------------------
    # Insert trades
    # --------------------------------------------------------

    for signal in selected:

        insert_trade(
            conn,
            {
                **signal,
                "entry_time": signal[
                    "trigger_time"
                ],
            }
        )

        print(
            f"[OPEN] "
            f"{display_symbol(signal['symbol'])} "
            f"{signal['side']} "
            f"Entry={fmt_price(signal['entry'])} "
            f"SL={fmt_price(signal['sl'])} "
            f"TP={fmt_price(signal['tp'])} "
            f"RR={signal['rr']:.2f}"
        )

    conn.commit()

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    print(
        "[STEP 6] Building report..."
    )

    # Refresh tickers for live open PnL
    try:
        tickers = get_all_tickers()
    except Exception:
        pass

    report = build_report(
        conn,
        selected,
        closed_count,
        tickers,
        len(markets)
    )

    print(report)

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    print(
        "[STEP 7] Sending Telegram..."
    )

    telegram_ok = send_telegram(
        report
    )

    conn.close()

    if not telegram_ok:

        raise RuntimeError(
            "Telegram report delivery failed"
        )

    print(
        "============================================================"
    )

    print(
        "[DONE]"
    )

    print(
        "============================================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        scan()

    except KeyboardInterrupt:

        print(
            "\n[STOP] Interrupted."
        )

    except Exception as e:

        print(
            f"\n[FATAL] {e}"
        )

        traceback.print_exc()

        # Try to notify Telegram even on fatal error.
        try:

            send_telegram(
                "🚨 VOLUME-KHAT 100\n"
                f"Scanner FAILED\n"
                f"Version: {STRATEGY_VERSION}\n"
                f"Error: {str(e)[:1000]}"
            )

        except Exception:
            pass

        raise
