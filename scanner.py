# ============================================================
# KRAKEN FUTURES ICHIMOKU SCANNER v10.0
# ============================================================
#
# FEATURES
# ------------------------------------------------------------
# - Kraken Futures
# - Top 100 markets
# - 1H / 30M trend
# - 15M pullback
# - 5M entry trigger
# - Maximum 4 open trades
# - Maximum 2 LONG
# - Maximum 2 SHORT
# - One trade per symbol
# - Persistent state
# - Persistent trade history
# - RR 1:1
# - Minimum risk 0.50%
# - TP / SL monitoring
# - 4 hour timeout
# - Live P/L
# - Telegram plain-text reporting
# - Telegram message splitting
#
# IMPORTANT
# GitHub Actions must commit:
#   ichimoku_state.json
#   ichimoku_trade_history.json
#
# ENV:
#   TELEGRAM_BOT_TOKEN
#   TELEGRAM_CHAT_ID
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

BASE_URL = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE_URL + "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE_URL + "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE_URL + "/api/charts/v1/trade/{symbol}/{resolution}"
)

TELEGRAM_URL = (
    "https://api.telegram.org/bot{token}/sendMessage"
)

STATE_FILE = "ichimoku_state.json"
HISTORY_FILE = "ichimoku_trade_history.json"

TIMEFRAME_ENTRY = "5m"

TOP_MARKETS = 100

CANDLE_LIMIT = 220

MAX_OPEN_TRADES = 4

MAX_LONG_TRADES = 2

MAX_SHORT_TRADES = 2

RR = 1.0

MIN_RISK_PERCENT = 0.50

MAX_HOLD_MINUTES = 240

REQUEST_TIMEOUT = 20

TELEGRAM_MAX_LENGTH = 3900

# Minimum score required
LONG_1H_SCORE = 6
SHORT_1H_SCORE = -6

LONG_30M_SCORE = 5
SHORT_30M_SCORE = -5

# Pullback tolerance
PULLBACK_ATR_MULTIPLIER = 1.5

# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 CryptoScanner/10.0"
})


# ============================================================
# ENV
# ============================================================

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
    return now_utc().isoformat()


def timestamp():
    return int(time.time())


def safe_float(value, default=None):
    try:
        if value is None:
            return default

        if isinstance(value, str):
            value = value.replace(",", "").strip()

        return float(value)

    except Exception:
        return default


def round_price(value):
    if value is None:
        return None

    return round(float(value), 4)


def clamp(value, low, high):
    return max(low, min(high, value))


def pct_change(a, b):
    if a is None or b is None or a == 0:
        return 0.0

    return ((b - a) / a) * 100.0


def format_price(value):
    if value is None:
        return "-"

    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def format_pct(value):
    if value is None:
        return "-"

    return f"{float(value):+.2f}%"


def html_escape(text):
    return str(text)


# ============================================================
# FILE STATE
# ============================================================

def load_json_file(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"[WARN] Cannot load {filename}: {e}")
        return default


def save_json_file(filename, data):
    temp = filename + ".tmp"

    with open(temp, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp, filename)


def load_state():
    state = load_json_file(
        STATE_FILE,
        {
            "open_trades": [],
            "last_scan": None
        }
    )

    if not isinstance(state, dict):
        state = {
            "open_trades": [],
            "last_scan": None
        }

    if "open_trades" not in state:
        state["open_trades"] = []

    return state


def save_state(state):
    state["last_scan"] = now_iso()
    save_json_file(STATE_FILE, state)


def load_history():
    history = load_json_file(
        HISTORY_FILE,
        []
    )

    if not isinstance(history, list):
        history = []

    return history


def save_history(history):
    save_json_file(
        HISTORY_FILE,
        history
    )


# ============================================================
# HTTP
# ============================================================

def http_get(url, params=None):
    for attempt in range(3):

        try:
            r = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            r.raise_for_status()

            return r.json()

        except Exception as e:

            print(
                f"[WARN] GET failed "
                f"{attempt + 1}/3: {e}"
            )

            if attempt < 2:
                time.sleep(1.5)

    return None


# ============================================================
# KRAKEN MARKETS
# ============================================================

