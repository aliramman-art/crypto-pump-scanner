# ============================================================
# BINANCE USD-M FUTURES PRICE ACTION SCANNER
# ============================================================
#
# MARKET:
#   Binance USD-M Futures
#
# TIMEFRAME:
#   5m CLOSED CANDLES
#
# UNIVERSE:
#   TOP 30 USDT-M PERPETUALS BY 24H QUOTE VOLUME
#
# STRATEGY:
#   1) Find ONLY the most recent Tenkan/Kijun crossover
#   2) Tenkan = 9
#   3) Kijun  = 26
#   4) Ignore all older crosses
#   5) Cross candle is NOT included in Box
#   6) Box = 26 candles immediately BEFORE latest cross
#   7) BUY  = later closed candle closes ABOVE Box High
#   8) SELL = later closed candle closes BELOW Box Low
#   9) Swing = confirmed pivot with 2 candles left + 2 right
#  10) BUY SL  = Swing Low - small buffer
#  11) SELL SL = Swing High + small buffer
#  12) TP = 50% of Box Width
#  13) Maximum 1 open trade
#
# NOTE:
#   This is a SIGNAL / PAPER-TRADE scanner.
#   It does NOT place real Binance orders.
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

BASE_URL = "https://fapi.binance.com"

ENDPOINT_EXCHANGE_INFO = "/fapi/v1/exchangeInfo"
ENDPOINT_24H_TICKER = "/fapi/v1/ticker/24hr"
ENDPOINT_KLINES = "/fapi/v1/klines"
ENDPOINT_PRICE = "/fapi/v1/ticker/price"
ENDPOINT_TIME = "/fapi/v1/time"

TIMEFRAME = "5m"

TOP_SYMBOLS = 30

# Number of candles requested from Binance
KLINE_LIMIT = 300

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

BOX_SIZE = 26

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

VOLUME_LOOKBACK = 20

# TP = 50% of Box width
TP_BOX_PERCENT = 0.50

# SL buffer
SL_BUFFER_PERCENT = 0.0015

# Maximum one open virtual trade
MAX_OPEN_TRADES = 1

# A breakout must be recent.
#
# The scanner runs every 5 minutes.
# 2 means:
#   current closed candle
#   or previous closed candle
#
# This prevents the scanner from opening a trade on
# an ancient historical breakout when state is empty.
MAX_BREAKOUT_AGE_CANDLES = 2

STATE_FILE = "binance_price_action_state.json"
HISTORY_FILE = "binance_price_action_trade_history.json"

REQUEST_TIMEOUT = 20

MAX_RETRIES = 3

RETRY_SLEEP = 1.5


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Mozilla/5.0 Binance-Futures-Price-Action-Scanner",
        "Accept": "application/json",
    }
)


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_now_text():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


def ms_to_text(ms):
    try:
        dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "N/A"


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=None):
    try:
        number = float(value)

        if not math.isfinite(number):
            return default

        return number

    except Exception:
        return default


def fmt_price(value):
    value = safe_float(value)

    if value is None:
        return "N/A"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.1:
        return f"{value:.6f}"

    if value >= 0.01:
        return f"{value:.7f}"

    return f"{value:.10f}"


def fmt_pct(value):
    value = safe_float(value)

    if value is None:
        return "N/A"

    return f"{value:.2f}%"


def fmt_ratio(value):
    value = safe_float(value)

    if value is None:
        return "N/A"

    return f"{value:.2f}x"


# ============================================================
# JSON
# ============================================================

def load_json(path, default):
    try:

        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:

        print(
            f"[WARN] Could not load {path}: {e}",
            flush=True,
        )

        return default


def save_json(path, data):

    temp_path = path + ".tmp"

    try:

        with open(
            temp_path,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_path, path)

    except Exception as e:

        print(
            f"[ERROR] Could not save {path}: {e}",
            flush=True,
        )


# ============================================================
# API REQUEST
# ============================================================

def api_get(endpoint, params=None):

    url = BASE_URL + endpoint

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 200:

                try:
                    return response.json()
                except Exception as e:

                    raise RuntimeError(
                        f"Invalid JSON response: {e}"
                    )

            # Binance API error
            try:
                error_json = response.json()

                code = error_json.get(
                    "code",
                    response.status_code,
                )

                msg = error_json.get(
                    "msg",
                    response.text,
                )

                last_error = (
                    f"Binance API error "
                    f"{code}: {msg}"
                )

            except Exception:

                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

            # Rate limit / temporary errors
            if response.status_code in (
                418,
                429,
                500,
                502,
                503,
                504,
            ):

                sleep_time = RETRY_SLEEP * attempt

                print(
                    f"[WARN] API temporary error "
                    f"{response.status_code}, "
                    f"retry {attempt}/{MAX_RETRIES} "
                    f"in {sleep_time:.1f}s",
                    flush=True,
                )

                time.sleep(sleep_time)

                continue

            break

        except requests.RequestException as e:

            last_error = str(e)

            print(
                f"[WARN] Network error "
                f"{attempt}/{MAX_RETRIES}: {e}",
                flush=True,
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_SLEEP * attempt
                )

        except Exception as e:

            last_error = str(e)

            print(
                f"[WARN] API error "
                f"{attempt}/{MAX_RETRIES}: {e}",
                flush=True,
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    RETRY_SLEEP * attempt
                )

    raise RuntimeError(
        f"API request failed: "
        f"{endpoint} | {last_error}"
    )


