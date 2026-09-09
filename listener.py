# ============================================================
# KRAKEN FUTURES ICHIMOKU TELEGRAM LIVE LISTENER v2.0
# ============================================================
#
# PURPOSE:
#   Independent real-time Telegram analysis engine
#
# COMMAND:
#   تحلیل btc
#   تحلیل BTC
#   تحلیل BTC ETH SOL XRP
#   /check BTC
#   /check BTC ETH SOL XRP
#
# IMPORTANT:
#   - DOES NOT IMPORT scanner.py
#   - DOES NOT OPEN TRADES
#   - DOES NOT MODIFY trade state
#   - DOES NOT MODIFY trade history
#   - DIRECT KRAKEN FUTURES API
#   - CLOSED CANDLES ONLY
#
# STRATEGY:
#   1H  = Trend
#   30M = Confirmation / Lock
#   15M = Pullback Structure
#   5M  = Pullback + Reversal Trigger
#
# ============================================================

import os
import re
import json
import time
import math
import traceback
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIGURATION
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

KRAKEN_BASE = "https://futures.kraken.com"
KRAKEN_API = f"{KRAKEN_BASE}/derivatives/api/v3"
KRAKEN_CHART_API = f"{KRAKEN_BASE}/api/charts/v1/trade"

TELEGRAM_API = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
    if TELEGRAM_BOT_TOKEN
    else ""
)

OFFSET_FILE = "telegram_listener_offset.json"

REQUEST_TIMEOUT = 20

POLL_TIMEOUT = 25

MAX_SYMBOLS_PER_COMMAND = 5


# ============================================================
# ICHIMOKU
# ============================================================

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52


# ============================================================
# STRATEGY THRESHOLDS
# ============================================================

MIN_1H_SCORE = 4
MIN_30M_SCORE = 3
MIN_15M_SCORE = 2
MIN_5M_SCORE = 2

MIN_TRIGGER_SCORE = 7
MIN_SCORE = 78.0

PULLBACK_MAX_AGE = 2
PULLBACK_TOUCH_ATR = 0.35
PULLBACK_MAX_DISTANCE_ATR = 1.50

MIN_TRIGGER_BODY_ATR = 0.10
MIN_REJECTION_WICK_RATIO = 0.35

MIN_STOP_PCT = 0.50
MAX_STOP_PCT = 2.00

RR = 1.0

STRUCTURE_LOOKBACK_5M = 10
STRUCTURE_LOOKBACK_15M = 4

STRUCTURE_ATR_BUFFER = 0.10


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Kraken-Ichi-Live-Listener/2.0",
        "Accept": "application/json",
    }
)


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def clamp(value, low, high):
    return max(low, min(high, value))


def fmt_price(price):
    if price is None:
        return "-"

    price = float(price)

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:,.4f}"

    if price >= 0.01:
        return f"{price:,.6f}"

    if price >= 0.0001:
        return f"{price:,.8f}"

    return f"{price:.10f}"


