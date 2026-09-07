# ============================================================
# BINANCE USD-M FUTURES PRICE ACTION SCANNER
# ============================================================
# DATA PROVIDER:
#   CoinAPI -> Binance USD-M Futures (BINANCEFTS)
#
# STRATEGY:
#   5m CLOSED candles
#   TOP 30 Binance USD-M perpetuals by 24h USD volume
#
# TENKAN / KIJUN:
#   Tenkan = 9
#   Kijun  = 26
#
# CROSS:
#   ONLY the most recent Tenkan/Kijun crossover is used.
#   Older crosses are ignored.
#
# BOX:
#   26 candles immediately BEFORE latest crossover.
#   Crossover candle itself is NOT included.
#
# ENTRY:
#   BUY  -> closed candle closes above Box High
#   SELL -> closed candle closes below Box Low
#
# SWING:
#   Confirmed pivot = 2 candles left + 2 candles right
#
# STOP:
#   BUY  -> slightly below latest valid Swing Low
#   SELL -> slightly above latest valid Swing High
#
# TAKE PROFIT:
#   50% of Box width
#
# POSITION:
#   Maximum 1 open virtual trade
#
# NOTE:
#   Signal simulator only.
#   No real Binance orders are placed.
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone

import requests
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

COINAPI_BASE_URL = "https://rest.coinapi.io"

COINAPI_KEY = os.getenv("COINAPI_KEY", "").strip()

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN", ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID", ""
).strip()

EXCHANGE_ID = "BINANCEFTS"

TIMEFRAME = "5MIN"

TOP_SYMBOLS = 30

CANDLE_LIMIT = 60

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

BOX_SIZE = 26

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

TP_BOX_PERCENT = 0.50

SL_BUFFER_PERCENT = 0.0015

MAX_OPEN_TRADES = 1

MAX_BREAKOUT_AGE_CANDLES = 2

STATE_FILE = "binance_price_action_state.json"

HISTORY_FILE = "binance_price_action_trade_history.json"

REQUEST_TIMEOUT = 25

MAX_WORKERS = 5

RETRY_COUNT = 3


# ============================================================
# HTTP SESSIONS
# ============================================================

COINAPI_SESSION = requests.Session()

COINAPI_SESSION.headers.update({
    "X-CoinAPI-Key": COINAPI_KEY,
    "Accept": "application/json",
    "User-Agent": "Binance-Price-Action-Scanner/1.0"
})


TELEGRAM_SESSION = requests.Session()

TELEGRAM_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent": "Binance-Price-Action-Scanner/1.0"
})


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        result = float(value)

        if math.isnan(result):
            return default

        if math.isinf(result):
            return default

        return result

    except Exception:
        return default


def fmt_price(value):

    value = safe_float(value)

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:.3f}"

    if abs(value) >= 10:
        return f"{value:.4f}"

    if abs(value) >= 1:
        return f"{value:.5f}"

    if abs(value) >= 0.1:
        return f"{value:.6f}"

    if abs(value) >= 0.01:
        return f"{value:.7f}"

    return f"{value:.8f}"


def fmt_percent(value):
    return f"{safe_float(value):+.2f}%"


def parse_time(value):

    if not value:
        return None

    try:

        return datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )

    except Exception:
        return None


# ============================================================
# JSON STATE
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

            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"[WARN] Could not load {path}: {e}"
        )

        return default


def save_json(path, data):

    temp_path = path + ".tmp"

    with open(
        temp_path,
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
        temp_path,
        path
    )


def load_state():

    default = {
        "open_trade": None,
        "last_scan": None,
        "last_signal_id": None,
        "stats": {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0
        }
    }

    state = load_json(
        STATE_FILE,
        default
    )

    if not isinstance(
        state,
        dict
    ):
        state = default

    if "stats" not in state:
        state["stats"] = default["stats"]

    for key in (
        "trades",
        "wins",
        "losses",
        "breakeven"
    ):

        if key not in state["stats"]:
            state["stats"][key] = 0

    return state


def save_state(state):
    save_json(
        STATE_FILE,
        state
    )


def load_history():

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(
        history,
        list
    ):
        history = []

    return history


def save_history(history):
    save_json(
        HISTORY_FILE,
        history
    )


# ============================================================
# COINAPI REQUEST
# ============================================================

def coinapi_get(
    path,
    params=None
):

    url = (
        COINAPI_BASE_URL
        + path
    )

    last_error = None

    for attempt in range(
        1,
        RETRY_COUNT + 1
    ):

        try:

            response = COINAPI_SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code == 200:

                return response.json()

            try:

                error_body = response.json()

            except Exception:

                error_body = (
                    response.text[:1000]
                )

            last_error = (
                f"HTTP {response.status_code}: "
                f"{error_body}"
            )

            if response.status_code in (
                429,
                500,
                502,
                503,
                504
            ):

                sleep_time = attempt * 2

                print(
                    f"[WARN] CoinAPI temporary error "
                    f"{response.status_code}. "
                    f"Retry {attempt}/{RETRY_COUNT} "
                    f"in {sleep_time}s..."
                )

                time.sleep(
                    sleep_time
                )

                continue

            break

        except requests.RequestException as e:

            last_error = str(e)

            print(
                f"[WARN] CoinAPI request error "
                f"attempt {attempt}/{RETRY_COUNT}: "
                f"{e}"
            )

            time.sleep(attempt)

    raise RuntimeError(
        f"CoinAPI request failed: "
        f"{path} | {last_error}"
    )


