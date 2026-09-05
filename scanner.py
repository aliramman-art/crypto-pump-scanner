# ============================================================
# CRYPTO UT BOT SCANNER v12.0
# ============================================================
# Kraken Futures
# 15M CLOSED CANDLES
#
# UT BOT:
#   Key Value = 3
#   ATR Period = 10
#
# NEW ENTRY LOGIC:
#
# LONG:
#   1) UT Bot gives BUY on a CLOSED candle
#   2) DO NOT ENTER immediately
#   3) Wait until a later CLOSED candle closes ABOVE
#      the HIGH of the UT BUY signal candle
#   4) Entry = confirmation candle CLOSE
#   5) SL = slightly below latest confirmed valid Swing Low
#   6) TP = 1R
#
# SHORT:
#   1) UT Bot gives SELL on a CLOSED candle
#   2) DO NOT ENTER immediately
#   3) Wait until a later CLOSED candle closes BELOW
#      the LOW of the UT SELL signal candle
#   4) Entry = confirmation candle CLOSE
#   5) SL = slightly above latest confirmed valid Swing High
#   6) TP = 1R
#
# IMPORTANT:
#   BUY  -> ONLY LONG
#   SELL -> ONLY SHORT
#   NEVER reverse UT direction
#
# SL / TP:
#   FIXED AFTER ENTRY
#
# REPORT:
#   Iran time (Asia/Tehran)
#   Signal time
#   Open time
#   Duration
#   SL percentage
#   TP percentage
#
# RESET:
#   Previous open trades = deleted
#   Previous cumulative statistics = deleted
#   Previous processed signals = deleted
#
# ============================================================

import os
import json
import time
import math
import traceback
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import ccxt
import pandas as pd
import requests


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "15m"

TOP_COINS = 30

UT_KEY = 3
UT_ATR_PERIOD = 10

RR = 1.0

# SL buffer beyond swing
SL_BUFFER_PERCENT = 0.10

# Number of candles used for confirmed swing
SWING_LEFT = 2
SWING_RIGHT = 2

OHLCV_LIMIT = 250

SCAN_INTERVAL_SECONDS = 60

# ------------------------------------------------------------
# Files
# ------------------------------------------------------------

STATE_FILE = "ut_bot_state.json"
HISTORY_FILE = "ut_bot_trade_history.json"

# Set True ONCE for a completely fresh start.
# After the first execution it automatically becomes False.
RESET_ON_START = True

RESET_MARKER_FILE = "ut_bot_reset_done.txt"


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    "YOUR_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    "YOUR_CHAT_ID"
)


# ============================================================
# TIMEZONE
# ============================================================

IRAN_TZ = ZoneInfo("Asia/Tehran")


# ============================================================
# EXCHANGE
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
})


# ============================================================
# GLOBAL STATE
# ============================================================

state = {
    "open_trades": {},
    "pending_signals": {},
    "processed_signals": {},
    "statistics": {
        "total_trades": 0,
        "wins": 0,
        "losses": 0,
        "breakeven": 0,
        "total_pnl": 0.0,
        "total_r": 0.0,
    }
}

trade_history = []


# ============================================================
# TIME HELPERS
# ============================================================

def now_utc():
    return datetime.now(timezone.utc)


def now_iran():
    return datetime.now(IRAN_TZ)


def iso_now_iran():
    return now_iran().isoformat()


def format_iran_time(value):
    if not value:
        return "-"

    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IRAN_TZ)

        dt = dt.astimezone(IRAN_TZ)

        return dt.strftime("%Y-%m-%d %H:%M:%S")

    except Exception:
        return str(value)


def parse_time(value):
    try:
        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IRAN_TZ)

        return dt

    except Exception:
        return now_iran()


def format_duration(start_time):
    if not start_time:
        return "-"

    try:
        start = parse_time(start_time)
        end = now_iran()

        seconds = int((end - start).total_seconds())

        if seconds < 0:
            seconds = 0

        days = seconds // 86400
        seconds %= 86400

        hours = seconds // 3600
        seconds %= 3600

        minutes = seconds // 60
        seconds %= 60

        if days > 0:
            return f"{days}d {hours}h {minutes}m"

        if hours > 0:
            return f"{hours}h {minutes}m"

        if minutes > 0:
            return f"{minutes}m {seconds}s"

        return f"{seconds}s"

    except Exception:
        return "-"


def candle_time_to_iran(ms):
    try:
        dt = datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc
        ).astimezone(IRAN_TZ)

        return dt.isoformat()

    except Exception:
        return iso_now_iran()


# ============================================================
# RESET
# ============================================================

