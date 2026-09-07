# ============================================================
# KRAKEN FUTURES PRICE ACTION SCANNER
# ============================================================
#
# Kraken Futures
# 5m CLOSED candles
# TOP 30 by 24h quote volume
#
# STRATEGY
# ------------------------------------------------------------
# Tenkan = 9
# Kijun  = 26
#
# 1) Find ONLY the latest Tenkan/Kijun crossover
# 2) Ignore all older crosses
# 3) Crossover candle is NOT part of the box
# 4) Box = 26 candles immediately BEFORE crossover
# 5) BUY  = later CLOSED candle closes above Box High
# 6) SELL = later CLOSED candle closes below Box Low
#
# SWING
# ------------------------------------------------------------
# Confirmed pivot:
# 2 candles left + 2 candles right
#
# BUY SL:
# slightly below latest valid Swing Low
#
# SELL SL:
# slightly above latest valid Swing High
#
# TP:
# 50% of Box width
#
# MAX OPEN TRADES:
# 1
#
# Telegram:
# Signal simulator only
# No real orders are placed
# ============================================================

import os
import json
import time
import math
import traceback
import threading
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


# ============================================================
# CONFIG
# ============================================================

KRAKEN_BASE_URL = "https://futures.kraken.com"

KRAKEN_API_BASE = (
    KRAKEN_BASE_URL +
    "/derivatives/api/v3"
)

KRAKEN_CHART_BASE = (
    KRAKEN_BASE_URL +
    "/api/charts/v1"
)

TELEGRAM_BOT_TOKEN = (
    os.getenv("TELEGRAM_BOT_TOKEN", "")
    .strip()
)

TELEGRAM_CHAT_ID = (
    os.getenv("TELEGRAM_CHAT_ID", "")
    .strip()
)

# ------------------------------------------------------------
# Strategy
# ------------------------------------------------------------

TIMEFRAME = "5m"

TOP_SYMBOLS = 30

CANDLE_LIMIT = 100

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

BOX_PERIOD = 26

SWING_LEFT = 2
SWING_RIGHT = 2

TP_BOX_PERCENT = 0.50

SL_BUFFER_PERCENT = 0.15

MAX_OPEN_TRADES = 1

# Maximum number of candles after breakout
# that the scanner accepts as a fresh signal.
MAX_BREAKOUT_AGE = 2

# ------------------------------------------------------------
# Runtime
# ------------------------------------------------------------

MAX_WORKERS = 5

REQUEST_TIMEOUT = 20

REQUEST_RETRIES = 3

STATE_FILE = (
    "kraken_price_action_state.json"
)

HISTORY_FILE = (
    "kraken_price_action_trade_history.json"
)


# ============================================================
# HTTP SESSIONS
# ============================================================

KRAKEN_SESSION = requests.Session()

KRAKEN_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent":
        "Kraken-Futures-Price-Action-Scanner/1.0"
})


TELEGRAM_SESSION = requests.Session()

TELEGRAM_SESSION.headers.update({
    "Accept": "application/json",
    "User-Agent":
        "Kraken-Futures-Price-Action-Scanner/1.0"
})


# ============================================================
# GLOBAL LOCK
# ============================================================

STATE_LOCK = threading.Lock()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_timestamp():
    return int(utc_now().timestamp())


def format_time(ts):
    try:
        if isinstance(ts, (int, float)):
            dt = datetime.fromtimestamp(
                ts,
                tz=timezone.utc
            )
        else:
            dt = datetime.fromisoformat(
                str(ts).replace("Z", "+00:00")
            )

        return dt.strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )

    except Exception:
        return str(ts)


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def fmt_price(value):
    value = safe_float(value)

    if value == 0:
        return "0"

    if abs(value) >= 1000:
        return f"{value:,.2f}"

    if abs(value) >= 100:
        return f"{value:,.3f}"

    if abs(value) >= 10:
        return f"{value:,.4f}"

    if abs(value) >= 1:
        return f"{value:,.5f}"

    if abs(value) >= 0.1:
        return f"{value:,.6f}"

    if abs(value) >= 0.01:
        return f"{value:,.7f}"

    return f"{value:.10f}"


def fmt_pct(value):
    return f"{safe_float(value):+.2f}%"


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

    except Exception as e:
        print(
            f"[WARN] Cannot load {path}: {e}"
        )
        return default


