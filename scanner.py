# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v11.0
# ============================================================
#
# TOP 100 MARKET SCANNER
#
# SELECTS:
#   BEST 2 LONG
#   BEST 2 SHORT
#
# RULES:
#   - Maximum 4 open trades
#   - Maximum 2 LONG
#   - Maximum 2 SHORT
#   - One trade per symbol
#   - Full TOP 100 scan before ranking
#   - Minimum Entry -> SL distance = 0.50%
#   - RR = 1:1
#   - 1H trend
#   - 30M confirmation
#   - 15M pullback
#   - 5M trigger
#   - Closed candles only
#   - Persistent open trades
#   - Persistent trade history
#   - Live P/L
#   - TP/SL monitoring
#   - 4 hour timeout
#   - Telegram plain text
#
# ENV:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
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

CHART_URL = (
    BASE_URL +
    "/api/charts/v1/trade/{symbol}/{resolution}"
)

TELEGRAM_URL = (
    "https://api.telegram.org/bot{token}/sendMessage"
)

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TOP_MARKETS = 100

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

MIN_RISK_PERCENT = 0.50

RR = 1.0

MAX_HOLD_MINUTES = 240

CANDLE_LIMIT = 220

REQUEST_TIMEOUT = 20

TELEGRAM_MAX_LENGTH = 3900

PULLBACK_ATR_MULTIPLIER = 1.5


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 KrakenIchimokuScanner/11.0"
})


# ============================================================
# TELEGRAM ENV
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def unix_now():
    return int(time.time())


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        if value is None:
            return default

        return float(value)

    except Exception:
        return default


def round_price(value):
    if value is None:
        return None

    return round(float(value), 4)


def format_price(value):
    if value is None:
        return "-"

    return (
        f"{float(value):.4f}"
        .rstrip("0")
        .rstrip(".")
    )


def clamp(value, low, high):
    return max(low, min(high, value))


# ============================================================
# JSON STATE
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

    except Exception as e:

        print(
            f"[WARN] Cannot load "
            f"{filename}: {e}"
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

    os.replace(
        tmp,
        filename
    )


def load_state():

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(state, dict):
        state = {}

    if "open_trades" not in state:
        state["open_trades"] = []

    return state


def save_state(state):

    state["updated_at"] = now_iso()

    save_json(
        STATE_FILE,
        state
    )


def load_history():

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(history, list):
        history = []

    return history


def save_history(history):

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# HTTP GET
# ============================================================

def http_get(url, params=None):

    for attempt in range(3):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            return response.json()

        except Exception as e:

            print(
                f"[WARN] GET attempt "
                f"{attempt + 1}/3 failed: "
                f"{e}"
            )

            if attempt < 2:
                time.sleep(1.5)

    return None


# ============================================================
# MARKETS
# ============================================================

def get_markets():

    data = http_get(
        INSTRUMENTS_URL
    )

    if not data:
        return []

    instruments = data.get(
        "instruments",
        []
    )

    result = []

    for item in instruments:

        symbol = (
            item.get("symbol")
            or item.get("instrument")
        )

        if not symbol:
            continue

        symbol = str(symbol)

        if not (
            symbol.startswith("PF_")
            or symbol.startswith("PI_")
        ):
            continue

        if item.get(
            "tradeable",
            True
        ) is False:
            continue

        result.append(symbol)

    return result


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = http_get(
        TICKERS_URL
    )

    if not data:
        return {}

    tickers = data.get(
        "tickers",
        []
    )

    result = {}

    for item in tickers:

        symbol = (
            item.get("symbol")
            or item.get("instrument")
        )

        if not symbol:
            continue

        price = (
            safe_float(item.get("last"))
            or safe_float(
                item.get("markPrice")
            )
            or safe_float(
                item.get("price")
            )
        )

        if price is None:
            continue

        volume = (
            safe_float(
                item.get("vol24h")
            )
            or safe_float(
                item.get("volume24h")
            )
            or 0
        )

        result[symbol] = {
            "price": price,
            "volume": volume
        }

    return result


# ============================================================
# TOP 100
# ============================================================

def get_top_markets():

    markets = get_markets()

    tickers = get_tickers()

    if not markets:
        print("[INFO] Markets: 0")
        return [], tickers

    ranked = []

    for symbol in markets:

        ticker = tickers.get(symbol)

        if not ticker:
            continue

        ranked.append(
            (
                symbol,
                ticker.get(
                    "volume",
                    0
                )
            )
        )

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    top = [
        x[0]
        for x in ranked[
            :TOP_MARKETS
        ]
    ]

    print(
        f"[INFO] Tradable markets: "
        f"{len(markets)}"
    )

    print(
        f"[INFO] Scanning TOP "
        f"{len(top)}"
    )

    return top, tickers


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution
):

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = http_get(url)

    if not data:
        return []

    raw = (
        data.get("candles")
        or data.get("data")
        or []
    )

    candles = []

    for item in raw:

        if not isinstance(
            item,
            dict
        ):
            continue

        timestamp = (
            item.get("time")
            or item.get("timestamp")
            or item.get("ts")
        )

        open_price = (
            item.get("open")
            or item.get("o")
        )

        high = (
            item.get("high")
            or item.get("h")
        )

        low = (
            item.get("low")
            or item.get("l")
        )

        close = (
            item.get("close")
            or item.get("c")
        )

        volume = (
            item.get("volume")
            or item.get("v")
            or 0
        )

        timestamp = safe_float(
            timestamp
        )

        open_price = safe_float(
            open_price
        )

        high = safe_float(
            high
        )

        low = safe_float(
            low
        )

        close = safe_float(
            close
        )

        volume = safe_float(
            volume,
            0
        )

        if None in (
            timestamp,
            open_price,
            high,
            low,
            close
        ):
            continue

        candles.append({

            "time": int(timestamp),

            "open": open_price,

            "high": high,

            "low": low,

            "close": close,

            "volume": volume
        })

    candles.sort(
        key=lambda x: x["time"]
    )

    # --------------------------------------------------------
    # REMOVE FORMING CANDLE
    # --------------------------------------------------------

    durations = {
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600
    }

    duration = durations.get(
        resolution,
        300
    )

    if candles:

        last = candles[-1]

        if (
            last["time"] + duration
            > unix_now()
        ):
            candles = candles[:-1]

    return candles[
        -CANDLE_LIMIT:
    ]


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

    for i, candle in enumerate(
        candles
    ):

        if i == 0:

            tr = (
                candle["high"]
                - candle["low"]
            )

        else:

            previous_close = (
                candles[i - 1]["close"]
            )

            tr = max(
                candle["high"]
                - candle["low"],

                abs(
                    candle["high"]
                    - previous_close
                ),

                abs(
                    candle["low"]
                    - previous_close
                )
            )

        trs.append(tr)

    return (
        sum(trs[-period:])
        / period
    )


