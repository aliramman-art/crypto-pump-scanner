# ============================================================
# KRAKEN FUTURES TELEGRAM LIVE ANALYZER v4.0
# ============================================================
#
# COMMANDS:
#   تحلیل BTC 5m
#   تحلیل ATOM 15m
#   تحلیل ETH 1h
#   /check BTC 5m
#
# STRATEGY:
#   CLOSED CANDLES ONLY
#   Pivot -> Trend -> Trend Break -> Pullback/Retest
#   -> Reversal Candle -> Entry -> Structural SL -> RR 1:1
#
# IMPORTANT:
#   Uses ACTIVE Kraken Futures perpetual contracts.
#   BTC -> PF_XBTUSD
#   ETH -> PF_ETHUSD
#   etc.
#
# DATA VALIDATION:
#   Ticker price and latest closed candle price are compared.
#   If data is obviously wrong, NO TRADE is returned.
#
# ============================================================

import os
import time
import traceback
import requests
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

KRAKEN_BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    f"{KRAKEN_BASE_URL}/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    f"{KRAKEN_BASE_URL}/derivatives/api/v3/tickers"
)

CHART_URL = (
    f"{KRAKEN_BASE_URL}/api/charts/v1/trade"
)

TIMEFRAME_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240,
}

MAX_CANDLES = 500

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MAX_PULLBACK_CANDLES = 20

SL_BUFFER_PERCENT = 0.001

RR = 1.0

REQUEST_TIMEOUT = 20

INSTRUMENT_CACHE_SECONDS = 300


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Kraken-Futures-Telegram-Analyzer/4.0"
})


# ============================================================
# CACHE
# ============================================================

INSTRUMENT_CACHE = None
INSTRUMENT_CACHE_TIME = 0


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def normalize_asset(asset):
    asset = asset.upper().strip()

    if asset.startswith("/"):
        asset = asset[1:]

    # Kraken uses XBT for Bitcoin
    if asset == "BTC":
        return "XBT"

    return asset


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text, chat_id=None):

    token = TELEGRAM_BOT_TOKEN

    if not token:
        print("ERROR: TELEGRAM_BOT_TOKEN is missing")
        return False

    target = chat_id or TELEGRAM_CHAT_ID

    if not target:
        print("ERROR: TELEGRAM_CHAT_ID is missing")
        return False

    url = (
        f"https://api.telegram.org/bot{token}/sendMessage"
    )

    payload = {
        "chat_id": target,
        "text": text,
        "disable_web_page_preview": True,
    }

    try:

        r = session.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:
            print(
                "Telegram error:",
                r.status_code,
                r.text
            )
            return False

        return True

    except Exception as e:

        print("Telegram exception:", e)

        return False


# ============================================================
# KRAKEN HTTP
# ============================================================

def kraken_get(url, params=None):

    r = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    r.raise_for_status()

    data = r.json()

    return data


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments(force=False):

    global INSTRUMENT_CACHE
    global INSTRUMENT_CACHE_TIME

    current_time = time.time()

    if (
        not force
        and INSTRUMENT_CACHE is not None
        and current_time - INSTRUMENT_CACHE_TIME
        < INSTRUMENT_CACHE_SECONDS
    ):
        return INSTRUMENT_CACHE

    data = kraken_get(INSTRUMENTS_URL)

    instruments = data.get("instruments", [])

    if not instruments:
        raise RuntimeError(
            "Kraken returned no instruments."
        )

    INSTRUMENT_CACHE = instruments
    INSTRUMENT_CACHE_TIME = current_time

    return instruments


# ============================================================
# INSTRUMENT VALIDATION
# ============================================================

def instrument_text(inst):

    parts = []

    for key in (
        "symbol",
        "pair",
        "baseCurrency",
        "quoteCurrency",
        "underlying",
        "type",
        "contractType",
        "status",
    ):

        value = inst.get(key)

        if value is not None:
            parts.append(str(value).upper())

    return " ".join(parts)


