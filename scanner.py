# ============================================================
# KRAKEN FUTURES ICHIMOKU TOP RANKER v13.2
# ============================================================
# FIXED TRADE STATE + CLOSED CANDLE EXIT TRACKING
#
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
# RR:
#   1:1
#
# MAX OPEN:
#   4 trades
#   max 2 LONG
#   max 2 SHORT
#
# IMPORTANT:
# State and history JSON files MUST be committed by GitHub Actions
# so open trades survive between workflow runs.
#
# ============================================================

import os
import json
import time
import requests

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

STRATEGY_VERSION = "v13.2"

# Keep same filenames so v13.1 state can be migrated.
STATE_FILE = "ichimoku_state_v13_1.json"
HISTORY_FILE = "ichimoku_trade_history_v13_1.json"

SUPPORTED_OLD_VERSIONS = {
    "v13.1",
    "v13.2",
}


# ============================================================
# STRATEGY SETTINGS
# ============================================================

TOP_N = 100

MAX_OPEN_TRADES = 4
MAX_LONG_TRADES = 2
MAX_SHORT_TRADES = 2

RR = 1.0

TIMEOUT_HOURS = 4

MIN_RISK_PCT = 0.50
MAX_RISK_PCT = 2.00

MIN_SCORE = 78
MIN_TRIGGER_SCORE = 7

PULLBACK_ATR_MULTIPLIER = 1.50
PULLBACK_TOUCH_ATR = 0.35
PULLBACK_MAX_AGE = 2

TRIGGER_BODY_ATR_MIN = 0.10

MAX_SPREAD_PCT = 0.80

MIN_24H_VOLUME = 0.0

FEE_PCT_PER_SIDE = 0.04
SLIPPAGE_PCT_PER_SIDE = 0.02

HTTP_TIMEOUT = 15


# ============================================================
# ICHIMOKU
# ============================================================

TENKAN = 9
KIJUN = 26
SENKOU_B = 52
DISPLACEMENT = 26


# ============================================================
# TIMEFRAMES
# ============================================================

TF_5M = "5m"
TF_15M = "15m"
TF_30M = "30m"
TF_1H = "1h"

TF_SECONDS = {
    TF_5M: 300,
    TF_15M: 900,
    TF_30M: 1800,
    TF_1H: 3600,
}


# ============================================================
# TREND WEIGHTS
# ============================================================

W_1H = 0.50
W_30M = 0.30
W_15M = 0.20


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Kraken-Ichimoku-v13.2"
    }
)


# ============================================================
# HTTP
# ============================================================

def http_get(path, params=None):

    url = BASE_URL + path

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"[HTTP ERROR] {path} -> {e}"
        )

        return None


# ============================================================
# TIME
# ============================================================

def now_ts():

    return int(time.time())


def utc_now():

    return datetime.now(timezone.utc)


def iso_now():

    return utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def timestamp_to_iso(ts):

    try:

        return datetime.fromtimestamp(
            int(ts),
            timezone.utc,
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    except Exception:

        return iso_now()


# ============================================================
# JSON
# ============================================================

def load_json_file(
    filename,
    default,
):

    if not os.path.exists(filename):

        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"[JSON LOAD ERROR] "
            f"{filename}: {e}"
        )

        return default


def save_json_file(
    filename,
    data,
):

    tmp_file = filename + ".tmp"

    try:

        with open(
            tmp_file,
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
            tmp_file,
            filename,
        )

        return True

    except Exception as e:

        print(
            f"[JSON SAVE ERROR] "
            f"{filename}: {e}"
        )

        try:

            if os.path.exists(
                tmp_file
            ):

                os.remove(
                    tmp_file
                )

        except Exception:
            pass

        return False


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "strategy_version": STRATEGY_VERSION,
        "updated_at": iso_now(),
        "open_trades": [],
    }


def normalize_trade_state(
    state
):

    if not isinstance(
        state,
        dict,
    ):

        return default_state()

    if not isinstance(
        state.get("open_trades"),
        list,
    ):

        state["open_trades"] = []

    # --------------------------------------------------------
    # Normalize every open trade
    # --------------------------------------------------------

    normalized = []

    for trade in state["open_trades"]:

        if not isinstance(
            trade,
            dict,
        ):

            continue

        if not trade.get("symbol"):

            continue

        if not trade.get("direction"):

            continue

        # Old trades remain OPEN unless explicitly closed.
        if not trade.get("status"):

            trade["status"] = "OPEN"

        if trade.get("status") != "OPEN":

            continue

        # Ensure tracking fields exist.
        trade.setdefault(
            "last_price",
            trade.get("entry", 0),
        )

        trade.setdefault(
            "pnl_pct",
            0.0,
        )

        trade.setdefault(
            "opened_at",
            now_ts(),
        )

        trade.setdefault(
            "opened_at_iso",
            timestamp_to_iso(
                trade["opened_at"]
            ),
        )

        trade.setdefault(
            "signal_candle_time",
            trade.get(
                "opened_at",
                0,
            ),
        )

        trade.setdefault(
            "last_checked_candle",
            trade.get(
                "signal_candle_time",
                0,
            ),
        )

        normalized.append(
            trade
        )

    state["open_trades"] = normalized

    state["strategy_version"] = STRATEGY_VERSION
    state["updated_at"] = iso_now()

    return state


def load_state():

    state = load_json_file(
        STATE_FILE,
        None,
    )

    if not isinstance(
        state,
        dict,
    ):

        print(
            "[STATE] No valid state -> fresh state"
        )

        return default_state()

    version = state.get(
        "strategy_version"
    )

    if version not in SUPPORTED_OLD_VERSIONS:

        print(
            "[STATE] Unknown version -> "
            "normalizing existing state"
        )

    else:

        print(
            f"[STATE] Loading {version} state"
        )

    state = normalize_trade_state(
        state
    )

    return state


# ============================================================
# HISTORY
# ============================================================

def default_history():

    return {
        "strategy_version": STRATEGY_VERSION,
        "updated_at": iso_now(),
        "trades": [],
    }


def deduplicate_history(
    trades
):

    result = []

    seen = set()

    for trade in trades:

        if not isinstance(
            trade,
            dict,
        ):

            continue

        trade_id = trade.get("id")

        if not trade_id:

            trade_id = (
                str(
                    trade.get(
                        "symbol",
                        "",
                    )
                )
                + "_"
                + str(
                    trade.get(
                        "direction",
                        "",
                    )
                )
                + "_"
                + str(
                    trade.get(
                        "signal_candle_time",
                        "",
                    )
                )
            )

        if trade_id in seen:

            continue

        seen.add(
            trade_id
        )

        result.append(
            trade
        )

    return result


def load_history():

    history = load_json_file(
        HISTORY_FILE,
        None,
    )

    if not isinstance(
        history,
        dict,
    ):

        return default_history()

    if not isinstance(
        history.get("trades"),
        list,
    ):

        history["trades"] = []

    history["trades"] = deduplicate_history(
        history["trades"]
    )

    # Do NOT erase old history just because
    # strategy version changed.
    history["strategy_version"] = STRATEGY_VERSION
    history["updated_at"] = iso_now()

    return history


