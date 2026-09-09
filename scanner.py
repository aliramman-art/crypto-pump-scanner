# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v13.4
# ============================================================
# Kraken Futures
# TOP 100 USD perpetual markets
#
# MTF:
#   1H  = Trend
#   30M = Confirmation / Lock
#   15M = Pullback Structure
#   5M  = Pullback + Reversal Trigger / Entry
#
# CLOSED CANDLES ONLY
#
# v13.4 FIXES:
#   - FIXED STATE FILE PERSISTENCE
#   - FIXED HISTORY FILE PERSISTENCE
#   - MAIN.YML COMPATIBLE FILE NAMES
#   - AUTOMATIC LEGACY STATE MIGRATION
#   - AUTOMATIC LEGACY HISTORY MIGRATION
#   - INCLUDES v13.3 MIGRATION
#   - OPEN TRADES NEVER LOST ON API FAILURE
#   - CLOSED TRADES SAVED BEFORE REMOVAL
#   - CUMULATIVE PERFORMANCE PERSISTS
#   - DUPLICATE TRADE / SETUP PROTECTION
#   - AMBIGUOUS TP/SL HANDLING
#   - CLOSED CANDLE EXIT TRACKING
#   - STRUCTURAL SL
#   - RR 1:1
#   - TOP 100
#   - MAX 4 OPEN TRADES
#   - MAX 2 LONG
#   - MAX 2 SHORT
#   - TELEGRAM ROBUST SEND
#   - TELEGRAM ERROR DIAGNOSTICS
#   - TELEGRAM RETRY
#   - TELEGRAM 4096 CHARACTER CHUNKING
#   - NO MARKDOWN PARSING ERRORS
# ============================================================

import os
import json
import time
import math
import traceback
import requests

from datetime import (
    datetime,
    timezone,
    timedelta
)


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE_URL
    + "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE_URL
    + "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE_URL
    + "/api/charts/v1/trade/{symbol}/{resolution}"
)

STRATEGY_VERSION = "v13.4"


# ============================================================
# IMPORTANT:
# These names MUST match main.yml
# ============================================================

STATE_FILE = "ichimoku_state.json"

HISTORY_FILE = (
    "ichimoku_trade_history.json"
)


# ============================================================
# LEGACY STATE FILES
# ============================================================

LEGACY_STATE_FILES = [

    "ichimoku_state_v13_3.json",

    "ichimoku_state_v13_2.json",

    "ichimoku_state_v13_1.json",

    "ichimoku_state_v13.json",
]


# ============================================================
# LEGACY HISTORY FILES
# ============================================================

LEGACY_HISTORY_FILES = [

    "ichimoku_trade_history_v13_3.json",

    "ichimoku_trade_history_v13_2.json",

    "ichimoku_trade_history_v13_1.json",

    "ichimoku_trade_history_v13.json",
]


# ============================================================
# TRADE LIMITS
# ============================================================

TOP_N = 100

MAX_OPEN_TRADES = 4

MAX_LONG_TRADES = 2

MAX_SHORT_TRADES = 2

RR = 1.0

TIMEOUT_HOURS = 4


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 20

RETRIES = 3

REQUEST_SLEEP = 0.12


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_RETRIES = 3

TELEGRAM_RETRY_SLEEP = 2

TELEGRAM_MAX_LENGTH = 4096


# ============================================================
# STOP DISTANCE
# ============================================================

MIN_STOP_PCT = 0.50

MAX_STOP_PCT = 2.00


# ============================================================
# SIGNAL QUALITY
# ============================================================

MIN_SCORE = 78.0

MIN_TRIGGER_SCORE = 7

MIN_1H_SCORE = 4.0

MIN_30M_SCORE = 3.0

MIN_15M_SCORE = 2.0

MIN_5M_SCORE = 2.0


# ============================================================
# PULLBACK
# ============================================================

PULLBACK_MAX_AGE = 2

PULLBACK_TOUCH_ATR = 0.35

PULLBACK_MAX_DISTANCE_ATR = 1.50


# ============================================================
# CANDLE QUALITY
# ============================================================

MIN_TRIGGER_BODY_ATR = 0.10

MIN_REJECTION_WICK_RATIO = 0.35


# ============================================================
# LIQUIDITY
# ============================================================

MIN_24H_VOLUME = 0.0

MAX_BID_ASK_SPREAD_PCT = 0.80


# ============================================================
# EXECUTION / STATISTICS
# ============================================================

FEE_PCT_PER_SIDE = 0.04

SLIPPAGE_PCT_PER_SIDE = 0.02


# ============================================================
# TIMEFRAMES
# ============================================================

TF_5M = "5m"

TF_15M = "15m"

TF_30M = "30m"

TF_1H = "1h"


RESOLUTION_SECONDS = {

    "1m": 60,

    "5m": 300,

    "15m": 900,

    "30m": 1800,

    "1h": 3600,

    "4h": 14400,

    "12h": 43200,

    "1d": 86400,

    "1w": 604800,
}


# ============================================================
# ICHIMOKU WEIGHTS
# ============================================================

WEIGHT_1H = 0.50

WEIGHT_30M = 0.30

WEIGHT_15M = 0.20


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({

    "User-Agent":
        "Kraken-Ichi-Scanner/13.4"
})


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None
):

    last_error = None

    for attempt in range(
        1,
        RETRIES + 1
    ):

        try:

            response = SESSION.get(

                url,

                params=params,

                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            time.sleep(
                REQUEST_SLEEP
            )

            return data

        except Exception as e:

            last_error = e

            print(
                f"[WARN] GET failed "
                f"{attempt}/{RETRIES}: "
                f"{url} | {e}"
            )

            if attempt < RETRIES:

                time.sleep(
                    1.5 * attempt
                )

    raise RuntimeError(

        f"Request failed after "
        f"{RETRIES} attempts: "
        f"{url} | {last_error}"
    )


# ============================================================
# HELPERS
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def now_iso():

    return now_utc().isoformat()


def safe_float(
    value,
    default=0.0
):

    try:

        return float(value)

    except Exception:

        return default


def clamp(
    value,
    low,
    high
):

    return max(
        low,
        min(high, value)
    )


def display_symbol(symbol):

    symbol = str(
        symbol or ""
    )

    if symbol.upper().startswith(
        "PF_"
    ):

        return symbol[3:]

    return symbol


def normalize_timestamp(value):

    try:

        ts = float(value)

        if ts > 10_000_000_000:

            ts /= 1000.0

        return int(ts)

    except Exception:

        return 0


# ============================================================
# JSON FILE HELPERS
# ============================================================

def load_json_file(
    path,
    default=None
):

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

        print(
            f"[WARN] Cannot load "
            f"{path}: {e}"
        )

        return default


def save_json_file(
    path,
    data
):

    tmp = path + ".tmp"

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
        path
    )


# ============================================================
# DEFAULT STATE
# ============================================================

def default_state():

    return {

        "strategy_version":
            STRATEGY_VERSION,

        "updated_at":
            now_iso(),

        "open_trades":
            [],

        "used_setups":
            {},
    }


# ============================================================
# STATE MIGRATION
# ============================================================

