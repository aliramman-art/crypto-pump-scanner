# ============================================================
# KRAKEN FUTURES TELEGRAM ANALYSIS LISTENER v5.0
# ============================================================
#
# DATA ENGINE:
#   Same market/candle logic as Kraken Scanner v14.1
#
# COMMANDS:
#   تحلیل BTC 5m
#   تحلیل ATOM 15m
#   تحلیل SOL 1h
#
#   /check BTC 5m
#   /check ATOM 15m
#
# STRATEGY:
#   1. CLOSED CANDLES ONLY
#   2. Valid pivot detection
#   3. Trend determination
#   4. Trend Break detection
#   5. Pullback / Retest
#   6. Reversal candle confirmation
#   7. Structural Stop Loss
#   8. RR 1:1 Take Profit
#
# ============================================================

import os
import time
import re
import requests
import traceback
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = BASE_URL + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE_URL + "/derivatives/api/v3/tickers"
CHART_URL = BASE_URL + "/api/charts/v1/trade/{symbol}/{resolution}"

TELEGRAM_API = (
    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
)

REQUEST_TIMEOUT = 20

MAX_CANDLES = 500

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MAX_PULLBACK_CANDLES = 20

SL_BUFFER_PERCENT = 0.001

RR = 1.0

INSTRUMENT_CACHE_SECONDS = 300

POLL_SECONDS = 2


# ============================================================
# TIMEFRAME MAP
# ============================================================

TIMEFRAME_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
    "12h": 720,
    "1d": 1440,
    "1w": 10080,
}

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
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Kraken-Ichi-Listener/5.0",
    "Accept": "application/json",
})


# ============================================================
# GLOBAL CACHE
# ============================================================

INSTRUMENT_CACHE = []
INSTRUMENT_CACHE_TIME = 0


# ============================================================
# BASIC HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)

    except Exception:
        return default


def normalize_timestamp(value):
    try:
        ts = float(value)

        # milliseconds -> seconds
        if ts > 10_000_000_000:
            ts /= 1000

        return int(ts)

    except Exception:
        return None


def utc_now_string():
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def fmt_price(value):
    if value is None:
        return "N/A"

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:,.6f}".rstrip("0").rstrip(".")

    if value >= 0.01:
        return f"{value:.8f}".rstrip("0").rstrip(".")

    if value >= 0.0001:
        return f"{value:.10f}".rstrip("0").rstrip(".")

    return f"{value:.12f}".rstrip("0").rstrip(".")


def pct_change(a, b):
    if a is None or b is None:
        return None

    if b == 0:
        return None

    return ((a - b) / b) * 100


# ============================================================
# HTTP GET
# SAME STYLE AS SCANNER v14.1
# ============================================================

def http_get(url, params=None, retries=3):

    last_error = None

    for attempt in range(1, retries + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            time.sleep(0.12)

            return data

        except Exception as e:

            last_error = e

            if attempt < retries:
                time.sleep(1.5 * attempt)

    raise RuntimeError(
        f"HTTP request failed: {last_error}"
    )


# ============================================================
# SYMBOL NORMALIZATION
# SAME LOGIC AS SCANNER v14.1
# ============================================================

def normalize_symbol_query(query):

    q = str(query).strip().upper()

    q = q.replace("$", "")
    q = q.replace(",", "")
    q = q.replace(" ", "")

    btc_aliases = {
        "BTC",
        "XBT",
        "BTCUSD",
        "XBTUSD",
        "PF_BTCUSD",
        "PF_XBTUSD",
    }

    if q in btc_aliases:
        return "PF_XBTUSD"

    if q.startswith("PF_"):
        return q

    if q.endswith("USD"):
        return "PF_" + q

    return "PF_" + q + "USD"


# ============================================================
# INSTRUMENTS
# SAME LOGIC AS SCANNER v14.1
# ============================================================

def get_instruments(force=False):

    global INSTRUMENT_CACHE
    global INSTRUMENT_CACHE_TIME

    now = time.time()

    if (
        not force
        and INSTRUMENT_CACHE
        and now - INSTRUMENT_CACHE_TIME
        < INSTRUMENT_CACHE_SECONDS
    ):
        return INSTRUMENT_CACHE

    data = http_get(INSTRUMENTS_URL)

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

    INSTRUMENT_CACHE = instruments
    INSTRUMENT_CACHE_TIME = now

    return instruments


# ============================================================
# TICKERS
# SAME LOGIC AS SCANNER v14.1
# ============================================================

def get_tickers():

    data = http_get(TICKERS_URL)

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

    indexed = {}

    for ticker in tickers:

        if not isinstance(ticker, dict):
            continue

        symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
        )

        if symbol:
            indexed[str(symbol).upper()] = ticker

    return indexed


