# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER
# ============================================================
# 1H  -> Trend
# 30m -> Confirmation
# 15m -> Pullback
# 5m  -> Trigger
#
# Kraken Futures
# TOP 100 by volume
# CLOSED CANDLES ONLY
# MAX OPEN TRADES = 1
# VIRTUAL SIGNAL SIMULATOR
#
# IMPORTANT:
# Current Kumo = Ichimoku spans from 26 candles ago
# Future Kumo  = Ichimoku spans calculated from current candle
# ============================================================

import os
import json
import time
import math
import threading
from datetime import datetime, timezone

import requests
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

SCAN_TIMEFRAME = "5m"

TIMEFRAMES = {
    "5m": 5,
    "15m": 15,
    "30m": 30,
    "1h": 60,
}

# Ichimoku
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26

# Pullback
PULLBACK_ATR_MULTIPLIER = 1.5

# Signal
MIN_1H_SCORE = 6
MIN_30M_SCORE = 5

# RR
TARGET_RR = 1.0

# ATR
ATR_PERIOD = 14

# Signal age
MAX_SIGNAL_AGE_MINUTES = 5

# HTTP
REQUEST_TIMEOUT = 20
MAX_WORKERS = 12

# ============================================================
# LOCKS
# ============================================================

STATE_LOCK = threading.Lock()

_thread_local = threading.local()


# ============================================================
# HTTP SESSION
# ============================================================

def get_session():
    if not hasattr(_thread_local, "session"):
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Kraken-Ichi-MTF-Scanner/1.0"
        })
        _thread_local.session = session

    return _thread_local.session


def http_get(path, params=None):
    url = BASE_URL + path

    session = get_session()

    response = session.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    return data


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_string(dt=None):
    if dt is None:
        dt = utc_now()

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def now_ms():
    return int(time.time() * 1000)


# ============================================================
# JSON STATE
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(filename, data):
    temp = filename + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp, filename)


def default_state():
    return {
        "open_trade": None,
        "last_signal_key": "",
        "last_scan": "",
    }


def load_state():
    state = load_json(STATE_FILE, default_state())

    if not isinstance(state, dict):
        state = default_state()

    if "open_trade" not in state:
        state["open_trade"] = None

    if "last_signal_key" not in state:
        state["last_signal_key"] = ""

    return state


def save_state(state):
    with STATE_LOCK:
        save_json(STATE_FILE, state)


# ============================================================
# TRADE HISTORY
# ============================================================

def load_history():
    data = load_json(HISTORY_FILE, [])

    if not isinstance(data, list):
        return []

    return data


def save_history(history):
    save_json(HISTORY_FILE, history)


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(text)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=20
        )

        response.raise_for_status()

        return True

    except Exception as e:
        print("[TELEGRAM ERROR]", e)
        return False


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_instruments():
    data = http_get("/derivatives/api/v3/instruments")

    instruments = data.get("instruments", [])

    result = []

    for x in instruments:

        symbol = x.get("symbol", "")

        if not symbol:
            continue

        if not symbol.startswith("PF_"):
            continue

        if "USD" not in symbol:
            continue

        status = str(
            x.get("status", "online")
        ).lower()

        if status not in ("online", "trading", "open"):
            continue

        result.append(x)

    return result


# ============================================================
# TICKERS
# ============================================================

