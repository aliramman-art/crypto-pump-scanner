# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v11.4
# ============================================================
# Kraken Futures
# TOP 100 USD perpetual markets
#
# MTF:
#   1H  = Trend
#   30M = Confirmation
#   15M = Pullback
#   5M  = Trigger / Entry
#
# CLOSED CANDLES ONLY
#
# ENTRY:
#   After CLOSED 5m candle
#
# ICHIMOKU:
#   Tenkan = 9
#   Kijun  = 26
#   Senkou B = 52
#   Displacement = 26
#
# RISK:
#   Minimum risk = 0.50%
#   RR = 1:1
#
# LIMITS:
#   Max open = 4
#   Max long = 2
#   Max short = 2
#   One trade per symbol
#
# MONITOR:
#   5m candle High/Low
#   TP / SL
#   4h timeout
#
# STATE:
#   ichimoku_state.json
#   ichimoku_trade_history.json
#
# TELEGRAM:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
# ============================================================

import os
import json
import time
import math
import traceback
import requests

from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE_URL + "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE_URL + "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE_URL
    + "/api/charts/v1/trade/{symbol}/{resolution}"
)

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"


# ============================================================
# STRATEGY SETTINGS
# ============================================================

TOP_N = 100

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

MIN_RISK_PCT = 0.50

RR = 1.0

TIMEOUT_HOURS = 4

REQUEST_TIMEOUT = 20
RETRIES = 3

# IMPORTANT:
# Kraken Futures requires strings like 5m / 15m / 30m / 1h
TF_5M = "5m"
TF_15M = "15m"
TF_30M = "30m"
TF_1H = "1h"

# Candle duration
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


# ============================================================
# ICHIMOKU
# ============================================================

TENKAN = 9
KIJUN = 26
SENKOU_B = 52
DISPLACEMENT = 26


# ============================================================
# MTF WEIGHTS
# ============================================================

WEIGHT_1H = 0.50
WEIGHT_30M = 0.30
WEIGHT_15M = 0.20


# ============================================================
# ENTRY FILTERS
# ============================================================

MIN_SCORE = 70.0

PULLBACK_ATR_MULTIPLIER = 1.5


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Kraken-Ichi-Scanner/11.4"
    }
)


def http_get(url, params=None):

    last_error = None

    for attempt in range(1, RETRIES + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            last_error = e

            print(
                f"[WARN] GET failed "
                f"{attempt}/{RETRIES}: "
                f"{url} | {e}"
            )

            if attempt < RETRIES:
                time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"Request failed after {RETRIES} attempts: "
        f"{url} | {last_error}"
    )


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():

    return datetime.now(timezone.utc)


def now_iso():

    return now_utc().isoformat()


def safe_float(value, default=0.0):

    try:
        return float(value)

    except Exception:
        return default


def clamp(value, low, high):

    return max(low, min(high, value))


def pct_change(a, b):

    if not a:
        return 0.0

    return ((b - a) / a) * 100.0


def direction_text(side):

    return "LONG" if side == "LONG" else "SHORT"


# ============================================================
# STATE
# ============================================================

def load_json_file(path, default):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[WARN] Cannot load {path}: {e}"
        )

        return default


def save_json_file(path, data):

    tmp = path + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(tmp, path)


def load_state():

    state = load_json_file(
        STATE_FILE,
        {},
    )

    if not isinstance(state, dict):
        state = {}

    if "open_trades" not in state:
        state["open_trades"] = []

    return state


def save_state(state):

    save_json_file(
        STATE_FILE,
        state,
    )


def load_history():

    history = load_json_file(
        HISTORY_FILE,
        [],
    )

    if not isinstance(history, list):
        history = []

    return history


def save_history(history):

    save_json_file(
        HISTORY_FILE,
        history,
    )


# ============================================================
# MARKET DATA
# ============================================================

def get_instruments():

    data = http_get(
        INSTRUMENTS_URL
    )

    if isinstance(data, dict):

        instruments = (
            data.get("instruments")
            or data.get("data")
            or []
        )

    elif isinstance(data, list):

        instruments = data

    else:

        instruments = []

    return instruments


def get_tickers():

    data = http_get(
        TICKERS_URL
    )

    if isinstance(data, dict):

        tickers = (
            data.get("tickers")
            or data.get("data")
            or []
        )

    elif isinstance(data, list):

        tickers = data

    else:

        tickers = []

    result = {}

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
        )

        if not symbol:
            continue

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
            ticker.get(key),
            0.0,
        )

        if value > 0:
            return value

    return 0.0


def ticker_volume(ticker):

    if not ticker:
        return 0.0

    for key in (
        "volume24h",
        "volume",
        "vol24h",
    ):

        value = safe_float(
            ticker.get(key),
            0.0,
        )

        if value > 0:
            return value

    return 0.0