def history_contains_trade(
    history,
    trade,
):

    trade_id = trade.get("id")

    if not trade_id:

        return False

    for item in history.get(
        "trades",
        [],
    ):

        if item.get("id") == trade_id:

            return True

    return False


def append_history_once(
    history,
    trade,
):

    if history_contains_trade(
        history,
        trade,
    ):

        return False

    history.setdefault(
        "trades",
        [],
    ).append(
        trade.copy()
    )

    history["updated_at"] = iso_now()

    return True


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    data = http_get(
        "/derivatives/api/v3/instruments"
    )

    if not data:

        return []

    if isinstance(
        data,
        dict,
    ):

        for key in (
            "instruments",
            "data",
            "results",
        ):

            if isinstance(
                data.get(key),
                list,
            ):

                return data[key]

    if isinstance(
        data,
        list,
    ):

        return data

    return []


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = http_get(
        "/derivatives/api/v3/tickers"
    )

    if not data:

        return []

    if isinstance(
        data,
        dict,
    ):

        for key in (
            "tickers",
            "data",
            "results",
        ):

            if isinstance(
                data.get(key),
                list,
            ):

                return data[key]

    if isinstance(
        data,
        list,
    ):

        return data

    return []


# ============================================================
# SYMBOL
# ============================================================

def normalize_symbol(
    symbol
):

    if not symbol:

        return ""

    return str(
        symbol
    ).strip()


# ============================================================
# NUMBER
# ============================================================

def safe_float(
    value,
    default=0.0,
):

    try:

        return float(value)

    except Exception:

        return default


# ============================================================
# MARKET METADATA
# ============================================================

def build_market_metadata():

    instruments = get_instruments()

    tickers = get_tickers()

    ticker_map = {}

    for ticker in tickers:

        if not isinstance(
            ticker,
            dict,
        ):

            continue

        symbol = normalize_symbol(
            ticker.get("symbol")
            or ticker.get("pair")
            or ticker.get("instrument")
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
            instrument.get("symbol")
            or instrument.get("pair")
            or instrument.get("instrument")
        )

        if not symbol:

            continue

        upper_symbol = symbol.upper()

        # ----------------------------------------------------
        # PERPETUAL
        # ----------------------------------------------------

        is_perpetual = (
            "PERP" in upper_symbol
            or "PI_" in upper_symbol
            or "PF_" in upper_symbol
        )

        if not is_perpetual:

            continue

        ticker = ticker_map.get(
            symbol
        )

        if not ticker:

            continue

        # ----------------------------------------------------
        # QUOTE
        # ----------------------------------------------------

        quote = str(
            instrument.get("quote")
            or instrument.get("quoteCurrency")
            or ""
        ).upper()

        if quote and quote != "USD":

            continue

        # ----------------------------------------------------
        # VOLUME
        # ----------------------------------------------------

        volume = safe_float(
            ticker.get("vol24h")
            or ticker.get("volume24h")
            or ticker.get("volume")
            or ticker.get("volume_24h")
        )

        if volume < MIN_24H_VOLUME:

            continue

        # ----------------------------------------------------
        # PRICE
        # ----------------------------------------------------

        last_price = safe_float(
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("price")
            or ticker.get("markPrice")
        )

        if last_price <= 0:

            continue

        # ----------------------------------------------------
        # BID / ASK
        # ----------------------------------------------------

        bid = safe_float(
            ticker.get("bid")
            or ticker.get("bidPrice")
        )

        ask = safe_float(
            ticker.get("ask")
            or ticker.get("askPrice")
        )

        spread_pct = 0.0

        if bid > 0 and ask > 0:

            spread_pct = (
                (ask - bid)
                / bid
                * 100.0
            )

            if spread_pct > MAX_SPREAD_PCT:

                continue

        # ----------------------------------------------------
        # TICK SIZE
        # ----------------------------------------------------

        tick_size = safe_float(
            instrument.get("tickSize")
            or instrument.get("priceIncrement")
            or instrument.get("quoteIncrement"),
            0.0,
        )

        markets.append(
            {
                "symbol": symbol,
                "volume": volume,
                "last": last_price,
                "bid": bid,
                "ask": ask,
                "spread_pct": spread_pct,
                "tick_size": tick_size,
            }
        )

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True,
    )

    markets = markets[:TOP_N]

    print(
        f"[INFO] Markets: {len(markets)}"
    )

    return markets


# ============================================================
# TICK ROUNDING
# ============================================================

def round_to_tick(
    price,
    tick_size,
):

    price = safe_float(
        price
    )

    tick_size = safe_float(
        tick_size
    )

    if price <= 0:

        return price

    if tick_size <= 0:

        return price

    steps = round(
        price / tick_size
    )

    return steps * tick_size


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle(
    item
):

    if isinstance(
        item,
        dict,
    ):

        ts = (
            item.get("time")
            or item.get("timestamp")
            or item.get("ts")
            or item.get("t")
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

    elif (
        isinstance(
            item,
            list,
        )
        and len(item) >= 5
    ):

        ts = item[0]
        open_price = item[1]
        high = item[2]
        low = item[3]
        close = item[4]

        volume = (
            item[5]
            if len(item) > 5
            else 0
        )

    else:

        return None

    try:

        ts = float(
            ts
        )

        if ts > 10_000_000_000:

            ts /= 1000.0

        return {
            "time": int(ts),
            "open": float(open_price),
            "high": float(high),
            "low": float(low),
            "close": float(close),
            "volume": float(volume),
        }

    except Exception:

        return None


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit=160,
):

    path = (
        f"/api/charts/v1/trade/"
        f"{symbol}/"
        f"{resolution}"
    )

    data = http_get(
        path
    )

    if not data:

        return []

    raw = []

    if isinstance(
        data,
        dict,
    ):

        for key in (
            "candles",
            "data",
            "results",
        ):

            if isinstance(
                data.get(key),
                list,
            ):

                raw = data[key]

                break

    elif isinstance(
        data,
        list,
    ):

        raw = data

    if not raw:

        return []

    current_ts = now_ts()

    seconds = TF_SECONDS.get(
        resolution,
        300,
    )

    candles = []

    for item in raw:

        candle = parse_candle(
            item
        )

        if not candle:

            continue

        # ----------------------------------------------------
        # CLOSED CANDLES ONLY
        # ----------------------------------------------------

        if (
            candle["time"]
            + seconds
            > current_ts
        ):

            continue

        candles.append(
            candle
        )

    candles.sort(
        key=lambda x: x["time"]
    )

    return candles[-limit:]


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14,
):

    if len(candles) < period + 1:

        return 0.0

    trs = []

    for i in range(
        1,
        len(candles),
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
            ),
        )

        trs.append(
            tr
        )

    if len(trs) < period:

        return 0.0

    return (
        sum(
            trs[-period:]
        )
        / period
    )


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku_values(
    candles
):

    required = (
        SENKOU_B
        + DISPLACEMENT
    )

    if len(candles) < required:

        return None

    highs = [
        c["high"]
        for c in candles
    ]

    lows = [
        c["low"]
        for c in candles
    ]

    closes = [
        c["close"]
        for c in candles
    ]

    tenkan_high = max(
        highs[-TENKAN:]
    )

    tenkan_low = min(
        lows[-TENKAN:]
    )

    tenkan = (
        tenkan_high
        + tenkan_low
    ) / 2.0

    kijun_high = max(
        highs[-KIJUN:]
    )

    kijun_low = min(
        lows[-KIJUN:]
    )

    kijun = (
        kijun_high
        + kijun_low
    ) / 2.0

    span_b_high = max(
        highs[-SENKOU_B:]
    )

    span_b_low = min(
        lows[-SENKOU_B:]
    )

    span_b = (
        span_b_high
        + span_b_low
    ) / 2.0

    span_a = (
        tenkan
        + kijun
    ) / 2.0

    close = closes[-1]

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
        "close": close,
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(
    candles
):

    values = ichimoku_values(
        candles
    )

    if not values:

        return 0, None

    price = values["close"]

    tenkan = values["tenkan"]

    kijun = values["kijun"]

    span_a = values["span_a"]

    span_b = values["span_b"]

    cloud_top = values["cloud_top"]

    cloud_bottom = values["cloud_bottom"]

    score = 0

    if price > cloud_top:

        score += 3

    elif price < cloud_bottom:

        score -= 3

    if tenkan > kijun:

        score += 2

    elif tenkan < kijun:

        score -= 2

    if price > kijun:

        score += 2

    elif price < kijun:

        score -= 2

    if price > tenkan:

        score += 1

    elif price < tenkan:

        score -= 1

    if span_a > span_b:

        score += 1

    elif span_a < span_b:

        score -= 1

    score = max(
        -10,
        min(
            10,
            score,
        ),
    )

    return score, values