def get_tickers():
    data = http_get("/derivatives/api/v3/tickers")

    tickers = data.get("tickers", [])

    result = {}

    for x in tickers:

        symbol = x.get("symbol")

        if not symbol:
            continue

        try:
            last = float(x.get("last", 0) or 0)
        except Exception:
            last = 0

        try:
            volume = float(
                x.get("vol24h", 0) or
                x.get("volume24h", 0) or
                0
            )
        except Exception:
            volume = 0

        if last <= 0:
            continue

        result[symbol] = {
            "last": last,
            "volume": volume,
        }

    return result


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    markets = []

    for inst in instruments:

        symbol = inst.get("symbol")

        if symbol not in tickers:
            continue

        ticker = tickers[symbol]

        markets.append({
            "symbol": symbol,
            "last": ticker["last"],
            "volume": ticker["volume"],
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N], tickers


# ============================================================
# CANDLE FETCH
# ============================================================

def fetch_candles(symbol, timeframe):

    minutes = TIMEFRAMES[timeframe]

    path = f"/api/charts/v1/trade/{symbol}/{minutes}"

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
            f"[CANDLE ERROR] {symbol} {timeframe}: {e}"
        )
        return []

    candles = []

    # Kraken response may use candles
    raw = data.get("candles", [])

    if not raw:
        raw = data.get("data", [])

    for c in raw:

        try:

            if isinstance(c, dict):

                timestamp = (
                    c.get("time") or
                    c.get("timestamp") or
                    c.get("ts")
                )

                open_price = (
                    c.get("open") or
                    c.get("o")
                )

                high = (
                    c.get("high") or
                    c.get("h")
                )

                low = (
                    c.get("low") or
                    c.get("l")
                )

                close = (
                    c.get("close") or
                    c.get("c")
                )

                volume = (
                    c.get("volume") or
                    c.get("v") or
                    0
                )

            else:

                if len(c) < 5:
                    continue

                timestamp = c[0]
                open_price = c[1]
                high = c[2]
                low = c[3]
                close = c[4]

                volume = c[5] if len(c) > 5 else 0

            timestamp = int(float(timestamp))

            # Normalize seconds -> milliseconds
            if timestamp < 10_000_000_000:
                timestamp *= 1000

            candles.append({
                "time": timestamp,
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": float(volume or 0),
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


# ============================================================
# CLOSED CANDLES
# ============================================================

def filter_closed_candles(candles, timeframe_minutes):

    current = now_ms()

    timeframe_ms = timeframe_minutes * 60 * 1000

    result = []

    for c in candles:

        close_time = c["time"] + timeframe_ms

        if close_time <= current:
            result.append(c)

    return result[-CANDLE_LIMIT:]


# ============================================================
# BASIC INDICATORS
# ============================================================

def highest(candles, start, end):
    values = [
        candles[i]["high"]
        for i in range(start, end + 1)
    ]

    return max(values)


def lowest(candles, start, end):
    values = [
        candles[i]["low"]
        for i in range(start, end + 1)
    ]

    return min(values)


def ichimoku_values(candles, index):

    if index < SENKOU_B_PERIOD - 1:
        return None

    tenkan_high = highest(
        candles,
        index - TENKAN_PERIOD + 1,
        index
    )

    tenkan_low = lowest(
        candles,
        index - TENKAN_PERIOD + 1,
        index
    )

    tenkan = (
        tenkan_high +
        tenkan_low
    ) / 2

    kijun_high = highest(
        candles,
        index - KIJUN_PERIOD + 1,
        index
    )

    kijun_low = lowest(
        candles,
        index - KIJUN_PERIOD + 1,
        index
    )

    kijun = (
        kijun_high +
        kijun_low
    ) / 2

    span_a = (
        tenkan +
        kijun
    ) / 2

    span_b_high = highest(
        candles,
        index - SENKOU_B_PERIOD + 1,
        index
    )

    span_b_low = lowest(
        candles,
        index - SENKOU_B_PERIOD + 1,
        index
    )

    span_b = (
        span_b_high +
        span_b_low
    ) / 2

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
    }


def ichimoku_at(candles, index):

    current = ichimoku_values(
        candles,
        index
    )

    if current is None:
        return None

    # ========================================================
    # REAL CURRENT KUMO
    #
    # Current cloud comes from spans calculated 26 candles
    # ago and projected to the current candle.
    # ========================================================

    cloud_index = index - DISPLACEMENT

    if cloud_index < SENKOU_B_PERIOD - 1:
        return None

    current_cloud = ichimoku_values(
        candles,
        cloud_index
    )

    if current_cloud is None:
        return None

    current_span_a = current_cloud["span_a"]
    current_span_b = current_cloud["span_b"]

    current_cloud_top = max(
        current_span_a,
        current_span_b
    )

    current_cloud_bottom = min(
        current_span_a,
        current_span_b
    )

    # ========================================================
    # REAL FUTURE KUMO
    #
    # Future cloud uses spans calculated NOW.
    # Those values are projected 26 candles forward.
    # ========================================================

    future_span_a = current["span_a"]
    future_span_b = current["span_b"]

    future_cloud_top = max(
        future_span_a,
        future_span_b
    )

    future_cloud_bottom = min(
        future_span_a,
        future_span_b
    )

    return {
        "tenkan": current["tenkan"],
        "kijun": current["kijun"],

        "current_span_a": current_span_a,
        "current_span_b": current_span_b,

        "current_cloud_top": current_cloud_top,
        "current_cloud_bottom": current_cloud_bottom,

        "future_span_a": future_span_a,
        "future_span_b": future_span_b,

        "future_cloud_top": future_cloud_top,
        "future_cloud_bottom": future_cloud_bottom,
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, index, period=ATR_PERIOD):

    if index < period:
        return None

    trs = []

    for i in range(
        index - period + 1,
        index + 1
    ):

        current = candles[i]

        previous_close = candles[i - 1]["close"]

        tr = max(
            current["high"] - current["low"],
            abs(
                current["high"] -
                previous_close
            ),
            abs(
                current["low"] -
                previous_close
            )
        )

        trs.append(tr)

    if not trs:
        return None

    return sum(trs) / len(trs)


# ============================================================
# CHIKOU
# ============================================================

def chikou_bullish(candles, index):

    past_index = index - DISPLACEMENT

    if past_index < 0:
        return False

    return (
        candles[index]["close"] >
        candles[past_index]["close"]
    )


def chikou_bearish(candles, index):

    past_index = index - DISPLACEMENT

    if past_index < 0:
        return False

    return (
        candles[index]["close"] <
        candles[past_index]["close"]
    )


# ============================================================
# KUMO
# ============================================================

def price_above_current_kumo(info, price):
    return price > info["current_cloud_top"]


def price_below_current_kumo(info, price):
    return price < info["current_cloud_bottom"]


def future_kumo_bullish(info):
    return (
        info["future_span_a"] >
        info["future_span_b"]
    )


def future_kumo_bearish(info):
    return (
        info["future_span_a"] <
        info["future_span_b"]
    )


# ============================================================
# TREND SCORE
# ============================================================

def score_1h(candles):

    i = len(candles) - 1

    info = ichimoku_at(
        candles,
        i
    )

    if info is None:
        return 0, {}

    price = candles[i]["close"]

    score = 0

    # Price above/below current Kumo
    if price > info["current_cloud_top"]:
        score += 3

    elif price < info["current_cloud_bottom"]:
        score -= 3

    # Tenkan / Kijun
    if price > info["tenkan"]:
        score += 1

    else:
        score -= 1

    if price > info["kijun"]:
        score += 2

    else:
        score -= 2

    # Future Kumo
    if future_kumo_bullish(info):
        score += 2

    elif future_kumo_bearish(info):
        score -= 2

    # Chikou
    if chikou_bullish(candles, i):
        score += 2

    elif chikou_bearish(candles, i):
        score -= 2

    score = max(-10, min(10, score))

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
        return 0, {}

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

    if future_kumo_bullish(info):
        score += 2

    elif future_kumo_bearish(info):
        score -= 2

    if chikou_bullish(candles, i):
        score += 2

    elif chikou_bearish(candles, i):
        score -= 2

    score = max(-10, min(10, score))

    return score, info


# ============================================================
# 15M PULLBACK
# ============================================================

def check_15m_pullback(candles, direction):

    i = len(candles) - 1

    info = ichimoku_at(
        candles,
        i
    )

    if info is None:
        return False, info, "Ichimoku data unavailable"

    atr = calculate_atr(
        candles,
        i
    )

    if atr is None or atr <= 0:
        return False, info, "ATR unavailable"

    price = candles[i]["close"]

    if direction == "LONG":

        if price <= info["current_cloud_top"]:
            return (
                False,
                info,
                "15m price is not above current Kumo"
            )

        dist_tenkan = abs(
            price - info["tenkan"]
        )

        dist_kijun = abs(
            price - info["kijun"]
        )

        max_distance = (
            atr *
            PULLBACK_ATR_MULTIPLIER
        )

        if (
            dist_tenkan <= max_distance or
            dist_kijun <= max_distance
        ):
            return True, info, "Valid bullish pullback"

        return (
            False,
            info,
            (
                "15m price is "
                f"{min(dist_tenkan, dist_kijun) / atr:.2f} ATR "
                "from Tenkan/Kijun"
            )
        )

    if direction == "SHORT":

        if price >= info["current_cloud_bottom"]:
            return (
                False,
                info,
                "15m price is not below current Kumo"
            )

        dist_tenkan = abs(
            price - info["tenkan"]
        )

        dist_kijun = abs(
            price - info["kijun"]
        )

        max_distance = (
            atr *
            PULLBACK_ATR_MULTIPLIER
        )

        if (
            dist_tenkan <= max_distance or
            dist_kijun <= max_distance
        ):
            return True, info, "Valid bearish pullback"

        return (
            False,
            info,
            (
                "15m price is "
                f"{min(dist_tenkan, dist_kijun) / atr:.2f} ATR "
                "from Tenkan/Kijun"
            )
        )

    return False, info, "Unknown direction"


# ============================================================
# 5M TRIGGER
# ============================================================

def check_5m_trigger(candles, direction):

    if len(candles) < 3:
        return False, "Not enough 5m candles"

    i = len(candles) - 1
    prev = i - 1

    current_info = ichimoku_at(
        candles,
        i
    )

    previous_info = ichimoku_at(
        candles,
        prev
    )

    if current_info is None or previous_info is None:
        return False, "5m Ichimoku unavailable"

    current_close = candles[i]["close"]
    previous_close = candles[prev]["close"]

    if direction == "LONG":

        crossed_tenkan = (
            previous_close <=
            previous_info["tenkan"]
            and
            current_close >
            current_info["tenkan"]
        )

        crossed_kijun = (
            previous_close <=
            previous_info["kijun"]
            and
            current_close >
            current_info["kijun"]
        )

        if crossed_tenkan or crossed_kijun:

            if current_close > current_info["current_cloud_top"]:
                return True, "Bullish 5m cross"

            return (
                False,
                "5m cross occurred but price is inside/below Kumo"
            )

        return False, "No bullish 5m cross"

    if direction == "SHORT":

        crossed_tenkan = (
            previous_close >=
            previous_info["tenkan"]
            and
            current_close <
            current_info["tenkan"]
        )

        crossed_kijun = (
            previous_close >=
            previous_info["kijun"]
            and
            current_close <
            current_info["kijun"]
        )

        if crossed_tenkan or crossed_kijun:

            if current_close < current_info["current_cloud_bottom"]:
                return True, "Bearish 5m cross"

            return (
                False,
                "5m cross occurred but price is inside/above Kumo"
            )

        return False, "No bearish 5m cross"

    return False, "Unknown direction"


# ============================================================
# STRUCTURAL STOP
# ============================================================

def latest_swing_low(candles):

    i = len(candles) - 1

    start = max(
        2,
        i - 30
    )

    for x in range(i - 2, start - 1, -1):

        if (
            candles[x]["low"] <
            candles[x - 1]["low"]
            and
            candles[x]["low"] <
            candles[x + 1]["low"]
            and
            candles[x]["low"] <=
            candles[x - 2]["low"]
            and
            candles[x]["low"] <=
            candles[x + 2]["low"]
        ):
            return candles[x]["low"]

    return None


def latest_swing_high(candles):

    i = len(candles) - 1

    start = max(
        2,
        i - 30
    )

    for x in range(i - 2, start - 1, -1):

        if (
            candles[x]["high"] >
            candles[x - 1]["high"]
            and
            candles[x]["high"] >
            candles[x + 1]["high"]
            and
            candles[x]["high"] >=
            candles[x - 2]["high"]
            and
            candles[x]["high"] >=
            candles[x + 2]["high"]
        ):
            return candles[x]["high"]

    return None


# ============================================================
# SIGNAL ANALYSIS
# ============================================================

def analyze_market(symbol, candles):

    result = {
        "symbol": symbol,
        "direction": None,
        "valid": False,
        "score_1h": 0,
        "score_30m": 0,
        "reason": "",
        "price": 0,
        "info_1h": None,
        "info_30m": None,
        "info_15m": None,
    }

    if any(
        len(candles.get(tf, [])) < 80
        for tf in ["5m", "15m", "30m", "1h"]
    ):
        result["reason"] = "Insufficient candle history"
        return result

    price = candles["5m"][-1]["close"]

    result["price"] = price

    # ========================================================
    # 1H
    # ========================================================

    score1h, info1h = score_1h(
        candles["1h"]
    )

    result["score_1h"] = score1h
    result["info_1h"] = info1h

    # ========================================================
    # DIRECTION
    # ========================================================

    if score1h >= MIN_1H_SCORE:
        direction = "LONG"

    elif score1h <= -MIN_1H_SCORE:
        direction = "SHORT"

    else:
        result["reason"] = (
            f"1H trend score {score1h} is too weak"
        )
        return result

    result["direction"] = direction

    # ========================================================
    # 30M
    # ========================================================

    score30, info30 = score_30m(
        candles["30m"]
    )

    result["score_30m"] = score30
    result["info_30m"] = info30

    if direction == "LONG":

        if score30 < MIN_30M_SCORE:
            result["reason"] = (
                f"30m bullish confirmation weak ({score30})"
            )
            return result

    else:

        if score30 > -MIN_30M_SCORE:
            result["reason"] = (
                f"30m bearish confirmation weak ({score30})"
            )
            return result

    # ========================================================
    # 15M PULLBACK
    # ========================================================

    pullback_ok, info15, pullback_reason = (
        check_15m_pullback(
            candles["15m"],
            direction
        )
    )

    result["info_15m"] = info15

    if not pullback_ok:
        result["reason"] = pullback_reason
        return result

    # ========================================================
    # 5M TRIGGER
    # ========================================================

    trigger_ok, trigger_reason = (
        check_5m_trigger(
            candles["5m"],
            direction
        )
    )

    if not trigger_ok:
        result["reason"] = trigger_reason
        return result

    # ========================================================
    # FINAL
    # ========================================================

    result["valid"] = True
    result["reason"] = "VALID MTF SETUP"

    return result


# ============================================================
# BEST REJECTED CANDIDATE
# ============================================================

def candidate_quality(result):

    score = 0

    score += abs(
        result.get("score_1h", 0)
    ) * 3

    score += abs(
        result.get("score_30m", 0)
    ) * 2

    if result.get("direction"):
        score += 1

    return score


def find_best_rejected(results):

    rejected = [
        x for x in results
        if not x.get("valid")
    ]

    if not rejected:
        return None

    rejected.sort(
        key=candidate_quality,
        reverse=True
    )

    return rejected[0]


# ============================================================
# SIGNAL CREATION
# ============================================================

def build_signal(result, candles):

    direction = result["direction"]

    c5 = candles["5m"]

    entry = c5[-1]["close"]

    if direction == "LONG":

        swing = latest_swing_low(c5)

        if swing is None:
            return None

        stop = swing

        if stop >= entry:
            return None

        risk = entry - stop

        target = (
            entry +
            risk * TARGET_RR
        )

    else:

        swing = latest_swing_high(c5)

        if swing is None:
            return None

        stop = swing

        if stop <= entry:
            return None

        risk = stop - entry

        target = (
            entry -
            risk * TARGET_RR
        )

    candle_time = c5[-1]["time"]

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
        "score_1h": result["score_1h"],
        "score_30m": result["score_30m"],
    }


