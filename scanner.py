# ============================================================
# CRYPTO DIVERGENCE SCANNER v10.7 SCORE
# ============================================================
# Kraken Futures
# Closed 5m Candles
# RSI Divergence
# Trendline Breakout / Breakdown
# TradingView-style UT Bot
# 15M + 1H Trend Filter
# Candidate Scoring /100
# Automatic Futures Symbol Discovery
# Trade History
# Open Signal P&L
# P&L / R / MFE / MAE / Duration
# Closed Signals Report
# ATR Based SL
# R Based TP
# Telegram
#
# v10.7 CHANGES
# ------------------------------------------------------------
# - Candidate Score /100
# - RSI Divergence       +30
# - UT Bot Trigger       +20
# - Trendline            +15
# - 15M Trend            +15
# - 1H Trend             +15
# - Volume Confirmation   +5
#
# - Candidate display threshold = 65
# - Final signal threshold = 75
#
# - Candidates sorted by highest SCORE
# - Candidate NAME + SCORE clearly displayed
# - BUY / SELL candidate shown separately
# - Final signal selected by highest score
# - No opposite-direction hedge
# - One OPEN trade per symbol
# - Existing OPEN trade blocks new signal on same symbol
#
# - Closed 5m candles only
# - ATR SL
# - R based TP1 / TP2 / TP3
# - Trade history
# - Open P&L / R / MFE / MAE / Duration
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
# STATE
# ============================================================

STATE_FILE = "trade_history_v10.7.json"


# ============================================================
# ONE OPEN TRADE PER SYMBOL
# ============================================================

ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False


# ============================================================
# NETWORK
# ============================================================

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
# VOLUME
# ============================================================

VOLUME_LOOKBACK = 20
VOLUME_CONFIRMATION_RATIO = 1.20


# ============================================================
# SCORE
# ============================================================

RSI_DIVERGENCE_SCORE = 30
UT_TRIGGER_SCORE = 20
TRENDLINE_SCORE = 15
TREND_15M_SCORE = 15
TREND_1H_SCORE = 15
VOLUME_SCORE = 5

MIN_DISPLAY_CANDIDATE_SCORE = 65
MIN_SIGNAL_SCORE = 75


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
UT_USE_HEIKIN_ASHI = False


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
    "User-Agent": "CryptoDivergenceScanner/10.7-SCORE",
    "Accept": "application/json",
})


# ============================================================
# MARKET MAP
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
        "version": 4,
        "scanner_version": "10.7-SCORE",
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
        return float(
            trade.get("tp2")
        )

    except Exception:
        return None


def get_trade_tp3(trade):

    try:
        return float(
            trade.get("tp3")
        )

    except Exception:
        return None


def get_trade_entry(trade):

    try:
        return float(
            trade.get("entry")
        )

    except Exception:
        return None