def save_json(path, data):
    temp_path = path + ".tmp"

    try:
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

    except Exception as e:
        print(
            f"[ERROR] Cannot save {path}: {e}"
        )


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "open_trade": None,
        "last_signal_key": None,
        "last_checked": None
    }


def default_history():
    return {
        "trades": []
    }


STATE = load_json(
    STATE_FILE,
    default_state()
)

HISTORY = load_json(
    HISTORY_FILE,
    default_history()
)


if not isinstance(STATE, dict):
    STATE = default_state()

if not isinstance(HISTORY, dict):
    HISTORY = default_history()

if "trades" not in HISTORY:
    HISTORY["trades"] = []


# ============================================================
# HTTP REQUEST
# ============================================================

def kraken_get(
    url,
    params=None,
    retries=REQUEST_RETRIES
):
    last_error = None

    for attempt in range(1, retries + 1):

        try:

            response = KRAKEN_SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            return data

        except Exception as e:

            last_error = e

            print(
                f"[WARN] Kraken request failed "
                f"attempt {attempt}/{retries}: "
                f"{e}"
            )

            if attempt < retries:
                time.sleep(
                    1.5 * attempt
                )

    raise RuntimeError(
        f"Kraken request failed: {last_error}"
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

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

        payload = {
            "chat_id": str(
                TELEGRAM_CHAT_ID
            ),
            "text": str(message),
            "parse_mode": "Markdown"
        }

        response = TELEGRAM_SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            print(
                "[WARN] Telegram HTTP error:",
                response.status_code,
                response.text[:500]
            )

            return False

        result = response.json()

        if not result.get("ok", False):

            print(
                "[WARN] Telegram API error:",
                response.text[:500]
            )

            return False

        print(
            "[INFO] Telegram message sent successfully."
        )

        return True

    except Exception as e:

        print(
            f"[WARN] Telegram exception: {e}"
        )

        return False


def send_telegram_error(message):

    try:
        send_telegram(
            "🚨 *SCANNER ERROR*\n\n" +
            str(message)
        )
    except Exception:
        pass


# ============================================================
# KRAKEN INSTRUMENTS
# ============================================================

def get_futures_instruments():

    print(
        "[INFO] Loading Kraken Futures instruments..."
    )

    url = (
        KRAKEN_API_BASE +
        "/instruments"
    )

    data = kraken_get(url)

    instruments = data.get(
        "instruments",
        []
    )

    if not instruments:
        raise RuntimeError(
            "Kraken returned zero futures instruments."
        )

    print(
        f"[INFO] Kraken instruments: "
        f"{len(instruments)}"
    )

    return instruments


# ============================================================
# KRAKEN TICKERS
# ============================================================

def get_futures_tickers():

    print(
        "[INFO] Loading Kraken Futures tickers..."
    )

    url = (
        KRAKEN_API_BASE +
        "/tickers"
    )

    data = kraken_get(url)

    tickers = data.get(
        "tickers",
        []
    )

    if not tickers:
        raise RuntimeError(
            "Kraken returned zero futures tickers."
        )

    print(
        f"[INFO] Kraken tickers: "
        f"{len(tickers)}"
    )

    return tickers


# ============================================================
# BUILD TOP SYMBOL LIST
# ============================================================

def get_top_symbols():

    instruments = get_futures_instruments()

    tickers = get_futures_tickers()

    instrument_map = {}

    for item in instruments:

        symbol = str(
            item.get("symbol", "")
        ).strip()

        if not symbol:
            continue

        instrument_map[
            symbol.upper()
        ] = item

    candidates = []

    for ticker in tickers:

        symbol = str(
            ticker.get("symbol", "")
        ).strip()

        if not symbol:
            continue

        upper_symbol = symbol.upper()

        instrument = instrument_map.get(
            upper_symbol
        )

        if not instrument:
            continue

        # ----------------------------------------------------
        # Only perpetual contracts
        # ----------------------------------------------------

        tag = str(
            ticker.get("tag", "")
        ).lower()

        instrument_type = str(
            instrument.get("type", "")
        ).lower()

        is_perpetual = (
            tag == "perpetual"
            or
            "perpetual" in instrument_type
        )

        if not is_perpetual:
            continue

        # ----------------------------------------------------
        # We prefer USD-margined perpetuals
        # ----------------------------------------------------

        pair = str(
            ticker.get("pair", "")
        ).upper()

        underlying = str(
            instrument.get(
                "underlying",
                ""
            )
        ).upper()

        # Kraken uses symbols such as:
        # PF_XBTUSD
        # PF_ETHUSD
        #
        # We keep USD perpetual contracts.
        if "USD" not in upper_symbol and \
           "USD" not in pair and \
           "USD" not in underlying:
            continue

        # ----------------------------------------------------
        # Volume
        # ----------------------------------------------------

        volume_quote = safe_float(
            ticker.get(
                "volumeQuote",
                0
            )
        )

        vol24 = safe_float(
            ticker.get(
                "vol24h",
                0
            )
        )

        # Some Kraken responses can have
        # volumeQuote missing/zero.
        # Use vol24h as fallback.
        if volume_quote <= 0:
            volume_quote = vol24

        mark_price = safe_float(
            ticker.get(
                "markPrice",
                0
            )
        )

        last_price = safe_float(
            ticker.get(
                "last",
                0
            )
        )

        if mark_price <= 0:
            mark_price = last_price

        if mark_price <= 0:
            continue

        candidates.append({
            "symbol": symbol,
            "pair": pair,
            "volume_quote": volume_quote,
            "mark_price": mark_price,
            "ticker": ticker,
            "instrument": instrument
        })

    candidates.sort(
        key=lambda x:
            x["volume_quote"],
        reverse=True
    )

    top = candidates[
        :TOP_SYMBOLS
    ]

    print(
        f"[INFO] Eligible perpetuals: "
        f"{len(candidates)}"
    )

    print(
        f"[INFO] TOP {TOP_SYMBOLS}:"
    )

    for i, item in enumerate(
        top,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{item['symbol']} | "
            f"Volume: "
            f"{item['volume_quote']:,.2f} | "
            f"Price: "
            f"{fmt_price(item['mark_price'])}"
        )

    return top


# ============================================================
# CANDLE PARSER
# ============================================================

def normalize_candle(item):

    try:

        ts = int(
            item.get("time")
        )

        if ts < 10_000_000_000:
            ts *= 1000

        return {
            "time": ts,
            "open": safe_float(
                item.get("open")
            ),
            "high": safe_float(
                item.get("high")
            ),
            "low": safe_float(
                item.get("low")
            ),
            "close": safe_float(
                item.get("close")
            ),
            "volume": safe_float(
                item.get("volume")
            )
        }

    except Exception:
        return None


# ============================================================
# FETCH CANDLES
# ============================================================

def fetch_candles(symbol):

    url = (
        KRAKEN_CHART_BASE +
        f"/trade/{symbol}/{TIMEFRAME}"
    )

    params = {
        "count": CANDLE_LIMIT
    }

    try:

        data = kraken_get(
            url,
            params=params
        )

        raw = data.get(
            "candles",
            []
        )

        candles = []

        for item in raw:

            candle = normalize_candle(
                item
            )

            if candle is None:
                continue

            if candle["close"] <= 0:
                continue

            candles.append(
                candle
            )

        candles.sort(
            key=lambda x:
                x["time"]
        )

        return candles

    except Exception as e:

        print(
            f"[WARN] Candle error "
            f"{symbol}: {e}"
        )

        return []


# ============================================================
# FILTER CLOSED CANDLES
# ============================================================

def filter_closed_candles(candles):

    if not candles:
        return []

    now_ms = int(
        utc_timestamp() * 1000
    )

    timeframe_ms = 5 * 60 * 1000

    closed = []

    for candle in candles:

        candle_end = (
            candle["time"] +
            timeframe_ms
        )

        if candle_end <= now_ms:
            closed.append(
                candle
            )

    return closed


# ============================================================
# FETCH ALL CANDLES
# ============================================================

def fetch_top_candles(top_symbols):

    result = {}

    print(
        "[INFO] Downloading 5m candles..."
    )

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {}

        for item in top_symbols:

            symbol = item["symbol"]

            futures[
                executor.submit(
                    fetch_candles,
                    symbol
                )
            ] = symbol

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                candles = future.result()

                candles = (
                    filter_closed_candles(
                        candles
                    )
                )

                if candles:
                    result[
                        symbol
                    ] = candles

            except Exception as e:

                print(
                    f"[WARN] "
                    f"{symbol}: {e}"
                )

    print(
        f"[INFO] Valid candle sets: "
        f"{len(result)}"
    )

    return result


# ============================================================
# TENKAN
# ============================================================

def tenkan_value(
    candles,
    index
):

    start = (
        index -
        TENKAN_PERIOD +
        1
    )

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    if len(window) != TENKAN_PERIOD:
        return None

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest +
        lowest
    ) / 2.0


