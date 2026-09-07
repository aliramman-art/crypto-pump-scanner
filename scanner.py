# ============================================================
# LBANK FUTURES PRICE ACTION SCANNER
# ============================================================
#
# MARKET / VOLUME / PRICE:
#     LBank Futures
#
# CANDLE SOURCE:
#     LBank Spot 5m
#
# STRATEGY:
#     Tenkan / Kijun Cross
#     26 Candle Box
#     Breakout
#     Swing 2/2
#     Structural SL
#     TP = 50% Box Width
#
# MAX OPEN TRADES = 1
#
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

FUTURES_BASE_URL = "https://lbkperp.lbank.com"
SPOT_BASE_URL = "https://api.lbank.info"

FUTURES_PUBLIC_PREFIX = "/cfd/openApi/v1/pub"

EXCHANGE_NAME = "LBANK FUTURES"

TIMEFRAME_MINUTES = 5
TIMEFRAME_SECONDS = 300

TIMEFRAME_TYPE = "minute5"

TOP_N = 30

# First select this many Futures markets by liquidity.
VOLUME_POOL = 80

MAX_OPEN_TRADES = 1

# ============================================================
# STRATEGY
# ============================================================

TENKAN_LENGTH = 9
KIJUN_LENGTH = 26

BOX_LENGTH = 26

SWING_LEFT = 2
SWING_RIGHT = 2

TP_BOX_MULTIPLIER = 0.50

SL_BUFFER_PCT = 0.0015

VOLUME_LOOKBACK = 20

MIN_CANDLES = 120

KLINE_LIMIT = 180


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 12
MAX_RETRIES = 2
SLEEP_BETWEEN_REQUESTS = 0.08


# ============================================================
# STATE
# ============================================================

STATE_FILE = "lbank_futures_price_action_state.json"

HISTORY_FILE = "lbank_futures_price_action_trade_history.json"


# ============================================================
# TELEGRAM
# ============================================================

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
    "User-Agent": "Mozilla/5.0 LBank-Futures-Scanner/1.0",
    "Accept": "application/json",
})


# ============================================================
# TIME
# ============================================================

def now_ts():

    return int(
        time.time()
    )


def now_iso():

    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:

        if value is None:
            return default

        return float(value)

    except Exception:

        return default


def fmt_price(value):

    value = safe_float(
        value
    )

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:.3f}"

    if abs(value) >= 1:
        return f"{value:.4f}"

    if abs(value) >= 0.1:
        return f"{value:.5f}"

    if abs(value) >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# JSON
# ============================================================

def load_json(
    filename,
    default
):

    try:

        if not os.path.exists(
            filename
        ):
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


def save_json(
    filename,
    data
):

    temp_file = filename + ".tmp"

    try:

        with open(
            temp_file,
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
            temp_file,
            filename
        )

    except Exception as e:

        print(
            f"[ERROR] Cannot save "
            f"{filename}: {e}"
        )


def default_state():

    return {
        "exchange": EXCHANGE_NAME,

        "open_trade": None,

        "last_scan": None,

        "last_signal": None,

        "last_signals": {},
    }


def load_state():

    state = load_json(
        STATE_FILE,
        default_state()
    )

    if not isinstance(
        state,
        dict
    ):
        state = default_state()

    if state.get(
        "exchange"
    ) != EXCHANGE_NAME:

        print(
            "[INFO] Old state belongs "
            "to another exchange."
        )

        state = default_state()

    if "last_signals" not in state:

        state["last_signals"] = {}

    return state


def load_history():

    history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(
        history,
        list
    ):
        return []

    return history


# ============================================================
# GENERIC HTTP
# ============================================================

def http_get(
    base_url,
    path,
    params=None
):

    url = base_url + path

    for attempt in range(
        1,
        MAX_RETRIES + 1
    ):

        try:

            print(
                f"[HTTP] GET {path} "
                f"attempt={attempt}/{MAX_RETRIES}"
            )

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            print(
                f"[HTTP] STATUS "
                f"{response.status_code}"
            )

            response.raise_for_status()

            return response.json()

        except requests.RequestException as e:

            print(
                f"[WARN] HTTP error: {e}"
            )

        except ValueError as e:

            print(
                f"[WARN] JSON error: {e}"
            )

        except Exception as e:

            print(
                f"[WARN] Unexpected HTTP error: {e}"
            )

        if attempt < MAX_RETRIES:

            time.sleep(1)

    return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    message
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] Telegram token missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] Telegram chat ID missing"
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        print(
            "[TELEGRAM] Sent"
        )

        return True

    except Exception as e:

        print(
            f"[ERROR] Telegram: {e}"
        )

        return False


# ============================================================
# LBANK FUTURES
# ============================================================

