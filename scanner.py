# ============================================================
# CRYPTO TENKAN/KIJUN 26 BOX BREAKOUT SCANNER
# ============================================================
# Kraken Futures
# 5m CLOSED CANDLES
#
# STRATEGY
# ------------------------------------------------------------
# 1) Select TOP 30 markets with abnormal volume
# 2) Detect Tenkan / Kijun crossover on 5m
# 3) At crossover:
#       Box High = highest HIGH of previous 26 candles
#       Box Low  = lowest LOW  of previous 26 candles
#
# 4) After crossover:
#       BUY  = candle CLOSE > Box High
#       SELL = candle CLOSE < Box Low
#
# 5) SL:
#       BUY  = slightly below last valid Swing Low
#       SELL = slightly above last valid Swing High
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
# STATE FILE:
#       price_action_state.json
#
# HISTORY:
#       price_action_trade_history.json
#
# ENV:
#       TELEGRAM_BOT_TOKEN
#       TELEGRAM_CHAT_ID
#
# ============================================================

import os
import json
import math
import time
import statistics
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
SL_BUFFER_PCT = 0.0015   # 0.15%

# Number of candles used for abnormal-volume calculation
VOLUME_LOOKBACK = 20

# Minimum abnormal-volume ratio
MIN_VOLUME_RATIO = 1.20

# Minimum amount of candles required
MIN_CANDLES = 100

# State files
STATE_FILE = "price_action_state.json"
HISTORY_FILE = "price_action_trade_history.json"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Request timeout
REQUEST_TIMEOUT = 20

# Retry
MAX_RETRIES = 3


# ============================================================
# GLOBAL SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent": "Crypto-Tenkan-Kijun-Scanner/1.0"
    }
)


# ============================================================
# UTILS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_string():
    return now_utc().strftime("%Y-%m-%d %H:%M:%S UTC")


def safe_float(value, default=0.0):
    try:
        return float(value)
    except Exception:
        return default


def pct_change(current, reference):
    if reference == 0:
        return 0.0
    return ((current - reference) / reference) * 100.0


def format_price(price):
    if price is None:
        return "-"
    price = float(price)

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


# ============================================================
# JSON STATE
# ============================================================

def load_json(path, default):
    try:
        if not os.path.exists(path):
            return default

        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:
        return default


def save_json(path, data):
    temp = path + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp, path)


def load_state():
    default = {
        "open_trade": None,
        "last_scan_candle": None,
        "last_signals": {}
    }

    state = load_json(STATE_FILE, default)

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
    save_json(STATE_FILE, state)


def load_history():
    default = {
        "trades": []
    }

    data = load_json(HISTORY_FILE, default)

    if not isinstance(data, dict):
        data = default

    if "trades" not in data:
        data["trades"] = []

    return data


def save_history(history):
    save_json(HISTORY_FILE, history)


# ============================================================
# HTTP
# ============================================================

def get_json(url, params=None):
    last_error = None

    for attempt in range(MAX_RETRIES):

        try:
            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            return data

        except Exception as exc:
            last_error = exc

            if attempt < MAX_RETRIES - 1:
                time.sleep(1.5)

    print(f"[ERROR] HTTP: {url}")
    print(f"[ERROR] {last_error}")

    return None


# ============================================================
# KRAKEN FUTURES
# ============================================================

def get_instruments():
    url = f"{BASE_URL}/derivatives/api/v3/instruments"

    data = get_json(url)

    if not data:
        return []

    instruments = data.get("instruments", [])

    result = []

    for item in instruments:

        symbol = item.get("symbol", "")
        tradeable = item.get("tradeable", True)

        if not symbol:
            continue

        if not tradeable:
            continue

        # We only want perpetual contracts
        if not symbol.startswith("PI_"):
            continue

        result.append(item)

    return result


def get_tickers():
    url = f"{BASE_URL}/derivatives/api/v3/tickers"

    data = get_json(url)

    if not data:
        return []

    return data.get("tickers", [])