# ============================================================
# KIJUN
# ============================================================

def kijun_value(
    candles,
    index
):

    start = (
        index -
        KIJUN_PERIOD +
        1
    )

    if start < 0:
        return None

    window = candles[
        start:index + 1
    ]

    if len(window) != KIJUN_PERIOD:
        return None

    highest = max(
        c["high"]
        for c in window
    )

    lowest = min(
        c["low"]
        for c in window
    )

    return (
        highest +
        lowest
    ) / 2.0


# ============================================================
# FIND LATEST TENKAN/KIJUN CROSS
# ============================================================

def find_latest_cross(candles):

    if len(candles) < (
        KIJUN_PERIOD + 2
    ):
        return None

    latest = None

    for i in range(
        KIJUN_PERIOD,
        len(candles)
    ):

        tenkan_prev = tenkan_value(
            candles,
            i - 1
        )

        kijun_prev = kijun_value(
            candles,
            i - 1
        )

        tenkan_now = tenkan_value(
            candles,
            i
        )

        kijun_now = kijun_value(
            candles,
            i
        )

        if (
            tenkan_prev is None
            or kijun_prev is None
            or tenkan_now is None
            or kijun_now is None
        ):
            continue

        direction = None

        # Bullish cross
        if (
            tenkan_prev <= kijun_prev
            and
            tenkan_now > kijun_now
        ):
            direction = "BUY"

        # Bearish cross
        elif (
            tenkan_prev >= kijun_prev
            and
            tenkan_now < kijun_now
        ):
            direction = "SELL"

        if direction:

            latest = {
                "index": i,
                "direction": direction,
                "time": candles[i]["time"],
                "tenkan": tenkan_now,
                "kijun": kijun_now
            }

    return latest


