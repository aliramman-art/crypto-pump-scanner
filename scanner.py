# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v11.3
# ============================================================
# Kraken Futures
# TOP 100 USD perpetual futures
#
# MTF ICHIMOKU:
#   1H  = Trend
#   30m = Confirmation
#   15m = Pullback
#   5m  = Trigger
#
# CLOSED CANDLES ONLY
#
# ENTRY:
#   BUY / SELL after 5m candle close
#
# RISK:
#   Minimum Entry -> SL = 0.50%
#   RR = 1:1
#
# OPEN TRADES:
#   Maximum 4
#   Maximum 2 LONG
#   Maximum 2 SHORT
#   One trade per symbol
#
# MANAGEMENT:
#   TP / SL monitoring
#   Historical 5m candle high/low monitoring
#   4 hour timeout
#   Persistent state
#   Persistent history
#
# TELEGRAM:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
#
# STATE FILES:
#   ichimoku_state.json
#   ichimoku_trade_history.json
# ============================================================

import os
import json
import time
import math
import traceback
from collections import Counter
from datetime import datetime, timezone, timedelta

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = BASE_URL + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE_URL + "/derivatives/api/v3/tickers"
CHART_URL = BASE_URL + "/api/charts/v1/trade/{symbol}/{resolution}"

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "",
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "",
).strip()


# ============================================================
# TRADING SETTINGS
# ============================================================

TOP_N = 100

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

MIN_RISK_PCT = 0.50

RR = 1.0

TIMEOUT_HOURS = 4

REQUEST_TIMEOUT = 20
RETRIES = 3


# ============================================================
# TIMEFRAMES
# ============================================================

TF_5M = 5
TF_15M = 15
TF_30M = 30
TF_1H = 60


# ============================================================
# ICHIMOKU
# ============================================================

TENKAN = 9
KIJUN = 26
SENKOU_B = 52
DISPLACEMENT = 26


# ============================================================
# MTF WEIGHTS
# ============================================================

WEIGHT_1H = 0.50
WEIGHT_30M = 0.30
WEIGHT_15M = 0.20


# ============================================================
# ENTRY FILTER
# ============================================================

MIN_SCORE = 70.0

PULLBACK_ATR_MULTIPLIER = 1.5


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Kraken-Ichi-Screener/11.3",
        "Accept": "application/json",
    }
)


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def parse_datetime(value):
    if not value:
        return now_utc()

    try:
        dt = datetime.fromisoformat(
            str(value).replace(
                "Z",
                "+00:00",
            )
        )

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return dt

    except Exception:
        return now_utc()


def timestamp_from_datetime(dt):
    try:
        return dt.timestamp()
    except Exception:
        return 0.0


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        if value is None:
            return default

        result = float(value)

        if not math.isfinite(result):
            return default

        return result

    except Exception:
        return default


def clean_number(value):
    value = safe_float(
        value,
        0.0,
    )

    if value is None:
        return 0.0

    return value


def first_value(*values):
    for value in values:
        if value is not None:
            return value

    return None


def round_price(value):
    value = clean_number(value)

    if value == 0:
        return 0.0

    abs_value = abs(value)

    if abs_value >= 1000:
        decimals = 2

    elif abs_value >= 100:
        decimals = 3

    elif abs_value >= 10:
        decimals = 4

    elif abs_value >= 1:
        decimals = 5

    elif abs_value >= 0.1:
        decimals = 6

    elif abs_value >= 0.01:
        decimals = 7

    else:
        decimals = 8

    return round(
        value,
        decimals,
    )


def format_price(value):
    value = clean_number(value)

    if value == 0:
        return "0"

    abs_value = abs(value)

    if abs_value >= 1000:
        decimals = 2

    elif abs_value >= 100:
        decimals = 3

    elif abs_value >= 10:
        decimals = 4

    elif abs_value >= 1:
        decimals = 5

    elif abs_value >= 0.1:
        decimals = 6

    elif abs_value >= 0.01:
        decimals = 7

    else:
        decimals = 8

    text = f"{value:.{decimals}f}"

    text = text.rstrip("0").rstrip(".")

    return text


def pct_change(
    entry,
    price,
    direction,
):
    entry = safe_float(entry)
    price = safe_float(price)

    if not entry or not price:
        return 0.0

    if direction == "LONG":
        return (
            (price - entry)
            / entry
            * 100.0
        )

    return (
        (entry - price)
        / entry
        * 100.0
    )


# ============================================================
# JSON STATE
# ============================================================

def load_json(
    filename,
    default,
):
    try:

        if not os.path.exists(filename):
            return default

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[WARN] Cannot load "
            f"{filename}: {e}"
        )

        return default


def save_json(
    filename,
    data,
):
    temp_file = filename + ".tmp"

    with open(
        temp_file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(
        temp_file,
        filename,
    )


def load_state():

    state = load_json(
        STATE_FILE,
        {
            "open_trades": [],
            "history": [],
            "updated_at": iso_now(),
        },
    )

    if not isinstance(
        state,
        dict,
    ):
        state = {}

    if not isinstance(
        state.get("open_trades"),
        list,
    ):
        state["open_trades"] = []

    if not isinstance(
        state.get("history"),
        list,
    ):
        state["history"] = []

    # --------------------------------------------------------
    # Load external history
    # --------------------------------------------------------

    external_history = load_json(
        HISTORY_FILE,
        [],
    )

    if isinstance(
        external_history,
        list,
    ):

        existing_ids = {
            str(x.get("id"))
            for x in state["history"]
            if isinstance(x, dict)
            and x.get("id")
        }

        for trade in external_history:

            if not isinstance(
                trade,
                dict,
            ):
                continue

            trade_id = str(
                trade.get(
                    "id",
                    "",
                )
            )

            if (
                trade_id
                and trade_id not in existing_ids
            ):

                state["history"].append(
                    trade
                )

    state["updated_at"] = iso_now()

    return state


def save_state(state):

    state["updated_at"] = iso_now()

    save_json(
        STATE_FILE,
        state,
    )

    history = state.get(
        "history",
        [],
    )

    if not isinstance(
        history,
        list,
    ):
        history = []

    save_json(
        HISTORY_FILE,
        history,
    )


# ============================================================
# HTTP
# ============================================================

def http_get(
    url,
    params=None,
):
    last_error = None

    for attempt in range(
        1,
        RETRIES + 1,
    ):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            response.raise_for_status()

            return response.json()

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
        "Request failed after "
        f"{RETRIES} attempts: "
        f"{url} | {last_error}"
    )


# ============================================================
# KRAKEN MARKETS
# ============================================================

def get_instruments():

    data = http_get(
        INSTRUMENTS_URL
    )

    instruments = data.get(
        "instruments",
        [],
    )

    if not isinstance(
        instruments,
        list,
    ):
        return []

    return instruments


def get_tickers():

    data = http_get(
        TICKERS_URL
    )

    tickers = data.get(
        "tickers",
        [],
    )

    if not isinstance(
        tickers,
        list,
    ):
        return []

    return tickers