def get_futures_instruments():

    data = http_get(
        FUTURES_BASE_URL,
        FUTURES_PUBLIC_PREFIX
        + "/instrument",
        {
            "productGroup": "SwapU"
        }
    )

    if not data:

        print(
            "[ERROR] Futures instrument "
            "request failed"
        )

        return []


    raw = data.get(
        "data",
        []
    )

    if isinstance(
        raw,
        dict
    ):

        raw = list(
            raw.values()
        )

    if not isinstance(
        raw,
        list
    ):

        return []


    result = []

    for item in raw:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper().strip()

        if not symbol:
            continue

        # USDT perpetual only
        if not symbol.endswith(
            "USDT"
        ):
            continue

        result.append(
            item
        )


    print(
        f"[INFO] Futures instruments: "
        f"{len(result)}"
    )

    return result


# ============================================================
# FUTURES MARKET DATA
# ============================================================

def get_futures_market_data():

    data = http_get(
        FUTURES_BASE_URL,
        FUTURES_PUBLIC_PREFIX
        + "/marketData",
        {
            "productGroup": "SwapU"
        }
    )

    if not data:

        print(
            "[ERROR] Futures marketData "
            "request failed"
        )

        return {}


    raw = data.get(
        "data",
        []
    )

    if isinstance(
        raw,
        dict
    ):

        raw = list(
            raw.values()
        )

    if not isinstance(
        raw,
        list
    ):

        return {}


    result = {}


    for item in raw:

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = str(
            item.get(
                "symbol",
                ""
            )
        ).upper().strip()

        if not symbol:
            continue

        last_price = safe_float(
            item.get(
                "lastPrice"
            )
        )

        volume = safe_float(
            item.get(
                "volume"
            )
        )

        turnover = safe_float(
            item.get(
                "turnover"
            )
        )

        marked_price = safe_float(
            item.get(
                "markedPrice"
            )
        )

        if last_price <= 0:
            continue

        result[symbol] = {
            "symbol": symbol,

            "last_price": last_price,

            "marked_price": marked_price,

            "volume24": volume,

            "turnover24": turnover,

            "highest24": safe_float(
                item.get(
                    "highestPrice"
                )
            ),

            "lowest24": safe_float(
                item.get(
                    "lowestPrice"
                )
            ),

            "open24": safe_float(
                item.get(
                    "openPrice"
                )
            ),

            "funding_rate": safe_float(
                item.get(
                    "prePositionFeeRate"
                )
            ),
        }


    print(
        f"[INFO] Futures market data: "
        f"{len(result)}"
    )

    return result


# ============================================================
# SYMBOL CONVERSION
# ============================================================

def futures_to_spot_symbol(
    futures_symbol
):

    symbol = (
        futures_symbol
        .lower()
    )

    if symbol.endswith(
        "usdt"
    ):

        base = symbol[
            :-4
        ]

        return (
            base
            + "_usdt"
        )

    return None


# ============================================================
# LBANK SPOT KLINE
# ============================================================

def get_spot_klines(
    spot_symbol,
    limit=KLINE_LIMIT
):

    data = http_get(
        SPOT_BASE_URL,
        "/v2/kline.do",
        {
            "symbol": spot_symbol,
            "size": limit,
            "type": TIMEFRAME_TYPE,
            "time": now_ts(),
        }
    )

    if not data:

        return []


    raw = data.get(
        "data",
        []
    )

    if not isinstance(
        raw,
        list
    ):

        return []


    candles = []


    for row in raw:

        if not isinstance(
            row,
            (list, tuple)
        ):
            continue

        if len(row) < 6:
            continue

        try:

            ts = safe_float(
                row[0]
            )

            # milliseconds protection
            if ts > 10_000_000_000:

                ts /= 1000


            candle = {

                "time": int(ts),

                "open": safe_float(
                    row[1]
                ),

                "high": safe_float(
                    row[2]
                ),

                "low": safe_float(
                    row[3]
                ),

                "close": safe_float(
                    row[4]
                ),

                "volume": safe_float(
                    row[5]
                ),
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

        except Exception:

            continue


    candles.sort(
        key=lambda x: x["time"]
    )


    return candles


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    candles
):

    current_time = now_ts()

    result = []


    for candle in candles:

        end_time = (
            candle["time"]
            + TIMEFRAME_SECONDS
        )

        if end_time <= current_time:

            result.append(
                candle
            )


    return result


# ============================================================
# VOLUME RATIO
# ============================================================

def volume_ratio(
    candles
):

    if len(candles) < (
        VOLUME_LOOKBACK + 1
    ):

        return 0.0


    current_volume = (
        candles[-1]["volume"]
    )


    previous = [
        c["volume"]
        for c in candles[
            -(VOLUME_LOOKBACK + 1):-1
        ]
        if c["volume"] > 0
    ]


    if not previous:

        return 0.0


    average = (
        sum(previous)
        / len(previous)
    )


    if average <= 0:

        return 0.0


    return (
        current_volume
        / average
    )


