# ============================================================
# CRYPTO DIVERGENCE SCANNER v10.4
# ============================================================
# Kraken Futures
# Closed 5m Candles
# RSI Divergence
# Trendline Breakout / Breakdown
# UT Bot
# 15M + 1H Trend Filter
# Automatic Futures Symbol Discovery
# Trade History
# Open Signal P&L
# P&L / R / MFE / MAE / Duration
# Closed Signals Report
# Signal Diagnostic / Rejection Reasons
# ATR Based SL
# R Based TP
# Telegram
#
# v10.4
# - NEW STATE FILE
# - Cumulative statistics start from ZERO
# - One OPEN trade per symbol
# - No opposite-direction hedge
# - 15M + 1H trend filter
# - Current P&L Bold
# - Signal Diagnostic
# - Rejection reasons
# ============================================================

import os
import json
import time
import hashlib
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"
INSTRUMENTS_URL = "https://futures.kraken.com/derivatives/api/v3/instruments"

TIMEFRAME = "5m"
CANDLE_LIMIT = 250

COINS = [
    "BTC", "ETH", "SOL", "XRP", "ADA",
    "DOGE", "AVAX", "LINK", "DOT", "LTC",
    "BCH", "ATOM", "UNI", "AAVE", "FIL",
    "ETC", "NEAR", "APT", "ARB", "OP",
    "SUI", "SEI", "INJ", "TIA", "TRX",
    "XLM", "ALGO", "VET", "MATIC", "HBAR",
]

# ============================================================
# NEW STATE FILE
# ============================================================

STATE_FILE = "trade_history_v10.4.json"

# ============================================================
# ONE OPEN TRADE PER SYMBOL
# ============================================================

ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False

REQUEST_TIMEOUT = 20


# ============================================================
# MULTI-TIMEFRAME TREND
# ============================================================

TREND_FAST_EMA = 20
TREND_SLOW_EMA = 50

TREND_TIMEFRAMES = [
    "15m",
    "1h",
]


# ============================================================
# RSI
# ============================================================

RSI_PERIOD = 14


# ============================================================
# DIVERGENCE
# ============================================================

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MAX_PIVOT_GAP = 60

MIN_RSI_DIFFERENCE = 2.0
MIN_PRICE_DIFFERENCE_PERCENT = 0.10


# ============================================================
# SL / TP
# ============================================================

SL_ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.5

SL_BUFFER_PERCENT = 0.10

MIN_SL_DISTANCE_PERCENT = 0.35
MAX_SL_DISTANCE_PERCENT = 2.00

TP1_R_MULTIPLE = 1.5
TP2_R_MULTIPLE = 2.5
TP3_R_MULTIPLE = 3.5


# ============================================================
# UT BOT
# ============================================================

UT_KEY_VALUE = 3.0
UT_ATR_PERIOD = 10


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


SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "CryptoDivergenceScanner/10.4",
    "Accept": "application/json",
})


# ============================================================
# GLOBAL MARKET MAP
# ============================================================

MARKET_MAP = {}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
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

        return response.ok

    except Exception:
        return False


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "version": 2,
        "scanner_version": "10.4",
        "trades": {},
        "last_run": None,
    }


def load_state():

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            return default_state()

        if "trades" not in data:
            data["trades"] = {}

        if not isinstance(
            data["trades"],
            (dict, list)
        ):
            data["trades"] = {}

        return data

    except Exception as e:

        print(
            f"WARNING: Could not load state: {e}"
        )

        return default_state()


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

    state["last_run"] = datetime.now(
        timezone.utc
    ).isoformat()

    with open(
        temp_file,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(
        temp_file,
        STATE_FILE
    )


# ============================================================
# TRADE HELPERS
# ============================================================

def get_all_trades(state):

    trades = state.get(
        "trades",
        {}
    )

    if isinstance(trades, dict):

        result = []

        for key, trade in trades.items():

            if isinstance(trade, dict):

                if not trade.get("id"):
                    trade["id"] = key

                if not trade.get("signal_id"):
                    trade["signal_id"] = key

                result.append(trade)

        return result

    if isinstance(trades, list):

        return [
            x for x in trades
            if isinstance(x, dict)
        ]

    return []


def normalize_side(trade):

    side = trade.get("side")

    if not side:
        side = trade.get("direction")

    if not side:
        return ""

    side = str(side).upper().strip()

    if side in ("LONG", "BUY"):
        return "BUY"

    if side in ("SHORT", "SELL"):
        return "SELL"

    return side


def normalize_coin(trade):

    name = trade.get("name")

    if name:
        return str(name).upper()

    symbol = str(
        trade.get("symbol", "")
    ).upper()

    if symbol.startswith("PF_"):
        symbol = symbol[3:]

    if symbol.startswith("PI_"):
        symbol = symbol[3:]

    if symbol.endswith("USD"):
        symbol = symbol[:-3]

    if symbol == "XBT":
        symbol = "BTC"

    return symbol


def parse_trade_time(value):

    if value is None:
        return None

    if isinstance(value, (int, float)):
        return int(value)

    value = str(value).strip()

    if not value:
        return None

    try:
        return int(float(value))
    except Exception:
        pass

    try:

        iso = value

        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"

        dt = datetime.fromisoformat(iso)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=timezone.utc
            )

        return int(
            dt.timestamp() * 1000
        )

    except Exception:

        return None


def get_trade_tp1(trade):

    value = trade.get("tp1")

    if value is None:
        value = trade.get("tp")

    try:
        return float(value)
    except Exception:
        return None


def get_trade_tp2(trade):

    try:
        return float(trade.get("tp2"))
    except Exception:
        return None


def get_trade_tp3(trade):

    try:
        return float(trade.get("tp3"))
    except Exception:
        return None


def get_trade_entry(trade):

    try:
        return float(trade.get("entry"))
    except Exception:
        return None


def get_trade_sl(trade):

    try:
        return float(trade.get("sl"))
    except Exception:
        return None


def get_trade_id(trade):

    value = trade.get("signal_id")

    if value:
        return str(value)

    value = trade.get("id")

    if value:
        return str(value)

    return None


def rebuild_trade_container(state):

    trades = state.get("trades", {})

    if isinstance(trades, dict):

        rebuilt = {}

        for trade in get_all_trades(state):

            trade_id = get_trade_id(trade)

            if not trade_id:

                trade_id = hashlib.sha256(
                    json.dumps(
                        trade,
                        sort_keys=True,
                        default=str
                    ).encode()
                ).hexdigest()[:16]

                trade["id"] = trade_id
                trade["signal_id"] = trade_id

            rebuilt[trade_id] = trade

        state["trades"] = rebuilt

    elif isinstance(trades, list):

        state["trades"] = get_all_trades(state)

    else:

        state["trades"] = {}


