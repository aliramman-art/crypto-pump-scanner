# ============================================================
# CRYPTO DIVERGENCE SCANNER v10.0
# ============================================================
# Kraken Futures
# Closed 5m Candles
# RSI Divergence
# Trendline Breakout / Breakdown
# UT Bot
# Automatic Futures Symbol Discovery
# Trade History
# Open Signal P&L
# P&L / R / MFE / MAE / Duration
# Telegram
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

STATE_FILE = "trade_history.json"

ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False

REQUEST_TIMEOUT = 20

# RSI
RSI_PERIOD = 14

# Divergence pivots
PIVOT_LEFT = 2
PIVOT_RIGHT = 2
MAX_PIVOT_GAP = 60

MIN_RSI_DIFFERENCE = 2.0
MIN_PRICE_DIFFERENCE_PERCENT = 0.10

# SL / TP
SL_BUFFER_PERCENT = 0.10
MIN_TP_DISTANCE_PERCENT = 0.30

# UT Bot
UT_KEY_VALUE = 3.0
UT_ATR_PERIOD = 10

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "CryptoDivergenceScanner/10.0",
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
        "trades": []
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
            data["trades"] = []

        return data

    except Exception:
        return default_state()


def save_state(state):

    temp_file = STATE_FILE + ".tmp"

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

    os.replace(temp_file, STATE_FILE)


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

            # We need USD crypto futures.
            if quote != "USD":
                continue

            # Prefer perpetual/flexible contracts.
            score = 0

            if symbol.startswith("PF_"):
                score += 100

            if instrument_type == "flexible_futures":
                score += 80

            if instrument_type == "futures_inverse":
                score += 50

            # XBT is Kraken's BTC symbol.
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
                result[normalized_base] = candidate

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

    coin = coin.upper()

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

def get_candles(symbol):

    futures_symbol = get_market_symbol(symbol)

    url = (
        f"{BASE_URL}/trade/"
        f"{futures_symbol}/"
        f"{TIMEFRAME}"
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
            f"No candles returned for {futures_symbol}"
        )

    rows = []

    for c in candles:

        try:

            if isinstance(c, dict):

                timestamp = c.get("time")
                open_price = c.get("open")
                high_price = c.get("high")
                low_price = c.get("low")
                close_price = c.get("close")
                volume = c.get("volume", 0)

            elif isinstance(c, (list, tuple)):

                # Defensive support for alternate API formats.
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
            f"Could not parse candles for {futures_symbol}"
        )

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    # ========================================================
    # REMOVE CURRENT INCOMPLETE 5M CANDLE
    # ========================================================

    now_ms = int(
        time.time() * 1000
    )

    candle_ms = 5 * 60 * 1000

    if len(df) > 0:

        last_time = int(
            df.iloc[-1]["time"]
        )

        if last_time + candle_ms > now_ms:

            df = df.iloc[:-1].copy()

    if len(df) < 50:
        raise RuntimeError(
            f"Not enough closed candles for {symbol}"
        )

    return df


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

    # Bullish:
    # price makes lower low
    # RSI makes higher low

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

    # Bearish:
    # price makes higher high
    # RSI makes lower high

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

    previous_close = close.shift(1)

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

    return min(candidates)


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

    return max(candidates)


# ============================================================
# SIGNAL REGISTRATION
# ============================================================

def register_signal(
    state,
    signal
):

    symbol = signal["symbol"]

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:

        for trade in state["trades"]:

            if (
                trade.get("symbol")
                == symbol
                and trade.get("status")
                == "OPEN"
            ):

                return False

    signal_id = signal["signal_id"]

    for trade in state["trades"]:

        if trade.get(
            "signal_id"
        ) == signal_id:

            return False

    state["trades"].append(
        signal
    )

    return True


# ============================================================
# EVALUATE OPEN TRADES
# ============================================================

