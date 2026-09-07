# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v8.0
# ============================================================
# TOP 100 MARKETS
#
# TIMEFRAMES:
#   1H  = TREND
#   30M = CONFIRMATION
#   15M = PULLBACK
#   5M  = TRIGGER
#
# CLOSED CANDLES ONLY FOR SIGNALS
#
# MAX OPEN TRADES = 3
# RR = 1:1
#
# FEATURES:
#   - Persistent open trades
#   - Up to 3 simultaneous trades
#   - Live market price for open-trade P/L
#   - Live P/L percentage
#   - TP/SL distance percentages
#   - 4 decimal maximum for setup prices
#   - No candidate list
#   - Telegram HTML
#   - Telegram failure = GitHub Actions failure
# ============================================================

import os
import json
import time
import traceback
import html
import requests

from datetime import datetime, timezone


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

CANDLES_URL = (
    BASE_URL + "/api/charts/v1/trade"
)

TOP_N = 100

CANDLE_LIMIT = 220

MIN_CANDLES = 80

REQUEST_TIMEOUT = 20

# ============================================================
# MAXIMUM SIMULTANEOUS OPEN TRADES
# ============================================================

MAX_OPEN_TRADES = 3

RR = 1.0

ATR_PERIOD = 14

PULLBACK_ATR_MULT = 1.5

TRADE_TIMEOUT_MINUTES = 240

STATE_FILE = "ichimoku_state.json"

HISTORY_FILE = "ichimoku_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# LOGGING
# ============================================================

def log(message):

    print(
        f"[INFO] {message}",
        flush=True
    )


def warn(message):

    print(
        f"[WARN] {message}",
        flush=True
    )


def error(message):

    print(
        f"[ERROR] {message}",
        flush=True
    )


# ============================================================
# NUMBER FORMATTING
# ============================================================

def fmt_price(value):

    try:

        value = float(value)

    except Exception:

        return "0"

    # Maximum 4 decimal places.
    # Remove unnecessary trailing zeros.

    text = f"{value:.4f}"

    text = text.rstrip("0").rstrip(".")

    if text == "-0":
        text = "0"

    return text


def fmt_percent(value):

    try:

        return f"{float(value):+.2f}%"

    except Exception:

        return "0.00%"


# ============================================================
# TELEGRAM HTML
# ============================================================

def tg_escape(value):

    return html.escape(
        str(value),
        quote=False
    )


def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN:

        warn(
            "TELEGRAM_BOT_TOKEN is missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        warn(
            "TELEGRAM_CHAT_ID is missing"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        if response.status_code != 200:

            error(
                "Telegram HTTP error "
                f"{response.status_code}: "
                f"{response.text[:1000]}"
            )

            return False

        try:

            data = response.json()

        except Exception:

            error(
                "Telegram returned invalid JSON"
            )

            return False

        if not data.get(
            "ok",
            False
        ):

            error(
                "Telegram API error: "
                f"{data}"
            )

            return False

        log(
            "Telegram report sent successfully"
        )

        return True

    except Exception as exc:

        error(
            "Telegram exception: "
            f"{type(exc).__name__}: {exc}"
        )

        return False


# ============================================================
# HTTP
# ============================================================

def api_get(
    url,
    params=None
):

    try:

        response = requests.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            warn(
                f"HTTP {response.status_code}: "
                f"{url}"
            )

            return None

        return response.json()

    except Exception as exc:

        warn(
            f"API request failed: "
            f"{type(exc).__name__}: {exc}"
        )

        return None


# ============================================================
# JSON STATE
# ============================================================

def load_json(
    filename,
    default
):

    try:

        if not os.path.exists(filename):

            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as exc:

        warn(
            f"Could not load {filename}: "
            f"{exc}"
        )

        return default


def save_json(
    filename,
    data
):

    temp = filename + ".tmp"

    try:

        with open(
            temp,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp,
            filename
        )

    except Exception as exc:

        error(
            f"Could not save {filename}: "
            f"{exc}"
        )


def load_state():

    state = load_json(
        STATE_FILE,
        {}
    )

    # --------------------------------------------------------
    # New state format
    # --------------------------------------------------------

    if not isinstance(
        state,
        dict
    ):

        state = {}

    if "open_trades" not in state:

        old_trade = state.get(
            "open_trade"
        )

        if old_trade:

            state["open_trades"] = [
                old_trade
            ]

        else:

            state["open_trades"] = []

    if not isinstance(
        state["open_trades"],
        list
    ):

        state["open_trades"] = []

    # Remove old single-trade key
    state.pop(
        "open_trade",
        None
    )

    if "last_signal_keys" not in state:

        state["last_signal_keys"] = []

    if not isinstance(
        state["last_signal_keys"],
        list
    ):

        state["last_signal_keys"] = []

    if "last_run" not in state:

        state["last_run"] = None

    return state


def save_state(state):

    save_json(
        STATE_FILE,
        state
    )


def load_history():

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(
        history,
        list
    ):

        return []

    return history


def save_history(history):

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# MARKET DATA
# ============================================================

def get_top_markets():

    instruments = api_get(
        INSTRUMENTS_URL
    )

    tickers = api_get(
        TICKERS_URL
    )

    if not instruments:
        return []

    if not tickers:
        return []

    # --------------------------------------------------------
    # Instruments
    # --------------------------------------------------------

    instrument_list = []

    if isinstance(
        instruments,
        dict
    ):

        for key in (
            "instruments",
            "data",
            "result"
        ):

            if isinstance(
                instruments.get(key),
                list
            ):

                instrument_list = (
                    instruments[key]
                )

                break

    elif isinstance(
        instruments,
        list
    ):

        instrument_list = instruments

    # --------------------------------------------------------
    # Tickers
    # --------------------------------------------------------

    ticker_list = []

    if isinstance(
        tickers,
        dict
    ):

        for key in (
            "tickers",
            "data",
            "result"
        ):

            if isinstance(
                tickers.get(key),
                list
            ):

                ticker_list = (
                    tickers[key]
                )

                break

    elif isinstance(
        tickers,
        list
    ):

        ticker_list = tickers

    # --------------------------------------------------------
    # Volume map
    # --------------------------------------------------------

    volume_map = {}

    for ticker in ticker_list:

        if not isinstance(
            ticker,
            dict
        ):

            continue

        symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
        )

        if not symbol:
            continue

        volume = (
            ticker.get("volume24h")
            or ticker.get("volume")
            or 0
        )

        try:

            volume = float(volume)

        except Exception:

            volume = 0.0

        volume_map[symbol] = volume

    # --------------------------------------------------------
    # Futures
    # --------------------------------------------------------

    markets = []

    for item in instrument_list:

        if not isinstance(
            item,
            dict
        ):

            continue

        symbol = (
            item.get("symbol")
            or item.get("instrument")
        )

        if not symbol:
            continue

        if not (
            symbol.startswith("PF_")
            or symbol.startswith("PI_")
        ):

            continue

        markets.append(
            (
                symbol,
                volume_map.get(
                    symbol,
                    0.0
                )
            )
        )

    markets.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [
        x[0]
        for x in markets[:TOP_N]
    ]