# ============================================================
# FIND MARKET
# SAME LOGIC AS SCANNER v14.1
# ============================================================

def find_market(query, markets=None):

    normalized = normalize_symbol_query(query)

    if markets:

        for market in markets:

            if not isinstance(market, dict):
                continue

            symbol = str(
                market.get("symbol", "")
            ).upper()

            if symbol == normalized:
                return market

    instruments = get_instruments()

    for market in instruments:

        if not isinstance(market, dict):
            continue

        symbol = str(
            market.get("symbol", "")
        ).upper()

        if symbol != normalized:
            continue

        if not symbol.startswith("PF_"):
            continue

        if "USD" not in symbol:
            continue

        return market

    return None


# ============================================================
# TICKER PRICE
# ============================================================

def get_ticker_price(ticker):

    if not ticker:
        return None

    possible_fields = [
        "last",
        "lastPrice",
        "last_price",
        "price",
        "markPrice",
        "mark_price",
        "indexPrice",
        "index_price",
    ]

    for field in possible_fields:

        value = safe_float(
            ticker.get(field)
        )

        if value is not None and value > 0:
            return value

    return None


# ============================================================
# TICKER BID / ASK
# ============================================================

def get_bid_ask(ticker):

    if not ticker:
        return None, None

    bid = (
        safe_float(ticker.get("bid"))
        or safe_float(ticker.get("bidPrice"))
        or safe_float(ticker.get("bid_price"))
    )

    ask = (
        safe_float(ticker.get("ask"))
        or safe_float(ticker.get("askPrice"))
        or safe_float(ticker.get("ask_price"))
    )

    return bid, ask


# ============================================================
# CANDLES
# EXACT LOGIC FROM SCANNER v14.1
# ============================================================