def fmt_pct(value):
    if value is None:
        return "-"

    return f"{float(value):+.2f}%"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_request(method, params=None, timeout=30):
    if not TELEGRAM_API:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not configured.")

    url = f"{TELEGRAM_API}/{method}"

    response = SESSION.get(
        url,
        params=params or {},
        timeout=timeout,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data


def send_telegram(text, chat_id=None):
    target = chat_id or TELEGRAM_CHAT_ID

    if not target:
        print("ERROR: TELEGRAM_CHAT_ID is not configured.")
        return False

    try:
        telegram_request(
            "sendMessage",
            {
                "chat_id": target,
                "text": text,
                "disable_web_page_preview": True,
            },
            timeout=30,
        )

        return True

    except Exception as exc:
        print(f"Telegram send error: {exc}")
        return False


# ============================================================
# TELEGRAM OFFSET
# ============================================================

def load_offset():
    if not os.path.exists(OFFSET_FILE):
        return 0

    try:
        with open(
            OFFSET_FILE,
            "r",
            encoding="utf-8",
        ) as f:
            data = json.load(f)

        return int(data.get("offset", 0))

    except Exception:
        return 0


def save_offset(offset):
    temp_file = f"{OFFSET_FILE}.tmp"

    try:
        with open(
            temp_file,
            "w",
            encoding="utf-8",
        ) as f:
            json.dump(
                {
                    "offset": int(offset),
                    "updated": utc_now(),
                },
                f,
                indent=2,
            )

        os.replace(temp_file, OFFSET_FILE)

    except Exception as exc:
        print(f"Offset save error: {exc}")


# ============================================================
# KRAKEN API
# ============================================================

def kraken_get(url, params=None):
    response = SESSION.get(
        url,
        params=params or {},
        timeout=REQUEST_TIMEOUT,
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):
        if data.get("result") == "error":
            raise RuntimeError(str(data))

        if data.get("error"):
            raise RuntimeError(str(data))

    return data


# ============================================================
# INSTRUMENTS
# ============================================================

_INSTRUMENT_CACHE = None


def get_instruments():
    global _INSTRUMENT_CACHE

    if _INSTRUMENT_CACHE is not None:
        return _INSTRUMENT_CACHE

    data = kraken_get(
        f"{KRAKEN_API}/instruments"
    )

    instruments = data.get("instruments", [])

    if not instruments:
        raise RuntimeError(
            "Kraken instruments list is empty."
        )

    _INSTRUMENT_CACHE = instruments

    return instruments


# ============================================================
# SYMBOL NORMALIZATION
# ============================================================

SYMBOL_ALIASES = {
    "BTC": "PF_XBTUSD",
    "XBT": "PF_XBTUSD",
    "BTCUSD": "PF_XBTUSD",
    "XBTUSD": "PF_XBTUSD",

    "ETH": "PF_ETHUSD",
    "ETHUSD": "PF_ETHUSD",

    "SOL": "PF_SOLUSD",
    "SOLUSD": "PF_SOLUSD",

    "XRP": "PF_XRPUSD",
    "XRPUSD": "PF_XRPUSD",

    "DOGE": "PF_DOGEUSD",
    "DOGEUSD": "PF_DOGEUSD",

    "ADA": "PF_ADAUSD",
    "ADAUSD": "PF_ADAUSD",

    "SUI": "PF_SUIUSD",
    "SUIUSD": "PF_SUIUSD",

    "AVAX": "PF_AVAXUSD",
    "AVAXUSD": "PF_AVAXUSD",

    "LINK": "PF_LINKUSD",
    "LINKUSD": "PF_LINKUSD",

    "DOT": "PF_DOTUSD",
    "DOTUSD": "PF_DOTUSD",

    "TRX": "PF_TRXUSD",
    "TRXUSD": "PF_TRXUSD",

    "LTC": "PF_LTCUSD",
    "LTCUSD": "PF_LTCUSD",

    "BCH": "PF_BCHUSD",
    "BCHUSD": "PF_BCHUSD",
}


def normalize_symbol(symbol):
    symbol = symbol.upper().strip()

    if symbol in SYMBOL_ALIASES:
        return SYMBOL_ALIASES[symbol]

    if symbol.startswith("PF_"):
        return symbol

    return f"PF_{symbol}USD"


def display_symbol(symbol):
    symbol = symbol.upper()

    if symbol == "PF_XBTUSD":
        return "BTC"

    if symbol.startswith("PF_"):
        value = symbol[3:]

        if value.endswith("USD"):
            value = value[:-3]

        return value

    return symbol


def find_market(symbol):
    normalized = normalize_symbol(symbol)

    instruments = get_instruments()

    exact = None

    for item in instruments:
        name = str(
            item.get("symbol")
            or item.get("instrument")
            or item.get("name")
            or ""
        ).upper()

        if name == normalized:
            exact = item
            break

    if exact:
        return exact

    # fallback
    wanted = normalized.replace("PF_", "")

    for item in instruments:
        name = str(
            item.get("symbol")
            or item.get("instrument")
            or item.get("name")
            or ""
        ).upper()

        if name.replace("PF_", "") == wanted:
            return item

    return None


# ============================================================
# CANDLES
# ============================================================

RESOLUTION_MAP = {
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "1h": "60",
}


def get_candles(symbol, timeframe, limit=180):
    resolution = RESOLUTION_MAP[timeframe]

    url = (
        f"{KRAKEN_CHART_API}/"
        f"{symbol}/{resolution}"
    )

    data = kraken_get(url)

    candles = []

    raw = data.get("candles", [])

    for c in raw:
        try:
            ts = int(
                c.get("time")
                or c.get("timestamp")
                or c.get("t")
            )

            o = safe_float(
                c.get("open", c.get("o"))
            )

            h = safe_float(
                c.get("high", c.get("h"))
            )

            l = safe_float(
                c.get("low", c.get("l"))
            )

            close = safe_float(
                c.get("close", c.get("c"))
            )

            volume = safe_float(
                c.get("volume", c.get("v"))
            )

            if not all(
                [
                    ts > 0,
                    o > 0,
                    h > 0,
                    l > 0,
                    close > 0,
                ]
            ):
                continue

            candles.append(
                {
                    "time": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": close,
                    "volume": volume,
                }
            )

        except Exception:
            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    interval_seconds = {
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
    }[timeframe]

    now_ts = int(time.time())

    closed = [
        c
        for c in candles
        if c["time"] + interval_seconds <= now_ts
    ]

    return closed[-limit:]


# ============================================================
# TICKER
# ============================================================

def get_ticker(symbol):
    data = kraken_get(
        f"{KRAKEN_API}/tickers"
    )

    tickers = data.get("tickers", [])

    for ticker in tickers:
        name = str(
            ticker.get("symbol")
            or ticker.get("instrument")
            or ticker.get("name")
            or ""
        ).upper()

        if name == symbol.upper():
            return ticker

    return None


def get_current_price(symbol, candles=None):
    ticker = get_ticker(symbol)

    if ticker:
        for key in [
            "last",
            "lastPrice",
            "price",
            "markPrice",
            "mark",
        ]:
            if key in ticker:
                value = safe_float(
                    ticker.get(key)
                )

                if value > 0:
                    return value

    if candles:
        return candles[-1]["close"]

    return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):
    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] - current["low"],
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
        return None

    return sum(trs[-period:]) / period


# ============================================================
# ICHIMOKU
# ============================================================

def midpoint(candles):
    if not candles:
        return None

    high = max(
        x["high"] for x in candles
    )

    low = min(
        x["low"] for x in candles
    )

    return (high + low) / 2.0