# ============================================================
# CANDLE STRUCTURE
# ============================================================

def candle_structure(
    candle
):

    open_price = candle["open"]
    high = candle["high"]
    low = candle["low"]
    close = candle["close"]

    body = abs(
        close - open_price
    )

    candle_range = max(
        high - low,
        0.0,
    )

    upper_wick = (
        high
        - max(
            open_price,
            close,
        )
    )

    lower_wick = (
        min(
            open_price,
            close,
        )
        - low
    )

    return {
        "body": body,
        "range": candle_range,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick,
        "bullish": close > open_price,
        "bearish": close < open_price,
    }


# ============================================================
# REVERSAL PATTERNS
# ============================================================

def is_bullish_engulfing(
    previous,
    current,
):

    return (
        previous["close"]
        < previous["open"]
        and current["close"]
        > current["open"]
        and current["open"]
        <= previous["close"]
        and current["close"]
        >= previous["open"]
    )


def is_bearish_engulfing(
    previous,
    current,
):

    return (
        previous["close"]
        > previous["open"]
        and current["close"]
        < current["open"]
        and current["open"]
        >= previous["close"]
        and current["close"]
        <= previous["open"]
    )


def is_bullish_pinbar(
    candle
):

    s = candle_structure(
        candle
    )

    if s["range"] <= 0:

        return False

    return (
        s["lower_wick"]
        >= s["body"] * 2
        and s["lower_wick"]
        >= s["upper_wick"] * 1.5
        and candle["close"]
        >= candle["low"]
        + s["range"] * 0.55
    )


def is_bearish_pinbar(
    candle
):

    s = candle_structure(
        candle
    )

    if s["range"] <= 0:

        return False

    return (
        s["upper_wick"]
        >= s["body"] * 2
        and s["upper_wick"]
        >= s["lower_wick"] * 1.5
        and candle["close"]
        <= candle["high"]
        - s["range"] * 0.55
    )


def reversal_pattern(
    candles,
    direction,
):

    if len(candles) < 2:

        return False

    previous = candles[-2]
    current = candles[-1]

    if direction == "LONG":

        return (
            is_bullish_engulfing(
                previous,
                current,
            )
            or is_bullish_pinbar(
                current
            )
        )

    return (
        is_bearish_engulfing(
            previous,
            current,
        )
        or is_bearish_pinbar(
            current
        )
    )


# ============================================================
# PULLBACK
# ============================================================

def five_minute_pullback(
    candles,
    direction,
):

    if len(candles) < 40:

        return None

    atr = calculate_atr(
        candles,
        14,
    )

    if atr <= 0:

        return None

    score, ichi = ichimoku_score(
        candles
    )

    if not ichi:

        return None

    tenkan = ichi["tenkan"]
    kijun = ichi["kijun"]

    trigger = candles[-1]

    previous_candidates = candles[
        max(
            0,
            len(candles)
            - 1
            - PULLBACK_MAX_AGE,
        ):
        -1
    ]

    if not previous_candidates:

        return None

    touched = False

    nearest_distance = float(
        "inf"
    )

    pullback_candle = None

    for candle in previous_candidates:

        low = candle["low"]
        high = candle["high"]

        touch_tenkan = (
            low
            <= tenkan
            <= high
        )

        touch_kijun = (
            low
            <= kijun
            <= high
        )

        distance_tenkan = abs(
            candle["close"]
            - tenkan
        )

        distance_kijun = abs(
            candle["close"]
            - kijun
        )

        distance = min(
            distance_tenkan,
            distance_kijun,
        )

        nearest_distance = min(
            nearest_distance,
            distance,
        )

        if (
            touch_tenkan
            or touch_kijun
            or distance
            <= atr
            * PULLBACK_TOUCH_ATR
        ):

            touched = True
            pullback_candle = candle

    if not touched:

        return None

    if (
        nearest_distance
        > atr
        * PULLBACK_ATR_MULTIPLIER
    ):

        return None

    ts = candle_structure(
        trigger
    )

    body_ok = (
        ts["body"]
        >= atr
        * TRIGGER_BODY_ATR_MIN
    )

    if not body_ok:

        return None

    if direction == "LONG":

        direction_ok = (
            trigger["close"]
            > trigger["open"]
        )

        recovery = (
            trigger["close"]
            > pullback_candle["close"]
            if pullback_candle
            else False
        )

        reclaim = (
            trigger["close"]
            > tenkan
        )

        rejection = (
            ts["lower_wick"]
            > ts["upper_wick"]
        )

        structure_break = (
            trigger["close"]
            > max(
                c["high"]
                for c in previous_candidates
            )
        )

    else:

        direction_ok = (
            trigger["close"]
            < trigger["open"]
        )

        recovery = (
            trigger["close"]
            < pullback_candle["close"]
            if pullback_candle
            else False
        )

        reclaim = (
            trigger["close"]
            < tenkan
        )

        rejection = (
            ts["upper_wick"]
            > ts["lower_wick"]
        )

        structure_break = (
            trigger["close"]
            < min(
                c["low"]
                for c in previous_candidates
            )
        )

    if not direction_ok:
        return None

    if not recovery:
        return None

    if not reclaim:
        return None

    if not rejection:
        return None

    if not structure_break:
        return None

    return {
        "atr": atr,
        "ichimoku_score": score,
        "tenkan": tenkan,
        "kijun": kijun,
        "pullback_candle_time": (
            pullback_candle["time"]
            if pullback_candle
            else None
        ),
        "trigger_time": trigger["time"],
        "reversal": reversal_pattern(
            candles,
            direction,
        ),
        "rejection": rejection,
        "reclaim": reclaim,
        "structure_break": structure_break,
        "body_ok": body_ok,
    }


