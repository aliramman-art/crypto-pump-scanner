# ============================================================
# LBANK FUTURES PRICE ACTION SCANNER v4.1
# ============================================================
# LBank Futures
#
# 5m CLOSED CANDLES
#
# STRATEGY
# ------------------------------------------------------------
# 1) Get Futures markets directly from Futures marketData
# 2) Rank markets by Futures turnover / volume
# 3) Select TOP 30
# 4) Fetch 5m candles only for TOP 30
# 5) Find ONLY the MOST RECENT Tenkan/Kijun crossover
# 6) Ignore all older crossovers
# 7) Cross candle is excluded from Box
# 8) Box = 26 candles immediately BEFORE latest cross
# 9) BUY  = later CLOSED candle closes above Box High
# 10) SELL = later CLOSED candle closes below Box Low
# 11) Swing = confirmed pivot with 2 candles left + 2 candles right
# 12) BUY SL = latest confirmed Swing Low - buffer
# 13) SELL SL = latest confirmed Swing High + buffer
# 14) TP = 50% of Box width
# 15) Maximum 1 open trade
#
# IMPORTANT
# ------------------------------------------------------------
# Volume is used for MARKET RANKING.
# Volume Ratio is used as a diagnostic / tie-breaker.
#
# If NO SIGNAL:
# Telegram reports exactly where markets stopped:
#   NO CROSS
#   NO BOX
#   NO BREAKOUT
#   NO SWING
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

FUTURES_BASE = "https://lbkperp.lbank.com/cfd/openApi/v1/pub"
SPOT_BASE = "https://api.lbank.info"

PRODUCT_GROUP = "SwapU"

TIMEFRAME = "minute5"

TOP_LIQUID = 80
TOP_VOLUME = 30

CANDLE_LIMIT = 200

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

BOX_SIZE = 26

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

VOLUME_LOOKBACK = 20

TP_BOX_PERCENT = 0.50

SL_BUFFER_PERCENT = 0.0015

MAX_OPEN_TRADES = 1

REQUEST_TIMEOUT = 20

STATE_FILE = "lbank_futures_price_action_state.json"
HISTORY_FILE = "lbank_futures_price_action_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
})


# ============================================================
# BASIC HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_text():
    return now_utc().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(value, default=0.0):

    try:

        if value is None:
            return default

        if isinstance(value, bool):
            return default

        return float(value)

    except Exception:

        return default


def normalize_symbol(symbol):

    if symbol is None:
        return ""

    s = str(symbol).strip().upper()

    s = s.replace("-", "")
    s = s.replace("/", "")
    s = s.replace("_", "")

    return s


def futures_to_spot_symbol(symbol):

    s = normalize_symbol(symbol)

    if s.endswith("USDT"):

        base = s[:-4]

        return (
            f"{base.lower()}_usdt"
        )

    return s.lower()


def is_usdt_symbol(symbol):

    s = normalize_symbol(symbol)

    return (
        s.endswith("USDT")
        and len(s) > 4
        and s != "USDTUSDT"
    )


# ============================================================
# HTTP GET
# ============================================================

def http_get(
    url,
    params=None
):

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as exc:

        print(
            f"[HTTP ERROR] "
            f"{url} "
            f"params={params} "
            f"error={exc}"
        )

        return None


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[TELEGRAM] "
            "Token/chat id not configured."
        )

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        return True

    except Exception as exc:

        print(
            f"[TELEGRAM ERROR] {exc}"
        )

        return False


# ============================================================
# JSON STATE
# ============================================================