def perform_full_reset():
    global state
    global trade_history

    state = {
        "open_trades": {},
        "pending_signals": {},
        "processed_signals": {},
        "statistics": {
            "total_trades": 0,
            "wins": 0,
            "losses": 0,
            "breakeven": 0,
            "total_pnl": 0.0,
            "total_r": 0.0,
        }
    }

    trade_history = []

    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
    except Exception:
        pass

    try:
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)
    except Exception:
        pass

    print("==============================================")
    print("FULL RESET COMPLETED")
    print("Open trades      : 0")
    print("Pending signals  : 0")
    print("Trade history    : 0")
    print("Statistics       : 0")
    print("==============================================")


def initialize():
    global state
    global trade_history

    if RESET_ON_START:
        perform_full_reset()

        try:
            with open(
                RESET_MARKER_FILE,
                "w",
                encoding="utf-8"
            ) as f:
                f.write(datetime.now().isoformat())
        except Exception:
            pass

        return

    load_state()
    load_history()


# ============================================================
# STATE
# ============================================================

def save_state():
    try:
        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                state,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(f"State save error: {e}")


def load_state():
    global state

    if not os.path.exists(STATE_FILE):
        return

    try:
        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            loaded = json.load(f)

        if isinstance(loaded, dict):
            state.update(loaded)

        state.setdefault("open_trades", {})
        state.setdefault("pending_signals", {})
        state.setdefault("processed_signals", {})
        state.setdefault("statistics", {})

        stats = state["statistics"]

        stats.setdefault("total_trades", 0)
        stats.setdefault("wins", 0)
        stats.setdefault("losses", 0)
        stats.setdefault("breakeven", 0)
        stats.setdefault("total_pnl", 0.0)
        stats.setdefault("total_r", 0.0)

    except Exception as e:
        print(f"State load error: {e}")


def save_history():
    try:
        with open(
            HISTORY_FILE,
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                trade_history,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:
        print(f"History save error: {e}")


def load_history():
    global trade_history

    if not os.path.exists(HISTORY_FILE):
        trade_history = []
        return

    try:
        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:
            data = json.load(f)

        if isinstance(data, list):
            trade_history = data
        else:
            trade_history = []

    except Exception as e:
        print(f"History load error: {e}")
        trade_history = []


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):
    if (
        not TELEGRAM_BOT_TOKEN
        or TELEGRAM_BOT_TOKEN == "YOUR_BOT_TOKEN"
        or not TELEGRAM_CHAT_ID
        or TELEGRAM_CHAT_ID == "YOUR_CHAT_ID"
    ):
        print(message)
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True,
    }

    try:
        response = requests.post(
            url,
            data=payload,
            timeout=15
        )

        if response.status_code != 200:
            print(
                "Telegram error:",
                response.status_code,
                response.text
            )
            return False

        return True

    except Exception as e:
        print(f"Telegram exception: {e}")
        return False


# ============================================================
# NUMBER FORMAT
# ============================================================

def fmt_price(value):
    if value is None:
        return "-"

    try:
        value = float(value)

        if abs(value) >= 1000:
            return f"{value:.2f}"

        if abs(value) >= 100:
            return f"{value:.4f}"

        if abs(value) >= 1:
            return f"{value:.5f}"

        if abs(value) >= 0.1:
            return f"{value:.6f}"

        if abs(value) >= 0.01:
            return f"{value:.7f}"

        return f"{value:.10f}"

    except Exception:
        return str(value)


def pct(value):
    return f"{value:+.2f}%"


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol):
    try:
        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=TIMEFRAME,
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
                "volume",
            ]
        )

        for col in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        df.dropna(
            subset=[
                "open",
                "high",
                "low",
                "close"
            ],
            inplace=True
        )

        return df.reset_index(drop=True)

    except Exception as e:
        print(
            f"OHLCV error {symbol}: {e}"
        )
        return None


# ============================================================
# LIVE PRICE
# ============================================================

def fetch_live_price(symbol):
    try:
        ticker = exchange.fetch_ticker(symbol)

        last = ticker.get("last")

        if last is None:
            last = ticker.get("close")

        if last is None:
            return None

        return float(last)

    except Exception as e:
        print(
            f"Ticker error {symbol}: {e}"
        )
        return None


# ============================================================
# WILDER ATR
# ============================================================