def get_markets():

    data = http_get(INSTRUMENTS_URL)

    if not data:
        return []

    instruments = data.get(
        "instruments",
        []
    )

    markets = []

    for item in instruments:

        symbol = (
            item.get("symbol")
            or item.get("instrument")
        )

        if not symbol:
            continue

        symbol = str(symbol)

        # Skip indexes / non-tradable
        if not (
            symbol.startswith("PF_")
            or symbol.startswith("PI_")
        ):
            continue

        tradeable = item.get(
            "tradeable",
            True
        )

        if tradeable is False:
            continue

        markets.append(symbol)

    return markets


def get_tickers():

    data = http_get(TICKERS_URL)

    if not data:
        return {}

    tickers = data.get(
        "tickers",
        []
    )

    result = {}

    for t in tickers:

        symbol = (
            t.get("symbol")
            or t.get("instrument")
        )

        if not symbol:
            continue

        price = (
            safe_float(t.get("last"))
            or safe_float(t.get("markPrice"))
            or safe_float(t.get("price"))
        )

        if price is None:
            continue

        volume = (
            safe_float(t.get("vol24h"))
            or safe_float(t.get("volume24h"))
            or 0
        )

        result[symbol] = {
            "price": price,
            "volume": volume
        }

    return result


def get_top_markets():

    markets = get_markets()

    if not markets:
        print("[INFO] Markets: 0")
        return []

    tickers = get_tickers()

    ranked = []

    for symbol in markets:

        t = tickers.get(symbol)

        if not t:
            continue

        volume = t.get("volume", 0)

        ranked.append(
            (
                symbol,
                volume
            )
        )

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    result = [
        x[0]
        for x in ranked[:TOP_MARKETS]
    ]

    print(
        f"[INFO] Markets: {len(markets)}"
    )

    print(
        f"[INFO] TOP {len(result)}:"
    )

    print(
        ", ".join(result)
    )

    return result


# ============================================================
# CANDLES
# ============================================================

def get_candles(symbol, resolution):

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = http_get(url)

    if not data:
        return []

    raw = (
        data.get("candles")
        or data.get("data")
        or []
    )

    candles = []

    for c in raw:

        if isinstance(c, dict):

            ts = (
                c.get("time")
                or c.get("timestamp")
                or c.get("ts")
            )

            o = (
                c.get("open")
                or c.get("o")
            )

            h = (
                c.get("high")
                or c.get("h")
            )

            l = (
                c.get("low")
                or c.get("l")
            )

            close = (
                c.get("close")
                or c.get("c")
            )

            volume = (
                c.get("volume")
                or c.get("v")
                or 0
            )

        else:
            continue

        ts = safe_float(ts)
        o = safe_float(o)
        h = safe_float(h)
        l = safe_float(l)
        close = safe_float(close)
        volume = safe_float(volume, 0)

        if None in (ts, o, h, l, close):
            continue

        candles.append({
            "time": int(ts),
            "open": o,
            "high": h,
            "low": l,
            "close": close,
            "volume": volume
        })

    candles.sort(
        key=lambda x: x["time"]
    )

    # Remove currently forming candle.
    # This is important because signals should use CLOSED candles.
    current = int(time.time())

    if candles:

        last = candles[-1]

        # Approximate candle duration
        if resolution == "5m":
            seconds = 300
        elif resolution == "15m":
            seconds = 900
        elif resolution == "30m":
            seconds = 1800
        elif resolution == "1h":
            seconds = 3600
        else:
            seconds = 300

        if last["time"] + seconds > current:
            candles = candles[:-1]

    return candles[-CANDLE_LIMIT:]


# ============================================================
# INDICATORS
# ============================================================

def true_ranges(candles):

    trs = []

    for i, c in enumerate(candles):

        if i == 0:

            tr = c["high"] - c["low"]

        else:

            prev = candles[i - 1]["close"]

            tr = max(
                c["high"] - c["low"],
                abs(c["high"] - prev),
                abs(c["low"] - prev)
            )

        trs.append(tr)

    return trs


def atr(candles, period=14):

    if len(candles) < period + 1:
        return None

    trs = true_ranges(candles)

    values = trs[-period:]

    if not values:
        return None

    return sum(values) / len(values)