def is_active_instrument(inst):

    text = instrument_text(inst)

    bad_statuses = (
        "DELISTED",
        "DELISTING",
        "EXPIRED",
        "SUSPENDED",
        "INACTIVE",
        "OFFLINE",
    )

    for bad in bad_statuses:

        if bad in text:
            return False

    status = str(
        inst.get("status", "")
    ).lower()

    if status:

        allowed = (
            "online",
            "active",
            "trading",
            "open",
        )

        if not any(x in status for x in allowed):

            # Some Kraken responses do not provide
            # a conventional status field.
            if status not in ("", "online"):
                return False

    return True


def is_perpetual(inst):

    symbol = str(
        inst.get("symbol", "")
    ).upper()

    text = instrument_text(inst)

    # PF_ is Kraken Futures perpetual convention
    if symbol.startswith("PF_"):
        return True

    # Other possible representations
    if "PERPETUAL" in text:
        return True

    if "PERP" in text:
        return True

    return False


def get_symbol(inst):

    return str(
        inst.get("symbol", "")
    ).upper().strip()


# ============================================================
# FIND ACTIVE PERPETUAL
# ============================================================

def find_futures_symbol(asset):

    asset = normalize_asset(asset)

    instruments = get_instruments()

    candidates = []

    for inst in instruments:

        symbol = get_symbol(inst)

        if not symbol:
            continue

        if not is_active_instrument(inst):
            continue

        if not is_perpetual(inst):
            continue

        text = instrument_text(inst)

        # ----------------------------------------------------
        # Strong matching
        # ----------------------------------------------------

        strong = False

        # PF_XBTUSD
        if symbol == f"PF_{asset}USD":
            strong = True

        # PF_XBTUSDT
        if symbol == f"PF_{asset}USDT":
            strong = True

        # underlying/base/pair matching
        fields = []

        for key in (
            "baseCurrency",
            "underlying",
            "pair",
        ):

            value = inst.get(key)

            if value:
                fields.append(
                    str(value).upper()
                )

        for value in fields:

            cleaned = (
                value
                .replace("/", "")
                .replace("-", "")
                .replace("_", "")
            )

            if cleaned == f"{asset}USD":
                strong = True

            if cleaned == f"{asset}USDT":
                strong = True

            if cleaned == asset:
                strong = True

        if strong:
            candidates.append(inst)

    if not candidates:

        # ----------------------------------------------------
        # Secondary search
        # ----------------------------------------------------

        for inst in instruments:

            symbol = get_symbol(inst)

            if not symbol:
                continue

            if not is_active_instrument(inst):
                continue

            if not is_perpetual(inst):
                continue

            text = instrument_text(inst)

            if asset in text:

                candidates.append(inst)

    if not candidates:

        raise RuntimeError(
            f"No active perpetual contract found for {asset}."
        )

    # --------------------------------------------------------
    # Ranking
    # --------------------------------------------------------

    def rank(inst):

        symbol = get_symbol(inst)

        score = 0

        if symbol == f"PF_{asset}USD":
            score += 1000

        if symbol == f"PF_{asset}USDT":
            score += 900

        if symbol.startswith("PF_"):
            score += 500

        text = instrument_text(inst)

        if "PERPETUAL" in text:
            score += 100

        if "ONLINE" in text:
            score += 50

        if "ACTIVE" in text:
            score += 50

        if "USD" in symbol:
            score += 20

        return score

    candidates.sort(
        key=rank,
        reverse=True
    )

    return candidates[0]


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = kraken_get(TICKERS_URL)

    tickers = data.get("tickers", [])

    if not tickers:
        raise RuntimeError(
            "Kraken returned no tickers."
        )

    return tickers


