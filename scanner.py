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
# Fixed BUY / SELL Emojis
# ATR Based SL
# R Based TP
# SL / TP Percentages
# Telegram
#
# v10.4 CHANGES:
# - NEW STATE FILE: trade_history_v10.4.json
# - v10.3 statistics/trades are NOT imported
# - Only one OPEN trade allowed per symbol
# - No duplicate signal while symbol has OPEN trade
# - No opposite-direction hedge while symbol has OPEN trade
# - 15M trend filter
# - 1H trend filter
# - BUY requires 15M + 1H bullish trend
# - SELL requires 15M + 1H bearish trend
# - Trend = EMA20 / EMA50 + price position
# - Current P&L displayed in Telegram as Markdown Bold
# - No P&L emoji
# - Existing ATR / SL / TP / MFE / MAE logic preserved
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
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "ATOM",
    "UNI",
    "AAVE",
    "FIL",
    "ETC",
    "NEAR",
    "APT",
    "ARB",
    "OP",
    "SUI",
    "SEI",
    "INJ",
    "TIA",
    "TRX",
    "XLM",
    "ALGO",
    "VET",
    "MATIC",
    "HBAR",
]


# ============================================================
# IMPORTANT:
# NEW STATE FILE FOR v10.4
#
# This intentionally does NOT use v10.3 trade_history.json.
# Therefore cumulative statistics start from ZERO.
# ============================================================

STATE_FILE = "trade_history_v10.4.json"


# ============================================================
# ONE OPEN TRADE PER SYMBOL
#
# False = no duplicate and no hedge
# ============================================================

ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False


REQUEST_TIMEOUT = 20


# ============================================================
# MULTI-TIMEFRAME TREND FILTER
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
# DIVERGENCE PIVOTS
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

        r = SESSION.post(
            url,
            json=payload,
            timeout=REQUEST_TIMEOUT
        )

        return r.ok

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

    # --------------------------------------------------------
    # v10.4 deliberately uses a new state file.
    # Old v10.3 statistics are therefore NOT loaded.
    # --------------------------------------------------------

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

    if isinstance(
        trades,
        dict
    ):

        result = []

        for key, trade in trades.items():

            if isinstance(
                trade,
                dict
            ):

                if not trade.get("id"):
                    trade["id"] = key

                if not trade.get("signal_id"):
                    trade["signal_id"] = key

                result.append(
                    trade
                )

        return result

    if isinstance(
        trades,
        list
    ):

        return [
            x for x in trades
            if isinstance(x, dict)
        ]

    return []


def normalize_side(trade):

    side = trade.get(
        "side"
    )

    if not side:

        side = trade.get(
            "direction"
        )

    if not side:
        return ""

    side = str(
        side
    ).upper().strip()

    if side in (
        "LONG",
        "BUY"
    ):
        return "BUY"

    if side in (
        "SHORT",
        "SELL"
    ):
        return "SELL"

    return side


