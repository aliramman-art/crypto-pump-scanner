
# ============================================================
# CRYPTO PRICE ACTION SCANNER v2.0
# ============================================================
# Kraken Futures
# TOP 30 high-volume USDT contracts
# 5M CLOSED CANDLES
#
# PRICE ACTION:
#   - Market Structure
#   - BOS
#   - Pullback
#   - Engulfing
#   - Momentum
#   - Volume confirmation
#
# RULES:
#   MIN SCORE      = 80
#   MAX OPEN       = 1
#   RR             = 1:1
#   SIGNAL         = CLOSED 5M CANDLE
#
# STATE:
#   price_action_state.json
#   price_action_trade_history.json
#
# IMPORTANT:
#   This file is designed to work with a GitHub Actions workflow
#   that commits BOTH state files after every execution.
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone, timedelta

import ccxt
import pandas as pd
import requests


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "5m"
CANDLE_LIMIT = 120

TOP_N = 30

MIN_SCORE = 80
MAX_OPEN_TRADES = 1

RR = 1.0

# ATR
ATR_PERIOD = 14

# Structure
SWING_LOOKBACK = 5

# Volume
VOLUME_LOOKBACK = 20
VOLUME_MIN_RATIO = 1.15

# Momentum
BODY_MIN_RATIO = 0.45

# Risk
MIN_SL_ATR = 0.35
MAX_SL_ATR = 3.0

# Cooldown in number of candles
SIGNAL_COOLDOWN_CANDLES = 3

# Files
STATE_FILE = "price_action_state.json"
HISTORY_FILE = "price_action_trade_history.json"

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Timezone Tehran
TEHRAN_TZ = timezone(timedelta(hours=3, minutes=30))


# ============================================================
# JSON HELPERS
# ============================================================

def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception as e:
        print(f"[WARN] Cannot load {filename}: {e}")
        return default


def save_json(filename, data):
    temp_file = filename + ".tmp"

    with open(temp_file, "w", encoding="utf-8") as f:
        json.dump(
            data,
            f,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temp_file, filename)


# ============================================================
# STATE
# ============================================================

state = load_json(
    STATE_FILE,
    {
        "open": {},
        "last_signals": {}
    }
)

history = load_json(
    HISTORY_FILE,
    []
)

if not isinstance(state, dict):
    state = {
        "open": {},
        "last_signals": {}
    }

if "open" not in state:
    state["open"] = {}

if "last_signals" not in state:
    state["last_signals"] = {}