def get_candles(symbol, resolution, limit=MAX_CANDLES):

    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(
            f"Unsupported timeframe: {resolution}"
        )

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = http_get(
        url,
        params={
            "from": 0,
            "to": int(time.time()),
        }
    )

    rows = []

    if isinstance(data, dict):

        rows = (
            data.get("candles")
            or data.get("data")
            or []
        )

    elif isinstance(data, list):

        rows = data

    candles = []

    for row in rows:

        try:

            # ------------------------------------------------
            # DICT FORMAT
            # ------------------------------------------------

            if isinstance(row, dict):

                timestamp = (
                    row.get("time")
                    or row.get("timestamp")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

                v = (
                    row.get("volume")
                    or row.get("v")
                )

            # ------------------------------------------------
            # LIST FORMAT
            # ------------------------------------------------

            elif isinstance(row, list):

                if len(row) < 5:
                    continue

                timestamp = row[0]
                o = row[1]
                h = row[2]
                l = row[3]
                c = row[4]

                v = row[5] if len(row) > 5 else None

            else:
                continue

            timestamp = normalize_timestamp(timestamp)

            o = safe_float(o)
            h = safe_float(h)
            l = safe_float(l)
            c = safe_float(c)
            v = safe_float(v, 0)

            if timestamp is None:
                continue

            if None in (o, h, l, c):
                continue

            if o <= 0 or h <= 0 or l <= 0 or c <= 0:
                continue

            candles.append({
                "time": timestamp,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            })

        except Exception:
            continue

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # SAME LOGIC AS SCANNER v14.1
    # --------------------------------------------------------

    now_ts = int(time.time())

    seconds = RESOLUTION_SECONDS[resolution]

    closed = []

    for candle in candles:

        if candle["time"] + seconds <= now_ts:
            closed.append(candle)

    return closed[-limit:]


# ============================================================
# DATA SANITY CHECK
# ============================================================

def validate_prices(
    ticker_price,
    last_closed_price
):

    if ticker_price is None:
        return True, "Ticker price unavailable"

    if last_closed_price is None:
        return False, "Last closed candle price unavailable"

    if last_closed_price <= 0:
        return False, "Invalid candle price"

    difference = abs(
        pct_change(
            ticker_price,
            last_closed_price
        )
    )

    # Gross mismatch protection.
    #
    # A normal crypto market should not suddenly show
    # a 30-50% mismatch between ticker and latest candle.
    #
    # 15% gives enough room for volatile assets while
    # catching wrong market/data responses.

    if difference > 15:

        return (
            False,
            f"Ticker/candle mismatch: {difference:.2f}%"
        )

    return True, f"Difference {difference:.3f}%"


# ============================================================
# PIVOT DETECTION
# ============================================================

def is_pivot_high(candles, i):

    start = i - PIVOT_LEFT
    end = i + PIVOT_RIGHT

    if start < 0:
        return False

    if end >= len(candles):
        return False

    value = candles[i]["high"]

    for j in range(start, end + 1):

        if j == i:
            continue

        if candles[j]["high"] >= value:
            return False

    return True


def is_pivot_low(candles, i):

    start = i - PIVOT_LEFT
    end = i + PIVOT_RIGHT

    if start < 0:
        return False

    if end >= len(candles):
        return False

    value = candles[i]["low"]

    for j in range(start, end + 1):

        if j == i:
            continue

        if candles[j]["low"] <= value:
            return False

    return True


def find_pivots(candles):

    highs = []
    lows = []

    start = PIVOT_LEFT
    end = len(candles) - PIVOT_RIGHT

    for i in range(start, end):

        if is_pivot_high(candles, i):

            highs.append({
                "index": i,
                "price": candles[i]["high"],
                "time": candles[i]["time"],
            })

        if is_pivot_low(candles, i):

            lows.append({
                "index": i,
                "price": candles[i]["low"],
                "time": candles[i]["time"],
            })

    return highs, lows


# ============================================================
# TREND DETECTION
# ============================================================

def determine_trend(highs, lows):

    if len(highs) < 2 or len(lows) < 2:
        return "UNKNOWN"

    h1 = highs[-2]["price"]
    h2 = highs[-1]["price"]

    l1 = lows[-2]["price"]
    l2 = lows[-1]["price"]

    # Higher High + Higher Low
    if h2 > h1 and l2 > l1:
        return "BULLISH"

    # Lower High + Lower Low
    if h2 < h1 and l2 < l1:
        return "BEARISH"

    return "RANGE"


# ============================================================
# TREND BREAK
# ============================================================

def detect_trend_break(
    candles,
    trend,
    highs,
    lows
):

    if len(candles) < 5:
        return None

    if trend == "BULLISH":

        if not lows:
            return None

        protected_low = lows[-1]

        for i in range(
            protected_low["index"] + 1,
            len(candles)
        ):

            candle = candles[i]

            if candle["close"] < protected_low["price"]:

                return {
                    "direction": "BEARISH_BREAK",
                    "index": i,
                    "price": candle["close"],
                    "level": protected_low["price"],
                    "time": candle["time"],
                }

    elif trend == "BEARISH":

        if not highs:
            return None

        protected_high = highs[-1]

        for i in range(
            protected_high["index"] + 1,
            len(candles)
        ):

            candle = candles[i]

            if candle["close"] > protected_high["price"]:

                return {
                    "direction": "BULLISH_BREAK",
                    "index": i,
                    "price": candle["close"],
                    "level": protected_high["price"],
                    "time": candle["time"],
                }

    return None


# ============================================================
# PULLBACK / RETEST
# ============================================================

def detect_pullback(
    candles,
    trend_break
):

    if not trend_break:
        return None

    break_index = trend_break["index"]

    level = trend_break["level"]

    end = min(
        len(candles),
        break_index + 1 + MAX_PULLBACK_CANDLES
    )

    if break_index + 1 >= end:
        return None

    break_direction = trend_break["direction"]

    for i in range(
        break_index + 1,
        end
    ):

        candle = candles[i]

        tolerance = abs(level) * 0.003

        # ----------------------------------------------------
        # BEARISH BREAK
        # Price returns toward broken support
        # ----------------------------------------------------

        if break_direction == "BEARISH_BREAK":

            touched = (
                candle["high"] >= level - tolerance
            )

            rejected = (
                candle["close"] < level
            )

            if touched and rejected:

                return {
                    "index": i,
                    "level": level,
                    "direction": "SHORT",
                    "time": candle["time"],
                }

        # ----------------------------------------------------
        # BULLISH BREAK
        # Price returns toward broken resistance
        # ----------------------------------------------------

        elif break_direction == "BULLISH_BREAK":

            touched = (
                candle["low"] <= level + tolerance
            )

            rejected = (
                candle["close"] > level
            )

            if touched and rejected:

                return {
                    "index": i,
                    "level": level,
                    "direction": "LONG",
                    "time": candle["time"],
                }

    return None


# ============================================================
# CANDLE PATTERNS
# ============================================================

def bullish_engulfing(prev, curr):

    return (
        prev["close"] < prev["open"]
        and curr["close"] > curr["open"]
        and curr["open"] <= prev["close"]
        and curr["close"] >= prev["open"]
    )


def bearish_engulfing(prev, curr):

    return (
        prev["close"] > prev["open"]
        and curr["close"] < curr["open"]
        and curr["open"] >= prev["close"]
        and curr["close"] <= prev["open"]
    )


def hammer(c):

    body = abs(c["close"] - c["open"])

    if body == 0:
        body = c["high"] - c["low"]

    upper = c["high"] - max(
        c["open"],
        c["close"]
    )

    lower = min(
        c["open"],
        c["close"]
    ) - c["low"]

    return (
        lower >= body * 2
        and upper <= body
    )


def shooting_star(c):

    body = abs(c["close"] - c["open"])

    if body == 0:
        body = c["high"] - c["low"]

    upper = c["high"] - max(
        c["open"],
        c["close"]
    )

    lower = min(
        c["open"],
        c["close"]
    ) - c["low"]

    return (
        upper >= body * 2
        and lower <= body
    )


def bullish_pin_bar(c):

    body = abs(c["close"] - c["open"])

    rng = c["high"] - c["low"]

    if rng <= 0:
        return False

    lower_wick = (
        min(c["open"], c["close"])
        - c["low"]
    )

    return (
        lower_wick / rng >= 0.55
        and c["close"] > c["open"]
    )


def bearish_pin_bar(c):

    body = abs(c["close"] - c["open"])

    rng = c["high"] - c["low"]

    if rng <= 0:
        return False

    upper_wick = (
        c["high"]
        - max(c["open"], c["close"])
    )

    return (
        upper_wick / rng >= 0.55
        and c["close"] < c["open"]
    )


def strong_bullish(c):

    rng = c["high"] - c["low"]

    if rng <= 0:
        return False

    body = c["close"] - c["open"]

    return (
        body > 0
        and body / rng >= 0.65
        and c["close"]
        >= c["low"] + rng * 0.75
    )


def strong_bearish(c):

    rng = c["high"] - c["low"]

    if rng <= 0:
        return False

    body = c["open"] - c["close"]

    return (
        body > 0
        and body / rng >= 0.65
        and c["close"]
        <= c["low"] + rng * 0.25
    )


# ============================================================
# REVERSAL CONFIRMATION
# ============================================================

def detect_reversal(candles, pullback):

    if not pullback:
        return None

    i = pullback["index"]

    if i < 1:
        return None

    prev = candles[i - 1]
    curr = candles[i]

    direction = pullback["direction"]

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        if bullish_engulfing(prev, curr):

            return {
                "pattern": "Bullish Engulfing",
                "index": i,
                "price": curr["close"],
            }

        if hammer(curr):

            return {
                "pattern": "Hammer",
                "index": i,
                "price": curr["close"],
            }

        if bullish_pin_bar(curr):

            return {
                "pattern": "Bullish Pin Bar",
                "index": i,
                "price": curr["close"],
            }

        if strong_bullish(curr):

            return {
                "pattern": "Strong Bullish Candle",
                "index": i,
                "price": curr["close"],
            }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        if bearish_engulfing(prev, curr):

            return {
                "pattern": "Bearish Engulfing",
                "index": i,
                "price": curr["close"],
            }

        if shooting_star(curr):

            return {
                "pattern": "Shooting Star",
                "index": i,
                "price": curr["close"],
            }

        if bearish_pin_bar(curr):

            return {
                "pattern": "Bearish Pin Bar",
                "index": i,
                "price": curr["close"],
            }

        if strong_bearish(curr):

            return {
                "pattern": "Strong Bearish Candle",
                "index": i,
                "price": curr["close"],
            }

    return None


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def calculate_trade_levels(
    candles,
    reversal,
    direction
):

    entry = candles[reversal["index"]]["close"]

    start = max(
        0,
        reversal["index"] - 10
    )

    recent = candles[
        start:
        reversal["index"] + 1
    ]

    if not recent:
        return None

    if direction == "LONG":

        structural_low = min(
            c["low"] for c in recent
        )

        sl = (
            structural_low
            * (1 - SL_BUFFER_PERCENT)
        )

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + risk * RR

    else:

        structural_high = max(
            c["high"] for c in recent
        )

        sl = (
            structural_high
            * (1 + SL_BUFFER_PERCENT)
        )

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - risk * RR

    risk_pct = abs(
        (entry - sl) / entry
    ) * 100

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_pct": risk_pct,
    }