def normalize_coin(trade):

    name = trade.get(
        "name"
    )

    if name:

        return str(
            name
        ).upper()

    symbol = trade.get(
        "symbol",
        ""
    )

    symbol = str(
        symbol
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

    if isinstance(
        value,
        (int, float)
    ):

        return int(
            value
        )

    value = str(
        value
    ).strip()

    if not value:
        return None

    try:

        return int(
            float(value)
        )

    except Exception:
        pass

    try:

        iso = value

        if iso.endswith("Z"):
            iso = iso[:-1] + "+00:00"

        dt = datetime.fromisoformat(
            iso
        )

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

    value = trade.get(
        "tp1"
    )

    if value is None:

        value = trade.get(
            "tp"
        )

    if value is None:
        return None

    try:

        return float(value)

    except Exception:

        return None


def get_trade_tp2(trade):

    value = trade.get(
        "tp2"
    )

    if value is None:
        return None

    try:

        return float(value)

    except Exception:

        return None


def get_trade_tp3(trade):

    value = trade.get(
        "tp3"
    )

    if value is None:
        return None

    try:

        return float(value)

    except Exception:

        return None


def get_trade_entry(trade):

    try:

        return float(
            trade.get(
                "entry"
            )
        )

    except Exception:

        return None


def get_trade_sl(trade):

    try:

        return float(
            trade.get(
                "sl"
            )
        )

    except Exception:

        return None


def get_trade_id(trade):

    value = trade.get(
        "signal_id"
    )

    if value:
        return str(value)

    value = trade.get(
        "id"
    )

    if value:
        return str(value)

    return None


def rebuild_trade_container(state):

    trades = state.get(
        "trades",
        {}
    )

    if isinstance(
        trades,
        dict
    ):

        rebuilt = {}

        for trade in get_all_trades(state):

            trade_id = get_trade_id(
                trade
            )

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

            rebuilt[
                trade_id
            ] = trade

        state["trades"] = rebuilt

    elif isinstance(
        trades,
        list
    ):

        state["trades"] = get_all_trades(
            state
        )

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
# KRAKEN FUTURES INSTRUMENTS
# ============================================================

def load_market_map():

    global MARKET_MAP

    try:

        r = SESSION.get(
            INSTRUMENTS_URL,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        instruments = data.get(
            "instruments",
            []
        )

        result = {}

        for item in instruments:

            symbol = str(
                item.get(
                    "symbol",
                    ""
                )
            ).upper()

            base = str(
                item.get(
                    "base",
                    ""
                )
            ).upper()

            quote = str(
                item.get(
                    "quote",
                    ""
                )
            ).upper()

            instrument_type = str(
                item.get(
                    "type",
                    ""
                )
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

            score = 0

            if symbol.startswith("PF_"):
                score += 100

            if instrument_type == "flexible_futures":
                score += 80

            if instrument_type == "futures_inverse":
                score += 50

            normalized_base = base

            if normalized_base == "XBT":
                normalized_base = "BTC"

            if normalized_base not in COINS:
                continue

            candidate = (
                score,
                symbol
            )

            if (
                normalized_base not in result
                or candidate[0]
                > result[normalized_base][0]
            ):

                result[
                    normalized_base
                ] = candidate

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

    coin = str(
        coin
    ).upper()

    if coin == "XBT":
        coin = "BTC"

    if coin not in MARKET_MAP:
        load_market_map()

    symbol = MARKET_MAP.get(
        coin
    )

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

    futures_symbol = get_market_symbol(
        symbol
    )

    url = (
        f"{BASE_URL}/trade/"
        f"{futures_symbol}/"
        f"{timeframe}"
    )

    params = {
        "count": CANDLE_LIMIT
    }

    r = SESSION.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT
    )

    if not r.ok:

        raise RuntimeError(
            f"HTTP {r.status_code} | "
            f"{futures_symbol} | "
            f"{r.text[:250]}"
        )

    data = r.json()

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

    for c in candles:

        try:

            if isinstance(
                c,
                dict
            ):

                timestamp = c.get(
                    "time"
                )

                open_price = c.get(
                    "open"
                )

                high_price = c.get(
                    "high"
                )

                low_price = c.get(
                    "low"
                )

                close_price = c.get(
                    "close"
                )

                volume = c.get(
                    "volume",
                    0
                )

            elif isinstance(
                c,
                (list, tuple)
            ):

                if len(c) < 6:
                    continue

                timestamp = c[0]
                open_price = c[1]
                high_price = c[2]
                low_price = c[3]
                close_price = c[4]
                volume = c[5]

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

    df = pd.DataFrame(
        rows
    )

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    ).reset_index(
        drop=True
    )

    # ========================================================
    # REMOVE CURRENT INCOMPLETE CANDLE
    # ========================================================

    now_ms = int(
        time.time() * 1000
    )

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

    if len(df) > 0:

        last_time = int(
            df.iloc[-1]["time"]
        )

        if (
            last_time + candle_ms
            > now_ms
        ):

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
# MULTI-TIMEFRAME TREND
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


def get_multi_timeframe_trend(
    symbol
):

    trend_data = {}

    for timeframe in TREND_TIMEFRAMES:

        df = get_candles(
            symbol,
            timeframe
        )

        trend = determine_trend(
            df
        )

        trend_data[
            timeframe
        ] = {
            "trend": trend,
            "df": df,
        }

    return trend_data


def trend_allows_signal(
    side,
    trend_data
):

    trend_15m = trend_data[
        "15m"
    ]["trend"]

    trend_1h = trend_data[
        "1h"
    ]["trend"]

    if side == "BUY":

        return (
            trend_15m == "BULLISH"
            and trend_1h == "BULLISH"
        )

    if side == "SELL":

        return (
            trend_15m == "BEARISH"
            and trend_1h == "BEARISH"
        )

    return False


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

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

            if (
                np.sum(
                    window == values[i]
                ) == 1
            ):

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

            if (
                np.sum(
                    window == values[i]
                ) == 1
            ):

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

    indexes = np.where(
        pivots
    )[0]

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
        (
            price_latest
            - price_previous
        )
        / price_previous
        * 100
    )

    rsi_change = (
        rsi_latest
        - rsi_previous
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

    indexes = np.where(
        pivots
    )[0]

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
        (
            price_latest
            - price_previous
        )
        / price_previous
        * 100
    )

    rsi_change = (
        rsi_latest
        - rsi_previous
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
# TRENDLINE BREAK
# ============================================================

def descending_trendline_break(df):

    highs = df["high"]

    pivots = pivot_highs(
        highs,
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    indexes = np.where(
        pivots
    )[0]

    if len(indexes) < 2:
        return False

    p2 = indexes[-1]
    p1 = indexes[-2]

    if p2 - p1 > MAX_PIVOT_GAP:
        return False

    y1 = float(
        highs.iloc[p1]
    )

    y2 = float(
        highs.iloc[p2]
    )

    if y2 >= y1:
        return False

    x1 = p1
    x2 = p2

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    current_x = len(df) - 1

    trendline = (
        y1
        + slope * (
            current_x - x1
        )
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

    indexes = np.where(
        pivots
    )[0]

    if len(indexes) < 2:
        return False

    p2 = indexes[-1]
    p1 = indexes[-2]

    if p2 - p1 > MAX_PIVOT_GAP:
        return False

    y1 = float(
        lows.iloc[p1]
    )

    y2 = float(
        lows.iloc[p2]
    )

    if y2 <= y1:
        return False

    x1 = p1
    x2 = p2

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    current_x = len(df) - 1

    trendline = (
        y1
        + slope * (
            current_x - x1
        )
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

    previous_close = close.shift(
        1
    )

    tr1 = high - low

    tr2 = (
        high
        - previous_close
    ).abs()

    tr3 = (
        low
        - previous_close
    ).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(
        axis=1
    )

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

    loss = (
        key_value
        * atr
    )

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

    for i in range(
        1,
        len(df)
    ):

        prev_stop = trailing_stop[
            i - 1
        ]

        current_close = close.iloc[i]

        previous_close = close.iloc[
            i - 1
        ]

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
                current_close
                - current_loss
            )

        else:

            trailing_stop[i] = (
                current_close
                + current_loss
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

    highs = df["high"].values

    candidates = [
        float(x)
        for x in highs
        if x > price
    ]

    if not candidates:
        return None

    return min(
        candidates
    )


def nearest_support(
    df,
    price
):

    lows = df["low"].values

    candidates = [
        float(x)
        for x in lows
        if x < price
    ]

    if not candidates:
        return None

    return max(
        candidates
    )


# ============================================================
# BUILD SL / TP
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
        atr
        * SL_ATR_MULTIPLIER
    )

    if side == "BUY":

        if swing_level is None:

            swing_distance = 0

        else:

            swing_sl = (
                swing_level
                * (
                    1
                    - SL_BUFFER_PERCENT
                    / 100
                )
            )

            swing_distance = (
                entry
                - swing_sl
            )

    else:

        if swing_level is None:

            swing_distance = 0

        else:

            swing_sl = (
                swing_level
                * (
                    1
                    + SL_BUFFER_PERCENT
                    / 100
                )
            )

            swing_distance = (
                swing_sl
                - entry
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

        sl = (
            entry
            - risk_distance
        )

    else:

        sl = (
            entry
            + risk_distance
        )

    if side == "BUY":

        tp1 = (
            entry
            + risk_distance
            * TP1_R_MULTIPLE
        )

        tp2 = (
            entry
            + risk_distance
            * TP2_R_MULTIPLE
        )

        tp3 = (
            entry
            + risk_distance
            * TP3_R_MULTIPLE
        )

    else:

        tp1 = (
            entry
            - risk_distance
            * TP1_R_MULTIPLE
        )

        tp2 = (
            entry
            - risk_distance
            * TP2_R_MULTIPLE
        )

        tp3 = (
            entry
            - risk_distance
            * TP3_R_MULTIPLE
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
# PERCENT FROM ENTRY
# ============================================================

def price_percent_from_entry(
    entry,
    price
):

    if (
        entry is None
        or price is None
        or entry == 0
    ):

        return 0

    return (
        (
            float(price)
            - float(entry)
        )
        / float(entry)
        * 100
    )


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
            (
                level
                - entry
            )
            / entry
            * 100
        )

    return (
        (
            entry
            - level
        )
        / entry
        * 100
    )


# ============================================================
# CHECK OPEN SYMBOL
# ============================================================

def has_open_trade_for_symbol(
    state,
    symbol
):

    symbol = str(
        symbol
    ).upper()

    for trade in get_all_trades(state):

        status = str(
            trade.get(
                "status",
                ""
            )
        ).upper()

        if status != "OPEN":
            continue

        trade_symbol = normalize_coin(
            trade
        )

        if trade_symbol == symbol:
            return True

    return False


# ============================================================
# SIGNAL REGISTRATION
# ============================================================

def register_signal(
    state,
    signal
):

    symbol = str(
        signal["symbol"]
    ).upper()

    trades = get_all_trades(
        state
    )

    # --------------------------------------------------------
    # HARD BLOCK:
    # Any OPEN trade on this symbol blocks BOTH BUY and SELL.
    # This prevents duplicate signals and hedge.
    # --------------------------------------------------------

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:

        for trade in trades:

            trade_symbol = normalize_coin(
                trade
            )

            trade_status = str(
                trade.get(
                    "status",
                    ""
                )
            ).upper()

            if (
                trade_symbol == symbol
                and trade_status == "OPEN"
            ):

                return False

    signal_id = signal.get(
        "signal_id"
    )

    if not signal_id:
        return False

    for trade in trades:

        existing_id = get_trade_id(
            trade
        )

        if existing_id == signal_id:

            return False

    trade = dict(
        signal
    )

    trade["status"] = "OPEN"

    trade["id"] = signal_id
    trade["signal_id"] = signal_id

    trade["direction"] = signal["side"]
    trade["name"] = signal["symbol"]

    if isinstance(
        state.get("trades"),
        dict
    ):

        state["trades"][
            signal_id
        ] = trade

    elif isinstance(
        state.get("trades"),
        list
    ):

        state["trades"].append(
            trade
        )

    else:

        state["trades"] = {
            signal_id: trade
        }

    return True


# ============================================================
# EVALUATE OPEN TRADES
# ============================================================

def evaluate_open_trades(
    state,
    data_cache
):

    changed = False

    trades = get_all_trades(
        state
    )

    for trade in trades:

        status = str(
            trade.get(
                "status",
                ""
            )
        ).upper()

        if status != "OPEN":
            continue

        coin = normalize_coin(
            trade
        )

        if not coin:
            continue

        df = data_cache.get(
            coin
        )

        if df is None or df.empty:
            continue

        side = normalize_side(
            trade
        )

        if side not in (
            "BUY",
            "SELL"
        ):

            continue

        entry = get_trade_entry(
            trade
        )

        sl = get_trade_sl(
            trade
        )

        if entry is None or sl is None:
            continue

        tp1 = get_trade_tp1(
            trade
        )

        tp2 = get_trade_tp2(
            trade
        )

        tp3 = get_trade_tp3(
            trade
        )

        signal_time = parse_trade_time(
            trade.get(
                "signal_time"
            )
        )

        if signal_time is None:

            signal_time = parse_trade_time(
                trade.get(
                    "signal_time_iso"
                )
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

        # ====================================================
        # BUY
        # ====================================================

        if side == "BUY":

            for _, row in after_signal.iterrows():

                candle_high = float(
                    row["high"]
                )

                candle_low = float(
                    row["low"]
                )

                candle_time = int(
                    row["time"]
                )

                if candle_low <= sl:

                    exit_reason = "SL"
                    exit_price = sl
                    exit_time = candle_time

                    break

                if (
                    tp1 is not None
                    and candle_high >= tp1
                ):

                    exit_reason = "TP1"
                    exit_price = tp1
                    exit_time = candle_time

                    break

                if (
                    tp2 is not None
                    and candle_high >= tp2
                ):

                    exit_reason = "TP2"
                    exit_price = tp2
                    exit_time = candle_time

                    break

                if (
                    tp3 is not None
                    and candle_high >= tp3
                ):

                    exit_reason = "TP3"
                    exit_price = tp3
                    exit_time = candle_time

                    break

        # ====================================================
        # SELL
        # ====================================================

        elif side == "SELL":

            for _, row in after_signal.iterrows():

                candle_high = float(
                    row["high"]
                )

                candle_low = float(
                    row["low"]
                )

                candle_time = int(
                    row["time"]
                )

                if candle_high >= sl:

                    exit_reason = "SL"
                    exit_price = sl
                    exit_time = candle_time

                    break

                if (
                    tp1 is not None
                    and candle_low <= tp1
                ):

                    exit_reason = "TP1"
                    exit_price = tp1
                    exit_time = candle_time

                    break

                if (
                    tp2 is not None
                    and candle_low <= tp2
                ):

                    exit_reason = "TP2"
                    exit_price = tp2
                    exit_time = candle_time

                    break

                if (
                    tp3 is not None
                    and candle_low <= tp3
                ):

                    exit_reason = "TP3"
                    exit_price = tp3
                    exit_time = candle_time

                    break

        # ====================================================
        # CLOSE TRADE
        # ====================================================

        if exit_reason is not None:

            if side == "BUY":

                pnl_percent = (
                    (
                        exit_price
                        - entry
                    )
                    / entry
                    * 100
                )

            else:

                pnl_percent = (
                    (
                        entry
                        - exit_price
                    )
                    / entry
                    * 100
                )

            risk = abs(
                entry - sl
            )

            if risk > 0:

                if side == "BUY":

                    r_multiple = (
                        exit_price
                        - entry
                    ) / risk

                else:

                    r_multiple = (
                        entry
                        - exit_price
                    ) / risk

            else:

                r_multiple = 0

            trade["status"] = "CLOSED"

            trade["exit_reason"] = (
                exit_reason
            )

            trade["result_reason"] = (
                exit_reason
            )

            trade["exit_price"] = (
                exit_price
            )

            trade["result_price"] = (
                exit_price
            )

            trade["exit_time"] = (
                exit_time
            )

            trade["closed_at"] = (
                datetime.fromtimestamp(
                    exit_time / 1000,
                    tz=timezone.utc
                ).isoformat()
            )

            trade["pnl_percent"] = (
                pnl_percent
            )

            trade["r_multiple"] = (
                r_multiple
            )

            trade["result_r"] = (
                r_multiple
            )

            duration_minutes = max(
                0,
                (
                    exit_time
                    - signal_time
                ) / 60000
            )

            trade["duration_minutes"] = (
                duration_minutes
            )

            changed = True

    return changed


# ============================================================
# GET NEWLY CLOSED TRADES
# ============================================================

def get_newly_closed_trades(
    state,
    previous_closed_ids
):

    result = []

    for trade in get_all_trades(state):

        status = str(
            trade.get(
                "status",
                ""
            )
        ).upper()

        if status != "CLOSED":
            continue

        trade_id = get_trade_id(
            trade
        )

        if not trade_id:
            continue

        if trade_id not in previous_closed_ids:

            result.append(
                trade
            )

    return result


# ============================================================
# OPEN PERFORMANCE
# ============================================================

def calculate_open_performance(
    state,
    data_cache
):

    result = []

    trades = get_all_trades(
        state
    )

    for trade in trades:

        status = str(
            trade.get(
                "status",
                ""
            )
        ).upper()

        if status != "OPEN":
            continue

        coin = normalize_coin(
            trade
        )

        if not coin:
            continue

        df = data_cache.get(
            coin
        )

        if df is None or df.empty:
            continue

        entry = get_trade_entry(
            trade
        )

        sl = get_trade_sl(
            trade
        )

        side = normalize_side(
            trade
        )

        if (
            entry is None
            or sl is None
            or side not in (
                "BUY",
                "SELL"
            )
        ):

            continue

        current_price = float(
            df["close"].iloc[-1]
        )

        # ====================================================
        # CURRENT PNL
        # ====================================================

        if side == "BUY":

            pnl_percent = (
                (
                    current_price
                    - entry
                )
                / entry
                * 100
            )

        else:

            pnl_percent = (
                (
                    entry
                    - current_price
                )
                / entry
                * 100
            )

        # ====================================================
        # CURRENT R
        # ====================================================

        risk = abs(
            entry - sl
        )

        if risk > 0:

            if side == "BUY":

                current_r = (
                    current_price
                    - entry
                ) / risk

            else:

                current_r = (
                    entry
                    - current_price
                ) / risk

        else:

            current_r = 0

        # ====================================================
        # MFE / MAE
        # ====================================================

        signal_time = parse_trade_time(
            trade.get(
                "signal_time"
            )
        )

        if signal_time is None:

            signal_time = parse_trade_time(
                trade.get(
                    "signal_time_iso"
                )
            )

        if signal_time is None:

            mfe = 0
            mae = 0
            after_signal = pd.DataFrame()

        else:

            after_signal = df[
                df["time"] > signal_time
            ]

            if after_signal.empty:

                mfe = 0
                mae = 0

            else:

                if side == "BUY":

                    best_price = float(
                        after_signal[
                            "high"
                        ].max()
                    )

                    worst_price = float(
                        after_signal[
                            "low"
                        ].min()
                    )

                    mfe = (
                        best_price
                        - entry
                    ) / entry * 100

                    mae = (
                        worst_price
                        - entry
                    ) / entry * 100

                else:

                    best_price = float(
                        after_signal[
                            "low"
                        ].min()
                    )

                    worst_price = float(
                        after_signal[
                            "high"
                        ].max()
                    )

                    mfe = (
                        entry
                        - best_price
                    ) / entry * 100

                    mae = (
                        entry
                        - worst_price
                    ) / entry * 100

        # ====================================================
        # DURATION
        # ====================================================

        if signal_time is None:

            duration_minutes = 0

        else:

            if after_signal.empty:

                last_time = int(
                    df["time"].iloc[-1]
                )

            else:

                last_time = int(
                    after_signal[
                        "time"
                    ].iloc[-1]
                )

            duration_minutes = max(
                0,
                (
                    last_time
                    - signal_time
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
            "pnl_percent": pnl_percent,
            "current_r": current_r,
            "mfe": mfe,
            "mae": mae,
            "duration_minutes": duration_minutes,
        })

    return result


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(
    state
):

    trades = get_all_trades(
        state
    )

    total = len(
        trades
    )

    open_trades = [
        x for x in trades
        if str(
            x.get(
                "status",
                ""
            )
        ).upper() == "OPEN"
    ]

    closed_trades = [
        x for x in trades
        if str(
            x.get(
                "status",
                ""
            )
        ).upper() == "CLOSED"
    ]

    wins = [
        x for x in closed_trades
        if float(
            x.get(
                "pnl_percent",
                0
            )
        ) > 0
    ]

    losses = [
        x for x in closed_trades
        if float(
            x.get(
                "pnl_percent",
                0
            )
        ) <= 0
    ]

    total_pnl = sum(
        float(
            x.get(
                "pnl_percent",
                0
            )
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
        np.mean([
            float(
                x.get(
                    "pnl_percent",
                    0
                )
            )
            for x in wins
        ])
        if wins
        else 0
    )

    avg_loss = (
        np.mean([
            float(
                x.get(
                    "pnl_percent",
                    0
                )
            )
            for x in losses
        ])
        if losses
        else 0
    )

    return {
        "total": total,
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
# FORMAT PRICE
# ============================================================

def format_price(
    price
):

    if price is None:
        return "-"

    price = float(
        price
    )

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
# DIRECTION EMOJI
# ============================================================

def direction_emoji(side):

    side = str(
        side
    ).upper()

    if side == "BUY":
        return "🟢"

    if side == "SELL":
        return "🔴"

    return "⚪"


def result_emoji(reason):

    reason = str(
        reason or ""
    ).upper()

    if reason.startswith("TP"):
        return "✅"

    if reason == "SL":
        return "❌"

    return "⚪"


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(
    symbol
):

    # ========================================================
    # MAIN 5M DATA
    # ========================================================

    df = get_candles(
        symbol,
        "5m"
    )

    # ========================================================
    # MULTI-TIMEFRAME TREND DATA
    # ========================================================

    trend_data = get_multi_timeframe_trend(
        symbol
    )

    trend_15m = trend_data[
        "15m"
    ]["trend"]

    trend_1h = trend_data[
        "1h"
    ]["trend"]

    # ========================================================
    # RSI
    # ========================================================

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    # ========================================================
    # ATR(14) FOR SL
    # ========================================================

    df["atr_sl"] = calculate_atr(
        df,
        SL_ATR_PERIOD
    )

    # ========================================================
    # ATR(10) FOR UT BOT
    # ========================================================

    df["atr"] = calculate_atr(
        df,
        UT_ATR_PERIOD
    )

    # ========================================================
    # UT BOT
    # ========================================================

    df["ut_stop"] = calculate_ut_bot(
        df,
        UT_KEY_VALUE,
        UT_ATR_PERIOD
    )

    # ========================================================
    # DIVERGENCE
    # ========================================================

    bullish_divergence = (
        find_bullish_divergence(df)
    )

    bearish_divergence = (
        find_bearish_divergence(df)
    )

    # ========================================================
    # TRENDLINE
    # ========================================================

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

    signal = None

    # ========================================================
    # BUY
    #
    # Requirements:
    # 1. Bullish RSI divergence
    # 2. UT or trendline bullish confirmation
    # 3. 15M bullish
    # 4. 1H bullish
    # ========================================================

    if (
        bullish_divergence is not None
        and (
            bullish_break
            or current_close > current_ut
        )
        and trend_15m == "BULLISH"
        and trend_1h == "BULLISH"
    ):

        support = nearest_support(
            df,
            current_close
        )

        sl_tp = build_sl_tp(
            side="BUY",
            entry=current_close,
            atr=current_atr_sl,
            swing_level=support
        )

        if sl_tp is None:

            return {
                "symbol": symbol,
                "df": df,
                "signal": None,
                "trend_data": trend_data,
                "market_symbol": get_market_symbol(
                    symbol
                ),
            }

        sl = sl_tp["sl"]
        tp1 = sl_tp["tp1"]
        tp2 = sl_tp["tp2"]
        tp3 = sl_tp["tp3"]

        signal = {
            "symbol": symbol,
            "side": "BUY",
            "direction": "BUY",
            "name": symbol,
            "entry": current_close,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "tp": tp1,
            "sl_percent": level_percent(
                "BUY",
                current_close,
                sl
            ),
            "tp1_percent": level_percent(
                "BUY",
                current_close,
                tp1
            ),
            "tp2_percent": level_percent(
                "BUY",
                current_close,
                tp2
            ),
            "tp3_percent": level_percent(
                "BUY",
                current_close,
                tp3
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

    # ========================================================
    # SELL
    #
    # Requirements:
    # 1. Bearish RSI divergence
    # 2. UT or trendline bearish confirmation
    # 3. 15M bearish
    # 4. 1H bearish
    # ========================================================

    elif (
        bearish_divergence is not None
        and (
            bearish_break
            or current_close < current_ut
        )
        and trend_15m == "BEARISH"
        and trend_1h == "BEARISH"
    ):

        resistance = nearest_resistance(
            df,
            current_close
        )

        sl_tp = build_sl_tp(
            side="SELL",
            entry=current_close,
            atr=current_atr_sl,
            swing_level=resistance
        )

        if sl_tp is None:

            return {
                "symbol": symbol,
                "df": df,
                "signal": None,
                "trend_data": trend_data,
                "market_symbol": get_market_symbol(
                    symbol
                ),
            }

        sl = sl_tp["sl"]
        tp1 = sl_tp["tp1"]
        tp2 = sl_tp["tp2"]
        tp3 = sl_tp["tp3"]

        signal = {
            "symbol": symbol,
            "side": "SELL",
            "direction": "SELL",
            "name": symbol,
            "entry": current_close,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "tp": tp1,
            "sl_percent": level_percent(
                "SELL",
                current_close,
                sl
            ),
            "tp1_percent": level_percent(
                "SELL",
                current_close,
                tp1
            ),
            "tp2_percent": level_percent(
                "SELL",
                current_close,
                tp2
            ),
            "tp3_percent": level_percent(
                "SELL",
                current_close,
                tp3
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

    return {
        "symbol": symbol,
        "df": df,
        "signal": signal,
        "trend_data": trend_data,
        "market_symbol": get_market_symbol(
            symbol
        ),
    }


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal
):

    side = str(
        signal["side"]
    ).upper()

    emoji = direction_emoji(
        side
    )

    entry = float(
        signal["entry"]
    )

    sl = float(
        signal["sl"]
    )

    tp1 = float(
        signal["tp1"]
    )

    tp2 = float(
        signal["tp2"]
    )

    tp3 = float(
        signal["tp3"]
    )

    sl_percent = level_percent(
        side,
        entry,
        sl
    )

    tp1_percent = level_percent(
        side,
        entry,
        tp1
    )

    tp2_percent = level_percent(
        side,
        entry,
        tp2
    )

    tp3_percent = level_percent(
        side,
        entry,
        tp3
    )

    text = []

    text.append(
        f"{emoji} {signal['symbol']}/USDT - {side}"
    )

    text.append(
        f"Entry: {format_price(entry)}"
    )

    text.append(
        f"Stop Loss: {format_price(sl)} ({sl_percent:+.2f}%)"
    )

    text.append(
        f"Target 1: {format_price(tp1)} ({tp1_percent:+.2f}%)"
    )

    text.append(
        f"Target 2: {format_price(tp2)} ({tp2_percent:+.2f}%)"
    )

    text.append(
        f"Target 3: {format_price(tp3)} ({tp3_percent:+.2f}%)"
    )

    text.append(
        f"Risk: {abs(sl_percent):.2f}%"
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

    return "\n".join(
        text
    )


# ============================================================
# FORMAT CLOSED SIGNAL
# ============================================================

def format_closed_signal(
    trade
):

    side = normalize_side(
        trade
    )

    coin = normalize_coin(
        trade
    )

    direction_icon = direction_emoji(
        side
    )

    reason = trade.get(
        "result_reason"
    )

    if not reason:

        reason = trade.get(
            "exit_reason"
        )

    result_icon = result_emoji(
        reason
    )

    entry = get_trade_entry(
        trade
    )

    exit_price = trade.get(
        "result_price"
    )

    if exit_price is None:

        exit_price = trade.get(
            "exit_price"
        )

    try:

        exit_price = float(
            exit_price
        )

    except Exception:

        exit_price = None

    try:

        pnl = float(
            trade.get(
                "pnl_percent",
                0
            )
        )

    except Exception:

        pnl = 0

    try:

        r_multiple = float(
            trade.get(
                "result_r",
                trade.get(
                    "r_multiple",
                    0
                )
            )
        )

    except Exception:

        r_multiple = 0

    duration = trade.get(
        "duration_minutes"
    )

    if duration is None:

        signal_time = parse_trade_time(
            trade.get(
                "signal_time"
            )
        )

        exit_time = parse_trade_time(
            trade.get(
                "exit_time"
            )
        )

        if (
            signal_time is not None
            and exit_time is not None
        ):

            duration = max(
                0,
                (
                    exit_time
                    - signal_time
                ) / 60000
            )

        else:

            duration = 0

    text = []

    text.append(
        f"{direction_icon} {coin} {side} | "
        f"{result_icon} {reason or 'UNKNOWN'}"
    )

    text.append(
        f"Entry: {format_price(entry)}"
    )

    text.append(
        f"Exit: {format_price(exit_price)}"
    )

    text.append(
        f"P&L: {pnl:+.2f}%"
    )

    text.append(
        f"R: {r_multiple:+.2f}R"
    )

    text.append(
        f"Duration: {float(duration):.0f} min"
    )

    return "\n".join(
        text
    )


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    state,
    results,
    errors,
    open_performance,
    closed_this_run,
    blocked_symbols=None
):

    if blocked_symbols is None:
        blocked_symbols = []

    stats = calculate_statistics(
        state
    )

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

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # CUMULATIVE PERFORMANCE
    # ========================================================

    lines.append(
        "📊 CUMULATIVE PERFORMANCE"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📈 Total Trades: {stats['total']}"
    )

    lines.append(
        f"🟡 Open: {stats['open']}"
    )

    lines.append(
        f"⚫ Closed: {stats['closed']}"
    )

    lines.append(
        f"🟢 Wins: {stats['wins']}"
    )

    lines.append(
        f"🔴 Losses: {stats['losses']}"
    )

    lines.append(
        f"🎯 Win Rate: {stats['win_rate']:.2f}%"
    )

    lines.append(
        f"💰 Closed P&L: {stats['total_pnl']:.2f}%"
    )

    lines.append(
        f"📈 Avg Win: {stats['avg_win']:.2f}%"
    )

    lines.append(
        f"📉 Avg Loss: {stats['avg_loss']:.2f}%"
    )

    # ========================================================
    # CLOSED SIGNALS
    # ========================================================

    lines.append("")

    lines.append(
        "🏁 CLOSED SIGNALS"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if closed_this_run:

        for trade in closed_this_run:

            lines.append(
                format_closed_signal(
                    trade
                )
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

    new_signals = [
        x["signal"]
        for x in results
        if x.get("signal") is not None
        and x["signal"]["symbol"]
        not in blocked_symbols
    ]

    lines.append("")

    lines.append(
        "🚨 NEW SIGNALS"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if new_signals:

        for signal in new_signals:

            lines.append(
                format_signal(
                    signal
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            "فعلاً سیگنال جدیدی وجود ندارد."
        )

    # ========================================================
    # OPEN SIGNAL PERFORMANCE
    # ========================================================

    lines.append("")

    lines.append(
        "📌 OPEN SIGNAL P&L"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if open_performance:

        for item in open_performance:

            direction_icon = direction_emoji(
                item["side"]
            )

            side = item["side"]
            entry = item["entry"]
            sl = item["sl"]

            pnl = item[
                "pnl_percent"
            ]

            current_r = item[
                "current_r"
            ]

            mfe = item[
                "mfe"
            ]

            mae = item[
                "mae"
            ]

            sl_percent = level_percent(
                side,
                entry,
                sl
            )

            lines.append(
                f"{direction_icon} "
                f"{item['symbol']} {side}"
            )

            lines.append(
                f"Entry: {format_price(entry)}"
            )

            lines.append(
                f"Current: {format_price(item['current_price'])}"
            )

            lines.append(
                f"SL: {format_price(sl)} "
                f"({sl_percent:+.2f}%)"
            )

            if item.get("tp1") is not None:

                tp1_percent = level_percent(
                    side,
                    entry,
                    item["tp1"]
                )

                lines.append(
                    f"TP1: {format_price(item['tp1'])} "
                    f"({tp1_percent:+.2f}%)"
                )

            if item.get("tp2") is not None:

                tp2_percent = level_percent(
                    side,
                    entry,
                    item["tp2"]
                )

                lines.append(
                    f"TP2: {format_price(item['tp2'])} "
                    f"({tp2_percent:+.2f}%)"
                )

            if item.get("tp3") is not None:

                tp3_percent = level_percent(
                    side,
                    entry,
                    item["tp3"]
                )

                lines.append(
                    f"TP3: {format_price(item['tp3'])} "
                    f"({tp3_percent:+.2f}%)"
                )

            # =================================================
            # CURRENT P&L
            #
            # Bold in Telegram.
            # No emoji.
            # =================================================

            lines.append(
                f"Current P&L: *{pnl:+.2f}%*"
            )

            lines.append(
                f"Current R: {current_r:+.2f}R"
            )

            lines.append(
                f"MFE: {mfe:+.2f}%"
            )

            lines.append(
                f"MAE: {mae:+.2f}%"
            )

            lines.append(
                f"Duration: {item['duration_minutes']:.0f} min"
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

        lines.append(
            "⚠️ ERRORS"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for symbol, error in errors.items():

            lines.append(
                f"• {symbol}: {error}"
            )

    return "\n".join(
        lines
    )


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
    # LOAD v10.4 STATE
    #
    # This is a NEW state file.
    # Therefore old v10.3 statistics are not included.
    # ========================================================

    state = load_state()

    rebuild_trade_container(
        state
    )

    # ========================================================
    # REMEMBER PREVIOUS CLOSED IDS
    # ========================================================

    previous_closed_ids = set()

    for trade in get_all_trades(state):

        status = str(
            trade.get(
                "status",
                ""
            )
        ).upper()

        if status == "CLOSED":

            trade_id = get_trade_id(
                trade
            )

            if trade_id:

                previous_closed_ids.add(
                    trade_id
                )

    results = []

    errors = {}

    data_cache = {}

    blocked_symbols = []

    # ========================================================
    # FIRST PASS
    # ========================================================

    for symbol in COINS:

        try:

            result = analyze_coin(
                symbol
            )

            results.append(
                result
            )

            data_cache[symbol] = (
                result["df"]
            )

        except Exception as e:

            errors[symbol] = str(e)

        time.sleep(
            0.05
        )

    # ========================================================
    # CLOSE OLD OPEN TRADES FIRST
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache
    )

    if changed:

        save_state(
            state
        )

    # ========================================================
    # REGISTER NEW SIGNALS
    # ========================================================

    new_registered = []

    for result in results:

        signal = result.get(
            "signal"
        )

        if signal is None:
            continue

        symbol = signal["symbol"]

        # ----------------------------------------------------
        # HARD SYMBOL LOCK
        #
        # If symbol already has an OPEN trade:
        # BUY = blocked
        # SELL = blocked
        #
        # This prevents both duplicate and hedge.
        # ----------------------------------------------------

        if (
            not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
            and has_open_trade_for_symbol(
                state,
                symbol
            )
        ):

            blocked_symbols.append(
                symbol
            )

            continue

        signal_id = make_signal_id(
            signal["symbol"],
            signal["side"],
            signal["signal_time"],
            signal["entry"]
        )

        signal["signal_id"] = (
            signal_id
        )

        signal["id"] = (
            signal_id
        )

        trade = dict(
            signal
        )

        trade["status"] = "OPEN"

        if register_signal(
            state,
            trade
        ):

            new_registered.append(
                signal
            )

    # ========================================================
    # SAVE
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # SECOND EVALUATION
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache
    )

    if changed:

        save_state(
            state
        )

    # ========================================================
    # FIND CLOSED SIGNALS FROM THIS RUN
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
        blocked_symbols
    )

    print()

    print(
        report
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_telegram(
        report
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