def ema(values, period):

    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)

    result = sum(
        values[:period]
    ) / period

    for value in values[period:]:
        result = (
            (value - result) * multiplier
        ) + result

    return result


def ichimoku(candles):

    if len(candles) < 60:
        return None

    highs = [
        x["high"]
        for x in candles
    ]

    lows = [
        x["low"]
        for x in candles
    ]

    closes = [
        x["close"]
        for x in candles
    ]

    def midpoint(period, end):
        if end < period:
            return None

        hi = max(
            highs[end - period:end]
        )

        lo = min(
            lows[end - period:end]
        )

        return (hi + lo) / 2

    idx = len(candles)

    tenkan = midpoint(9, idx)

    kijun = midpoint(26, idx)

    span_a = None
    span_b = None

    if tenkan is not None and kijun is not None:
        span_a = (
            tenkan + kijun
        ) / 2

    span_b = midpoint(52, idx)

    prev_tenkan = midpoint(
        9,
        idx - 1
    )

    prev_kijun = midpoint(
        26,
        idx - 1
    )

    if span_a is None or span_b is None:
        return None

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "span_a": span_a,
        "span_b": span_b,
        "cloud_top": max(
            span_a,
            span_b
        ),
        "cloud_bottom": min(
            span_a,
            span_b
        ),
        "prev_tenkan": prev_tenkan,
        "prev_kijun": prev_kijun,
        "close": closes[-1],
        "prev_close": closes[-2]
    }


# ============================================================
# ICHIMOKU SCORE
# ============================================================

def ichimoku_score(candles):

    data = ichimoku(candles)

    if not data:
        return None, None

    price = data["close"]

    score = 0

    # --------------------------------------------------------
    # PRICE VS CLOUD
    # --------------------------------------------------------

    if price > data["cloud_top"]:
        score += 3

    elif price < data["cloud_bottom"]:
        score -= 3

    # --------------------------------------------------------
    # PRICE VS TENKAN
    # --------------------------------------------------------

    if price > data["tenkan"]:
        score += 1

    elif price < data["tenkan"]:
        score -= 1

    # --------------------------------------------------------
    # PRICE VS KIJUN
    # --------------------------------------------------------

    if price > data["kijun"]:
        score += 2

    elif price < data["kijun"]:
        score -= 2

    # --------------------------------------------------------
    # FUTURE CLOUD
    # --------------------------------------------------------

    if data["span_a"] > data["span_b"]:
        score += 2

    elif data["span_a"] < data["span_b"]:
        score -= 2

    # --------------------------------------------------------
    # CHIKOU APPROXIMATION
    # --------------------------------------------------------

    if len(candles) >= 53:

        past_price = candles[-27]["close"]

        if price > past_price:
            score += 2

        elif price < past_price:
            score -= 2

    score = clamp(
        score,
        -10,
        10
    )

    return score, data


# ============================================================
# TREND
# ============================================================

def get_direction(
    score_1h,
    score_30m
):

    if (
        score_1h is not None
        and score_30m is not None
    ):

        if (
            score_1h >= LONG_1H_SCORE
            and score_30m >= LONG_30M_SCORE
        ):
            return "LONG"

        if (
            score_1h <= SHORT_1H_SCORE
            and score_30m <= SHORT_30M_SCORE
        ):
            return "SHORT"

    return None


# ============================================================
# PULLBACK
# ============================================================

def pullback_ok(
    candles,
    direction
):

    score, data = ichimoku_score(
        candles
    )

    if not data:
        return False

    a = atr(candles, 14)

    if not a or a <= 0:
        return False

    price = data["close"]

    distance_tenkan = abs(
        price - data["tenkan"]
    )

    distance_kijun = abs(
        price - data["kijun"]
    )

    near_line = (
        distance_tenkan
        <= a * PULLBACK_ATR_MULTIPLIER
        or
        distance_kijun
        <= a * PULLBACK_ATR_MULTIPLIER
    )

    if direction == "LONG":

        correct_side = (
            price >= data["cloud_bottom"]
        )

    else:

        correct_side = (
            price <= data["cloud_top"]
        )

    return (
        near_line
        and correct_side
    )


