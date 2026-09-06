# ============================================================
# CRYPTO UT BOT SCANNER v13.0
# ============================================================
# Kraken Futures
# TOP 60 IMPORTANT / HIGH-VOLUME COINS
#
# STRATEGY:
#   1H  = MAIN TREND
#   15M = TREND CONFIRMATION
#   5M  = UT BOT ENTRY TRIGGER
#
# UT BOT:
#   Key Value = 3
#   ATR Period = 5
#
# ENTRY:
#   LONG:
#       1H bullish
#       15M bullish
#       5M UT BUY
#
#   SHORT:
#       1H bearish
#       15M bearish
#       5M UT SELL
#
# ENTRY CONFIRMATION:
#   Signal is generated on a CLOSED 5M candle.
#   Entry occurs at the CLOSE of that candle.
#
# SL:
#   LONG  -> latest confirmed swing low - 0.10%
#   SHORT -> latest confirmed swing high + 0.10%
#
# TP:
#   1R
#
# TELEGRAM:
#   EXACTLY ONE REPORT PER RUN
#
# STATE:
#   Open trades
#   Pending signals
#   Statistics
#   Processed signals
#
# ============================================================

import os
import json
import traceback
from datetime import datetime, timezone

import ccxt
import pandas as pd
import requests


# ============================================================
# CONFIG
# ============================================================

TOP_COINS = 60

UT_KEY = 3
UT_ATR_PERIOD = 5

RR = 1.0

SL_BUFFER_PERCENT = 0.10

SWING_LEFT = 2
SWING_RIGHT = 2

OHLCV_LIMIT = 500

STATE_FILE = "ut_bot_state.json"
HISTORY_FILE = "ut_bot_trade_history.json"

RESET_ON_START = False
RESET_MARKER_FILE = "ut_bot_reset_done.txt"

MAX_TELEGRAM_LENGTH = 3900

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True
})


# ============================================================
# RUN EVENTS
# ============================================================

RUN_EVENTS = []


# ============================================================
# DEFAULT STATE
# ============================================================

DEFAULT_STATE = {
    "open_trades": {},
    "pending_signals": {},
    "processed_signals": {},
    "statistics": {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "total_pnl": 0.0,
        "total_r": 0.0
    }
}


# ============================================================
# TIME
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def candle_time_to_string(timestamp_ms):
    dt = datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc
    )
    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def short_time(timestamp_ms):
    dt = datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc
    )
    return dt.strftime("%m-%d %H:%M")


def format_duration(start_timestamp_ms):
    if not start_timestamp_ms:
        return "-"

    now_ms = int(now_utc().timestamp() * 1000)
    seconds = max(0, (now_ms - start_timestamp_ms) / 1000)

    minutes = int(seconds // 60)
    hours = minutes // 60
    minutes = minutes % 60

    if hours > 0:
        return f"{hours}h {minutes}m"

    return f"{minutes}m"


# ============================================================
# STATE
# ============================================================

def fresh_state():
    return json.loads(json.dumps(DEFAULT_STATE))


def save_state(state):

    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(
            state,
            f,
            ensure_ascii=False,
            indent=2
        )


def save_history(history):

    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(
            history,
            f,
            ensure_ascii=False,
            indent=2
        )


def load_state():

    if not os.path.exists(STATE_FILE):
        return fresh_state()

    try:

        with open(STATE_FILE, "r", encoding="utf-8") as f:
            state = json.load(f)

        for key in DEFAULT_STATE:

            if key not in state:
                state[key] = DEFAULT_STATE[key]

        for key in DEFAULT_STATE["statistics"]:

            if key not in state["statistics"]:
                state["statistics"][key] = 0

        return state

    except Exception:

        return fresh_state()


def load_history():

    if not os.path.exists(HISTORY_FILE):
        return []

    try:

        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    except Exception:

        return []


def initialize():

    global RUN_EVENTS

    RUN_EVENTS = []

    if RESET_ON_START:

        if not os.path.exists(RESET_MARKER_FILE):

            save_state(fresh_state())
            save_history([])

            with open(
                RESET_MARKER_FILE,
                "w",
                encoding="utf-8"
            ) as f:
                f.write(iso_now())

    return load_state(), load_history()


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": text,
                "parse_mode": "Markdown"
            },
            timeout=20
        )

        return response.ok

    except Exception as e:

        print("Telegram error:", e)
        return False


# ============================================================
# DATA
# ============================================================

