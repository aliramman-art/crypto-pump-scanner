# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v14.0
# ============================================================
# Kraken Futures
#
# FEATURES:
#   - TOP 100 USD perpetual markets by 24H volume
#   - 1H  = Trend
#   - 30M = Confirmation / Lock
#   - 15M = Pullback Structure
#   - 5M  = Pullback + Reversal Trigger / Entry
#   - CLOSED CANDLES ONLY
#
# NEW v14.0:
#   - Telegram /check BTC
#   - Telegram /check ETH
#   - Telegram /check BTC ETH SOL
#   - Single-symbol MTF analysis
#   - NO TRADE diagnosis
#   - Entry / SL / TP / RR
#   - Detailed MTF scores
#   - Pullback diagnostics
#   - Trigger diagnostics
#   - Does NOT open a trade for /check
#   - Telegram getUpdates support
#   - Telegram offset persistence
#   - TOP 100 selected by 24H volume
#
# PRESERVED FROM v13.5:
#   - Performance history reset once
#   - State preserved
#   - Open trades never lost on API failure
#   - Closed trades saved before removal
#   - Duplicate setup protection
#   - Ambiguous TP/SL handling
#   - Closed candle exit tracking
#   - Structural SL
#   - RR 1:1
#   - MAX 4 OPEN TRADES
#   - MAX 2 LONG
#   - MAX 2 SHORT
#   - Telegram retry
#   - Telegram chunking
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

STRATEGY_VERSION = "v14.0"


# ============================================================
# FILES
# ============================================================

STATE_FILE = "ichimoku_state.json"

HISTORY_FILE = "ichimoku_trade_history.json"

HISTORY_RESET_MARKER = (
    "ichimoku_history_reset_v13_5.flag"
)

TELEGRAM_OFFSET_FILE = (
    "telegram_update_offset.json"
)


# ============================================================
# PERFORMANCE RESET
# ============================================================

RESET_HISTORY_ONCE = True


# ============================================================
# LEGACY STATE
# ============================================================

LEGACY_STATE_FILES = [

    "ichimoku_state_v13_5.json",
    "ichimoku_state_v13_4.json",
    "ichimoku_state_v13_3.json",
    "ichimoku_state_v13_2.json",
    "ichimoku_state_v13_1.json",
    "ichimoku_state_v13.json",
]


# ============================================================
# LEGACY HISTORY
# ============================================================

LEGACY_HISTORY_FILES = []


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

TELEGRAM_MAX_LENGTH = 4000

TELEGRAM_UPDATE_TIMEOUT = 1


# ============================================================
# STOP
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
# COSTS
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
        "Kraken-Ichi-Scanner/14.0"
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


def normalize_symbol_query(
    query
):

    q = str(
        query or ""
    ).strip().upper()

    if not q:

        return ""

    if q.startswith(
        "PF_"
    ):

        return q

    if q.endswith(
        "USD"
    ):

        return "PF_" + q

    return "PF_" + q + "USD"


def normalize_timestamp(value):

    try:

        ts = float(value)

        if ts > 10_000_000_000:

            ts /= 1000.0

        return int(ts)

    except Exception:

        return 0


# ============================================================
# JSON
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
# HISTORY RESET
# ============================================================

def history_reset_required():

    if not RESET_HISTORY_ONCE:

        return False

    return not os.path.exists(
        HISTORY_RESET_MARKER
    )


def reset_history_once():

    if not history_reset_required():

        return False

    print(
        "============================================================"
    )

    print(
        "[RESET] PERFORMANCE HISTORY RESET"
    )

    print(
        "[RESET] Old cumulative history "
        "will NOT be migrated."
    )

    print(
        "[RESET] Starting fresh."
    )

    try:

        if os.path.exists(
            HISTORY_FILE
        ):

            os.remove(
                HISTORY_FILE
            )

            print(
                f"[RESET] Removed "
                f"{HISTORY_FILE}"
            )

    except Exception as e:

        print(
            f"[RESET ERROR] "
            f"{e}"
        )

    for old_file in [

        "ichimoku_trade_history_v13_5.json",
        "ichimoku_trade_history_v13_4.json",
        "ichimoku_trade_history_v13_3.json",
        "ichimoku_trade_history_v13_2.json",
        "ichimoku_trade_history_v13_1.json",
        "ichimoku_trade_history_v13.json",

    ]:

        try:

            if os.path.exists(
                old_file
            ):

                os.remove(
                    old_file
                )

                print(
                    f"[RESET] Removed "
                    f"{old_file}"
                )

        except Exception as e:

            print(
                f"[RESET WARN] "
                f"{old_file}: {e}"
            )

    save_json_file(
        HISTORY_FILE,
        []
    )

    try:

        with open(
            HISTORY_RESET_MARKER,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                f"History reset by "
                f"{STRATEGY_VERSION}\n"
            )

            f.write(
                f"Reset time: "
                f"{now_iso()}\n"
            )

    except Exception as e:

        print(
            f"[RESET ERROR] "
            f"Marker: {e}"
        )

    print(
        "============================================================"
    )

    return True


# ============================================================
# STATE
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