# ============================================================
# BUILD TOP FUTURES MARKETS
# ============================================================

def get_top_futures_markets():

    instruments = (
        get_futures_instruments()
    )

    markets = (
        get_futures_market_data()
    )

    if not instruments:

        return []

    if not markets:

        return []


    valid_symbols = set()


    for instrument in instruments:

        symbol = str(
            instrument.get(
                "symbol",
                ""
            )
        ).upper().strip()

        if symbol:

            valid_symbols.add(
                symbol
            )


    candidates = []


    for symbol in valid_symbols:

        market = markets.get(
            symbol
        )

        if not market:

            continue


        turnover = safe_float(
            market.get(
                "turnover24"
            )
        )

        volume = safe_float(
            market.get(
                "volume24"
            )
        )

        price = safe_float(
            market.get(
                "last_price"
            )
        )


        if price <= 0:

            continue


        liquidity = turnover

        if liquidity <= 0:

            liquidity = (
                volume
                * price
            )


        candidates.append({

            "symbol": symbol,

            "last_price": price,

            "marked_price": market.get(
                "marked_price"
            ),

            "volume24": volume,

            "turnover24": turnover,

            "liquidity": liquidity,

            "funding_rate": market.get(
                "funding_rate"
            ),

        })


    candidates.sort(
        key=lambda x:
        x["liquidity"],
        reverse=True
    )


    candidates = candidates[
        :VOLUME_POOL
    ]


    print(
        "\n"
        + "=" * 70
    )

    print(
        "FUTURES LIQUIDITY POOL"
    )

    print(
        "=" * 70
    )


    ranked = []


    for rank, item in enumerate(
        candidates,
        1
    ):

        futures_symbol = (
            item["symbol"]
        )

        spot_symbol = (
            futures_to_spot_symbol(
                futures_symbol
            )
        )


        if not spot_symbol:

            continue


        print(
            f"\n[{rank:02d}/"
            f"{len(candidates)}] "
            f"{futures_symbol}"
        )


        try:

            candles = get_spot_klines(
                spot_symbol,
                KLINE_LIMIT
            )


            closed = (
                get_closed_candles(
                    candles
                )
            )


            if len(closed) < MIN_CANDLES:

                print(
                    f"[SKIP] "
                    f"{futures_symbol} "
                    f"not enough candles: "
                    f"{len(closed)}"
                )

                continue


            ratio = (
                volume_ratio(
                    closed
                )
            )


            item_copy = dict(
                item
            )


            item_copy[
                "spot_symbol"
            ] = spot_symbol


            item_copy[
                "candles"
            ] = closed


            item_copy[
                "volume_ratio"
            ] = ratio


            ranked.append(
                item_copy
            )


            print(
                f"[VOLUME] "
                f"{futures_symbol:<15} "
                f"5m ratio="
                f"{ratio:.2f}x"
            )


        except Exception as e:

            print(
                f"[ERROR] "
                f"{futures_symbol}: "
                f"{e}"
            )


        time.sleep(
            SLEEP_BETWEEN_REQUESTS
        )


    # ========================================================
    # Rank by abnormal volume first
    # ========================================================

    ranked.sort(

        key=lambda x: (

            x["volume_ratio"],

            math.log10(
                max(
                    x["liquidity"],
                    1
                )
            )
        ),

        reverse=True
    )


    ranked = ranked[
        :TOP_N
    ]


    print(
        "\n"
        + "=" * 70
    )

    print(
        f"TOP {TOP_N} FUTURES MARKETS"
    )

    print(
        "=" * 70
    )


    for i, item in enumerate(
        ranked,
        1
    ):

        print(
            f"{i:02d}. "
            f"{item['symbol']:<15} "
            f"volume="
            f"{item['volume_ratio']:.2f}x"
        )


    return ranked


# ============================================================
# TENKAN
# ============================================================

def tenkan(
    candles,
    index
):

    if index < (
        TENKAN_LENGTH - 1
    ):

        return None


    window = candles[
        index
        - TENKAN_LENGTH
        + 1:
        index + 1
    ]


    high = max(
        c["high"]
        for c in window
    )


    low = min(
        c["low"]
        for c in window
    )


    return (
        high + low
    ) / 2


# ============================================================
# KIJUN
# ============================================================

def kijun(
    candles,
    index
):

    if index < (
        KIJUN_LENGTH - 1
    ):

        return None


    window = candles[
        index
        - KIJUN_LENGTH
        + 1:
        index + 1
    ]


    high = max(
        c["high"]
        for c in window
    )


    low = min(
        c["low"]
        for c in window
    )


    return (
        high + low
    ) / 2