# ============================================================
# SIGNAL ID
# ============================================================

def make_signal_id(
    symbol,
    side,
    signal_time,
    entry
):

    raw = (
        f"{symbol}|"
        f"{side}|"
        f"{signal_time}|"
        f"{entry}"
    )

    return hashlib.sha256(
        raw.encode()
    ).hexdigest()[:16]


# ============================================================
# MARKET MAP
# ============================================================

def load_market_map():

    global MARKET_MAP

    try:

        response = SESSION.get(
            INSTRUMENTS_URL,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

        instruments = data.get(
            "instruments",
            []
        )

        result = {}

        for item in instruments:

            symbol = str(
                item.get("symbol", "")
            ).upper()

            base = str(
                item.get("base", "")
            ).upper()

            quote = str(
                item.get("quote", "")
            ).upper()

            instrument_type = str(
                item.get("type", "")
            ).lower()

            tradeable = item.get(
                "tradeable",
                False
            )

            expired = item.get(
                "isExpired",
                False
            )

            if not symbol:
                continue

            if not tradeable:
                continue

            if expired:
                continue

            if quote != "USD":
                continue

            if base == "XBT":
                base = "BTC"

            if base not in COINS:
                continue

            score = 0

            if symbol.startswith("PF_"):
                score += 100

            if instrument_type == "flexible_futures":
                score += 80

            if instrument_type == "futures_inverse":
                score += 50

            candidate = (
                score,
                symbol
            )

            if (
                base not in result
                or candidate[0] > result[base][0]
            ):

                result[base] = candidate

        MARKET_MAP = {
            coin: value[1]
            for coin, value in result.items()
        }

        return MARKET_MAP

    except Exception as e:

        raise RuntimeError(
            f"Failed to load Kraken instruments: {e}"
        )


def get_market_symbol(coin):

    coin = str(coin).upper()

    if coin == "XBT":
        coin = "BTC"

    if coin not in MARKET_MAP:
        load_market_map()

    symbol = MARKET_MAP.get(coin)

    if not symbol:

        raise RuntimeError(
            f"No active USD Futures market found for {coin}"
        )

    return symbol


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    timeframe="5m"
):

    futures_symbol = get_market_symbol(symbol)

    url = (
        f"{BASE_URL}/trade/"
        f"{futures_symbol}/"
        f"{timeframe}"
    )

    response = SESSION.get(
        url,
        params={"count": CANDLE_LIMIT},
        timeout=REQUEST_TIMEOUT
    )

    if not response.ok:

        raise RuntimeError(
            f"HTTP {response.status_code} | "
            f"{futures_symbol} | "
            f"{response.text[:250]}"
        )

    data = response.json()

    candles = data.get(
        "candles",
        []
    )

    if not candles:

        raise RuntimeError(
            f"No candles returned for "
            f"{futures_symbol} {timeframe}"
        )

    rows = []

    for candle in candles:

        try:

            if isinstance(candle, dict):

                timestamp = candle.get("time")
                open_price = candle.get("open")
                high_price = candle.get("high")
                low_price = candle.get("low")
                close_price = candle.get("close")
                volume = candle.get("volume", 0)

            elif isinstance(candle, (list, tuple)):

                if len(candle) < 6:
                    continue

                timestamp = candle[0]
                open_price = candle[1]
                high_price = candle[2]
                low_price = candle[3]
                close_price = candle[4]
                volume = candle[5]

            else:
                continue

            if timestamp is None:
                continue

            rows.append({
                "time": int(timestamp),
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
                "volume": float(volume),
            })

        except Exception:
            continue

    if not rows:

        raise RuntimeError(
            f"Could not parse candles for "
            f"{futures_symbol} {timeframe}"
        )

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    timeframe_minutes = {
        "5m": 5,
        "15m": 15,
        "1h": 60,
    }

    minutes = timeframe_minutes.get(
        timeframe,
        5
    )

    candle_ms = (
        minutes
        * 60
        * 1000
    )

    now_ms = int(
        time.time() * 1000
    )

    if len(df) > 0:

        last_time = int(
            df.iloc[-1]["time"]
        )

        if last_time + candle_ms > now_ms:

            df = df.iloc[:-1].copy()

    if len(df) < 50:

        raise RuntimeError(
            f"Not enough closed candles for "
            f"{symbol} {timeframe}"
        )

    return df


# ============================================================
# EMA
# ============================================================

def calculate_ema(
    series,
    period
):

    return series.ewm(
        span=period,
        adjust=False
    ).mean()


# ============================================================
# TREND
# ============================================================

def determine_trend(df):

    if df is None or df.empty:
        return "NEUTRAL"

    if len(df) < TREND_SLOW_EMA + 5:
        return "NEUTRAL"

    close = df["close"]

    ema_fast = calculate_ema(
        close,
        TREND_FAST_EMA
    )

    ema_slow = calculate_ema(
        close,
        TREND_SLOW_EMA
    )

    current_close = float(
        close.iloc[-1]
    )

    current_fast = float(
        ema_fast.iloc[-1]
    )

    current_slow = float(
        ema_slow.iloc[-1]
    )

    if (
        current_close > current_fast
        and current_fast > current_slow
    ):
        return "BULLISH"

    if (
        current_close < current_fast
        and current_fast < current_slow
    ):
        return "BEARISH"

    return "NEUTRAL"


def get_multi_timeframe_trend(symbol):

    trend_data = {}

    for timeframe in TREND_TIMEFRAMES:

        df = get_candles(
            symbol,
            timeframe
        )

        trend_data[timeframe] = {
            "trend": determine_trend(df),
            "df": df,
        }

    return trend_data


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


# ============================================================
# PIVOTS
# ============================================================

def pivot_lows(
    series,
    left=2,
    right=2
):

    result = np.zeros(
        len(series),
        dtype=bool
    )

    values = series.values

    for i in range(
        left,
        len(values) - right
    ):

        window = values[
            i-left:i+right+1
        ]

        if values[i] == np.min(window):

            if np.sum(
                window == values[i]
            ) == 1:

                result[i] = True

    return result


def pivot_highs(
    series,
    left=2,
    right=2
):

    result = np.zeros(
        len(series),
        dtype=bool
    )

    values = series.values

    for i in range(
        left,
        len(values) - right
    ):

        window = values[
            i-left:i+right+1
        ]

        if values[i] == np.max(window):

            if np.sum(
                window == values[i]
            ) == 1:

                result[i] = True

    return result


# ============================================================
# BULLISH DIVERGENCE
# ============================================================

