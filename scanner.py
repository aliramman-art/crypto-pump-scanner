# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v4.0
# ============================================================
# 1H  = TREND
# 30M = CONFIRM
# 15M = PULLBACK
# 5M  = TRIGGER
#
# TOP 100 KRAKEN FUTURES
# CLOSED CANDLES ONLY
# MAX OPEN TRADE = 1
# RR = 1:1
# VIRTUAL SIGNAL SIMULATOR
#
# IMPORTANT:
# - Robust Kraken candle parser
# - Flexible candle minimum
# - Full diagnostic report
# - TOP 5 candidates
# - Never hides rejection reason
# ============================================================

import os
import json
import time
import requests
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

INSTRUMENTS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    "https://futures.kraken.com/derivatives/api/v3/tickers"
)

CHART_URL = (
    "https://futures.kraken.com/api/charts/v1/trade/"
    "{symbol}/{minutes}"
)

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TOP_N = 100
MAX_OPEN_TRADES = 1

RR = 1.0
TIMEOUT_MINUTES = 240

ATR_PERIOD = 14

MIN_1H_LONG = 6
MIN_1H_SHORT = -6

MIN_30M_LONG = 5
MIN_30M_SHORT = -5

PULLBACK_ATR_MAX = 1.5

MAX_WORKERS = 10

# Minimum required candles.
# Ichimoku itself needs 52+ candles.
MIN_CANDLES = 60


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json"
})


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

    except Exception:

        return default


def save_json(path, data):

    tmp = path + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        tmp,
        path
    )


state = load_json(
    STATE_FILE,
    {
        "open_trade": None,
        "last_signal": None
    }
)

history = load_json(
    HISTORY_FILE,
    []
)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            "[INFO] Telegram variables not configured."
        )

        return

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=15
        )

        if not response.ok:

            print(
                "[TELEGRAM ERROR]",
                response.text
            )

    except Exception as e:

        print(
            "[TELEGRAM ERROR]",
            e
        )


# ============================================================
# HELPERS
# ============================================================

def safe_float(value):

    try:
        return float(value)
    except Exception:
        return None


def utc_now_ts():

    return int(
        time.time()
    )


