# ============================================================
# CRYPTO TENKAN/KIJUN 26 BOX BREAKOUT SCANNER
# ============================================================
# Kraken Futures
# 5m CLOSED CANDLES
#
# STRATEGY
# ------------------------------------------------------------
# 1) Select TOP 30 markets with abnormal volume
#
# 2) Detect Tenkan / Kijun crossover on 5m
#
# 3) At crossover:
#       Box High = highest HIGH of previous 26 candles
#       Box Low  = lowest LOW  of previous 26 candles
#
#    Crossover candle itself is NOT included in box.
#
# 4) After crossover:
#       BUY  = CLOSED candle CLOSE > Box High
#       SELL = CLOSED candle CLOSE < Box Low
#
# 5) SL:
#       BUY  = below latest valid Swing Low
#       SELL = above latest valid Swing High
#
#       Swing = pivot with 2 candles on each side
#
# 6) TP:
#       50% of Box Width
#
# 7) Only ONE open trade
#
# 8) Live P&L
# 9) Win Rate
# 10) Total R
#
# ============================================================

import os
import json
import math
import time
import statistics
import traceback

from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com"

TIMEFRAME = 5

TOP_N = 30

MAX_OPEN_TRADES = 1

BOX_LENGTH = 26

SWING_LEFT = 2
SWING_RIGHT = 2

TP_BOX_MULTIPLIER = 0.50

# SL buffer
SL_BUFFER_PCT = 0.0015

# Abnormal volume
VOLUME_LOOKBACK = 20
MIN_VOLUME_RATIO = 1.20

# Minimum candles
MIN_CANDLES = 100

# State
STATE_FILE = "price_action_state.json"
HISTORY_FILE = "price_action_trade_history.json"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

# HTTP
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3


# ============================================================
# GLOBAL SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
        "Crypto-Tenkan-Kijun-Scanner/2.0"
    }
)


# ============================================================
# UTILS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_string():
    return now_utc().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=0.0):

    try:
        return float(value)

    except Exception:
        return default


def pct_change(current, reference):

    if reference == 0:
        return 0.0

    return (
        (current - reference)
        / reference
    ) * 100.0


def format_price(price):

    if price is None:
        return "-"

    try:
        price = float(price)

    except Exception:
        return "-"

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 100:
        return f"{price:,.3f}"

    if price >= 1:
        return f"{price:,.4f}"

    if price >= 0.01:
        return f"{price:,.5f}"

    return f"{price:,.8f}"


def format_pct(value):
    return f"{value:+.2f}%"


def format_time(timestamp):

    try:
        return datetime.fromtimestamp(
            int(timestamp),
            tz=timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    except Exception:
        return "-"


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

    except Exception as exc:

        print(
            f"[JSON LOAD ERROR] {path}: "
            f"{repr(exc)}",
            flush=True
        )

        traceback.print_exc()

        return default


def save_json(path, data):

    temp = path + ".tmp"

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
        path
    )


def load_state():

    default = {
        "open_trade": None,
        "last_scan_candle": None,
        "last_signals": {}
    }

    state = load_json(
        STATE_FILE,
        default
    )

    if not isinstance(state, dict):
        state = default

    if "open_trade" not in state:
        state["open_trade"] = None

    if "last_scan_candle" not in state:
        state["last_scan_candle"] = None

    if "last_signals" not in state:
        state["last_signals"] = {}

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state
    )


def load_history():

    default = {
        "trades": []
    }

    history = load_json(
        HISTORY_FILE,
        default
    )

    if not isinstance(history, dict):
        history = default

    if "trades" not in history:
        history["trades"] = []

    return history


def save_history(history):

    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# HTTP
# ============================================================