def find_bullish_divergence(df):

    lows = df["low"]
    rsi = df["rsi"]

    pivots = pivot_lows(
        lows,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    indexes = np.where(pivots)[0]

    if len(indexes) < 2:
        return None

    latest = indexes[-1]

    previous_candidates = [
        x for x in indexes[:-1]
        if latest - x <= MAX_PIVOT_GAP
    ]

    if not previous_candidates:
        return None

    previous = previous_candidates[-1]

    price_previous = float(
        lows.iloc[previous]
    )

    price_latest = float(
        lows.iloc[latest]
    )

    rsi_previous = float(
        rsi.iloc[previous]
    )

    rsi_latest = float(
        rsi.iloc[latest]
    )

    price_change = (
        (price_latest - price_previous)
        / price_previous
        * 100
    )

    rsi_change = (
        rsi_latest - rsi_previous
    )

    if (
        price_latest < price_previous
        and rsi_latest > rsi_previous
        and abs(price_change)
        >= MIN_PRICE_DIFFERENCE_PERCENT
        and rsi_change
        >= MIN_RSI_DIFFERENCE
    ):

        return {
            "pivot_index": latest,
            "previous_index": previous,
            "price_previous": price_previous,
            "price_latest": price_latest,
            "rsi_previous": rsi_previous,
            "rsi_latest": rsi_latest,
            "price_change": price_change,
            "rsi_change": rsi_change,
        }

    return None


# ============================================================
# BEARISH DIVERGENCE
# ============================================================

def find_bearish_divergence(df):

    highs = df["high"]
    rsi = df["rsi"]

    pivots = pivot_highs(
        highs,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    indexes = np.where(pivots)[0]

    if len(indexes) < 2:
        return None

    latest = indexes[-1]

    previous_candidates = [
        x for x in indexes[:-1]
        if latest - x <= MAX_PIVOT_GAP
    ]

    if not previous_candidates:
        return None

    previous = previous_candidates[-1]

    price_previous = float(
        highs.iloc[previous]
    )

    price_latest = float(
        highs.iloc[latest]
    )

    rsi_previous = float(
        rsi.iloc[previous]
    )

    rsi_latest = float(
        rsi.iloc[latest]
    )

    price_change = (
        (price_latest - price_previous)
        / price_previous
        * 100
    )

    rsi_change = (
        rsi_latest - rsi_previous
    )

    if (
        price_latest > price_previous
        and rsi_latest < rsi_previous
        and abs(price_change)
        >= MIN_PRICE_DIFFERENCE_PERCENT
        and abs(rsi_change)
        >= MIN_RSI_DIFFERENCE
    ):

        return {
            "pivot_index": latest,
            "previous_index": previous,
            "price_previous": price_previous,
            "price_latest": price_latest,
            "rsi_previous": rsi_previous,
            "rsi_latest": rsi_latest,
            "price_change": price_change,
            "rsi_change": rsi_change,
        }

    return None


# ============================================================
# TRENDLINE
# ============================================================

def descending_trendline_break(df):

    highs = df["high"]

    pivots = pivot_highs(
        highs,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    indexes = np.where(pivots)[0]

    if len(indexes) < 2:
        return False

    p2 = indexes[-1]
    p1 = indexes[-2]

    if p2 - p1 > MAX_PIVOT_GAP:
        return False

    y1 = float(highs.iloc[p1])
    y2 = float(highs.iloc[p2])

    if y2 >= y1:
        return False

    slope = (
        y2 - y1
    ) / (
        p2 - p1
    )

    current_x = len(df) - 1

    trendline = (
        y1
        + slope * (current_x - p1)
    )

    current_close = float(
        df["close"].iloc[-1]
    )

    previous_close = float(
        df["close"].iloc[-2]
    )

    return (
        previous_close <= trendline
        and current_close > trendline
    )


def ascending_trendline_break(df):

    lows = df["low"]

    pivots = pivot_lows(
        lows,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    indexes = np.where(pivots)[0]

    if len(indexes) < 2:
        return False

    p2 = indexes[-1]
    p1 = indexes[-2]

    if p2 - p1 > MAX_PIVOT_GAP:
        return False

    y1 = float(lows.iloc[p1])
    y2 = float(lows.iloc[p2])

    if y2 <= y1:
        return False

    slope = (
        y2 - y1
    ) / (
        p2 - p1
    )

    current_x = len(df) - 1

    trendline = (
        y1
        + slope * (current_x - p1)
    )

    current_close = float(
        df["close"].iloc[-1]
    )

    previous_close = float(
        df["close"].iloc[-2]
    )

    return (
        previous_close >= trendline
        and current_close < trendline
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(
    df,
    key_value=3.0,
    atr_period=10
):

    close = df["close"]

    atr = calculate_atr(
        df,
        atr_period
    )

    loss = key_value * atr

    trailing_stop = np.zeros(
        len(df)
    )

    if len(df) == 0:

        return pd.Series(
            dtype=float
        )

    trailing_stop[0] = (
        close.iloc[0]
        - loss.iloc[0]
    )

    for i in range(1, len(df)):

        prev_stop = trailing_stop[i - 1]

        current_close = close.iloc[i]
        previous_close = close.iloc[i - 1]

        current_loss = loss.iloc[i]

        if (
            current_close > prev_stop
            and previous_close > prev_stop
        ):

            trailing_stop[i] = max(
                prev_stop,
                current_close - current_loss
            )

        elif (
            current_close < prev_stop
            and previous_close < prev_stop
        ):

            trailing_stop[i] = min(
                prev_stop,
                current_close + current_loss
            )

        elif current_close > prev_stop:

            trailing_stop[i] = (
                current_close - current_loss
            )

        else:

            trailing_stop[i] = (
                current_close + current_loss
            )

    return pd.Series(
        trailing_stop,
        index=df.index
    )


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def nearest_resistance(
    df,
    price
):

    candidates = [
        float(x)
        for x in df["high"].values
        if x > price
    ]

    if not candidates:
        return None

    return min(candidates)


def nearest_support(
    df,
    price
):

    candidates = [
        float(x)
        for x in df["low"].values
        if x < price
    ]

    if not candidates:
        return None

    return max(candidates)


# ============================================================
# SL / TP
# ============================================================

def build_sl_tp(
    side,
    entry,
    atr,
    swing_level
):

    if entry <= 0:
        return None

    if atr is None or atr <= 0:
        return None

    atr_distance = (
        atr * SL_ATR_MULTIPLIER
    )

    if side == "BUY":

        if swing_level is None:
            swing_distance = 0
        else:

            swing_sl = (
                swing_level
                * (
                    1
                    - SL_BUFFER_PERCENT / 100
                )
            )

            swing_distance = (
                entry - swing_sl
            )

    else:

        if swing_level is None:
            swing_distance = 0
        else:

            swing_sl = (
                swing_level
                * (
                    1
                    + SL_BUFFER_PERCENT / 100
                )
            )

            swing_distance = (
                swing_sl - entry
            )

    minimum_distance = (
        entry
        * MIN_SL_DISTANCE_PERCENT
        / 100
    )

    risk_distance = max(
        atr_distance,
        swing_distance,
        minimum_distance
    )

    risk_percent = (
        risk_distance
        / entry
        * 100
    )

    if risk_percent > MAX_SL_DISTANCE_PERCENT:
        return None

    if side == "BUY":

        sl = entry - risk_distance

        tp1 = (
            entry
            + risk_distance * TP1_R_MULTIPLE
        )

        tp2 = (
            entry
            + risk_distance * TP2_R_MULTIPLE
        )

        tp3 = (
            entry
            + risk_distance * TP3_R_MULTIPLE
        )

    else:

        sl = entry + risk_distance

        tp1 = (
            entry
            - risk_distance * TP1_R_MULTIPLE
        )

        tp2 = (
            entry
            - risk_distance * TP2_R_MULTIPLE
        )

        tp3 = (
            entry
            - risk_distance * TP3_R_MULTIPLE
        )

    return {
        "sl": sl,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "risk_distance": risk_distance,
        "risk_percent": risk_percent,
    }


# ============================================================
# PERCENT HELPERS
# ============================================================

def level_percent(
    side,
    entry,
    level
):

    if (
        entry is None
        or level is None
        or entry == 0
    ):
        return 0

    if side == "BUY":

        return (
            (level - entry)
            / entry
            * 100
        )

    return (
        (entry - level)
        / entry
        * 100
    )


def format_price(price):

    if price is None:
        return "-"

    price = float(price)

    if price >= 1000:
        return f"{price:,.2f}"

    if price >= 1:
        return f"{price:.4f}"

    if price >= 0.1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# OPEN TRADE CHECK
# ============================================================

def has_open_trade_for_symbol(
    state,
    symbol
):

    symbol = str(symbol).upper()

    for trade in get_all_trades(state):

        status = str(
            trade.get("status", "")
        ).upper()

        if status != "OPEN":
            continue

        if normalize_coin(trade) == symbol:
            return True

    return False


# ============================================================
# REGISTER SIGNAL
# ============================================================

def register_signal(
    state,
    signal
):

    symbol = str(
        signal["symbol"]
    ).upper()

    trades = get_all_trades(state)

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:

        for trade in trades:

            if (
                normalize_coin(trade) == symbol
                and str(
                    trade.get(
                        "status",
                        ""
                    )
                ).upper() == "OPEN"
            ):

                return False

    signal_id = signal.get(
        "signal_id"
    )

    if not signal_id:
        return False

    for trade in trades:

        if get_trade_id(trade) == signal_id:
            return False

    trade = dict(signal)

    trade["status"] = "OPEN"
    trade["id"] = signal_id
    trade["signal_id"] = signal_id
    trade["direction"] = signal["side"]
    trade["name"] = signal["symbol"]

    if isinstance(
        state.get("trades"),
        dict
    ):

        state["trades"][signal_id] = trade

    elif isinstance(
        state.get("trades"),
        list
    ):

        state["trades"].append(trade)

    else:

        state["trades"] = {
            signal_id: trade
        }

    return True


# ============================================================
# ANALYZE COIN
#
# IMPORTANT:
# This function now returns diagnostic information.
# ============================================================

def analyze_coin(symbol):

    df = get_candles(
        symbol,
        "5m"
    )

    trend_data = get_multi_timeframe_trend(
        symbol
    )

    trend_15m = trend_data["15m"]["trend"]
    trend_1h = trend_data["1h"]["trend"]

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    df["atr_sl"] = calculate_atr(
        df,
        SL_ATR_PERIOD
    )

    df["atr"] = calculate_atr(
        df,
        UT_ATR_PERIOD
    )

    df["ut_stop"] = calculate_ut_bot(
        df,
        UT_KEY_VALUE,
        UT_ATR_PERIOD
    )

    bullish_divergence = (
        find_bullish_divergence(df)
    )

    bearish_divergence = (
        find_bearish_divergence(df)
    )

    bullish_break = (
        descending_trendline_break(df)
    )

    bearish_break = (
        ascending_trendline_break(df)
    )

    current_close = float(
        df["close"].iloc[-1]
    )

    current_rsi = float(
        df["rsi"].iloc[-1]
    )

    current_atr_sl = float(
        df["atr_sl"].iloc[-1]
    )

    current_ut = float(
        df["ut_stop"].iloc[-1]
    )

    signal_time = int(
        df["time"].iloc[-1]
    )

    ut_buy = current_close > current_ut
    ut_sell = current_close < current_ut

    diagnostic = {
        "symbol": symbol,
        "bullish_divergence": bullish_divergence is not None,
        "bearish_divergence": bearish_divergence is not None,
        "bullish_break": bullish_break,
        "bearish_break": bearish_break,
        "ut_buy": ut_buy,
        "ut_sell": ut_sell,
        "trend_15m": trend_15m,
        "trend_1h": trend_1h,
        "current_rsi": current_rsi,
        "rejections": [],
        "candidate_sides": [],
    }

    signal = None

    # ========================================================
    # BUY DIAGNOSTIC
    # ========================================================

    if bullish_divergence is not None:

        diagnostic["candidate_sides"].append(
            "BUY"
        )

        buy_reasons = []

        confirmation_buy = (
            bullish_break or ut_buy
        )

        if not confirmation_buy:

            buy_reasons.append(
                "No bullish UT/Trendline confirmation"
            )

        if trend_15m != "BULLISH":

            buy_reasons.append(
                f"15M trend is {trend_15m}"
            )

        if trend_1h != "BULLISH":

            buy_reasons.append(
                f"1H trend is {trend_1h}"
            )

        if not buy_reasons:

            support = nearest_support(
                df,
                current_close
            )

            sl_tp = build_sl_tp(
                "BUY",
                current_close,
                current_atr_sl,
                support
            )

            if sl_tp is None:

                buy_reasons.append(
                    "SL distance exceeds MAX_SL_DISTANCE"
                )

            else:

                signal = {
                    "symbol": symbol,
                    "side": "BUY",
                    "direction": "BUY",
                    "name": symbol,
                    "entry": current_close,
                    "sl": sl_tp["sl"],
                    "tp1": sl_tp["tp1"],
                    "tp2": sl_tp["tp2"],
                    "tp3": sl_tp["tp3"],
                    "tp": sl_tp["tp1"],
                    "sl_percent": level_percent(
                        "BUY",
                        current_close,
                        sl_tp["sl"]
                    ),
                    "tp1_percent": level_percent(
                        "BUY",
                        current_close,
                        sl_tp["tp1"]
                    ),
                    "tp2_percent": level_percent(
                        "BUY",
                        current_close,
                        sl_tp["tp2"]
                    ),
                    "tp3_percent": level_percent(
                        "BUY",
                        current_close,
                        sl_tp["tp3"]
                    ),
                    "risk_percent": sl_tp[
                        "risk_percent"
                    ],
                    "atr": current_atr_sl,
                    "atr_multiplier": SL_ATR_MULTIPLIER,
                    "signal_time": signal_time,
                    "signal_time_iso": datetime.fromtimestamp(
                        signal_time / 1000,
                        tz=timezone.utc
                    ).isoformat(),
                    "trend_15m": trend_15m,
                    "trend_1h": trend_1h,
                    "reason": (
                        "Bullish RSI Divergence + "
                        "UT/Trendline + 15M/1H Bullish"
                    ),
                }

        for reason in buy_reasons:

            diagnostic["rejections"].append(
                f"BUY: {reason}"
            )

    # ========================================================
    # SELL DIAGNOSTIC
    # ========================================================

    if bearish_divergence is not None:

        diagnostic["candidate_sides"].append(
            "SELL"
        )

        sell_reasons = []

        confirmation_sell = (
            bearish_break or ut_sell
        )

        if not confirmation_sell:

            sell_reasons.append(
                "No bearish UT/Trendline confirmation"
            )

        if trend_15m != "BEARISH":

            sell_reasons.append(
                f"15M trend is {trend_15m}"
            )

        if trend_1h != "BEARISH":

            sell_reasons.append(
                f"1H trend is {trend_1h}"
            )

        if not sell_reasons:

            resistance = nearest_resistance(
                df,
                current_close
            )

            sl_tp = build_sl_tp(
                "SELL",
                current_close,
                current_atr_sl,
                resistance
            )

            if sl_tp is None:

                sell_reasons.append(
                    "SL distance exceeds MAX_SL_DISTANCE"
                )

            else:

                signal = {
                    "symbol": symbol,
                    "side": "SELL",
                    "direction": "SELL",
                    "name": symbol,
                    "entry": current_close,
                    "sl": sl_tp["sl"],
                    "tp1": sl_tp["tp1"],
                    "tp2": sl_tp["tp2"],
                    "tp3": sl_tp["tp3"],
                    "tp": sl_tp["tp1"],
                    "sl_percent": level_percent(
                        "SELL",
                        current_close,
                        sl_tp["sl"]
                    ),
                    "tp1_percent": level_percent(
                        "SELL",
                        current_close,
                        sl_tp["tp1"]
                    ),
                    "tp2_percent": level_percent(
                        "SELL",
                        current_close,
                        sl_tp["tp2"]
                    ),
                    "tp3_percent": level_percent(
                        "SELL",
                        current_close,
                        sl_tp["tp3"]
                    ),
                    "risk_percent": sl_tp[
                        "risk_percent"
                    ],
                    "atr": current_atr_sl,
                    "atr_multiplier": SL_ATR_MULTIPLIER,
                    "signal_time": signal_time,
                    "signal_time_iso": datetime.fromtimestamp(
                        signal_time / 1000,
                        tz=timezone.utc
                    ).isoformat(),
                    "trend_15m": trend_15m,
                    "trend_1h": trend_1h,
                    "reason": (
                        "Bearish RSI Divergence + "
                        "UT/Trendline + 15M/1H Bearish"
                    ),
                }

        for reason in sell_reasons:

            diagnostic["rejections"].append(
                f"SELL: {reason}"
            )

    # ========================================================
    # NO DIVERGENCE
    # ========================================================

    if (
        bullish_divergence is None
        and bearish_divergence is None
    ):

        diagnostic["rejections"].append(
            "No RSI divergence detected"
        )

    return {
        "symbol": symbol,
        "df": df,
        "signal": signal,
        "trend_data": trend_data,
        "market_symbol": get_market_symbol(symbol),
        "diagnostic": diagnostic,
    }


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):

    side = str(
        signal["side"]
    ).upper()

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    entry = signal["entry"]
    sl = signal["sl"]
    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    tp3 = signal["tp3"]

    text = []

    text.append(
        f"{icon} {signal['symbol']}/USDT - {side}"
    )

    text.append(
        f"Entry: {format_price(entry)}"
    )

    text.append(
        f"Stop Loss: {format_price(sl)} "
        f"({level_percent(side, entry, sl):+.2f}%)"
    )

    text.append(
        f"Target 1: {format_price(tp1)} "
        f"({level_percent(side, entry, tp1):+.2f}%)"
    )

    text.append(
        f"Target 2: {format_price(tp2)} "
        f"({level_percent(side, entry, tp2):+.2f}%)"
    )

    text.append(
        f"Target 3: {format_price(tp3)} "
        f"({level_percent(side, entry, tp3):+.2f}%)"
    )

    text.append(
        f"Risk: {abs(level_percent(side, entry, sl)):.2f}%"
    )

    text.append(
        f"RR: 1:{TP1_R_MULTIPLE:.1f} / "
        f"1:{TP2_R_MULTIPLE:.1f} / "
        f"1:{TP3_R_MULTIPLE:.1f}"
    )

    text.append(
        f"15M Trend: {signal.get('trend_15m', 'N/A')}"
    )

    text.append(
        f"1H Trend: {signal.get('trend_1h', 'N/A')}"
    )

    text.append(
        f"Reason: {signal['reason']}"
    )

    return "\n".join(text)


# ============================================================
# CLOSED SIGNAL
# ============================================================

def format_closed_signal(trade):

    side = normalize_side(trade)
    coin = normalize_coin(trade)

    icon = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    reason = (
        trade.get("result_reason")
        or trade.get("exit_reason")
        or "UNKNOWN"
    )

    result_icon = (
        "✅"
        if str(reason).upper().startswith("TP")
        else "❌"
        if str(reason).upper() == "SL"
        else "⚪"
    )

    entry = get_trade_entry(trade)

    exit_price = trade.get(
        "result_price",
        trade.get("exit_price")
    )

    try:
        exit_price = float(exit_price)
    except Exception:
        exit_price = None

    pnl = float(
        trade.get("pnl_percent", 0)
    )

    r_multiple = float(
        trade.get(
            "result_r",
            trade.get(
                "r_multiple",
                0
            )
        )
    )

    duration = trade.get(
        "duration_minutes",
        0
    )

    return "\n".join([
        f"{icon} {coin} {side} | "
        f"{result_icon} {reason}",
        f"Entry: {format_price(entry)}",
        f"Exit: {format_price(exit_price)}",
        f"P&L: {pnl:+.2f}%",
        f"R: {r_multiple:+.2f}R",
        f"Duration: {float(duration):.0f} min",
    ])


# ============================================================
# OPEN PERFORMANCE
# ============================================================

def calculate_open_performance(
    state,
    data_cache
):

    result = []

    for trade in get_all_trades(state):

        if str(
            trade.get("status", "")
        ).upper() != "OPEN":
            continue

        coin = normalize_coin(trade)

        df = data_cache.get(coin)

        if df is None or df.empty:
            continue

        entry = get_trade_entry(trade)
        sl = get_trade_sl(trade)
        side = normalize_side(trade)

        if (
            entry is None
            or sl is None
            or side not in ("BUY", "SELL")
        ):
            continue

        current_price = float(
            df["close"].iloc[-1]
        )

        if side == "BUY":

            pnl = (
                current_price - entry
            ) / entry * 100

        else:

            pnl = (
                entry - current_price
            ) / entry * 100

        risk = abs(entry - sl)

        if risk > 0:

            if side == "BUY":

                current_r = (
                    current_price - entry
                ) / risk

            else:

                current_r = (
                    entry - current_price
                ) / risk

        else:
            current_r = 0

        signal_time = parse_trade_time(
            trade.get("signal_time")
        )

        if signal_time is None:

            signal_time = parse_trade_time(
                trade.get("signal_time_iso")
            )

        if signal_time is None:

            mfe = 0
            mae = 0
            duration = 0

        else:

            after_signal = df[
                df["time"] > signal_time
            ]

            if after_signal.empty:

                mfe = 0
                mae = 0

            elif side == "BUY":

                best = float(
                    after_signal["high"].max()
                )

                worst = float(
                    after_signal["low"].min()
                )

                mfe = (
                    best - entry
                ) / entry * 100

                mae = (
                    worst - entry
                ) / entry * 100

            else:

                best = float(
                    after_signal["low"].min()
                )

                worst = float(
                    after_signal["high"].max()
                )

                mfe = (
                    entry - best
                ) / entry * 100

                mae = (
                    entry - worst
                ) / entry * 100

            last_time = int(
                df["time"].iloc[-1]
            )

            duration = max(
                0,
                (
                    last_time - signal_time
                ) / 60000
            )

        result.append({
            "symbol": coin,
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp1": get_trade_tp1(trade),
            "tp2": get_trade_tp2(trade),
            "tp3": get_trade_tp3(trade),
            "current_price": current_price,
            "pnl_percent": pnl,
            "current_r": current_r,
            "mfe": mfe,
            "mae": mae,
            "duration_minutes": duration,
        })

    return result


# ============================================================
# EVALUATE OPEN TRADES
# ============================================================

def evaluate_open_trades(
    state,
    data_cache
):

    changed = False

    for trade in get_all_trades(state):

        if str(
            trade.get("status", "")
        ).upper() != "OPEN":
            continue

        coin = normalize_coin(trade)
        df = data_cache.get(coin)

        if df is None or df.empty:
            continue

        side = normalize_side(trade)

        entry = get_trade_entry(trade)
        sl = get_trade_sl(trade)

        if (
            entry is None
            or sl is None
            or side not in ("BUY", "SELL")
        ):
            continue

        tp1 = get_trade_tp1(trade)
        tp2 = get_trade_tp2(trade)
        tp3 = get_trade_tp3(trade)

        signal_time = parse_trade_time(
            trade.get("signal_time")
        )

        if signal_time is None:
            signal_time = parse_trade_time(
                trade.get("signal_time_iso")
            )

        if signal_time is None:
            continue

        after_signal = df[
            df["time"] > signal_time
        ]

        if after_signal.empty:
            continue

        exit_reason = None
        exit_price = None
        exit_time = None

        for _, row in after_signal.iterrows():

            high = float(row["high"])
            low = float(row["low"])
            candle_time = int(row["time"])

            if side == "BUY":

                if low <= sl:

                    exit_reason = "SL"
                    exit_price = sl
                    exit_time = candle_time
                    break

                if tp1 is not None and high >= tp1:

                    exit_reason = "TP1"
                    exit_price = tp1
                    exit_time = candle_time
                    break

                if tp2 is not None and high >= tp2:

                    exit_reason = "TP2"
                    exit_price = tp2
                    exit_time = candle_time
                    break

                if tp3 is not None and high >= tp3:

                    exit_reason = "TP3"
                    exit_price = tp3
                    exit_time = candle_time
                    break

            else:

                if high >= sl:

                    exit_reason = "SL"
                    exit_price = sl
                    exit_time = candle_time
                    break

                if tp1 is not None and low <= tp1:

                    exit_reason = "TP1"
                    exit_price = tp1
                    exit_time = candle_time
                    break

                if tp2 is not None and low <= tp2:

                    exit_reason = "TP2"
                    exit_price = tp2
                    exit_time = candle_time
                    break

                if tp3 is not None and low <= tp3:

                    exit_reason = "TP3"
                    exit_price = tp3
                    exit_time = candle_time
                    break

        if exit_reason is None:
            continue

        if side == "BUY":

            pnl = (
                exit_price - entry
            ) / entry * 100

            r_multiple = (
                exit_price - entry
            ) / abs(entry - sl)

        else:

            pnl = (
                entry - exit_price
            ) / entry * 100

            r_multiple = (
                entry - exit_price
            ) / abs(entry - sl)

        duration = max(
            0,
            (
                exit_time - signal_time
            ) / 60000
        )

        trade["status"] = "CLOSED"
        trade["exit_reason"] = exit_reason
        trade["result_reason"] = exit_reason
        trade["exit_price"] = exit_price
        trade["result_price"] = exit_price
        trade["exit_time"] = exit_time
        trade["pnl_percent"] = pnl
        trade["r_multiple"] = r_multiple
        trade["result_r"] = r_multiple
        trade["duration_minutes"] = duration

        trade["closed_at"] = datetime.fromtimestamp(
            exit_time / 1000,
            tz=timezone.utc
        ).isoformat()

        changed = True

    return changed


# ============================================================
# NEWLY CLOSED
# ============================================================

def get_newly_closed_trades(
    state,
    previous_closed_ids
):

    result = []

    for trade in get_all_trades(state):

        if str(
            trade.get("status", "")
        ).upper() != "CLOSED":
            continue

        trade_id = get_trade_id(trade)

        if (
            trade_id
            and trade_id not in previous_closed_ids
        ):

            result.append(trade)

    return result


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(state):

    trades = get_all_trades(state)

    open_trades = [
        x for x in trades
        if str(
            x.get("status", "")
        ).upper() == "OPEN"
    ]

    closed_trades = [
        x for x in trades
        if str(
            x.get("status", "")
        ).upper() == "CLOSED"
    ]

    wins = [
        x for x in closed_trades
        if float(
            x.get("pnl_percent", 0)
        ) > 0
    ]

    losses = [
        x for x in closed_trades
        if float(
            x.get("pnl_percent", 0)
        ) <= 0
    ]

    total_pnl = sum(
        float(
            x.get("pnl_percent", 0)
        )
        for x in closed_trades
    )

    win_rate = (
        len(wins)
        / len(closed_trades)
        * 100
        if closed_trades
        else 0
    )

    avg_win = (
        float(np.mean([
            float(
                x.get("pnl_percent", 0)
            )
            for x in wins
        ]))
        if wins
        else 0
    )

    avg_loss = (
        float(np.mean([
            float(
                x.get("pnl_percent", 0)
            )
            for x in losses
        ]))
        if losses
        else 0
    )

    return {
        "total": len(trades),
        "open": len(open_trades),
        "closed": len(closed_trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
    }


# ============================================================
# DIAGNOSTIC STATISTICS
# ============================================================

def calculate_diagnostics(results):

    stats = {
        "bullish_divergence": 0,
        "bearish_divergence": 0,
        "bullish_break": 0,
        "bearish_break": 0,
        "ut_buy": 0,
        "ut_sell": 0,
        "setup_candidates": 0,
    }

    for result in results:

        diagnostic = result.get(
            "diagnostic",
            {}
        )

        if diagnostic.get(
            "bullish_divergence"
        ):
            stats["bullish_divergence"] += 1

        if diagnostic.get(
            "bearish_divergence"
        ):
            stats["bearish_divergence"] += 1

        if diagnostic.get(
            "bullish_break"
        ):
            stats["bullish_break"] += 1

        if diagnostic.get(
            "bearish_break"
        ):
            stats["bearish_break"] += 1

        if diagnostic.get(
            "ut_buy"
        ):
            stats["ut_buy"] += 1

        if diagnostic.get(
            "ut_sell"
        ):
            stats["ut_sell"] += 1

        stats["setup_candidates"] += len(
            diagnostic.get(
                "candidate_sides",
                []
            )
        )

    return stats


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    state,
    results,
    errors,
    open_performance,
    closed_this_run,
    blocked_symbols=None,
    registered_signals=None
):

    if blocked_symbols is None:
        blocked_symbols = []

    if registered_signals is None:
        registered_signals = []

    stats = calculate_statistics(state)
    diagnostic = calculate_diagnostics(results)

    lines = []

    lines.append(
        "📡 CRYPTO DIVERGENCE SCANNER v10.4"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        f"⏱ Timeframe: {TIMEFRAME.upper()} CLOSED"
    )

    lines.append(
        f"📊 DATA OK: {len(results)}/{len(COINS)}"
    )

    lines.append(
        f"⚠️ DATA ERROR: {len(errors)}"
    )

    # ========================================================
    # CUMULATIVE
    # ========================================================

    lines.append("")
    lines.append("📊 CUMULATIVE PERFORMANCE")
    lines.append("━━━━━━━━━━━━━━━━━━")

    lines.append(
        f"Total Trades: {stats['total']}"
    )

    lines.append(
        f"Open: {stats['open']}"
    )

    lines.append(
        f"Closed: {stats['closed']}"
    )

    lines.append(
        f"Wins: {stats['wins']}"
    )

    lines.append(
        f"Losses: {stats['losses']}"
    )

    lines.append(
        f"Win Rate: {stats['win_rate']:.2f}%"
    )

    lines.append(
        f"Closed P&L: {stats['total_pnl']:.2f}%"
    )

    lines.append(
        f"Avg Win: {stats['avg_win']:.2f}%"
    )

    lines.append(
        f"Avg Loss: {stats['avg_loss']:.2f}%"
    )

    # ========================================================
    # SIGNAL DIAGNOSTIC
    # ========================================================

    lines.append("")
    lines.append("📊 SIGNAL DIAGNOSTIC")
    lines.append("━━━━━━━━━━━━━━━━━━")

    lines.append(
        f"RSI Bullish Divergence: "
        f"{diagnostic['bullish_divergence']}"
    )

    lines.append(
        f"RSI Bearish Divergence: "
        f"{diagnostic['bearish_divergence']}"
    )

    lines.append(
        f"Trendline Breakout: "
        f"{diagnostic['bullish_break']}"
    )

    lines.append(
        f"Trendline Breakdown: "
        f"{diagnostic['bearish_break']}"
    )

    lines.append(
        f"UT Bot BUY: "
        f"{diagnostic['ut_buy']}"
    )

    lines.append(
        f"UT Bot SELL: "
        f"{diagnostic['ut_sell']}"
    )

    lines.append(
        f"SETUP CANDIDATES: "
        f"{diagnostic['setup_candidates']}"
    )

    lines.append(
        f"FINAL SIGNALS: "
        f"{len(registered_signals)}"
    )

    # ========================================================
    # REJECTED CANDIDATES
    # ========================================================

    rejected = []

    for result in results:

        diag = result.get(
            "diagnostic",
            {}
        )

        reasons = diag.get(
            "rejections",
            []
        )

        if reasons:

            rejected.append(
                (
                    result["symbol"],
                    reasons
                )
            )

    if rejected:

        lines.append("")
        lines.append("🔎 REJECTED SETUPS")
        lines.append("━━━━━━━━━━━━━━━━━━")

        for symbol, reasons in rejected:

            diag = next(
                (
                    x.get("diagnostic", {})
                    for x in results
                    if x["symbol"] == symbol
                ),
                {}
            )

            candidate_sides = diag.get(
                "candidate_sides",
                []
            )

            if candidate_sides:

                lines.append(
                    f"{symbol} "
                    f"[{', '.join(candidate_sides)}]"
                )

            else:

                lines.append(
                    f"{symbol}"
                )

            for reason in reasons:

                lines.append(
                    f"  ❌ {reason}"
                )

    # ========================================================
    # BLOCKED
    # ========================================================

    if blocked_symbols:

        lines.append("")
        lines.append("🔒 SYMBOL LOCK")
        lines.append("━━━━━━━━━━━━━━━━━━")

        for symbol in sorted(
            set(blocked_symbols)
        ):

            lines.append(
                f"{symbol}: OPEN trade already exists"
            )

    # ========================================================
    # CLOSED SIGNALS
    # ========================================================

    lines.append("")
    lines.append("🏁 CLOSED SIGNALS")
    lines.append("━━━━━━━━━━━━━━━━━━")

    if closed_this_run:

        for trade in closed_this_run:

            lines.append(
                format_closed_signal(trade)
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "در این اجرا معامله‌ای بسته نشده است."
        )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    lines.append("")
    lines.append("🚨 NEW SIGNALS")
    lines.append("━━━━━━━━━━━━━━━━━━")

    if registered_signals:

        for signal in registered_signals:

            lines.append(
                format_signal(signal)
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "فعلاً سیگنال جدیدی وجود ندارد."
        )

    # ========================================================
    # OPEN PERFORMANCE
    # ========================================================

    lines.append("")
    lines.append("📌 OPEN SIGNAL P&L")
    lines.append("━━━━━━━━━━━━━━━━━━")

    if open_performance:

        for item in open_performance:

            icon = (
                "🟢"
                if item["side"] == "BUY"
                else "🔴"
            )

            side = item["side"]
            entry = item["entry"]
            sl = item["sl"]
            pnl = item["pnl_percent"]

            lines.append(
                f"{icon} "
                f"{item['symbol']} {side}"
            )

            lines.append(
                f"Entry: {format_price(entry)}"
            )

            lines.append(
                f"Current: "
                f"{format_price(item['current_price'])}"
            )

            lines.append(
                f"SL: {format_price(sl)} "
                f"({level_percent(side, entry, sl):+.2f}%)"
            )

            if item.get("tp1") is not None:

                lines.append(
                    f"TP1: "
                    f"{format_price(item['tp1'])} "
                    f"({level_percent(side, entry, item['tp1']):+.2f}%)"
                )

            if item.get("tp2") is not None:

                lines.append(
                    f"TP2: "
                    f"{format_price(item['tp2'])} "
                    f"({level_percent(side, entry, item['tp2']):+.2f}%)"
                )

            if item.get("tp3") is not None:

                lines.append(
                    f"TP3: "
                    f"{format_price(item['tp3'])} "
                    f"({level_percent(side, entry, item['tp3']):+.2f}%)"
                )

            # =================================================
            # BOLD CURRENT P&L
            # =================================================

            lines.append(
                f"Current P&L: *{pnl:+.2f}%*"
            )

            lines.append(
                f"Current R: "
                f"{item['current_r']:+.2f}R"
            )

            lines.append(
                f"MFE: "
                f"{item['mfe']:+.2f}%"
            )

            lines.append(
                f"MAE: "
                f"{item['mae']:+.2f}%"
            )

            lines.append(
                f"Duration: "
                f"{item['duration_minutes']:.0f} min"
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "هیچ سیگنال بازی وجود ندارد."
        )

    # ========================================================
    # ERRORS
    # ========================================================

    if errors:

        lines.append("")
        lines.append("⚠️ ERRORS")
        lines.append("━━━━━━━━━━━━━━━━━━")

        for symbol, error in errors.items():

            lines.append(
                f"{symbol}: {error}"
            )

    # ========================================================
    # NO SIGNAL SUMMARY
    # ========================================================

    if not registered_signals:

        lines.append("")
        lines.append("📌 NO SIGNAL SUMMARY")
        lines.append("━━━━━━━━━━━━━━━━━━")

        if diagnostic["setup_candidates"] == 0:

            lines.append(
                "❌ No RSI divergence candidate."
            )

        elif diagnostic["bullish_divergence"] > 0:

            lines.append(
                "BUY candidates exist, "
                "but one or more filters rejected them."
            )

        elif diagnostic["bearish_divergence"] > 0:

            lines.append(
                "SELL candidates exist, "
                "but one or more filters rejected them."
            )

        if diagnostic["setup_candidates"] > 0:

            lines.append(
                "Check REJECTED SETUPS above."
            )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "Loading Kraken Futures instruments..."
    )

    try:

        market_map = load_market_map()

        print(
            f"Loaded {len(market_map)} markets."
        )

    except Exception as e:

        print(
            f"FATAL: {e}"
        )

        return

    # ========================================================
    # LOAD FRESH v10.4 STATE
    # ========================================================

    state = load_state()

    rebuild_trade_container(state)

    # ========================================================
    # PREVIOUS CLOSED IDS
    # ========================================================

    previous_closed_ids = set()

    for trade in get_all_trades(state):

        if str(
            trade.get("status", "")
        ).upper() == "CLOSED":

            trade_id = get_trade_id(trade)

            if trade_id:
                previous_closed_ids.add(trade_id)

    results = []
    errors = {}
    data_cache = {}
    blocked_symbols = []

    # ========================================================
    # SCAN ALL COINS
    # ========================================================

    for symbol in COINS:

        try:

            result = analyze_coin(symbol)

            results.append(result)

            data_cache[symbol] = result["df"]

        except Exception as e:

            errors[symbol] = str(e)

        time.sleep(0.05)

    # ========================================================
    # CLOSE OLD OPEN TRADES
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache
    )

    if changed:
        save_state(state)

    # ========================================================
    # REGISTER NEW SIGNALS
    # ========================================================

    new_registered = []

    for result in results:

        signal = result.get("signal")

        if signal is None:
            continue

        symbol = signal["symbol"]

        # ----------------------------------------------------
        # SYMBOL LOCK
        # ----------------------------------------------------

        if (
            not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
            and has_open_trade_for_symbol(
                state,
                symbol
            )
        ):

            blocked_symbols.append(symbol)

            result["diagnostic"].setdefault(
                "rejections",
                []
            ).append(
                "BLOCKED: Symbol already has OPEN trade"
            )

            continue

        signal_id = make_signal_id(
            signal["symbol"],
            signal["side"],
            signal["signal_time"],
            signal["entry"]
        )

        signal["signal_id"] = signal_id
        signal["id"] = signal_id

        if register_signal(
            state,
            signal
        ):

            new_registered.append(signal)

    # ========================================================
    # SAVE
    # ========================================================

    save_state(state)

    # ========================================================
    # SECOND EVALUATION
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache
    )

    if changed:
        save_state(state)

    # ========================================================
    # CLOSED THIS RUN
    # ========================================================

    closed_this_run = (
        get_newly_closed_trades(
            state,
            previous_closed_ids
        )
    )

    # ========================================================
    # OPEN PERFORMANCE
    # ========================================================

    open_performance = (
        calculate_open_performance(
            state,
            data_cache
        )
    )

    # ========================================================
    # REPORT
    # ========================================================

    report = format_report(
        state,
        results,
        errors,
        open_performance,
        closed_this_run,
        blocked_symbols,
        new_registered
    )

    print()
    print(report)

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_telegram(report)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
