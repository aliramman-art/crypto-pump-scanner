# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF SCANNER v7.0
# ============================================================
# Kraken Futures
# TOP 100 Markets
#
# TIMEFRAMES:
#   1H  -> Trend
#   30m -> Confirmation
#   15m -> Pullback
#   5m  -> Trigger
#
# CLOSED CANDLES ONLY
# MAX OPEN TRADES = 1
# RR = 1:1
# VIRTUAL TRADING
#
# TELEGRAM:
#   HTML mode
#   Report every execution
#   GitHub Actions should run every 5 minutes
#
# STATE:
#   ichimoku_state.json
#   ichimoku_trade_history.json
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
# TELEGRAM
# ============================================================

def tg_escape(value):
    """
    Escape dynamic text for Telegram HTML.
    """
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

        if not data.get("ok", False):

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
# STATE
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

    return load_json(
        STATE_FILE,
        {
            "open_trade": None,
            "last_signal_key": None,
            "last_run": None
        }
    )


def save_state(state):

    save_json(
        STATE_FILE,
        state
    )


def load_history():

    return load_json(
        HISTORY_FILE,
        []
    )


def save_history(history):

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# MARKETS
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
# CANDLE DATA
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

    resolution = RESOLUTION_MAP.get(
        minutes
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

    candles = []

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

    # ========================================================
    # REMOVE CURRENT OPEN CANDLE
    # ========================================================

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
# INDICATORS
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

    return sum(
        trs[-period:]
    ) / period


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

    # --------------------------------------------------------
    # Current cloud approximation
    # --------------------------------------------------------

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
    future_index = i

    future_a = ich["span_a"][
        future_index
    ]

    future_b = ich["span_b"][
        future_index
    ]

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
# 30m CONFIRMATION
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
# 15m PULLBACK
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
            abs(close - tenkan)
            <= max_distance
        )

        near_kijun = (
            abs(close - kijun)
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
            abs(close - tenkan)
            <= max_distance
        )

        near_kijun = (
            abs(close - kijun)
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
# 5m PRICE ACTION TRIGGER
# ============================================================

def trigger_5m(
    candles,
    direction
):

    if len(candles) < 60:
        return False, "Not enough 5m candles"

    result = calculate_score(
        candles
    )

    if not result:
        return False, "5m Ichimoku unavailable"

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

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

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

        # Tenkan cross
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

        # Bullish engulfing
        bullish_engulfing = (
            previous["close"]
            < previous["open"]
            and close > open_price
            and close >= previous["open"]
            and open_price
            <= previous["close"]
        )

        # Break previous high
        break_high = (
            close > previous_high
        )

        # Momentum
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

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

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
# SWING SL
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

    candles = get_candles(
        symbol,
        5
    )

    if len(candles) < 2:
        return

    # --------------------------------------------------------
    # Timeout
    # --------------------------------------------------------

    age_minutes = (
        time.time()
        - opened_at
    ) / 60.0

    if (
        age_minutes
        >= TRADE_TIMEOUT_MINUTES
    ):

        exit_price = candles[-1][
            "close"
        ]

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

        trade["exit"] = exit_price

        trade["pnl_r"] = pnl_r

        trade["closed_at"] = time.time()

        history.append(
            trade
        )

        state["open_trade"] = None

        log(
            f"Trade timeout: "
            f"{symbol}"
        )

        return

    # --------------------------------------------------------
    # Check latest candle
    # --------------------------------------------------------

    candle = candles[-1]

    high = candle["high"]

    low = candle["low"]

    result = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        hit_sl = (
            low <= sl
        )

        hit_tp = (
            high >= tp
        )

        # Conservative assumption:
        # SL first if both happen in same candle.

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

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif direction == "SHORT":

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

        state["open_trade"] = None

        log(
            f"Trade closed: "
            f"{symbol} "
            f"{reason} "
            f"R={pnl_r:+.2f}"
        )


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
        ) * 100

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
# SCAN ONE MARKET
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
            "stage": "1H",
            "direction": "NONE",
            "reason": "No 1H trend"
        }

    # ========================================================
    # 30m
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
    # 15m
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
    # 5m
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
# CANDIDATE RANKING
# ============================================================

def stage_rank(
    stage
):

    ranks = {
        "VALID": 5,
        "5m": 4,
        "15m": 3,
        "30m": 2,
        "1H": 1
    }

    return ranks.get(
        stage,
        0
    )