def get_json(
    url,
    params=None
):

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            print(
                f"[HTTP] GET {url} "
                f"attempt={attempt}",
                flush=True
            )

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            print(
                f"[HTTP] STATUS {response.status_code}",
                flush=True
            )

            response.raise_for_status()

            data = response.json()

            if not isinstance(data, dict):
                raise ValueError(
                    "API response is not a JSON object"
                )

            # Kraken API may return an error field
            errors = data.get("errors")

            if errors:
                print(
                    f"[KRAKEN API ERRORS] "
                    f"{errors}",
                    flush=True
                )

            return data

        except Exception as exc:

            last_error = exc

            print(
                f"[HTTP ERROR] "
                f"{url}",
                flush=True
            )

            print(
                f"[HTTP ERROR DETAIL] "
                f"{repr(exc)}",
                flush=True
            )

            traceback.print_exc()

            if attempt < MAX_RETRIES:

                print(
                    "[HTTP] Retrying...",
                    flush=True
                )

                time.sleep(1.5)

    print(
        f"[HTTP FAILED] {url}",
        flush=True
    )

    print(
        f"[HTTP FINAL ERROR] "
        f"{repr(last_error)}",
        flush=True
    )

    return None


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_instruments():

    url = (
        f"{BASE_URL}"
        "/derivatives/api/v3/instruments"
    )

    data = get_json(url)

    if not data:
        return []

    instruments = data.get(
        "instruments",
        []
    )

    if not isinstance(
        instruments,
        list
    ):

        print(
            "[ERROR] instruments is not list",
            flush=True
        )

        return []

    result = []

    for item in instruments:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = item.get(
            "symbol",
            ""
        )

        tradeable = item.get(
            "tradeable",
            True
        )

        if not symbol:
            continue

        if not tradeable:
            continue

        # Perpetual contracts
        if not symbol.startswith("PI_"):
            continue

        result.append(item)

    print(
        f"[INFO] Perpetual instruments: "
        f"{len(result)}",
        flush=True
    )

    return result


# ============================================================
# KRAKEN TICKERS
# ============================================================