# ============================================================
# ANALYZE
# ============================================================

def analyze_symbol(
    symbol,
    timeframe
):

    # --------------------------------------------------------
    # VALIDATE TIMEFRAME
    # --------------------------------------------------------

    if timeframe not in TIMEFRAME_MAP:

        return {
            "ok": False,
            "error": (
                f"تایم‌فریم {timeframe} پشتیبانی نمی‌شود."
            )
        }

    # --------------------------------------------------------
    # FIND MARKET
    # --------------------------------------------------------

    market = find_market(symbol)

    if not market:

        normalized = normalize_symbol_query(symbol)

        return {
            "ok": False,
            "error": (
                f"مارکت پیدا نشد.\n"
                f"Requested: {symbol}\n"
                f"Normalized: {normalized}\n"
                f"Only active PF_ USD perpetuals are used."
            )
        }

    actual_symbol = str(
        market.get("symbol", "")
    ).upper()

    if not actual_symbol.startswith("PF_"):

        return {
            "ok": False,
            "error": (
                f"Invalid market returned: "
                f"{actual_symbol}"
            )
        }

    # --------------------------------------------------------
    # TICKER
    # --------------------------------------------------------

    tickers = get_tickers()

    ticker = tickers.get(actual_symbol)

    live_price = get_ticker_price(ticker)

    bid, ask = get_bid_ask(ticker)

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    candles = get_candles(
        actual_symbol,
        timeframe,
        MAX_CANDLES
    )

    if len(candles) < 50:

        return {
            "ok": False,
            "error": (
                f"کندل کافی دریافت نشد.\n"
                f"Market: {actual_symbol}\n"
                f"Timeframe: {timeframe}\n"
                f"Candles: {len(candles)}"
            )
        }

    # --------------------------------------------------------
    # LAST CLOSED
    # --------------------------------------------------------

    last_closed = candles[-1]

    last_closed_price = last_closed["close"]

    # --------------------------------------------------------
    # PRICE SANITY
    # --------------------------------------------------------

    valid, sanity_message = validate_prices(
        live_price,
        last_closed_price
    )

    if not valid:

        return {
            "ok": False,
            "error": (
                f"خطای داده قیمت\n\n"
                f"Market: {actual_symbol}\n"
                f"Ticker: {fmt_price(live_price)}\n"
                f"Last Closed: "
                f"{fmt_price(last_closed_price)}\n"
                f"Difference: "
                f"{sanity_message}"
            )
        }

    # --------------------------------------------------------
    # PIVOTS
    # --------------------------------------------------------

    highs, lows = find_pivots(candles)

    if len(highs) < 2 or len(lows) < 2:

        return {
            "ok": False,
            "error": (
                "پیوت معتبر کافی برای تعیین روند وجود ندارد."
            )
        }

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    trend = determine_trend(
        highs,
        lows
    )

    # --------------------------------------------------------
    # BREAK
    # --------------------------------------------------------

    trend_break = detect_trend_break(
        candles,
        trend,
        highs,
        lows
    )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    pullback = detect_pullback(
        candles,
        trend_break
    )

    # --------------------------------------------------------
    # REVERSAL
    # --------------------------------------------------------

    reversal = detect_reversal(
        candles,
        pullback
    )

    # --------------------------------------------------------
    # TRADE
    # --------------------------------------------------------

    trade = None

    if reversal and pullback:

        trade = calculate_trade_levels(
            candles,
            reversal,
            pullback["direction"]
        )

    return {
        "ok": True,
        "market": actual_symbol,
        "requested_symbol": symbol,
        "timeframe": timeframe,
        "candles": len(candles),
        "live_price": live_price,
        "last_closed_price": last_closed_price,
        "bid": bid,
        "ask": ask,
        "sanity": sanity_message,
        "trend": trend,
        "highs": highs,
        "lows": lows,
        "trend_break": trend_break,
        "pullback": pullback,
        "reversal": reversal,
        "trade": trade,
        "last_candle_time": last_closed["time"],
    }


