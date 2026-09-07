# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v9.0
# ============================================================
#
# TIMEFRAMES:
#   1H  = TREND
#   30M = CONFIRMATION
#   15M = PULLBACK
#   5M  = TRIGGER
#
# RULES:
#   MAX OPEN TRADES = 4
#   MAX LONG        = 2
#   MAX SHORT       = 2
#   RR              = 1:1
#   MIN RISK        = 0.50%
#
# IMPORTANT:
#   - CLOSED candles only
#   - OPEN trades persist in state
#   - CLOSED trades go immediately into PERFORMANCE
#   - NO "NEW SIGNALS" section
#   - LIVE P/L based on current market price
#
# ============================================================

import os
import json
import time
import html
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

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

RR = 1.0

ATR_PERIOD = 14

PULLBACK_ATR_MULT = 1.5

TRADE_TIMEOUT_MINUTES = 240

MIN_RISK_PERCENT = 0.50

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
    "User-Agent": "Kraken-Ice-MTF-Scanner/9.0"
})


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def iso_now():
    return utc_now().isoformat()


def safe_float(value, default=None):
    try:
        return float(value)
    except Exception:
        return default


def fmt_price(value):
    """
    Maximum 4 decimal places.
    """
    if value is None:
        return "-"

    try:
        x = float(value)

        if x == 0:
            return "0"

        s = f"{x:.4f}"

        s = s.rstrip("0").rstrip(".")

        return s

    except Exception:
        return str(value)


def fmt_pct(value):
    if value is None:
        return "0.00%"

    return f"{value:+.2f}%"


def html_escape(value):
    return html.escape(str(value))


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):

    response = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    return response.json()


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):

    if not TELEGRAM_BOT_TOKEN:
        print("[WARN] TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[WARN] TELEGRAM_CHAT_ID missing")
        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    response = SESSION.post(
        url,
        data=payload,
        timeout=REQUEST_TIMEOUT
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram error: {data}"
        )

    return True


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "open_trades": [],
        "last_signal_keys": [],
        "updated_at": iso_now()
    }


def default_history():

    return []


def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:
        with open(
            filename,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"[WARN] Failed loading {filename}: {e}"
        )

        return default


def save_json(filename, data):

    tmp = filename + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(tmp, filename)


def load_state():

    state = load_json(
        STATE_FILE,
        default_state()
    )

    if not isinstance(state, dict):
        state = default_state()

    # --------------------------------------------------------
    # Migration from old versions
    # --------------------------------------------------------

    if "open_trades" not in state:

        old_trade = state.get("open_trade")

        if old_trade:
            state["open_trades"] = [old_trade]

        else:
            state["open_trades"] = []

    if not isinstance(
        state["open_trades"],
        list
    ):
        state["open_trades"] = []

    if "last_signal_keys" not in state:
        state["last_signal_keys"] = []

    return state


def load_history():

    history = load_json(
        HISTORY_FILE,
        default_history()
    )

    if not isinstance(history, list):
        history = []

    return history


# ============================================================
# MARKETS
# ============================================================