def get_tickers():

    url = (
        f"{BASE_URL}"
        "/derivatives/api/v3/tickers"
    )

    data = get_json(url)

    if not data:
        return []

    tickers = data.get(
        "tickers",
        []
    )

    if not isinstance(
        tickers,
        list
    ):

        print(
            "[ERROR] tickers is not list",
            flush=True
        )

        return []

    return tickers


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution=5,
    count=150
):

    url = (
        f"{BASE_URL}"
        f"/api/charts/v1/trade/"
        f"{symbol}/{resolution}"
    )

    seconds = resolution * 60

    end_time = int(
        time.time()
    )

    start_time = (
        end_time
        - (count * seconds * 2)
    )

    params = {
        "from": start_time,
        "to": end_time
    }

    data = get_json(
        url,
        params=params
    )

    if not data:
        return []

    candles = data.get(
        "candles",
        []
    )

    if not isinstance(
        candles,
        list
    ):

        print(
            f"[ERROR] candles invalid "
            f"for {symbol}",
            flush=True
        )

        return []

    result = []

    for c in candles:

        try:

            if not isinstance(
                c,
                dict
            ):
                continue

            ts = int(
                c.get("time")
                or c.get("timestamp")
                or 0
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

            cl = safe_float(
                c.get("close")
            )

            v = safe_float(
                c.get("volume")
            )

            if ts <= 0:
                continue

            if cl <= 0:
                continue

            if h <= 0:
                continue

            if l <= 0:
                continue

            result.append(
                {
                    "time": ts,
                    "open": o,
                    "high": h,
                    "low": l,
                    "close": cl,
                    "volume": v
                }
            )

        except Exception as exc:

            print(
                f"[CANDLE PARSE ERROR] "
                f"{symbol}: {repr(exc)}",
                flush=True
            )

            traceback.print_exc()

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

    if len(result) > count:

        result = result[-count:]

    return result


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(c):
    return abs(
        c["close"] - c["open"]
    )


def candle_range(c):
    return max(
        c["high"] - c["low"],
        1e-12
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


# ============================================================
# ICHIMOKU
# ============================================================

def donchian_value(
    candles,
    index,
    length
):

    if index - length + 1 < 0:
        return None

    window = candles[
        index - length + 1:
        index + 1
    ]

    if len(window) != length:
        return None

    highs = [
        c["high"]
        for c in window
    ]

    lows = [
        c["low"]
        for c in window
    ]

    return (
        max(highs)
        + min(lows)
    ) / 2.0


def calculate_tenkan(
    candles,
    index
):

    return donchian_value(
        candles,
        index,
        9
    )


def calculate_kijun(
    candles,
    index
):

    return donchian_value(
        candles,
        index,
        26
    )


def crossover_at(
    candles,
    index
):

    if index < 30:
        return None

    tenkan_prev = calculate_tenkan(
        candles,
        index - 1
    )

    kijun_prev = calculate_kijun(
        candles,
        index - 1
    )

    tenkan_now = calculate_tenkan(
        candles,
        index
    )

    kijun_now = calculate_kijun(
        candles,
        index
    )

    if any(
        value is None
        for value in (
            tenkan_prev,
            kijun_prev,
            tenkan_now,
            kijun_now
        )
    ):
        return None

    # Bullish
    if (
        tenkan_prev <= kijun_prev
        and
        tenkan_now > kijun_now
    ):

        return "BUY"

    # Bearish
    if (
        tenkan_prev >= kijun_prev
        and
        tenkan_now < kijun_now
    ):

        return "SELL"

    return None


# ============================================================
# BOX
# ============================================================

def get_box(
    candles,
    cross_index
):

    start = (
        cross_index
        - BOX_LENGTH
    )

    end = cross_index

    if start < 0:
        return None

    previous = candles[
        start:end
    ]

    if len(previous) != BOX_LENGTH:
        return None

    box_high = max(
        c["high"]
        for c in previous
    )

    box_low = min(
        c["low"]
        for c in previous
    )

    width = (
        box_high
        - box_low
    )

    if width <= 0:
        return None

    return {
        "high": box_high,
        "low": box_low,
        "width": width
    }


# ============================================================
# SWINGS
# ============================================================

def is_swing_low(
    candles,
    index
):

    if (
        index - SWING_LEFT < 0
        or
        index + SWING_RIGHT
        >= len(candles)
    ):
        return False

    current = candles[
        index
    ]["low"]

    for i in range(
        index - SWING_LEFT,
        index
    ):

        if current >= candles[i]["low"]:
            return False

    for i in range(
        index + 1,
        index + SWING_RIGHT + 1
    ):

        if current >= candles[i]["low"]:
            return False

    return True


def is_swing_high(
    candles,
    index
):

    if (
        index - SWING_LEFT < 0
        or
        index + SWING_RIGHT
        >= len(candles)
    ):
        return False

    current = candles[
        index
    ]["high"]

    for i in range(
        index - SWING_LEFT,
        index
    ):

        if current <= candles[i]["high"]:
            return False

    for i in range(
        index + 1,
        index + SWING_RIGHT + 1
    ):

        if current <= candles[i]["high"]:
            return False

    return True


def find_last_swing_low(
    candles,
    start_index,
    end_index
):

    start_index = max(
        SWING_LEFT,
        start_index
    )

    end_index = min(
        len(candles)
        - SWING_RIGHT
        - 1,
        end_index
    )

    for i in range(
        end_index,
        start_index - 1,
        -1
    ):

        if is_swing_low(
            candles,
            i
        ):

            return {
                "index": i,
                "price": candles[i]["low"]
            }

    return None


def find_last_swing_high(
    candles,
    start_index,
    end_index
):

    start_index = max(
        SWING_LEFT,
        start_index
    )

    end_index = min(
        len(candles)
        - SWING_RIGHT
        - 1,
        end_index
    )

    for i in range(
        end_index,
        start_index - 1,
        -1
    ):

        if is_swing_high(
            candles,
            i
        ):

            return {
                "index": i,
                "price": candles[i]["high"]
            }

    return None


# ============================================================
# VOLUME
# ============================================================

def volume_ratio(
    candles
):

    if len(candles) < (
        VOLUME_LOOKBACK + 2
    ):
        return 0.0

    # Last CLOSED candle
    current_volume = candles[-1][
        "volume"
    ]

    previous = candles[
        -VOLUME_LOOKBACK - 1:
        -1
    ]

    volumes = [
        c["volume"]
        for c in previous
        if c["volume"] > 0
    ]

    if not volumes:
        return 0.0

    avg_volume = statistics.mean(
        volumes
    )

    if avg_volume <= 0:
        return 0.0

    return (
        current_volume
        / avg_volume
    )


# ============================================================
# TOP ABNORMAL VOLUME
# ============================================================

def get_top_volume_markets():

    print(
        "[INFO] Loading instruments...",
        flush=True
    )

    instruments = get_instruments()

    if not instruments:

        print(
            "[ERROR] No instruments",
            flush=True
        )

        return []

    print(
        "[INFO] Loading tickers...",
        flush=True
    )

    tickers = get_tickers()

    if not tickers:

        print(
            "[ERROR] No tickers",
            flush=True
        )

        return []

    ticker_map = {}

    for ticker in tickers:

        if not isinstance(
            ticker,
            dict
        ):
            continue

        symbol = ticker.get(
            "symbol"
        )

        if symbol:
            ticker_map[symbol] = ticker

    candidates = []

    print(
        f"[INFO] Markets available: "
        f"{len(instruments)}",
        flush=True
    )

    for instrument in instruments:

        symbol = instrument.get(
            "symbol"
        )

        ticker = ticker_map.get(
            symbol
        )

        if not ticker:
            continue

        volume24 = safe_float(
            ticker.get("vol24h")
            or
            ticker.get("volume24h")
            or
            ticker.get("volume")
        )

        if volume24 <= 0:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "volume24": volume24
            }
        )

    candidates.sort(
        key=lambda x:
        x["volume24"],
        reverse=True
    )

    # First pool
    candidates = candidates[:80]

    print(
        f"[INFO] Checking abnormal "
        f"volume for {len(candidates)} "
        f"markets...",
        flush=True
    )

    ranked = []

    for number, item in enumerate(
        candidates,
        start=1
    ):

        symbol = item["symbol"]

        print(
            f"[VOLUME] "
            f"{number:02d}/"
            f"{len(candidates)} "
            f"{symbol}",
            flush=True
        )

        try:

            candles = get_candles(
                symbol,
                TIMEFRAME,
                100
            )

            if len(candles) < (
                VOLUME_LOOKBACK + 2
            ):

                print(
                    f"[VOLUME SKIP] "
                    f"{symbol} "
                    f"candles={len(candles)}",
                    flush=True
                )

                continue

            ratio = volume_ratio(
                candles
            )

            print(
                f"[VOLUME] "
                f"{symbol} "
                f"ratio={ratio:.2f}x",
                flush=True
            )

            if ratio < MIN_VOLUME_RATIO:
                continue

            ranked.append(
                {
                    "symbol": symbol,
                    "volume24":
                        item["volume24"],
                    "volume_ratio":
                        ratio
                }
            )

        except Exception as exc:

            print(
                f"[VOLUME ERROR] "
                f"{symbol}: "
                f"{repr(exc)}",
                flush=True
            )

            traceback.print_exc()

    ranked.sort(
        key=lambda x: (
            x["volume_ratio"],
            math.log1p(
                x["volume24"]
            )
        ),
        reverse=True
    )

    return ranked[:TOP_N]


