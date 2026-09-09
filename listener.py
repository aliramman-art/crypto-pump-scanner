# ============================================================
# TELEGRAM KRAKEN FUTURES STRATEGY LISTENER v3.0
# ============================================================
#
# COMMANDS:
#
#   تحلیل BTC 5m
#   تحلیل ATOM 15m
#   تحلیل ETH 1h
#   تحلیل SOL 4h
#
#   /check BTC 15m
#
# STRATEGY:
#
#   1. Real Kraken Futures instrument discovery
#   2. Closed candles only
#   3. Valid pivot detection
#   4. Trend detection
#   5. Trend break
#   6. Pullback / retest
#   7. Reversal candle confirmation
#   8. Entry
#   9. Structural SL
#   10. RR 1:1 TP
#
# TELEGRAM:
#   This file is the ONLY Telegram getUpdates listener.
#
# ENV:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# REQUIREMENTS:
#   requests
# ============================================================

import os
import time
import traceback
import requests


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

KRAKEN_BASE_URL = "https://futures.kraken.com"

TELEGRAM_TIMEOUT = 35
KRAKEN_TIMEOUT = 20

POLL_RETRY_SECONDS = 5
ERROR_RETRY_SECONDS = 10

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

MAX_CANDLES = 500

MAX_PULLBACK_CANDLES = 20

SL_BUFFER_PERCENT = 0.001

RR = 1.0


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Kraken-Futures-Telegram-Listener/3.0",
    "Accept": "application/json"
})


# ============================================================
# TIMEFRAME
# ============================================================

TIMEFRAME_MAP = {
    "1m": 1,
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "4h": 240
}


def normalize_timeframe(tf):

    tf = str(tf).strip().lower()

    aliases = {
        "1": "1m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "240": "4h",
        "60m": "1h",
        "240m": "4h"
    }

    tf = aliases.get(tf, tf)

    if tf not in TIMEFRAME_MAP:
        raise ValueError(
            "تایم‌فریم نامعتبر است."
        )

    return tf


# ============================================================
# SYMBOL CACHE
# ============================================================

INSTRUMENT_CACHE = {}
INSTRUMENT_CACHE_TIME = 0

INSTRUMENT_CACHE_SECONDS = 300


# ============================================================
# TELEGRAM
# ============================================================

def telegram_url(method):

    return (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/{method}"
    )


def send_telegram(text, chat_id=None):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "ERROR: TELEGRAM_BOT_TOKEN missing."
        )

        return False

    target_chat = (
        chat_id
        or TELEGRAM_CHAT_ID
    )

    if not target_chat:

        print(
            "ERROR: TELEGRAM_CHAT_ID missing."
        )

        return False

    payload = {
        "chat_id": target_chat,
        "text": text
    }

    for attempt in range(3):

        try:

            response = session.post(
                telegram_url("sendMessage"),
                json=payload,
                timeout=TELEGRAM_TIMEOUT
            )

            if response.ok:
                return True

            print(
                "Telegram error:",
                response.status_code,
                response.text[:1000]
            )

        except Exception as e:

            print(
                "Telegram send exception:",
                repr(e)
            )

        time.sleep(2)

    return False


def telegram_get_updates(offset=None):

    params = {
        "timeout": TELEGRAM_TIMEOUT
    }

    if offset is not None:
        params["offset"] = offset

    response = session.get(
        telegram_url("getUpdates"),
        params=params,
        timeout=TELEGRAM_TIMEOUT + 10
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):

        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data.get(
        "result",
        []
    )


# ============================================================
# KRAKEN API
# ============================================================