# ============================================================
# CROSS
# ============================================================

def find_latest_cross(
    candles
):

    if len(candles) < (
        KIJUN_LENGTH
        + BOX_LENGTH
        + 5
    ):

        return None


    latest = None


    # Last candle is closed,
    # but we scan only existing
    # closed candles.
    for i in range(
        KIJUN_LENGTH,
        len(candles)
    ):

        previous_tenkan = tenkan(
            candles,
            i - 1
        )

        previous_kijun = kijun(
            candles,
            i - 1
        )

        current_tenkan = tenkan(
            candles,
            i
        )

        current_kijun = kijun(
            candles,
            i
        )


        if (
            previous_tenkan is None
            or previous_kijun is None
            or current_tenkan is None
            or current_kijun is None
        ):

            continue


        bullish = (

            previous_tenkan
            <= previous_kijun

            and

            current_tenkan
            > current_kijun

        )


        bearish = (

            previous_tenkan
            >= previous_kijun

            and

            current_tenkan
            < current_kijun

        )


        if not (
            bullish
            or bearish
        ):

            continue


        if i < BOX_LENGTH:

            continue


        latest = {

            "index": i,

            "direction": (
                "BUY"
                if bullish
                else "SELL"
            ),

            "time": candles[i][
                "time"
            ],

            "price": candles[i][
                "close"
            ],

            "tenkan": current_tenkan,

            "kijun": current_kijun,
        }


    return latest


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


    box_candles = candles[
        start:end
    ]


    if len(box_candles) != (
        BOX_LENGTH
    ):

        return None


    high = max(
        c["high"]
        for c in box_candles
    )


    low = min(
        c["low"]
        for c in box_candles
    )


    width = (
        high - low
    )


    if width <= 0:

        return None


    return {

        "high": high,

        "low": low,

        "width": width,

        "start_time":
            box_candles[0][
                "time"
            ],

        "end_time":
            box_candles[-1][
                "time"
            ],
    }


# ============================================================
# SWING LOW
# ============================================================

def is_swing_low(
    candles,
    index
):

    left = (
        index
        - SWING_LEFT
    )

    right = (
        index
        + SWING_RIGHT
    )


    if left < 0:

        return False


    if right >= len(candles):

        return False


    center = candles[
        index
    ]["low"]


    for i in range(
        left,
        right + 1
    ):

        if i == index:

            continue


        if candles[i][
            "low"
        ] <= center:

            return False


    return True


# ============================================================
# SWING HIGH
# ============================================================

def is_swing_high(
    candles,
    index
):

    left = (
        index
        - SWING_LEFT
    )

    right = (
        index
        + SWING_RIGHT
    )


    if left < 0:

        return False


    if right >= len(candles):

        return False


    center = candles[
        index
    ]["high"]


    for i in range(
        left,
        right + 1
    ):

        if i == index:

            continue


        if candles[i][
            "high"
        ] >= center:

            return False


    return True


# ============================================================
# LATEST SWING LOW
# ============================================================

def latest_swing_low(
    candles,
    start_index,
    end_index
):

    max_index = (
        end_index
        - SWING_RIGHT
    )


    for i in range(
        max_index,
        start_index - 1,
        -1
    ):

        if is_swing_low(
            candles,
            i
        ):

            return {

                "index": i,

                "price":
                    candles[i][
                        "low"
                    ],

                "time":
                    candles[i][
                        "time"
                    ],
            }


    return None


# ============================================================
# LATEST SWING HIGH
# ============================================================

def latest_swing_high(
    candles,
    start_index,
    end_index
):

    max_index = (
        end_index
        - SWING_RIGHT
    )


    for i in range(
        max_index,
        start_index - 1,
        -1
    ):

        if is_swing_high(
            candles,
            i
        ):

            return {

                "index": i,

                "price":
                    candles[i][
                        "high"
                    ],

                "time":
                    candles[i][
                        "time"
                    ],
            }


    return None


# ============================================================
# BREAKOUT
# ============================================================