# ============================================================
# ICHIMOKU
# ============================================================

def midpoint(
    candles,
    period
):

    if len(candles) < period:
        return None

    window = candles[
        -period:
    ]

    high = max(
        x["high"]
        for x in window
    )

    low = min(
        x["low"]
        for x in window
    )

    return (
        high + low
    ) / 2


def ichimoku_data(candles):

    if len(candles) < 60:
        return None

    tenkan = midpoint(
        candles,
        9
    )

    kijun = midpoint(
        candles,
        26
    )

    span_b = midpoint(
        candles,
        52
    )

    if (
        tenkan is None
        or kijun is None
        or span_b is None
    ):
        return None

    span_a = (
        tenkan + kijun
    ) / 2

    previous = candles[:-1]

    previous_tenkan = midpoint(
        previous,
        9
    )

    previous_kijun = midpoint(
        previous,
        26
    )

    close = candles[-1]["close"]

    previous_close = candles[-2]["close"]

    return {

        "close": close,

        "previous_close":
            previous_close,

        "tenkan": tenkan,

        "kijun": kijun,

        "span_a": span_a,

        "span_b": span_b,

        "cloud_top":
            max(
                span_a,
                span_b
            ),

        "cloud_bottom":
            min(
                span_a,
                span_b
            ),

        "previous_tenkan":
            previous_tenkan,

        "previous_kijun":
            previous_kijun
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(candles):

    data = ichimoku_data(
        candles
    )

    if not data:
        return None

    price = data["close"]

    score = 0

    # Price vs cloud
    if price > data["cloud_top"]:
        score += 3

    elif price < data["cloud_bottom"]:
        score -= 3

    # Price vs Tenkan
    if price > data["tenkan"]:
        score += 1

    elif price < data["tenkan"]:
        score -= 1

    # Price vs Kijun
    if price > data["kijun"]:
        score += 2

    elif price < data["kijun"]:
        score -= 2

    # Future cloud
    if data["span_a"] > data["span_b"]:
        score += 2

    elif data["span_a"] < data["span_b"]:
        score -= 2

    # Chikou approximation
    if len(candles) >= 53:

        old_price = (
            candles[-27]["close"]
        )

        if price > old_price:
            score += 2

        elif price < old_price:
            score -= 2

    score = clamp(
        score,
        -10,
        10
    )

    return score


# ============================================================
# 15M PULLBACK
# ============================================================

def pullback_score(
    candles,
    direction
):

    data = ichimoku_data(
        candles
    )

    if not data:
        return None

    atr = calculate_atr(
        candles,
        14
    )

    if not atr or atr <= 0:
        return None

    price = data["close"]

    distance_tenkan = abs(
        price - data["tenkan"]
    )

    distance_kijun = abs(
        price - data["kijun"]
    )

    nearest = min(
        distance_tenkan,
        distance_kijun
    )

    normalized = (
        nearest / atr
    )

    if normalized <= 0.50:
        quality = 10

    elif normalized <= 0.75:
        quality = 9

    elif normalized <= 1.00:
        quality = 8

    elif normalized <= 1.25:
        quality = 7

    elif normalized <= 1.50:
        quality = 6

    else:
        return None

    if direction == "LONG":

        if price < data["cloud_bottom"]:
            return None

        if price < data["kijun"] * 0.985:
            return None

    else:

        if price > data["cloud_top"]:
            return None

        if price > data["kijun"] * 1.015:
            return None

    return quality


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


def trigger_quality(
    candles,
    direction
):

    if len(candles) < 10:
        return None

    data = ichimoku_data(
        candles
    )

    if not data:
        return None

    current = candles[-1]
    previous = candles[-2]

    bullish = (
        current["close"]
        > current["open"]
    )

    bearish = (
        current["close"]
        < current["open"]
    )

    if direction == "LONG" and not bullish:
        return None

    if direction == "SHORT" and not bearish:
        return None

    points = 0

    # --------------------------------------------------------
    # Tenkan cross
    # --------------------------------------------------------

    if (
        data["previous_tenkan"]
        is not None
    ):

        if direction == "LONG":

            if (
                previous["close"]
                <= data["previous_tenkan"]
                and
                current["close"]
                > data["tenkan"]
            ):
                points += 3

        else:

            if (
                previous["close"]
                >= data["previous_tenkan"]
                and
                current["close"]
                < data["tenkan"]
            ):
                points += 3

    # --------------------------------------------------------
    # Kijun cross
    # --------------------------------------------------------

    if (
        data["previous_kijun"]
        is not None
    ):

        if direction == "LONG":

            if (
                previous["close"]
                <= data["previous_kijun"]
                and
                current["close"]
                > data["kijun"]
            ):
                points += 2

        else:

            if (
                previous["close"]
                >= data["previous_kijun"]
                and
                current["close"]
                < data["kijun"]
            ):
                points += 2

    # --------------------------------------------------------
    # Engulfing
    # --------------------------------------------------------

    if direction == "LONG":

        if bullish_engulfing(
            previous,
            current
        ):
            points += 3

    else:

        if bearish_engulfing(
            previous,
            current
        ):
            points += 3

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    previous_high = max(
        x["high"]
        for x in candles[-5:-1]
    )

    previous_low = min(
        x["low"]
        for x in candles[-5:-1]
    )

    if direction == "LONG":

        if current["close"] > previous_high:
            points += 3

    else:

        if current["close"] < previous_low:
            points += 3

    # --------------------------------------------------------
    # Momentum
    # --------------------------------------------------------

    candle_range = (
        current["high"]
        - current["low"]
    )

    body = abs(
        current["close"]
        - current["open"]
    )

    if candle_range > 0:

        body_ratio = (
            body / candle_range
        )

        if body_ratio >= 0.55:
            points += 2

        if body_ratio >= 0.75:
            points += 1

    # --------------------------------------------------------
    # Minimum trigger
    # --------------------------------------------------------

    if points < 3:
        return None

    return min(
        points,
        10
    )


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def calculate_trade_levels(
    candles,
    direction
):

    if len(candles) < 15:
        return None

    entry = candles[-1]["close"]

    structure = candles[
        -11:-1
    ]

    if direction == "LONG":

        swing_low = min(
            x["low"]
            for x in structure
        )

        sl = swing_low

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

        swing_high = max(
            x["high"]
            for x in structure
        )

        sl = swing_high

        risk = (
            sl - entry
        )

        if risk <= 0:
            return None

        tp = (
            entry
            - risk * RR
        )

    risk_percent = (
        abs(entry - sl)
        / entry
        * 100
    )

    # ========================================================
    # HARD 0.50% FILTER
    # ========================================================

    if risk_percent < MIN_RISK_PERCENT:

        return None

    return {

        "entry":
            round_price(entry),

        "sl":
            round_price(sl),

        "tp":
            round_price(tp),

        "risk_percent":
            round(
                risk_percent,
                4
            )
    }


# ============================================================
# RISK QUALITY
# ============================================================

def risk_quality(
    risk_percent
):

    if risk_percent is None:
        return 0

    # Best zone around 0.5 - 1.5%
    if (
        risk_percent >= 0.50
        and risk_percent <= 1.00
    ):
        return 10

    if (
        risk_percent > 1.00
        and risk_percent <= 1.50
    ):
        return 9

    if (
        risk_percent > 1.50
        and risk_percent <= 2.00
    ):
        return 7

    if (
        risk_percent > 2.00
        and risk_percent <= 3.00
    ):
        return 5

    if risk_percent > 3.00:
        return 3

    return 0


# ============================================================
# FINAL RANK SCORE
# ============================================================

def final_rank_score(
    score_1h,
    score_30m,
    score_15m,
    trigger,
    risk_score,
    direction
):

    if direction == "LONG":

        trend_1h = (
            score_1h / 10
        )

        trend_30m = (
            score_30m / 10
        )

    else:

        trend_1h = (
            abs(score_1h) / 10
        )

        trend_30m = (
            abs(score_30m) / 10
        )

    pullback = (
        score_15m / 10
    )

    trigger_score = (
        trigger / 10
    )

    risk = (
        risk_score / 10
    )

    total = (
        trend_1h * 30
        +
        trend_30m * 25
        +
        pullback * 20
        +
        trigger_score * 15
        +
        risk * 10
    )

    return round(
        total,
        2
    )


# ============================================================
# CANDIDATE
# ============================================================

def build_candidate(
    symbol,
    direction,
    candles_1h,
    candles_30m,
    candles_15m,
    candles_5m
):

    score_1h = ichimoku_score(
        candles_1h
    )

    score_30m = ichimoku_score(
        candles_30m
    )

    if score_1h is None:
        return None

    if score_30m is None:
        return None

    # --------------------------------------------------------
    # HARD TREND FILTER
    # --------------------------------------------------------

    if direction == "LONG":

        if score_1h < 6:
            return None

        if score_30m < 5:
            return None

    else:

        if score_1h > -6:
            return None

        if score_30m > -5:
            return None

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    pb_score = pullback_score(
        candles_15m,
        direction
    )

    if pb_score is None:
        return None

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    trigger = trigger_quality(
        candles_5m,
        direction
    )

    if trigger is None:
        return None

    # --------------------------------------------------------
    # LEVELS
    # --------------------------------------------------------

    levels = calculate_trade_levels(
        candles_5m,
        direction
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # if risk < 0.50%, candidate is rejected
    # --------------------------------------------------------

    if levels is None:
        return None

    risk_percent = levels[
        "risk_percent"
    ]

    risk_score = risk_quality(
        risk_percent
    )

    total_score = final_rank_score(
        score_1h,
        score_30m,
        pb_score,
        trigger,
        risk_score,
        direction
    )

    return {

        "symbol":
            symbol,

        "direction":
            direction,

        "score":
            total_score,

        "score_1h":
            score_1h,

        "score_30m":
            score_30m,

        "score_15m":
            pb_score,

        "trigger":
            trigger,

        "risk_score":
            risk_score,

        "risk_percent":
            risk_percent,

        "entry":
            levels["entry"],

        "sl":
            levels["sl"],

        "tp":
            levels["tp"],

        "candle_time":
            candles_5m[-1]["time"]
    }


# ============================================================
# TRADE ID
# ============================================================

def trade_id(
    symbol,
    direction,
    candle_time
):

    return (
        f"{symbol}|"
        f"{direction}|"
        f"{candle_time}"
    )


# ============================================================
# OPEN TRADE OBJECT
# ============================================================

def candidate_to_trade(
    candidate
):

    return {

        "id":
            trade_id(
                candidate["symbol"],
                candidate["direction"],
                candidate["candle_time"]
            ),

        "symbol":
            candidate["symbol"],

        "direction":
            candidate["direction"],

        "entry":
            candidate["entry"],

        "sl":
            candidate["sl"],

        "tp":
            candidate["tp"],

        "risk_percent":
            candidate["risk_percent"],

        "score":
            candidate["score"],

        "score_1h":
            candidate["score_1h"],

        "score_30m":
            candidate["score_30m"],

        "score_15m":
            candidate["score_15m"],

        "trigger":
            candidate["trigger"],

        "opened_at":
            now_iso(),

        "opened_timestamp":
            unix_now(),

        "entry_candle_time":
            candidate["candle_time"],

        "status":
            "OPEN"
    }


# ============================================================
# EXISTING TRADE CHECK
# ============================================================

def symbol_open(
    open_trades,
    symbol
):

    return any(
        x.get("symbol") == symbol
        for x in open_trades
    )


def direction_counts(
    open_trades
):

    long_count = 0
    short_count = 0

    for trade in open_trades:

        if trade.get(
            "direction"
        ) == "LONG":

            long_count += 1

        elif trade.get(
            "direction"
        ) == "SHORT":

            short_count += 1

    return (
        long_count,
        short_count
    )


# ============================================================
# DUPLICATE HISTORY
# ============================================================

def already_traded(
    history,
    candidate
):

    wanted = trade_id(
        candidate["symbol"],
        candidate["direction"],
        candidate["candle_time"]
    )

    for item in history:

        if item.get("id") == wanted:
            return True

    return False


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    reason
):

    entry = float(
        trade["entry"]
    )

    direction = trade[
        "direction"
    ]

    if direction == "LONG":

        pnl = (
            exit_price - entry
        ) / entry * 100

    else:

        pnl = (
            entry - exit_price
        ) / entry * 100

    if reason == "TP":

        result = "WIN"

    elif reason == "SL":

        result = "LOSS"

    else:

        result = (
            "WIN"
            if pnl > 0
            else
            "LOSS"
            if pnl < 0
            else
            "TIMEOUT"
        )

    closed = dict(
        trade
    )

    closed.update({

        "status":
            "CLOSED",

        "exit":
            round_price(
                exit_price
            ),

        "closed_at":
            now_iso(),

        "close_reason":
            reason,

        "result":
            result,

        "pnl_percent":
            round(
                pnl,
                4
            )
    })

    return closed


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    open_trades,
    history,
    tickers
):

    remaining = []

    for trade in open_trades:

        symbol = trade[
            "symbol"
        ]

        ticker = tickers.get(
            symbol
        )

        if not ticker:

            remaining.append(
                trade
            )

            continue

        price = ticker[
            "price"
        ]

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        reason = None

        # ----------------------------------------------------
        # TP / SL
        # ----------------------------------------------------

        if direction == "LONG":

            if price <= sl:

                reason = "SL"

            elif price >= tp:

                reason = "TP"

        else:

            if price >= sl:

                reason = "SL"

            elif price <= tp:

                reason = "TP"

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        opened = safe_float(
            trade.get(
                "opened_timestamp"
            )
        )

        if (
            reason is None
            and opened is not None
        ):

            age_minutes = (
                unix_now()
                - opened
            ) / 60

            if (
                age_minutes
                >= MAX_HOLD_MINUTES
            ):

                reason = "TIMEOUT"

        # ----------------------------------------------------
        # CLOSE
        # ----------------------------------------------------

        if reason:

            closed = close_trade(
                trade,
                price,
                reason
            )

            history.append(
                closed
            )

            print(
                f"[CLOSED] "
                f"{symbol} "
                f"{direction} "
                f"{reason} "
                f"{closed['pnl_percent']:+.2f}%"
            )

        else:

            remaining.append(
                trade
            )

    return (
        remaining,
        history
    )


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(
    symbol
):

    try:

        candles_1h = get_candles(
            symbol,
            "1h"
        )

        if len(candles_1h) < 80:
            return []

        candles_30m = get_candles(
            symbol,
            "30m"
        )

        if len(candles_30m) < 80:
            return []

        candles_15m = get_candles(
            symbol,
            "15m"
        )

        if len(candles_15m) < 80:
            return []

        candles_5m = get_candles(
            symbol,
            "5m"
        )

        if len(candles_5m) < 80:
            return []

        candidates = []

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        long_candidate = build_candidate(
            symbol,
            "LONG",
            candles_1h,
            candles_30m,
            candles_15m,
            candles_5m
        )

        if long_candidate:

            candidates.append(
                long_candidate
            )

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        short_candidate = build_candidate(
            symbol,
            "SHORT",
            candles_1h,
            candles_30m,
            candles_15m,
            candles_5m
        )

        if short_candidate:

            candidates.append(
                short_candidate
            )

        return candidates

    except Exception as e:

        print(
            f"[WARN] "
            f"{symbol}: {e}"
        )

        return []