# ============================================================
# FIND LATEST CROSS
# ============================================================

def find_latest_setup(
    symbol,
    candles
):

    if len(candles) < 80:
        return None

    # Ignore current possibly-forming candle
    closed = candles[:-1]

    if len(closed) < 60:
        return None

    max_age = 40

    start = max(
        30,
        len(closed) - max_age
    )

    latest_setup = None

    for i in range(
        start,
        len(closed)
    ):

        cross = crossover_at(
            closed,
            i
        )

        if cross is None:
            continue

        box = get_box(
            closed,
            i
        )

        if box is None:
            continue

        latest_setup = {
            "symbol": symbol,
            "side": cross,
            "cross_index": i,
            "cross_time":
                closed[i]["time"],
            "box_high":
                box["high"],
            "box_low":
                box["low"],
            "box_width":
                box["width"]
        }

    return latest_setup


# ============================================================
# BREAKOUT
# ============================================================

def check_breakout(
    setup,
    candles
):

    if setup is None:
        return None

    closed = candles[:-1]

    cross_index = setup[
        "cross_index"
    ]

    if cross_index >= (
        len(closed) - 1
    ):
        return None

    for i in range(
        cross_index + 1,
        len(closed)
    ):

        candle = closed[i]

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if setup["side"] == "BUY":

            if candle["close"] <= (
                setup["box_high"]
            ):
                continue

            swing_end = (
                i - SWING_RIGHT
            )

            swing = find_last_swing_low(
                closed,
                cross_index,
                swing_end
            )

            if not swing:
                continue

            entry = candle["close"]

            sl = (
                swing["price"]
                * (1.0 - SL_BUFFER_PCT)
            )

            if sl >= entry:
                continue

            tp = (
                entry
                +
                setup["box_width"]
                *
                TP_BOX_MULTIPLIER
            )

            return {
                "side": "BUY",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "box_high":
                    setup["box_high"],
                "box_low":
                    setup["box_low"],
                "box_width":
                    setup["box_width"],
                "cross_time":
                    setup["cross_time"],
                "breakout_time":
                    candle["time"],
                "swing_price":
                    swing["price"],
                "breakout_index": i
            }

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        if setup["side"] == "SELL":

            if candle["close"] >= (
                setup["box_low"]
            ):
                continue

            swing_end = (
                i - SWING_RIGHT
            )

            swing = find_last_swing_high(
                closed,
                cross_index,
                swing_end
            )

            if not swing:
                continue

            entry = candle["close"]

            sl = (
                swing["price"]
                * (1.0 + SL_BUFFER_PCT)
            )

            if sl <= entry:
                continue

            tp = (
                entry
                -
                setup["box_width"]
                *
                TP_BOX_MULTIPLIER
            )

            return {
                "side": "SELL",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "box_high":
                    setup["box_high"],
                "box_low":
                    setup["box_low"],
                "box_width":
                    setup["box_width"],
                "cross_time":
                    setup["cross_time"],
                "breakout_time":
                    candle["time"],
                "swing_price":
                    swing["price"],
                "breakout_index": i
            }

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol
):

    tickers = get_tickers()

    if not tickers:
        return None

    for ticker in tickers:

        if not isinstance(
            ticker,
            dict
        ):
            continue

        if ticker.get(
            "symbol"
        ) != symbol:
            continue

        price = (
            ticker.get("last")
            or
            ticker.get("lastPrice")
            or
            ticker.get("markPrice")
        )

        price = safe_float(
            price
        )

        if price > 0:
            return price

    return None