# ============================================================
# 5M TRIGGER
# ============================================================

def bullish_engulfing(prev, cur):

    return (
        prev["close"] < prev["open"]
        and cur["close"] > cur["open"]
        and cur["open"] <= prev["close"]
        and cur["close"] >= prev["open"]
    )


def bearish_engulfing(prev, cur):

    return (
        prev["close"] > prev["open"]
        and cur["close"] < cur["open"]
        and cur["open"] >= prev["close"]
        and cur["close"] <= prev["open"]
    )


def entry_trigger(
    candles,
    direction
):

    if len(candles) < 5:
        return False

    cur = candles[-1]
    prev = candles[-2]

    data = ichimoku(candles)

    if not data:
        return False

    trigger = False

    # --------------------------------------------------------
    # CANDLE DIRECTION
    # --------------------------------------------------------

    bullish = cur["close"] > cur["open"]

    bearish = cur["close"] < cur["open"]

    # --------------------------------------------------------
    # TENKAN CROSS
    # --------------------------------------------------------

    if (
        data["prev_tenkan"] is not None
        and data["prev_kijun"] is not None
    ):

        if direction == "LONG":

            if (
                prev["close"]
                <= data["prev_tenkan"]
                and
                cur["close"]
                > data["tenkan"]
            ):
                trigger = True

        else:

            if (
                prev["close"]
                >= data["prev_tenkan"]
                and
                cur["close"]
                < data["tenkan"]
            ):
                trigger = True

    # --------------------------------------------------------
    # KIJUN CROSS
    # --------------------------------------------------------

    if direction == "LONG":

        if (
            prev["close"] <= data["prev_kijun"]
            and cur["close"] > data["kijun"]
        ):
            trigger = True

    else:

        if (
            prev["close"] >= data["prev_kijun"]
            and cur["close"] < data["kijun"]
        ):
            trigger = True

    # --------------------------------------------------------
    # ENGULFING
    # --------------------------------------------------------

    if direction == "LONG":
        if bullish_engulfing(prev, cur):
            trigger = True

    else:
        if bearish_engulfing(prev, cur):
            trigger = True

    # --------------------------------------------------------
    # BREAK PREVIOUS HIGH / LOW
    # --------------------------------------------------------

    if direction == "LONG":

        if (
            cur["close"]
            > max(
                x["high"]
                for x in candles[-4:-1]
            )
        ):
            trigger = True

    else:

        if (
            cur["close"]
            < min(
                x["low"]
                for x in candles[-4:-1]
            )
        ):
            trigger = True

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    body = abs(
        cur["close"] - cur["open"]
    )

    range_ = (
        cur["high"] - cur["low"]
    )

    if range_ > 0:

        body_ratio = body / range_

        if body_ratio >= 0.55:

            if direction == "LONG" and bullish:
                trigger = True

            if direction == "SHORT" and bearish:
                trigger = True

    # Candle must agree with direction
    if direction == "LONG" and not bullish:
        return False

    if direction == "SHORT" and not bearish:
        return False

    return trigger


# ============================================================
# STRUCTURAL STOP
# ============================================================

def calculate_entry_sl(
    candles,
    direction
):

    if len(candles) < 12:
        return None, None

    entry = candles[-1]["close"]

    lookback = candles[-11:-1]

    if direction == "LONG":

        swing_low = min(
            x["low"]
            for x in lookback
        )

        sl = swing_low

        risk = entry - sl

        if risk <= 0:
            return None, None

        tp = entry + (
            risk * RR
        )

    else:

        swing_high = max(
            x["high"]
            for x in lookback
        )

        sl = swing_high

        risk = sl - entry

        if risk <= 0:
            return None, None

        tp = entry - (
            risk * RR
        )

    risk_percent = (
        abs(entry - sl)
        / entry
        * 100
    )

    return (
        round_price(sl),
        round_price(tp)
    )


# ============================================================
# OPEN TRADE LIMITS
# ============================================================