def normalize_symbol(value):

    if not value:
        return ""

    return str(
        value
    ).upper().strip()


def is_usd_futures_symbol(
    symbol,
):

    symbol = normalize_symbol(
        symbol
    )

    return (
        symbol.startswith("PF_")
        and symbol.endswith("USD")
    )


def ticker_symbol(ticker):

    if not isinstance(
        ticker,
        dict,
    ):
        return ""

    return normalize_symbol(
        first_value(
            ticker.get("symbol"),
            ticker.get("pair"),
            ticker.get("instrument"),
        )
    )


def ticker_last_price(ticker):

    if not isinstance(
        ticker,
        dict,
    ):
        return 0.0

    value = first_value(
        ticker.get("last"),
        ticker.get("lastPrice"),
        ticker.get("markPrice"),
    )

    return safe_float(
        value,
        0.0,
    )


def ticker_volume(ticker):

    if not isinstance(
        ticker,
        dict,
    ):
        return 0.0

    value = first_value(
        ticker.get("vol24h"),
        ticker.get("volume24h"),
        ticker.get("volume"),
        ticker.get("volume_quote"),
    )

    return safe_float(
        value,
        0.0,
    )


def build_market_list():

    instruments = get_instruments()

    tickers = get_tickers()

    ticker_map = {}

    for ticker in tickers:

        symbol = ticker_symbol(
            ticker
        )

        if symbol:
            ticker_map[symbol] = ticker

    markets = []

    for instrument in instruments:

        if not isinstance(
            instrument,
            dict,
        ):
            continue

        symbol = normalize_symbol(
            first_value(
                instrument.get("symbol"),
                instrument.get("name"),
                instrument.get("pair"),
            )
        )

        if not symbol:
            continue

        if not is_usd_futures_symbol(
            symbol
        ):
            continue

        ticker = ticker_map.get(
            symbol,
            {},
        )

        markets.append(
            {
                "symbol": symbol,
                "volume": ticker_volume(
                    ticker
                ),
                "last": ticker_last_price(
                    ticker
                ),
            }
        )

    markets.sort(
        key=lambda x: x.get(
            "volume",
            0.0,
        ),
        reverse=True,
    )

    return markets[:TOP_N]


# ============================================================
# CANDLE PARSING
# ============================================================

def normalize_timestamp(value):

    value = safe_float(
        value,
        0.0,
    )

    if value <= 0:
        return 0

    if value > 1_000_000_000_000:
        value /= 1000.0

    return int(value)


def parse_candle(raw):

    if isinstance(
        raw,
        dict,
    ):

        ts = first_value(
            raw.get("time"),
            raw.get("timestamp"),
            raw.get("t"),
        )

        o = first_value(
            raw.get("open"),
            raw.get("o"),
        )

        h = first_value(
            raw.get("high"),
            raw.get("h"),
        )

        l = first_value(
            raw.get("low"),
            raw.get("l"),
        )

        c = first_value(
            raw.get("close"),
            raw.get("c"),
        )

        v = first_value(
            raw.get("volume"),
            raw.get("v"),
            0.0,
        )

        return {
            "time": normalize_timestamp(
                ts
            ),
            "open": safe_float(
                o,
                0.0,
            ),
            "high": safe_float(
                h,
                0.0,
            ),
            "low": safe_float(
                l,
                0.0,
            ),
            "close": safe_float(
                c,
                0.0,
            ),
            "volume": safe_float(
                v,
                0.0,
            ),
        }

    if (
        isinstance(
            raw,
            (list, tuple),
        )
        and len(raw) >= 5
    ):

        return {
            "time": normalize_timestamp(
                raw[0]
            ),
            "open": safe_float(
                raw[1],
                0.0,
            ),
            "high": safe_float(
                raw[2],
                0.0,
            ),
            "low": safe_float(
                raw[3],
                0.0,
            ),
            "close": safe_float(
                raw[4],
                0.0,
            ),
            "volume": (
                safe_float(
                    raw[5],
                    0.0,
                )
                if len(raw) > 5
                else 0.0
            ),
        }

    return None


def get_candles(
    symbol,
    resolution,
    limit=250,
):

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution,
    )

    data = http_get(
        url
    )

    raw_candles = []

    if isinstance(
        data,
        dict,
    ):

        raw_candles = first_value(
            data.get("candles"),
            data.get("data"),
            data.get("results"),
            [],
        )

    elif isinstance(
        data,
        list,
    ):

        raw_candles = data

    if not isinstance(
        raw_candles,
        list,
    ):
        raw_candles = []

    candles = []

    for raw in raw_candles:

        candle = parse_candle(
            raw
        )

        if candle is None:
            continue

        if candle["time"] <= 0:
            continue

        if candle["close"] <= 0:
            continue

        if candle["high"] <= 0:
            continue

        if candle["low"] <= 0:
            continue

        candles.append(
            candle
        )

    # --------------------------------------------------------
    # Sort + deduplicate
    # --------------------------------------------------------

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

    candle_seconds = (
        resolution * 60
    )

    current_ts = int(
        time.time()
    )

    closed = [
        candle
        for candle in candles
        if (
            candle["time"]
            + candle_seconds
            <= current_ts
        )
    ]

    if len(closed) > limit:
        closed = closed[-limit:]

    return closed


# ============================================================
# INDICATORS
# ============================================================

def true_range(candles):

    if not candles:
        return []

    result = []

    previous_close = None

    for candle in candles:

        high = candle["high"]
        low = candle["low"]

        if previous_close is None:

            value = (
                high - low
            )

        else:

            value = max(
                high - low,
                abs(
                    high
                    - previous_close
                ),
                abs(
                    low
                    - previous_close
                ),
            )

        result.append(
            value
        )

        previous_close = candle[
            "close"
        ]

    return result


def atr(
    candles,
    period=14,
):

    if len(candles) < period:
        return None

    values = true_range(
        candles
    )[-period:]

    if not values:
        return None

    return sum(values) / len(
        values
    )


def rolling_mid_at(
    candles,
    end_index,
    period,
):

    start_index = (
        end_index
        - period
        + 1
    )

    if start_index < 0:
        return None

    section = candles[
        start_index:
        end_index + 1
    ]

    if len(section) != period:
        return None

    highest = max(
        candle["high"]
        for candle in section
    )

    lowest = min(
        candle["low"]
        for candle in section
    )

    return (
        highest + lowest
    ) / 2.0


# ============================================================
# STANDARD ICHIMOKU
# ============================================================

