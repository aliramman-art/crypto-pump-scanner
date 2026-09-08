# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v13.0
# ============================================================
# Kraken Futures
# TOP 100 USD perpetual markets
#
# MTF:
#   1H  = Trend
#   30M = Confirmation / Lock
#   15M = Pullback Structure
#   5M  = Pullback + Reversal Trigger / Entry
#
# CLOSED CANDLES ONLY
#
# FEATURES:
#   - Fresh pullback: max 2 closed candles old
#   - 1H / 30M / 15M / 5M MTF confirmation
#   - Ichimoku
#   - Structural SL
#   - RR 1:1
#   - Minimum stop distance
#   - Maximum stop distance
#   - Spread filter
#   - Tick-size aware prices
#   - Fee + slippage accounting
#   - Same-candle TP/SL = AMBIGUOUS
#   - Versioned state/history
#   - Old v12 statistics ignored
#   - Telegram reporting
# ============================================================

import os
import json
import time
import math
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STRATEGY_VERSION = "v13.0"

STATE_FILE = "ichimoku_state_v13.json"
HISTORY_FILE = "ichimoku_trade_history_v13.json"

TOP_N = 100

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

RR = 1.0

TIMEOUT_HOURS = 4

MIN_RISK_PCT = 0.50
MAX_RISK_PCT = 2.00

MIN_SCORE = 78

MIN_TRIGGER_SCORE = 7

PULLBACK_ATR_MULTIPLIER = 1.50
PULLBACK_TOUCH_ATR = 0.35
PULLBACK_MAX_AGE = 2

TRIGGER_BODY_ATR_MIN = 0.10

MAX_SPREAD_PCT = 0.80

MIN_24H_VOLUME = 0.0

FEE_PCT_PER_SIDE = 0.04
SLIPPAGE_PCT_PER_SIDE = 0.02

HTTP_TIMEOUT = 15

# Ichimoku
TENKAN = 9
KIJUN = 26
SENKOU_B = 52
DISPLACEMENT = 26

# Timeframes
TF_5M = "5m"
TF_15M = "15m"
TF_30M = "30m"
TF_1H = "1h"

TF_SECONDS = {
    TF_5M: 5 * 60,
    TF_15M: 15 * 60,
    TF_30M: 30 * 60,
    TF_1H: 60 * 60,
}

# MTF weights
W_1H = 0.50
W_30M = 0.30
W_15M = 0.20


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Kraken-Ichimoku-v13"
})


def http_get(path, params=None):
    url = BASE_URL + path

    try:
        r = SESSION.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT
        )
        r.raise_for_status()
        return r.json()

    except Exception as e:
        print(f"[HTTP ERROR] {path}: {e}")
        return None


# ============================================================
# TIME
# ============================================================

def now_ts():
    return int(time.time())


def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# JSON STATE
# ============================================================

def load_json_file(filename, default):
    if not os.path.exists(filename):
        return default

    try:
        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"[JSON LOAD ERROR] {filename}: {e}")
        return default


def save_json_file(filename, data):
    tmp = filename + ".tmp"

    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(tmp, filename)

    except Exception as e:
        print(f"[JSON SAVE ERROR] {filename}: {e}")


def load_state():
    default = {
        "strategy_version": STRATEGY_VERSION,
        "open_trades": []
    }

    data = load_json_file(STATE_FILE, default)

    if not isinstance(data, dict):
        return default

    if data.get("strategy_version") != STRATEGY_VERSION:
        print("[STATE] Old strategy state ignored.")
        return default

    if "open_trades" not in data:
        data["open_trades"] = []

    return data


def save_state(state):
    state["strategy_version"] = STRATEGY_VERSION
    save_json_file(STATE_FILE, state)


def load_history():
    default = {
        "strategy_version": STRATEGY_VERSION,
        "trades": []
    }

    data = load_json_file(HISTORY_FILE, default)

    if not isinstance(data, dict):
        return default

    if data.get("strategy_version") != STRATEGY_VERSION:
        print("[HISTORY] Old strategy history ignored.")
        return default

    if "trades" not in data:
        data["trades"] = []

    return data


def save_history(history):
    history["strategy_version"] = STRATEGY_VERSION
    save_json_file(HISTORY_FILE, history)


# ============================================================
# MARKETS
# ============================================================

def get_instruments():
    data = http_get("/derivatives/api/v3/instruments")

    if not data:
        return []

    instruments = data.get("instruments", [])

    if not instruments and isinstance(data.get("data"), list):
        instruments = data.get("data")

    return instruments or []


def get_tickers():
    data = http_get("/derivatives/api/v3/tickers")

    if not data:
        return []

    if isinstance(data, dict):
        return (
            data.get("tickers")
            or data.get("data")
            or []
        )

    return data


def normalize_symbol(symbol):
    return str(symbol or "").upper()


def ticker_volume(ticker):
    candidates = [
        "vol24h",
        "volume24h",
        "volume",
        "volume_24h"
    ]

    for key in candidates:
        value = ticker.get(key)

        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    return 0.0