def calculate_ichimoku(candles):
    if len(candles) < SENKOU_B_PERIOD:
        return None

    tenkan = midpoint(
        candles[-TENKAN_PERIOD:]
    )

    kijun = midpoint(
        candles[-KIJUN_PERIOD:]
    )

    span_b = midpoint(
        candles[-SENKOU_B_PERIOD:]
    )

    # Same logical cloud scoring used by v14.1.
    # Current Span A / Span B are used for scoring.
    span_a = (
        (tenkan + kijun) / 2.0
        if tenkan is not None
        and kijun is not None
        else None
    )

    close = candles[-1]["close"]

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "close": close,
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(candles):
    ichi = calculate_ichimoku(candles)

    if ichi is None:
        return None

    close = ichi["close"]
    tenkan = ichi["tenkan"]
    kijun = ichi["kijun"]
    span_a = ichi["span_a"]
    span_b = ichi["span_b"]

    score = 0
    reasons = []

    # --------------------------------------------------------
    # PRICE VS CLOUD = +/-4
    # --------------------------------------------------------

    if close > ichi["cloud_top"]:
        score += 4
        reasons.append("+4 Price above cloud")

    elif close < ichi["cloud_bottom"]:
        score -= 4
        reasons.append("-4 Price below cloud")

    else:
        reasons.append("0 Price inside cloud")

    # --------------------------------------------------------
    # PRICE VS KIJUN = +/-2
    # --------------------------------------------------------

    if close > kijun:
        score += 2
        reasons.append("+2 Price > Kijun")

    elif close < kijun:
        score -= 2
        reasons.append("-2 Price < Kijun")

    # --------------------------------------------------------
    # TENKAN VS KIJUN = +/-2
    # --------------------------------------------------------

    if tenkan > kijun:
        score += 2
        reasons.append("+2 Tenkan > Kijun")

    elif tenkan < kijun:
        score -= 2
        reasons.append("-2 Tenkan < Kijun")

    # --------------------------------------------------------
    # SPAN A VS SPAN B = +/-2
    # --------------------------------------------------------

    if span_a > span_b:
        score += 2
        reasons.append("+2 Span A > Span B")

    elif span_a < span_b:
        score -= 2
        reasons.append("-2 Span A < Span B")

    return {
        "score": score,
        "ichimoku": ichi,
        "reasons": reasons,
    }


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(candle):
    return abs(
        candle["close"]
        - candle["open"]
    )


def candle_range(candle):
    return (
        candle["high"]
        - candle["low"]
    )


def upper_wick(candle):
    return (
        candle["high"]
        - max(
            candle["open"],
            candle["close"],
        )
    )


def lower_wick(candle):
    return (
        min(
            candle["open"],
            candle["close"],
        )
        - candle["low"]
    )


# ============================================================
# PULLBACK ANALYSIS
# ============================================================

def analyze_pullback(candles, side):
    result = {
        "valid": False,
        "touch": False,
        "touch_age": None,
        "distance_atr": None,
        "reversal": False,
        "reclaim": False,
        "structure": False,
        "body_ok": False,
        "reason": "",
    }

    if len(candles) < 30:
        result["reason"] = "Not enough 5m candles."
        return result

    current = candles[-1]

    atr = calculate_atr(candles, 14)

    if atr is None or atr <= 0:
        result["reason"] = "ATR unavailable."
        return result

    ichi = calculate_ichimoku(candles)

    if ichi is None:
        result["reason"] = "Ichimoku unavailable."
        return result

    tenkan = ichi["tenkan"]
    kijun = ichi["kijun"]

    # --------------------------------------------------------
    # Find recent touch
    # --------------------------------------------------------

    touch_index = None

    search_start = max(
        0,
        len(candles) - PULLBACK_MAX_AGE - 1,
    )

    for i in range(
        search_start,
        len(candles),
    ):
        c = candles[i]

        if side == "LONG":
            levels = [
                tenkan,
                kijun,
                ichi["cloud_top"],
            ]

            touched = any(
                abs(c["low"] - level)
                <= atr * PULLBACK_TOUCH_ATR
                or (
                    c["low"]
                    <= level
                    <= c["high"]
                )
                for level in levels
                if level is not None
            )

        else:
            levels = [
                tenkan,
                kijun,
                ichi["cloud_bottom"],
            ]

            touched = any(
                abs(c["high"] - level)
                <= atr * PULLBACK_TOUCH_ATR
                or (
                    c["low"]
                    <= level
                    <= c["high"]
                )
                for level in levels
                if level is not None
            )

        if touched:
            touch_index = i

    if touch_index is None:
        result["reason"] = "No fresh pullback touch."
        return result

    touch_age = (
        len(candles)
        - 1
        - touch_index
    )

    result["touch"] = True
    result["touch_age"] = touch_age

    if touch_age > PULLBACK_MAX_AGE:
        result["reason"] = (
            f"Pullback too old: {touch_age} candles."
        )
        return result

    # --------------------------------------------------------
    # Distance from relevant level
    # --------------------------------------------------------

    if side == "LONG":
        levels = [
            tenkan,
            kijun,
            ichi["cloud_top"],
        ]

    else:
        levels = [
            tenkan,
            kijun,
            ichi["cloud_bottom"],
        ]

    relevant_levels = [
        x for x in levels
        if x is not None
    ]

    distance = min(
        abs(current["close"] - level)
        for level in relevant_levels
    )

    distance_atr = distance / atr

    result["distance_atr"] = distance_atr

    if distance_atr > PULLBACK_MAX_DISTANCE_ATR:
        result["reason"] = (
            f"Pullback too far: "
            f"{distance_atr:.2f} ATR."
        )
        return result

    # --------------------------------------------------------
    # Reversal
    # --------------------------------------------------------

    body = candle_body(current)
    rng = candle_range(current)

    if rng <= 0:
        result["reason"] = "Invalid trigger candle."
        return result

    if side == "LONG":
        bullish = current["close"] > current["open"]

        rejection = (
            lower_wick(current) / rng
            >= MIN_REJECTION_WICK_RATIO
        )

        reversal = bullish or rejection

    else:
        bearish = current["close"] < current["open"]

        rejection = (
            upper_wick(current) / rng
            >= MIN_REJECTION_WICK_RATIO
        )

        reversal = bearish or rejection

    result["reversal"] = reversal

    if not reversal:
        result["reason"] = "No reversal candle."
        return result

    # --------------------------------------------------------
    # Reclaim
    # --------------------------------------------------------

    if side == "LONG":
        reclaim = (
            current["close"] > tenkan
            and current["close"] > kijun
        )
    else:
        reclaim = (
            current["close"] < tenkan
            and current["close"] < kijun
        )

    result["reclaim"] = reclaim

    # --------------------------------------------------------
    # Structure
    # --------------------------------------------------------

    lookback = candles[
        max(
            0,
            len(candles)
            - STRUCTURE_LOOKBACK_5M,
        ):
    ]

    previous = candles[-2]

    if side == "LONG":
        structure = (
            current["close"]
            > previous["high"]
            or current["high"]
            > previous["high"]
        )
    else:
        structure = (
            current["close"]
            < previous["low"]
            or current["low"]
            < previous["low"]
        )

    result["structure"] = structure

    # --------------------------------------------------------
    # Body quality
    # --------------------------------------------------------

    body_ok = (
        body
        >= atr * MIN_TRIGGER_BODY_ATR
    )

    result["body_ok"] = body_ok

    # --------------------------------------------------------
    # Final pullback validity
    # --------------------------------------------------------

    result["valid"] = (
        result["touch"]
        and result["touch_age"]
        <= PULLBACK_MAX_AGE
        and result["distance_atr"]
        <= PULLBACK_MAX_DISTANCE_ATR
        and result["reversal"]
        and result["body_ok"]
    )

    if result["valid"]:
        result["reason"] = "Valid pullback."
    else:
        result["reason"] = (
            "Pullback incomplete."
        )

    return result