def ichimoku(candles):

    required = (
        SENKOU_B
        + DISPLACEMENT
    )

    if len(candles) < required:
        return None

    current_index = (
        len(candles) - 1
    )

    # --------------------------------------------------------
    # Current Tenkan / Kijun
    # --------------------------------------------------------

    tenkan = rolling_mid_at(
        candles,
        current_index,
        TENKAN,
    )

    kijun = rolling_mid_at(
        candles,
        current_index,
        KIJUN,
    )

    if (
        tenkan is None
        or kijun is None
    ):
        return None

    # --------------------------------------------------------
    # IMPORTANT:
    # Current visible cloud must use values calculated
    # DISPLACEMENT candles ago.
    #
    # This prevents using the unshifted cloud and makes the
    # current cloud comparable with current price.
    # --------------------------------------------------------

    cloud_index = (
        current_index
        - DISPLACEMENT
    )

    if cloud_index < 0:
        return None

    tenkan_previous = (
        rolling_mid_at(
            candles,
            cloud_index,
            TENKAN,
        )
    )

    kijun_previous = (
        rolling_mid_at(
            candles,
            cloud_index,
            KIJUN,
        )
    )

    span_b = rolling_mid_at(
        candles,
        cloud_index,
        SENKOU_B,
    )

    if (
        tenkan_previous is None
        or kijun_previous is None
        or span_b is None
    ):
        return None

    span_a = (
        tenkan_previous
        + kijun_previous
    ) / 2.0

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    price = candles[
        current_index
    ]["close"]

    return {
        "price": price,
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(candles):

    data = ichimoku(
        candles
    )

    if not data:
        return 0.0, None

    price = data["price"]

    tenkan = data["tenkan"]
    kijun = data["kijun"]

    cloud_top = data[
        "cloud_top"
    ]

    cloud_bottom = data[
        "cloud_bottom"
    ]

    score = 0.0

    # --------------------------------------------------------
    # Price vs Cloud
    # --------------------------------------------------------

    if price > cloud_top:
        score += 4.0

    elif price < cloud_bottom:
        score -= 4.0

    # --------------------------------------------------------
    # Tenkan vs Kijun
    # --------------------------------------------------------

    if tenkan > kijun:
        score += 3.0

    elif tenkan < kijun:
        score -= 3.0

    # --------------------------------------------------------
    # Price vs Kijun
    # --------------------------------------------------------

    if price > kijun:
        score += 2.0

    elif price < kijun:
        score -= 2.0

    # --------------------------------------------------------
    # Price vs Tenkan
    # --------------------------------------------------------

    if price > tenkan:
        score += 1.0

    elif price < tenkan:
        score -= 1.0

    score = max(
        -10.0,
        min(
            10.0,
            score,
        ),
    )

    return score, data


# ============================================================
# MTF ANALYSIS
# ============================================================

def get_mtf_analysis(
    symbol
):

    candles_1h = get_candles(
        symbol,
        TF_1H,
        250,
    )

    candles_30m = get_candles(
        symbol,
        TF_30M,
        250,
    )

    candles_15m = get_candles(
        symbol,
        TF_15M,
        250,
    )

    candles_5m = get_candles(
        symbol,
        TF_5M,
        250,
    )

    if (
        len(candles_1h) < 100
        or len(candles_30m) < 100
        or len(candles_15m) < 100
        or len(candles_5m) < 100
    ):

        return None

    score_1h, ichi_1h = (
        ichimoku_score(
            candles_1h
        )
    )

    score_30m, ichi_30m = (
        ichimoku_score(
            candles_30m
        )
    )

    score_15m, ichi_15m = (
        ichimoku_score(
            candles_15m
        )
    )

    score_5m, ichi_5m = (
        ichimoku_score(
            candles_5m
        )
    )

    if (
        ichi_1h is None
        or ichi_30m is None
        or ichi_15m is None
        or ichi_5m is None
    ):

        return None

    trend_score = (
        score_1h * WEIGHT_1H
        + score_30m * WEIGHT_30M
        + score_15m * WEIGHT_15M
    )

    return {
        "symbol": symbol,

        "candles_1h": candles_1h,
        "candles_30m": candles_30m,
        "candles_15m": candles_15m,
        "candles_5m": candles_5m,

        "score_1h": score_1h,
        "score_30m": score_30m,
        "score_15m": score_15m,
        "score_5m": score_5m,

        "trend_score": trend_score,

        "ichi_1h": ichi_1h,
        "ichi_30m": ichi_30m,
        "ichi_15m": ichi_15m,
        "ichi_5m": ichi_5m,
    }


# ============================================================
# PULLBACK SCORE
# ============================================================

def pullback_score(
    direction,
    candles_15m,
    ichi_15m,
):

    if (
        not candles_15m
        or not ichi_15m
    ):
        return 0.0

    price = candles_15m[
        -1
    ]["close"]

    tenkan = ichi_15m[
        "tenkan"
    ]

    kijun = ichi_15m[
        "kijun"
    ]

    atr_value = atr(
        candles_15m,
        14,
    )

    if (
        not atr_value
        or atr_value <= 0
    ):

        atr_value = (
            price * 0.005
        )

    distance_tenkan = abs(
        price - tenkan
    )

    distance_kijun = abs(
        price - kijun
    )

    score = 0.0

    if direction == "LONG":

        if price >= tenkan:
            score += 2.0

        if (
            distance_tenkan
            <= atr_value
            * PULLBACK_ATR_MULTIPLIER
        ):
            score += 2.0

        if price >= kijun:
            score += 1.0

        if (
            distance_kijun
            <= atr_value * 2.0
        ):
            score += 1.0

    else:

        if price <= tenkan:
            score += 2.0

        if (
            distance_tenkan
            <= atr_value
            * PULLBACK_ATR_MULTIPLIER
        ):
            score += 2.0

        if price <= kijun:
            score += 1.0

        if (
            distance_kijun
            <= atr_value * 2.0
        ):
            score += 1.0

    return min(
        6.0,
        score,
    )


# ============================================================
# 5M TRIGGER
# ============================================================

def trigger_score(
    direction,
    candles,
):

    if len(candles) < 5:
        return 0.0

    current = candles[
        -1
    ]

    previous = candles[
        -2
    ]

    score = 0.0

    if direction == "LONG":

        if (
            current["close"]
            > current["open"]
        ):
            score += 2.0

        if (
            current["close"]
            > previous["high"]
        ):
            score += 3.0

        elif (
            current["close"]
            > previous["close"]
        ):
            score += 1.0

        if (
            current["low"]
            >= previous["low"]
        ):
            score += 1.0

    else:

        if (
            current["close"]
            < current["open"]
        ):
            score += 2.0

        if (
            current["close"]
            < previous["low"]
        ):
            score += 3.0

        elif (
            current["close"]
            < previous["close"]
        ):
            score += 1.0

        if (
            current["high"]
            <= previous["high"]
        ):
            score += 1.0

    return min(
        6.0,
        score,
    )


# ============================================================
# STRUCTURAL SL / TP
# ============================================================

def structural_levels(
    direction,
    candles_5m,
    candles_15m,
    entry,
):

    if (
        len(candles_5m) < 10
        or len(candles_15m) < 10
        or entry <= 0
    ):
        return None

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

    atr_5m = atr(
        candles_5m,
        14,
    )

    if (
        not atr_5m
        or atr_5m <= 0
    ):

        atr_5m = (
            entry * 0.005
        )

    # --------------------------------------------------------
    # Structure
    # --------------------------------------------------------

    recent_5m = candles_5m[
        -10:
    ]

    recent_15m = candles_15m[
        -6:
    ]

    swing_low = min(
        min(
            candle["low"]
            for candle in recent_5m
        ),
        min(
            candle["low"]
            for candle in recent_15m
        ),
    )

    swing_high = max(
        max(
            candle["high"]
            for candle in recent_5m
        ),
        max(
            candle["high"]
            for candle in recent_15m
        ),
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        sl_raw = (
            swing_low
            - atr_5m * 0.10
        )

        minimum_risk = (
            entry
            * MIN_RISK_PCT
            / 100.0
        )

        risk_raw = (
            entry - sl_raw
        )

        if risk_raw < minimum_risk:

            sl_raw = (
                entry
                - minimum_risk
            )

        if sl_raw >= entry:
            return None

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        sl_raw = (
            swing_high
            + atr_5m * 0.10
        )

        minimum_risk = (
            entry
            * MIN_RISK_PCT
            / 100.0
        )

        risk_raw = (
            sl_raw - entry
        )

        if risk_raw < minimum_risk:

            sl_raw = (
                entry
                + minimum_risk
            )

        if sl_raw <= entry:
            return None

    # --------------------------------------------------------
    # Round ENTRY + SL first
    # --------------------------------------------------------

    entry_final = round_price(
        entry
    )

    sl_final = round_price(
        sl_raw
    )

    if entry_final <= 0:
        return None

    # --------------------------------------------------------
    # Recalculate exact risk after rounding
    # --------------------------------------------------------

    if direction == "LONG":

        if sl_final >= entry_final:
            return None

        risk = (
            entry_final
            - sl_final
        )

        minimum_risk = (
            entry_final
            * MIN_RISK_PCT
            / 100.0
        )

        if risk < minimum_risk:

            sl_final = round_price(
                entry_final
                - minimum_risk
            )

            risk = (
                entry_final
                - sl_final
            )

        tp_raw = (
            entry_final
            + risk * RR
        )

        tp_final = round_price(
            tp_raw
        )

        if tp_final <= entry_final:
            return None

    else:

        if sl_final <= entry_final:
            return None

        risk = (
            sl_final
            - entry_final
        )

        minimum_risk = (
            entry_final
            * MIN_RISK_PCT
            / 100.0
        )

        if risk < minimum_risk:

            sl_final = round_price(
                entry_final
                + minimum_risk
            )

            risk = (
                sl_final
                - entry_final
            )

        tp_raw = (
            entry_final
            - risk * RR
        )

        tp_final = round_price(
            tp_raw
        )

        if tp_final >= entry_final:
            return None

    # --------------------------------------------------------
    # Final percentages
    # --------------------------------------------------------

    risk_pct = (
        abs(
            entry_final
            - sl_final
        )
        / entry_final
        * 100.0
    )

    reward_pct = (
        abs(
            tp_final
            - entry_final
        )
        / entry_final
        * 100.0
    )

    if risk_pct < (
        MIN_RISK_PCT - 0.000001
    ):
        return None

    return {
        "entry": entry_final,
        "sl": sl_final,
        "tp": tp_final,
        "risk_pct": risk_pct,
        "reward_pct": reward_pct,
    }


# ============================================================
# RISK QUALITY
# ============================================================

def risk_quality(levels):

    if not levels:
        return 0.0

    risk_pct = safe_float(
        levels.get(
            "risk_pct"
        ),
        0.0,
    )

    if risk_pct < MIN_RISK_PCT:
        return 0.0

    if risk_pct <= 0.75:
        return 10.0

    if risk_pct <= 1.00:
        return 8.0

    if risk_pct <= 1.50:
        return 6.0

    if risk_pct <= 2.00:
        return 4.0

    if risk_pct <= 3.00:
        return 2.0

    return 0.0


# ============================================================
# FINAL SCORE
# ============================================================

def final_rank_score(
    direction,
    mtf,
    levels,
    pullback,
    trigger,
):

    if (
        not mtf
        or not levels
    ):
        return -999.0

    if direction == "LONG":

        trend_component = (
            max(
                0.0,
                mtf["score_1h"],
            ) * 3.0
            + max(
                0.0,
                mtf["score_30m"],
            ) * 2.0
            + max(
                0.0,
                mtf["score_15m"],
            ) * 1.0
        )

    else:

        trend_component = (
            abs(
                min(
                    0.0,
                    mtf["score_1h"],
                )
            ) * 3.0
            + abs(
                min(
                    0.0,
                    mtf["score_30m"],
                )
            ) * 2.0
            + abs(
                min(
                    0.0,
                    mtf["score_15m"],
                )
            ) * 1.0
        )

    quality = risk_quality(
        levels
    )

    score = (
        trend_component
        + pullback
        + trigger
        + quality
    )

    return round(
        score,
        2,
    )


# ============================================================
# CANDIDATE ANALYSIS
# ============================================================

def analyze_candidate(
    symbol,
    market_last,
):

    reasons = []

    try:

        mtf = get_mtf_analysis(
            symbol
        )

        if not mtf:

            reasons.append(
                "INSUFFICIENT_DATA"
            )

            return [], reasons

        candidates = []

        entry = (
            mtf["candles_5m"][
                -1
            ]["close"]
            if mtf["candles_5m"]
            else market_last
        )

        if entry <= 0:

            reasons.append(
                "INVALID_ENTRY"
            )

            return [], reasons

        # ====================================================
        # LONG FILTERS
        # ====================================================

        long_structure = (
            mtf["score_1h"] >= 4.0
            and mtf["score_30m"] >= 2.0
            and mtf["score_15m"] >= 0.0
            and mtf["score_5m"] >= 0.0
        )

        if long_structure:

            pb_long = pullback_score(
                "LONG",
                mtf["candles_15m"],
                mtf["ichi_15m"],
            )

            trigger_long = trigger_score(
                "LONG",
                mtf["candles_5m"],
            )

            if trigger_long < 1.0:

                reasons.append(
                    "LONG_TRIGGER"
                )

            else:

                levels_long = structural_levels(
                    "LONG",
                    mtf["candles_5m"],
                    mtf["candles_15m"],
                    entry,
                )

                if not levels_long:

                    reasons.append(
                        "LONG_LEVELS"
                    )

                else:

                    score_long = final_rank_score(
                        "LONG",
                        mtf,
                        levels_long,
                        pb_long,
                        trigger_long,
                    )

                    if (
                        score_long
                        < MIN_SCORE
                    ):

                        reasons.append(
                            "LONG_SCORE"
                        )

                    else:

                        candidates.append(
                            {
                                "symbol": symbol,
                                "direction": "LONG",
                                "entry": levels_long["entry"],
                                "sl": levels_long["sl"],
                                "tp": levels_long["tp"],
                                "risk_pct": levels_long["risk_pct"],
                                "reward_pct": levels_long["reward_pct"],
                                "score": score_long,
                                "trend_score": mtf["trend_score"],
                                "score_1h": mtf["score_1h"],
                                "score_30m": mtf["score_30m"],
                                "score_15m": mtf["score_15m"],
                                "score_5m": mtf["score_5m"],
                                "pullback_score": pb_long,
                                "trigger_score": trigger_long,
                            }
                        )

        else:

            reasons.append(
                "LONG_MTF"
            )

        # ====================================================
        # SHORT FILTERS
        # ====================================================

        short_structure = (
            mtf["score_1h"] <= -4.0
            and mtf["score_30m"] <= -2.0
            and mtf["score_15m"] <= 0.0
            and mtf["score_5m"] <= 0.0
        )

        if short_structure:

            pb_short = pullback_score(
                "SHORT",
                mtf["candles_15m"],
                mtf["ichi_15m"],
            )

            trigger_short = trigger_score(
                "SHORT",
                mtf["candles_5m"],
            )

            if trigger_short < 1.0:

                reasons.append(
                    "SHORT_TRIGGER"
                )

            else:

                levels_short = structural_levels(
                    "SHORT",
                    mtf["candles_5m"],
                    mtf["candles_15m"],
                    entry,
                )

                if not levels_short:

                    reasons.append(
                        "SHORT_LEVELS"
                    )

                else:

                    score_short = final_rank_score(
                        "SHORT",
                        mtf,
                        levels_short,
                        pb_short,
                        trigger_short,
                    )

                    if (
                        score_short
                        < MIN_SCORE
                    ):

                        reasons.append(
                            "SHORT_SCORE"
                        )

                    else:

                        candidates.append(
                            {
                                "symbol": symbol,
                                "direction": "SHORT",
                                "entry": levels_short["entry"],
                                "sl": levels_short["sl"],
                                "tp": levels_short["tp"],
                                "risk_pct": levels_short["risk_pct"],
                                "reward_pct": levels_short["reward_pct"],
                                "score": score_short,
                                "trend_score": mtf["trend_score"],
                                "score_1h": mtf["score_1h"],
                                "score_30m": mtf["score_30m"],
                                "score_15m": mtf["score_15m"],
                                "score_5m": mtf["score_5m"],
                                "pullback_score": pb_short,
                                "trigger_score": trigger_short,
                            }
                        )

        else:

            reasons.append(
                "SHORT_MTF"
            )

        return (
            candidates,
            reasons,
        )

    except Exception as e:

        print(
            f"[WARN] Candidate failed "
            f"{symbol}: {e}"
        )

        reasons.append(
            "ERROR"
        )

        return [], reasons


# ============================================================
# TRADE ID
# ============================================================

def make_trade_id(
    symbol,
    direction,
):

    timestamp = int(
        time.time() * 1000
    )

    return (
        f"{symbol}_"
        f"{direction}_"
        f"{timestamp}"
    )


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    candidate
):

    return {
        "id": make_trade_id(
            candidate["symbol"],
            candidate["direction"],
        ),

        "symbol": candidate[
            "symbol"
        ],

        "direction": candidate[
            "direction"
        ],

        "entry": candidate[
            "entry"
        ],

        "sl": candidate[
            "sl"
        ],

        "tp": candidate[
            "tp"
        ],

        "score": candidate[
            "score"
        ],

        "risk_pct": candidate[
            "risk_pct"
        ],

        "reward_pct": candidate[
            "reward_pct"
        ],

        "opened_at": iso_now(),

        "status": "OPEN",

        "current_price": candidate[
            "entry"
        ],

        "current_pnl_pct": 0.0,

        "result": None,

        "closed_at": None,

        "close_price": None,

        "close_reason": None,

        "timeout": False,
    }


# ============================================================
# LIVE PRICE MAP
# ============================================================

def get_live_price_map():

    result = {}

    try:

        tickers = get_tickers()

        for ticker in tickers:

            symbol = ticker_symbol(
                ticker
            )

            if not symbol:
                continue

            price = ticker_last_price(
                ticker
            )

            if price > 0:
                result[symbol] = price

    except Exception as e:

        print(
            f"[WARN] Live ticker map "
            f"failed: {e}"
        )

    return result


def get_live_price(
    symbol,
    price_map=None,
):

    if price_map is not None:

        price = price_map.get(
            normalize_symbol(
                symbol
            )
        )

        if price and price > 0:
            return price

    try:

        tickers = get_tickers()

        target = normalize_symbol(
            symbol
        )

        for ticker in tickers:

            if (
                ticker_symbol(
                    ticker
                )
                != target
            ):
                continue

            price = ticker_last_price(
                ticker
            )

            if price > 0:
                return price

    except Exception as e:

        print(
            f"[WARN] Live price failed "
            f"{symbol}: {e}"
        )

    return None


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    close_price,
    reason,
):

    direction = trade[
        "direction"
    ]

    entry = safe_float(
        trade.get("entry"),
        0.0,
    )

    close_price = safe_float(
        close_price,
        entry,
    )

    if entry <= 0:
        entry = close_price

    pnl_pct = pct_change(
        entry,
        close_price,
        direction,
    )

    if reason == "TIMEOUT":

        result = "TIMEOUT"

    elif pnl_pct > 0:

        result = "WIN"

    elif pnl_pct < 0:

        result = "LOSS"

    else:

        result = "BREAKEVEN"

    trade["status"] = "CLOSED"

    trade["current_price"] = (
        close_price
    )

    trade["current_pnl_pct"] = (
        pnl_pct
    )

    trade["close_price"] = (
        close_price
    )

    trade["close_reason"] = (
        reason
    )

    trade["closed_at"] = iso_now()

    trade["result"] = result

    trade["timeout"] = (
        reason == "TIMEOUT"
    )

    return trade