def candidate_sort_key(
    item
):

    return (
        stage_rank(
            item.get("stage")
        ),
        abs(
            item.get(
                "score_1h",
                0
            )
        )
        + abs(
            item.get(
                "score_30m",
                0
            )
        )
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    results,
    history,
    state,
    new_signal
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
    # OPEN TRADE
    # ========================================================

    trade = state.get(
        "open_trade"
    )

    if trade:

        lines.append(
            "<b>🔴 OPEN TRADE</b>"
        )

        lines.append(
            f"Symbol: "
            f"<code>{tg_escape(trade['symbol'])}</code>"
        )

        lines.append(
            f"Direction: "
            f"<b>{tg_escape(trade['direction'])}</b>"
        )

        lines.append(
            f"Entry: "
            f"<code>{trade['entry']:.8f}</code>"
        )

        lines.append(
            f"SL: "
            f"<code>{trade['sl']:.8f}</code>"
        )

        lines.append(
            f"TP: "
            f"<code>{trade['tp']:.8f}</code>"
        )

        lines.append(
            f"Trigger: "
            f"<code>{tg_escape(trade.get('trigger_reason', ''))}</code>"
        )

    else:

        lines.append(
            "<b>OPEN TRADE</b> None"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    if new_signal:

        t = new_signal

        lines.append(
            "<b>🚨 NEW SIGNAL</b>"
        )

        lines.append(
            f"<code>{tg_escape(t['symbol'])}</code> "
            f"<b>{tg_escape(t['direction'])}</b>"
        )

        lines.append(
            f"Entry: "
            f"<code>{t['entry']:.8f}</code>"
        )

        lines.append(
            f"SL: "
            f"<code>{t['sl']:.8f}</code>"
        )

        lines.append(
            f"TP: "
            f"<code>{t['tp']:.8f}</code>"
        )

        lines.append(
            f"Trigger: "
            f"<code>{tg_escape(t.get('trigger_reason', ''))}</code>"
        )

    else:

        lines.append(
            "<b>NEW SIGNAL</b> "
            "No new valid MTF setup."
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # TOP CANDIDATES
    # ========================================================

    candidates = sorted(
        results,
        key=candidate_sort_key,
        reverse=True
    )[:5]

    lines.append(
        "<b>TOP 5 CANDIDATES</b>"
    )

    if not candidates:

        lines.append(
            "No candidates."
        )

    else:

        for idx, item in enumerate(
            candidates,
            start=1
        ):

            symbol = tg_escape(
                item.get(
                    "symbol",
                    "?"
                )
            )

            direction = tg_escape(
                item.get(
                    "direction",
                    "NONE"
                )
            )

            score_1h = item.get(
                "score_1h",
                0
            )

            score_30m = item.get(
                "score_30m",
                0
            )

            stage = tg_escape(
                item.get(
                    "stage",
                    "?"
                )
            )

            reason = tg_escape(
                item.get(
                    "reason",
                    ""
                )
            )

            lines.append(
                f"{idx}. "
                f"<code>{symbol}</code> "
                f"{direction} | "
                f"1H {score_1h:+d} | "
                f"30m {score_30m:+d} | "
                f"Stage: {stage}"
            )

            lines.append(
                f"   {reason}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # SCANNER STATUS
    # ========================================================

    total = len(results)

    scanned = total

    valid = sum(
        1
        for x in results
        if x.get("valid")
    )

    rejected_1h = sum(
        1
        for x in results
        if x.get("reject") == "1H"
    )

    rejected_30m = sum(
        1
        for x in results
        if x.get("reject") == "30m"
    )

    rejected_15m = sum(
        1
        for x in results
        if x.get("reject") == "15m"
    )

    rejected_5m = sum(
        1
        for x in results
        if x.get("reject") == "5m"
    )

    data_errors = sum(
        1
        for x in results
        if x.get("reject") == "DATA"
    )

    lines.append(
        "<b>SCANNER STATUS</b>"
    )

    lines.append(
        f"Markets: {total}"
    )

    lines.append(
        f"Scanned: {scanned}"
    )

    lines.append(
        f"Valid: {valid}"
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
        "KRAKEN FUTURES ICHIMOKU MTF v7.0"
    )

    log(
        "=========================================="
    )

    log(
        "Checking open trade..."
    )

    # ========================================================
    # UPDATE OPEN TRADE
    # ========================================================

    check_open_trade(
        state,
        history
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

        report = (
            "<b>📡 KRAKEN FUTURES "
            "ICHIMOKU REPORT</b>\n\n"
            "❌ Could not load markets."
        )

        telegram_send(
            report
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

            results.append(
                {
                    "valid": False,
                    "reject": "DATA",
                    "symbol": symbol,
                    "direction": "NONE",
                    "score_1h": 0,
                    "score_30m": 0,
                    "stage": "ERROR",
                    "reason": str(exc)
                }
            )

    # ========================================================
    # FIND VALID SIGNALS
    # ========================================================

    valid_candidates = [
        x
        for x in results
        if x.get("valid")
    ]

    valid_candidates.sort(
        key=candidate_sort_key,
        reverse=True
    )

    new_signal = None

    # ========================================================
    # ONLY ONE OPEN TRADE
    # ========================================================

    if (
        state.get("open_trade")
        is None
        and valid_candidates
    ):

        candidate = (
            valid_candidates[0]
        )

        trade = candidate[
            "trade"
        ]

        signal_key = (
            f"{trade['symbol']}_"
            f"{trade['direction']}_"
            f"{int(trade['opened_at'] // 300)}"
        )

        last_key = state.get(
            "last_signal_key"
        )

        if signal_key != last_key:

            state[
                "open_trade"
            ] = trade

            state[
                "last_signal_key"
            ] = signal_key

            new_signal = trade

            log(
                "NEW SIGNAL: "
                f"{trade['symbol']} "
                f"{trade['direction']}"
            )

        else:

            log(
                "Valid setup found, "
                "but duplicate signal key."
            )

    elif state.get(
        "open_trade"
    ):

        log(
            "Open trade exists. "
            "No new trade allowed."
        )

    else:

        log(
            "No valid signal."
        )

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
    # REPORT
    # ========================================================

    report = build_report(
        results,
        history,
        state,
        new_signal
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

        # Make GitHub Actions visibly fail.
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

        # Try Telegram one final time
        try:

            fatal_report = (
                "<b>🚨 ICHIMOKU SCANNER ERROR</b>\n\n"
                f"<code>{tg_escape(type(exc).__name__)}</code>\n"
                f"<code>{tg_escape(str(exc))}</code>"
            )

            telegram_send(
                fatal_report
            )

        except Exception:
            pass

        raise