# ============================================================
# TRIGGER SCORE
# ============================================================

def calculate_trigger_score(
    candles,
    side,
    pullback,
):
    if not pullback["valid"]:
        return {
            "score": 0,
            "reasons": [
                "Pullback invalid"
            ],
        }

    current = candles[-1]
    previous = candles[-2]

    score = 0
    reasons = []

    # Fresh touch
    if pullback["touch"]:
        score += 2
        reasons.append("+2 Fresh touch")

    # Reversal
    if pullback["reversal"]:
        score += 2
        reasons.append("+2 Reversal")

    # Rejection
    rng = candle_range(current)

    if rng > 0:
        if side == "LONG":
            rejection_ratio = (
                lower_wick(current) / rng
            )
        else:
            rejection_ratio = (
                upper_wick(current) / rng
            )

        if (
            rejection_ratio
            >= MIN_REJECTION_WICK_RATIO
        ):
            score += 1
            reasons.append(
                "+1 Rejection wick"
            )

    # Reclaim
    if pullback["reclaim"]:
        score += 2
        reasons.append("+2 Reclaim")

    # Structure
    if pullback["structure"]:
        score += 1
        reasons.append("+1 Structure")

    # Body
    if pullback["body_ok"]:
        score += 1
        reasons.append("+1 Body quality")

    # Previous candle break
    if side == "LONG":
        if current["close"] > previous["high"]:
            score += 1
            reasons.append(
                "+1 Close > previous high"
            )
    else:
        if current["close"] < previous["low"]:
            score += 1
            reasons.append(
                "+1 Close < previous low"
            )

    return {
        "score": min(score, 10),
        "reasons": reasons,
    }


# ============================================================
# 15M STRUCTURE
# ============================================================

def check_15m_structure(candles, side):
    if len(candles) < 10:
        return False

    recent = candles[
        -STRUCTURE_LOOKBACK_15M:
    ]

    if side == "LONG":
        return (
            recent[-1]["close"]
            >= recent[0]["close"]
        )

    return (
        recent[-1]["close"]
        <= recent[0]["close"]
    )


# ============================================================
# SCORE TO PERCENT
# ============================================================

def normalized_score(score):
    if score is None:
        return 0.0

    # -10 to +10 -> 0 to 100
    return (
        (score + 10.0)
        / 20.0
        * 100.0
    )


# ============================================================
# FINAL SCORE
# ============================================================

def calculate_final_score(
    score_1h,
    score_30m,
    score_15m,
    score_5m,
    trigger_score,
    side,
):
    # --------------------------------------------------------
    # Same weighted architecture as v14.1:
    #
    # 1H      = 35
    # 30M     = 25
    # 15M     = 20
    # Trigger = 15
    # 5M      = 5
    # --------------------------------------------------------

    def directional(score):
        if side == "LONG":
            return max(score, 0)
        return max(-score, 0)

    s1 = directional(score_1h)
    s30 = directional(score_30m)
    s15 = directional(score_15m)
    s5 = directional(score_5m)

    final = (
        (s1 / 10.0) * 35.0
        + (s30 / 10.0) * 25.0
        + (s15 / 10.0) * 20.0
        + (trigger_score / 10.0) * 15.0
        + (s5 / 10.0) * 5.0
    )

    return clamp(
        final,
        0.0,
        100.0,
    )


# ============================================================
# STOP / TARGET
# ============================================================