def load_state():

    state = load_json_file(
        STATE_FILE,
        None
    )

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
                    f"{legacy} -> "
                    f"{STATE_FILE}"
                )

                state = legacy_state

                break

    if not isinstance(
        state,
        dict
    ):

        return default_state()

    if not isinstance(
        state.get("open_trades"),
        list
    ):

        state["open_trades"] = []

    if not isinstance(
        state.get("used_setups"),
        dict
    ):

        state["used_setups"] = {}

    normalized = []

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
            trade.get("opened_at")
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
                trade.get("entry")
            )
        )

        trade.setdefault(
            "pnl_pct",
            0.0
        )

        normalized.append(
            trade
        )

    state[
        "open_trades"
    ] = normalized

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
        f"[STATE] Loaded "
        f"{len(normalized)} open trade(s)"
    )

    return state


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


# ============================================================
# HISTORY
# ============================================================

def load_history():

    reset_history_once()

    history = load_json_file(
        HISTORY_FILE,
        []
    )

    if not isinstance(
        history,
        list
    ):

        history = []

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

        result.append(
            trade
        )

    save_json_file(
        HISTORY_FILE,
        result
    )

    return result


def save_history(history):

    save_json_file(
        HISTORY_FILE,
        history
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
            data.get("instruments")
            or data.get("data")
            or []
        )

    if isinstance(
        data,
        list
    ):

        return data

    return []


def get_tickers():

    data = http_get(
        TICKERS_URL
    )

    if isinstance(
        data,
        dict
    ):

        raw = (
            data.get("tickers")
            or data.get("data")
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

            item.get("symbol")
            or item.get("instrument")
            or ""
        )

        if symbol:

            result[
                symbol
            ] = item

    return result


# ============================================================
# TICKER VOLUME
# ============================================================

def ticker_volume(
    ticker
):

    if not isinstance(
        ticker,
        dict
    ):

        return 0.0

    candidates = [

        ticker.get(
            "volumeQuote"
        ),

        ticker.get(
            "volume_quote"
        ),

        ticker.get(
            "quoteVolume"
        ),

        ticker.get(
            "quote_volume"
        ),

        ticker.get(
            "volume24h"
        ),

        ticker.get(
            "volume"
        ),

    ]

    for value in candidates:

        n = safe_float(
            value,
            -1
        )

        if n >= 0:

            return n

    return 0.0


# ============================================================
# BID / ASK SPREAD
# ============================================================

def ticker_spread_pct(
    ticker
):

    if not isinstance(
        ticker,
        dict
    ):

        return 0.0

    bid = safe_float(

        ticker.get("bid")
        or ticker.get("bidPrice"),

        0.0
    )

    ask = safe_float(

        ticker.get("ask")
        or ticker.get("askPrice"),

        0.0
    )

    if bid <= 0 or ask <= 0:

        return 0.0

    mid = (
        bid + ask
    ) / 2

    if mid <= 0:

        return 0.0

    return (
        (ask - bid)
        / mid
        * 100
    )


# ============================================================
# BUILD TOP 100
# ============================================================

def build_market_list():

    instruments = get_instruments()

    tickers = get_tickers()

    markets = []

    for item in instruments:

        if not isinstance(
            item,
            dict
        ):

            continue

        symbol = str(

            item.get("symbol")
            or item.get("instrument")
            or ""
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

        ticker = tickers.get(
            symbol,
            {}
        )

        volume = ticker_volume(
            ticker
        )

        spread = ticker_spread_pct(
            ticker
        )

        if volume < MIN_24H_VOLUME:

            continue

        if (

            spread > 0

            and

            spread > MAX_BID_ASK_SPREAD_PCT

        ):

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

            "volume":
                volume,

            "spread_pct":
                spread,
        })

    unique = {}

    for market in markets:

        unique[
            market["symbol"]
        ] = market

    markets = list(
        unique.values()
    )

    # --------------------------------------------------------
    # REAL TOP 100:
    # Sort by available 24H ticker volume.
    # --------------------------------------------------------

    markets.sort(

        key=lambda x:
            x.get(
                "volume",
                0.0
            ),

        reverse=True
    )

    markets = markets[
        :TOP_N
    ]

    print(
        f"[MARKETS] "
        f"Top {len(markets)} "
        f"USD perpetual markets "
        f"selected by volume"
    )

    return markets


# ============================================================
# FIND MARKET
# ============================================================