# ============================================================
# BOX
# ============================================================

def build_box(
    candles,
    cross_index
):

    start = (
        cross_index -
        BOX_PERIOD
    )

    end = cross_index

    if start < 0:
        return None

    box_candles = candles[
        start:end
    ]

    if len(box_candles) != BOX_PERIOD:
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
        box_high -
        box_low
    )

    if width <= 0:
        return None

    return {
        "high": box_high,
        "low": box_low,
        "width": width,
        "start_time":
            box_candles[0]["time"],
        "end_time":
            box_candles[-1]["time"]
    }


# ============================================================
# BREAKOUT
# ============================================================

def find_breakout(
    candles,
    cross,
    box
):

    cross_index = cross[
        "index"
    ]

    direction = cross[
        "direction"
    ]

    # Breakout must happen AFTER cross.
    for i in range(
        cross_index + 1,
        len(candles)
    ):

        candle = candles[i]

        if direction == "BUY":

            if candle["close"] > box["high"]:

                return {
                    "index": i,
                    "direction": "BUY",
                    "time": candle["time"],
                    "entry": candle["close"],
                    "age":
                        len(candles) -
                        1 -
                        i
                }

        elif direction == "SELL":

            if candle["close"] < box["low"]:

                return {
                    "index": i,
                    "direction": "SELL",
                    "time": candle["time"],
                    "entry": candle["close"],
                    "age":
                        len(candles) -
                        1 -
                        i
                }

    return None


# ============================================================
# SWING LOW
# ============================================================

def is_swing_low(
    candles,
    index
):

    if index < SWING_LEFT:
        return False

    if (
        index +
        SWING_RIGHT
        >= len(candles)
    ):
        return False

    value = candles[
        index
    ]["low"]

    left = candles[
        index - SWING_LEFT:
        index
    ]

    right = candles[
        index + 1:
        index + 1 +
        SWING_RIGHT
    ]

    for c in left:
        if value >= c["low"]:
            return False

    for c in right:
        if value >= c["low"]:
            return False

    return True


# ============================================================
# SWING HIGH
# ============================================================