# ============================================================
# FULL TOP 100 RANKING
# ============================================================

def scan_all_markets(
    markets,
    open_trades,
    history
):

    long_candidates = []

    short_candidates = []

    print("")
    print(
        "=========================================="
    )

    print(
        "SCANNING FULL TOP 100"
    )

    print(
        "=========================================="
    )

    for index, symbol in enumerate(
        markets,
        start=1
    ):

        # Existing symbol cannot open again
        if symbol_open(
            open_trades,
            symbol
        ):
            continue

        print(
            f"[SCAN] "
            f"{index}/{len(markets)} "
            f"{symbol}"
        )

        candidates = scan_symbol(
            symbol
        )

        for candidate in candidates:

            if already_traded(
                history,
                candidate
            ):
                continue

            if candidate[
                "direction"
            ] == "LONG":

                long_candidates.append(
                    candidate
                )

            else:

                short_candidates.append(
                    candidate
                )

    # --------------------------------------------------------
    # SORT
    # --------------------------------------------------------

    long_candidates.sort(
        key=lambda x: (
            x["score"],
            x["score_1h"],
            x["score_30m"],
            x["trigger"]
        ),
        reverse=True
    )

    short_candidates.sort(
        key=lambda x: (
            x["score"],
            abs(x["score_1h"]),
            abs(x["score_30m"]),
            x["trigger"]
        ),
        reverse=True
    )

    return (
        long_candidates,
        short_candidates
    )