# ============================================================
# TRADE PERCENT
# ============================================================

def pnl_percent(direction, entry, price):

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
# TRADE MANAGEMENT
# ============================================================

def update_open_trade(
    state,
    history,
    candles_by_symbol
):

    trade = state.get("open_trade")

    if not trade:
        return None

    symbol = trade["symbol"]

    if symbol not in candles_by_symbol:
        return None

    candles = candles_by_symbol[symbol]["5m"]

    if not candles:
        return None

    direction = trade["direction"]

    entry = trade["entry"]
    stop = trade["stop"]
    target = trade["target"]

    last_checked = int(
        trade.get("last_checked_candle", 0)
    )

    closed_trade = None

    for candle in candles:

        candle_time = candle["time"]

        if candle_time <= last_checked:
            continue

        high = candle["high"]
        low = candle["low"]

        exit_reason = None
        exit_price = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            hit_sl = low <= stop
            hit_tp = high >= target

            # Conservative rule:
            # If both occur in same candle, SL wins.
            if hit_sl:
                exit_reason = "SL"
                exit_price = stop

            elif hit_tp:
                exit_reason = "TP"
                exit_price = target

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            hit_sl = high >= stop
            hit_tp = low <= target

            if hit_sl:
                exit_reason = "SL"
                exit_price = stop

            elif hit_tp:
                exit_reason = "TP"
                exit_price = target

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        if exit_reason is None:

            elapsed = (
                candle_time -
                trade["opened_at"]
            ) / 60000

            if elapsed >= 240:
                exit_reason = "TIMEOUT"
                exit_price = candle["close"]

        trade["last_checked_candle"] = candle_time

        if exit_reason:

            pnl = pnl_percent(
                direction,
                entry,
                exit_price
            )

            if exit_reason == "TP":
                r_multiple = TARGET_RR

            elif exit_reason == "SL":
                r_multiple = -1.0

            else:

                if direction == "LONG":
                    risk = entry - stop

                    if risk > 0:
                        r_multiple = (
                            exit_price - entry
                        ) / risk
                    else:
                        r_multiple = 0

                else:
                    risk = stop - entry

                    if risk > 0:
                        r_multiple = (
                            entry - exit_price
                        ) / risk
                    else:
                        r_multiple = 0

            closed_trade = {
                **trade,
                "exit_price": exit_price,
                "exit_reason": exit_reason,
                "closed_at": candle_time,
                "pnl_percent": pnl,
                "r_multiple": r_multiple,
            }

            history.append(
                closed_trade
            )

            state["open_trade"] = None

            save_history(history)
            save_state(state)

            return closed_trade

    save_state(state)

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

    gross_profit_r = 0
    gross_loss_r = 0

    current_losing_streak = 0
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
            win_values.append(pnl)

            gross_profit_r += max(r, 0)

            current_losing_streak = 0

        elif pnl < -0.0001:

            losses += 1
            loss_values.append(pnl)

            gross_loss_r += abs(
                min(r, 0)
            )

            current_losing_streak += 1

            max_losing_streak = max(
                max_losing_streak,
                current_losing_streak
            )

        else:

            be += 1
            current_losing_streak = 0

    if trades:
        win_rate = (
            wins /
            trades
        ) * 100

    else:
        win_rate = 0

    if gross_loss_r > 0:
        profit_factor = (
            gross_profit_r /
            gross_loss_r
        )

    else:
        profit_factor = 0

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
        "profit_factor": profit_factor,
        "max_losing_streak": max_losing_streak,
    }