def is_swing_high(
    candles,
    index
):

    if index < SWING_LEFT:
        return False

    if (
        index +
        SWING_RIGHT
        >= len(candles)
    ):
        return False

    value = candles[
        index
    ]["high"]

    left = candles[
        index - SWING_LEFT:
        index
    ]

    right = candles[
        index + 1:
        index + 1 +
        SWING_RIGHT
    ]

    for c in left:
        if value <= c["high"]:
            return False

    for c in right:
        if value <= c["high"]:
            return False

    return True


# ============================================================
# FIND LATEST VALID SWING
# ============================================================

def find_latest_swing(
    candles,
    direction,
    before_index
):

    if before_index <= 0:
        return None

    # We search backwards.
    # Only CONFIRMED swings are accepted.
    start = min(
        before_index - 1,
        len(candles) -
        SWING_RIGHT -
        1
    )

    if direction == "BUY":

        for i in range(
            start,
            SWING_LEFT - 1,
            -1
        ):

            if is_swing_low(
                candles,
                i
            ):

                return {
                    "index": i,
                    "time":
                        candles[i]["time"],
                    "price":
                        candles[i]["low"],
                    "type":
                        "SWING_LOW"
                }

    else:

        for i in range(
            start,
            SWING_LEFT - 1,
            -1
        ):

            if is_swing_high(
                candles,
                i
            ):

                return {
                    "index": i,
                    "time":
                        candles[i]["time"],
                    "price":
                        candles[i]["high"],
                    "type":
                        "SWING_HIGH"
                }

    return None


# ============================================================
# CREATE SIGNAL
# ============================================================

def analyze_symbol(
    symbol,
    candles
):

    if len(candles) < (
        KIJUN_PERIOD +
        BOX_PERIOD +
        SWING_LEFT +
        SWING_RIGHT +
        5
    ):
        return None

    cross = find_latest_cross(
        candles
    )

    if not cross:
        return None

    box = build_box(
        candles,
        cross["index"]
    )

    if not box:
        return None

    breakout = find_breakout(
        candles,
        cross,
        box
    )

    if not breakout:
        return None

    # --------------------------------------------------------
    # Breakout must be fresh
    # --------------------------------------------------------

    if breakout["age"] > MAX_BREAKOUT_AGE:
        return None

    # --------------------------------------------------------
    # Find confirmed swing
    # --------------------------------------------------------

    swing = find_latest_swing(
        candles,
        breakout["direction"],
        breakout["index"]
    )

    if not swing:
        return None

    entry = breakout[
        "entry"
    ]

    box_width = box[
        "width"
    ]

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if breakout["direction"] == "BUY":

        sl = (
            swing["price"] *
            (
                1 -
                SL_BUFFER_PERCENT /
                100
            )
        )

        tp = (
            entry +
            box_width *
            TP_BOX_PERCENT
        )

        if sl >= entry:
            return None

        if tp <= entry:
            return None

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    else:

        sl = (
            swing["price"] *
            (
                1 +
                SL_BUFFER_PERCENT /
                100
            )
        )

        tp = (
            entry -
            box_width *
            TP_BOX_PERCENT
        )

        if sl <= entry:
            return None

        if tp >= entry:
            return None

    risk = abs(
        entry -
        sl
    )

    reward = abs(
        tp -
        entry
    )

    if risk <= 0:
        return None

    rr = reward / risk

    signal_key = (
        f"{symbol}|"
        f"{breakout['direction']}|"
        f"{breakout['time']}"
    )

    return {
        "symbol": symbol,
        "direction":
            breakout["direction"],
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "reward": reward,
        "rr": rr,

        "cross_time":
            cross["time"],

        "cross_direction":
            cross["direction"],

        "cross_tenkan":
            cross["tenkan"],

        "cross_kijun":
            cross["kijun"],

        "box_high":
            box["high"],

        "box_low":
            box["low"],

        "box_width":
            box["width"],

        "box_start":
            box["start_time"],

        "box_end":
            box["end_time"],

        "breakout_time":
            breakout["time"],

        "breakout_index":
            breakout["index"],

        "breakout_age":
            breakout["age"],

        "swing_type":
            swing["type"],

        "swing_price":
            swing["price"],

        "swing_time":
            swing["time"],

        "signal_key":
            signal_key
    }


# ============================================================
# VOLUME RATIO
# ============================================================