# ============================================================
# SELECT BEST TRADES
# ============================================================

def select_best_trades(
    long_candidates,
    short_candidates,
    open_trades
):

    selected = []

    long_count, short_count = (
        direction_counts(
            open_trades
        )
    )

    total_count = len(
        open_trades
    )

    # ========================================================
    # BEST LONGS
    # ========================================================

    for candidate in long_candidates:

        if (
            total_count
            >= MAX_OPEN_TRADES
        ):
            break

        if (
            long_count
            >= MAX_LONG_TRADES
        ):
            break

        symbol = candidate[
            "symbol"
        ]

        if symbol_open(
            open_trades + selected,
            symbol
        ):
            continue

        selected.append(
            candidate
        )

        long_count += 1

        total_count += 1

    # ========================================================
    # BEST SHORTS
    # ========================================================

    for candidate in short_candidates:

        if (
            total_count
            >= MAX_OPEN_TRADES
        ):
            break

        if (
            short_count
            >= MAX_SHORT_TRADES
        ):
            break

        symbol = candidate[
            "symbol"
        ]

        if symbol_open(
            open_trades + selected,
            symbol
        ):
            continue

        selected.append(
            candidate
        )

        short_count += 1

        total_count += 1

    return selected


# ============================================================
# LIVE DATA
# ============================================================