def get_ticker_for_symbol(symbol):

    tickers = get_tickers()

    symbol = symbol.upper()

    for ticker in tickers:

        tsymbol = str(
            ticker.get("symbol", "")
        ).upper()

        if tsymbol == symbol:
            return ticker

    # Some responses may use pair
    for ticker in tickers:

        tsymbol = str(
            ticker.get("pair", "")
        ).upper()

        if tsymbol == symbol:
            return ticker

    raise RuntimeError(
        f"Ticker not found for {symbol}"
    )


def extract_ticker_price(ticker):

    possible_fields = (
        "last",
        "lastPrice",
        "markPrice",
        "price",
    )

    for field in possible_fields:

        value = safe_float(
            ticker.get(field)
        )

        if value is not None and value > 0:
            return value

    # Kraken sometimes uses bid/ask only
    bid = safe_float(ticker.get("bid"))
    ask = safe_float(ticker.get("ask"))

    if (
        bid is not None
        and ask is not None
        and bid > 0
        and ask > 0
    ):
        return (bid + ask) / 2

    raise RuntimeError(
        "Could not extract ticker price."
    )


# ============================================================
# CHART DATA
# ============================================================

def get_chart(symbol, resolution):

    url = (
        f"{CHART_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    data = kraken_get(url)

    candles = data.get("candles")

    if candles is None:

        result = data.get("result")

        if isinstance(result, dict):
            candles = result.get("candles")

    if not candles:
        raise RuntimeError(
            f"No candles returned for {symbol} {resolution}m."
        )

    return candles


# ============================================================
# NORMALIZE CANDLES
# ============================================================

def normalize_candle(c):

    # Dictionary format
    if isinstance(c, dict):

        timestamp = (
            c.get("time")
            or c.get("timestamp")
            or c.get("ts")
        )

        open_price = (
            c.get("open")
            or c.get("o")
        )

        high = (
            c.get("high")
            or c.get("h")
        )

        low = (
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

    # List format
    elif isinstance(c, (list, tuple)):

        if len(c) < 5:
            return None

        timestamp = c[0]
        open_price = c[1]
        high = c[2]
        low = c[3]
        close = c[4]

        volume = c[5] if len(c) > 5 else 0

    else:
        return None

    timestamp = safe_float(timestamp)

    open_price = safe_float(open_price)
    high = safe_float(high)
    low = safe_float(low)
    close = safe_float(close)
    volume = safe_float(volume, 0)

    if None in (
        timestamp,
        open_price,
        high,
        low,
        close,
    ):
        return None

    # Normalize milliseconds
    if timestamp > 10_000_000_000:
        timestamp /= 1000.0

    return {
        "time": timestamp,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def normalize_candles(raw):

    result = []

    for item in raw:

        candle = normalize_candle(item)

        if candle is not None:
            result.append(candle)

    result.sort(
        key=lambda x: x["time"]
    )

    return result


# ============================================================
# CLOSED CANDLES ONLY
# ============================================================

def get_closed_candles(
    symbol,
    resolution,
    limit=MAX_CANDLES
):

    raw = get_chart(
        symbol,
        resolution
    )

    candles = normalize_candles(raw)

    if len(candles) < 20:
        raise RuntimeError(
            f"Not enough candles for {symbol}."
        )

    current_time = time.time()

    timeframe_seconds = (
        resolution * 60
    )

    closed = []

    for candle in candles:

        candle_close_time = (
            candle["time"]
            + timeframe_seconds
        )

        if candle_close_time <= current_time:

            closed.append(candle)

    if len(closed) < 20:
        raise RuntimeError(
            "Not enough CLOSED candles."
        )

    return closed[-limit:]


# ============================================================
# PRICE VALIDATION
# ============================================================

def validate_market_data(
    ticker_price,
    candles,
    symbol
):

    last_close = candles[-1]["close"]

    if ticker_price <= 0 or last_close <= 0:
        raise RuntimeError(
            "Invalid market price."
        )

    difference = (
        abs(ticker_price - last_close)
        / last_close
    ) * 100

    # Normal difference between ticker and
    # latest closed candle can be small.
    #
    # 15% is deliberately conservative.
    # If this occurs, something is almost certainly wrong.
    if difference > 15:

        raise RuntimeError(
            "DATA ERROR: "
            f"Ticker={ticker_price:.8f}, "
            f"LastClosed={last_close:.8f}, "
            f"Difference={difference:.2f}% "
            f"for {symbol}"
        )

    return difference


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(candles):

    highs = []
    lows = []

    left = PIVOT_LEFT
    right = PIVOT_RIGHT

    for i in range(
        left,
        len(candles) - right
    ):

        current_high = candles[i]["high"]
        current_low = candles[i]["low"]

        left_highs = [
            candles[j]["high"]
            for j in range(i - left, i)
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + right + 1
            )
        ]

        left_lows = [
            candles[j]["low"]
            for j in range(i - left, i)
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + right + 1
            )
        ]

        if (
            current_high > max(left_highs)
            and current_high > max(right_highs)
        ):
            highs.append({
                "index": i,
                "price": current_high,
            })

        if (
            current_low < min(left_lows)
            and current_low < min(right_lows)
        ):
            lows.append({
                "index": i,
                "price": current_low,
            })

    return highs, lows


# ============================================================
# TREND
# ============================================================

def determine_trend(highs, lows):

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    h1 = highs[-2]["price"]
    h2 = highs[-1]["price"]

    l1 = lows[-2]["price"]
    l2 = lows[-1]["price"]

    if h2 > h1 and l2 > l1:
        return "BULLISH"

    if h2 < h1 and l2 < l1:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# TREND BREAK
# ============================================================

def find_latest_break(
    candles,
    highs,
    lows
):

    candidates = []

    # Bullish break
    for pivot in highs:

        start = pivot["index"] + 1

        for i in range(
            start,
            len(candles)
        ):

            if (
                candles[i]["close"]
                > pivot["price"]
            ):

                candidates.append({
                    "type": "BULLISH_BREAK",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"],
                })

                break

    # Bearish break
    for pivot in lows:

        start = pivot["index"] + 1

        for i in range(
            start,
            len(candles)
        ):

            if (
                candles[i]["close"]
                < pivot["price"]
            ):

                candidates.append({
                    "type": "BEARISH_BREAK",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"],
                })

                break

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: x["index"]
    )

    return candidates[-1]


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

    body = abs(
        c["close"] - c["open"]
    )

    upper = (
        c["high"]
        - max(c["open"], c["close"])
    )

    lower = (
        min(c["open"], c["close"])
        - c["low"]
    )

    if body == 0:
        body = 1e-9

    return (
        lower >= body * 2
        and upper <= body
    )