# ============================================================
# SYMBOL DISCOVERY
# ============================================================

def get_binance_futures_symbols():

    print(
        "[INFO] Loading Binance USD-M "
        "perpetual symbols..."
    )

    data = coinapi_get(
        f"/v1/symbols/{EXCHANGE_ID}/active"
    )

    if not isinstance(
        data,
        list
    ):
        raise RuntimeError(
            "Invalid symbols response."
        )

    symbols = []

    for item in data:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol_id = item.get(
            "symbol_id",
            ""
        )

        symbol_type = item.get(
            "symbol_type",
            ""
        )

        base = item.get(
            "asset_id_base",
            ""
        )

        quote = item.get(
            "asset_id_quote",
            ""
        )

        if symbol_type != "PERPETUAL":
            continue

        if quote != "USDT":
            continue

        if not symbol_id.startswith(
            f"{EXCHANGE_ID}_PERP_"
        ):
            continue

        volume_24h = safe_float(
            item.get(
                "volume_1day_usd"
            )
        )

        price = safe_float(
            item.get("price")
        )

        symbols.append({
            "symbol_id": symbol_id,
            "base": base,
            "quote": quote,
            "volume_24h_usd": volume_24h,
            "metadata_price": price
        })

    symbols.sort(
        key=lambda x:
            x["volume_24h_usd"],
        reverse=True
    )

    top = symbols[:TOP_SYMBOLS]

    print(
        f"[INFO] Active USDT perpetuals: "
        f"{len(symbols)}"
    )

    print(
        f"[INFO] TOP {TOP_SYMBOLS}: "
        f"{len(top)}"
    )

    for i, item in enumerate(
        top,
        1
    ):

        print(
            f"  {i:02d}. "
            f"{item['base']}/USDT "
            f"vol="
            f"${item['volume_24h_usd']:,.0f}"
        )

    return top


# ============================================================
# CURRENT QUOTES
# ============================================================

def get_current_quotes(
    symbol_ids
):

    if not symbol_ids:
        return {}

    print(
        "[INFO] Loading current Binance "
        "Futures prices..."
    )

    joined = ";".join(
        symbol_ids
    )

    try:

        data = coinapi_get(
            "/v1/quotes/current",
            params={
                "filter_symbol_id":
                    joined
            }
        )

    except Exception as e:

        print(
            "[WARN] Bulk quote request "
            f"failed: {e}"
        )

        data = []

    quotes = {}

    if isinstance(
        data,
        dict
    ):
        data = [data]

    if not isinstance(
        data,
        list
    ):
        data = []

    for item in data:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol_id = item.get(
            "symbol_id"
        )

        if not symbol_id:
            continue

        last_trade = item.get(
            "last_trade"
        )

        price = 0.0

        trade_time = None

        if isinstance(
            last_trade,
            dict
        ):

            price = safe_float(
                last_trade.get(
                    "price"
                )
            )

            trade_time = (
                last_trade.get(
                    "time_exchange"
                )
            )

        if price <= 0:

            bid = safe_float(
                item.get(
                    "bid_price"
                )
            )

            ask = safe_float(
                item.get(
                    "ask_price"
                )
            )

            if bid > 0 and ask > 0:

                price = (
                    bid + ask
                ) / 2.0

        if price <= 0:
            continue

        previous = quotes.get(
            symbol_id
        )

        if previous is None:

            quotes[symbol_id] = {
                "price": price,
                "time": trade_time
            }

        else:

            old_time = parse_time(
                previous.get(
                    "time"
                )
            )

            new_time = parse_time(
                trade_time
            )

            if (
                new_time
                and old_time
                and new_time >= old_time
            ):

                quotes[symbol_id] = {
                    "price": price,
                    "time": trade_time
                }

    print(
        f"[INFO] Current prices loaded: "
        f"{len(quotes)}"
    )

    return quotes


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(
    symbol_id
):

    data = coinapi_get(
        f"/v1/ohlcv/{symbol_id}/latest",
        params={
            "period_id": TIMEFRAME,
            "limit": CANDLE_LIMIT
        }
    )

    if not isinstance(
        data,
        list
    ):
        return []

    candles = []

    for row in data:

        if not isinstance(
            row,
            dict
        ):
            continue

        timestamp = row.get(
            "time_period_start"
        )

        if not timestamp:
            continue

        candle = {
            "time": timestamp,
            "open": safe_float(
                row.get(
                    "price_open"
                )
            ),
            "high": safe_float(
                row.get(
                    "price_high"
                )
            ),
            "low": safe_float(
                row.get(
                    "price_low"
                )
            ),
            "close": safe_float(
                row.get(
                    "price_close"
                )
            ),
            "volume": safe_float(
                row.get(
                    "volume_traded"
                )
            )
        }

        if (
            candle["open"] <= 0
            or candle["high"] <= 0
            or candle["low"] <= 0
            or candle["close"] <= 0
        ):
            continue

        candles.append(
            candle
        )

    candles.sort(
        key=lambda x:
            x["time"]
    )

    return candles