def ticker_price(ticker):
    for key in [
        "last",
        "lastPrice",
        "price",
        "markPrice"
    ]:
        value = ticker.get(key)

        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    return None


def ticker_bid(ticker):
    for key in [
        "bid",
        "bidPrice"
    ]:
        value = ticker.get(key)

        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    return None


def ticker_ask(ticker):
    for key in [
        "ask",
        "askPrice"
    ]:
        value = ticker.get(key)

        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    return None


def calculate_spread_pct(ticker):
    bid = ticker_bid(ticker)
    ask = ticker_ask(ticker)

    if bid is None or ask is None:
        return 0.0

    if bid <= 0:
        return 999.0

    return ((ask - bid) / bid) * 100.0


def build_market_metadata():
    instruments = get_instruments()
    tickers = get_tickers()

    ticker_map = {}

    for t in tickers:
        symbol = normalize_symbol(
            t.get("symbol")
            or t.get("instrument")
            or t.get("pair")
        )

        if symbol:
            ticker_map[symbol] = t

    markets = []

    for inst in instruments:

        symbol = normalize_symbol(
            inst.get("symbol")
            or inst.get("instrument")
        )

        if not symbol:
            continue

        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        quote = str(
            inst.get("quoteCurrency")
            or inst.get("quote")
            or ""
        ).upper()

        base = str(
            inst.get("baseCurrency")
            or inst.get("base")
            or ""
        ).upper()

        # Kraken futures perpetual USD markets
        is_perpetual = (
            "PERP" in symbol
            or "PI_" in symbol
            or "PF_" in symbol
        )

        if quote and quote != "USD":
            continue

        if not is_perpetual:
            continue

        volume = ticker_volume(ticker)

        if volume < MIN_24H_VOLUME:
            continue

        spread = calculate_spread_pct(ticker)

        if spread > MAX_SPREAD_PCT:
            continue

        markets.append({
            "symbol": symbol,
            "base": base,
            "quote": quote,
            "ticker": ticker,
            "volume": volume,
            "spread_pct": spread,
            "instrument": inst
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# TICK SIZE
# ============================================================

def get_tick_size(instrument):
    if not instrument:
        return None

    for key in [
        "tickSize",
        "priceIncrement",
        "quoteIncrement"
    ]:
        value = instrument.get(key)

        try:
            if value is not None:
                v = float(value)
                if v > 0:
                    return v
        except Exception:
            pass

    return None


def decimal_places_from_tick(tick):
    if tick is None or tick <= 0:
        return 8

    text = f"{tick:.12f}".rstrip("0")

    if "." not in text:
        return 0

    return len(text.split(".")[1])


def round_to_tick(price, tick_size=None):
    if price is None:
        return None

    if tick_size and tick_size > 0:
        value = round(
            price / tick_size
        ) * tick_size

        decimals = decimal_places_from_tick(
            tick_size
        )

        return round(value, decimals)

    if price >= 1000:
        decimals = 2
    elif price >= 1:
        decimals = 4
    elif price >= 0.01:
        decimals = 6
    else:
        decimals = 10

    return round(price, decimals)


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution, limit=150):
    seconds = TF_SECONDS[resolution]

    data = http_get(
        f"/api/charts/v1/trade/{symbol}/{resolution}"
    )

    if not data:
        return []

    raw = data

    if isinstance(data, dict):
        raw = (
            data.get("candles")
            or data.get("data")
            or data.get("results")
            or []
        )

    if not isinstance(raw, list):
        return []

    candles = []

    current = now_ts()

    for c in raw:

        try:
            if isinstance(c, dict):

                ts = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("t")
                )

                o = (
                    c.get("open")
                    or c.get("o")
                )

                h = (
                    c.get("high")
                    or c.get("h")
                )

                l = (
                    c.get("low")
                    or c.get("l")
                )

                close = (
                    c.get("close")
                    or c.get("c")
                )

                volume = (
                    c.get("volume")
                    or c.get("v")
                    or 0
                )

            else:
                if len(c) < 5:
                    continue

                ts = c[0]
                o = c[1]
                h = c[2]
                l = c[3]
                close = c[4]
                volume = c[5] if len(c) > 5 else 0

            ts = float(ts)

            # milliseconds
            if ts > 10_000_000_000:
                ts /= 1000

            ts = int(ts)

            # CLOSED CANDLES ONLY
            if ts + seconds > current:
                continue

            candles.append({
                "time": ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(close),
                "volume": float(volume or 0)
            })

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles[-limit:]


# ============================================================
# ATR
# ============================================================

def true_range(candle, previous_close):
    if previous_close is None:
        return (
            candle["high"] -
            candle["low"]
        )

    return max(
        candle["high"] - candle["low"],
        abs(candle["high"] - previous_close),
        abs(candle["low"] - previous_close)
    )


def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        prev_close = candles[i - 1]["close"]

        trs.append(
            true_range(
                candles[i],
                prev_close
            )
        )

    if len(trs) < period:
        return None

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# ICHIMOKU
# ============================================================

def midpoint(highs, lows):
    if not highs or not lows:
        return None

    return (
        max(highs) +
        min(lows)
    ) / 2.0


