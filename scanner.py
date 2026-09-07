# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v6.0
# ============================================================
#
# 1H  = TREND
# 30M = CONFIRMATION
# 15M = PULLBACK
# 5M  = PRICE ACTION TRIGGER
#
# CLOSED CANDLES ONLY
# TOP 100 FUTURES
# MAX 1 OPEN TRADE
# RR 1:1
# VIRTUAL TRADING
#
# ============================================================

import os
import json
import time
import traceback
from datetime import datetime, timezone

import requests


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

MAX_OPEN_TRADES = 1

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
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "Mozilla/5.0 Kraken-Ichi-Scanner/6.0",
    "Accept":
        "application/json",
})


# ============================================================
# TIMEFRAME
# ============================================================

TIMEFRAME_MAP = {
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
}


# ============================================================
# LOG
# ============================================================

def log(text):

    print(
        f"[INFO] {text}",
        flush=True
    )


def warn(text):

    print(
        f"[WARN] {text}",
        flush=True
    )


# ============================================================
# JSON
# ============================================================

def load_json(path, default):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception as e:

        warn(
            f"Cannot load {path}: {e}"
        )

        return default


def save_json(path, data):

    temp = path + ".tmp"

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
        path
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        warn(
            "Telegram credentials missing."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            warn(
                "Telegram error "
                f"{response.status_code}: "
                f"{response.text[:500]}"
            )

            return False

        return True

    except Exception as e:

        warn(
            f"Telegram exception: {e}"
        )

        return False


# ============================================================
# API
# ============================================================

def api_get(
    url,
    params=None
):

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            warn(
                f"HTTP {response.status_code} "
                f"{response.url} "
                f"{response.text[:500]}"
            )

            return None

        try:

            return response.json()

        except Exception:

            warn(
                f"Invalid JSON: "
                f"{response.text[:500]}"
            )

            return None

    except requests.RequestException as e:

        warn(
            f"Request failed: {e}"
        )

        return None


# ============================================================
# HELPERS
# ============================================================

def first_value(
    obj,
    keys,
    default=None
):

    if not isinstance(
        obj,
        dict
    ):

        return default

    for key in keys:

        if (
            key in obj
            and obj[key] is not None
        ):

            return obj[key]

    return default


def safe_float(
    value,
    default=0.0
):

    try:

        return float(value)

    except Exception:

        return default


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    data = api_get(
        INSTRUMENTS_URL
    )

    if not data:
        return []

    if isinstance(
        data,
        list
    ):

        return data

    if not isinstance(
        data,
        dict
    ):

        return []

    for key in [
        "instruments",
        "data",
        "result",
        "markets"
    ]:

        value = data.get(key)

        if isinstance(
            value,
            list
        ):

            return value

        if isinstance(
            value,
            dict
        ):

            for subkey in [
                "instruments",
                "data",
                "markets"
            ]:

                subvalue = value.get(
                    subkey
                )

                if isinstance(
                    subvalue,
                    list
                ):

                    return subvalue

    return []


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = api_get(
        TICKERS_URL
    )

    if not data:
        return []

    if isinstance(
        data,
        list
    ):

        return data

    if not isinstance(
        data,
        dict
    ):

        return []

    for key in [
        "tickers",
        "data",
        "result"
    ]:

        value = data.get(key)

        if isinstance(
            value,
            list
        ):

            return value

        if isinstance(
            value,
            dict
        ):

            for subkey in [
                "tickers",
                "data"
            ]:

                subvalue = value.get(
                    subkey
                )

                if isinstance(
                    subvalue,
                    list
                ):

                    return subvalue

    return []


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    instruments = get_instruments()

    tickers = get_tickers()

    if not instruments:

        warn(
            "No instruments."
        )

        return []

    ticker_map = {}

    for ticker in tickers:

        if not isinstance(
            ticker,
            dict
        ):

            continue

        symbol = first_value(
            ticker,
            [
                "symbol",
                "instrument",
                "market"
            ]
        )

        if symbol:

            ticker_map[
                str(symbol).upper()
            ] = ticker

    markets = []

    for instrument in instruments:

        if not isinstance(
            instrument,
            dict
        ):

            continue

        symbol = first_value(
            instrument,
            [
                "symbol",
                "instrument",
                "market"
            ]
        )

        if not symbol:
            continue

        symbol = str(symbol)

        upper = symbol.upper()

        if not (
            upper.startswith("PF_")
            or upper.startswith("PI_")
        ):

            continue

        ticker = ticker_map.get(
            upper,
            {}
        )

        volume = first_value(
            ticker,
            [
                "volumeQuote",
                "volume24h",
                "quoteVolume",
                "volume"
            ],
            0
        )

        price = first_value(
            ticker,
            [
                "last",
                "lastPrice",
                "markPrice",
                "price"
            ],
            0
        )

        volume = safe_float(
            volume
        )

        price = safe_float(
            price
        )

        markets.append({
            "symbol": symbol,
            "volume": volume,
            "price": price,
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle(candle):

    if isinstance(
        candle,
        dict
    ):

        timestamp = first_value(
            candle,
            [
                "time",
                "timestamp",
                "ts",
                "t"
            ]
        )

        open_price = first_value(
            candle,
            [
                "open",
                "o"
            ]
        )

        high_price = first_value(
            candle,
            [
                "high",
                "h"
            ]
        )

        low_price = first_value(
            candle,
            [
                "low",
                "l"
            ]
        )

        close_price = first_value(
            candle,
            [
                "close",
                "c"
            ]
        )

    elif isinstance(
        candle,
        list
    ):

        if len(candle) < 5:
            return None

        timestamp = candle[0]

        open_price = candle[1]

        high_price = candle[2]

        low_price = candle[3]

        close_price = candle[4]

    else:

        return None

    try:

        timestamp = int(
            float(timestamp)
        )

        if timestamp < 10_000_000_000:

            timestamp *= 1000

        open_price = float(
            open_price
        )

        high_price = float(
            high_price
        )

        low_price = float(
            low_price
        )

        close_price = float(
            close_price
        )

        if high_price < low_price:
            return None

        return {
            "time": timestamp,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "close": close_price,
        }

    except Exception:

        return None


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(
    symbol,
    timeframe_minutes,
    limit=CANDLE_LIMIT
):

    resolution = TIMEFRAME_MAP.get(
        timeframe_minutes
    )

    if not resolution:

        return []

    url = (
        f"{CANDLES_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "count": int(limit)
    }

    data = api_get(
        url,
        params
    )

    if data is None:

        return []

    raw = None

    if isinstance(
        data,
        dict
    ):

        if isinstance(
            data.get("candles"),
            list
        ):

            raw = data["candles"]

        elif isinstance(
            data.get("data"),
            list
        ):

            raw = data["data"]

        elif isinstance(
            data.get("result"),
            dict
        ):

            result = data["result"]

            if isinstance(
                result.get("candles"),
                list
            ):

                raw = result["candles"]

    elif isinstance(
        data,
        list
    ):

        raw = data

    if not raw:

        warn(
            f"No candle data: "
            f"{symbol} {resolution}"
        )

        return []

    candles = []

    for item in raw:

        parsed = parse_candle(
            item
        )

        if parsed:

            candles.append(
                parsed
            )

    if not candles:

        warn(
            f"Cannot parse candles: "
            f"{symbol} {resolution}"
        )

        return []

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

    now_ms = int(
        time.time() * 1000
    )

    interval_ms = (
        timeframe_minutes
        * 60
        * 1000
    )

    closed = []

    for candle in candles:

        end_time = (
            candle["time"]
            + interval_ms
        )

        if end_time <= now_ms:

            closed.append(
                candle
            )

    return closed[-limit:]


# ============================================================
# ROLLING HIGH / LOW
# ============================================================

def rolling_high(
    candles,
    period,
    index
):

    start = index - period + 1

    if start < 0:
        return None

    return max(
        candle["high"]
        for candle in candles[
            start:index + 1
        ]
    )


def rolling_low(
    candles,
    period,
    index
):

    start = index - period + 1

    if start < 0:
        return None

    return min(
        candle["low"]
        for candle in candles[
            start:index + 1
        ]
    )


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(
    candles
):

    if len(candles) < MIN_CANDLES:

        return None

    index = len(candles) - 1

    high9 = rolling_high(
        candles,
        9,
        index
    )

    low9 = rolling_low(
        candles,
        9,
        index
    )

    high26 = rolling_high(
        candles,
        26,
        index
    )

    low26 = rolling_low(
        candles,
        26,
        index
    )

    high52 = rolling_high(
        candles,
        52,
        index
    )

    low52 = rolling_low(
        candles,
        52,
        index
    )

    if None in [
        high9,
        low9,
        high26,
        low26,
        high52,
        low52
    ]:

        return None

    tenkan = (
        high9 + low9
    ) / 2

    kijun = (
        high26 + low26
    ) / 2

    senkou_a = (
        tenkan + kijun
    ) / 2

    senkou_b = (
        high52 + low52
    ) / 2

    # --------------------------------------------------------
    # CURRENT VISIBLE CLOUD
    # --------------------------------------------------------

    cloud_index = index - 26

    if cloud_index >= 52:

        old_high9 = rolling_high(
            candles,
            9,
            cloud_index
        )

        old_low9 = rolling_low(
            candles,
            9,
            cloud_index
        )

        old_high26 = rolling_high(
            candles,
            26,
            cloud_index
        )

        old_low26 = rolling_low(
            candles,
            26,
            cloud_index
        )

        old_high52 = rolling_high(
            candles,
            52,
            cloud_index
        )

        old_low52 = rolling_low(
            candles,
            52,
            cloud_index
        )

        if None not in [
            old_high9,
            old_low9,
            old_high26,
            old_low26,
            old_high52,
            old_low52
        ]:

            old_tenkan = (
                old_high9 + old_low9
            ) / 2

            old_kijun = (
                old_high26 + old_low26
            ) / 2

            cloud_a = (
                old_tenkan
                + old_kijun
            ) / 2

            cloud_b = (
                old_high52
                + old_low52
            ) / 2

        else:

            cloud_a = senkou_a
            cloud_b = senkou_b

    else:

        cloud_a = senkou_a
        cloud_b = senkou_b

    # --------------------------------------------------------
    # CHIKOU
    # --------------------------------------------------------

    chikou = None

    if index >= 26:

        chikou = candles[
            index - 26
        ]["close"]

    return {
        "close": candles[index]["close"],
        "open": candles[index]["open"],
        "high": candles[index]["high"],
        "low": candles[index]["low"],
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "cloud_a": cloud_a,
        "cloud_b": cloud_b,
        "chikou": chikou,
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=ATR_PERIOD
):

    if len(candles) < period + 2:

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
                high - previous_close
            ),
            abs(
                low - previous_close
            )
        )

        trs.append(tr)

    if len(trs) < period:

        return None

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def calculate_score(info):

    if not info:
        return 0

    score = 0

    close = info["close"]

    cloud_top = max(
        info["cloud_a"],
        info["cloud_b"]
    )

    cloud_bottom = min(
        info["cloud_a"],
        info["cloud_b"]
    )

    # Price vs Kumo
    if close > cloud_top:

        score += 3

    elif close < cloud_bottom:

        score -= 3

    # Price vs Tenkan
    if close > info["tenkan"]:

        score += 1

    elif close < info["tenkan"]:

        score -= 1

    # Price vs Kijun
    if close > info["kijun"]:

        score += 2

    elif close < info["kijun"]:

        score -= 2

    # Future Kumo
    if (
        info["senkou_a"]
        > info["senkou_b"]
    ):

        score += 2

    elif (
        info["senkou_a"]
        < info["senkou_b"]
    ):

        score -= 2

    # Chikou
    if info["chikou"] is not None:

        if close > info["chikou"]:

            score += 2

        elif close < info["chikou"]:

            score -= 2

    return max(
        -10,
        min(
            10,
            score
        )
    )


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
# 15M PULLBACK
# ============================================================

def check_pullback(
    candles,
    info,
    direction
):

    if not info:

        return False, "No Ichimoku"

    atr = calculate_atr(
        candles
    )

    if atr is None or atr <= 0:

        return False, "No ATR"

    close = info["close"]

    cloud_top = max(
        info["cloud_a"],
        info["cloud_b"]
    )

    cloud_bottom = min(
        info["cloud_a"],
        info["cloud_b"]
    )

    distance_tenkan = abs(
        close - info["tenkan"]
    )

    distance_kijun = abs(
        close - info["kijun"]
    )

    near_tenkan = (
        distance_tenkan
        <= atr * PULLBACK_ATR_MULT
    )

    near_kijun = (
        distance_kijun
        <= atr * PULLBACK_ATR_MULT
    )

    near_line = (
        near_tenkan
        or near_kijun
    )

    if direction == "LONG":

        if close <= cloud_bottom:

            return (
                False,
                "15m below Kumo"
            )

        if not near_line:

            return (
                False,
                "15m too far from Tenkan/Kijun"
            )

        return (
            True,
            "15m LONG pullback"
        )

    if direction == "SHORT":

        if close >= cloud_top:

            return (
                False,
                "15m above Kumo"
            )

        if not near_line:

            return (
                False,
                "15m too far from Tenkan/Kijun"
            )

        return (
            True,
            "15m SHORT pullback"
        )

    return False, "No direction"


# ============================================================
# 5M PRICE ACTION TRIGGER
# ============================================================

def check_5m_trigger(
    candles,
    info,
    direction
):

    if (
        not info
        or len(candles) < 5
    ):

        return (
            False,
            "Not enough 5m candles"
        )

    current = candles[-1]

    previous = candles[-2]

    prev2 = candles[-3]

    close = current["close"]

    open_price = current["open"]

    high = current["high"]

    low = current["low"]

    prev_close = previous["close"]

    prev_open = previous["open"]

    prev_high = previous["high"]

    prev_low = previous["low"]

    tenkan = info["tenkan"]

    kijun = info["kijun"]

    cloud_top = max(
        info["cloud_a"],
        info["cloud_b"]
    )

    cloud_bottom = min(
        info["cloud_a"],
        info["cloud_b"]
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        # 1. Tenkan cross
        cross_tenkan = (
            prev_close <= tenkan
            and
            close > tenkan
        )

        # 2. Kijun cross
        cross_kijun = (
            prev_close <= kijun
            and
            close > kijun
        )

        # 3. Strong bullish close above Tenkan
        bullish_close = (
            close > open_price
            and
            close > tenkan
        )

        # 4. Break previous candle high
        break_prev_high = (
            close > prev_high
        )

        # 5. Break cloud
        break_cloud = (
            close > cloud_top
        )

        # 6. Bullish engulfing
        bullish_engulfing = (
            prev_close < prev_open
            and
            close > open_price
            and
            open_price <= prev_close
            and
            close >= prev_open
        )

        # 7. Momentum continuation
        momentum = (
            close > prev_close
            and
            prev_close >= prev2["close"]
            and
            close > tenkan
        )

        # ----------------------------------------------------
        # Strong trigger
        # ----------------------------------------------------

        if (
            (
                cross_tenkan
                or cross_kijun
                or bullish_engulfing
                or break_prev_high
                or momentum
            )
            and
            bullish_close
            and
            close > cloud_bottom
        ):

            reasons = []

            if cross_tenkan:
                reasons.append(
                    "Tenkan Cross"
                )

            if cross_kijun:
                reasons.append(
                    "Kijun Cross"
                )

            if bullish_engulfing:
                reasons.append(
                    "Bull Engulf"
                )

            if break_prev_high:
                reasons.append(
                    "Break High"
                )

            if momentum:
                reasons.append(
                    "Momentum"
                )

            if break_cloud:
                reasons.append(
                    "Cloud Break"
                )

            return (
                True,
                "LONG: "
                + ", ".join(reasons)
            )

        return (
            False,
            "No LONG trigger"
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        # 1. Tenkan cross
        cross_tenkan = (
            prev_close >= tenkan
            and
            close < tenkan
        )

        # 2. Kijun cross
        cross_kijun = (
            prev_close >= kijun
            and
            close < kijun
        )

        # 3. Strong bearish close
        bearish_close = (
            close < open_price
            and
            close < kijun
        )

        # 4. Break previous low
        break_prev_low = (
            close < prev_low
        )

        # 5. Break cloud
        break_cloud = (
            close < cloud_bottom
        )

        # 6. Bearish engulfing
        bearish_engulfing = (
            prev_close > prev_open
            and
            close < open_price
            and
            open_price >= prev_close
            and
            close <= prev_open
        )

        # 7. Momentum continuation
        momentum = (
            close < prev_close
            and
            prev_close <= prev2["close"]
            and
            close < kijun
        )

        if (
            (
                cross_tenkan
                or cross_kijun
                or bearish_engulfing
                or break_prev_low
                or momentum
            )
            and
            bearish_close
            and
            close < cloud_top
        ):

            reasons = []

            if cross_tenkan:
                reasons.append(
                    "Tenkan Cross"
                )

            if cross_kijun:
                reasons.append(
                    "Kijun Cross"
                )

            if bearish_engulfing:
                reasons.append(
                    "Bear Engulf"
                )

            if break_prev_low:
                reasons.append(
                    "Break Low"
                )

            if momentum:
                reasons.append(
                    "Momentum"
                )

            if break_cloud:
                reasons.append(
                    "Cloud Break"
                )

            return (
                True,
                "SHORT: "
                + ", ".join(reasons)
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
# SWING LOW
# ============================================================

def latest_swing_low(
    candles,
    lookback=20
):

    if len(candles) < 7:

        return None

    start = max(
        2,
        len(candles) - lookback
    )

    end = len(candles) - 2

    for i in range(
        end,
        start - 1,
        -1
    ):

        if (
            candles[i]["low"]
            <
            candles[i - 1]["low"]
            and
            candles[i]["low"]
            <
            candles[i + 1]["low"]
        ):

            return candles[i]["low"]

    return min(
        c["low"]
        for c in candles[-lookback:]
    )


# ============================================================
# SWING HIGH
# ============================================================

def latest_swing_high(
    candles,
    lookback=20
):

    if len(candles) < 7:

        return None

    start = max(
        2,
        len(candles) - lookback
    )

    end = len(candles) - 2

    for i in range(
        end,
        start - 1,
        -1
    ):

        if (
            candles[i]["high"]
            >
            candles[i - 1]["high"]
            and
            candles[i]["high"]
            >
            candles[i + 1]["high"]
        ):

            return candles[i]["high"]

    return max(
        c["high"]
        for c in candles[-lookback:]
    )


# ============================================================
# ANALYZE MARKET
# ============================================================

def analyze_market(
    symbol
):

    result = {
        "symbol": symbol,
        "valid": False,
        "direction": "NONE",
        "stage": "1H",
        "score_1h": 0,
        "score_30m": 0,
        "pullback": False,
        "trigger": False,
        "reason": "",
    }

    # ========================================================
    # 1H
    # ========================================================

    candles_1h = get_candles(
        symbol,
        60
    )

    if len(candles_1h) < MIN_CANDLES:

        result["reason"] = (
            f"Not enough 1H candles "
            f"({len(candles_1h)}/{MIN_CANDLES})"
        )

        return result

    info_1h = calculate_ichimoku(
        candles_1h
    )

    if not info_1h:

        result["reason"] = (
            "1H Ichimoku unavailable"
        )

        return result

    score_1h = calculate_score(
        info_1h
    )

    result["score_1h"] = score_1h

    direction = direction_from_score(
        score_1h
    )

    if direction == "NONE":

        result["reason"] = (
            f"1H score {score_1h} "
            f"not strong enough"
        )

        return result

    result["stage"] = "30m"

    # ========================================================
    # 30M
    # ========================================================

    candles_30m = get_candles(
        symbol,
        30
    )

    if len(candles_30m) < MIN_CANDLES:

        result["reason"] = (
            f"Not enough 30m candles "
            f"({len(candles_30m)}/{MIN_CANDLES})"
        )

        return result

    info_30m = calculate_ichimoku(
        candles_30m
    )

    if not info_30m:

        result["reason"] = (
            "30m Ichimoku unavailable"
        )

        return result

    score_30m = calculate_score(
        info_30m
    )

    result["score_30m"] = score_30m

    # Long confirmation
    if direction == "LONG":

        if score_30m < 5:

            result["reason"] = (
                f"30m LONG confirmation weak "
                f"({score_30m})"
            )

            return result

    # Short confirmation
    if direction == "SHORT":

        if score_30m > -5:

            result["reason"] = (
                f"30m SHORT confirmation weak "
                f"({score_30m})"
            )

            return result

    result["stage"] = "15m"

    # ========================================================
    # 15M
    # ========================================================

    candles_15m = get_candles(
        symbol,
        15
    )

    if len(candles_15m) < MIN_CANDLES:

        result["reason"] = (
            f"Not enough 15m candles "
            f"({len(candles_15m)}/{MIN_CANDLES})"
        )

        return result

    info_15m = calculate_ichimoku(
        candles_15m
    )

    if not info_15m:

        result["reason"] = (
            "15m Ichimoku unavailable"
        )

        return result

    pullback, pullback_reason = (
        check_pullback(
            candles_15m,
            info_15m,
            direction
        )
    )

    if not pullback:

        result["reason"] = (
            pullback_reason
        )

        return result

    result["pullback"] = True

    result["stage"] = "5m"

    # ========================================================
    # 5M
    # ========================================================

    candles_5m = get_candles(
        symbol,
        5
    )

    if len(candles_5m) < MIN_CANDLES:

        result["reason"] = (
            f"Not enough 5m candles "
            f"({len(candles_5m)}/{MIN_CANDLES})"
        )

        return result

    info_5m = calculate_ichimoku(
        candles_5m
    )

    if not info_5m:

        result["reason"] = (
            "5m Ichimoku unavailable"
        )

        return result

    trigger, trigger_reason = (
        check_5m_trigger(
            candles_5m,
            info_5m,
            direction
        )
    )

    if not trigger:

        result["reason"] = (
            trigger_reason
        )

        return result

    result["trigger"] = True

    # ========================================================
    # ENTRY
    # ========================================================

    entry = candles_5m[-1]["close"]

    # ========================================================
    # LONG SL / TP
    # ========================================================

    if direction == "LONG":

        swing_low = latest_swing_low(
            candles_5m
        )

        if (
            swing_low is None
            or swing_low >= entry
        ):

            result["stage"] = "5m"

            result["reason"] = (
                "Invalid LONG swing"
            )

            return result

        sl = swing_low

        risk = entry - sl

        if risk <= 0:

            result["reason"] = (
                "Invalid LONG risk"
            )

            return result

        tp = (
            entry
            + risk * RR
        )

    # ========================================================
    # SHORT SL / TP
    # ========================================================

    else:

        swing_high = latest_swing_high(
            candles_5m
        )

        if (
            swing_high is None
            or swing_high <= entry
        ):

            result["stage"] = "5m"

            result["reason"] = (
                "Invalid SHORT swing"
            )

            return result

        sl = swing_high

        risk = sl - entry

        if risk <= 0:

            result["reason"] = (
                "Invalid SHORT risk"
            )

            return result

        tp = (
            entry
            - risk * RR
        )

    # ========================================================
    # VALID
    # ========================================================

    result["valid"] = True

    result["direction"] = direction

    result["stage"] = "VALID"

    result["entry"] = entry

    result["sl"] = sl

    result["tp"] = tp

    result["risk"] = risk

    result["reason"] = trigger_reason

    return result


# ============================================================
# CANDIDATE RANK
# ============================================================

STAGE_RANK = {
    "VALID": 5,
    "5m": 4,
    "15m": 3,
    "30m": 2,
    "1H": 1,
}


def candidate_rank(
    result
):

    stage = result.get(
        "stage",
        "1H"
    )

    stage_rank = STAGE_RANK.get(
        stage,
        0
    )

    score_total = (
        abs(
            result.get(
                "score_1h",
                0
            )
        )
        +
        abs(
            result.get(
                "score_30m",
                0
            )
        )
    )

    return (
        stage_rank,
        score_total
    )


# ============================================================
# STATE
# ============================================================

def load_state():

    default = {
        "open_trade": None,
        "last_signal_key": "",
        "last_run": "",
    }

    data = load_json(
        STATE_FILE,
        default
    )

    if not isinstance(
        data,
        dict
    ):

        return default

    return data


def load_history():

    data = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(
        data,
        list
    ):

        return []

    return data


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    state,
    history
):

    trade = state.get(
        "open_trade"
    )

    if not trade:
        return

    symbol = trade["symbol"]

    direction = trade["direction"]

    candles = get_candles(
        symbol,
        5,
        20
    )

    if not candles:

        return

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp = float(
        trade["tp"]
    )

    opened_at = int(
        trade["opened_at"]
    )

    result = None

    exit_price = None

    reason = ""

    for candle in candles:

        candle_time = int(
            candle["time"] / 1000
        )

        if candle_time <= opened_at:

            continue

        high = candle["high"]

        low = candle["low"]

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            # Conservative:
            # SL before TP
            if low <= sl:

                result = "LOSS"

                exit_price = sl

                reason = "SL"

                break

            if high >= tp:

                result = "WIN"

                exit_price = tp

                reason = "TP"

                break

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            if high >= sl:

                result = "LOSS"

                exit_price = sl

                reason = "SL"

                break

            if low <= tp:

                result = "WIN"

                exit_price = tp

                reason = "TP"

                break

    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    if result is None:

        age_minutes = (
            time.time()
            - opened_at
        ) / 60

        if (
            age_minutes
            >= TRADE_TIMEOUT_MINUTES
        ):

            result = "TIMEOUT"

            exit_price = candles[-1]["close"]

            reason = "TIMEOUT"

    if result is None:

        return

    # --------------------------------------------------------
    # R
    # --------------------------------------------------------

    risk = abs(
        entry - sl
    )

    if risk <= 0:

        r = 0

    elif direction == "LONG":

        r = (
            exit_price
            - entry
        ) / risk

    else:

        r = (
            entry
            - exit_price
        ) / risk

    closed_trade = dict(
        trade
    )

    closed_trade.update({
        "exit": exit_price,
        "result": result,
        "reason": reason,
        "r": round(
            r,
            4
        ),
        "closed_at": int(
            time.time()
        ),
    })

    history.append(
        closed_trade
    )

    state["open_trade"] = None

    save_json(
        HISTORY_FILE,
        history
    )

    save_json(
        STATE_FILE,
        state
    )

    log(
        f"CLOSED "
        f"{symbol} "
        f"{direction} "
        f"{result} "
        f"{reason}"
    )


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    state,
    signal
):

    signal_key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['entry']}_"
        f"{signal['sl']}_"
        f"{signal['tp']}"
    )

    state["open_trade"] = {

        "symbol":
            signal["symbol"],

        "direction":
            signal["direction"],

        "entry":
            signal["entry"],

        "sl":
            signal["sl"],

        "tp":
            signal["tp"],

        "opened_at":
            int(time.time()),

        "signal_key":
            signal_key,
    }

    state["last_signal_key"] = (
        signal_key
    )

    save_json(
        STATE_FILE,
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

    wins = sum(
        1
        for t in history
        if t.get("result")
        == "WIN"
    )

    losses = sum(
        1
        for t in history
        if t.get("result")
        == "LOSS"
    )

    timeouts = sum(
        1
        for t in history
        if t.get("result")
        == "TIMEOUT"
    )

    total_r = sum(
        float(
            t.get(
                "r",
                0
            )
        )
        for t in history
    )

    if trades:

        win_rate = (
            wins / trades
        ) * 100

    else:

        win_rate = 0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "wr": win_rate,
        "r": total_r,
    }