def calculate_atr(df, period=10):
    """
    TradingView-style Wilder RMA ATR.

    First ATR value:
        SMA(TR, period)

    Next:
        RMA = ((previous_RMA * (period - 1)) + TR) / period
    """

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = pd.Series(
        index=df.index,
        dtype=float
    )

    if len(df) < period:
        return atr

    first_value = tr.iloc[:period].mean()

    atr.iloc[period - 1] = first_value

    for i in range(period, len(df)):
        prev = atr.iloc[i - 1]

        if pd.isna(prev):
            atr.iloc[i] = tr.iloc[i]
        else:
            atr.iloc[i] = (
                (
                    prev * (period - 1)
                )
                + tr.iloc[i]
            ) / period

    return atr


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(df):
    """
    TradingView-style UT Bot trailing stop.

    nLoss = Key Value * ATR

    Trailing stop logic:

    if src > prev_stop and prev_src > prev_stop:
        max(prev_stop, src - nLoss)

    elif src < prev_stop and prev_src < prev_stop:
        min(prev_stop, src + nLoss)

    elif src > prev_stop:
        src - nLoss

    else:
        src + nLoss

    BUY:
        src > stop
        AND previous src <= previous stop

    SELL:
        src < stop
        AND previous src >= previous stop
    """

    df = df.copy()

    df["atr"] = calculate_atr(
        df,
        UT_ATR_PERIOD
    )

    df["nloss"] = (
        UT_KEY * df["atr"]
    )

    df["ut_stop"] = float("nan")
    df["ut_direction"] = 0
    df["ut_buy"] = False
    df["ut_sell"] = False

    first_valid = None

    for i in range(len(df)):
        if not pd.isna(df.iloc[i]["atr"]):
            first_valid = i
            break

    if first_valid is None:
        return df

    first_close = float(
        df.iloc[first_valid]["close"]
    )

    first_nloss = float(
        df.iloc[first_valid]["nloss"]
    )

    if first_nloss <= 0:
        return df

    df.iloc[first_valid, df.columns.get_loc("ut_stop")] = (
        first_close - first_nloss
    )

    df.iloc[first_valid, df.columns.get_loc("ut_direction")] = 1

    for i in range(first_valid + 1, len(df)):

        src = float(
            df.iloc[i]["close"]
        )

        prev_src = float(
            df.iloc[i - 1]["close"]
        )

        nloss = float(
            df.iloc[i]["nloss"]
        )

        prev_stop = float(
            df.iloc[i - 1]["ut_stop"]
        )

        if (
            src > prev_stop
            and prev_src > prev_stop
        ):
            stop = max(
                prev_stop,
                src - nloss
            )

        elif (
            src < prev_stop
            and prev_src < prev_stop
        ):
            stop = min(
                prev_stop,
                src + nloss
            )

        elif src > prev_stop:
            stop = src - nloss

        else:
            stop = src + nloss

        df.iloc[
            i,
            df.columns.get_loc("ut_stop")
        ] = stop

        if src > stop:
            direction = 1

        elif src < stop:
            direction = -1

        else:
            direction = int(
                df.iloc[
                    i - 1,
                    df.columns.get_loc(
                        "ut_direction"
                    )
                ]
            )

        df.iloc[
            i,
            df.columns.get_loc(
                "ut_direction"
            )
        ] = direction

        buy = (
            src > stop
            and prev_src <= prev_stop
        )

        sell = (
            src < stop
            and prev_src >= prev_stop
        )

        df.iloc[
            i,
            df.columns.get_loc("ut_buy")
        ] = buy

        df.iloc[
            i,
            df.columns.get_loc("ut_sell")
        ] = sell

    return df


# ============================================================
# SWING DETECTION
# ============================================================

def is_swing_low(
    df,
    index,
    left=SWING_LEFT,
    right=SWING_RIGHT
):
    if index - left < 0:
        return False

    if index + right >= len(df):
        return False

    value = float(
        df.iloc[index]["low"]
    )

    left_values = [
        float(df.iloc[j]["low"])
        for j in range(
            index - left,
            index
        )
    ]

    right_values = [
        float(df.iloc[j]["low"])
        for j in range(
            index + 1,
            index + right + 1
        )
    ]

    return (
        value < min(left_values)
        and value <= min(right_values)
    )


def is_swing_high(
    df,
    index,
    left=SWING_LEFT,
    right=SWING_RIGHT
):
    if index - left < 0:
        return False

    if index + right >= len(df):
        return False

    value = float(
        df.iloc[index]["high"]
    )

    left_values = [
        float(df.iloc[j]["high"])
        for j in range(
            index - left,
            index
        )
    ]

    right_values = [
        float(df.iloc[j]["high"])
        for j in range(
            index + 1,
            index + right + 1
        )
    ]

    return (
        value > max(left_values)
        and value >= max(right_values)
    )


def find_last_valid_swing_low(
    df,
    before_index
):
    """
    Find the latest confirmed swing low
    before the confirmation candle.

    We do not use future candles beyond the
    information that existed at confirmation.
    """

    last_index = before_index - SWING_RIGHT

    for i in range(
        last_index,
        SWING_LEFT - 1,
        -1
    ):
        if is_swing_low(
            df,
            i,
            SWING_LEFT,
            SWING_RIGHT
        ):
            return float(
                df.iloc[i]["low"]
            ), i

    return None, None


def find_last_valid_swing_high(
    df,
    before_index
):
    last_index = before_index - SWING_RIGHT

    for i in range(
        last_index,
        SWING_LEFT - 1,
        -1
    ):
        if is_swing_high(
            df,
            i,
            SWING_LEFT,
            SWING_RIGHT
        ):
            return float(
                df.iloc[i]["high"]
            ), i

    return None, None


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(
    side,
    entry,
    current
):
    if side == "LONG":
        return (
            (current - entry)
            / entry
        ) * 100

    return (
        (entry - current)
        / entry
    ) * 100