def kraken_get(path, params=None):

    url = (
        KRAKEN_BASE_URL
        + path
    )

    response = session.get(
        url,
        params=params or {},
        timeout=KRAKEN_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    return data


# ============================================================
# INSTRUMENT DISCOVERY
# ============================================================

def load_instruments():

    global INSTRUMENT_CACHE
    global INSTRUMENT_CACHE_TIME

    now = time.time()

    if (
        INSTRUMENT_CACHE
        and
        now - INSTRUMENT_CACHE_TIME
        < INSTRUMENT_CACHE_SECONDS
    ):

        return INSTRUMENT_CACHE

    data = kraken_get(
        "/derivatives/api/v3/instruments"
    )

    instruments = (
        data.get("instruments")
        or data.get("result")
        or []
    )

    if not instruments:

        raise RuntimeError(
            "Kraken returned no instruments."
        )

    cache = {}

    for item in instruments:

        try:

            symbol = str(
                item.get("symbol", "")
            ).upper()

            if not symbol:
                continue

            # We only want perpetual contracts
            tradeable = item.get(
                "tradeable",
                True
            )

            if tradeable is False:
                continue

            contract_type = str(
                item.get(
                    "contractType",
                    ""
                )
            ).lower()

            quote_currency = str(
                item.get(
                    "quoteCurrency",
                    ""
                )
            ).upper()

            # Store every usable instrument
            cache[symbol] = item

        except Exception:
            continue

    INSTRUMENT_CACHE = cache
    INSTRUMENT_CACHE_TIME = now

    print(
        f"Loaded Kraken instruments: {len(cache)}"
    )

    return cache


# ============================================================
# SYMBOL CLEANING
# ============================================================

def clean_asset_name(symbol):

    symbol = (
        str(symbol)
        .upper()
        .strip()
    )

    symbol = symbol.replace(
        "/",
        ""
    )

    symbol = symbol.replace(
        "-",
        ""
    )

    symbol = symbol.replace(
        "_",
        ""
    )

    # Remove common quote suffixes
    for suffix in (
        "USDT",
        "USD",
        "USDC"
    ):

        if symbol.endswith(suffix):

            symbol = symbol[
                :-len(suffix)
            ]

            break

    return symbol


def find_futures_symbol(user_symbol):

    requested = clean_asset_name(
        user_symbol
    )

    instruments = load_instruments()

    candidates = []

    # --------------------------------------------------------
    # Exact symbol / asset matching
    # --------------------------------------------------------

    for symbol, item in instruments.items():

        symbol_upper = symbol.upper()

        base = str(
            item.get(
                "baseCurrency",
                ""
            )
        ).upper()

        quote = str(
            item.get(
                "quoteCurrency",
                ""
            )
        ).upper()

        pair = str(
            item.get(
                "pair",
                ""
            )
        ).upper()

        # Direct base currency match
        if base == requested:

            candidates.append(
                (
                    100,
                    symbol
                )
            )

            continue

        # Pair matching
        if clean_asset_name(pair) == requested:

            candidates.append(
                (
                    90,
                    symbol
                )
            )

            continue

        # Symbol matching
        if clean_asset_name(symbol_upper) == requested:

            candidates.append(
                (
                    80,
                    symbol
                )

    if not candidates:

        # Fallback: symbol contains asset
        for symbol, item in instruments.items():

            base = str(
                item.get(
                    "baseCurrency",
                    ""
                )
            ).upper()

            if base == requested:

                candidates.append(
                    (
                        50,
                        symbol
                    )
                )

    if not candidates:

        raise ValueError(
            f"Kraken Futures symbol not found: "
            f"{requested}"
        )

    candidates.sort(
        key=lambda x: (
            x[0],
            x[1]
        ),
        reverse=True
    )

    selected = candidates[0][1]

    print(
        f"Symbol mapping: "
        f"{user_symbol} -> {selected}"
    )

    return selected


# ============================================================
# CANDLE RESPONSE PARSER
# ============================================================

def parse_candle_item(item):

    if isinstance(item, dict):

        timestamp = (
            item.get("time")
            or item.get("timestamp")
            or item.get("ts")
        )

        open_price = (
            item.get("open")
            or item.get("o")
        )

        high_price = (
            item.get("high")
            or item.get("h")
        )

        low_price = (
            item.get("low")
            or item.get("l")
        )

        close_price = (
            item.get("close")
            or item.get("c")
        )

        volume = (
            item.get("volume")
            or item.get("v")
            or 0
        )

    elif isinstance(item, list):

        if len(item) < 5:
            return None

        timestamp = item[0]
        open_price = item[1]
        high_price = item[2]
        low_price = item[3]
        close_price = item[4]

        volume = (
            item[5]
            if len(item) > 5
            else 0
        )

    else:

        return None

    try:

        return {
            "time": float(timestamp),
            "open": float(open_price),
            "high": float(high_price),
            "low": float(low_price),
            "close": float(close_price),
            "volume": float(volume)
        }

    except Exception:

        return None


# ============================================================
# GET CANDLES
# ============================================================

def get_kraken_candles(
    futures_symbol,
    timeframe,
    limit=MAX_CANDLES
):

    timeframe = normalize_timeframe(
        timeframe
    )

    resolution = TIMEFRAME_MAP[
        timeframe
    ]

    path = (
        "/api/charts/v1/trade/"
        f"{futures_symbol}/"
        f"{resolution}"
    )

    data = kraken_get(
        path
    )

    raw = []

    if isinstance(data, dict):

        raw = (
            data.get("candles")
            or data.get("data")
            or data.get("result")
            or []
        )

    elif isinstance(data, list):

        raw = data

    candles = []

    for item in raw:

        candle = parse_candle_item(
            item
        )

        if candle:

            candles.append(
                candle
            )

    if not candles:

        raise RuntimeError(
            "Kraken returned no candle data "
            f"for {futures_symbol} "
            f"{timeframe}."
        )

    # Sort
    candles.sort(
        key=lambda x: x["time"]
    )

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

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    seconds = (
        resolution * 60
    )

    now = time.time()

    closed = []

    for candle in candles:

        candle_end = (
            candle["time"]
            + seconds
        )

        if candle_end <= now:

            closed.append(
                candle
            )

    if len(closed) > limit:

        closed = closed[-limit:]

    if len(closed) < 50:

        raise RuntimeError(
            f"Not enough closed candles: "
            f"{len(closed)}"
        )

    return closed


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return 0.0

    trs = []

    for i in range(
        1,
        len(candles)
    ):

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
            )
        )

        trs.append(
            tr
        )

    if len(trs) < period:

        return 0.0

    return (
        sum(
            trs[-period:]
        )
        / period
    )


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

        high = candles[i]["high"]
        low = candles[i]["low"]

        is_high = True
        is_low = True

        for j in range(
            i - left,
            i + right + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] >= high:

                is_high = False

            if candles[j]["low"] <= low:

                is_low = False

        if is_high:

            highs.append({
                "index": i,
                "price": high
            })

        if is_low:

            lows.append({
                "index": i,
                "price": low
            })

    return highs, lows