# ============================================================
# TRIGGER SCORE
# ============================================================

def calculate_trigger_score(
    pullback
):

    if not pullback:

        return 0

    score = 0

    score += 1

    if pullback["reversal"]:
        score += 2

    if pullback["rejection"]:
        score += 1

    if pullback["reclaim"]:
        score += 1

    if pullback["structure_break"]:
        score += 2

    if pullback["body_ok"]:
        score += 1

    return min(
        10,
        score,
    )


# ============================================================
# TREND STRUCTURE
# ============================================================

def trend_structure(
    candles,
    direction,
):

    if len(candles) < 10:

        return False

    recent = candles[-10:]

    highs = [
        c["high"]
        for c in recent
    ]

    lows = [
        c["low"]
        for c in recent
    ]

    if direction == "LONG":

        return (
            highs[-1] >= highs[-3]
            and lows[-1] >= lows[-3]
        )

    return (
        highs[-1] <= highs[-3]
        and lows[-1] <= lows[-3]
    )


# ============================================================
# TREND SCORE
# ============================================================

def calculate_trend_score(
    s1h,
    s30,
    s15,
):

    return (
        s1h * W_1H
        + s30 * W_30M
        + s15 * W_15M
    )


# ============================================================
# REVERSAL SCORE
# ============================================================

def calculate_reversal_score(
    s1h,
    s30,
    s15,
    s5,
):

    return (
        (s15 - s1h)
        + (s5 - s30)
    )


# ============================================================
# FINAL RANK SCORE
# ============================================================

def final_rank_score(
    s1h,
    s30,
    s15,
    s5,
    trigger_score,
):

    trend_component = (
        max(
            0,
            abs(s1h),
        )
        / 10.0
        * 35.0
    )

    confirmation_component = (
        max(
            0,
            abs(s30),
        )
        / 10.0
        * 25.0
    )

    structure_component = (
        max(
            0,
            abs(s15),
        )
        / 10.0
        * 20.0
    )

    trigger_component = (
        trigger_score
        / 10.0
        * 15.0
    )

    five_component = (
        max(
            0,
            abs(s5),
        )
        / 10.0
        * 5.0
    )

    score = (
        trend_component
        + confirmation_component
        + structure_component
        + trigger_component
        + five_component
    )

    return max(
        0.0,
        min(
            100.0,
            score,
        ),
    )


# ============================================================
# STRUCTURAL LEVELS
# ============================================================

def structural_levels(
    candles_5m,
    candles_15m,
    direction,
    tick_size,
):

    if len(candles_5m) < 10:
        return None

    if len(candles_15m) < 4:
        return None

    entry = candles_5m[-1]["close"]

    recent_5m = candles_5m[-10:]
    recent_15m = candles_15m[-4:]

    if direction == "LONG":

        structural_low = min(
            min(
                c["low"]
                for c in recent_5m
            ),
            min(
                c["low"]
                for c in recent_15m
            ),
        )

        sl = structural_low

        risk = entry - sl

        if risk <= 0:
            return None

        tp = entry + risk * RR

    else:

        structural_high = max(
            max(
                c["high"]
                for c in recent_5m
            ),
            max(
                c["high"]
                for c in recent_15m
            ),
        )

        sl = structural_high

        risk = sl - entry

        if risk <= 0:
            return None

        tp = entry - risk * RR

    entry = round_to_tick(
        entry,
        tick_size,
    )

    sl = round_to_tick(
        sl,
        tick_size,
    )

    tp = round_to_tick(
        tp,
        tick_size,
    )

    if direction == "LONG":

        if not (
            sl < entry < tp
        ):

            return None

    else:

        if not (
            tp < entry < sl
        ):

            return None

    risk_pct = (
        abs(entry - sl)
        / entry
        * 100.0
    )

    if (
        risk_pct < MIN_RISK_PCT
        or risk_pct > MAX_RISK_PCT
    ):

        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk_pct": risk_pct,
    }


# ============================================================
# MTF DATA
# ============================================================

def get_mtf_data(
    symbol
):

    candles_5m = get_candles(
        symbol,
        TF_5M,
        160,
    )

    candles_15m = get_candles(
        symbol,
        TF_15M,
        160,
    )

    candles_30m = get_candles(
        symbol,
        TF_30M,
        160,
    )

    candles_1h = get_candles(
        symbol,
        TF_1H,
        160,
    )

    if len(candles_5m) < 80:
        return None

    if len(candles_15m) < 80:
        return None

    if len(candles_30m) < 80:
        return None

    if len(candles_1h) < 80:
        return None

    s5, i5 = ichimoku_score(
        candles_5m
    )

    s15, i15 = ichimoku_score(
        candles_15m
    )

    s30, i30 = ichimoku_score(
        candles_30m
    )

    s1h, i1h = ichimoku_score(
        candles_1h
    )

    return {
        "5m": candles_5m,
        "15m": candles_15m,
        "30m": candles_30m,
        "1h": candles_1h,

        "s5": s5,
        "s15": s15,
        "s30": s30,
        "s1h": s1h,

        "i5": i5,
        "i15": i15,
        "i30": i30,
        "i1h": i1h,
    }


# ============================================================
# DIAGNOSTICS
# ============================================================

def new_diagnostics():

    return {
        "scanned": 0,
        "data_ok": 0,
        "long_direction": 0,
        "short_direction": 0,
        "long_pullback": 0,
        "short_pullback": 0,
        "long_trigger": 0,
        "short_trigger": 0,
        "long_trend": 0,
        "short_trend": 0,
        "long_rank": 0,
        "short_rank": 0,
        "levels_ok": 0,
        "risk_ok": 0,
        "qualified": 0,
    }


# ============================================================
# ANALYZE CANDIDATE
# ============================================================