def fetch_symbol_data(
    symbol
):

    symbol_id = symbol[
        "symbol_id"
    ]

    try:

        candles = fetch_ohlcv(
            symbol_id
        )

        return (
            symbol_id,
            candles,
            None
        )

    except Exception as e:

        return (
            symbol_id,
            [],
            str(e)
        )


def fetch_top_candles(
    symbols
):

    result = {}

    print(
        f"[INFO] Fetching {TIMEFRAME} "
        f"candles for "
        f"{len(symbols)} symbols..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {
            executor.submit(
                fetch_symbol_data,
                symbol
            ): symbol
            for symbol in symbols
        }

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                (
                    symbol_id,
                    candles,
                    error
                ) = future.result()

                if error:

                    print(
                        f"[WARN] "
                        f"{symbol_id}: "
                        f"{error}"
                    )

                elif candles:

                    result[
                        symbol_id
                    ] = candles

            except Exception as e:

                print(
                    f"[WARN] Worker failure "
                    f"{symbol['symbol_id']}: "
                    f"{e}"
                )

    print(
        f"[INFO] Candle datasets loaded: "
        f"{len(result)}"
    )

    return result


# ============================================================
# CLOSED CANDLES
# ============================================================

def filter_closed_candles(
    candles
):

    if not candles:
        return []

    now_ts = (
        now_utc().timestamp()
    )

    closed = []

    for candle in candles:

        start = parse_time(
            candle["time"]
        )

        if not start:
            continue

        end_ts = (
            start.timestamp()
            + 5 * 60
        )

        if now_ts >= end_ts:

            closed.append(
                candle
            )

    return closed


# ============================================================
# TENKAN / KIJUN
# ============================================================

def midpoint(
    highs,
    lows
):

    if not highs or not lows:
        return None

    return (
        max(highs)
        + min(lows)
    ) / 2.0


def calculate_tenkan(
    candles,
    index
):

    if index < TENKAN_PERIOD - 1:
        return None

    window = candles[
        index - TENKAN_PERIOD + 1:
        index + 1
    ]

    highs = [
        x["high"]
        for x in window
    ]

    lows = [
        x["low"]
        for x in window
    ]

    return midpoint(
        highs,
        lows
    )


def calculate_kijun(
    candles,
    index
):

    if index < KIJUN_PERIOD - 1:
        return None

    window = candles[
        index - KIJUN_PERIOD + 1:
        index + 1
    ]

    highs = [
        x["high"]
        for x in window
    ]

    lows = [
        x["low"]
        for x in window
    ]

    return midpoint(
        highs,
        lows
    )


# ============================================================
# LATEST TENKAN / KIJUN CROSS
# ============================================================

def find_latest_cross(
    candles
):

    if len(candles) < (
        KIJUN_PERIOD + 2
    ):
        return None

    latest_cross = None

    previous_tenkan = None
    previous_kijun = None

    for i in range(
        len(candles)
    ):

        tenkan = calculate_tenkan(
            candles,
            i
        )

        kijun = calculate_kijun(
            candles,
            i
        )

        if (
            tenkan is None
            or kijun is None
        ):
            continue

        if (
            previous_tenkan is not None
            and previous_kijun is not None
        ):

            bullish = (
                previous_tenkan
                <= previous_kijun
                and tenkan > kijun
            )

            bearish = (
                previous_tenkan
                >= previous_kijun
                and tenkan < kijun
            )

            if bullish:

                latest_cross = {
                    "index": i,
                    "direction": "BUY",
                    "time":
                        candles[i]["time"],
                    "tenkan": tenkan,
                    "kijun": kijun
                }

            elif bearish:

                latest_cross = {
                    "index": i,
                    "direction": "SELL",
                    "time":
                        candles[i]["time"],
                    "tenkan": tenkan,
                    "kijun": kijun
                }

        previous_tenkan = tenkan
        previous_kijun = kijun

    return latest_cross


# ============================================================
# BOX
# ============================================================