# ============================================================
# TREND
# ============================================================

def determine_trend(
    highs,
    lows
):

    if (
        len(highs) < 2
        or
        len(lows) < 2
    ):

        return "RANGE"

    h1 = highs[-2]["price"]
    h2 = highs[-1]["price"]

    l1 = lows[-2]["price"]
    l2 = lows[-1]["price"]

    if (
        h2 > h1
        and
        l2 > l1
    ):

        return "UP"

    if (
        h2 < h1
        and
        l2 < l1
    ):

        return "DOWN"

    return "RANGE"


# ============================================================
# BREAK DETECTION
# ============================================================

def detect_break(
    candles,
    highs,
    lows,
    trend
):

    candidates = []

    # --------------------------------------------------------
    # UP TREND
    # Bearish break
    # --------------------------------------------------------

    if trend == "UP" and lows:

        pivot = lows[-1]

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            if (
                candles[i]["close"]
                < pivot["price"]
            ):

                candidates.append({
                    "direction": "BEARISH",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"]
                })

    # --------------------------------------------------------
    # DOWN TREND
    # Bullish break
    # --------------------------------------------------------

    if trend == "DOWN" and highs:

        pivot = highs[-1]

        for i in range(
            pivot["index"] + 1,
            len(candles)
        ):

            if (
                candles[i]["close"]
                > pivot["price"]
            ):

                candidates.append({
                    "direction": "BULLISH",
                    "index": i,
                    "level": pivot["price"],
                    "pivot_index": pivot["index"]
                })

    # --------------------------------------------------------
    # RANGE
    # --------------------------------------------------------

    if trend == "RANGE":

        if highs:

            pivot = highs[-1]

            for i in range(
                pivot["index"] + 1,
                len(candles)
            ):

                if (
                    candles[i]["close"]
                    > pivot["price"]
                ):

                    candidates.append({
                        "direction": "BULLISH",
                        "index": i,
                        "level": pivot["price"],
                        "pivot_index": pivot["index"]
                    })

        if lows:

            pivot = lows[-1]

            for i in range(
                pivot["index"] + 1,
                len(candles)
            ):

                if (
                    candles[i]["close"]
                    < pivot["price"]
                ):

                    candidates.append({
                        "direction": "BEARISH",
                        "index": i,
                        "level": pivot["price"],
                        "pivot_index": pivot["index"]
                    })

    if not candidates:

        return None

    candidates.sort(
        key=lambda x: x["index"]
    )

    # Most recent confirmed break
    return candidates[-1]