# ============================================================
# LIVE PNL
# ============================================================

def calculate_live_pnl(
    side,
    entry,
    current
):

    if entry <= 0:
        return 0.0

    if side == "BUY":

        return (
            (current - entry)
            / entry
        ) * 100.0

    return (
        (entry - current)
        / entry
    ) * 100.0


# ============================================================
# R
# ============================================================

def calculate_r(
    trade,
    exit_price
):

    entry = safe_float(
        trade.get("entry")
    )

    sl = safe_float(
        trade.get("sl")
    )

    risk = abs(
        entry - sl
    )

    if risk <= 0:
        return 0.0

    if trade["side"] == "BUY":

        return (
            (exit_price - entry)
            / risk
        )

    return (
        (entry - exit_price)
        / risk
    )


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    state,
    history,
    exit_price,
    result_r,
    result
):

    trade = state.get(
        "open_trade"
    )

    if not trade:
        return

    trade["exit"] = exit_price

    trade["result"] = result

    trade["result_r"] = result_r

    trade["closed_at"] = int(
        time.time()
    )

    history["trades"].append(
        trade
    )

    if len(
        history["trades"]
    ) > 1000:

        history["trades"] = (
            history["trades"][-1000:]
        )

    state["open_trade"] = None

    save_history(
        history
    )

    save_state(
        state
    )

    print(
        "[TRADE CLOSED] "
        f"{trade['symbol']} "
        f"{result} "
        f"R={result_r:+.2f}",
        flush=True
    )

    send_telegram(
        build_close_message(
            trade
        )
    )


# ============================================================
# UPDATE OPEN TRADE
# ============================================================

def update_open_trade(
    state,
    history
):

    trade = state.get(
        "open_trade"
    )

    if not trade:
        return False

    symbol = trade[
        "symbol"
    ]

    print(
        f"[TRADE] Updating "
        f"{symbol}",
        flush=True
    )

    price = get_current_price(
        symbol
    )

    if price is None:

        print(
            f"[WARN] No price for "
            f"{symbol}",
            flush=True
        )

        return False

    trade[
        "current_price"
    ] = price

    trade[
        "live_pnl_pct"
    ] = calculate_live_pnl(
        trade["side"],
        trade["entry"],
        price
    )

    print(
        f"[TRADE] Current="
        f"{format_price(price)} "
        f"PnL="
        f"{trade['live_pnl_pct']:+.2f}%",
        flush=True
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if trade["side"] == "BUY":

        if price <= trade["sl"]:

            exit_price = trade["sl"]

            result_r = calculate_r(
                trade,
                exit_price
            )

            close_trade(
                state,
                history,
                exit_price,
                result_r,
                "SL"
            )

            return True

        if price >= trade["tp"]:

            exit_price = trade["tp"]

            result_r = calculate_r(
                trade,
                exit_price
            )

            close_trade(
                state,
                history,
                exit_price,
                result_r,
                "TP"
            )

            return True

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    else:

        if price >= trade["sl"]:

            exit_price = trade["sl"]

            result_r = calculate_r(
                trade,
                exit_price
            )

            close_trade(
                state,
                history,
                exit_price,
                result_r,
                "SL"
            )

            return True

        if price <= trade["tp"]:

            exit_price = trade["tp"]

            result_r = calculate_r(
                trade,
                exit_price
            )

            close_trade(
                state,
                history,
                exit_price,
                result_r,
                "TP"
            )

            return True

    return True


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    state,
    signal,
    volume_ratio_value
):

    if state.get(
        "open_trade"
    ):

        print(
            "[INFO] Trade already open",
            flush=True
        )

        return False

    trade = {

        "symbol":
            signal["symbol"],

        "side":
            signal["side"],

        "entry":
            signal["entry"],

        "sl":
            signal["sl"],

        "tp":
            signal["tp"],

        "box_high":
            signal["box_high"],

        "box_low":
            signal["box_low"],

        "box_width":
            signal["box_width"],

        "swing_price":
            signal["swing_price"],

        "cross_time":
            signal["cross_time"],

        "breakout_time":
            signal["breakout_time"],

        "opened_at":
            int(time.time()),

        "current_price":
            signal["entry"],

        "live_pnl_pct":
            0.0,

        "volume_ratio":
            volume_ratio_value
    }

    state["open_trade"] = trade

    save_state(
        state
    )

    print(
        "[OPEN] "
        f"{trade['symbol']} "
        f"{trade['side']} "
        f"Entry="
        f"{format_price(trade['entry'])} "
        f"SL="
        f"{format_price(trade['sl'])} "
        f"TP="
        f"{format_price(trade['tp'])}",
        flush=True
    )

    send_telegram(
        build_open_message(
            trade
        )
    )

    return True