def check_breakout(
    candles,
    cross,
    box
):

    cross_index = (
        cross["index"]
    )

    direction = (
        cross["direction"]
    )


    start = (
        cross_index + 1
    )


    if start >= len(candles):

        return None


    # ========================================================
    # BUY
    # ========================================================

    if direction == "BUY":

        for i in range(
            start,
            len(candles)
        ):

            candle = candles[i]


            if candle[
                "close"
            ] <= box["high"]:

                continue


            swing = latest_swing_low(
                candles,
                cross_index + 1,
                i
            )


            if not swing:

                continue


            entry = candle[
                "close"
            ]


            sl = (
                swing["price"]
                * (
                    1
                    - SL_BUFFER_PCT
                )
            )


            tp = (
                entry
                + (
                    box["width"]
                    * TP_BOX_MULTIPLIER
                )
            )


            if sl >= entry:

                continue


            if tp <= entry:

                continue


            return {

                "direction": "BUY",

                "entry": entry,

                "sl": sl,

                "tp": tp,

                "breakout_index": i,

                "breakout_time":
                    candle["time"],

                "breakout_price":
                    candle["close"],

                "swing_price":
                    swing["price"],

                "swing_time":
                    swing["time"],
            }


    # ========================================================
    # SELL
    # ========================================================

    else:

        for i in range(
            start,
            len(candles)
        ):

            candle = candles[i]


            if candle[
                "close"
            ] >= box["low"]:

                continue


            swing = latest_swing_high(
                candles,
                cross_index + 1,
                i
            )


            if not swing:

                continue


            entry = candle[
                "close"
            ]


            sl = (
                swing["price"]
                * (
                    1
                    + SL_BUFFER_PCT
                )
            )


            tp = (
                entry
                - (
                    box["width"]
                    * TP_BOX_MULTIPLIER
                )
            )


            if sl <= entry:

                continue


            if tp >= entry:

                continue


            return {

                "direction": "SELL",

                "entry": entry,

                "sl": sl,

                "tp": tp,

                "breakout_index": i,

                "breakout_time":
                    candle["time"],

                "breakout_price":
                    candle["close"],

                "swing_price":
                    swing["price"],

                "swing_time":
                    swing["time"],
            }


    return None


# ============================================================
# FIND SETUP
# ============================================================

def find_latest_setup(
    candles
):

    if len(candles) < (
        MIN_CANDLES
    ):

        return None


    cross = find_latest_cross(
        candles
    )


    if not cross:

        return None


    print(
        f"[CROSS] "
        f"{cross['direction']} "
        f"price="
        f"{fmt_price(cross['price'])} "
        f"Tenkan="
        f"{fmt_price(cross['tenkan'])} "
        f"Kijun="
        f"{fmt_price(cross['kijun'])}"
    )


    box = get_box(
        candles,
        cross["index"]
    )


    if not box:

        return None


    print(
        f"[BOX] "
        f"H={fmt_price(box['high'])} "
        f"L={fmt_price(box['low'])} "
        f"W={fmt_price(box['width'])}"
    )


    breakout = check_breakout(
        candles,
        cross,
        box
    )


    if not breakout:

        return None


    return {

        "cross": cross,

        "box": box,

        "breakout": breakout,
    }


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    trade,
    current_price
):

    entry = safe_float(
        trade["entry"]
    )


    if entry <= 0:

        return 0.0


    if trade["direction"] == "BUY":

        return (
            current_price
            - entry
        ) / entry * 100


    return (
        entry
        - current_price
    ) / entry * 100


# ============================================================
# UPDATE OPEN TRADE
# ============================================================