# ============================================================
# CHECK HISTORICAL 5M CANDLES
# ============================================================

def find_historical_exit(
    trade,
    candles,
):

    if not candles:
        return None

    direction = trade[
        "direction"
    ]

    sl = safe_float(
        trade.get("sl"),
        0.0,
    )

    tp = safe_float(
        trade.get("tp"),
        0.0,
    )

    opened_at = parse_datetime(
        trade.get("opened_at")
    )

    opened_ts = timestamp_from_datetime(
        opened_at
    )

    for candle in candles:

        candle_start = candle[
            "time"
        ]

        candle_end = (
            candle_start
            + TF_5M * 60
        )

        # Ignore candles completely before entry
        if candle_end <= opened_ts:
            continue

        high = candle[
            "high"
        ]

        low = candle[
            "low"
        ]

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            if hit_sl and hit_tp:

                # Same candle touched both levels.
                # Conservative assumption:
                # SL happened first.
                return {
                    "price": sl,
                    "reason": "SL",
                }

            if hit_sl:

                return {
                    "price": sl,
                    "reason": "SL",
                }

            if hit_tp:

                return {
                    "price": tp,
                    "reason": "TP",
                }

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            if hit_sl and hit_tp:

                return {
                    "price": sl,
                    "reason": "SL",
                }

            if hit_sl:

                return {
                    "price": sl,
                    "reason": "SL",
                }

            if hit_tp:

                return {
                    "price": tp,
                    "reason": "TP",
                }

    return None


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    state
):

    open_trades = state.get(
        "open_trades",
        [],
    )

    remaining = []

    history = state.get(
        "history",
        [],
    )

    if not open_trades:

        state["open_trades"] = []

        state["history"] = history

        return state

    print(
        f"[INFO] Monitoring "
        f"{len(open_trades)} open trade(s)"
    )

    # One ticker request for all trades
    price_map = get_live_price_map()

    for trade in open_trades:

        try:

            symbol = normalize_symbol(
                trade.get("symbol")
            )

            direction = trade.get(
                "direction"
            )

            entry = safe_float(
                trade.get("entry"),
                0.0,
            )

            sl = safe_float(
                trade.get("sl"),
                0.0,
            )

            tp = safe_float(
                trade.get("tp"),
                0.0,
            )

            if (
                not symbol
                or not direction
                or entry <= 0
                or sl <= 0
                or tp <= 0
            ):

                print(
                    f"[WARN] Invalid trade "
                    f"state: {trade}"
                )

                remaining.append(
                    trade
                )

                continue

            live = get_live_price(
                symbol,
                price_map,
            )

            if live and live > 0:

                trade[
                    "current_price"
                ] = live

                trade[
                    "current_pnl_pct"
                ] = pct_change(
                    entry,
                    live,
                    direction,
                )

            # ------------------------------------------------
            # Historical 5m candle monitoring
            # ------------------------------------------------

            historical_exit = None

            try:

                candles_5m = get_candles(
                    symbol,
                    TF_5M,
                    250,
                )

                historical_exit = (
                    find_historical_exit(
                        trade,
                        candles_5m,
                    )
                )

            except Exception as e:

                print(
                    f"[WARN] Historical "
                    f"monitor failed "
                    f"{symbol}: {e}"
                )

            if historical_exit:

                closed = close_trade(
                    trade,
                    historical_exit[
                        "price"
                    ],
                    historical_exit[
                        "reason"
                    ],
                )

                print(
                    f"[CLOSE] "
                    f"{symbol} "
                    f"{direction} "
                    f"{historical_exit['reason']} "
                    f"@ {format_price(historical_exit['price'])} "
                    f"P/L={closed['current_pnl_pct']:+.2f}%"
                )

                history.append(
                    closed
                )

                continue

            # ------------------------------------------------
            # Live fallback
            # ------------------------------------------------

            if live and live > 0:

                if direction == "LONG":

                    if live <= sl:

                        closed = close_trade(
                            trade,
                            sl,
                            "SL",
                        )

                        print(
                            f"[CLOSE] "
                            f"{symbol} "
                            f"LONG SL"
                        )

                        history.append(
                            closed
                        )

                        continue

                    if live >= tp:

                        closed = close_trade(
                            trade,
                            tp,
                            "TP",
                        )

                        print(
                            f"[CLOSE] "
                            f"{symbol} "
                            f"LONG TP"
                        )

                        history.append(
                            closed
                        )

                        continue

                else:

                    if live >= sl:

                        closed = close_trade(
                            trade,
                            sl,
                            "SL",
                        )

                        print(
                            f"[CLOSE] "
                            f"{symbol} "
                            f"SHORT SL"
                        )

                        history.append(
                            closed
                        )

                        continue

                    if live <= tp:

                        closed = close_trade(
                            trade,
                            tp,
                            "TP",
                        )

                        print(
                            f"[CLOSE] "
                            f"{symbol} "
                            f"SHORT TP"
                        )

                        history.append(
                            closed
                        )

                        continue

            # ------------------------------------------------
            # Timeout
            # ------------------------------------------------

            opened_at = parse_datetime(
                trade.get(
                    "opened_at"
                )
            )

            elapsed = (
                now_utc()
                - opened_at
            )

            if elapsed >= timedelta(
                hours=TIMEOUT_HOURS
            ):

                timeout_price = (
                    live
                    if live and live > 0
                    else entry
                )

                closed = close_trade(
                    trade,
                    timeout_price,
                    "TIMEOUT",
                )

                print(
                    f"[CLOSE] "
                    f"{symbol} "
                    f"TIMEOUT "
                    f"P/L={closed['current_pnl_pct']:+.2f}%"
                )

                history.append(
                    closed
                )

                continue

            remaining.append(
                trade
            )

        except Exception as e:

            print(
                f"[WARN] Monitor failed "
                f"{trade.get('symbol')}: "
                f"{e}"
            )

            remaining.append(
                trade
            )

    state[
        "open_trades"
    ] = remaining

    state[
        "history"
    ] = history

    return state