def get_candles(symbol, resolution=5, count=150):
    """
    Kraken Futures charts API.

    We request a sufficiently large time range and then
    keep the most recent candles.
    """

    url = f"{BASE_URL}/api/charts/v1/trade/{symbol}/{resolution}"

    # resolution is in minutes
    seconds = resolution * 60

    end_time = int(time.time())
    start_time = end_time - (count * seconds * 2)

    params = {
        "from": start_time,
        "to": end_time
    }

    data = get_json(url, params=params)

    if not data:
        return []

    candles = data.get("candles", [])

    result = []

    for c in candles:

        try:
            ts = int(
                c.get("time")
                or c.get("timestamp")
                or 0
            )

            o = safe_float(c.get("open"))
            h = safe_float(c.get("high"))
            l = safe_float(c.get("low"))
            cl = safe_float(c.get("close"))
            v = safe_float(c.get("volume"))

            if ts == 0:
                continue

            if cl <= 0:
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

        except Exception:
            continue

    result.sort(key=lambda x: x["time"])

    # Remove duplicates
    unique = {}

    for c in result:
        unique[c["time"]] = c

    result = list(unique.values())

    result.sort(key=lambda x: x["time"])

    # Keep requested amount
    if len(result) > count:
        result = result[-count:]

    return result


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(c):
    return abs(c["close"] - c["open"])


def candle_range(c):
    return max(c["high"] - c["low"], 1e-12)


def upper_wick(c):
    return c["high"] - max(c["open"], c["close"])


def lower_wick(c):
    return min(c["open"], c["close"]) - c["low"]


# ============================================================
# ICHIMOKU
# ============================================================

def donchian_value(candles, index, length):
    if index - length + 1 < 0:
        return None

    window = candles[
        index - length + 1:
        index + 1
    ]

    highs = [c["high"] for c in window]
    lows = [c["low"] for c in window]

    return (
        max(highs) +
        min(lows)
    ) / 2.0


def calculate_tenkan(candles, index):
    return donchian_value(
        candles,
        index,
        9
    )


def calculate_kijun(candles, index):
    return donchian_value(
        candles,
        index,
        26
    )


def crossover_at(candles, index):
    """
    Returns:
        BUY
        SELL
        None

    Cross is calculated using CLOSED candles only.
    """

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

    if None in (
        tenkan_prev,
        kijun_prev,
        tenkan_now,
        kijun_now
    ):
        return None

    # Bullish cross
    if (
        tenkan_prev <= kijun_prev
        and tenkan_now > kijun_now
    ):
        return "BUY"

    # Bearish cross
    if (
        tenkan_prev >= kijun_prev
        and tenkan_now < kijun_now
    ):
        return "SELL"

    return None


# ============================================================
# 26 CANDLE BOX
# ============================================================

def get_box(candles, cross_index):

    start = cross_index - BOX_LENGTH
    end = cross_index

    if start < 0:
        return None

    previous = candles[start:end]

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

    width = box_high - box_low

    if width <= 0:
        return None

    return {
        "high": box_high,
        "low": box_low,
        "width": width
    }


# ============================================================
# SWING DETECTION
# ============================================================

def is_swing_low(candles, index):

    if index - SWING_LEFT < 0:
        return False

    if index + SWING_RIGHT >= len(candles):
        return False

    current = candles[index]["low"]

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


def is_swing_high(candles, index):

    if index - SWING_LEFT < 0:
        return False

    if index + SWING_RIGHT >= len(candles):
        return False

    current = candles[index]["high"]

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
        len(candles) - SWING_RIGHT - 1,
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
        len(candles) - SWING_RIGHT - 1,
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
# ABNORMAL VOLUME
# ============================================================

def volume_ratio(candles):

    if len(candles) < VOLUME_LOOKBACK + 2:
        return 0.0

    # Last CLOSED candle
    current_volume = candles[-1]["volume"]

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

    avg_volume = statistics.mean(volumes)

    if avg_volume <= 0:
        return 0.0

    return current_volume / avg_volume