def ichimoku_values(candles):
    if len(candles) < SENKOU_B + DISPLACEMENT:
        return None

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    closes = [
        x["close"]
        for x in candles
    ]

    tenkan = midpoint(
        highs[-TENKAN:],
        lows[-TENKAN:]
    )

    kijun = midpoint(
        highs[-KIJUN:],
        lows[-KIJUN:]
    )

    span_b = midpoint(
        highs[-SENKOU_B:],
        lows[-SENKOU_B:]
    )

    span_a = (
        tenkan +
        kijun
    ) / 2.0

    close = closes[-1]

    cloud_top = max(
        span_a,
        span_b
    )

    cloud_bottom = min(
        span_a,
        span_b
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "close": close
    }


def ichimoku_score(candles):
    values = ichimoku_values(candles)

    if not values:
        return 0, None

    close = values["close"]
    tenkan = values["tenkan"]
    kijun = values["kijun"]
    cloud_top = values["cloud_top"]
    cloud_bottom = values["cloud_bottom"]

    score = 0

    # Price vs cloud
    if close > cloud_top:
        score += 3
    elif close < cloud_bottom:
        score -= 3

    # Tenkan vs Kijun
    if tenkan > kijun:
        score += 2
    elif tenkan < kijun:
        score -= 2

    # Price vs Kijun
    if close > kijun:
        score += 2
    elif close < kijun:
        score -= 2

    # Price vs Tenkan
    if close > tenkan:
        score += 1
    elif close < tenkan:
        score -= 1

    # Cloud direction
    if values["span_a"] > values["span_b"]:
        score += 1
    elif values["span_a"] < values["span_b"]:
        score -= 1

    score = max(-10, min(10, score))

    return score, values


# ============================================================
# CANDLE STRUCTURE
# ============================================================

def candle_body(c):
    return abs(
        c["close"] - c["open"]
    )


def candle_range(c):
    return max(
        c["high"] - c["low"],
        1e-12
    )


def upper_wick(c):
    return (
        c["high"] -
        max(c["open"], c["close"])
    )


def lower_wick(c):
    return (
        min(c["open"], c["close"]) -
        c["low"]
    )


def bullish(c):
    return c["close"] > c["open"]


def bearish(c):
    return c["close"] < c["open"]


def bullish_engulfing(prev, cur):
    if not bearish(prev):
        return False

    if not bullish(cur):
        return False

    return (
        cur["open"] <= prev["close"]
        and
        cur["close"] >= prev["open"]
    )


def bearish_engulfing(prev, cur):
    if not bullish(prev):
        return False

    if not bearish(cur):
        return False

    return (
        cur["open"] >= prev["close"]
        and
        cur["close"] <= prev["open"]
    )


def bullish_pinbar(c):
    body = candle_body(c)
    lower = lower_wick(c)
    upper = upper_wick(c)
    rng = candle_range(c)

    return (
        lower >= body * 2
        and
        lower >= upper * 1.5
        and
        (body / rng) <= 0.45
    )


def bearish_pinbar(c):
    body = candle_body(c)
    lower = lower_wick(c)
    upper = upper_wick(c)
    rng = candle_range(c)

    return (
        upper >= body * 2
        and
        upper >= lower * 1.5
        and
        (body / rng) <= 0.45
    )


def bullish_reversal(prev, cur):
    return (
        bullish_engulfing(prev, cur)
        or
        bullish_pinbar(cur)
    )


def bearish_reversal(prev, cur):
    return (
        bearish_engulfing(prev, cur)
        or
        bearish_pinbar(cur)
    )


# ============================================================
# PULLBACK
# ============================================================

def candle_touches_level(candle, level, atr):
    if level is None or atr is None:
        return False

    tolerance = atr * PULLBACK_TOUCH_ATR

    return (
        candle["low"] <= level + tolerance
        and
        candle["high"] >= level - tolerance
    )