# ============================================================
# PERFORMANCE
# ============================================================

def performance(
    history
):

    trades = history.get(
        "trades",
        []
    )

    wins = 0
    losses = 0
    total_r = 0.0

    for trade in trades:

        r = safe_float(
            trade.get(
                "result_r"
            )
        )

        total_r += r

        if trade.get(
            "result"
        ) == "TP":

            wins += 1

        elif trade.get(
            "result"
        ) == "SL":

            losses += 1

    closed = (
        wins
        + losses
    )

    if closed > 0:

        wr = (
            wins
            / closed
        ) * 100.0

    else:

        wr = 0.0

    return {
        "trades": closed,
        "wins": wins,
        "losses": losses,
        "wr": wr,
        "total_r": total_r
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] "
            "TELEGRAM_BOT_TOKEN missing",
            flush=True
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] "
            "TELEGRAM_CHAT_ID missing",
            flush=True
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            message,

        "parse_mode":
            "HTML",

        "disable_web_page_preview":
            True
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:

            print(
                "[TELEGRAM] Message sent",
                flush=True
            )

            return True

        print(
            "[TELEGRAM ERROR] "
            f"HTTP {response.status_code}",
            flush=True
        )

        print(
            response.text,
            flush=True
        )

    except Exception as exc:

        print(
            "[TELEGRAM EXCEPTION]",
            repr(exc),
            flush=True
        )

        traceback.print_exc()

    return False


# ============================================================
# TELEGRAM OPEN MESSAGE
# ============================================================

def build_open_message(
    trade
):

    entry = trade["entry"]

    sl = trade["sl"]

    tp = trade["tp"]

    sl_pct = abs(
        pct_change(
            sl,
            entry
        )
    )

    tp_pct = abs(
        pct_change(
            tp,
            entry
        )
    )

    side_icon = (
        "🟢"
        if trade["side"] == "BUY"
        else "🔴"
    )

    return (
        "📡 <b>PRICE ACTION BREAKOUT</b>\n"
        f"🕐 {now_string()}\n"
        "⏱ <b>5m CLOSED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{side_icon} "
        f"<b>{trade['symbol']} "
        f"{trade['side']}</b>\n\n"
        f"🎯 Entry: "
        f"<b>{format_price(entry)}</b>\n"
        f"🛑 SL: "
        f"<b>{format_price(sl)}</b> "
        f"({format_pct(-sl_pct)})\n"
        f"🎯 TP: "
        f"<b>{format_price(tp)}</b> "
        f"({format_pct(tp_pct)})\n\n"
        f"📦 Box High: "
        f"{format_price(trade['box_high'])}\n"
        f"📦 Box Low: "
        f"{format_price(trade['box_low'])}\n"
        f"📐 Box Width: "
        f"{format_price(trade['box_width'])}\n\n"
        f"🔄 Cross: "
        f"{format_time(trade['cross_time'])}\n"
        f"⚡ Breakout Close: "
        f"{format_price(entry)}\n"
        f"🦴 Swing: "
        f"{format_price(trade['swing_price'])}\n"
        f"📊 Volume Ratio: "
        f"{trade['volume_ratio']:.2f}x\n\n"
        "📂 <b>OPEN: 1/1</b>"
    )


# ============================================================
# TELEGRAM CLOSE MESSAGE
# ============================================================

def build_close_message(
    trade
):

    result = trade.get(
        "result",
        "?"
    )

    result_r = safe_float(
        trade.get(
            "result_r"
        )
    )

    if result == "TP":

        icon = "🟢"

    elif result == "SL":

        icon = "🔴"

    else:

        icon = "⚪"

    return (
        f"{icon} "
        "<b>TRADE CLOSED</b>\n"
        f"🕐 {now_string()}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 {trade['symbol']} "
        f"{trade['side']}\n"
        f"Entry: "
        f"{format_price(trade['entry'])}\n"
        f"Exit: "
        f"{format_price(trade['exit'])}\n"
        f"Result: <b>{result}</b>\n"
        f"R: <b>{result_r:+.2f}R</b>\n"
    )


# ============================================================
# TELEGRAM REPORT
# ============================================================