# ============================================================
# TELEGRAM SEND
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing")
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        return bool(
            data.get("ok")
        )

    except Exception as e:

        print(
            "Telegram send error:",
            e
        )

        return False


# ============================================================
# MESSAGE FORMAT
# ============================================================

def generate_message(result):

    if not result["ok"]:

        return (
            "❌ ANALYSIS ERROR\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{result['error']}\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    market = result["market"]

    timeframe = result["timeframe"]

    live = result["live_price"]

    closed = result["last_closed_price"]

    trend = result["trend"]

    trend_break = result["trend_break"]

    pullback = result["pullback"]

    reversal = result["reversal"]

    trade = result["trade"]

    lines = []

    lines.append(
        "🔎 KRAKEN FUTURES ANALYSIS v5.0"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🪙 {market}"
    )

    lines.append(
        f"⏱ {timeframe} | CLOSED CANDLES ONLY"
    )

    lines.append("")

    # --------------------------------------------------------
    # PRICE
    # --------------------------------------------------------

    lines.append(
        f"💰 Live Price: {fmt_price(live)}"
    )

    lines.append(
        f"🕯 Last Closed: {fmt_price(closed)}"
    )

    lines.append(
        f"📊 Candles: {result['candles']}"
    )

    lines.append("")

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    if trend == "BULLISH":

        trend_text = "🟢 BULLISH"

    elif trend == "BEARISH":

        trend_text = "🔴 BEARISH"

    else:

        trend_text = "🟡 RANGE / UNCLEAR"

    lines.append(
        f"📈 TREND: {trend_text}"
    )

    lines.append("")

    # --------------------------------------------------------
    # BREAK
    # --------------------------------------------------------

    if trend_break:

        if (
            trend_break["direction"]
            == "BULLISH_BREAK"
        ):

            lines.append(
                "⚡ TREND BREAK: BULLISH"
            )

        else:

            lines.append(
                "⚡ TREND BREAK: BEARISH"
            )

        lines.append(
            f"Break Level: "
            f"{fmt_price(trend_break['level'])}"
        )

    else:

        lines.append(
            "⚪ TREND BREAK: Not confirmed"
        )

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    if pullback:

        lines.append("")

        lines.append(
            "🔄 PULLBACK / RETEST: CONFIRMED"
        )

        lines.append(
            f"Retest Level: "
            f"{fmt_price(pullback['level'])}"
        )

    else:

        lines.append("")

        lines.append(
            "⚪ PULLBACK / RETEST: Not confirmed"
        )

    # --------------------------------------------------------
    # REVERSAL
    # --------------------------------------------------------

    if reversal:

        lines.append("")

        lines.append(
            "🕯 REVERSAL CONFIRMATION: YES"
        )

        lines.append(
            f"Pattern: {reversal['pattern']}"
        )

    else:

        lines.append("")

        lines.append(
            "⚪ REVERSAL CONFIRMATION: No"
        )

    # --------------------------------------------------------
    # TRADE
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if trade and pullback and reversal:

        direction = pullback["direction"]

        if direction == "LONG":

            lines.append(
                "🟢 SIGNAL: LONG"
            )

        else:

            lines.append(
                "🔴 SIGNAL: SHORT"
            )

        lines.append("")

        lines.append(
            f"🎯 Entry: "
            f"{fmt_price(trade['entry'])}"
        )

        lines.append(
            f"🛑 SL: "
            f"{fmt_price(trade['sl'])}"
        )

        lines.append(
            f"🎯 TP 1:1: "
            f"{fmt_price(trade['tp'])}"
        )

        lines.append(
            f"📐 Risk: "
            f"{trade['risk_pct']:.2f}%"
        )

        lines.append("")

        lines.append(
            "✅ SETUP CONFIRMED"
        )

    else:

        lines.append(
            "⛔ NO TRADE"
        )

        lines.append(
            "شرایط کامل ورود هنوز تأیید نشده."
        )

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🛡 Data Check: {result['sanity']}"
    )

    lines.append(
        f"🕐 {utc_now_string()}"
    )

    return "\n".join(lines)