def evaluate_open_trades(
    state,
    data_cache
):

    changed = False

    for trade in state["trades"]:

        if trade.get(
            "status"
        ) != "OPEN":

            continue

        symbol = trade["symbol"]

        df = data_cache.get(
            symbol
        )

        if df is None or df.empty:
            continue

        side = trade["side"]

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp1 = trade.get(
            "tp1"
        )

        tp2 = trade.get(
            "tp2"
        )

        tp3 = trade.get(
            "tp3"
        )

        highs = df["high"]
        lows = df["low"]

        signal_time = int(
            trade["signal_time"]
        )

        after_signal = df[
            df["time"] > signal_time
        ]

        if after_signal.empty:
            continue

        exit_reason = None
        exit_price = None

        # ====================================================
        # LONG
        # ====================================================

        if side == "BUY":

            for _, row in after_signal.iterrows():

                candle_high = float(
                    row["high"]
                )

                candle_low = float(
                    row["low"]
                )

                # SL first for conservative evaluation.
                if candle_low <= sl:

                    exit_reason = "SL"
                    exit_price = sl
                    break

                if (
                    tp1 is not None
                    and candle_high >= float(tp1)
                ):

                    exit_reason = "TP1"
                    exit_price = float(tp1)
                    break

                if (
                    tp2 is not None
                    and candle_high >= float(tp2)
                ):

                    exit_reason = "TP2"
                    exit_price = float(tp2)
                    break

                if (
                    tp3 is not None
                    and candle_high >= float(tp3)
                ):

                    exit_reason = "TP3"
                    exit_price = float(tp3)
                    break

        # ====================================================
        # SHORT
        # ====================================================

        elif side == "SELL":

            for _, row in after_signal.iterrows():

                candle_high = float(
                    row["high"]
                )

                candle_low = float(
                    row["low"]
                )

                # SL first for conservative evaluation.
                if candle_high >= sl:

                    exit_reason = "SL"
                    exit_price = sl
                    break

                if (
                    tp1 is not None
                    and candle_low <= float(tp1)
                ):

                    exit_reason = "TP1"
                    exit_price = float(tp1)
                    break

                if (
                    tp2 is not None
                    and candle_low <= float(tp2)
                ):

                    exit_reason = "TP2"
                    exit_price = float(tp2)
                    break

                if (
                    tp3 is not None
                    and candle_low <= float(tp3)
                ):

                    exit_reason = "TP3"
                    exit_price = float(tp3)
                    break

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

            trade["exit_price"] = (
                exit_price
            )

            trade["exit_time"] = int(
                after_signal.iloc[0]["time"]
            )

            trade["pnl_percent"] = (
                pnl_percent
            )

            trade["r_multiple"] = (
                r_multiple
            )

            changed = True

    return changed


# ============================================================
# OPEN PERFORMANCE
# ============================================================