def build_report(
    history,
    state,
    scanned,
    events
):

    perf = performance(
        history
    )

    open_trade = state.get(
        "open_trade"
    )

    if open_trade:

        current = open_trade.get(
            "current_price"
        )

        live = safe_float(
            open_trade.get(
                "live_pnl_pct"
            )
        )

        sl_percentage = (
            -abs(
                pct_change(
                    open_trade["sl"],
                    open_trade["entry"]
                )
            )
        )

        tp_percentage = (
            abs(
                pct_change(
                    open_trade["tp"],
                    open_trade["entry"]
                )
            )
        )

        open_text = (
            "📌 <b>OPEN TRADE</b>\n"
            f"{open_trade['symbol']} "
            f"{open_trade['side']}\n"
            f"Entry: "
            f"{format_price(open_trade['entry'])}\n"
            f"SL: "
            f"{format_price(open_trade['sl'])} "
            f"({format_pct(sl_percentage)})\n"
            f"TP: "
            f"{format_price(open_trade['tp'])} "
            f"({format_pct(tp_percentage)})\n"
            f"Current: "
            f"{format_price(current)}\n"
            f"📈 Live P&L: "
            f"<b>{format_pct(live)}</b>\n"
        )

    else:

        open_text = (
            "📂 <b>OPEN: 0/1</b>\n"
        )

    return (
        "📡 <b>CRYPTO PRICE ACTION REPORT</b>\n"
        f"🕐 {now_string()}\n"
        "⏱ <b>5m CLOSED</b>\n"
        "🤖 <b>TENKAN/KIJUN 26 BOX</b>\n"
        f"🎯 TOP {TOP_N} ABNORMAL VOLUME\n"
        "🔒 MAX OPEN: 1\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 <b>PERFORMANCE</b>\n"
        f"Trades: {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ 0\n"
        f"🏆 WR: {perf['wr']:.1f}%\n"
        f"📈 Total R: "
        f"{perf['total_r']:+.2f}\n"
        f"🔎 Scanned: {scanned}\n"
        f"⚡ THIS RUN: "
        f"{events} EVENTS\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"{open_text}"
    )


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    item,
    state
):

    symbol = item["symbol"]

    print(
        f"[SCAN MARKET] {symbol}",
        flush=True
    )

    candles = get_candles(
        symbol,
        TIMEFRAME,
        160
    )

    print(
        f"[SCAN MARKET] "
        f"{symbol} candles="
        f"{len(candles)}",
        flush=True
    )

    if len(candles) < 80:

        print(
            f"[SKIP] {symbol} "
            f"not enough candles",
            flush=True
        )

        return None

    ratio = volume_ratio(
        candles
    )

    if ratio < MIN_VOLUME_RATIO:

        print(
            f"[SKIP] {symbol} "
            f"volume={ratio:.2f}x",
            flush=True
        )

        return None

    setup = find_latest_setup(
        symbol,
        candles
    )

    if setup is None:

        print(
            f"[NO CROSS] {symbol}",
            flush=True
        )

        return None

    print(
        f"[CROSS] {symbol} "
        f"{setup['side']} "
        f"BoxHigh="
        f"{format_price(setup['box_high'])} "
        f"BoxLow="
        f"{format_price(setup['box_low'])}",
        flush=True
    )

    signal = check_breakout(
        setup,
        candles
    )

    if signal is None:

        print(
            f"[NO BREAKOUT] {symbol}",
            flush=True
        )

        return None

    signal["symbol"] = symbol

    signal_id = (
        f"{symbol}_"
        f"{signal['side']}_"
        f"{signal['breakout_time']}"
    )

    previous = state.get(
        "last_signals",
        {}
    )

    if previous.get(
        symbol
    ) == signal_id:

        print(
            f"[DUPLICATE] "
            f"{symbol}",
            flush=True
        )

        return None

    state[
        "last_signals"
    ][symbol] = signal_id

    return {
        "signal": signal,
        "volume_ratio": ratio
    }


# ============================================================
# MAIN
# ============================================================

