# ============================================================
# CRYPTO DIVERGENCE SCANNER v10.9 SCORE
# Kraken Futures | Closed 5m Candles
# RSI Divergence | Trendline Breakout/Breakdown
# TradingView-style UT Bot | 15M + 1H Trend Filter
# Candidate Scoring /100 | Global Best Signal
# One Open Trade Per Symbol | NO HEDGE
# Partial TP1/TP2/TP3 | Dynamic SL Management
# Trade History | P&L / R / MFE / MAE / Duration
# Telegram Long Message Support
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

STATE_FILE = "trade_history_v10.9.json"

ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False

REQUEST_TIMEOUT = 20

TREND_FAST_EMA = 20
TREND_SLOW_EMA = 50

TREND_TIMEFRAMES = ["15m", "1h"]

RSI_PERIOD = 14

PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MAX_PIVOT_GAP = 60

MIN_RSI_DIFFERENCE = 2.0
MIN_PRICE_DIFFERENCE_PERCENT = 0.10

VOLUME_LOOKBACK = 20
VOLUME_CONFIRMATION_RATIO = 1.20

RSI_DIVERGENCE_SCORE = 30
UT_TRIGGER_SCORE = 20
TRENDLINE_SCORE = 15
TREND_15M_SCORE = 15
TREND_1H_SCORE = 15
VOLUME_SCORE = 5

MIN_DISPLAY_CANDIDATE_SCORE = 65
MIN_SIGNAL_SCORE = 75

SL_ATR_PERIOD = 14
SL_ATR_MULTIPLIER = 1.5
SL_BUFFER_PERCENT = 0.10

MIN_SL_DISTANCE_PERCENT = 0.35
MAX_SL_DISTANCE_PERCENT = 2.00

TP1_R_MULTIPLE = 1.5
TP2_R_MULTIPLE = 2.5
TP3_R_MULTIPLE = 3.5

TP1_CLOSE_PERCENT = 33
TP2_CLOSE_PERCENT = 33
TP3_CLOSE_PERCENT = 34

UT_KEY_VALUE = 3.0
UT_ATR_PERIOD = 10
UT_USE_HEIKIN_ASHI = False

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "CryptoDivergenceScanner/10.9-SCORE",
    "Accept": "application/json",
})

MARKET_MAP = {}


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("TELEGRAM: BOT_TOKEN or CHAT_ID is missing.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    max_length = 3900

    chunks = []

    while len(message) > max_length:
        cut = message.rfind("\n", 0, max_length)

        if cut < 500:
            cut = max_length

        chunks.append(message[:cut])
        message = message[cut:].lstrip("\n")

    if message:
        chunks.append(message)

    success = True

    for index, chunk in enumerate(chunks, start=1):
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "disable_web_page_preview": True,
        }

        try:
            response = SESSION.post(
                url,
                json=payload,
                timeout=REQUEST_TIMEOUT,
            )

            if not response.ok:
                success = False
                print(
                    f"TELEGRAM ERROR {response.status_code}: "
                    f"{response.text[:1000]}"
                )
            else:
                print(
                    f"TELEGRAM OK: "
                    f"{index}/{len(chunks)}"
                )

        except Exception as e:
            success = False
            print(f"TELEGRAM EXCEPTION: {e}")

    return success


# ============================================================
# STATE
# ============================================================

def default_state():
    return {
        "version": 5,
        "scanner_version": "10.9-SCORE",
        "trades": {},
        "last_run": None,
    }


def load_state():
    if not os.path.exists(STATE_FILE):
        return default_state()

    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            return default_state()

        if "trades" not in data:
            data["trades"] = {}

        if not isinstance(data["trades"], (dict, list)):
            data["trades"] = {}

        return data

    except Exception as e:
        print(f"WARNING: Could not load state: {e}")
        return default_state()


def save_state(state):
    temp_file = STATE_FILE + ".tmp"

    state["last_run"] = datetime.now(
        timezone.utc
    ).isoformat()

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2,
        )

    os.replace(temp_file, STATE_FILE)


# ============================================================
# TRADE HELPERS
# ============================================================

def get_all_trades(state):
    trades = state.get("trades", {})

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
    side = trade.get("side") or trade.get("direction")

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
            dt = dt.replace(tzinfo=timezone.utc)

        return int(dt.timestamp() * 1000)

    except Exception:
        return None


def get_trade_tp1(trade):
    try:
        value = trade.get("tp1")

        if value is None:
            value = trade.get("tp")

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
                        default=str,
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
    entry,
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
            timeout=REQUEST_TIMEOUT,
        )

        response.raise_for_status()

        data = response.json()

        instruments = data.get(
            "instruments",
            [],
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
                False,
            )

            expired = item.get(
                "isExpired",
                False,
            )

            if not symbol or not tradeable or expired:
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

            candidate = (score, symbol)

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