# ============================================================
# STATISTICS
# ============================================================

def get_statistics():
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

    pnl = float(
        stats.get("total_pnl", 0.0)
    )

    total_r = float(
        stats.get("total_r", 0.0)
    )

    if total > 0:
        win_rate = (
            wins / total
        ) * 100
    else:
        win_rate = 0.0

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "pnl": pnl,
        "r": total_r,
        "win_rate": win_rate,
    }


# ============================================================
# TELEGRAM SIGNAL
# ============================================================

def telegram_ut_signal(
    symbol,
    side,
    signal_price,
    signal_high,
    signal_low,
    ut_stop,
    signal_time
):
    emoji = "🟢" if side == "BUY" else "🔴"

    message = (
        f"{emoji} <b>UT BOT {side}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{symbol}</b>\n"
        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"
        f"🤖 UT Bot: <b>Key {UT_KEY} / ATR {UT_ATR_PERIOD}</b>\n"
        f"📌 Signal candle: <b>{fmt_price(signal_price)}</b>\n"
        f"🔺 High: <b>{fmt_price(signal_high)}</b>\n"
        f"🔻 Low: <b>{fmt_price(signal_low)}</b>\n"
        f"🛑 UT Stop: <b>{fmt_price(ut_stop)}</b>\n"
        f"🕐 Signal Time: <b>{format_iran_time(signal_time)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>WAITING FOR CONFIRMATION CLOSE</b>"
    )

    send_telegram(message)


# ============================================================
# TELEGRAM TRADE OPEN
# ============================================================

def telegram_trade_open(trade):
    side = trade["side"]

    emoji = "🟢" if side == "LONG" else "🔴"

    entry = float(trade["entry"])
    sl = float(trade["sl"])
    tp = float(trade["tp"])

    if side == "LONG":
        sl_pct = (
            (sl - entry)
            / entry
        ) * 100

        tp_pct = (
            (tp - entry)
            / entry
        ) * 100

    else:
        sl_pct = (
            (sl - entry)
            / entry
        ) * 100

        tp_pct = (
            (tp - entry)
            / entry
        ) * 100

    message = (
        f"{emoji} <b>{side} ENTRY</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{trade['symbol']}</b>\n"
        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"
        f"🤖 UT Bot: <b>{trade['ut_signal']}</b>\n"
        f"🎯 Entry: <b>{fmt_price(entry)}</b>\n"
        f"🛑 SL: <b>{fmt_price(sl)}</b> "
        f"({pct(sl_pct)})\n"
        f"💰 TP: <b>{fmt_price(tp)}</b> "
        f"({pct(tp_pct)})\n"
        f"📐 RR: <b>1:1</b>\n"
        f"🔻 Swing Low: "
        f"<b>{fmt_price(trade.get('swing_low'))}</b>\n"
        f"🔺 Swing High: "
        f"<b>{fmt_price(trade.get('swing_high'))}</b>\n"
        f"🕐 Signal Time: "
        f"<b>{format_iran_time(trade['signal_time'])}</b>\n"
        f"🕐 Open Time: "
        f"<b>{format_iran_time(trade['opened_at'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔒 <b>SL / TP FIXED</b>"
    )

    send_telegram(message)


# ============================================================
# TELEGRAM EXIT
# ============================================================

def telegram_trade_exit(
    trade,
    exit_price,
    result,
    pnl_pct,
    r_multiple
):
    if result == "WIN":
        emoji = "✅"
    elif result == "LOSS":
        emoji = "❌"
    else:
        emoji = "➖"

    stats = get_statistics()

    message = (
        f"{emoji} <b>TRADE CLOSED - {result}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{trade['symbol']}</b>\n"
        f"📊 Side: <b>{trade['side']}</b>\n"
        f"🎯 Entry: <b>{fmt_price(trade['entry'])}</b>\n"
        f"🚪 Exit: <b>{fmt_price(exit_price)}</b>\n"
        f"🛑 SL: <b>{fmt_price(trade['sl'])}</b>\n"
        f"💰 TP: <b>{fmt_price(trade['tp'])}</b>\n"
        f"📈 P&L: <b>{pct(pnl_pct)}</b>\n"
        f"📐 R: <b>{r_multiple:+.2f}R</b>\n"
        f"🕐 Open Time: "
        f"<b>{format_iran_time(trade['opened_at'])}</b>\n"
        f"⏱ Duration: "
        f"<b>{format_duration(trade['opened_at'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>CUMULATIVE PERFORMANCE</b>\n"
        f"Trades: <b>{stats['total']}</b>\n"
        f"🟢 Wins: <b>{stats['wins']}</b>\n"
        f"🔴 Losses: <b>{stats['losses']}</b>\n"
        f"⚪ BE: <b>{stats['breakeven']}</b>\n"
        f"🏆 Win Rate: <b>{stats['win_rate']:.2f}%</b>\n"
        f"💵 Total P&L: <b>{stats['pnl']:+.2f}%</b>\n"
        f"📐 Total R: <b>{stats['r']:+.2f}R</b>"
    )

    send_telegram(message)