def load_json(
    path,
    default
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

    except Exception as exc:

        print(
            f"[FILE ERROR] "
            f"Could not load {path}: {exc}"
        )

        return default


def save_json(
    path,
    data
):

    try:

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

    except Exception as exc:

        print(
            f"[FILE ERROR] "
            f"Could not save {path}: {exc}"
        )


# ============================================================
# FUTURES MARKET DATA
# ============================================================

def get_futures_market_data():

    url = (
        f"{FUTURES_BASE}"
        f"/marketData"
    )

    params = {
        "productGroup": PRODUCT_GROUP
    }

    data = http_get(
        url,
        params
    )

    if data is None:

        return []

    print(
        "[DEBUG] Futures marketData response:",
        type(data).__name__
    )

    if isinstance(data, dict):

        if (
            str(
                data.get("result")
            ).lower()
            == "false"
        ):

            print(
                "[FUTURES API ERROR]",
                data
            )

            return []

        rows = data.get("data")

        if rows is None:
            rows = data.get(
                "marketData"
            )

        if rows is None:
            rows = data.get(
                "result"
            )

        if isinstance(rows, list):

            return rows

        if isinstance(rows, dict):

            for key in (
                "data",
                "marketData",
                "list",
                "rows"
            ):

                value = rows.get(key)

                if isinstance(
                    value,
                    list
                ):

                    return value

    if isinstance(data, list):

        return data

    return []


# ============================================================
# MARKET VOLUME
# ============================================================

def market_volume(row):

    if not isinstance(
        row,
        dict
    ):

        return 0.0

    candidates = [
        "turnover",
        "turnover24h",
        "quoteVolume",
        "quote_volume",
        "volume",
        "vol",
    ]

    for key in candidates:

        value = safe_float(
            row.get(key),
            0.0
        )

        if value > 0:

            return value

    return 0.0


def market_last_price(row):

    if not isinstance(
        row,
        dict
    ):

        return 0.0

    candidates = [
        "lastPrice",
        "last_price",
        "price",
        "close",
        "markPrice",
        "markedPrice",
    ]

    for key in candidates:

        value = safe_float(
            row.get(key),
            0.0
        )

        if value > 0:

            return value

    return 0.0


# ============================================================
# BUILD FUTURES UNIVERSE
# ============================================================

def build_liquid_universe():

    rows = get_futures_market_data()

    if not rows:

        print(
            "[ERROR] "
            "Futures marketData returned no rows."
        )

        return []

    markets = []

    seen = set()

    for row in rows:

        if not isinstance(
            row,
            dict
        ):

            continue

        symbol = (
            row.get("symbol")
            or row.get("pair")
            or row.get("contract")
            or row.get("instrument")
            or row.get("productCode")
        )

        symbol = normalize_symbol(
            symbol
        )

        if not symbol:
            continue

        if not is_usdt_symbol(
            symbol
        ):

            continue

        if symbol in seen:
            continue

        price = market_last_price(
            row
        )

        volume = market_volume(
            row
        )

        if price <= 0:
            continue

        if volume <= 0:
            continue

        seen.add(symbol)

        markets.append({
            "symbol": symbol,
            "price": price,
            "volume": volume,
            "raw": row,
        })

    markets.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    selected = markets[
        :TOP_LIQUID
    ]

    print(
        f"[INFO] "
        f"Futures markets discovered: "
        f"{len(markets)}"
    )

    print(
        f"[INFO] "
        f"TOP liquid universe: "
        f"{len(selected)}"
    )

    return selected


# ============================================================
# KLINE PARSER
# ============================================================

def parse_kline_rows(data):

    if data is None:
        return []

    rows = None

    if isinstance(
        data,
        list
    ):

        rows = data

    elif isinstance(
        data,
        dict
    ):

        if (
            str(
                data.get("result")
            ).lower()
            == "false"
        ):

            print(
                "[KLINE API ERROR]",
                data
            )

            return []

        for key in (
            "data",
            "kline",
            "klines",
            "result",
            "rows"
        ):

            value = data.get(
                key
            )

            if isinstance(
                value,
                list
            ):

                rows = value
                break

    if not rows:
        return []

    candles = []

    for row in rows:

        if isinstance(
            row,
            dict
        ):

            timestamp = (
                row.get("timestamp")
                or row.get("time")
                or row.get("ts")
            )

            open_price = (
                row.get("open")
                or row.get("Open")
            )

            high_price = (
                row.get("high")
                or row.get("High")
            )

            low_price = (
                row.get("low")
                or row.get("Low")
            )

            close_price = (
                row.get("close")
                or row.get("Close")
            )

            volume = (
                row.get("volume")
                or row.get(
                    "Trading Volume"
                )
                or row.get("vol")
            )

        elif isinstance(
            row,
            list
        ):

            if len(row) < 6:
                continue

            timestamp = row[0]
            open_price = row[1]
            high_price = row[2]
            low_price = row[3]
            close_price = row[4]
            volume = row[5]

        else:

            continue

        ts = safe_float(
            timestamp,
            0
        )

        if ts <= 0:
            continue

        if ts > 10_000_000_000:

            ts = ts / 1000.0

        o = safe_float(
            open_price,
            0
        )

        h = safe_float(
            high_price,
            0
        )

        l = safe_float(
            low_price,
            0
        )

        c = safe_float(
            close_price,
            0
        )

        v = safe_float(
            volume,
            0
        )

        if (
            o <= 0
            or h <= 0
            or l <= 0
            or c <= 0
        ):

            continue

        candles.append({
            "timestamp": int(ts),
            "open": o,
            "high": h,
            "low": l,
            "close": c,
            "volume": max(
                v,
                0.0
            ),
        })

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    unique = {}

    for candle in candles:

        unique[
            candle["timestamp"]
        ] = candle

    candles = list(
        unique.values()
    )

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    return candles


# ============================================================
# GET 5M CANDLES
# ============================================================

def get_5m_candles(
    symbol,
    limit=CANDLE_LIMIT
):

    spot_symbol = futures_to_spot_symbol(
        symbol
    )

    url = (
        f"{SPOT_BASE}"
        f"/v2/kline.do"
    )

    params = {
        "symbol": spot_symbol,
        "size": limit,
        "type": TIMEFRAME,
    }

    data = http_get(
        url,
        params
    )

    candles = parse_kline_rows(
        data
    )

    if candles:

        print(
            f"[KLINE] "
            f"{symbol} "
            f"({spot_symbol}) "
            f"=> {len(candles)}"
        )

        return candles

    # --------------------------------------------------------
    # Alternative symbol formats
    # --------------------------------------------------------

    alternatives = [
        symbol.lower(),
        symbol.upper(),
    ]

    for alt in alternatives:

        if alt == spot_symbol:
            continue

        alt_params = {
            "symbol": alt,
            "size": limit,
            "type": TIMEFRAME,
        }

        alt_data = http_get(
            url,
            alt_params
        )

        alt_candles = parse_kline_rows(
            alt_data
        )

        if alt_candles:

            print(
                f"[KLINE] "
                f"{symbol} "
                f"alternative={alt} "
                f"=> {len(alt_candles)}"
            )

            return alt_candles

    print(
        f"[KLINE EMPTY] "
        f"{symbol} "
        f"spot={spot_symbol}"
    )

    return []


# ============================================================
# CLOSED CANDLES
# ============================================================

def get_closed_candles(
    candles
):

    if not candles:
        return []

    current_time = int(
        time.time()
    )

    closed = []

    for candle in candles:

        if (
            candle["timestamp"]
            + 300
            <= current_time
        ):

            closed.append(
                candle
            )

    closed.sort(
        key=lambda x: x["timestamp"]
    )

    return closed


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(
    candles,
    index
):

    start = (
        index
        - TENKAN_PERIOD
        + 1
    )

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest
        + lowest
    ) / 2.0


# ============================================================
# KIJUN
# ============================================================

def calculate_kijun(
    candles,
    index
):

    start = (
        index
        - KIJUN_PERIOD
        + 1
    )

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest
        + lowest
    ) / 2.0


# ============================================================
# ICHIMOKU VALUES
# ============================================================

def build_ichimoku_values(
    candles
):

    values = []

    for i in range(
        len(candles)
    ):

        values.append({
            "tenkan": calculate_tenkan(
                candles,
                i
            ),
            "kijun": calculate_kijun(
                candles,
                i
            ),
        })

    return values


# ============================================================
# MOST RECENT CROSS
# ============================================================