def fetch_ohlcv(symbol, timeframe):

    try:

        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=timeframe,
            limit=OHLCV_LIMIT
        )

        if not data:
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

        if len(df) < 50:
            return None

        return df

    except Exception as e:

        print(f"OHLCV error {symbol} {timeframe}: {e}")
        return None


def fetch_live_price(symbol):

    try:

        ticker = exchange.fetch_ticker(symbol)

        last = ticker.get("last")

        if last is not None:
            return float(last)

    except Exception:
        pass

    return None


# ============================================================
# ATR
# ============================================================

def calculate_true_range(df):

    prev_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]

    tr2 = (df["high"] - prev_close).abs()

    tr3 = (df["low"] - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    return tr


def calculate_atr(df, period):

    tr = calculate_true_range(df)

    atr = pd.Series(
        index=df.index,
        dtype=float
    )

    if len(tr) < period:
        return atr

    atr.iloc[period - 1] = tr.iloc[:period].mean()

    for i in range(period, len(tr)):

        atr.iloc[i] = (
            (
                atr.iloc[i - 1] * (period - 1)
            ) + tr.iloc[i]
        ) / period

    return atr


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(df):

    df = df.copy()

    src = df["close"]

    atr = calculate_atr(
        df,
        UT_ATR_PERIOD
    )

    nloss = UT_KEY * atr

    trailing_stop = pd.Series(
        index=df.index,
        dtype=float
    )

    direction = pd.Series(
        index=df.index,
        dtype=int
    )

    buy = pd.Series(
        False,
        index=df.index
    )

    sell = pd.Series(
        False,
        index=df.index
    )

    if len(df) == 0:
        return df

    trailing_stop.iloc[0] = src.iloc[0]
    direction.iloc[0] = 0

    for i in range(1, len(df)):

        prev_stop = trailing_stop.iloc[i - 1]
        prev_src = src.iloc[i - 1]

        current_src = src.iloc[i]
        current_loss = nloss.iloc[i]

        if pd.isna(current_loss):

            trailing_stop.iloc[i] = prev_stop
            direction.iloc[i] = direction.iloc[i - 1]

            continue

        if current_src > prev_stop and prev_src > prev_stop:

            trailing_stop.iloc[i] = max(
                prev_stop,
                current_src - current_loss
            )

        elif current_src < prev_stop and prev_src < prev_stop:

            trailing_stop.iloc[i] = min(
                prev_stop,
                current_src + current_loss
            )

        elif current_src > prev_stop:

            trailing_stop.iloc[i] = (
                current_src - current_loss
            )

        else:

            trailing_stop.iloc[i] = (
                current_src + current_loss
            )

        if current_src > trailing_stop.iloc[i]:

            direction.iloc[i] = 1

        elif current_src < trailing_stop.iloc[i]:

            direction.iloc[i] = -1

        else:

            direction.iloc[i] = direction.iloc[i - 1]

        previous_src = src.iloc[i - 1]

        previous_stop = trailing_stop.iloc[i - 1]

        current_stop = trailing_stop.iloc[i]

        buy.iloc[i] = (
            current_src > current_stop
            and previous_src <= previous_stop
        )

        sell.iloc[i] = (
            current_src < current_stop
            and previous_src >= previous_stop
        )

    df["atr"] = atr
    df["ut_stop"] = trailing_stop
    df["ut_direction"] = direction
    df["ut_buy"] = buy
    df["ut_sell"] = sell

    return df


# ============================================================
# TREND
# ============================================================

def determine_trend(df):

    """
    Simple closed-candle trend filter.

    BULLISH:
        close > EMA20 > EMA50

    BEARISH:
        close < EMA20 < EMA50

    Otherwise:
        NEUTRAL
    """

    if df is None or len(df) < 60:
        return "NEUTRAL"

    d = df.copy()

    d["ema20"] = d["close"].ewm(
        span=20,
        adjust=False
    ).mean()

    d["ema50"] = d["close"].ewm(
        span=50,
        adjust=False
    ).mean()

    i = len(d) - 2

    close = float(d["close"].iloc[i])
    ema20 = float(d["ema20"].iloc[i])
    ema50 = float(d["ema50"].iloc[i])

    if close > ema20 > ema50:
        return "BULLISH"

    if close < ema20 < ema50:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# SWINGS
# ============================================================

def find_swing_low(df, end_index):

    start = max(
        SWING_LEFT,
        0
    )

    end = min(
        end_index - SWING_RIGHT,
        len(df) - SWING_RIGHT - 1
    )

    if end < start:
        return None

    for i in range(end, start - 1, -1):

        value = df["low"].iloc[i]

        left = df["low"].iloc[
            i - SWING_LEFT:i
        ]

        right = df["low"].iloc[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if (
            len(left) == SWING_LEFT
            and len(right) == SWING_RIGHT
            and value < left.min()
            and value < right.min()
        ):
            return float(value)

    return None


def find_swing_high(df, end_index):

    start = max(
        SWING_LEFT,
        0
    )

    end = min(
        end_index - SWING_RIGHT,
        len(df) - SWING_RIGHT - 1
    )

    if end < start:
        return None

    for i in range(end, start - 1, -1):

        value = df["high"].iloc[i]

        left = df["high"].iloc[
            i - SWING_LEFT:i
        ]

        right = df["high"].iloc[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if (
            len(left) == SWING_LEFT
            and len(right) == SWING_RIGHT
            and value > left.max()
            and value > right.max()
        ):
            return float(value)

    return None


# ============================================================
# EVENT HELPERS
# ============================================================

def add_event(event):

    RUN_EVENTS.append(event)


# ============================================================
# SIGNAL EVENT
# ============================================================

def add_signal_event(
    symbol,
    side,
    price,
    timestamp
):

    emoji = "🟢" if side == "LONG" else "🔴"

    add_event(
        f"{emoji} {symbol} {side} "
        f"@ {price:.8g} "
        f"5M {short_time(timestamp)}"
    )


# ============================================================
# TRADE OPEN EVENT
# ============================================================

def add_trade_open_event(
    symbol,
    trade
):

    side = trade["side"]

    emoji = "🟢" if side == "LONG" else "🔴"

    add_event(
        f"{emoji} OPEN {symbol} {side} "
        f"Entry {trade['entry']:.8g} "
        f"SL {trade['sl']:.8g} "
        f"TP {trade['tp']:.8g}"
    )


# ============================================================
# TRADE EXIT EVENT
# ============================================================

def add_trade_exit_event(
    symbol,
    trade,
    result
):

    if result == "WIN":
        emoji = "🏆"
    elif result == "LOSS":
        emoji = "❌"
    else:
        emoji = "⚪"

    add_event(
        f"{emoji} CLOSE {symbol} "
        f"{trade['side']} "
        f"{result} "
        f"R {trade['r_result']:+.2f}"
    )


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    state,
    symbol,
    side,
    entry,
    sl,
    timestamp
):

    risk = abs(entry - sl)

    if risk <= 0:
        return False

    if side == "LONG":

        tp = entry + risk * RR

    else:

        tp = entry - risk * RR

    trade = {
        "symbol": symbol,
        "side": side,
        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),
        "risk": float(risk),
        "entry_time": int(timestamp),
        "entry_time_text": candle_time_to_string(timestamp),
        "r_result": 0.0,
        "status": "OPEN"
    }

    state["open_trades"][symbol] = trade

    add_trade_open_event(
        symbol,
        trade
    )

    return True


# ============================================================
# PROCESS PENDING SIGNAL
# ============================================================

def process_pending_signal(
    state,
    symbol,
    df5
):

    pending = state["pending_signals"].get(symbol)

    if not pending:
        return

    if len(df5) < 10:
        return

    i = len(df5) - 2

    candle_timestamp = int(
        df5["timestamp"].iloc[i]
    )

    if candle_timestamp <= int(
        pending["signal_timestamp"]
    ):
        return

    close = float(
        df5["close"].iloc[i]
    )

    signal_high = float(
        pending["signal_high"]
    )

    signal_low = float(
        pending["signal_low"]
    )

    side = pending["side"]

    # --------------------------------------------------------
    # CANCEL IF OPPOSITE UT SIGNAL
    # --------------------------------------------------------

    if side == "LONG" and bool(
        df5["ut_sell"].iloc[i]
    ):

        del state["pending_signals"][symbol]

        add_event(
            f"⚪ CANCEL {symbol} LONG "
            f"opposite UT signal"
        )

        return

    if side == "SHORT" and bool(
        df5["ut_buy"].iloc[i]
    ):

        del state["pending_signals"][symbol]

        add_event(
            f"⚪ CANCEL {symbol} SHORT "
            f"opposite UT signal"
        )

        return

    # --------------------------------------------------------
    # LONG CONFIRMATION
    # --------------------------------------------------------

    if side == "LONG":

        if close <= signal_high:
            return

        swing_low = find_swing_low(
            df5,
            i
        )

        if swing_low is None:
            return

        sl = swing_low * (
            1 - SL_BUFFER_PERCENT / 100
        )

        if sl >= close:
            return

        create_trade(
            state,
            symbol,
            "LONG",
            close,
            sl,
            candle_timestamp
        )

        del state["pending_signals"][symbol]

        return

    # --------------------------------------------------------
    # SHORT CONFIRMATION
    # --------------------------------------------------------

    if side == "SHORT":

        if close >= signal_low:
            return

        swing_high = find_swing_high(
            df5,
            i
        )

        if swing_high is None:
            return

        sl = swing_high * (
            1 + SL_BUFFER_PERCENT / 100
        )

        if sl <= close:
            return

        create_trade(
            state,
            symbol,
            "SHORT",
            close,
            sl,
            candle_timestamp
        )

        del state["pending_signals"][symbol]


# ============================================================
# DETECT NEW 5M UT SIGNAL
# ============================================================

def detect_new_ut_signal(
    state,
    symbol,
    df5,
    trend1h,
    trend15m
):

    if len(df5) < 20:
        return

    i = len(df5) - 2

    timestamp = int(
        df5["timestamp"].iloc[i]
    )

    signal_key = str(timestamp)

    previous_key = state[
        "processed_signals"
    ].get(symbol)

    if previous_key == signal_key:
        return

    # --------------------------------------------------------
    # Mark processed immediately
    # --------------------------------------------------------

    state[
        "processed_signals"
    ][symbol] = signal_key

    buy_signal = bool(
        df5["ut_buy"].iloc[i]
    )

    sell_signal = bool(
        df5["ut_sell"].iloc[i]
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if buy_signal:

        if (
            trend1h == "BULLISH"
            and trend15m == "BULLISH"
        ):

            price = float(
                df5["close"].iloc[i]
            )

            state["pending_signals"][symbol] = {
                "symbol": symbol,
                "side": "LONG",
                "signal_price": price,
                "signal_high": float(
                    df5["high"].iloc[i]
                ),
                "signal_low": float(
                    df5["low"].iloc[i]
                ),
                "signal_timestamp": timestamp
            }

            add_signal_event(
                symbol,
                "LONG",
                price,
                timestamp
            )

        else:

            add_event(
                f"⚪ {symbol} BUY filtered "
                f"(1H {trend1h} / 15M {trend15m})"
            )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif sell_signal:

        if (
            trend1h == "BEARISH"
            and trend15m == "BEARISH"
        ):

            price = float(
                df5["close"].iloc[i]
            )

            state["pending_signals"][symbol] = {
                "symbol": symbol,
                "side": "SHORT",
                "signal_price": price,
                "signal_high": float(
                    df5["high"].iloc[i]
                ),
                "signal_low": float(
                    df5["low"].iloc[i]
                ),
                "signal_timestamp": timestamp
            }

            add_signal_event(
                symbol,
                "SHORT",
                price,
                timestamp
            )

        else:

            add_event(
                f"⚪ {symbol} SELL filtered "
                f"(1H {trend1h} / 15M {trend15m})"
            )


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    state,
    history,
    symbol,
    df5
):

    trade = state["open_trades"].get(symbol)

    if not trade:
        return

    if len(df5) < 10:
        return

    i = len(df5) - 2

    candle_timestamp = int(
        df5["timestamp"].iloc[i]
    )

    if candle_timestamp <= int(
        trade["entry_time"]
    ):
        return

    high = float(
        df5["high"].iloc[i]
    )

    low = float(
        df5["low"].iloc[i]
    )

    result = None
    exit_price = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if trade["side"] == "LONG":

        # Conservative:
        # If both SL and TP are touched in same candle,
        # SL is assumed first.

        if low <= trade["sl"]:

            result = "LOSS"
            exit_price = trade["sl"]

        elif high >= trade["tp"]:

            result = "WIN"
            exit_price = trade["tp"]

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        if high >= trade["sl"]:

            result = "LOSS"
            exit_price = trade["sl"]

        elif low <= trade["tp"]:

            result = "WIN"
            exit_price = trade["tp"]

    if result is None:
        return

    if result == "WIN":

        r_result = RR

    elif result == "LOSS":

        r_result = -1.0

    else:

        r_result = 0.0

    if trade["side"] == "LONG":

        pnl_percent = (
            (exit_price - trade["entry"])
            / trade["entry"]
        ) * 100

    else:

        pnl_percent = (
            (trade["entry"] - exit_price)
            / trade["entry"]
        ) * 100

    trade["exit"] = float(exit_price)

    trade["exit_time"] = candle_timestamp

    trade["exit_time_text"] = candle_time_to_string(
        candle_timestamp
    )

    trade["result"] = result

    trade["r_result"] = float(r_result)

    trade["pnl_percent"] = float(pnl_percent)

    trade["status"] = "CLOSED"

    history.append(trade.copy())

    stats = state["statistics"]

    stats["total_trades"] += 1

    if result == "WIN":
        stats["wins"] += 1

    elif result == "LOSS":
        stats["losses"] += 1

    else:
        stats["breakeven"] += 1

    stats["total_pnl"] += pnl_percent

    stats["total_r"] += r_result

    add_trade_exit_event(
        symbol,
        trade,
        result
    )

    del state["open_trades"][symbol]


# ============================================================
# OPEN PNL
# ============================================================

def calculate_open_pnl(
    state
):

    total = 0.0

    for symbol, trade in state[
        "open_trades"
    ].items():

        price = fetch_live_price(symbol)

        if price is None:
            continue

        entry = trade["entry"]

        if trade["side"] == "LONG":

            pnl = (
                (price - entry)
                / entry
            ) * 100

        else:

            pnl = (
                (entry - price)
                / entry
            ) * 100

        total += pnl

    return total


# ============================================================
# TOP COINS
# ============================================================

def get_top_symbols():

    try:

        markets = exchange.load_markets()

        candidates = []

        for symbol, market in markets.items():

            try:

                if not market.get("active", True):
                    continue

                if not market.get("swap", False):
                    continue

                if not market.get("linear", False):
                    continue

                quote = market.get("quote")
                settle = market.get("settle")

                if quote != "USD":
                    continue

                if settle != "USD":
                    continue

                candidates.append(symbol)

            except Exception:
                continue

        if not candidates:
            return []

        tickers = exchange.fetch_tickers()

        ranked = []

        for symbol in candidates:

            ticker = tickers.get(symbol)

            if not ticker:
                continue

            quote_volume = ticker.get(
                "quoteVolume"
            )

            if quote_volume is None:
                base_volume = ticker.get(
                    "baseVolume"
                )

                last = ticker.get("last")

                if (
                    base_volume is not None
                    and last is not None
                ):

                    quote_volume = (
                        float(base_volume)
                        * float(last)
                    )

            if quote_volume is None:
                continue

            ranked.append(
                (
                    symbol,
                    float(quote_volume)
                )
            )

        ranked.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            x[0]
            for x in ranked[:TOP_COINS]
        ]

    except Exception as e:

        print("Top symbols error:", e)
        return []