def get_candles(symbol, timeframe="5m"):
    futures_symbol = get_market_symbol(symbol)

    url = (
        f"{BASE_URL}/trade/"
        f"{futures_symbol}/"
        f"{timeframe}"
    )

    response = SESSION.get(
        url,
        params={"count": CANDLE_LIMIT},
        timeout=REQUEST_TIMEOUT,
    )

    if not response.ok:
        raise RuntimeError(
            f"HTTP {response.status_code} | "
            f"{futures_symbol} | "
            f"{response.text[:250]}"
        )

    data = response.json()

    candles = data.get("candles", [])

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

    df = (
        df.drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    timeframe_minutes = {
        "5m": 5,
        "15m": 15,
        "1h": 60,
    }

    minutes = timeframe_minutes.get(
        timeframe,
        5,
    )

    candle_ms = minutes * 60 * 1000

    now_ms = int(time.time() * 1000)

    if len(df) > 0:
        last_time = int(df.iloc[-1]["time"])

        if last_time + candle_ms > now_ms:
            df = df.iloc[:-1].copy()

    if len(df) < 50:
        raise RuntimeError(
            f"Not enough closed candles for "
            f"{symbol} {timeframe}"
        )

    return df


# ============================================================
# EMA / TREND
# ============================================================

def calculate_ema(series, period):
    return series.ewm(
        span=period,
        adjust=False,
    ).mean()


def determine_trend(df):
    if df is None or df.empty:
        return "NEUTRAL"

    if len(df) < TREND_SLOW_EMA + 5:
        return "NEUTRAL"

    close = df["close"]

    ema_fast = calculate_ema(
        close,
        TREND_FAST_EMA,
    )

    ema_slow = calculate_ema(
        close,
        TREND_SLOW_EMA,
    )

    current_close = float(close.iloc[-1])
    current_fast = float(ema_fast.iloc[-1])
    current_slow = float(ema_slow.iloc[-1])

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
        df = get_candles(symbol, timeframe)

        trend_data[timeframe] = {
            "trend": determine_trend(df),
            "df": df,
        }

    return trend_data


# ============================================================
# RSI
# ============================================================

def calculate_rsi(series, period=14):
    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
    ).mean()

    rs = avg_gain / avg_loss.replace(
        0,
        np.nan,
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi.fillna(50)


# ============================================================
# PIVOTS
# ============================================================

def pivot_lows(series, left=2, right=2):
    result = np.zeros(
        len(series),
        dtype=bool,
    )

    values = series.values

    for i in range(
        left,
        len(values) - right,
    ):
        window = values[
            i - left:i + right + 1
        ]

        if (
            values[i] == np.min(window)
            and np.sum(window == values[i]) == 1
        ):
            result[i] = True

    return result


def pivot_highs(series, left=2, right=2):
    result = np.zeros(
        len(series),
        dtype=bool,
    )

    values = series.values

    for i in range(
        left,
        len(values) - right,
    ):
        window = values[
            i - left:i + right + 1
        ]

        if (
            values[i] == np.max(window)
            and np.sum(window == values[i]) == 1
        ):
            result[i] = True

    return result


# ============================================================
# DIVERGENCE
# ============================================================

def find_bullish_divergence(df):
    lows = df["low"]
    rsi = df["rsi"]

    pivots = pivot_lows(
        lows,
        PIVOT_LEFT,
        PIVOT_RIGHT,
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

    price_previous = float(lows.iloc[previous])
    price_latest = float(lows.iloc[latest])

    rsi_previous = float(rsi.iloc[previous])
    rsi_latest = float(rsi.iloc[latest])

    price_change = (
        (price_latest - price_previous)
        / price_previous
        * 100
    )

    rsi_change = rsi_latest - rsi_previous

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


def find_bearish_divergence(df):
    highs = df["high"]
    rsi = df["rsi"]

    pivots = pivot_highs(
        highs,
        PIVOT_LEFT,
        PIVOT_RIGHT,
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

    price_previous = float(highs.iloc[previous])
    price_latest = float(highs.iloc[latest])

    rsi_previous = float(rsi.iloc[previous])
    rsi_latest = float(rsi.iloc[latest])

    price_change = (
        (price_latest - price_previous)
        / price_previous
        * 100
    )

    rsi_change = rsi_latest - rsi_previous

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
        PIVOT_RIGHT,
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

    if y2 >= y1 or p2 == p1:
        return False

    slope = (y2 - y1) / (p2 - p1)

    current_x = len(df) - 1

    trendline = y1 + slope * (
        current_x - p1
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
        PIVOT_RIGHT,
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

    if y2 <= y1 or p2 == p1:
        return False

    slope = (y2 - y1) / (p2 - p1)

    current_x = len(df) - 1

    trendline = y1 + slope * (
        current_x - p1
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

def calculate_atr(df, period=14):
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1,
    ).max(axis=1)

    tr_values = tr.to_numpy(dtype=float)

    atr_values = np.full(
        len(df),
        np.nan,
        dtype=float,
    )

    if len(df) < period:
        return pd.Series(
            atr_values,
            index=df.index,
        )

    first_atr = np.nanmean(
        tr_values[:period]
    )

    atr_values[period - 1] = first_atr

    for i in range(
        period,
        len(df),
    ):
        previous_atr = atr_values[i - 1]
        current_tr = tr_values[i]

        if np.isnan(previous_atr):
            atr_values[i] = current_tr
        else:
            atr_values[i] = (
                previous_atr * (period - 1)
                + current_tr
            ) / period

    return pd.Series(
        atr_values,
        index=df.index,
    )


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(
    df,
    key_value=3.0,
    atr_period=10,
):
    src = df["close"].astype(float)

    atr = calculate_atr(
        df,
        atr_period,
    )

    nloss = key_value * atr

    trailing_stop = np.full(
        len(df),
        np.nan,
        dtype=float,
    )

    buy_signal = np.zeros(
        len(df),
        dtype=bool,
    )

    sell_signal = np.zeros(
        len(df),
        dtype=bool,
    )

    if len(df) == 0:
        return {
            "stop": pd.Series(
                dtype=float,
                index=df.index,
            ),
            "buy": pd.Series(
                dtype=bool,
                index=df.index,
            ),
            "sell": pd.Series(
                dtype=bool,
                index=df.index,
            ),
        }

    src_values = src.to_numpy(dtype=float)
    loss_values = nloss.to_numpy(dtype=float)

    valid_indexes = np.where(
        np.isfinite(loss_values)
    )[0]

    if len(valid_indexes) == 0:
        return {
            "stop": pd.Series(
                trailing_stop,
                index=df.index,
            ),
            "buy": pd.Series(
                buy_signal,
                index=df.index,
            ),
            "sell": pd.Series(
                sell_signal,
                index=df.index,
            ),
        }

    first_valid = int(valid_indexes[0])

    trailing_stop[first_valid] = (
        src_values[first_valid]
        - loss_values[first_valid]
    )

    for i in range(
        first_valid + 1,
        len(df),
    ):
        current_src = src_values[i]
        previous_src = src_values[i - 1]
        current_loss = loss_values[i]
        previous_stop = trailing_stop[i - 1]

        if not np.isfinite(current_loss):
            trailing_stop[i] = previous_stop
            continue

        if not np.isfinite(previous_stop):
            previous_stop = 0.0

        if (
            current_src > previous_stop
            and previous_src > previous_stop
        ):
            trailing_stop[i] = max(
                previous_stop,
                current_src - current_loss,
            )

        elif (
            current_src < previous_stop
            and previous_src < previous_stop
        ):
            trailing_stop[i] = min(
                previous_stop,
                current_src + current_loss,
            )

        elif current_src > previous_stop:
            trailing_stop[i] = (
                current_src - current_loss
            )

        else:
            trailing_stop[i] = (
                current_src + current_loss
            )

    for i in range(
        first_valid + 1,
        len(df),
    ):
        current_src = src_values[i]
        previous_src = src_values[i - 1]

        current_stop = trailing_stop[i]
        previous_stop = trailing_stop[i - 1]

        if not (
            np.isfinite(current_stop)
            and np.isfinite(previous_stop)
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
            index=df.index,
        ),
        "buy": pd.Series(
            buy_signal,
            index=df.index,
        ),
        "sell": pd.Series(
            sell_signal,
            index=df.index,
        ),
    }


# ============================================================
# VOLUME
# ============================================================

def calculate_volume_ratio(df, lookback=20):
    if len(df) <= lookback:
        return 0.0

    current_volume = float(
        df["volume"].iloc[-1]
    )

    previous_volumes = (
        df["volume"]
        .iloc[-lookback - 1:-1]
    )

    average_volume = float(
        previous_volumes.mean()
    )

    if (
        not np.isfinite(average_volume)
        or average_volume <= 0
    ):
        return 0.0

    return current_volume / average_volume


# ============================================================
# SCORE
# ============================================================

def score_label(score):
    if score >= 85:
        return "🔥 STRONG"

    if score >= 75:
        return "🟢 GOOD"

    if score >= 65:
        return "🟡 WATCH"

    return "⚪ WEAK"


def calculate_candidate_score(
    side,
    divergence,
    ut_trigger,
    trendline,
    trend_15m,
    trend_1h,
    volume_ratio,
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

    if divergence:
        score += RSI_DIVERGENCE_SCORE
        components["divergence"] = RSI_DIVERGENCE_SCORE

    if ut_trigger:
        score += UT_TRIGGER_SCORE
        components["ut"] = UT_TRIGGER_SCORE

    if trendline:
        score += TRENDLINE_SCORE
        components["trendline"] = TRENDLINE_SCORE

    if trend_15m == expected_trend:
        score += TREND_15M_SCORE
        components["trend_15m"] = TREND_15M_SCORE

    if trend_1h == expected_trend:
        score += TREND_1H_SCORE
        components["trend_1h"] = TREND_1H_SCORE

    if volume_ratio >= VOLUME_CONFIRMATION_RATIO:
        score += VOLUME_SCORE
        components["volume"] = VOLUME_SCORE

    return {
        "score": score,
        "components": components,
        "label": score_label(score),
        "volume_ratio": volume_ratio,
    }


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def nearest_support(df, price):
    candidates = [
        float(x)
        for x in df["low"].values
        if x < price
    ]

    if not candidates:
        return None

    return max(candidates)


def nearest_resistance(df, price):
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
    swing_level,
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
        atr * SL_ATR_MULTIPLIER
    )

    if side == "BUY":
        if swing_level is None:
            swing_distance = 0
        else:
            swing_sl = (
                swing_level
                * (1 - SL_BUFFER_PERCENT / 100)
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
                * (1 + SL_BUFFER_PERCENT / 100)
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
        minimum_distance,
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


def level_percent(side, entry, level):
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
# OPEN TRADE
# ============================================================

def has_open_trade_for_symbol(state, symbol):
    symbol = str(symbol).upper()

    for trade in get_all_trades(state):
        if (
            str(
                trade.get(
                    "status",
                    "",
                )
            ).upper() != "OPEN"
        ):
            continue

        if normalize_coin(trade) == symbol:
            return True

    return False


def register_signal(state, signal):
    symbol = str(
        signal["symbol"]
    ).upper()

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:
        if has_open_trade_for_symbol(
            state,
            symbol,
        ):
            return False

    signal_id = signal.get("signal_id")

    if not signal_id:
        return False

    for trade in get_all_trades(state):
        if get_trade_id(trade) == signal_id:
            return False

    trade = dict(signal)

    trade["status"] = "OPEN"
    trade["id"] = signal_id
    trade["signal_id"] = signal_id
    trade["direction"] = signal["side"]
    trade["name"] = signal["symbol"]

    trade["tp1_hit"] = False
    trade["tp2_hit"] = False
    trade["tp3_hit"] = False

    trade["remaining_percent"] = 100.0
    trade["realized_pnl_percent"] = 0.0
    trade["realized_r"] = 0.0

    trade["sl_moved_to_entry"] = False
    trade["sl_moved_to_tp1"] = False

    if isinstance(state.get("trades"), dict):
        state["trades"][signal_id] = trade

    elif isinstance(state.get("trades"), list):
        state["trades"].append(trade)

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
    atr,
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
            sl_tp["sl"],
        ),

        "tp1_percent": level_percent(
            side,
            entry,
            sl_tp["tp1"],
        ),

        "tp2_percent": level_percent(
            side,
            entry,
            sl_tp["tp2"],
        ),

        "tp3_percent": level_percent(
            side,
            entry,
            sl_tp["tp3"],
        ),

        "risk_percent": sl_tp["risk_percent"],
        "atr": atr,
        "atr_multiplier": SL_ATR_MULTIPLIER,

        "signal_time": signal_time,

        "signal_time_iso": datetime.fromtimestamp(
            signal_time / 1000,
            tz=timezone.utc,
        ).isoformat(),

        "trend_15m": trend_15m,
        "trend_1h": trend_1h,
        "ut_trigger": ut_trigger,
        "trendline_break": trendline_break,
        "volume_ratio": volume_ratio,

        "score": score_data["score"],
        "score_label": score_data["label"],
        "score_components": score_data["components"],

        "reason": (
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
        "5m",
    )

    trend_data = get_multi_timeframe_trend(
        symbol
    )

    trend_15m = trend_data["15m"]["trend"]
    trend_1h = trend_data["1h"]["trend"]

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD,
    )

    df["atr_sl"] = calculate_atr(
        df,
        SL_ATR_PERIOD,
    )

    df["atr"] = calculate_atr(
        df,
        UT_ATR_PERIOD,
    )

    ut_data = calculate_ut_bot(
        df,
        UT_KEY_VALUE,
        UT_ATR_PERIOD,
    )

    df["ut_stop"] = ut_data["stop"]
    df["ut_buy_signal"] = ut_data["buy"]
    df["ut_sell_signal"] = ut_data["sell"]

    bullish_divergence = find_bullish_divergence(df)
    bearish_divergence = find_bearish_divergence(df)

    bullish_break = descending_trendline_break(df)
    bearish_break = ascending_trendline_break(df)

    volume_ratio = calculate_volume_ratio(
        df,
        VOLUME_LOOKBACK,
    )

    volume_confirmed = (
        volume_ratio
        >= VOLUME_CONFIRMATION_RATIO
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

    ut_buy = bool(
        df["ut_buy_signal"].iloc[-1]
    )

    ut_sell = bool(
        df["ut_sell_signal"].iloc[-1]
    )

    bullish_confirmation = (
        bullish_break or ut_buy
    )

    bearish_confirmation = (
        bearish_break or ut_sell
    )

    buy_trend_ok = (
        trend_15m == "BULLISH"
        and trend_1h == "BULLISH"
    )

    sell_trend_ok = (
        trend_15m == "BEARISH"
        and trend_1h == "BEARISH"
    )

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

        "candidate_sides": [],
        "candidate_details": [],
        "rejections": [],
        "is_setup_candidate": False,
    }

    final_candidates = []

    # ========================================================
    # BUY CANDIDATE
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
            volume_ratio=volume_ratio,
        )

        candidate_info = {
            "side": "BUY",
            "divergence": True,
            "ut": ut_buy,
            "trendline": bullish_break,
            "trend_15m": trend_15m,
            "trend_1h": trend_1h,
            "volume_ratio": volume_ratio,
            "volume_confirmed": volume_confirmed,
            "score": score_data["score"],
            "components": score_data["components"],
            "label": score_data["label"],
            "trend_ok": buy_trend_ok,
            "sl_ok": None,
            "final_ready": False,
            "rejection": None,
            "entry": current_close,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
        }

        if not buy_trend_ok:
            candidate_info["rejection"] = "TREND_FILTER"

        else:
            support = nearest_support(
                df,
                current_close,
            )

            sl_tp = build_sl_tp(
                "BUY",
                current_close,
                current_atr_sl,
                support,
            )

            if sl_tp is None:
                candidate_info["sl_ok"] = False
                candidate_info["rejection"] = "SL"

            else:
                candidate_info["sl_ok"] = True
                candidate_info["sl"] = sl_tp["sl"]
                candidate_info["tp1"] = sl_tp["tp1"]
                candidate_info["tp2"] = sl_tp["tp2"]
                candidate_info["tp3"] = sl_tp["tp3"]

                if score_data["score"] >= MIN_SIGNAL_SCORE:
                    candidate_info["final_ready"] = True

                    final_candidates.append(
                        create_signal(
                            symbol=symbol,
                            side="BUY",
                            entry=current_close,
                            sl_tp=sl_tp,
                            signal_time=signal_time,
                            trend_15m=trend_15m,
                            trend_1h=trend_1h,
                            ut_trigger=ut_buy,
                            trendline_break=bullish_break,
                            volume_ratio=volume_ratio,
                            score_data=score_data,
                            atr=current_atr_sl,
                        )
                    )

                else:
                    candidate_info["rejection"] = "SCORE"

        diagnostic["candidate_sides"].append("BUY")
        diagnostic["candidate_details"].append(candidate_info)
        diagnostic["is_setup_candidate"] = True

    # ========================================================
    # SELL CANDIDATE
    # ========================================================

    if (
        bearish_divergence is not None
        and bearish_confirmation
    ):
        score_data = calculate_candidate_score(
            side="SELL",
            divergence=True,
            ut_trigger=ut_sell,
            trendline=bearish_break,
            trend_15m=trend_15m,
            trend_1h=trend_1h,
            volume_ratio=volume_ratio,
        )

        candidate_info = {
            "side": "SELL",
            "divergence": True,
            "ut": ut_sell,
            "trendline": bearish_break,
            "trend_15m": trend_15m,
            "trend_1h": trend_1h,
            "volume_ratio": volume_ratio,
            "volume_confirmed": volume_confirmed,
            "score": score_data["score"],
            "components": score_data["components"],
            "label": score_data["label"],
            "trend_ok": sell_trend_ok,
            "sl_ok": None,
            "final_ready": False,
            "rejection": None,
            "entry": current_close,
            "sl": None,
            "tp1": None,
            "tp2": None,
            "tp3": None,
        }

        if not sell_trend_ok:
            candidate_info["rejection"] = "TREND_FILTER"

        else:
            resistance = nearest_resistance(
                df,
                current_close,
            )

            sl_tp = build_sl_tp(
                "SELL",
                current_close,
                current_atr_sl,
                resistance,
            )

            if sl_tp is None:
                candidate_info["sl_ok"] = False
                candidate_info["rejection"] = "SL"

            else:
                candidate_info["sl_ok"] = True
                candidate_info["sl"] = sl_tp["sl"]
                candidate_info["tp1"] = sl_tp["tp1"]
                candidate_info["tp2"] = sl_tp["tp2"]
                candidate_info["tp3"] = sl_tp["tp3"]

                if score_data["score"] >= MIN_SIGNAL_SCORE:
                    candidate_info["final_ready"] = True

                    final_candidates.append(
                        create_signal(
                            symbol=symbol,
                            side="SELL",
                            entry=current_close,
                            sl_tp=sl_tp,
                            signal_time=signal_time,
                            trend_15m=trend_15m,
                            trend_1h=trend_1h,
                            ut_trigger=ut_sell,
                            trendline_break=bearish_break,
                            volume_ratio=volume_ratio,
                            score_data=score_data,
                            atr=current_atr_sl,
                        )
                    )

                else:
                    candidate_info["rejection"] = "SCORE"

        diagnostic["candidate_sides"].append("SELL")
        diagnostic["candidate_details"].append(candidate_info)
        diagnostic["is_setup_candidate"] = True

    symbol_best_signal = None

    if final_candidates:
        final_candidates.sort(
            key=lambda x: (
                x.get("score", 0),
                x.get("volume_ratio", 0),
            ),
            reverse=True,
        )

        symbol_best_signal = final_candidates[0]

    return {
        "symbol": symbol,
        "df": df,
        "signal": symbol_best_signal,
        "final_candidates": final_candidates,
        "trend_data": trend_data,
        "market_symbol": get_market_symbol(symbol),
        "diagnostic": diagnostic,
    }


# ============================================================
# GLOBAL CANDIDATE COLLECTION
# ============================================================

def get_visible_candidates(results):
    candidates = []

    for result in results:
        symbol = result["symbol"]

        diagnostic = result.get(
            "diagnostic",
            {},
        )

        for candidate in diagnostic.get(
            "candidate_details",
            [],
        ):
            score = candidate.get(
                "score",
                0,
            )

            if score >= MIN_DISPLAY_CANDIDATE_SCORE:
                item = dict(candidate)
                item["symbol"] = symbol

                candidates.append(item)

    candidates.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("volume_ratio", 0),
        ),
        reverse=True,
    )

    return candidates