# ============================================================
# COMMAND PARSER
# ============================================================

def parse_command(text):

    if not text:
        return None

    text = text.strip()

    # --------------------------------------------------------
    # Persian:
    #
    # تحلیل BTC 5m
    # تحلیل ATOM 15m
    #
    # --------------------------------------------------------

    pattern = re.compile(
        r"^تحلیل\s+([A-Za-z0-9_$.-]+)"
        r"(?:\s+([A-Za-z0-9]+))?\s*$",
        re.IGNORECASE
    )

    match = pattern.match(text)

    if match:

        symbol = match.group(1)

        timeframe = (
            match.group(2)
            or "5m"
        ).lower()

        return symbol, timeframe

    # --------------------------------------------------------
    # /check BTC 5m
    # --------------------------------------------------------

    pattern = re.compile(
        r"^/check(?:@\w+)?\s+"
        r"([A-Za-z0-9_$.-]+)"
        r"(?:\s+([A-Za-z0-9]+))?\s*$",
        re.IGNORECASE
    )

    match = pattern.match(text)

    if match:

        symbol = match.group(1)

        timeframe = (
            match.group(2)
            or "5m"
        ).lower()

        return symbol, timeframe

    return None


# ============================================================
# TELEGRAM GET UPDATES
# ============================================================