def update_open_trade(
    state,
    history,
    futures_markets
):

    trade = state.get(
        "open_trade"
    )


    if not trade:

        return False


    symbol = (
        trade["symbol"]
    )


    market = futures_markets.get(
        symbol
    )


    if not market:

        print(
            f"[WARN] Futures price "
            f"not found: {symbol}"
        )

        return False


    current_price = safe_float(
        market["last_price"]
    )


    if current_price <= 0:

        return False


    trade[
        "current_price"
    ] = current_price


    pnl = calculate_pnl(
        trade,
        current_price
    )


    trade[
        "live_pnl_pct"
    ] = pnl


    direction = (
        trade["direction"]
    )


    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )


    exit_reason = None


    if direction == "BUY":

        if current_price <= sl:

            exit_reason = "SL"

        elif current_price >= tp:

            exit_reason = "TP"


    else:

        if current_price >= sl:

            exit_reason = "SL"

        elif current_price <= tp:

            exit_reason = "TP"


    if exit_reason:

        trade[
            "exit"
        ] = current_price

        trade[
            "exit_time"
        ] = now_iso()

        trade[
            "exit_reason"
        ] = exit_reason

        trade[
            "final_pnl_pct"
        ] = pnl


        history.append(
            trade.copy()
        )


        state[
            "open_trade"
        ] = None


        save_json(
            HISTORY_FILE,
            history
        )

        save_json(
            STATE_FILE,
            state
        )


        print(
            f"[CLOSED] "
            f"{symbol} "
            f"{direction} "
            f"{exit_reason} "
            f"PNL={pnl:+.2f}%"
        )


        return True


    return False


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    state,
    signal
):

    trade = {

        "exchange":
            EXCHANGE_NAME,

        "symbol":
            signal["symbol"],

        "direction":
            signal["direction"],

        "entry":
            signal["entry"],

        "sl":
            signal["sl"],

        "tp":
            signal["tp"],

        "current_price":
            signal["entry"],

        "live_pnl_pct":
            0.0,

        "box_high":
            signal["box_high"],

        "box_low":
            signal["box_low"],

        "box_width":
            signal["box_width"],

        "volume_ratio":
            signal["volume_ratio"],

        "cross_price":
            signal["cross_price"],

        "cross_time":
            signal["cross_time"],

        "breakout_price":
            signal["breakout_price"],

        "breakout_time":
            signal["breakout_time"],

        "swing_price":
            signal["swing_price"],

        "swing_time":
            signal["swing_time"],

        "open_time":
            now_iso(),
    }


    state[
        "open_trade"
    ] = trade


    state[
        "last_signal"
    ] = trade.copy()


    state[
        "last_signals"
    ][
        signal["symbol"]
    ] = {

        "direction":
            signal["direction"],

        "breakout_time":
            signal["breakout_time"],
    }


    save_json(
        STATE_FILE,
        state
    )


    print(
        f"[OPEN] "
        f"{signal['symbol']} "
        f"{signal['direction']} "
        f"Entry="
        f"{fmt_price(signal['entry'])} "
        f"SL="
        f"{fmt_price(signal['sl'])} "
        f"TP="
        f"{fmt_price(signal['tp'])}"
    )


    return trade


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

        if safe_float(
            x.get(
                "final_pnl_pct"
            )
        ) > 0
    )


    losses = sum(

        1

        for x in history

        if safe_float(
            x.get(
                "final_pnl_pct"
            )
        ) < 0
    )


    breakeven = (
        trades
        - wins
        - losses
    )


    total_pnl = sum(

        safe_float(
            x.get(
                "final_pnl_pct"
            )
        )

        for x in history
    )


    win_rate = (

        wins
        / trades
        * 100

        if trades > 0

        else 0
    )


    return {

        "trades": trades,

        "wins": wins,

        "losses": losses,

        "breakeven":
            breakeven,

        "pnl":
            total_pnl,

        "win_rate":
            win_rate,
    }


# ============================================================
# TELEGRAM REPORT
# ============================================================