# ============================================================
# TOP 30 ABNORMAL VOLUME
# ============================================================

def get_top_volume_markets():

    instruments = get_instruments()

    if not instruments:
        print("[ERROR] No instruments")
        return []

    tickers = get_tickers()

    ticker_map = {}

    for t in tickers:

        symbol = t.get("symbol")

        if symbol:
            ticker_map[symbol] = t

    candidates = []

    print(
        f"[INFO] Markets available: "
        f"{len(instruments)}"
    )

    # First use ticker information to avoid
    # downloading candles for every contract.
    for instrument in instruments:

        symbol = instrument.get("symbol")

        ticker = ticker_map.get(symbol)

        if not ticker:
            continue

        volume24 = safe_float(
            ticker.get("vol24h")
            or ticker.get("volume24h")
            or ticker.get("volume")
        )

        if volume24 <= 0:
            continue

        candidates.append(
            {
                "symbol": symbol,
                "volume24": volume24
            }
        )

    # Keep a wider pool first
    candidates.sort(
        key=lambda x: x["volume24"],
        reverse=True
    )

    candidates = candidates[:80]

    ranked = []

    print(
        f"[INFO] Checking abnormal volume "
        f"for {len(candidates)} candidates..."
    )

    for item in candidates:

        symbol = item["symbol"]

        candles = get_candles(
            symbol,
            TIMEFRAME,
            80
        )

        if len(candles) < MIN_CANDLES - 20:
            continue

        ratio = volume_ratio(candles)

        if ratio < MIN_VOLUME_RATIO:
            continue

        ranked.append(
            {
                "symbol": symbol,
                "volume24": item["volume24"],
                "volume_ratio": ratio
            }
        )

    ranked.sort(
        key=lambda x: (
            x["volume_ratio"],
            math.log1p(x["volume24"])
        ),
        reverse=True
    )

    return ranked[:TOP_N]


# ============================================================
# SIGNAL SEARCH
# ============================================================

def find_latest_setup(
    symbol,
    candles
):

    if len(candles) < 80:
        return None

    # We use CLOSED candles only.
    #
    # Ignore the latest candle because it may
    # still be forming depending on API timing.
    closed = candles[:-1]

    if len(closed) < 60:
        return None

    # Search recent crossovers.
    #
    # We don't search forever because a very old
    # crossover should not generate a fresh trade.
    max_age = 40

    start = max(
        30,
        len(closed) - max_age
    )

    latest_setup = None

    for i in range(start, len(closed)):

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
            "cross_time": closed[i]["time"],
            "box_high": box["high"],
            "box_low": box["low"],
            "box_width": box["width"]
        }

    return latest_setup


# ============================================================
# BREAKOUT CHECK
# ============================================================

def check_breakout(
    setup,
    candles
):

    if setup is None:
        return None

    closed = candles[:-1]

    cross_index = setup["cross_index"]

    # Need candles after cross
    if cross_index >= len(closed) - 1:
        return None

    # Search only after crossover
    for i in range(
        cross_index + 1,
        len(closed)
    ):

        candle = closed[i]

        # BUY
        if setup["side"] == "BUY":

            if candle["close"] <= setup["box_high"]:
                continue

            # Need valid swing low.
            # We use candles up to the breakout candle.
            #
            # A swing needs right-side confirmation,
            # therefore only swings before the final
            # 2 candles are eligible.
            swing_end = i - SWING_RIGHT

            swing = find_last_swing_low(
                closed,
                cross_index,
                swing_end
            )

            if not swing:
                continue

            entry = candle["close"]

            sl = (
                swing["price"] *
                (1.0 - SL_BUFFER_PCT)
            )

            if sl >= entry:
                continue

            tp = (
                entry +
                setup["box_width"] *
                TP_BOX_MULTIPLIER
            )

            return {
                "side": "BUY",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "box_high": setup["box_high"],
                "box_low": setup["box_low"],
                "box_width": setup["box_width"],
                "cross_time": setup["cross_time"],
                "breakout_time": candle["time"],
                "swing_price": swing["price"],
                "breakout_index": i
            }

        # SELL
        if setup["side"] == "SELL":

            if candle["close"] >= setup["box_low"]:
                continue

            swing_end = i - SWING_RIGHT

            swing = find_last_swing_high(
                closed,
                cross_index,
                swing_end
            )

            if not swing:
                continue

            entry = candle["close"]

            sl = (
                swing["price"] *
                (1.0 + SL_BUFFER_PCT)
            )

            if sl <= entry:
                continue

            tp = (
                entry -
                setup["box_width"] *
                TP_BOX_MULTIPLIER
            )

            return {
                "side": "SELL",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "box_high": setup["box_high"],
                "box_low": setup["box_low"],
                "box_width": setup["box_width"],
                "cross_time": setup["cross_time"],
                "breakout_time": candle["time"],
                "swing_price": swing["price"],
                "breakout_index": i
            }

    return None