# ============================================================
# CANDLE COMPONENTS
# ============================================================

def candle_parts(c):

    body = abs(
        c["close"]
        - c["open"]
    )

    total = (
        c["high"]
        - c["low"]
    )

    upper = (
        c["high"]
        -
        max(
            c["open"],
            c["close"]
        )
    )

    lower = (
        min(
            c["open"],
            c["close"]
        )
        -
        c["low"]
    )

    return (
        body,
        total,
        upper,
        lower
    )


# ============================================================
# REVERSAL PATTERNS
# ============================================================

def bullish_engulfing(
    previous,
    current
):

    return (
        previous["close"]
        < previous["open"]
        and
        current["close"]
        > current["open"]
        and
        current["open"]
        <= previous["close"]
        and
        current["close"]
        >= previous["open"]
    )


def bearish_engulfing(
    previous,
    current
):

    return (
        previous["close"]
        > previous["open"]
        and
        current["close"]
        < current["open"]
        and
        current["open"]
        >= previous["close"]
        and
        current["close"]
        <= previous["open"]
    )


def hammer(
    candle,
    atr
):

    body, total, upper, lower = (
        candle_parts(candle)
    )

    if total <= 0:
        return False

    return (
        body >= atr * 0.05
        and
        lower >= body * 2
        and
        upper <= body
    )


def shooting_star(
    candle,
    atr
):

    body, total, upper, lower = (
        candle_parts(candle)
    )

    if total <= 0:
        return False

    return (
        body >= atr * 0.05
        and
        upper >= body * 2
        and
        lower <= body
    )


def bullish_pin_bar(c):

    body, total, upper, lower = (
        candle_parts(c)
    )

    if total <= 0:
        return False

    return (
        lower >= body * 2
        and
        lower >= upper * 1.5
    )


def bearish_pin_bar(c):

    body, total, upper, lower = (
        candle_parts(c)
    )

    if total <= 0:
        return False

    return (
        upper >= body * 2
        and
        upper >= lower * 1.5
    )


def strong_bullish(
    candle,
    atr
):

    body, total, upper, lower = (
        candle_parts(candle)
    )

    if total <= 0:
        return False

    return (
        candle["close"]
        > candle["open"]
        and
        body >= atr * 0.5
        and
        candle["close"]
        >=
        candle["high"]
        -
        total * 0.30
    )


def strong_bearish(
    candle,
    atr
):

    body, total, upper, lower = (
        candle_parts(candle)
    )

    if total <= 0:
        return False

    return (
        candle["close"]
        < candle["open"]
        and
        body >= atr * 0.5
        and
        candle["close"]
        <=
        candle["low"]
        +
        total * 0.30
    )


# ============================================================
# PULLBACK + REVERSAL
# ============================================================

def reversal_confirmation(
    candles,
    break_info
):

    break_index = (
        break_info["index"]
    )

    direction = (
        break_info["direction"]
    )

    level = (
        break_info["level"]
    )

    atr = calculate_atr(
        candles
    )

    if atr <= 0:

        return None

    start = (
        break_index + 1
    )

    end = min(
        len(candles),
        start + MAX_PULLBACK_CANDLES
    )

    for i in range(
        start,
        end
    ):

        candle = candles[i]

        tolerance = max(
            atr * 0.30,
            abs(level) * 0.001
        )

        touched = (
            candle["low"]
            <= level + tolerance
            and
            candle["high"]
            >= level - tolerance
        )

        if not touched:
            continue

        previous = (
            candles[i - 1]
            if i > 0
            else None
        )

        pattern = None

        if direction == "BULLISH":

            if (
                previous
                and
                bullish_engulfing(
                    previous,
                    candle
                )
            ):

                pattern = (
                    "Bullish Engulfing"
                )

            elif hammer(
                candle,
                atr
            ):

                pattern = "Hammer"

            elif bullish_pin_bar(
                candle
            ):

                pattern = (
                    "Bullish Pin Bar"
                )

            elif strong_bullish(
                candle,
                atr
            ):

                pattern = (
                    "Strong Bullish Candle"
                )

            valid_close = (
                candle["close"]
                > level
            )

        else:

            if (
                previous
                and
                bearish_engulfing(
                    previous,
                    candle
                )
            ):

                pattern = (
                    "Bearish Engulfing"
                )

            elif shooting_star(
                candle,
                atr
            ):

                pattern = (
                    "Shooting Star"
                )

            elif bearish_pin_bar(
                candle
            ):

                pattern = (
                    "Bearish Pin Bar"
                )

            elif strong_bearish(
                candle,
                atr
            ):

                pattern = (
                    "Strong Bearish Candle"
                )

            valid_close = (
                candle["close"]
                < level
            )

        if pattern and valid_close:

            return {
                "index": i,
                "pattern": pattern,
                "level": level
            }

    return None