# ============================================================
# TICKER / LIVE PRICE
# ============================================================

def get_live_prices():

    data = api_get(
        TICKERS_URL
    )

    if not data:

        return {}

    if isinstance(
        data,
        dict
    ):

        raw = []

        for key in (
            "tickers",
            "data",
            "result"
        ):

            if isinstance(
                data.get(key),
                list
            ):

                raw = data[key]

                break

    elif isinstance(
        data,
        list
    ):

        raw = data

    else:

        raw = []

    prices = {}

    for ticker in raw:

        if not isinstance(
            ticker,
            dict
        ):

            continue

        symbol = (
            ticker.get("symbol")
            or ticker.get("instrument")
        )

        if not symbol:
            continue

        price = (
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice")
            or ticker.get("price")
        )

        if price is None:
            continue

        try:

            prices[symbol] = float(
                price
            )

        except Exception:

            continue

    return prices


# ============================================================
# CANDLES
# ============================================================

RESOLUTION_MAP = {
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
}


def get_candles(
    symbol,
    minutes
):

    resolution = (
        RESOLUTION_MAP.get(
            minutes
        )
    )

    if not resolution:

        return []

    url = (
        f"{CANDLES_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    data = api_get(
        url,
        params={
            "count": CANDLE_LIMIT
        }
    )

    if not data:

        return []

    if isinstance(
        data,
        dict
    ):

        raw = data.get(
            "candles",
            []
        )

    elif isinstance(
        data,
        list
    ):

        raw = data

    else:

        raw = []

    candles = []

    for c in raw:

        if not isinstance(
            c,
            dict
        ):

            continue

        try:

            timestamp = float(
                c.get("time")
            )

            o = float(
                c.get("open")
            )

            h = float(
                c.get("high")
            )

            l = float(
                c.get("low")
            )

            close = float(
                c.get("close")
            )

            volume = float(
                c.get(
                    "volume",
                    0
                )
            )

        except Exception:

            continue

        candles.append(
            {
                "time": timestamp,
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": volume
            }
        )

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

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
    # Closed candles only
    # --------------------------------------------------------

    now_ms = (
        time.time() * 1000
    )

    interval_ms = (
        minutes * 60 * 1000
    )

    closed = []

    for candle in candles:

        if (
            candle["time"]
            + interval_ms
            <= now_ms
        ):

            closed.append(
                candle
            )

    return closed


# ============================================================
# ROLLING
# ============================================================

def rolling_high(
    values,
    period
):

    result = [
        None
    ] * len(values)

    for i in range(
        period - 1,
        len(values)
    ):

        result[i] = max(
            values[
                i - period + 1:
                i + 1
            ]
        )

    return result