def analyze_candidate(
    market,
    diagnostics,
):

    symbol = market["symbol"]

    diagnostics["scanned"] += 1

    mtf = get_mtf_data(
        symbol
    )

    if not mtf:

        return None

    diagnostics["data_ok"] += 1

    s5 = mtf["s5"]
    s15 = mtf["s15"]
    s30 = mtf["s30"]
    s1h = mtf["s1h"]

    trend_score = calculate_trend_score(
        s1h,
        s30,
        s15,
    )

    reversal_score = calculate_reversal_score(
        s1h,
        s30,
        s15,
        s5,
    )

    # ========================================================
    # LONG
    # ========================================================

    if (
        s1h >= 4
        and s30 >= 3
        and s15 >= 2
        and s5 >= 2
    ):

        diagnostics[
            "long_direction"
        ] += 1

        direction = "LONG"

        pullback = five_minute_pullback(
            mtf["5m"],
            direction,
        )

        if not pullback:
            return None

        diagnostics[
            "long_pullback"
        ] += 1

        trigger_score = calculate_trigger_score(
            pullback
        )

        if trigger_score < MIN_TRIGGER_SCORE:
            return None

        diagnostics[
            "long_trigger"
        ] += 1

        if trend_score < 5:
            return None

        diagnostics[
            "long_trend"
        ] += 1

        rank = final_rank_score(
            s1h,
            s30,
            s15,
            s5,
            trigger_score,
        )

        if rank < MIN_SCORE:
            return None

        diagnostics[
            "long_rank"
        ] += 1

    # ========================================================
    # SHORT
    # ========================================================

    elif (
        s1h <= -4
        and s30 <= -3
        and s15 <= -2
        and s5 <= -2
    ):

        diagnostics[
            "short_direction"
        ] += 1

        direction = "SHORT"

        pullback = five_minute_pullback(
            mtf["5m"],
            direction,
        )

        if not pullback:
            return None

        diagnostics[
            "short_pullback"
        ] += 1

        trigger_score = calculate_trigger_score(
            pullback
        )

        if trigger_score < MIN_TRIGGER_SCORE:
            return None

        diagnostics[
            "short_trigger"
        ] += 1

        if trend_score > -5:
            return None

        diagnostics[
            "short_trend"
        ] += 1

        rank = final_rank_score(
            abs(s1h),
            abs(s30),
            abs(s15),
            abs(s5),
            trigger_score,
        )

        if rank < MIN_SCORE:
            return None

        diagnostics[
            "short_rank"
        ] += 1

    else:

        return None

    # ========================================================
    # LEVELS
    # ========================================================

    levels = structural_levels(
        mtf["5m"],
        mtf["15m"],
        direction,
        market["tick_size"],
    )

    if not levels:
        return None

    diagnostics[
        "levels_ok"
    ] += 1

    risk_pct = levels["risk_pct"]

    if (
        risk_pct < MIN_RISK_PCT
        or risk_pct > MAX_RISK_PCT
    ):

        return None

    diagnostics[
        "risk_ok"
    ] += 1

    diagnostics[
        "qualified"
    ] += 1

    signal_candle_time = mtf[
        "5m"
    ][-1]["time"]

    setup_id = (
        f"{symbol}_"
        f"{direction}_"
        f"{signal_candle_time}"
    )

    return {
        "id": setup_id,
        "symbol": symbol,
        "direction": direction,

        "entry": levels["entry"],
        "sl": levels["sl"],
        "tp": levels["tp"],

        "risk_pct": risk_pct,

        "score": rank,

        "trend_score": trend_score,
        "reversal_score": reversal_score,
        "trigger_score": trigger_score,

        "s1h": s1h,
        "s30": s30,
        "s15": s15,
        "s5": s5,

        "signal_candle_time": signal_candle_time,

        "created_at": now_ts(),
        "created_at_iso": iso_now(),
    }


# ============================================================
# SETUP ID
# ============================================================

def setup_key(
    candidate
):

    return candidate["id"]


# ============================================================
# OPEN TRADE COUNTS
# ============================================================

def count_direction_trades(
    open_trades,
    direction,
):

    return sum(
        1
        for trade in open_trades
        if trade.get("direction")
        == direction
        and trade.get("status")
        == "OPEN"
    )


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    candidate
):

    return {
        "strategy_version": STRATEGY_VERSION,

        "id": candidate["id"],

        "symbol": candidate["symbol"],
        "direction": candidate["direction"],

        "entry": candidate["entry"],
        "sl": candidate["sl"],
        "tp": candidate["tp"],

        "risk_pct": candidate["risk_pct"],

        "score": candidate["score"],

        "trend_score": candidate[
            "trend_score"
        ],

        "reversal_score": candidate[
            "reversal_score"
        ],

        "trigger_score": candidate[
            "trigger_score"
        ],

        "s1h": candidate["s1h"],
        "s30": candidate["s30"],
        "s15": candidate["s15"],
        "s5": candidate["s5"],

        "opened_at": now_ts(),

        "opened_at_iso": iso_now(),

        "signal_candle_time": candidate[
            "signal_candle_time"
        ],

        # NEW:
        # Last closed candle checked for exit.
        "last_checked_candle": candidate[
            "signal_candle_time"
        ],

        "status": "OPEN",

        "last_price": candidate[
            "entry"
        ],

        "pnl_pct": 0.0,
    }


# ============================================================
# PNL
# ============================================================

def calculate_pnl_pct(
    trade,
    price,
):

    entry = safe_float(
        trade.get("entry")
    )

    price = safe_float(
        price
    )

    if entry <= 0 or price <= 0:

        return 0.0

    direction = trade.get(
        "direction"
    )

    if direction == "LONG":

        raw = (
            price - entry
        ) / entry * 100.0

    else:

        raw = (
            entry - price
        ) / entry * 100.0

    total_cost = (
        2
        * (
            FEE_PCT_PER_SIDE
            + SLIPPAGE_PCT_PER_SIDE
        )
    )

    return raw - total_cost


# ============================================================
# CLOSED CANDLE EXIT
# ============================================================

def check_closed_candle_exit(
    trade,
    candles,
):

    if not candles:

        return None

    entry = safe_float(
        trade.get("entry")
    )

    sl = safe_float(
        trade.get("sl")
    )

    tp = safe_float(
        trade.get("tp")
    )

    signal_time = int(
        trade.get(
            "signal_candle_time",
            0,
        )
    )

    last_checked = int(
        trade.get(
            "last_checked_candle",
            signal_time,
        )
    )

    direction = trade.get(
        "direction"
    )

    # --------------------------------------------------------
    # ONLY NEW CLOSED CANDLES
    # --------------------------------------------------------

    relevant = [
        c
        for c in candles
        if (
            c["time"] > signal_time
            and c["time"] > last_checked
        )
    ]

    if not relevant:

        return {
            "status": "NO_EXIT",
            "last_checked_candle": (
                last_checked
            ),
        }

    # --------------------------------------------------------
    # PROCESS CHRONOLOGICALLY
    # --------------------------------------------------------

    relevant.sort(
        key=lambda x: x["time"]
    )

    for candle in relevant:

        hit_sl = False
        hit_tp = False

        if direction == "LONG":

            if candle["low"] <= sl:

                hit_sl = True

            if candle["high"] >= tp:

                hit_tp = True

        else:

            if candle["high"] >= sl:

                hit_sl = True

            if candle["low"] <= tp:

                hit_tp = True

        # ----------------------------------------------------
        # BOTH LEVELS SAME CANDLE
        # ----------------------------------------------------

        if hit_sl and hit_tp:

            return {
                "status": "AMBIGUOUS",
                "exit_price": candle["close"],
                "exit_time": candle["time"],
                "exit_time_iso": timestamp_to_iso(
                    candle["time"]
                ),
                "reason": "AMBIGUOUS",
                "last_checked_candle": candle[
                    "time"
                ],
            }

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        if hit_tp:

            return {
                "status": "TP",
                "exit_price": tp,
                "exit_time": candle["time"],
                "exit_time_iso": timestamp_to_iso(
                    candle["time"]
                ),
                "reason": "TP",
                "last_checked_candle": candle[
                    "time"
                ],
            }

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        if hit_sl:

            return {
                "status": "SL",
                "exit_price": sl,
                "exit_time": candle["time"],
                "exit_time_iso": timestamp_to_iso(
                    candle["time"]
                ),
                "reason": "SL",
                "last_checked_candle": candle[
                    "time"
                ],
            }

    # --------------------------------------------------------
    # NO EXIT, BUT CANDLES WERE CHECKED
    # --------------------------------------------------------

    return {
        "status": "NO_EXIT",
        "last_checked_candle": relevant[
            -1
        ]["time"],
    }