def build_report(
    state,
    history,
    ranked_markets,
    signal=None,
    trade_closed=False
):

    perf = performance(
        history
    )


    lines = []


    lines.append(
        "📡 <b>LBANK FUTURES "
        "PRICE ACTION REPORT</b>"
    )


    lines.append(
        f"🕐 {now_iso()}"
    )


    lines.append(
        f"⏱ <b>5m CLOSED | TOP {TOP_N}</b>"
    )


    lines.append(
        "🤖 <b>TENKAN/KIJUN + BOX 26</b>"
    )


    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )


    lines.append(
        "📊 <b>PERFORMANCE</b>"
    )


    lines.append(
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['breakeven']}"
    )


    lines.append(
        f"🏆 WR {perf['win_rate']:.1f}% | "
        f"PnL {perf['pnl']:+.2f}%"
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

        pnl = safe_float(
            trade.get(
                "live_pnl_pct"
            )
        )


        emoji = (
            "🟢"
            if pnl >= 0
            else "🔴"
        )


        lines.append(
            "📌 <b>OPEN FUTURES TRADE</b>"
        )


        lines.append(
            f"💰 <b>{trade['symbol']}</b>"
        )


        lines.append(
            f"{'🟢' if trade['direction'] == 'BUY' else '🔴'} "
            f"<b>{trade['direction']}</b>"
        )


        lines.append(
            f"Entry: "
            f"<code>{fmt_price(trade['entry'])}</code>"
        )


        lines.append(
            f"SL: "
            f"<code>{fmt_price(trade['sl'])}</code>"
        )


        lines.append(
            f"TP: "
            f"<code>{fmt_price(trade['tp'])}</code>"
        )


        lines.append(
            f"Price: "
            f"<code>{fmt_price(trade.get('current_price'))}</code>"
        )


        lines.append(
            f"{emoji} Live P&L: "
            f"<b>{pnl:+.2f}%</b>"
        )


        lines.append(
            f"📦 Box: "
            f"{fmt_price(trade['box_low'])} → "
            f"{fmt_price(trade['box_high'])}"
        )


        lines.append(
            f"📊 Volume: "
            f"{trade['volume_ratio']:.2f}x"
        )


    elif trade_closed:

        lines.append(
            "✅ <b>FUTURES TRADE CLOSED</b>"
        )


        if history:

            last = history[-1]


            lines.append(
                f"Symbol: "
                f"<b>{last.get('symbol')}</b>"
            )


            lines.append(
                f"Direction: "
                f"<b>{last.get('direction')}</b>"
            )


            lines.append(
                f"Reason: "
                f"<b>{last.get('exit_reason')}</b>"
            )


            lines.append(
                f"Final P&L: "
                f"<b>{safe_float(last.get('final_pnl_pct')):+.2f}%</b>"
            )


    elif signal:

        lines.append(
            "🚨 <b>NEW FUTURES SIGNAL</b>"
        )


        lines.append(
            f"💰 <b>{signal['symbol']}</b>"
        )


        lines.append(
            f"{'🟢' if signal['direction'] == 'BUY' else '🔴'} "
            f"<b>{signal['direction']}</b>"
        )


        lines.append(
            f"Entry: "
            f"<code>{fmt_price(signal['entry'])}</code>"
        )


        lines.append(
            f"SL: "
            f"<code>{fmt_price(signal['sl'])}</code>"
        )


        lines.append(
            f"TP: "
            f"<code>{fmt_price(signal['tp'])}</code>"
        )


        lines.append(
            f"📦 Box High: "
            f"<code>{fmt_price(signal['box_high'])}</code>"
        )


        lines.append(
            f"📦 Box Low: "
            f"<code>{fmt_price(signal['box_low'])}</code>"
        )


        lines.append(
            f"📏 Box Width: "
            f"<code>{fmt_price(signal['box_width'])}</code>"
        )


        lines.append(
            f"🔀 Cross: "
            f"<code>{fmt_price(signal['cross_price'])}</code>"
        )


        lines.append(
            f"🚀 Breakout: "
            f"<code>{fmt_price(signal['breakout_price'])}</code>"
        )


        lines.append(
            f"📐 Swing: "
            f"<code>{fmt_price(signal['swing_price'])}</code>"
        )


        lines.append(
            f"📊 Volume: "
            f"<b>{signal['volume_ratio']:.2f}x</b>"
        )


    else:

        lines.append(
            "⚪ <b>NO ACTIVE TRADE / "
            "NO NEW SIGNAL</b>"
        )


    # ========================================================
    # TOP MARKETS
    # ========================================================

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )


    lines.append(
        "📈 <b>TOP FUTURES VOLUME</b>"
    )


    for i, item in enumerate(
        ranked_markets[:10],
        1
    ):

        lines.append(
            f"{i}. "
            f"{item['symbol']} "
            f"📊 "
            f"{item['volume_ratio']:.2f}x"
        )


    return "\n".join(
        lines
    )


# ============================================================
# SCAN MARKET
# ============================================================

def scan_market(
    item
):

    candles = item.get(
        "candles",
        []
    )


    if not candles:

        spot_symbol = (
            item["spot_symbol"]
        )

        raw = get_spot_klines(
            spot_symbol,
            KLINE_LIMIT
        )

        candles = (
            get_closed_candles(
                raw
            )
        )


    if len(candles) < (
        MIN_CANDLES
    ):

        return None


    setup = find_latest_setup(
        candles
    )


    if not setup:

        return None


    cross = setup[
        "cross"
    ]

    box = setup[
        "box"
    ]

    breakout = setup[
        "breakout"
    ]


    signal = {

        "symbol":
            item["symbol"],

        "spot_symbol":
            item["spot_symbol"],

        "direction":
            breakout[
                "direction"
            ],

        "entry":
            breakout[
                "entry"
            ],

        "sl":
            breakout[
                "sl"
            ],

        "tp":
            breakout[
                "tp"
            ],

        "box_high":
            box["high"],

        "box_low":
            box["low"],

        "box_width":
            box["width"],

        "cross_price":
            cross["price"],

        "cross_time":
            cross["time"],

        "breakout_price":
            breakout[
                "breakout_price"
            ],

        "breakout_time":
            breakout[
                "breakout_time"
            ],

        "swing_price":
            breakout[
                "swing_price"
            ],

        "swing_time":
            breakout[
                "swing_time"
            ],

        "volume_ratio":
            item[
                "volume_ratio"
            ],
    }


    return signal


# ============================================================
# MAIN RUN
# ============================================================