# ============================================================
# ALL SETUP CANDIDATES
# ============================================================

def get_all_setup_candidates(results):
    candidates = []

    for result in results:
        symbol = result["symbol"]

        diagnostic = result.get(
            "diagnostic",
            {},
        )

        for candidate in diagnostic.get(
            "candidate_details",
            [],
        ):
            item = dict(candidate)
            item["symbol"] = symbol
            candidates.append(item)

    candidates.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("volume_ratio", 0),
        ),
        reverse=True,
    )

    return candidates


def get_global_final_signal(
    results,
    state,
):
    candidates = []

    for result in results:
        symbol = result["symbol"]

        if (
            not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
            and has_open_trade_for_symbol(
                state,
                symbol,
            )
        ):
            continue

        for signal in result.get(
            "final_candidates",
            [],
        ):
            if signal.get("score", 0) < MIN_SIGNAL_SCORE:
                continue

            item = dict(signal)
            item["symbol"] = symbol

            candidates.append(item)

    if not candidates:
        return None

    candidates.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("volume_ratio", 0),
        ),
        reverse=True,
    )

    best = candidates[0]

    best["signal_id"] = make_signal_id(
        best["symbol"],
        best["side"],
        best["signal_time"],
        best["entry"],
    )

    best["id"] = best["signal_id"]

    return best


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(signal):
    side = str(
        signal["side"]
    ).upper()

    icon = "🟢" if side == "BUY" else "🔴"

    entry = signal["entry"]
    sl = signal["sl"]

    tp1 = signal["tp1"]
    tp2 = signal["tp2"]
    tp3 = signal["tp3"]

    score = signal.get("score", 0)

    label = signal.get(
        "score_label",
        score_label(score),
    )

    return "\n".join([
        f"{icon} {signal['symbol']}/USDT - "
        f"{side} ⭐ {score}/100",

        f"Score: {label}",

        f"Entry: {format_price(entry)}",

        f"Stop Loss: {format_price(sl)} "
        f"({level_percent(side, entry, sl):+.2f}%)",

        f"Target 1: {format_price(tp1)} "
        f"({level_percent(side, entry, tp1):+.2f}%) "
        f"[{TP1_CLOSE_PERCENT}%]",

        f"Target 2: {format_price(tp2)} "
        f"({level_percent(side, entry, tp2):+.2f}%) "
        f"[{TP2_CLOSE_PERCENT}%]",

        f"Target 3: {format_price(tp3)} "
        f"({level_percent(side, entry, tp3):+.2f}%) "
        f"[{TP3_CLOSE_PERCENT}%]",

        f"Risk: {abs(level_percent(side, entry, sl)):.2f}%",

        f"RR: 1:{TP1_R_MULTIPLE:.1f} / "
        f"1:{TP2_R_MULTIPLE:.1f} / "
        f"1:{TP3_R_MULTIPLE:.1f}",

        f"15M Trend: {signal.get('trend_15m', 'N/A')}",

        f"1H Trend: {signal.get('trend_1h', 'N/A')}",

        f"Volume: {signal.get('volume_ratio', 0):.2f}x",

        f"Reason: {signal['reason']}",

        "Management: "
        "TP1→SL Entry | "
        "TP2→SL TP1",
    ])