def rolling_low(
    values,
    period
):

    result = [
        None
    ] * len(values)

    for i in range(
        period - 1,
        len(values)
    ):

        result[i] = min(
            values[
                i - period + 1:
                i + 1
            ]
        )

    return result


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(
    candles
):

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

    high9 = rolling_high(
        highs,
        9
    )

    low9 = rolling_low(
        lows,
        9
    )

    high26 = rolling_high(
        highs,
        26
    )

    low26 = rolling_low(
        lows,
        26
    )

    high52 = rolling_high(
        highs,
        52
    )

    low52 = rolling_low(
        lows,
        52
    )

    tenkan = []

    kijun = []

    span_a = []

    span_b = []

    for i in range(
        len(candles)
    ):

        if (
            high9[i] is None
            or low9[i] is None
        ):

            tenkan.append(None)

        else:

            tenkan.append(
                (
                    high9[i]
                    + low9[i]
                ) / 2
            )

        if (
            high26[i] is None
            or low26[i] is None
        ):

            kijun.append(None)

        else:

            kijun.append(
                (
                    high26[i]
                    + low26[i]
                ) / 2
            )

        if (
            tenkan[i] is None
            or kijun[i] is None
        ):

            span_a.append(None)

        else:

            span_a.append(
                (
                    tenkan[i]
                    + kijun[i]
                ) / 2
            )

        if (
            high52[i] is None
            or low52[i] is None
        ):

            span_b.append(None)

        else:

            span_b.append(
                (
                    high52[i]
                    + low52[i]
                ) / 2
            )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "close": closes,
        "high": highs,
        "low": lows
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]

        low = candles[i]["low"]

        previous_close = (
            candles[i - 1]["close"]
        )

        tr = max(
            high - low,
            abs(
                high
                - previous_close
            ),
            abs(
                low
                - previous_close
            )
        )

        trs.append(tr)

    if len(trs) < period:

        return None

    return (
        sum(
            trs[-period:]
        )
        / period
    )


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def calculate_score(
    candles
):

    if len(candles) < MIN_CANDLES:

        return None

    ich = calculate_ichimoku(
        candles
    )

    i = len(candles) - 1

    close = ich["close"][i]

    tenkan = ich["tenkan"][i]

    kijun = ich["kijun"][i]

    if (
        tenkan is None
        or kijun is None
    ):

        return None

    cloud_index = i - 26

    if cloud_index < 0:

        return None

    span_a = ich["span_a"][
        cloud_index
    ]

    span_b = ich["span_b"][
        cloud_index
    ]

    if (
        span_a is None
        or span_b is None
    ):

        return None

    cloud_top = max(
        span_a,
        span_b
    )

    cloud_bottom = min(
        span_a,
        span_b
    )

    score = 0

    # Price vs Kumo
    if close > cloud_top:

        score += 3

    elif close < cloud_bottom:

        score -= 3

    # Price vs Tenkan
    if close > tenkan:

        score += 1

    elif close < tenkan:

        score -= 1

    # Price vs Kijun
    if close > kijun:

        score += 2

    elif close < kijun:

        score -= 2

    # Future Kumo
    future_a = ich["span_a"][i]

    future_b = ich["span_b"][i]

    if (
        future_a is not None
        and future_b is not None
    ):

        if future_a > future_b:

            score += 2

        elif future_a < future_b:

            score -= 2

    # Chikou approximation
    chikou_index = i - 26

    if chikou_index >= 0:

        chikou = ich["close"][
            chikou_index
        ]

        if chikou > close:

            score += 2

        elif chikou < close:

            score -= 2

    score = max(
        -10,
        min(
            10,
            score
        )
    )

    return {
        "score": score,
        "close": close,
        "tenkan": tenkan,
        "kijun": kijun,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "atr": calculate_atr(
            candles,
            ATR_PERIOD
        )
    }


# ============================================================
# DIRECTION
# ============================================================

def direction_from_score(
    score
):

    if score >= 6:

        return "LONG"

    if score <= -6:

        return "SHORT"

    return "NONE"


# ============================================================
# 30M CONFIRMATION
# ============================================================

def confirmation_30m(
    direction,
    score
):

    if direction == "LONG":

        return score >= 5

    if direction == "SHORT":

        return score <= -5

    return False


# ============================================================
# 15M PULLBACK
# ============================================================

def pullback_15m(
    candles,
    direction
):

    result = calculate_score(
        candles
    )

    if not result:

        return False, None

    close = result["close"]

    tenkan = result["tenkan"]

    kijun = result["kijun"]

    cloud_top = result["cloud_top"]

    cloud_bottom = result[
        "cloud_bottom"
    ]

    atr = result["atr"]

    if atr is None:

        return False, result

    max_distance = (
        atr * PULLBACK_ATR_MULT
    )

    if direction == "LONG":

        above_cloud = (
            close > cloud_bottom
        )

        near_tenkan = (
            abs(
                close - tenkan
            )
            <= max_distance
        )

        near_kijun = (
            abs(
                close - kijun
            )
            <= max_distance
        )

        return (
            above_cloud
            and (
                near_tenkan
                or near_kijun
            )
        ), result

    if direction == "SHORT":

        below_cloud = (
            close < cloud_top
        )

        near_tenkan = (
            abs(
                close - tenkan
            )
            <= max_distance
        )

        near_kijun = (
            abs(
                close - kijun
            )
            <= max_distance
        )

        return (
            below_cloud
            and (
                near_tenkan
                or near_kijun
            )
        ), result

    return False, result


# ============================================================
# 5M TRIGGER
# ============================================================