def volume_ratio(
    candles,
    index,
    period=20
):

    if index < period:
        return 1.0

    current = safe_float(
        candles[index]["volume"]
    )

    previous = candles[
        index - period:
        index
    ]

    values = [
        safe_float(
            c["volume"]
        )
        for c in previous
    ]

    values = [
        x for x in values
        if x > 0
    ]

    if not values:
        return 1.0

    avg = sum(values) / len(values)

    if avg <= 0:
        return 1.0

    return current / avg


# ============================================================
# OPEN TRADE
# ============================================================

def get_open_trade():

    with STATE_LOCK:

        trade = STATE.get(
            "open_trade"
        )

        if isinstance(
            trade,
            dict
        ):
            return dict(trade)

    return None


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    reason,
    candle_time
):

    direction = trade[
        "direction"
    ]

    entry = safe_float(
        trade["entry"]
    )

    exit_price = safe_float(
        exit_price
    )

    if direction == "BUY":

        pnl_pct = (
            (
                exit_price -
                entry
            ) /
            entry
        ) * 100

    else:

        pnl_pct = (
            (
                entry -
                exit_price
            ) /
            entry
        ) * 100

    result = (
        "WIN"
        if pnl_pct > 0
        else
        "LOSS"
        if pnl_pct < 0
        else
        "BE"
    )

    completed = dict(trade)

    completed.update({
        "exit": exit_price,
        "exit_reason": reason,
        "exit_time": candle_time,
        "pnl_pct": pnl_pct,
        "result": result
    })

    HISTORY.setdefault(
        "trades",
        []
    ).append(
        completed
    )

    STATE[
        "open_trade"
    ] = None

    save_json(
        STATE_FILE,
        STATE
    )

    save_json(
        HISTORY_FILE,
        HISTORY
    )

    print(
        f"[TRADE CLOSED] "
        f"{trade['symbol']} "
        f"{direction} "
        f"{reason} "
        f"{pnl_pct:+.2f}%"
    )

    return completed


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def update_open_trade(
    candles_by_symbol
):

    trade = get_open_trade()

    if not trade:
        return None

    symbol = trade[
        "symbol"
    ]

    candles = candles_by_symbol.get(
        symbol,
        []
    )

    if not candles:
        return None

    direction = trade[
        "direction"
    ]

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    last_processed = trade.get(
        "last_checked_candle"
    )

    new_candles = []

    for candle in candles:

        if (
            last_processed is not None
            and candle["time"] <=
            last_processed
        ):
            continue

        new_candles.append(
            candle
        )

    if not new_candles:
        return None

    for candle in new_candles:

        high = candle["high"]
        low = candle["low"]

        exit_price = None
        reason = None

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if direction == "BUY":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            # Conservative assumption:
            # if both are touched on the same candle,
            # SL comes first.
            if hit_sl:

                exit_price = sl
                reason = "SL"

            elif hit_tp:

                exit_price = tp
                reason = "TP"

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            if hit_sl:

                exit_price = sl
                reason = "SL"

            elif hit_tp:

                exit_price = tp
                reason = "TP"

        trade[
            "last_checked_candle"
        ] = candle["time"]

        if exit_price is not None:

            result = close_trade(
                trade,
                exit_price,
                reason,
                candle["time"]
            )

            return result

    STATE[
        "open_trade"
    ] = trade

    save_json(
        STATE_FILE,
        STATE
    )

    return None


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    trades = HISTORY.get(
        "trades",
        []
    )

    total = len(trades)

    wins = sum(
        1
        for t in trades
        if t.get("result") == "WIN"
    )

    losses = sum(
        1
        for t in trades
        if t.get("result") == "LOSS"
    )

    be = sum(
        1
        for t in trades
        if t.get("result") == "BE"
    )

    if total > 0:
        win_rate = (
            wins /
            total
        ) * 100
    else:
        win_rate = 0.0

    pnl = sum(
        safe_float(
            t.get("pnl_pct", 0)
        )
        for t in trades
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "be": be,
        "win_rate": win_rate,
        "pnl": pnl
    }


# ============================================================
# LIVE PNL
# ============================================================

def live_pnl(
    trade,
    current_price
):

    entry = safe_float(
        trade["entry"]
    )

    current = safe_float(
        current_price
    )

    if entry <= 0:
        return 0.0

    if trade[
        "direction"
    ] == "BUY":

        return (
            (
                current -
                entry
            ) /
            entry
        ) * 100

    return (
        (
            entry -
            current
        ) /
        entry
    ) * 100


