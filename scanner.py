# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v5.0
# ============================================================
#
# Kraken Futures
# TOP 100 markets
#
# TIMEFRAMES:
#   1H  = Trend
#   30M = Confirmation
#   15M = Pullback
#   5M  = Trigger
#
# CLOSED CANDLES ONLY
# MAX OPEN TRADES = 1
# RR = 1:1
#
# VIRTUAL TRADE SIMULATOR
#
# STATE:
#   ichimoku_state.json
#   ichimoku_trade_history.json
#
# TELEGRAM:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE + "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE + "/derivatives/api/v3/tickers"
)

CANDLES_URL = (
    BASE + "/api/charts/v1/trade"
)

TOP_N = 100

MAX_OPEN_TRADES = 1

CANDLE_LIMIT = 220

REQUEST_TIMEOUT = 20

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Minimum scores
TREND_LONG_SCORE = 6
TREND_SHORT_SCORE = -6

CONFIRM_LONG_SCORE = 5
CONFIRM_SHORT_SCORE = -5

# Pullback distance from Tenkan/Kijun
PULLBACK_ATR_MULT = 1.5

# Trade timeout
TRADE_TIMEOUT_MINUTES = 240

# HTTP session
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 Crypto-Ichi-Scanner/5.0",
    "Accept": "application/json",
})


# ============================================================
# LOGGING
# ============================================================

def log(msg):
    print(f"[INFO] {msg}", flush=True)


def warn(msg):
    print(f"[WARN] {msg}", flush=True)


# ============================================================
# JSON
# ============================================================

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        warn(f"Cannot load {path}: {e}")
        return default


def save_json(path, data):
    tmp = path + ".tmp"

    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp, path)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        warn("Telegram credentials not configured.")
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
        r = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:
            warn(
                f"Telegram HTTP {r.status_code}: "
                f"{r.text[:300]}"
            )
            return False

        return True

    except Exception as e:
        warn(f"Telegram error: {e}")
        return False


# ============================================================
# KRAKEN API
# ============================================================

def api_get(url, params=None):
    try:
        r = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:
            warn(
                f"API HTTP {r.status_code} "
                f"{r.url} "
                f"{r.text[:500]}"
            )
            return None

        try:
            return r.json()

        except Exception:
            warn(
                f"Invalid JSON from {r.url}: "
                f"{r.text[:500]}"
            )
            return None

    except requests.RequestException as e:
        warn(f"API request failed: {e}")
        return None


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    data = api_get(INSTRUMENTS_URL)

    if not data:
        return []

    if isinstance(data, dict):

        for key in [
            "instruments",
            "markets",
            "result",
            "data"
        ]:

            value = data.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):

                for k in [
                    "instruments",
                    "markets",
                    "data"
                ]:
                    if isinstance(value.get(k), list):
                        return value[k]

    if isinstance(data, list):
        return data

    return []


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = api_get(TICKERS_URL)

    if not data:
        return []

    if isinstance(data, dict):

        for key in [
            "tickers",
            "data",
            "result"
        ]:

            value = data.get(key)

            if isinstance(value, list):
                return value

            if isinstance(value, dict):

                for k in [
                    "tickers",
                    "data"
                ]:
                    if isinstance(value.get(k), list):
                        return value[k]

    if isinstance(data, list):
        return data

    return []


# ============================================================
# HELPERS
# ============================================================

def first_value(obj, keys, default=None):

    if not isinstance(obj, dict):
        return default

    for key in keys:

        if key in obj and obj[key] is not None:
            return obj[key]

    return default


