# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v11.2
# ============================================================
# Kraken Futures
# TOP 100 markets
#
# MTF ICHIMOKU:
#   1H  = Trend
#   30m = Confirmation
#   15m = Pullback
#   5m  = Trigger
#
# CLOSED CANDLES ONLY
#
# ENTRY:
#   BUY / SELL after 5m candle close
#
# RISK:
#   Minimum Entry -> SL = 0.50%
#   RR = 1:1
#
# OPEN TRADES:
#   Maximum 4
#   Maximum 2 LONG
#   Maximum 2 SHORT
#   One trade per symbol
#
# MANAGEMENT:
#   TP / SL monitoring
#   4 hour timeout
#   Persistent state
#   Persistent history
#
# TELEGRAM:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# STATE FILES:
#   ichimoku_state.json
#   ichimoku_trade_history.json
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone, timedelta

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = BASE_URL + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE_URL + "/derivatives/api/v3/tickers"
CHART_URL = BASE_URL + "/api/charts/v1/trade/{symbol}/{resolution}"

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TOP_N = 100

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

MIN_RISK_PCT = 0.50

RR = 1.0

TIMEOUT_HOURS = 4

REQUEST_TIMEOUT = 20

RETRIES = 3

# Timeframes
TF_5M = 5
TF_15M = 15
TF_30M = 30
TF_1H = 60

# Ichimoku
TENKAN = 9
KIJUN = 26
SENKOU_B = 52
DISPLACEMENT = 26

# Ranking weights
WEIGHT_1H = 0.50
WEIGHT_30M = 0.30
WEIGHT_15M = 0.20

# Scanner thresholds
MIN_SCORE = 70.0

# Pullback tolerance
PULLBACK_ATR_MULTIPLIER = 1.5

# Session
SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Kraken-Ichi-Screener/11.2",
        "Accept": "application/json",
    }
)


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def parse_datetime(value):
    if not value:
        return now_utc()

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return now_utc()


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def clean_number(value):
    if value is None:
        return 0.0

    try:
        value = float(value)

        if not math.isfinite(value):
            return 0.0

        return value
    except Exception:
        return 0.0


def round_price(value):
    """
    Higher precision than the previous version.
    Important for low-priced coins such as ALGO/ENA/CRV.
    """
    value = clean_number(value)

    if value == 0:
        return 0.0

    abs_value = abs(value)

    if abs_value >= 1000:
        decimals = 2
    elif abs_value >= 100:
        decimals = 3
    elif abs_value >= 10:
        decimals = 4
    elif abs_value >= 1:
        decimals = 5
    elif abs_value >= 0.1:
        decimals = 6
    elif abs_value >= 0.01:
        decimals = 7
    else:
        decimals = 8

    return round(value, decimals)


def format_price(value):
    value = clean_number(value)

    if value == 0:
        return "0"

    abs_value = abs(value)

    if abs_value >= 1000:
        decimals = 2
    elif abs_value >= 100:
        decimals = 3
    elif abs_value >= 10:
        decimals = 4
    elif abs_value >= 1:
        decimals = 5
    elif abs_value >= 0.1:
        decimals = 6
    elif abs_value >= 0.01:
        decimals = 7
    else:
        decimals = 8

    text = f"{value:.{decimals}f}"

    text = text.rstrip("0").rstrip(".")

    return text


def pct_change(entry, price, direction):
    entry = safe_float(entry)
    price = safe_float(price)

    if not entry or not price:
        return 0.0

    if direction == "LONG":
        return ((price - entry) / entry) * 100.0

    return ((entry - price) / entry) * 100.0


def distance_pct(a, b):
    a = safe_float(a)
    b = safe_float(b)

    if not a or not b:
        return 0.0

    return abs((a - b) / b) * 100.0


# ============================================================
# JSON STATE
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"[WARN] Cannot load {filename}: {e}")
        return default


def save_json(filename, data):
    temp_file = filename + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp_file, filename)


def load_state():
    state = load_json(
        STATE_FILE,
        {
            "open_trades": [],
            "history": [],
            "updated_at": iso_now(),
        },
    )

    if not isinstance(state, dict):
        state = {}

    if not isinstance(state.get("open_trades"), list):
        state["open_trades"] = []

    if not isinstance(state.get("history"), list):
        state["history"] = []

    state["updated_at"] = iso_now()

    # Load separate history file too
    external_history = load_json(HISTORY_FILE, [])

    if isinstance(external_history, list):
        existing_ids = {
            str(x.get("id"))
            for x in state["history"]
            if isinstance(x, dict) and x.get("id")
        }

        for trade in external_history:
            if not isinstance(trade, dict):
                continue

            trade_id = str(trade.get("id", ""))

            if trade_id and trade_id not in existing_ids:
                state["history"].append(trade)

    return state