def calculate_trade_levels(
    candles_5m,
    candles_15m,
    side,
):
    if len(candles_5m) < 10:
        return None

    if len(candles_15m) < 4:
        return None

    entry = candles_5m[-1]["close"]

    atr = calculate_atr(
        candles_5m,
        14,
    )

    if atr is None or atr <= 0:
        return None

    low_5m = min(
        x["low"]
        for x in candles_5m[
            -STRUCTURE_LOOKBACK_5M:
        ]
    )

    high_5m = max(
        x["high"]
        for x in candles_5m[
            -STRUCTURE_LOOKBACK_5M:
        ]
    )

    low_15m = min(
        x["low"]
        for x in candles_15m[
            -STRUCTURE_LOOKBACK_15M:
        ]
    )

    high_15m = max(
        x["high"]
        for x in candles_15m[
            -STRUCTURE_LOOKBACK_15M:
        ]
    )

    if side == "LONG":
        structure_low = min(
            low_5m,
            low_15m,
        )

        sl = (
            structure_low
            - atr * STRUCTURE_ATR_BUFFER
        )

        if sl >= entry:
            return None

        risk = entry - sl

        tp = entry + risk * RR

    else:
        structure_high = max(
            high_5m,
            high_15m,
        )

        sl = (
            structure_high
            + atr * STRUCTURE_ATR_BUFFER
        )

        if sl <= entry:
            return None

        risk = sl - entry

        tp = entry - risk * RR

    stop_pct = (
        abs(entry - sl)
        / entry
        * 100.0
    )

    if (
        stop_pct < MIN_STOP_PCT
        or stop_pct > MAX_STOP_PCT
    ):
        return {
            "valid": False,
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "stop_pct": stop_pct,
            "reason": (
                f"SL {stop_pct:.2f}% "
                f"outside "
                f"{MIN_STOP_PCT:.2f}%"
                f"-"
                f"{MAX_STOP_PCT:.2f}%"
            ),
        }

    return {
        "valid": True,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "stop_pct": stop_pct,
        "risk": risk,
    }


# ============================================================
# SIDE ANALYSIS
# ============================================================

def analyze_side(
    side,
    candles_1h,
    candles_30m,
    candles_15m,
    candles_5m,
):
    r1 = ichimoku_score(candles_1h)
    r30 = ichimoku_score(candles_30m)
    r15 = ichimoku_score(candles_15m)
    r5 = ichimoku_score(candles_5m)

    if not all(
        [
            r1,
            r30,
            r15,
            r5,
        ]
    ):
        return {
            "side": side,
            "valid": False,
            "reason": "Insufficient Ichimoku data.",
            "score_1h": 0,
            "score_30m": 0,
            "score_15m": 0,
            "score_5m": 0,
            "trigger_score": 0,
            "final_score": 0,
        }

    score_1h = r1["score"]
    score_30m = r30["score"]
    score_15m = r15["score"]
    score_5m = r5["score"]

    # --------------------------------------------------------
    # Directional thresholds
    # --------------------------------------------------------

    if side == "LONG":
        trend_ok = (
            score_1h >= MIN_1H_SCORE
        )

        lock_ok = (
            score_30m >= MIN_30M_SCORE
        )

        structure_ok = (
            score_15m >= MIN_15M_SCORE
        )

        trigger_ichi_ok = (
            score_5m >= MIN_5M_SCORE
        )

    else:
        trend_ok = (
            score_1h <= -MIN_1H_SCORE
        )

        lock_ok = (
            score_30m <= -MIN_30M_SCORE
        )

        structure_ok = (
            score_15m <= -MIN_15M_SCORE
        )

        trigger_ichi_ok = (
            score_5m <= -MIN_5M_SCORE
        )

    pullback = analyze_pullback(
        candles_5m,
        side,
    )

    trigger = calculate_trigger_score(
        candles_5m,
        side,
        pullback,
    )

    trigger_score = trigger["score"]

    structure_15m = check_15m_structure(
        candles_15m,
        side,
    )

    final = calculate_final_score(
        score_1h,
        score_30m,
        score_15m,
        score_5m,
        trigger_score,
        side,
    )

    trigger_ok = (
        trigger_score
        >= MIN_TRIGGER_SCORE
    )

    # --------------------------------------------------------
    # All strategy gates
    # --------------------------------------------------------

    valid = (
        trend_ok
        and lock_ok
        and structure_ok
        and trigger_ichi_ok
        and structure_15m
        and trigger_ok
        and final >= MIN_SCORE
        and pullback["valid"]
    )

    failures = []

    if not trend_ok:
        failures.append(
            f"1H score {score_1h}"
        )

    if not lock_ok:
        failures.append(
            f"30M score {score_30m}"
        )

    if not structure_ok:
        failures.append(
            f"15M score {score_15m}"
        )

    if not trigger_ichi_ok:
        failures.append(
            f"5M score {score_5m}"
        )

    if not structure_15m:
        failures.append(
            "15M structure"
        )

    if not pullback["valid"]:
        failures.append(
            "5M pullback"
        )

    if not trigger_ok:
        failures.append(
            f"Trigger {trigger_score}/10"
        )

    if final < MIN_SCORE:
        failures.append(
            f"Final {final:.1f}/100"
        )

    return {
        "side": side,
        "valid": valid,

        "score_1h": score_1h,
        "score_30m": score_30m,
        "score_15m": score_15m,
        "score_5m": score_5m,

        "trigger_score": trigger_score,
        "final_score": final,

        "trend_ok": trend_ok,
        "lock_ok": lock_ok,
        "structure_ok": structure_ok,
        "trigger_ichi_ok": trigger_ichi_ok,
        "structure_15m": structure_15m,
        "trigger_ok": trigger_ok,

        "pullback": pullback,
        "trigger": trigger,

        "failures": failures,

        "levels": None,
    }


# ============================================================
# SYMBOL ANALYSIS
# ============================================================