def count_directions(open_trades):

    longs = 0
    shorts = 0

    for trade in open_trades:

        if trade.get("direction") == "LONG":
            longs += 1

        elif trade.get("direction") == "SHORT":
            shorts += 1

    return longs, shorts


def symbol_is_open(
    open_trades,
    symbol
):

    return any(
        t.get("symbol") == symbol
        for t in open_trades
    )


# ============================================================
# TRADE ID
# ============================================================

def make_trade_id(
    symbol,
    direction,
    candle_time
):

    return (
        f"{symbol}|"
        f"{direction}|"
        f"{candle_time}"
    )


# ============================================================
# OPEN TRADE
# ============================================================

def create_trade(
    symbol,
    direction,
    candles,
    score_1h,
    score_30m,
    score_15m
):

    entry = candles[-1]["close"]

    sl, tp = calculate_entry_sl(
        candles,
        direction
    )

    if sl is None or tp is None:
        return None

    risk_percent = (
        abs(entry - sl)
        / entry
        * 100
    )

    if risk_percent < MIN_RISK_PERCENT:

        print(
            f"[SKIP] {symbol} "
            f"{direction} "
            f"risk={risk_percent:.2f}% "
            f"< {MIN_RISK_PERCENT:.2f}%"
        )

        return None

    candle_time = candles[-1]["time"]

    trade = {

        "id": make_trade_id(
            symbol,
            direction,
            candle_time
        ),

        "symbol": symbol,

        "direction": direction,

        "entry": round_price(entry),

        "sl": round_price(sl),

        "tp": round_price(tp),

        "risk_percent": round(
            risk_percent,
            4
        ),

        "rr": RR,

        "opened_at": now_iso(),

        "opened_timestamp": timestamp(),

        "entry_candle_time": candle_time,

        "score_1h": score_1h,

        "score_30m": score_30m,

        "score_15m": score_15m,

        "status": "OPEN"
    }

    return trade


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade,
    exit_price,
    reason
):

    entry = safe_float(
        trade.get("entry")
    )

    direction = trade.get(
        "direction"
    )

    if not entry:
        pnl_percent = 0
    else:

        if direction == "LONG":

            pnl_percent = (
                (exit_price - entry)
                / entry
                * 100
            )

        else:

            pnl_percent = (
                (entry - exit_price)
                / entry
                * 100
            )

    if reason == "TP":
        result = "WIN"

    elif reason == "SL":
        result = "LOSS"

    else:
        result = (
            "WIN"
            if pnl_percent > 0
            else
            "LOSS"
            if pnl_percent < 0
            else
            "TIMEOUT"
        )

    closed = dict(trade)

    closed.update({

        "status": "CLOSED",

        "exit": round_price(
            exit_price
        ),

        "closed_at": now_iso(),

        "close_reason": reason,

        "result": result,

        "pnl_percent": round(
            pnl_percent,
            4
        )
    })

    return closed


# ============================================================
# CHECK OPEN TRADES
# ============================================================

def check_open_trades(
    state,
    history,
    tickers
):

    open_trades = state.get(
        "open_trades",
        []
    )

    if not open_trades:
        return [], history

    still_open = []

    for trade in open_trades:

        symbol = trade.get(
            "symbol"
        )

        direction = trade.get(
            "direction"
        )

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
                f"[WARN] Invalid trade "
                f"{symbol}, keeping it."
            )

            still_open.append(trade)
            continue

        ticker = tickers.get(symbol)

        if not ticker:

            print(
                f"[WARN] No price for "
                f"{symbol}, keeping trade."
            )

            still_open.append(trade)
            continue

        price = ticker["price"]

        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        opened_timestamp = safe_float(
            trade.get(
                "opened_timestamp"
            )
        )

        timed_out = False

        if opened_timestamp:

            age_minutes = (
                timestamp()
                - opened_timestamp
            ) / 60

            if age_minutes >= MAX_HOLD_MINUTES:
                timed_out = True

        # ----------------------------------------------------
        # EXIT LOGIC
        # ----------------------------------------------------

        close_reason = None

        if direction == "LONG":

            # Same candle ambiguity:
            # SL gets priority.
            if price <= sl:
                close_reason = "SL"

            elif price >= tp:
                close_reason = "TP"

        else:

            if price >= sl:
                close_reason = "SL"

            elif price <= tp:
                close_reason = "TP"

        if timed_out and close_reason is None:
            close_reason = "TIMEOUT"

        # ----------------------------------------------------
        # CLOSE
        # ----------------------------------------------------

        if close_reason:

            closed = close_trade(
                trade,
                price,
                close_reason
            )

            history.append(
                closed
            )

            print(
                f"[CLOSED] "
                f"{symbol} "
                f"{direction} "
                f"{close_reason} "
                f"PnL={closed['pnl_percent']:+.2f}%"
            )

        else:

            still_open.append(
                trade
            )

    return still_open, history