def live_trade(
    trade,
    tickers
):

    ticker = tickers.get(
        trade["symbol"]
    )

    if not ticker:
        return None

    price = ticker[
        "price"
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

    direction = trade[
        "direction"
    ]

    if direction == "LONG":

        pnl = (
            price - entry
        ) / entry * 100

        tp_distance = (
            tp - price
        ) / price * 100

        sl_distance = (
            price - sl
        ) / price * 100

    else:

        pnl = (
            entry - price
        ) / entry * 100

        tp_distance = (
            price - tp
        ) / price * 100

        sl_distance = (
            sl - price
        ) / price * 100

    return {

        "price":
            price,

        "pnl":
            pnl,

        "tp_distance":
            tp_distance,

        "sl_distance":
            sl_distance
    }


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
        for x in history
        if x.get(
            "result"
        ) == "WIN"
    )

    losses = sum(
        1
        for x in history
        if x.get(
            "result"
        ) == "LOSS"
    )

    timeout = sum(
        1
        for x in history
        if x.get(
            "result"
        ) == "TIMEOUT"
    )

    pnl = sum(
        safe_float(
            x.get(
                "pnl_percent"
            ),
            0
        )
        for x in history
    )

    decided = (
        wins + losses
    )

    win_rate = (
        wins / decided * 100
        if decided > 0
        else 0
    )

    return {

        "trades":
            trades,

        "wins":
            wins,

        "losses":
            losses,

        "timeout":
            timeout,

        "pnl":
            pnl,

        "win_rate":
            win_rate
    }