def get_top_markets():

    data = http_get(INSTRUMENTS_URL)

    instruments = data.get(
        "instruments",
        []
    )

    markets = []

    for item in instruments:

        symbol = item.get("symbol")

        if not symbol:
            continue

        symbol = str(symbol)

        # Kraken perpetual futures
        if not (
            symbol.startswith("PF_")
            or symbol.startswith("PI_")
        ):
            continue

        markets.append({
            "symbol": symbol,
            "volume": safe_float(
                item.get(
                    "volume24h",
                    item.get(
                        "volume",
                        0
                    )
                ),
                0
            )
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    return markets[:TOP_N]


# ============================================================
# LIVE PRICES
# ============================================================

def get_live_prices():

    data = http_get(TICKERS_URL)

    tickers = data.get(
        "tickers",
        []
    )

    result = {}

    for item in tickers:

        symbol = item.get("symbol")

        if not symbol:
            continue

        price = None

        for field in (
            "last",
            "lastPrice",
            "markPrice",
            "price"
        ):

            if item.get(field) is not None:

                price = safe_float(
                    item.get(field)
                )

                if price is not None:
                    break

        if price is not None:
            result[symbol] = price

    return result


# ============================================================
# CANDLES
# ============================================================

RESOLUTION_MAP = {
    5: "5m",
    15: "15m",
    30: "30m",
    60: "1h"
}


INTERVAL_MS = {
    5: 5 * 60 * 1000,
    15: 15 * 60 * 1000,
    30: 30 * 60 * 1000,
    60: 60 * 60 * 1000
}


def get_candles(symbol, timeframe):

    resolution = RESOLUTION_MAP[timeframe]

    url = (
        CANDLES_URL
        + "/"
        + symbol
        + "/"
        + resolution
    )

    params = {
        "count": CANDLE_LIMIT
    }

    data = http_get(
        url,
        params=params
    )

    raw = data.get(
        "candles",
        []
    )

    candles = []

    now_ms = int(
        time.time() * 1000
    )

    interval = INTERVAL_MS[timeframe]

    for c in raw:

        try:

            ts = int(
                c.get("time")
            )

            o = safe_float(
                c.get("open")
            )

            h = safe_float(
                c.get("high")
            )

            l = safe_float(
                c.get("low")
            )

            close = safe_float(
                c.get("close")
            )

            volume = safe_float(
                c.get("volume"),
                0
            )

            if None in (
                o,
                h,
                l,
                close
            ):
                continue

            # ------------------------------------------------
            # Remove currently open candle
            # ------------------------------------------------

            if (
                ts + interval
                > now_ms
            ):
                continue

            candles.append({
                "time": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": close,
                "volume": volume
            })

        except Exception:
            continue

    # Remove duplicates
    unique = {}

    for c in candles:
        unique[c["time"]] = c

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles


# ============================================================
# INDICATORS
# ============================================================

def rolling_high(candles, period):

    result = [None] * len(candles)

    for i in range(
        period - 1,
        len(candles)
    ):

        values = [
            c["high"]
            for c in candles[
                i - period + 1:
                i + 1
            ]
        ]

        result[i] = max(values)

    return result


def rolling_low(candles, period):

    result = [None] * len(candles)

    for i in range(
        period - 1,
        len(candles)
    ):

        values = [
            c["low"]
            for c in candles[
                i - period + 1:
                i + 1
            ]
        ]

        result[i] = min(values)

    return result


def calculate_ichimoku(candles):

    n = len(candles)

    tenkan = [None] * n
    kijun = [None] * n
    senkou_a = [None] * n
    senkou_b = [None] * n

    for i in range(n):

        # ----------------------------------------------------
        # Tenkan 9
        # ----------------------------------------------------

        if i >= 8:

            highs = [
                c["high"]
                for c in candles[
                    i - 8:
                    i + 1
                ]
            ]

            lows = [
                c["low"]
                for c in candles[
                    i - 8:
                    i + 1
                ]
            ]

            tenkan[i] = (
                max(highs)
                + min(lows)
            ) / 2

        # ----------------------------------------------------
        # Kijun 26
        # ----------------------------------------------------

        if i >= 25:

            highs = [
                c["high"]
                for c in candles[
                    i - 25:
                    i + 1
                ]
            ]

            lows = [
                c["low"]
                for c in candles[
                    i - 25:
                    i + 1
                ]
            ]

            kijun[i] = (
                max(highs)
                + min(lows)
            ) / 2

        # ----------------------------------------------------
        # Senkou B 52
        # ----------------------------------------------------

        if i >= 51:

            highs = [
                c["high"]
                for c in candles[
                    i - 51:
                    i + 1
                ]
            ]

            lows = [
                c["low"]
                for c in candles[
                    i - 51:
                    i + 1
                ]
            ]

            senkou_b[i] = (
                max(highs)
                + min(lows)
            ) / 2

        # ----------------------------------------------------
        # Senkou A
        # ----------------------------------------------------

        if (
            tenkan[i] is not None
            and kijun[i] is not None
        ):

            senkou_a[i] = (
                tenkan[i]
                + kijun[i]
            ) / 2

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b
    }


# ============================================================
# ATR
# ============================================================

def calculate_atr(candles, period=14):

    if len(candles) < period + 1:
        return None

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

        trs.append(tr)

    if len(trs) < period:
        return None

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# ICHIMOKU CLOUD
# ============================================================

def get_cloud_values(
    candles,
    ichimoku,
    index
):

    # Current visible cloud approximated
    # by shifted 26 candles.

    cloud_index = index - 26

    if cloud_index < 0:
        return None, None

    sa = ichimoku["senkou_a"][
        cloud_index
    ]

    sb = ichimoku["senkou_b"][
        cloud_index
    ]

    if sa is None or sb is None:
        return None, None

    return (
        min(sa, sb),
        max(sa, sb)
    )


def get_future_cloud(
    ichimoku,
    index
):

    sa = ichimoku["senkou_a"][index]
    sb = ichimoku["senkou_b"][index]

    if sa is None or sb is None:
        return None, None

    return (
        min(sa, sb),
        max(sa, sb)
    )


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(
    candles,
    ichimoku
):

    i = len(candles) - 1

    close = candles[i]["close"]

    tenkan = ichimoku["tenkan"][i]
    kijun = ichimoku["kijun"][i]

    if tenkan is None or kijun is None:
        return 0

    cloud_bottom, cloud_top = (
        get_cloud_values(
            candles,
            ichimoku,
            i
        )
    )

    if (
        cloud_bottom is None
        or cloud_top is None
    ):
        return 0

    score = 0

    # --------------------------------------------------------
    # Price vs Kumo
    # --------------------------------------------------------

    if close > cloud_top:
        score += 3

    elif close < cloud_bottom:
        score -= 3

    # --------------------------------------------------------
    # Price vs Tenkan
    # --------------------------------------------------------

    if close > tenkan:
        score += 1

    elif close < tenkan:
        score -= 1

    # --------------------------------------------------------
    # Price vs Kijun
    # --------------------------------------------------------

    if close > kijun:
        score += 2

    elif close < kijun:
        score -= 2

    # --------------------------------------------------------
    # Future cloud
    # --------------------------------------------------------

    future_bottom, future_top = (
        get_future_cloud(
            ichimoku,
            i
        )
    )

    if (
        future_bottom is not None
        and future_top is not None
    ):

        if future_bottom > close:
            score -= 2

        elif future_top < close:
            score += 2

    # --------------------------------------------------------
    # Chikou approximation
    # --------------------------------------------------------

    if i >= 26:

        past_close = candles[
            i - 26
        ]["close"]

        if close > past_close:
            score += 2

        elif close < past_close:
            score -= 2

    return max(
        -10,
        min(
            10,
            score
        )
    )


# ============================================================
# PULLBACK
# ============================================================

def check_pullback_long(
    candles,
    ichimoku
):

    i = len(candles) - 1

    close = candles[i]["close"]

    tenkan = ichimoku["tenkan"][i]
    kijun = ichimoku["kijun"][i]

    if (
        tenkan is None
        or kijun is None
    ):
        return False

    cloud_bottom, cloud_top = (
        get_cloud_values(
            candles,
            ichimoku,
            i
        )
    )

    if cloud_bottom is None:
        return False

    atr = calculate_atr(
        candles,
        ATR_PERIOD
    )

    if atr is None:
        return False

    near_tenkan = (
        abs(close - tenkan)
        <= atr * PULLBACK_ATR_MULT
    )

    near_kijun = (
        abs(close - kijun)
        <= atr * PULLBACK_ATR_MULT
    )

    return (
        close > cloud_bottom
        and (
            near_tenkan
            or near_kijun
        )
    )


def check_pullback_short(
    candles,
    ichimoku
):

    i = len(candles) - 1

    close = candles[i]["close"]

    tenkan = ichimoku["tenkan"][i]
    kijun = ichimoku["kijun"][i]

    if (
        tenkan is None
        or kijun is None
    ):
        return False

    cloud_bottom, cloud_top = (
        get_cloud_values(
            candles,
            ichimoku,
            i
        )
    )

    if cloud_top is None:
        return False

    atr = calculate_atr(
        candles,
        ATR_PERIOD
    )

    if atr is None:
        return False

    near_tenkan = (
        abs(close - tenkan)
        <= atr * PULLBACK_ATR_MULT
    )

    near_kijun = (
        abs(close - kijun)
        <= atr * PULLBACK_ATR_MULT
    )

    return (
        close < cloud_top
        and (
            near_tenkan
            or near_kijun
        )
    )


# ============================================================
# 5M TRIGGER
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


def check_trigger_long(
    candles,
    ichimoku
):

    if len(candles) < 3:
        return False, ""

    i = len(candles) - 1

    current = candles[i]
    previous = candles[i - 1]

    close = current["close"]

    tenkan = ichimoku["tenkan"][i]
    kijun = ichimoku["kijun"][i]

    cloud_bottom, cloud_top = (
        get_cloud_values(
            candles,
            ichimoku,
            i
        )
    )

    if (
        tenkan is None
        or kijun is None
        or cloud_bottom is None
    ):
        return False, ""

    reasons = []

    # --------------------------------------------------------
    # Bullish candle
    # --------------------------------------------------------

    bullish_candle = (
        current["close"]
        > current["open"]
    )

    if not bullish_candle:
        return False, ""

    # --------------------------------------------------------
    # Tenkan cross
    # --------------------------------------------------------

    prev_tenkan = (
        ichimoku["tenkan"][i - 1]
    )

    if prev_tenkan is not None:

        if (
            previous["close"]
            <= prev_tenkan
            and
            close
            > tenkan
        ):

            reasons.append(
                "Tenkan cross"
            )

    # --------------------------------------------------------
    # Kijun cross
    # --------------------------------------------------------

    prev_kijun = (
        ichimoku["kijun"][i - 1]
    )

    if prev_kijun is not None:

        if (
            previous["close"]
            <= prev_kijun
            and
            close
            > kijun
        ):

            reasons.append(
                "Kijun cross"
            )

    # --------------------------------------------------------
    # Engulfing
    # --------------------------------------------------------

    if bullish_engulfing(
        previous,
        current
    ):

        reasons.append(
            "Bullish engulfing"
        )

    # --------------------------------------------------------
    # Break previous high
    # --------------------------------------------------------

    if (
        current["close"]
        > previous["high"]
    ):

        reasons.append(
            "Break previous high"
        )

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    body = abs(
        current["close"]
        - current["open"]
    )

    candle_range = (
        current["high"]
        - current["low"]
    )

    if (
        candle_range > 0
        and body / candle_range >= 0.55
        and current["close"] > previous["close"]
    ):

        reasons.append(
            "Bullish momentum"
        )

    if close <= cloud_bottom:
        return False, ""

    # Require at least one trigger
    if not reasons:
        return False, ""

    return True, " + ".join(reasons)


def check_trigger_short(
    candles,
    ichimoku
):

    if len(candles) < 3:
        return False, ""

    i = len(candles) - 1

    current = candles[i]
    previous = candles[i - 1]

    close = current["close"]

    tenkan = ichimoku["tenkan"][i]
    kijun = ichimoku["kijun"][i]

    cloud_bottom, cloud_top = (
        get_cloud_values(
            candles,
            ichimoku,
            i
        )
    )

    if (
        tenkan is None
        or kijun is None
        or cloud_top is None
    ):
        return False, ""

    reasons = []

    bearish_candle = (
        current["close"]
        < current["open"]
    )

    if not bearish_candle:
        return False, ""

    # --------------------------------------------------------
    # Tenkan cross
    # --------------------------------------------------------

    prev_tenkan = (
        ichimoku["tenkan"][i - 1]
    )

    if prev_tenkan is not None:

        if (
            previous["close"]
            >= prev_tenkan
            and
            close
            < tenkan
        ):

            reasons.append(
                "Tenkan cross"
            )

    # --------------------------------------------------------
    # Kijun cross
    # --------------------------------------------------------

    prev_kijun = (
        ichimoku["kijun"][i - 1]
    )

    if prev_kijun is not None:

        if (
            previous["close"]
            >= prev_kijun
            and
            close
            < kijun
        ):

            reasons.append(
                "Kijun cross"
            )

    # --------------------------------------------------------
    # Engulfing
    # --------------------------------------------------------

    if bearish_engulfing(
        previous,
        current
    ):

        reasons.append(
            "Bearish engulfing"
        )

    # --------------------------------------------------------
    # Break previous low
    # --------------------------------------------------------

    if (
        current["close"]
        < previous["low"]
    ):

        reasons.append(
            "Break previous low"
        )

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    body = abs(
        current["close"]
        - current["open"]
    )

    candle_range = (
        current["high"]
        - current["low"]
    )

    if (
        candle_range > 0
        and body / candle_range >= 0.55
        and current["close"] < previous["close"]
    ):

        reasons.append(
            "Bearish momentum"
        )

    if close >= cloud_top:
        return False, ""

    if not reasons:
        return False, ""

    return True, " + ".join(reasons)


# ============================================================
# STOP LOSS
# ============================================================

def calculate_long_sl(
    candles
):

    recent = candles[-10:]

    if not recent:
        return None

    return min(
        c["low"]
        for c in recent
    )


def calculate_short_sl(
    candles
):

    recent = candles[-10:]

    if not recent:
        return None

    return max(
        c["high"]
        for c in recent
    )


# ============================================================
# TRADE CREATION
# ============================================================

def create_trade(
    symbol,
    direction,
    entry,
    sl,
    trigger_reason
):

    if entry is None or sl is None:
        return None

    entry = float(entry)
    sl = float(sl)

    # --------------------------------------------------------
    # Validate direction
    # --------------------------------------------------------

    if direction == "LONG":

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + (
            risk * RR
        )

    else:

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - (
            risk * RR
        )

    # --------------------------------------------------------
    # Minimum risk filter
    # --------------------------------------------------------

    risk_percent = (
        abs(risk)
        / entry
        * 100
    )

    if risk_percent < MIN_RISK_PERCENT:
        return None

    return {
        "symbol": symbol,
        "direction": direction,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "risk_percent": risk_percent,
        "opened_at": iso_now(),
        "trigger_reason": trigger_reason,
        "status": "OPEN"
    }


# ============================================================
# SIGNAL SCANNER
# ============================================================

def scan_market(
    symbol
):

    stats = {
        "stage": "1H",
        "score_1h": None,
        "score_30m": None,
        "valid": False,
        "direction": None,
        "trigger": ""
    }

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    candles_1h = get_candles(
        symbol,
        60
    )

    if len(candles_1h) < MIN_CANDLES:

        stats["stage"] = "1H"
        return None, stats

    ich_1h = calculate_ichimoku(
        candles_1h
    )

    score_1h = ichimoku_score(
        candles_1h,
        ich_1h
    )

    stats["score_1h"] = score_1h

    if score_1h >= 6:

        direction = "LONG"

    elif score_1h <= -6:

        direction = "SHORT"

    else:

        stats["stage"] = "1H"
        return None, stats

    stats["direction"] = direction

    # --------------------------------------------------------
    # 30M
    # --------------------------------------------------------

    candles_30m = get_candles(
        symbol,
        30
    )

    if len(candles_30m) < MIN_CANDLES:

        stats["stage"] = "30m"
        return None, stats

    ich_30m = calculate_ichimoku(
        candles_30m
    )

    score_30m = ichimoku_score(
        candles_30m,
        ich_30m
    )

    stats["score_30m"] = score_30m

    if direction == "LONG":

        if score_30m < 5:
            stats["stage"] = "30m"
            return None, stats

    else:

        if score_30m > -5:
            stats["stage"] = "30m"
            return None, stats

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    candles_15m = get_candles(
        symbol,
        15
    )

    if len(candles_15m) < MIN_CANDLES:

        stats["stage"] = "15m"
        return None, stats

    ich_15m = calculate_ichimoku(
        candles_15m
    )

    if direction == "LONG":

        pullback_ok = (
            check_pullback_long(
                candles_15m,
                ich_15m
            )
        )

    else:

        pullback_ok = (
            check_pullback_short(
                candles_15m,
                ich_15m
            )
        )

    if not pullback_ok:

        stats["stage"] = "15m"
        return None, stats

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    candles_5m = get_candles(
        symbol,
        5
    )

    if len(candles_5m) < MIN_CANDLES:

        stats["stage"] = "5m"
        return None, stats

    ich_5m = calculate_ichimoku(
        candles_5m
    )

    if direction == "LONG":

        trigger_ok, reason = (
            check_trigger_long(
                candles_5m,
                ich_5m
            )
        )

    else:

        trigger_ok, reason = (
            check_trigger_short(
                candles_5m,
                ich_5m
            )
        )

    if not trigger_ok:

        stats["stage"] = "5m"
        return None, stats

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = candles_5m[-1]["close"]

    # --------------------------------------------------------
    # SL
    # --------------------------------------------------------

    if direction == "LONG":

        sl = calculate_long_sl(
            candles_5m
        )

    else:

        sl = calculate_short_sl(
            candles_5m
        )

    trade = create_trade(
        symbol=symbol,
        direction=direction,
        entry=entry,
        sl=sl,
        trigger_reason=reason
    )

    if trade is None:

        # It reached trigger but failed
        # minimum-risk requirement.
        stats["stage"] = "RISK"
        return None, stats

    stats["valid"] = True
    stats["stage"] = "VALID"
    stats["trigger"] = reason

    return trade, stats


# ============================================================
# OPEN TRADE COUNTS
# ============================================================

def count_direction(
    open_trades,
    direction
):

    return sum(
        1
        for t in open_trades
        if t.get("direction")
        == direction
    )


def symbol_is_open(
    open_trades,
    symbol
):

    return any(
        t.get("symbol") == symbol
        for t in open_trades
    )


# ============================================================
# TRADE TIME
# ============================================================

def trade_age_minutes(trade):

    try:

        opened = datetime.fromisoformat(
            trade["opened_at"]
        )

        if opened.tzinfo is None:
            opened = opened.replace(
                tzinfo=timezone.utc
            )

        seconds = (
            utc_now()
            - opened
        ).total_seconds()

        return max(
            0,
            int(seconds / 60)
        )

    except Exception:

        return 0


# ============================================================
# LIVE P/L
# ============================================================

def calculate_live_pnl(
    trade,
    market_price
):

    if market_price is None:
        return 0.0

    entry = float(
        trade["entry"]
    )

    if entry == 0:
        return 0.0

    if trade["direction"] == "LONG":

        return (
            (
                market_price
                - entry
            )
            / entry
            * 100
        )

    return (
        (
            entry
            - market_price
        )
        / entry
        * 100
    )


# ============================================================
# OPEN TRADE TP/SL DISTANCES
# ============================================================

def trade_current_distances(
    trade,
    market_price
):

    if market_price is None:
        return None, None

    if market_price == 0:
        return None, None

    tp = float(
        trade["tp"]
    )

    sl = float(
        trade["sl"]
    )

    if trade["direction"] == "LONG":

        tp_pct = (
            (tp - market_price)
            / market_price
            * 100
        )

        sl_pct = (
            (market_price - sl)
            / market_price
            * 100
        )

    else:

        tp_pct = (
            (market_price - tp)
            / market_price
            * 100
        )

        sl_pct = (
            (sl - market_price)
            / market_price
            * 100
        )

    return tp_pct, sl_pct


# ============================================================
# CLOSED TRADE RESULT
# ============================================================

def add_closed_trade(
    history,
    trade,
    result,
    exit_price,
    closed_at
):

    closed = dict(trade)

    closed["status"] = "CLOSED"
    closed["result"] = result
    closed["exit_price"] = exit_price
    closed["closed_at"] = closed_at

    if result == "TP":

        closed["r"] = RR

    elif result == "SL":

        closed["r"] = -1.0

    else:

        closed["r"] = 0.0

    history.append(
        closed
    )


# ============================================================
# CHECK OPEN TRADES
# ============================================================

def check_open_trades(
    state,
    history
):

    remaining = []

    closed_count = 0

    for trade in state["open_trades"]:

        symbol = trade.get("symbol")

        direction = trade.get(
            "direction"
        )

        candles = None

        try:

            candles = get_candles(
                symbol,
                5
            )

        except Exception as e:

            print(
                f"[WARN] "
                f"Cannot update "
                f"{symbol}: {e}"
            )

            remaining.append(
                trade
            )

            continue

        if not candles:

            remaining.append(
                trade
            )

            continue

        latest = candles[-1]

        high = latest["high"]
        low = latest["low"]

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        age = trade_age_minutes(
            trade
        )

        if age >= TRADE_TIMEOUT_MINUTES:

            exit_price = latest["close"]

            add_closed_trade(
                history,
                trade,
                "TIMEOUT",
                exit_price,
                iso_now()
            )

            closed_count += 1

            continue

        hit_tp = False
        hit_sl = False

        if direction == "LONG":

            if low <= sl:
                hit_sl = True

            if high >= tp:
                hit_tp = True

        else:

            if high >= sl:
                hit_sl = True

            if low <= tp:
                hit_tp = True

        # ----------------------------------------------------
        # Conservative rule:
        # If TP and SL occur inside the same candle,
        # assume SL first.
        # ----------------------------------------------------

        if hit_sl:

            add_closed_trade(
                history,
                trade,
                "SL",
                sl,
                iso_now()
            )

            closed_count += 1

            continue

        if hit_tp:

            add_closed_trade(
                history,
                trade,
                "TP",
                tp,
                iso_now()
            )

            closed_count += 1

            continue

        remaining.append(
            trade
        )

    state["open_trades"] = remaining

    return closed_count


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    history
):

    trades = len(history)

    wins = sum(
        1
        for t in history
        if t.get("result") == "TP"
    )

    losses = sum(
        1
        for t in history
        if t.get("result") == "SL"
    )

    timeouts = sum(
        1
        for t in history
        if t.get("result") == "TIMEOUT"
    )

    r_total = sum(
        float(
            t.get("r", 0)
        )
        for t in history
    )

    decisive = wins + losses

    if decisive > 0:

        win_rate = (
            wins
            / decisive
            * 100
        )

    else:

        win_rate = 0.0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "r": r_total,
        "win_rate": win_rate
    }