def load_state():

    state = load_json_file(

        STATE_FILE,

        None
    )

    # --------------------------------------------------------
    # Current state missing
    # --------------------------------------------------------

    if state is None:

        for legacy in (
            LEGACY_STATE_FILES
        ):

            legacy_state = load_json_file(

                legacy,

                None
            )

            if isinstance(
                legacy_state,
                dict
            ):

                print(

                    f"[MIGRATE] "
                    f"Loaded legacy state: "
                    f"{legacy} -> "
                    f"{STATE_FILE}"
                )

                state = legacy_state

                break

    # --------------------------------------------------------
    # No valid state
    # --------------------------------------------------------

    if not isinstance(
        state,
        dict
    ):

        print(

            f"[INFO] No valid state "
            f"found. Starting fresh "
            f"{STRATEGY_VERSION}."
        )

        return default_state()

    # --------------------------------------------------------
    # Normalize
    # --------------------------------------------------------

    if not isinstance(

        state.get(
            "open_trades"
        ),

        list
    ):

        state["open_trades"] = []

    if not isinstance(

        state.get(
            "used_setups"
        ),

        dict
    ):

        state["used_setups"] = {}

    # --------------------------------------------------------
    # Preserve open trades
    # --------------------------------------------------------

    normalized_open = []

    for trade in state[
        "open_trades"
    ]:

        if not isinstance(
            trade,
            dict
        ):

            continue

        if not trade.get(
            "symbol"
        ):

            continue

        if trade.get(
            "status"
        ) not in (
            None,
            "OPEN"
        ):

            continue

        trade["status"] = "OPEN"

        trade.setdefault(

            "strategy_version",

            STRATEGY_VERSION
        )

        trade.setdefault(

            "opened_at",

            now_iso()
        )

        trade.setdefault(

            "opened_at_iso",

            trade.get(
                "opened_at"
            )
        )

        trade.setdefault(

            "last_checked_candle",

            trade.get(
                "signal_candle_time",
                0
            )
        )

        trade.setdefault(

            "last_price",

            safe_float(
                trade.get(
                    "entry"
                )
            )
        )

        trade.setdefault(
            "pnl_pct",
            0.0
        )

        normalized_open.append(
            trade
        )

    state[
        "open_trades"
    ] = normalized_open

    # --------------------------------------------------------
    # Version migration
    # --------------------------------------------------------

    old_version = state.get(
        "strategy_version"
    )

    if old_version != (
        STRATEGY_VERSION
    ):

        print(

            f"[MIGRATE] State "
            f"{old_version} -> "
            f"{STRATEGY_VERSION}"
        )

        state[
            "strategy_version"
        ] = STRATEGY_VERSION

    state[
        "updated_at"
    ] = now_iso()

    # --------------------------------------------------------
    # Save immediately
    # --------------------------------------------------------

    save_json_file(

        STATE_FILE,

        state
    )

    print(

        f"[STATE] Loaded "
        f"{len(state['open_trades'])} "
        f"open trade(s)"
    )

    return state


# ============================================================
# SAVE STATE
# ============================================================

def save_state(state):

    state[
        "strategy_version"
    ] = STRATEGY_VERSION

    state[
        "updated_at"
    ] = now_iso()

    save_json_file(

        STATE_FILE,

        state
    )

    print(

        f"[STATE] Saved: "
        f"{STATE_FILE} | "
        f"open="
        f"{len(state.get('open_trades', []))}"
    )


# ============================================================
# LOAD HISTORY
# ============================================================

def load_history():

    history = load_json_file(

        HISTORY_FILE,

        None
    )

    # --------------------------------------------------------
    # Legacy migration
    # --------------------------------------------------------

    if history is None:

        for legacy in (
            LEGACY_HISTORY_FILES
        ):

            legacy_history = load_json_file(

                legacy,

                None
            )

            if isinstance(

                legacy_history,

                list
            ):

                print(

                    f"[MIGRATE] "
                    f"Loaded legacy history: "
                    f"{legacy} -> "
                    f"{HISTORY_FILE}"
                )

                history = legacy_history

                break

    # --------------------------------------------------------
    # Empty
    # --------------------------------------------------------

    if not isinstance(
        history,
        list
    ):

        history = []

    # --------------------------------------------------------
    # Normalize / deduplicate
    # --------------------------------------------------------

    result = []

    seen_ids = set()

    for trade in history:

        if not isinstance(
            trade,
            dict
        ):

            continue

        trade_id = trade.get(
            "id"
        )

        if trade_id:

            if trade_id in seen_ids:

                continue

            seen_ids.add(
                trade_id
            )

        version = str(

            trade.get(
                "strategy_version",
                ""
            )
        )

        # Preserve all v13.x
        if version.startswith(
            "v13"
        ):

            trade[
                "strategy_version"
            ] = STRATEGY_VERSION

            result.append(
                trade
            )

    save_json_file(

        HISTORY_FILE,

        result
    )

    print(

        f"[HISTORY] Loaded "
        f"{len(result)} "
        f"historical trade(s)"
    )

    return result


# ============================================================
# SAVE HISTORY
# ============================================================

def save_history(history):

    save_json_file(

        HISTORY_FILE,

        history
    )

    print(

        f"[HISTORY] Saved: "
        f"{HISTORY_FILE} | "
        f"closed={len(history)}"
    )


# ============================================================
# MARKET DATA
# ============================================================

def get_instruments():

    data = http_get(
        INSTRUMENTS_URL
    )

    if isinstance(
        data,
        dict
    ):

        return (

            data.get(
                "instruments"
            )

            or data.get(
                "data"
            )

            or []
        )

    if isinstance(
        data,
        list
    ):

        return data

    return []


# ============================================================
# MARKET LIST
# ============================================================

def build_market_list():

    instruments = get_instruments()

    markets = []

    for item in instruments:

        if not isinstance(
            item,
            dict
        ):

            continue

        symbol = (

            item.get(
                "symbol"
            )

            or item.get(
                "instrument"
            )

            or ""
        )

        symbol = str(
            symbol
        )

        if not symbol:

            continue

        upper = symbol.upper()

        if not upper.startswith(
            "PF_"
        ):

            continue

        if "USD" not in upper:

            continue

        markets.append({

            "symbol":
                symbol,

            "tick_size":
                safe_float(

                    item.get(
                        "tickSize"
                    )

                    or item.get(
                        "tick_size"
                    )

                    or item.get(
                        "priceIncrement"
                    ),

                    0.0
                ),
        })

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique = {}

    for market in markets:

        unique[
            market["symbol"]
        ] = market

    markets = list(
        unique.values()
    )

    markets.sort(

        key=lambda x:
            x["symbol"]
    )

    markets = markets[
        :TOP_N
    ]

    print(

        f"[MARKETS] Loaded "
        f"{len(markets)} markets"
    )

    return markets


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = http_get(
        TICKERS_URL
    )

    if isinstance(
        data,
        dict
    ):

        raw = (

            data.get(
                "tickers"
            )

            or data.get(
                "data"
            )

            or []
        )

    elif isinstance(
        data,
        list
    ):

        raw = data

    else:

        raw = []

    result = {}

    for item in raw:

        if not isinstance(
            item,
            dict
        ):

            continue

        symbol = str(

            item.get(
                "symbol"
            )

            or item.get(
                "instrument"
            )

            or ""
        )

        if not symbol:

            continue

        result[
            symbol
        ] = item

    return result


# ============================================================
# CANDLES
# ============================================================

