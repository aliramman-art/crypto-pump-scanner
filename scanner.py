# ============================================================
# CRYPTO UT BOT SCANNER
# Kraken Futures | Closed 5m Candles
# TradingView UT Bot Alerts v4
#
# UT BOT:
#   Key Value = 3
#   ATR Period = 10
#   Heikin Ashi = OFF
#
# LOGIC:
#   LONG:
#     1. UT BUY
#     2. Save BUY candle HIGH
#     3. Wait for CLOSED candle CLOSE > BUY HIGH
#     4. Entry = confirmation CLOSE
#     5. SL = latest valid swing LOW
#     6. TP = Entry + Risk   (RR 1:1)
#
#   SHORT:
#     1. UT SELL
#     2. Save SELL candle LOW
#     3. Wait for CLOSED candle CLOSE < SELL LOW
#     4. Entry = confirmation CLOSE
#     5. SL = latest valid swing HIGH
#     6. TP = Entry - Risk   (RR 1:1)
#
# GitHub Actions One-Shot Mode
# Telegram Alerts
# Persistent State
# Trade History
# Telegram Statistics:
#   Open Signals
#   Closed Signals
#   Win Rate
#   Total Profit
# ============================================================

import os
import json
import math
import traceback
from datetime import datetime, timezone

import ccxt
import pandas as pd
import numpy as np
import requests


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "5m"

TOP_COINS = 30

UT_KEY_VALUE = 3
UT_ATR_PERIOD = 10

ATR_MULTIPLIER = UT_KEY_VALUE

RR = 1.0

OHLCV_LIMIT = 250

STATE_FILE = "utbot_state.json"
HISTORY_FILE = "utbot_trade_history.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

TELEGRAM_ENABLED = bool(
    TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID
)


# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
    "options": {
        "defaultType": "future",
    },
})


# ============================================================
# GLOBAL DATA
# ============================================================

pending_setups = {}
open_trades = {}
trade_history = []

last_processed_candle = {}


# ============================================================
# TIME
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def utc_string():
    return utc_now().strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# JSON HELPERS
# ============================================================

def safe_float(value):
    try:
        value = float(value)

        if math.isnan(value) or math.isinf(value):
            return None

        return value

    except Exception:
        return None


def load_json(filename, default):
    try:
        if not os.path.exists(filename):
            return default

        with open(filename, "r", encoding="utf-8") as f:
            data = json.load(f)

        return data

    except Exception as e:
        print(f"WARNING: Cannot load {filename}: {e}")
        return default