def build_box(
    candles,
    cross
):

    if not cross:
        return None

    cross_index = cross[
        "index"
    ]

    start = (
        cross_index
        - BOX_SIZE
    )

    end = cross_index

    if start < 0:
        return None

    box_candles = candles[
        start:end
    ]

    if len(box_candles) != BOX_SIZE:
        return None

    high = max(
        x["high"]
        for x in box_candles
    )

    low = min(
        x["low"]
        for x in box_candles
    )

    width = high - low

    if width <= 0:
        return None

    return {
        "high": high,
        "low": low,
        "width": width,
        "start_time":
            box_candles[0]["time"],
        "end_time":
            box_candles[-1]["time"],
        "count":
            len(box_candles)
    }


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(
    candles,
    cross,
    box
):

    if not cross or not box:
        return None

    cross_index = cross[
        "index"
    ]

    first_index = (
        cross_index + 1
    )

    last_index = (
        len(candles) - 1
    )

    if first_index > last_index:
        return None

    candidates = []

    for i in range(
        first_index,
        last_index + 1
    ):

        candle = candles[i]

        close = candle[
            "close"
        ]

        if (
            cross["direction"] == "BUY"
            and close > box["high"]
        ):

            candidates.append({
                "index": i,
                "direction": "BUY",
                "price": close,
                "time":
                    candle["time"]
            })

        elif (
            cross["direction"] == "SELL"
            and close < box["low"]
        ):

            candidates.append({
                "index": i,
                "direction": "SELL",
                "price": close,
                "time":
                    candle["time"]
            })

    if not candidates:
        return None

    breakout = candidates[-1]

    age = (
        len(candles)
        - 1
        - breakout["index"]
    )

    breakout[
        "age_candles"
    ] = age

    if age > MAX_BREAKOUT_AGE_CANDLES:
        return None

    return breakout


# ============================================================
# CONFIRMED SWINGS
# ============================================================

def is_swing_low(
    candles,
    index
):

    if (
        index < PIVOT_LEFT
        or index + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    value = candles[
        index
    ]["low"]

    left = candles[
        index - PIVOT_LEFT:
        index
    ]

    right = candles[
        index + 1:
        index + 1 + PIVOT_RIGHT
    ]

    for candle in left:

        if candle["low"] <= value:
            return False

    for candle in right:

        if candle["low"] <= value:
            return False

    return True


def is_swing_high(
    candles,
    index
):

    if (
        index < PIVOT_LEFT
        or index + PIVOT_RIGHT
        >= len(candles)
    ):
        return False

    value = candles[
        index
    ]["high"]

    left = candles[
        index - PIVOT_LEFT:
        index
    ]

    right = candles[
        index + 1:
        index + 1 + PIVOT_RIGHT
    ]

    for candle in left:

        if candle["high"] >= value:
            return False

    for candle in right:

        if candle["high"] >= value:
            return False

    return True


def find_latest_swing(
    candles,
    direction,
    before_index=None
):

    if before_index is None:

        before_index = (
            len(candles) - 1
        )

    last_index = min(
        before_index,
        len(candles)
        - PIVOT_RIGHT
        - 1
    )

    if direction == "BUY":

        for i in range(
            last_index,
            PIVOT_LEFT - 1,
            -1
        ):

            if is_swing_low(
                candles,
                i
            ):

                return {
                    "type": "LOW",
                    "index": i,
                    "price":
                        candles[i]["low"],
                    "time":
                        candles[i]["time"]
                }

    else:

        for i in range(
            last_index,
            PIVOT_LEFT - 1,
            -1
        ):

            if is_swing_high(
                candles,
                i
            ):

                return {
                    "type": "HIGH",
                    "index": i,
                    "price":
                        candles[i]["high"],
                    "time":
                        candles[i]["time"]
                }

    return None


# ============================================================
# TRADE SETUP
# ============================================================

def build_trade_setup(
    symbol,
    candles,
    cross,
    box,
    breakout
):

    if not breakout:
        return None

    direction = (
        breakout["direction"]
    )

    entry = safe_float(
        breakout["price"]
    )

    if entry <= 0:
        return None

    swing = find_latest_swing(
        candles,
        direction,
        breakout["index"]
    )

    if not swing:
        return None

    box_width = safe_float(
        box["width"]
    )

    if box_width <= 0:
        return None

    if direction == "BUY":

        sl = (
            swing["price"]
            * (
                1.0
                - SL_BUFFER_PERCENT
            )
        )

        tp = (
            entry
            + box_width
            * TP_BOX_PERCENT
        )

        if not (
            sl < entry < tp
        ):
            return None

    else:

        sl = (
            swing["price"]
            * (
                1.0
                + SL_BUFFER_PERCENT
            )
        )

        tp = (
            entry
            - box_width
            * TP_BOX_PERCENT
        )

        if not (
            tp < entry < sl
        ):
            return None

    signal_id = (
        f"{symbol['symbol_id']}_"
        f"{direction}_"
        f"{breakout['time']}"
    )

    return {

        "signal_id":
            signal_id,

        "symbol_id":
            symbol["symbol_id"],

        "symbol":
            f"{symbol['base']}/"
            f"{symbol['quote']}",

        "direction":
            direction,

        "entry":
            entry,

        "sl":
            sl,

        "tp":
            tp,

        "box_high":
            box["high"],

        "box_low":
            box["low"],

        "box_width":
            box_width,

        "cross_time":
            cross["time"],

        "cross_direction":
            cross["direction"],

        "breakout_time":
            breakout["time"],

        "breakout_age_candles":
            breakout["age_candles"],

        "swing_type":
            swing["type"],

        "swing_price":
            swing["price"],

        "swing_time":
            swing["time"],

        "volume_24h_usd":
            symbol["volume_24h_usd"],

        "created_at":
            iso_now(),

        "status":
            "OPEN"
    }


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles,
    index
):

    if index < 1:
        return 0.0

    start = max(
        0,
        index - 20
    )

    previous = candles[
        start:index
    ]

    volumes = [
        x["volume"]
        for x in previous
        if x["volume"] > 0
    ]

    if not volumes:
        return 0.0

    average = (
        sum(volumes)
        / len(volumes)
    )

    if average <= 0:
        return 0.0

    current = candles[
        index
    ]["volume"]

    return (
        current
        / average
    )


