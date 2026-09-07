# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v2
# ============================================================
#
# 1H  -> TREND
# 30m -> CONFIRMATION
# 15m -> PULLBACK
# 5m  -> TRIGGER
#
# TOP 100
# CLOSED CANDLES ONLY
# MAX OPEN TRADES = 1
# VIRTUAL SIGNAL SIMULATOR
#
# IMPORTANT:
# Current Kumo = spans calculated 26 candles ago
# Future Kumo  = spans calculated on current candle
#
# REPORT:
# Shows TOP 5 rejected candidates and exact rejection reason
# ============================================================

import os
import json
import time
import threading
import requests

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TOP_N = 100
MAX_OPEN_TRADES = 1

CANDLE_LIMIT = 180
MAX_WORKERS = 12
REQUEST_TIMEOUT = 20

TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
}

# ============================================================
# ICHIMOKU
# ============================================================

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26

# ============================================================
# ATR / PULLBACK
# ============================================================

ATR_PERIOD = 14
PULLBACK_ATR_MULTIPLIER = 1.5

# ============================================================
# SCORE
# ============================================================

MIN_1H_SCORE = 6
MIN_30M_SCORE = 5

# ============================================================
# TRADE
# ============================================================

TARGET_RR = 1.0
MAX_TRADE_MINUTES = 240

_thread_local = threading.local()
STATE_LOCK = threading.Lock()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_string():
    return utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def now_ms():
    return int(time.time() * 1000)


# ============================================================
# HTTP
# ============================================================

def get_session():

    if not hasattr(
        _thread_local,
        "session"
    ):

        session = requests.Session()

        session.headers.update({
            "User-Agent":
                "Kraken-Ichi-MTF-Scanner/2.0"
        })

        _thread_local.session = session

    return _thread_local.session


def http_get(path, params=None):

    url = BASE_URL + path

    response = get_session().get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# JSON
# ============================================================

def load_json(filename, default):

    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return default


def save_json(filename, data):

    temp = filename + ".tmp"

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


def load_state():

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(
        state,
        dict
    ):

        state = {}

    state.setdefault(
        "open_trade",
        None
    )

    state.setdefault(
        "last_signal_key",
        ""
    )

    state.setdefault(
        "last_scan",
        ""
    )

    return state


def save_state(state):

    with STATE_LOCK:

        save_json(
            STATE_FILE,
            state
        )


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


def save_history(history):

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN:
        print(text)
        return False

    if not TELEGRAM_CHAT_ID:
        print(text)
        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=20
        )

        r.raise_for_status()

        return True

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )

        return False


# ============================================================
# KRAKEN MARKETS
# ============================================================

def get_instruments():

    data = http_get(
        "/derivatives/api/v3/instruments"
    )

    instruments = data.get(
        "instruments",
        []
    )

    result = []

    for item in instruments:

        symbol = item.get(
            "symbol",
            ""
        )

        if not symbol:
            continue

        if not symbol.startswith("PF_"):
            continue

        if "USD" not in symbol:
            continue

        status = str(
            item.get(
                "status",
                "online"
            )
        ).lower()

        if status not in (
            "online",
            "open",
            "trading"
        ):
            continue

        result.append(
            item
        )

    return result


def get_tickers():

    data = http_get(
        "/derivatives/api/v3/tickers"
    )

    tickers = data.get(
        "tickers",
        []
    )

    result = {}

    for item in tickers:

        symbol = item.get(
            "symbol"
        )

        if not symbol:
            continue

        try:

            last = float(
                item.get(
                    "last",
                    0
                ) or 0
            )

        except Exception:

            last = 0

        try:

            volume = float(
                item.get(
                    "vol24h",
                    item.get(
                        "volume24h",
                        0
                    )
                ) or 0
            )

        except Exception:

            volume = 0

        if last <= 0:
            continue

        result[symbol] = {
            "last": last,
            "volume": volume
        }

    return result