def safe_float(value, default=None):

    try:
        return float(value)
    except Exception:
        return default


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    if not instruments:
        warn("No instruments returned.")
        return []

    if not tickers:
        warn("No tickers returned.")

    ticker_map = {}

    for t in tickers:

        if not isinstance(t, dict):
            continue

        symbol = first_value(
            t,
            ["symbol", "instrument", "market"]
        )

        if symbol:
            ticker_map[str(symbol).upper()] = t

    markets = []

    for ins in instruments:

        if not isinstance(ins, dict):
            continue

        symbol = first_value(
            ins,
            ["symbol", "instrument", "market"]
        )

        if not symbol:
            continue

        symbol = str(symbol)

        # Only perpetual futures
        if not (
            symbol.upper().startswith("PF_")
            or symbol.upper().startswith("PI_")
        ):
            continue

        ticker = ticker_map.get(symbol.upper(), {})

        volume = first_value(
            ticker,
            [
                "volumeQuote",
                "volume24h",
                "quoteVolume",
                "volume",
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

        volume = safe_float(volume, 0)
        price = safe_float(price, 0)

        if volume <= 0:
            volume = 0

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
# CANDLE API
# ============================================================

TIMEFRAME_MAP = {
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h",
}


def parse_candle(c):

    if isinstance(c, dict):

        ts = first_value(
            c,
            ["time", "timestamp", "ts", "t"]
        )

        o = first_value(
            c,
            ["open", "o"]
        )

        h = first_value(
            c,
            ["high", "h"]
        )

        l = first_value(
            c,
            ["low", "l"]
        )

        cl = first_value(
            c,
            ["close", "c"]
        )

    elif isinstance(c, list):

        if len(c) < 5:
            return None

        ts = c[0]
        o = c[1]
        h = c[2]
        l = c[3]
        cl = c[4]

    else:
        return None

    try:

        ts = int(float(ts))

        # Kraken returns epoch milliseconds
        if ts < 10_000_000_000:
            ts *= 1000

        o = float(o)
        h = float(h)
        l = float(l)
        cl = float(cl)

        if h < l:
            return None

        return {
            "time": ts,
            "open": o,
            "high": h,
            "low": l,
            "close": cl,
        }

    except Exception:
        return None


def get_candles(symbol, timeframe_minutes, limit=CANDLE_LIMIT):

    resolution = TIMEFRAME_MAP.get(
        timeframe_minutes
    )

    if not resolution:
        warn(
            f"Unsupported timeframe: "
            f"{timeframe_minutes}"
        )
        return []

    # --------------------------------------------------------
    # IMPORTANT:
    # Kraken Futures Charts API expects:
    #
    # /api/charts/v1/trade/{symbol}/{resolution}
    #
    # resolution examples:
    # 5m
    # 15m
    # 30m
    # 1h
    #
    # count is number of candles.
    # --------------------------------------------------------

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
        params=params
    )

    if data is None:
        return []

    raw = None

    # Official response:
    # {
    #   "candles": [...]
    #   "more_candles": true
    # }

    if isinstance(data, dict):

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

    elif isinstance(data, list):
        raw = data

    if not raw:
        warn(
            f"No candles: "
            f"{symbol} {resolution} "
            f"response={str(data)[:400]}"
        )
        return []

    candles = []

    for item in raw:

        c = parse_candle(item)

        if c:
            candles.append(c)

    if not candles:
        warn(
            f"Could not parse candles: "
            f"{symbol} {resolution}"
        )
        return []

    # Sort oldest -> newest
    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # REMOVE CURRENT OPEN CANDLE
    # --------------------------------------------------------

    now_ms = int(
        time.time() * 1000
    )

    interval_ms = (
        timeframe_minutes * 60 * 1000
    )

    closed = []

    for c in candles:

        candle_end = (
            c["time"] + interval_ms
        )

        if candle_end <= now_ms:
            closed.append(c)

    # Remove duplicates
    unique = {}

    for c in closed:
        unique[c["time"]] = c

    closed = list(
        unique.values()
    )

    closed.sort(
        key=lambda x: x["time"]
    )

    return closed[-limit:]


# ============================================================
# INDICATORS
# ============================================================

def rolling_high(candles, period, index):

    start = index - period + 1

    if start < 0:
        return None

    return max(
        c["high"]
        for c in candles[start:index + 1]
    )


def rolling_low(candles, period, index):

    start = index - period + 1

    if start < 0:
        return None

    return min(
        c["low"]
        for c in candles[start:index + 1]
    )


def calculate_ichimoku(candles):

    n = len(candles)

    if n < 80:
        return None

    i = n - 1

    # Tenkan 9
    hi9 = rolling_high(
        candles, 9, i
    )

    lo9 = rolling_low(
        candles, 9, i
    )

    # Kijun 26
    hi26 = rolling_high(
        candles, 26, i
    )

    lo26 = rolling_low(
        candles, 26, i
    )

    # Senkou B 52
    hi52 = rolling_high(
        candles, 52, i
    )

    lo52 = rolling_low(
        candles, 52, i
    )

    if None in [
        hi9,
        lo9,
        hi26,
        lo26,
        hi52,
        lo52
    ]:
        return None

    tenkan = (
        hi9 + lo9
    ) / 2

    kijun = (
        hi26 + lo26
    ) / 2

    senkou_a_current = (
        tenkan + kijun
    ) / 2

    senkou_b_current = (
        hi52 + lo52
    ) / 2

    # --------------------------------------------------------
    # Visible Kumo
    # The cloud visible now was calculated 26 candles ago.
    # --------------------------------------------------------

    cloud_index = i - 26

    if cloud_index >= 52:

        hi9_old = rolling_high(
            candles, 9, cloud_index
        )

        lo9_old = rolling_low(
            candles, 9, cloud_index
        )

        hi26_old = rolling_high(
            candles, 26, cloud_index
        )

        lo26_old = rolling_low(
            candles, 26, cloud_index
        )

        hi52_old = rolling_high(
            candles, 52, cloud_index
        )

        lo52_old = rolling_low(
            candles, 52, cloud_index
        )

        if None not in [
            hi9_old,
            lo9_old,
            hi26_old,
            lo26_old,
            hi52_old,
            lo52_old
        ]:

            tenkan_old = (
                hi9_old + lo9_old
            ) / 2

            kijun_old = (
                hi26_old + lo26_old
            ) / 2

            cloud_a = (
                tenkan_old + kijun_old
            ) / 2

            cloud_b = (
                hi52_old + lo52_old
            ) / 2

        else:
            cloud_a = senkou_a_current
            cloud_b = senkou_b_current

    else:

        cloud_a = senkou_a_current
        cloud_b = senkou_b_current

    # --------------------------------------------------------
    # Chikou
    # --------------------------------------------------------

    chikou_price = None

    if i >= 26:
        chikou_price = candles[i - 26]["close"]

    close = candles[i]["close"]

    return {
        "close": close,
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a_current,
        "senkou_b": senkou_b_current,
        "cloud_a": cloud_a,
        "cloud_b": cloud_b,
        "chikou": chikou_price,
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 2:
        return None

    trs = []

    for i in range(1, len(candles)):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
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

    # --------------------------------------------------------
    # Price vs current Kumo
    # --------------------------------------------------------

    if close > cloud_top:
        score += 3

    elif close < cloud_bottom:
        score -= 3

    # --------------------------------------------------------
    # Price vs Tenkan
    # --------------------------------------------------------

    if close > info["tenkan"]:
        score += 1

    elif close < info["tenkan"]:
        score -= 1

    # --------------------------------------------------------
    # Price vs Kijun
    # --------------------------------------------------------

    if close > info["kijun"]:
        score += 2

    elif close < info["kijun"]:
        score -= 2

    # --------------------------------------------------------
    # Future Kumo
    # --------------------------------------------------------

    future_a = info["senkou_a"]
    future_b = info["senkou_b"]

    if future_a > future_b:
        score += 2

    elif future_a < future_b:
        score -= 2

    # --------------------------------------------------------
    # Chikou
    # --------------------------------------------------------

    if info["chikou"] is not None:

        if close > info["chikou"]:
            score += 2

        elif close < info["chikou"]:
            score -= 2

    return max(
        -10,
        min(10, score)
    )


def direction_from_score(
    score,
    long_threshold,
    short_threshold
):

    if score >= long_threshold:
        return "LONG"

    if score <= short_threshold:
        return "SHORT"

    return "NONE"


# ============================================================
# PULLBACK
# ============================================================

def check_pullback(
    candles,
    info,
    direction
):

    if not info:
        return False, "No Ichimoku data"

    atr = calculate_atr(
        candles,
        14
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

    near_line = (
        distance_tenkan <= atr * PULLBACK_ATR_MULT
        or
        distance_kijun <= atr * PULLBACK_ATR_MULT
    )

    if direction == "LONG":

        if close <= cloud_bottom:
            return False, "Below Kumo"

        if not near_line:
            return False, "Too far from Tenkan/Kijun"

        return True, "LONG pullback"

    if direction == "SHORT":

        if close >= cloud_top:
            return False, "Above Kumo"

        if not near_line:
            return False, "Too far from Tenkan/Kijun"

        return True, "SHORT pullback"

    return False, "No direction"


# ============================================================
# TRIGGER
# ============================================================

def check_trigger(
    candles,
    info,
    direction
):

    if not info or len(candles) < 3:
        return False, "Not enough candles"

    current = candles[-1]
    previous = candles[-2]

    close = current["close"]
    prev_close = previous["close"]

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

        cross_tenkan = (
            prev_close <= tenkan
            and close > tenkan
        )

        cross_kijun = (
            prev_close <= kijun
            and close > kijun
        )

        above_cloud = (
            close > cloud_top
        )

        if (
            (cross_tenkan or cross_kijun)
            and above_cloud
        ):
            return True, "LONG trigger"

        return False, "No LONG trigger"

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        cross_tenkan = (
            prev_close >= tenkan
            and close < tenkan
        )

        cross_kijun = (
            prev_close >= kijun
            and close < kijun
        )

        below_cloud = (
            close < cloud_bottom
        )

        if (
            (cross_tenkan or cross_kijun)
            and below_cloud
        ):
            return True, "SHORT trigger"

        return False, "No SHORT trigger"

    return False, "No direction"


# ============================================================
# SWING
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
            < candles[i - 1]["low"]
            and
            candles[i]["low"]
            < candles[i + 1]["low"]
        ):
            return candles[i]["low"]

    return min(
        c["low"]
        for c in candles[-lookback:]
    )


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
            > candles[i - 1]["high"]
            and
            candles[i]["high"]
            > candles[i + 1]["high"]
        ):
            return candles[i]["high"]

    return max(
        c["high"]
        for c in candles[-lookback:]
    )


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(symbol):

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

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    candles_1h = get_candles(
        symbol,
        60
    )

    if len(candles_1h) < 80:

        result["reason"] = (
            f"Not enough 1H candles "
            f"({len(candles_1h)}/80)"
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
        score_1h,
        TREND_LONG_SCORE,
        TREND_SHORT_SCORE
    )

    if direction == "NONE":

        result["reason"] = (
            f"1H score {score_1h} "
            f"below trend threshold"
        )

        return result

    result["stage"] = "30m"

    # --------------------------------------------------------
    # 30M
    # --------------------------------------------------------

    candles_30m = get_candles(
        symbol,
        30
    )

    if len(candles_30m) < 80:

        result["reason"] = (
            f"Not enough 30m candles "
            f"({len(candles_30m)}/80)"
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

    if direction == "LONG":

        if score_30m < CONFIRM_LONG_SCORE:

            result["reason"] = (
                f"30m confirmation weak "
                f"({score_30m})"
            )

            return result

    elif direction == "SHORT":

        if score_30m > CONFIRM_SHORT_SCORE:

            result["reason"] = (
                f"30m confirmation weak "
                f"({score_30m})"
            )

            return result

    result["stage"] = "15m"

    # --------------------------------------------------------
    # 15M PULLBACK
    # --------------------------------------------------------

    candles_15m = get_candles(
        symbol,
        15
    )

    if len(candles_15m) < 80:

        result["reason"] = (
            f"Not enough 15m candles "
            f"({len(candles_15m)}/80)"
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

    pullback, pullback_reason = check_pullback(
        candles_15m,
        info_15m,
        direction
    )

    if not pullback:

        result["reason"] = pullback_reason

        return result

    result["pullback"] = True
    result["stage"] = "5m"

    # --------------------------------------------------------
    # 5M TRIGGER
    # --------------------------------------------------------

    candles_5m = get_candles(
        symbol,
        5
    )

    if len(candles_5m) < 80:

        result["reason"] = (
            f"Not enough 5m candles "
            f"({len(candles_5m)}/80)"
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

    trigger, trigger_reason = check_trigger(
        candles_5m,
        info_5m,
        direction
    )

    if not trigger:

        result["reason"] = trigger_reason

        return result

    result["trigger"] = True
    result["stage"] = "VALID"
    result["valid"] = True
    result["direction"] = direction
    result["reason"] = "FULL MTF SETUP"

    # --------------------------------------------------------
    # ENTRY / SL / TP
    # --------------------------------------------------------

    entry = candles_5m[-1]["close"]

    if direction == "LONG":

        swing = latest_swing_low(
            candles_5m
        )

        if swing is None or swing >= entry:

            result["valid"] = False
            result["stage"] = "5m"
            result["reason"] = (
                "Invalid LONG swing SL"
            )

            return result

        sl = swing

        risk = entry - sl

        if risk <= 0:

            result["valid"] = False
            result["stage"] = "5m"
            result["reason"] = (
                "Invalid LONG risk"
            )

            return result

        tp = entry + risk

    else:

        swing = latest_swing_high(
            candles_5m
        )

        if swing is None or swing <= entry:

            result["valid"] = False
            result["stage"] = "5m"
            result["reason"] = (
                "Invalid SHORT swing SL"
            )

            return result

        sl = swing

        risk = sl - entry

        if risk <= 0:

            result["valid"] = False
            result["stage"] = "5m"
            result["reason"] = (
                "Invalid SHORT risk"
            )

            return result

        tp = entry - risk

    result["entry"] = entry
    result["sl"] = sl
    result["tp"] = tp
    result["risk"] = risk

    return result


# ============================================================
# CANDIDATE RANKING
# ============================================================

STAGE_RANK = {
    "VALID": 5,
    "5m": 4,
    "15m": 3,
    "30m": 2,
    "1H": 1,
}


def candidate_rank(result):

    stage = result.get(
        "stage",
        "1H"
    )

    rank = STAGE_RANK.get(
        stage,
        0
    )

    score = (
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
        rank,
        score
    )


# ============================================================
# TRADE MANAGEMENT
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

    if not isinstance(data, dict):
        return default

    return data


def load_history():

    data = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(data, list):
        return []

    return data


def save_state(state):
    save_json(
        STATE_FILE,
        state
    )


def save_history(history):
    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# TRADE CHECK
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
        5
    )

    if not candles:
        return

    now = int(
        time.time()
    )

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

    for c in candles:

        candle_time = int(
            c["time"] / 1000
        )

        if candle_time <= opened_at:
            continue

        high = c["high"]
        low = c["low"]

        # Conservative:
        # if SL and TP hit same candle,
        # SL is assumed first.
        if direction == "LONG":

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

    # Timeout
    if result is None:

        age_minutes = (
            now - opened_at
        ) / 60

        if age_minutes >= TRADE_TIMEOUT_MINUTES:

            result = "TIMEOUT"

            exit_price = candles[-1]["close"]

            reason = "TIMEOUT"

    if result is None:
        return

    # --------------------------------------------------------
    # PNL R
    # --------------------------------------------------------

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        r = 0
    else:

        if direction == "LONG":
            r = (
                exit_price - entry
            ) / risk

        else:
            r = (
                entry - exit_price
            ) / risk

    closed_trade = dict(trade)

    closed_trade.update({
        "exit": exit_price,
        "result": result,
        "reason": reason,
        "r": round(r, 4),
        "closed_at": now,
    })

    history.append(
        closed_trade
    )

    state["open_trade"] = None

    save_history(history)
    save_state(state)

    log(
        f"TRADE CLOSED "
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

    now = int(
        time.time()
    )

    signal_key = (
        f"{signal['symbol']}_"
        f"{signal['direction']}_"
        f"{signal['entry']}_"
        f"{signal['tp']}"
    )

    state["open_trade"] = {
        "symbol": signal["symbol"],
        "direction": signal["direction"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "opened_at": now,
        "signal_key": signal_key,
    }

    state["last_signal_key"] = signal_key

    save_state(state)


# ============================================================
# PERFORMANCE
# ============================================================

def performance(history):

    trades = len(history)

    wins = sum(
        1
        for t in history
        if t.get("result") == "WIN"
    )

    losses = sum(
        1
        for t in history
        if t.get("result") == "LOSS"
    )

    timeouts = sum(
        1
        for t in history
        if t.get("result") == "TIMEOUT"
    )

    total_r = sum(
        float(t.get("r", 0))
        for t in history
    )

    if trades:
        wr = (
            wins / trades
        ) * 100
    else:
        wr = 0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "wr": wr,
        "r": total_r,
    }


# ============================================================
# TELEGRAM REPORT
# ============================================================

def format_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.10f}"


def build_report(
    markets,
    results,
    state,
    history,
    scan_stats,
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
        "⏱ 1H → Trend | 30m → Confirm | "
        "15m → Pullback | 5m → Trigger"
    )

    lines.append(
        "🤖 ICHIMOKU MTF | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

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

    # Open trade
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
            f"Entry: `{format_price(trade['entry'])}`"
        )

        lines.append(
            f"SL: `{format_price(trade['sl'])}`"
        )

        lines.append(
            f"TP: `{format_price(trade['tp'])}`"
        )

    else:

        lines.append(
            "⚪ *OPEN TRADE* None"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # New signal
    if new_signal:

        s = new_signal

        lines.append(
            "🚨 *NEW SIGNAL*"
        )

        lines.append(
            f"*{s['symbol']}* "
            f"{'🟢 LONG' if s['direction'] == 'LONG' else '🔴 SHORT'}"
        )

        lines.append(
            f"1H Score: `{s['score_1h']:+d}`"
        )

        lines.append(
            f"30m Score: `{s['score_30m']:+d}`"
        )

        lines.append(
            f"Entry: `{format_price(s['entry'])}`"
        )

        lines.append(
            f"SL: `{format_price(s['sl'])}`"
        )

        lines.append(
            f"TP: `{format_price(s['tp'])}`"
        )

    else:

        lines.append(
            "🔎 *NEW SIGNAL* No new valid MTF setup."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Candidate list
    lines.append(
        "🎯 *TOP 5 CANDIDATES*"
    )

    ranked = sorted(
        results,
        key=candidate_rank,
        reverse=True
    )[:5]

    if not ranked:

        lines.append(
            "No candidates."
        )

    for i, r in enumerate(
        ranked,
        1
    ):

        direction = r.get(
            "direction",
            "NONE"
        )

        lines.append(
            f"{i}. *{r['symbol']}* "
            f"{direction} | "
            f"1H `{r.get('score_1h', 0):+d}` | "
            f"30m `{r.get('score_30m', 0):+d}` | "
            f"Stage: `{r.get('stage', '-')}`"
        )

        reason = r.get(
            "reason",
            ""
        )

        if reason:
            lines.append(
                f"   └ {reason[:100]}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # Scanner stats
    lines.append(
        "📈 *SCANNER STATUS*"
    )

    lines.append(
        f"Markets: {len(markets)}"
    )

    lines.append(
        f"Scanned: {scan_stats['scanned']}"
    )

    lines.append(
        f"Valid: {scan_stats['valid']}"
    )

    lines.append(
        f"1H rejected: {scan_stats['1h']}"
    )

    lines.append(
        f"30m rejected: {scan_stats['30m']}"
    )

    lines.append(
        f"15m rejected: {scan_stats['15m']}"
    )

    lines.append(
        f"5m rejected: {scan_stats['5m']}"
    )

    lines.append(
        f"Data errors: {scan_stats['data_errors']}"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 70
    )

    print(
        "KRAKEN FUTURES ICHIMOKU MTF SCANNER v5.0"
    )

    print(
        "=" * 70
    )

    state = load_state()

    history = load_history()

    # --------------------------------------------------------
    # Check existing trade first
    # --------------------------------------------------------

    if state.get("open_trade"):

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
                f"Open trade check error: {e}"
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

        msg = (
            "❌ *ICHIMOKU SCANNER ERROR*\n\n"
            "No Kraken Futures markets returned."
        )

        telegram_send(msg)

        raise SystemExit(
            1
        )

    log(
        f"TOP {len(markets)} markets loaded."
    )

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    results = []

    stats = {
        "scanned": 0,
        "valid": 0,
        "1h": 0,
        "30m": 0,
        "15m": 0,
        "5m": 0,
        "data_errors": 0,
    }

    for index, market in enumerate(
        markets,
        1
    ):

        symbol = market["symbol"]

        stats["scanned"] += 1

        try:

            result = analyze_market(
                symbol
            )

            results.append(
                result
            )

            if result["valid"]:

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

                if (
                    "Not enough" in
                    result.get(
                        "reason",
                        ""
                    )
                ):
                    stats["data_errors"] += 1

        except Exception as e:

            stats["data_errors"] += 1

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
                "reason": f"ERROR: {e}",
            })

        if index % 10 == 0:

            log(
                f"Progress: "
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

    # Max 1 open trade
    if not state.get("open_trade"):

        if valid_results:

            candidate = valid_results[0]

            signal_key = (
                f"{candidate['symbol']}_"
                f"{candidate['direction']}_"
                f"{candidate['entry']}_"
                f"{candidate['tp']}"
            )

            if (
                signal_key
                != state.get(
                    "last_signal_key",
                    ""
                )
            ):

                new_signal = candidate

                open_trade(
                    state,
                    candidate
                )

                log(
                    f"NEW SIGNAL: "
                    f"{candidate['symbol']} "
                    f"{candidate['direction']}"
                )

            else:

                log(
                    "Valid setup already processed."
                )

    else:

        log(
            "Open trade exists. "
            "No new trade allowed."
        )

    # --------------------------------------------------------
    # Save run time
    # --------------------------------------------------------

    state["last_run"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    save_state(
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
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:
        main()

    except KeyboardInterrupt:

        print(
            "\nStopped by user."
        )

    except Exception as e:

        print(
            f"\nFATAL ERROR: {e}"
        )

        traceback.print_exc()

        telegram_send(
            "❌ *ICHIMOKU SCANNER FATAL ERROR*\n\n"
            f"`{str(e)[:1000]}`"
        )

        raise