# ============================================================
# FORMAT CANDIDATE
# ============================================================

def format_candidate(
    symbol,
    candidate,
    rank=None,
):
    side = candidate["side"]

    icon = "🟢" if side == "BUY" else "🔴"

    score = candidate["score"]
    label = candidate["label"]

    components = candidate["components"]

    rank_text = (
        f"#{rank} "
        if rank is not None
        else ""
    )

    lines = [
        f"{rank_text}🪙 {symbol}/USDT",
        f"{icon} Direction: {side}",
        f"⭐ FINAL SCORE: {score}/100 {label}",

        "━━━━━━━━━━━━━━━━━━",

        f"RSI Divergence: "
        f"+{components['divergence']}",

        f"UT Bot Trigger: "
        f"+{components['ut']}",

        f"Trendline: "
        f"+{components['trendline']}",

        f"15M Trend: "
        f"+{components['trend_15m']} "
        f"({candidate['trend_15m']})",

        f"1H Trend: "
        f"+{components['trend_1h']} "
        f"({candidate['trend_1h']})",

        f"Volume: "
        f"+{components['volume']} "
        f"({candidate['volume_ratio']:.2f}x)",

        "━━━━━━━━━━━━━━━━━━",

        f"Entry: "
        f"{format_price(candidate.get('entry'))}",
    ]

    if candidate.get("sl") is not None:
        lines.append(
            f"SL: {format_price(candidate['sl'])}"
        )

    if candidate.get("tp1") is not None:
        lines.append(
            f"TP1: {format_price(candidate['tp1'])}"
        )

    if candidate["final_ready"]:
        lines.append(
            "STATUS: 🚨 READY 75+"
        )

    elif candidate["rejection"] == "SCORE":
        lines.append(
            "STATUS: 🟡 BELOW SIGNAL THRESHOLD"
        )

    elif candidate["rejection"] == "SL":
        lines.append(
            "STATUS: ❌ INVALID SL"
        )

    elif candidate["rejection"] == "TREND_FILTER":
        lines.append(
            "STATUS: ❌ TREND FILTER"
        )

    else:
        lines.append(
            "STATUS: 🟡 WATCH"
        )

    return "\n".join(lines)