def telegram_get_updates(offset=None):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 30,
        "allowed_updates": ["message"],
    }

    if offset is not None:
        params["offset"] = offset

    response = SESSION.get(
        url,
        params=params,
        timeout=40
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data.get("result", [])


# ============================================================
# REMOVE WEBHOOK
# ============================================================

def delete_webhook():

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/deleteWebhook"
    )

    try:

        response = SESSION.post(
            url,
            json={
                "drop_pending_updates": False
            },
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        print(
            "Telegram webhook removed."
        )

    except Exception as e:

        print(
            "Webhook removal warning:",
            e
        )


# ============================================================
# MAIN LISTENER
# ============================================================

def main():

    print("")
    print("=" * 60)
    print("KRAKEN FUTURES TELEGRAM LISTENER v5.0")
    print("=" * 60)

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN "
            "is not configured."
        )

        return

    if not TELEGRAM_CHAT_ID:

        print(
            "WARNING: TELEGRAM_CHAT_ID "
            "is not configured."
        )

    print(
        "Data engine: Scanner v14.1 compatible"
    )

    print(
        "Mode: CLOSED CANDLES ONLY"
    )

    print(
        "Strategy: Pivot -> Break -> Pullback "
        "-> Reversal -> SL/TP"
    )

    print("=" * 60)

    # --------------------------------------------------------
    # WEBHOOK
    # --------------------------------------------------------

    delete_webhook()

    offset = None

    while True:

        try:

            updates = telegram_get_updates(
                offset
            )

            if not updates:

                continue

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = update_id + 1

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                chat = message.get(
                    "chat",
                    {}
                )

                chat_id = str(
                    chat.get("id", "")
                )

                text = message.get(
                    "text",
                    ""
                )

                print(
                    f"[{utc_now_string()}] "
                    f"Message from {chat_id}: "
                    f"{text}"
                )

                # ------------------------------------------------
                # SECURITY
                # ------------------------------------------------

                if (
                    TELEGRAM_CHAT_ID
                    and chat_id
                    != TELEGRAM_CHAT_ID
                ):

                    print(
                        "Ignored unauthorized chat:",
                        chat_id
                    )

                    continue

                # ------------------------------------------------
                # COMMAND
                # ------------------------------------------------

                parsed = parse_command(text)

                if not parsed:

                    continue

                symbol, timeframe = parsed

                # ------------------------------------------------
                # ACK
                # ------------------------------------------------

                telegram_send(
                    f"🔎 در حال بررسی "
                    f"{symbol.upper()} "
                    f"در تایم‌فریم {timeframe}..."
                )

                try:

                    result = analyze_symbol(
                        symbol,
                        timeframe
                    )

                    report = generate_message(
                        result
                    )

                    telegram_send(report)

                except Exception as e:

                    error_text = (
                        "❌ ANALYSIS EXCEPTION\n"
                        "━━━━━━━━━━━━━━━━━━\n"
                        f"Symbol: {symbol}\n"
                        f"Timeframe: {timeframe}\n"
                        f"Error: {str(e)}"
                    )

                    print(
                        traceback.format_exc()
                    )

                    telegram_send(
                        error_text
                    )

        except KeyboardInterrupt:

            print(
                "Listener stopped manually."
            )

            break

        except Exception as e:

            print(
                f"[{utc_now_string()}] "
                f"Listener error: {e}"
            )

            print(
                traceback.format_exc()
            )

            print(
                "Reconnecting in 5 seconds..."
            )

            time.sleep(5)


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