# ============================================================
# OPEN TRADE MANAGEMENT
# ============================================================

def update_open_trade(
    state,
    candles_by_symbol,
    current_prices
):

    trade = state.get(
        "open_trade"
    )

    if not trade:
        return None

    symbol_id = trade[
        "symbol_id"
    ]

    raw_candles = (
        candles_by_symbol.get(
            symbol_id,
            []
        )
    )

    if not raw_candles:
        return None

    # IMPORTANT:
    # Only CLOSED 5m candles are used
    # for SL/TP decisions.
    candles = filter_closed_candles(
        raw_candles
    )

    if not candles:
        return None

    direction = trade[
        "direction"
    ]

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    entry = safe_float(
        trade["entry"]
    )

    current_price = safe_float(
        current_prices.get(
            symbol_id,
            0
        )
    )

    if current_price <= 0:

        current_price = (
            candles[-1]["close"]
        )

    exit_reason = None
    exit_price = None

    last_checked = trade.get(
        "last_checked_candle"
    )

    for candle in candles:

        candle_time = candle[
            "time"
        ]

        if (
            last_checked
            and candle_time <= last_checked
        ):
            continue

        high = candle[
            "high"
        ]

        low = candle[
            "low"
        ]

        if direction == "BUY":

            # Conservative:
            # If SL and TP are both touched
            # on the same candle, SL is assumed first.
            if low <= sl:

                exit_reason = "SL"
                exit_price = sl

            elif high >= tp:

                exit_reason = "TP"
                exit_price = tp

        else:

            if high >= sl:

                exit_reason = "SL"
                exit_price = sl

            elif low <= tp:

                exit_reason = "TP"
                exit_price = tp

        trade[
            "last_checked_candle"
        ] = candle_time

        if exit_reason:
            break

    # --------------------------------------------------------
    # CLOSE TRADE
    # --------------------------------------------------------

    if exit_reason:

        if direction == "BUY":

            pnl_percent = (
                (
                    exit_price
                    - entry
                )
                / entry
            ) * 100.0

        else:

            pnl_percent = (
                (
                    entry
                    - exit_price
                )
                / entry
            ) * 100.0

        if pnl_percent > 0:

            result = "WIN"

        elif pnl_percent < 0:

            result = "LOSS"

        else:

            result = "BREAKEVEN"

        stats = state[
            "stats"
        ]

        stats["trades"] += 1

        if result == "WIN":

            stats["wins"] += 1

        elif result == "LOSS":

            stats["losses"] += 1

        else:

            stats["breakeven"] += 1

        history = load_history()

        closed_trade = dict(
            trade
        )

        closed_trade.update({

            "status":
                "CLOSED",

            "exit_reason":
                exit_reason,

            "exit_price":
                exit_price,

            "pnl_percent":
                pnl_percent,

            "closed_at":
                iso_now(),

            "result":
                result
        })

        history.append(
            closed_trade
        )

        save_history(
            history
        )

        state[
            "open_trade"
        ] = None

        print(
            f"[TRADE CLOSED] "
            f"{trade['symbol']} "
            f"{direction} "
            f"{exit_reason} "
            f"PnL="
            f"{pnl_percent:+.2f}%"
        )

        return {
            "closed_trade":
                closed_trade
        }

    # --------------------------------------------------------
    # LIVE P&L
    # --------------------------------------------------------

    if direction == "BUY":

        live_pnl = (
            (
                current_price
                - entry
            )
            / entry
        ) * 100.0

    else:

        live_pnl = (
            (
                entry
                - current_price
            )
            / entry
        ) * 100.0

    trade[
        "current_price"
    ] = current_price

    trade[
        "live_pnl_percent"
    ] = live_pnl

    if candles:

        trade[
            "last_checked_candle"
        ] = candles[-1]["time"]

    return {
        "open_trade":
            trade
    }