def shooting_star(c):

    body = abs(
        c["close"] - c["open"]
    )

    upper = (
        c["high"]
        - max(c["open"], c["close"])
    )

    lower = (
        min(c["open"], c["close"])
        - c["low"]
    )

    if body == 0:
        body = 1e-9

    return (
        upper >= body * 2
        and lower <= body
    )


def bullish_pinbar(c):

    body = abs(
        c["close"] - c["open"]
    )

    lower = (
        min(c["open"], c["close"])
        - c["low"]
    )

    if body == 0:
        body = 1e-9

    return lower >= body * 2.5


def bearish_pinbar(c):

    body = abs(
        c["close"] - c["open"]
    )

    upper = (
        c["high"]
        - max(c["open"], c["close"])
    )

    if body == 0:
        body = 1e-9

    return upper >= body * 2.5


def strong_bullish(c):

    total = (
        c["high"]
        - c["low"]
    )

    body = abs(
        c["close"]
        - c["open"]
    )

    if total <= 0:
        return False

    return (
        c["close"] > c["open"]
        and body / total >= 0.65
    )


def strong_bearish(c):

    total = (
        c["high"]
        - c["low"]
    )

    body = abs(
        c["close"]
        - c["open"]
    )

    if total <= 0:
        return False

    return (
        c["close"] < c["open"]
        and body / total >= 0.65
    )