# ============================================================
# BINANCE SERVER TIME
# ============================================================

def get_server_time():

    data = api_get(
        ENDPOINT_TIME
    )

    server_time = data.get(
        "serverTime"
    )

    if server_time is None:
        raise RuntimeError(
            "Binance serverTime missing"
        )

    return int(server_time)


# ============================================================
# EXCHANGE INFO
# ============================================================

def get_trading_symbols():

    data = api_get(
        ENDPOINT_EXCHANGE_INFO
    )

    symbols = []

    for item in data.get("symbols", []):

        try:

            if item.get("status") != "TRADING":
                continue

            if item.get("contractType") != "PERPETUAL":
                continue

            if item.get("quoteAsset") != "USDT":
                continue

            symbol = item.get("symbol")

            if not symbol:
                continue

            symbols.append(symbol)

        except Exception:
            continue

    return symbols


# ============================================================
# TOP 30 BY 24H QUOTE VOLUME
# ============================================================

def get_top_symbols(allowed_symbols):

    allowed = set(allowed_symbols)

    data = api_get(
        ENDPOINT_24H_TICKER
    )

    ranked = []

    for item in data:

        try:

            symbol = item.get("symbol")

            if symbol not in allowed:
                continue

            quote_volume = safe_float(
                item.get("quoteVolume"),
                0.0,
            )

            if quote_volume is None:
                quote_volume = 0.0

            ranked.append(
                {
                    "symbol": symbol,
                    "quote_volume": quote_volume,
                }
            )

        except Exception:
            continue

    ranked.sort(
        key=lambda x: x["quote_volume"],
        reverse=True,
    )

    return ranked[:TOP_SYMBOLS]


# ============================================================
# KLINES
# ============================================================

def get_klines(
    symbol,
    server_time_ms,
):

    data = api_get(
        ENDPOINT_KLINES,
        {
            "symbol": symbol,
            "interval": TIMEFRAME,
            "limit": KLINE_LIMIT,
        },
    )

    candles = []

    for row in data:

        if not isinstance(row, list):
            continue

        if len(row) < 11:
            continue

        try:

            open_time = int(row[0])
            open_price = float(row[1])
            high = float(row[2])
            low = float(row[3])
            close = float(row[4])
            volume = float(row[5])
            close_time = int(row[6])
            trades = int(row[8])

            # Only CLOSED candles.
            if close_time >= server_time_ms:
                continue

            candles.append(
                {
                    "open_time": open_time,
                    "close_time": close_time,
                    "open": open_price,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "trades": trades,
                }
            )

        except Exception:
            continue

    return candles


# ============================================================
# ICHIMOKU TENKAN / KIJUN
# ============================================================

def calculate_ichimoku(candles):

    n = len(candles)

    tenkan = [None] * n
    kijun = [None] * n

    for i in range(n):

        # Tenkan
        if i >= TENKAN_PERIOD - 1:

            window = candles[
                i - TENKAN_PERIOD + 1:
                i + 1
            ]

            highest = max(
                c["high"] for c in window
            )

            lowest = min(
                c["low"] for c in window
            )

            tenkan[i] = (
                highest + lowest
            ) / 2.0

        # Kijun
        if i >= KIJUN_PERIOD - 1:

            window = candles[
                i - KIJUN_PERIOD + 1:
                i + 1
            ]

            highest = max(
                c["high"] for c in window
            )

            lowest = min(
                c["low"] for c in window
            )

            kijun[i] = (
                highest + lowest
            ) / 2.0

    return tenkan, kijun


# ============================================================
# FIND ONLY MOST RECENT CROSS
# ============================================================

def find_latest_cross(
    candles,
    tenkan,
    kijun,
):

    latest = None

    for i in range(1, len(candles)):

        if (
            tenkan[i - 1] is None
            or kijun[i - 1] is None
            or tenkan[i] is None
            or kijun[i] is None
        ):
            continue

        prev_t = tenkan[i - 1]
        prev_k = kijun[i - 1]

        curr_t = tenkan[i]
        curr_k = kijun[i]

        # Bullish cross
        if (
            prev_t <= prev_k
            and curr_t > curr_k
        ):

            latest = {
                "type": "BUY",
                "index": i,
                "time": candles[i]["close_time"],
                "tenkan": curr_t,
                "kijun": curr_k,
            }

        # Bearish cross
        elif (
            prev_t >= prev_k
            and curr_t < curr_k
        ):

            latest = {
                "type": "SELL",
                "index": i,
                "time": candles[i]["close_time"],
                "tenkan": curr_t,
                "kijun": curr_k,
            }

    return latest


# ============================================================
# BOX
# ============================================================

def build_box(
    candles,
    cross,
):

    if cross is None:
        return None

    cross_index = cross["index"]

    start = (
        cross_index - BOX_SIZE
    )

    end = cross_index

    if start < 0:
        return None

    # IMPORTANT:
    # Cross candle itself is excluded.
    box_candles = candles[start:end]

    if len(box_candles) != BOX_SIZE:
        return None

    box_high = max(
        c["high"] for c in box_candles
    )

    box_low = min(
        c["low"] for c in box_candles
    )

    width = box_high - box_low

    if width <= 0:
        return None

    return {
        "start_index": start,
        "end_index": end - 1,
        "high": box_high,
        "low": box_low,
        "width": width,
        "candles": len(box_candles),
    }


# ============================================================
# FIND LATEST BREAKOUT
# ============================================================