def five_minute_pullback(candles, direction):
    if len(candles) < 40:
        return None

    atr = calculate_atr(candles, 14)

    if atr is None or atr <= 0:
        return None

    current = candles[-1]

    ich = ichimoku_values(candles)

    if not ich:
        return None

    tenkan = ich["tenkan"]
    kijun = ich["kijun"]

    # Only the last PULLBACK_MAX_AGE candles
    # before the trigger candle are allowed.
    start = max(
        0,
        len(candles) - 1 - PULLBACK_MAX_AGE
    )

    candidates = candles[start:-1]

    for c in reversed(candidates):

        touch_tenkan = candle_touches_level(
            c,
            tenkan,
            atr
        )

        touch_kijun = candle_touches_level(
            c,
            kijun,
            atr
        )

        touch = (
            touch_tenkan
            or
            touch_kijun
        )

        if not touch:
            continue

        nearest = min(
            abs(c["close"] - tenkan),
            abs(c["close"] - kijun)
        )

        distance_ok = (
            nearest <=
            atr * PULLBACK_ATR_MULTIPLIER
        )

        if not distance_ok:
            continue

        if direction == "LONG":

            if current["close"] <= current["open"]:
                continue

            recovery = (
                current["close"] >
                c["close"]
            )

            reclaim = (
                current["close"] >
                tenkan
            )

            rejection = (
                bullish_reversal(
                    candles[-2],
                    current
                )
                or
                lower_wick(current) >
                candle_body(current)
            )

            structure = (
                current["close"] >
                candles[-2]["high"]
                or
                current["close"] >
                tenkan
            )

            body_ok = (
                candle_body(current) >=
                atr * TRIGGER_BODY_ATR_MIN
            )

            valid = (
                recovery
                and reclaim
                and rejection
                and structure
                and body_ok
            )

        else:

            if current["close"] >= current["open"]:
                continue

            recovery = (
                current["close"] <
                c["close"]
            )

            reclaim = (
                current["close"] <
                tenkan
            )

            rejection = (
                bearish_reversal(
                    candles[-2],
                    current
                )
                or
                upper_wick(current) >
                candle_body(current)
            )

            structure = (
                current["close"] <
                candles[-2]["low"]
                or
                current["close"] <
                tenkan
            )

            body_ok = (
                candle_body(current) >=
                atr * TRIGGER_BODY_ATR_MIN
            )

            valid = (
                recovery
                and reclaim
                and rejection
                and structure
                and body_ok
            )

        if not valid:
            continue

        return {
            "pullback_candle": c,
            "trigger_candle": current,
            "atr": atr,
            "tenkan": tenkan,
            "kijun": kijun,
            "touch_tenkan": touch_tenkan,
            "touch_kijun": touch_kijun,
            "recovery": recovery,
            "reclaim": reclaim,
            "rejection": rejection,
            "structure": structure,
            "body_ok": body_ok,
            "distance": nearest
        }

    return None


# ============================================================
# TREND STRUCTURE
# ============================================================

def trend_structure(candles, direction):
    if len(candles) < 6:
        return False

    recent = candles[-5:]

    highs = [
        c["high"]
        for c in recent
    ]

    lows = [
        c["low"]
        for c in recent
    ]

    if direction == "LONG":
        return (
            highs[-1] >= max(highs[:-1])
            and
            lows[-1] >= min(lows[:-1])
        )

    return (
        lows[-1] <= min(lows[:-1])
        and
        highs[-1] <= max(highs[:-1])
    )


# ============================================================
# TRIGGER SCORE
# ============================================================

def trigger_score(candles, direction, pullback):
    if not pullback:
        return 0

    current = pullback["trigger_candle"]

    if len(candles) < 2:
        return 0

    prev = candles[-2]

    score = 0

    # Fresh pullback
    score += 1

    # Reversal pattern
    if direction == "LONG":
        if bullish_reversal(prev, current):
            score += 2
    else:
        if bearish_reversal(prev, current):
            score += 2

    # Rejection
    if pullback["rejection"]:
        score += 1

    # Reclaim
    if pullback["reclaim"]:
        score += 1

    # Structure break
    if direction == "LONG":
        if current["close"] > prev["high"]:
            score += 2
    else:
        if current["close"] < prev["low"]:
            score += 2

    # Body
    if pullback["body_ok"]:
        score += 1

    return min(score, 10)


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def structural_levels(candles_5m, candles_15m, direction, tick_size):
    if len(candles_5m) < 10:
        return None

    if len(candles_15m) < 4:
        return None

    recent_5 = candles_5m[-10:]
    recent_15 = candles_15m[-4:]

    if direction == "LONG":

        structural_low = min(
            min(c["low"] for c in recent_5),
            min(c["low"] for c in recent_15)
        )

        entry = candles_5m[-1]["close"]

        sl = structural_low

        if sl >= entry:
            return None

        risk = entry - sl

        tp = entry + (
            risk * RR
        )

    else:

        structural_high = max(
            max(c["high"] for c in recent_5),
            max(c["high"] for c in recent_15)
        )

        entry = candles_5m[-1]["close"]

        sl = structural_high

        if sl <= entry:
            return None

        risk = sl - entry

        tp = entry - (
            risk * RR
        )

    sl = round_to_tick(
        sl,
        tick_size
    )

    tp = round_to_tick(
        tp,
        tick_size
    )

    entry = round_to_tick(
        entry,
        tick_size
    )

    if direction == "LONG":

        if sl >= entry:
            return None

        if tp <= entry:
            return None

    else:

        if sl <= entry:
            return None

        if tp >= entry:
            return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_price": abs(entry - sl),
        "risk_pct": (
            abs(entry - sl) /
            entry *
            100
        )
    }


def risk_quality(levels):
    if not levels:
        return False

    risk_pct = levels["risk_pct"]

    return (
        risk_pct >= MIN_RISK_PCT
        and
        risk_pct <= MAX_RISK_PCT
    )


# ============================================================
# MTF ANALYSIS
# ============================================================