# ============================================================
# TRADE SETUP
# ============================================================

def build_trade_setup(
    candles,
    break_info,
    confirmation
):

    direction = (
        break_info["direction"]
    )

    break_index = (
        break_info["index"]
    )

    confirmation_index = (
        confirmation["index"]
    )

    candle = candles[
        confirmation_index
    ]

    entry = candle["close"]

    if direction == "BULLISH":

        structural_low = min(
            c["low"]
            for c in candles[
                break_index:
                confirmation_index + 1
            ]
        )

        sl = (
            structural_low
            *
            (
                1
                -
                SL_BUFFER_PERCENT
            )
        )

        risk = (
            entry - sl
        )

        if risk <= 0:

            return None

        tp = (
            entry
            +
            risk * RR
        )

        side = "LONG"

    else:

        structural_high = max(
            c["high"]
            for c in candles[
                break_index:
                confirmation_index + 1
            ]
        )

        sl = (
            structural_high
            *
            (
                1
                +
                SL_BUFFER_PERCENT
            )
        )

        risk = (
            sl - entry
        )

        if risk <= 0:

            return None

        tp = (
            entry
            -
            risk * RR
        )

        side = "SHORT"

    return {
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "pattern": confirmation[
            "pattern"
        ],
        "break_level": break_info[
            "level"
        ]
    }


# ============================================================
# ANALYSIS
# ============================================================

def analyze(
    user_symbol,
    timeframe
):

    timeframe = normalize_timeframe(
        timeframe
    )

    futures_symbol = (
        find_futures_symbol(
            user_symbol
        )
    )

    candles = get_kraken_candles(
        futures_symbol,
        timeframe
    )

    highs, lows = find_pivots(
        candles
    )

    trend = determine_trend(
        highs,
        lows
    )

    break_info = detect_break(
        candles,
        highs,
        lows,
        trend
    )

    result = {
        "requested_symbol": (
            user_symbol.upper()
        ),
        "kraken_symbol": futures_symbol,
        "timeframe": timeframe,
        "trend": trend,
        "candles": len(candles),
        "pivots_high": len(highs),
        "pivots_low": len(lows),
        "break": break_info,
        "confirmation": None,
        "setup": None
    }

    if not break_info:

        return result

    confirmation = (
        reversal_confirmation(
            candles,
            break_info
        )
    )

    result[
        "confirmation"
    ] = confirmation

    if not confirmation:

        return result

    setup = build_trade_setup(
        candles,
        break_info,
        confirmation
    )

    result[
        "setup"
    ] = setup

    return result


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# TREND TEXT
# ============================================================