if not isinstance(history, list):
    history = []


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("[INFO] Telegram credentials not configured.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    }

    try:
        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.ok:
            return True

        print(
            "[WARN] Telegram error:",
            response.status_code,
            response.text[:500]
        )

    except Exception as e:
        print("[WARN] Telegram exception:", e)

    return False


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_tehran():
    return now_utc().astimezone(TEHRAN_TZ)


def jalali_date(dt):
    """
    Simple Gregorian -> Jalali conversion.
    """

    gy = dt.year
    gm = dt.month
    gd = dt.day

    g_days_in_month = [
        31, 28, 31, 30, 31, 30,
        31, 31, 30, 31, 30, 31
    ]

    if gy % 4 == 0 and (
        gy % 100 != 0 or gy % 400 == 0
    ):
        g_days_in_month[1] = 29

    if gm > 2:
        gy2 = gy + 1
    else:
        gy2 = gy

    days = (
        355666
        + (365 * gy)
        + ((gy2 + 3) // 4)
        - ((gy2 + 99) // 100)
        + ((gy2 + 399) // 400)
        + gd
    )

    jy = -1595 + 33 * (days // 12053)
    days %= 12053

    jy += 4 * (days // 1461)
    days %= 1461

    if days > 365:
        jy += (days - 1) // 365
        days = (days - 1) % 365

    if days < 186:
        jm = 1 + days // 31
        jd = 1 + days % 31
    else:
        jm = 7 + (days - 186) // 30
        jd = 1 + (days - 186) % 30

    return jy, jm, jd


def tehran_string(dt=None):
    if dt is None:
        dt = now_tehran()

    jy, jm, jd = jalali_date(dt)

    return (
        f"{jy:04d}/{jm:02d}/{jd:02d} "
        f"{dt.hour:02d}:{dt.minute:02d}:{dt.second:02d}"
    )


# ============================================================
# NUMBER HELPERS
# ============================================================

def safe_float(value, default=0.0):
    try:
        value = float(value)

        if math.isnan(value) or math.isinf(value):
            return default

        return value

    except Exception:
        return default


def pct(a, b):
    if b == 0:
        return 0.0

    return ((a - b) / b) * 100.0


def round_price(value):
    value = safe_float(value)

    if value >= 1000:
        return round(value, 2)

    if value >= 100:
        return round(value, 3)

    if value >= 10:
        return round(value, 4)

    if value >= 1:
        return round(value, 5)

    if value >= 0.1:
        return round(value, 6)

    if value >= 0.01:
        return round(value, 7)

    return round(value, 8)


# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
    "timeout": 20000
})


# ============================================================
# SYMBOL
# ============================================================

def clean_symbol(symbol):
    symbol = str(symbol)

    symbol = symbol.replace(":USDT", "")
    symbol = symbol.replace(":USD", "")

    if symbol.endswith("/USD"):
        symbol = symbol[:-4] + "/USDT"

    return symbol


# ============================================================
# FETCH MARKETS
# ============================================================

def get_usdt_markets():
    markets = exchange.load_markets()

    result = []

    for symbol, market in markets.items():

        try:
            if not market.get("active", True):
                continue

            if market.get("contract") is not True:
                continue

            if market.get("linear") is not True:
                continue

            quote = market.get("quote")

            if quote != "USDT":
                continue

            result.append(market)

        except Exception:
            continue

    return result


# ============================================================
# TOP VOLUME
# ============================================================

def get_top_symbols():
    markets = get_usdt_markets()

    print(
        f"[INFO] Found {len(markets)} active USDT futures markets."
    )

    tickers = {}

    try:
        tickers = exchange.fetch_tickers()
    except Exception as e:
        print("[WARN] fetch_tickers failed:", e)

    rows = []

    for market in markets:

        symbol = market.get("symbol")

        if symbol not in tickers:
            continue

        ticker = tickers[symbol]

        quote_volume = safe_float(
            ticker.get("quoteVolume")
        )

        base_volume = safe_float(
            ticker.get("baseVolume")
        )

        last = safe_float(
            ticker.get("last")
        )

        if quote_volume <= 0:
            quote_volume = base_volume * last

        if quote_volume <= 0:
            continue

        rows.append(
            (
                symbol,
                quote_volume
            )
        )

    rows.sort(
        key=lambda x: x[1],
        reverse=True
    )

    symbols = [
        symbol
        for symbol, _ in rows[:TOP_N]
    ]

    print(
        "[INFO] TOP symbols:",
        ", ".join(symbols)
    )

    return symbols


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol):
    try:
        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=TIMEFRAME,
            limit=CANDLE_LIMIT
        )

        if not data or len(data) < 60:
            return None

        df = pd.DataFrame(
            data,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

        numeric_cols = [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

        for col in numeric_cols:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df.dropna(
            subset=numeric_cols,
            inplace=True
        )

        if len(df) < 60:
            return None

        return df.reset_index(drop=True)

    except Exception as e:
        print(
            f"[WARN] OHLCV failed {symbol}: {e}"
        )

        return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=ATR_PERIOD):
    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - previous_close).abs()
    tr3 = (low - previous_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(
        period
    ).mean()

    return atr


# ============================================================
# CANDLE HELPERS
# ============================================================

def candle_body(row):
    return abs(
        safe_float(row["close"])
        - safe_float(row["open"])
    )


def candle_range(row):
    return (
        safe_float(row["high"])
        - safe_float(row["low"])
    )


def is_bullish(row):
    return row["close"] > row["open"]


def is_bearish(row):
    return row["close"] < row["open"]


def body_ratio(row):
    r = candle_range(row)

    if r <= 0:
        return 0

    return candle_body(row) / r


# ============================================================
# STRUCTURE
# ============================================================

def detect_structure(df):

    last = df.iloc[-1]

    previous = df.iloc[-2]

    lookback = df.iloc[
        -(SWING_LOOKBACK + 1):-1
    ]

    recent_high = safe_float(
        lookback["high"].max()
    )

    recent_low = safe_float(
        lookback["low"].min()
    )

    previous_high = safe_float(
        df["high"].iloc[-SWING_LOOKBACK-1:-1].max()
    )

    previous_low = safe_float(
        df["low"].iloc[-SWING_LOOKBACK-1:-1].min()
    )

    close = safe_float(last["close"])

    bullish_bos = close > recent_high
    bearish_bos = close < recent_low

    # Local directional structure
    last_high = safe_float(last["high"])
    last_low = safe_float(last["low"])

    structure = "RANGE"

    if bullish_bos:
        structure = "BULLISH"

    elif bearish_bos:
        structure = "BEARISH"

    elif (
        close > previous["close"]
        and last_low >= previous["low"]
    ):
        structure = "BULLISH"

    elif (
        close < previous["close"]
        and last_high <= previous["high"]
    ):
        structure = "BEARISH"

    return {
        "structure": structure,
        "bullish_bos": bool(bullish_bos),
        "bearish_bos": bool(bearish_bos),
        "recent_high": recent_high,
        "recent_low": recent_low,
        "previous_high": previous_high,
        "previous_low": previous_low,
        "last_close": close
    }


# ============================================================
# PULLBACK
# ============================================================

def detect_pullback(df, direction):

    last = df.iloc[-1]

    recent = df.iloc[-6:-1]

    close = safe_float(last["close"])

    recent_high = safe_float(
        recent["high"].max()
    )

    recent_low = safe_float(
        recent["low"].min()
    )

    if direction == "BUY":

        touched_support = (
            safe_float(last["low"])
            <= recent_high
        )

        recovering = (
            close > safe_float(last["open"])
        )

        return touched_support and recovering

    if direction == "SELL":

        touched_resistance = (
            safe_float(last["high"])
            >= recent_low
        )

        rejecting = (
            close < safe_float(last["open"])
        )

        return touched_resistance and rejecting

    return False


# ============================================================
# ENGULFING
# ============================================================

def detect_engulfing(df):

    prev = df.iloc[-2]
    last = df.iloc[-1]

    prev_open = safe_float(prev["open"])
    prev_close = safe_float(prev["close"])

    last_open = safe_float(last["open"])
    last_close = safe_float(last["close"])

    bullish = (
        prev_close < prev_open
        and last_close > last_open
        and last_open <= prev_close
        and last_close >= prev_open
    )

    bearish = (
        prev_close > prev_open
        and last_close < last_open
        and last_open >= prev_close
        and last_close <= prev_open
    )

    if bullish:
        return "BULLISH ENGULFING"

    if bearish:
        return "BEARISH ENGULFING"

    return None


# ============================================================
# MOMENTUM
# ============================================================

def momentum_signal(df):

    last = df.iloc[-1]

    ratio = body_ratio(last)

    if ratio < BODY_MIN_RATIO:
        return None

    if is_bullish(last):
        return "BULLISH MOMENTUM"

    if is_bearish(last):
        return "BEARISH MOMENTUM"

    return None


# ============================================================
# VOLUME
# ============================================================

def volume_confirmation(df):

    if len(df) < VOLUME_LOOKBACK + 2:
        return False, 0.0

    last_volume = safe_float(
        df["volume"].iloc[-1]
    )

    average_volume = safe_float(
        df["volume"]
        .iloc[-VOLUME_LOOKBACK-1:-1]
        .mean()
    )

    if average_volume <= 0:
        return False, 0.0

    ratio = (
        last_volume
        / average_volume
    )

    return (
        ratio >= VOLUME_MIN_RATIO,
        ratio
    )


# ============================================================
# SIGNAL SCORE
# ============================================================

def calculate_signal(df):

    structure = detect_structure(df)

    engulfing = detect_engulfing(df)

    momentum = momentum_signal(df)

    volume_ok, volume_ratio = (
        volume_confirmation(df)
    )

    last = df.iloc[-1]

    atr = safe_float(
        df["atr"].iloc[-1]
    )

    close = safe_float(
        last["close"]
    )

    score_buy = 0
    score_sell = 0

    buy_reasons = []
    sell_reasons = []

    # --------------------------------------------------------
    # STRUCTURE
    # --------------------------------------------------------

    if structure["structure"] == "BULLISH":
        score_buy += 25
        buy_reasons.append(
            "Bullish Structure"
        )

    if structure["structure"] == "BEARISH":
        score_sell += 25
        sell_reasons.append(
            "Bearish Structure"
        )

    # --------------------------------------------------------
    # BOS
    # --------------------------------------------------------

    if structure["bullish_bos"]:
        score_buy += 25
        buy_reasons.append("BOS")

    if structure["bearish_bos"]:
        score_sell += 25
        sell_reasons.append("BOS")

    # --------------------------------------------------------
    # PULLBACK
    # --------------------------------------------------------

    if detect_pullback(df, "BUY"):
        score_buy += 15
        buy_reasons.append("Pullback")

    if detect_pullback(df, "SELL"):
        score_sell += 15
        sell_reasons.append("Pullback")

    # --------------------------------------------------------
    # ENGULFING
    # --------------------------------------------------------

    if engulfing == "BULLISH ENGULFING":
        score_buy += 20
        buy_reasons.append(
            "BULLISH ENGULFING"
        )

    if engulfing == "BEARISH ENGULFING":
        score_sell += 20
        sell_reasons.append(
            "BEARISH ENGULFING"
        )

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    if momentum == "BULLISH MOMENTUM":
        score_buy += 10
        buy_reasons.append(
            "Bullish Momentum"
        )

    if momentum == "BEARISH MOMENTUM":
        score_sell += 10
        sell_reasons.append(
            "Bearish Momentum"
        )

    # --------------------------------------------------------
    # VOLUME
    # --------------------------------------------------------

    if volume_ok:

        if score_buy > score_sell:
            score_buy += 5
            buy_reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

        elif score_sell > score_buy:
            score_sell += 5
            sell_reasons.append(
                f"Volume {volume_ratio:.1f}x"
            )

    # --------------------------------------------------------
    # DECISION
    # --------------------------------------------------------

    if score_buy >= MIN_SCORE and score_buy > score_sell:

        return {
            "side": "BUY",
            "score": min(score_buy, 100),
            "reasons": buy_reasons,
            "atr": atr,
            "close": close,
            "structure": structure
        }

    if score_sell >= MIN_SCORE and score_sell > score_buy:

        return {
            "side": "SELL",
            "score": min(score_sell, 100),
            "reasons": sell_reasons,
            "atr": atr,
            "close": close,
            "structure": structure
        }

    return None


# ============================================================
# RISK / SL / TP
# ============================================================

def build_trade(symbol, signal, df):

    side = signal["side"]

    entry = safe_float(
        signal["close"]
    )

    atr = safe_float(
        signal["atr"]
    )

    if entry <= 0 or atr <= 0:
        return None

    structure = signal["structure"]

    if side == "BUY":

        structural_sl = safe_float(
            structure["recent_low"]
        )

        atr_sl = entry - (
            atr * 1.0
        )

        if structural_sl > 0:
            sl = min(
                structural_sl,
                atr_sl
            )
        else:
            sl = atr_sl

        risk = entry - sl

        if risk <= 0:
            return None

        if risk < atr * MIN_SL_ATR:
            risk = atr * MIN_SL_ATR
            sl = entry - risk

        if risk > atr * MAX_SL_ATR:
            risk = atr * MAX_SL_ATR
            sl = entry - risk

        tp = entry + risk

    else:

        structural_sl = safe_float(
            structure["recent_high"]
        )

        atr_sl = entry + (
            atr * 1.0
        )

        if structural_sl > 0:
            sl = max(
                structural_sl,
                atr_sl
            )
        else:
            sl = atr_sl

        risk = sl - entry

        if risk <= 0:
            return None

        if risk < atr * MIN_SL_ATR:
            risk = atr * MIN_SL_ATR
            sl = entry + risk

        if risk > atr * MAX_SL_ATR:
            risk = atr * MAX_SL_ATR
            sl = entry + risk

        tp = entry - risk

    risk_pct = abs(
        pct(entry, sl)
    )

    return {
        "trade_id": (
            f"{clean_symbol(symbol)}_"
            f"{int(time.time())}"
        ),
        "symbol": clean_symbol(symbol),
        "exchange_symbol": symbol,
        "side": side,
        "entry": round_price(entry),
        "sl": round_price(sl),
        "tp": round_price(tp),
        "risk_pct": round(risk_pct, 3),
        "score": int(signal["score"]),
        "reasons": signal["reasons"],
        "atr": round_price(atr),
        "opened_at": now_utc().isoformat(),
        "opened_at_tehran": tehran_string(),
        "entry_candle": (
            signal["structure"]
            .get("last_candle_time")
        )
    }


# ============================================================
# CURRENT MARKET PRICE
# ============================================================

def get_current_price(symbol):

    try:

        ticker = exchange.fetch_ticker(
            symbol
        )

        price = safe_float(
            ticker.get("last")
        )

        return price

    except Exception as e:

        print(
            f"[WARN] Price failed {symbol}: {e}"
        )

        return 0.0


# ============================================================
# OPEN TRADE UPDATE
# ============================================================

def update_open_trades(results):

    global state
    global history

    open_trades = state.get(
        "open",
        {}
    )

    if not open_trades:
        return []

    closed = []

    remaining = {}

    for trade_id, trade in open_trades.items():

        symbol = trade.get(
            "exchange_symbol"
        )

        result = results.get(symbol)

        # If symbol failed to scan, DO NOT delete trade.
        if not result:

            remaining[trade_id] = trade
            continue

        structure = result.get(
            "structure",
            {}
        )

        # IMPORTANT:
        # SL/TP is evaluated on CLOSED 5M candle.
        price = safe_float(
            structure.get("last_close")
        )

        if price <= 0:

            remaining[trade_id] = trade
            continue

        side = trade.get("side")

        entry = safe_float(
            trade.get("entry")
        )

        sl = safe_float(
            trade.get("sl")
        )

        tp = safe_float(
            trade.get("tp")
        )

        result_status = None
        exit_price = None
        r_multiple = 0.0

        # ----------------------------------------------------
        # BUY
        # ----------------------------------------------------

        if side == "BUY":

            if price <= sl:

                result_status = "LOSS"
                exit_price = sl
                r_multiple = -1.0

            elif price >= tp:

                result_status = "WIN"
                exit_price = tp
                r_multiple = RR

        # ----------------------------------------------------
        # SELL
        # ----------------------------------------------------

        elif side == "SELL":

            if price >= sl:

                result_status = "LOSS"
                exit_price = sl
                r_multiple = -1.0

            elif price <= tp:

                result_status = "WIN"
                exit_price = tp
                r_multiple = RR

        # ----------------------------------------------------
        # STILL OPEN
        # ----------------------------------------------------

        if result_status is None:

            remaining[trade_id] = trade
            continue

        exit_price = round_price(
            exit_price
        )

        if side == "BUY":

            pnl_pct = (
                (exit_price - entry)
                / entry
            ) * 100

        else:

            pnl_pct = (
                (entry - exit_price)
                / entry
            ) * 100

        closed_trade = dict(trade)

        closed_trade.update({
            "status": result_status,
            "exit": exit_price,
            "pnl_pct": round(
                pnl_pct,
                3
            ),
            "r": round(
                r_multiple,
                2
            ),
            "closed_at": now_utc().isoformat(),
            "closed_at_tehran": tehran_string()
        })

        history.append(
            closed_trade
        )

        closed.append(
            closed_trade
        )

        print(
            f"[CLOSED] "
            f"{trade['symbol']} "
            f"{side} "
            f"{result_status} "
            f"Exit={exit_price}"
        )

    state["open"] = remaining

    return closed


# ============================================================
# COOLDOWN
# ============================================================

def candle_index_from_time(df, candle_time):
    try:

        timestamps = (
            df["timestamp"]
            .astype("int64")
            .tolist()
        )

        if candle_time in timestamps:
            return timestamps.index(
                candle_time
            )

    except Exception:
        pass

    return None


def is_in_cooldown(symbol, current_timestamp):

    last_signal = state[
        "last_signals"
    ].get(symbol)

    if not last_signal:
        return False

    last_ts = safe_float(
        last_signal.get("timestamp")
    )

    if last_ts <= 0:
        return False

    candle_ms = 5 * 60 * 1000

    elapsed_candles = (
        current_timestamp - last_ts
    ) / candle_ms

    return (
        elapsed_candles
        < SIGNAL_COOLDOWN_CANDLES
    )


# ============================================================
# CREATE SIGNAL
# ============================================================

def create_signal(symbol, result):

    global state

    if len(state["open"]) >= MAX_OPEN_TRADES:

        print(
            "[INFO] MAX_OPEN_TRADES reached."
        )

        return None

    side = result["signal"]["side"]

    score = result["signal"]["score"]

    if score < MIN_SCORE:
        return None

    candle_timestamp = safe_float(
        result["structure"]
        .get("last_candle_timestamp")
    )

    if candle_timestamp <= 0:
        candle_timestamp = (
            result["df_timestamp"]
        )

    if is_in_cooldown(
        symbol,
        candle_timestamp
    ):

        print(
            f"[COOLDOWN] {clean_symbol(symbol)}"
        )

        return None

    trade = build_trade(
        symbol,
        result["signal"],
        result["df"]
    )

    if not trade:
        return None

    state["open"][
        trade["trade_id"]
    ] = trade

    state["last_signals"][
        symbol
    ] = {
        "timestamp": candle_timestamp,
        "side": side,
        "score": score
    }

    return trade


# ============================================================
# PERFORMANCE
# ============================================================

def performance():

    trades = len(history)

    wins = sum(
        1
        for x in history
        if x.get("status") == "WIN"
    )

    losses = sum(
        1
        for x in history
        if x.get("status") == "LOSS"
    )

    neutral = sum(
        1
        for x in history
        if x.get("status") not in (
            "WIN",
            "LOSS"
        )
    )

    if trades > 0:

        wr = (
            wins / trades
        ) * 100

    else:

        wr = 0.0

    total_r = sum(
        safe_float(
            x.get("r")
        )
        for x in history
    )

    return {
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "neutral": neutral,
        "wr": wr,
        "total_r": total_r
    }


# ============================================================
# OPEN TRADE INFO
# ============================================================

def open_trade_report():

    open_trades = state.get(
        "open",
        {}
    )

    if not open_trades:
        return (
            "📭 **OPEN TRADE**\n"
            "None"
        )

    lines = [
        "📌 **OPEN TRADE**"
    ]

    for trade in open_trades.values():

        symbol = trade.get(
            "symbol",
            "?"
        )

        side = trade.get(
            "side",
            "?"
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

        score = trade.get(
            "score",
            0
        )

        current = get_current_price(
            trade.get(
                "exchange_symbol"
            )
        )

        if current > 0:

            if side == "BUY":

                live_pnl = (
                    (current - entry)
                    / entry
                ) * 100

            else:

                live_pnl = (
                    (entry - current)
                    / entry
                ) * 100

        else:

            live_pnl = 0.0

        try:

            opened = datetime.fromisoformat(
                trade["opened_at"]
            )

            duration = (
                now_utc() - opened
            )

            minutes = int(
                duration.total_seconds()
                / 60
            )

        except Exception:

            minutes = 0

        emoji = (
            "🟢"
            if live_pnl >= 0
            else "🔴"
        )

        lines.extend([
            "",
            f"**{symbol} {side}**",
            f"Entry: `{entry}`",
            f"SL: `{sl}`",
            f"TP: `{tp}`",
            f"Current: `{round_price(current)}`",
            (
                f"{emoji} Live P&L: "
                f"`{live_pnl:+.2f}%`"
            ),
            f"Score: `{score}/100`",
            f"Duration: `{minutes}m`"
        ])

    return "\n".join(lines)


# ============================================================
# CLOSED TRADE REPORT
# ============================================================

def closed_report(closed):

    if not closed:
        return ""

    lines = [
        "━━━━━━━━━━━━━━━━━━",
        "📕 **CLOSED THIS RUN**"
    ]

    for trade in closed:

        symbol = trade.get(
            "symbol",
            "?"
        )

        side = trade.get(
            "side",
            "?"
        )

        status = trade.get(
            "status",
            "?"
        )

        exit_price = trade.get(
            "exit",
            0
        )

        pnl = safe_float(
            trade.get("pnl_pct")
        )

        if status == "WIN":
            emoji = "🟢"
        elif status == "LOSS":
            emoji = "🔴"
        else:
            emoji = "⚪"

        lines.extend([
            "",
            (
                f"{emoji} **{symbol} {side} "
                f"{status}**"
            ),
            f"Exit: `{exit_price}`",
            f"P&L: `{pnl:+.2f}%`",
            f"R: `{trade.get('r', 0):+.2f}`"
        ])

    return "\n".join(lines)


# ============================================================
# NEW SIGNAL REPORT
# ============================================================

def signal_report(trade):

    if not trade:
        return ""

    side = trade["side"]

    if side == "BUY":
        emoji = "🟢"
    else:
        emoji = "🔴"

    reasons = " + ".join(
        trade.get(
            "reasons",
            []
        )
    )

    return "\n".join([
        "━━━━━━━━━━━━━━━━━━",
        "🚨 **NEW SIGNAL**",
        "",
        (
            f"{emoji} **"
            f"{trade['symbol']} - "
            f"{side}**"
        ),
        "",
        f"Entry: `{trade['entry']}`",
        (
            f"Stop Loss: `{trade['sl']}` "
            f"({-trade['risk_pct']:.2f}%)"
        ),
        (
            f"Target: `{trade['tp']}` "
            f"(+{trade['risk_pct']:.2f}%)"
        ),
        "",
        (
            f"Score: "
            f"`{trade['score']}/100`"
        ),
        "",
        f"Reasons: `{reasons}`",
        "",
        "⚖️ **RR 1:1**"
    ])


# ============================================================
# FULL REPORT
# ============================================================

def build_report(
    scanned_count,
    events,
    new_trade,
    closed
):

    p = performance()

    open_count = len(
        state.get(
            "open",
            {}
        )
    )

    lines = [
        "📡 **CRYPTO PRICE ACTION REPORT**",
        "",
        f"🕐 {tehran_string()}",
        (
            f"⏱ **{TIMEFRAME} CLOSED | "
            f"TOP {TOP_N}**"
        ),
        "🤖 **PRICE ACTION | RR 1:1**",
        f"🎯 **MIN SCORE: {MIN_SCORE}+**",
        f"🔒 **MAX OPEN: {MAX_OPEN_TRADES}**",
        "━━━━━━━━━━━━━━━━━━",
        "📊 **PERFORMANCE**",
        (
            f"Trades {p['trades']} | "
            f"🟢 {p['wins']} | "
            f"🔴 {p['losses']} | "
            f"⚪ {p['neutral']}"
        ),
        (
            f"🏆 WR: "
            f"{p['wr']:.1f}%"
        ),
        (
            f"📈 Total R: "
            f"{p['total_r']:+.2f}"
        ),
        "",
        (
            f"🔎 Scanned: "
            f"{scanned_count}"
        ),
        (
            f"⚡ THIS RUN: "
            f"{events} EVENTS"
        ),
        (
            f"📂 OPEN: "
            f"{open_count}/{MAX_OPEN_TRADES}"
        )
    ]

    if closed:
        lines.extend([
            "",
            closed_report(closed)
        ])

    if new_trade:
        lines.extend([
            "",
            signal_report(new_trade)
        ])

    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━",
        open_trade_report()
    ])

    return "\n".join(lines)


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def analyze_symbol(symbol):

    df = fetch_ohlcv(symbol)

    if df is None:
        return None

    # --------------------------------------------------------
    # CRITICAL:
    # The LAST candle may still be open.
    # We ALWAYS use the previous candle as the CLOSED candle.
    # --------------------------------------------------------

    if len(df) < 5:
        return None

    df = df.iloc[:-1].copy()

    if len(df) < 60:
        return None

    df["atr"] = calculate_atr(
        df
    )

    # Remove rows before ATR becomes valid
    df = df.dropna(
        subset=["atr"]
    ).reset_index(
        drop=True
    )

    if len(df) < 30:
        return None

    closed = df.iloc[-1]

    candle_timestamp = int(
        safe_float(
            closed["timestamp"]
        )
    )

    signal = calculate_signal(
        df
    )

    structure = detect_structure(
        df
    )

    structure[
        "last_candle_timestamp"
    ] = candle_timestamp

    structure[
        "last_candle_time"
    ] = datetime.fromtimestamp(
        candle_timestamp / 1000,
        tz=timezone.utc
    ).isoformat()

    return {
        "symbol": symbol,
        "df": df,
        "df_timestamp": candle_timestamp,
        "signal": signal,
        "structure": structure
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "CRYPTO PRICE ACTION SCANNER v2.0"
    )
    print("=" * 70)

    print(
        f"[INFO] Timeframe: {TIMEFRAME}"
    )

    print(
        f"[INFO] TOP N: {TOP_N}"
    )

    print(
        f"[INFO] MIN SCORE: {MIN_SCORE}"
    )

    print(
        f"[INFO] MAX OPEN: {MAX_OPEN_TRADES}"
    )

    print(
        f"[INFO] STATE FILE: {STATE_FILE}"
    )

    print(
        f"[INFO] HISTORY FILE: {HISTORY_FILE}"
    )

    # --------------------------------------------------------
    # TOP SYMBOLS
    # --------------------------------------------------------

    symbols = get_top_symbols()

    if not symbols:

        print(
            "[ERROR] No symbols found."
        )

        return

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    results = {}

    candidates = []

    scanned_count = 0

    for symbol in symbols:

        try:

            print(
                f"[SCAN] {clean_symbol(symbol)}"
            )

            result = analyze_symbol(
                symbol
            )

            if not result:
                continue

            results[symbol] = result

            scanned_count += 1

            signal = result.get(
                "signal"
            )

            if signal:

                print(
                    f"[SIGNAL] "
                    f"{clean_symbol(symbol)} "
                    f"{signal['side']} "
                    f"{signal['score']}/100"
                )

                candidates.append(
                    result
                )

        except Exception as e:

            print(
                f"[WARN] Analyze failed "
                f"{symbol}: {e}"
            )

        # Respect Kraken rate limits
        time.sleep(0.15)

    # --------------------------------------------------------
    # UPDATE EXISTING TRADE FIRST
    # --------------------------------------------------------

    print(
        "[INFO] Updating open trades..."
    )

    closed = update_open_trades(
        results
    )

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    new_trade = None

    if (
        len(state["open"])
        < MAX_OPEN_TRADES
    ):

        # Highest score first
        candidates.sort(
            key=lambda x: x["signal"]["score"],
            reverse=True
        )

        for candidate in candidates:

            symbol = candidate["symbol"]

            # Never create another trade
            # for a symbol that already has state.
            already_open = any(
                t.get(
                    "exchange_symbol"
                ) == symbol
                for t in state["open"].values()
            )

            if already_open:
                continue

            trade = create_signal(
                symbol,
                candidate
            )

            if trade:

                new_trade = trade

                print(
                    f"[OPEN] "
                    f"{trade['symbol']} "
                    f"{trade['side']} "
                    f"Score={trade['score']}"
                )

                break

    else:

        print(
            "[INFO] One trade already open."
        )

    # --------------------------------------------------------
    # SAVE STATE IMMEDIATELY
    # --------------------------------------------------------

    save_json(
        STATE_FILE,
        state
    )

    save_json(
        HISTORY_FILE,
        history
    )

    print(
        "[INFO] State saved."
    )

    print(
        f"[INFO] Open trades: "
        f"{len(state['open'])}"
    )

    print(
        f"[INFO] Closed trades: "
        f"{len(history)}"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    report = build_report(
        scanned_count=scanned_count,
        events=(
            len(closed)
            + (1 if new_trade else 0)
        ),
        new_trade=new_trade,
        closed=closed
    )

    print("")
    print("=" * 70)
    print(report)
    print("=" * 70)

    telegram_send(
        report
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        print(
            "[INFO] Interrupted."
        )

    except Exception as e:

        print(
            "[FATAL ERROR]",
            e
        )

        traceback.print_exc()

        # Important:
        # Do NOT destroy existing state if scanner crashes.
        try:
            save_json(
                STATE_FILE,
                state
            )

            save_json(
                HISTORY_FILE,
                history
            )

        except Exception as save_error:

            print(
                "[ERROR] Could not save state:",
                save_error
            )

        raise