def get_mtf_data(symbol):
    c5 = get_candles(
        symbol,
        TF_5M,
        160
    )

    c15 = get_candles(
        symbol,
        TF_15M,
        160
    )

    c30 = get_candles(
        symbol,
        TF_30M,
        160
    )

    c1h = get_candles(
        symbol,
        TF_1H,
        160
    )

    if (
        len(c5) < 80
        or len(c15) < 80
        or len(c30) < 80
        or len(c1h) < 80
    ):
        return None

    s5, i5 = ichimoku_score(c5)
    s15, i15 = ichimoku_score(c15)
    s30, i30 = ichimoku_score(c30)
    s1h, i1h = ichimoku_score(c1h)

    return {
        "5m": {
            "candles": c5,
            "score": s5,
            "ich": i5
        },
        "15m": {
            "candles": c15,
            "score": s15,
            "ich": i15
        },
        "30m": {
            "candles": c30,
            "score": s30,
            "ich": i30
        },
        "1h": {
            "candles": c1h,
            "score": s1h,
            "ich": i1h
        }
    }


# ============================================================
# TREND SCORE
# ============================================================

def calculate_trend_score(s1h, s30, s15):
    return (
        s1h * W_1H
        +
        s30 * W_30M
        +
        s15 * W_15M
    )


# ============================================================
# REVERSAL SCORE
# ============================================================

def calculate_reversal_score(
    s1h,
    s30,
    s15,
    s5
):
    return (
        (s15 - s1h)
        +
        (s5 - s30)
    )


# ============================================================
# FINAL RANK SCORE
# ============================================================

def final_rank_score(
    s1h,
    s30,
    s15,
    s5,
    trigger
):
    # Normalize each component from -10..10
    # into 0..100 contribution.

    trend_component = (
        max(0, s1h) / 10
    ) * 35

    confirmation_component = (
        max(0, s30) / 10
    ) * 25

    structure_component = (
        max(0, s15) / 10
    ) * 20

    trigger_component = (
        max(0, trigger) / 10
    ) * 15

    five_component = (
        max(0, s5) / 10
    ) * 5

    total = (
        trend_component
        +
        confirmation_component
        +
        structure_component
        +
        trigger_component
        +
        five_component
    )

    return round(
        max(0, min(100, total)),
        2
    )


# ============================================================
# CANDIDATE ANALYSIS
# ============================================================

def analyze_candidate(market):
    symbol = market["symbol"]

    mtf = get_mtf_data(symbol)

    if not mtf:
        return None

    s1h = mtf["1h"]["score"]
    s30 = mtf["30m"]["score"]
    s15 = mtf["15m"]["score"]
    s5 = mtf["5m"]["score"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    long_trend_ok = (
        s1h >= 4
        and
        s30 >= 3
        and
        s15 >= 2
        and
        s5 >= 2
    )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    short_trend_ok = (
        s1h <= -4
        and
        s30 <= -3
        and
        s15 <= -2
        and
        s5 <= -2
    )

    if not long_trend_ok and not short_trend_ok:
        return None

    candidates = []

    if long_trend_ok:

        pullback = five_minute_pullback(
            mtf["5m"]["candles"],
            "LONG"
        )

        if pullback:

            trigger = trigger_score(
                mtf["5m"]["candles"],
                "LONG",
                pullback
            )

            if trigger >= MIN_TRIGGER_SCORE:

                trend_score = calculate_trend_score(
                    s1h,
                    s30,
                    s15
                )

                reversal_score = calculate_reversal_score(
                    s1h,
                    s30,
                    s15,
                    s5
                )

                rank = final_rank_score(
                    s1h,
                    s30,
                    s15,
                    s5,
                    trigger
                )

                if (
                    trend_score >= 5
                    and
                    rank >= MIN_SCORE
                ):

                    candidates.append({
                        "direction": "LONG",
                        "symbol": symbol,
                        "score": rank,
                        "trend_score": trend_score,
                        "reversal_score": reversal_score,
                        "trigger_score": trigger,
                        "mtf": mtf,
                        "pullback": pullback,
                        "market": market
                    })

    if short_trend_ok:

        pullback = five_minute_pullback(
            mtf["5m"]["candles"],
            "SHORT"
        )

        if pullback:

            trigger = trigger_score(
                mtf["5m"]["candles"],
                "SHORT",
                pullback
            )

            if trigger >= MIN_TRIGGER_SCORE:

                trend_score = calculate_trend_score(
                    s1h,
                    s30,
                    s15
                )

                reversal_score = calculate_reversal_score(
                    s1h,
                    s30,
                    s15,
                    s5
                )

                rank = final_rank_score(
                    abs(s1h),
                    abs(s30),
                    abs(s15),
                    abs(s5),
                    trigger
                )

                if (
                    trend_score <= -5
                    and
                    rank >= MIN_SCORE
                ):

                    candidates.append({
                        "direction": "SHORT",
                        "symbol": symbol,
                        "score": rank,
                        "trend_score": trend_score,
                        "reversal_score": reversal_score,
                        "trigger_score": trigger,
                        "mtf": mtf,
                        "pullback": pullback,
                        "market": market
                    })

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    candidate = candidates[0]

    tick_size = get_tick_size(
        market.get("instrument")
    )

    levels = structural_levels(
        mtf["5m"]["candles"],
        mtf["15m"]["candles"],
        candidate["direction"],
        tick_size
    )

    if not levels:
        return None

    if not risk_quality(levels):
        return None

    candidate["levels"] = levels
    candidate["tick_size"] = tick_size

    return candidate


# ============================================================
# TRADE KEY
# ============================================================

def setup_key(candidate):
    trigger = candidate["pullback"]["trigger_candle"]

    return (
        f'{candidate["symbol"]}_'
        f'{candidate["direction"]}_'
        f'{trigger["time"]}'
    )


# ============================================================
# OPEN TRADE CHECKS
# ============================================================

def count_direction(open_trades, direction):
    return sum(
        1
        for t in open_trades
        if t.get("direction") == direction
    )


def symbol_has_open_trade(open_trades, symbol):
    return any(
        t.get("symbol") == symbol
        for t in open_trades
    )


def create_trade(candidate):
    levels = candidate["levels"]

    return {
        "strategy_version": STRATEGY_VERSION,
        "id": setup_key(candidate),
        "symbol": candidate["symbol"],
        "direction": candidate["direction"],
        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],
        "risk_pct": levels["risk_pct"],
        "score": candidate["score"],
        "trend_score": candidate["trend_score"],
        "reversal_score": candidate["reversal_score"],
        "trigger_score": candidate["trigger_score"],
        "opened_at": now_ts(),
        "opened_at_iso": iso_now(),
        "signal_candle_time": candidate["pullback"]["trigger_candle"]["time"],
        "status": "OPEN",
        "last_price": levels["entry"],
        "pnl_pct": 0.0
    }


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(direction, entry, exit_price):
    if entry <= 0:
        return 0.0

    if direction == "LONG":

        raw = (
            (exit_price - entry) /
            entry *
            100
        )

    else:

        raw = (
            (entry - exit_price) /
            entry *
            100
        )

    costs = 2 * (
        FEE_PCT_PER_SIDE +
        SLIPPAGE_PCT_PER_SIDE
    )

    return raw - costs