def trend_text(
    trend
):

    return {
        "UP": "🟢 صعودی",
        "DOWN": "🔴 نزولی",
        "RANGE": "🟡 رنج"
    }.get(
        trend,
        trend
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    result
):

    symbol = result[
        "requested_symbol"
    ]

    kraken_symbol = result[
        "kraken_symbol"
    ]

    timeframe = result[
        "timeframe"
    ]

    lines = []

    lines.append(
        "📊 KRAKEN STRATEGY ANALYSIS"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"💰 ارز: {symbol}"
    )

    lines.append(
        f"🔗 Kraken: {kraken_symbol}"
    )

    lines.append(
        f"⏱ تایم‌فریم: {timeframe}"
    )

    lines.append(
        f"📈 روند: "
        f"{trend_text(result['trend'])}"
    )

    lines.append(
        f"🕯 کندل بسته: "
        f"{result['candles']}"
    )

    lines.append(
        f"🔺 Pivot High: "
        f"{result['pivots_high']}"
    )

    lines.append(
        f"🔻 Pivot Low: "
        f"{result['pivots_low']}"
    )

    # --------------------------------------------------------
    # No break
    # --------------------------------------------------------

    if not result["break"]:

        lines.append("")
        lines.append(
            "❌ شکست روند معتبر پیدا نشد."
        )

        lines.append(
            "⏳ وضعیت: NO TRADE"
        )

        return "\n".join(lines)

    br = result[
        "break"
    ]

    lines.append("")
    lines.append(
        "⚡ TREND BREAK"
    )

    lines.append(
        f"نوع: {br['direction']}"
    )

    lines.append(
        f"سطح: "
        f"{fmt_price(br['level'])}"
    )

    # --------------------------------------------------------
    # No confirmation
    # --------------------------------------------------------

    if not result[
        "confirmation"
    ]:

        lines.append("")
        lines.append(
            "⏳ پولبک / ریتست "
            "و کندل برگشتی تأیید نشده."
        )

        lines.append(
            "وضعیت: WAIT"
        )

        return "\n".join(lines)

    conf = result[
        "confirmation"
    ]

    lines.append("")
    lines.append(
        "🔄 REVERSAL CONFIRMATION"
    )

    lines.append(
        f"🕯 الگو: "
        f"{conf['pattern']}"
    )

    lines.append(
        f"📍 Retest: "
        f"{fmt_price(conf['level'])}"
    )

    # --------------------------------------------------------
    # No setup
    # --------------------------------------------------------

    setup = result[
        "setup"
    ]

    if not setup:

        lines.append("")
        lines.append(
            "⚠️ Setup معتبر ساخته نشد."
        )

        lines.append(
            "وضعیت: NO TRADE"
        )

        return "\n".join(lines)

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    lines.append("")
    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🚨 SIGNAL: "
        f"{setup['side']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🎯 Entry: "
        f"{fmt_price(setup['entry'])}"
    )

    lines.append(
        f"🛑 SL: "
        f"{fmt_price(setup['sl'])}"
    )

    lines.append(
        f"🎯 TP: "
        f"{fmt_price(setup['tp'])}"
    )

    lines.append(
        "📐 RR: 1:1"
    )

    lines.append(
        f"🕯 Confirmation: "
        f"{setup['pattern']}"
    )

    lines.append("")
    lines.append(
        "⚠️ تحلیل فقط بر اساس "
        "کندل‌های بسته‌شده است."
    )

    return "\n".join(lines)


# ============================================================
# HELP
# ============================================================

HELP_TEXT = """
🤖 KRAKEN FUTURES STRATEGY BOT

دستور تحلیل:

تحلیل BTC 5m
تحلیل ATOM 15m
تحلیل ETH 1h
تحلیل SOL 4h

یا:

/check BTC 5m

تایم‌فریم‌های مجاز:

1m
5m
15m
30m
1h
4h

استراتژی:

Pivot
↓
Trend Break
↓
Pullback / Retest
↓
Reversal Candle
↓
Entry
↓
Structural SL
↓
RR 1:1 TP

فقط کندل‌های بسته‌شده بررسی می‌شوند.
"""


# ============================================================
# PARSE COMMAND
# ============================================================

def parse_command(
    text
):

    if not text:
        return None

    parts = text.strip().split()

    if len(parts) < 3:
        return None

    command = parts[0].lower()

    if (
        parts[0] == "تحلیل"
        or
        command in (
            "/check",
            "check"
        )
    ):

        symbol = parts[1].strip()
        timeframe = parts[2].strip()

        return (
            symbol,
            timeframe
        )

    return None


# ============================================================
# HANDLE MESSAGE
# ============================================================