# ============================================================
# PRICE FORMAT
# ============================================================

def format_price(
    value
):

    if value is None:

        return "-"

    value = float(
        value
    )

    if value >= 1000:

        return f"{value:.2f}"

    if value >= 1:

        return f"{value:.5f}"

    if value >= 0.01:

        return f"{value:.6f}"

    return f"{value:.10f}"


# ============================================================
# REPORT
# ============================================================

def build_report(
    markets,
    results,
    state,
    history,
    stats,
    new_signal
):

    p = performance(
        history
    )

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    lines = []

    lines.append(
        "📡 *KRAKEN FUTURES ICHIMOKU REPORT*"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        "⏱ 1H → Trend | "
        "30m → Confirm | "
        "15m → Pullback | "
        "5m → Trigger"
    )

    lines.append(
        "🤖 ICHIMOKU MTF v6 | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Performance
    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        f"Trades {p['trades']} | "
        f"🟢 {p['wins']} | "
        f"🔴 {p['losses']} | "
        f"⚪ {p['timeouts']}"
    )

    lines.append(
        f"🏆 WR {p['wr']:.1f}% | "
        f"R {p['r']:+.2f}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Open
    trade = state.get(
        "open_trade"
    )

    if trade:

        lines.append(
            "🔴 *OPEN TRADE*"
        )

        lines.append(
            f"{trade['symbol']} "
            f"{trade['direction']}"
        )

        lines.append(
            "Entry: "
            f"`{format_price(trade['entry'])}`"
        )

        lines.append(
            "SL: "
            f"`{format_price(trade['sl'])}`"
        )

        lines.append(
            "TP: "
            f"`{format_price(trade['tp'])}`"
        )

    else:

        lines.append(
            "⚪ *OPEN TRADE* None"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Signal
    if new_signal:

        s = new_signal

        emoji = (
            "🟢"
            if s["direction"]
            == "LONG"
            else
            "🔴"
        )

        lines.append(
            "🚨 *NEW SIGNAL*"
        )

        lines.append(
            f"*{s['symbol']}* "
            f"{emoji} "
            f"{s['direction']}"
        )

        lines.append(
            f"1H Score: "
            f"`{s['score_1h']:+d}`"
        )

        lines.append(
            f"30m Score: "
            f"`{s['score_30m']:+d}`"
        )

        lines.append(
            "Entry: "
            f"`{format_price(s['entry'])}`"
        )

        lines.append(
            "SL: "
            f"`{format_price(s['sl'])}`"
        )

        lines.append(
            "TP: "
            f"`{format_price(s['tp'])}`"
        )

        lines.append(
            f"Trigger: "
            f"`{s['reason']}`"
        )

    else:

        lines.append(
            "🔎 *NEW SIGNAL* "
            "No new valid MTF setup."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Candidates
    lines.append(
        "🎯 *TOP 5 CANDIDATES*"
    )

    ranked = sorted(
        results,
        key=candidate_rank,
        reverse=True
    )

    ranked = ranked[:5]

    if not ranked:

        lines.append(
            "No candidates."
        )

    for number, result in enumerate(
        ranked,
        1
    ):

        direction = result.get(
            "direction",
            "NONE"
        )

        score_1h = result.get(
            "score_1h",
            0
        )

        score_30m = result.get(
            "score_30m",
            0
        )

        stage = result.get(
            "stage",
            "-"
        )

        lines.append(
            f"{number}. "
            f"*{result['symbol']}* "
            f"{direction} | "
            f"1H `{score_1h:+d}` | "
            f"30m `{score_30m:+d}` | "
            f"Stage: `{stage}`"
        )

        reason = result.get(
            "reason",
            ""
        )

        if reason:

            lines.append(
                f"   └ {reason[:120]}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Scanner
    lines.append(
        "📈 *SCANNER STATUS*"
    )

    lines.append(
        f"Markets: {len(markets)}"
    )

    lines.append(
        f"Scanned: "
        f"{stats['scanned']}"
    )

    lines.append(
        f"Valid: "
        f"{stats['valid']}"
    )

    lines.append(
        f"1H rejected: "
        f"{stats['1h']}"
    )

    lines.append(
        f"30m rejected: "
        f"{stats['30m']}"
    )

    lines.append(
        f"15m rejected: "
        f"{stats['15m']}"
    )

    lines.append(
        f"5m rejected: "
        f"{stats['5m']}"
    )

    lines.append(
        f"Data errors: "
        f"{stats['data_errors']}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "KRAKEN FUTURES "
        "ICHIMOKU MTF SCANNER v6.0"
    )

    print(
        "=" * 70
    )

    state = load_state()

    history = load_history()

    # --------------------------------------------------------
    # Existing trade
    # --------------------------------------------------------

    if state.get(
        "open_trade"
    ):

        log(
            "Checking open trade..."
        )

        try:

            check_open_trade(
                state,
                history
            )

        except Exception as e:

            warn(
                f"Trade check error: {e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Markets
    # --------------------------------------------------------

    log(
        "Loading Kraken markets..."
    )

    markets = get_top_markets()

    if not markets:

        telegram_send(
            "❌ *ICHIMOKU SCANNER ERROR*\n\n"
            "No Kraken Futures markets."
        )

        raise SystemExit(
            1
        )

    log(
        f"Loaded {len(markets)} markets."
    )

    # --------------------------------------------------------
    # Stats
    # --------------------------------------------------------

    stats = {
        "scanned": 0,
        "valid": 0,
        "1h": 0,
        "30m": 0,
        "15m": 0,
        "5m": 0,
        "data_errors": 0,
    }

    results = []

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for index, market in enumerate(
        markets,
        1
    ):

        symbol = market[
            "symbol"
        ]

        stats["scanned"] += 1

        try:

            result = analyze_market(
                symbol
            )

            results.append(
                result
            )

            if result.get(
                "valid"
            ):

                stats["valid"] += 1

            else:

                stage = result.get(
                    "stage",
                    "1H"
                )

                if stage == "1H":

                    stats["1h"] += 1

                elif stage == "30m":

                    stats["30m"] += 1

                elif stage == "15m":

                    stats["15m"] += 1

                elif stage == "5m":

                    stats["5m"] += 1

                reason = result.get(
                    "reason",
                    ""
                )

                if (
                    "Not enough"
                    in reason
                    or
                    "unavailable"
                    in reason
                ):

                    stats[
                        "data_errors"
                    ] += 1

        except Exception as e:

            stats[
                "data_errors"
            ] += 1

            warn(
                f"{symbol}: {e}"
            )

            results.append({
                "symbol": symbol,
                "valid": False,
                "direction": "NONE",
                "stage": "1H",
                "score_1h": 0,
                "score_30m": 0,
                "reason":
                    f"ERROR: {e}",
            })

        if index % 10 == 0:

            log(
                f"Progress "
                f"{index}/{len(markets)}"
            )

    # --------------------------------------------------------
    # New signal
    # --------------------------------------------------------

    valid_results = [
        r
        for r in results
        if r.get("valid")
    ]

    valid_results.sort(
        key=candidate_rank,
        reverse=True
    )

    new_signal = None

    if not state.get(
        "open_trade"
    ):

        if valid_results:

            candidate = (
                valid_results[0]
            )

            signal_key = (
                f"{candidate['symbol']}_"
                f"{candidate['direction']}_"
                f"{candidate['entry']}_"
                f"{candidate['sl']}_"
                f"{candidate['tp']}"
            )

            if (
                signal_key
                != state.get(
                    "last_signal_key",
                    ""
                )
            ):

                new_signal = (
                    candidate
                )

                open_trade(
                    state,
                    candidate
                )

                log(
                    "NEW SIGNAL "
                    f"{candidate['symbol']} "
                    f"{candidate['direction']}"
                )

    else:

        log(
            "Open trade exists. "
            "No new signal."
        )

    # --------------------------------------------------------
    # Save last run
    # --------------------------------------------------------

    state["last_run"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    save_json(
        STATE_FILE,
        state
    )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report = build_report(
        markets,
        results,
        state,
        history,
        stats,
        new_signal
    )

    print()
    print(report)
    print()

    telegram_send(
        report
    )

    print(
        "=" * 70
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        "=" * 70
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "Stopped."
        )

    except Exception as e:

        print(
            f"FATAL ERROR: {e}"
        )

        traceback.print_exc()

        telegram_send(
            "❌ *ICHIMOKU FATAL ERROR*\n\n"
            f"`{str(e)[:1000]}`"
        )

        raise