def run():

    print(
        "=" * 70
    )

    print(
        "LBANK FUTURES PRICE ACTION SCANNER"
    )

    print(
        "=" * 70
    )

    print(
        "Market Source: LBANK FUTURES"
    )

    print(
        "Candle Source: LBANK SPOT 5m"
    )

    print(
        f"TOP: {TOP_N}"
    )

    print(
        f"BOX: {BOX_LENGTH}"
    )

    print(
        f"MAX OPEN: {MAX_OPEN_TRADES}"
    )

    print(
        "=" * 70
    )


    state = load_state()

    history = load_history()


    # ========================================================
    # GET FUTURES MARKET DATA
    # ========================================================

    futures_markets = (
        get_futures_market_data()
    )


    if not futures_markets:

        print(
            "[FATAL] Futures market "
            "data unavailable."
        )

        send_telegram(
            "⚠️ <b>LBANK FUTURES SCANNER</b>\n\n"
            "❌ Futures market data unavailable."
        )

        return


    # ========================================================
    # UPDATE OPEN TRADE
    # ========================================================

    trade_closed = False


    if state.get(
        "open_trade"
    ):

        print(
            "[INFO] Open trade detected."
        )


        trade_closed = (
            update_open_trade(
                state,
                history,
                futures_markets
            )
        )


        save_json(
            STATE_FILE,
            state
        )


        # ====================================================
        # Still open
        # ====================================================

        if state.get(
            "open_trade"
        ):

            trade = (
                state[
                    "open_trade"
                ]
            )


            ranked = []


            report = build_report(
                state,
                history,
                ranked,
                signal=None,
                trade_closed=False
            )


            send_telegram(
                report
            )


            print(
                "[INFO] Trade remains open."
            )


            return


        # ====================================================
        # Just closed
        # ====================================================

        if trade_closed:

            ranked = (
                get_top_futures_markets()
            )


            report = build_report(
                state,
                history,
                ranked,
                signal=None,
                trade_closed=True
            )


            send_telegram(
                report
            )


            # Do not immediately reopen
            # another trade in same run.
            return


    # ========================================================
    # TOP FUTURES MARKETS
    # ========================================================

    ranked_markets = (
        get_top_futures_markets()
    )


    if not ranked_markets:

        print(
            "[WARN] No ranked Futures "
            "markets."
        )


        report = build_report(
            state,
            history,
            [],
            signal=None
        )


        send_telegram(
            report
        )


        return


    # ========================================================
    # SCAN
    # ========================================================

    signal = None


    print(
        "\n"
        + "=" * 70
    )

    print(
        "SCANNING FUTURES TOP MARKETS"
    )

    print(
        "=" * 70
    )


    for rank, item in enumerate(
        ranked_markets,
        1
    ):

        symbol = item[
            "symbol"
        ]


        print(
            f"\n[{rank:02d}/"
            f"{len(ranked_markets)}] "
            f"{symbol}"
        )


        try:

            result = scan_market(
                item
            )


            if not result:

                print(
                    "[NO SETUP]"
                )

                continue


            # =================================================
            # DUPLICATE PROTECTION
            # =================================================

            last_signal = (
                state
                .get(
                    "last_signals",
                    {}
                )
                .get(
                    symbol
                )
            )


            if last_signal:

                same_direction = (
                    last_signal.get(
                        "direction"
                    )
                    ==
                    result[
                        "direction"
                    ]
                )


                same_breakout = (
                    last_signal.get(
                        "breakout_time"
                    )
                    ==
                    result[
                        "breakout_time"
                    ]
                )


                if (
                    same_direction
                    and
                    same_breakout
                ):

                    print(
                        "[SKIP] Duplicate "
                        "signal."
                    )

                    continue


            signal = result


            print(
                "\n"
                + "=" * 70
            )

            print(
                "🚨 NEW SIGNAL"
            )

            print(
                "=" * 70
            )


            print(
                f"Symbol: "
                f"{signal['symbol']}"
            )

            print(
                f"Direction: "
                f"{signal['direction']}"
            )

            print(
                f"Entry: "
                f"{fmt_price(signal['entry'])}"
            )

            print(
                f"SL: "
                f"{fmt_price(signal['sl'])}"
            )

            print(
                f"TP: "
                f"{fmt_price(signal['tp'])}"
            )

            print(
                f"Volume: "
                f"{signal['volume_ratio']:.2f}x"
            )


            break


        except Exception as e:

            print(
                f"[ERROR] "
                f"{symbol}: {e}"
            )

            traceback.print_exc()


        time.sleep(
            SLEEP_BETWEEN_REQUESTS
        )


    # ========================================================
    # OPEN SIGNAL
    # ========================================================

    if signal:

        open_trade(
            state,
            signal
        )


        report = build_report(
            state,
            history,
            ranked_markets,
            signal=signal
        )


        send_telegram(
            report
        )


        return


    # ========================================================
    # NO SIGNAL
    # ========================================================

    print(
        "\n"
        "[INFO] No new signal found."
    )


    state[
        "last_scan"
    ] = now_iso()


    save_json(
        STATE_FILE,
        state
    )


    report = build_report(
        state,
        history,
        ranked_markets,
        signal=None
    )


    send_telegram(
        report
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        run()

    except KeyboardInterrupt:

        print(
            "[INFO] Scanner stopped."
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