def find_latest_breakout(
    candles,
    cross,
    box,
):

    if cross is None or box is None:
        return None

    cross_index = cross["index"]

    latest = None

    for i in range(
        cross_index + 1,
        len(candles),
    ):

        candle = candles[i]

        close = candle["close"]

        # BUY breakout
        if close > box["high"]:

            latest = {
                "type": "BUY",
                "index": i,
                "time": candle["close_time"],
                "price": close,
            }

        # SELL breakout
        elif close < box["low"]:

            latest = {
                "type": "SELL",
                "index": i,
                "time": candle["close_time"],
                "price": close,
            }

    return latest


# ============================================================
# CONFIRMED SWING LOW
# ============================================================

def is_swing_low(
    candles,
    index,
):

    if (
        index - PIVOT_LEFT < 0
        or index + PIVOT_RIGHT >= len(candles)
    ):
        return False

    center = candles[index]["low"]

    # Left candles
    for j in range(
        index - PIVOT_LEFT,
        index,
    ):

        if candles[j]["low"] <= center:
            return False

    # Right candles
    for j in range(
        index + 1,
        index + PIVOT_RIGHT + 1,
    ):

        if candles[j]["low"] <= center:
            return False

    return True


# ============================================================
# CONFIRMED SWING HIGH
# ============================================================

def is_swing_high(
    candles,
    index,
):

    if (
        index - PIVOT_LEFT < 0
        or index + PIVOT_RIGHT >= len(candles)
    ):
        return False

    center = candles[index]["high"]

    # Left candles
    for j in range(
        index - PIVOT_LEFT,
        index,
    ):

        if candles[j]["high"] >= center:
            return False

    # Right candles
    for j in range(
        index + 1,
        index + PIVOT_RIGHT + 1,
    ):

        if candles[j]["high"] >= center:
            return False

    return True


# ============================================================
# FIND LATEST VALID SWING
# ============================================================

def find_latest_swing(
    candles,
    cross,
    breakout,
):

    if cross is None or breakout is None:
        return None

    start = (
        cross["index"] + 1
    )

    end = breakout["index"]

    if end <= start:
        return None

    # Search newest to oldest
    for i in range(
        end - 1,
        start - 1,
        -1,
    ):

        if breakout["type"] == "BUY":

            if is_swing_low(
                candles,
                i,
            ):

                return {
                    "type": "LOW",
                    "index": i,
                    "time": candles[i]["close_time"],
                    "price": candles[i]["low"],
                }

        else:

            if is_swing_high(
                candles,
                i,
            ):

                return {
                    "type": "HIGH",
                    "index": i,
                    "time": candles[i]["close_time"],
                    "price": candles[i]["high"],
                }

    return None


# ============================================================
# VOLUME RATIO
# ============================================================

def calculate_volume_ratio(
    candles,
    index,
):

    if index <= 0:
        return None

    start = max(
        0,
        index - VOLUME_LOOKBACK,
    )

    previous = candles[start:index]

    if not previous:
        return None

    avg_volume = sum(
        c["volume"]
        for c in previous
    ) / len(previous)

    if avg_volume <= 0:
        return None

    current_volume = candles[index]["volume"]

    return (
        current_volume / avg_volume
    )


# ============================================================
# SIGNAL FRESHNESS
# ============================================================

def is_breakout_fresh(
    candles,
    breakout,
):

    if breakout is None:
        return False

    latest_index = len(candles) - 1

    age = (
        latest_index
        - breakout["index"]
    )

    return (
        0 <= age
        <= MAX_BREAKOUT_AGE_CANDLES
    )


# ============================================================
# ANALYZE ONE SYMBOL
# ============================================================