def find_market(
    query,
    markets=None
):

    normalized = normalize_symbol_query(
        query
    )

    if not normalized:

        return None

    if markets:

        for market in markets:

            if market[
                "symbol"
            ].upper() == normalized:

                return market

    # --------------------------------------------------------
    # If symbol isn't in TOP 100,
    # fetch instruments directly.
    # This makes /check BTC work even when BTC
    # is not currently in the top ranking list.
    # --------------------------------------------------------

    instruments = get_instruments()

    for item in instruments:

        if not isinstance(
            item,
            dict
        ):

            continue

        symbol = str(

            item.get("symbol")
            or item.get("instrument")
            or ""
        )

        if symbol.upper() != normalized:

            continue

        if not symbol.upper().startswith(
            "PF_"
        ):

            continue

        return {

            "symbol":
                symbol,

            "tick_size":
                safe_float(

                    item.get("tickSize")
                    or item.get("tick_size")
                    or item.get("priceIncrement"),

                    0.0
                ),

            "volume":
                0.0,

            "spread_pct":
                0.0,
        }

    return None


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
            data.get("candles")
            or data.get("data")
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
                    row.get("time")
                    or row.get("timestamp")
                )

                o = (
                    row.get("open")
                    or row.get("o")
                )

                h = (
                    row.get("high")
                    or row.get("h")
                )

                l = (
                    row.get("low")
                    or row.get("l")
                )

                c = (
                    row.get("close")
                    or row.get("c")
                )

                v = (
                    row.get("volume")
                    or row.get("v")
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

    if price > cloud_top:

        score += 4

    elif price < cloud_bottom:

        score -= 4

    if price > kijun:

        score += 2

    elif price < kijun:

        score -= 2

    if tenkan > kijun:

        score += 2

    elif tenkan < kijun:

        score -= 2

    if i[
        "span_a"
    ] > i[
        "span_b"
    ]:

        score += 2

    elif i[
        "span_a"
    ] < i[
        "span_b"
    ]:

        score -= 2

    score = int(
        clamp(
            score,
            -10,
            10
        )
    )

    i[
        "score"
    ] = score

    return score, i


# ============================================================
# MTF
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
            "Not enough closed candles"
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
# PULLBACK
# ============================================================

def five_minute_pullback(

    side,
    candles
):

    if len(candles) < 8:

        return {
            "valid": False,
            "reason": "Not enough candles"
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
            "valid": False,
            "reason": "Invalid ATR"
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

        reversal = is_bullish(
            cur
        )

        rejection = (

            lower_wick(b)
            >= candle_range(b)
            * MIN_REJECTION_WICK_RATIO
        )

        reclaim = (

            cur["close"]
            > tenkan

            or

            cur["close"]
            > kijun
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

        reversal = is_bearish(
            cur
        )

        rejection = (

            upper_wick(b)
            >= candle_range(b)
            * MIN_REJECTION_WICK_RATIO
        )

        reclaim = (

            cur["close"]
            < tenkan

            or

            cur["close"]
            < kijun
        )

    valid = (

        touch

        and

        reversal
    )

    if not touch:

        reason = "No fresh Tenkan/Kijun touch"

    elif not reversal:

        reason = "5M reversal candle not confirmed"

    elif not body_ok:

        reason = "Trigger candle body too weak"

    else:

        reason = "Pullback valid"

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

        "reason":
            reason,
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
            "Invalid ATR"
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

    else:

        structural = (

            max(
                x["high"]
                for x in combined
            )
            + a * 0.10
        )

    entry_r = round_to_tick(
        entry,
        tick_size
    )

    sl_r = round_to_tick(
        structural,
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
            "Invalid structural risk"
        )

    risk_pct = (
        risk
        / entry_r
        * 100
    )

    if risk_pct < MIN_STOP_PCT:

        min_dist = (
            entry_r
            * MIN_STOP_PCT
            / 100
        )

        if side == "LONG":

            sl_r = round_to_tick(
                entry_r - min_dist,
                tick_size
            )

        else:

            sl_r = round_to_tick(
                entry_r + min_dist,
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

    if risk_pct > MAX_STOP_PCT:

        raise RuntimeError(
            f"Stop too wide: "
            f"{risk_pct:.2f}%"
        )

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
            "Invalid TP"
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
# DIAGNOSTIC SIDE ANALYSIS
# ============================================================

def analyze_side_diagnostic(

    side,
    analysis,
    market
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

    c5 = analysis[
        "5m"
    ]["candles"]

    c15 = analysis[
        "15m"
    ]["candles"]

    sign = (
        1
        if side == "LONG"
        else -1
    )

    pb = five_minute_pullback(
        side,
        c5
    )

    trigger = trigger_score(
        side,
        c5,
        pb
    )

    score = final_rank_score(
        side,
        analysis,
        trigger
    )

    patterns = reversal_patterns(
        side,
        c5
    )

    reasons = []

    if sign * s1 < MIN_1H_SCORE:

        reasons.append(
            f"1H score "
            f"{s1:+.0f} < "
            f"{MIN_1H_SCORE:.0f}"
        )

    if sign * s30 < MIN_30M_SCORE:

        reasons.append(
            f"30M Lock "
            f"{s30:+.0f} < "
            f"{MIN_30M_SCORE:.0f}"
        )

    if sign * s15 < MIN_15M_SCORE:

        reasons.append(
            f"15M structure "
            f"{s15:+.0f} < "
            f"{MIN_15M_SCORE:.0f}"
        )

    if sign * s5 < MIN_5M_SCORE:

        reasons.append(
            f"5M score "
            f"{s5:+.0f} < "
            f"{MIN_5M_SCORE:.0f}"
        )

    if not pb.get(
        "touch"
    ):

        reasons.append(
            "No Tenkan/Kijun pullback touch"
        )

    if not pb.get(
        "reversal"
    ):

        reasons.append(
            "No 5M reversal candle"
        )

    if not pb.get(
        "reclaim"
    ):

        reasons.append(
            "No Tenkan/Kijun reclaim"
        )

    if not pb.get(
        "structure_break"
    ):

        reasons.append(
            "Structure break not confirmed"
        )

    if not pb.get(
        "body_ok"
    ):

        reasons.append(
            "Trigger candle body weak"
        )

    if trigger < MIN_TRIGGER_SCORE:

        reasons.append(
            f"Trigger {trigger}/10 < "
            f"{MIN_TRIGGER_SCORE}/10"
        )

    if score < MIN_SCORE:

        reasons.append(
            f"Final score "
            f"{score:.1f} < "
            f"{MIN_SCORE:.1f}"
        )

    levels = None

    if not reasons:

        try:

            levels = structural_levels(

                side,

                c5[-1]["close"],

                c5,

                c15,

                market.get(
                    "tick_size",
                    0.0
                )
            )

        except Exception as e:

            reasons.append(
                f"SL/TP invalid: {e}"
            )

    return {

        "side":
            side,

        "s1":
            s1,

        "s30":
            s30,

        "s15":
            s15,

        "s5":
            s5,

        "pullback":
            pb,

        "trigger":
            trigger,

        "score":
            score,

        "patterns":
            patterns,

        "levels":
            levels,

        "reasons":
            reasons,
    }


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

        result = analyze_side_diagnostic(

            side,

            analysis,

            market
        )

        if result[
            "reasons"
        ]:

            continue

        levels = result[
            "levels"
        ]

        (
            entry_r,
            sl,
            tp,
            risk,
            reward,
            risk_pct
        ) = levels

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
                result["score"],

            "trigger":
                result["trigger"],

            "trend":
                result["s1"],

            "reversal":
                result["s5"]
                - result["s30"],

            "s1h":
                result["s1"],

            "s30":
                result["s30"],

            "s15":
                result["s15"],

            "s5":
                result["s5"],

            "pullback_patterns":
                result["patterns"],

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
# DETAILED /CHECK ANALYSIS
# ============================================================

def check_symbol(
    market
):

    symbol = market[
        "symbol"
    ]

    analysis = mtf_analysis(
        symbol
    )

    long_result = analyze_side_diagnostic(

        "LONG",

        analysis,

        market
    )

    short_result = analyze_side_diagnostic(

        "SHORT",

        analysis,

        market
    )

    # --------------------------------------------------------
    # Determine best side
    # --------------------------------------------------------

    valid_long = not bool(
        long_result["reasons"]
    )

    valid_short = not bool(
        short_result["reasons"]
    )

    if valid_long and valid_short:

        best = (

            long_result

            if long_result["score"]
            >= short_result["score"]

            else

            short_result
        )

    elif valid_long:

        best = long_result

    elif valid_short:

        best = short_result

    else:

        best = (

            long_result

            if long_result["score"]
            >= short_result["score"]

            else

            short_result
        )

    return {

        "symbol":
            symbol,

        "analysis":
            analysis,

        "long":
            long_result,

        "short":
            short_result,

        "best":
            best,

        "valid":
            valid_long
            or valid_short,
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
# CLOUD STATUS
# ============================================================

def cloud_status(
    info
):

    price = info[
        "price"
    ]

    top = info[
        "cloud_top"
    ]

    bottom = info[
        "cloud_bottom"
    ]

    if price > top:

        return "ABOVE 🟢"

    if price < bottom:

        return "BELOW 🔴"

    return "INSIDE 🟡"


def direction_text(
    score
):

    if score >= 4:

        return "BULLISH 🟢"

    if score <= -4:

        return "BEARISH 🔴"

    return "NEUTRAL 🟡"


# ============================================================
# CHECK MESSAGE
# ============================================================

def generate_check_message(
    result
):

    symbol = result[
        "symbol"
    ]

    display = display_symbol(
        symbol
    )

    analysis = result[
        "analysis"
    ]

    long_r = result[
        "long"
    ]

    short_r = result[
        "short"
    ]

    best = result[
        "best"
    ]

    lines = []

    lines.append(
        f"🔎 ICHIMOKU SETUP CHECK {STRATEGY_VERSION}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🪙 {display}"
    )

    lines.append(
        "⏱ CLOSED CANDLES ONLY"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # MTF
    # --------------------------------------------------------

    lines.append(
        "📊 MTF STRUCTURE"
    )

    for tf in (
        "1h",
        "30m",
        "15m",
        "5m"
    ):

        item = analysis[
            tf
        ]

        score = item[
            "score"
        ]

        info = item[
            "info"
        ]

        name = tf.upper()

        lines.append(

            f"{name}: "
            f"{direction_text(score)} "
            f"Score {score:+.0f}/10 | "
            f"Cloud {cloud_status(info)}"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # ICHIMOKU DETAILS
    # --------------------------------------------------------

    i5 = analysis[
        "5m"
    ]["info"]

    lines.append(
        "☁️ 5M ICHIMOKU"
    )

    lines.append(
        f"Price: {fmt_price(i5['price'])}"
    )

    lines.append(
        f"Tenkan: {fmt_price(i5['tenkan'])}"
    )

    lines.append(
        f"Kijun: {fmt_price(i5['kijun'])}"
    )

    lines.append(
        f"Cloud: "
        f"{fmt_price(i5['cloud_bottom'])}"
        f" - "
        f"{fmt_price(i5['cloud_top'])}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    lines.append(
        "🟢 LONG CHECK"
    )

    lines.append(
        f"1H: {long_r['s1']:+.0f}/10"
    )

    lines.append(
        f"30M Lock: {long_r['s30']:+.0f}/10"
    )

    lines.append(
        f"15M Structure: {long_r['s15']:+.0f}/10"
    )

    lines.append(
        f"5M Trigger TF: {long_r['s5']:+.0f}/10"
    )

    lines.append(
        f"Pullback: "
        f"{'✅' if long_r['pullback'].get('valid') else '❌'}"
    )

    lines.append(
        f"Trigger: "
        f"{long_r['trigger']}/10"
    )

    lines.append(
        f"Score: "
        f"{long_r['score']:.1f}/100"
    )

    if long_r[
        "patterns"
    ]:

        lines.append(
            "🔄 "
            + ", ".join(
                long_r["patterns"]
            )
        )

    if long_r[
        "reasons"
    ]:

        lines.append(
            "❌ "
            + "; ".join(
                long_r["reasons"][:5]
            )
        )

    else:

        lines.append(
            "✅ LONG SETUP VALID"
        )

    lines.append(
        "────────────"
    )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    lines.append(
        "🔴 SHORT CHECK"
    )

    lines.append(
        f"1H: {short_r['s1']:+.0f}/10"
    )

    lines.append(
        f"30M Lock: {short_r['s30']:+.0f}/10"
    )

    lines.append(
        f"15M Structure: {short_r['s15']:+.0f}/10"
    )

    lines.append(
        f"5M Trigger TF: {short_r['s5']:+.0f}/10"
    )

    lines.append(
        f"Pullback: "
        f"{'✅' if short_r['pullback'].get('valid') else '❌'}"
    )

    lines.append(
        f"Trigger: "
        f"{short_r['trigger']}/10"
    )

    lines.append(
        f"Score: "
        f"{short_r['score']:.1f}/100"
    )

    if short_r[
        "patterns"
    ]:

        lines.append(
            "🔄 "
            + ", ".join(
                short_r["patterns"]
            )
        )

    if short_r[
        "reasons"
    ]:

        lines.append(
            "❌ "
            + "; ".join(
                short_r["reasons"][:5]
            )
        )

    else:

        lines.append(
            "✅ SHORT SETUP VALID"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # VERDICT
    # --------------------------------------------------------

    if result[
        "valid"
    ]:

        side = best[
            "side"
        ]

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        lines.append(
            f"🎯 VERDICT: "
            f"{emoji} {side}"
        )

        levels = best[
            "levels"
        ]

        if levels:

            (
                entry,
                sl,
                tp,
                risk,
                reward,
                risk_pct
            ) = levels

            lines.append(
                f"Entry: {fmt_price(entry)}"
            )

            lines.append(
                f"SL: {fmt_price(sl)} "
                f"({risk_pct:.2f}%)"
            )

            lines.append(
                f"TP: {fmt_price(tp)}"
            )

            lines.append(
                f"RR: {RR:.2f}:1"
            )

        lines.append(
            f"Confidence: "
            f"{best['score']:.1f}/100"
        )

        lines.append(
            "⚠️ Entry only after CLOSED 5M candle."
        )

        lines.append(
            "ℹ️ /check does NOT open a trade."
        )

    else:

        lines.append(
            "⛔ VERDICT: NO TRADE"
        )

        if (
            best["score"]
            > 0
        ):

            lines.append(
                f"Best side: "
                f"{best['side']} "
                f"{best['score']:.1f}/100"
            )

        lines.append(
            "Reason:"
        )

        # Put the most important reasons first.
        for reason in best[
            "reasons"
        ][:6]:

            lines.append(
                f"• {reason}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    return "\n".join(
        lines
    )


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

    scanned = 0

    no_signal = 0

    failed = 0

    skipped_open = 0

    for idx, market in enumerate(
        markets,
        start=1
    ):

        symbol = market[
            "symbol"
        ]

        if symbol in open_symbols:

            skipped_open += 1

            print(
                f"[{idx}/{len(markets)}] "
                f"{symbol} -> "
                f"already open"
            )

            continue

        scanned += 1

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

                no_signal += 1

                print(

                    f"[{idx}/{len(markets)}] "
                    f"{symbol} -> "
                    f"no signal"
                )

        except Exception as e:

            failed += 1

            print(

                f"[WARN] "
                f"{symbol}: "
                f"{e}"
            )

    return {

        "candidates":
            candidates,

        "scanned":
            scanned,

        "no_signal":
            no_signal,

        "failed":
            failed,

        "skipped_open":
            skipped_open,

        "total":
            len(markets),
    }


# ============================================================
# PRUNE
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
        - 7 * 24 * 3600
    )

    cleaned = {}

    for key, value in used.items():

        try:

            ts = int(value)

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
# SELECT
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

        if c[
            "symbol"
        ] in used_symbols:

            continue

        if c[
            "setup_key"
        ] in used_setups:

            continue

        if (
            c["side"] == "LONG"
            and slots_long <= 0
        ):

            continue

        if (
            c["side"] == "SHORT"
            and slots_short <= 0
        ):

            continue

        selected.append(
            c
        )

        used_symbols.add(
            c["symbol"]
        )

        if c[
            "side"
        ] == "LONG":

            slots_long -= 1

        else:

            slots_short -= 1

    return selected


# ============================================================
# ADD TRADES
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
            f"Entry="
            f"{trade['entry']} "
            f"SL="
            f"{trade['sl']} "
            f"TP="
            f"{trade['tp']}"
        )


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(

    trade,
    exit_price
):

    entry = safe_float(
        trade.get("entry")
    )

    exit_price = safe_float(
        exit_price
    )

    if (
        entry <= 0
        or exit_price <= 0
    ):

        return 0.0

    if trade[
        "side"
    ] == "LONG":

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

    if reason == "AMBIGUOUS":

        t[
            "pnl_pct"
        ] = 0.0

    else:

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
# OPENED TIMESTAMP
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

    try:

        tickers = get_tickers()

    except Exception as e:

        print(
            f"[ERROR] Ticker loading failed: "
            f"{e}"
        )

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
            trade.get("sl")
        )

        tp = safe_float(
            trade.get("tp")
        )

        opened_ts = parse_opened_ts(
            trade.get("opened_at")
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

                remaining.append(
                    trade
                )

                continue

            relevant = []

            for c in candles:

                if opened_ts:

                    if c[
                        "time"
                    ] > opened_ts:

                        relevant.append(
                            c
                        )

                else:

                    relevant.append(
                        c
                    )

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

            if result_reason:

                closed_trade = close_trade(

                    trade,

                    exit_price,

                    result_reason
                )

                history.append(
                    closed_trade
                )

                closed_count += 1

                continue

            if relevant:

                trade[
                    "last_checked_candle"
                ] = relevant[-1][
                    "time"
                ]

            ticker = tickers.get(
                symbol
            )

            if ticker:

                price = safe_float(

                    ticker.get("last")
                    or ticker.get("lastPrice")
                    or ticker.get("markPrice")
                    or ticker.get("price"),

                    0.0
                )

                if price > 0:

                    trade[
                        "last_price"
                    ] = price

                    entry = safe_float(
                        trade.get("entry")
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

                    continue

            remaining.append(
                trade
            )

        except Exception as e:

            print(
                f"[ERROR] "
                f"Monitor {symbol}: "
                f"{e}"
            )

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
            x.get("status")
            == "CLOSED"
        )
    ]

    trades = len(
        valid
    )

    wins = sum(

        x.get("exit_reason")
        == "TP"
        for x in valid
    )

    losses = sum(

        x.get("exit_reason")
        == "SL"
        for x in valid
    )

    timeouts = sum(

        x.get("exit_reason")
        == "TIMEOUT"
        for x in valid
    )

    ambiguous = sum(

        x.get("exit_reason")
        == "AMBIGUOUS"
        for x in valid
    )

    total_pnl = sum(

        safe_float(
            x.get("pnl_pct")
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
# TELEGRAM CONFIG
# ============================================================

def telegram_configured():

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = os.getenv(
        "TELEGRAM_CHAT_ID"
    )

    ok = bool(
        token
        and token.strip()
        and chat_id
        and chat_id.strip()
    )

    print(
        f"[TELEGRAM] configured={ok}"
    )

    return ok


# ============================================================
# TELEGRAM SEND
# ============================================================

def send_telegram(
    message,
    chat_id_override=None
):

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    chat_id = (
        chat_id_override
        or os.getenv(
            "TELEGRAM_CHAT_ID"
        )
    )

    if not token:

        print(
            "[TELEGRAM ERROR] "
            "Missing BOT_TOKEN"
        )

        return False

    if not chat_id:

        print(
            "[TELEGRAM ERROR] "
            "Missing CHAT_ID"
        )

        return False

    if not message:

        return False

    chunks = split_telegram_message(
        message
    )

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    all_success = True

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

                        "disable_web_page_preview":
                            True,
                    },

                    timeout=
                        REQUEST_TIMEOUT
                )

                try:

                    result = response.json()

                except Exception:

                    result = {
                        "ok": False,
                        "raw":
                            response.text
                    }

                if (
                    response.ok
                    and
                    result.get("ok")
                    is True
                ):

                    sent = True

                    break

                print(
                    f"[TELEGRAM ERROR] "
                    f"{result}"
                )

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

    return all_success


# ============================================================
# TELEGRAM UPDATE OFFSET
# ============================================================

def load_telegram_offset():

    data = load_json_file(

        TELEGRAM_OFFSET_FILE,

        {}
    )

    if not isinstance(
        data,
        dict
    ):

        return 0

    return int(
        safe_float(
            data.get(
                "offset",
                0
            )
        )
    )


def save_telegram_offset(
    offset
):

    save_json_file(

        TELEGRAM_OFFSET_FILE,

        {
            "offset":
                int(offset),

            "updated_at":
                now_iso(),
        }
    )


# ============================================================
# TELEGRAM GET UPDATES
# ============================================================

def telegram_get_updates():

    token = os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )

    if not token:

        return []

    offset = load_telegram_offset()

    url = (

        "https://api.telegram.org/"
        f"bot{token}/getUpdates"
    )

    try:

        response = SESSION.get(

            url,

            params={

                "offset":
                    offset,

                "timeout":
                    TELEGRAM_UPDATE_TIMEOUT,

                "allowed_updates":
                    json.dumps([
                        "message"
                    ]),
            },

            timeout=(
                TELEGRAM_UPDATE_TIMEOUT
                + 5
            )
        )

        response.raise_for_status()

        data = response.json()

        if not data.get(
            "ok"
        ):

            print(
                f"[TELEGRAM ERROR] "
                f"getUpdates: {data}"
            )

            return []

        return data.get(
            "result",
            []
        )

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] "
            f"getUpdates failed: "
            f"{e}"
        )

        return []


# ============================================================
# PARSE /CHECK
# ============================================================

def parse_check_command(
    text
):

    if not text:

        return []

    text = text.strip()

    parts = text.split()

    if not parts:

        return []

    command = parts[0].lower()

    if command.startswith(
        "/check"
    ):

        symbols = parts[1:]

    else:

        return []

    cleaned = []

    for symbol in symbols:

        symbol = (
            symbol
            .replace(",", "")
            .replace("$", "")
            .strip()
        )

        if symbol:

            cleaned.append(
                symbol
            )

    return cleaned


# ============================================================
# PROCESS TELEGRAM COMMANDS
# ============================================================

def process_telegram_commands(
    markets
):

    updates = telegram_get_updates()

    if not updates:

        return 0

    processed = 0

    for update in updates:

        update_id = update.get(
            "update_id"
        )

        try:

            if not isinstance(
                update,
                dict
            ):

                continue

            message = update.get(
                "message"
            )

            if not isinstance(
                message,
                dict
            ):

                continue

            chat = message.get(
                "chat",
                {}
            )

            chat_id = chat.get(
                "id"
            )

            text = message.get(
                "text",
                ""
            )

            symbols = parse_check_command(
                text
            )

            if not symbols:

                # Unknown commands are ignored.
                if update_id is not None:

                    save_telegram_offset(
                        int(update_id) + 1
                    )

                continue

            # ------------------------------------------------
            # SECURITY:
            # If TELEGRAM_CHAT_ID is configured,
            # only that chat can use /check.
            # ------------------------------------------------

            allowed_chat = os.getenv(
                "TELEGRAM_CHAT_ID"
            )

            if (
                allowed_chat
                and
                str(chat_id)
                != str(allowed_chat)
            ):

                print(
                    f"[TELEGRAM] "
                    f"Ignored /check from "
                    f"unauthorized chat "
                    f"{chat_id}"
                )

                if update_id is not None:

                    save_telegram_offset(
                        int(update_id) + 1
                    )

                continue

            # ------------------------------------------------
            # Limit number of symbols per message.
            # ------------------------------------------------

            symbols = symbols[:5]

            lines = []

            lines.append(
                "📡 CHECK REQUEST"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

            lines.append(
                f"Requested: "
                f"{len(symbols)}"
            )

            for raw_symbol in symbols:

                market = find_market(

                    raw_symbol,

                    markets
                )

                if not market:

                    lines.append(
                        f"❌ {raw_symbol.upper()}: "
                        f"Kraken USD perpetual "
                        f"not found"
                    )

                    continue

                try:

                    result = check_symbol(
                        market
                    )

                    check_message = (
                        generate_check_message(
                            result
                        )
                    )

                    send_telegram(
                        check_message,
                        chat_id_override=chat_id
                    )

                    processed += 1

                except Exception as e:

                    error_message = (

                        f"❌ CHECK ERROR\n"
                        f"🪙 {raw_symbol.upper()}\n"
                        f"Reason: {e}"
                    )

                    send_telegram(
                        error_message,
                        chat_id_override=chat_id
                    )

                    print(
                        f"[CHECK ERROR] "
                        f"{raw_symbol}: "
                        f"{e}"
                    )

            if update_id is not None:

                save_telegram_offset(
                    int(update_id) + 1
                )

        except Exception as e:

            print(
                f"[TELEGRAM COMMAND ERROR] "
                f"{e}"
            )

            if update_id is not None:

                save_telegram_offset(
                    int(update_id) + 1
                )

    return processed


# ============================================================
# REPORT
# ============================================================

def generate_report(

    state,
    history,
    closed_count,
    scan_stats=None
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
    # SCAN STATISTICS
    # --------------------------------------------------------

    if scan_stats:

        lines.append(
            "🔎 SCAN STATISTICS"
        )

        lines.append(

            f"Markets loaded: "
            f"{scan_stats.get('total', 0)}"
        )

        lines.append(

            f"Actually scanned: "
            f"{scan_stats.get('scanned', 0)}"
        )

        lines.append(

            f"❌ No signal: "
            f"{scan_stats.get('no_signal', 0)}"
        )

        lines.append(

            f"⚠️ API/analysis failed: "
            f"{scan_stats.get('failed', 0)}"
        )

        lines.append(

            f"📂 Already open: "
            f"{scan_stats.get('skipped_open', 0)}"
        )

        lines.append(

            f"🎯 Valid candidates: "
            f"{len(scan_stats.get('candidates', []))}"
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
                t.get("symbol")
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
                    t.get("entry")
                )
            )

            pnl = safe_float(
                t.get("pnl_pct")
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

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(

        f"✅ CLOSED THIS RUN: "
        f"{closed_count}"
    )

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
        f"⚪ {stats['ambiguous']} | "
        f"⚠️ {stats['timeouts']}"
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

    long_count = sum(

        x.get("side")
        == "LONG"

        for x in opens
    )

    short_count = sum(

        x.get("side")
        == "SHORT"

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
        f"{MAX_STOP_PCT:.2f}%"
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
        "5M Trigger"
    )

    print(

        f"TOP={TOP_N} | "
        f"OPEN={MAX_OPEN_TRADES} | "
        f"RR={RR}:1 | "
        f"STOP={MIN_STOP_PCT:.2f}%.."
        f"{MAX_STOP_PCT:.2f}%"
    )

    # ========================================================
    # STEP -1
    # TELEGRAM COMMANDS
    # ========================================================

    print(
        "[STEP -1] Telegram command check..."
    )

    telegram_configured()

    # ========================================================
    # STATE
    # ========================================================

    print(
        "[STEP 0] Loading state..."
    )

    state = load_state()

    history = load_history()

    prune_used_setups(
        state
    )

    # ========================================================
    # MARKETS FIRST
    # ========================================================

    print(
        "[STEP 1] Loading markets..."
    )

    try:

        markets = build_market_list()

    except Exception as e:

        print(
            f"[FATAL] "
            f"Market loading failed: "
            f"{e}"
        )

        return

    if not markets:

        print(
            "[FATAL] "
            "No markets available."
        )

        return

    # ========================================================
    # TELEGRAM /CHECK
    # ========================================================

    print(
        "[STEP 2] Processing Telegram /check..."
    )

    try:

        check_count = (
            process_telegram_commands(
                markets
            )
        )

        print(
            f"[TELEGRAM] "
            f"/check processed: "
            f"{check_count}"
        )

    except Exception as e:

        print(
            f"[TELEGRAM CHECK ERROR] "
            f"{e}"
        )

        traceback.print_exc()

    # ========================================================
    # MONITOR
    # ========================================================

    print(
        "[STEP 3] Monitoring open trades..."
    )

    closed_count = 0

    try:

        closed_count = (
            monitor_open_trades(
                state,
                history
            )
        )

    except Exception as e:

        print(
            f"[ERROR] "
            f"Monitor failed: "
            f"{e}"
        )

        traceback.print_exc()

    save_state(
        state
    )

    save_history(
        history
    )

    # ========================================================
    # SCAN
    # ========================================================

    print(
        "[STEP 4] Scanning TOP markets..."
    )

    try:

        scan_result = (
            scan_all_markets(
                markets,
                state
            )
        )

    except Exception as e:

        print(
            f"[FATAL] "
            f"Scan failed: "
            f"{e}"
        )

        scan_result = {

            "candidates":
                [],

            "scanned":
                0,

            "no_signal":
                0,

            "failed":
                0,

            "skipped_open":
                0,

            "total":
                len(markets),
        }

    candidates = scan_result[
        "candidates"
    ]

    print(

        f"[INFO] "
        f"Markets={scan_result['total']} | "
        f"Scanned={scan_result['scanned']} | "
        f"NoSignal={scan_result['no_signal']} | "
        f"Failed={scan_result['failed']} | "
        f"Candidates={len(candidates)}"
    )

    # ========================================================
    # SELECT
    # ========================================================

    print(
        "[STEP 5] Selecting best trades..."
    )

    selected = select_best_trades(

        candidates,

        state
    )

    print(
        f"[INFO] "
        f"Selected={len(selected)}"
    )

    # ========================================================
    # OPEN
    # ========================================================

    if selected:

        print(
            "[STEP 6] Opening selected trades..."
        )

        add_selected_trades(

            state,

            selected
        )

    else:

        print(
            "[STEP 6] No new trades."
        )

    save_state(
        state
    )

    save_history(
        history
    )

    # ========================================================
    # NEW SIGNAL
    # ========================================================

    if selected:

        signal_message = (
            generate_signal_message(
                selected
            )
        )

        if signal_message:

            send_telegram(
                signal_message
            )

    # ========================================================
    # REPORT
    # ========================================================

    print(
        "[STEP 7] Generating report..."
    )

    report = generate_report(

        state,

        history,

        closed_count,

        scan_result
    )

    print(
        report
    )

    telegram_ok = send_telegram(
        report
    )

    print(
        f"[TELEGRAM] "
        f"Final report: "
        f"{telegram_ok}"
    )

    # ========================================================
    # FINAL STATS
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
        f"AMBIGUOUS={stats['ambiguous']} "
        f"TIMEOUT={stats['timeouts']} "
        f"WR={stats['win_rate']:.1f}% "
        f"P/L={stats['total_pnl']:+.2f}%"
    )

    print(

        f"[SCAN] "
        f"Total={scan_result['total']} "
        f"Scanned={scan_result['scanned']} "
        f"NoSignal={scan_result['no_signal']} "
        f"Failed={scan_result['failed']} "
        f"Candidates={len(candidates)}"
    )

    print(

        f"[STATE] "
        f"Open={len(state.get('open_trades', []))}"
    )

    print(

        f"[STATE] "
        f"History={len(history)}"
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