def trigger_5m(
    candles,
    direction
):

    if len(candles) < 60:

        return (
            False,
            "Not enough 5m candles"
        )

    result = calculate_score(
        candles
    )

    if not result:

        return (
            False,
            "5m Ichimoku unavailable"
        )

    i = len(candles) - 1

    current = candles[i]

    previous = candles[i - 1]

    close = current["close"]

    open_price = current["open"]

    high = current["high"]

    low = current["low"]

    previous_high = previous["high"]

    previous_low = previous["low"]

    tenkan = result["tenkan"]

    kijun = result["kijun"]

    cloud_top = result["cloud_top"]

    cloud_bottom = result[
        "cloud_bottom"
    ]

    reasons = []

    previous_ich = calculate_ichimoku(
        candles[:-1]
    )

    pi = len(candles) - 2

    prev_tenkan = (
        previous_ich["tenkan"][pi]
    )

    prev_kijun = (
        previous_ich["kijun"][pi]
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        bullish_close = (
            close > open_price
        )

        if not bullish_close:

            return (
                False,
                "5m candle not bullish"
            )

        if close <= cloud_bottom:

            return (
                False,
                "5m price below cloud"
            )

        tenkan_cross = (
            prev_tenkan is not None
            and tenkan is not None
            and previous["close"]
            <= prev_tenkan
            and close > tenkan
        )

        kijun_cross = (
            prev_kijun is not None
            and kijun is not None
            and previous["close"]
            <= prev_kijun
            and close > kijun
        )

        bullish_engulfing = (
            previous["close"]
            < previous["open"]
            and close > open_price
            and close >= previous["open"]
            and open_price
            <= previous["close"]
        )

        break_high = (
            close > previous_high
        )

        momentum = (
            close > previous["close"]
            and high > previous["high"]
        )

        if tenkan_cross:

            reasons.append(
                "Tenkan cross"
            )

        if kijun_cross:

            reasons.append(
                "Kijun cross"
            )

        if bullish_engulfing:

            reasons.append(
                "Bullish engulfing"
            )

        if break_high:

            reasons.append(
                "Break previous high"
            )

        if momentum:

            reasons.append(
                "Bullish momentum"
            )

        if reasons:

            return (
                True,
                " + ".join(reasons)
            )

        return (
            False,
            "No LONG trigger"
        )

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        bearish_close = (
            close < open_price
        )

        if not bearish_close:

            return (
                False,
                "5m candle not bearish"
            )

        if close >= cloud_top:

            return (
                False,
                "5m price above cloud"
            )

        tenkan_cross = (
            prev_tenkan is not None
            and tenkan is not None
            and previous["close"]
            >= prev_tenkan
            and close < tenkan
        )

        kijun_cross = (
            prev_kijun is not None
            and kijun is not None
            and previous["close"]
            >= prev_kijun
            and close < kijun
        )

        bearish_engulfing = (
            previous["close"]
            > previous["open"]
            and close < open_price
            and close <= previous["open"]
            and open_price
            >= previous["close"]
        )

        break_low = (
            close < previous_low
        )

        momentum = (
            close < previous["close"]
            and low < previous["low"]
        )

        if tenkan_cross:

            reasons.append(
                "Tenkan cross"
            )

        if kijun_cross:

            reasons.append(
                "Kijun cross"
            )

        if bearish_engulfing:

            reasons.append(
                "Bearish engulfing"
            )

        if break_low:

            reasons.append(
                "Break previous low"
            )

        if momentum:

            reasons.append(
                "Bearish momentum"
            )

        if reasons:

            return (
                True,
                " + ".join(reasons)
            )

        return (
            False,
            "No SHORT trigger"
        )

    return (
        False,
        "No direction"
    )


# ============================================================
# SWINGS
# ============================================================

def latest_swing_low(
    candles,
    lookback=10
):

    subset = candles[
        -lookback:
    ]

    return min(
        x["low"]
        for x in subset
    )


def latest_swing_high(
    candles,
    lookback=10
):

    subset = candles[
        -lookback:
    ]

    return max(
        x["high"]
        for x in subset
    )


# ============================================================
# BUILD TRADE
# ============================================================

def build_trade(
    symbol,
    direction,
    candles,
    trigger_reason
):

    entry = candles[-1]["close"]

    if direction == "LONG":

        sl = latest_swing_low(
            candles,
            10
        )

        risk = (
            entry - sl
        )

        if risk <= 0:

            return None

        tp = (
            entry
            + risk * RR
        )

    else:

        sl = latest_swing_high(
            candles,
            10
        )

        risk = (
            sl - entry
        )

        if risk <= 0:

            return None

        tp = (
            entry
            - risk * RR
        )

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "opened_at": time.time(),
        "trigger_reason": trigger_reason,
        "status": "OPEN"
    }


# ============================================================
# OPEN SYMBOLS
# ============================================================

def get_open_symbols(
    state
):

    symbols = set()

    for trade in state.get(
        "open_trades",
        []
    ):

        symbol = trade.get(
            "symbol"
        )

        if symbol:

            symbols.add(
                symbol
            )

    return symbols


# ============================================================
# LIVE P/L
# ============================================================

def calculate_live_pnl(
    trade,
    market_price
):

    entry = float(
        trade["entry"]
    )

    direction = trade[
        "direction"
    ]

    if entry <= 0:

        return 0.0

    if direction == "LONG":

        return (
            (
                market_price
                - entry
            )
            / entry
        ) * 100.0

    else:

        return (
            (
                entry
                - market_price
            )
            / entry
        ) * 100.0


# ============================================================
# TP / SL DISTANCE
# ============================================================