# ============================================================
# SCAN ONE SYMBOL
# ============================================================

def scan_symbol(
    state,
    history,
    symbol
):

    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    df1h = fetch_ohlcv(
        symbol,
        "1h"
    )

    if df1h is None:
        return

    trend1h = determine_trend(
        df1h
    )

    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    df15 = fetch_ohlcv(
        symbol,
        "15m"
    )

    if df15 is None:
        return

    trend15m = determine_trend(
        df15
    )

    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    df5 = fetch_ohlcv(
        symbol,
        "5m"
    )

    if df5 is None:
        return

    df5 = calculate_ut_bot(
        df5
    )

    # --------------------------------------------------------
    # OPEN TRADE FIRST
    # --------------------------------------------------------

    check_open_trade(
        state,
        history,
        symbol,
        df5
    )

    # --------------------------------------------------------
    # PENDING SIGNAL
    # --------------------------------------------------------

    if symbol in state[
        "pending_signals"
    ]:

        process_pending_signal(
            state,
            symbol,
            df5
        )

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    if symbol not in state[
        "open_trades"
    ] and symbol not in state[
        "pending_signals"
    ]:

        detect_new_ut_signal(
            state,
            symbol,
            df5,
            trend1h,
            trend15m
        )


# ============================================================
# RUN SCAN
# ============================================================