# ============================================================
# OPEN SYMBOLS
# ============================================================

def open_symbols(
    state
):

    result = set()

    for trade in state.get(
        "open_trades",
        [],
    ):

        symbol = normalize_symbol(
            trade.get("symbol")
        )

        if symbol:
            result.add(
                symbol
            )

    return result


# ============================================================
# SCAN ALL MARKETS
# ============================================================

def scan_all_markets(
    markets,
    state,
):

    candidates = []

    opened = open_symbols(
        state
    )

    rejection_counter = Counter()

    print(
        f"[INFO] Markets: "
        f"{len(markets)}"
    )

    print(
        f"[INFO] Open symbols: "
        f"{len(opened)}"
    )

    for index, market in enumerate(
        markets,
        start=1,
    ):

        symbol = market[
            "symbol"
        ]

        if symbol in opened:

            print(
                f"[{index:03d}/"
                f"{len(markets):03d}] "
                f"{symbol} "
                f"SKIP OPEN"
            )

            continue

        print(
            f"[{index:03d}/"
            f"{len(markets):03d}] "
            f"{symbol}"
        )

        market_last = safe_float(
            market.get(
                "last"
            ),
            0.0,
        )

        result, reasons = (
            analyze_candidate(
                symbol,
                market_last,
            )
        )

        for reason in reasons:

            rejection_counter[
                reason
            ] += 1

        if result:

            for candidate in result:

                candidates.append(
                    candidate
                )

                print(
                    f"    "
                    f"✅ {candidate['direction']} "
                    f"Score={candidate['score']:.1f} "
                    f"Risk={candidate['risk_pct']:.2f}% "
                    f"RR={candidate['reward_pct'] / max(candidate['risk_pct'], 0.000001):.2f}"
                )

        else:

            # Print the main rejection reason
            if reasons:

                print(
                    f"    "
                    f"❌ {','.join(reasons)}"
                )

    return (
        candidates,
        rejection_counter,
    )