def bullish_reversal_name(
    candles,
    index
):

    if index < 1:
        return None

    prev = candles[index - 1]
    curr = candles[index]

    if bullish_engulfing(prev, curr):
        return "Bullish Engulfing"

    if hammer(curr):
        return "Hammer"

    if bullish_pinbar(curr):
        return "Bullish Pin Bar"

    if strong_bullish(curr):
        return "Strong Bullish Candle"

    return None


def bearish_reversal_name(
    candles,
    index
):

    if index < 1:
        return None

    prev = candles[index - 1]
    curr = candles[index]

    if bearish_engulfing(prev, curr):
        return "Bearish Engulfing"

    if shooting_star(curr):
        return "Shooting Star"

    if bearish_pinbar(curr):
        return "Bearish Pin Bar"

    if strong_bearish(curr):
        return "Strong Bearish Candle"

    return None


# ============================================================
# PULLBACK + REVERSAL
# ============================================================

def find_setup(
    candles,
    trend_break
):

    if trend_break is None:
        return None

    break_index = trend_break["index"]
    level = trend_break["level"]

    start = break_index + 1

    end = min(
        len(candles),
        start + MAX_PULLBACK_CANDLES
    )

    if start >= end:
        return None

    break_type = trend_break["type"]

    # --------------------------------------------------------
    # Bullish setup
    # --------------------------------------------------------

    if break_type == "BULLISH_BREAK":

        for i in range(start, end):

            candle = candles[i]

            touched = (
                candle["low"]
                <= level * 1.003
            )

            reclaimed = (
                candle["close"]
                > level
            )

            reversal = (
                bullish_reversal_name(
                    candles,
                    i
                )
            )

            if (
                touched
                and reclaimed
                and reversal
            ):

                entry = candle["close"]

                sl_base = candle["low"]

                sl = (
                    sl_base
                    * (1 - SL_BUFFER_PERCENT)
                )

                risk = entry - sl

                if risk <= 0:
                    continue

                tp = entry + (
                    risk * RR
                )

                return {
                    "side": "LONG",
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "reversal": reversal,
                    "pullback": True,
                    "trigger_index": i,
                    "break_level": level,
                }

    # --------------------------------------------------------
    # Bearish setup
    # --------------------------------------------------------

    if break_type == "BEARISH_BREAK":

        for i in range(start, end):

            candle = candles[i]

            touched = (
                candle["high"]
                >= level * 0.997
            )

            rejected = (
                candle["close"]
                < level
            )

            reversal = (
                bearish_reversal_name(
                    candles,
                    i
                )
            )

            if (
                touched
                and rejected
                and reversal
            ):

                entry = candle["close"]

                sl_base = candle["high"]

                sl = (
                    sl_base
                    * (1 + SL_BUFFER_PERCENT)
                )

                risk = sl - entry

                if risk <= 0:
                    continue

                tp = entry - (
                    risk * RR
                )

                return {
                    "side": "SHORT",
                    "entry": entry,
                    "sl": sl,
                    "tp": tp,
                    "reversal": reversal,
                    "pullback": True,
                    "trigger_index": i,
                    "break_level": level,
                }

    return None


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(price):

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:,.4f}"

    if price >= 0.01:
        return f"{price:,.6f}"

    return f"{price:,.8f}"


# ============================================================
# ANALYZE
# ============================================================