# ============================================================
# REPORT
# ============================================================

def build_report(
    top_symbols,
    signals,
    current_prices
):

    perf = performance()

    open_trade = get_open_trade()

    now = utc_now().strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    lines = []

    lines.append(
        "📡 *KRAKEN FUTURES PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        f"⏱ *{TIMEFRAME.upper()} CLOSED | TOP {TOP_SYMBOLS}*"
    )

    lines.append(
        "🤖 *TENKAN/KIJUN + BOX BREAKOUT*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 *PERFORMANCE*"
    )

    lines.append(
        f"Trades {perf['total']} | "
        f"🟢 {perf['wins']} | "
        f"🔴 {perf['losses']} | "
        f"⚪ {perf['be']}"
    )

    lines.append(
        f"🏆 WR: {perf['win_rate']:.1f}%"
    )

    lines.append(
        f"📈 Closed P&L: "
        f"{perf['pnl']:+.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------------

    if open_trade:

        symbol = open_trade[
            "symbol"
        ]

        price = safe_float(
            current_prices.get(
                symbol,
                open_trade["entry"]
            )
        )

        pnl = live_pnl(
            open_trade,
            price
        )

        emoji = (
            "🟢"
            if pnl >= 0
            else
            "🔴"
        )

        lines.append(
            "📌 *OPEN TRADE*"
        )

        lines.append(
            f"{emoji} "
            f"*{symbol}* "
            f"{open_trade['direction']}"
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
            f"Price: "
            f"`{fmt_price(price)}`"
        )

        lines.append(
            f"Live P&L: "
            f"`{pnl:+.2f}%`"
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
            f"Swing: "
            f"`{fmt_price(open_trade['swing_price'])}`"
        )

    else:

        lines.append(
            "📌 *OPEN TRADE*"
        )

        lines.append(
            "None"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # SIGNAL
    # --------------------------------------------------------

    if signals:

        lines.append(
            "🚨 *NEW SIGNAL*"
        )

        for signal in signals:

            symbol = signal[
                "symbol"
            ]

            direction = signal[
                "direction"
            ]

            emoji = (
                "🟢"
                if direction == "BUY"
                else
                "🔴"
            )

            lines.append(
                f"{emoji} *{symbol}* "
                f"*{direction}*"
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
                f"RR: "
                f"`1:{signal['rr']:.2f}`"
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
                f"Cross Time: "
                f"`{format_time(signal['cross_time'])}`"
            )

            lines.append(
                f"Breakout: "
                f"`{format_time(signal['breakout_time'])}`"
            )

            lines.append(
                f"Swing: "
                f"`{signal['swing_type']}` "
                f"`{fmt_price(signal['swing_price'])}`"
            )

            lines.append(
                f"Volume Ratio: "
                f"`{signal.get('volume_ratio', 1.0):.2f}x`"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "🔎 *NEW SIGNAL*"
        )

        lines.append(
            "No fresh breakout."
        )

    # --------------------------------------------------------
    # SCAN SUMMARY
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📡 Scanned: "
        f"`{len(top_symbols)}`"
    )

    lines.append(
        f"🎯 Signals: "
        f"`{len(signals)}`"
    )

    lines.append(
        "⚠️ Signal simulator only"
    )

    return "\n".join(lines)


# ============================================================
# MAIN ANALYSIS
# ============================================================

def main():

    print("=" * 70)

    print(
        "KRAKEN FUTURES PRICE ACTION SCANNER"
    )

    print("=" * 70)

    print(
        "Provider: Kraken Futures"
    )

    print(
        f"Timeframe: {TIMEFRAME.upper()}"
    )

    print(
        f"Top: {TOP_SYMBOLS}"
    )

    print(
        f"Tenkan: {TENKAN_PERIOD}"
    )

    print(
        f"Kijun: {KIJUN_PERIOD}"
    )

    print(
        f"Box: {BOX_PERIOD} candles"
    )

    print(
        "Swing: "
        f"{SWING_LEFT} left + "
        f"{SWING_RIGHT} right"
    )

    print(
        f"TP: {TP_BOX_PERCENT * 100:.0f}% Box"
    )

    print(
        f"Max Open: {MAX_OPEN_TRADES}"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # TOP SYMBOLS
    # --------------------------------------------------------

    top_symbols = get_top_symbols()

    if not top_symbols:

        raise RuntimeError(
            "No eligible Kraken perpetuals found."
        )

    # --------------------------------------------------------
    # CANDLES
    # --------------------------------------------------------

    candles_by_symbol = (
        fetch_top_candles(
            top_symbols
        )
    )

    if not candles_by_symbol:

        raise RuntimeError(
            "No candle data received from Kraken."
        )

    # --------------------------------------------------------
    # CURRENT PRICES
    # --------------------------------------------------------

    current_prices = {}

    for item in top_symbols:

        symbol = item[
            "symbol"
        ]

        price = safe_float(
            item.get(
                "mark_price",
                0
            )
        )

        if price > 0:
            current_prices[
                symbol
            ] = price

    # --------------------------------------------------------
    # MANAGE EXISTING TRADE FIRST
    # --------------------------------------------------------

    closed_trade = update_open_trade(
        candles_by_symbol
    )

    if closed_trade:

        print(
            "[INFO] Existing trade closed:"
        )

        print(
            json.dumps(
                closed_trade,
                ensure_ascii=False,
                indent=2
            )
        )

    # --------------------------------------------------------
    # CHECK MAX OPEN
    # --------------------------------------------------------

    open_trade = get_open_trade()

    signals = []

    if open_trade:

        print(
            "[INFO] One trade is already open."
        )

        print(
            f"[INFO] "
            f"{open_trade['symbol']} "
            f"{open_trade['direction']}"
        )

    else:

        print(
            "[INFO] No open trade. "
            "Scanning for new signal..."
        )

        # ----------------------------------------------------
        # Analyze symbols
        # ----------------------------------------------------

        for item in top_symbols:

            symbol = item[
                "symbol"
            ]

            candles = candles_by_symbol.get(
                symbol,
                []
            )

            if not candles:
                continue

            try:

                signal = analyze_symbol(
                    symbol,
                    candles
                )

                if not signal:
                    continue

                # ------------------------------------------------
                # Volume ratio
                # ------------------------------------------------

                breakout_index = signal[
                    "breakout_index"
                ]

                signal[
                    "volume_ratio"
                ] = volume_ratio(
                    candles,
                    breakout_index
                )

                signals.append(
                    signal
                )

            except Exception as e:

                print(
                    f"[WARN] Analysis error "
                    f"{symbol}: {e}"
                )

        # ----------------------------------------------------
        # Only ONE signal allowed
        # ----------------------------------------------------

        if signals:

            # Prefer the freshest breakout.
            signals.sort(
                key=lambda x:
                    x["breakout_time"],
                reverse=True
            )

            selected = signals[0]

            signals = [
                selected
            ]

            # ------------------------------------------------
            # Open virtual trade
            # ------------------------------------------------

            trade = dict(
                selected
            )

            trade[
                "opened_at"
            ] = utc_timestamp()

            trade[
                "last_checked_candle"
            ] = selected[
                "breakout_time"
            ]

            STATE[
                "open_trade"
            ] = trade

            STATE[
                "last_signal_key"
            ] = selected[
                "signal_key"
            ]

            save_json(
                STATE_FILE,
                STATE
            )

            print(
                "[SIGNAL] "
                f"{selected['symbol']} "
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

        else:

            print(
                "[INFO] No fresh signal."
            )

    # --------------------------------------------------------
    # SAVE LAST CHECK
    # --------------------------------------------------------

    STATE[
        "last_checked"
    ] = utc_timestamp()

    save_json(
        STATE_FILE,
        STATE
    )

    save_json(
        HISTORY_FILE,
        HISTORY
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    report = build_report(
        top_symbols,
        signals,
        current_prices
    )

    send_telegram(
        report
    )

    print("=" * 70)

    print(
        "SCAN COMPLETE"
    )

    print("=" * 70)


# ============================================================
# FATAL ERROR HANDLER
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print("=" * 70)

        print(
            "FATAL SCANNER ERROR"
        )

        print("=" * 70)

        print(
            str(e)
        )

        traceback.print_exc()

        error_text = (
            f"{type(e).__name__}: {e}"
        )

        send_telegram_error(
            error_text
        )

        raise