# ============================================================
# CURRENT PRICE
# ============================================================

def get_current_price(symbol):

    tickers = get_tickers()

    for ticker in tickers:

        if ticker.get("symbol") != symbol:
            continue

        price = (
            ticker.get("last")
            or ticker.get("lastPrice")
            or ticker.get("markPrice")
        )

        price = safe_float(price)

        if price > 0:
            return price

    return None


# ============================================================
# LIVE P&L
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
# TRADE RESULT
# ============================================================

def calculate_r(trade, exit_price):

    entry = trade["entry"]
    sl = trade["sl"]

    risk = abs(entry - sl)

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
# CHECK OPEN TRADE
# ============================================================

def update_open_trade(
    state,
    history
):

    trade = state.get("open_trade")

    if not trade:
        return False

    symbol = trade["symbol"]

    price = get_current_price(symbol)

    if price is None:
        print(
            f"[WARN] No price for {symbol}"
        )
        return False

    trade["current_price"] = price

    trade["live_pnl_pct"] = calculate_live_pnl(
        trade["side"],
        trade["entry"],
        price
    )

    # BUY
    if trade["side"] == "BUY":

        # Conservative rule:
        # if both TP and SL are somehow touched
        # in the same update, SL is prioritized.
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

    # SELL
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
# CLOSE TRADE
# ============================================================