# ============================================================
# SELECT BEST TRADES
# ============================================================

def select_best_trades(
    candidates,
    state,
):

    open_trades = state.get(
        "open_trades",
        [],
    )

    long_count = sum(
        1
        for trade in open_trades
        if trade.get(
            "direction"
        ) == "LONG"
    )

    short_count = sum(
        1
        for trade in open_trades
        if trade.get(
            "direction"
        ) == "SHORT"
    )

    total = len(
        open_trades
    )

    selected = []

    existing_symbols = (
        open_symbols(
            state
        )
    )

    # --------------------------------------------------------
    # Deduplicate
    # --------------------------------------------------------

    unique = {}

    for candidate in candidates:

        key = (
            candidate["symbol"],
            candidate["direction"],
        )

        old = unique.get(
            key
        )

        if (
            old is None
            or candidate["score"]
            > old["score"]
        ):

            unique[key] = (
                candidate
            )

    candidates = list(
        unique.values()
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    longs = sorted(
        [
            candidate
            for candidate in candidates
            if candidate[
                "direction"
            ] == "LONG"
        ],
        key=lambda x: x[
            "score"
        ],
        reverse=True,
    )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    shorts = sorted(
        [
            candidate
            for candidate in candidates
            if candidate[
                "direction"
            ] == "SHORT"
        ],
        key=lambda x: x[
            "score"
        ],
        reverse=True,
    )

    selected_symbols = set()

    # --------------------------------------------------------
    # Best LONG
    # --------------------------------------------------------

    for candidate in longs:

        if total >= MAX_OPEN_TRADES:
            break

        if long_count >= MAX_LONG_TRADES:
            break

        symbol = candidate[
            "symbol"
        ]

        if symbol in existing_symbols:
            continue

        if symbol in selected_symbols:
            continue

        selected.append(
            candidate
        )

        selected_symbols.add(
            symbol
        )

        long_count += 1
        total += 1

    # --------------------------------------------------------
    # Best SHORT
    # --------------------------------------------------------

    for candidate in shorts:

        if total >= MAX_OPEN_TRADES:
            break

        if short_count >= MAX_SHORT_TRADES:
            break

        symbol = candidate[
            "symbol"
        ]

        if symbol in existing_symbols:
            continue

        if symbol in selected_symbols:
            continue

        selected.append(
            candidate
        )

        selected_symbols.add(
            symbol
        )

        short_count += 1
        total += 1

    return selected


# ============================================================
# ADD SELECTED TRADES
# ============================================================

def add_selected_trades(
    state,
    selected,
):

    for candidate in selected:

        trade = create_trade(
            candidate
        )

        state[
            "open_trades"
        ].append(
            trade
        )

        print(
            f"[OPEN] "
            f"{trade['symbol']} "
            f"{trade['direction']} "
            f"Entry={format_price(trade['entry'])} "
            f"SL={format_price(trade['sl'])} "
            f"TP={format_price(trade['tp'])} "
            f"Score={trade['score']:.1f} "
            f"Risk={trade['risk_pct']:.2f}% "
            f"Reward={trade['reward_pct']:.2f}%"
        )

    return state


# ============================================================
# PERFORMANCE
# ============================================================

def performance(
    state
):

    history = state.get(
        "history",
        [],
    )

    closed = [
        trade
        for trade in history
        if (
            isinstance(
                trade,
                dict,
            )
            and trade.get(
                "status"
            ) == "CLOSED"
        )
    ]

    wins = sum(
        1
        for trade in closed
        if trade.get(
            "result"
        ) == "WIN"
    )

    losses = sum(
        1
        for trade in closed
        if trade.get(
            "result"
        ) == "LOSS"
    )

    breakeven = sum(
        1
        for trade in closed
        if trade.get(
            "result"
        ) == "BREAKEVEN"
    )

    timeout = sum(
        1
        for trade in closed
        if (
            trade.get(
                "result"
            ) == "TIMEOUT"
            or trade.get(
                "close_reason"
            ) == "TIMEOUT"
        )
    )

    decisive = (
        wins + losses
    )

    if decisive > 0:

        wr = (
            wins
            / decisive
            * 100.0
        )

    else:

        wr = 0.0

    total_pnl = sum(
        safe_float(
            trade.get(
                "current_pnl_pct"
            ),
            0.0,
        )
        for trade in closed
    )

    return {
        "trades": len(
            closed
        ),
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "timeout": timeout,
        "wr": wr,
        "total_pnl": total_pnl,
    }


# ============================================================
# REPORT
# ============================================================

def generate_report(
    state,
    scan_info=None,
):

    open_trades = state.get(
        "open_trades",
        [],
    )

    perf = performance(
        state
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
        "⏱ 5m CLOSED | TOP 100"
    )

    lines.append(
        "🤖 MTF ICHIMOKU | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/"
        f"{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append(
            "No open trades"
        )

    else:

        for index, trade in enumerate(
            open_trades,
            start=1,
        ):

            symbol = trade[
                "symbol"
            ]

            direction = trade[
                "direction"
            ]

            icon = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            entry = safe_float(
                trade.get(
                    "entry"
                ),
                0.0,
            )

            now_price = safe_float(
                trade.get(
                    "current_price"
                ),
                entry,
            )

            sl = safe_float(
                trade.get(
                    "sl"
                ),
                0.0,
            )

            tp = safe_float(
                trade.get(
                    "tp"
                ),
                0.0,
            )

            if direction == "LONG":

                sl_pct = (
                    entry - sl
                ) / entry * 100.0

                tp_pct = (
                    tp - entry
                ) / entry * 100.0

            else:

                sl_pct = (
                    sl - entry
                ) / entry * 100.0

                tp_pct = (
                    entry - tp
                ) / entry * 100.0

            pnl = pct_change(
                entry,
                now_price,
                direction,
            )

            score = safe_float(
                trade.get(
                    "score"
                ),
                0.0,
            )

            risk = safe_float(
                trade.get(
                    "risk_pct"
                ),
                sl_pct,
            )

            rr_actual = (
                tp_pct
                / risk
                if risk > 0
                else 0.0
            )

            lines.append(
                f"{index}. "
                f"{symbol} "
                f"{icon} "
                f"{direction}"
            )

            lines.append(
                f"Entry: "
                f"{format_price(entry)}"
            )

            lines.append(
                f"Now: "
                f"{format_price(now_price)}"
            )

            lines.append(
                f"SL: "
                f"{format_price(sl)} "
                f"(-{sl_pct:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{format_price(tp)} "
                f"(+{tp_pct:.2f}%)"
            )

            lines.append(
                f"P/L: "
                f"{pnl:+.2f}%"
            )

            lines.append(
                f"Score: "
                f"{score:.1f}"
            )

            lines.append(
                f"Risk: "
                f"{risk:.2f}%"
            )

            lines.append(
                f"RR: "
                f"{rr_actual:.2f}:1"
            )

    # --------------------------------------------------------
    # Scan information
    # --------------------------------------------------------

    if scan_info:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🔎 SCAN"
        )

        scanned = scan_info.get(
            "scanned",
            0,
        )

        signal_count = scan_info.get(
            "signals",
            0,
        )

        lines.append(
            f"Markets: {scanned} | "
            f"Qualifying: {signal_count}"
        )

        reasons = scan_info.get(
            "rejections",
            {},
        )

        if (
            signal_count == 0
            and reasons
        ):

            top_reasons = sorted(
                reasons.items(),
                key=lambda x: x[1],
                reverse=True,
            )[:4]

            for reason, count in top_reasons:

                lines.append(
                    f"• {reason}: {count}"
                )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

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
        f"⚪ {perf['breakeven']} | "
        f"⏱ {perf['timeout']}"
    )

    lines.append(
        f"🏆 WR: "
        f"{perf['wr']:.1f}%"
    )

    lines.append(
        f"💰 Total P/L: "
        f"{perf['total_pnl']:+.2f}%"
    )

    long_count = sum(
        1
        for trade in open_trades
        if trade.get(
            "direction"
        ) == "LONG"
    )

    short_count = sum(
        1
        for trade in open_trades
        if trade.get(
            "direction"
        ) == "SHORT"
    )

    lines.append(
        "⚙️ LIMITS: "
        f"LONG {long_count}/"
        f"{MAX_LONG_TRADES} | "
        f"SHORT {short_count}/"
        f"{MAX_SHORT_TRADES} | "
        f"TOTAL {len(open_trades)}/"
        f"{MAX_OPEN_TRADES}"
    )

    lines.append(
        f"🛡 Minimum Risk: "
        f"{MIN_RISK_PCT:.2f}%"
    )

    return "\n".join(
        lines
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] "
            "TELEGRAM_BOT_TOKEN "
            "is not configured."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] "
            "TELEGRAM_CHAT_ID "
            "is not configured."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=20,
        )

        response.raise_for_status()

        data = response.json()

        if not data.get(
            "ok",
            False,
        ):

            print(
                "[WARN] Telegram "
                f"returned error: {data}"
            )

            return False

        return True

    except Exception as e:

        print(
            f"[WARN] Telegram failed: "
            f"{e}"
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "============================================================"
    )

    print(
        "KRAKEN FUTURES ICHIMOKU "
        "TOP RANKER v11.3"
    )

    print(
        "============================================================"
    )

    print(
        "Timeframe: 5m CLOSED"
    )

    print(
        "Trend: 1H"
    )

    print(
        "Confirmation: 30m"
    )

    print(
        "Pullback: 15m"
    )

    print(
        "Trigger: 5m"
    )

    print(
        f"TOP: {TOP_N}"
    )

    print(
        f"MAX OPEN: "
        f"{MAX_OPEN_TRADES}"
    )

    print(
        f"MAX LONG: "
        f"{MAX_LONG_TRADES}"
    )

    print(
        f"MAX SHORT: "
        f"{MAX_SHORT_TRADES}"
    )

    print(
        f"MIN RISK: "
        f"{MIN_RISK_PCT:.2f}%"
    )

    print(
        "RR: 1:1"
    )

    print(
        "Ichimoku: STANDARD DISPLACED CLOUD"
    )

    print(
        "============================================================"
    )

    # ========================================================
    # LOAD STATE
    # ========================================================

    state = load_state()

    # ========================================================
    # STEP 1: MONITOR
    # ========================================================

    print(
        "\n[STEP 1] "
        "Monitoring open trades..."
    )

    before = len(
        state.get(
            "open_trades",
            [],
        )
    )

    state = monitor_open_trades(
        state
    )

    after = len(
        state.get(
            "open_trades",
            [],
        )
    )

    closed_now = (
        before - after
    )

    print(
        f"[INFO] Open trades before: "
        f"{before}"
    )

    print(
        f"[INFO] Open trades after: "
        f"{after}"
    )

    print(
        f"[INFO] Closed this run: "
        f"{closed_now}"
    )

    # Save immediately
    save_state(
        state
    )

    # ========================================================
    # STEP 2: MARKETS
    # ========================================================

    print(
        "\n[STEP 2] "
        "Loading markets..."
    )

    markets = build_market_list()

    print(
        f"[INFO] TOP {len(markets)} "
        f"markets loaded."
    )

    # ========================================================
    # SCAN INFO
    # ========================================================

    scan_info = {
        "scanned": 0,
        "signals": 0,
        "rejections": {},
    }

    # ========================================================
    # STEP 3: SCAN
    # ========================================================

    open_count = len(
        state.get(
            "open_trades",
            [],
        )
    )

    if open_count < MAX_OPEN_TRADES:

        print(
            "\n[STEP 3] "
            "Scanning markets..."
        )

        (
            candidates,
            rejection_counter,
        ) = scan_all_markets(
            markets,
            state,
        )

        scan_info[
            "scanned"
        ] = len(markets)

        scan_info[
            "signals"
        ] = len(candidates)

        scan_info[
            "rejections"
        ] = dict(
            rejection_counter
        )

        print(
            "\n============================================================"
        )

        print(
            "[SCAN SUMMARY]"
        )

        print(
            f"Markets scanned: "
            f"{len(markets)}"
        )

        print(
            f"Candidates found: "
            f"{len(candidates)}"
        )

        if rejection_counter:

            print(
                "[TOP REJECTION REASONS]"
            )

            for reason, count in (
                rejection_counter.most_common(
                    10
                )
            ):

                print(
                    f"  {reason}: "
                    f"{count}"
                )

        print(
            "============================================================"
        )

        # ====================================================
        # SELECT
        # ====================================================

        selected = select_best_trades(
            candidates,
            state,
        )

        print(
            f"[INFO] Selected: "
            f"{len(selected)}"
        )

        # ====================================================
        # OPEN
        # ====================================================

        state = add_selected_trades(
            state,
            selected,
        )

    else:

        print(
            "\n[STEP 3] "
            "Capacity full."
        )

        print(
            f"[INFO] Open trades: "
            f"{open_count}/"
            f"{MAX_OPEN_TRADES}"
        )

    # ========================================================
    # STEP 4: SAVE
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # STEP 5: REPORT
    # ========================================================

    report = generate_report(
        state,
        scan_info,
    )

    print(
        "\n============================================================"
    )

    print(
        report
    )

    print(
        "============================================================"
    )

    # ========================================================
    # STEP 6: TELEGRAM
    # ========================================================

    print(
        "\n[STEP 4] "
        "Sending Telegram report..."
    )

    sent = send_telegram(
        report
    )

    if sent:

        print(
            "[INFO] "
            "Telegram report sent."
        )

    else:

        print(
            "[WARN] "
            "Telegram report was not sent."
        )

    # ========================================================
    # FINAL SAVE
    # ========================================================

    save_state(
        state
    )

    print(
        "\n[INFO] "
        "Scanner completed."
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[INFO] Interrupted."
        )

    except Exception as e:

        print(
            "\n[FATAL ERROR]"
        )

        print(
            str(e)
        )

        traceback.print_exc()

        raise