# ============================================================
# DUPLICATE PROTECTION
# ============================================================

def signal_already_seen(
    history,
    symbol,
    direction,
    candle_time
):

    trade_id = make_trade_id(
        symbol,
        direction,
        candle_time
    )

    for trade in history:

        if trade.get("id") == trade_id:
            return True

    return False


# ============================================================
# FIND SIGNAL
# ============================================================

def scan_symbol(
    symbol,
    open_trades,
    history
):

    # Same symbol cannot have another trade
    if symbol_is_open(
        open_trades,
        symbol
    ):
        return None

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    candles_1h = get_candles(
        symbol,
        "1h"
    )

    if len(candles_1h) < 80:
        return None

    score_1h, _ = ichimoku_score(
        candles_1h
    )

    if score_1h is None:
        return None

    # --------------------------------------------------------
    # 30M
    # --------------------------------------------------------

    candles_30m = get_candles(
        symbol,
        "30m"
    )

    if len(candles_30m) < 80:
        return None

    score_30m, _ = ichimoku_score(
        candles_30m
    )

    if score_30m is None:
        return None

    direction = get_direction(
        score_1h,
        score_30m
    )

    if direction is None:
        return None

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    candles_15m = get_candles(
        symbol,
        "15m"
    )

    if len(candles_15m) < 80:
        return None

    score_15m, _ = ichimoku_score(
        candles_15m
    )

    if score_15m is None:
        return None

    if not pullback_ok(
        candles_15m,
        direction
    ):
        return None

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    candles_5m = get_candles(
        symbol,
        "5m"
    )

    if len(candles_5m) < 80:
        return None

    if not entry_trigger(
        candles_5m,
        direction
    ):
        return None

    candle_time = candles_5m[-1]["time"]

    if signal_already_seen(
        history,
        symbol,
        direction,
        candle_time
    ):
        return None

    # --------------------------------------------------------
    # TRADE
    # --------------------------------------------------------

    trade = create_trade(
        symbol,
        direction,
        candles_5m,
        score_1h,
        score_30m,
        score_15m
    )

    return trade


# ============================================================
# PERFORMANCE
# ============================================================

def performance(history):

    trades = len(history)

    wins = sum(
        1
        for x in history
        if x.get("result") == "WIN"
    )

    losses = sum(
        1
        for x in history
        if x.get("result") == "LOSS"
    )

    timeout = sum(
        1
        for x in history
        if x.get("result") == "TIMEOUT"
    )

    pnl = sum(
        safe_float(
            x.get("pnl_percent"),
            0
        )
        for x in history
    )

    decided = wins + losses

    if decided:
        win_rate = (
            wins / decided
        ) * 100

    else:
        win_rate = 0

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "timeout": timeout,
        "pnl": pnl,
        "win_rate": win_rate
    }


# ============================================================
# LIVE TRADE P/L
# ============================================================

def live_trade_data(
    trade,
    tickers
):

    symbol = trade["symbol"]

    price_data = tickers.get(
        symbol
    )

    if not price_data:
        return None

    current = price_data["price"]

    entry = trade["entry"]

    direction = trade["direction"]

    if direction == "LONG":

        pnl = (
            current - entry
        ) / entry * 100

        tp_distance = (
            trade["tp"] - current
        ) / current * 100

        sl_distance = (
            current - trade["sl"]
        ) / current * 100

    else:

        pnl = (
            entry - current
        ) / entry * 100

        tp_distance = (
            current - trade["tp"]
        ) / current * 100

        sl_distance = (
            trade["sl"] - current
        ) / current * 100

    return {
        "price": current,
        "pnl": pnl,
        "tp_distance": tp_distance,
        "sl_distance": sl_distance
    }