def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    markets = []

    for item in instruments:

        symbol = item.get(
            "symbol"
        )

        if symbol not in tickers:
            continue

        markets.append({
            "symbol": symbol,
            "last":
                tickers[symbol]["last"],
            "volume":
                tickers[symbol]["volume"]
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# CANDLES
# ============================================================

def filter_closed_candles(
    candles,
    timeframe_minutes
):

    current = now_ms()

    candle_ms = (
        timeframe_minutes *
        60 *
        1000
    )

    result = []

    for candle in candles:

        close_time = (
            candle["time"] +
            candle_ms
        )

        if close_time <= current:
            result.append(
                candle
            )

    return result[-CANDLE_LIMIT:]


def fetch_candles(
    symbol,
    timeframe
):

    minutes = TIMEFRAMES[
        timeframe
    ]

    path = (
        f"/api/charts/v1/trade/"
        f"{symbol}/{minutes}"
    )

    try:

        data = http_get(
            path,
            params={
                "from": 0,
                "to": int(time.time())
            }
        )

    except Exception as e:

        print(
            f"[CANDLE ERROR] "
            f"{symbol} {timeframe}: {e}"
        )

        return []

    raw = data.get(
        "candles",
        []
    )

    if not raw:
        raw = data.get(
            "data",
            []
        )

    candles = []

    for c in raw:

        try:

            if isinstance(
                c,
                dict
            ):

                timestamp = (
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("ts")
                )

                op = (
                    c.get("open")
                    or c.get("o")
                )

                hi = (
                    c.get("high")
                    or c.get("h")
                )

                lo = (
                    c.get("low")
                    or c.get("l")
                )

                cl = (
                    c.get("close")
                    or c.get("c")
                )

                vol = (
                    c.get("volume")
                    or c.get("v")
                    or 0
                )

            else:

                if len(c) < 5:
                    continue

                timestamp = c[0]
                op = c[1]
                hi = c[2]
                lo = c[3]
                cl = c[4]

                vol = (
                    c[5]
                    if len(c) > 5
                    else 0
                )

            timestamp = int(
                float(timestamp)
            )

            if timestamp < 10_000_000_000:
                timestamp *= 1000

            candles.append({
                "time": timestamp,
                "open": float(op),
                "high": float(hi),
                "low": float(lo),
                "close": float(cl),
                "volume": float(vol or 0)
            })

        except Exception:

            continue

    candles.sort(
        key=lambda x: x["time"]
    )

    return filter_closed_candles(
        candles,
        minutes
    )


def fetch_market_data(market):

    symbol = market["symbol"]

    result = {
        "symbol": symbol,
        "5m": [],
        "15m": [],
        "30m": [],
        "1h": []
    }

    for tf in TIMEFRAMES:

        candles = fetch_candles(
            symbol,
            tf
        )

        if len(candles) < 80:
            return None

        result[tf] = candles

    return result


# ============================================================
# BASIC
# ============================================================

def highest(
    candles,
    start,
    end
):

    return max(
        candles[i]["high"]
        for i in range(
            start,
            end + 1
        )
    )


def lowest(
    candles,
    start,
    end
):

    return min(
        candles[i]["low"]
        for i in range(
            start,
            end + 1
        )
    )


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku_values(
    candles,
    index
):

    if index < SENKOU_B_PERIOD - 1:
        return None

    tenkan = (
        highest(
            candles,
            index -
            TENKAN_PERIOD + 1,
            index
        )
        +
        lowest(
            candles,
            index -
            TENKAN_PERIOD + 1,
            index
        )
    ) / 2

    kijun = (
        highest(
            candles,
            index -
            KIJUN_PERIOD + 1,
            index
        )
        +
        lowest(
            candles,
            index -
            KIJUN_PERIOD + 1,
            index
        )
    ) / 2

    span_a = (
        tenkan +
        kijun
    ) / 2

    span_b = (
        highest(
            candles,
            index -
            SENKOU_B_PERIOD + 1,
            index
        )
        +
        lowest(
            candles,
            index -
            SENKOU_B_PERIOD + 1,
            index
        )
    ) / 2

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b
    }


def ichimoku_at(
    candles,
    index
):

    current = ichimoku_values(
        candles,
        index
    )

    if current is None:
        return None

    # --------------------------------------------------------
    # CURRENT KUMO
    # --------------------------------------------------------

    cloud_index = (
        index -
        DISPLACEMENT
    )

    if cloud_index < (
        SENKOU_B_PERIOD - 1
    ):
        return None

    old = ichimoku_values(
        candles,
        cloud_index
    )

    if old is None:
        return None

    current_span_a = old[
        "span_a"
    ]

    current_span_b = old[
        "span_b"
    ]

    current_top = max(
        current_span_a,
        current_span_b
    )

    current_bottom = min(
        current_span_a,
        current_span_b
    )

    # --------------------------------------------------------
    # FUTURE KUMO
    # --------------------------------------------------------

    future_span_a = current[
        "span_a"
    ]

    future_span_b = current[
        "span_b"
    ]

    future_top = max(
        future_span_a,
        future_span_b
    )

    future_bottom = min(
        future_span_a,
        future_span_b
    )

    return {
        "tenkan":
            current["tenkan"],

        "kijun":
            current["kijun"],

        "current_span_a":
            current_span_a,

        "current_span_b":
            current_span_b,

        "current_cloud_top":
            current_top,

        "current_cloud_bottom":
            current_bottom,

        "future_span_a":
            future_span_a,

        "future_span_b":
            future_span_b,

        "future_cloud_top":
            future_top,

        "future_cloud_bottom":
            future_bottom
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    index
):

    if index < ATR_PERIOD:
        return None

    values = []

    for i in range(
        index -
        ATR_PERIOD + 1,
        index + 1
    ):

        current = candles[i]
        previous = candles[i - 1]

        tr = max(
            current["high"] -
            current["low"],

            abs(
                current["high"] -
                previous["close"]
            ),

            abs(
                current["low"] -
                previous["close"]
            )
        )

        values.append(
            tr
        )

    if not values:
        return None

    return (
        sum(values) /
        len(values)
    )


# ============================================================
# CHIKOU
# ============================================================

def chikou_bullish(
    candles,
    index
):

    old_index = (
        index -
        DISPLACEMENT
    )

    if old_index < 0:
        return False

    return (
        candles[index]["close"]
        >
        candles[old_index]["close"]
    )


def chikou_bearish(
    candles,
    index
):

    old_index = (
        index -
        DISPLACEMENT
    )

    if old_index < 0:
        return False

    return (
        candles[index]["close"]
        <
        candles[old_index]["close"]
    )


# ============================================================
# 1H SCORE
# ============================================================

def score_1h(candles):

    i = len(candles) - 1

    info = ichimoku_at(
        candles,
        i
    )

    if info is None:
        return 0, None

    price = candles[i]["close"]

    score = 0

    # Price / Kumo
    if price > info["current_cloud_top"]:
        score += 3

    elif price < info["current_cloud_bottom"]:
        score -= 3

    # Tenkan
    if price > info["tenkan"]:
        score += 1
    else:
        score -= 1

    # Kijun
    if price > info["kijun"]:
        score += 2
    else:
        score -= 2

    # Future Kumo
    if (
        info["future_span_a"]
        >
        info["future_span_b"]
    ):
        score += 2

    elif (
        info["future_span_a"]
        <
        info["future_span_b"]
    ):
        score -= 2

    # Chikou
    if chikou_bullish(
        candles,
        i
    ):
        score += 2

    elif chikou_bearish(
        candles,
        i
    ):
        score -= 2

    score = max(
        -10,
        min(
            10,
            score
        )
    )

    return score, info


# ============================================================
# 30M SCORE
# ============================================================

def score_30m(candles):

    i = len(candles) - 1

    info = ichimoku_at(
        candles,
        i
    )

    if info is None:
        return 0, None

    price = candles[i]["close"]

    score = 0

    if price > info["current_cloud_top"]:
        score += 3

    elif price < info["current_cloud_bottom"]:
        score -= 3

    if price > info["tenkan"]:
        score += 1
    else:
        score -= 1

    if price > info["kijun"]:
        score += 2
    else:
        score -= 2

    if (
        info["future_span_a"]
        >
        info["future_span_b"]
    ):
        score += 2

    elif (
        info["future_span_a"]
        <
        info["future_span_b"]
    ):
        score -= 2

    if chikou_bullish(
        candles,
        i
    ):
        score += 2

    elif chikou_bearish(
        candles,
        i
    ):
        score -= 2

    score = max(
        -10,
        min(
            10,
            score
        )
    )

    return score, info


# ============================================================
# 15M ANALYSIS
# ============================================================

def analyze_15m(
    candles,
    direction
):

    i = len(candles) - 1

    info = ichimoku_at(
        candles,
        i
    )

    if info is None:
        return {
            "ok": False,
            "reason":
                "15m Ichimoku unavailable"
        }

    atr = calculate_atr(
        candles,
        i
    )

    if atr is None or atr <= 0:
        return {
            "ok": False,
            "reason":
                "15m ATR unavailable"
        }

    price = candles[i]["close"]

    distance_tenkan = abs(
        price -
        info["tenkan"]
    )

    distance_kijun = abs(
        price -
        info["kijun"]
    )

    nearest_distance = min(
        distance_tenkan,
        distance_kijun
    )

    distance_atr = (
        nearest_distance /
        atr
    )

    max_distance = (
        atr *
        PULLBACK_ATR_MULTIPLIER
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if price <= info[
            "current_cloud_top"
        ]:

            return {
                "ok": False,
                "reason":
                    "15m price not above Kumo"
            }

        if (
            distance_tenkan <= max_distance
            or
            distance_kijun <= max_distance
        ):

            return {
                "ok": True,
                "reason":
                    "Valid bullish pullback",
                "distance_atr":
                    distance_atr
            }

        return {
            "ok": False,
            "reason":
                f"15m pullback too far "
                f"({distance_atr:.2f} ATR)"
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if price >= info[
            "current_cloud_bottom"
        ]:

            return {
                "ok": False,
                "reason":
                    "15m price not below Kumo"
            }

        if (
            distance_tenkan <= max_distance
            or
            distance_kijun <= max_distance
        ):

            return {
                "ok": True,
                "reason":
                    "Valid bearish pullback",
                "distance_atr":
                    distance_atr
            }

        return {
            "ok": False,
            "reason":
                f"15m pullback too far "
                f"({distance_atr:.2f} ATR)"
        }

    return {
        "ok": False,
        "reason": "Unknown direction"
    }


# ============================================================
# 5M TRIGGER
# ============================================================

def analyze_5m(
    candles,
    direction
):

    if len(candles) < 3:

        return {
            "ok": False,
            "reason":
                "Not enough 5m candles"
        }

    i = len(candles) - 1
    p = i - 1

    current = ichimoku_at(
        candles,
        i
    )

    previous = ichimoku_at(
        candles,
        p
    )

    if (
        current is None
        or
        previous is None
    ):

        return {
            "ok": False,
            "reason":
                "5m Ichimoku unavailable"
        }

    close_now = candles[i]["close"]
    close_prev = candles[p]["close"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        cross_tenkan = (
            close_prev <=
            previous["tenkan"]
            and
            close_now >
            current["tenkan"]
        )

        cross_kijun = (
            close_prev <=
            previous["kijun"]
            and
            close_now >
            current["kijun"]
        )

        if not (
            cross_tenkan
            or
            cross_kijun
        ):

            return {
                "ok": False,
                "reason":
                    "No bullish 5m cross"
            }

        if close_now <= current[
            "current_cloud_top"
        ]:

            return {
                "ok": False,
                "reason":
                    "5m cross but price not above Kumo"
            }

        return {
            "ok": True,
            "reason":
                "Bullish 5m trigger"
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        cross_tenkan = (
            close_prev >=
            previous["tenkan"]
            and
            close_now <
            current["tenkan"]
        )

        cross_kijun = (
            close_prev >=
            previous["kijun"]
            and
            close_now <
            current["kijun"]
        )

        if not (
            cross_tenkan
            or
            cross_kijun
        ):

            return {
                "ok": False,
                "reason":
                    "No bearish 5m cross"
            }

        if close_now >= current[
            "current_cloud_bottom"
        ]:

            return {
                "ok": False,
                "reason":
                    "5m cross but price not below Kumo"
            }

        return {
            "ok": True,
            "reason":
                "Bearish 5m trigger"
        }

    return {
        "ok": False,
        "reason": "Unknown direction"
    }


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(
    symbol,
    data
):

    result = {
        "symbol": symbol,
        "valid": False,
        "direction": None,
        "score_1h": 0,
        "score_30m": 0,
        "reason": "",
        "stage": "START",
        "pullback": None,
        "trigger": None
    }

    # ========================================================
    # 1H
    # ========================================================

    score1h, info1h = score_1h(
        data["1h"]
    )

    result["score_1h"] = score1h

    if score1h >= MIN_1H_SCORE:

        direction = "LONG"

    elif score1h <= -MIN_1H_SCORE:

        direction = "SHORT"

    else:

        result["stage"] = "1H"

        result["reason"] = (
            f"1H score too weak "
            f"({score1h:+d})"
        )

        return result

    result["direction"] = direction

    # ========================================================
    # 30M
    # ========================================================

    score30, info30 = score_30m(
        data["30m"]
    )

    result["score_30m"] = score30

    if direction == "LONG":

        if score30 < MIN_30M_SCORE:

            result["stage"] = "30M"

            result["reason"] = (
                f"30m bullish confirmation "
                f"weak ({score30:+d})"
            )

            return result

    else:

        if score30 > -MIN_30M_SCORE:

            result["stage"] = "30M"

            result["reason"] = (
                f"30m bearish confirmation "
                f"weak ({score30:+d})"
            )

            return result

    # ========================================================
    # 15M
    # ========================================================

    pullback = analyze_15m(
        data["15m"],
        direction
    )

    result["pullback"] = pullback

    if not pullback["ok"]:

        result["stage"] = "15M"

        result["reason"] = (
            pullback["reason"]
        )

        return result

    # ========================================================
    # 5M
    # ========================================================

    trigger = analyze_5m(
        data["5m"],
        direction
    )

    result["trigger"] = trigger

    if not trigger["ok"]:

        result["stage"] = "5M"

        result["reason"] = (
            trigger["reason"]
        )

        return result

    # ========================================================
    # VALID
    # ========================================================

    result["valid"] = True

    result["stage"] = "VALID"

    result["reason"] = (
        "VALID MTF SETUP"
    )

    return result


# ============================================================
# REJECTION RANKING
# ============================================================

def rejection_quality(result):

    score = 0

    s1 = abs(
        result.get(
            "score_1h",
            0
        )
    )

    s30 = abs(
        result.get(
            "score_30m",
            0
        )
    )

    score += s1 * 10
    score += s30 * 8

    stage = result.get(
        "stage",
        ""
    )

    # Higher score if candidate reached
    # later stages.
    if stage == "5M":
        score += 50

    elif stage == "15M":
        score += 40

    elif stage == "30M":
        score += 25

    elif stage == "1H":
        score += 10

    return score


def top_rejected(
    results,
    limit=5
):

    rejected = [
        x for x in results
        if not x["valid"]
    ]

    rejected.sort(
        key=rejection_quality,
        reverse=True
    )

    return rejected[:limit]


# ============================================================
# SWING
# ============================================================

def latest_swing_low(
    candles
):

    i = len(candles) - 1

    start = max(
        2,
        i - 40
    )

    for x in range(
        i - 2,
        start - 1,
        -1
    ):

        if (
            candles[x]["low"]
            <
            candles[x - 1]["low"]
            and
            candles[x]["low"]
            <
            candles[x + 1]["low"]
            and
            candles[x]["low"]
            <=
            candles[x - 2]["low"]
            and
            candles[x]["low"]
            <=
            candles[x + 2]["low"]
        ):

            return candles[x]["low"]

    return None


def latest_swing_high(
    candles
):

    i = len(candles) - 1

    start = max(
        2,
        i - 40
    )

    for x in range(
        i - 2,
        start - 1,
        -1
    ):

        if (
            candles[x]["high"]
            >
            candles[x - 1]["high"]
            and
            candles[x]["high"]
            >
            candles[x + 1]["high"]
            and
            candles[x]["high"]
            >=
            candles[x - 2]["high"]
            and
            candles[x]["high"]
            >=
            candles[x + 2]["high"]
        ):

            return candles[x]["high"]

    return None


# ============================================================
# SIGNAL
# ============================================================

def build_signal(
    result,
    data
):

    direction = result[
        "direction"
    ]

    candles = data["5m"]

    entry = candles[-1]["close"]

    if direction == "LONG":

        stop = latest_swing_low(
            candles
        )

        if stop is None:
            return None

        if stop >= entry:
            return None

        risk = entry - stop

        target = (
            entry +
            risk * TARGET_RR
        )

    else:

        stop = latest_swing_high(
            candles
        )

        if stop is None:
            return None

        if stop <= entry:
            return None

        risk = stop - entry

        target = (
            entry -
            risk * TARGET_RR
        )

    candle_time = candles[-1]["time"]

    signal_key = (
        f"{result['symbol']}_"
        f"{direction}_"
        f"{candle_time}"
    )

    return {
        "signal_key": signal_key,
        "symbol": result["symbol"],
        "direction": direction,
        "entry": entry,
        "stop": stop,
        "target": target,
        "opened_at": now_ms(),
        "signal_candle": candle_time,
        "last_checked_candle": candle_time,
        "score_1h": result["score_1h"],
        "score_30m": result["score_30m"]
    }


# ============================================================
# PNL
# ============================================================

def pnl_percent(
    direction,
    entry,
    price
):

    if entry <= 0:
        return 0

    if direction == "LONG":

        return (
            (price - entry) /
            entry
        ) * 100

    return (
        (entry - price) /
        entry
    ) * 100


# ============================================================
# UPDATE OPEN TRADE
# ============================================================

def update_open_trade(
    state,
    history,
    all_data
):

    trade = state.get(
        "open_trade"
    )

    if not trade:
        return None

    symbol = trade["symbol"]

    if symbol not in all_data:
        return None

    candles = all_data[
        symbol
    ]["5m"]

    if not candles:
        return None

    last_checked = int(
        trade.get(
            "last_checked_candle",
            0
        )
    )

    direction = trade["direction"]

    entry = float(
        trade["entry"]
    )

    stop = float(
        trade["stop"]
    )

    target = float(
        trade["target"]
    )

    closed = None

    for candle in candles:

        t = candle["time"]

        if t <= last_checked:
            continue

        high = candle["high"]
        low = candle["low"]

        exit_reason = None
        exit_price = None

        if direction == "LONG":

            hit_sl = (
                low <= stop
            )

            hit_tp = (
                high >= target
            )

            # Conservative:
            # SL first if both happen
            # inside same candle.

            if hit_sl:

                exit_reason = "SL"
                exit_price = stop

            elif hit_tp:

                exit_reason = "TP"
                exit_price = target

        else:

            hit_sl = (
                high >= stop
            )

            hit_tp = (
                low <= target
            )

            if hit_sl:

                exit_reason = "SL"
                exit_price = stop

            elif hit_tp:

                exit_reason = "TP"
                exit_price = target

        # ====================================================
        # TIMEOUT
        # ====================================================

        if exit_reason is None:

            elapsed = (
                t -
                trade["opened_at"]
            ) / 60000

            if elapsed >= MAX_TRADE_MINUTES:

                exit_reason = "TIMEOUT"
                exit_price = candle["close"]

        trade[
            "last_checked_candle"
        ] = t

        if exit_reason:

            pnl = pnl_percent(
                direction,
                entry,
                exit_price
            )

            if exit_reason == "TP":

                r = TARGET_RR

            elif exit_reason == "SL":

                r = -1.0

            else:

                if direction == "LONG":

                    risk = (
                        entry -
                        stop
                    )

                    r = (
                        (
                            exit_price -
                            entry
                        ) /
                        risk
                        if risk > 0
                        else 0
                    )

                else:

                    risk = (
                        stop -
                        entry
                    )

                    r = (
                        (
                            entry -
                            exit_price
                        ) /
                        risk
                        if risk > 0
                        else 0
                    )

            closed = {
                **trade,
                "exit_price":
                    exit_price,
                "exit_reason":
                    exit_reason,
                "closed_at":
                    t,
                "pnl_percent":
                    pnl,
                "r_multiple":
                    r
            }

            history.append(
                closed
            )

            state[
                "open_trade"
            ] = None

            save_history(
                history
            )

            save_state(
                state
            )

            return closed

    save_state(
        state
    )

    return None


# ============================================================
# PERFORMANCE
# ============================================================

def performance(history):

    trades = len(history)

    wins = 0
    losses = 0
    be = 0

    net_pnl = 0
    net_r = 0

    win_values = []
    loss_values = []

    gross_profit = 0
    gross_loss = 0

    losing_streak = 0
    max_losing_streak = 0

    for trade in history:

        pnl = float(
            trade.get(
                "pnl_percent",
                0
            )
        )

        r = float(
            trade.get(
                "r_multiple",
                0
            )
        )

        net_pnl += pnl
        net_r += r

        if pnl > 0.0001:

            wins += 1

            win_values.append(
                pnl
            )

            gross_profit += max(
                r,
                0
            )

            losing_streak = 0

        elif pnl < -0.0001:

            losses += 1

            loss_values.append(
                pnl
            )

            gross_loss += abs(
                min(
                    r,
                    0
                )
            )

            losing_streak += 1

            max_losing_streak = max(
                max_losing_streak,
                losing_streak
            )

        else:

            be += 1

            losing_streak = 0

    win_rate = (
        wins / trades * 100
        if trades
        else 0
    )

    profit_factor = (
        gross_profit /
        gross_loss
        if gross_loss > 0
        else 0
    )

    avg_win = (
        sum(win_values) /
        len(win_values)
        if win_values
        else 0
    )

    avg_loss = (
        sum(loss_values) /
        len(loss_values)
        if loss_values
        else 0
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "be": be,
        "win_rate": win_rate,
        "net_pnl": net_pnl,
        "net_r": net_r,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "profit_factor":
            profit_factor,
        "max_losing_streak":
            max_losing_streak
    }


# ============================================================
# REPORT
# ============================================================

def build_report(
    state,
    history,
    markets,
    signal,
    rejected,
    closed_trade
):

    p = performance(
        history
    )

    lines = []

    lines.append(
        "📡 **KRAKEN FUTURES ICHIMOKU REPORT**"
    )

    lines.append(
        f"🕐 {utc_string()}"
    )

    lines.append(
        f"⏱ **5m CLOSED | TOP {TOP_N}**"
    )

    lines.append(
        "🤖 **ICHIMOKU MTF**"
    )

    lines.append(
        "1H → Trend | 30m → Confirm | "
        "15m → Pullback | 5m → Trigger"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 **PERFORMANCE**"
    )

    lines.append(
        f"Trades: {p['trades']}"
    )

    lines.append(
        f"🟢 Wins: {p['wins']}"
    )

    lines.append(
        f"🔴 Losses: {p['losses']}"
    )

    lines.append(
        f"⚪ BE: {p['be']}"
    )

    lines.append(
        f"🏆 Win Rate: "
        f"{p['win_rate']:.2f}%"
    )

    lines.append(
        f"📈 Net P&L: "
        f"{p['net_pnl']:+.2f}%"
    )

    lines.append(
        f"💰 Net R: "
        f"{p['net_r']:+.2f}R"
    )

    lines.append(
        f"📊 Avg Win: "
        f"{p['avg_win']:+.2f}%"
    )

    lines.append(
        f"📉 Avg Loss: "
        f"{p['avg_loss']:+.2f}%"
    )

    lines.append(
        f"⚖️ Profit Factor: "
        f"{p['profit_factor']:.2f}"
    )

    lines.append(
        f"🔥 Max Losing Streak: "
        f"{p['max_losing_streak']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📌 **OPEN TRADE**"
    )

    trade = state.get(
        "open_trade"
    )

    if trade:

        emoji = (
            "🟢"
            if trade["direction"] == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} **"
            f"{trade['direction']} "
            f"{trade['symbol']}**"
        )

        lines.append(
            f"Entry: `{trade['entry']:.8g}`"
        )

        lines.append(
            f"SL: `{trade['stop']:.8g}`"
        )

        lines.append(
            f"TP: `{trade['target']:.8g}`"
        )

        lines.append(
            f"1H: `{trade.get('score_1h', 0):+d}` | "
            f"30m: `{trade.get('score_30m', 0):+d}`"
        )

    else:

        lines.append(
            "None"
        )

    if closed_trade:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🏁 **TRADE CLOSED**"
        )

        emoji = (
            "🟢"
            if closed_trade["pnl_percent"] > 0
            else "🔴"
        )

        lines.append(
            f"{emoji} "
            f"{closed_trade['symbol']} "
            f"{closed_trade['exit_reason']}"
        )

        lines.append(
            f"P&L: "
            f"{closed_trade['pnl_percent']:+.2f}%"
        )

        lines.append(
            f"R: "
            f"{closed_trade['r_multiple']:+.2f}R"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # SIGNAL
    # ========================================================

    if signal:

        lines.append(
            "🚨 **NEW SIGNAL**"
        )

        emoji = (
            "🟢"
            if signal["direction"] == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} **"
            f"{signal['direction']} "
            f"{signal['symbol']}**"
        )

        lines.append(
            f"Entry: `{signal['entry']:.8g}`"
        )

        lines.append(
            f"SL: `{signal['stop']:.8g}`"
        )

        lines.append(
            f"TP: `{signal['target']:.8g}`"
        )

        lines.append(
            f"RR: `1:{TARGET_RR:.1f}`"
        )

        lines.append(
            f"1H Score: "
            f"`{signal['score_1h']:+d}`"
        )

        lines.append(
            f"30m Score: "
            f"`{signal['score_30m']:+d}`"
        )

    else:

        lines.append(
            "🔎 **NEW SIGNAL**"
        )

        lines.append(
            "No valid MTF setup."
        )

    # ========================================================
    # TOP 5 REJECTED
    # ========================================================

    if rejected:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🧪 **TOP 5 NEAR-SIGNAL CANDIDATES**"
        )

        for n, item in enumerate(
            rejected,
            1
        ):

            direction = (
                item.get(
                    "direction"
                )
                or "-"
            )

            lines.append(
                f"{n}. `{item['symbol']}` "
                f"{direction} | "
                f"1H `{item['score_1h']:+d}` | "
                f"30m `{item['score_30m']:+d}`"
            )

            lines.append(
                f"   ❌ "
                f"{item['stage']}: "
                f"{item['reason']}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📡 Scanned: "
        f"{len(markets)}"
    )

    lines.append(
        f"🎯 New Signal: "
        f"{1 if signal else 0}"
    )

    lines.append(
        f"🔒 Max Open: "
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(
        "⚠️ Virtual signal simulator only"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "KRAKEN FUTURES ICHIMOKU MTF SCANNER v2"
    )
    print("=" * 70)

    print(
        "Time:",
        utc_string()
    )

    state = load_state()
    history = load_history()

    # ========================================================
    # MARKETS
    # ========================================================

    try:

        markets = get_top_markets()

    except Exception as e:

        print(
            "[FATAL MARKET ERROR]",
            e
        )

        telegram_send(
            "❌ **ICHIMOKU SCANNER ERROR**\n"
            f"`{str(e)[:600]}`"
        )

        return

    print(
        f"[INFO] Markets: "
        f"{len(markets)}"
    )

    if not markets:

        print(
            "[ERROR] No markets"
        )

        return

    # ========================================================
    # FETCH
    # ========================================================

    all_data = {}

    print(
        "[INFO] Fetching candles..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_market_data,
                market
            ):
            market["symbol"]

            for market in markets
        }

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                data = future.result()

                if data:

                    all_data[
                        symbol
                    ] = data

            except Exception as e:

                print(
                    f"[ERROR] "
                    f"{symbol}: {e}"
                )

    print(
        f"[INFO] Valid markets: "
        f"{len(all_data)}"
    )

    # ========================================================
    # OPEN TRADE
    # ========================================================

    closed_trade = (
        update_open_trade(
            state,
            history,
            all_data
        )
    )

    # ========================================================
    # ANALYSIS
    # ========================================================

    results = []

    for symbol, data in all_data.items():

        try:

            result = analyze_market(
                symbol,
                data
            )

            results.append(
                result
            )

        except Exception as e:

            results.append({
                "symbol": symbol,
                "valid": False,
                "direction": None,
                "score_1h": 0,
                "score_30m": 0,
                "stage": "ERROR",
                "reason":
                    str(e)[:150]
            })

    # ========================================================
    # SIGNAL
    # ========================================================

    signal = None

    # Only scan for new trade if no open trade
    if state.get(
        "open_trade"
    ) is None:

        valid = [
            x for x in results
            if x["valid"]
        ]

        # Strongest setup first
        valid.sort(
            key=lambda x: (
                abs(
                    x["score_1h"]
                ),
                abs(
                    x["score_30m"]
                )
            ),
            reverse=True
        )

        if valid:

            best = valid[0]

            signal = build_signal(
                best,
                all_data[
                    best["symbol"]
                ]
            )

            if signal:

                if (
                    signal["signal_key"]
                    !=
                    state.get(
                        "last_signal_key",
                        ""
                    )
                ):

                    state[
                        "last_signal_key"
                    ] = signal[
                        "signal_key"
                    ]

                    state[
                        "open_trade"
                    ] = signal

                    save_state(
                        state
                    )

                    print(
                        "[NEW SIGNAL]",
                        signal
                    )

                else:

                    print(
                        "[INFO] "
                        "Duplicate signal ignored"
                    )

                    signal = None

    else:

        print(
            "[INFO] Open trade exists. "
            "New entry locked."
        )

    # ========================================================
    # REJECTED
    # ========================================================

    rejected = top_rejected(
        results,
        5
    )

    # If signal exists, rejected candidates
    # are still useful, but report focuses on signal.
    if signal:

        rejected = []

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        state,
        history,
        markets,
        signal,
        rejected,
        closed_trade
    )

    print()
    print(report)
    print()

    telegram_send(
        report
    )

    state[
        "last_scan"
    ] = utc_string()

    save_state(
        state
    )


# ============================================================
# RUN
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
            "[FATAL ERROR]",
            repr(e)
        )

        try:

            telegram_send(
                "❌ **ICHIMOKU FATAL ERROR**\n"
                f"`{str(e)[:700]}`"
            )

        except Exception:
            pass