# ============================================================
# OPEN TRADE
# ============================================================

def create_trade(
    symbol,
    side,
    entry,
    sl,
    tp,
    signal_time,
    confirmation_time,
    ut_signal,
    swing_low=None,
    swing_high=None
):
    return {
        "symbol": symbol,
        "side": side,
        "ut_signal": ut_signal,

        "entry": float(entry),
        "sl": float(sl),
        "tp": float(tp),

        "signal_time": signal_time,
        "opened_at": confirmation_time,

        "confirmation_time": confirmation_time,

        "swing_low": (
            float(swing_low)
            if swing_low is not None
            else None
        ),

        "swing_high": (
            float(swing_high)
            if swing_high is not None
            else None
        ),

        "status": "OPEN",
    }


# ============================================================
# PROCESS CONFIRMATION
# ============================================================

def process_pending_signal(
    symbol,
    df
):
    pending = state["pending_signals"].get(symbol)

    if not pending:
        return False

    # --------------------------------------------------------
    # Last CLOSED candle
    # --------------------------------------------------------

    if len(df) < 5:
        return False

    confirmation_index = len(df) - 2

    confirmation = df.iloc[
        confirmation_index
    ]

    confirmation_time = candle_time_to_iran(
        int(confirmation["timestamp"])
    )

    confirmation_close = float(
        confirmation["close"]
    )

    signal_side = pending["side"]

    signal_high = float(
        pending["signal_high"]
    )

    signal_low = float(
        pending["signal_low"]
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # BUY -> ONLY LONG
    # SELL -> ONLY SHORT
    #
    # If UT has already produced an opposite signal,
    # cancel the old pending setup.
    # --------------------------------------------------------

    if bool(confirmation.get("ut_buy", False)):

        if signal_side == "SHORT":
            del state["pending_signals"][symbol]
            save_state()
            return False

    if bool(confirmation.get("ut_sell", False)):

        if signal_side == "LONG":
            del state["pending_signals"][symbol]
            save_state()
            return False

    # --------------------------------------------------------
    # LONG CONFIRMATION
    #
    # Close must be ABOVE HIGH of UT BUY candle.
    # --------------------------------------------------------

    if signal_side == "LONG":

        if confirmation_close <= signal_high:
            return False

        # Find latest confirmed swing low
        swing_low, swing_index = (
            find_last_valid_swing_low(
                df,
                confirmation_index
            )
        )

        if swing_low is None:
            print(
                f"{symbol}: "
                f"LONG confirmation but no valid swing low."
            )
            return False

        # SL slightly below swing low
        sl = (
            swing_low
            * (
                1
                - SL_BUFFER_PERCENT / 100
            )
        )

        entry = confirmation_close

        # SL must be below entry
        if sl >= entry:
            print(
                f"{symbol}: invalid LONG SL"
            )
            return False

        risk = entry - sl

        if risk <= 0:
            return False

        tp = entry + (
            risk * RR
        )

        trade = create_trade(
            symbol=symbol,
            side="LONG",
            entry=entry,
            sl=sl,
            tp=tp,
            signal_time=pending["signal_time"],
            confirmation_time=confirmation_time,
            ut_signal="BUY",
            swing_low=swing_low,
            swing_high=None
        )

        state["open_trades"][symbol] = trade

        del state["pending_signals"][symbol]

        save_state()

        telegram_trade_open(trade)

        return True

    # --------------------------------------------------------
    # SHORT CONFIRMATION
    #
    # Close must be BELOW LOW of UT SELL candle.
    # --------------------------------------------------------

    if signal_side == "SHORT":

        if confirmation_close >= signal_low:
            return False

        # Find latest confirmed swing high
        swing_high, swing_index = (
            find_last_valid_swing_high(
                df,
                confirmation_index
            )
        )

        if swing_high is None:
            print(
                f"{symbol}: "
                f"SHORT confirmation but no valid swing high."
            )
            return False

        # SL slightly above swing high
        sl = (
            swing_high
            * (
                1
                + SL_BUFFER_PERCENT / 100
            )
        )

        entry = confirmation_close

        # SL must be above entry
        if sl <= entry:
            print(
                f"{symbol}: invalid SHORT SL"
            )
            return False

        risk = sl - entry

        if risk <= 0:
            return False

        tp = entry - (
            risk * RR
        )

        trade = create_trade(
            symbol=symbol,
            side="SHORT",
            entry=entry,
            sl=sl,
            tp=tp,
            signal_time=pending["signal_time"],
            confirmation_time=confirmation_time,
            ut_signal="SELL",
            swing_low=None,
            swing_high=swing_high
        )

        state["open_trades"][symbol] = trade

        del state["pending_signals"][symbol]

        save_state()

        telegram_trade_open(trade)

        return True

    return False


# ============================================================
# DETECT NEW UT SIGNAL
# ============================================================

def detect_new_ut_signal(
    symbol,
    df
):
    if len(df) < 30:
        return None

    signal_index = len(df) - 2

    signal = df.iloc[
        signal_index
    ]

    signal_time = candle_time_to_iran(
        int(signal["timestamp"])
    )

    signal_key = str(
        int(signal["timestamp"])
    )

    # Already processed?
    if (
        state["processed_signals"].get(symbol)
        == signal_key
    ):
        return None

    side = None

    # --------------------------------------------------------
    # UT BUY
    # --------------------------------------------------------

    if bool(signal["ut_buy"]):

        side = "LONG"

    # --------------------------------------------------------
    # UT SELL
    # --------------------------------------------------------

    elif bool(signal["ut_sell"]):

        side = "SHORT"

    else:
        # Even if there is no new signal,
        # remember this candle as checked.
        state["processed_signals"][symbol] = signal_key
        save_state()
        return None

    # Mark processed
    state["processed_signals"][symbol] = signal_key

    pending = {
        "symbol": symbol,
        "side": side,

        "signal_time": signal_time,

        "signal_timestamp": int(
            signal["timestamp"]
        ),

        "signal_close": float(
            signal["close"]
        ),

        "signal_high": float(
            signal["high"]
        ),

        "signal_low": float(
            signal["low"]
        ),

        "ut_stop": float(
            signal["ut_stop"]
        ),
    }

    state["pending_signals"][symbol] = pending

    save_state()

    telegram_ut_signal(
        symbol=symbol,
        side="BUY" if side == "LONG" else "SELL",
        signal_price=float(signal["close"]),
        signal_high=float(signal["high"]),
        signal_low=float(signal["low"]),
        ut_stop=float(signal["ut_stop"]),
        signal_time=signal_time
    )

    return pending


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    symbol,
    df
):
    trade = state["open_trades"].get(symbol)

    if not trade:
        return False

    if len(df) < 3:
        return False

    # ONLY LAST CLOSED CANDLE
    candle = df.iloc[-2]

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

    side = trade["side"]

    exit_price = None
    result = None
    r_multiple = 0.0

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        hit_sl = low <= sl
        hit_tp = high >= tp

        # If both happen on same candle,
        # conservative assumption = SL first.
        if hit_sl:
            exit_price = sl
            result = "LOSS"
            r_multiple = -1.0

        elif hit_tp:
            exit_price = tp
            result = "WIN"
            r_multiple = RR

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    elif side == "SHORT":

        hit_sl = high >= sl
        hit_tp = low <= tp

        # Conservative if both are touched
        if hit_sl:
            exit_price = sl
            result = "LOSS"
            r_multiple = -1.0

        elif hit_tp:
            exit_price = tp
            result = "WIN"
            r_multiple = RR

    if exit_price is None:
        return False

    # --------------------------------------------------------
    # PNL
    # --------------------------------------------------------

    pnl_pct = calculate_trade_pnl(
        side,
        entry,
        exit_price
    )

    # --------------------------------------------------------
    # UPDATE CUMULATIVE STATS
    # --------------------------------------------------------

    stats = state["statistics"]

    stats["total_trades"] += 1

    if result == "WIN":
        stats["wins"] += 1

    elif result == "LOSS":
        stats["losses"] += 1

    else:
        stats["breakeven"] += 1

    stats["total_pnl"] += pnl_pct
    stats["total_r"] += r_multiple

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    closed_trade = dict(trade)

    closed_trade["exit_price"] = float(
        exit_price
    )

    closed_trade["result"] = result

    closed_trade["pnl_pct"] = float(
        pnl_pct
    )

    closed_trade["r_multiple"] = float(
        r_multiple
    )

    closed_trade["closed_at"] = iso_now_iran()

    closed_trade["duration"] = format_duration(
        trade["opened_at"]
    )

    trade_history.append(
        closed_trade
    )

    # --------------------------------------------------------
    # REMOVE OPEN TRADE
    # --------------------------------------------------------

    del state["open_trades"][symbol]

    save_state()
    save_history()

    telegram_trade_exit(
        trade=trade,
        exit_price=exit_price,
        result=result,
        pnl_pct=pnl_pct,
        r_multiple=r_multiple
    )

    return True