def find_latest_cross(
    candles
):

    if len(candles) < (
        KIJUN_PERIOD + 2
    ):

        return None

    ichimoku = (
        build_ichimoku_values(
            candles
        )
    )

    latest_cross = None

    for i in range(
        1,
        len(candles)
    ):

        previous = (
            ichimoku[i - 1]
        )

        current = (
            ichimoku[i]
        )

        if (
            previous["tenkan"]
            is None
            or previous["kijun"]
            is None
            or current["tenkan"]
            is None
            or current["kijun"]
            is None
        ):

            continue

        previous_diff = (
            previous["tenkan"]
            - previous["kijun"]
        )

        current_diff = (
            current["tenkan"]
            - current["kijun"]
        )

        cross_type = None

        if (
            previous_diff <= 0
            and current_diff > 0
        ):

            cross_type = "BUY"

        elif (
            previous_diff >= 0
            and current_diff < 0
        ):

            cross_type = "SELL"

        if cross_type:

            latest_cross = {
                "index": i,
                "type": cross_type,
                "timestamp": candles[i][
                    "timestamp"
                ],
                "price": candles[i][
                    "close"
                ],
                "tenkan": current[
                    "tenkan"
                ],
                "kijun": current[
                    "kijun"
                ],
            }

    return latest_cross


# ============================================================
# BOX
# ============================================================

def build_box(
    candles,
    cross_index
):

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

    box_high = max(
        c["high"]
        for c in box_candles
    )

    box_low = min(
        c["low"]
        for c in box_candles
    )

    width = (
        box_high
        - box_low
    )

    if width <= 0:
        return None

    return {
        "start_index": start,
        "end_index": end - 1,
        "high": box_high,
        "low": box_low,
        "width": width,
    }


# ============================================================
# SWING LOW
# ============================================================