def utc_string(ts=None):

    if ts is None:
        ts = time.time()

    return datetime.fromtimestamp(
        ts,
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def fmt_price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def clamp(value, low, high):

    return max(
        low,
        min(high, value)
    )


# ============================================================
# API
# ============================================================

def get_instruments():

    try:

        r = session.get(
            INSTRUMENTS_URL,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, dict):

            return data.get(
                "instruments",
                []
            )

    except Exception as e:

        print(
            "[ERROR] instruments:",
            e
        )

    return []


def get_tickers():

    try:

        r = session.get(
            TICKERS_URL,
            timeout=20
        )

        r.raise_for_status()

        data = r.json()

        if isinstance(data, dict):

            return data.get(
                "tickers",
                []
            )

    except Exception as e:

        print(
            "[ERROR] tickers:",
            e
        )

    return []


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    ticker_map = {}

    for ticker in tickers:

        symbol = ticker.get(
            "symbol"
        )

        if symbol:

            ticker_map[symbol] = ticker

    markets = []

    for ins in instruments:

        symbol = ins.get(
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
            ins.get(
                "status",
                ""
            )
        ).lower()

        if status:

            if status not in (
                "online",
                "open",
                "trading"
            ):

                continue

        ticker = ticker_map.get(
            symbol,
            {}
        )

        volume = (
            safe_float(
                ticker.get("vol24h")
            )
            or
            safe_float(
                ticker.get("volume24h")
            )
            or
            0
        )

        markets.append({
            "symbol": symbol,
            "volume": volume
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# KRAKEN CANDLE PARSER
# ============================================================

def parse_candle(raw):

    if isinstance(raw, dict):

        ts = (
            raw.get("time")
            or raw.get("timestamp")
            or raw.get("ts")
            or raw.get("t")
        )

        op = (
            raw.get("open")
            or raw.get("o")
        )

        high = (
            raw.get("high")
            or raw.get("h")
        )

        low = (
            raw.get("low")
            or raw.get("l")
        )

        close = (
            raw.get("close")
            or raw.get("c")
        )

        return (
            safe_float(ts),
            safe_float(op),
            safe_float(high),
            safe_float(low),
            safe_float(close)
        )

    if isinstance(raw, list):

        if len(raw) < 5:
            return None

        # Kraken chart responses may contain:
        # [time, open, high, low, close, ...]
        ts = safe_float(raw[0])
        op = safe_float(raw[1])
        high = safe_float(raw[2])
        low = safe_float(raw[3])
        close = safe_float(raw[4])

        return (
            ts,
            op,
            high,
            low,
            close
        )

    return None


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, minutes, limit=220):

    url = CHART_URL.format(
        symbol=symbol,
        minutes=minutes
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        response.raise_for_status()

        data = response.json()

        # ----------------------------------------------------
        # Extract candle container
        # ----------------------------------------------------

        raw_candles = None

        if isinstance(data, dict):

            for key in (
                "candles",
                "data",
                "result"
            ):

                value = data.get(key)

                if isinstance(value, list):

                    raw_candles = value
                    break

                if isinstance(value, dict):

                    for subkey in (
                        "candles",
                        "data",
                        "result"
                    ):

                        sub = value.get(
                            subkey
                        )

                        if isinstance(
                            sub,
                            list
                        ):

                            raw_candles = sub
                            break

                    if raw_candles is not None:
                        break

        elif isinstance(data, list):

            raw_candles = data

        if not raw_candles:

            return []

        result = []

        now_ts = time.time()

        candle_seconds = (
            minutes * 60
        )

        for raw in raw_candles:

            parsed = parse_candle(
                raw
            )

            if not parsed:
                continue

            ts, op, high, low, close = parsed

            if None in (
                ts,
                op,
                high,
                low,
                close
            ):
                continue

            # ------------------------------------------------
            # Timestamp normalization
            # ------------------------------------------------

            # milliseconds
            if ts > 10_000_000_000:

                ts /= 1000.0

            # microseconds
            elif ts > 10_000_000_000_000:

                ts /= 1_000_000.0

            # ------------------------------------------------
            # CLOSED CANDLE ONLY
            # ------------------------------------------------

            if ts + candle_seconds > now_ts:

                continue

            result.append({
                "time": int(ts),
                "open": op,
                "high": high,
                "low": low,
                "close": close
            })

        # Remove duplicates
        unique = {}

        for candle in result:

            unique[
                candle["time"]
            ] = candle

        result = list(
            unique.values()
        )

        result.sort(
            key=lambda x: x["time"]
        )

        if len(result) > limit:

            result = result[-limit:]

        return result

    except Exception as e:

        print(
            f"[CANDLE ERROR] "
            f"{symbol} {minutes}m: {e}"
        )

        return []


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(candles):

    n = len(candles)

    if n < MIN_CANDLES:

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

    tenkan = [None] * n
    kijun = [None] * n
    span_a = [None] * n
    span_b = [None] * n

    # --------------------------------------------------------
    # Tenkan 9
    # --------------------------------------------------------

    for i in range(n):

        if i >= 8:

            tenkan[i] = (
                max(
                    highs[i - 8:i + 1]
                )
                +
                min(
                    lows[i - 8:i + 1]
                )
            ) / 2

        # ----------------------------------------------------
        # Kijun 26
        # ----------------------------------------------------

        if i >= 25:

            kijun[i] = (
                max(
                    highs[i - 25:i + 1]
                )
                +
                min(
                    lows[i - 25:i + 1]
                )
            ) / 2

        # ----------------------------------------------------
        # Senkou A
        # ----------------------------------------------------

        if (
            tenkan[i] is not None
            and
            kijun[i] is not None
        ):

            span_a[i] = (
                tenkan[i]
                +
                kijun[i]
            ) / 2

        # ----------------------------------------------------
        # Senkou B 52
        # ----------------------------------------------------

        if i >= 51:

            span_b[i] = (
                max(
                    highs[i - 51:i + 1]
                )
                +
                min(
                    lows[i - 51:i + 1]
                )
            ) / 2

    current = n - 1

    # Current visible Kumo
    # = spans calculated 26 candles ago

    cloud_index = current - 26

    if cloud_index < 0:

        return None

    current_a = span_a[
        cloud_index
    ]

    current_b = span_b[
        cloud_index
    ]

    if (
        current_a is None
        or
        current_b is None
    ):

        return None

    cloud_top = max(
        current_a,
        current_b
    )

    cloud_bottom = min(
        current_a,
        current_b
    )

    # Future Kumo
    future_a = span_a[current]
    future_b = span_b[current]

    # Chikou
    chikou_index = current - 26

    chikou_bull = None

    if chikou_index >= 0:

        chikou_bull = (
            closes[current]
            >
            closes[chikou_index]
        )

    # Previous cloud
    prev_cloud_index = (
        current - 27
    )

    cloud_direction = 0

    if prev_cloud_index >= 0:

        pa = span_a[
            prev_cloud_index
        ]

        pb = span_b[
            prev_cloud_index
        ]

        if (
            pa is not None
            and
            pb is not None
        ):

            previous_mid = (
                pa + pb
            ) / 2

            current_mid = (
                current_a
                +
                current_b
            ) / 2

            if current_mid > previous_mid:

                cloud_direction = 1

            elif current_mid < previous_mid:

                cloud_direction = -1

    return {

        "price": closes[current],

        "prev_price": closes[
            current - 1
        ],

        "tenkan": tenkan[current],

        "prev_tenkan": tenkan[
            current - 1
        ],

        "kijun": kijun[current],

        "prev_kijun": kijun[
            current - 1
        ],

        "cloud_top": cloud_top,

        "cloud_bottom": cloud_bottom,

        "future_a": future_a,

        "future_b": future_b,

        "chikou_bull": chikou_bull,

        "cloud_direction": cloud_direction
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

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[
            i - 1
        ]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:

        return None

    return (
        sum(
            trs[-period:]
        )
        /
        period
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(info):

    if not info:

        return 0

    price = info["price"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    top = info["cloud_top"]
    bottom = info["cloud_bottom"]

    score = 0

    # Price vs Kumo
    if price > top:

        score += 3

    elif price < bottom:

        score -= 3

    # Price vs Tenkan
    if price > tenkan:

        score += 1

    elif price < tenkan:

        score -= 1

    # Price vs Kijun
    if price > kijun:

        score += 2

    elif price < kijun:

        score -= 2

    # Future Kumo
    if (
        info["future_a"]
        >
        info["future_b"]
    ):

        score += 2

    elif (
        info["future_a"]
        <
        info["future_b"]
    ):

        score -= 2

    # Chikou
    if info["chikou_bull"] is True:

        score += 2

    elif info["chikou_bull"] is False:

        score -= 2

    return int(
        clamp(
            score,
            -10,
            10
        )
    )


# ============================================================
# DIRECTION
# ============================================================

def direction_from_score(score):

    if score > 0:

        return "LONG"

    if score < 0:

        return "SHORT"

    return "NONE"


# ============================================================
# 15M PULLBACK
# ============================================================

def check_pullback(
    info,
    atr,
    direction
):

    if not info:

        return {
            "ok": False,
            "distance_atr": None,
            "reason": "Ichimoku unavailable"
        }

    if not atr or atr <= 0:

        return {
            "ok": False,
            "distance_atr": None,
            "reason": "ATR unavailable"
        }

    price = info["price"]

    top = info["cloud_top"]
    bottom = info["cloud_bottom"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    distance = min(
        abs(price - tenkan),
        abs(price - kijun)
    )

    distance_atr = (
        distance / atr
    )

    if direction == "LONG":

        if price <= top:

            return {
                "ok": False,
                "distance_atr": distance_atr,
                "reason": "Price inside/below Kumo"
            }

        if distance_atr <= PULLBACK_ATR_MAX:

            return {
                "ok": True,
                "distance_atr": distance_atr,
                "reason": "Bullish pullback valid"
            }

        return {
            "ok": False,
            "distance_atr": distance_atr,
            "reason": (
                f"Pullback too far "
                f"({distance_atr:.2f} ATR)"
            )
        }

    if direction == "SHORT":

        if price >= bottom:

            return {
                "ok": False,
                "distance_atr": distance_atr,
                "reason": "Price inside/above Kumo"
            }

        if distance_atr <= PULLBACK_ATR_MAX:

            return {
                "ok": True,
                "distance_atr": distance_atr,
                "reason": "Bearish pullback valid"
            }

        return {
            "ok": False,
            "distance_atr": distance_atr,
            "reason": (
                f"Pullback too far "
                f"({distance_atr:.2f} ATR)"
            )
        }

    return {
        "ok": False,
        "distance_atr": distance_atr,
        "reason": "No direction"
    }


# ============================================================
# 5M TRIGGER
# ============================================================

def check_trigger(
    candles,
    info,
    direction
):

    if (
        not info
        or
        len(candles) < 3
    ):

        return {
            "ok": False,
            "reason": "Not enough 5m candles"
        }

    prev = candles[-2]
    curr = candles[-1]

    price = curr["close"]

    tenkan = info["tenkan"]
    kijun = info["kijun"]

    prev_tenkan = info[
        "prev_tenkan"
    ]

    prev_kijun = info[
        "prev_kijun"
    ]

    cloud_top = info[
        "cloud_top"
    ]

    cloud_bottom = info[
        "cloud_bottom"
    ]

    if direction == "LONG":

        cross_tenkan = (
            prev["close"]
            <=
            prev_tenkan
            and
            price
            >
            tenkan
        )

        cross_kijun = (
            prev["close"]
            <=
            prev_kijun
            and
            price
            >
            kijun
        )

        cloud_ok = (
            price > cloud_top
        )

        if (
            (cross_tenkan or cross_kijun)
            and
            cloud_ok
        ):

            if cross_tenkan:

                reason = (
                    "Bullish Tenkan cross"
                )

            else:

                reason = (
                    "Bullish Kijun cross"
                )

            return {
                "ok": True,
                "reason": reason
            }

        if not cloud_ok:

            return {
                "ok": False,
                "reason": (
                    "5m price not above Kumo"
                )
            }

        return {
            "ok": False,
            "reason": (
                "No bullish Tenkan/Kijun cross"
            )
        }

    if direction == "SHORT":

        cross_tenkan = (
            prev["close"]
            >=
            prev_tenkan
            and
            price
            <
            tenkan
        )

        cross_kijun = (
            prev["close"]
            >=
            prev_kijun
            and
            price
            <
            kijun
        )

        cloud_ok = (
            price < cloud_bottom
        )

        if (
            (cross_tenkan or cross_kijun)
            and
            cloud_ok
        ):

            if cross_tenkan:

                reason = (
                    "Bearish Tenkan cross"
                )

            else:

                reason = (
                    "Bearish Kijun cross"
                )

            return {
                "ok": True,
                "reason": reason
            }

        if not cloud_ok:

            return {
                "ok": False,
                "reason": (
                    "5m price not below Kumo"
                )
            }

        return {
            "ok": False,
            "reason": (
                "No bearish Tenkan/Kijun cross"
            )
        }

    return {
        "ok": False,
        "reason": "No direction"
    }


# ============================================================
# SWINGS
# ============================================================

def latest_swing_low(candles):

    if len(candles) < 7:

        return None

    for i in range(
        len(candles) - 3,
        2,
        -1
    ):

        low = candles[i]["low"]

        if (
            low < candles[i - 1]["low"]
            and
            low < candles[i - 2]["low"]
            and
            low < candles[i + 1]["low"]
            and
            low < candles[i + 2]["low"]
        ):

            return low

    return min(
        x["low"]
        for x in candles[-10:]
    )


def latest_swing_high(candles):

    if len(candles) < 7:

        return None

    for i in range(
        len(candles) - 3,
        2,
        -1
    ):

        high = candles[i]["high"]

        if (
            high > candles[i - 1]["high"]
            and
            high > candles[i - 2]["high"]
            and
            high > candles[i + 1]["high"]
            and
            high > candles[i + 2]["high"]
        ):

            return high

    return max(
        x["high"]
        for x in candles[-10:]
    )


# ============================================================
# EMPTY RESULT
# ============================================================

def base_result(symbol, volume):

    return {
        "symbol": symbol,
        "volume": volume,

        "valid": False,

        "direction": "NONE",

        "stage": "ERROR",
        "reason": "Unknown",

        "score_1h": 0,
        "score_30m": 0,

        "trend_ok": False,
        "confirm_ok": False,
        "pullback_ok": False,
        "trigger_ok": False,

        "distance_atr": None,

        "entry": None,
        "sl": None,
        "tp": None
    }


# ============================================================
# MARKET ANALYSIS
# ============================================================

def analyze_market(market):

    symbol = market["symbol"]

    result = base_result(
        symbol,
        market.get(
            "volume",
            0
        )
    )

    try:

        # ====================================================
        # 1H
        # ====================================================

        c1h = get_candles(
            symbol,
            60,
            220
        )

        count1h = len(c1h)

        if count1h < MIN_CANDLES:

            result["stage"] = "1H"

            result["reason"] = (
                f"Not enough 1H candles "
                f"({count1h}/{MIN_CANDLES})"
            )

            return result

        i1h = calculate_ichimoku(
            c1h
        )

        if not i1h:

            result["stage"] = "1H"

            result["reason"] = (
                "1H Ichimoku unavailable"
            )

            return result

        score1h = calculate_score(
            i1h
        )

        result["score_1h"] = score1h

        direction = (
            direction_from_score(
                score1h
            )
        )

        result["direction"] = direction

        # ----------------------------------------------------
        # Trend
        # ----------------------------------------------------

        if score1h >= MIN_1H_LONG:

            direction = "LONG"

            result["direction"] = direction
            result["trend_ok"] = True

        elif score1h <= MIN_1H_SHORT:

            direction = "SHORT"

            result["direction"] = direction
            result["trend_ok"] = True

        else:

            result["stage"] = "1H"

            result["reason"] = (
                f"Trend too weak "
                f"({score1h:+d}/10)"
            )

            return result

        # ====================================================
        # 30M
        # ====================================================

        c30 = get_candles(
            symbol,
            30,
            220
        )

        count30 = len(c30)

        if count30 < MIN_CANDLES:

            result["stage"] = "30M"

            result["reason"] = (
                f"Not enough 30m candles "
                f"({count30}/{MIN_CANDLES})"
            )

            return result

        i30 = calculate_ichimoku(
            c30
        )

        if not i30:

            result["stage"] = "30M"

            result["reason"] = (
                "30m Ichimoku unavailable"
            )

            return result

        score30 = calculate_score(
            i30
        )

        result["score_30m"] = score30

        # ----------------------------------------------------
        # Confirmation
        # ----------------------------------------------------

        if direction == "LONG":

            if score30 < MIN_30M_LONG:

                result["stage"] = "30M"

                result["reason"] = (
                    f"30m confirmation weak "
                    f"({score30:+d}/10)"
                )

                return result

        elif direction == "SHORT":

            if score30 > MIN_30M_SHORT:

                result["stage"] = "30M"

                result["reason"] = (
                    f"30m confirmation weak "
                    f"({score30:+d}/10)"
                )

                return result

        result["confirm_ok"] = True

        # ====================================================
        # 15M
        # ====================================================

        c15 = get_candles(
            symbol,
            15,
            220
        )

        count15 = len(c15)

        if count15 < MIN_CANDLES:

            result["stage"] = "15M"

            result["reason"] = (
                f"Not enough 15m candles "
                f"({count15}/{MIN_CANDLES})"
            )

            return result

        i15 = calculate_ichimoku(
            c15
        )

        atr15 = calculate_atr(
            c15,
            ATR_PERIOD
        )

        if not i15:

            result["stage"] = "15M"

            result["reason"] = (
                "15m Ichimoku unavailable"
            )

            return result

        if not atr15:

            result["stage"] = "15M"

            result["reason"] = (
                "15m ATR unavailable"
            )

            return result

        pullback = check_pullback(
            i15,
            atr15,
            direction
        )

        result["distance_atr"] = (
            pullback["distance_atr"]
        )

        if not pullback["ok"]:

            result["stage"] = "15M"

            result["reason"] = (
                pullback["reason"]
            )

            return result

        result["pullback_ok"] = True

        # ====================================================
        # 5M
        # ====================================================

        c5 = get_candles(
            symbol,
            5,
            220
        )

        count5 = len(c5)

        if count5 < MIN_CANDLES:

            result["stage"] = "5M"

            result["reason"] = (
                f"Not enough 5m candles "
                f"({count5}/{MIN_CANDLES})"
            )

            return result

        i5 = calculate_ichimoku(
            c5
        )

        if not i5:

            result["stage"] = "5M"

            result["reason"] = (
                "5m Ichimoku unavailable"
            )

            return result

        trigger = check_trigger(
            c5,
            i5,
            direction
        )

        if not trigger["ok"]:

            result["stage"] = "5M"

            result["reason"] = (
                trigger["reason"]
            )

            return result

        result["trigger_ok"] = True

        # ====================================================
        # VALID SIGNAL
        # ====================================================

        entry = c5[-1]["close"]

        if direction == "LONG":

            sl = latest_swing_low(
                c5
            )

            if (
                sl is None
                or
                sl >= entry
            ):

                result["stage"] = "5M"

                result["reason"] = (
                    "Invalid LONG structural SL"
                )

                return result

            risk = entry - sl

            tp = entry + (
                risk * RR
            )

        else:

            sl = latest_swing_high(
                c5
            )

            if (
                sl is None
                or
                sl <= entry
            ):

                result["stage"] = "5M"

                result["reason"] = (
                    "Invalid SHORT structural SL"
                )

                return result

            risk = sl - entry

            tp = entry - (
                risk * RR
            )

        result["valid"] = True

        result["stage"] = "VALID"

        result["reason"] = (
            f"Valid {direction} setup"
        )

        result["entry"] = entry
        result["sl"] = sl
        result["tp"] = tp

        return result

    except Exception as e:

        result["stage"] = "ERROR"

        result["reason"] = (
            str(e)[:150]
        )

        return result


# ============================================================
# RANKING
# ============================================================

STAGE_RANK = {
    "VALID": 1000,
    "5M": 800,
    "15M": 600,
    "30M": 400,
    "1H": 200,
    "ERROR": 0
}


def candidate_rank(result):

    stage = result.get(
        "stage",
        "ERROR"
    )

    score = STAGE_RANK.get(
        stage,
        0
    )

    score += (
        abs(
            result.get(
                "score_1h",
                0
            )
        )
        * 10
    )

    score += (
        abs(
            result.get(
                "score_30m",
                0
            )
        )
        * 8
    )

    distance = result.get(
        "distance_atr"
    )

    if distance is not None:

        score += max(
            0,
            40 - (
                distance * 15
            )
        )

    return score


# ============================================================
# TRADE
# ============================================================

def create_trade(signal):

    return {
        "symbol": signal["symbol"],

        "direction": signal[
            "direction"
        ],

        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],

        "opened_at": int(
            time.time()
        ),

        "opened_at_utc": utc_string(),

        "status": "OPEN"
    }


# ============================================================
# OPEN TRADE MANAGEMENT
# ============================================================

def check_open_trade():

    trade = state.get(
        "open_trade"
    )

    if not trade:

        return None

    symbol = trade["symbol"]

    direction = trade[
        "direction"
    ]

    entry = trade["entry"]
    sl = trade["sl"]
    tp = trade["tp"]

    opened_at = trade[
        "opened_at"
    ]

    age_minutes = (
        time.time()
        -
        opened_at
    ) / 60

    candles = get_candles(
        symbol,
        5,
        10
    )

    if not candles:

        return trade

    candle = candles[-1]

    high = candle["high"]
    low = candle["low"]

    result = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if low <= sl:

            result = {
                "status": "SL",
                "exit": sl,
                "pnl_r": -1
            }

        elif high >= tp:

            result = {
                "status": "TP",
                "exit": tp,
                "pnl_r": RR
            }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif direction == "SHORT":

        if high >= sl:

            result = {
                "status": "SL",
                "exit": sl,
                "pnl_r": -1
            }

        elif low <= tp:

            result = {
                "status": "TP",
                "exit": tp,
                "pnl_r": RR
            }

    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    if (
        result is None
        and
        age_minutes >= TIMEOUT_MINUTES
    ):

        exit_price = candle[
            "close"
        ]

        if direction == "LONG":

            risk = entry - sl

            pnl_r = (
                exit_price - entry
            ) / risk if risk > 0 else 0

        else:

            risk = sl - entry

            pnl_r = (
                entry - exit_price
            ) / risk if risk > 0 else 0

        result = {
            "status": "TIMEOUT",
            "exit": exit_price,
            "pnl_r": pnl_r
        }

    if result:

        closed = dict(
            trade
        )

        closed.update({
            "status": result["status"],
            "exit": result["exit"],
            "pnl_r": result["pnl_r"],
            "closed_at": int(
                time.time()
            ),
            "closed_at_utc": utc_string()
        })

        history.append(
            closed
        )

        state["open_trade"] = None

        save_json(
            STATE_FILE,
            state
        )

        save_json(
            HISTORY_FILE,
            history
        )

        return None

    return trade


# ============================================================
# PERFORMANCE
# ============================================================

def get_performance():

    trades = len(
        history
    )

    wins = sum(
        1
        for x in history
        if x.get("status") == "TP"
    )

    losses = sum(
        1
        for x in history
        if x.get("status") == "SL"
    )

    timeouts = sum(
        1
        for x in history
        if x.get("status") == "TIMEOUT"
    )

    win_rate = (
        wins / trades * 100
        if trades
        else 0
    )

    total_r = sum(
        safe_float(
            x.get("pnl_r")
        ) or 0
        for x in history
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate": win_rate,
        "total_r": total_r
    }


# ============================================================
# CANDIDATE DISPLAY
# ============================================================

def candidate_text(
    index,
    result
):

    symbol = result[
        "symbol"
    ].replace(
        "PF_",
        ""
    )

    direction = result[
        "direction"
    ]

    if direction == "LONG":

        emoji = "🟢"

    elif direction == "SHORT":

        emoji = "🔴"

    else:

        emoji = "⚪"

    stage = result[
        "stage"
    ]

    score1 = result[
        "score_1h"
    ]

    score30 = result[
        "score_30m"
    ]

    reason = result[
        "reason"
    ]

    text = (
        f"{index}. {emoji} "
        f"`{symbol}` "
        f"{direction}\n"
        f"   1H {score1:+d} | "
        f"30m {score30:+d} | "
        f"Stage: {stage}\n"
        f"   {reason}"
    )

    distance = result.get(
        "distance_atr"
    )

    if distance is not None:

        text += (
            f"\n   📏 "
            f"{distance:.2f} ATR"
        )

    return text


# ============================================================
# REPORT
# ============================================================

def build_report(
    results,
    markets_count,
    open_trade,
    new_signal
):

    perf = get_performance()

    valid = [
        x
        for x in results
        if x["valid"]
    ]

    ranked = sorted(
        results,
        key=candidate_rank,
        reverse=True
    )

    top5 = ranked[:5]

    # --------------------------------------------------------
    # Stage statistics
    # --------------------------------------------------------

    stages = {}

    for r in results:

        stage = r.get(
            "stage",
            "ERROR"
        )

        stages[stage] = (
            stages.get(
                stage,
                0
            )
            + 1
        )

    # --------------------------------------------------------
    # Header
    # --------------------------------------------------------

    report = (
        "📡 *KRAKEN FUTURES ICHIMOKU REPORT*\n"
        f"🕐 {utc_string()}\n"
        "⏱ *5m CLOSED | TOP 100*\n"
        "🤖 *ICHIMOKU MTF*\n"
        "1H → Trend | "
        "30m → Confirm | "
        "15m → Pullback | "
        "5m → Trigger\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    report += (
        "📊 *PERFORMANCE*\n"
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['timeouts']}\n"
        f"🏆 WR: "
        f"{perf['win_rate']:.1f}%\n"
        f"📈 Total R: "
        f"{perf['total_r']:+.2f}\n"
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # Open trade
    # --------------------------------------------------------

    report += (
        "📌 *OPEN TRADE*\n"
    )

    if open_trade:

        symbol = open_trade[
            "symbol"
        ].replace(
            "PF_",
            ""
        )

        emoji = (
            "🟢"
            if open_trade[
                "direction"
            ] == "LONG"
            else "🔴"
        )

        report += (
            f"{emoji} `{symbol}` "
            f"{open_trade['direction']}\n"
            f"Entry: "
            f"`{fmt_price(open_trade['entry'])}`\n"
            f"SL: "
            f"`{fmt_price(open_trade['sl'])}`\n"
            f"TP: "
            f"`{fmt_price(open_trade['tp'])}`\n"
        )

    else:

        report += "None\n"

    report += (
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # Signal
    # --------------------------------------------------------

    report += (
        "🔎 *NEW SIGNAL*\n"
    )

    if new_signal:

        symbol = new_signal[
            "symbol"
        ].replace(
            "PF_",
            ""
        )

        emoji = (
            "🟢"
            if new_signal[
                "direction"
            ] == "LONG"
            else "🔴"
        )

        report += (
            f"{emoji} *{symbol}* "
            f"{new_signal['direction']}\n"
            f"Entry: "
            f"`{fmt_price(new_signal['entry'])}`\n"
            f"SL: "
            f"`{fmt_price(new_signal['sl'])}`\n"
            f"TP: "
            f"`{fmt_price(new_signal['tp'])}`\n"
            f"1H: "
            f"`{new_signal['score_1h']:+d}/10`\n"
            f"30m: "
            f"`{new_signal['score_30m']:+d}/10`\n"
        )

    else:

        report += (
            "No new valid MTF setup.\n"
        )

    report += (
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # TOP 5
    # --------------------------------------------------------

    report += (
        "🧪 *TOP 5 CANDIDATES*\n"
        "_Closest markets to a complete setup._\n\n"
    )

    if top5:

        for i, result in enumerate(
            top5,
            1
        ):

            report += (
                candidate_text(
                    i,
                    result
                )
                +
                "\n\n"
            )

    else:

        report += (
            "No candidates.\n\n"
        )

    report += (
        "━━━━━━━━━━━━━━━━━━\n"
    )

    # --------------------------------------------------------
    # Scanner status
    # --------------------------------------------------------

    report += (
        "📡 *SCANNER STATUS*\n"
        f"Scanned: {markets_count}\n"
        f"Valid: {len(valid)}\n"
        f"1H rejected: "
        f"{stages.get('1H', 0)}\n"
        f"30m rejected: "
        f"{stages.get('30M', 0)}\n"
        f"15m rejected: "
        f"{stages.get('15M', 0)}\n"
        f"5m rejected: "
        f"{stages.get('5M', 0)}\n"
        f"Errors: "
        f"{stages.get('ERROR', 0)}\n"
        f"🔒 Max Open: "
        f"{MAX_OPEN_TRADES}\n"
        "⚠️ Virtual signal simulator only"
    )

    return report


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "KRAKEN FUTURES "
        "ICHIMOKU MTF SCANNER v4.0"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # Existing trade
    # --------------------------------------------------------

    open_trade = check_open_trade()

    # --------------------------------------------------------
    # Markets
    # --------------------------------------------------------

    markets = get_top_markets()

    markets_count = len(
        markets
    )

    print(
        f"[INFO] Markets: "
        f"{markets_count}"
    )

    if not markets:

        send_telegram(
            "❌ *SCANNER ERROR*\n"
            "No Kraken Futures markets found."
        )

        return

    print(
        "[INFO] Starting full "
        "100-market analysis..."
    )

    # --------------------------------------------------------
    # Analyze all
    # --------------------------------------------------------

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        future_map = {
            executor.submit(
                analyze_market,
                market
            ): market
            for market in markets
        }

        for future in as_completed(
            future_map
        ):

            market = future_map[
                future
            ]

            try:

                result = future.result()

            except Exception as e:

                result = base_result(
                    market["symbol"],
                    market.get(
                        "volume",
                        0
                    )
                )

                result["stage"] = (
                    "ERROR"
                )

                result["reason"] = (
                    str(e)[:150]
                )

            results.append(
                result
            )

            print(
                f"[SCAN] "
                f"{result['symbol']} | "
                f"{result['stage']} | "
                f"1H {result['score_1h']:+d} | "
                f"30M {result['score_30m']:+d} | "
                f"{result['reason']}"
            )

    # --------------------------------------------------------
    # Valid signals
    # --------------------------------------------------------

    valid_signals = [
        x
        for x in results
        if x["valid"]
    ]

    valid_signals.sort(
        key=candidate_rank,
        reverse=True
    )

    new_signal = None

    # --------------------------------------------------------
    # Max one open trade
    # --------------------------------------------------------

    if (
        open_trade is None
        and
        valid_signals
    ):

        new_signal = (
            valid_signals[0]
        )

        open_trade = create_trade(
            new_signal
        )

        state["open_trade"] = (
            open_trade
        )

        state["last_signal"] = (
            new_signal
        )

        save_json(
            STATE_FILE,
            state
        )

        print(
            "[NEW SIGNAL]",
            new_signal["symbol"],
            new_signal["direction"]
        )

    elif open_trade:

        print(
            "[INFO] Existing trade:",
            open_trade["symbol"]
        )

    else:

        print(
            "[INFO] No valid signal."
        )

    # --------------------------------------------------------
    # Report
    # --------------------------------------------------------

    report = build_report(
        results,
        markets_count,
        open_trade,
        new_signal
    )

    print()
    print(report)
    print()

    send_telegram(
        report
    )

    print("=" * 70)
    print("SCAN COMPLETE")
    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