# ============================================================
# REPORT
# ============================================================

def build_report(
    open_trades,
    history,
    tickers
):

    perf = performance(
        history
    )

    lines = []

    lines.append(
        "📡 CRYPTO ICHIMOKU REPORT"
    )

    lines.append(
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    lines.append(
        "⏱ 5m CLOSED | TOP 100"
    )

    lines.append(
        "🤖 MTF ICHIMOKU | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/4)"
    )

    if not open_trades:

        lines.append(
            "No open trades"
        )

    else:

        for number, trade in enumerate(
            open_trades,
            start=1
        ):

            live = live_trade(
                trade,
                tickers
            )

            if live:

                current = live[
                    "price"
                ]

                pnl = live[
                    "pnl"
                ]

                tp_distance = live[
                    "tp_distance"
                ]

                sl_distance = live[
                    "sl_distance"
                ]

            else:

                current = trade[
                    "entry"
                ]

                pnl = 0

                tp_distance = 0

                sl_distance = 0

            direction = trade[
                "direction"
            ]

            emoji = (
                "🟢"
                if direction == "LONG"
                else
                "🔴"
            )

            lines.append("")

            lines.append(
                f"{number}. "
                f"{trade['symbol']} "
                f"{emoji} "
                f"{direction}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(trade['entry'])}"
            )

            lines.append(
                f"Now: "
                f"{format_price(current)}"
            )

            lines.append(
                f"SL: "
                f"{format_price(trade['sl'])} "
                f"({sl_distance:+.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{format_price(trade['tp'])} "
                f"({tp_distance:+.2f}%)"
            )

            lines.append(
                f"P/L: {pnl:+.2f}%"
            )

            lines.append(
                f"Score: "
                f"{trade.get('score', 0):.1f}"
            )

            lines.append(
                f"Risk: "
                f"{trade.get('risk_percent', 0):.2f}%"
            )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append("")

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 PERFORMANCE"
    )

    lines.append(
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['timeout']}"
    )

    lines.append(
        f"🏆 WR: "
        f"{perf['win_rate']:.1f}%"
    )

    lines.append(
        f"💰 Total P/L: "
        f"{perf['pnl']:+.2f}%"
    )

    # --------------------------------------------------------
    # LIMITS
    # --------------------------------------------------------

    longs, shorts = (
        direction_counts(
            open_trades
        )
    )

    lines.append("")

    lines.append(
        f"⚙️ LIMITS: "
        f"LONG {longs}/2 | "
        f"SHORT {shorts}/2 | "
        f"TOTAL {len(open_trades)}/4"
    )

    lines.append(
        "🛡 Minimum Risk: "
        f"{MIN_RISK_PERCENT:.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    return "\n".join(
        lines
    )