# ============================================================
# PARTIAL TP HELPERS
# ============================================================

def calculate_partial_result(
    side,
    entry,
    exit_price,
    risk,
    percent,
):
    if side == "BUY":
        pnl_percent = (
            (exit_price - entry)
            / entry
            * 100
        )

        r_multiple = (
            (exit_price - entry)
            / risk
        )

    else:
        pnl_percent = (
            (entry - exit_price)
            / entry
            * 100
        )

        r_multiple = (
            (entry - exit_price)
            / risk
        )

    weighted_pnl = (
        pnl_percent
        * percent
        / 100
    )

    weighted_r = (
        r_multiple
        * percent
        / 100
    )

    return (
        weighted_pnl,
        weighted_r,
    )


def close_trade(
    trade,
    reason,
    price,
    candle_time,
):
    entry = get_trade_entry(trade)
    sl = get_trade_sl(trade)
    side = normalize_side(trade)

    if (
        entry is None
        or sl is None
        or side not in ("BUY", "SELL")
    ):
        return False

    risk = abs(entry - sl)

    if risk <= 0:
        return False

    if side == "BUY":
        pnl = (
            (price - entry)
            / entry
            * 100
        )

        r_multiple = (
            (price - entry)
            / risk
        )

    else:
        pnl = (
            (entry - price)
            / entry
            * 100
        )

        r_multiple = (
            (entry - price)
            / risk
        )

    signal_time = parse_trade_time(
        trade.get("signal_time")
    )

    duration = 0

    if signal_time is not None:
        duration = max(
            0,
            (
                candle_time
                - signal_time
            ) / 60000,
        )

    trade["status"] = "CLOSED"
    trade["exit_reason"] = reason
    trade["result_reason"] = reason
    trade["exit_price"] = price
    trade["result_price"] = price
    trade["exit_time"] = candle_time

    trade["pnl_percent"] = (
        float(
            trade.get(
                "realized_pnl_percent",
                0,
            )
        )
        + (
            pnl
            * float(
                trade.get(
                    "remaining_percent",
                    100,
                )
            )
            / 100
        )
    )

    trade["r_multiple"] = (
        float(
            trade.get(
                "realized_r",
                0,
            )
        )
        + (
            r_multiple
            * float(
                trade.get(
                    "remaining_percent",
                    100,
                )
            )
            / 100
        )
    )

    trade["result_r"] = trade["r_multiple"]

    trade["realized_pnl_percent"] = (
        trade["pnl_percent"]
    )

    trade["realized_r"] = (
        trade["r_multiple"]
    )

    trade["remaining_percent"] = 0

    trade["duration_minutes"] = duration

    trade["closed_at"] = datetime.fromtimestamp(
        candle_time / 1000,
        tz=timezone.utc,
    ).isoformat()

    return True


# ============================================================
# OPEN PERFORMANCE
# ============================================================

def calculate_open_performance(
    state,
    data_cache,
):
    result = []

    for trade in get_all_trades(state):
        if str(
            trade.get(
                "status",
                "",
            )
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
                (current_price - entry)
                / entry
                * 100
            )
        else:
            pnl = (
                (entry - current_price)
                / entry
                * 100
            )

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
                ) / 60000,
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

            "score": trade.get("score"),

            "tp1_hit": trade.get(
                "tp1_hit",
                False,
            ),

            "tp2_hit": trade.get(
                "tp2_hit",
                False,
            ),

            "tp3_hit": trade.get(
                "tp3_hit",
                False,
            ),

            "remaining_percent": trade.get(
                "remaining_percent",
                100,
            ),

            "realized_pnl_percent": trade.get(
                "realized_pnl_percent",
                0,
            ),
        })

    return result


# ============================================================
# EVALUATE OPEN TRADES
# ============================================================