def analyze_symbol(user_symbol):
    requested = user_symbol.upper().strip()

    market = find_market(requested)

    if not market:
        return {
            "symbol": requested,
            "error": (
                f"Kraken Futures market "
                f"not found for {requested}."
            ),
        }

    symbol = str(
        market.get("symbol")
        or market.get("instrument")
        or market.get("name")
    ).upper()

    print(
        f"[{utc_now()}] "
        f"Analyzing {display_symbol(symbol)} "
        f"({symbol})"
    )

    candles_1h = get_candles(
        symbol,
        "1h",
        180,
    )

    candles_30m = get_candles(
        symbol,
        "30m",
        180,
    )

    candles_15m = get_candles(
        symbol,
        "15m",
        180,
    )

    candles_5m = get_candles(
        symbol,
        "5m",
        180,
    )

    minimums = {
        "1H": 60,
        "30M": 60,
        "15M": 60,
        "5M": 60,
    }

    if len(candles_1h) < minimums["1H"]:
        return {
            "symbol": symbol,
            "error": "Not enough 1H candles.",
        }

    if len(candles_30m) < minimums["30M"]:
        return {
            "symbol": symbol,
            "error": "Not enough 30M candles.",
        }

    if len(candles_15m) < minimums["15M"]:
        return {
            "symbol": symbol,
            "error": "Not enough 15M candles.",
        }

    if len(candles_5m) < minimums["5M"]:
        return {
            "symbol": symbol,
            "error": "Not enough 5M candles.",
        }

    r1 = ichimoku_score(
        candles_1h
    )

    r30 = ichimoku_score(
        candles_30m
    )

    r15 = ichimoku_score(
        candles_15m
    )

    r5 = ichimoku_score(
        candles_5m
    )

    long_result = analyze_side(
        "LONG",
        candles_1h,
        candles_30m,
        candles_15m,
        candles_5m,
    )

    short_result = analyze_side(
        "SHORT",
        candles_1h,
        candles_30m,
        candles_15m,
        candles_5m,
    )

    # --------------------------------------------------------
    # Select best side
    # --------------------------------------------------------

    candidates = [
        long_result,
        short_result,
    ]

    valid_candidates = [
        x
        for x in candidates
        if x["valid"]
    ]

    if valid_candidates:
        winner = max(
            valid_candidates,
            key=lambda x: x["final_score"],
        )

        verdict = winner["side"]

        levels = calculate_trade_levels(
            candles_5m,
            candles_15m,
            verdict,
        )

        winner["levels"] = levels

        if not levels or not levels.get("valid"):
            winner["valid"] = False

            if levels:
                winner["failures"].append(
                    levels.get(
                        "reason",
                        "Invalid SL",
                    )
                )

            verdict = "NO TRADE"

    else:
        winner = max(
            candidates,
            key=lambda x: x["final_score"],
        )

        verdict = "NO TRADE"

    current_price = get_current_price(
        symbol,
        candles_5m,
    )

    # --------------------------------------------------------
    # Last CLOSED 5m candle
    # --------------------------------------------------------

    last_5m = candles_5m[-1]

    last_closed_time = datetime.fromtimestamp(
        last_5m["time"],
        tz=timezone.utc,
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    return {
        "symbol": symbol,
        "display": display_symbol(symbol),

        "current_price": current_price,

        "last_closed_time": last_closed_time,

        "candles": {
            "1h": len(candles_1h),
            "30m": len(candles_30m),
            "15m": len(candles_15m),
            "5m": len(candles_5m),
        },

        "scores": {
            "1h": r1["score"],
            "30m": r30["score"],
            "15m": r15["score"],
            "5m": r5["score"],
        },

        "ichimoku_5m": r5["ichimoku"],

        "LONG": long_result,
        "SHORT": short_result,

        "winner": winner,
        "verdict": verdict,
    }


# ============================================================
# REPORT
# ============================================================

def pass_mark(value):
    return "✅" if value else "❌"


def score_text(score):
    if score > 0:
        return f"+{score}"

    return str(score)


def build_side_report(result, side):
    r = result[side]

    pb = r["pullback"]

    lines = []

    lines.append(
        f"{'🟢' if side == 'LONG' else '🔴'} {side}"
    )

    lines.append(
        f"1H:  {score_text(r['score_1h'])}/10 "
        f"{pass_mark(r['trend_ok'])}"
    )

    lines.append(
        f"30M: {score_text(r['score_30m'])}/10 "
        f"{pass_mark(r['lock_ok'])}"
    )

    lines.append(
        f"15M: {score_text(r['score_15m'])}/10 "
        f"{pass_mark(r['structure_ok'])}"
    )

    lines.append(
        f"5M:  {score_text(r['score_5m'])}/10 "
        f"{pass_mark(r['trigger_ichi_ok'])}"
    )

    lines.append(
        f"Pullback: "
        f"{pass_mark(pb['valid'])}"
    )

    if pb.get("touch_age") is not None:
        lines.append(
            f"  Touch age: "
            f"{pb['touch_age']} candle(s)"
        )

    if pb.get("distance_atr") is not None:
        lines.append(
            f"  Distance: "
            f"{pb['distance_atr']:.2f} ATR"
        )

    lines.append(
        f"  Reversal: "
        f"{pass_mark(pb['reversal'])}"
    )

    lines.append(
        f"  Reclaim: "
        f"{pass_mark(pb['reclaim'])}"
    )

    lines.append(
        f"  Structure: "
        f"{pass_mark(pb['structure'])}"
    )

    lines.append(
        f"  Body: "
        f"{pass_mark(pb['body_ok'])}"
    )

    lines.append(
        f"Trigger: "
        f"{r['trigger_score']}/10 "
        f"{pass_mark(r['trigger_ok'])}"
    )

    lines.append(
        f"15M Structure: "
        f"{pass_mark(r['structure_15m'])}"
    )

    lines.append(
        f"Final Score: "
        f"{r['final_score']:.1f}/100 "
        f"{pass_mark(r['final_score'] >= MIN_SCORE)}"
    )

    if not r["valid"] and r["failures"]:
        lines.append(
            "Reasons: "
            + ", ".join(
                r["failures"][:6]
            )
        )

    return "\n".join(lines)


def generate_report(result):
    if result.get("error"):
        return (
            "❌ KRAKEN ANALYSIS ERROR\n\n"
            f"Symbol: {result.get('symbol')}\n"
            f"Error: {result['error']}"
        )

    symbol = result["display"]
    price = result["current_price"]

    scores = result["scores"]
    ichi = result["ichimoku_5m"]

    verdict = result["verdict"]

    if verdict == "LONG":
        verdict_text = "🟢 LONG"
    elif verdict == "SHORT":
        verdict_text = "🔴 SHORT"
    else:
        verdict_text = "⚪ NO TRADE"

    lines = []

    lines.append(
        "📡 KRAKEN FUTURES LIVE ANALYSIS"
    )

    lines.append(
        "🤖 ICHIMOKU MTF v14.1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🪙 {symbol}"
    )

    lines.append(
        f"💰 Price: {fmt_price(price)}"
    )

    lines.append(
        f"🕐 Analysis: {utc_now()}"
    )

    lines.append(
        f"⏱ Last CLOSED 5M: "
        f"{result['last_closed_time']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 MTF SCORES"
    )

    lines.append(
        f"1H  Trend: "
        f"{score_text(scores['1h'])}/10"
    )

    lines.append(
        f"30M Lock: "
        f"{score_text(scores['30m'])}/10"
    )

    lines.append(
        f"15M Structure: "
        f"{score_text(scores['15m'])}/10"
    )

    lines.append(
        f"5M Trigger: "
        f"{score_text(scores['5m'])}/10"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "☁️ 5M ICHIMOKU"
    )

    lines.append(
        f"Price:  {fmt_price(ichi['close'])}"
    )

    lines.append(
        f"Tenkan: {fmt_price(ichi['tenkan'])}"
    )

    lines.append(
        f"Kijun:  {fmt_price(ichi['kijun'])}"
    )

    lines.append(
        f"Span A: {fmt_price(ichi['span_a'])}"
    )

    lines.append(
        f"Span B: {fmt_price(ichi['span_b'])}"
    )

    lines.append(
        f"Cloud: "
        f"{fmt_price(ichi['cloud_bottom'])}"
        f" - "
        f"{fmt_price(ichi['cloud_top'])}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        build_side_report(
            result,
            "LONG",
        )
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        build_side_report(
            result,
            "SHORT",
        )
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🎯 VERDICT: {verdict_text}"
    )

    winner = result["winner"]

    if verdict in ("LONG", "SHORT"):
        levels = winner.get("levels")

        if levels and levels.get("valid"):
            lines.append(
                ""
            )

            lines.append(
                f"📌 ENTRY: "
                f"{fmt_price(levels['entry'])}"
            )

            lines.append(
                f"🛑 SL: "
                f"{fmt_price(levels['sl'])} "
                f"({levels['stop_pct']:.2f}%)"
            )

            lines.append(
                f"🎯 TP: "
                f"{fmt_price(levels['tp'])}"
            )

            lines.append(
                f"⚖️ RR: 1:{RR:.1f}"
            )

    else:
        lines.append(
            ""
        )

        lines.append(
            "ℹ️ No valid setup met "
            "all strategy conditions."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔒 CLOSED CANDLES ONLY"
    )

    lines.append(
        "🧠 Analysis only"
    )

    lines.append(
        "🚫 No trade was opened."
    )

    return "\n".join(lines)