def calculate_trade_percentages(
    trade,
    market_price
):

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp = float(
        trade["tp"]
    )

    direction = trade[
        "direction"
    ]

    if direction == "LONG":

        tp_pct = (
            (tp - market_price)
            / market_price
        ) * 100.0

        sl_pct = (
            (market_price - sl)
            / market_price
        ) * 100.0

    else:

        tp_pct = (
            (market_price - tp)
            / market_price
        ) * 100.0

        sl_pct = (
            (sl - market_price)
            / market_price
        ) * 100.0

    return (
        tp_pct,
        sl_pct
    )


# ============================================================
# CHECK OPEN TRADES
# ============================================================

def check_open_trades(
    state,
    history,
    live_prices
):

    open_trades = state.get(
        "open_trades",
        []
    )

    remaining = []

    for trade in open_trades:

        symbol = trade[
            "symbol"
        ]

        direction = trade[
            "direction"
        ]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        opened_at = float(
            trade["opened_at"]
        )

        # ----------------------------------------------------
        # We still use closed 5m candles for TP/SL execution.
        # Live price is only used for report P/L.
        # ----------------------------------------------------

        candles = get_candles(
            symbol,
            5
        )

        if len(candles) < 2:

            remaining.append(
                trade
            )

            continue

        age_minutes = (
            time.time()
            - opened_at
        ) / 60.0

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        if age_minutes >= (
            TRADE_TIMEOUT_MINUTES
        ):

            exit_price = (
                candles[-1]["close"]
            )

            if direction == "LONG":

                pnl_r = (
                    exit_price - entry
                ) / (
                    entry - sl
                )

            else:

                pnl_r = (
                    entry - exit_price
                ) / (
                    sl - entry
                )

            trade["status"] = (
                "TIMEOUT"
            )

            trade["exit"] = (
                exit_price
            )

            trade["pnl_r"] = (
                pnl_r
            )

            trade["closed_at"] = (
                time.time()
            )

            history.append(
                trade
            )

            log(
                f"Trade timeout: "
                f"{symbol}"
            )

            continue

        # ----------------------------------------------------
        # CLOSED CANDLE TP / SL
        # ----------------------------------------------------

        candle = candles[-1]

        high = candle["high"]

        low = candle["low"]

        result = None

        if direction == "LONG":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            # Conservative:
            # if both happen in same candle,
            # SL is assumed first.

            if hit_sl:

                result = (
                    sl,
                    -1.0,
                    "SL"
                )

            elif hit_tp:

                result = (
                    tp,
                    1.0,
                    "TP"
                )

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            if hit_sl:

                result = (
                    sl,
                    -1.0,
                    "SL"
                )

            elif hit_tp:

                result = (
                    tp,
                    1.0,
                    "TP"
                )

        if result:

            exit_price, pnl_r, reason = (
                result
            )

            trade["status"] = (
                reason
            )

            trade["exit"] = (
                exit_price
            )

            trade["pnl_r"] = (
                pnl_r
            )

            trade["closed_at"] = (
                time.time()
            )

            history.append(
                trade
            )

            log(
                f"Trade closed: "
                f"{symbol} "
                f"{reason} "
                f"R={pnl_r:+.2f}"
            )

            continue

        # Still open
        remaining.append(
            trade
        )

    state[
        "open_trades"
    ] = remaining


# ============================================================
# PERFORMANCE
# ============================================================

def performance(
    history
):

    trades = len(history)

    wins = sum(
        1
        for x in history
        if float(
            x.get(
                "pnl_r",
                0
            )
        ) > 0
    )

    losses = sum(
        1
        for x in history
        if float(
            x.get(
                "pnl_r",
                0
            )
        ) < 0
    )

    flat = (
        trades
        - wins
        - losses
    )

    total_r = sum(
        float(
            x.get(
                "pnl_r",
                0
            )
        )
        for x in history
    )

    if trades > 0:

        wr = (
            wins / trades
        ) * 100.0

    else:

        wr = 0.0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "wr": wr,
        "r": total_r
    }


# ============================================================
# SCAN MARKET
# ============================================================