def get_trade_sl(trade):

    try:
        return float(
            trade.get("sl")
        )

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

    trades = state.get(
        "trades",
        {}
    )

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

    # --------------------------------------------------------
    # REMOVE CURRENT UNFINISHED CANDLE
    # --------------------------------------------------------

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
# TRENDLINE BREAKOUT
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

    y1 = float(
        highs.iloc[p1]
    )

    y2 = float(
        highs.iloc[p2]
    )

    if y2 >= y1:
        return False

    if p2 == p1:
        return False

    slope = (
        y2 - y1
    ) / (
        p2 - p1
    )

    current_x = len(df) - 1

    trendline = (
        y1
        + slope * (
            current_x - p1
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


# ============================================================
# TRENDLINE BREAKDOWN
# ============================================================

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

    y1 = float(
        lows.iloc[p1]
    )

    y2 = float(
        lows.iloc[p2]
    )

    if y2 <= y1:
        return False

    if p2 == p1:
        return False

    slope = (
        y2 - y1
    ) / (
        p2 - p1
    )

    current_x = len(df) - 1

    trendline = (
        y1
        + slope * (
            current_x - p1
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
# ATR - WILDER RMA
# ============================================================

def calculate_atr(
    df,
    period=14
):

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

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

    tr_values = tr.to_numpy(
        dtype=float
    )

    atr_values = np.full(
        len(df),
        np.nan,
        dtype=float
    )

    if len(df) < period:
        return pd.Series(
            atr_values,
            index=df.index
        )

    first_atr = np.nanmean(
        tr_values[:period]
    )

    atr_values[period - 1] = first_atr

    for i in range(
        period,
        len(df)
    ):

        previous_atr = (
            atr_values[i - 1]
        )

        current_tr = (
            tr_values[i]
        )

        if np.isnan(previous_atr):

            atr_values[i] = current_tr

        else:

            atr_values[i] = (
                (
                    previous_atr
                    * (period - 1)
                )
                + current_tr
            ) / period

    return pd.Series(
        atr_values,
        index=df.index
    )


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(
    df,
    key_value=3.0,
    atr_period=10
):

    src = df["close"].astype(float)

    atr = calculate_atr(
        df,
        atr_period
    )

    nloss = (
        key_value * atr
    )

    trailing_stop = np.full(
        len(df),
        np.nan,
        dtype=float
    )

    buy_signal = np.zeros(
        len(df),
        dtype=bool
    )

    sell_signal = np.zeros(
        len(df),
        dtype=bool
    )

    if len(df) == 0:

        return {
            "stop": pd.Series(
                dtype=float,
                index=df.index
            ),
            "buy": pd.Series(
                dtype=bool,
                index=df.index
            ),
            "sell": pd.Series(
                dtype=bool,
                index=df.index
            ),
        }

    src_values = src.to_numpy(
        dtype=float
    )

    loss_values = nloss.to_numpy(
        dtype=float
    )

    valid_indexes = np.where(
        np.isfinite(loss_values)
    )[0]

    if len(valid_indexes) == 0:

        return {
            "stop": pd.Series(
                trailing_stop,
                index=df.index
            ),
            "buy": pd.Series(
                buy_signal,
                index=df.index
            ),
            "sell": pd.Series(
                sell_signal,
                index=df.index
            ),
        }

    first_valid = int(
        valid_indexes[0]
    )

    trailing_stop[first_valid] = (
        src_values[first_valid]
        - loss_values[first_valid]
    )

    for i in range(
        first_valid + 1,
        len(df)
    ):

        current_src = (
            src_values[i]
        )

        previous_src = (
            src_values[i - 1]
        )

        current_loss = (
            loss_values[i]
        )

        previous_stop = (
            trailing_stop[i - 1]
        )

        if not np.isfinite(current_loss):

            trailing_stop[i] = (
                previous_stop
            )

            continue

        if not np.isfinite(previous_stop):

            previous_stop = 0.0

        if (
            current_src > previous_stop
            and previous_src > previous_stop
        ):

            trailing_stop[i] = max(
                previous_stop,
                current_src - current_loss
            )

        elif (
            current_src < previous_stop
            and previous_src < previous_stop
        ):

            trailing_stop[i] = min(
                previous_stop,
                current_src + current_loss
            )

        elif current_src > previous_stop:

            trailing_stop[i] = (
                current_src
                - current_loss
            )

        else:

            trailing_stop[i] = (
                current_src
                + current_loss
            )

    for i in range(
        first_valid + 1,
        len(df)
    ):

        current_src = (
            src_values[i]
        )

        previous_src = (
            src_values[i - 1]
        )

        current_stop = (
            trailing_stop[i]
        )

        previous_stop = (
            trailing_stop[i - 1]
        )

        if not (
            np.isfinite(
                current_stop
            )
            and np.isfinite(
                previous_stop
            )
        ):

            continue

        above = (
            current_src > current_stop
            and previous_src <= previous_stop
        )

        below = (
            current_src < current_stop
            and previous_src >= previous_stop
        )

        buy_signal[i] = (
            current_src > current_stop
            and above
        )

        sell_signal[i] = (
            current_src < current_stop
            and below
        )

    return {
        "stop": pd.Series(
            trailing_stop,
            index=df.index
        ),
        "buy": pd.Series(
            buy_signal,
            index=df.index
        ),
        "sell": pd.Series(
            sell_signal,
            index=df.index
        ),
    }


# ============================================================
# VOLUME
# ============================================================

def calculate_volume_ratio(
    df,
    lookback=20
):

    if len(df) <= lookback:
        return 0.0

    current_volume = float(
        df["volume"].iloc[-1]
    )

    previous_volumes = (
        df["volume"]
        .iloc[-lookback-1:-1]
    )

    average_volume = float(
        previous_volumes.mean()
    )

    if (
        not np.isfinite(
            average_volume
        )
        or average_volume <= 0
    ):

        return 0.0

    return (
        current_volume
        / average_volume
    )


# ============================================================
# SCORE LABEL
# ============================================================

def score_label(score):

    if score >= 85:
        return "🔥 STRONG"

    if score >= 75:
        return "🟢 GOOD"

    if score >= 65:
        return "🟡 WATCH"

    return "⚪ WEAK"


# ============================================================
# SCORE
# ============================================================

def calculate_candidate_score(
    side,
    divergence,
    ut_trigger,
    trendline,
    trend_15m,
    trend_1h,
    volume_ratio
):

    score = 0

    components = {
        "divergence": 0,
        "ut": 0,
        "trendline": 0,
        "trend_15m": 0,
        "trend_1h": 0,
        "volume": 0,
    }

    expected_trend = (
        "BULLISH"
        if side == "BUY"
        else "BEARISH"
    )

    # RSI Divergence
    if divergence:

        score += RSI_DIVERGENCE_SCORE

        components["divergence"] = (
            RSI_DIVERGENCE_SCORE
        )

    # UT Bot
    if ut_trigger:

        score += UT_TRIGGER_SCORE

        components["ut"] = (
            UT_TRIGGER_SCORE
        )

    # Trendline
    if trendline:

        score += TRENDLINE_SCORE

        components["trendline"] = (
            TRENDLINE_SCORE
        )

    # 15M
    if trend_15m == expected_trend:

        score += TREND_15M_SCORE

        components["trend_15m"] = (
            TREND_15M_SCORE
        )

    # 1H
    if trend_1h == expected_trend:

        score += TREND_1H_SCORE

        components["trend_1h"] = (
            TREND_1H_SCORE
        )

    # Volume
    if (
        volume_ratio
        >= VOLUME_CONFIRMATION_RATIO
    ):

        score += VOLUME_SCORE

        components["volume"] = (
            VOLUME_SCORE
        )

    return {
        "score": score,
        "components": components,
        "label": score_label(score),
        "volume_ratio": volume_ratio,
    }


# ============================================================
# SUPPORT
# ============================================================

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
# RESISTANCE
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

    if (
        atr is None
        or not np.isfinite(atr)
        or atr <= 0
    ):

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

    if (
        risk_percent
        > MAX_SL_DISTANCE_PERCENT
    ):

        return None

    if side == "BUY":

        sl = (
            entry
            - risk_distance
        )

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

        sl = (
            entry
            + risk_distance
        )

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
# LEVEL PERCENT
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


# ============================================================
# FORMAT PRICE
# ============================================================

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

    symbol = str(
        symbol
    ).upper()

    for trade in get_all_trades(state):

        if str(
            trade.get(
                "status",
                ""
            )
        ).upper() != "OPEN":

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

    trades = get_all_trades(
        state
    )

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:

        for trade in trades:

            if (
                normalize_coin(trade)
                == symbol
                and str(
                    trade.get(
                        "status",
                        ""
                    )
                ).upper()
                == "OPEN"
            ):

                return False

    signal_id = signal.get(
        "signal_id"
    )

    if not signal_id:
        return False

    for trade in trades:

        if (
            get_trade_id(trade)
            == signal_id
        ):

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
# CREATE SIGNAL
# ============================================================

def create_signal(
    symbol,
    side,
    entry,
    sl_tp,
    signal_time,
    trend_15m,
    trend_1h,
    ut_trigger,
    trendline_break,
    volume_ratio,
    score_data,
    atr
):

    return {

        "symbol": symbol,

        "side": side,

        "entry": entry,

        "sl": sl_tp["sl"],

        "tp1": sl_tp["tp1"],

        "tp2": sl_tp["tp2"],

        "tp3": sl_tp["tp3"],

        "tp": sl_tp["tp1"],

        "sl_percent": level_percent(
            side,
            entry,
            sl_tp["sl"]
        ),

        "tp1_percent": level_percent(
            side,
            entry,
            sl_tp["tp1"]
        ),

        "tp2_percent": level_percent(
            side,
            entry,
            sl_tp["tp2"]
        ),

        "tp3_percent": level_percent(
            side,
            entry,
            sl_tp["tp3"]
        ),

        "risk_percent":
            sl_tp["risk_percent"],

        "atr": atr,

        "atr_multiplier":
            SL_ATR_MULTIPLIER,

        "signal_time":
            signal_time,

        "signal_time_iso":
            datetime.fromtimestamp(
                signal_time / 1000,
                tz=timezone.utc
            ).isoformat(),

        "trend_15m":
            trend_15m,

        "trend_1h":
            trend_1h,

        "ut_trigger":
            ut_trigger,

        "trendline_break":
            trendline_break,

        "volume_ratio":
            volume_ratio,

        "score":
            score_data["score"],

        "score_label":
            score_data["label"],

        "score_components":
            score_data["components"],

        "reason":
            (
                "RSI Divergence + "
                "UT/Trendline + "
                "15M/1H Trend"
            ),
    }


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(symbol):

    df = get_candles(
        symbol,
        "5m"
    )

    trend_data = get_multi_timeframe_trend(
        symbol
    )

    trend_15m = (
        trend_data["15m"]["trend"]
    )

    trend_1h = (
        trend_data["1h"]["trend"]
    )

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # UT BOT
    # --------------------------------------------------------

    ut_data = calculate_ut_bot(
        df,
        UT_KEY_VALUE,
        UT_ATR_PERIOD
    )

    df["ut_stop"] = (
        ut_data["stop"]
    )

    df["ut_buy_signal"] = (
        ut_data["buy"]
    )

    df["ut_sell_signal"] = (
        ut_data["sell"]
    )

    # --------------------------------------------------------
    # DIVERGENCE
    # --------------------------------------------------------

    bullish_divergence = (
        find_bullish_divergence(df)
    )

    bearish_divergence = (
        find_bearish_divergence(df)
    )

    # --------------------------------------------------------
    # TRENDLINE
    # --------------------------------------------------------

    bullish_break = (
        descending_trendline_break(df)
    )

    bearish_break = (
        ascending_trendline_break(df)
    )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    volume_ratio = calculate_volume_ratio(
        df,
        VOLUME_LOOKBACK
    )

    volume_confirmed = (
        volume_ratio
        >= VOLUME_CONFIRMATION_RATIO
    )

    # --------------------------------------------------------
    # CURRENT VALUES
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # UT TRIGGERS
    # --------------------------------------------------------

    ut_buy = bool(
        df["ut_buy_signal"].iloc[-1]
    )

    ut_sell = bool(
        df["ut_sell_signal"].iloc[-1]
    )

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    bullish_confirmation = (
        bullish_break
        or ut_buy
    )

    bearish_confirmation = (
        bearish_break
        or ut_sell
    )

    # --------------------------------------------------------
    # TREND FILTER
    # --------------------------------------------------------

    buy_trend_ok = (
        trend_15m == "BULLISH"
        and trend_1h == "BULLISH"
    )

    sell_trend_ok = (
        trend_15m == "BEARISH"
        and trend_1h == "BEARISH"
    )

    # ========================================================
    # DIAGNOSTIC
    # ========================================================

    diagnostic = {

        "symbol": symbol,

        "bullish_divergence":
            bullish_divergence is not None,

        "bearish_divergence":
            bearish_divergence is not None,

        "bullish_break":
            bullish_break,

        "bearish_break":
            bearish_break,

        "ut_buy":
            ut_buy,

        "ut_sell":
            ut_sell,

        "bullish_confirmation":
            bullish_confirmation,

        "bearish_confirmation":
            bearish_confirmation,

        "buy_trend_ok":
            buy_trend_ok,

        "sell_trend_ok":
            sell_trend_ok,

        "trend_15m":
            trend_15m,

        "trend_1h":
            trend_1h,

        "current_rsi":
            current_rsi,

        "ut_stop":
            current_ut,

        "volume_ratio":
            volume_ratio,

        "volume_confirmed":
            volume_confirmed,

        "candidate_sides":
            [],

        "candidate_details":
            [],

        "rejections":
            [],

        "is_setup_candidate":
            False,
    }

    # ========================================================
    # CANDIDATES
    # ========================================================

    final_candidates = []

    # ========================================================
    # BUY
    # ========================================================

    if (
        bullish_divergence is not None
        and bullish_confirmation
    ):

        score_data = calculate_candidate_score(
            side="BUY",
            divergence=True,
            ut_trigger=ut_buy,
            trendline=bullish_break,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            volume_ratio=volume_ratio
        )

        candidate_info = {

            "side": "BUY",

            "divergence": True,

            "ut": ut_buy,

            "trendline":
                bullish_break,

            "trend_15m":
                trend_15m,

            "trend_1h":
                trend_1h,

            "volume_ratio":
                volume_ratio,

            "volume_confirmed":
                volume_confirmed,

            "score":
                score_data["score"],

            "components":
                score_data["components"],

            "label":
                score_data["label"],

            "trend_ok":
                buy_trend_ok,

            "sl_ok":
                None,

            "final_ready":
                False,

            "rejection":
                None,

            "entry":
                current_close,

            "sl":
                None,

            "tp1":
                None,

            "tp2":
                None,

            "tp3":
                None,
        }

        # ----------------------------------------------------
        # TREND FILTER
        # ----------------------------------------------------

        if not buy_trend_ok:

            candidate_info[
                "rejection"
            ] = "TREND_FILTER"

        else:

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

                candidate_info[
                    "sl_ok"
                ] =