def build_market_list():

    instruments = get_instruments()
    tickers = get_tickers()

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

        # We only want Futures perpetual contracts
        symbol_upper = symbol.upper()

        if not symbol_upper.startswith("PF_"):
            continue

        ticker = tickers.get(symbol)

        if not ticker:
            continue

        price = ticker_price(ticker)

        volume = ticker_volume(ticker)

        if price <= 0:
            continue

        markets.append(
            {
                "symbol": symbol,
                "price": price,
                "volume": volume,
            }
        )

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True,
    )

    markets = markets[:TOP_N]

    print(
        f"[INFO] TOP {len(markets)} markets loaded."
    )

    return markets


# ============================================================
# CANDLE DATA
# ============================================================

def normalize_timestamp(value):

    try:

        ts = float(value)

        # milliseconds
        if ts > 10_000_000_000:
            ts /= 1000.0

        return int(ts)

    except Exception:

        return 0


def parse_candle(raw):

    if isinstance(raw, dict):

        timestamp = (
            raw.get("time")
            or raw.get("timestamp")
            or raw.get("t")
        )

        open_price = (
            raw.get("open")
            or raw.get("o")
        )

        high = (
            raw.get("high")
            or raw.get("h")
        )

        low = (
            raw.get("low")
            or raw.get("l")
        )

        close = (
            raw.get("close")
            or raw.get("c")
        )

        volume = (
            raw.get("volume")
            or raw.get("v")
            or 0
        )

        return {
            "time": normalize_timestamp(
                timestamp
            ),
            "open": safe_float(
                open_price
            ),
            "high": safe_float(
                high
            ),
            "low": safe_float(
                low
            ),
            "close": safe_float(
                close
            ),
            "volume": safe_float(
                volume
            ),
        }

    if isinstance(raw, (list, tuple)):

        if len(raw) < 5:
            return None

        return {
            "time": normalize_timestamp(
                raw[0]
            ),
            "open": safe_float(
                raw[1]
            ),
            "high": safe_float(
                raw[2]
            ),
            "low": safe_float(
                raw[3]
            ),
            "close": safe_float(
                raw[4]
            ),
            "volume": safe_float(
                raw[5]
            ) if len(raw) > 5 else 0.0,
        }

    return None


def get_candles(
    symbol,
    resolution,
    limit=250,
):

    if resolution not in RESOLUTION_SECONDS:

        raise ValueError(
            f"Unsupported Kraken resolution: "
            f"{resolution}"
        )

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution,
    )

    data = http_get(
        url,
        params={
            "count": limit,
        },
    )

    if isinstance(data, dict):

        raw_candles = (
            data.get("candles")
            or data.get("data")
            or data.get("results")
            or []
        )

    elif isinstance(data, list):

        raw_candles = data

    else:

        raw_candles = []

    candles = []

    for raw in raw_candles:

        candle = parse_candle(raw)

        if candle is None:
            continue

        if candle["time"] <= 0:
            continue

        if candle["close"] <= 0:
            continue

        if candle["high"] <= 0:
            continue

        if candle["low"] <= 0:
            continue

        if candle["high"] < candle["low"]:
            continue

        candles.append(candle)

    # Remove duplicates
    unique = {}

    for candle in candles:

        unique[
            candle["time"]
        ] = candle

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["time"]
    )

    candle_seconds = RESOLUTION_SECONDS[
        resolution
    ]

    current_ts = int(
        time.time()
    )

    closed = []

    for candle in candles:

        if (
            candle["time"]
            + candle_seconds
            <= current_ts
        ):

            closed.append(candle)

    if len(closed) > limit:
        closed = closed[-limit:]

    return closed


# ============================================================
# INDICATORS
# ============================================================

def true_range(candles):

    if not candles:
        return []

    tr = []

    previous_close = None

    for candle in candles:

        high = candle["high"]
        low = candle["low"]

        if previous_close is None:

            value = high - low

        else:

            value = max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )

        tr.append(value)

        previous_close = candle["close"]

    return tr


def atr(candles, period=14):

    if len(candles) < period:
        return 0.0

    tr = true_range(candles)

    values = tr[-period:]

    if not values:
        return 0.0

    return sum(values) / len(values)


def rolling_mid(candles, period):

    if len(candles) < period:
        return None

    window = candles[-period:]

    highest = max(
        x["high"]
        for x in window
    )

    lowest = min(
        x["low"]
        for x in window
    )

    return (
        highest + lowest
    ) / 2.0


# ============================================================
# STANDARD DISPLACED ICHIMOKU
# ============================================================