def close_trade(
    state,
    history,
    exit_price,
    result_r,
    result
):

    trade = state["open_trade"]

    if not trade:
        return

    trade["exit"] = exit_price
    trade["result"] = result
    trade["result_r"] = result_r
    trade["closed_at"] = int(time.time())

    history["trades"].append(trade)

    # Keep history manageable
    if len(history["trades"]) > 1000:
        history["trades"] = history["trades"][-1000:]

    state["open_trade"] = None

    save_history(history)
    save_state(state)

    print(
        f"[TRADE CLOSED] "
        f"{trade['symbol']} "
        f"{result} "
        f"R={result_r:+.2f}"
    )

    send_telegram(
        build_close_message(
            trade
        )
    )


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    state,
    signal,
    volume_ratio_value
):

    if state.get("open_trade"):
        return False

    trade = {
        "symbol": signal["symbol"],
        "side": signal["side"],

        "entry": signal["entry"],
        "sl": signal["sl"],
        "tp": signal["tp"],

        "box_high": signal["box_high"],
        "box_low": signal["box_low"],
        "box_width": signal["box_width"],

        "swing_price": signal["swing_price"],

        "cross_time": signal["cross_time"],
        "breakout_time": signal["breakout_time"],

        "opened_at": int(time.time()),

        "current_price": signal["entry"],
        "live_pnl_pct": 0.0,

        "volume_ratio": volume_ratio_value
    }

    state["open_trade"] = trade

    save_state(state)

    print(
        f"[OPEN] "
        f"{trade['symbol']} "
        f"{trade['side']} "
        f"Entry={trade['entry']}"
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

def performance(history):

    trades = history.get(
        "trades",
        []
    )

    wins = 0
    losses = 0
    total_r = 0.0

    for trade in trades:

        r = safe_float(
            trade.get("result_r")
        )

        total_r += r

        if trade.get("result") == "TP":
            wins += 1

        elif trade.get("result") == "SL":
            losses += 1

    closed = wins + losses

    if closed > 0:
        wr = (
            wins / closed
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

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print("[WARN] TELEGRAM_BOT_TOKEN missing")
        return False

    if not TELEGRAM_CHAT_ID:
        print("[WARN] TELEGRAM_CHAT_ID missing")
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        if response.ok:
            return True

        print(
            "[TELEGRAM ERROR]",
            response.text
        )

    except Exception as exc:
        print(
            "[TELEGRAM EXCEPTION]",
            exc
        )

    return False


# ============================================================
# TELEGRAM OPEN
# ============================================================

def build_open_message(trade):

    entry = trade["entry"]
    sl = trade["sl"]
    tp = trade["tp"]

    sl_pct = abs(
        pct_change(sl, entry)
    )

    tp_pct = abs(
        pct_change(tp, entry)
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
        f"{datetime.fromtimestamp("
        f"trade['cross_time'], "
        f"tz=timezone.utc"
        f").strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"⚡ Breakout Close: "
        f"{format_price(entry)}\n"
        f"🦴 Swing: "
        f"{format_price(trade['swing_price'])}\n"
        f"📊 Volume Ratio: "
        f"{trade['volume_ratio']:.2f}x\n\n"
        "📂 <b>OPEN: 1/1</b>"
    )


# ============================================================
# TELEGRAM CLOSE
# ============================================================

def build_close_message(trade):

    result = trade.get(
        "result",
        "?"
    )

    result_r = safe_float(
        trade.get("result_r")
    )

    if result == "TP":
        icon = "🟢"
    elif result == "SL":
        icon = "🔴"
    else:
        icon = "⚪"

    return (
        f"{icon} <b>TRADE CLOSED</b>\n"
        f"🕐 {now_string()}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📌 {trade['symbol']} "
        f"{trade['side']}\n"
        f"Entry: {format_price(trade['entry'])}\n"
        f"Exit: {format_price(trade['exit'])}\n"
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

        open_text = (
            f"📌 <b>OPEN TRADE</b>\n"
            f"{open_trade['symbol']} "
            f"{open_trade['side']}\n"
            f"Entry: "
            f"{format_price(open_trade['entry'])}\n"
            f"SL: "
            f"{format_price(open_trade['sl'])} "
            f"({format_pct("
            f"-abs(pct_change("
            f"open_trade['sl'], "
            f"open_trade['entry']"
            f"))"
            f")})\n"
            f"TP: "
            f"{format_price(open_trade['tp'])} "
            f"({format_pct("
            f"abs(pct_change("
            f"open_trade['tp'], "
            f"open_trade['entry']"
            f"))"
            f")})\n"
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
        f"⚡ THIS RUN: {events} EVENTS\n"
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

    candles = get_candles(
        symbol,
        TIMEFRAME,
        160
    )

    if len(candles) < 80:
        return None

    ratio = volume_ratio(
        candles
    )

    if ratio < MIN_VOLUME_RATIO:
        return None

    setup = find_latest_setup(
        symbol,
        candles
    )

    if setup is None:
        return None

    signal = check_breakout(
        setup,
        candles
    )

    if signal is None:
        return None

    signal["symbol"] = symbol

    # Prevent duplicate signal
    signal_id = (
        f"{symbol}_"
        f"{signal['side']}_"
        f"{signal['breakout_time']}"
    )

    previous = state.get(
        "last_signals",
        {}
    )

    if previous.get(symbol) == signal_id:
        return None

    state["last_signals"][symbol] = signal_id

    return {
        "signal": signal,
        "volume_ratio": ratio
    }


# ============================================================
# MAIN SCANNER
# ============================================================

def run():

    print("=" * 70)
    print("CRYPTO TENKAN/KIJUN 26 BOX BREAKOUT SCANNER")
    print("=" * 70)
    print(f"Timeframe: {TIMEFRAME}m")
    print(f"TOP: {TOP_N}")
    print("MAX OPEN: 1")
    print(f"BOX: {BOX_LENGTH}")
    print("TP: 50% BOX WIDTH")
    print("SWING: 2 LEFT / 2 RIGHT")
    print("=" * 70)

    state = load_state()
    history = load_history()

    # --------------------------------------------------------
    # FIRST: UPDATE EXISTING TRADE
    # --------------------------------------------------------

    if state.get("open_trade"):

        print(
            "[INFO] Updating open trade..."
        )

        update_open_trade(
            state,
            history
        )

        save_state(state)
        save_history(history)

    # --------------------------------------------------------
    # IF TRADE IS STILL OPEN, DO NOT OPEN ANOTHER
    # --------------------------------------------------------

    if state.get("open_trade"):

        trade = state["open_trade"]

        print(
            f"[INFO] OPEN TRADE: "
            f"{trade['symbol']} "
            f"{trade['side']}"
        )

        print(
            f"[INFO] Current: "
            f"{format_price("
            f"trade.get('current_price')"
            f")}"
        )

        print(
            f"[INFO] Live P&L: "
            f"{trade.get('live_pnl_pct', 0):+.2f}%"
        )

        # Still send report
        send_telegram(
            build_report(
                history,
                state,
                TOP_N,
                0
            )
        )

        return

    # --------------------------------------------------------
    # TOP 30 ABNORMAL VOLUME
    # --------------------------------------------------------

    ranked = get_top_volume_markets()

    if not ranked:

        print(
            "[ERROR] No abnormal-volume markets"
        )

        send_telegram(
            "⚠️ <b>PRICE ACTION SCANNER</b>\n"
            "No abnormal-volume markets found."
        )

        return

    print(
        f"[INFO] TOP {len(ranked)} "
        f"ABNORMAL VOLUME:"
    )

    for i, item in enumerate(
        ranked,
        start=1
    ):

        print(
            f"{i:02d}. "
            f"{item['symbol']} "
            f"Ratio={item['volume_ratio']:.2f}x"
        )

    # --------------------------------------------------------
    # SCAN SIGNALS
    # --------------------------------------------------------

    events = 0

    for item in ranked:

        if state.get("open_trade"):
            break

        symbol = item["symbol"]

        print(
            f"[SCAN] {symbol} "
            f"VOL={item['volume_ratio']:.2f}x"
        )

        try:

            result = scan_market(
                item,
                state
            )

            if result is None:
                continue

            signal = result["signal"]
            ratio = result["volume_ratio"]

            print(
                "[SIGNAL] "
                f"{symbol} "
                f"{signal['side']} "
                f"Entry={format_price(signal['entry'])} "
                f"SL={format_price(signal['sl'])} "
                f"TP={format_price(signal['tp'])} "
                f"VOL={ratio:.2f}x"
            )

            opened = open_trade(
                state,
                signal,
                ratio
            )

            if opened:
                events += 1

                # Save immediately
                save_state(state)

                break

        except Exception as exc:

            print(
                f"[ERROR] {symbol}: "
                f"{exc}"
            )

    # --------------------------------------------------------
    # CLEAN OLD SIGNAL MEMORY
    # --------------------------------------------------------

    if len(state["last_signals"]) > 500:

        keys = list(
            state["last_signals"].keys()
        )

        for key in keys[:-250]:
            del state["last_signals"][key]

    save_state(state)
    save_history(history)

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        history,
        state,
        len(ranked),
        events
    )

    print("\n" + "=" * 70)
    print(report)
    print("=" * 70)

    send_telegram(report)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:
        run()

    except KeyboardInterrupt:

        print(
            "\n[INFO] Scanner stopped."
        )

    except Exception as exc:

        print(
            "[FATAL ERROR]",
            exc
        )

        try:
            send_telegram(
                "🚨 <b>SCANNER ERROR</b>\n"
                f"<code>{str(exc)[:1000]}</code>"
            )
        except Exception:
            pass