def save_state(state):
    state["updated_at"] = iso_now()

    save_json(STATE_FILE, state)

    history = state.get("history", [])

    if not isinstance(history, list):
        history = []

    save_json(HISTORY_FILE, history)


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
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            last_error = e

            print(
                f"[WARN] GET failed "
                f"{attempt}/{RETRIES}: {url} | {e}"
            )

            if attempt < RETRIES:
                time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"Request failed after {RETRIES} attempts: "
        f"{url} | {last_error}"
    )


# ============================================================
# KRAKEN MARKETS
# ============================================================

def get_instruments():
    data = http_get(INSTRUMENTS_URL)

    instruments = data.get("instruments", [])

    if not isinstance(instruments, list):
        return []

    return instruments


def get_tickers():
    data = http_get(TICKERS_URL)

    tickers = data.get("tickers", [])

    if not isinstance(tickers, list):
        return []

    return tickers


def normalize_symbol(value):
    if not value:
        return ""

    return str(value).upper().strip()


def is_usd_futures_symbol(symbol):
    symbol = normalize_symbol(symbol)

    return (
        symbol.startswith("PF_")
        and symbol.endswith("USD")
    )


def build_market_list():
    instruments = get_instruments()

    tickers = get_tickers()

    ticker_map = {}

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        symbol = normalize_symbol(
            ticker.get("symbol")
            or ticker.get("pair")
            or ticker.get("instrument")
        )

        if symbol:
            ticker_map[symbol] = ticker

    markets = []

    for instrument in instruments:

        if not isinstance(instrument, dict):
            continue

        symbol = normalize_symbol(
            instrument.get("symbol")
            or instrument.get("name")
            or instrument.get("pair")
        )

        if not symbol:
            continue

        if not is_usd_futures_symbol(symbol):
            continue

        ticker = ticker_map.get(symbol, {})

        volume = safe_float(
            ticker.get("vol24h")
            or ticker.get("volume24h")
            or ticker.get("volume")
            or ticker.get("volume_quote"),
            0.0,
        )

        last = safe_float(
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice"),
            0.0,
        )

        markets.append(
            {
                "symbol": symbol,
                "volume": volume or 0.0,
                "last": last or 0.0,
            }
        )

    markets.sort(
        key=lambda x: x.get("volume", 0.0),
        reverse=True,
    )

    return markets[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def normalize_timestamp(value):
    value = safe_float(value, 0)

    if value <= 0:
        return 0

    # milliseconds -> seconds
    if value > 1_000_000_000_000:
        value /= 1000.0

    return int(value)


def parse_candle(raw):
    """
    Kraken chart API normally returns:
    [time, open, high, low, close, volume]
    """

    if isinstance(raw, dict):

        ts = (
            raw.get("time")
            or raw.get("timestamp")
            or raw.get("t")
        )

        o = raw.get("open") or raw.get("o")
        h = raw.get("high") or raw.get("h")
        l = raw.get("low") or raw.get("l")
        c = raw.get("close") or raw.get("c")
        v = raw.get("volume") or raw.get("v") or 0

        return {
            "time": normalize_timestamp(ts),
            "open": safe_float(o, 0.0),
            "high": safe_float(h, 0.0),
            "low": safe_float(l, 0.0),
            "close": safe_float(c, 0.0),
            "volume": safe_float(v, 0.0),
        }

    if isinstance(raw, (list, tuple)) and len(raw) >= 5:

        return {
            "time": normalize_timestamp(raw[0]),
            "open": safe_float(raw[1], 0.0),
            "high": safe_float(raw[2], 0.0),
            "low": safe_float(raw[3], 0.0),
            "close": safe_float(raw[4], 0.0),
            "volume": (
                safe_float(raw[5], 0.0)
                if len(raw) > 5
                else 0.0
            ),
        }

    return None


def get_candles(symbol, resolution, limit=250):
    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution,
    )

    data = http_get(url)

    raw_candles = []

    if isinstance(data, dict):

        raw_candles = (
            data.get("candles")
            or data.get("data")
            or data.get("results")
            or []
        )

    elif isinstance(data, list):

        raw_candles = data

    candles = []

    for raw in raw_candles:

        candle = parse_candle(raw)

        if candle is None:
            continue

        if candle["time"] <= 0:
            continue

        if candle["close"] <= 0:
            continue

        candles.append(candle)

    candles.sort(key=lambda x: x["time"])

    # Remove duplicate timestamps
    unique = {}

    for candle in candles:
        unique[candle["time"]] = candle

    candles = list(unique.values())

    candles.sort(key=lambda x: x["time"])

    # CLOSED CANDLES ONLY
    candle_seconds = resolution * 60

    current_ts = int(time.time())

    closed = []

    for candle in candles:

        if candle["time"] + candle_seconds <= current_ts:
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
        return None

    tr = true_range(candles)

    values = tr[-period:]

    if not values:
        return None

    return sum(values) / len(values)


