# ============================================================
# BINANCE FUTURES PRICE ACTION SCANNER
# ============================================================
#
# MARKET:
#   Binance USDⓈ-M Futures
#
# RANKING:
#   24h quote volume
#
# TIMEFRAME:
#   5m CLOSED candles
#
# STRATEGY:
#   Tenkan  = 9
#   Kijun   = 26
#
#   ONLY MOST RECENT Tenkan/Kijun CROSS
#
#   Cross candle is excluded from Box.
#
#   Box = 26 candles immediately BEFORE latest cross.
#
#   BUY:
#       later CLOSED candle closes above Box High
#
#   SELL:
#       later CLOSED candle closes below Box Low
#
#   SWING:
#       confirmed pivot:
#       2 candles left + 2 candles right
#
#   BUY SL:
#       below latest valid Swing Low
#
#   SELL SL:
#       above latest valid Swing High
#
#   TP:
#       50% of Box width
#
#   MAX OPEN TRADES:
#       1
#
# ============================================================

import os
import json
import time
from datetime import datetime, timezone

import requests


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://fapi.binance.com"

EXCHANGE_INFO_URL = (
    f"{BASE_URL}/fapi/v1/exchangeInfo"
)

TICKER_24H_URL = (
    f"{BASE_URL}/fapi/v1/ticker/24hr"
)

KLINES_URL = (
    f"{BASE_URL}/fapi/v1/klines"
)

PRICE_URL = (
    f"{BASE_URL}/fapi/v1/ticker/price"
)

TIME_URL = (
    f"{BASE_URL}/fapi/v1/time"
)


# ------------------------------------------------------------
# Strategy
# ------------------------------------------------------------

TIMEFRAME = "5m"

TOP_SYMBOLS = 30

KLINE_LIMIT = 300

TENKAN_PERIOD = 9

KIJUN_PERIOD = 26

BOX_SIZE = 26

PIVOT_LEFT = 2

PIVOT_RIGHT = 2

VOLUME_LOOKBACK = 20

TP_BOX_PERCENT = 0.50

SL_BUFFER_PERCENT = 0.0015

MAX_OPEN_TRADES = 1


# ------------------------------------------------------------
# Files
# ------------------------------------------------------------

STATE_FILE = (
    "binance_price_action_state.json"
)

HISTORY_FILE = (
    "binance_price_action_trade_history.json"
)


# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

REQUEST_TIMEOUT = 20

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
        "Binance-Futures-Price-Action-Scanner/1.0"
    }
)


# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# GENERAL HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def fmt_price(value):
    if value is None:
        return "N/A"

    try:
        value = float(value)
    except Exception:
        return str(value)

    if value >= 10000:
        return f"{value:,.2f}"

    if value >= 1000:
        return f"{value:,.3f}"

    if value >= 100:
        return f"{value:,.4f}"

    if value >= 1:
        return f"{value:,.5f}"

    if value >= 0.1:
        return f"{value:,.6f}"

    if value >= 0.01:
        return f"{value:,.7f}"

    return f"{value:.10f}"


def fmt_pct(value):
    try:
        return f"{float(value):+.2f}%"
    except Exception:
        return "N/A"


def ms_to_iso(ms):
    try:
        return datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except Exception:
        return "N/A"


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

    except Exception as e:
        print(
            f"[WARN] Cannot read {path}: {e}"
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
    return load_json(
        STATE_FILE,
        {
            "open_trade": None,
            "last_run": None,
            "last_signal": None
        }
    )


def save_state(state):
    state["last_run"] = now_iso()

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
# HTTP REQUEST
# ============================================================

def http_get(url, params=None):

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
            "[HTTP ERROR]"
        )

        print(
            f"URL: {url}"
        )

        print(
            f"PARAMS: {params}"
        )

        print(
            f"ERROR: {e}"
        )

        return None


# ============================================================
# BINANCE TIME
# ============================================================

def get_server_time():

    data = http_get(
        TIME_URL
    )

    if isinstance(data, dict):

        return int(
            data.get(
                "serverTime",
                int(time.time() * 1000)
            )
        )

    return int(
        time.time() * 1000
    )


# ============================================================
# BINANCE FUTURES SYMBOLS
# ============================================================