def ichimoku(candles):

    required = (
        SENKOU_B
        + DISPLACEMENT
        + 5
    )

    if len(candles) < required:
        return None

    i = len(candles) - 1

    close = candles[i]["close"]

    # Current Tenkan
    tenkan = rolling_mid(
        candles[:i + 1],
        TENKAN,
    )

    # Current Kijun
    kijun = rolling_mid(
        candles[:i + 1],
        KIJUN,
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Current cloud uses values calculated DISPLACEMENT
    # candles ago.
    # --------------------------------------------------------

    cloud_index = (
        i - DISPLACEMENT
    )

    if cloud_index < SENKOU_B - 1:
        return None

    tenkan_at_cloud = rolling_mid(
        candles[:cloud_index + 1],
        TENKAN,
    )

    kijun_at_cloud = rolling_mid(
        candles[:cloud_index + 1],
        KIJUN,
    )

    span_a = (
        tenkan_at_cloud
        + kijun_at_cloud
    ) / 2.0

    span_b = rolling_mid(
        candles[:cloud_index + 1],
        SENKOU_B,
    )

    if any(
        x is None
        for x in (
            tenkan,
            kijun,
            span_a,
            span_b,
        )
    ):
        return None

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    return {
        "close": close,
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "cloud_bullish": span_a > span_b,
        "cloud_bearish": span_a < span_b,
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(candles):

    info = ichimoku(candles)

    if info is None:
        return 0.0, None

    close = info["close"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    cloud_top = info["cloud_top"]
    cloud_bottom = info["cloud_bottom"]

    score = 0.0

    # --------------------------------------------------------
    # PRICE VS CLOUD
    # --------------------------------------------------------

    if close > cloud_top:
        score += 4.0

    elif close < cloud_bottom:
        score -= 4.0

    else:
        # inside cloud
        if close > (
            cloud_top + cloud_bottom
        ) / 2.0:

            score += 1.0

        else:

            score -= 1.0

    # --------------------------------------------------------
    # TENKAN / KIJUN
    # --------------------------------------------------------

    if tenkan > kijun:
        score += 2.0

    elif tenkan < kijun:
        score -= 2.0

    # --------------------------------------------------------
    # PRICE VS KIJUN
    # --------------------------------------------------------

    if close > kijun:
        score += 2.0

    elif close < kijun:
        score -= 2.0

    # --------------------------------------------------------
    # CLOUD DIRECTION
    # --------------------------------------------------------

    if info["cloud_bullish"]:
        score += 2.0

    elif info["cloud_bearish"]:
        score -= 2.0

    score = clamp(
        score,
        -10.0,
        10.0,
    )

    return score, info


# ============================================================
# MTF ANALYSIS
# ============================================================

def get_mtf_analysis(symbol):

    candles_1h = get_candles(
        symbol,
        TF_1H,
        180,
    )

    candles_30m = get_candles(
        symbol,
        TF_30M,
        180,
    )

    candles_15m = get_candles(
        symbol,
        TF_15M,
        180,
    )

    candles_5m = get_candles(
        symbol,
        TF_5M,
        250,
    )

    if min(
        len(candles_1h),
        len(candles_30m),
        len(candles_15m),
        len(candles_5m),
    ) < 90:

        raise RuntimeError(
            "Not enough closed candles"
        )

    score_1h, info_1h = (
        ichimoku_score(
            candles_1h
        )
    )

    score_30m, info_30m = (
        ichimoku_score(
            candles_30m
        )
    )

    score_15m, info_15m = (
        ichimoku_score(
            candles_15m
        )
    )

    score_5m, info_5m = (
        ichimoku_score(
            candles_5m
        )
    )

    atr_5m = atr(
        candles_5m,
        14,
    )

    return {
        "1h": {
            "score": score_1h,
            "info": info_1h,
            "candles": candles_1h,
        },
        "30m": {
            "score": score_30m,
            "info": info_30m,
            "candles": candles_30m,
        },
        "15m": {
            "score": score_15m,
            "info": info_15m,
            "candles": candles_15m,
        },
        "5m": {
            "score": score_5m,
            "info": info_5m,
            "candles": candles_5m,
        },
        "atr_5m": atr_5m,
    }


# ============================================================
# PULLBACK SCORE
# ============================================================

def pullback_score(
    side,
    analysis,
):

    info = analysis["15m"]["info"]

    score = analysis["15m"]["score"]

    if info is None:
        return 0

    close = info["close"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    if side == "LONG":

        if close >= kijun:
            score += 1

        if close >= tenkan:
            score += 1

    else:

        if close <= kijun:
            score += 1

        if close <= tenkan:
            score += 1

    return int(
        clamp(
            score,
            -10,
            10,
        )
    )


# ============================================================
# 5M TRIGGER
# ============================================================

def trigger_score(
    side,
    candles,
):

    if len(candles) < 5:
        return 0

    current = candles[-1]
    previous = candles[-2]

    score = 0

    close = current["close"]

    previous_high = previous["high"]
    previous_low = previous["low"]

    if side == "LONG":

        # Bullish close
        if current["close"] > current["open"]:
            score += 1

        # Break previous candle high
        if close > previous_high:
            score += 2

        # Higher close
        if close > previous["close"]:
            score += 1

    else:

        # Bearish close
        if current["close"] < current["open"]:
            score += 1

        # Break previous candle low
        if close < previous_low:
            score += 2

        # Lower close
        if close < previous["close"]:
            score += 1

    return score


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def structural_levels(
    side,
    entry,
    candles_5m,
    candles_15m,
):

    atr_value = atr(
        candles_5m,
        14,
    )

    if atr_value <= 0:
        raise RuntimeError(
            "Invalid ATR"
        )

    recent_5m = candles_5m[-10:]
    recent_15m = candles_15m[-6:]

    if side == "LONG":

        lows = (
            [
                x["low"]
                for x in recent_5m
            ]
            +
            [
                x["low"]
                for x in recent_15m
            ]
        )

        structural_low = min(
            lows
        )

        raw_sl = (
            structural_low
            - atr_value * 0.10
        )

        minimum_sl = (
            entry
            * (
                1
                - MIN_RISK_PCT / 100
            )
        )

        sl = min(
            raw_sl,
            minimum_sl,
        )

        risk = (
            entry - sl
        )

        if risk <= 0:
            raise RuntimeError(
                "Invalid LONG risk"
            )

        tp = (
            entry
            + risk * RR
        )

    else:

        highs = (
            [
                x["high"]
                for x in recent_5m
            ]
            +
            [
                x["high"]
                for x in recent_15m
            ]
        )

        structural_high = max(
            highs
        )

        raw_sl = (
            structural_high
            + atr_value * 0.10
        )

        minimum_sl = (
            entry
            * (
                1
                + MIN_RISK_PCT / 100
            )
        )

        sl = max(
            raw_sl,
            minimum_sl,
        )

        risk = (
            sl - entry
        )

        if risk <= 0:
            raise RuntimeError(
                "Invalid SHORT risk"
            )

        tp = (
            entry
            - risk * RR
        )

    return {
        "sl": sl,
        "tp": tp,
        "atr": atr_value,
    }


# ============================================================
# PRICE PRECISION
# ============================================================

def decimal_places(price):

    if price >= 1000:
        return 2

    if price >= 100:
        return 3

    if price >= 10:
        return 4

    if price >= 1:
        return 5

    if price >= 0.1:
        return 6

    if price >= 0.01:
        return 7

    return 8


def round_price(price):

    places = decimal_places(
        price
    )

    return round(
        price,
        places,
    )


# ============================================================
# EXACT RR LEVELS
# ============================================================

def exact_levels(
    side,
    entry,
    sl,
):

    entry = round_price(
        entry
    )

    sl = round_price(
        sl
    )

    if side == "LONG":

        risk = (
            entry - sl
        )

        if risk <= 0:
            raise RuntimeError(
                "Invalid rounded LONG risk"
            )

        min_risk = (
            entry
            * MIN_RISK_PCT
            / 100.0
        )

        if risk < min_risk:

            sl = round_price(
                entry - min_risk
            )

            risk = (
                entry - sl
            )

        tp = round_price(
            entry
            + risk * RR
        )

        reward = (
            tp - entry
        )

    else:

        risk = (
            sl - entry
        )

        if risk <= 0:
            raise RuntimeError(
                "Invalid rounded SHORT risk"
            )

        min_risk = (
            entry
            * MIN_RISK_PCT
            / 100.0
        )

        if risk < min_risk:

            sl = round_price(
                entry + min_risk
            )

            risk = (
                sl - entry
            )

        tp = round_price(
            entry
            - risk * RR
        )

        reward = (
            entry - tp
        )

    if reward <= 0:
        raise RuntimeError(
            "Invalid TP"
        )

    return (
        entry,
        sl,
        tp,
        risk,
        reward,
    )


# ============================================================
# RISK QUALITY
# ============================================================

def risk_quality(
    entry,
    sl,
    tp,
    side,
):

    if entry <= 0:
        return 0

    if side == "LONG":

        risk = entry - sl
        reward = tp - entry

    else:

        risk = sl - entry
        reward = entry - tp

    if risk <= 0:
        return 0

    if reward <= 0:
        return 0

    risk_pct = (
        risk
        / entry
        * 100
    )

    rr = (
        reward
        / risk
    )

    if risk_pct < MIN_RISK_PCT:
        return 0

    score = 0

    if rr >= 1.0:
        score += 20

    if risk_pct >= 0.50:
        score += 10

    if risk_pct <= 3.0:
        score += 10

    return score


# ============================================================
# FINAL RANK SCORE
# ============================================================

def final_rank_score(
    side,
    analysis,
    trigger,
):

    score_1h = analysis["1h"]["score"]
    score_30m = analysis["30m"]["score"]
    score_15m = analysis["15m"]["score"]

    if side == "LONG":

        trend_component = (
            max(score_1h, 0)
            * 5.0
        )

        confirmation = (
            max(score_30m, 0)
            * 3.0
        )

        pullback = (
            max(score_15m, 0)
            * 2.0
        )

    else:

        trend_component = (
            max(-score_1h, 0)
            * 5.0
        )

        confirmation = (
            max(-score_30m, 0)
            * 3.0
        )

        pullback = (
            max(-score_15m, 0)
            * 2.0
        )

    trigger_component = min(
        trigger * 5.0,
        20.0,
    )

    total = (
        trend_component
        + confirmation
        + pullback
        + trigger_component
    )

    return clamp(
        total,
        0,
        100,
    )


# ============================================================
# CANDIDATE ANALYSIS
# ============================================================

def analyze_candidate(
    market
):

    symbol = market["symbol"]

    analysis = get_mtf_analysis(
        symbol
    )

    s1 = analysis["1h"]["score"]
    s30 = analysis["30m"]["score"]
    s15 = analysis["15m"]["score"]
    s5 = analysis["5m"]["score"]

    candles_5m = analysis["5m"]["candles"]
    candles_15m = analysis["15m"]["candles"]

    entry = candles_5m[-1]["close"]

    long_trigger = trigger_score(
        "LONG",
        candles_5m,
    )

    short_trigger = trigger_score(
        "SHORT",
        candles_5m,
    )

    # ========================================================
    # LONG
    # ========================================================

    if (
        s1 >= 4
        and s30 >= 2
        and s15 >= 0
        and s5 >= 0
        and long_trigger >= 1
    ):

        score = final_rank_score(
            "LONG",
            analysis,
            long_trigger,
        )

        if score >= MIN_SCORE:

            levels = structural_levels(
                "LONG",
                entry,
                candles_5m,
                candles_15m,
            )

            (
                entry_r,
                sl_r,
                tp_r,
                risk,
                reward,
            ) = exact_levels(
                "LONG",
                entry,
                levels["sl"],
            )

            rq = risk_quality(
                entry_r,
                sl_r,
                tp_r,
                "LONG",
            )

            if rq > 0:

                return {
                    "symbol": symbol,
                    "side": "LONG",
                    "entry": entry_r,
                    "sl": sl_r,
                    "tp": tp_r,
                    "risk": risk,
                    "reward": reward,
                    "risk_pct": (
                        risk
                        / entry_r
                        * 100
                    ),
                    "rr": (
                        reward
                        / risk
                    ),
                    "score": score,
                    "trigger": long_trigger,
                    "s1": s1,
                    "s30": s30,
                    "s15": s15,
                    "s5": s5,
                    "atr": levels["atr"],
                    "opened_at": now_iso(),
                    "status": "OPEN",
                }

    # ========================================================
    # SHORT
    # ========================================================

    if (
        s1 <= -4
        and s30 <= -2
        and s15 <= 0
        and s5 <= 0
        and short_trigger >= 1
    ):

        score = final_rank_score(
            "SHORT",
            analysis,
            short_trigger,
        )

        if score >= MIN_SCORE:

            levels = structural_levels(
                "SHORT",
                entry,
                candles_5m,
                candles_15m,
            )

            (
                entry_r,
                sl_r,
                tp_r,
                risk,
                reward,
            ) = exact_levels(
                "SHORT",
                entry,
                levels["sl"],
            )

            rq = risk_quality(
                entry_r,
                sl_r,
                tp_r,
                "SHORT",
            )

            if rq > 0:

                return {
                    "symbol": symbol,
                    "side": "SHORT",
                    "entry": entry_r,
                    "sl": sl_r,
                    "tp": tp_r,
                    "risk": risk,
                    "reward": reward,
                    "risk_pct": (
                        risk
                        / entry_r
                        * 100
                    ),
                    "rr": (
                        reward
                        / risk
                    ),
                    "score": score,
                    "trigger": short_trigger,
                    "s1": s1,
                    "s30": s30,
                    "s15": s15,
                    "s5": s5,
                    "atr": levels["atr"],
                    "opened_at": now_iso(),
                    "status": "OPEN",
                }

    return None


# ============================================================
# LIVE PRICE
# ============================================================

def get_live_price(
    symbol,
    tickers=None,
):

    if tickers is None:
        tickers = get_tickers()

    ticker = tickers.get(
        symbol
    )

    return ticker_price(
        ticker
    )


# ============================================================
# TRADE P/L
# ============================================================

def calculate_trade_pnl(
    trade,
    exit_price,
):

    entry = trade["entry"]

    if entry <= 0:
        return 0.0

    if trade["side"] == "LONG":

        return (
            exit_price - entry
        ) / entry * 100.0

    return (
        entry - exit_price
    ) / entry * 100.0


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    reason,
):

    trade = dict(trade)

    exit_price = round_price(
        exit_price
    )

    pnl = calculate_trade_pnl(
        trade,
        exit_price,
    )

    trade["exit_price"] = exit_price
    trade["exit_reason"] = reason
    trade["closed_at"] = now_iso()
    trade["pnl_pct"] = pnl
    trade["status"] = "CLOSED"

    return trade


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    state,
    history,
):

    open_trades = state.get(
        "open_trades",
        [],
    )

    if not open_trades:

        return 0

    print(
        f"[INFO] Open trades before: "
        f"{len(open_trades)}"
    )

    tickers = get_tickers()

    remaining = []

    closed_count = 0

    current_time = now_utc()

    for trade in open_trades:

        symbol = trade["symbol"]
        side = trade["side"]

        entry = safe_float(
            trade.get("entry")
        )

        sl = safe_float(
            trade.get("sl")
        )

        tp = safe_float(
            trade.get("tp")
        )

        opened_at_text = trade.get(
            "opened_at"
        )

        hit_reason = None
        exit_price = None

        # ----------------------------------------------------
        # First: inspect CLOSED 5m candles
        # This avoids missing TP/SL between GitHub runs.
        # ----------------------------------------------------

        try:

            candles = get_candles(
                symbol,
                TF_5M,
                250,
            )

            opened_ts = 0

            if opened_at_text:

                try:

                    opened_ts = int(
                        datetime.fromisoformat(
                            opened_at_text.replace(
                                "Z",
                                "+00:00",
                            )
                        ).timestamp()
                    )

                except Exception:
                    opened_ts = 0

            relevant = []

            for candle in candles:

                if (
                    opened_ts > 0
                    and candle["time"]
                    < opened_ts
                ):
                    continue

                relevant.append(
                    candle
                )

            for candle in relevant:

                high = candle["high"]
                low = candle["low"]

                if side == "LONG":

                    hit_sl = (
                        low <= sl
                    )

                    hit_tp = (
                        high >= tp
                    )

                    if hit_sl and hit_tp:

                        # Conservative assumption:
                        # SL first if both occur
                        # inside same candle.
                        hit_reason = "SL"
                        exit_price = sl
                        break

                    if hit_sl:

                        hit_reason = "SL"
                        exit_price = sl
                        break

                    if hit_tp:

                        hit_reason = "TP"
                        exit_price = tp
                        break

                else:

                    hit_sl = (
                        high >= sl
                    )

                    hit_tp = (
                        low <= tp
                    )

                    if hit_sl and hit_tp:

                        hit_reason = "SL"
                        exit_price = sl
                        break

                    if hit_sl:

                        hit_reason = "SL"
                        exit_price = sl
                        break

                    if hit_tp:

                        hit_reason = "TP"
                        exit_price = tp
                        break

        except Exception as e:

            print(
                f"[WARN] Candle monitor failed "
                f"{symbol}: {e}"
            )

        # ----------------------------------------------------
        # Fallback to live ticker
        # ----------------------------------------------------

        if hit_reason is None:

            live = get_live_price(
                symbol,
                tickers,
            )

            if live > 0:

                if side == "LONG":

                    if live <= sl:

                        hit_reason = "SL"
                        exit_price = sl

                    elif live >= tp:

                        hit_reason = "TP"
                        exit_price = tp

                else:

                    if live >= sl:

                        hit_reason = "SL"
                        exit_price = sl

                    elif live <= tp:

                        hit_reason = "TP"
                        exit_price = tp

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        if hit_reason is None:

            if opened_at_text:

                try:

                    opened_dt = datetime.fromisoformat(
                        opened_at_text.replace(
                            "Z",
                            "+00:00",
                        )
                    )

                    age = (
                        current_time
                        - opened_dt
                    )

                    if (
                        age
                        >= timedelta(
                            hours=TIMEOUT_HOURS
                        )
                    ):

                        live = get_live_price(
                            symbol,
                            tickers,
                        )

                        if live > 0:

                            hit_reason = "TIMEOUT"
                            exit_price = live

                except Exception:
                    pass

        # ----------------------------------------------------
        # CLOSE
        # ----------------------------------------------------

        if hit_reason:

            closed = close_trade(
                trade,
                exit_price,
                hit_reason,
            )

            history.append(
                closed
            )

            closed_count += 1

            print(
                f"[CLOSED] "
                f"{symbol} "
                f"{side} "
                f"{hit_reason} "
                f"P/L={closed['pnl_pct']:+.2f}%"
            )

        else:

            remaining.append(
                trade
            )

    state["open_trades"] = remaining

    save_state(
        state
    )

    save_history(
        history
    )

    print(
        f"[INFO] Open trades after: "
        f"{len(remaining)}"
    )

    print(
        f"[INFO] Closed this run: "
        f"{closed_count}"
    )

    return closed_count


# ============================================================
# OPEN SYMBOLS
# ============================================================

def open_symbols(
    state
):

    return {
        trade["symbol"]
        for trade
        in state.get(
            "open_trades",
            [],
        )
    }


# ============================================================
# SCAN ALL MARKETS
# ============================================================

def scan_all_markets(
    markets,
    state,
):

    candidates = []

    opened = open_symbols(
        state
    )

    print(
        f"[INFO] Markets: "
        f"{len(markets)}"
    )

    print(
        f"[INFO] Open symbols: "
        f"{len(opened)}"
    )

    for index, market in enumerate(
        markets,
        start=1,
    ):

        symbol = market["symbol"]

        print(
            f"[{index:03d}/{len(markets):03d}] "
            f"{symbol}"
        )

        if symbol in opened:

            print(
                "    ⏭ OPEN"
            )

            continue

        try:

            candidate = analyze_candidate(
                market
            )

            if candidate:

                candidates.append(
                    candidate
                )

                print(
                    f"    ✅ "
                    f"{candidate['side']} "
                    f"Score={candidate['score']:.1f} "
                    f"Entry={candidate['entry']} "
                    f"SL={candidate['sl']} "
                    f"TP={candidate['tp']}"
                )

            else:

                print(
                    "    ⏭ NO SIGNAL"
                )

        except Exception as e:

            print(
                f"    ❌ ERROR "
                f"{e}"
            )

    return candidates


# ============================================================
# SELECT BEST TRADES
# ============================================================

def select_best_trades(
    candidates,
    state,
):

    open_trades = state.get(
        "open_trades",
        [],
    )

    current_total = len(
        open_trades
    )

    current_long = sum(
        1
        for x in open_trades
        if x.get("side") == "LONG"
    )

    current_short = sum(
        1
        for x in open_trades
        if x.get("side") == "SHORT"
    )

    slots_total = (
        MAX_OPEN_TRADES
        - current_total
    )

    slots_long = (
        MAX_LONG_TRADES
        - current_long
    )

    slots_short = (
        MAX_SHORT_TRADES
        - current_short
    )

    if slots_total <= 0:

        return []

    candidates = sorted(
        candidates,
        key=lambda x: (
            x["score"],
            x["trigger"],
            x["risk_pct"],
        ),
        reverse=True,
    )

    selected = []

    used_symbols = set()

    for candidate in candidates:

        if len(selected) >= slots_total:
            break

        symbol = candidate[
            "symbol"
        ]

        if symbol in used_symbols:
            continue

        side = candidate[
            "side"
        ]

        if side == "LONG":

            if slots_long <= 0:
                continue

        else:

            if slots_short <= 0:
                continue

        selected.append(
            candidate
        )

        used_symbols.add(
            symbol
        )

        if side == "LONG":
            slots_long -= 1

        else:
            slots_short -= 1

    return selected


# ============================================================
# ADD SELECTED TRADES
# ============================================================

def add_selected_trades(
    state,
    selected,
):

    for candidate in selected:

        trade = dict(
            candidate
        )

        state[
            "open_trades"
        ].append(
            trade
        )

        print(
            f"[OPEN] "
            f"{trade['symbol']} "
            f"{trade['side']} "
            f"Entry={trade['entry']} "
            f"SL={trade['sl']} "
            f"TP={trade['tp']} "
            f"Risk={trade['risk_pct']:.2f}% "
            f"RR={trade['rr']:.2f}"
        )

    save_state(
        state
    )


# ============================================================
# PERFORMANCE
# ============================================================

def performance(
    history
):

    trades = len(
        history
    )

    wins = 0
    losses = 0
    timeout = 0

    total_pnl = 0.0

    for trade in history:

        pnl = safe_float(
            trade.get(
                "pnl_pct",
                0
            )
        )

        total_pnl += pnl

        reason = trade.get(
            "exit_reason"
        )

        if reason == "TP":
            wins += 1

        elif reason == "SL":
            losses += 1

        elif reason == "TIMEOUT":
            timeout += 1

    decided = (
        wins + losses
    )

    if decided > 0:

        win_rate = (
            wins
            / decided
            * 100
        )

    else:

        win_rate = 0.0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeout": timeout,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    value = safe_float(
        value
    )

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


# ============================================================
# TELEGRAM REPORT
# ============================================================

def generate_report(
    state,
    history,
    closed_count=0,
):

    stats = performance(
        history
    )

    open_trades = state.get(
        "open_trades",
        [],
    )

    long_count = sum(
        1
        for x in open_trades
        if x.get("side") == "LONG"
    )

    short_count = sum(
        1
        for x in open_trades
        if x.get("side") == "SHORT"
    )

    lines = []

    lines.append(
        "📡 **CRYPTO ICHIMOKU REPORT**"
    )

    lines.append(
        f"🕐 {now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        "⏱ **5m CLOSED | TOP 100**"
    )

    lines.append(
        "🤖 **MTF ICHIMOKU | RR 1:1**"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📂 **OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})**"
    )

    if not open_trades:

        lines.append(
            "No open trades"
        )

    else:

        for trade in open_trades:

            side_emoji = (
                "🟢"
                if trade["side"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{side_emoji} "
                f"**{trade['symbol']}** "
                f"{trade['side']}"
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(trade['entry'])}"
            )

            lines.append(
                f"SL: "
                f"{fmt_price(trade['sl'])} "
                f"({trade['risk_pct']:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{fmt_price(trade['tp'])} "
                f"(RR {trade['rr']:.2f})"
            )

            lines.append(
                f"Score: "
                f"{trade['score']:.1f}"
            )

            lines.append(
                "────────────"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 **PERFORMANCE**"
    )

    lines.append(
        f"Trades {stats['trades']} | "
        f"🟢 {stats['wins']} | "
        f"🔴 {stats['losses']} | "
        f"⚪ {stats['timeout']} | "
        f"⏱ {closed_count}"
    )

    lines.append(
        f"🏆 WR: "
        f"{stats['win_rate']:.1f}%"
    )

    lines.append(
        f"💰 Total P/L: "
        f"{stats['total_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"⚙️ LIMITS: "
        f"LONG {long_count}/{MAX_LONG_TRADES} | "
        f"SHORT {short_count}/{MAX_SHORT_TRADES} | "
        f"TOTAL {len(open_trades)}/{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"🛡 Minimum Risk: "
        f"{MIN_RISK_PCT:.2f}%"
    )

    return "\n".join(
        lines
    )