# ============================================================
# TELEGRAM SPLIT
# ============================================================

def split_message(
    text,
    max_length=TELEGRAM_MAX_LENGTH
):

    if len(text) <= max_length:
        return [text]

    chunks = []

    current = ""

    for line in text.splitlines(
        keepends=True
    ):

        if len(current) + len(line) <= max_length:

            current += line

        else:

            if current:
                chunks.append(
                    current.rstrip()
                )

            # Extremely long single line
            while len(line) > max_length:

                chunks.append(
                    line[:max_length]
                )

                line = line[
                    max_length:
                ]

            current = line

    if current:
        chunks.append(
            current.rstrip()
        )

    return chunks


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):

    if not TELEGRAM_BOT_TOKEN:

        raise RuntimeError(
            "TELEGRAM_BOT_TOKEN is missing"
        )

    if not TELEGRAM_CHAT_ID:

        raise RuntimeError(
            "TELEGRAM_CHAT_ID is missing"
        )

    url = TELEGRAM_URL.format(
        token=TELEGRAM_BOT_TOKEN
    )

    chunks = split_message(
        message
    )

    for i, chunk in enumerate(chunks):

        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "disable_web_page_preview": True
        }

        try:

            response = SESSION.post(
                url,
                data=payload,
                timeout=REQUEST_TIMEOUT
            )

        except Exception as e:

            raise RuntimeError(
                f"Telegram connection error: {e}"
            )

        try:
            data = response.json()

        except Exception:

            data = {
                "ok": False,
                "description": response.text
            }

        if (
            response.status_code != 200
            or not data.get("ok", False)
        ):

            description = data.get(
                "description",
                response.text
            )

            raise RuntimeError(
                f"Telegram HTTP "
                f"{response.status_code}: "
                f"{description}"
            )

        print(
            f"[TELEGRAM] "
            f"Message {i + 1}/"
            f"{len(chunks)} sent"
        )


# ============================================================
# REPORT
# ============================================================