def analyze(asset, timeframe):

    timeframe = timeframe.lower().strip()

    if timeframe not in TIMEFRAME_MAP:

        raise ValueError(
            "Invalid timeframe. "
            "Use: 1m, 5m, 15m, 30m, 1h, 4h"
        )

    # --------------------------------------------------------
    # Find correct active perpetual
    # --------------------------------------------------------

    instrument = find_futures_symbol(asset)

    symbol = get_symbol(instrument)

    # --------------------------------------------------------
    # Ticker
    # --------------------------------------------------------

    ticker = get_ticker_for_symbol(symbol)

    ticker_price = extract_ticker_price(
        ticker
    )

    # --------------------------------------------------------
    # Closed candles
    # --------------------------------------------------------

    resolution = TIMEFRAME_MAP[timeframe]

    candles = get_closed_candles(
        symbol,
        resolution
    )

    # --------------------------------------------------------
    # Critical validation
    # --------------------------------------------------------

    difference = validate_market_data(
        ticker_price,
        candles,
        symbol
    )

    # --------------------------------------------------------
    # Pivots
    # --------------------------------------------------------

    highs, lows = find_pivots(
        candles
    )

    trend = determine_trend(
        highs,
        lows
    )

    trend_break = find_latest_break(
        candles,
        highs,
        lows
    )

    setup = find_setup(
        candles,
        trend_break
    )

    last_close = candles[-1]["close"]

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    lines = []

    lines.append(
        "🔎 KRAKEN FUTURES LIVE CHECK"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    display_asset = (
        "BTC"
        if normalize_asset(asset) == "XBT"
        else normalize_asset(asset)
    )

    lines.append(
        f"🪙 {display_asset} "
        f"({symbol})"
    )

    lines.append(
        f"⏱ {timeframe.upper()} "
        f"| CLOSED CANDLES ONLY"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"💵 Market Price: "
        f"{format_price(ticker_price)}"
    )

    lines.append(
        f"🕯 Last Closed: "
        f"{format_price(last_close)}"
    )

    lines.append(
        f"📏 Data Difference: "
        f"{difference:.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📊 TREND: {trend}"
    )

    if trend_break:

        break_name = (
            "Bullish Break"
            if trend_break["type"]
            == "BULLISH_BREAK"
            else "Bearish Break"
        )

        lines.append(
            f"🔄 Structure Break: "
            f"✅ {break_name}"
        )

        lines.append(
            f"Level: "
            f"{format_price(trend_break['level'])}"
        )

    else:

        lines.append(
            "🔄 Structure Break: ❌"
        )

    if setup:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🚨 SETUP CONFIRMED"
        )

        if setup["side"] == "LONG":
            lines.append(
                "🟢 LONG"
            )
        else:
            lines.append(
                "🔴 SHORT"
            )

        lines.append(
            f"Entry: "
            f"{format_price(setup['entry'])}"
        )

        lines.append(
            f"SL: "
            f"{format_price(setup['sl'])}"
        )

        lines.append(
            f"TP 1:1: "
            f"{format_price(setup['tp'])}"
        )

        lines.append(
            f"🕯 Reversal: "
            f"{setup['reversal']}"
        )

        lines.append(
            "Pullback/Retest: ✅"
        )

    else:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "⛔ VERDICT: NO TRADE"
        )

        if trend == "BEARISH":
            lines.append(
                "Bias: 🔴 SHORT"
            )

        elif trend == "BULLISH":
            lines.append(
                "Bias: 🟢 LONG"
            )

        else:
            lines.append(
                "Bias: 🟡 NEUTRAL"
            )

        lines.append(
            "Reason:"
        )

        if not trend_break:
            lines.append(
                "• No confirmed structure break"
            )
        else:
            lines.append(
                "• No valid pullback/retest + "
                "reversal confirmation"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 {now_utc()}"
    )

    return "\n".join(lines)


# ============================================================
# COMMAND PARSER
# ============================================================

def parse_command(text):

    text = text.strip()

    if not text:
        return None

    text_lower = text.lower()

    # --------------------------------------------------------
    # Remove command prefix
    # --------------------------------------------------------

    if text_lower.startswith("/check"):

        text = text[6:].strip()

    elif text_lower.startswith("تحلیل"):

        text = text[5:].strip()

    else:

        return None

    parts = text.split()

    if len(parts) < 2:
        return {
            "error": (
                "فرمت صحیح:\n\n"
                "تحلیل BTC 5m\n"
                "تحلیل ATOM 15m\n\n"
                "یا:\n"
                "/check BTC 5m"
            )
        }

    asset = parts[0].upper()
    timeframe = parts[1].lower()

    return {
        "asset": asset,
        "timeframe": timeframe,
    }