def run_scan(
    state,
    history
):

    symbols = get_top_symbols()

    print(
        f"Scanning {len(symbols)} symbols..."
    )

    if not symbols:
        add_event(
            "❌ No symbols available"
        )
        return

    # --------------------------------------------------------
    # Always scan open trades
    # --------------------------------------------------------

    extra_symbols = set()

    extra_symbols.update(
        state["open_trades"].keys()
    )

    extra_symbols.update(
        state["pending_signals"].keys()
    )

    for symbol in extra_symbols:

        if symbol not in symbols:
            symbols.append(symbol)

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        try:

            print(
                f"[{index}/{len(symbols)}] "
                f"{symbol}"
            )

            scan_symbol(
                state,
                history,
                symbol
            )

        except Exception as e:

            print(
                f"Error scanning {symbol}: {e}"
            )

    save_state(state)
    save_history(history)


# ============================================================
# FORMAT EVENT
# ============================================================

def format_event_line(event):

    return event


# ============================================================
# BUILD REPORT
# ============================================================

def build_report(
    state
):

    now = now_utc().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    stats = state["statistics"]

    total = int(
        stats.get("total_trades", 0)
    )

    wins = int(
        stats.get("wins", 0)
    )

    losses = int(
        stats.get("losses", 0)
    )

    breakeven = int(
        stats.get("breakeven", 0)
    )

    total_pnl = float(
        stats.get("total_pnl", 0.0)
    )

    total_r = float(
        stats.get("total_r", 0.0)
    )

    if total > 0:

        wr = (
            wins / total
        ) * 100

    else:

        wr = 0.0

    # --------------------------------------------------------
    # HEADER
    # --------------------------------------------------------

    lines = []

    lines.append(
        "📡 **CRYPTO UT BOT REPORT**"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        f"⏱ 5m CLOSED | TOP {TOP_COINS}"
    )

    lines.append(
        f"🤖 UT {UT_KEY}/{UT_ATR_PERIOD} | RR 1:1"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    lines.append(
        "📊 **PERFORMANCE**"
    )

    lines.append(
        f"Trades {total} | "
        f"🟢 {wins} | "
        f"🔴 {losses} | "
        f"⚪ {breakeven}"
    )

    lines.append(
        f"🏆 WR {wr:.1f}% | "
        f"P&L {total_pnl:+.2f}% | "
        f"R {total_r:+.2f}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # THIS RUN
    # --------------------------------------------------------

    lines.append(
        f"⚡ **THIS RUN: {len(RUN_EVENTS)} EVENTS**"
    )

    if RUN_EVENTS:

        for event in RUN_EVENTS:

            lines.append(
                format_event_line(event)
            )

    else:

        # دقیقاً مثل قالب موردنظر کاربر
        pass

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PENDING
    # --------------------------------------------------------

    pending = state[
        "pending_signals"
    ]

    lines.append(
        f"⏳ **PENDING: {len(pending)}**"
    )

    if not pending:

        lines.append(
            "⚪ None"
        )

    else:

        for symbol, p in pending.items():

            side = p["side"]

            emoji = (
                "🟢"
                if side == "LONG"
                else "🔴"
            )

            lines.append(
                f"{emoji} {symbol} {side} "
                f"@ {p['signal_price']:.8g}"
            )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    open_trades = state[
        "open_trades"
    ]

    lines.append(
        f"📂 **OPEN: {len(open_trades)}**"
    )

    if not open_trades:

        lines.append(
            "⚪ None"
        )

    else:

        for symbol, trade in open_trades.items():

            if trade["side"] == "LONG":
                emoji = "🟢"
            else:
                emoji = "🔴"

            lines.append(
                f"{emoji} {symbol} "
                f"{trade['side']} | "
                f"E {trade['entry']:.8g} | "
                f"SL {trade['sl']:.8g} | "
                f"TP {trade['tp']:.8g}"
            )

    return "\n".join(lines)


# ============================================================
# TELEGRAM REPORT
# ============================================================

def send_report(
    state
):

    report = build_report(
        state
    )

    if len(report) > MAX_TELEGRAM_LENGTH:

        # Keep header + important sections.
        report = report[:MAX_TELEGRAM_LENGTH]

    print("\n" + "=" * 60)
    print(report)
    print("=" * 60)

    send_telegram(
        report
    )


# ============================================================
# MAIN
# ============================================================

def main():

    state = None
    history = None

    try:

        state, history = initialize()

        run_scan(
            state,
            history
        )

        send_report(
            state
        )

        save_state(
            state
        )

        save_history(
            history
        )

    except Exception as e:

        print(
            "FATAL ERROR:",
            e
        )

        traceback.print_exc()

        # حتی در صورت خطا، یک گزارش تلگرام ارسال شود
        if state is None:
            state = fresh_state()

        add_event(
            f"❌ SCANNER ERROR: {str(e)[:200]}"
        )

        try:
            send_report(
                state
            )
        except Exception:
            pass


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