def run():

    print(
        "=" * 70,
        flush=True
    )

    print(
        "CRYPTO TENKAN/KIJUN "
        "26 BOX BREAKOUT SCANNER",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    print(
        f"Timeframe: {TIMEFRAME}m",
        flush=True
    )

    print(
        f"TOP: {TOP_N}",
        flush=True
    )

    print(
        f"MAX OPEN: "
        f"{MAX_OPEN_TRADES}",
        flush=True
    )

    print(
        f"BOX: {BOX_LENGTH}",
        flush=True
    )

    print(
        "TP: 50% BOX WIDTH",
        flush=True
    )

    print(
        "SWING: 2 LEFT / 2 RIGHT",
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    state = load_state()

    history = load_history()

    # ========================================================
    # UPDATE EXISTING TRADE
    # ========================================================

    if state.get(
        "open_trade"
    ):

        print(
            "[INFO] Updating "
            "existing trade...",
            flush=True
        )

        try:

            update_open_trade(
                state,
                history
            )

        except Exception as exc:

            print(
                "[OPEN TRADE ERROR]",
                repr(exc),
                flush=True
            )

            traceback.print_exc()

        save_state(
            state
        )

        save_history(
            history
        )

    # ========================================================
    # ONE TRADE ONLY
    # ========================================================

    if state.get(
        "open_trade"
    ):

        trade = state[
            "open_trade"
        ]

        print(
            f"[INFO] OPEN TRADE: "
            f"{trade['symbol']} "
            f"{trade['side']}",
            flush=True
        )

        print(
            f"[INFO] Current: "
            f"{format_price(trade.get('current_price'))}",
            flush=True
        )

        print(
            f"[INFO] Live P&L: "
            f"{trade.get('live_pnl_pct', 0):+.2f}%",
            flush=True
        )

        send_telegram(
            build_report(
                history,
                state,
                TOP_N,
                0
            )
        )

        return

    # ========================================================
    # TOP ABNORMAL VOLUME
    # ========================================================

    ranked = get_top_volume_markets()

    if not ranked:

        print(
            "[ERROR] No abnormal-volume "
            "markets found",
            flush=True
        )

        send_telegram(
            "⚠️ "
            "<b>PRICE ACTION SCANNER</b>\n"
            "No abnormal-volume markets found."
        )

        return

    print(
        f"[INFO] TOP {len(ranked)} "
        "ABNORMAL VOLUME:",
        flush=True
    )

    for i, item in enumerate(
        ranked,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{item['symbol']} "
            f"Ratio="
            f"{item['volume_ratio']:.2f}x",
            flush=True
        )

    # ========================================================
    # SIGNAL SCAN
    # ========================================================

    events = 0

    for item in ranked:

        if state.get(
            "open_trade"
        ):

            break

        symbol = item[
            "symbol"
        ]

        print(
            "-" * 60,
            flush=True
        )

        print(
            f"[SCAN] {symbol} "
            f"VOL="
            f"{item['volume_ratio']:.2f}x",
            flush=True
        )

        try:

            result = scan_market(
                item,
                state
            )

            if result is None:
                continue

            signal = result[
                "signal"
            ]

            ratio = result[
                "volume_ratio"
            ]

            print(
                "[SIGNAL] "
                f"{symbol} "
                f"{signal['side']} "
                f"Entry="
                f"{format_price(signal['entry'])} "
                f"SL="
                f"{format_price(signal['sl'])} "
                f"TP="
                f"{format_price(signal['tp'])} "
                f"VOL="
                f"{ratio:.2f}x",
                flush=True
            )

            opened = open_trade(
                state,
                signal,
                ratio
            )

            if opened:

                events += 1

                save_state(
                    state
                )

                break

        except Exception as exc:

            print(
                f"\n[ERROR] "
                f"{symbol}: "
                f"{repr(exc)}",
                flush=True
            )

            traceback.print_exc()

            # Continue scanning other markets
            continue

    # ========================================================
    # CLEAN SIGNAL MEMORY
    # ========================================================

    if len(
        state["last_signals"]
    ) > 500:

        keys = list(
            state["last_signals"].keys()
        )

        for key in keys[:-250]:

            del state[
                "last_signals"
            ][key]

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
        history,
        state,
        len(ranked),
        events
    )

    print(
        "\n" + "=" * 70,
        flush=True
    )

    print(
        report,
        flush=True
    )

    print(
        "=" * 70,
        flush=True
    )

    send_telegram(
        report
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run()

    except KeyboardInterrupt:

        print(
            "\n[INFO] Scanner stopped.",
            flush=True
        )

    except Exception as exc:

        print(
            "\n" + "=" * 70,
            flush=True
        )

        print(
            "[FATAL ERROR]",
            repr(exc),
            flush=True
        )

        print(
            "=" * 70,
            flush=True
        )

        traceback.print_exc()

        try:

            send_telegram(
                "🚨 "
                "<b>SCANNER FATAL ERROR</b>\n"
                f"<code>{str(exc)[:3000]}</code>"
            )

        except Exception as telegram_error:

            print(
                "[TELEGRAM ERROR]",
                repr(telegram_error),
                flush=True
            )

            traceback.print_exc()

        # مهم:
        # خطا را دوباره پرتاب می‌کنیم تا
        # GitHub Actions واقعاً آن را Failed
        # تشخیص دهد.
        raise