# ============================================================
# PERFORMANCE
# ============================================================

def performance_text(
    state
):

    stats = state[
        "stats"
    ]

    trades = stats[
        "trades"
    ]

    wins = stats[
        "wins"
    ]

    losses = stats[
        "losses"
    ]

    breakeven = stats[
        "breakeven"
    ]

    if trades > 0:

        win_rate = (
            wins
            / trades
        ) * 100.0

    else:

        win_rate = 0.0

    return (
        f"Trades {trades} | "
        f"🟢 {wins} | "
        f"🔴 {losses} | "
        f"⚪ {breakeven}\n"
        f"🏆 WR: "
        f"{win_rate:.1f}%"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] TELEGRAM_BOT_TOKEN "
            "not configured."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] TELEGRAM_CHAT_ID "
            "not configured."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        "sendMessage"
    )

    try:

        # ----------------------------------------------------
        # Force message to Unicode string
        # ----------------------------------------------------

        message = str(
            message
        )

        payload = {
            "chat_id":
                str(TELEGRAM_CHAT_ID),

            "text":
                message,

            "parse_mode":
                "Markdown"
        }

        # ----------------------------------------------------
        # Explicit UTF-8 JSON encoding
        # ----------------------------------------------------

        body = json.dumps(
            payload,
            ensure_ascii=False
        ).encode("utf-8")

        headers = {
            "Content-Type":
                "application/json; charset=utf-8",

            "Accept":
                "application/json",

            "User-Agent":
                "Binance-Price-Action-Scanner/1.0"
        }

        response = (
            TELEGRAM_SESSION.post(
                url,
                data=body,
                headers=headers,
                timeout=20
            )
        )

        if response.status_code != 200:

            print(
                "[WARN] Telegram HTTP error:",
                response.status_code,
                response.text[:1000]
            )

            return False

        try:

            result = response.json()

        except Exception:

            result = {}

        if not result.get(
            "ok",
            False
        ):

            print(
                "[WARN] Telegram API error:",
                response.text[:1000]
            )

            return False

        print(
            "[INFO] Telegram message "
            "sent successfully."
        )

        return True

    except UnicodeEncodeError as e:

        print(
            "[ERROR] Unicode encoding error "
            "while sending Telegram message: "
            f"{e}"
        )

        return False

    except requests.RequestException as e:

        print(
            "[WARN] Telegram request exception: "
            f"{e}"
        )

        return False

    except Exception as e:

        print(
            "[WARN] Telegram exception: "
            f"{e}"
        )

        return False


# ============================================================
# TELEGRAM ERROR
# ============================================================

def send_telegram_error(
    error_text
):

    try:

        text = (
            "🚨 *SCANNER ERROR*\n\n"
            f"`{str(error_text)[:3000]}`"
        )

        send_telegram(
            text
        )

    except Exception as e:

        print(
            "[WARN] Could not send "
            f"Telegram error: {e}"
        )


# ============================================================
# REPORT
# ============================================================