def analyze_symbol(
    symbol,
    quote_volume,
    candles,
):

    result = {
        "symbol": symbol,
        "quote_volume": quote_volume,
        "signal": None,
        "reason": None,
        "cross": None,
        "box": None,
        "breakout": None,
        "swing": None,
        "volume_ratio": None,
    }

    if len(candles) < (
        KIJUN_PERIOD
        + BOX_SIZE
        + 20
    ):

        result["reason"] = (
            "Not enough closed candles"
        )

        return result

    # --------------------------------------------------------
    # ICHIMOKU
    # --------------------------------------------------------

    tenkan, kijun = calculate_ichimoku(
        candles
    )

    # --------------------------------------------------------
    # LATEST CROSS ONLY
    # --------------------------------------------------------

    cross = find_latest_cross(
        candles,
        tenkan,
        kijun,
    )

    if cross is None:

        result["reason"] = (
            "No Tenkan/Kijun cross"
        )

        return result

    result["cross"] = cross

    # --------------------------------------------------------
    # BOX
    # --------------------------------------------------------

    box = build_box(
        candles,
        cross,
    )

    if box is None:

        result["reason"] = (
            "Box not available"
        )

        return result

    result["box"] = box

    # --------------------------------------------------------
    # BREAKOUT
    # --------------------------------------------------------

    breakout = find_latest_breakout(
        candles,
        cross,
        box,
    )

    if breakout is None:

        result["reason"] = (
            "No breakout"
        )

        return result

    result["breakout"] = breakout

    # --------------------------------------------------------
    # FRESHNESS FILTER
    # --------------------------------------------------------

    if not is_breakout_fresh(
        candles,
        breakout,
    ):

        result["reason"] = (
            "Breakout is too old"
        )

        return result

    # --------------------------------------------------------
    # DIRECTION CONSISTENCY
    #
    # We only accept breakout direction
    # matching latest cross direction.
    # --------------------------------------------------------

    if (
        breakout["type"]
        != cross["type"]
    ):

        result["reason"] = (
            "Breakout direction "
            "does not match latest cross"
        )

        return result

    # --------------------------------------------------------
    # SWING
    # --------------------------------------------------------

    swing = find_latest_swing(
        candles,
        cross,
        breakout,
    )

    if swing is None:

        result["reason"] = (
            "No confirmed swing"
        )

        return result

    result["swing"] = swing

    # --------------------------------------------------------
    # ENTRY
    # --------------------------------------------------------

    entry = breakout["price"]

    box_width = box["width"]

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if breakout["type"] == "BUY":

        if swing["type"] != "LOW":

            result["reason"] = (
                "Invalid BUY swing"
            )

            return result

        swing_low = swing["price"]

        sl = (
            swing_low
            * (1.0 - SL_BUFFER_PERCENT)
        )

        tp = (
            entry
            + box_width
            * TP_BOX_PERCENT
        )

        # Basic validation
        if not (
            sl < entry < tp
        ):

            result["reason"] = (
                "Invalid BUY SL/TP"
            )

            return result

        signal = "BUY"

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    else:

        if swing["type"] != "HIGH":

            result["reason"] = (
                "Invalid SELL swing"
            )

            return result

        swing_high = swing["price"]

        sl = (
            swing_high
            * (1.0 + SL_BUFFER_PERCENT)
        )

        tp = (
            entry
            - box_width
            * TP_BOX_PERCENT
        )

        # Basic validation
        if not (
            tp < entry < sl
        ):

            result["reason"] = (
                "Invalid SELL SL/TP"
            )

            return result

        signal = "SELL"

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_ratio = calculate_volume_ratio(
        candles,
        breakout["index"],
    )

    result["volume_ratio"] = volume_ratio

    # --------------------------------------------------------
    # FINAL SIGNAL
    # --------------------------------------------------------

    result["signal"] = signal

    result["entry"] = entry
    result["sl"] = sl
    result["tp"] = tp

    result["breakout_age"] = (
        len(candles) - 1
        - breakout["index"]
    )

    result["risk"] = abs(
        entry - sl
    )

    result["reward"] = abs(
        tp - entry
    )

    if result["risk"] > 0:

        result["rr"] = (
            result["reward"]
            / result["risk"]
        )

    else:

        result["rr"] = None

    return result


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    data = api_get(
        ENDPOINT_PRICE,
        {
            "symbol": symbol
        },
    )

    price = safe_float(
        data.get("price")
    )

    if price is None:
        raise RuntimeError(
            f"Invalid current price "
            f"for {symbol}"
        )

    return price


# ============================================================
# TRADE ID
# ============================================================

def make_trade_id(signal):

    return (
        f"{signal['symbol']}_"
        f"{signal['signal']}_"
        f"{signal['breakout']['time']}"
    )


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    signal,
):

    trade = {
        "id": make_trade_id(signal),
        "symbol": signal["symbol"],
        "side": signal["signal"],
        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],
        "opened_at": utc_now().isoformat(),
        "opened_at_ms": int(
            time.time() * 1000
        ),
        "cross_time": signal["cross"]["time"],
        "breakout_time": signal["breakout"]["time"],
        "swing_time": signal["swing"]["time"],
        "box_high": signal["box"]["high"],
        "box_low": signal["box"]["low"],
        "box_width": signal["box"]["width"],
        "volume_ratio": signal["volume_ratio"],
        "rr": signal["rr"],
        "breakout_age": signal["breakout_age"],
        "status": "OPEN",
    }

    return trade


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    state,
    history,
):

    trade = state.get("open_trade")

    if not trade:
        return None, False

    symbol = trade.get("symbol")

    if not symbol:

        state["open_trade"] = None

        return None, True

    try:

        current_price = get_current_price(
            symbol
        )

    except Exception as e:

        print(
            f"[WARN] Could not check "
            f"open trade {symbol}: {e}",
            flush=True,
        )

        return trade, False

    side = trade["side"]

    entry = safe_float(
        trade.get("entry")
    )

    sl = safe_float(
        trade.get("sl")
    )

    tp = safe_float(
        trade.get("tp")
    )

    if (
        entry is None
        or sl is None
        or tp is None
    ):

        print(
            "[ERROR] Open trade has "
            "invalid prices.",
            flush=True,
        )

        state["open_trade"] = None

        return None, True

    exit_reason = None

    exit_price = None

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if side == "BUY":

        if current_price <= sl:

            exit_reason = "SL"
            exit_price = sl

        elif current_price >= tp:

            exit_reason = "TP"
            exit_price = tp

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    elif side == "SELL":

        if current_price >= sl:

            exit_reason = "SL"
            exit_price = sl

        elif current_price <= tp:

            exit_reason = "TP"
            exit_price = tp

    # --------------------------------------------------------
    # STILL OPEN
    # --------------------------------------------------------

    if exit_reason is None:

        return trade, False

    # --------------------------------------------------------
    # PNL %
    # --------------------------------------------------------

    if side == "BUY":

        pnl_pct = (
            (
                exit_price
                - entry
            )
            / entry
        ) * 100.0

    else:

        pnl_pct = (
            (
                entry
                - exit_price
            )
            / entry
        ) * 100.0

    closed_trade = dict(trade)

    closed_trade.update(
        {
            "status": "CLOSED",
            "exit_reason": exit_reason,
            "exit_price": exit_price,
            "closed_at": utc_now().isoformat(),
            "pnl_pct": pnl_pct,
        }
    )

    history.append(
        closed_trade
    )

    state["open_trade"] = None

    print(
        f"[TRADE CLOSED] "
        f"{symbol} {side} "
        f"{exit_reason} "
        f"PnL={pnl_pct:.2f}%",
        flush=True,
    )

    return None, True


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    history,
):

    trades = len(history)

    wins = 0
    losses = 0
    breakeven = 0

    total_pnl = 0.0

    for trade in history:

        pnl = safe_float(
            trade.get("pnl_pct"),
            0.0,
        )

        total_pnl += pnl

        if pnl > 0.000001:

            wins += 1

        elif pnl < -0.000001:

            losses += 1

        else:

            breakeven += 1

    if trades > 0:

        win_rate = (
            wins / trades
        ) * 100.0

    else:

        win_rate = 0.0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "win_rate": win_rate,
        "total_pnl": total_pnl,
    }


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(
    text,
):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "[WARN] Telegram credentials "
            "are not configured.",
            flush=True,
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }

    try:

        response = SESSION.post(
            url,
            data=payload,
            timeout=REQUEST_TIMEOUT,
        )

        if response.status_code != 200:

            print(
                "[ERROR] Telegram error:",
                response.text[:500],
                flush=True,
            )

            return False

        return True

    except Exception as e:

        print(
            f"[ERROR] Telegram exception: {e}",
            flush=True,
        )

        return False