def calculate_open_performance(
    state,
    data_cache
):

    result = []

    for trade in state["trades"]:

        if trade.get(
            "status"
        ) != "OPEN":

            continue

        symbol = trade["symbol"]

        df = data_cache.get(
            symbol
        )

        if df is None or df.empty:
            continue

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        side = trade["side"]

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

        signal_time = int(
            trade["signal_time"]
        )

        after_signal = df[
            df["time"] > signal_time
        ]

        if after_signal.empty:

            mfe = 0
            mae = 0

        else:

            if side == "BUY":

                best_price = float(
                    after_signal["high"].max()
                )

                worst_price = float(
                    after_signal["low"].min()
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
                    after_signal["low"].min()
                )

                worst_price = float(
                    after_signal["high"].max()
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

        duration_minutes = 0

        if after_signal.empty:

            last_time = (
                int(df["time"].iloc[-1])
            )

        else:

            last_time = (
                int(
                    after_signal[
                        "time"
                    ].iloc[-1]
                )
            )

        duration_minutes = max(
            0,
            (
                last_time
                - signal_time
            ) / 60000
        )

        result.append({
            "symbol": symbol,
            "side": side,
            "entry": entry,
            "sl": sl,
            "tp1": trade.get("tp1"),
            "tp2": trade.get("tp2"),
            "tp3": trade.get("tp3"),
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

    trades = state.get(
        "trades",
        []
    )

    total = len(trades)

    open_trades = [
        x for x in trades
        if x.get("status") == "OPEN"
    ]

    closed_trades = [
        x for x in trades
        if x.get("status") == "CLOSED"
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
# ANALYZE COIN
# ============================================================

def analyze_coin(
    symbol
):

    df = get_candles(
        symbol
    )

    # RSI
    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    # ATR
    df["atr"] = calculate_atr(
        df,
        UT_ATR_PERIOD
    )

    # UT Bot
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

    current_atr = float(
        df["atr"].iloc[-1]
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
    # ========================================================

    if (
        bullish_divergence is not None
        and (
            bullish_break
            or current_close > current_ut
        )
    ):

        support = nearest_support(
            df,
            current_close
        )

        if support is None:
            support = (
                current_close
                - current_atr
            )

        sl = (
            support
            * (
                1
                - SL_BUFFER_PERCENT
                / 100
            )
        )

        resistance = nearest_resistance(
            df,
            current_close
        )

        if resistance is None:
            resistance = (
                current_close
                + current_atr * 2
            )

        tp1 = resistance

        if (
            tp1 - current_close
        ) / current_close * 100 < MIN_TP_DISTANCE_PERCENT:

            tp1 = (
                current_close
                + current_atr
            )

        tp2 = (
            current_close
            + (
                tp1
                - current_close
            ) * 2
        )

        tp3 = (
            current_close
            + (
                tp1
                - current_close
            ) * 3
        )

        signal = {
            "symbol": symbol,
            "side": "BUY",
            "entry": current_close,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "signal_time": signal_time,
            "signal_time_iso": datetime.fromtimestamp(
                signal_time / 1000,
                tz=timezone.utc
            ).isoformat(),
            "reason": "Bullish RSI Divergence + UT/Trendline",
        }

    # ========================================================
    # SELL
    # ========================================================

    elif (
        bearish_divergence is not None
        and (
            bearish_break
            or current_close < current_ut
        )
    ):

        resistance = nearest_resistance(
            df,
            current_close
        )

        if resistance is None:
            resistance = (
                current_close
                + current_atr
            )

        sl = (
            resistance
            * (
                1
                + SL_BUFFER_PERCENT
                / 100
            )
        )

        support = nearest_support(
            df,
            current_close
        )

        if support is None:
            support = (
                current_close
                - current_atr * 2
            )

        tp1 = support

        if (
            current_close - tp1
        ) / current_close * 100 < MIN_TP_DISTANCE_PERCENT:

            tp1 = (
                current_close
                - current_atr
            )

        tp2 = (
            current_close
            - (
                current_close
                - tp1
            ) * 2
        )

        tp3 = (
            current_close
            - (
                current_close
                - tp1
            ) * 3
        )

        signal = {
            "symbol": symbol,
            "side": "SELL",
            "entry": current_close,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "signal_time": signal_time,
            "signal_time_iso": datetime.fromtimestamp(
                signal_time / 1000,
                tz=timezone.utc
            ).isoformat(),
            "reason": "Bearish RSI Divergence + UT/Trendline",
        }

    return {
        "symbol": symbol,
        "df": df,
        "signal": signal,
        "market_symbol": get_market_symbol(symbol),
    }


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal
):

    side = signal["side"]

    emoji = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    text = []

    text.append(
        f"{emoji} {signal['symbol']}/USDT - {side}"
    )

    text.append(
        f"Entry: {format_price(signal['entry'])}"
    )

    text.append(
        f"Stop Loss: {format_price(signal['sl'])}"
    )

    text.append(
        f"Target 1: {format_price(signal['tp1'])}"
    )

    text.append(
        f"Target 2: {format_price(signal['tp2'])}"
    )

    text.append(
        f"Target 3: {format_price(signal['tp3'])}"
    )

    text.append(
        f"Reason: {signal['reason']}"
    )

    return "\n".join(text)


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    state,
    results,
    errors,
    open_performance
):

    stats = calculate_statistics(
        state
    )

    lines = []

    lines.append(
        "📡 CRYPTO DIVERGENCE SCANNER v10.0"
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
    # NEW SIGNALS
    # ========================================================

    new_signals = [
        x["signal"]
        for x in results
        if x.get("signal") is not None
    ]

    if new_signals:

        lines.append(
            ""
        )

        lines.append(
            "🚨 NEW SIGNALS"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for signal in new_signals:

            lines.append(
                format_signal(signal)
            )

            lines.append(
                "━━━━━━━━━━━━━━━━━━"
            )

    else:

        lines.append(
            ""
        )

        lines.append(
            "🚨 NEW SIGNALS"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "فعلاً سیگنال جدیدی وجود ندارد."
        )

    # ========================================================
    # OPEN SIGNAL PERFORMANCE
    # ========================================================

    lines.append(
        ""
    )

    lines.append(
        "📌 OPEN SIGNAL P&L"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    if open_performance:

        for item in open_performance:

            pnl = item["pnl_percent"]
            current_r = item["current_r"]
            mfe = item["mfe"]
            mae = item["mae"]

            pnl_icon = (
                "🟢"
                if pnl >= 0
                else "🔴"
            )

            lines.append(
                f"{pnl_icon} {item['symbol']} {item['side']}"
            )

            lines.append(
                f"Entry: {format_price(item['entry'])}"
            )

            lines.append(
                f"Current: {format_price(item['current_price'])}"
            )

            lines.append(
                f"SL: {format_price(item['sl'])}"
            )

            lines.append(
                f"Current P&L: {pnl:+.2f}%"
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

        lines.append(
            ""
        )

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

    state = load_state()

    results = []
    errors = {}

    data_cache = {}

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

        signal = result.get(
            "signal"
        )

        if signal is None:
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

        # Store as OPEN trade.
        trade = dict(signal)

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
        open_performance
    )

    print()
    print(report)

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