def rolling_mid(candles, period):
    if len(candles) < period:
        return None

    section = candles[-period:]

    highest = max(x["high"] for x in section)
    lowest = min(x["low"] for x in section)

    return (highest + lowest) / 2.0


def ichimoku(candles):
    required = SENKOU_B + DISPLACEMENT

    if len(candles) < required:
        return None

    tenkan = rolling_mid(candles, TENKAN)
    kijun = rolling_mid(candles, KIJUN)

    span_a_base = (
        (tenkan + kijun) / 2.0
        if tenkan is not None and kijun is not None
        else None
    )

    span_b_base = rolling_mid(
        candles,
        SENKOU_B,
    )

    if (
        tenkan is None
        or kijun is None
        or span_a_base is None
        or span_b_base is None
    ):
        return None

    # Cloud used for current decision
    cloud_top = max(
        span_a_base,
        span_b_base,
    )

    cloud_bottom = min(
        span_a_base,
        span_b_base,
    )

    price = candles[-1]["close"]

    return {
        "price": price,
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a_base,
        "span_b": span_b_base,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(candles):
    data = ichimoku(candles)

    if not data:
        return 0.0, None

    price = data["price"]

    tenkan = data["tenkan"]
    kijun = data["kijun"]

    cloud_top = data["cloud_top"]
    cloud_bottom = data["cloud_bottom"]

    score = 0.0

    # Price vs cloud
    if price > cloud_top:
        score += 4.0

    elif price < cloud_bottom:
        score -= 4.0

    # Tenkan vs Kijun
    if tenkan > kijun:
        score += 3.0

    elif tenkan < kijun:
        score -= 3.0

    # Price vs Kijun
    if price > kijun:
        score += 2.0

    elif price < kijun:
        score -= 2.0

    # Price vs Tenkan
    if price > tenkan:
        score += 1.0

    elif price < tenkan:
        score -= 1.0

    score = max(-10.0, min(10.0, score))

    return score, data


# ============================================================
# MTF ANALYSIS
# ============================================================

def get_mtf_analysis(symbol):
    candles_1h = get_candles(
        symbol,
        TF_1H,
        250,
    )

    candles_30m = get_candles(
        symbol,
        TF_30M,
        250,
    )

    candles_15m = get_candles(
        symbol,
        TF_15M,
        250,
    )

    candles_5m = get_candles(
        symbol,
        TF_5M,
        250,
    )

    if (
        len(candles_1h) < 100
        or len(candles_30m) < 100
        or len(candles_15m) < 100
        or len(candles_5m) < 100
    ):
        return None

    score_1h, ichi_1h = ichimoku_score(candles_1h)
    score_30m, ichi_30m = ichimoku_score(candles_30m)
    score_15m, ichi_15m = ichimoku_score(candles_15m)
    score_5m, ichi_5m = ichimoku_score(candles_5m)

    if (
        ichi_1h is None
        or ichi_30m is None
        or ichi_15m is None
        or ichi_5m is None
    ):
        return None

    trend_score = (
        score_1h * WEIGHT_1H
        + score_30m * WEIGHT_30M
        + score_15m * WEIGHT_15M
    )

    return {
        "symbol": symbol,

        "candles_1h": candles_1h,
        "candles_30m": candles_30m,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,

        "score_1h": score_1h,
        "score_30m": score_30m,
        "score_15m": score_15m,
        "score_5m": score_5m,

        "trend_score": trend_score,

        "ichi_1h": ichi_1h,
        "ichi_30m": ichi_30m,
        "ichi_15m": ichi_15m,
        "ichi_5m": ichi_5m,
    }


# ============================================================
# PULLBACK
# ============================================================

def pullback_score(
    direction,
    candles_15m,
    ichi_15m,
):
    if not candles_15m or not ichi_15m:
        return 0.0

    price = candles_15m[-1]["close"]

    tenkan = ichi_15m["tenkan"]
    kijun = ichi_15m["kijun"]

    atr_value = atr(
        candles_15m,
        14,
    )

    if not atr_value or atr_value <= 0:
        atr_value = abs(price) * 0.005

    if direction == "LONG":

        distance_tenkan = abs(
            price - tenkan
        )

        distance_kijun = abs(
            price - kijun
        )

        score = 0.0

        if price >= tenkan:
            score += 2.0

        if distance_tenkan <= atr_value * PULLBACK_ATR_MULTIPLIER:
            score += 2.0

        if price >= kijun:
            score += 1.0

        if distance_kijun <= atr_value * 2.0:
            score += 1.0

        return min(6.0, score)

    else:

        distance_tenkan = abs(
            price - tenkan
        )

        distance_kijun = abs(
            price - kijun
        )

        score = 0.0

        if price <= tenkan:
            score += 2.0

        if distance_tenkan <= atr_value * PULLBACK_ATR_MULTIPLIER:
            score += 2.0

        if price <= kijun:
            score += 1.0

        if distance_kijun <= atr_value * 2.0:
            score += 1.0

        return min(6.0, score)


# ============================================================
# 5M TRIGGER
# ============================================================

def trigger_score(direction, candles):
    if len(candles) < 5:
        return 0.0

    c0 = candles[-1]
    c1 = candles[-2]

    score = 0.0

    if direction == "LONG":

        if c0["close"] > c0["open"]:
            score += 2.0

        if c0["close"] > c1["high"]:
            score += 3.0

        elif c0["close"] > c1["close"]:
            score += 1.0

        if c0["low"] >= c1["low"]:
            score += 1.0

    else:

        if c0["close"] < c0["open"]:
            score += 2.0

        if c0["close"] < c1["low"]:
            score += 3.0

        elif c0["close"] < c1["close"]:
            score += 1.0

        if c0["high"] <= c1["high"]:
            score += 1.0

    return min(6.0, score)


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def structural_levels(
    direction,
    candles_5m,
    candles_15m,
    entry,
):
    if (
        len(candles_5m) < 10
        or len(candles_15m) < 10
        or entry <= 0
    ):
        return None

    atr_5m = atr(
        candles_5m,
        14,
    )

    if not atr_5m or atr_5m <= 0:
        atr_5m = entry * 0.005

    # Recent structure
    recent_5m = candles_5m[-10:]
    recent_15m = candles_15m[-6:]

    swing_low = min(
        min(c["low"] for c in recent_5m),
        min(c["low"] for c in recent_15m),
    )

    swing_high = max(
        max(c["high"] for c in recent_5m),
        max(c["high"] for c in recent_15m),
    )

    if direction == "LONG":

        sl = swing_low

        # small ATR safety buffer
        sl = sl - (atr_5m * 0.10)

        risk = entry - sl

        minimum_risk = entry * (
            MIN_RISK_PCT / 100.0
        )

        if risk < minimum_risk:
            risk = minimum_risk
            sl = entry - risk

        if sl >= entry:
            return None

        tp = entry + (
            risk * RR
        )

    else:

        sl = swing_high

        sl = sl + (atr_5m * 0.10)

        risk = sl - entry

        minimum_risk = entry * (
            MIN_RISK_PCT / 100.0
        )

        if risk < minimum_risk:
            risk = minimum_risk
            sl = entry + risk

        if sl <= entry:
            return None

        tp = entry - (
            risk * RR
        )

    risk_pct = (
        abs(entry - sl)
        / entry
        * 100.0
    )

    reward_pct = (
        abs(tp - entry)
        / entry
        * 100.0
    )

    if risk_pct < MIN_RISK_PCT:
        return None

    return {
        "entry": round_price(entry),
        "sl": round_price(sl),
        "tp": round_price(tp),
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
    }


# ============================================================
# RISK QUALITY
# ============================================================

def risk_quality(levels):
    if not levels:
        return 0.0

    risk_pct = levels["risk_pct"]

    if risk_pct < MIN_RISK_PCT:
        return 0.0

    # Prefer reasonable risk rather than extremely wide SL
    if risk_pct <= 0.75:
        return 10.0

    if risk_pct <= 1.00:
        return 8.0

    if risk_pct <= 1.50:
        return 6.0

    if risk_pct <= 2.00:
        return 4.0

    if risk_pct <= 3.00:
        return 2.0

    return 0.0


# ============================================================
# FINAL SCORE
# ============================================================

def final_rank_score(
    direction,
    mtf,
    levels,
    pullback,
    trigger,
):
    if not mtf or not levels:
        return -999.0

    if direction == "LONG":

        trend_component = (
            max(0.0, mtf["score_1h"]) * 3.0
            + max(0.0, mtf["score_30m"]) * 2.0
            + max(0.0, mtf["score_15m"]) * 1.0
        )

    else:

        trend_component = (
            abs(min(0.0, mtf["score_1h"])) * 3.0
            + abs(min(0.0, mtf["score_30m"])) * 2.0
            + abs(min(0.0, mtf["score_15m"])) * 1.0
        )

    quality = risk_quality(levels)

    score = (
        trend_component
        + pullback
        + trigger
        + quality
    )

    return round(score, 2)


# ============================================================
# CANDIDATE
# ============================================================

def analyze_candidate(
    symbol,
    market_last,
):
    try:

        mtf = get_mtf_analysis(symbol)

        if not mtf:
            return []

        candidates = []

        entry = (
            mtf["candles_5m"][-1]["close"]
            if mtf["candles_5m"]
            else market_last
        )

        if entry <= 0:
            return []

        # ====================================================
        # LONG
        # ====================================================

        if (
            mtf["score_1h"] >= 4.0
            and mtf["score_30m"] >= 2.0
            and mtf["score_15m"] >= 0.0
            and mtf["score_5m"] >= 0.0
        ):

            pb = pullback_score(
                "LONG",
                mtf["candles_15m"],
                mtf["ichi_15m"],
            )

            trigger = trigger_score(
                "LONG",
                mtf["candles_5m"],
            )

            if trigger >= 1.0:

                levels = structural_levels(
                    "LONG",
                    mtf["candles_5m"],
                    mtf["candles_15m"],
                    entry,
                )

                if levels:

                    score = final_rank_score(
                        "LONG",
                        mtf,
                        levels,
                        pb,
                        trigger,
                    )

                    if score >= MIN_SCORE:

                        candidates.append(
                            {
                                "symbol": symbol,
                                "direction": "LONG",
                                "entry": levels["entry"],
                                "sl": levels["sl"],
                                "tp": levels["tp"],
                                "risk_pct": levels["risk_pct"],
                                "reward_pct": levels["reward_pct"],
                                "score": score,
                                "trend_score": mtf["trend_score"],
                                "score_1h": mtf["score_1h"],
                                "score_30m": mtf["score_30m"],
                                "score_15m": mtf["score_15m"],
                                "score_5m": mtf["score_5m"],
                            }
                        )

        # ====================================================
        # SHORT
        # ====================================================

        if (
            mtf["score_1h"] <= -4.0
            and mtf["score_30m"] <= -2.0
            and mtf["score_15m"] <= 0.0
            and mtf["score_5m"] <= 0.0
        ):

            pb = pullback_score(
                "SHORT",
                mtf["candles_15m"],
                mtf["ichi_15m"],
            )

            trigger = trigger_score(
                "SHORT",
                mtf["candles_5m"],
            )

            if trigger >= 1.0:

                levels = structural_levels(
                    "SHORT",
                    mtf["candles_5m"],
                    mtf["candles_15m"],
                    entry,
                )

                if levels:

                    score = final_rank_score(
                        "SHORT",
                        mtf,
                        levels,
                        pb,
                        trigger,
                    )

                    if score >= MIN_SCORE:

                        candidates.append(
                            {
                                "symbol": symbol,
                                "direction": "SHORT",
                                "entry": levels["entry"],
                                "sl": levels["sl"],
                                "tp": levels["tp"],
                                "risk_pct": levels["risk_pct"],
                                "reward_pct": levels["reward_pct"],
                                "score": score,
                                "trend_score": mtf["trend_score"],
                                "score_1h": mtf["score_1h"],
                                "score_30m": mtf["score_30m"],
                                "score_15m": mtf["score_15m"],
                                "score_5m": mtf["score_5m"],
                            }
                        )

        return candidates

    except Exception as e:

        print(
            f"[WARN] Candidate failed "
            f"{symbol}: {e}"
        )

        return []


# ============================================================
# TRADE ID
# ============================================================

def make_trade_id(symbol, direction):
    timestamp = int(time.time() * 1000)

    return (
        f"{symbol}_{direction}_{timestamp}"
    )


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(candidate):
    return {
        "id": make_trade_id(
            candidate["symbol"],
            candidate["direction"],
        ),

        "symbol": candidate["symbol"],

        "direction": candidate["direction"],

        "entry": candidate["entry"],

        "sl": candidate["sl"],

        "tp": candidate["tp"],

        "score": candidate["score"],

        "risk_pct": candidate["risk_pct"],

        "reward_pct": candidate["reward_pct"],

        "opened_at": iso_now(),

        "status": "OPEN",

        "current_price": candidate["entry"],

        "current_pnl_pct": 0.0,

        "result": None,

        "closed_at": None,

        "close_price": None,

        "close_reason": None,

        "timeout": False,
    }


# ============================================================
# LIVE PRICE
# ============================================================

def get_live_price(symbol):
    try:

        tickers = get_tickers()

        for ticker in tickers:

            if not isinstance(ticker, dict):
                continue

            ticker_symbol = normalize_symbol(
                ticker.get("symbol")
                or ticker.get("pair")
                or ticker.get("instrument")
            )

            if ticker_symbol != normalize_symbol(symbol):
                continue

            price = (
                ticker.get("last")
                or ticker.get("lastPrice")
                or ticker.get("markPrice")
            )

            price = safe_float(price)

            if price and price > 0:
                return price

        return None

    except Exception as e:

        print(
            f"[WARN] Live price failed "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    close_price,
    reason,
):
    direction = trade["direction"]

    entry = safe_float(
        trade.get("entry"),
        0.0,
    )

    close_price = safe_float(
        close_price,
        entry,
    )

    if direction == "LONG":

        pnl_pct = (
            (close_price - entry)
            / entry
            * 100.0
        )

    else:

        pnl_pct = (
            (entry - close_price)
            / entry
            * 100.0
        )

    if reason == "TIMEOUT":

        result = "TIMEOUT"

    elif pnl_pct > 0:

        result = "WIN"

    elif pnl_pct < 0:

        result = "LOSS"

    else:

        result = "BREAKEVEN"

    trade["status"] = "CLOSED"

    trade["current_price"] = close_price

    trade["current_pnl_pct"] = pnl_pct

    trade["close_price"] = close_price

    trade["close_reason"] = reason

    trade["closed_at"] = iso_now()

    trade["result"] = result

    trade["timeout"] = (
        reason == "TIMEOUT"
    )

    return trade


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(state):
    open_trades = state.get(
        "open_trades",
        [],
    )

    remaining = []

    history = state.get(
        "history",
        [],
    )

    for trade in open_trades:

        try:

            symbol = trade["symbol"]

            direction = trade["direction"]

            entry = safe_float(
                trade.get("entry"),
                0.0,
            )

            sl = safe_float(
                trade.get("sl"),
                0.0,
            )

            tp = safe_float(
                trade.get("tp"),
                0.0,
            )

            live = get_live_price(symbol)

            if not live or live <= 0:

                remaining.append(trade)

                continue

            trade["current_price"] = live

            trade["current_pnl_pct"] = pct_change(
                entry,
                live,
                direction,
            )

            opened_at = parse_datetime(
                trade.get("opened_at")
            )

            elapsed = (
                now_utc()
                - opened_at
            )

            # =================================================
            # LONG
            # =================================================

            if direction == "LONG":

                if live <= sl:

                    closed = close_trade(
                        trade,
                        sl,
                        "SL",
                    )

                    history.append(closed)

                    continue

                if live >= tp:

                    closed = close_trade(
                        trade,
                        tp,
                        "TP",
                    )

                    history.append(closed)

                    continue

            # =================================================
            # SHORT
            # =================================================

            else:

                if live >= sl:

                    closed = close_trade(
                        trade,
                        sl,
                        "SL",
                    )

                    history.append(closed)

                    continue

                if live <= tp:

                    closed = close_trade(
                        trade,
                        tp,
                        "TP",
                    )

                    history.append(closed)

                    continue

            # =================================================
            # TIMEOUT
            # =================================================

            if elapsed >= timedelta(
                hours=TIMEOUT_HOURS
            ):

                closed = close_trade(
                    trade,
                    live,
                    "TIMEOUT",
                )

                history.append(closed)

                continue

            remaining.append(trade)

        except Exception as e:

            print(
                f"[WARN] Monitor failed "
                f"{trade.get('symbol')}: {e}"
            )

            remaining.append(trade)

    state["open_trades"] = remaining

    state["history"] = history

    return state


# ============================================================
# OPEN SYMBOLS
# ============================================================

def open_symbols(state):
    result = set()

    for trade in state.get(
        "open_trades",
        [],
    ):

        symbol = normalize_symbol(
            trade.get("symbol")
        )

        if symbol:
            result.add(symbol)

    return result


# ============================================================
# SCAN ALL
# ============================================================

def scan_all_markets(
    markets,
    state,
):
    candidates = []

    opened = open_symbols(state)

    print(
        f"[INFO] Markets: {len(markets)}"
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

        if symbol in opened:

            print(
                f"[{index:03d}/{len(markets):03d}] "
                f"{symbol} SKIP OPEN"
            )

            continue

        print(
            f"[{index:03d}/{len(markets):03d}] "
            f"{symbol}"
        )

        market_last = market.get(
            "last",
            0.0,
        )

        result = analyze_candidate(
            symbol,
            market_last,
        )

        if result:

            for candidate in result:

                candidates.append(
                    candidate
                )

                print(
                    f"    "
                    f"{candidate['direction']} "
                    f"Score={candidate['score']:.1f} "
                    f"Risk={candidate['risk_pct']:.2f}%"
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

    long_count = sum(
        1
        for x in open_trades
        if x.get("direction") == "LONG"
    )

    short_count = sum(
        1
        for x in open_trades
        if x.get("direction") == "SHORT"
    )

    total = len(open_trades)

    selected = []

    # Remove duplicate symbol/direction
    unique = {}

    for candidate in candidates:

        key = (
            candidate["symbol"],
            candidate["direction"],
        )

        old = unique.get(key)

        if (
            old is None
            or candidate["score"] > old["score"]
        ):
            unique[key] = candidate

    candidates = list(unique.values())

    longs = sorted(
        [
            x
            for x in candidates
            if x["direction"] == "LONG"
        ],
        key=lambda x: x["score"],
        reverse=True,
    )

    shorts = sorted(
        [
            x
            for x in candidates
            if x["direction"] == "SHORT"
        ],
        key=lambda x: x["score"],
        reverse=True,
    )

    # Best LONG
    for candidate in longs:

        if total >= MAX_OPEN_TRADES:
            break

        if long_count >= MAX_LONG_TRADES:
            break

        symbol = candidate["symbol"]

        if symbol in open_symbols(state):
            continue

        selected.append(candidate)

        long_count += 1
        total += 1

    # Best SHORT
    for candidate in shorts:

        if total >= MAX_OPEN_TRADES:
            break

        if short_count >= MAX_SHORT_TRADES:
            break

        symbol = candidate["symbol"]

        if symbol in open_symbols(state):
            continue

        if any(
            x["symbol"] == symbol
            for x in selected
        ):
            continue

        selected.append(candidate)

        short_count += 1
        total += 1

    return selected


# ============================================================
# ADD SELECTED TRADES
# ============================================================

def add_selected_trades(
    state,
    selected,
):
    for candidate in selected:

        trade = create_trade(
            candidate
        )

        state["open_trades"].append(
            trade
        )

        print(
            f"[OPEN] "
            f"{trade['symbol']} "
            f"{trade['direction']} "
            f"Entry={format_price(trade['entry'])} "
            f"SL={format_price(trade['sl'])} "
            f"TP={format_price(trade['tp'])} "
            f"Score={trade['score']:.1f}"
        )

    return state


# ============================================================
# PERFORMANCE
# ============================================================

def performance(state):
    history = state.get(
        "history",
        [],
    )

    closed = [
        x
        for x in history
        if x.get("status") == "CLOSED"
    ]

    wins = sum(
        1
        for x in closed
        if x.get("result") == "WIN"
    )

    losses = sum(
        1
        for x in closed
        if x.get("result") == "LOSS"
    )

    breakeven = sum(
        1
        for x in closed
        if x.get("result") == "BREAKEVEN"
    )

    timeout = sum(
        1
        for x in closed
        if x.get("result") == "TIMEOUT"
        or x.get("close_reason") == "TIMEOUT"
    )

    # WR excludes timeout and breakeven
    decisive = wins + losses

    if decisive > 0:
        wr = (
            wins
            / decisive
            * 100.0
        )
    else:
        wr = 0.0

    total_pnl = sum(
        safe_float(
            x.get(
                "current_pnl_pct",
                0.0,
            ),
            0.0,
        )
        for x in closed
    )

    return {
        "trades": len(closed),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "timeout": timeout,
        "wr": wr,
        "total_pnl": total_pnl,
    }


# ============================================================
# REPORT
# ============================================================

def generate_report(state):
    open_trades = state.get(
        "open_trades",
        [],
    )

    perf = performance(state)

    lines = []

    lines.append(
        "📡 CRYPTO ICHIMOKU REPORT"
    )

    lines.append(
        f"🕐 {now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        "⏱ 5m CLOSED | TOP 100"
    )

    lines.append(
        "🤖 MTF ICHIMOKU | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append(
            "No open trades"
        )

    else:

        for index, trade in enumerate(
            open_trades,
            start=1,
        ):

            symbol = trade["symbol"]

            direction = trade["direction"]

            if direction == "LONG":

                icon = "🟢"

            else:

                icon = "🔴"

            entry = safe_float(
                trade.get("entry"),
                0.0,
            )

            now_price = safe_float(
                trade.get("current_price"),
                entry,
            )

            sl = safe_float(
                trade.get("sl"),
                0.0,
            )

            tp = safe_float(
                trade.get("tp"),
                0.0,
            )

            # IMPORTANT:
            # SL/TP percentage is calculated from ENTRY,
            # not from current price.
            if direction == "LONG":

                sl_pct = (
                    (entry - sl)
                    / entry
                    * 100.0
                )

                tp_pct = (
                    (tp - entry)
                    / entry
                    * 100.0
                )

            else:

                sl_pct = (
                    (sl - entry)
                    / entry
                    * 100.0
                )

                tp_pct = (
                    (entry - tp)
                    / entry
                    * 100.0
                )

            pnl = pct_change(
                entry,
                now_price,
                direction,
            )

            score = safe_float(
                trade.get("score"),
                0.0,
            )

            risk = safe_float(
                trade.get("risk_pct"),
                sl_pct,
            )

            lines.append(
                f"{index}. "
                f"{symbol} "
                f"{icon} {direction}"
            )

            lines.append(
                f"Entry: {format_price(entry)}"
            )

            lines.append(
                f"Now: {format_price(now_price)}"
            )

            lines.append(
                f"SL: {format_price(sl)} "
                f"({sl_pct:+.2f}%)"
            )

            lines.append(
                f"TP: {format_price(tp)} "
                f"({tp_pct:+.2f}%)"
            )

            lines.append(
                f"P/L: {pnl:+.2f}%"
            )

            lines.append(
                f"Score: {score:.1f}"
            )

            lines.append(
                f"Risk: {risk:.2f}%"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 PERFORMANCE"
    )

    lines.append(
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['breakeven']} | "
        f"⏱ {perf['timeout']}"
    )

    lines.append(
        f"🏆 WR: {perf['wr']:.1f}%"
    )

    lines.append(
        f"💰 Total P/L: "
        f"{perf['total_pnl']:+.2f}%"
    )

    long_count = sum(
        1
        for x in open_trades
        if x.get("direction") == "LONG"
    )

    short_count = sum(
        1
        for x in open_trades
        if x.get("direction") == "SHORT"
    )

    lines.append(
        "⚙️ LIMITS: "
        f"LONG {long_count}/{MAX_LONG_TRADES} | "
        f"SHORT {short_count}/{MAX_SHORT_TRADES} | "
        f"TOTAL {len(open_trades)}/{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"🛡 Minimum Risk: {MIN_RISK_PCT:.2f}%"
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN:
        print(
            "[WARN] TELEGRAM_BOT_TOKEN "
            "is not configured."
        )

        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "[WARN] TELEGRAM_CHAT_ID "
            "is not configured."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        if not data.get("ok", False):

            print(
                "[WARN] Telegram returned "
                f"error: {data}"
            )

            return False

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
        "KRAKEN FUTURES ICHIMOKU TOP RANKER v11.2"
    )

    print(
        "============================================================"
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
        "RR: 1:1"
    )

    print(
        "============================================================"
    )

    state = load_state()

    # --------------------------------------------------------
    # 1. Monitor existing trades
    # --------------------------------------------------------

    print(
        "\n[STEP 1] Monitoring open trades..."
    )

    before = len(
        state.get("open_trades", [])
    )

    state = monitor_open_trades(
        state
    )

    after = len(
        state.get("open_trades", [])
    )

    closed_now = before - after

    if closed_now > 0:

        print(
            f"[INFO] Closed trades this run: "
            f"{closed_now}"
        )

    # Save immediately after monitoring
    save_state(state)

    # --------------------------------------------------------
    # 2. Market list
    # --------------------------------------------------------

    print(
        "\n[STEP 2] Loading markets..."
    )

    markets = build_market_list()

    print(
        f"[INFO] TOP {len(markets)} markets loaded."
    )

    # --------------------------------------------------------
    # 3. Scan only if capacity exists
    # --------------------------------------------------------

    open_count = len(
        state.get("open_trades", [])
    )

    if open_count < MAX_OPEN_TRADES:

        print(
            "\n[STEP 3] Scanning markets..."
        )

        candidates = scan_all_markets(
            markets,
            state,
        )

        print(
            f"[INFO] Candidates found: "
            f"{len(candidates)}"
        )

        # ----------------------------------------------------
        # 4. Select best
        # ----------------------------------------------------

        selected = select_best_trades(
            candidates,
            state,
        )

        print(
            f"[INFO] Selected: "
            f"{len(selected)}"
        )

        # ----------------------------------------------------
        # 5. Open selected
        # ----------------------------------------------------

        state = add_selected_trades(
            state,
            selected,
        )

    else:

        print(
            "\n[STEP 3] Capacity full."
        )

        print(
            f"[INFO] Open trades: "
            f"{open_count}/{MAX_OPEN_TRADES}"
        )

    # --------------------------------------------------------
    # 6. Save state
    # --------------------------------------------------------

    save_state(state)

    # --------------------------------------------------------
    # 7. Report
    # --------------------------------------------------------

    report = generate_report(
        state
    )

    print(
        "\n============================================================"
    )

    print(report)

    print(
        "============================================================"
    )

    # --------------------------------------------------------
    # 8. Telegram
    # --------------------------------------------------------

    print(
        "\n[STEP 4] Sending Telegram report..."
    )

    sent = send_telegram(
        report
    )

    if sent:

        print(
            "[INFO] Telegram report sent."
        )

    else:

        print(
            "[WARN] Telegram report was not sent."
        )

    # Final save
    save_state(state)

    print(
        "\n[INFO] Scanner completed."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[INFO] Interrupted."
        )

    except Exception as e:

        print(
            "\n[FATAL ERROR]"
        )

        print(
            str(e)
        )

        traceback.print_exc()

        raise