# ============================================================
# MONITOR
# ============================================================

def check_closed_candle_exit(trade, candles):
    if not candles:
        return None

    direction = trade["direction"]

    entry = float(trade["entry"])
    sl = float(trade["sl"])
    tp = float(trade["tp"])

    # Start after entry candle
    entry_time = int(
        trade.get("signal_candle_time", 0)
    )

    relevant = [
        c for c in candles
        if c["time"] > entry_time
    ]

    for c in relevant:

        high = c["high"]
        low = c["low"]

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl and hit_tp:

                exit_price = c["close"]

                return {
                    "status": "AMBIGUOUS",
                    "exit_price": exit_price,
                    "exit_time": c["time"],
                    "reason": "TP_AND_SL_SAME_CANDLE"
                }

            if hit_tp:

                return {
                    "status": "TP",
                    "exit_price": tp,
                    "exit_time": c["time"],
                    "reason": "TP"
                }

            if hit_sl:

                return {
                    "status": "SL",
                    "exit_price": sl,
                    "exit_time": c["time"],
                    "reason": "SL"
                }

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl and hit_tp:

                exit_price = c["close"]

                return {
                    "status": "AMBIGUOUS",
                    "exit_price": exit_price,
                    "exit_time": c["time"],
                    "reason": "TP_AND_SL_SAME_CANDLE"
                }

            if hit_tp:

                return {
                    "status": "TP",
                    "exit_price": tp,
                    "exit_time": c["time"],
                    "reason": "TP"
                }

            if hit_sl:

                return {
                    "status": "SL",
                    "exit_price": sl,
                    "exit_time": c["time"],
                    "reason": "SL"
                }

    return None


def monitor_open_trades(state, history):
    open_trades = state.get(
        "open_trades",
        []
    )

    if not open_trades:
        return False

    changed = False

    remaining = []

    for trade in open_trades:

        symbol = trade["symbol"]

        candles = get_candles(
            symbol,
            TF_5M,
            120
        )

        result = check_closed_candle_exit(
            trade,
            candles
        )

        # ----------------------------------------------------
        # Closed candle exit
        # ----------------------------------------------------

        if result:

            exit_price = result["exit_price"]

            pnl = calculate_trade_pnl(
                trade["direction"],
                float(trade["entry"]),
                float(exit_price)
            )

            trade["status"] = result["status"]
            trade["exit_price"] = exit_price
            trade["exit_time"] = result["exit_time"]
            trade["exit_time_iso"] = (
                datetime.fromtimestamp(
                    result["exit_time"],
                    timezone.utc
                ).strftime(
                    "%Y-%m-%d %H:%M:%S UTC"
                )
            )
            trade["reason"] = result["reason"]
            trade["pnl_pct"] = pnl

            history["trades"].append(
                trade.copy()
            )

            changed = True

            print(
                f'[CLOSE] {symbol} '
                f'{trade["direction"]} '
                f'{result["status"]} '
                f'PnL={pnl:.2f}%'
            )

            continue

        # ----------------------------------------------------
        # Live ticker fallback
        # ----------------------------------------------------

        tickers = get_tickers()

        live_ticker = None

        for t in tickers:
            tsymbol = normalize_symbol(
                t.get("symbol")
                or t.get("instrument")
                or t.get("pair")
            )

            if tsymbol == symbol:
                live_ticker = t
                break

        live_price = (
            ticker_price(live_ticker)
            if live_ticker
            else None
        )

        if live_price is not None:

            trade["last_price"] = live_price

            trade["pnl_pct"] = calculate_trade_pnl(
                trade["direction"],
                float(trade["entry"]),
                float(live_price)
            )

        # ----------------------------------------------------
        # Timeout
        # ----------------------------------------------------

        age_hours = (
            now_ts() -
            int(trade["opened_at"])
        ) / 3600.0

        if age_hours >= TIMEOUT_HOURS:

            exit_price = (
                live_price
                if live_price is not None
                else trade.get(
                    "last_price",
                    trade["entry"]
                )
            )

            pnl = calculate_trade_pnl(
                trade["direction"],
                float(trade["entry"]),
                float(exit_price)
            )

            trade["status"] = "TIMEOUT"
            trade["exit_price"] = exit_price
            trade["exit_time"] = now_ts()
            trade["exit_time_iso"] = iso_now()
            trade["reason"] = "TIMEOUT"
            trade["pnl_pct"] = pnl

            history["trades"].append(
                trade.copy()
            )

            changed = True

            print(
                f'[TIMEOUT] {symbol} '
                f'PnL={pnl:.2f}%'
            )

            continue

        remaining.append(trade)

    state["open_trades"] = remaining

    return changed