def save_json(filename, data):
    try:
        temp_file = filename + ".tmp"

        with open(temp_file, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(temp_file, filename)

    except Exception as e:
        print(f"ERROR saving {filename}: {e}")


# ============================================================
# STATE
# ============================================================

def load_state():

    global pending_setups
    global open_trades
    global last_processed_candle

    state = load_json(
        STATE_FILE,
        {
            "pending_setups": {},
            "open_trades": {},
            "last_processed_candle": {},
        },
    )

    pending_setups = state.get(
        "pending_setups",
        {}
    )

    open_trades = state.get(
        "open_trades",
        {}
    )

    last_processed_candle = state.get(
        "last_processed_candle",
        {}
    )

    print(
        f"State loaded | "
        f"Pending: {len(pending_setups)} | "
        f"Open: {len(open_trades)}"
    )


def save_state():

    state = {
        "pending_setups": pending_setups,
        "open_trades": open_trades,
        "last_processed_candle": last_processed_candle,
        "updated_at": utc_string(),
    }

    save_json(
        STATE_FILE,
        state
    )


def load_history():

    global trade_history

    trade_history = load_json(
        HISTORY_FILE,
        []
    )

    if not isinstance(trade_history, list):
        trade_history = []

    print(
        f"Trade history loaded: "
        f"{len(trade_history)} trades"
    )


def save_history():

    save_json(
        HISTORY_FILE,
        trade_history
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_ENABLED:
        print("Telegram: DISABLED")
        return False

    try:

        url = (
            "https://api.telegram.org/bot"
            f"{TELEGRAM_BOT_TOKEN}"
            "/sendMessage"
        )

        response = requests.post(
            url,
            data={
                "chat_id": TELEGRAM_CHAT_ID,
                "text": message,
            },
            timeout=20,
        )

        if response.ok:

            print("Telegram: message sent")

            return True

        print(
            "Telegram ERROR:",
            response.status_code,
            response.text[:500],
        )

        return False

    except Exception as e:

        print(
            f"Telegram ERROR: {e}"
        )

        return False


# ============================================================
# TELEGRAM TEST
# ============================================================

def telegram_start_message():

    message = (
        "📡 CRYPTO UT BOT SCANNER\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🕐 {utc_string()}\n"
        f"⏱ Timeframe: {TIMEFRAME} CLOSED\n"
        f"🤖 UT Bot: Key {UT_KEY_VALUE} / ATR {UT_ATR_PERIOD}\n"
        f"🪙 Coins: {TOP_COINS}\n"
        "⚙️ Mode: GitHub Actions One-Shot\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(message)


# ============================================================
# TELEGRAM SIGNALS
# ============================================================

def telegram_ut_signal(symbol, side, signal_high, signal_low):

    if side == "LONG":

        message = (
            "🟢 UT BUY SIGNAL\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🪙 {symbol}\n"
            f"⏱ {TIMEFRAME} CLOSED\n"
            f"📈 BUY HIGH: {signal_high:.8f}\n"
            "⏳ Waiting for:\n"
            f"CLOSE > {signal_high:.8f}\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    else:

        message = (
            "🔴 UT SELL SIGNAL\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"🪙 {symbol}\n"
            f"⏱ {TIMEFRAME} CLOSED\n"
            f"📉 SELL LOW: {signal_low:.8f}\n"
            "⏳ Waiting for:\n"
            f"CLOSE < {signal_low:.8f}\n"
            "━━━━━━━━━━━━━━━━━━"
        )

    send_telegram(message)


# ============================================================
# TELEGRAM ENTRY + STATISTICS
# ============================================================

def telegram_entry(trade):

    side = trade["side"]

    symbol = trade["symbol"]
    entry = trade["entry"]
    sl = trade["sl"]
    tp = trade["tp"]
    risk_pct = trade["risk_pct"]

    if side == "LONG":
        emoji = "🟢"
        title = "CONFIRMED LONG"
    else:
        emoji = "🔴"
        title = "CONFIRMED SHORT"

    # --------------------------------------------------------
    # LIVE STATISTICS
    # --------------------------------------------------------

    stats = calculate_stats()

    open_signals = len(open_trades)

    closed_signals = stats["total"]

    win_rate = stats["win_rate"]

    total_profit = stats["net_pnl"]

    message = (
        f"{emoji} {title}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {symbol}\n"
        f"⏱ {TIMEFRAME}\n"
        f"💰 Entry: {entry:.8f}\n"
        f"🛑 SL: {sl:.8f}\n"
        f"🎯 TP: {tp:.8f}\n"
        f"📊 Risk: {risk_pct:.2f}%\n"
        f"⚖️ RR: 1:{RR:.1f}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 STATISTICS\n"
        f"📈 Open Signals: {open_signals}\n"
        f"📁 Closed Signals: {closed_signals}\n"
        f"🎯 Win Rate: {win_rate:.2f}%\n"
        f"💰 Total Profit: {total_profit:+.2f}%\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(message)


# ============================================================
# TELEGRAM EXIT
# ============================================================

def telegram_exit(trade, result, exit_price):

    symbol = trade["symbol"]
    side = trade["side"]

    pnl_pct = trade.get(
        "pnl_pct",
        0
    )

    r_multiple = trade.get(
        "r_multiple",
        0
    )

    if result == "TP":

        emoji = "🎯"
        title = "TAKE PROFIT"

    else:

        emoji = "🛑"
        title = "STOP LOSS"

    # --------------------------------------------------------
    # Calculate statistics AFTER this trade is closed
    # --------------------------------------------------------

    stats = calculate_stats()

    open_signals = len(open_trades) - 1
    closed_signals = stats["total"] + 1

    total_profit = (
        stats["net_pnl"]
        + float(pnl_pct or 0)
    )

    wins = stats["tp"]

    if result == "TP":
        wins += 1

    win_rate = (
        wins / closed_signals * 100
        if closed_signals
        else 0
    )

    message = (
        f"{emoji} {title}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {symbol}\n"
        f"📌 {side}\n"
        f"💰 Entry: {trade['entry']:.8f}\n"
        f"🚪 Exit: {exit_price:.8f}\n"
        f"📈 P&L: {pnl_pct:+.2f}%\n"
        f"📊 R: {r_multiple:+.2f}R\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📊 STATISTICS\n"
        f"📈 Open Signals: {max(0, open_signals)}\n"
        f"📁 Closed Signals: {closed_signals}\n"
        f"🎯 Win Rate: {win_rate:.2f}%\n"
        f"💰 Total Profit: {total_profit:+.2f}%\n"
        "━━━━━━━━━━━━━━━━━━"
    )

    send_telegram(message)


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol):

    try:

        candles = exchange.fetch_ohlcv(
            symbol,
            timeframe=TIMEFRAME,
            limit=OHLCV_LIMIT,
        )

        if not candles:
            return None

        df = pd.DataFrame(
            candles,
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True,
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]:

            df[col] = pd.to_numeric(
                df[col],
                errors="coerce",
            )

        df = df.dropna().reset_index(
            drop=True
        )

        # --------------------------------------------------------
        # REMOVE CURRENT UNFINISHED 5M CANDLE
        # --------------------------------------------------------

        now_ms = exchange.milliseconds()

        timeframe_ms = 5 * 60 * 1000

        if len(df) > 0:

            last_ts = int(
                df.iloc[-1]["timestamp"].timestamp()
                * 1000
            )

            if last_ts + timeframe_ms > now_ms:

                df = df.iloc[:-1].copy()

        if len(df) < 50:
            return None

        return df.reset_index(
            drop=True
        )

    except Exception as e:

        print(
            f"OHLCV ERROR {symbol}: {e}"
        )

        return None


# ============================================================
# ATR - WILDER / TRADINGVIEW STYLE
# ============================================================

def calculate_atr(df, period=10):

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

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    atr = true_range.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period,
    ).mean()

    return atr


# ============================================================
# UT BOT
# TradingView Pine v4 logic
# ============================================================

def calculate_ut_bot(
    df,
    key_value=3,
    atr_period=10,
):

    df = df.copy()

    src = df["close"]

    atr = calculate_atr(
        df,
        atr_period,
    )

    n_loss = (
        key_value * atr
    )

    trailing_stop = np.zeros(
        len(df),
        dtype=float,
    )

    pos = np.zeros(
        len(df),
        dtype=int,
    )

    buy = np.zeros(
        len(df),
        dtype=bool,
    )

    sell = np.zeros(
        len(df),
        dtype=bool,
    )

    for i in range(len(df)):

        current_src = float(
            src.iloc[i]
        )

        current_loss = (
            float(n_loss.iloc[i])
            if pd.notna(n_loss.iloc[i])
            else 0.0
        )

        previous_stop = (
            trailing_stop[i - 1]
            if i > 0
            else 0.0
        )

        previous_src = (
            float(src.iloc[i - 1])
            if i > 0
            else current_src
        )

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

        previous_pos = (
            pos[i - 1]
            if i > 0
            else 0
        )

        if (
            i > 0
            and previous_src < previous_stop
            and current_src > previous_stop
        ):

            pos[i] = 1

        elif (
            i > 0
            and previous_src > previous_stop
            and current_src < previous_stop
        ):

            pos[i] = -1

        else:

            pos[i] = previous_pos

        if i > 0:

            above = (
                src.iloc[i] > trailing_stop[i]
                and src.iloc[i - 1]
                <= trailing_stop[i - 1]
            )

            below = (
                trailing_stop[i] > src.iloc[i]
                and trailing_stop[i - 1]
                <= src.iloc[i - 1]
            )

            buy[i] = (
                current_src > trailing_stop[i]
                and above
            )

            sell[i] = (
                current_src < trailing_stop[i]
                and below
            )

    df["atr"] = atr
    df["ut_stop"] = trailing_stop
    df["ut_pos"] = pos
    df["ut_buy"] = buy
    df["ut_sell"] = sell

    return df


# ============================================================
# SWING DETECTION
# ============================================================

def latest_swing_low(
    df,
    before_index,
    left=2,
    right=2,
):

    if before_index <= right:
        return None

    last_valid = (
        before_index - right
    )

    for i in range(
        last_valid,
        left - 1,
        -1,
    ):

        low = float(
            df.iloc[i]["low"]
        )

        left_lows = df.iloc[
            i - left:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:i + right + 1
        ]["low"]

        if (
            low < float(left_lows.min())
            and
            low <= float(right_lows.min())
        ):

            return low

    return None


def latest_swing_high(
    df,
    before_index,
    left=2,
    right=2,
):

    if before_index <= right:
        return None

    last_valid = (
        before_index - right
    )

    for i in range(
        last_valid,
        left - 1,
        -1,
    ):

        high = float(
            df.iloc[i]["high"]
        )

        left_highs = df.iloc[
            i - left:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:i + right + 1
        ]["high"]

        if (
            high > float(left_highs.max())
            and
            high >= float(right_highs.max())
        ):

            return high

    return None


# ============================================================
# FALLBACK SWINGS
# ============================================================

def fallback_swing_low(
    df,
    before_index,
    lookback=10,
):

    start = max(
        0,
        before_index - lookback,
    )

    section = df.iloc[
        start:before_index
    ]

    if len(section) == 0:
        return None

    return float(
        section["low"].min()
    )


def fallback_swing_high(
    df,
    before_index,
    lookback=10,
):

    start = max(
        0,
        before_index - lookback,
    )

    section = df.iloc[
        start:before_index
    ]

    if len(section) == 0:
        return None

    return float(
        section["high"].max()
    )


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(price):

    price = float(price)

    if price >= 1000:
        return f"{price:.2f}"

    if price >= 100:
        return f"{price:.3f}"

    if price >= 1:
        return f"{price:.5f}"

    if price >= 0.01:
        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# CREATE LONG TRADE
# ============================================================

def create_long_trade(
    symbol,
    df,
    confirmation_index,
    pending,
):

    entry = float(
        df.iloc[
            confirmation_index
        ]["close"]
    )

    swing_low = latest_swing_low(
        df,
        confirmation_index,
    )

    if swing_low is None:

        swing_low = fallback_swing_low(
            df,
            confirmation_index,
        )

    if swing_low is None:
        return None

    if swing_low >= entry:

        swing_low = fallback_swing_low(
            df,
            confirmation_index,
            lookback=20,
        )

    if swing_low is None:
        return None

    if swing_low >= entry:
        return None

    sl_buffer = (
        float(df.iloc[
            confirmation_index
        ]["atr"])
        * 0.05
    )

    if not np.isfinite(sl_buffer):
        sl_buffer = 0.0

    sl = swing_low - sl_buffer

    risk = entry - sl

    if risk <= 0:
        return None

    tp = entry + (
        risk * RR
    )

    risk_pct = (
        risk / entry
    ) * 100

    candle = df.iloc[
        confirmation_index
    ]

    trade = {

        "id": (
            f"{symbol}_LONG_"
            f"{int(candle['timestamp'].timestamp())}"
        ),

        "symbol": symbol,

        "side": "LONG",

        "signal_time": pending[
            "signal_time"
        ],

        "confirmation_time": str(
            candle["timestamp"]
        ),

        "entry": safe_float(entry),

        "sl": safe_float(sl),

        "tp": safe_float(tp),

        "risk": safe_float(risk),

        "risk_pct": safe_float(
            risk_pct
        ),

        "status": "OPEN",

        "exit_time": None,

        "exit_price": None,

        "result": None,

        "pnl_pct": None,

        "r_multiple": None,

    }

    return trade


# ============================================================
# CREATE SHORT TRADE
# ============================================================

def create_short_trade(
    symbol,
    df,
    confirmation_index,
    pending,
):

    entry = float(
        df.iloc[
            confirmation_index
        ]["close"]
    )

    swing_high = latest_swing_high(
        df,
        confirmation_index,
    )

    if swing_high is None:

        swing_high = fallback_swing_high(
            df,
            confirmation_index,
        )

    if swing_high is None:
        return None

    if swing_high <= entry:

        swing_high = fallback_swing_high(
            df,
            confirmation_index,
            lookback=20,
        )

    if swing_high is None:
        return None

    if swing_high <= entry:
        return None

    sl_buffer = (
        float(df.iloc[
            confirmation_index
        ]["atr"])
        * 0.05
    )

    if not np.isfinite(sl_buffer):
        sl_buffer = 0.0

    sl = swing_high + sl_buffer

    risk = sl - entry

    if risk <= 0:
        return None

    tp = entry - (
        risk * RR
    )

    risk_pct = (
        risk / entry
    ) * 100

    candle = df.iloc[
        confirmation_index
    ]

    trade = {

        "id": (
            f"{symbol}_SHORT_"
            f"{int(candle['timestamp'].timestamp())}"
        ),

        "symbol": symbol,

        "side": "SHORT",

        "signal_time": pending[
            "signal_time"
        ],

        "confirmation_time": str(
            candle["timestamp"]
        ),

        "entry": safe_float(entry),

        "sl": safe_float(sl),

        "tp": safe_float(tp),

        "risk": safe_float(risk),

        "risk_pct": safe_float(
            risk_pct
        ),

        "status": "OPEN",

        "exit_time": None,

        "exit_price": None,

        "result": None,

        "pnl_pct": None,

        "r_multiple": None,

    }

    return trade


# ============================================================
# OPEN TRADE MANAGEMENT
# ============================================================

def check_open_trade(
    symbol,
    df,
):

    if symbol not in open_trades:
        return

    trade = open_trades[
        symbol
    ]

    if trade.get("status") != "OPEN":
        return

    candle = df.iloc[-1]

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp = float(
        trade["tp"]
    )

    result = None
    exit_price = None

    if trade["side"] == "LONG":

        if low <= sl:

            result = "SL"
            exit_price = sl

        elif high >= tp:

            result = "TP"
            exit_price = tp

        current_price = float(
            candle["close"]
        )

        pnl_pct = (
            (current_price - entry)
            / entry
        ) * 100

        r_multiple = (
            (current_price - entry)
            / trade["risk"]
        )

    else:

        if high >= sl:

            result = "SL"
            exit_price = sl

        elif low <= tp:

            result = "TP"
            exit_price = tp

        current_price = float(
            candle["close"]
        )

        pnl_pct = (
            (entry - current_price)
            / entry
        ) * 100

        r_multiple = (
            (entry - current_price)
            / trade["risk"]
        )

    if result:

        if trade["side"] == "LONG":

            final_pnl = (
                (exit_price - entry)
                / entry
            ) * 100

            final_r = (
                (exit_price - entry)
                / trade["risk"]
            )

        else:

            final_pnl = (
                (entry - exit_price)
                / entry
            ) * 100

            final_r = (
                (entry - exit_price)
                / trade["risk"]
            )

        trade["exit_time"] = str(
            candle["timestamp"]
        )

        trade["exit_price"] = safe_float(
            exit_price
        )

        trade["result"] = result

        trade["pnl_pct"] = safe_float(
            final_pnl
        )

        trade["r_multiple"] = safe_float(
            final_r
        )

        trade["status"] = "CLOSED"

        trade_history.append(
            trade.copy()
        )

        telegram_exit(
            trade,
            result,
            exit_price,
        )

        print(
            f"\n{'🎯' if result == 'TP' else '🛑'} "
            f"{result}: {symbol}"
        )

        print(
            f"Exit: {fmt_price(exit_price)}"
        )

        print(
            f"P&L: {final_pnl:+.2f}%"
        )

        print(
            f"R: {final_r:+.2f}R"
        )

        del open_trades[
            symbol
        ]

        save_history()
        save_state()


# ============================================================
# PROCESS PENDING SETUP
# ============================================================

def check_pending_setup(
    symbol,
    df,
):

    if symbol not in pending_setups:
        return

    if symbol in open_trades:
        return

    pending = pending_setups[
        symbol
    ]

    confirmation_index = (
        len(df) - 1
    )

    candle = df.iloc[
        confirmation_index
    ]

    close = float(
        candle["close"]
    )

    if pending["side"] == "LONG":

        signal_high = float(
            pending["signal_high"]
        )

        if close > signal_high:

            trade = create_long_trade(
                symbol,
                df,
                confirmation_index,
                pending,
            )

            if trade:

                open_trades[
                    symbol
                ] = trade

                del pending_setups[
                    symbol
                ]

                print(
                    "\n🟢 CONFIRMED LONG"
                )

                print(
                    f"Symbol: {symbol}"
                )

                print(
                    f"Entry: "
                    f"{fmt_price(trade['entry'])}"
                )

                print(
                    f"SL: "
                    f"{fmt_price(trade['sl'])}"
                )

                print(
                    f"TP: "
                    f"{fmt_price(trade['tp'])}"
                )

                print(
                    f"Risk: "
                    f"{trade['risk_pct']:.2f}%"
                )

                telegram_entry(
                    trade
                )

                save_state()

    else:

        signal_low = float(
            pending["signal_low"]
        )

        if close < signal_low:

            trade = create_short_trade(
                symbol,
                df,
                confirmation_index,
                pending,
            )

            if trade:

                open_trades[
                    symbol
                ] = trade

                del pending_setups[
                    symbol
                ]

                print(
                    "\n🔴 CONFIRMED SHORT"
                )

                print(
                    f"Symbol: {symbol}"
                )

                print(
                    f"Entry: "
                    f"{fmt_price(trade['entry'])}"
                )

                print(
                    f"SL: "
                    f"{fmt_price(trade['sl'])}"
                )

                print(
                    f"TP: "
                    f"{fmt_price(trade['tp'])}"
                )

                print(
                    f"Risk: "
                    f"{trade['risk_pct']:.2f}%"
                )

                telegram_entry(
                    trade
                )

                save_state()


# ============================================================
# PROCESS NEW UT SIGNAL
# ============================================================

def process_ut_signal(
    symbol,
    df,
):

    index = len(df) - 1

    candle = df.iloc[
        index
    ]

    candle_time = str(
        candle["timestamp"]
    )

    if (
        last_processed_candle.get(symbol)
        == candle_time
    ):

        return

    last_processed_candle[
        symbol
    ] = candle_time

    if symbol in open_trades:
        return

    if symbol in pending_setups:

        check_pending_setup(
            symbol,
            df,
        )

        return

    if bool(candle["ut_buy"]):

        signal_high = float(
            candle["high"]
        )

        pending_setups[
            symbol
        ] = {

            "symbol": symbol,

            "side": "LONG",

            "signal_time": candle_time,

            "signal_high": safe_float(
                signal_high
            ),

            "signal_low": None,

            "created_at": utc_string(),
        }

        print(
            f"\n🟢 UT BUY: {symbol}"
        )

        print(
            f"BUY HIGH: "
            f"{fmt_price(signal_high)}"
        )

        print(
            "⏳ Waiting for "
            f"CLOSE > {fmt_price(signal_high)}"
        )

        telegram_ut_signal(
            symbol,
            "LONG",
            signal_high,
            0,
        )

        save_state()

        return

    if bool(candle["ut_sell"]):

        signal_low = float(
            candle["low"]
        )

        pending_setups[
            symbol
        ] = {

            "symbol": symbol,

            "side": "SHORT",

            "signal_time": candle_time,

            "signal_high": None,

            "signal_low": safe_float(
                signal_low
            ),

            "created_at": utc_string(),
        }

        print(
            f"\n🔴 UT SELL: {symbol}"
        )

        print(
            f"SELL LOW: "
            f"{fmt_price(signal_low)}"
        )

        print(
            "⏳ Waiting for "
            f"CLOSE < {fmt_price(signal_low)}"
        )

        telegram_ut_signal(
            symbol,
            "SHORT",
            0,
            signal_low,
        )

        save_state()

        return


# ============================================================
# TOP SYMBOLS
# ============================================================

def get_top_symbols():

    print(
        "\nLoading Kraken Futures markets..."
    )

    markets = exchange.load_markets()

    print(
        f"Markets loaded: "
        f"{len(markets)}"
    )

    candidates = []

    for symbol, market in markets.items():

        try:

            if not market.get(
                "active",
                True
            ):
                continue

            if not market.get(
                "swap",
                False
            ):
                continue

            if market.get(
                "quote"
            ) != "USD":
                continue

            candidates.append(
                symbol
            )

        except Exception:
            continue

    print(
        f"\nLoading TOP {TOP_COINS} "
        "Futures symbols..."
    )

    print(
        f"Checking volume for "
        f"{len(candidates)} futures symbols..."
    )

    try:

        tickers = exchange.fetch_tickers()

    except Exception as e:

        print(
            f"fetch_tickers error: {e}"
        )

        return candidates[
            :TOP_COINS
        ]

    volume_data = []

    for symbol in candidates:

        ticker = tickers.get(
            symbol
        )

        if not ticker:
            continue

        try:

            quote_volume = ticker.get(
                "quoteVolume"
            )

            if quote_volume is None:

                base_volume = ticker.get(
                    "baseVolume"
                )

                last = ticker.get(
                    "last"
                )

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

            quote_volume = float(
                quote_volume
            )

            if quote_volume <= 0:
                continue

            volume_data.append(
                (
                    symbol,
                    quote_volume,
                )
            )

        except Exception:
            continue

    volume_data.sort(
        key=lambda x: x[1],
        reverse=True,
    )

    symbols = [
        x[0]
        for x in volume_data[
            :TOP_COINS
        ]
    ]

    print(
        f"\nLoaded {len(symbols)} symbols:"
    )

    for symbol in symbols:

        print(
            f"  {symbol}"
        )

    return symbols


# ============================================================
# DASHBOARD STATISTICS
# ============================================================

def calculate_stats():

    total = len(
        trade_history
    )

    tp_count = sum(
        1
        for t in trade_history
        if t.get("result") == "TP"
    )

    sl_count = sum(
        1
        for t in trade_history
        if t.get("result") == "SL"
    )

    wins = [
        float(t.get("pnl_pct", 0))
        for t in trade_history
        if t.get("result") == "TP"
    ]

    losses = [
        float(t.get("pnl_pct", 0))
        for t in trade_history
        if t.get("result") == "SL"
    ]

    net_pnl = sum(
        float(t.get("pnl_pct", 0))
        for t in trade_history
    )

    gross_profit = sum(
        x for x in wins
        if x > 0
    )

    gross_loss = abs(
        sum(
            x for x in losses
            if x < 0
        )
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = (
            float("inf")
            if gross_profit > 0
            else 0
        )

    win_rate = (
        tp_count / total * 100
        if total
        else 0
    )

    loss_rate = (
        sl_count / total * 100
        if total
        else 0
    )

    avg_tp = (
        sum(wins) / len(wins)
        if wins
        else 0
    )

    avg_sl = (
        sum(losses) / len(losses)
        if losses
        else 0
    )

    max_win_streak = 0
    max_loss_streak = 0

    current_win = 0
    current_loss = 0

    for trade in trade_history:

        if trade.get(
            "result"
        ) == "TP":

            current_win += 1
            current_loss = 0

        elif trade.get(
            "result"
        ) == "SL":

            current_loss += 1
            current_win = 0

        max_win_streak = max(
            max_win_streak,
            current_win,
        )

        max_loss_streak = max(
            max_loss_streak,
            current_loss,
        )

    return {

        "total": total,

        "tp": tp_count,

        "sl": sl_count,

        "win_rate": win_rate,

        "loss_rate": loss_rate,

        "gross_profit": gross_profit,

        "gross_loss": gross_loss,

        "net_pnl": net_pnl,

        "profit_factor": profit_factor,

        "avg_tp": avg_tp,

        "avg_sl": avg_sl,

        "max_win_streak": max_win_streak,

        "max_loss_streak": max_loss_streak,
    }


# ============================================================
# DASHBOARD
# ============================================================

def print_dashboard():

    stats = calculate_stats()

    print(
        "\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        "📊 CRYPTO UT BOT SCANNER DASHBOARD"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        f"🕐 {utc_string()}"
    )

    print(
        f"⏱ Timeframe : {TIMEFRAME} CLOSED"
    )

    print(
        f"🤖 UT Bot   : "
        f"Key {UT_KEY_VALUE} / "
        f"ATR {UT_ATR_PERIOD}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        f"Trades      : {stats['total']}"
    )

    print(
        f"TP          : {stats['tp']}"
    )

    print(
        f"SL          : {stats['sl']}"
    )

    print(
        f"Win Rate    : "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"Loss Rate   : "
        f"{stats['loss_rate']:.2f}%"
    )

    print(
        f"Gross Profit: "
        f"+{stats['gross_profit']:.2f}%"
    )

    print(
        f"Gross Loss  : "
        f"-{stats['gross_loss']:.2f}%"
    )

    print(
        f"Net P&L     : "
        f"{stats['net_pnl']:+.2f}%"
    )

    pf = stats[
        "profit_factor"
    ]

    if math.isinf(pf):

        pf_text = "∞"

    else:

        pf_text = f"{pf:.2f}"

    print(
        f"Profit Factor: {pf_text}"
    )

    print(
        f"Avg TP      : "
        f"{stats['avg_tp']:+.2f}%"
    )

    print(
        f"Avg SL      : "
        f"{stats['avg_sl']:+.2f}%"
    )

    print(
        f"Max Win Streak : "
        f"{stats['max_win_streak']}"
    )

    print(
        f"Max Loss Streak: "
        f"{stats['max_loss_streak']}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        f"⏳ Pending Setups: "
        f"{len(pending_setups)}"
    )

    print(
        f"📈 Open Trades: "
        f"{len(open_trades)}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# OPEN TRADE STATUS
# ============================================================

def print_open_trades_status():

    if not open_trades:

        print(
            "\n📭 No open trades."
        )

        return

    print(
        "\n📈 OPEN TRADES"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    for symbol, trade in open_trades.items():

        try:

            ticker = exchange.fetch_ticker(
                symbol
            )

            current = ticker.get(
                "last"
            )

            if current is None:
                continue

            current = float(
                current
            )

            entry = float(
                trade["entry"]
            )

            risk = float(
                trade["risk"]
            )

            if trade["side"] == "LONG":

                pnl_pct = (
                    (current - entry)
                    / entry
                ) * 100

                r = (
                    current - entry
                ) / risk

            else:

                pnl_pct = (
                    (entry - current)
                    / entry
                ) * 100

                r = (
                    entry - current
                ) / risk

            print(
                f"{symbol} | "
                f"{trade['side']} | "
                f"Price {fmt_price(current)} | "
                f"P&L {pnl_pct:+.2f}% | "
                f"{r:+.2f}R"
            )

        except Exception as e:

            print(
                f"{symbol}: "
                f"status error: {e}"
            )


# ============================================================
# PENDING STATUS
# ============================================================

def print_pending_setups():

    if not pending_setups:

        print(
            "\n📭 No pending setups."
        )

        return

    print(
        "\n⏳ PENDING SETUPS"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    for symbol, setup in pending_setups.items():

        if setup["side"] == "LONG":

            print(
                f"🟢 {symbol} | "
                f"BUY HIGH: "
                f"{fmt_price(setup['signal_high'])}"
            )

        else:

            print(
                f"🔴 {symbol} | "
                f"SELL LOW: "
                f"{fmt_price(setup['signal_low'])}"
            )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print(
        "\n"
        "======================================"
    )

    print(
        "CRYPTO UT BOT SCANNER"
    )

    print(
        f"UT BOT {UT_KEY_VALUE} / "
        f"{UT_ATR_PERIOD}"
    )

    print(
        "ONE-SHOT GITHUB ACTION MODE"
    )

    print(
        "======================================"
    )

    if TELEGRAM_ENABLED:

        print(
            "Telegram: ENABLED"
        )

    else:

        print(
            "Telegram: DISABLED "
            "(check GitHub Secrets)"
        )

    # --------------------------------------------------------
    # Load state
    # --------------------------------------------------------

    load_state()
    load_history()

    # --------------------------------------------------------
    # Telegram startup
    # --------------------------------------------------------

    telegram_start_message()

    # --------------------------------------------------------
    # Symbols
    # --------------------------------------------------------

    symbols = get_top_symbols()

    if not symbols:

        print(
            "ERROR: No symbols found."
        )

        send_telegram(
            "⚠️ UT Bot Scanner\n"
            "No Futures symbols found."
        )

        return

    # --------------------------------------------------------
    # Scan
    # --------------------------------------------------------

    for number, symbol in enumerate(
        symbols,
        start=1,
    ):

        print(
            f"\n[{number:02d}/{len(symbols):02d}] "
            f"Scanning {symbol}"
        )

        try:

            df = fetch_ohlcv(
                symbol
            )

            if df is None:

                print(
                    "No valid OHLCV data."
                )

                continue

            df = calculate_ut_bot(
                df,
                UT_KEY_VALUE,
                UT_ATR_PERIOD,
            )

            # ------------------------------------------------
            # Check existing open trade first
            # ------------------------------------------------

            if symbol in open_trades:

                check_open_trade(
                    symbol,
                    df,
                )

            # ------------------------------------------------
            # Existing pending setup
            # ------------------------------------------------

            if (
                symbol not in open_trades
                and symbol in pending_setups
            ):

                check_pending_setup(
                    symbol,
                    df,
                )

            # ------------------------------------------------
            # Process newest UT signal
            # ------------------------------------------------

            process_ut_signal(
                symbol,
                df,
            )

        except Exception as e:

            print(
                f"ERROR scanning {symbol}: "
                f"{e}"
            )

            traceback.print_exc()

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_state()
    save_history()

    # --------------------------------------------------------
    # Dashboard
    # --------------------------------------------------------

    print_pending_setups()

    print_open_trades_status()

    print_dashboard()

    # --------------------------------------------------------
    # Finish
    # --------------------------------------------------------

    print(
        "\n======================================"
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        f"{utc_string()}"
    )

    print(
        "======================================"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()

    except Exception as e:

        print(
            "\nFATAL ERROR:"
        )

        print(
            str(e)
        )

        traceback.print_exc()

        send_telegram(
            "🚨 CRYPTO UT BOT SCANNER ERROR\n"
            "━━━━━━━━━━━━━━━━━━\n"
            f"{str(e)[:800]}"
        )

        raise