# ============================================================
# SIGNAL KEY
# ============================================================

def signal_key(trade):

    symbol = trade["symbol"]

    direction = trade["direction"]

    opened = trade["opened_at"]

    try:

        dt = datetime.fromisoformat(
            opened
        )

        bucket = int(
            dt.timestamp()
            // 300
        )

    except Exception:

        bucket = int(
            time.time()
            // 300
        )

    return (
        f"{symbol}|"
        f"{direction}|"
        f"{bucket}"
    )


# ============================================================
# BUILD REPORT
# ============================================================

def build_report(
    state,
    history,
    live_prices,
    scan_stats
):

    performance = (
        calculate_performance(
            history
        )
    )

    lines = []

    lines.append(
        "<b>📡 KRAKEN FUTURES "
        "ICHIMOKU REPORT</b>"
    )

    lines.append(
        "🕐 "
        + utc_now().strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
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

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "<b>📊 PERFORMANCE</b>"
    )

    lines.append(
        f"Trades {performance['trades']} | "
        f"🟢 {performance['wins']} | "
        f"🔴 {performance['losses']} | "
        f"⚪ {performance['timeouts']}"
    )

    lines.append(
        f"🏆 WR "
        f"{performance['win_rate']:.1f}% | "
        f"R {performance['r']:+.2f}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    open_trades = state["open_trades"]

    lines.append(
        f"<b>🔴 OPEN TRADES "
        f"({len(open_trades)}/"
        f"{MAX_OPEN_TRADES})</b>"
    )

    if not open_trades:

        lines.append(
            "No open trades"
        )

    else:

        for idx, trade in enumerate(
            open_trades,
            start=1
        ):

            symbol = trade["symbol"]

            direction = trade["direction"]

            entry = float(
                trade["entry"]
            )

            sl = float(
                trade["sl"]
            )

            tp = float(
                trade["tp"]
            )

            market = live_prices.get(
                symbol,
                entry
            )

            pnl = calculate_live_pnl(
                trade,
                market
            )

            tp_pct, sl_pct = (
                trade_current_distances(
                    trade,
                    market
                )
            )

            age = trade_age_minutes(
                trade
            )

            pnl_icon = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            direction_icon = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            lines.append(
                f"<b>#{idx} "
                f"{html_escape(symbol)} "
                f"{direction_icon} "
                f"{direction}</b>"
            )

            lines.append(
                f"Entry: {fmt_price(entry)}"
            )

            lines.append(
                f"Market: {fmt_price(market)}"
            )

            lines.append(
                f"SL: {fmt_price(sl)} "
                f"({sl_pct:+.2f}%)"
            )

            lines.append(
                f"TP: {fmt_price(tp)} "
                f"({tp_pct:+.2f}%)"
            )

            lines.append(
                f"{pnl_icon} Live P/L: "
                f"<b>{pnl:+.2f}%</b>"
            )

            lines.append(
                f"⏱ Age: {age}m"
            )

            lines.append(
                "Trigger: "
                + html_escape(
                    trade.get(
                        "trigger_reason",
                        "-"
                    )
                )
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # SCANNER STATUS
    # --------------------------------------------------------

    lines.append(
        "<b>SCANNER STATUS</b>"
    )

    lines.append(
        f"Markets: {scan_stats['markets']}"
    )

    lines.append(
        f"Scanned: {scan_stats['scanned']}"
    )

    lines.append(
        f"Valid: {scan_stats['valid']}"
    )

    lines.append(
        f"1H rejected: "
        f"{scan_stats['1h_rejected']}"
    )

    lines.append(
        f"30m rejected: "
        f"{scan_stats['30m_rejected']}"
    )

    lines.append(
        f"15m rejected: "
        f"{scan_stats['15m_rejected']}"
    )

    lines.append(
        f"5m rejected: "
        f"{scan_stats['5m_rejected']}"
    )

    lines.append(
        f"Risk < {MIN_RISK_PERCENT:.2f}%: "
        f"{scan_stats['risk_rejected']}"
    )

    lines.append(
        f"Data errors: "
        f"{scan_stats['data_errors']}"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("KRAKEN FUTURES ICHIMOKU MTF SCANNER v9.0")
    print("=" * 70)

    state = load_state()

    history = load_history()

    # --------------------------------------------------------
    # CLEAN OLD / INVALID OPEN TRADES
    # --------------------------------------------------------

    cleaned = []

    for trade in state["open_trades"]:

        if (
            trade.get("symbol")
            and trade.get("direction")
            and trade.get("entry") is not None
            and trade.get("sl") is not None
            and trade.get("tp") is not None
        ):

            cleaned.append(
                trade
            )

    state["open_trades"] = cleaned

    # --------------------------------------------------------
    # LIVE PRICES
    # --------------------------------------------------------

    try:

        live_prices = (
            get_live_prices()
        )

    except Exception as e:

        print(
            f"[WARN] Live prices failed: {e}"
        )

        live_prices = {}

    # --------------------------------------------------------
    # CHECK EXISTING TRADES FIRST
    # --------------------------------------------------------

    closed_now = check_open_trades(
        state,
        history
    )

    print(
        f"[INFO] Closed this run: "
        f"{closed_now}"
    )

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    markets = get_top_markets()

    print(
        f"[INFO] Markets: "
        f"{len(markets)}"
    )

    scan_stats = {
        "markets": len(markets),
        "scanned": 0,
        "valid": 0,
        "1h_rejected": 0,
        "30m_rejected": 0,
        "15m_rejected": 0,
        "5m_rejected": 0,
        "risk_rejected": 0,
        "data_errors": 0,
    }

    candidates = []

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    for market in markets:

        symbol = market["symbol"]

        scan_stats["scanned"] += 1

        try:

            trade, stats = scan_market(
                symbol
            )

            if trade is not None:

                candidates.append(
                    trade
                )

                scan_stats["valid"] += 1

            else:

                stage = stats.get(
                    "stage"
                )

                if stage == "1H":

                    scan_stats[
                        "1h_rejected"
                    ] += 1

                elif stage == "30m":

                    scan_stats[
                        "30m_rejected"
                    ] += 1

                elif stage == "15m":

                    scan_stats[
                        "15m_rejected"
                    ] += 1

                elif stage == "5m":

                    scan_stats[
                        "5m_rejected"
                    ] += 1

                elif stage == "RISK":

                    scan_stats[
                        "risk_rejected"
                    ] += 1

        except Exception as e:

            scan_stats[
                "data_errors"
            ] += 1

            print(
                f"[ERROR] {symbol}: {e}"
            )

    # --------------------------------------------------------
    # RANK CANDIDATES
    #
    # Internal only.
    # Candidate list is NEVER shown in Telegram.
    # --------------------------------------------------------

    def candidate_score(trade):

        direction_bonus = 0

        if trade["direction"] == "LONG":
            direction_bonus = 0.1

        return (
            trade.get(
                "risk_percent",
                0
            )
            + direction_bonus
        )

    candidates.sort(
        key=candidate_score,
        reverse=True
    )

    # --------------------------------------------------------
    # OPEN NEW TRADES
    #
    # MAX:
    #   4 total
    #   2 LONG
    #   2 SHORT
    # --------------------------------------------------------

    used_signal_keys = set(
        state.get(
            "last_signal_keys",
            []
        )
    )

    for candidate in candidates:

        if (
            len(state["open_trades"])
            >= MAX_OPEN_TRADES
        ):
            break

        symbol = candidate[
            "symbol"
        ]

        direction = candidate[
            "direction"
        ]

        # ----------------------------------------------------
        # Don't open same symbol twice
        # ----------------------------------------------------

        if symbol_is_open(
            state["open_trades"],
            symbol
        ):
            continue

        # ----------------------------------------------------
        # Direction limits
        # ----------------------------------------------------

        if direction == "LONG":

            if (
                count_direction(
                    state["open_trades"],
                    "LONG"
                )
                >= MAX_LONG_TRADES
            ):
                continue

        else:

            if (
                count_direction(
                    state["open_trades"],
                    "SHORT"
                )
                >= MAX_SHORT_TRADES
            ):
                continue

        # ----------------------------------------------------
        # Duplicate signal protection
        # ----------------------------------------------------

        key = signal_key(
            candidate
        )

        if key in used_signal_keys:
            continue

        # ----------------------------------------------------
        # Open
        # ----------------------------------------------------

        state["open_trades"].append(
            candidate
        )

        used_signal_keys.add(
            key
        )

        print(
            f"[OPEN] "
            f"{symbol} "
            f"{direction} "
            f"Entry={fmt_price(candidate['entry'])} "
            f"SL={fmt_price(candidate['sl'])} "
            f"TP={fmt_price(candidate['tp'])} "
            f"Risk={candidate['risk_percent']:.2f}%"
        )

    # --------------------------------------------------------
    # KEEP SIGNAL HISTORY SMALL
    # --------------------------------------------------------

    state["last_signal_keys"] = list(
        used_signal_keys
    )[-500:]

    state["updated_at"] = iso_now()

    # --------------------------------------------------------
    # SAVE STATE BEFORE REPORT
    # --------------------------------------------------------

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        HISTORY_FILE,
        history
    )

    # --------------------------------------------------------
    # REFRESH LIVE PRICES
    # --------------------------------------------------------

    try:

        live_prices = (
            get_live_prices()
        )

    except Exception as e:

        print(
            f"[WARN] "
            f"Live price refresh failed: {e}"
        )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        state,
        history,
        live_prices,
        scan_stats
    )

    print()
    print(report)
    print()

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    sent = telegram_send(
        report
    )

    if not sent:

        raise RuntimeError(
            "Telegram report was not sent"
        )

    print(
        "[OK] Telegram report sent."
    )


# ============================================================
# FATAL ERROR
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "=" * 70
        )

        print(
            "[FATAL ERROR]"
        )

        print(
            str(e)
        )

        traceback.print_exc()

        # ----------------------------------------------------
        # Try sending error to Telegram
        # ----------------------------------------------------

        try:

            error_message = (
                "<b>🚨 ICHIMOKU SCANNER ERROR</b>\n\n"
                "<code>"
                + html_escape(
                    str(e)
                )
                + "</code>"
            )

            telegram_send(
                error_message
            )

        except Exception:

            pass

        raise