# ============================================================
# MONITOR OPEN TRADES
# ============================================================

def monitor_open_trades(
    state,
    history,
):

    open_trades = state.get(
        "open_trades",
        [],
    )

    if not open_trades:

        return False, []

    changed = False

    closed_this_run = []

    remaining = []

    # --------------------------------------------------------
    # TICKERS
    # --------------------------------------------------------

    tickers = get_tickers()

    ticker_map = {}

    for ticker in tickers:

        if not isinstance(
            ticker,
            dict,
        ):

            continue

        symbol = normalize_symbol(
            ticker.get("symbol")
            or ticker.get("pair")
            or ticker.get("instrument")
        )

        if symbol:

            ticker_map[
                symbol
            ] = ticker

    # --------------------------------------------------------
    # EACH OPEN TRADE
    # --------------------------------------------------------

    for trade in open_trades:

        symbol = trade.get(
            "symbol"
        )

        if not symbol:

            print(
                "[STATE WARNING] "
                "Trade without symbol kept."
            )

            remaining.append(
                trade
            )

            continue

        # ----------------------------------------------------
        # GET 5M CLOSED CANDLES
        # ----------------------------------------------------

        candles = get_candles(
            symbol,
            TF_5M,
            120,
        )

        # ----------------------------------------------------
        # IMPORTANT:
        # API FAILURE MUST NEVER DELETE TRADE.
        # ----------------------------------------------------

        if not candles:

            print(
                f"[MONITOR] "
                f"{symbol}: candle data unavailable "
                f"-> KEEP OPEN"
            )

            ticker = ticker_map.get(
                symbol
            )

            if ticker:

                live_price = safe_float(
                    ticker.get("last")
                    or ticker.get("lastPrice")
                    or ticker.get("price")
                    or ticker.get("markPrice")
                )

                if live_price > 0:

                    trade["last_price"] = (
                        live_price
                    )

                    trade["pnl_pct"] = (
                        calculate_pnl_pct(
                            trade,
                            live_price,
                        )
                    )

                    changed = True

            remaining.append(
                trade
            )

            continue

        # ----------------------------------------------------
        # CHECK EXIT
        # ----------------------------------------------------

        result = check_closed_candle_exit(
            trade,
            candles,
        )

        if not result:

            result = {
                "status": "NO_EXIT"
            }

        result_status = result.get(
            "status"
        )

        # ----------------------------------------------------
        # EXIT FOUND
        # ----------------------------------------------------

        if result_status in (
            "TP",
            "SL",
            "AMBIGUOUS",
        ):

            exit_price = safe_float(
                result.get(
                    "exit_price"
                )
            )

            pnl = calculate_pnl_pct(
                trade,
                exit_price,
            )

            trade["status"] = (
                result_status
            )

            trade["exit_reason"] = (
                result.get(
                    "reason",
                    result_status,
                )
            )

            trade["exit_price"] = (
                exit_price
            )

            trade["exit_time"] = (
                result.get(
                    "exit_time",
                    now_ts(),
                )
            )

            trade["exit_time_iso"] = (
                result.get(
                    "exit_time_iso",
                    iso_now(),
                )
            )

            trade["pnl_pct"] = pnl

            trade["last_price"] = (
                exit_price
            )

            trade["last_checked_candle"] = (
                result.get(
                    "last_checked_candle",
                    trade.get(
                        "last_checked_candle",
                        0,
                    ),
                )
            )

            # ------------------------------------------------
            # CRITICAL:
            # WRITE TO HISTORY BEFORE REMOVING FROM STATE.
            # ------------------------------------------------

            append_history_once(
                history,
                trade,
            )

            closed_this_run.append(
                trade.copy()
            )

            changed = True

            print(
                "[CLOSED]",
                symbol,
                trade.get(
                    "direction"
                ),
                result_status,
                f"{pnl:+.2f}%",
                result.get(
                    "exit_time_iso",
                    "",
                ),
            )

            # Do NOT add to remaining.
            continue

        # ----------------------------------------------------
        # UPDATE LAST CHECKED CANDLE
        # ----------------------------------------------------

        last_checked = result.get(
            "last_checked_candle"
        )

        if last_checked:

            if int(
                last_checked
            ) > int(
                trade.get(
                    "last_checked_candle",
                    0,
                )
            ):

                trade[
                    "last_checked_candle"
                ] = int(
                    last_checked
                )

                changed = True

        # ----------------------------------------------------
        # LIVE PRICE
        # ----------------------------------------------------

        ticker = ticker_map.get(
            symbol
        )

        if ticker:

            live_price = safe_float(
                ticker.get("last")
                or ticker.get("lastPrice")
                or ticker.get("price")
                or ticker.get("markPrice")
            )

            if live_price > 0:

                trade["last_price"] = (
                    live_price
                )

                trade["pnl_pct"] = (
                    calculate_pnl_pct(
                        trade,
                        live_price,
                    )
                )

                changed = True

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        opened_at = int(
            trade.get(
                "opened_at",
                now_ts(),
            )
        )

        age_seconds = (
            now_ts()
            - opened_at
        )

        if age_seconds >= (
            TIMEOUT_HOURS
            * 3600
        ):

            exit_price = safe_float(
                trade.get(
                    "last_price",
                    trade.get(
                        "entry"
                    ),
                )
            )

            if exit_price <= 0:

                exit_price = safe_float(
                    trade.get(
                        "entry"
                    )
                )

            pnl = calculate_pnl_pct(
                trade,
                exit_price,
            )

            trade["status"] = (
                "TIMEOUT"
            )

            trade["exit_reason"] = (
                "TIMEOUT"
            )

            trade["exit_price"] = (
                exit_price
            )

            trade["exit_time"] = (
                now_ts()
            )

            trade["exit_time_iso"] = (
                iso_now()
            )

            trade["pnl_pct"] = pnl

            append_history_once(
                history,
                trade,
            )

            closed_this_run.append(
                trade.copy()
            )

            changed = True

            print(
                "[TIMEOUT]",
                symbol,
                trade.get(
                    "direction"
                ),
                f"{pnl:+.2f}%",
            )

            continue

        # ----------------------------------------------------
        # STILL OPEN
        # ----------------------------------------------------

        remaining.append(
            trade
        )

    # --------------------------------------------------------
    # STATE UPDATE
    # --------------------------------------------------------

    state["open_trades"] = remaining

    state["strategy_version"] = (
        STRATEGY_VERSION
    )

    state["updated_at"] = iso_now()

    history["strategy_version"] = (
        STRATEGY_VERSION
    )

    history["updated_at"] = iso_now()

    return changed, closed_this_run


# ============================================================
# SCAN MARKETS
# ============================================================