def scan_market(
    symbol
):

    # ========================================================
    # 1H
    # ========================================================

    candles_1h = get_candles(
        symbol,
        60
    )

    if len(candles_1h) < MIN_CANDLES:

        return {
            "valid": False,
            "reject": "1H",
            "symbol": symbol,
            "reason": (
                f"Not enough 1H candles "
                f"({len(candles_1h)}/{MIN_CANDLES})"
            )
        }

    score_1h = calculate_score(
        candles_1h
    )

    if not score_1h:

        return {
            "valid": False,
            "reject": "1H",
            "symbol": symbol,
            "reason": "1H indicator error"
        }

    direction = direction_from_score(
        score_1h["score"]
    )

    if direction == "NONE":

        return {
            "valid": False,
            "reject": "1H",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": 0,
            "direction": "NONE",
            "stage": "1H",
            "reason": "No 1H trend"
        }

    # ========================================================
    # 30M
    # ========================================================

    candles_30m = get_candles(
        symbol,
        30
    )

    if len(candles_30m) < MIN_CANDLES:

        return {
            "valid": False,
            "reject": "30m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": 0,
            "direction": direction,
            "stage": "30m",
            "reason": (
                f"Not enough 30m candles "
                f"({len(candles_30m)}/{MIN_CANDLES})"
            )
        }

    score_30m = calculate_score(
        candles_30m
    )

    if not score_30m:

        return {
            "valid": False,
            "reject": "30m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": 0,
            "direction": direction,
            "stage": "30m",
            "reason": "30m indicator error"
        }

    if not confirmation_30m(
        direction,
        score_30m["score"]
    ):

        return {
            "valid": False,
            "reject": "30m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": score_30m["score"],
            "direction": direction,
            "stage": "30m",
            "reason": "30m confirmation failed"
        }

    # ========================================================
    # 15M
    # ========================================================

    candles_15m = get_candles(
        symbol,
        15
    )

    if len(candles_15m) < MIN_CANDLES:

        return {
            "valid": False,
            "reject": "15m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": score_30m["score"],
            "direction": direction,
            "stage": "15m",
            "reason": (
                f"Not enough 15m candles "
                f"({len(candles_15m)}/{MIN_CANDLES})"
            )
        }

    pullback_ok, score_15m = (
        pullback_15m(
            candles_15m,
            direction
        )
    )

    if not pullback_ok:

        return {
            "valid": False,
            "reject": "15m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": score_30m["score"],
            "direction": direction,
            "stage": "15m",
            "reason": "15m pullback not ready"
        }

    # ========================================================
    # 5M
    # ========================================================

    candles_5m = get_candles(
        symbol,
        5
    )

    if len(candles_5m) < MIN_CANDLES:

        return {
            "valid": False,
            "reject": "5m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": score_30m["score"],
            "direction": direction,
            "stage": "5m",
            "reason": (
                f"Not enough 5m candles "
                f"({len(candles_5m)}/{MIN_CANDLES})"
            )
        }

    trigger_ok, trigger_reason = (
        trigger_5m(
            candles_5m,
            direction
        )
    )

    if not trigger_ok:

        return {
            "valid": False,
            "reject": "5m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": score_30m["score"],
            "direction": direction,
            "stage": "5m",
            "reason": trigger_reason
        }

    # ========================================================
    # VALID
    # ========================================================

    trade = build_trade(
        symbol,
        direction,
        candles_5m,
        trigger_reason
    )

    if not trade:

        return {
            "valid": False,
            "reject": "5m",
            "symbol": symbol,
            "score_1h": score_1h["score"],
            "score_30m": score_30m["score"],
            "direction": direction,
            "stage": "5m",
            "reason": "Invalid SL/TP structure"
        }

    return {
        "valid": True,
        "reject": None,
        "symbol": symbol,
        "direction": direction,
        "score_1h": score_1h["score"],
        "score_30m": score_30m["score"],
        "score_15m": (
            score_15m["score"]
            if score_15m
            else 0
        ),
        "stage": "VALID",
        "reason": trigger_reason,
        "trade": trade
    }


# ============================================================
# CANDIDATE INTERNAL RANK
# ============================================================

def candidate_rank(
    item
):

    # Stronger higher timeframe alignment first.
    #
    # 1H is more important than 30m.
    #
    # We do NOT show this list in the report.
    # It is only used internally to decide which
    # maximum 3 trades should be opened.

    score_1h = abs(
        item.get(
            "score_1h",
            0
        )
    )

    score_30m = abs(
        item.get(
            "score_30m",
            0
        )
    )

    score_15m = abs(
        item.get(
            "score_15m",
            0
        )
    )

    return (
        score_1h * 100
        + score_30m * 10
        + score_15m
    )


# ============================================================
# SIGNAL KEY
# ============================================================