# ============================================================
# PERFORMANCE
# ============================================================

def performance(history):
    trades = history.get(
        "trades",
        []
    )

    if not trades:
        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "timeouts": 0,
            "ambiguous": 0,
            "wr": 0.0,
            "pnl": 0.0
        }

    wins = 0
    losses = 0
    timeouts = 0
    ambiguous = 0
    pnl = 0.0

    for t in trades:

        status = t.get("status")
        value = float(
            t.get("pnl_pct", 0)
        )

        pnl += value

        if status == "TP":
            wins += 1

        elif status == "SL":
            losses += 1

        elif status == "TIMEOUT":
            timeouts += 1

        elif status == "AMBIGUOUS":
            ambiguous += 1

    closed_directional = (
        wins + losses
    )

    wr = (
        wins /
        closed_directional *
        100
        if closed_directional > 0
        else 0.0
    )

    return {
        "total": len(trades),
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "ambiguous": ambiguous,
        "wr": wr,
        "pnl": pnl
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN:
        print("[TELEGRAM] BOT TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[TELEGRAM] CHAT ID missing")
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
            timeout=HTTP_TIMEOUT
        )

        if r.ok:
            return True

        print(
            "[TELEGRAM ERROR]",
            r.text
        )

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

    return False


# ============================================================
# REPORT
# ============================================================

def format_trade(trade):
    direction = trade["direction"]

    emoji = (
        "🟢"
        if direction == "LONG"
        else
        "🔴"
    )

    pnl = float(
        trade.get("pnl_pct", 0)
    )

    return (
        f'{emoji} {trade["symbol"]} '
        f'{direction}\n'
        f'Entry: {trade["entry"]}\n'
        f'SL: {trade["sl"]}\n'
        f'TP: {trade["tp"]}\n'
        f'Score: {trade.get("score", 0):.1f}\n'
        f'P/L: {pnl:+.2f}%'
    )