def build_report(
    state,
    signals,
    symbols
):

    lines = []

    lines.append(
        "📡 *BINANCE FUTURES "
        "PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        "⏱ *5m CLOSED | TOP 30*"
    )

    lines.append(
        "🤖 *TENKAN/KIJUN + BOX + SWING*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        performance_text(
            state
        )
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # OPEN TRADE
    # ========================================================

    open_trade = state.get(
        "open_trade"
    )

    if open_trade:

        emoji = (
            "🟢"
            if open_trade[
                "direction"
            ] == "BUY"
            else "🔴"
        )

        lines.append(
            f"{emoji} *OPEN TRADE*"
        )

        lines.append(
            f"Symbol: "
            f"`{open_trade['symbol']}`"
        )

        lines.append(
            f"Side: "
            f"`{open_trade['direction']}`"
        )

        lines.append(
            f"Entry: "
            f"`{fmt_price(open_trade['entry'])}`"
        )

        lines.append(
            f"SL: "
            f"`{fmt_price(open_trade['sl'])}`"
        )

        lines.append(
            f"TP: "
            f"`{fmt_price(open_trade['tp'])}`"
        )

        lines.append(
            f"Current: "
            f"`{fmt_price(open_trade.get('current_price', 0))}`"
        )

        lines.append(
            "Live P&L: "
            f"*{fmt_percent(open_trade.get('live_pnl_percent', 0))}*"
        )

        lines.append(
            f"Box High: "
            f"`{fmt_price(open_trade['box_high'])}`"
        )

        lines.append(
            f"Box Low: "
            f"`{fmt_price(open_trade['box_low'])}`"
        )

        lines.append(
            f"Box Width: "
            f"`{fmt_price(open_trade['box_width'])}`"
        )

        lines.append(
            f"Cross: "
            f"`{open_trade['cross_direction']}`"
        )

        lines.append(
            f"Breakout: "
            f"`{open_trade['breakout_time']}`"
        )

        lines.append(
            f"Swing: "
            f"`{open_trade['swing_type']}` "
            f"`{fmt_price(open_trade['swing_price'])}`"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    if signals:

        lines.append(
            f"🚨 *NEW SIGNALS: "
            f"{len(signals)}*"
        )

        for signal in signals:

            emoji = (
                "🟢"
                if signal[
                    "direction"
                ] == "BUY"
                else "🔴"
            )

            lines.append(
                f"{emoji} "
                f"*{signal['symbol']}*"
            )

            lines.append(
                f"Side: "
                f"`{signal['direction']}`"
            )

            lines.append(
                f"Entry: "
                f"`{fmt_price(signal['entry'])}`"
            )

            lines.append(
                f"SL: "
                f"`{fmt_price(signal['sl'])}`"
            )

            lines.append(
                f"TP: "
                f"`{fmt_price(signal['tp'])}`"
            )

            lines.append(
                f"Box High: "
                f"`{fmt_price(signal['box_high'])}`"
            )

            lines.append(
                f"Box Low: "
                f"`{fmt_price(signal['box_low'])}`"
            )

            lines.append(
                f"Box Width: "
                f"`{fmt_price(signal['box_width'])}`"
            )

            lines.append(
                f"Cross: "
                f"`{signal['cross_direction']}`"
            )

            lines.append(
                f"Breakout: "
                f"`{signal['breakout_time']}`"
            )

            lines.append(
                f"Swing: "
                f"`{signal['swing_type']}` "
                f"`{fmt_price(signal['swing_price'])}`"
            )

            lines.append(
                f"24h Vol: "
                f"${signal['volume_24h_usd']:,.0f}"
            )

            lines.append(
                f"Volume Ratio: "
                f"{safe_float(signal.get('volume_ratio')):.2f}x"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    elif not open_trade:

        lines.append(
            "⚪ *NO ACTIVE SIGNAL*"
        )

        lines.append(
            "Latest Tenkan/Kijun cross "
            "did not produce a valid breakout."
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    lines.append(
        "📡 Data: CoinAPI → "
        "Binance USD-M Futures"
    )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN SCANNER
# ============================================================

def main():

    print("=" * 70)

    print(
        "BINANCE USD-M FUTURES "
        "PRICE ACTION SCANNER"
    )

    print("=" * 70)

    print(
        f"Provider: CoinAPI / "
        f"{EXCHANGE_ID}"
    )

    print(
        f"Timeframe: {TIMEFRAME}"
    )

    print(
        f"Top: {TOP_SYMBOLS}"
    )

    print(
        f"Tenkan: "
        f"{TENKAN_PERIOD}"
    )

    print(
        f"Kijun: "
        f"{KIJUN_PERIOD}"
    )

    print(
        f"Box: "
        f"{BOX_SIZE} candles"
    )

    print(
        "Swing: 2 left + 2 right"
    )

    print(
        f"TP: "
        f"{TP_BOX_PERCENT * 100:.0f}% Box"
    )

    print(
        f"Max Open: "
        f"{MAX_OPEN_TRADES}"
    )

    print("=" * 70)

    # ========================================================
    # VALIDATE CONFIG
    # ========================================================

    if not COINAPI_KEY:

        raise RuntimeError(
            "COINAPI_KEY is missing. "
            "Add it to GitHub Secrets."
        )

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] TELEGRAM_BOT_TOKEN "
            "is missing."
        )

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] TELEGRAM_CHAT_ID "
            "is missing."
        )

    # ========================================================
    # LOAD STATE
    # ========================================================

    state = load_state()

    state[
        "last_scan"
    ] = iso_now()

    # ========================================================
    # SYMBOLS
    # ========================================================

    symbols = (
        get_binance_futures_symbols()
    )

    if not symbols:

        raise RuntimeError(
            "No Binance Futures symbols found."
        )

    symbol_ids = [
        x["symbol_id"]
        for x in symbols
    ]

    # ========================================================
    # CURRENT PRICES
    # ========================================================

    current_quotes = (
        get_current_quotes(
            symbol_ids
        )
    )

    current_prices = {
        symbol_id:
            quote["price"]
        for symbol_id, quote
        in current_quotes.items()
    }

    # ========================================================
    # CANDLES
    # ========================================================

    candles_by_symbol = (
        fetch_top_candles(
            symbols
        )
    )

    # ========================================================
    # MANAGE EXISTING TRADE FIRST
    # ========================================================

    close_result = (
        update_open_trade(
            state,
            candles_by_symbol,
            current_prices
        )
    )

    if close_result:

        closed_trade = (
            close_result.get(
                "closed_trade"
            )
        )

        if closed_trade:

            send_telegram(
                "📢 *TRADE CLOSED*\n\n"
                f"Symbol: "
                f"`{closed_trade['symbol']}`\n"
                f"Side: "
                f"`{closed_trade['direction']}`\n"
                f"Exit: "
                f"`{closed_trade['exit_reason']}`\n"
                f"Exit Price: "
                f"`{fmt_price(closed_trade['exit_price'])}`\n"
                f"PnL: "
                f"*{fmt_percent(closed_trade['pnl_percent'])}*\n\n"
                f"{performance_text(state)}"
            )

    # ========================================================
    # MAX ONE OPEN TRADE
    # ========================================================

    if state.get(
        "open_trade"
    ):

        open_symbol = (
            state[
                "open_trade"
            ]["symbol_id"]
        )

        print(
            f"[INFO] One trade already "
            f"open: {open_symbol}"
        )

        save_state(
            state
        )

        report = build_report(
            state,
            [],
            symbols
        )

        send_telegram(
            report
        )

        print(
            "[INFO] Scan completed."
        )

        return

    # ========================================================
    # FIND NEW SIGNALS
    # ========================================================

    candidates = []

    symbol_lookup = {
        x["symbol_id"]: x
        for x in symbols
    }

    for (
        symbol_id,
        candles
    ) in candles_by_symbol.items():

        closed = (
            filter_closed_candles(
                candles
            )
        )

        if len(closed) < (
            KIJUN_PERIOD
            + BOX_SIZE
            + 5
        ):

            continue

        # ----------------------------------------------------
        # Latest Tenkan/Kijun cross
        # ----------------------------------------------------

        cross = (
            find_latest_cross(
                closed
            )
        )

        if not cross:
            continue

        # ----------------------------------------------------
        # Box
        # ----------------------------------------------------

        box = (
            build_box(
                closed,
                cross
            )
        )

        if not box:
            continue

        # ----------------------------------------------------
        # Breakout
        # ----------------------------------------------------

        breakout = (
            find_breakout(
                closed,
                cross,
                box
            )
        )

        if not breakout:
            continue

        # ----------------------------------------------------
        # Setup
        # ----------------------------------------------------

        symbol = (
            symbol_lookup.get(
                symbol_id
            )
        )

        if not symbol:
            continue

        setup = (
            build_trade_setup(
                symbol,
                closed,
                cross,
                box,
                breakout
            )
        )

        if not setup:
            continue

        # ----------------------------------------------------
        # Volume ratio
        # ----------------------------------------------------

        ratio = (
            calculate_volume_ratio(
                closed,
                breakout["index"]
            )
        )

        setup[
            "volume_ratio"
        ] = ratio

        candidates.append(
            setup
        )

    # ========================================================
    # SELECT ONE SIGNAL
    # ========================================================

    candidates.sort(
        key=lambda x: (
            x["breakout_time"],
            x["volume_ratio"]
        ),
        reverse=True
    )

    new_signals = []

    if candidates:

        selected = candidates[0]

        last_signal_id = (
            state.get(
                "last_signal_id"
            )
        )

        if (
            selected["signal_id"]
            != last_signal_id
        ):

            state[
                "open_trade"
            ] = selected

            state[
                "last_signal_id"
            ] = selected[
                "signal_id"
            ]

            new_signals.append(
                selected
            )

            print(
                "[SIGNAL]"
            )

            print(
                f"Symbol: "
                f"{selected['symbol']}"
            )

            print(
                f"Direction: "
                f"{selected['direction']}"
            )

            print(
                f"Entry: "
                f"{fmt_price(selected['entry'])}"
            )

            print(
                f"SL: "
                f"{fmt_price(selected['sl'])}"
            )

            print(
                f"TP: "
                f"{fmt_price(selected['tp'])}"
            )

            print(
                f"Box High: "
                f"{fmt_price(selected['box_high'])}"
            )

            print(
                f"Box Low: "
                f"{fmt_price(selected['box_low'])}"
            )

            print(
                f"Swing: "
                f"{selected['swing_type']} "
                f"{fmt_price(selected['swing_price'])}"
            )

            print(
                f"Volume Ratio: "
                f"{selected['volume_ratio']:.2f}x"
            )

        else:

            print(
                "[INFO] Same signal "
                "already processed."
            )

    else:

        print(
            "[INFO] No valid signal."
        )

    # ========================================================
    # SAVE STATE
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # TELEGRAM REPORT
    # ========================================================

    report = build_report(
        state,
        new_signals,
        symbols
    )

    send_telegram(
        report
    )

    print("=" * 70)

    print(
        "SCAN COMPLETED"
    )

    print("=" * 70)


# ============================================================
# ERROR HANDLING
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[INFO] Scanner stopped."
        )

        raise

    except Exception as e:

        print(
            "\n"
            + "=" * 70
        )

        print(
            "FATAL SCANNER ERROR"
        )

        print(
            "=" * 70
        )

        print(
            str(e)
        )

        traceback.print_exc()

        print(
            "=" * 70
        )

        try:

            send_telegram_error(
                str(e)
            )

        except Exception:

            pass

        raise