def get_trading_symbols():

    data = http_get(
        EXCHANGE_INFO_URL
    )

    if not isinstance(data, dict):

        return set()

    symbols = set()

    for item in data.get(
        "symbols",
        []
    ):

        if not isinstance(
            item,
            dict
        ):
            continue

        symbol = item.get(
            "symbol"
        )

        status = item.get(
            "status"
        )

        contract_type = item.get(
            "contractType"
        )

        quote_asset = item.get(
            "quoteAsset"
        )

        # Only active perpetual USDT futures.
        if (
            symbol
            and status == "TRADING"
            and contract_type == "PERPETUAL"
            and quote_asset == "USDT"
        ):

            symbols.add(
                symbol.upper()
            )

    return symbols


# ============================================================
# 24H VOLUME RANKING
# ============================================================

def get_top_symbols(
    trading_symbols
):

    data = http_get(
        TICKER_24H_URL
    )

    if not isinstance(
        data,
        list
    ):

        return []

    markets = []

    for item in data:

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
        ).upper()

        if symbol not in trading_symbols:
            continue

        # quoteVolume = USDT turnover over 24h
        quote_volume = safe_float(
            item.get(
                "quoteVolume"
            )
        )

        volume = safe_float(
            item.get(
                "volume"
            )
        )

        last_price = safe_float(
            item.get(
                "lastPrice"
            )
        )

        if quote_volume <= 0:
            continue

        markets.append(
            {
                "symbol": symbol,
                "quote_volume": quote_volume,
                "volume": volume,
                "last_price": last_price
            }
        )

    markets.sort(
        key=lambda x:
        x["quote_volume"],
        reverse=True
    )

    return markets[:TOP_SYMBOLS]


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol
):

    params = {
        "symbol": symbol,
        "interval": TIMEFRAME,
        "limit": KLINE_LIMIT
    }

    data = http_get(
        KLINES_URL,
        params
    )

    if not isinstance(
        data,
        list
    ):
        return []

    server_time = get_server_time()

    candles = []

    for row in data:

        if not isinstance(
            row,
            list
        ):
            continue

        if len(row) < 7:
            continue

        try:

            open_time = int(
                row[0]
            )

            open_price = float(
                row[1]
            )

            high = float(
                row[2]
            )

            low = float(
                row[3]
            )

            close = float(
                row[4]
            )

            volume = float(
                row[5]
            )

            close_time = int(
                row[6]
            )

        except Exception:

            continue

        # ----------------------------------------------------
        # Only CLOSED candles.
        # ----------------------------------------------------

        if close_time >= server_time:

            continue

        candles.append(
            {
                "open_time":
                    open_time,

                "close_time":
                    close_time,

                "open":
                    open_price,

                "high":
                    high,

                "low":
                    low,

                "close":
                    close,

                "volume":
                    volume
            }
        )

    candles.sort(
        key=lambda x:
        x["open_time"]
    )

    return candles


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(
    symbol
):

    data = http_get(
        PRICE_URL,
        {
            "symbol": symbol
        }
    )

    if isinstance(
        data,
        dict
    ):

        return safe_float(
            data.get(
                "price"
            )
        )

    return 0.0


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(
    candles,
    index
):

    if index + 1 < TENKAN_PERIOD:

        return None

    start = (
        index
        - TENKAN_PERIOD
        + 1
    )

    section = candles[
        start:index + 1
    ]

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
# KIJUN
# ============================================================