# ============================================================
# CURRENT OPEN PNL
# ============================================================

def calculate_open_pnl():
    results = []

    for symbol, trade in state[
        "open_trades"
    ].items():

        current = fetch_live_price(
            symbol
        )

        if current is None:
            continue

        entry = float(
            trade["entry"]
        )

        pnl = calculate_trade_pnl(
            trade["side"],
            entry,
            current
        )

        results.append({
            "symbol": symbol,
            "side": trade["side"],
            "entry": entry,
            "current": current,
            "pnl": pnl,
            "sl": float(trade["sl"]),
            "tp": float(trade["tp"]),
            "opened_at": trade["opened_at"],
        })

    return results


# ============================================================
# REPORT
# ============================================================

def build_report():
    stats = get_statistics()

    lines = []

    lines.append(
        "📡 <b>CRYPTO UT BOT REPORT</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"🕐 {now_iran().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    lines.append(
        f"⏱ Timeframe: <b>{TIMEFRAME}</b>"
    )

    lines.append(
        f"🤖 UT Bot: "
        f"<b>Key {UT_KEY} / ATR {UT_ATR_PERIOD}</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # CUMULATIVE
    # --------------------------------------------------------

    lines.append(
        "📊 <b>CUMULATIVE PERFORMANCE</b>"
    )

    lines.append(
        f"📈 Total Trades: <b>{stats['total']}</b>"
    )

    lines.append(
        f"🟢 Wins: <b>{stats['wins']}</b>"
    )

    lines.append(
        f"🔴 Losses: <b>{stats['losses']}</b>"
    )

    lines.append(
        f"⚪ Breakeven: <b>{stats['breakeven']}</b>"
    )

    lines.append(
        f"🏆 Win Rate: "
        f"<b>{stats['win_rate']:.2f}%</b>"
    )

    lines.append(
        f"💵 Total P&L: "
        f"<b>{stats['pnl']:+.2f}%</b>"
    )

    lines.append(
        f"📐 Total R: "
        f"<b>{stats['r']:+.2f}R</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PENDING
    # --------------------------------------------------------

    pending_count = len(
        state["pending_signals"]
    )

    lines.append(
        f"⏳ Pending UT Signals: "
        f"<b>{pending_count}</b>"
    )

    if pending_count > 0:

        for symbol, pending in (
            state["pending_signals"].items()
        ):

            if pending["side"] == "LONG":
                emoji = "🟢"
                direction = "LONG"
                condition = (
                    f"Close > "
                    f"{fmt_price(pending['signal_high'])}"
                )
            else:
                emoji = "🔴"
                direction = "SHORT"
                condition = (
                    f"Close < "
                    f"{fmt_price(pending['signal_low'])}"
                )

            lines.append(
                f"\n{emoji} <b>{symbol}</b>"
            )

            lines.append(
                f"UT: <b>{direction}</b>"
            )

            lines.append(
                f"🎯 Signal Close: "
                f"<b>{fmt_price(pending['signal_close'])}</b>"
            )

            lines.append(
                f"📌 Confirmation: "
                f"<b>{condition}</b>"
            )

            lines.append(
                f"🕐 Signal: "
                f"<b>{format_iran_time(pending['signal_time'])}</b>"
            )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    open_results = calculate_open_pnl()

    lines.append(
        "\n━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📂 <b>OPEN TRADES: "
        f"{len(open_results)}</b>"
    )

    if not open_results:

        lines.append(
            "⚪ No open trades"
        )

    else:

        total_open_pnl = 0.0

        for item in open_results:

            total_open_pnl += item["pnl"]

            emoji = (
                "🟢"
                if item["side"] == "LONG"
                else "🔴"
            )

            entry = item["entry"]
            sl = item["sl"]
            tp = item["tp"]

            if item["side"] == "LONG":

                sl_pct = (
                    (sl - entry)
                    / entry
                ) * 100

                tp_pct = (
                    (tp - entry)
                    / entry
                ) * 100

            else:

                sl_pct = (
                    (sl - entry)
                    / entry
                ) * 100

                tp_pct = (
                    (tp - entry)
                    / entry
                ) * 100

            lines.append(
                f"\n{emoji} "
                f"<b>{item['symbol']}</b>"
            )

            lines.append(
                f"Side: <b>{item['side']}</b>"
            )

            lines.append(
                f"Entry: <b>{fmt_price(entry)}</b>"
            )

            lines.append(
                f"Current: <b>{fmt_price(item['current'])}</b>"
            )

            lines.append(
                f"P&L: <b>{pct(item['pnl'])}</b>"
            )

            lines.append(
                f"🛑 SL: "
                f"<b>{fmt_price(sl)}</b> "
                f"({pct(sl_pct)})"
            )

            lines.append(
                f"💰 TP: "
                f"<b>{fmt_price(tp)}</b> "
                f"({pct(tp_pct)})"
            )

            lines.append(
                f"🕐 Open: "
                f"<b>{format_iran_time(item['opened_at'])}</b>"
            )

            lines.append(
                f"⏱ Duration: "
                f"<b>{format_duration(item['opened_at'])}</b>"
            )

        lines.append(
            f"\n📊 Open P&L: "
            f"<b>{total_open_pnl:+.2f}%</b>"
        )

    return "\n".join(lines)


# ============================================================
# SEND REPORT
# ============================================================

def send_report():
    message = build_report()

    send_telegram(message)


# ============================================================
# TOP 30 SYMBOLS
# ============================================================

def get_top_symbols():
    try:
        markets = exchange.load_markets()

        candidates = []

        for symbol, market in markets.items():

            try:

                if not market.get("active", True):
                    continue

                # USD quoted linear perpetual
                if market.get("linear") is not True:
                    continue

                if market.get("quote") != "USD":
                    continue

                if market.get("settle") != "USD":
                    continue

                if market.get("swap") is not True:
                    continue

                candidates.append(
                    symbol
                )

            except Exception:
                continue

        if not candidates:
            return []

        tickers = exchange.fetch_tickers(
            candidates
        )

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

            try:
                quote_volume = float(
                    quote_volume
                )
            except Exception:
                continue

            ranked.append(
                (
                    symbol,
                    quote_volume
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

        print(
            f"Top symbols error: {e}"
        )

        return []


# ============================================================
# SCAN SYMBOL
# ============================================================

def scan_symbol(symbol):
    try:

        df = fetch_ohlcv(symbol)

        if df is None:
            return

        if len(df) < 50:
            return

        # ----------------------------------------------------
        # Calculate UT
        # ----------------------------------------------------

        df = calculate_ut_bot(df)

        # ----------------------------------------------------
        # 1. Existing open trade
        # ----------------------------------------------------

        if symbol in state["open_trades"]:

            check_open_trade(
                symbol,
                df
            )

            # IMPORTANT:
            # If trade is still open, don't create
            # another trade on the same symbol.
            if symbol in state["open_trades"]:
                return

        # ----------------------------------------------------
        # 2. Pending signal
        # ----------------------------------------------------

        if symbol in state["pending_signals"]:

            process_pending_signal(
                symbol,
                df
            )

            # If still pending, stop here.
            if symbol in state["pending_signals"]:
                return

            # If a trade was opened,
            # don't search for another signal.
            if symbol in state["open_trades"]:
                return

        # ----------------------------------------------------
        # 3. Detect NEW UT signal
        # ----------------------------------------------------

        detect_new_ut_signal(
            symbol,
            df
        )

    except Exception as e:

        print(
            f"Scan error {symbol}: {e}"
        )

        traceback.print_exc()


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():
    print(
        "\n=============================================="
    )

    print(
        f"SCAN {now_iran().strftime('%Y-%m-%d %H:%M:%S')} IRAN"
    )

    print(
        "=============================================="
    )

    symbols = get_top_symbols()

    print(
        f"Top symbols: {len(symbols)}"
    )

    if not symbols:
        print(
            "No symbols found."
        )
        return

    # --------------------------------------------------------
    # Always keep symbols with open/pending state
    # even if they temporarily leave top 30.
    # --------------------------------------------------------

    important_symbols = set(
        state["open_trades"].keys()
    )

    important_symbols.update(
        state["pending_signals"].keys()
    )

    for symbol in important_symbols:
        if symbol not in symbols:
            symbols.append(symbol)

    for symbol in symbols:

        print(
            f"Scanning {symbol}"
        )

        scan_symbol(symbol)

        # Small delay to avoid unnecessary pressure
        time.sleep(0.15)

    save_state()
    save_history()

    print(
        "Scan completed."
    )


# ============================================================
# STARTUP REPORT
# ============================================================

def send_startup_report():
    message = (
        "🚀 <b>CRYPTO UT BOT SCANNER STARTED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"
        f"🤖 UT Bot: "
        f"<b>Key {UT_KEY} / ATR {UT_ATR_PERIOD}</b>\n"
        f"🎯 RR: <b>1:1</b>\n"
        f"📊 Top Coins: <b>{TOP_COINS}</b>\n"
        f"🕐 Timezone: <b>Iran</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🟢 BUY → ONLY LONG\n"
        "🔴 SELL → ONLY SHORT\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "⏳ Entry requires confirmation CLOSE\n"
        "🟢 LONG: Close > BUY candle HIGH\n"
        "🔴 SHORT: Close < SELL candle LOW\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🛑 SL: beyond latest valid swing\n"
        "💰 TP: 1R\n"
        "🔒 SL / TP: FIXED\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "♻️ Statistics RESET = 0"
    )

    send_telegram(message)


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    print(
        "=============================================="
    )

    print(
        "CRYPTO UT BOT SCANNER v12.0"
    )

    print(
        "=============================================="
    )

    initialize()

    send_startup_report()

    # First scan immediately
    try:
        run_scan()
        send_report()

    except Exception as e:

        print(
            f"Initial scan error: {e}"
        )

        traceback.print_exc()

    # --------------------------------------------------------
    # Continuous loop
    # --------------------------------------------------------

    while True:

        try:

            time.sleep(
                SCAN_INTERVAL_SECONDS
            )

            run_scan()

            # Report after each scan
            send_report()

        except KeyboardInterrupt:

            print(
                "Scanner stopped."
            )
            break

        except Exception as e:

            print(
                f"MAIN LOOP ERROR: {e}"
            )

            traceback.print_exc()

            time.sleep(10)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