def build_report(state, history, candidates):
    perf = performance(history)

    lines = []

    lines.append(
        "📡 CRYPTO ICHIMOKU REPORT"
    )

    lines.append(
        f"🕐 {iso_now()}"
    )

    lines.append(
        "⏱ 5m CLOSED | TOP 100"
    )

    lines.append(
        f"🤖 MTF ICHIMOKU {STRATEGY_VERSION} | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    open_trades = state.get(
        "open_trades",
        []
    )

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})"
    )

    if open_trades:

        for i, trade in enumerate(
            open_trades,
            1
        ):

            pnl = float(
                trade.get("pnl_pct", 0)
            )

            emoji = (
                "🟢"
                if pnl >= 0
                else
                "🔴"
            )

            lines.append(
                f'{i}. {trade["symbol"]} '
                f'{emoji} {trade["direction"]} '
                f'{pnl:+.2f}%'
            )

    else:

        lines.append(
            "None"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if candidates:

        for c in candidates:

            d = c["direction"]

            emoji = (
                "🟢"
                if d == "LONG"
                else
                "🔴"
            )

            levels = c["levels"]

            lines.append(
                f'{emoji} {c["symbol"]} {d}\n'
                f'Entry: {levels["entry"]}\n'
                f'SL: {levels["sl"]} '
                f'({levels["risk_pct"]:.2f}%)\n'
                f'TP: {levels["tp"]}\n'
                f'Score: {c["score"]:.1f}\n'
                f'Trigger: {c["trigger_score"]}/10\n'
                f'Trend: {c["trend_score"]:+.2f}'
            )

    else:

        lines.append(
            "No qualified setup"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "📊 PERFORMANCE"
    )

    lines.append(
        f'Trades: {perf["total"]} | '
        f'W: {perf["wins"]} | '
        f'L: {perf["losses"]} | '
        f'T: {perf["timeouts"]} | '
        f'A: {perf["ambiguous"]}'
    )

    lines.append(
        f'WR: {perf["wr"]:.1f}% | '
        f'P/L: {perf["pnl"]:+.2f}%'
    )

    return "\n".join(lines)


# ============================================================
# SCAN
# ============================================================

def scan_markets(markets, state):
    candidates = []

    for index, market in enumerate(
        markets,
        1
    ):

        symbol = market["symbol"]

        print(
            f"[SCAN] {index}/{len(markets)} "
            f"{symbol}"
        )

        # Existing symbol trade
        if symbol_has_open_trade(
            state["open_trades"],
            symbol
        ):
            continue

        # Capacity
        if len(
            state["open_trades"]
        ) >= MAX_OPEN_TRADES:
            break

        try:

            candidate = analyze_candidate(
                market
            )

            if not candidate:
                continue

            direction = candidate[
                "direction"
            ]

            if direction == "LONG":

                if count_direction(
                    state["open_trades"],
                    "LONG"
                ) >= MAX_LONG_TRADES:
                    continue

            else:

                if count_direction(
                    state["open_trades"],
                    "SHORT"
                ) >= MAX_SHORT_TRADES:
                    continue

            candidates.append(
                candidate
            )

        except Exception as e:

            print(
                f"[ANALYZE ERROR] "
                f"{symbol}: {e}"
            )

    candidates.sort(
        key=lambda x: x["score"],
        reverse=True
    )

    return candidates


# ============================================================
# OPEN BEST CANDIDATES
# ============================================================

def open_candidates(
    state,
    candidates
):
    opened = []

    used_symbols = set(
        t["symbol"]
        for t in state["open_trades"]
    )

    for candidate in candidates:

        if len(
            state["open_trades"]
        ) >= MAX_OPEN_TRADES:
            break

        symbol = candidate["symbol"]

        if symbol in used_symbols:
            continue

        direction = candidate["direction"]

        if direction == "LONG":

            if count_direction(
                state["open_trades"],
                "LONG"
            ) >= MAX_LONG_TRADES:
                continue

        else:

            if count_direction(
                state["open_trades"],
                "SHORT"
            ) >= MAX_SHORT_TRADES:
                continue

        trade = create_trade(
            candidate
        )

        state["open_trades"].append(
            trade
        )

        used_symbols.add(
            symbol
        )

        opened.append(
            trade
        )

        print(
            f'[OPEN] {symbol} '
            f'{direction} '
            f'Entry={trade["entry"]} '
            f'SL={trade["sl"]} '
            f'TP={trade["tp"]} '
            f'Score={trade["score"]:.1f}'
        )

    return opened


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "============================================================"
    )

    print(
        f"KRAKEN FUTURES ICHIMOKU TOP RANKER {STRATEGY_VERSION}"
    )

    print(
        "============================================================"
    )

    print(
        f"[TIME] {iso_now()}"
    )

    state = load_state()
    history = load_history()

    # --------------------------------------------------------
    # Monitor existing trades
    # --------------------------------------------------------

    print(
        f'[INFO] Open trades: '
        f'{len(state["open_trades"])}'
    )

    monitor_changed = monitor_open_trades(
        state,
        history
    )

    if monitor_changed:

        save_state(
            state
        )

        save_history(
            history
        )

    # --------------------------------------------------------
    # Markets
    # --------------------------------------------------------

    markets = build_market_metadata()

    print(
        f"[INFO] Markets: {len(markets)}"
    )

    if not markets:

        print(
            "[ERROR] No markets found."
        )

        report = build_report(
            state,
            history,
            []
        )

        telegram_send(report)

        return

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    candidates = scan_markets(
        markets,
        state
    )

    print(
        f"[INFO] Candidates: "
        f"{len(candidates)}"
    )

    # --------------------------------------------------------
    # Open
    # --------------------------------------------------------

    opened = open_candidates(
        state,
        candidates
    )

    if opened:

        save_state(
            state
        )

        telegram_send(
            "🚨 NEW ICHIMOKU SIGNAL\n\n"
            +
            "\n\n".join(
                format_trade(t)
                for t in opened
            )
        )

    # --------------------------------------------------------
    # Final report
    # --------------------------------------------------------

    save_state(
        state
    )

    save_history(
        history
    )

    report = build_report(
        state,
        history,
        candidates
    )

    print()
    print(report)
    print()

    telegram_send(
        report
    )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    perf = performance(
        history
    )

    print(
        "============================================================"
    )

    print(
        f'[PERFORMANCE] '
        f'Trades={perf["total"]} '
        f'Wins={perf["wins"]} '
        f'Losses={perf["losses"]} '
        f'Timeouts={perf["timeouts"]} '
        f'Ambiguous={perf["ambiguous"]} '
        f'WR={perf["wr"]:.1f}% '
        f'PnL={perf["pnl"]:+.2f}%'
    )

    print(
        "============================================================"
    )


if __name__ == "__main__":
    main()