# ============================================================
# TELEGRAM UPDATE
# ============================================================

def get_updates(offset=None):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/getUpdates"
    )

    params = {
        "timeout": 30,
    }

    if offset is not None:
        params["offset"] = offset

    r = session.get(
        url,
        params=params,
        timeout=40
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram getUpdates failed: {data}"
        )

    return data.get("result", [])


# ============================================================
# PROCESS COMMAND
# ============================================================

def process_message(message):

    if not message:
        return

    chat = message.get("chat", {})

    chat_id = chat.get("id")

    text = message.get("text", "")

    if not text:
        return

    parsed = parse_command(text)

    if parsed is None:
        return

    if "error" in parsed:

        telegram_send(
            parsed["error"],
            chat_id
        )

        return

    asset = parsed["asset"]

    timeframe = parsed["timeframe"]

    telegram_send(
        (
            f"🔎 در حال بررسی {asset} "
            f"{timeframe.upper()}...\n"
            f"داده جدید Kraken در حال دریافت است."
        ),
        chat_id
    )

    try:

        report = analyze(
            asset,
            timeframe
        )

        telegram_send(
            report,
            chat_id
        )

    except Exception as e:

        print(
            "ANALYSIS ERROR:",
            repr(e)
        )

        error_text = str(e)

        telegram_send(
            (
                "⚠️ DATA / ANALYSIS ERROR\n"
                "━━━━━━━━━━━━━━━━━━\n"
                f"Asset: {asset}\n"
                f"Timeframe: {timeframe}\n\n"
                f"{error_text}\n"
                "━━━━━━━━━━━━━━━━━━\n"
                "تحلیل انجام نشد تا از ارسال "
                "سیگنال اشتباه جلوگیری شود."
            ),
            chat_id
        )


# ============================================================
# TELEGRAM CONNECTION TEST
# ============================================================

def telegram_test():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing."
        )

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/getMe"
    )

    r = session.get(
        url,
        timeout=REQUEST_TIMEOUT
    )

    r.raise_for_status()

    data = r.json()

    if not data.get("ok"):

        raise RuntimeError(
            "Telegram bot token is invalid."
        )

    bot = data.get("result", {})

    print(
        "Telegram bot:",
        bot.get("username")
    )


# ============================================================
# MAIN LISTENER
# ============================================================

def main():

    print(
        "======================================"
    )

    print(
        "KRAKEN FUTURES TELEGRAM ANALYZER v4.0"
    )

    print(
        "======================================"
    )

    telegram_test()

    print(
        "Listener started."
    )

    print(
        "Commands:"
    )

    print(
        "  تحلیل BTC 5m"
    )

    print(
        "  تحلیل ATOM 15m"
    )

    print(
        "  /check BTC 5m"
    )

    offset = None

    while True:

        try:

            updates = get_updates(
                offset
            )

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = update_id + 1

                message = update.get(
                    "message"
                )

                if message:

                    process_message(
                        message
                    )

        except KeyboardInterrupt:

            print(
                "Listener stopped."
            )

            break

        except Exception as e:

            print(
                "LISTENER ERROR:",
                repr(e)
            )

            traceback.print_exc()

            time.sleep(10)


# ============================================================
# AUTO RESTART
# ============================================================

if __name__ == "__main__":

    while True:

        try:

            main()

        except KeyboardInterrupt:

            print(
                "Program stopped manually."
            )

            break

        except Exception as e:

            print(
                "FATAL ERROR:",
                repr(e)
            )

            traceback.print_exc()

            print(
                "Restarting in 10 seconds..."
            )

            time.sleep(10)