def build_report(
    state,
    history,
    tickers
):

    open_trades = state.get(
        "open_trades",
        []
    )

    perf = performance(
        history
    )

    lines = []

    lines.append(
        "📡 KRAKEN ICHIMOKU REPORT"
    )

    lines.append(
        f"🕐 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    lines.append(
        "⏱ 5m CLOSED | TOP 100"
    )

    lines.append(
        "🤖 ICHIMOKU MTF | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/4)"
    )

    if not open_trades:

        lines.append(
            "No open trades"
        )

    else:

        for i, trade in enumerate(
            open_trades,
            start=1
        ):

            live = live_trade_data(
                trade,
                tickers
            )

            symbol = trade["symbol"]

            direction = trade["direction"]

            entry = trade["entry"]

            sl = trade["sl"]

            tp = trade["tp"]

            if live:

                current = live["price"]

                pnl = live["pnl"]

                tp_distance = (
                    live["tp_distance"]
                )

                sl_distance = (
                    live["sl_distance"]
                )

            else:

                current = entry

                pnl = 0

                tp_distance = 0

                sl_distance = 0

            emoji = (
                "🟢"
                if direction == "LONG"
                else
                "🔴"
            )

            lines.append("")

            lines.append(
                f"{i}. {symbol} "
                f"{emoji} {direction}"
            )

            lines.append(
                f"Entry: {format_price(entry)}"
            )

            lines.append(
                f"Now: {format_price(current)}"
            )

            lines.append(
                f"SL: {format_price(sl)} "
                f"({sl_distance:+.2f}%)"
            )

            lines.append(
                f"TP: {format_price(tp)} "
                f"({tp_distance:+.2f}%)"
            )

            lines.append(
                f"P/L: {pnl:+.2f}%"
            )

            lines.append(
                f"Risk: {trade.get('risk_percent', 0):.2f}%"
            )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append("")

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
        f"⚪ {perf['timeout']}"
    )

    lines.append(
        f"🏆 WR: {perf['win_rate']:.1f}%"
    )

    lines.append(
        f"💰 Total P/L: "
        f"{perf['pnl']:+.2f}%"
    )

    # --------------------------------------------------------
    # LIMITS
    # --------------------------------------------------------

    longs, shorts = count_directions(
        open_trades
    )

    lines.append("")

    lines.append(
        f"⚙️ LIMITS: "
        f"LONG {longs}/2 | "
        f"SHORT {shorts}/2 | "
        f"TOTAL {len(open_trades)}/4"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)

    print(
        "KRAKEN FUTURES "
        "ICHIMOKU SCANNER v10.0"
    )

    print("=" * 70)

    state = load_state()

    history = load_history()

    # --------------------------------------------------------
    # GET TICKERS
    # --------------------------------------------------------

    print(
        "[INFO] Loading tickers..."
    )

    tickers = get_tickers()

    if not tickers:

        raise RuntimeError(
            "No ticker data received"
        )

    # --------------------------------------------------------
    # CHECK EXISTING TRADES FIRST
    # --------------------------------------------------------

    print(
        "[INFO] Checking open trades..."
    )

    open_trades, history = (
        check_open_trades(
            state,
            history,
            tickers
        )
    )

    state["open_trades"] = (
        open_trades
    )

    save_history(history)

    save_state(state)

    # --------------------------------------------------------
    # CURRENT LIMITS
    # --------------------------------------------------------

    longs, shorts = count_directions(
        open_trades
    )

    print(
        f"[INFO] Open trades: "
        f"{len(open_trades)}/4"
    )

    print(
        f"[INFO] LONG: "
        f"{longs}/2"
    )

    print(
        f"[INFO] SHORT: "
        f"{shorts}/2"
    )

    # --------------------------------------------------------
    # SCAN ONLY IF ROOM EXISTS
    # --------------------------------------------------------

    if len(open_trades) < MAX_OPEN_TRADES:

        markets = get_top_markets()

        for symbol in markets:

            # Stop when full
            if len(open_trades) >= MAX_OPEN_TRADES:
                break

            # Refresh counts
            longs, shorts = (
                count_directions(
                    open_trades
                )
            )

            # Same symbol
            if symbol_is_open(
                open_trades,
                symbol
            ):
                continue

            # ------------------------------------------------
            # Scan
            # ------------------------------------------------

            try:

                trade = scan_symbol(
                    symbol,
                    open_trades,
                    history
                )

            except Exception as e:

                print(
                    f"[WARN] Scan failed "
                    f"{symbol}: {e}"
                )

                continue

            if not trade:
                continue

            direction = trade[
                "direction"
            ]

            # ------------------------------------------------
            # Direction limit
            # ------------------------------------------------

            if (
                direction == "LONG"
                and longs >= MAX_LONG_TRADES
            ):

                print(
                    f"[SKIP] {symbol} "
                    f"LONG limit reached"
                )

                continue

            if (
                direction == "SHORT"
                and shorts >= MAX_SHORT_TRADES
            ):

                print(
                    f"[SKIP] {symbol} "
                    f"SHORT limit reached"
                )

                continue

            # ------------------------------------------------
            # Add trade
            # ------------------------------------------------

            open_trades.append(
                trade
            )

            print(
                f"[OPEN] "
                f"{symbol} "
                f"{direction} "
                f"Entry={trade['entry']} "
                f"SL={trade['sl']} "
                f"TP={trade['tp']} "
                f"Risk={trade['risk_percent']:.2f}%"
            )

    else:

        print(
            "[INFO] Maximum open trades "
            "reached. No new entries."
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    state["open_trades"] = (
        open_trades
    )

    save_history(history)

    save_state(state)

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        state,
        history,
        tickers
    )

    print("")
    print(report)
    print("")

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    try:

        telegram_send(
            report
        )

    except Exception as e:

        print(
            "[ERROR] Telegram:"
        )

        print(str(e))

        # Do not destroy scanner state
        # because Telegram failed.

    print(
        "[INFO] Scanner completed."
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        print(
            "[FATAL ERROR]"
        )

        print(
            str(e)
        )

        traceback.print_exc()

        raise