def send_telegram(
    message
):

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    if not token or not chat_id:

        print(
            "[WARN] Telegram secrets missing"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + token
        + "/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        print(
            "[INFO] Telegram report sent."
        )

        return True

    except Exception as e:

        print(
            f"[WARN] Telegram failed: {e}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "============================================================"
    )

    print(
        "KRAKEN FUTURES ICHIMOKU TOP RANKER v11.4"
    )

    print(
        "============================================================"
    )

    print(
        "Exchange: Kraken Futures"
    )

    print(
        "Timeframe: 5m CLOSED"
    )

    print(
        "Trend: 1H"
    )

    print(
        "Confirmation: 30m"
    )

    print(
        "Pullback: 15m"
    )

    print(
        "Trigger: 5m"
    )

    print(
        f"TOP: {TOP_N}"
    )

    print(
        f"MAX OPEN: {MAX_OPEN_TRADES}"
    )

    print(
        f"MAX LONG: {MAX_LONG_TRADES}"
    )

    print(
        f"MAX SHORT: {MAX_SHORT_TRADES}"
    )

    print(
        f"MIN RISK: {MIN_RISK_PCT:.2f}%"
    )

    print(
        f"RR: {RR}:1"
    )

    print(
        "Ichimoku: STANDARD DISPLACED CLOUD"
    )

    print(
        "Kraken Resolutions: "
        "1h / 30m / 15m / 5m"
    )

    print(
        "============================================================"
    )

    state = load_state()

    history = load_history()

    # ========================================================
    # STEP 1
    # ========================================================

    print(
        "[STEP 1] Monitoring open trades..."
    )

    closed_count = 0

    try:

        closed_count = monitor_open_trades(
            state,
            history,
        )

    except Exception as e:

        print(
            f"[ERROR] Monitor failed: {e}"
        )

        traceback.print_exc()

    # ========================================================
    # STEP 2
    # ========================================================

    print(
        "[STEP 2] Loading markets..."
    )

    try:

        markets = build_market_list()

    except Exception as e:

        print(
            f"[FATAL] Market loading failed: "
            f"{e}"
        )

        traceback.print_exc()

        report = generate_report(
            state,
            history,
            closed_count,
        )

        send_telegram(
            report
        )

        return

    if not markets:

        print(
            "[FATAL] No markets available."
        )

        report = generate_report(
            state,
            history,
            closed_count,
        )

        send_telegram(
            report
        )

        return

    # ========================================================
    # STEP 3
    # ========================================================

    print(
        "[STEP 3] Scanning markets..."
    )

    candidates = []

    try:

        candidates = scan_all_markets(
            markets,
            state,
        )

    except Exception as e:

        print(
            f"[ERROR] Scanner failed: "
            f"{e}"
        )

        traceback.print_exc()

    print(
        f"[INFO] Valid candidates: "
        f"{len(candidates)}"
    )

    # ========================================================
    # STEP 4
    # ========================================================

    print(
        "[STEP 4] Selecting best trades..."
    )

    selected = select_best_trades(
        candidates,
        state,
    )

    print(
        f"[INFO] Selected: "
        f"{len(selected)}"
    )

    # ========================================================
    # STEP 5
    # ========================================================

    if selected:

        print(
            "[STEP 5] Opening selected trades..."
        )

        add_selected_trades(
            state,
            selected,
        )

    else:

        print(
            "[STEP 5] No new trades."
        )

    # ========================================================
    # SAVE
    # ========================================================

    save_state(
        state
    )

    save_history(
        history
    )

    # ========================================================
    # REPORT
    # ========================================================

    print(
        "[STEP 6] Generating Telegram report..."
    )

    report = generate_report(
        state,
        history,
        closed_count,
    )

    print(
        "============================================================"
    )

    print(
        report
    )

    print(
        "============================================================"
    )

    send_telegram(
        report
    )

    print(
        "[DONE]"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOP] Interrupted."
        )

    except Exception as e:

        print(
            f"\n[FATAL] {e}"
        )

        traceback.print_exc()