def scan_markets(
    markets,
    state,
):

    diagnostics = new_diagnostics()

    candidates = []

    open_symbols = {
        trade.get("symbol")
        for trade in state.get(
            "open_trades",
            [],
        )
        if trade.get("status")
        == "OPEN"
    }

    for market in markets:

        if len(
            candidates
        ) >= MAX_OPEN_TRADES:

            break

        symbol = market[
            "symbol"
        ]

        if symbol in open_symbols:

            continue

        try:

            candidate = analyze_candidate(
                market,
                diagnostics,
            )

            if candidate:

                candidates.append(
                    candidate
                )

        except Exception as e:

            print(
                f"[ANALYZE ERROR] "
                f"{symbol}: {e}"
            )

    candidates.sort(
        key=lambda x: (
            x["score"],
            x["trigger_score"],
            abs(
                x["trend_score"]
            ),
        ),
        reverse=True,
    )

    return (
        candidates,
        diagnostics,
    )


# ============================================================
# OPEN BEST CANDIDATES
# ============================================================

def open_candidates(
    state,
    candidates,
):

    opened = []

    open_trades = state.setdefault(
        "open_trades",
        [],
    )

    existing_ids = {
        trade.get("id")
        for trade in open_trades
    }

    existing_symbols = {
        trade.get("symbol")
        for trade in open_trades
    }

    long_count = count_direction_trades(
        open_trades,
        "LONG",
    )

    short_count = count_direction_trades(
        open_trades,
        "SHORT",
    )

    for candidate in candidates:

        if len(
            open_trades
        ) >= MAX_OPEN_TRADES:

            break

        candidate_id = setup_key(
            candidate
        )

        if candidate_id in existing_ids:

            continue

        symbol = candidate[
            "symbol"
        ]

        direction = candidate[
            "direction"
        ]

        if symbol in existing_symbols:

            continue

        if (
            direction == "LONG"
            and long_count
            >= MAX_LONG_TRADES
        ):

            continue

        if (
            direction == "SHORT"
            and short_count
            >= MAX_SHORT_TRADES
        ):

            continue

        trade = create_trade(
            candidate
        )

        open_trades.append(
            trade
        )

        opened.append(
            trade.copy()
        )

        existing_ids.add(
            candidate_id
        )

        existing_symbols.add(
            symbol
        )

        if direction == "LONG":

            long_count += 1

        else:

            short_count += 1

        print(
            "[OPEN]",
            symbol,
            direction,
            f"Score={candidate['score']:.1f}",
            f"Trigger={candidate['trigger_score']}",
        )

    state["open_trades"] = (
        open_trades
    )

    state["updated_at"] = iso_now()

    return opened


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    history
):

    trades = [
        t
        for t in history.get(
            "trades",
            [],
        )
        if t.get("status")
        in (
            "TP",
            "SL",
            "TIMEOUT",
            "AMBIGUOUS",
        )
    ]

    total = len(
        trades
    )

    wins = sum(
        1
        for t in trades
        if t.get("status")
        == "TP"
    )

    losses = sum(
        1
        for t in trades
        if t.get("status")
        == "SL"
    )

    timeouts = sum(
        1
        for t in trades
        if t.get("status")
        == "TIMEOUT"
    )

    ambiguous = sum(
        1
        for t in trades
        if t.get("status")
        == "AMBIGUOUS"
    )

    decided = (
        wins
        + losses
    )

    if decided > 0:

        win_rate = (
            wins
            / decided
            * 100.0
        )

    else:

        win_rate = 0.0

    total_pnl = sum(
        safe_float(
            t.get(
                "pnl_pct",
                0,
            )
        )
        for t in trades
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "ambiguous": ambiguous,
        "win_rate": win_rate,
        "pnl": total_pnl,
    }


# ============================================================
# FORMAT TRADE
# ============================================================

def format_trade(
    trade
):

    direction = trade.get(
        "direction",
        "",
    )

    emoji = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    return (
        f"{emoji} "
        f"{trade.get('symbol')} "
        f"{direction}\n"
        f"Entry: {trade.get('entry')}\n"
        f"SL: {trade.get('sl')}\n"
        f"TP: {trade.get('tp')}\n"
        f"Risk: "
        f"{safe_float(trade.get('risk_pct')):.2f}%\n"
        f"Score: "
        f"{safe_float(trade.get('score')):.1f}\n"
        f"Trigger: "
        f"{trade.get('trigger_score')}/10\n"
        f"Trend: "
        f"{safe_float(trade.get('trend_score')):+.2f}\n"
        f"Reversal: "
        f"{safe_float(trade.get('reversal_score')):+.2f}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    text
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[TELEGRAM] BOT TOKEN missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[TELEGRAM] CHAT ID missing"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=HTTP_TIMEOUT,
        )

        response.raise_for_status()

        return True

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )

        return False


# ============================================================
# REPORT
# ============================================================