def make_signal_key(
    trade
):

    # 5-minute bucket prevents the same setup
    # from being repeatedly opened every 5 minutes.

    bucket = int(
        trade["opened_at"]
        // 300
    )

    return (
        f"{trade['symbol']}_"
        f"{trade['direction']}_"
        f"{bucket}"
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    history,
    state,
    live_prices,
    new_trades,
    markets_count,
    scanned_count,
    valid_count,
    rejected_1h,
    rejected_30m,
    rejected_15m,
    rejected_5m,
    data_errors
):

    perf = performance(
        history
    )

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    lines = []

    lines.append(
        "<b>📡 KRAKEN FUTURES "
        "ICHIMOKU REPORT</b>"
    )

    lines.append(
        f"🕐 {tg_escape(now)}"
    )

    lines.append(
        "⏱ 1H → Trend | "
        "30m → Confirm | "
        "15m → Pullback | "
        "5m → Trigger"
    )

    lines.append(
        "🤖 ICHIMOKU MTF | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    lines.append(
        "<b>📊 PERFORMANCE</b>"
    )

    lines.append(
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['flat']}"
    )

    lines.append(
        f"🏆 WR {perf['wr']:.1f}% | "
        f"R {perf['r']:+.2f}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # OPEN TRADES
    # ========================================================

    open_trades = state.get(
        "open_trades",
        []
    )

    if open_trades:

        lines.append(
            f"<b>🔴 OPEN TRADES "
            f"({len(open_trades)}/{MAX_OPEN_TRADES})</b>"
        )

        for idx, trade in enumerate(
            open_trades,
            start=1
        ):

            symbol = trade[
                "symbol"
            ]

            direction = trade[
                "direction"
            ]

            entry = float(
                trade["entry"]
            )

            sl = float(
                trade["sl"]
            )

            tp = float(
                trade["tp"]
            )

            live_price = live_prices.get(
                symbol,
                entry
            )

            live_pnl = (
                calculate_live_pnl(
                    trade,
                    live_price
                )
            )

            tp_pct, sl_pct = (
                calculate_trade_percentages(
                    trade,
                    live_price
                )
            )

            age_minutes = (
                time.time()
                - float(
                    trade["opened_at"]
                )
            ) / 60.0

            if live_pnl >= 0:

                pnl_icon = "🟢"

            else:

                pnl_icon = "🔴"

            lines.append(
                f"\n<b>#{idx} "
                f"{tg_escape(symbol)} "
                f"{tg_escape(direction)}</b>"
            )

            lines.append(
                f"Entry: "
                f"<code>{fmt_price(entry)}</code>"
            )

            lines.append(
                f"Market: "
                f"<code>{fmt_price(live_price)}</code>"
            )

            lines.append(
                f"SL: "
                f"<code>{fmt_price(sl)}</code> "
                f"({fmt_percent(sl_pct)})"
            )

            lines.append(
                f"TP: "
                f"<code>{fmt_price(tp)}</code> "
                f"({fmt_percent(tp_pct)})"
            )

            lines.append(
                f"{pnl_icon} "
                f"Live P/L: "
                f"<b>{fmt_percent(live_pnl)}</b>"
            )

            lines.append(
                f"⏱ Age: "
                f"{age_minutes:.0f}m"
            )

            lines.append(
                f"Trigger: "
                f"<code>"
                f"{tg_escape(trade.get('trigger_reason', ''))}"
                f"</code>"
            )

    else:

        lines.append(
            "<b>🔴 OPEN TRADES</b> None"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    if new_trades:

        lines.append(
            f"<b>🚨 NEW SIGNALS "
            f"({len(new_trades)})</b>"
        )

        for trade in new_trades:

            lines.append(
                f"\n<b>"
                f"{tg_escape(trade['symbol'])} "
                f"{tg_escape(trade['direction'])}"
                f"</b>"
            )

            lines.append(
                f"Entry: "
                f"<code>{fmt_price(trade['entry'])}</code>"
            )

            lines.append(
                f"SL: "
                f"<code>{fmt_price(trade['sl'])}</code>"
            )

            lines.append(
                f"TP: "
                f"<code>{fmt_price(trade['tp'])}</code>"
            )

            # Initial setup percentages

            entry = float(
                trade["entry"]
            )

            sl = float(
                trade["sl"]
            )

            tp = float(
                trade["tp"]
            )

            if trade["direction"] == "LONG":

                sl_pct = (
                    (entry - sl)
                    / entry
                ) * 100.0

                tp_pct = (
                    (tp - entry)
                    / entry
                ) * 100.0

            else:

                sl_pct = (
                    (sl - entry)
                    / entry
                ) * 100.0

                tp_pct = (
                    (entry - tp)
                    / entry
                ) * 100.0

            lines.append(
                f"🎯 TP: "
                f"+{abs(tp_pct):.2f}%"
            )

            lines.append(
                f"🛑 SL: "
                f"-{abs(sl_pct):.2f}%"
            )

            lines.append(
                f"Trigger: "
                f"<code>"
                f"{tg_escape(trade.get('trigger_reason', ''))}"
                f"</code>"
            )

    else:

        lines.append(
            "<b>NEW SIGNALS</b> "
            "No new valid signal."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # SCANNER STATUS
    # ========================================================

    lines.append(
        "<b>SCANNER STATUS</b>"
    )

    lines.append(
        f"Markets: {markets_count}"
    )

    lines.append(
        f"Scanned: {scanned_count}"
    )

    lines.append(
        f"Valid: {valid_count}"
    )

    lines.append(
        f"1H rejected: {rejected_1h}"
    )

    lines.append(
        f"30m rejected: {rejected_30m}"
    )

    lines.append(
        f"15m rejected: {rejected_15m}"
    )

    lines.append(
        f"5m rejected: {rejected_5m}"
    )

    lines.append(
        f"Data errors: {data_errors}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    state = load_state()

    history = load_history()

    log(
        "=========================================="
    )

    log(
        "KRAKEN FUTURES ICHIMOKU MTF v8.0"
    )

    log(
        "MAX OPEN TRADES = 3"
    )

    log(
        "=========================================="
    )

    # ========================================================
    # LIVE PRICES
    # ========================================================

    log(
        "Loading live prices..."
    )

    live_prices = get_live_prices()

    log(
        f"Live prices: {len(live_prices)}"
    )

    # ========================================================
    # CHECK EXISTING TRADES
    # ========================================================

    log(
        "Checking open trades..."
    )

    check_open_trades(
        state,
        history,
        live_prices
    )

    save_history(
        history
    )

    save_state(
        state
    )

    # ========================================================
    # MARKETS
    # ========================================================

    log(
        "Loading top markets..."
    )

    markets = get_top_markets()

    if not markets:

        error(
            "Markets: 0"
        )

        fatal_report = (
            "<b>🚨 ICHIMOKU SCANNER ERROR</b>\n\n"
            "Could not load Kraken markets."
        )

        telegram_send(
            fatal_report
        )

        raise RuntimeError(
            "No markets available"
        )

    log(
        f"Markets: {len(markets)}"
    )

    # ========================================================
    # SCAN
    # ========================================================

    results = []

    rejected_1h = 0

    rejected_30m = 0

    rejected_15m = 0

    rejected_5m = 0

    data_errors = 0

    for index, symbol in enumerate(
        markets,
        start=1
    ):

        try:

            result = scan_market(
                symbol
            )

            results.append(
                result
            )

            reject = result.get(
                "reject"
            )

            if reject == "1H":

                rejected_1h += 1

            elif reject == "30m":

                rejected_30m += 1

            elif reject == "15m":

                rejected_15m += 1

            elif reject == "5m":

                rejected_5m += 1

            elif reject == "DATA":

                data_errors += 1

            log(
                f"[{index}/{len(markets)}] "
                f"{symbol} | "
                f"{result.get('stage')} | "
                f"{result.get('direction')}"
            )

        except Exception as exc:

            error(
                f"{symbol}: "
                f"{type(exc).__name__}: "
                f"{exc}"
            )

            data_errors += 1

            results.append(
                {
                    "valid": False,
                    "reject": "DATA",
                    "symbol": symbol,
                    "direction": "NONE",
                    "score_1h": 0,
                    "score_30m": 0,
                    "score_15m": 0,
                    "stage": "ERROR",
                    "reason": str(exc)
                }
            )

    # ========================================================
    # VALID SETUPS
    # ========================================================

    valid_candidates = [
        x
        for x in results
        if x.get("valid")
    ]

    valid_candidates.sort(
        key=candidate_rank,
        reverse=True
    )

    # ========================================================
    # OPEN TRADE CAPACITY
    # ========================================================

    current_open = len(
        state.get(
            "open_trades",
            []
        )
    )

    free_slots = max(
        0,
        MAX_OPEN_TRADES
        - current_open
    )

    log(
        f"Open trades: "
        f"{current_open}/{MAX_OPEN_TRADES}"
    )

    log(
        f"Free slots: {free_slots}"
    )

    # ========================================================
    # OPEN NEW TRADES
    # ========================================================

    open_symbols = get_open_symbols(
        state
    )

    last_signal_keys = set(
        state.get(
            "last_signal_keys",
            []
        )
    )

    new_trades = []

    if free_slots > 0:

        for candidate in valid_candidates:

            if len(new_trades) >= free_slots:

                break

            trade = candidate.get(
                "trade"
            )

            if not trade:

                continue

            symbol = trade[
                "symbol"
            ]

            # ------------------------------------------------
            # Do not open same symbol twice.
            # ------------------------------------------------

            if symbol in open_symbols:

                continue

            signal_key = make_signal_key(
                trade
            )

            # ------------------------------------------------
            # Prevent duplicate signal.
            # ------------------------------------------------

            if signal_key in last_signal_keys:

                continue

            state[
                "open_trades"
            ].append(
                trade
            )

            open_symbols.add(
                symbol
            )

            last_signal_keys.add(
                signal_key
            )

            new_trades.append(
                trade
            )

            log(
                "NEW SIGNAL: "
                f"{symbol} "
                f"{trade['direction']}"
            )

    else:

        log(
            "Maximum open trades reached."
        )

    # ========================================================
    # KEEP LAST SIGNAL KEYS SMALL
    # ========================================================

    # We don't need unlimited history of signal keys.
    # Keep latest 300 only.

    state[
        "last_signal_keys"
    ] = list(
        last_signal_keys
    )[-300:]

    # ========================================================
    # SAVE
    # ========================================================

    state[
        "last_run"
    ] = datetime.now(
        timezone.utc
    ).isoformat()

    save_state(
        state
    )

    save_history(
        history
    )

    # ========================================================
    # REFRESH LIVE PRICES
    # ========================================================

    # Refresh after scanning because the scan itself
    # may take some time across 100 markets.

    live_prices = get_live_prices()

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        history=history,
        state=state,
        live_prices=live_prices,
        new_trades=new_trades,
        markets_count=len(markets),
        scanned_count=len(results),
        valid_count=len(valid_candidates),
        rejected_1h=rejected_1h,
        rejected_30m=rejected_30m,
        rejected_15m=rejected_15m,
        rejected_5m=rejected_5m,
        data_errors=data_errors
    )

    print(
        "\n"
        + report
        + "\n",
        flush=True
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    sent = telegram_send(
        report
    )

    if not sent:

        error(
            "Telegram report FAILED"
        )

        raise RuntimeError(
            "Telegram report was not sent"
        )

    log(
        "=========================================="
    )

    log(
        "SCAN COMPLETE"
    )

    log(
        f"Open trades: "
        f"{len(state.get('open_trades', []))}/"
        f"{MAX_OPEN_TRADES}"
    )

    log(
        "=========================================="
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        error(
            "FATAL ERROR: "
            f"{type(exc).__name__}: {exc}"
        )

        traceback.print_exc()

        # Final emergency Telegram message.
        try:

            fatal_report = (
                "<b>🚨 ICHIMOKU SCANNER ERROR</b>\n\n"
                f"<code>"
                f"{tg_escape(type(exc).__name__)}"
                f"</code>\n"
                f"<code>"
                f"{tg_escape(str(exc))}"
                f"</code>"
            )

            telegram_send(
                fatal_report
            )

        except Exception:
            pass

        raise