# ============================================================
# REPORT
# ============================================================

def build_report(
    state,
    history,
    markets,
    signal,
    best_rejected,
    closed_trade
):

    perf = performance(history)

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

    lines.append("📊 **PERFORMANCE**")

    lines.append(
        f"Trades: {perf['trades']}"
    )

    lines.append(
        f"🟢 Wins: {perf['wins']}"
    )

    lines.append(
        f"🔴 Losses: {perf['losses']}"
    )

    lines.append(
        f"⚪ BE: {perf['be']}"
    )

    lines.append(
        f"🏆 Win Rate: {perf['win_rate']:.2f}%"
    )

    lines.append(
        f"📈 Net P&L: "
        f"{perf['net_pnl']:+.2f}%"
    )

    lines.append(
        f"💰 Net R: "
        f"{perf['net_r']:+.2f}R"
    )

    lines.append(
        f"📊 Avg Win: "
        f"{perf['avg_win']:+.2f}%"
    )

    lines.append(
        f"📉 Avg Loss: "
        f"{perf['avg_loss']:+.2f}%"
    )

    lines.append(
        f"⚖️ Profit Factor: "
        f"{perf['profit_factor']:.2f}"
    )

    lines.append(
        f"🔥 Max Losing Streak: "
        f"{perf['max_losing_streak']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append("📌 **OPEN TRADE**")

    trade = state.get("open_trade")

    if trade:

        lines.append(
            f"{'🟢' if trade['direction'] == 'LONG' else '🔴'} "
            f"**{trade['direction']} {trade['symbol']}**"
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

        lines.append("None")

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
            f"{emoji} **{signal['direction']} "
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
            f"1H Score: `{signal['score_1h']:+d}`"
        )

        lines.append(
            f"30m Score: `{signal['score_30m']:+d}`"
        )

    else:

        lines.append(
            "🔎 **NEW SIGNAL**"
        )

        lines.append(
            "No valid MTF setup."
        )

    # ========================================================
    # BEST REJECTED
    # ========================================================

    if best_rejected:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🧪 **BEST REJECTED CANDIDATE**"
        )

        direction = (
            best_rejected.get("direction")
            or "UNDEFINED"
        )

        lines.append(
            f"Symbol: `{best_rejected['symbol']}`"
        )

        lines.append(
            f"Direction: `{direction}`"
        )

        lines.append(
            f"1H Score: "
            f"`{best_rejected.get('score_1h', 0):+d}`"
        )

        lines.append(
            f"30m Score: "
            f"`{best_rejected.get('score_30m', 0):+d}`"
        )

        lines.append(
            f"❌ Reason: "
            f"{best_rejected.get('reason', 'Unknown')}"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📡 Scanned: {len(markets)}"
    )

    lines.append(
        f"🎯 New Signal: "
        f"{1 if signal else 0}"
    )

    lines.append(
        f"🔒 Max Open: {MAX_OPEN_TRADES}"
    )

    lines.append(
        "⚠️ Virtual signal simulator only"
    )

    return "\n".join(lines)