def build_report(
    state,
    history,
    opened,
    closed_this_run,
    diagnostics,
    market_count,
):

    performance = calculate_performance(
        history
    )

    open_trades = state.get(
        "open_trades",
        [],
    )

    lines = []

    lines.append(
        "📡 CRYPTO ICHIMOKU REPORT"
    )

    lines.append(
        f"🕐 {iso_now()}"
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

    # ========================================================
    # OPEN
    # ========================================================

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/{MAX_OPEN_TRADES})"
    )

    if open_trades:

        for i, trade in enumerate(
            open_trades,
            1,
        ):

            direction = trade.get(
                "direction"
            )

            emoji = (
                "🟢"
                if direction == "LONG"
                else "🔴"
            )

            pnl = safe_float(
                trade.get(
                    "pnl_pct",
                    0,
                )
            )

            lines.append(
                f"{i}. "
                f"{emoji} "
                f"{trade.get('symbol')} "
                f"{direction}"
            )

            lines.append(
                f"Entry: "
                f"{trade.get('entry')} "
                f"Now: "
                f"{trade.get('last_price')} "
                f"({pnl:+.2f}%)"
            )

            lines.append(
                f"SL: "
                f"{trade.get('sl')}"
            )

            lines.append(
                f"TP: "
                f"{trade.get('tp')}"
            )

    else:

        lines.append(
            "No open trades."
        )

    # ========================================================
    # CLOSED THIS RUN
    # ========================================================

    if closed_this_run:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "📕 CLOSED THIS RUN"
        )

        for trade in closed_this_run:

            status = trade.get(
                "status"
            )

            pnl = safe_float(
                trade.get(
                    "pnl_pct",
                    0,
                )
            )

            if status == "TP":

                emoji = "🟢"

            elif status == "SL":

                emoji = "🔴"

            elif status == "TIMEOUT":

                emoji = "🟡"

            else:

                emoji = "⚠️"

            lines.append(
                f"{emoji} "
                f"{trade.get('symbol')} "
                f"{trade.get('direction')} "
                f"{status} "
                f"{pnl:+.2f}%"
            )

            lines.append(
                f"Exit: "
                f"{trade.get('exit_price')} "
                f"@ "
                f"{trade.get('exit_time_iso')}"
            )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🚨 NEW SIGNALS"
    )

    if opened:

        for trade in opened:

            lines.append(
                format_trade(
                    trade
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "No qualified setup."
        )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔎 SCANNER DIAGNOSTICS"
    )

    lines.append(
        f"Markets: "
        f"{market_count}"
    )

    lines.append(
        f"Data OK: "
        f"{diagnostics['data_ok']}"
    )

    lines.append(
        f"LONG direction: "
        f"{diagnostics['long_direction']}"
    )

    lines.append(
        f"SHORT direction: "
        f"{diagnostics['short_direction']}"
    )

    lines.append(
        f"LONG pullback: "
        f"{diagnostics['long_pullback']}"
    )

    lines.append(
        f"SHORT pullback: "
        f"{diagnostics['short_pullback']}"
    )

    lines.append(
        f"LONG trigger: "
        f"{diagnostics['long_trigger']}"
    )

    lines.append(
        f"SHORT trigger: "
        f"{diagnostics['short_trigger']}"
    )

    lines.append(
        f"LONG trend: "
        f"{diagnostics['long_trend']}"
    )

    lines.append(
        f"SHORT trend: "
        f"{diagnostics['short_trend']}"
    )

    lines.append(
        f"LONG rank: "
        f"{diagnostics['long_rank']}"
    )

    lines.append(
        f"SHORT rank: "
        f"{diagnostics['short_rank']}"
    )

    lines.append(
        f"Levels OK: "
        f"{diagnostics['levels_ok']}"
    )

    lines.append(
        f"Risk OK: "
        f"{diagnostics['risk_ok']}"
    )

    lines.append(
        f"QUALIFIED: "
        f"{diagnostics['qualified']}"
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 PERFORMANCE"
    )

    lines.append(
        f"Trades: "
        f"{performance['total']}"
    )

    lines.append(
        f"TP: "
        f"{performance['wins']}"
    )

    lines.append(
        f"SL: "
        f"{performance['losses']}"
    )

    lines.append(
        f"Timeout: "
        f"{performance['timeouts']}"
    )

    lines.append(
        f"Ambiguous: "
        f"{performance['ambiguous']}"
    )

    lines.append(
        f"Win Rate: "
        f"{performance['win_rate']:.1f}%"
    )

    lines.append(
        f"P/L: "
        f"{performance['pnl']:+.2f}%"
    )

    lines.append(
        f"Version: "
        f"{STRATEGY_VERSION}"
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
        f"KRAKEN FUTURES ICHIMOKU "
        f"TOP RANKER {STRATEGY_VERSION}"
    )

    print("=" * 70)

    # ========================================================
    # LOAD
    # ========================================================

    state = load_state()

    history = load_history()

    print(
        f"[INFO] Existing open trades: "
        f"{len(state.get('open_trades', []))}"
    )

    print(
        f"[INFO] Historical trades: "
        f"{len(history.get('trades', []))}"
    )

    # ========================================================
    # CRITICAL:
    # MONITOR BEFORE SCANNING
    # ========================================================

    changed, closed_this_run = (
        monitor_open_trades(
            state,
            history,
        )
    )

    # --------------------------------------------------------
    # SAVE IMMEDIATELY
    # --------------------------------------------------------

    state_saved = save_json_file(
        STATE_FILE,
        state,
    )

    history_saved = save_json_file(
        HISTORY_FILE,
        history,
    )

    print(
        f"[STATE SAVE] "
        f"{state_saved}"
    )

    print(
        f"[HISTORY SAVE] "
        f"{history_saved}"
    )

    # ========================================================
    # MARKETS
    # ========================================================

    markets = build_market_metadata()

    if not markets:

        print(
            "[ERROR] No markets available."
        )

        report = build_report(
            state,
            history,
            [],
            closed_this_run,
            new_diagnostics(),
            0,
        )

        send_telegram(
            report
        )

        return

    print(
        f"[INFO] TOP {len(markets)}"
    )

    # ========================================================
    # SCAN
    # ========================================================

    candidates, diagnostics = scan_markets(
        markets,
        state,
    )

    print(
        f"[INFO] Qualified candidates: "
        f"{len(candidates)}"
    )

    # ========================================================
    # PRINT CANDIDATES
    # ========================================================

    for candidate in candidates:

        print(
            "--------------------------------"
        )

        print(
            f"{candidate['symbol']} "
            f"{candidate['direction']}"
        )

        print(
            f"Score: "
            f"{candidate['score']:.1f}"
        )

        print(
            f"Trigger: "
            f"{candidate['trigger_score']}/10"
        )

        print(
            f"Trend: "
            f"{candidate['trend_score']:+.2f}"
        )

        print(
            f"Reversal: "
            f"{candidate['reversal_score']:+.2f}"
        )

        print(
            f"Entry: "
            f"{candidate['entry']}"
        )

        print(
            f"SL: "
            f"{candidate['sl']}"
        )

        print(
            f"TP: "
            f"{candidate['tp']}"
        )

        print(
            f"Risk: "
            f"{candidate['risk_pct']:.2f}%"
        )

    # ========================================================
    # OPEN
    # ========================================================

    opened = open_candidates(
        state,
        candidates,
    )

    # ========================================================
    # SAVE AFTER OPENING
    # ========================================================

    state["updated_at"] = iso_now()

    history["updated_at"] = iso_now()

    state_saved = save_json_file(
        STATE_FILE,
        state,
    )

    history_saved = save_json_file(
        HISTORY_FILE,
        history,
    )

    print(
        f"[FINAL STATE SAVE] "
        f"{state_saved}"
    )

    print(
        f"[FINAL HISTORY SAVE] "
        f"{history_saved}"
    )

    # ========================================================
    # NEW SIGNAL TELEGRAM
    # ========================================================

    if opened:

        signal_lines = []

        signal_lines.append(
            "🚨 NEW ICHIMOKU SIGNAL"
        )

        signal_lines.append(
            f"🕐 {iso_now()}"
        )

        signal_lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for trade in opened:

            signal_lines.append(
                format_trade(
                    trade
                )
            )

            signal_lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

        send_telegram(
            "\n".join(
                signal_lines
            )
        )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    report = build_report(
        state,
        history,
        opened,
        closed_this_run,
        diagnostics,
        len(markets),
    )

    send_telegram(
        report
    )

    # ========================================================
    # CONSOLE
    # ========================================================

    performance = calculate_performance(
        history
    )

    print("=" * 70)

    print(
        f"OPEN TRADES: "
        f"{len(state.get('open_trades', []))}"
    )

    print(
        f"HISTORY: "
        f"{performance['total']}"
    )

    print(
        f"TP: "
        f"{performance['wins']}"
    )

    print(
        f"SL: "
        f"{performance['losses']}"
    )

    print(
        f"TIMEOUT: "
        f"{performance['timeouts']}"
    )

    print(
        f"AMBIGUOUS: "
        f"{performance['ambiguous']}"
    )

    print(
        f"WIN RATE: "
        f"{performance['win_rate']:.1f}%"
    )

    print(
        f"P/L: "
        f"{performance['pnl']:+.2f}%"
    )

    print(
        f"QUALIFIED: "
        f"{diagnostics['qualified']}"
    )

    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