def handle_message(
    message
):

    chat = message.get(
        "chat",
        {}
    )

    chat_id = str(
        chat.get(
            "id",
            ""
        )
    )

    text = str(
        message.get(
            "text",
            ""
        )
    ).strip()

    if not chat_id:
        return

    # --------------------------------------------------------
    # START / HELP
    # --------------------------------------------------------

    if text.lower() in (
        "/start",
        "/help",
        "help"
    ):

        send_telegram(
            HELP_TEXT,
            chat_id
        )

        return

    parsed = parse_command(
        text
    )

    if not parsed:

        send_telegram(
            "❌ دستور نامعتبر.\n\n"
            "مثال:\n"
            "تحلیل ATOM 15m\n\n"
            "یا:\n"
            "/check BTC 5m",
            chat_id
        )

        return

    symbol, timeframe = parsed

    try:

        timeframe = normalize_timeframe(
            timeframe
        )

    except Exception:

        send_telegram(
            "❌ تایم‌فریم نامعتبر.\n\n"
            "مجاز:\n"
            "1m, 5m, 15m, 30m, 1h, 4h",
            chat_id
        )

        return

    send_telegram(
        f"🔎 در حال بررسی "
        f"{symbol.upper()} "
        f"در {timeframe} ...",
        chat_id
    )

    try:

        result = analyze(
            symbol,
            timeframe
        )

        report = build_report(
            result
        )

        send_telegram(
            report,
            chat_id
        )

    except ValueError as e:

        print(
            "Validation error:",
            repr(e)
        )

        send_telegram(
            "❌ خطای نماد / ورودی\n\n"
            f"ارز: {symbol.upper()}\n"
            f"تایم‌فریم: {timeframe}\n\n"
            f"جزئیات:\n{str(e)}",
            chat_id
        )

    except requests.exceptions.HTTPError as e:

        print(
            "Kraken HTTP error:",
            repr(e)
        )

        status = ""

        try:
            status = (
                f"HTTP {e.response.status_code}"
            )
        except Exception:
            pass

        send_telegram(
            "🔴 خطای Kraken API\n\n"
            f"ارز: {symbol.upper()}\n"
            f"تایم‌فریم: {timeframe}\n"
            f"{status}\n\n"
            "API موقتاً پاسخ مناسب نداده است.",
            chat_id
        )

    except requests.exceptions.RequestException as e:

        print(
            "Network/API error:",
            repr(e)
        )

        send_telegram(
            "🔴 خطای ارتباط با Kraken\n\n"
            f"ارز: {symbol.upper()}\n"
            f"تایم‌فریم: {timeframe}\n\n"
            "اتصال قطع یا API موقتاً در دسترس نیست.",
            chat_id
        )

    except Exception as e:

        print(
            "Analysis error:",
            repr(e)
        )

        traceback.print_exc()

        send_telegram(
            "❌ خطای غیرمنتظره در تحلیل\n\n"
            f"ارز: {symbol.upper()}\n"
            f"تایم‌فریم: {timeframe}\n\n"
            f"جزئیات:\n{str(e)[:500]}",
            chat_id
        )


# ============================================================
# MAIN TELEGRAM LOOP
# ============================================================

def listener_loop():

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN تنظیم نشده."
        )

    print("=" * 60)
    print(
        "KRAKEN TELEGRAM LISTENER v3.0"
    )
    print("=" * 60)

    print(
        "Loading Kraken instruments..."
    )

    try:

        instruments = load_instruments()

        print(
            f"Loaded {len(instruments)} instruments."
        )

    except Exception as e:

        print(
            "Initial instrument loading failed:",
            repr(e)
        )

        print(
            "Listener will retry automatically."
        )

    print(
        "Listener started."
    )

    print(
        "Waiting for Telegram commands..."
    )

    print("=" * 60)

    offset = None

    while True:

        try:

            updates = telegram_get_updates(
                offset
            )

            for update in updates:

                update_id = update.get(
                    "update_id"
                )

                if update_id is not None:

                    offset = (
                        update_id + 1
                    )

                message = update.get(
                    "message"
                )

                if not message:
                    continue

                try:

                    handle_message(
                        message
                    )

                except Exception as e:

                    print(
                        "Message error:",
                        repr(e)
                    )

                    traceback.print_exc()

            if not updates:

                time.sleep(
                    0.5
                )

        except requests.exceptions.Timeout:

            print(
                "Telegram timeout. Reconnecting..."
            )

            time.sleep(
                POLL_RETRY_SECONDS
            )

        except requests.exceptions.ConnectionError:

            print(
                "Network connection error."
            )

            print(
                "Reconnecting..."
            )

            time.sleep(
                ERROR_RETRY_SECONDS
            )

        except requests.exceptions.HTTPError as e:

            print(
                "Telegram HTTP error:",
                repr(e)
            )

            time.sleep(
                ERROR_RETRY_SECONDS
            )

        except Exception as e:

            print(
                "Listener error:",
                repr(e)
            )

            traceback.print_exc()

            time.sleep(
                ERROR_RETRY_SECONDS
            )


# ============================================================
# AUTO RESTART
# ============================================================

def main():

    while True:

        try:

            listener_loop()

        except KeyboardInterrupt:

            print(
                "Listener stopped manually."
            )

            break

        except Exception as e:

            print(
                "=" * 60
            )

            print(
                "FATAL ERROR"
            )

            print(
                repr(e)
            )

            traceback.print_exc()

            print(
                "Restarting in 15 seconds..."
            )

            print(
                "=" * 60
            )

            time.sleep(
                15
            )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