# ============================================================
# COMMAND PARSER
# ============================================================

def parse_command(text):
    if not text:
        return []

    text = text.strip()

    # Remove bot mention from commands such as:
    # /check@MyBot BTC
    text = re.sub(
        r"@\w+",
        "",
        text,
    )

    # Normalize Persian/English spacing
    text = re.sub(
        r"\s+",
        " ",
        text,
    )

    parts = text.split()

    if not parts:
        return []

    first = parts[0].lower()

    command_words = {
        "تحلیل",
        "/تحلیل",
        "analysis",
        "/analysis",
        "check",
        "/check",
    }

    if first not in command_words:
        return []

    symbols = []

    for token in parts[1:]:
        token = token.strip()

        token = token.replace(
            ",",
            "",
        )

        token = token.upper()

        if not token:
            continue

        # Don't accept obvious command tokens
        if token.startswith("/"):
            continue

        symbols.append(token)

    return symbols[:MAX_SYMBOLS_PER_COMMAND]


# ============================================================
# HELP
# ============================================================

def help_message():
    return (
        "📘 KRAKEN LIVE ANALYSIS\n\n"
        "فرمان‌ها:\n\n"
        "تحلیل btc\n"
        "تحلیل BTC ETH SOL\n"
        "/check BTC\n"
        "/check BTC ETH SOL XRP\n\n"
        "سیستم:\n"
        "1H = Trend\n"
        "30M = Lock\n"
        "15M = Pullback Structure\n"
        "5M = Reversal Trigger\n\n"
        "⚠️ تحلیل مستقل از scanner.py\n"
        "🚫 بدون باز کردن معامله"
    )


# ============================================================
# UPDATE HANDLER
# ============================================================