# ============================================================
# MARKET DATA COLLECTION
# ============================================================

def fetch_market_data(market):

    symbol = market["symbol"]

    result = {
        "symbol": symbol,
        "5m": [],
        "15m": [],
        "30m": [],
        "1h": [],
    }

    for tf in TIMEFRAMES:

        candles = fetch_candles(
            symbol,
            tf
        )

        result[tf] = candles

        if not candles:
            return None

    return result


# ============================================================
# MAIN SCAN
# ============================================================

def main():

    print("=" * 70)
    print("KRAKEN FUTURES ICHIMOKU MTF SCANNER")
    print("=" * 70)
    print(
        f"Time: {utc_string()}"
    )

    state = load_state()
    history = load_history()

    # ========================================================
    # TOP MARKETS
    # ========================================================

    try:

        markets, tickers = get_top_markets()

    except Exception as e:

        print(
            "[FATAL] Market loading failed:",
            e
        )

        telegram_send(
            "❌ **ICHIMOKU SCANNER ERROR**\n"
            f"`{str(e)[:500]}`"
        )

        return

    print(
        f"[INFO] Markets: {len(markets)}"
    )

    if not markets:
        print(
            "[ERROR] No markets."
        )
        return

    # ========================================================
    # FETCH DATA
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
            ): market["symbol"]
            for market in markets
        }

        for future in as_completed(futures):

            symbol = futures[future]

            try:

                data = future.result()

                if data:
                    all_data[symbol] = data

            except Exception as e:

                print(
                    f"[ERROR] {symbol}: {e}"
                )

    print(
        f"[INFO] Valid markets: "
        f"{len(all_data)}"
    )

    # ========================================================
    # UPDATE EXISTING TRADE
    # ========================================================

    closed_trade = update_open_trade(
        state,
        history,
        all_data
    )

    # ========================================================
    # ONE TRADE LOCK
    # ========================================================

    signal = None

    if state.get("open_trade") is None:

        print(
            "[INFO] No open trade. "
            "Scanning signals..."
        )

        results = []

        for symbol, data in all_data.items():

            result = analyze_market(
                symbol,
                data
            )

            results.append(result)

        valid_results = [
            x for x in results
            if x.get("valid")
        ]

        if valid_results:

            # strongest first
            valid_results.sort(
                key=lambda x: (
                    abs(x["score_1h"]),
                    abs(x["score_30m"])
                ),
                reverse=True
            )

            best = valid_results[0]

            signal = build_signal(
                best,
                all_data[best["symbol"]]
            )

            if signal:

                signal_key = signal[
                    "signal_key"
                ]

                if (
                    signal_key ==
                    state.get("last_signal_key")
                ):

                    print(
                        "[INFO] Duplicate signal ignored."
                    )

                    signal = None

                else:

                    state["last_signal_key"] = (
                        signal_key
                    )

                    state["open_trade"] = {
                        **signal,
                        "last_checked_candle":
                            signal["signal_candle"],
                    }

                    save_state(state)

                    print(
                        "[SIGNAL]",
                        signal
                    )

        else:

            best_rejected = (
                find_best_rejected(
                    results
                )
            )

    else:

        print(
            "[INFO] Open trade exists. "
            "Signal scan locked."
        )

        results = []

        for symbol, data in all_data.items():

            result = analyze_market(
                symbol,
                data
            )

            results.append(result)

        best_rejected = (
            find_best_rejected(
                results
            )
        )

    # ========================================================
    # IF VALID SIGNAL WAS FOUND
    # ========================================================

    if signal:

        best_rejected = None

    # ========================================================
    # REPORT
    # ========================================================

    report = build_report(
        state,
        history,
        markets,
        signal,
        best_rejected,
        closed_trade
    )

    print()
    print(report)
    print()

    telegram_send(report)

    state["last_scan"] = utc_string()

    save_state(state)


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
            "[FATAL ERROR]",
            repr(e)
        )

        try:
            telegram_send(
                "❌ **ICHIMOKU SCANNER FATAL ERROR**\n"
                f"`{str(e)[:700]}`"
            )
        except Exception:
            pass