def calculate_kijun(
    candles,
    index
):

    if index + 1 < KIJUN_PERIOD:

        return None

    start = (
        index
        - KIJUN_PERIOD
        + 1
    )

    section = candles[
        start:index + 1
    ]

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

    for index in range(
        len(candles)
    ):

        tenkan = calculate_tenkan(
            candles,
            index
        )

        kijun = calculate_kijun(
            candles,
            index
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

            cross_type = None

            # Bullish cross
            if (
                previous_tenkan
                <= previous_kijun
                and
                tenkan
                > kijun
            ):

                cross_type = "BUY"

            # Bearish cross
            elif (
                previous_tenkan
                >= previous_kijun
                and
                tenkan
                < kijun
            ):

                cross_type = "SELL"

            if cross_type:

                latest_cross = {
                    "index":
                        index,

                    "type":
                        cross_type,

                    "open_time":
                        candles[
                            index
                        ]["open_time"],

                    "close":
                        candles[
                            index
                        ]["close"],

                    "tenkan":
                        tenkan,

                    "kijun":
                        kijun
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

    # Cross candle excluded.
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

    if len(
        box_candles
    ) != BOX_SIZE:

        return None

    box_high = max(
        candle["high"]
        for candle in box_candles
    )

    box_low = min(
        candle["low"]
        for candle in box_candles
    )

    width = (
        box_high
        - box_low
    )

    if width <= 0:

        return None

    return {
        "start_index":
            start,

        "end_index":
            end - 1,

        "high":
            box_high,

        "low":
            box_low,

        "width":
            width,

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

    if (
        not cross
        or not box
    ):

        return None

    cross_index = cross[
        "index"
    ]

    latest_breakout = None

    for index in range(
        cross_index + 1,
        len(candles)
    ):

        candle = candles[
            index
        ]

        # BUY breakout
        if candle["close"] > box["high"]:

            latest_breakout = {
                "index":
                    index,

                "type":
                    "BUY",

                "open_time":
                    candle[
                        "open_time"
                    ],

                "close":
                    candle[
                        "close"
                    ]
            }

        # SELL breakout
        elif candle["close"] < box["low"]:

            latest_breakout = {
                "index":
                    index,

                "type":
                    "SELL",

                "open_time":
                    candle[
                        "open_time"
                    ],

                "close":
                    candle[
                        "close"
                    ]
            }

    return latest_breakout


# ============================================================
# SWING LOW
# ============================================================

def is_swing_low(
    candles,
    index
):

    if index - PIVOT_LEFT < 0:

        return False

    if (
        index + PIVOT_RIGHT
        >= len(candles)
    ):

        return False

    pivot_low = candles[
        index
    ]["low"]

    for i in range(
        index - PIVOT_LEFT,
        index + PIVOT_RIGHT + 1
    ):

        if i == index:
            continue

        if (
            candles[i]["low"]
            <= pivot_low
        ):

            return False

    return True


# ============================================================
# SWING HIGH
# ============================================================

def is_swing_high(
    candles,
    index
):

    if index - PIVOT_LEFT < 0:

        return False

    if (
        index + PIVOT_RIGHT
        >= len(candles)
    ):

        return False

    pivot_high = candles[
        index
    ]["high"]

    for i in range(
        index - PIVOT_LEFT,
        index + PIVOT_RIGHT + 1
    ):

        if i == index:
            continue

        if (
            candles[i]["high"]
            >= pivot_high
        ):

            return False

    return True


# ============================================================
# LATEST VALID SWING
# ============================================================

def find_latest_swing(
    candles,
    cross,
    breakout
):

    if (
        not cross
        or not breakout
    ):

        return None

    cross_index = cross[
        "index"
    ]

    breakout_index = breakout[
        "index"
    ]

    start = (
        cross_index + 1
    )

    end = (
        breakout_index - 1
    )

    if end < start:

        return None

    # --------------------------------------------------------
    # BUY needs Swing Low
    # --------------------------------------------------------

    if breakout["type"] == "BUY":

        for index in range(
            end,
            start - 1,
            -1
        ):

            if is_swing_low(
                candles,
                index
            ):

                return {
                    "type":
                        "LOW",

                    "index":
                        index,

                    "price":
                        candles[
                            index
                        ]["low"],

                    "open_time":
                        candles[
                            index
                        ]["open_time"]
                }

    # --------------------------------------------------------
    # SELL needs Swing High
    # --------------------------------------------------------

    if breakout["type"] == "SELL":

        for index in range(
            end,
            start - 1,
            -1
        ):

            if is_swing_high(
                candles,
                index
            ):

                return {
                    "type":
                        "HIGH",

                    "index":
                        index,

                    "price":
                        candles[
                            index
                        ]["high"],

                    "open_time":
                        candles[
                            index
                        ]["open_time"]
                }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles
):

    if len(candles) <= (
        VOLUME_LOOKBACK
    ):

        return 0.0

    current_volume = candles[
        -1
    ]["volume"]

    previous = candles[
        -VOLUME_LOOKBACK - 1:-1
    ]

    if not previous:

        return 0.0

    average_volume = (
        sum(
            candle["volume"]
            for candle in previous
        )
        / len(previous)
    )

    if average_volume <= 0:

        return 0.0

    return (
        current_volume
        / average_volume
    )


# ============================================================
# ANALYZE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    candles,
    market
):

    result = {

        "symbol":
            symbol,

        "status":
            "NO SIGNAL",

        "reason":
            "",

        "candles":
            len(candles),

        "quote_volume":
            market.get(
                "quote_volume",
                0
            ),

        "cross":
            None,

        "box":
            None,

        "breakout":
            None,

        "swing":
            None,

        "volume_ratio":
            calculate_volume_ratio(
                candles
            )
    }

    # --------------------------------------------------------
    # Minimum data
    # --------------------------------------------------------

    minimum_candles = (
        KIJUN_PERIOD
        + BOX_SIZE
        + 10
    )

    if len(candles) < minimum_candles:

        result["reason"] = (
            f"INSUFFICIENT KLINE "
            f"({len(candles)})"
        )

        return result

    # --------------------------------------------------------
    # Latest cross
    # --------------------------------------------------------

    cross = find_latest_cross(
        candles
    )

    if not cross:

        result["reason"] = (
            "NO LATEST CROSS"
        )

        return result

    result["cross"] = cross

    # --------------------------------------------------------
    # Box
    # --------------------------------------------------------

    box = build_box(
        candles,
        cross
    )

    if not box:

        result["reason"] = (
            "BOX FAILED"
        )

        return result

    result["box"] = box

    # --------------------------------------------------------
    # Breakout
    # --------------------------------------------------------

    breakout = find_breakout(
        candles,
        cross,
        box
    )

    if not breakout:

        result["reason"] = (
            "NO BREAKOUT"
        )

        return result

    result["breakout"] = breakout

    # --------------------------------------------------------
    # Swing
    # --------------------------------------------------------

    swing = find_latest_swing(
        candles,
        cross,
        breakout
    )

    if not swing:

        result["reason"] = (
            "NO CONFIRMED SWING"
        )

        return result

    result["swing"] = swing

    # --------------------------------------------------------
    # Entry
    # --------------------------------------------------------

    entry = breakout[
        "close"
    ]

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if breakout["type"] == "BUY":

        sl = (
            swing["price"]
            * (
                1
                - SL_BUFFER_PERCENT
            )
        )

        risk = (
            entry
            - sl
        )

        tp = (
            entry
            + (
                box["width"]
                * TP_BOX_PERCENT
            )
        )

        if risk <= 0:

            result["reason"] = (
                "INVALID BUY SL"
            )

            return result

        if tp <= entry:

            result["reason"] = (
                "INVALID BUY TP"
            )

            return result

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    else:

        sl = (
            swing["price"]
            * (
                1
                + SL_BUFFER_PERCENT
            )
        )

        risk = (
            sl
            - entry
        )

        tp = (
            entry
            - (
                box["width"]
                * TP_BOX_PERCENT
            )
        )

        if risk <= 0:

            result["reason"] = (
                "INVALID SELL SL"
            )

            return result

        if tp >= entry:

            result["reason"] = (
                "INVALID SELL TP"
            )

            return result

    # --------------------------------------------------------
    # Valid signal
    # --------------------------------------------------------

    result["status"] = (
        breakout["type"]
    )

    result["entry"] = entry

    result["sl"] = sl

    result["tp"] = tp

    result["risk"] = risk

    result["reward"] = abs(
        tp - entry
    )

    result["rr"] = (
        abs(tp - entry)
        / risk
        if risk > 0
        else 0
    )

    return result


# ============================================================
# PNL
# ============================================================

def calculate_pnl(
    trade,
    current_price
):

    entry = safe_float(
        trade.get(
            "entry"
        )
    )

    if entry <= 0:

        return 0.0

    side = trade.get(
        "side"
    )

    if side == "BUY":

        return (
            (
                current_price
                - entry
            )
            / entry
        ) * 100

    return (
        (
            entry
            - current_price
        )
        / entry
    ) * 100


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def check_open_trade(
    state,
    history
):

    trade = state.get(
        "open_trade"
    )

    if not trade:

        return None

    symbol = trade.get(
        "symbol"
    )

    if not symbol:

        return None

    current_price = get_current_price(
        symbol
    )

    if current_price <= 0:

        return None

    pnl = calculate_pnl(
        trade,
        current_price
    )

    trade[
        "current_price"
    ] = current_price

    trade[
        "pnl_percent"
    ] = pnl

    side = trade.get(
        "side"
    )

    tp = safe_float(
        trade.get(
            "tp"
        )
    )

    sl = safe_float(
        trade.get(
            "sl"
        )
    )

    exit_reason = None

    if side == "BUY":

        if current_price >= tp:

            exit_reason = "TP"

        elif current_price <= sl:

            exit_reason = "SL"

    else:

        if current_price <= tp:

            exit_reason = "TP"

        elif current_price >= sl:

            exit_reason = "SL"

    if not exit_reason:

        return {
            "closed":
                False,

            "pnl":
                pnl,

            "trade":
                trade
        }

    closed_trade = dict(
        trade
    )

    closed_trade[
        "exit_price"
    ] = current_price

    closed_trade[
        "exit_time"
    ] = now_iso()

    closed_trade[
        "exit_reason"
    ] = exit_reason

    closed_trade[
        "final_pnl_percent"
    ] = pnl

    history.append(
        closed_trade
    )

    state[
        "open_trade"
    ] = None

    return {
        "closed":
            True,

        "pnl":
            pnl,

        "reason":
            exit_reason,

        "trade":
            closed_trade
    }


# ============================================================
# OPEN NEW TRADE
# ============================================================

def open_trade(
    state,
    signal
):

    if state.get(
        "open_trade"
    ):

        return False

    trade = {

        "symbol":
            signal["symbol"],

        "side":
            signal["status"],

        "entry":
            signal["entry"],

        "sl":
            signal["sl"],

        "tp":
            signal["tp"],

        "rr":
            signal["rr"],

        "box_high":
            signal[
                "box"
            ]["high"],

        "box_low":
            signal[
                "box"
            ]["low"],

        "box_width":
            signal[
                "box"
            ]["width"],

        "cross_type":
            signal[
                "cross"
            ]["type"],

        "cross_time":
            ms_to_iso(
                signal[
                    "cross"
                ]["open_time"]
            ),

        "breakout_time":
            ms_to_iso(
                signal[
                    "breakout"
                ]["open_time"]
            ),

        "swing_price":
            signal[
                "swing"
            ]["price"],

        "volume_ratio":
            signal[
                "volume_ratio"
            ],

        "opened_at":
            now_iso(),

        "current_price":
            signal[
                "entry"
            ],

        "pnl_percent":
            0.0
    }

    state[
        "open_trade"
    ] = trade

    return True


# ============================================================
# PERFORMANCE
# ============================================================

def performance(
    history
):

    trades = len(
        history
    )

    wins = 0

    losses = 0

    breakeven = 0

    total_pnl = 0.0

    for trade in history:

        pnl = safe_float(
            trade.get(
                "final_pnl_percent",
                0
            )
        )

        total_pnl += pnl

        if pnl > 0:

            wins += 1

        elif pnl < 0:

            losses += 1

        else:

            breakeven += 1

    win_rate = (
        (
            wins
            / trades
        )
        * 100
        if trades
        else 0
    )

    return {
        "trades":
            trades,

        "wins":
            wins,

        "losses":
            losses,

        "breakeven":
            breakeven,

        "win_rate":
            win_rate,

        "total_pnl":
            total_pnl
    }


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text
):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "[WARN] TELEGRAM_BOT_TOKEN missing"
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "[WARN] TELEGRAM_CHAT_ID missing"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        + TELEGRAM_BOT_TOKEN
        + "/sendMessage"
    )

    payload = {
        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            text,

        "parse_mode":
            "Markdown"
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
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
    top_markets,
    analyses,
    state,
    history,
    closed_trade
):

    perf = performance(
        history
    )

    cross_buy = 0
    cross_sell = 0
    cross_none = 0

    box_ready = 0
    box_failed = 0

    breakout_buy = 0
    breakout_sell = 0
    breakout_none = 0

    swing_valid = 0
    swing_failed = 0

    for item in analyses:

        cross = item.get(
            "cross"
        )

        if not cross:

            cross_none += 1

        elif cross["type"] == "BUY":

            cross_buy += 1

        else:

            cross_sell += 1

        if item.get(
            "box"
        ):

            box_ready += 1

        else:

            box_failed += 1

        breakout = item.get(
            "breakout"
        )

        if not breakout:

            breakout_none += 1

        elif breakout["type"] == "BUY":

            breakout_buy += 1

        else:

            breakout_sell += 1

        if item.get(
            "swing"
        ):

            swing_valid += 1

        else:

            swing_failed += 1

    candidates = [
        item
        for item in analyses
        if item.get(
            "status"
        ) in (
            "BUY",
            "SELL"
        )
    ]

    candidates.sort(
        key=lambda x: (
            x.get(
                "volume_ratio",
                0
            ),
            x.get(
                "breakout",
                {}
            ).get(
                "open_time",
                0
            )
        ),
        reverse=True
    )

    lines = []

    lines.append(
        "📡 *BINANCE FUTURES PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 {now_iso()}"
    )

    lines.append(
        f"⏱ *{TIMEFRAME} CLOSED | TOP {TOP_SYMBOLS}*"
    )

    lines.append(
        "🤖 *TENKAN/KIJUN 9/26 + BOX 26*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # Performance
    # --------------------------------------------------------

    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        f"Trades {perf['trades']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['breakeven']}"
    )

    lines.append(
        f"🏆 WR: "
        f"{perf['win_rate']:.1f}%"
    )

    lines.append(
        f"📈 Total P&L: "
        f"{fmt_pct(perf['total_pnl'])}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # Pipeline
    # --------------------------------------------------------

    lines.append(
        "🔎 *PIPELINE*"
    )

    lines.append(
        f"Futures Universe: "
        f"{len(top_markets)}"
    )

    lines.append(
        f"Kline Valid: "
        f"{sum(
            1
            for x in analyses
            if x.get('candles', 0) > 0
        )}"
    )

    lines.append(
        f"Latest Cross: "
        f"🟢 {cross_buy} / "
        f"🔴 {cross_sell} / "
        f"⚪ {cross_none}"
    )

    lines.append(
        f"Box Ready: "
        f"{box_ready} | "
        f"Failed: {box_failed}"
    )

    lines.append(
        f"Breakout: "
        f"🟢 {breakout_buy} / "
        f"🔴 {breakout_sell} / "
        f"⚪ {breakout_none}"
    )

    lines.append(
        f"Swing: "
        f"{swing_valid} | "
        f"Failed: {swing_failed}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # Closed trade
    # --------------------------------------------------------

    if closed_trade:

        lines.append(
            "🔔 *TRADE CLOSED*"
        )

        trade = closed_trade[
            "trade"
        ]

        emoji = (
            "🟢"
            if closed_trade["pnl"] > 0
            else "🔴"
            if closed_trade["pnl"] < 0
            else "⚪"
        )

        lines.append(
            f"{emoji} "
            f"{trade['symbol']} "
            f"{closed_trade['reason']}"
        )

        lines.append(
            f"P&L: "
            f"{fmt_pct(
                closed_trade['pnl']
            )}"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # Open trade
    # --------------------------------------------------------

    open_trade_data = state.get(
        "open_trade"
    )

    if open_trade_data:

        lines.append(
            "🔴 *OPEN TRADE*"
        )

        lines.append(
            f"{open_trade_data['symbol']} "
            f"{open_trade_data['side']}"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(
                open_trade_data['entry']
            )}"
        )

        lines.append(
            f"SL: "
            f"{fmt_price(
                open_trade_data['sl']
            )}"
        )

        lines.append(
            f"TP: "
            f"{fmt_price(
                open_trade_data['tp']
            )}"
        )

        lines.append(
            f"Current: "
            f"{fmt_price(
                open_trade_data[
                    'current_price'
                ]
            )}"
        )

        lines.append(
            f"P&L: "
            f"{fmt_pct(
                open_trade_data[
                    'pnl_percent'
                ]
            )}"
        )

        lines.append(
            f"Box High: "
            f"{fmt_price(
                open_trade_data[
                    'box_high'
                ]
            )}"
        )

        lines.append(
            f"Box Low: "
            f"{fmt_price(
                open_trade_data[
                    'box_low'
                ]
            )}"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    else:

        lines.append(
            "OPEN TRADE: *NONE*"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # New signal
    # --------------------------------------------------------

    if candidates:

        signal = candidates[0]

        emoji = (
            "🟢"
            if signal["status"] == "BUY"
            else "🔴"
        )

        lines.append(
            f"{emoji} *NEW SIGNAL*"
        )

        lines.append(
            f"*{signal['symbol']}* "
            f"{signal['status']}"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(
                signal['entry']
            )}"
        )

        lines.append(
            f"SL: "
            f"{fmt_price(
                signal['sl']
            )}"
        )

        lines.append(
            f"TP: "
            f"{fmt_price(
                signal['tp']
            )}"
        )

        lines.append(
            f"RR: "
            f"{signal['rr']:.2f}"
        )

        lines.append(
            f"Box High: "
            f"{fmt_price(
                signal['box']['high']
            )}"
        )

        lines.append(
            f"Box Low: "
            f"{fmt_price(
                signal['box']['low']
            )}"
        )

        lines.append(
            f"Box Width: "
            f"{fmt_price(
                signal['box']['width']
            )}"
        )

        lines.append(
            f"Cross: "
            f"{ms_to_iso(
                signal[
                    'cross'
                ]['open_time']
            )}"
        )

        lines.append(
            f"Breakout: "
            f"{ms_to_iso(
                signal[
                    'breakout'
                ]['open_time']
            )}"
        )

        lines.append(
            f"Swing: "
            f"{fmt_price(
                signal[
                    'swing'
                ]['price']
            )}"
        )

        lines.append(
            f"Volume Ratio: "
            f"{signal[
                'volume_ratio'
            ]:.2f}x"
        )

    else:

        lines.append(
            "⚪ *NO VALID SIGNAL*"
        )

        reason_counts = {}

        for item in analyses:

            reason = item.get(
                "reason",
                "UNKNOWN"
            )

            reason_counts[
                reason
            ] = (
                reason_counts.get(
                    reason,
                    0
                )
                + 1
            )

        sorted_reasons = sorted(
            reason_counts.items(),
            key=lambda x:
            x[1],
            reverse=True
        )

        if sorted_reasons:

            lines.append(
                "Main reasons:"
            )

            for reason, count in (
                sorted_reasons[:6]
            ):

                lines.append(
                    f"• {reason}: {count}"
                )

    # --------------------------------------------------------
    # Diagnostics
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔍 *TOP DIAGNOSTICS*"
    )

    diagnostic = sorted(
        analyses,
        key=lambda x:
        x.get(
            "quote_volume",
            0
        ),
        reverse=True
    )

    for item in diagnostic[:10]:

        symbol = item.get(
            "symbol",
            "?"
        )

        status = item.get(
            "status",
            "?"
        )

        reason = item.get(
            "reason",
            ""
        )

        if status in (
            "BUY",
            "SELL"
        ):

            lines.append(
                f"• {symbol} → "
                f"{status} | "
                f"Vol "
                f"{item.get(
                    'volume_ratio',
                    0
                ):.2f}x"
            )

        else:

            lines.append(
                f"• {symbol} → "
                f"{reason}"
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
        "BINANCE USD-M FUTURES "
        "PRICE ACTION SCANNER"
    )

    print("=" * 70)

    print(
        f"Timeframe: {TIMEFRAME}"
    )

    print(
        f"TOP: {TOP_SYMBOLS}"
    )

    print(
        f"Box: {BOX_SIZE}"
    )

    print(
        "Exchange: Binance USD-M Futures"
    )

    print(
        "Max Open Trades: 1"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # State
    # --------------------------------------------------------

    state = load_state()

    history = load_history()

    # --------------------------------------------------------
    # Check existing trade
    # --------------------------------------------------------

    closed_trade = check_open_trade(
        state,
        history
    )

    if closed_trade:

        print(
            "[TRADE CLOSED] "
            f"{closed_trade['reason']} "
            f"P&L="
            f"{closed_trade['pnl']:.2f}%"
        )

        save_history(
            history
        )

    # --------------------------------------------------------
    # Exchange symbols
    # --------------------------------------------------------

    print(
        "[INFO] Loading Binance Futures symbols..."
    )

    trading_symbols = (
        get_trading_symbols()
    )

    print(
        f"[INFO] Trading perpetual "
        f"USDT symbols: "
        f"{len(trading_symbols)}"
    )

    if not trading_symbols:

        print(
            "[ERROR] Binance Futures "
            "returned no trading symbols."
        )

        telegram_send(
            "❌ *SCANNER ERROR*\n"
            "Binance Futures returned "
            "no trading symbols."
        )

        save_state(
            state
        )

        return

    # --------------------------------------------------------
    # TOP 30
    # --------------------------------------------------------

    print(
        "[INFO] Ranking markets by "
        "24h quote volume..."
    )

    top_markets = get_top_symbols(
        trading_symbols
    )

    if not top_markets:

        print(
            "[ERROR] No markets after "
            "Binance volume ranking."
        )

        telegram_send(
            "❌ *SCANNER ERROR*\n"
            "Binance Futures 24h ticker "
            "returned no valid markets."
        )

        save_state(
            state
        )

        return

    print(
        f"[INFO] TOP {len(top_markets)}:"
    )

    for index, market in enumerate(
        top_markets,
        start=1
    ):

        print(
            f"{index:02d}. "
            f"{market['symbol']} | "
            f"24h Quote Vol="
            f"{market['quote_volume']:,.0f}"
        )

    # --------------------------------------------------------
    # Analyze
    # --------------------------------------------------------

    analyses = []

    for market in top_markets:

        symbol = market[
            "symbol"
        ]

        print(
            f"[KLINE] {symbol}"
        )

        candles = get_klines(
            symbol
        )

        if not candles:

            result = {

                "symbol":
                    symbol,

                "status":
                    "NO SIGNAL",

                "reason":
                    "BAD BINANCE KLINE",

                "candles":
                    0,

                "quote_volume":
                    market[
                        "quote_volume"
                    ],

                "cross":
                    None,

                "box":
                    None,

                "breakout":
                    None,

                "swing":
                    None,

                "volume_ratio":
                    0
            }

            analyses.append(
                result
            )

            print(
                f"[BAD KLINE] "
                f"{symbol}"
            )

            continue

        result = analyze_symbol(
            symbol,
            candles,
            market
        )

        analyses.append(
            result
        )

        print(
            f"[RESULT] "
            f"{symbol} | "
            f"{result['status']} | "
            f"{result['reason']}"
        )

        if result["status"] in (
            "BUY",
            "SELL"
        ):

            print(
                f"         Entry="
                f"{fmt_price(
                    result['entry']
                )} "
                f"SL="
                f"{fmt_price(
                    result['sl']
                )} "
                f"TP="
                f"{fmt_price(
                    result['tp']
                )}"
            )

    # --------------------------------------------------------
    # Candidates
    # --------------------------------------------------------

    candidates = [
        item
        for item in analyses
        if item.get(
            "status"
        ) in (
            "BUY",
            "SELL"
        )
    ]

    candidates.sort(
        key=lambda x: (
            x.get(
                "volume_ratio",
                0
            ),
            x.get(
                "quote_volume",
                0
            ),
            x.get(
                "breakout",
                {}
            ).get(
                "open_time",
                0
            )
        ),
        reverse=True
    )

    # --------------------------------------------------------
    # MAX 1 OPEN TRADE
    # --------------------------------------------------------

    if (
        not state.get(
            "open_trade"
        )
        and candidates
    ):

        best_signal = candidates[
            0
        ]

        opened = open_trade(
            state,
            best_signal
        )

        if opened:

            current_price = (
                get_current_price(
                    best_signal[
                        "symbol"
                    ]
                )
            )

            if current_price > 0:

                state[
                    "open_trade"
                ][
                    "current_price"
                ] = current_price

                state[
                    "open_trade"
                ][
                    "pnl_percent"
                ] = calculate_pnl(
                    state[
                        "open_trade"
                    ],
                    current_price
                )

            print(
                "[NEW TRADE] "
                f"{best_signal['symbol']} "
                f"{best_signal['status']}"
            )

    elif state.get(
        "open_trade"
    ):

        print(
            "[INFO] Existing trade "
            "is open."
        )

        print(
            "[INFO] No new trade "
            "will be opened."
        )

    else:

        print(
            "[INFO] No valid signal."
        )

    # --------------------------------------------------------
    # Save state
    # --------------------------------------------------------

    save_history(
        history
    )

    save_state(
        state
    )

    # --------------------------------------------------------
    # Telegram
    # --------------------------------------------------------

    report = build_report(
        top_markets=top_markets,
        analyses=analyses,
        state=state,
        history=history,
        closed_trade=closed_trade
    )

    print("=" * 70)

    print(
        report
    )

    print("=" * 70)

    telegram_send(
        report
    )

    print(
        "[DONE]"
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    main()