def is_swing_low(
    candles,
    index
):

    if (
        index - PIVOT_LEFT < 0
        or
        index + PIVOT_RIGHT
        >= len(candles)
    ):

        return False

    current = candles[index][
        "low"
    ]

    for i in range(
        index - PIVOT_LEFT,
        index
    ):

        if (
            candles[i]["low"]
            <= current
        ):

            return False

    for i in range(
        index + 1,
        index + PIVOT_RIGHT + 1
    ):

        if (
            candles[i]["low"]
            <= current
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

    if (
        index - PIVOT_LEFT < 0
        or
        index + PIVOT_RIGHT
        >= len(candles)
    ):

        return False

    current = candles[index][
        "high"
    ]

    for i in range(
        index - PIVOT_LEFT,
        index
    ):

        if (
            candles[i]["high"]
            >= current
        ):

            return False

    for i in range(
        index + 1,
        index + PIVOT_RIGHT + 1
    ):

        if (
            candles[i]["high"]
            >= current
        ):

            return False

    return True


# ============================================================
# LATEST SWING LOW
# ============================================================

def find_latest_swing_low(
    candles,
    start_index,
    end_index
):

    max_confirmed = (
        len(candles)
        - PIVOT_RIGHT
        - 1
    )

    end_index = min(
        end_index,
        max_confirmed
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
                "price": candles[i][
                    "low"
                ],
                "timestamp": candles[i][
                    "timestamp"
                ],
            }

    return None


# ============================================================
# LATEST SWING HIGH
# ============================================================

def find_latest_swing_high(
    candles,
    start_index,
    end_index
):

    max_confirmed = (
        len(candles)
        - PIVOT_RIGHT
        - 1
    )

    end_index = min(
        end_index,
        max_confirmed
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
                "price": candles[i][
                    "high"
                ],
                "timestamp": candles[i][
                    "timestamp"
                ],
            }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles,
    index
):

    if index < VOLUME_LOOKBACK:
        return 0.0

    current_volume = candles[
        index
    ]["volume"]

    previous = [
        candles[i]["volume"]
        for i in range(
            index - VOLUME_LOOKBACK,
            index
        )
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
# DISTANCE TO BOX
# ============================================================

def breakout_distance(
    close,
    box
):

    if not box:
        return None

    width = box["width"]

    if width <= 0:
        return None

    if close > box["high"]:

        return {
            "direction": "BUY",
            "distance_percent": 0.0,
            "broken": True,
        }

    if close < box["low"]:

        return {
            "direction": "SELL",
            "distance_percent": 0.0,
            "broken": True,
        }

    buy_distance = (
        (box["high"] - close)
        / box["high"]
    ) * 100.0

    sell_distance = (
        (close - box["low"])
        / box["low"]
    ) * 100.0

    if buy_distance <= sell_distance:

        return {
            "direction": "BUY",
            "distance_percent": -buy_distance,
            "broken": False,
        }

    return {
        "direction": "SELL",
        "distance_percent": -sell_distance,
        "broken": False,
    }


# ============================================================
# FIND BREAKOUT
# ============================================================

def find_latest_breakout(
    candles,
    cross,
    box
):

    if not cross or not box:
        return None

    cross_index = cross[
        "index"
    ]

    if (
        cross_index + 1
        >= len(candles)
    ):

        return None

    latest_breakout = None

    for i in range(
        cross_index + 1,
        len(candles)
    ):

        candle = candles[i]

        close = candle[
            "close"
        ]

        direction = None

        if close > box["high"]:

            direction = "BUY"

        elif close < box["low"]:

            direction = "SELL"

        if direction:

            latest_breakout = {
                "index": i,
                "type": direction,
                "price": close,
                "timestamp": candle[
                    "timestamp"
                ],
                "volume_ratio": (
                    calculate_volume_ratio(
                        candles,
                        i
                    )
                ),
            }

    return latest_breakout


# ============================================================
# BUILD TRADE
# ============================================================

def build_trade(
    symbol,
    candles,
    cross,
    box,
    breakout,
    market_volume
):

    if not breakout:
        return None

    direction = breakout[
        "type"
    ]

    breakout_index = breakout[
        "index"
    ]

    swing_start = (
        cross["index"] + 1
    )

    swing_end = (
        breakout_index - 1
    )

    if swing_end < swing_start:

        return None

    if direction == "BUY":

        swing = find_latest_swing_low(
            candles,
            swing_start,
            swing_end
        )

        if not swing:
            return None

        entry = breakout[
            "price"
        ]

        sl = (
            swing["price"]
            * (
                1.0
                - SL_BUFFER_PERCENT
            )
        )

        risk = (
            entry - sl
        )

        if risk <= 0:
            return None

        tp = (
            entry
            + (
                box["width"]
                * TP_BOX_PERCENT
            )
        )

        if tp <= entry:
            return None

    else:

        swing = find_latest_swing_high(
            candles,
            swing_start,
            swing_end
        )

        if not swing:
            return None

        entry = breakout[
            "price"
        ]

        sl = (
            swing["price"]
            * (
                1.0
                + SL_BUFFER_PERCENT
            )
        )

        risk = (
            sl - entry
        )

        if risk <= 0:
            return None

        tp = (
            entry
            - (
                box["width"]
                * TP_BOX_PERCENT
            )
        )

        if tp >= entry:
            return None

    return {
        "symbol": symbol,
        "direction": direction,

        "entry": entry,
        "sl": sl,
        "tp": tp,

        "risk": risk,

        "box_high": box[
            "high"
        ],

        "box_low": box[
            "low"
        ],

        "box_width": box[
            "width"
        ],

        "cross_index": cross[
            "index"
        ],

        "cross_price": cross[
            "price"
        ],

        "cross_timestamp": cross[
            "timestamp"
        ],

        "breakout_index": breakout[
            "index"
        ],

        "breakout_price": breakout[
            "price"
        ],

        "breakout_timestamp": breakout[
            "timestamp"
        ],

        "swing_index": swing[
            "index"
        ],

        "swing_price": swing[
            "price"
        ],

        "swing_timestamp": swing[
            "timestamp"
        ],

        "volume": market_volume,

        "volume_ratio": breakout[
            "volume_ratio"
        ],

        "opened_at": (
            now_utc().isoformat()
        ),

        "status": "OPEN",
    }


# ============================================================
# DIAGNOSTIC RECORD
# ============================================================

def create_diagnostic(
    symbol,
    volume
):

    return {
        "symbol": symbol,
        "volume": volume,

        "stage": "START",

        "kline_valid": False,

        "cross": None,
        "cross_type": None,

        "box_ready": False,

        "breakout": None,
        "breakout_type": None,

        "swing_valid": False,

        "trade_valid": False,

        "box_high": None,
        "box_low": None,

        "current_price": None,

        "distance_direction": None,
        "distance_percent": None,

        "reason": "NOT_SCANNED",
    }


# ============================================================
# SCAN MARKET WITH DIAGNOSTIC
# ============================================================

def scan_market(
    market
):

    symbol = market[
        "symbol"
    ]

    diagnostic = create_diagnostic(
        symbol,
        market["volume"]
    )

    candles = get_5m_candles(
        symbol,
        CANDLE_LIMIT
    )

    if len(candles) < 60:

        diagnostic[
            "reason"
        ] = "INSUFFICIENT_KLINE"

        diagnostic[
            "stage"
        ] = "KLINE"

        return None, diagnostic

    candles = get_closed_candles(
        candles
    )

    if len(candles) < 60:

        diagnostic[
            "reason"
        ] = "INSUFFICIENT_CLOSED_CANDLES"

        diagnostic[
            "stage"
        ] = "CLOSED_KLINE"

        return None, diagnostic

    diagnostic[
        "kline_valid"
    ] = True

    # --------------------------------------------------------
    # LATEST CROSS
    # --------------------------------------------------------

    cross = find_latest_cross(
        candles
    )

    if not cross:

        diagnostic[
            "reason"
        ] = "NO_LATEST_CROSS"

        diagnostic[
            "stage"
        ] = "CROSS"

        return None, diagnostic

    diagnostic[
        "cross"
    ] = cross

    diagnostic[
        "cross_type"
    ] = cross["type"]

    # --------------------------------------------------------
    # BOX
    # --------------------------------------------------------

    box = build_box(
        candles,
        cross["index"]
    )

    if not box:

        diagnostic[
            "reason"
        ] = "BOX_NOT_READY"

        diagnostic[
            "stage"
        ] = "BOX"

        return None, diagnostic

    diagnostic[
        "box_ready"
    ] = True

    diagnostic[
        "box_high"
    ] = box["high"]

    diagnostic[
        "box_low"
    ] = box["low"]

    # --------------------------------------------------------
    # CURRENT PRICE
    # --------------------------------------------------------

    current_price = candles[
        -1
    ]["close"]

    diagnostic[
        "current_price"
    ] = current_price

    distance = breakout_distance(
        current_price,
        box
    )

    if distance:

        diagnostic[
            "distance_direction"
        ] = distance[
            "direction"
        ]

        diagnostic[
            "distance_percent"
        ] = distance[
            "distance_percent"
        ]

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    breakout = find_latest_breakout(
        candles,
        cross,
        box
    )

    if not breakout:

        diagnostic[
            "reason"
        ] = "NO_BREAKOUT"

        diagnostic[
            "stage"
        ] = "BREAKOUT"

        return None, diagnostic

    diagnostic[
        "breakout"
    ] = breakout

    diagnostic[
        "breakout_type"
    ] = breakout["type"]

    # --------------------------------------------------------
    # SWING
    # --------------------------------------------------------

    swing_start = (
        cross["index"] + 1
    )

    swing_end = (
        breakout["index"] - 1
    )

    if (
        swing_end
        < swing_start
    ):

        diagnostic[
            "reason"
        ] = (
            "BREAKOUT_TOO_EARLY_"
            "FOR_CONFIRMED_SWING"
        )

        diagnostic[
            "stage"
        ] = "SWING"

        return None, diagnostic

    if breakout["type"] == "BUY":

        swing = find_latest_swing_low(
            candles,
            swing_start,
            swing_end
        )

    else:

        swing = find_latest_swing_high(
            candles,
            swing_start,
            swing_end
        )

    if not swing:

        diagnostic[
            "reason"
        ] = "NO_CONFIRMED_SWING"

        diagnostic[
            "stage"
        ] = "SWING"

        return None, diagnostic

    diagnostic[
        "swing_valid"
    ] = True

    # --------------------------------------------------------
    # TRADE
    # --------------------------------------------------------

    trade = build_trade(
        symbol,
        candles,
        cross,
        box,
        breakout,
        market["volume"]
    )

    if not trade:

        diagnostic[
            "reason"
        ] = "TRADE_BUILD_FAILED"

        diagnostic[
            "stage"
        ] = "TRADE"

        return None, diagnostic

    diagnostic[
        "trade_valid"
    ] = True

    diagnostic[
        "stage"
    ] = "VALID"

    diagnostic[
        "reason"
    ] = "VALID_SIGNAL"

    trade[
        "current_price"
    ] = current_price

    trade[
        "live_pnl"
    ] = calculate_pnl_percent(
        trade,
        current_price
    )

    return trade, diagnostic


# ============================================================
# PNL
# ============================================================

def calculate_pnl_percent(
    trade,
    current_price
):

    if not trade:
        return 0.0

    entry = safe_float(
        trade.get(
            "entry"
        ),
        0
    )

    if entry <= 0:
        return 0.0

    if (
        trade.get(
            "direction"
        )
        == "BUY"
    ):

        return (
            (
                current_price
                - entry
            )
            / entry
        ) * 100.0

    return (
        (
            entry
            - current_price
        )
        / entry
    ) * 100.0


# ============================================================
# CURRENT TRADE EXIT
# ============================================================

def check_trade_exit(
    trade,
    current_price
):

    if not trade:
        return None

    direction = trade[
        "direction"
    ]

    sl = trade[
        "sl"
    ]

    tp = trade[
        "tp"
    ]

    if direction == "BUY":

        if current_price <= sl:
            return "SL"

        if current_price >= tp:
            return "TP"

    else:

        if current_price >= sl:
            return "SL"

        if current_price <= tp:
            return "TP"

    return None


# ============================================================
# HISTORY
# ============================================================

def add_history_trade(
    history,
    trade,
    exit_reason,
    exit_price
):

    result = (
        "WIN"
        if exit_reason == "TP"
        else "LOSS"
    )

    pnl = calculate_pnl_percent(
        trade,
        exit_price
    )

    item = dict(
        trade
    )

    item[
        "exit_reason"
    ] = exit_reason

    item[
        "exit_price"
    ] = exit_price

    item[
        "pnl_percent"
    ] = pnl

    item[
        "result"
    ] = result

    item[
        "closed_at"
    ] = now_utc().isoformat()

    history.append(
        item
    )

    return item


def performance_summary(
    history
):

    trades = len(
        history
    )

    wins = sum(
        1
        for x in history
        if x.get("result")
        == "WIN"
    )

    losses = sum(
        1
        for x in history
        if x.get("result")
        == "LOSS"
    )

    breakeven = sum(
        1
        for x in history
        if x.get("result")
        == "BREAKEVEN"
    )

    win_rate = (
        (
            wins
            / trades
        ) * 100.0
        if trades > 0
        else 0.0
    )

    total_pnl = sum(
        safe_float(
            x.get(
                "pnl_percent"
            ),
            0
        )
        for x in history
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


# ============================================================
# FORMAT
# ============================================================

def fmt_price(value):

    value = safe_float(
        value,
        0
    )

    if value == 0:
        return "0"

    if abs(value) >= 1000:

        return f"{value:,.2f}"

    if abs(value) >= 1:

        return (
            f"{value:.6f}"
            .rstrip("0")
            .rstrip(".")
        )

    if abs(value) >= 0.01:

        return (
            f"{value:.6f}"
            .rstrip("0")
            .rstrip(".")
        )

    if abs(value) >= 0.0001:

        return (
            f"{value:.8f}"
            .rstrip("0")
            .rstrip(".")
        )

    return (
        f"{value:.10f}"
        .rstrip("0")
        .rstrip(".")
    )


def fmt_volume(value):

    value = safe_float(
        value,
        0
    )

    if value >= 1_000_000_000:

        return (
            f"{value / 1_000_000_000:.2f}B"
        )

    if value >= 1_000_000:

        return (
            f"{value / 1_000_000:.2f}M"
        )

    if value >= 1_000:

        return (
            f"{value / 1_000:.2f}K"
        )

    return f"{value:.2f}"


def fmt_time(timestamp):

    try:

        return datetime.fromtimestamp(
            timestamp,
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

    except Exception:

        return "-"


# ============================================================
# DIAGNOSTIC SUMMARY
# ============================================================

def build_diagnostic_summary(
    diagnostics
):

    summary = {
        "total": len(
            diagnostics
        ),

        "kline_valid": 0,

        "cross_buy": 0,
        "cross_sell": 0,
        "cross_none": 0,

        "box_ready": 0,
        "box_failed": 0,

        "breakout_buy": 0,
        "breakout_sell": 0,
        "breakout_none": 0,

        "swing_valid": 0,
        "swing_failed": 0,

        "trade_valid": 0,

        "reasons": {},
    }

    for d in diagnostics:

        if d[
            "kline_valid"
        ]:

            summary[
                "kline_valid"
            ] += 1

        cross_type = d.get(
            "cross_type"
        )

        if cross_type == "BUY":

            summary[
                "cross_buy"
            ] += 1

        elif cross_type == "SELL":

            summary[
                "cross_sell"
            ] += 1

        else:

            summary[
                "cross_none"
            ] += 1

        if d[
            "box_ready"
        ]:

            summary[
                "box_ready"
            ] += 1

        else:

            summary[
                "box_failed"
            ] += 1

        breakout_type = d.get(
            "breakout_type"
        )

        if breakout_type == "BUY":

            summary[
                "breakout_buy"
            ] += 1

        elif breakout_type == "SELL":

            summary[
                "breakout_sell"
            ] += 1

        else:

            summary[
                "breakout_none"
            ] += 1

        if d[
            "swing_valid"
        ]:

            summary[
                "swing_valid"
            ] += 1

        else:

            if d.get(
                "breakout_type"
            ):

                summary[
                    "swing_failed"
                ] += 1

        if d[
            "trade_valid"
        ]:

            summary[
                "trade_valid"
            ] += 1

        reason = d.get(
            "reason",
            "UNKNOWN"
        )

        summary[
            "reasons"
        ][reason] = (
            summary[
                "reasons"
            ].get(
                reason,
                0
            ) + 1
        )

    return summary


# ============================================================
# NEAREST BREAKOUTS
# ============================================================

def nearest_setups(
    diagnostics,
    count=3
):

    candidates = []

    for d in diagnostics:

        if not d[
            "box_ready"
        ]:

            continue

        if not d[
            "kline_valid"
        ]:

            continue

        distance = d.get(
            "distance_percent"
        )

        direction = d.get(
            "distance_direction"
        )

        current = d.get(
            "current_price"
        )

        box_high = d.get(
            "box_high"
        )

        box_low = d.get(
            "box_low"
        )

        if (
            distance is None
            or direction is None
        ):

            continue

        candidates.append({
            "symbol": d[
                "symbol"
            ],

            "direction": direction,

            "distance_percent": (
                distance
            ),

            "current_price": (
                current
            ),

            "box_high": box_high,

            "box_low": box_low,

            "cross_type": d.get(
                "cross_type"
            ),

            "breakout": bool(
                d.get(
                    "breakout_type"
                )
            ),
        })

    candidates.sort(
        key=lambda x: abs(
            x["distance_percent"]
        )
    )

    return candidates[
        :count
    ]


# ============================================================
# MAIN TELEGRAM REPORT
# ============================================================

def build_report(
    state,
    history,
    diagnostics,
    candidates
):

    performance = (
        performance_summary(
            history
        )
    )

    summary = (
        build_diagnostic_summary(
            diagnostics
        )
    )

    lines = []

    lines.append(
        "📡 *LBANK FUTURES PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 {now_text()}"
    )

    lines.append(
        f"⏱ *5m CLOSED | TOP {TOP_VOLUME}*"
    )

    lines.append(
        "🤖 *TENKAN/KIJUN 26 BOX*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        f"Trades {performance['trades']} | "
        f"🟢 {performance['wins']} | "
        f"🔴 {performance['losses']} | "
        f"⚪ {performance['breakeven']}"
    )

    lines.append(
        f"🏆 WR: "
        f"{performance['win_rate']:.1f}%"
    )

    lines.append(
        f"💰 Total P&L: "
        f"{performance['total_pnl']:+.2f}%"
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

        direction = (
            open_trade[
                "direction"
            ]
        )

        emoji = (
            "🟢"
            if direction == "BUY"
            else "🔴"
        )

        lines.append(
            f"{emoji} *OPEN TRADE*"
        )

        lines.append(
            f"{open_trade['symbol']} "
            f"| {direction}"
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
            f"Live P&L: "
            f"`{safe_float(open_trade.get('live_pnl'), 0):+.2f}%`"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "📦 *BOX*"
        )

        lines.append(
            f"High: "
            f"`{fmt_price(open_trade['box_high'])}`"
        )

        lines.append(
            f"Low: "
            f"`{fmt_price(open_trade['box_low'])}`"
        )

        lines.append(
            f"Width: "
            f"`{fmt_price(open_trade['box_width'])}`"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🚀 *BREAKOUT*"
        )

        lines.append(
            f"Price: "
            f"`{fmt_price(open_trade['breakout_price'])}`"
        )

        lines.append(
            f"Volume Ratio: "
            f"`{safe_float(open_trade.get('volume_ratio'), 0):.2f}x`"
        )

        return "\n".join(
            lines
        )

    # ========================================================
    # DIAGNOSTIC
    # ========================================================

    lines.append(
        "🔍 *SCAN DIAGNOSTIC*"
    )

    lines.append(
        f"Markets: "
        f"{summary['total']}"
    )

    lines.append(
        f"Kline Valid: "
        f"{summary['kline_valid']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🔄 *LATEST CROSS*"
    )

    lines.append(
        f"🟢 BUY: "
        f"{summary['cross_buy']}"
    )

    lines.append(
        f"🔴 SELL: "
        f"{summary['cross_sell']}"
    )

    lines.append(
        f"⚪ NONE: "
        f"{summary['cross_none']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📦 *BOX*"
    )

    lines.append(
        f"READY: "
        f"{summary['box_ready']}"
    )

    lines.append(
        f"FAILED: "
        f"{summary['box_failed']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🚀 *BREAKOUT*"
    )

    lines.append(
        f"🟢 BUY: "
        f"{summary['breakout_buy']}"
    )

    lines.append(
        f"🔴 SELL: "
        f"{summary['breakout_sell']}"
    )

    lines.append(
        f"⚪ NONE: "
        f"{summary['breakout_none']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🧭 *CONFIRMED SWING*"
    )

    lines.append(
        f"VALID: "
        f"{summary['swing_valid']}"
    )

    lines.append(
        f"FAILED: "
        f"{summary['swing_failed']}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # NO SIGNAL REASON
    # ========================================================

    if candidates:

        lines.append(
            f"🎯 *VALID CANDIDATES: "
            f"{len(candidates)}*"
        )

    else:

        reasons = summary[
            "reasons"
        ]

        if reasons:

            main_reason = max(
                reasons,
                key=reasons.get
            )

            main_count = reasons[
                main_reason
            ]

            reason_map = {

                "NO_LATEST_CROSS":
                    "No Tenkan/Kijun cross",

                "BOX_NOT_READY":
                    "Box cannot be built",

                "NO_BREAKOUT":
                    "No Box breakout",

                "NO_CONFIRMED_SWING":
                    "Breakout found but no confirmed Swing",

                "BREAKOUT_TOO_EARLY_FOR_CONFIRMED_SWING":
                    "Breakout occurred too early for Swing confirmation",

                "TRADE_BUILD_FAILED":
                    "Trade parameters invalid",

                "INSUFFICIENT_KLINE":
                    "Insufficient Kline",

                "INSUFFICIENT_CLOSED_CANDLES":
                    "Insufficient CLOSED candles",

                "NOT_SCANNED":
                    "Market not scanned",
            }

            readable = reason_map.get(
                main_reason,
                main_reason
            )

            lines.append(
                "⚠️ *NO VALID SIGNAL*"
            )

            lines.append(
                f"Main reason: "
                f"*{readable}*"
            )

            lines.append(
                f"Markets affected: "
                f"`{main_count}`"
            )

    # ========================================================
    # NEAREST SETUPS
    # ========================================================

    nearest = nearest_setups(
        diagnostics,
        3
    )

    if nearest:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "👀 *NEAREST SETUPS*"
        )

        for i, item in enumerate(
            nearest,
            start=1
        ):

            direction_emoji = (
                "🟢"
                if item["direction"]
                == "BUY"
                else "🔴"
            )

            if item[
                "breakout"
            ]:

                distance_text = (
                    "BREAKOUT"
                )

            else:

                distance_text = (
                    f"{abs(item['distance_percent']):.2f}% "
                    f"to Box"
                )

            lines.append(
                f"{i}️⃣ "
                f"*{item['symbol']}* "
                f"{direction_emoji} "
                f"{item['direction']} "
                f"| {distance_text}"
            )

            lines.append(
                f"   Price: "
                f"`{fmt_price(item['current_price'])}`"
            )

            if (
                item["direction"]
                == "BUY"
            ):

                lines.append(
                    f"   Box High: "
                    f"`{fmt_price(item['box_high'])}`"
                )

            else:

                lines.append(
                    f"   Box Low: "
                    f"`{fmt_price(item['box_low'])}`"
                )

    # ========================================================
    # DETAILED FAILED MARKETS
    # ========================================================

    failed = [
        d
        for d in diagnostics
        if not d["trade_valid"]
    ]

    if failed:

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "🧾 *MARKET STATUS*"
        )

        reason_short = {

            "NO_LATEST_CROSS":
                "NO CROSS",

            "BOX_NOT_READY":
                "NO BOX",

            "NO_BREAKOUT":
                "NO BREAKOUT",

            "NO_CONFIRMED_SWING":
                "NO SWING",

            "BREAKOUT_TOO_EARLY_FOR_CONFIRMED_SWING":
                "SWING TOO EARLY",

            "TRADE_BUILD_FAILED":
                "TRADE FAILED",

            "INSUFFICIENT_KLINE":
                "BAD KLINE",

            "INSUFFICIENT_CLOSED_CANDLES":
                "BAD CLOSED KLINE",
        }

        # Show max 12 to avoid Telegram wall of text.
        for d in failed[:12]:

            reason = reason_short.get(
                d["reason"],
                d["reason"]
            )

            extra = ""

            if (
                d.get(
                    "breakout_type"
                )
            ):

                extra = (
                    f" → {d['breakout_type']}"
                )

            elif (
                d.get(
                    "cross_type"
                )
            ):

                extra = (
                    f" → CROSS "
                    f"{d['cross_type']}"
                )

            lines.append(
                f"• {d['symbol']} "
                f"→ {reason}{extra}"
            )

        if len(failed) > 12:

            lines.append(
                f"... +{len(failed) - 12} more"
            )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "======================================================================"
    )

    print(
        "LBANK FUTURES PRICE ACTION SCANNER v4.1"
    )

    print(
        "======================================================================"
    )

    print(
        f"Timeframe: {TIMEFRAME}"
    )

    print(
        f"TOP Futures: {TOP_LIQUID}"
    )

    print(
        f"Final TOP: {TOP_VOLUME}"
    )

    print(
        f"Box: {BOX_SIZE} candles"
    )

    print(
        "Latest Cross Only: YES"
    )

    print(
        "Cross Candle In Box: NO"
    )

    print(
        "Swing Confirmation: 2 LEFT + 2 RIGHT"
    )

    print(
        f"TP: {TP_BOX_PERCENT * 100:.0f}% Box Width"
    )

    print(
        f"SL Buffer: "
        f"{SL_BUFFER_PERCENT * 100:.2f}%"
    )

    print(
        f"Max Open Trades: "
        f"{MAX_OPEN_TRADES}"
    )

    print(
        "======================================================================"
    )

    # ========================================================
    # LOAD STATE
    # ========================================================

    state = load_json(
        STATE_FILE,
        {
            "open_trade": None,
            "last_scan": None,
            "last_signal_key": None,
        }
    )

    history = load_json(
        HISTORY_FILE,
        []
    )

    # ========================================================
    # EXISTING TRADE
    # ========================================================

    open_trade = state.get(
        "open_trade"
    )

    if open_trade:

        symbol = open_trade[
            "symbol"
        ]

        print(
            f"[OPEN TRADE] "
            f"{symbol} "
            f"{open_trade['direction']}"
        )

        candles = get_5m_candles(
            symbol,
            30
        )

        closed = get_closed_candles(
            candles
        )

        if closed:

            current_price = closed[
                -1
            ]["close"]

            open_trade[
                "current_price"
            ] = current_price

            open_trade[
                "live_pnl"
            ] = calculate_pnl_percent(
                open_trade,
                current_price
            )

            exit_reason = (
                check_trade_exit(
                    open_trade,
                    current_price
                )
            )

            if exit_reason:

                print(
                    f"[TRADE CLOSED] "
                    f"{symbol} "
                    f"{exit_reason} "
                    f"@ "
                    f"{fmt_price(current_price)}"
                )

                closed_trade = (
                    add_history_trade(
                        history,
                        open_trade,
                        exit_reason,
                        current_price
                    )
                )

                state[
                    "open_trade"
                ] = None

                state[
                    "last_signal_key"
                ] = None

                save_json(
                    HISTORY_FILE,
                    history
                )

                print(
                    f"[RESULT] "
                    f"{closed_trade['result']} "
                    f"{closed_trade['pnl_percent']:+.2f}%"
                )

        state[
            "last_scan"
        ] = now_utc().isoformat()

        save_json(
            STATE_FILE,
            state
        )

        report = build_report(
            state,
            history,
            [],
            []
        )

        send_telegram(
            report
        )

        return

    # ========================================================
    # FUTURES UNIVERSE
    # ========================================================

    universe = build_liquid_universe()

    if not universe:

        message = (
            "⚠️ *LBANK FUTURES SCANNER*\n\n"
            "No Futures markets available "
            "from marketData.\n\n"
            "Check LBank Futures API."
        )

        send_telegram(
            message
        )

        return

    # ========================================================
    # TOP 30
    # ========================================================

    selected_markets = universe[
        :TOP_VOLUME
    ]

    print(
        "======================================================================"
    )

    print(
        f"[INFO] "
        f"FINAL TOP {TOP_VOLUME}"
    )

    print(
        "======================================================================"
    )

    for i, market in enumerate(
        selected_markets,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{market['symbol']:15s} "
            f"volume="
            f"{fmt_volume(market['volume'])}"
        )

    # ========================================================
    # SCAN
    # ========================================================

    diagnostics = []

    candidates = []

    for number, market in enumerate(
        selected_markets,
        start=1
    ):

        symbol = market[
            "symbol"
        ]

        print(
            "------------------------------------------------------------------"
        )

        print(
            f"[SCAN {number}/"
            f"{len(selected_markets)}] "
            f"{symbol}"
        )

        try:

            trade, diagnostic = (
                scan_market(
                    market
                )
            )

            diagnostics.append(
                diagnostic
            )

            if trade:

                candidates.append(
                    trade
                )

                print(
                    f"[VALID SIGNAL] "
                    f"{symbol} "
                    f"{trade['direction']}"
                )

            else:

                print(
                    f"[DIAGNOSTIC] "
                    f"{symbol} "
                    f"{diagnostic['reason']}"
                )

        except Exception as exc:

            print(
                f"[SCAN ERROR] "
                f"{symbol} "
                f"{exc}"
            )

            diagnostics.append({
                "symbol": symbol,
                "volume": market[
                    "volume"
                ],
                "stage": "ERROR",
                "kline_valid": False,
                "cross": None,
                "cross_type": None,
                "box_ready": False,
                "breakout": None,
                "breakout_type": None,
                "swing_valid": False,
                "trade_valid": False,
                "box_high": None,
                "box_low": None,
                "current_price": None,
                "distance_direction": None,
                "distance_percent": None,
                "reason": "SCAN_ERROR",
            })

    # ========================================================
    # SELECT ONE SIGNAL
    # ========================================================

    selected_trade = None

    if candidates:

        candidates.sort(
            key=lambda x: (
                safe_float(
                    x.get(
                        "volume_ratio"
                    ),
                    0
                ),
                x.get(
                    "breakout_timestamp",
                    0
                )
            ),
            reverse=True
        )

        selected_trade = (
            candidates[0]
        )

    # ========================================================
    # OPEN SELECTED SIGNAL
    # ========================================================

    if selected_trade:

        signal_key = (
            f"{selected_trade['symbol']}_"
            f"{selected_trade['direction']}_"
            f"{selected_trade['breakout_timestamp']}"
        )

        if (
            state.get(
                "last_signal_key"
            )
            != signal_key
        ):

            state[
                "open_trade"
            ] = selected_trade

            state[
                "last_signal_key"
            ] = signal_key

            print(
                "======================================================================"
            )

            print(
                "🎯 NEW TRADE"
            )

            print(
                f"Symbol: "
                f"{selected_trade['symbol']}"
            )

            print(
                f"Direction: "
                f"{selected_trade['direction']}"
            )

            print(
                f"Entry: "
                f"{fmt_price(selected_trade['entry'])}"
            )

            print(
                f"SL: "
                f"{fmt_price(selected_trade['sl'])}"
            )

            print(
                f"TP: "
                f"{fmt_price(selected_trade['tp'])}"
            )

            print(
                f"Box High: "
                f"{fmt_price(selected_trade['box_high'])}"
            )

            print(
                f"Box Low: "
                f"{fmt_price(selected_trade['box_low'])}"
            )

            print(
                f"Swing: "
                f"{fmt_price(selected_trade['swing_price'])}"
            )

            print(
                f"Volume Ratio: "
                f"{selected_trade['volume_ratio']:.2f}x"
            )

            print(
                "======================================================================"
            )

    else:

        print(
            "======================================================================"
        )

        print(
            "⚠️ NO VALID SIGNAL"
        )

        diagnostic_summary = (
            build_diagnostic_summary(
                diagnostics
            )
        )

        print(
            json.dumps(
                diagnostic_summary,
                ensure_ascii=False,
                indent=2
            )
        )

        print(
            "======================================================================"
        )

    # ========================================================
    # SAVE STATE
    # ========================================================

    state[
        "last_scan"
    ] = now_utc().isoformat()

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        HISTORY_FILE,
        history
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    report = build_report(
        state,
        history,
        diagnostics,
        candidates
    )

    print(
        "======================================================================"
    )

    print(
        "TELEGRAM REPORT"
    )

    print(
        report
    )

    print(
        "======================================================================"
    )

    send_telegram(
        report
    )

    print(
        "======================================================================"
    )

    print(
        "SCAN COMPLETED"
    )

    print(
        "======================================================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOPPED] Keyboard interrupt."
        )

    except Exception as exc:

        print(
            f"\n[FATAL ERROR] "
            f"{exc}"
        )

        try:

            send_telegram(
                "🚨 *LBANK FUTURES SCANNER ERROR*\n\n"
                f"`{str(exc)[:3500]}`"
            )

        except Exception:
            pass

        raise