def process_update(update):
    message = update.get("message")

    if not message:
        return

    chat = message.get("chat", {})

    chat_id = str(
        chat.get("id", "")
    )

    text = message.get(
        "text",
        "",
    )

    if not text:
        return

    print(
        f"[{utc_now()}] "
        f"Telegram message from "
        f"{chat_id}: {text}"
    )

    # --------------------------------------------------------
    # Optional chat restriction
    # --------------------------------------------------------

    if TELEGRAM_CHAT_ID:
        if chat_id != str(
            TELEGRAM_CHAT_ID
        ):
            print(
                "Ignoring message from "
                f"unauthorized chat: {chat_id}"
            )
            return

    symbols = parse_command(text)

    if not symbols:
        if text.strip().lower() in {
            "/start",
            "/help",
            "help",
            "راهنما",
        }:
            send_telegram(
                help_message(),
                chat_id,
            )

        return

    # --------------------------------------------------------
    # Acknowledge
    # --------------------------------------------------------

    send_telegram(
        "⏳ در حال دریافت داده مستقیم "
        "از Kraken Futures...\n"
        f"🔎 {', '.join(symbols)}",
        chat_id,
    )

    # --------------------------------------------------------
    # Analyze each symbol independently
    # --------------------------------------------------------

    for symbol in symbols:
        try:
            result = analyze_symbol(
                symbol
            )

            report = generate_report(
                result
            )

            send_telegram(
                report,
                chat_id,
            )

        except Exception as exc:
            print(
                f"Analysis error for "
                f"{symbol}: {exc}"
            )

            traceback.print_exc()

            send_telegram(
                "❌ خطا در تحلیل "
                f"{symbol}\n\n"
                f"{type(exc).__name__}: "
                f"{exc}",
                chat_id,
            )


# ============================================================
# TELEGRAM POLLING
# ============================================================

def get_updates(offset):
    return telegram_request(
        "getUpdates",
        {
            "offset": offset,
            "timeout": POLL_TIMEOUT,
            "allowed_updates": json.dumps(
                ["message"]
            ),
        },
        timeout=POLL_TIMEOUT + 10,
    )


# ============================================================
# WEBHOOK CHECK
# ============================================================

def check_webhook():
    try:
        data = telegram_request(
            "getWebhookInfo",
            timeout=20,
        )

        result = data.get(
            "result",
            {},
        )

        url = result.get(
            "url",
            "",
        )

        if url:
            print(
                "⚠️ Telegram webhook is active:"
            )

            print(url)

            print(
                "Deleting webhook so "
                "getUpdates can work..."
            )

            telegram_request(
                "deleteWebhook",
                {
                    "drop_pending_updates": False
                },
                timeout=20,
            )

            print(
                "Webhook deleted."
            )

        else:
            print(
                "Telegram webhook: NONE"
            )

    except Exception as exc:
        print(
            f"Webhook check warning: {exc}"
        )


# ============================================================
# STARTUP
# ============================================================

def startup_check():
    print()
    print(
        "============================================================"
    )
    print(
        "KRAKEN FUTURES ICHIMOKU TELEGRAM"
    )
    print(
        "LIVE LISTENER v2.0"
    )
    print(
        "============================================================"
    )

    print(
        f"Time: {utc_now()}"
    )

    print(
        "Mode: INDEPENDENT"
    )

    print(
        "Scanner dependency: NONE"
    )

    print(
        "Trade execution: DISABLED"
    )

    print(
        "State modification: DISABLED"
    )

    print(
        "History modification: DISABLED"
    )

    print(
        "Candles: CLOSED ONLY"
    )

    print(
        "Strategy: Ichimoku MTF v14.1"
    )

    print(
        "1H = Trend"
    )

    print(
        "30M = Lock"
    )

    print(
        "15M = Pullback Structure"
    )

    print(
        "5M = Reversal Trigger"
    )

    print(
        "============================================================"
    )

    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN "
            "is not configured."
        )

    if not TELEGRAM_CHAT_ID:
        print(
            "⚠️ TELEGRAM_CHAT_ID is not "
            "configured. Listener will "
            "accept messages from any chat."
        )

    me = telegram_request(
        "getMe",
        timeout=20,
    )

    bot = me.get(
        "result",
        {},
    )

    print(
        "Telegram bot:"
        f" @{bot.get('username', 'unknown')}"
    )

    print(
        "Telegram connection: OK"
    )

    check_webhook()

    print(
        "============================================================"
    )


# ============================================================
# MAIN LOOP
# ============================================================

def main():
    startup_check()

    offset = load_offset()

    print(
        f"Telegram offset: {offset}"
    )

    print()
    print(
        "🚀 LISTENER IS RUNNING"
    )

    print(
        "Waiting for Telegram commands..."
    )

    print(
        "Example: تحلیل btc"
    )

    print(
        "============================================================"
    )

    while True:
        try:
            updates = get_updates(
                offset
            )

            for update in updates.get(
                "result",
                [],
            ):
                update_id = update.get(
                    "update_id"
                )

                if update_id is None:
                    continue

                next_offset = (
                    update_id + 1
                )

                try:
                    process_update(
                        update
                    )

                except Exception as exc:
                    print(
                        "Update processing error:"
                        f" {exc}"
                    )

                    traceback.print_exc()

                # ------------------------------------------------
                # Save offset AFTER processing
                # ------------------------------------------------

                offset = next_offset

                save_offset(
                    offset
                )

        except KeyboardInterrupt:
            print()
            print(
                "Listener stopped by user."
            )
            break

        except requests.exceptions.RequestException as exc:
            print(
                f"[{utc_now()}] "
                f"Network error: {exc}"
            )

            time.sleep(5)

        except Exception as exc:
            print(
                f"[{utc_now()}] "
                f"Listener error: {exc}"
            )

            traceback.print_exc()

            time.sleep(5)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