# ============================================================
# TELEGRAM MESSAGE SPLIT
# ============================================================

def split_message(
    text,
    max_length=TELEGRAM_MAX_LENGTH
):

    if len(text) <= max_length:
        return [text]

    chunks = []

    current = ""

    for line in text.splitlines(
        keepends=True
    ):

        if (
            len(current)
            + len(line)
            <= max_length
        ):

            current += line

        else:

            if current:

                chunks.append(
                    current.rstrip()
                )

            while len(line) > max_length:

                chunks.append(
                    line[:max_length]
                )

                line = line[
                    max_length:
                ]

            current = line

    if current:
        chunks.append(
            current.rstrip()
        )

    return chunks


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN "
            "is missing"
        )

    if not TELEGRAM_CHAT_ID:

        raise RuntimeError(
            "TELEGRAM_CHAT_ID "
            "is missing"
        )

    url = TELEGRAM_URL.format(
        token=TELEGRAM_BOT_TOKEN
    )

    chunks = split_message(
        message
    )

    for index, chunk in enumerate(
        chunks,
        start=1
    ):

        payload = {

            "chat_id":
                TELEGRAM_CHAT_ID,

            "text":
                chunk,

            "disable_web_page_preview":
                True
        }

        response = SESSION.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT
        )

        try:

            data = response.json()

        except Exception:

            data = {
                "ok": False,
                "description":
                    response.text
            }

        if (
            response.status_code != 200
            or not data.get(
                "ok",
                False
            )
        ):

            raise RuntimeError(
                "Telegram error "
                f"{response.status_code}: "
                f"{data.get('description', response.text)}"
            )

        print(
            f"[TELEGRAM] "
            f"{index}/{len(chunks)} sent"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("")
    print(
        "============================================================"
    )
    print(
        "KRAKEN FUTURES ICHIMOKU TOP RANKER v11.0"
    )
    print(
        "BEST 2 LONG + BEST 2 SHORT"
    )
    print(
        "MINIMUM RISK = 0.50%"
    )
    print(
        "============================================================"
    )

    # --------------------------------------------------------
    # LOAD STATE
    # --------------------------------------------------------

    state = load_state()

    history = load_history()

    open_trades = state.get(
        "open_trades",
        []
    )

    # --------------------------------------------------------
    # GET TOP MARKETS + TICKERS
    # --------------------------------------------------------

    markets, tickers = (
        get_top_markets()
    )

    if not markets:

        raise RuntimeError(
            "No markets received"
        )

    if not tickers:

        raise RuntimeError(
            "No ticker data received"
        )

    # --------------------------------------------------------
    # MONITOR EXISTING TRADES
    # --------------------------------------------------------

    print("")
    print(
        "[INFO] Monitoring existing trades..."
    )

    open_trades, history = (
        monitor_open_trades(
            open_trades,
            history,
            tickers
        )
    )

    # --------------------------------------------------------
    # SCAN NEW TRADES
    # --------------------------------------------------------

    if len(open_trades) < MAX_OPEN_TRADES:

        long_candidates, short_candidates = (
            scan_all_markets(
                markets,
                open_trades,
                history
            )
        )

        print("")
        print(
            "============================================================"
        )

        print(
            f"[RANKING] LONG candidates: "
            f"{len(long_candidates)}"
        )

        print(
            f"[RANKING] SHORT candidates: "
            f"{len(short_candidates)}"
        )

        print(
            "============================================================"
        )

        # ----------------------------------------------------
        # SHOW TOP CANDIDATES IN LOG
        # ----------------------------------------------------

        print("")
        print(
            "TOP LONG CANDIDATES:"
        )

        for item in long_candidates[:5]:

            print(
                f"  {item['symbol']} "
                f"Score={item['score']:.2f} "
                f"1H={item['score_1h']} "
                f"30M={item['score_30m']} "
                f"15M={item['score_15m']:.1f} "
                f"Trigger={item['trigger']} "
                f"Risk={item['risk_percent']:.2f}%"
            )

        print("")
        print(
            "TOP SHORT CANDIDATES:"
        )

        for item in short_candidates[:5]:

            print(
                f"  {item['symbol']} "
                f"Score={item['score']:.2f} "
                f"1H={item['score_1h']} "
                f"30M={item['score_30m']} "
                f"15M={item['score_15m']:.1f} "
                f"Trigger={item['trigger']} "
                f"Risk={item['risk_percent']:.2f}%"
            )

        # ----------------------------------------------------
        # SELECT BEST
        # ----------------------------------------------------

        selected = select_best_trades(
            long_candidates,
            short_candidates,
            open_trades
        )

        print("")
        print(
            "============================================================"
        )

        print(
            f"[SELECTED] "
            f"{len(selected)} trades"
        )

        print(
            "============================================================"
        )

        # ----------------------------------------------------
        # OPEN SELECTED
        # ----------------------------------------------------

        for candidate in selected:

            trade = candidate_to_trade(
                candidate
            )

            open_trades.append(
                trade
            )

            print(
                f"[OPEN] "
                f"{trade['symbol']} "
                f"{trade['direction']} "
                f"Score={trade['score']:.2f} "
                f"Entry={trade['entry']} "
                f"SL={trade['sl']} "
                f"TP={trade['tp']} "
                f"Risk={trade['risk_percent']:.2f}%"
            )

    else:

        print(
            "[INFO] 4 open trades already active."
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    state["open_trades"] = (
        open_trades
    )

    save_history(
        history
    )

    save_state(
        state
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        open_trades,
        history,
        tickers
    )

    print("")
    print(report)
    print("")

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    try:

        send_telegram(
            report
        )

    except Exception as e:

        print(
            "[ERROR] Telegram:"
        )

        print(
            str(e)
        )

    print(
        "[INFO] Scanner completed."
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("")
        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        print(
            "[FATAL ERROR]"
        )

        print(
            str(e)
        )

        print(
            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
        )

        traceback.print_exc()

        raise