def get_candles(

    symbol,

    resolution,

    limit=250
):

    url = CHART_URL.format(

        symbol=symbol,

        resolution=resolution
    )

    data = http_get(

        url,

        params={

            "from": 0,

            "to":
                int(time.time()),
        }
    )

    rows = []

    if isinstance(
        data,
        dict
    ):

        rows = (

            data.get(
                "candles"
            )

            or data.get(
                "data"
            )

            or []
        )

    elif isinstance(
        data,
        list
    ):

        rows = data

    result = []

    for row in rows:

        try:

            if isinstance(
                row,
                dict
            ):

                t = (

                    row.get(
                        "time"
                    )

                    or row.get(
                        "timestamp"
                    )
                )

                o = (

                    row.get(
                        "open"
                    )

                    or row.get(
                        "o"
                    )
                )

                h = (

                    row.get(
                        "high"
                    )

                    or row.get(
                        "h"
                    )
                )

                l = (

                    row.get(
                        "low"
                    )

                    or row.get(
                        "l"
                    )
                )

                c = (

                    row.get(
                        "close"
                    )

                    or row.get(
                        "c"
                    )
                )

                v = (

                    row.get(
                        "volume"
                    )

                    or row.get(
                        "v"
                    )

                    or 0
                )

            else:

                if len(row) < 5:

                    continue

                t = row[0]

                o = row[1]

                h = row[2]

                l = row[3]

                c = row[4]

                v = (

                    row[5]

                    if len(row) > 5

                    else 0
                )

            result.append({

                "time":
                    normalize_timestamp(t),

                "open":
                    safe_float(o),

                "high":
                    safe_float(h),

                "low":
                    safe_float(l),

                "close":
                    safe_float(c),

                "volume":
                    safe_float(v),
            })

        except Exception:

            continue

    result.sort(

        key=lambda x:
            x["time"]
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    now_ts = int(
        time.time()
    )

    seconds = RESOLUTION_SECONDS.get(

        resolution,

        300
    )

    closed = []

    for candle in result:

        if (

            candle["time"]
            + seconds
            <= now_ts

        ):

            closed.append(
                candle
            )

    if limit > 0:

        closed = closed[
            -limit:
        ]

    return closed


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_range(c):

    return max(

        c["high"]
        - c["low"],

        1e-12
    )


def candle_body(c):

    return abs(

        c["close"]
        - c["open"]
    )


def upper_wick(c):

    return (

        c["high"]

        - max(
            c["open"],
            c["close"]
        )
    )


def lower_wick(c):

    return (

        min(
            c["open"],
            c["close"]
        )

        - c["low"]
    )


def is_bullish(c):

    return (

        c["close"]
        > c["open"]
    )


def is_bearish(c):

    return (

        c["close"]
        < c["open"]
    )


# ============================================================
# ATR
# ============================================================

def atr(
    candles,
    period=14
):

    if len(candles) < (
        period + 1
    ):

        return 0.0

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        cur = candles[i]

        prev = candles[
            i - 1
        ]

        tr = max(

            cur["high"]
            - cur["low"],

            abs(
                cur["high"]
                - prev["close"]
            ),

            abs(
                cur["low"]
                - prev["close"]
            )
        )

        trs.append(
            tr
        )

    if len(trs) < period:

        return 0.0

    return sum(
        trs[-period:]
    ) / period


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku_values(
    candles
):

    if len(candles) < 52:

        raise RuntimeError(

            "Not enough candles "
            "for Ichimoku"
        )

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

    tenkan = (

        max(highs[-9:])
        + min(lows[-9:])
    ) / 2

    kijun = (

        max(highs[-26:])
        + min(lows[-26:])
    ) / 2

    span_a = (

        tenkan
        + kijun
    ) / 2

    span_b = (

        max(highs[-52:])
        + min(lows[-52:])
    ) / 2

    price = closes[-1]

    cloud_top = max(

        span_a,
        span_b
    )

    cloud_bottom = min(

        span_a,
        span_b
    )

    return {

        "price":
            price,

        "tenkan":
            tenkan,

        "kijun":
            kijun,

        "span_a":
            span_a,

        "span_b":
            span_b,

        "cloud_top":
            cloud_top,

        "cloud_bottom":
            cloud_bottom,
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(
    candles
):

    i = ichimoku_values(
        candles
    )

    price = i[
        "price"
    ]

    tenkan = i[
        "tenkan"
    ]

    kijun = i[
        "kijun"
    ]

    cloud_top = i[
        "cloud_top"
    ]

    cloud_bottom = i[
        "cloud_bottom"
    ]

    score = 0

    # Price vs cloud
    if price > cloud_top:

        score += 4

    elif price < cloud_bottom:

        score -= 4

    # Price vs Kijun
    if price > kijun:

        score += 2

    elif price < kijun:

        score -= 2

    # Tenkan vs Kijun
    if tenkan > kijun:

        score += 2

    elif tenkan < kijun:

        score -= 2

    # Cloud direction
    if i["span_a"] > i["span_b"]:

        score += 2

    elif i["span_a"] < i["span_b"]:

        score -= 2

    score = int(

        clamp(
            score,
            -10,
            10
        )
    )

    i["score"] = score

    return score, i


# ============================================================
# MTF ANALYSIS
# ============================================================

def mtf_analysis(
    symbol
):

    c1 = get_candles(

        symbol,

        TF_1H,

        180
    )

    c30 = get_candles(

        symbol,

        TF_30M,

        180
    )

    c15 = get_candles(

        symbol,

        TF_15M,

        180
    )

    c5 = get_candles(

        symbol,

        TF_5M,

        250
    )

    if min(

        len(c1),
        len(c30),
        len(c15),
        len(c5)

    ) < 90:

        raise RuntimeError(

            "Not enough closed "
            "candles"
        )

    s1, i1 = ichimoku_score(
        c1
    )

    s30, i30 = ichimoku_score(
        c30
    )

    s15, i15 = ichimoku_score(
        c15
    )

    s5, i5 = ichimoku_score(
        c5
    )

    return {

        "1h": {

            "score": s1,

            "info": i1,

            "candles": c1,
        },

        "30m": {

            "score": s30,

            "info": i30,

            "candles": c30,
        },

        "15m": {

            "score": s15,

            "info": i15,

            "candles": c15,
        },

        "5m": {

            "score": s5,

            "info": i5,

            "candles": c5,
        },

        "atr_5m":
            atr(c5, 14),
    }


# ============================================================
# REVERSAL PATTERNS
# ============================================================

def bullish_engulfing(
    a,
    b
):

    return (

        is_bearish(a)

        and is_bullish(b)

        and b["open"]
        <= a["close"]

        and b["close"]
        >= a["open"]
    )


def bearish_engulfing(
    a,
    b
):

    return (

        is_bullish(a)

        and is_bearish(b)

        and b["open"]
        >= a["close"]

        and b["close"]
        <= a["open"]
    )


def hammer(c):

    body = max(

        candle_body(c),

        candle_range(c)
        * 0.02
    )

    return (

        lower_wick(c)
        >= body * 2

        and upper_wick(c)
        <= body * 0.8

        and body
        / candle_range(c)
        <= 0.45
    )


def shooting_star(c):

    body = max(

        candle_body(c),

        candle_range(c)
        * 0.02
    )

    return (

        upper_wick(c)
        >= body * 2

        and lower_wick(c)
        <= body * 0.8

        and body
        / candle_range(c)
        <= 0.45
    )


def bullish_pin_bar(c):

    r = candle_range(c)

    return (

        lower_wick(c)
        >= r * 0.55

        and upper_wick(c)
        <= r * 0.20

        and candle_body(c)
        <= r * 0.35
    )


def bearish_pin_bar(c):

    r = candle_range(c)

    return (

        upper_wick(c)
        >= r * 0.55

        and lower_wick(c)
        <= r * 0.20

        and candle_body(c)
        <= r * 0.35
    )


def morning_star(
    candles
):

    if len(candles) < 3:

        return False

    a, b, c = candles[-3:]

    return (

        is_bearish(a)

        and is_bullish(c)

        and candle_body(b)
        <= candle_range(b)
        * 0.35

        and c["close"]
        > (
            a["open"]
            + a["close"]
        ) / 2
    )


def evening_star(
    candles
):

    if len(candles) < 3:

        return False

    a, b, c = candles[-3:]

    return (

        is_bullish(a)

        and is_bearish(c)

        and candle_body(b)
        <= candle_range(b)
        * 0.35

        and c["close"]
        < (
            a["open"]
            + a["close"]
        ) / 2
    )


def reversal_patterns(
    side,
    candles
):

    if len(candles) < 3:

        return []

    cur = candles[-1]

    prev = candles[-2]

    patterns = []

    if side == "LONG":

        if bullish_engulfing(
            prev,
            cur
        ):

            patterns.append(
                "Bullish Engulfing"
            )

        if (

            hammer(cur)

            and is_bullish(cur)

        ):

            patterns.append(
                "Hammer"
            )

        if bullish_pin_bar(cur):

            patterns.append(
                "Bullish Pin Bar"
            )

        if morning_star(
            candles
        ):

            patterns.append(
                "Morning Star"
            )

    else:

        if bearish_engulfing(
            prev,
            cur
        ):

            patterns.append(
                "Bearish Engulfing"
            )

        if (

            shooting_star(cur)

            and is_bearish(cur)

        ):

            patterns.append(
                "Shooting Star"
            )

        if bearish_pin_bar(cur):

            patterns.append(
                "Bearish Pin Bar"
            )

        if evening_star(
            candles
        ):

            patterns.append(
                "Evening Star"
            )

    return patterns


# ============================================================
# TREND STRUCTURE
# ============================================================

def trend_structure(

    side,

    candles,

    lookback=6
):

    if len(candles) < (
        lookback + 2
    ):

        return False

    w = candles[
        -(lookback + 1):
    ]

    highs = [

        x["high"]

        for x in w
    ]

    lows = [

        x["low"]

        for x in w
    ]

    if side == "LONG":

        return (

            highs[-1]
            >= max(highs[:-1])

            or

            lows[-1]
            > min(lows[:-1])
        )

    return (

        lows[-1]
        <= min(lows[:-1])

        or

        highs[-1]
        < max(highs[:-1])
    )


# ============================================================
# 5M PULLBACK
# ============================================================

def five_minute_pullback(

    side,

    candles
):

    if len(candles) < 8:

        return {
            "valid": False
        }

    a = candles[-3]

    b = candles[-2]

    cur = candles[-1]

    a5 = atr(
        candles,
        14
    )

    if a5 <= 0:

        return {
            "valid": False
        }

    i = ichimoku_values(
        candles
    )

    kijun = i[
        "kijun"
    ]

    tenkan = i[
        "tenkan"
    ]

    touch = False

    reversal = False

    rejection = False

    reclaim = False

    structure_break = (
        trend_structure(
            side,
            candles
        )
    )

    body_ok = (

        candle_body(cur)
        >= a5
        * MIN_TRIGGER_BODY_ATR
    )

    if side == "LONG":

        touch_a = (

            abs(
                a["low"]
                - tenkan
            )
            <= a5
            * PULLBACK_TOUCH_ATR

            or

            abs(
                a["low"]
                - kijun
            )
            <= a5
            * PULLBACK_TOUCH_ATR
        )

        touch_b = (

            abs(
                b["low"]
                - tenkan
            )
            <= a5
            * PULLBACK_TOUCH_ATR

            or

            abs(
                b["low"]
                - kijun
            )
            <= a5
            * PULLBACK_TOUCH_ATR
        )

        touch = (
            touch_a
            or touch_b
        )

        reversal = (
            is_bullish(cur)
        )

        rejection = (

            lower_wick(b)
            >= candle_range(b)
            * MIN_REJECTION_WICK_RATIO
        )

        reclaim = (

            cur["close"] > tenkan

            or

            cur["close"] > kijun
        )

    else:

        touch_a = (

            abs(
                a["high"]
                - tenkan
            )
            <= a5
            * PULLBACK_TOUCH_ATR

            or

            abs(
                a["high"]
                - kijun
            )
            <= a5
            * PULLBACK_TOUCH_ATR
        )

        touch_b = (

            abs(
                b["high"]
                - tenkan
            )
            <= a5
            * PULLBACK_TOUCH_ATR

            or

            abs(
                b["high"]
                - kijun
            )
            <= a5
            * PULLBACK_TOUCH_ATR
        )

        touch = (
            touch_a
            or touch_b
        )

        reversal = (
            is_bearish(cur)
        )

        rejection = (

            upper_wick(b)
            >= candle_range(b)
            * MIN_REJECTION_WICK_RATIO
        )

        reclaim = (

            cur["close"] < tenkan

            or

            cur["close"] < kijun
        )

    # --------------------------------------------------------
    # Pullback must be fresh
    # --------------------------------------------------------

    valid = (

        touch

        and

        reversal
    )

    return {

        "valid":
            valid,

        "touch":
            touch,

        "reversal":
            reversal,

        "rejection":
            rejection,

        "reclaim":
            reclaim,

        "structure_break":
            structure_break,

        "body_ok":
            body_ok,
    }


# ============================================================
# TRIGGER SCORE
# ============================================================

def trigger_score(

    side,

    candles,

    pb
):

    if (

        not pb.get("valid")

        or len(candles) < 3

    ):

        return 0

    cur = candles[-1]

    prev = candles[-2]

    score = 0

    if pb.get("touch"):

        score += 1

    if pb.get("reversal"):

        score += 2

    if pb.get("rejection"):

        score += 1

    if pb.get("reclaim"):

        score += 1

    if pb.get("structure_break"):

        score += 2

    if pb.get("body_ok"):

        score += 1

    if (

        side == "LONG"

        and

        cur["close"]
        > prev["high"]

    ):

        score += 2

    if (

        side == "SHORT"

        and

        cur["close"]
        < prev["low"]

    ):

        score += 2

    return min(
        score,
        10
    )


# ============================================================
# TICK ROUNDING
# ============================================================

def round_to_tick(

    price,

    tick_size
):

    if (

        tick_size
        and tick_size > 0

    ):

        return round(

            math.floor(

                price
                / tick_size
                + 0.5

            )

            * tick_size,

            12
        )

    if price >= 1000:

        return round(
            price,
            2
        )

    if price >= 100:

        return round(
            price,
            3
        )

    if price >= 10:

        return round(
            price,
            4
        )

    if price >= 1:

        return round(
            price,
            5
        )

    if price >= 0.1:

        return round(
            price,
            6
        )

    if price >= 0.01:

        return round(
            price,
            7
        )

    return round(
        price,
        8
    )


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def structural_levels(

    side,

    entry,

    c5,

    c15,

    tick_size
):

    a = atr(
        c5,
        14
    )

    if a <= 0:

        raise RuntimeError(
            "invalid ATR"
        )

    recent5 = c5[-10:]

    recent15 = c15[-4:]

    combined = (
        recent5
        + recent15
    )

    if side == "LONG":

        structural = (

            min(
                x["low"]
                for x in combined
            )

            - a * 0.10
        )

        sl = structural

    else:

        structural = (

            max(
                x["high"]
                for x in combined
            )

            + a * 0.10
        )

        sl = structural

    entry_r = round_to_tick(

        entry,

        tick_size
    )

    sl_r = round_to_tick(

        sl,

        tick_size
    )

    if side == "LONG":

        risk = (

            entry_r
            - sl_r
        )

    else:

        risk = (

            sl_r
            - entry_r
        )

    if risk <= 0:

        raise RuntimeError(
            "invalid structural risk"
        )

    risk_pct = (

        risk
        / entry_r
        * 100
    )

    # --------------------------------------------------------
    # Minimum stop
    # --------------------------------------------------------

    if risk_pct < MIN_STOP_PCT:

        min_dist = (

            entry_r
            * MIN_STOP_PCT
            / 100
        )

        if side == "LONG":

            sl_r = round_to_tick(

                entry_r
                - min_dist,

                tick_size
            )

        else:

            sl_r = round_to_tick(

                entry_r
                + min_dist,

                tick_size
            )

        risk = (

            entry_r - sl_r

            if side == "LONG"

            else

            sl_r - entry_r
        )

        risk_pct = (

            risk
            / entry_r
            * 100
        )

    # --------------------------------------------------------
    # Maximum stop
    # --------------------------------------------------------

    if risk_pct > MAX_STOP_PCT:

        raise RuntimeError(

            f"stop too wide: "
            f"{risk_pct:.2f}%"
        )

    # --------------------------------------------------------
    # TP
    # --------------------------------------------------------

    if side == "LONG":

        tp = round_to_tick(

            entry_r
            + risk * RR,

            tick_size
        )

    else:

        tp = round_to_tick(

            entry_r
            - risk * RR,

            tick_size
        )

    reward = (

        tp - entry_r

        if side == "LONG"

        else

        entry_r - tp
    )

    if reward <= 0:

        raise RuntimeError(
            "invalid TP"
        )

    return (

        entry_r,

        sl_r,

        tp,

        risk,

        reward,

        risk_pct
    )


# ============================================================
# FINAL RANK SCORE
# ============================================================

def final_rank_score(

    side,

    analysis,

    trigger
):

    s1 = analysis[
        "1h"
    ]["score"]

    s30 = analysis[
        "30m"
    ]["score"]

    s15 = analysis[
        "15m"
    ]["score"]

    s5 = analysis[
        "5m"
    ]["score"]

    sign = (

        1

        if side == "LONG"

        else -1
    )

    trend = (

        max(
            sign * s1,
            0
        )

        / 10
        * 35
    )

    confirm = (

        max(
            sign * s30,
            0
        )

        / 10
        * 25
    )

    structure = (

        max(
            sign * s15,
            0
        )

        / 10
        * 20
    )

    trigger_component = (

        trigger
        / 10
        * 15
    )

    five = (

        max(
            sign * s5,
            0
        )

        / 10
        * 5
    )

    return round(

        clamp(

            trend
            + confirm
            + structure
            + trigger_component
            + five,

            0,

            100
        ),

        1
    )


# ============================================================
# CANDIDATE ANALYSIS
# ============================================================

def analyze_candidate(
    market
):

    symbol = market[
        "symbol"
    ]

    analysis = mtf_analysis(
        symbol
    )

    s1 = analysis[
        "1h"
    ]["score"]

    s30 = analysis[
        "30m"
    ]["score"]

    s15 = analysis[
        "15m"
    ]["score"]

    s5 = analysis[
        "5m"
    ]["score"]

    c5 = analysis[
        "5m"
    ]["candles"]

    c15 = analysis[
        "15m"
    ]["candles"]

    entry = c5[
        -1
    ]["close"]

    signal_candle_time = c5[
        -1
    ]["time"]

    for side in (

        "LONG",

        "SHORT"

    ):

        sign = (

            1

            if side == "LONG"

            else -1
        )

        if (

            sign * s1
            < MIN_1H_SCORE

        ):

            continue

        if (

            sign * s30
            < MIN_30M_SCORE

        ):

            continue

        if (

            sign * s15
            < MIN_15M_SCORE

        ):

            continue

        if (

            sign * s5
            < MIN_5M_SCORE

        ):

            continue

        pb = five_minute_pullback(

            side,

            c5
        )

        if not pb.get(
            "valid"
        ):

            continue

        trigger = trigger_score(

            side,

            c5,

            pb
        )

        if trigger < (
            MIN_TRIGGER_SCORE
        ):

            continue

        score = final_rank_score(

            side,

            analysis,

            trigger
        )

        if score < MIN_SCORE:

            continue

        try:

            (

                entry_r,

                sl,

                tp,

                risk,

                reward,

                risk_pct

            ) = structural_levels(

                side,

                entry,

                c5,

                c15,

                market.get(
                    "tick_size",
                    0.0
                )
            )

        except Exception as e:

            print(

                f"[SKIP] "
                f"{symbol} "
                f"{side}: "
                f"levels failed: "
                f"{e}"
            )

            continue

        patterns = reversal_patterns(

            side,

            c5
        )

        setup_key = (

            f"{symbol}|"
            f"{side}|"
            f"{signal_candle_time}"
        )

        return {

            "id": (

                f"{symbol}-"
                f"{side}-"
                f"{signal_candle_time}"
            ),

            "setup_key":
                setup_key,

            "symbol":
                symbol,

            "side":
                side,

            "entry":
                entry_r,

            "sl":
                sl,

            "tp":
                tp,

            "risk":
                risk,

            "reward":
                reward,

            "risk_pct":
                risk_pct,

            "rr":
                RR,

            "score":
                score,

            "trigger":
                trigger,

            "trend":
                s1,

            "reversal":
                s5 - s30,

            "s1h":
                s1,

            "s30":
                s30,

            "s15":
                s15,

            "s5":
                s5,

            "pullback_patterns":
                patterns,

            "signal_candle_time":
                signal_candle_time,

            "opened_at":
                now_iso(),

            "opened_at_iso":
                now_iso(),

            "last_checked_candle":
                signal_candle_time,

            "status":
                "OPEN",

            "last_price":
                entry_r,

            "pnl_pct":
                0.0,

            "strategy_version":
                STRATEGY_VERSION,
        }

    return None


# ============================================================
# SCAN ALL MARKETS
# ============================================================

def scan_all_markets(

    markets,

    state
):

    open_symbols = {

        x.get("symbol")

        for x in state.get(

            "open_trades",

            []
        )
    }

    candidates = []

    for idx, market in enumerate(

        markets,

        start=1
    ):

        symbol = market[
            "symbol"
        ]

        if symbol in open_symbols:

            print(

                f"[{idx}/{len(markets)}] "
                f"{symbol} -> "
                f"already open"
            )

            continue

        try:

            candidate = analyze_candidate(

                market
            )

            if candidate:

                candidates.append(
                    candidate
                )

                print(

                    f"[SIGNAL] "
                    f"{display_symbol(symbol)} "
                    f"{candidate['side']} "
                    f"Score="
                    f"{candidate['score']}"
                )

            else:

                print(

                    f"[{idx}/{len(markets)}] "
                    f"{symbol} -> "
                    f"no signal"
                )

        except Exception as e:

            print(

                f"[WARN] "
                f"{symbol}: {e}"
            )

    return candidates


# ============================================================
# PRUNE USED SETUPS
# ============================================================

def prune_used_setups(
    state
):

    used = state.get(

        "used_setups",

        {}
    )

    if not isinstance(
        used,
        dict
    ):

        used = {}

    cutoff = (

        int(time.time())

        - (

            7
            * 24
            * 3600
        )
    )

    cleaned = {}

    for key, value in used.items():

        try:

            ts = int(
                value
            )

            if ts >= cutoff:

                cleaned[
                    key
                ] = ts

        except Exception:

            continue

    state[
        "used_setups"
    ] = cleaned


# ============================================================
# SELECT BEST TRADES
# ============================================================

def select_best_trades(

    candidates,

    state
):

    open_trades = state.get(

        "open_trades",

        []
    )

    used_symbols = {

        x.get("symbol")

        for x in open_trades
    }

    used_setups = state.get(

        "used_setups",

        {}
    )

    slots_total = max(

        0,

        MAX_OPEN_TRADES
        - len(open_trades)
    )

    slots_long = max(

        0,

        MAX_LONG_TRADES

        - sum(

            x.get("side")
            == "LONG"

            for x in open_trades
        )
    )

    slots_short = max(

        0,

        MAX_SHORT_TRADES

        - sum(

            x.get("side")
            == "SHORT"

            for x in open_trades
        )
    )

    selected = []

    candidates = sorted(

        candidates,

        key=lambda x: (

            x["score"],

            x["trigger"],

            -x["risk_pct"]
        ),

        reverse=True
    )

    for c in candidates:

        if len(selected) >= slots_total:

            break

        if c["symbol"] in used_symbols:

            continue

        if c["setup_key"] in used_setups:

            continue

        if (

            c["side"] == "LONG"

            and

            slots_long <= 0

        ):

            continue

        if (

            c["side"] == "SHORT"

            and

            slots_short <= 0

        ):

            continue

        selected.append(
            c
        )

        used_symbols.add(
            c["symbol"]
        )

        if c["side"] == "LONG":

            slots_long -= 1

        else:

            slots_short -= 1

    return selected


# ============================================================
# ADD SELECTED TRADES
# ============================================================

def add_selected_trades(

    state,

    selected
):

    for trade in selected:

        state[
            "open_trades"
        ].append(
            trade
        )

        state[
            "used_setups"
        ][
            trade["setup_key"]
        ] = int(
            time.time()
        )

        print(

            f"[OPEN] "
            f"{display_symbol(trade['symbol'])} "
            f"{trade['side']} "
            f"Entry={trade['entry']} "
            f"SL={trade['sl']} "
            f"TP={trade['tp']} "
            f"Risk="
            f"{trade['risk_pct']:.2f}% "
            f"RR="
            f"{trade['rr']:.2f}"
        )


# ============================================================
# TRADE PNL
# ============================================================

def calculate_trade_pnl(

    trade,

    exit_price
):

    entry = safe_float(

        trade.get(
            "entry"
        )
    )

    exit_price = safe_float(
        exit_price
    )

    if (

        entry <= 0

        or

        exit_price <= 0

    ):

        return 0.0

    if trade["side"] == "LONG":

        raw = (

            (
                exit_price
                - entry
            )

            / entry
            * 100
        )

    else:

        raw = (

            (
                entry
                - exit_price
            )

            / entry
            * 100
        )

    costs = 2 * (

        FEE_PCT_PER_SIDE
        + SLIPPAGE_PCT_PER_SIDE
    )

    return raw - costs


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(

    trade,

    exit_price,

    reason
):

    t = dict(
        trade
    )

    t[
        "exit_price"
    ] = exit_price

    t[
        "exit_reason"
    ] = reason

    t[
        "closed_at"
    ] = now_iso()

    t[
        "pnl_pct"
    ] = calculate_trade_pnl(

        t,

        exit_price
    )

    t[
        "status"
    ] = "CLOSED"

    t[
        "strategy_version"
    ] = STRATEGY_VERSION

    return t


# ============================================================
# PARSE OPENED TIMESTAMP
# ============================================================

def parse_opened_ts(
    text
):

    if not text:

        return 0

    try:

        return int(

            datetime.fromisoformat(

                text.replace(
                    "Z",
                    "+00:00"
                )

            ).timestamp()
        )

    except Exception:

        return 0


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(

    state,

    history
):

    open_trades = state.get(

        "open_trades",

        []
    )

    if not open_trades:

        return 0

    print(

        f"[MONITOR] "
        f"{len(open_trades)} "
        f"open trade(s)"
    )

    try:

        tickers = get_tickers()

    except Exception as e:

        print(

            f"[ERROR] "
            f"Ticker loading failed: "
            f"{e}"
        )

        # NEVER DELETE TRADES
        # ON API FAILURE

        return 0

    remaining = []

    closed_count = 0

    now = now_utc()

    for trade in open_trades:

        symbol = trade[
            "symbol"
        ]

        side = trade[
            "side"
        ]

        sl = safe_float(

            trade.get(
                "sl"
            )
        )

        tp = safe_float(

            trade.get(
                "tp"
            )
        )

        opened_ts = parse_opened_ts(

            trade.get(
                "opened_at"
            )
        )

        result_reason = None

        exit_price = None

        try:

            candles = get_candles(

                symbol,

                TF_5M,

                250
            )

            if not candles:

                print(

                    f"[MONITOR] "
                    f"{symbol}: "
                    f"no candle data, "
                    f"KEEPING TRADE"
                )

                remaining.append(
                    trade
                )

                continue

            # ------------------------------------------------
            # Candles after trade opening
            # ------------------------------------------------

            relevant = []

            for c in candles:

                if opened_ts:

                    if (

                        c["time"]
                        > opened_ts

                    ):

                        relevant.append(
                            c
                        )

                else:

                    relevant.append(
                        c
                    )

            # ------------------------------------------------
            # Chronological exit check
            # ------------------------------------------------

            for c in relevant:

                hit_sl = (

                    c["low"] <= sl

                    if side == "LONG"

                    else

                    c["high"] >= sl
                )

                hit_tp = (

                    c["high"] >= tp

                    if side == "LONG"

                    else

                    c["low"] <= tp
                )

                # ------------------------------------------------
                # Both TP and SL in same candle
                # ------------------------------------------------

                if hit_sl and hit_tp:

                    result_reason = (
                        "AMBIGUOUS"
                    )

                    exit_price = (
                        c["close"]
                    )

                    break

                if hit_sl:

                    result_reason = "SL"

                    exit_price = sl

                    break

                if hit_tp:

                    result_reason = "TP"

                    exit_price = tp

                    break

            # ------------------------------------------------
            # EXIT FOUND
            # ------------------------------------------------

            if result_reason:

                closed_trade = close_trade(

                    trade,

                    exit_price,

                    result_reason
                )

                # Save BEFORE removing
                # from open state.

                history.append(
                    closed_trade
                )

                closed_count += 1

                print(

                    f"[CLOSED] "
                    f"{display_symbol(symbol)} "
                    f"{side} "
                    f"{result_reason} "
                    f"Exit="
                    f"{exit_price} "
                    f"P/L="
                    f"{closed_trade['pnl_pct']:+.2f}%"
                )

                continue

            # ------------------------------------------------
            # Update checked candle
            # ------------------------------------------------

            if relevant:

                trade[
                    "last_checked_candle"
                ] = relevant[
                    -1
                ]["time"]

            # ------------------------------------------------
            # Live price
            # ------------------------------------------------

            ticker = tickers.get(
                symbol
            )

            if ticker:

                price = safe_float(

                    ticker.get(
                        "last"
                    )

                    or ticker.get(
                        "lastPrice"
                    )

                    or ticker.get(
                        "markPrice"
                    )

                    or ticker.get(
                        "price"
                    ),

                    0.0
                )

                if price > 0:

                    trade[
                        "last_price"
                    ] = price

                    entry = safe_float(

                        trade.get(
                            "entry"
                        )
                    )

                    if entry > 0:

                        if side == "LONG":

                            pnl = (

                                (
                                    price
                                    - entry
                                )

                                / entry
                                * 100
                            )

                        else:

                            pnl = (

                                (
                                    entry
                                    - price
                                )

                                / entry
                                * 100
                            )

                        trade[
                            "pnl_pct"
                        ] = pnl

            # ------------------------------------------------
            # TIMEOUT
            # ------------------------------------------------

            if opened_ts:

                age = (

                    now

                    - datetime.fromtimestamp(

                        opened_ts,

                        tz=timezone.utc
                    )
                )

                if age >= timedelta(

                    hours=TIMEOUT_HOURS
                ):

                    timeout_price = safe_float(

                        trade.get(
                            "last_price"
                        ),

                        safe_float(

                            trade.get(
                                "entry"
                            )
                        )
                    )

                    closed_trade = close_trade(

                        trade,

                        timeout_price,

                        "TIMEOUT"
                    )

                    history.append(
                        closed_trade
                    )

                    closed_count += 1

                    print(

                        f"[TIMEOUT] "
                        f"{display_symbol(symbol)} "
                        f"{side} "
                        f"Exit="
                        f"{timeout_price} "
                        f"P/L="
                        f"{closed_trade['pnl_pct']:+.2f}%"
                    )

                    continue

            # ------------------------------------------------
            # STILL OPEN
            # ------------------------------------------------

            remaining.append(
                trade
            )

        except Exception as e:

            print(

                f"[ERROR] "
                f"Monitor {symbol}: "
                f"{e}"
            )

            # NEVER DELETE
            # ON MONITOR FAILURE

            remaining.append(
                trade
            )

    state[
        "open_trades"
    ] = remaining

    return closed_count


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    history
):

    valid = [

        x

        for x in history

        if (

            x.get(
                "status"
            )
            == "CLOSED"

            and

            str(
                x.get(
                    "strategy_version",
                    ""
                )
            ).startswith("v13")
        )
    ]

    trades = len(
        valid
    )

    wins = sum(

        x.get(
            "exit_reason"
        )
        == "TP"

        for x in valid
    )

    losses = sum(

        x.get(
            "exit_reason"
        )
        == "SL"

        for x in valid
    )

    timeouts = sum(

        x.get(
            "exit_reason"
        )
        == "TIMEOUT"

        for x in valid
    )

    ambiguous = sum(

        x.get(
            "exit_reason"
        )
        == "AMBIGUOUS"

        for x in valid
    )

    total_pnl = sum(

        safe_float(

            x.get(
                "pnl_pct"
            )
        )

        for x in valid
    )

    win_rate = (

        wins
        / trades
        * 100

        if trades > 0

        else 0.0
    )

    return {

        "trades":
            trades,

        "wins":
            wins,

        "losses":
            losses,

        "timeouts":
            timeouts,

        "ambiguous":
            ambiguous,

        "win_rate":
            win_rate,

        "total_pnl":
            total_pnl,
    }


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt_price(
    value
):

    value = safe_float(
        value
    )

    if value >= 1000:

        return f"{value:.2f}"

    if value >= 100:

        return f"{value:.3f}"

    if value >= 10:

        return f"{value:.4f}"

    if value >= 1:

        return f"{value:.5f}"

    if value >= 0.1:

        return f"{value:.6f}"

    if value >= 0.01:

        return f"{value:.7f}"

    return f"{value:.8f}"


# ============================================================
# TELEGRAM MESSAGE CHUNKING
# ============================================================

def split_telegram_message(
    message,
    max_length=TELEGRAM_MAX_LENGTH
):

    if not message:

        return []

    if len(message) <= max_length:

        return [message]

    chunks = []

    remaining = message

    while len(remaining) > max_length:

        cut = remaining.rfind(
            "\n",
            0,
            max_length
        )

        if cut <= 0:

            cut = max_length

        chunks.append(
            remaining[:cut]
        )

        remaining = remaining[
            cut:
        ]

        remaining = remaining.lstrip(
            "\n"
        )

    if remaining:

        chunks.append(
            remaining
        )

    return chunks


# ============================================================
# TELEGRAM CONFIG CHECK
# ============================================================

def telegram_configured():

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    token_ok = bool(
        token
        and token.strip()
    )

    chat_ok = bool(
        chat_id
        and chat_id.strip()
    )

    print(

        "[TELEGRAM] "
        f"BOT_TOKEN configured: "
        f"{token_ok}"
    )

    print(

        "[TELEGRAM] "
        f"CHAT_ID configured: "
        f"{chat_ok}"
    )

    if not token_ok:

        print(

            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN "
            "is missing."
        )

    if not chat_ok:

        print(

            "[TELEGRAM ERROR] "
            "TELEGRAM_CHAT_ID "
            "is missing."
        )

    return (
        token_ok
        and chat_ok
    )


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_telegram(
    message
):

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    # --------------------------------------------------------
    # Configuration
    # --------------------------------------------------------

    if not token:

        print(

            "[TELEGRAM ERROR] "
            "TELEGRAM_BOT_TOKEN "
            "is missing."
        )

        return False

    if not chat_id:

        print(

            "[TELEGRAM ERROR] "
            "TELEGRAM_CHAT_ID "
            "is missing."
        )

        return False

    if not message:

        print(
            "[TELEGRAM ERROR] "
            "Empty message."
        )

        return False

    # --------------------------------------------------------
    # Split long messages
    # --------------------------------------------------------

    chunks = split_telegram_message(
        message
    )

    print(

        f"[TELEGRAM] "
        f"Preparing {len(chunks)} "
        f"message chunk(s)"
    )

    url = (

        f"https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    all_success = True

    # --------------------------------------------------------
    # Send chunks
    # --------------------------------------------------------

    for index, chunk in enumerate(
        chunks,
        start=1
    ):

        sent = False

        for attempt in range(

            1,

            TELEGRAM_RETRIES + 1
        ):

            try:

                response = SESSION.post(

                    url,

                    json={

                        "chat_id":
                            chat_id,

                        "text":
                            chunk,

                        # IMPORTANT:
                        # No Markdown / HTML.
                        # Plain text avoids parsing
                        # failures caused by symbols
                        # such as "_" and "*".

                        "disable_web_page_preview":
                            True,
                    },

                    timeout=
                        REQUEST_TIMEOUT
                )

                print(

                    f"[TELEGRAM] "
                    f"Chunk {index}/"
                    f"{len(chunks)} "
                    f"Attempt {attempt} "
                    f"HTTP "
                    f"{response.status_code}"
                )

                # ------------------------------------------------
                # Parse Telegram response
                # ------------------------------------------------

                try:

                    result = (
                        response.json()
                    )

                except Exception:

                    result = {

                        "ok": False,

                        "raw":
                            response.text
                    }

                # ------------------------------------------------
                # SUCCESS
                # ------------------------------------------------

                if (

                    response.ok

                    and

                    result.get(
                        "ok"
                    )
                    is True

                ):

                    print(

                        f"[TELEGRAM] "
                        f"Chunk {index}/"
                        f"{len(chunks)} "
                        f"sent successfully."
                    )

                    sent = True

                    break

                # ------------------------------------------------
                # TELEGRAM ERROR
                # ------------------------------------------------

                print(

                    f"[TELEGRAM ERROR] "
                    f"Chunk {index}: "
                    f"{result}"
                )

                # Telegram 429
                if (

                    result.get(
                        "error_code"
                    )
                    == 429

                ):

                    retry_after = (

                        result.get(
                            "parameters",
                            {}
                        ).get(
                            "retry_after",
                            TELEGRAM_RETRY_SLEEP
                        )
                    )

                    print(

                        f"[TELEGRAM] "
                        f"Rate limited. "
                        f"Waiting "
                        f"{retry_after}s"
                    )

                    time.sleep(
                        safe_float(
                            retry_after,
                            TELEGRAM_RETRY_SLEEP
                        )
                    )

                elif attempt < (
                    TELEGRAM_RETRIES
                ):

                    time.sleep(
                        TELEGRAM_RETRY_SLEEP
                    )

            except Exception as e:

                print(

                    f"[TELEGRAM ERROR] "
                    f"Chunk {index} "
                    f"Attempt {attempt}: "
                    f"{e}"
                )

                if attempt < (
                    TELEGRAM_RETRIES
                ):

                    time.sleep(
                        TELEGRAM_RETRY_SLEEP
                    )

        if not sent:

            all_success = False

            print(

                f"[TELEGRAM ERROR] "
                f"Failed to send "
                f"chunk {index}/"
                f"{len(chunks)}"
            )

    # --------------------------------------------------------
    # Final result
    # --------------------------------------------------------

    if all_success:

        print(
            "[TELEGRAM] "
            "ALL messages sent successfully."
        )

        return True

    print(

        "[TELEGRAM ERROR] "
        "One or more messages failed."
    )

    return False


# ============================================================
# TELEGRAM REPORT
# ============================================================

def generate_report(

    state,

    history,

    closed_count
):

    opens = state.get(

        "open_trades",

        []
    )

    stats = calculate_performance(
        history
    )

    lines = []

    lines.append(
        "📡 CRYPTO ICHIMOKU REPORT"
    )

    lines.append(

        f"🕐 "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S')} "
        f"UTC"
    )

    lines.append(

        f"⏱ 5m CLOSED | "
        f"TOP {TOP_N}"
    )

    lines.append(

        f"🤖 MTF ICHIMOKU "
        f"{STRATEGY_VERSION} | "
        f"RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append(

        f"📂 OPEN TRADES "
        f"({len(opens)}/"
        f"{MAX_OPEN_TRADES})"
    )

    if not opens:

        lines.append(
            "No open trades."
        )

    else:

        for idx, t in enumerate(

            opens,

            start=1
        ):

            symbol = display_symbol(

                t.get(
                    "symbol"
                )
            )

            side = t.get(
                "side",
                ""
            )

            emoji = (

                "🟢"

                if side == "LONG"

                else "🔴"
            )

            price = safe_float(

                t.get(
                    "last_price"
                ),

                safe_float(

                    t.get(
                        "entry"
                    )
                )
            )

            pnl = safe_float(

                t.get(
                    "pnl_pct"
                )
            )

            lines.append(

                f"{idx}. "
                f"{symbol} "
                f"{emoji} "
                f"{side}"
            )

            lines.append(

                f"Entry: "
                f"{fmt_price(t.get('entry'))}"
            )

            lines.append(

                f"Now: "
                f"{fmt_price(price)} "
                f"{'🟢' if pnl >= 0 else '🔴'} "
                f"{pnl:+.2f}%"
            )

            lines.append(

                f"SL: "
                f"{fmt_price(t.get('sl'))} "
                f"("
                f"{safe_float(t.get('risk_pct')):.2f}"
                f"%)"
            )

            lines.append(

                f"TP: "
                f"{fmt_price(t.get('tp'))} "
                f"(RR "
                f"{safe_float(t.get('rr'), RR):.2f}"
                f")"
            )

            lines.append(

                f"Score: "
                f"{safe_float(t.get('score')):.1f}"
            )

            if t.get(
                "pullback_patterns"
            ):

                lines.append(

                    "🔄 "
                    + ", ".join(

                        t[
                            "pullback_patterns"
                        ]
                    )
                )

            lines.append(
                "────────────"
            )

    # --------------------------------------------------------
    # CLOSED THIS RUN
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(

        f"✅ CLOSED THIS RUN: "
        f"{closed_count}"
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 CUMULATIVE PERFORMANCE"
    )

    lines.append(

        f"Trades {stats['trades']} | "
        f"🟢 {stats['wins']} | "
        f"🔴 {stats['losses']} | "
        f"⚪ {stats['timeouts']} | "
        f"⚠️ {stats['ambiguous']}"
    )

    lines.append(

        f"🏆 WR: "
        f"{stats['win_rate']:.1f}%"
    )

    lines.append(

        f"💰 Total P/L: "
        f"{stats['total_pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # LIMITS
    # --------------------------------------------------------

    long_count = sum(

        x.get("side") == "LONG"

        for x in opens
    )

    short_count = sum(

        x.get("side") == "SHORT"

        for x in opens
    )

    lines.append(

        f"⚙️ LIMITS: "
        f"LONG {long_count}/"
        f"{MAX_LONG_TRADES} | "
        f"SHORT {short_count}/"
        f"{MAX_SHORT_TRADES} | "
        f"TOTAL {len(opens)}/"
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(

        f"🛡 Stop: "
        f"{MIN_STOP_PCT:.2f}%–"
        f"{MAX_STOP_PCT:.2f}% | "
        f"Costs/side: "
        f"fee {FEE_PCT_PER_SIDE:.2f}% + "
        f"slip {SLIPPAGE_PCT_PER_SIDE:.2f}%"
    )

    lines.append(

        f"💾 State: "
        f"{STATE_FILE}"
    )

    lines.append(

        f"📚 History: "
        f"{HISTORY_FILE}"
    )

    return "\n".join(
        lines
    )


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def generate_signal_message(
    selected
):

    if not selected:

        return None

    lines = []

    lines.append(

        f"🚨 NEW ICHIMOKU SIGNAL "
        f"{STRATEGY_VERSION}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    for t in selected:

        emoji = (

            "🟢"

            if t["side"] == "LONG"

            else "🔴"
        )

        lines.append(

            f"{emoji} "
            f"{display_symbol(t['symbol'])} "
            f"{t['side']}"
        )

        lines.append(

            f"Entry: "
            f"{fmt_price(t['entry'])}"
        )

        lines.append(

            f"SL: "
            f"{fmt_price(t['sl'])} "
            f"({t['risk_pct']:.2f}%)"
        )

        lines.append(

            f"TP: "
            f"{fmt_price(t['tp'])}"
        )

        lines.append(

            f"RR: "
            f"{t['rr']:.2f}"
        )

        lines.append(

            f"Score: "
            f"{t['score']:.1f}/100"
        )

        lines.append(

            f"Trigger: "
            f"{t['trigger']}/10"
        )

        lines.append(

            f"Trend 1H: "
            f"{t['s1h']:+.0f}"
        )

        lines.append(

            f"Lock 30M: "
            f"{t['s30']:+.0f}"
        )

        lines.append(

            f"Structure 15M: "
            f"{t['s15']:+.0f}"
        )

        lines.append(

            f"Trigger 5M: "
            f"{t['s5']:+.0f}"
        )

        if t.get(
            "pullback_patterns"
        ):

            lines.append(

                "🔄 "
                + ", ".join(

                    t[
                        "pullback_patterns"
                    ]
                )
            )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "============================================================"
    )

    print(

        f"KRAKEN FUTURES "
        f"ICHIMOKU TOP RANKER "
        f"{STRATEGY_VERSION}"
    )

    print(
        "============================================================"
    )

    print(

        "1H Trend -> "
        "30M Lock -> "
        "15M Structure -> "
        "5M Fresh Trigger"
    )

    print(

        f"TOP={TOP_N} | "
        f"OPEN={MAX_OPEN_TRADES} | "
        f"RR={RR}:1 | "
        f"STOP={MIN_STOP_PCT:.2f}%.."
        f"{MAX_STOP_PCT:.2f}%"
    )

    print(
        f"STATE={STATE_FILE}"
    )

    print(
        f"HISTORY={HISTORY_FILE}"
    )

    # ========================================================
    # TELEGRAM CONFIG DIAGNOSTIC
    # ========================================================

    print(
        "[STEP -1] Checking Telegram configuration..."
    )

    telegram_configured()

    # ========================================================
    # STEP 0
    # ========================================================

    print(
        "[STEP 0] Loading persistent state..."
    )

    state = load_state()

    history = load_history()

    prune_used_setups(
        state
    )

    # ========================================================
    # STEP 1
    # MONITOR OPEN TRADES
    # ========================================================

    print(
        "[STEP 1] Monitoring open trades..."
    )

    closed_count = 0

    try:

        closed_count = monitor_open_trades(

            state,

            history
        )

    except Exception as e:

        print(

            f"[ERROR] "
            f"Monitor failed: "
            f"{e}"
        )

        traceback.print_exc()

    # ========================================================
    # CRITICAL PERSISTENCE
    # ========================================================

    save_state(
        state
    )

    save_history(
        history
    )

    # ========================================================
    # STEP 2
    # MARKETS
    # ========================================================

    print(
        "[STEP 2] Loading markets..."
    )

    try:

        markets = build_market_list()

    except Exception as e:

        print(

            f"[FATAL] "
            f"Market loading failed: "
            f"{e}"
        )

        report = generate_report(

            state,

            history,

            closed_count
        )

        telegram_ok = send_telegram(
            report
        )

        print(

            f"[TELEGRAM] "
            f"Final report result: "
            f"{telegram_ok}"
        )

        return

    if not markets:

        print(
            "[FATAL] No markets available."
        )

        report = generate_report(

            state,

            history,

            closed_count
        )

        telegram_ok = send_telegram(
            report
        )

        print(

            f"[TELEGRAM] "
            f"Final report result: "
            f"{telegram_ok}"
        )

        return

    # ========================================================
    # STEP 3
    # SCAN
    # ========================================================

    print(
        "[STEP 3] Scanning markets..."
    )

    candidates = scan_all_markets(

        markets,

        state
    )

    print(

        f"[INFO] Valid candidates: "
        f"{len(candidates)}"
    )

    # ========================================================
    # STEP 4
    # SELECT
    # ========================================================

    print(
        "[STEP 4] Selecting best trades..."
    )

    selected = select_best_trades(

        candidates,

        state
    )

    print(

        f"[INFO] Selected: "
        f"{len(selected)}"
    )

    # ========================================================
    # STEP 5
    # OPEN
    # ========================================================

    if selected:

        print(
            "[STEP 5] Opening selected trades..."
        )

        add_selected_trades(

            state,

            selected
        )

    else:

        print(
            "[STEP 5] No new trades."
        )

    # ========================================================
    # CRITICAL PERSISTENCE POINT
    # ========================================================

    save_state(
        state
    )

    save_history(
        history
    )

    # ========================================================
    # STEP 6
    # NEW SIGNAL TELEGRAM
    # ========================================================

    if selected:

        signal_message = (
            generate_signal_message(
                selected
            )
        )

        if signal_message:

            print(
                "[TELEGRAM] "
                "Sending new signal..."
            )

            telegram_ok = send_telegram(

                signal_message
            )

            print(

                f"[TELEGRAM] "
                f"New signal result: "
                f"{telegram_ok}"
            )

    # ========================================================
    # STEP 7
    # FINAL REPORT
    # ========================================================

    print(
        "[STEP 7] "
        "Generating Telegram report..."
    )

    report = generate_report(

        state,

        history,

        closed_count
    )

    print(
        report
    )

    print(
        "[TELEGRAM] "
        "Sending final report..."
    )

    telegram_ok = send_telegram(
        report
    )

    print(

        f"[TELEGRAM] "
        f"Final report result: "
        f"{telegram_ok}"
    )

    # ========================================================
    # FINAL PERFORMANCE
    # ========================================================

    stats = calculate_performance(
        history
    )

    print(
        "============================================================"
    )

    print(

        f"[PERFORMANCE] "
        f"Trades={stats['trades']} "
        f"TP={stats['wins']} "
        f"SL={stats['losses']} "
        f"TIMEOUT={stats['timeouts']} "
        f"AMBIGUOUS={stats['ambiguous']} "
        f"WR={stats['win_rate']:.1f}% "
        f"P/L={stats['total_pnl']:+.2f}%"
    )

    print(

        f"[STATE] "
        f"Open trades="
        f"{len(state.get('open_trades', []))}"
    )

    print(

        f"[STATE] "
        f"History trades="
        f"{len(history)}"
    )

    print(
        "============================================================"
    )

    print(
        "[DONE]"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOP] Interrupted."
        )

    except Exception as e:

        print(
            f"\n[FATAL] {e}"
        )

        traceback.print_exc()