def evaluate_open_trades(
    state,
    data_cache,
):
    changed = False

    for trade in get_all_trades(state):
        if str(
            trade.get(
                "status",
                "",
            )
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

        for _, row in after_signal.iterrows():
            if str(
                trade.get(
                    "status",
                    "",
                )
            ).upper() != "OPEN":
                break

            high = float(row["high"])
            low = float(row["low"])
            candle_time = int(row["time"])

            risk = abs(
                entry - sl
            )

            if risk <= 0:
                continue

            # =================================================
            # SL CHECK
            # =================================================

            if side == "BUY":
                if low <= sl:
                    if close_trade(
                        trade,
                        "SL",
                        sl,
                        candle_time,
                    ):
                        changed = True

                    break

            else:
                if high >= sl:
                    if close_trade(
                        trade,
                        "SL",
                        sl,
                        candle_time,
                    ):
                        changed = True

                    break

            # =================================================
            # TP1
            # =================================================

            if (
                tp1 is not None
                and not trade.get(
                    "tp1_hit",
                    False,
                )
            ):
                tp1_hit = (
                    high >= tp1
                    if side == "BUY"
                    else low <= tp1
                )

                if tp1_hit:
                    weighted_pnl, weighted_r = (
                        calculate_partial_result(
                            side,
                            entry,
                            tp1,
                            risk,
                            TP1_CLOSE_PERCENT,
                        )
                    )

                    trade["tp1_hit"] = True

                    trade["remaining_percent"] = (
                        float(
                            trade.get(
                                "remaining_percent",
                                100,
                            )
                        )
                        - TP1_CLOSE_PERCENT
                    )

                    trade["realized_pnl_percent"] = (
                        float(
                            trade.get(
                                "realized_pnl_percent",
                                0,
                            )
                        )
                        + weighted_pnl
                    )

                    trade["realized_r"] = (
                        float(
                            trade.get(
                                "realized_r",
                                0,
                            )
                        )
                        + weighted_r
                    )

                    trade["sl"] = entry
                    trade["sl_moved_to_entry"] = True

                    changed = True

            # =================================================
            # TP2
            # =================================================

            if (
                tp2 is not None
                and trade.get("tp1_hit", False)
                and not trade.get(
                    "tp2_hit",
                    False,
                )
            ):
                tp2_hit = (
                    high >= tp2
                    if side == "BUY"
                    else low <= tp2
                )

                if tp2_hit:
                    weighted_pnl, weighted_r = (
                        calculate_partial_result(
                            side,
                            entry,
                            tp2,
                            risk,
                            TP2_CLOSE_PERCENT,
                        )
                    )

                    trade["tp2_hit"] = True

                    trade["remaining_percent"] = (
                        float(
                            trade.get(
                                "remaining_percent",
                                67,
                            )
                        )
                        - TP2_CLOSE_PERCENT
                    )

                    trade["realized_pnl_percent"] = (
                        float(
                            trade.get(
                                "realized_pnl_percent",
                                0,
                            )
                        )
                        + weighted_pnl
                    )

                    trade["realized_r"] = (
                        float(
                            trade.get(
                                "realized_r",
                                0,
                            )
                        )
                        + weighted_r
                    )

                    if tp1 is not None:
                        trade["sl"] = tp1
                        trade["sl_moved_to_tp1"] = True

                    changed = True

            # =================================================
            # TP3
            # =================================================

            if (
                tp3 is not None
                and trade.get("tp2_hit", False)
                and not trade.get(
                    "tp3_hit",
                    False,
                )
            ):
                tp3_hit = (
                    high >= tp3
                    if side == "BUY"
                    else low <= tp3
                )

                if tp3_hit:
                    weighted_pnl, weighted_r = (
                        calculate_partial_result(
                            side,
                            entry,
                            tp3,
                            risk,
                            TP3_CLOSE_PERCENT,
                        )
                    )

                    trade["tp3_hit"] = True

                    trade["remaining_percent"] = 0

                    trade["realized_pnl_percent"] = (
                        float(
                            trade.get(
                                "realized_pnl_percent",
                                0,
                            )
                        )
                        + weighted_pnl
                    )

                    trade["realized_r"] = (
                        float(
                            trade.get(
                                "realized_r",
                                0,
                            )
                        )
                        + weighted_r
                    )

                    trade["status"] = "CLOSED"
                    trade["exit_reason"] = "TP3"
                    trade["result_reason"] = "TP3"
                    trade["exit_price"] = tp3
                    trade["result_price"] = tp3
                    trade["exit_time"] = candle_time
                    trade["pnl_percent"] = (
                        trade["realized_pnl_percent"]
                    )
                    trade["r_multiple"] = (
                        trade["realized_r"]
                    )
                    trade["result_r"] = (
                        trade["realized_r"]
                    )

                    duration = max(
                        0,
                        (
                            candle_time
                            - signal_time
                        ) / 60000,
                    )

                    trade["duration_minutes"] = duration

                    trade["closed_at"] = (
                        datetime.fromtimestamp(
                            candle_time / 1000,
                            tz=timezone.utc,
                        ).isoformat()
                    )

                    changed = True
                    break

    return changed


# ============================================================
# NEWLY CLOSED
# ============================================================

def get_newly_closed_trades(
    state,
    previous_closed_ids,
):
    result = []

    for trade in get_all_trades(state):
        if str(
            trade.get(
                "status",
                "",
            )
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
# CLOSED SIGNAL FORMAT
# ============================================================

def format_closed_signal(trade):
    side = normalize_side(trade)
    coin = normalize_coin(trade)

    icon = "🟢" if side == "BUY" else "🔴"

    reason = (
        trade.get("result_reason")
        or trade.get("exit_reason")
        or "UNKNOWN"
    )

    reason_upper = str(reason).upper()

    if reason_upper.startswith("TP"):
        result_icon = "✅"
    elif reason_upper == "SL":
        result_icon = "❌"
    else:
        result_icon = "⚪"

    entry = get_trade_entry(trade)

    exit_price = trade.get(
        "result_price",
        trade.get("exit_price"),
    )

    try:
        exit_price = float(exit_price)
    except Exception:
        exit_price = None

    try:
        pnl = float(
            trade.get(
                "pnl_percent",
                0,
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
                    0,
                ),
            )
        )
    except Exception:
        r_multiple = 0

    duration = trade.get(
        "duration_minutes",
        0,
    )

    score = trade.get("score")

    score_text = ""

    if score is not None:
        try:
            score_text = (
                f" | ⭐ {float(score):.0f}/100"
            )
        except Exception:
            pass

    return "\n".join([
        f"{icon} {coin} {side} | "
        f"{result_icon} {reason}{score_text}",

        f"Entry: {format_price(entry)}",
        f"Exit: {format_price(exit_price)}",
        f"P&L: {pnl:+.2f}%",
        f"R: {r_multiple:+.2f}R",
        f"Duration: {float(duration):.0f} min",
    ])


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(state):
    trades = get_all_trades(state)

    open_trades = [
        x for x in trades
        if str(
            x.get(
                "status",
                "",
            )
        ).upper() == "OPEN"
    ]

    closed_trades = [
        x for x in trades
        if str(
            x.get(
                "status",
                "",
            )
        ).upper() == "CLOSED"
    ]

    wins = []
    losses = []

    for trade in closed_trades:
        try:
            pnl = float(
                trade.get(
                    "pnl_percent",
                    0,
                )
            )
        except Exception:
            pnl = 0

        if pnl > 0:
            wins.append(trade)
        else:
            losses.append(trade)

    total_pnl = sum(
        float(
            x.get(
                "pnl_percent",
                0,
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
        float(
            np.mean([
                float(
                    x.get(
                        "pnl_percent",
                        0,
                    )
                )
                for x in wins
            ])
        )
        if wins
        else 0
    )

    avg_loss = (
        float(
            np.mean([
                float(
                    x.get(
                        "pnl_percent",
                        0,
                    )
                )
                for x in losses
            ])
        )
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
# DIAGNOSTICS
# ============================================================

def calculate_diagnostics(results):
    stats = {
        "bullish_divergence": 0,
        "bearish_divergence": 0,
        "bullish_break": 0,
        "bearish_break": 0,
        "ut_buy": 0,
        "ut_sell": 0,
        "buy_candidates": 0,
        "sell_candidates": 0,
        "setup_candidates": 0,
        "visible_candidates": 0,
        "ready_candidates": 0,
        "buy_trend_ready": 0,
        "sell_trend_ready": 0,
    }

    for result in results:
        diagnostic = result.get(
            "diagnostic",
            {},
        )

        if diagnostic.get("bullish_divergence"):
            stats["bullish_divergence"] += 1

        if diagnostic.get("bearish_divergence"):
            stats["bearish_divergence"] += 1

        if diagnostic.get("bullish_break"):
            stats["bullish_break"] += 1

        if diagnostic.get("bearish_break"):
            stats["bearish_break"] += 1

        if diagnostic.get("ut_buy"):
            stats["ut_buy"] += 1

        if diagnostic.get("ut_sell"):
            stats["ut_sell"] += 1

        candidate_sides = diagnostic.get(
            "candidate_sides",
            [],
        )

        if "BUY" in candidate_sides:
            stats["buy_candidates"] += 1

        if "SELL" in candidate_sides:
            stats["sell_candidates"] += 1

        stats["setup_candidates"] += len(
            candidate_sides
        )

        for candidate in diagnostic.get(
            "candidate_details",
            [],
        ):
            score = candidate.get(
                "score",
                0,
            )

            if score >= MIN_DISPLAY_CANDIDATE_SCORE:
                stats["visible_candidates"] += 1

            if candidate.get("final_ready", False):
                stats["ready_candidates"] += 1

        if diagnostic.get("buy_trend_ok"):
            stats["buy_trend_ready"] += 1

        if diagnostic.get("sell_trend_ok"):
            stats["sell_trend_ready"] += 1

    return stats


# ============================================================
# REPORT
# ============================================================

def format_report(
    state,
    results,
    errors,
    open_performance,
    closed_this_run,
    blocked_symbols=None,
    registered_signals=None,
):
    if blocked_symbols is None:
        blocked_symbols = []

    if registered_signals is None:
        registered_signals = []

    stats = calculate_statistics(state)
    diagnostic = calculate_diagnostics(results)

    visible_candidates = get_visible_candidates(results)

    # NEW:
    # ALL setup candidates including those below 65.
    all_setup_candidates = get_all_setup_candidates(results)

    global_final = (
        registered_signals[0]
        if registered_signals
        else None
    )

    lines = []

    lines.append(
        "📡 CRYPTO DIVERGENCE SCANNER v10.9 SCORE"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC"
    )

    lines.append(
        f"⏱ Timeframe: {TIMEFRAME.upper()} CLOSED"
    )

    lines.append(
        f"🤖 UT Bot: Key {UT_KEY_VALUE:g} / ATR {UT_ATR_PERIOD}"
    )

    lines.append(
        f"🎯 Candidate: {MIN_DISPLAY_CANDIDATE_SCORE}+ / 100"
    )

    lines.append(
        f"🚨 Signal: {MIN_SIGNAL_SCORE}+ / 100"
    )

    lines.append(
        f"📊 DATA OK: {len(results)}/{len(COINS)}"
    )

    lines.append(
        f"⚠️ DATA ERROR: {len(errors)}"
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    lines.extend([
        "",
        "📊 CUMULATIVE PERFORMANCE",
        "━━━━━━━━━━━━━━━━━━",
        f"Total Trades: {stats['total']}",
        f"Open: {stats['open']}",
        f"Closed: {stats['closed']}",
        f"Wins: {stats['wins']}",
        f"Losses: {stats['losses']}",
        f"Win Rate: {stats['win_rate']:.2f}%",
        f"Closed P&L: {stats['total_pnl']:.2f}%",
        f"Avg Win: {stats['avg_win']:.2f}%",
        f"Avg Loss: {stats['avg_loss']:.2f}%",
    ])

    # ========================================================
    # DIAGNOSTIC
    # ========================================================

    lines.extend([
        "",
        "📊 SIGNAL DIAGNOSTIC",
        "━━━━━━━━━━━━━━━━━━",
        f"RSI Bullish Divergence: "
        f"{diagnostic['bullish_divergence']}",
        f"RSI Bearish Divergence: "
        f"{diagnostic['bearish_divergence']}",
        f"Trendline Breakout: "
        f"{diagnostic['bullish_break']}",
        f"Trendline Breakdown: "
        f"{diagnostic['bearish_break']}",
        f"UT Bot BUY CROSS: "
        f"{diagnostic['ut_buy']}",
        f"UT Bot SELL CROSS: "
        f"{diagnostic['ut_sell']}",
        f"SETUP CANDIDATES: "
        f"{diagnostic['setup_candidates']}",
        f"DISPLAYED 65+: "
        f"{diagnostic['visible_candidates']}",
        f"READY 75+: "
        f"{diagnostic['ready_candidates']}",
        f"GLOBAL FINAL SIGNALS: "
        f"{len(registered_signals)}",
    ])

    # ========================================================
    # SCORE SYSTEM
    # ========================================================

    lines.extend([
        "",
        "🎯 SCORE SYSTEM",
        "━━━━━━━━━━━━━━━━━━",
        "RSI Divergence +30",
        "UT Bot Trigger +20",
        "Trendline +15",
        "15M Trend +15",
        "1H Trend +15",
        "Volume +5",
        "🔥 85-100 STRONG",
        "🟢 75-84 GOOD",
        "🟡 65-74 WATCH",
    ])

    # ========================================================
    # NEW CANDIDATE SCORE BREAKDOWN
    # ========================================================

    lines.extend([
        "",
        "🔎 CANDIDATE SCORE BREAKDOWN",
        "━━━━━━━━━━━━━━━━━━",
    ])

    if all_setup_candidates:

        for rank, candidate in enumerate(
            all_setup_candidates,
            start=1,
        ):
            lines.append(
                format_candidate(
                    candidate["symbol"],
                    candidate,
                    rank,
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:
        lines.append(
            "هیچ Setup Candidate واقعی "
            "پیدا نشده است."
        )

    # ========================================================
    # GLOBAL CANDIDATES 65+
    # ========================================================

    lines.extend([
        "",
        "🎯 GLOBAL SETUP CANDIDATES 65+",
        "━━━━━━━━━━━━━━━━━━",
    ])

    if visible_candidates:

        for rank, candidate in enumerate(
            visible_candidates,
            start=1,
        ):
            lines.append(
                format_candidate(
                    candidate["symbol"],
                    candidate,
                    rank,
                )
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:
        lines.append(
            f"فعلاً کاندیدای "
            f"{MIN_DISPLAY_CANDIDATE_SCORE}+ وجود ندارد."
        )

    # ========================================================
    # GLOBAL FINAL
    # ========================================================

    lines.extend([
        "",
        "🏆 GLOBAL FINAL SELECTION",
        "━━━━━━━━━━━━━━━━━━",
    ])

    if global_final:

        lines.append(
            format_signal(global_final)
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "فقط همین یک سیگنال از کل بازار "
            "برای ثبت انتخاب شده است."
        )

    else:
        lines.append(
            "هیچ سیگنال 75+ واجد شرایطی وجود ندارد."
        )

    # ========================================================
    # LOCK
    # ========================================================

    if blocked_symbols:

        lines.extend([
            "",
            "🔒 SYMBOL LOCK",
            "━━━━━━━━━━━━━━━━━━",
        ])

        unique_blocked = sorted(
            set(blocked_symbols)
        )

        lines.append(
            f"{len(unique_blocked)} symbol blocked "
            f"due to OPEN trade"
        )

        lines.append(
            ", ".join(unique_blocked)
        )

    # ========================================================
    # CLOSED
    # ========================================================

    lines.extend([
        "",
        "🏁 CLOSED SIGNALS",
        "━━━━━━━━━━━━━━━━━━",
    ])

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
    # OPEN
    # ========================================================

    lines.extend([
        "",
        "📌 OPEN SIGNAL P&L",
        "━━━━━━━━━━━━━━━━━━",
    ])

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

            score = item.get("score")

            score_text = ""

            if score is not None:

                try:
                    score_text = (
                        f" ⭐{float(score):.0f}/100"
                    )
                except Exception:
                    pass

            lines.append(
                f"{icon} "
                f"{item['symbol']} "
                f"{side}{score_text}"
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

                status = (
                    "HIT"
                    if item.get("tp1_hit")
                    else "WAIT"
                )

                lines.append(
                    f"TP1: "
                    f"{format_price(item['tp1'])} "
                    f"[{status}]"
                )

            if item.get("tp2") is not None:

                status = (
                    "HIT"
                    if item.get("tp2_hit")
                    else "WAIT"
                )

                lines.append(
                    f"TP2: "
                    f"{format_price(item['tp2'])} "
                    f"[{status}]"
                )

            if item.get("tp3") is not None:

                status = (
                    "HIT"
                    if item.get("tp3_hit")
                    else "WAIT"
                )

                lines.append(
                    f"TP3: "
                    f"{format_price(item['tp3'])} "
                    f"[{status}]"
                )

            lines.append(
                f"Remaining: "
                f"{item['remaining_percent']:.0f}%"
            )

            lines.append(
                f"Realized P&L: "
                f"{item['realized_pnl_percent']:+.2f}%"
            )

            lines.append(
                f"Current P&L: "
                f"{pnl:+.2f}%"
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

        lines.extend([
            "",
            "⚠️ ERRORS",
            "━━━━━━━━━━━━━━━━━━",
        ])

        for symbol, error in errors.items():

            lines.append(
                f"{symbol}: {error}"
            )

    # ========================================================
    # SUMMARY
    # ========================================================

    lines.extend([
        "",
        "📋 SCAN SUMMARY",
        "━━━━━━━━━━━━━━━━━━",
        f"Symbols Scanned: {len(COINS)}",
        f"Data OK: {len(results)}",
        f"Data Errors: {len(errors)}",
        f"Setup Candidates: "
        f"{diagnostic['setup_candidates']}",
        f"Displayed 65+: "
        f"{diagnostic['visible_candidates']}",
        f"Ready 75+: "
        f"{diagnostic['ready_candidates']}",
        f"Global Final Signals: "
        f"{len(registered_signals)}",
        f"Open Trades: {stats['open']}",
    ])

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

        print(f"FATAL: {e}")
        return

    # ========================================================
    # STATE
    # ========================================================

    state = load_state()

    rebuild_trade_container(state)

    # ========================================================
    # PREVIOUS CLOSED IDS
    # ========================================================

    previous_closed_ids = set()

    for trade in get_all_trades(state):

        if str(
            trade.get(
                "status",
                "",
            )
        ).upper() == "CLOSED":

            trade_id = get_trade_id(trade)

            if trade_id:
                previous_closed_ids.add(
                    trade_id
                )

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
    # MANAGE EXISTING OPEN TRADES
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache,
    )

    if changed:
        save_state(state)

    # ========================================================
    # GLOBAL FINAL SIGNAL
    # ========================================================

    global_signal = get_global_final_signal(
        results,
        state,
    )

    new_registered = []

    if global_signal is not None:

        symbol = global_signal["symbol"]

        if (
            not ALLOW_MULTIPLE_OPEN_PER_SYMBOL
            and has_open_trade_for_symbol(
                state,
                symbol,
            )
        ):

            blocked_symbols.append(symbol)

        else:

            if register_signal(
                state,
                global_signal,
            ):

                new_registered.append(
                    global_signal
                )

    # ========================================================
    # SAVE
    # ========================================================

    save_state(state)

    # ========================================================
    # SECOND EVALUATION
    # ========================================================

    changed = evaluate_open_trades(
        state,
        data_cache,
    )

    if changed:
        save_state(state)

    # ========================================================
    # CLOSED THIS RUN
    # ========================================================

    closed_this_run = (
        get_newly_closed_trades(
            state,
            previous_closed_ids,
        )
    )

    # ========================================================
    # OPEN PERFORMANCE
    # ========================================================

    open_performance = (
        calculate_open_performance(
            state,
            data_cache,
        )
    )

    # ========================================================
    # REPORT
    # ========================================================

    report = format_report(
        state=state,
        results=results,
        errors=errors,
        open_performance=open_performance,
        closed_this_run=closed_this_run,
        blocked_symbols=blocked_symbols,
        registered_signals=new_registered,
    )

    print()
    print(report)

    # ========================================================
    # TELEGRAM
    # ========================================================

    telegram_ok = send_telegram(report)

    if telegram_ok:
        print(
            "Telegram report sent successfully."
        )
    else:
        print(
            "Telegram report FAILED."
        )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