# ============================================================
# TELEGRAM SAFE TEXT
# ============================================================

def telegram_symbol(symbol):

    # Avoid Markdown surprises
    return str(symbol).replace(
        "_",
        "\\_",
    )


# ============================================================
# BUILD REPORT
# ============================================================

def build_report(
    state,
    history,
    top_symbols,
    diagnostics,
    new_signal,
    closed_trade,
):

    performance = calculate_performance(
        history
    )

    lines = []

    lines.append(
        "📡 *BINANCE FUTURES PRICE ACTION REPORT*"
    )

    lines.append(
        f"🕐 {utc_now_text()}"
    )

    lines.append(
        f"⏱ *{TIMEFRAME} CLOSED | TOP {TOP_SYMBOLS}*"
    )

    lines.append(
        "🤖 *TENKAN 9 / KIJUN 26 | BOX 26*"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append("📊 *PERFORMANCE*")

    lines.append(
        f"Trades {performance['trades']} | "
        f"🟢 {performance['wins']} | "
        f"🔴 {performance['losses']} | "
        f"⚪ {performance['breakeven']}"
    )

    lines.append(
        f"🏆 WR: "
        f"{performance['win_rate']:.1f}% | "
        f"PnL: "
        f"{performance['total_pnl']:.2f}%"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PIPELINE
    # --------------------------------------------------------

    lines.append("🔎 *SCANNER PIPELINE*")

    lines.append(
        f"Universe: "
        f"{diagnostics.get('universe', 0)}"
    )

    lines.append(
        f"Kline OK: "
        f"{diagnostics.get('kline_ok', 0)}"
    )

    lines.append(
        f"Latest Cross: "
        f"🟢 {diagnostics.get('cross_buy', 0)} | "
        f"🔴 {diagnostics.get('cross_sell', 0)} | "
        f"⚪ {diagnostics.get('cross_none', 0)}"
    )

    lines.append(
        f"Box: "
        f"✅ {diagnostics.get('box_ok', 0)} | "
        f"❌ {diagnostics.get('box_fail', 0)}"
    )

    lines.append(
        f"Breakout: "
        f"🟢 {diagnostics.get('breakout_buy', 0)} | "
        f"🔴 {diagnostics.get('breakout_sell', 0)} | "
        f"⚪ {diagnostics.get('breakout_none', 0)}"
    )

    lines.append(
        f"Fresh Breakout: "
        f"✅ {diagnostics.get('fresh_breakout', 0)} | "
        f"⌛ {diagnostics.get('stale_breakout', 0)}"
    )

    lines.append(
        f"Swing: "
        f"✅ {diagnostics.get('swing_ok', 0)} | "
        f"❌ {diagnostics.get('swing_fail', 0)}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # CLOSED TRADE
    # --------------------------------------------------------

    if closed_trade:

        lines.append(
            "🔔 *TRADE CLOSED*"
        )

        side_icon = (
            "🟢"
            if closed_trade["side"] == "BUY"
            else "🔴"
        )

        lines.append(
            f"{side_icon} "
            f"{telegram_symbol(closed_trade['symbol'])} "
            f"{closed_trade['side']}"
        )

        lines.append(
            f"Exit: "
            f"{fmt_price(closed_trade['exit_price'])}"
        )

        lines.append(
            f"Reason: "
            f"{closed_trade['exit_reason']}"
        )

        lines.append(
            f"PnL: "
            f"{closed_trade['pnl_pct']:.2f}%"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # OPEN TRADE
    # --------------------------------------------------------

    open_trade = state.get(
        "open_trade"
    )

    if open_trade:

        symbol = open_trade["symbol"]

        try:

            current_price = get_current_price(
                symbol
            )

        except Exception:

            current_price = None

        entry = open_trade["entry"]

        if current_price is not None:

            if open_trade["side"] == "BUY":

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

        else:

            live_pnl = None

        icon = (
            "🟢"
            if open_trade["side"] == "BUY"
            else "🔴"
        )

        lines.append(
            "📌 *OPEN TRADE*"
        )

        lines.append(
            f"{icon} "
            f"{telegram_symbol(symbol)} "
            f"{open_trade['side']}"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(entry)}"
        )

        lines.append(
            f"SL: "
            f"{fmt_price(open_trade['sl'])}"
        )

        lines.append(
            f"TP: "
            f"{fmt_price(open_trade['tp'])}"
        )

        if current_price is not None:

            lines.append(
                f"Current: "
                f"{fmt_price(current_price)}"
            )

        if live_pnl is not None:

            pnl_icon = (
                "🟢"
                if live_pnl >= 0
                else "🔴"
            )

            lines.append(
                f"{pnl_icon} Live P&L: "
                f"{live_pnl:.2f}%"
            )

        lines.append(
            f"Box High: "
            f"{fmt_price(open_trade['box_high'])}"
        )

        lines.append(
            f"Box Low: "
            f"{fmt_price(open_trade['box_low'])}"
        )

        lines.append(
            f"Box Width: "
            f"{fmt_price(open_trade['box_width'])}"
        )

        lines.append(
            f"RR: "
            f"{fmt_ratio(open_trade.get('rr'))}"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    if new_signal:

        icon = (
            "🟢"
            if new_signal["signal"] == "BUY"
            else "🔴"
        )

        lines.append(
            "🚨 *NEW SIGNAL*"
        )

        lines.append(
            f"{icon} "
            f"{telegram_symbol(new_signal['symbol'])} "
            f"*{new_signal['signal']}*"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(new_signal['entry'])}"
        )

        lines.append(
            f"SL: "
            f"{fmt_price(new_signal['sl'])}"
        )

        lines.append(
            f"TP: "
            f"{fmt_price(new_signal['tp'])}"
        )

        lines.append(
            f"RR: "
            f"{fmt_ratio(new_signal.get('rr'))}"
        )

        lines.append(
            f"Box High: "
            f"{fmt_price(new_signal['box']['high'])}"
        )

        lines.append(
            f"Box Low: "
            f"{fmt_price(new_signal['box']['low'])}"
        )

        lines.append(
            f"Box Width: "
            f"{fmt_price(new_signal['box']['width'])}"
        )

        lines.append(
            f"Cross: "
            f"{new_signal['cross']['type']} "
            f"{ms_to_text(new_signal['cross']['time'])}"
        )

        lines.append(
            f"Breakout: "
            f"{ms_to_text(new_signal['breakout']['time'])}"
        )

        lines.append(
            f"Swing: "
            f"{fmt_price(new_signal['swing']['price'])}"
        )

        lines.append(
            f"Volume: "
            f"{fmt_ratio(new_signal.get('volume_ratio'))}"
        )

        lines.append(
            f"Breakout Age: "
            f"{new_signal.get('breakout_age', 'N/A')} candle(s)"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

    # --------------------------------------------------------
    # NO NEW SIGNAL
    # --------------------------------------------------------

    else:

        if not open_trade:

            lines.append(
                "⚪ *NO NEW SIGNAL*"
            )

            lines.append(
                "Waiting for a fresh "
                "confirmed breakout."
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    # --------------------------------------------------------
    # TOP DIAGNOSTICS
    # --------------------------------------------------------

    reasons = diagnostics.get(
        "reasons",
        {},
    )

    if reasons:

        lines.append(
            "🧠 *MAIN REASONS*"
        )

        sorted_reasons = sorted(
            reasons.items(),
            key=lambda x: x[1],
            reverse=True,
        )

        for reason, count in sorted_reasons[:5]:

            lines.append(
                f"• {reason}: {count}"
            )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "==========================================",
        flush=True,
    )

    print(
        "BINANCE USD-M FUTURES PRICE ACTION SCANNER",
        flush=True,
    )

    print(
        "==========================================",
        flush=True,
    )

    print(
        f"Timeframe: {TIMEFRAME}",
        flush=True,
    )

    print(
        f"TOP: {TOP_SYMBOLS}",
        flush=True,
    )

    print(
        f"Tenkan: {TENKAN_PERIOD}",
        flush=True,
    )

    print(
        f"Kijun: {KIJUN_PERIOD}",
        flush=True,
    )

    print(
        f"Box: {BOX_SIZE}",
        flush=True,
    )

    print(
        f"Swing: {PIVOT_LEFT} + {PIVOT_RIGHT}",
        flush=True,
    )

    print(
        f"TP: {TP_BOX_PERCENT * 100:.0f}% Box",
        flush=True,
    )

    print(
        f"Max Open: {MAX_OPEN_TRADES}",
        flush=True,
    )

    print(
        "==========================================",
        flush=True,
    )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    state = load_json(
        STATE_FILE,
        {
            "open_trade": None,
            "last_run": None,
        },
    )

    history = load_json(
        HISTORY_FILE,
        [],
    )

    if not isinstance(history, list):

        history = []

    state["last_run"] = utc_now().isoformat()

    # --------------------------------------------------------
    # CHECK EXISTING TRADE
    # --------------------------------------------------------

    closed_trade = None

    try:

        previous_trade = state.get(
            "open_trade"
        )

        checked_trade, changed = check_open_trade(
            state,
            history,
        )

        if (
            previous_trade
            and changed
        ):

            # Most recently closed trade
            if history:

                closed_trade = history[-1]

    except Exception as e:

        print(
            "[ERROR] Open trade check failed:",
            e,
            flush=True,
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # GET SERVER TIME
    # --------------------------------------------------------

    try:

        server_time_ms = get_server_time()

        print(
            f"[INFO] Binance server time: "
            f"{ms_to_text(server_time_ms)}",
            flush=True,
        )

    except Exception as e:

        print(
            "[FATAL] Could not get Binance "
            f"server time: {e}",
            flush=True,
        )

        send_telegram(
            "❌ *BINANCE SCANNER ERROR*\n\n"
            f"`{str(e)[:700]}`"
        )

        save_json(
            STATE_FILE,
            state,
        )

        save_json(
            HISTORY_FILE,
            history,
        )

        return

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    try:

        allowed_symbols = get_trading_symbols()

        print(
            f"[INFO] Binance perpetual USDT symbols: "
            f"{len(allowed_symbols)}",
            flush=True,
        )

    except Exception as e:

        print(
            "[FATAL] Could not load exchangeInfo:",
            e,
            flush=True,
        )

        traceback.print_exc()

        send_telegram(
            "❌ *BINANCE SCANNER ERROR*\n\n"
            f"`exchangeInfo failed: {str(e)[:600]}`"
        )

        save_json(
            STATE_FILE,
            state,
        )

        save_json(
            HISTORY_FILE,
            history,
        )

        return

    # --------------------------------------------------------
    # TOP 30
    # --------------------------------------------------------

    try:

        top_symbols = get_top_symbols(
            allowed_symbols
        )

    except Exception as e:

        print(
            "[FATAL] Could not load 24h ticker:",
            e,
            flush=True,
        )

        traceback.print_exc()

        send_telegram(
            "❌ *BINANCE SCANNER ERROR*\n\n"
            f"`24h ticker failed: {str(e)[:600]}`"
        )

        save_json(
            STATE_FILE,
            state,
        )

        save_json(
            HISTORY_FILE,
            history,
        )

        return

    print(
        f"[INFO] TOP {len(top_symbols)}:",
        flush=True,
    )

    for i, item in enumerate(
        top_symbols,
        start=1,
    ):

        print(
            f"{i:02d}. "
            f"{item['symbol']} "
            f"24hQuote={item['quote_volume']:,.0f}",
            flush=True,
        )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    diagnostics = {
        "universe": len(top_symbols),
        "kline_ok": 0,
        "cross_buy": 0,
        "cross_sell": 0,
        "cross_none": 0,
        "box_ok": 0,
        "box_fail": 0,
        "breakout_buy": 0,
        "breakout_sell": 0,
        "breakout_none": 0,
        "fresh_breakout": 0,
        "stale_breakout": 0,
        "swing_ok": 0,
        "swing_fail": 0,
        "errors": 0,
        "reasons": {},
    }

    candidates = []

    # --------------------------------------------------------
    # ANALYZE TOP 30
    # --------------------------------------------------------

    for rank, item in enumerate(
        top_symbols,
        start=1,
    ):

        symbol = item["symbol"]

        quote_volume = item[
            "quote_volume"
        ]

        print(
            f"\n[{rank:02d}/{len(top_symbols):02d}] "
            f"Analyzing {symbol}",
            flush=True,
        )

        try:

            candles = get_klines(
                symbol,
                server_time_ms,
            )

            if len(candles) < (
                KIJUN_PERIOD
                + BOX_SIZE
                + 20
            ):

                reason = (
                    "Not enough closed candles"
                )

                diagnostics["reasons"][reason] = (
                    diagnostics["reasons"].get(
                        reason,
                        0,
                    )
                    + 1
                )

                print(
                    f"[SKIP] {symbol}: "
                    f"{reason} "
                    f"({len(candles)} candles)",
                    flush=True,
                )

                continue

            diagnostics["kline_ok"] += 1

            result = analyze_symbol(
                symbol,
                quote_volume,
                candles,
            )

            # ------------------------------------------------
            # CROSS DIAGNOSTICS
            # ------------------------------------------------

            cross = result.get(
                "cross"
            )

            if cross:

                if cross["type"] == "BUY":

                    diagnostics["cross_buy"] += 1

                else:

                    diagnostics["cross_sell"] += 1

            else:

                diagnostics["cross_none"] += 1

            # ------------------------------------------------
            # BOX
            # ------------------------------------------------

            if result.get("box"):

                diagnostics["box_ok"] += 1

            else:

                diagnostics["box_fail"] += 1

            # ------------------------------------------------
            # BREAKOUT
            # ------------------------------------------------

            breakout = result.get(
                "breakout"
            )

            if breakout:

                if breakout["type"] == "BUY":

                    diagnostics[
                        "breakout_buy"
                    ] += 1

                else:

                    diagnostics[
                        "breakout_sell"
                    ] += 1

                if is_breakout_fresh(
                    candles,
                    breakout,
                ):

                    diagnostics[
                        "fresh_breakout"
                    ] += 1

                else:

                    diagnostics[
                        "stale_breakout"
                    ] += 1

            else:

                diagnostics[
                    "breakout_none"
                ] += 1

            # ------------------------------------------------
            # SWING
            # ------------------------------------------------

            if result.get("swing"):

                diagnostics[
                    "swing_ok"
                ] += 1

            else:

                diagnostics[
                    "swing_fail"
                ] += 1

            # ------------------------------------------------
            # FINAL SIGNAL
            # ------------------------------------------------

            if result.get("signal"):

                candidates.append(
                    result
                )

                print(
                    f"[SIGNAL] "
                    f"{symbol} "
                    f"{result['signal']} "
                    f"Entry={fmt_price(result['entry'])} "
                    f"SL={fmt_price(result['sl'])} "
                    f"TP={fmt_price(result['tp'])} "
                    f"RR={fmt_ratio(result.get('rr'))} "
                    f"Vol={fmt_ratio(result.get('volume_ratio'))}",
                    flush=True,
                )

            else:

                reason = result.get(
                    "reason"
                ) or "No signal"

                diagnostics["reasons"][reason] = (
                    diagnostics["reasons"].get(
                        reason,
                        0,
                    )
                    + 1
                )

                print(
                    f"[NO SIGNAL] "
                    f"{symbol}: "
                    f"{reason}",
                    flush=True,
                )

        except Exception as e:

            diagnostics[
                "errors"
            ] += 1

            reason = (
                f"API/analysis error: "
                f"{type(e).__name__}"
            )

            diagnostics["reasons"][reason] = (
                diagnostics["reasons"].get(
                    reason,
                    0,
                )
                + 1
            )

            print(
                f"[ERROR] {symbol}: {e}",
                flush=True,
            )

            traceback.print_exc()

            continue

    # --------------------------------------------------------
    # SORT CANDIDATES
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            safe_float(
                x.get("volume_ratio"),
                0.0,
            ),
            safe_float(
                x.get("quote_volume"),
                0.0,
            ),
            x["breakout"]["time"],
        ),
        reverse=True,
    )

    # --------------------------------------------------------
    # OPEN NEW TRADE
    # --------------------------------------------------------

    new_signal = None

    existing_trade = state.get(
        "open_trade"
    )

    if (
        existing_trade is None
        and candidates
        and MAX_OPEN_TRADES >= 1
    ):

        # Prevent reopening exact historical trade
        used_trade_ids = {
            str(t.get("id"))
            for t in history
            if t.get("id")
        }

        selected = None

        for candidate in candidates:

            candidate_id = make_trade_id(
                candidate
            )

            if candidate_id in used_trade_ids:
                continue

            selected = candidate

            break

        if selected:

            new_trade = open_trade(
                selected
            )

            state["open_trade"] = new_trade

            new_signal = selected

            print(
                "\n==========================================",
                flush=True,
            )

            print(
                "[NEW TRADE OPENED]",
                flush=True,
            )

            print(
                f"Symbol: "
                f"{new_trade['symbol']}",
                flush=True,
            )

            print(
                f"Side: "
                f"{new_trade['side']}",
                flush=True,
            )

            print(
                f"Entry: "
                f"{fmt_price(new_trade['entry'])}",
                flush=True,
            )

            print(
                f"SL: "
                f"{fmt_price(new_trade['sl'])}",
                flush=True,
            )

            print(
                f"TP: "
                f"{fmt_price(new_trade['tp'])}",
                flush=True,
            )

            print(
                f"RR: "
                f"{fmt_ratio(new_trade.get('rr'))}",
                flush=True,
            )

            print(
                "==========================================",
                flush=True,
            )

    elif existing_trade:

        print(
            "\n[INFO] One trade already open:",
            flush=True,
        )

        print(
            f"{existing_trade['symbol']} "
            f"{existing_trade['side']}",
            flush=True,
        )

    else:

        print(
            "\n[INFO] No valid fresh signal.",
            flush=True,
        )

    # --------------------------------------------------------
    # SAVE STATE
    # --------------------------------------------------------

    state["last_run"] = utc_now().isoformat()

    save_json(
        STATE_FILE,
        state,
    )

    save_json(
        HISTORY_FILE,
        history,
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    try:

        report = build_report(
            state=state,
            history=history,
            top_symbols=top_symbols,
            diagnostics=diagnostics,
            new_signal=new_signal,
            closed_trade=closed_trade,
        )

        print(
            "\n==========================================",
            flush=True,
        )

        print(
            report,
            flush=True,
        )

        print(
            "==========================================",
            flush=True,
        )

        send_telegram(
            report
        )

    except Exception as e:

        print(
            "[ERROR] Report generation failed:",
            e,
            flush=True,
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    print(
        "\n==========================================",
        flush=True,
    )

    print(
        "SCAN COMPLETED",
        flush=True,
    )

    print(
        f"Candidates: {len(candidates)}",
        flush=True,
    )

    print(
        f"API/Analysis Errors: "
        f"{diagnostics['errors']}",
        flush=True,
    )

    if state.get("open_trade"):

        print(
            f"Open Trade: "
            f"{state['open_trade']['symbol']} "
            f"{state['open_trade']['side']}",
            flush=True,
        )

    else:

        print(
            "Open Trade: NONE",
            flush=True,
        )

    print(
        "==========================================",
        flush=True,
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "\n[STOPPED] Keyboard interrupt.",
            flush=True,
        )

    except Exception as e:

        print(
            "\n[FATAL ERROR]",
            flush=True,
        )

        print(
            str(e),
            flush=True,
        )

        traceback.print_exc()

        # Telegram fatal error
        try:

            send_telegram(
                "❌ *BINANCE SCANNER FATAL ERROR*\n\n"
                f"`{str(e)[:800]}`"
            )

        except Exception:
            pass

        raise
