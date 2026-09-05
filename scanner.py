# ============================================================
# CRYPTO UT BOT SCANNER v12.2
# ============================================================
# Kraken Futures
# 15M CLOSED CANDLES
#
# GitHub Actions:
#   scanner.py runs ONCE per execution
#   GitHub Actions runs it every 5 minutes
#
# UT BOT:
#   Key Value = 3
#   ATR Period = 10
#
# ENTRY:
#   BUY  -> Wait for later CLOSED candle CLOSE > BUY candle HIGH
#   SELL -> Wait for later CLOSED candle CLOSE < SELL candle LOW
#
# SL:
#   Latest confirmed valid swing
#
# TP:
#   1R
#
# STATE:
#   Cumulative statistics preserved
#   Open trades preserved
#   Pending signals preserved
#
# ============================================================

import os
import json
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

SL_BUFFER_PERCENT = 0.10

SWING_LEFT = 2
SWING_RIGHT = 2

OHLCV_LIMIT = 250


# ============================================================
# FILES
# ============================================================

STATE_FILE = "ut_bot_state.json"
HISTORY_FILE = "ut_bot_trade_history.json"

RESET_ON_START = False

RESET_MARKER_FILE = "ut_bot_reset_done.txt"


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
# DEFAULT STATE
# ============================================================

def create_default_state():
    return {
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


state = create_default_state()

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


def candle_time_to_iran(ms):

    try:
        return datetime.fromtimestamp(
            ms / 1000,
            tz=timezone.utc
        ).astimezone(IRAN_TZ).isoformat()

    except Exception:
        return iso_now_iran()


def format_iran_time(value):

    if not value:
        return "-"

    try:

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IRAN_TZ)

        return dt.astimezone(
            IRAN_TZ
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )

    except Exception:
        return str(value)


def parse_time(value):

    try:

        dt = datetime.fromisoformat(value)

        if dt.tzinfo is None:
            dt = dt.replace(
                tzinfo=IRAN_TZ
            )

        return dt

    except Exception:
        return now_iran()


def format_duration(start_time):

    if not start_time:
        return "-"

    try:

        seconds = int(
            (
                now_iran()
                - parse_time(start_time)
            ).total_seconds()
        )

        seconds = max(
            seconds,
            0
        )

        days = seconds // 86400
        seconds %= 86400

        hours = seconds // 3600
        seconds %= 3600

        minutes = seconds // 60
        seconds %= 60

        if days:
            return (
                f"{days}d "
                f"{hours}h "
                f"{minutes}m"
            )

        if hours:
            return (
                f"{hours}h "
                f"{minutes}m"
            )

        if minutes:
            return (
                f"{minutes}m "
                f"{seconds}s"
            )

        return f"{seconds}s"

    except Exception:
        return "-"


# ============================================================
# RESET
# ============================================================

def perform_full_reset():

    global state
    global trade_history

    state = create_default_state()

    trade_history = []

    for filename in [
        STATE_FILE,
        HISTORY_FILE
    ]:

        try:

            if os.path.exists(filename):
                os.remove(filename)

        except Exception as e:

            print(
                f"Delete error {filename}: {e}"
            )

    print(
        "=========================================="
    )
    print(
        "FULL RESET COMPLETED"
    )
    print(
        "=========================================="
    )


# ============================================================
# STATE SAVE
# ============================================================

def save_state():

    try:

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

        os.replace(
            temp_file,
            STATE_FILE
        )

    except Exception as e:

        print(
            f"State save error: {e}"
        )


def save_history():

    try:

        temp_file = HISTORY_FILE + ".tmp"

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                trade_history,
                f,
                ensure_ascii=False,
                indent=2
            )

        os.replace(
            temp_file,
            HISTORY_FILE
        )

    except Exception as e:

        print(
            f"History save error: {e}"
        )


# ============================================================
# STATE LOAD
# ============================================================

def load_state():

    global state

    if not os.path.exists(
        STATE_FILE
    ):

        print(
            "State file not found."
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            loaded = json.load(f)

        default = create_default_state()

        if not isinstance(
            loaded,
            dict
        ):

            state = default
            return

        state = default

        for key in [
            "open_trades",
            "pending_signals",
            "processed_signals"
        ]:

            if isinstance(
                loaded.get(key),
                dict
            ):

                state[key] = loaded[key]

        loaded_stats = loaded.get(
            "statistics",
            {}
        )

        if isinstance(
            loaded_stats,
            dict
        ):

            for key in [
                "total_trades",
                "wins",
                "losses",
                "breakeven",
                "total_pnl",
                "total_r"
            ]:

                if key in loaded_stats:

                    state[
                        "statistics"
                    ][key] = loaded_stats[key]

        stats = state[
            "statistics"
        ]

        stats["total_trades"] = int(
            stats.get(
                "total_trades",
                0
            )
        )

        stats["wins"] = int(
            stats.get(
                "wins",
                0
            )
        )

        stats["losses"] = int(
            stats.get(
                "losses",
                0
            )
        )

        stats["breakeven"] = int(
            stats.get(
                "breakeven",
                0
            )
        )

        stats["total_pnl"] = float(
            stats.get(
                "total_pnl",
                0
            )
        )

        stats["total_r"] = float(
            stats.get(
                "total_r",
                0
            )
        )

        print(
            "State loaded successfully."
        )

    except Exception as e:

        print(
            f"State load error: {e}"
        )

        state = create_default_state()


def load_history():

    global trade_history

    if not os.path.exists(
        HISTORY_FILE
    ):

        trade_history = []

        print(
            "History file not found."
        )

        return

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(
            data,
            list
        ):

            trade_history = data

        else:

            trade_history = []

        print(
            f"History loaded: "
            f"{len(trade_history)}"
        )

    except Exception as e:

        print(
            f"History load error: {e}"
        )

        trade_history = []


# ============================================================
# INITIALIZE
# ============================================================

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

                f.write(
                    datetime.now().isoformat()
                )

        except Exception as e:

            print(
                f"Reset marker error: {e}"
            )

        return

    load_state()
    load_history()

    stats = state[
        "statistics"
    ]

    print(
        "=========================================="
    )

    print(
        "STATE LOADED"
    )

    print(
        f"Open trades: "
        f"{len(state['open_trades'])}"
    )

    print(
        f"Pending signals: "
        f"{len(state['pending_signals'])}"
    )

    print(
        f"Processed signals: "
        f"{len(state['processed_signals'])}"
    )

    print(
        f"History: "
        f"{len(trade_history)}"
    )

    print(
        f"Trades: "
        f"{stats['total_trades']}"
    )

    print(
        f"Wins: "
        f"{stats['wins']}"
    )

    print(
        f"Losses: "
        f"{stats['losses']}"
    )

    print(
        f"Total P&L: "
        f"{stats['total_pnl']:+.2f}%"
    )

    print(
        f"Total R: "
        f"{stats['total_r']:+.2f}R"
    )

    print(
        "=========================================="
    )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials missing."
        )

        print(message)

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }

    try:

        response = requests.post(
            url,
            data=payload,
            timeout=20
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

        print(
            f"Telegram exception: {e}"
        )

        return False


# ============================================================
# FORMAT
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

    return f"{float(value):+.2f}%"


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
                "volume"
            ]
        )

        for column in [
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]:

            df[column] = pd.to_numeric(
                df[column],
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

        return df.reset_index(
            drop=True
        )

    except Exception as e:

        print(
            f"OHLCV error "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# LIVE PRICE
# ============================================================

def fetch_live_price(symbol):

    try:

        ticker = exchange.fetch_ticker(
            symbol
        )

        price = ticker.get(
            "last"
        )

        if price is None:

            price = ticker.get(
                "close"
            )

        if price is None:
            return None

        return float(price)

    except Exception as e:

        print(
            f"Ticker error "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=10
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
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    atr = pd.Series(
        index=df.index,
        dtype=float
    )

    if len(df) < period:
        return atr

    atr.iloc[
        period - 1
    ] = tr.iloc[
        :period
    ].mean()

    for i in range(
        period,
        len(df)
    ):

        atr.iloc[i] = (
            (
                atr.iloc[i - 1]
                * (period - 1)
            )
            + tr.iloc[i]
        ) / period

    return atr


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(df):

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

    for i in range(
        len(df)
    ):

        if not pd.isna(
            df.iloc[i]["atr"]
        ):

            first_valid = i
            break

    if first_valid is None:
        return df

    close = float(
        df.iloc[first_valid]["close"]
    )

    nloss = float(
        df.iloc[first_valid]["nloss"]
    )

    if nloss <= 0:
        return df

    df.loc[
        df.index[first_valid],
        "ut_stop"
    ] = close - nloss

    df.loc[
        df.index[first_valid],
        "ut_direction"
    ] = 1

    for i in range(
        first_valid + 1,
        len(df)
    ):

        src = float(
            df.iloc[i]["close"]
        )

        previous_src = float(
            df.iloc[i - 1]["close"]
        )

        nloss = float(
            df.iloc[i]["nloss"]
        )

        previous_stop = float(
            df.iloc[i - 1]["ut_stop"]
        )

        if (
            src > previous_stop
            and previous_src > previous_stop
        ):

            stop = max(
                previous_stop,
                src - nloss
            )

        elif (
            src < previous_stop
            and previous_src < previous_stop
        ):

            stop = min(
                previous_stop,
                src + nloss
            )

        elif src > previous_stop:

            stop = src - nloss

        else:

            stop = src + nloss

        df.loc[
            df.index[i],
            "ut_stop"
        ] = stop

        if src > stop:

            direction = 1

        elif src < stop:

            direction = -1

        else:

            direction = int(
                df.iloc[
                    i - 1
                ]["ut_direction"]
            )

        df.loc[
            df.index[i],
            "ut_direction"
        ] = direction

        buy = (
            src > stop
            and previous_src <= previous_stop
        )

        sell = (
            src < stop
            and previous_src >= previous_stop
        )

        df.loc[
            df.index[i],
            "ut_buy"
        ] = buy

        df.loc[
            df.index[i],
            "ut_sell"
        ] = sell

    return df


# ============================================================
# SWING LOW
# ============================================================

def is_swing_low(
    df,
    index
):

    if index - SWING_LEFT < 0:
        return False

    if index + SWING_RIGHT >= len(df):
        return False

    value = float(
        df.iloc[index]["low"]
    )

    left = [
        float(
            df.iloc[i]["low"]
        )
        for i in range(
            index - SWING_LEFT,
            index
        )
    ]

    right = [
        float(
            df.iloc[i]["low"]
        )
        for i in range(
            index + 1,
            index + SWING_RIGHT + 1
        )
    ]

    return (
        value < min(left)
        and value <= min(right)
    )


# ============================================================
# SWING HIGH
# ============================================================

def is_swing_high(
    df,
    index
):

    if index - SWING_LEFT < 0:
        return False

    if index + SWING_RIGHT >= len(df):
        return False

    value = float(
        df.iloc[index]["high"]
    )

    left = [
        float(
            df.iloc[i]["high"]
        )
        for i in range(
            index - SWING_LEFT,
            index
        )
    ]

    right = [
        float(
            df.iloc[i]["high"]
        )
        for i in range(
            index + 1,
            index + SWING_RIGHT + 1
        )
    ]

    return (
        value > max(left)
        and value >= max(right)
    )


# ============================================================
# LAST CONFIRMED SWING LOW
# ============================================================

def find_last_valid_swing_low(
    df,
    before_index
):

    last_index = (
        before_index - SWING_RIGHT
    )

    for i in range(
        last_index,
        SWING_LEFT - 1,
        -1
    ):

        if is_swing_low(
            df,
            i
        ):

            return (
                float(
                    df.iloc[i]["low"]
                ),
                i
            )

    return None, None


# ============================================================
# LAST CONFIRMED SWING HIGH
# ============================================================

def find_last_valid_swing_high(
    df,
    before_index
):

    last_index = (
        before_index - SWING_RIGHT
    )

    for i in range(
        last_index,
        SWING_LEFT - 1,
        -1
    ):

        if is_swing_high(
            df,
            i
        ):

            return (
                float(
                    df.iloc[i]["high"]
                ),
                i
            )

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

    stats = state[
        "statistics"
    ]

    total = int(
        stats.get(
            "total_trades",
            0
        )
    )

    wins = int(
        stats.get(
            "wins",
            0
        )
    )

    losses = int(
        stats.get(
            "losses",
            0
        )
    )

    breakeven = int(
        stats.get(
            "breakeven",
            0
        )
    )

    pnl = float(
        stats.get(
            "total_pnl",
            0
        )
    )

    total_r = float(
        stats.get(
            "total_r",
            0
        )
    )

    win_rate = (
        wins / total * 100
        if total > 0
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "breakeven": breakeven,
        "pnl": pnl,
        "r": total_r,
        "win_rate": win_rate
    }


# ============================================================
# TELEGRAM - UT SIGNAL
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

    emoji = (
        "🟢"
        if side == "BUY"
        else "🔴"
    )

    message = (
        f"{emoji} <b>UT BOT {side}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{symbol}</b>\n"
        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"
        f"🤖 UT Bot: "
        f"<b>Key {UT_KEY} / ATR {UT_ATR_PERIOD}</b>\n"
        f"📌 Signal Close: "
        f"<b>{fmt_price(signal_price)}</b>\n"
        f"🔺 Signal High: "
        f"<b>{fmt_price(signal_high)}</b>\n"
        f"🔻 Signal Low: "
        f"<b>{fmt_price(signal_low)}</b>\n"
        f"🛑 UT Stop: "
        f"<b>{fmt_price(ut_stop)}</b>\n"
        f"🕐 Signal Time: "
        f"<b>{format_iran_time(signal_time)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"⏳ <b>WAITING FOR CONFIRMATION CLOSE</b>"
    )

    send_telegram(message)


# ============================================================
# TELEGRAM - TRADE OPEN
# ============================================================

def telegram_trade_open(
    trade
):

    side = trade["side"]

    emoji = (
        "🟢"
        if side == "LONG"
        else "🔴"
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

    sl_pct = (
        (sl - entry)
        / entry
        * 100
    )

    tp_pct = (
        (tp - entry)
        / entry
        * 100
    )

    message = (
        f"{emoji} <b>{side} ENTRY</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"💎 <b>{trade['symbol']}</b>\n"
        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"
        f"🤖 UT Bot: "
        f"<b>{trade['ut_signal']}</b>\n"
        f"🎯 Entry: "
        f"<b>{fmt_price(entry)}</b>\n"
        f"🛑 SL: "
        f"<b>{fmt_price(sl)}</b> "
        f"({pct(sl_pct)})\n"
        f"💰 TP: "
        f"<b>{fmt_price(tp)}</b> "
        f"({pct(tp_pct)})\n"
        f"📐 RR: <b>1:1</b>\n"
        f"🔻 Swing Low: "
        f"<b>{fmt_price(trade.get('swing_low'))}</b>\n"
        f"🔺 Swing High: "
        f"<b>{fmt_price(trade.get('swing_high'))}</b>\n"
        f"🕐 Signal: "
        f"<b>{format_iran_time(trade['signal_time'])}</b>\n"
        f"🕐 Entry Time: "
        f"<b>{format_iran_time(trade['opened_at'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔒 <b>SL / TP FIXED</b>"
    )

    send_telegram(message)


# ============================================================
# TELEGRAM - TRADE EXIT
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
        f"🎯 Entry: "
        f"<b>{fmt_price(trade['entry'])}</b>\n"
        f"🚪 Exit: "
        f"<b>{fmt_price(exit_price)}</b>\n"
        f"🛑 SL: "
        f"<b>{fmt_price(trade['sl'])}</b>\n"
        f"💰 TP: "
        f"<b>{fmt_price(trade['tp'])}</b>\n"
        f"📈 P&L: "
        f"<b>{pct(pnl_pct)}</b>\n"
        f"📐 R: "
        f"<b>{r_multiple:+.2f}R</b>\n"
        f"⏱ Duration: "
        f"<b>{format_duration(trade['opened_at'])}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"📊 <b>CUMULATIVE PERFORMANCE</b>\n"
        f"Trades: <b>{stats['total']}</b>\n"
        f"🟢 Wins: <b>{stats['wins']}</b>\n"
        f"🔴 Losses: <b>{stats['losses']}</b>\n"
        f"⚪ BE: <b>{stats['breakeven']}</b>\n"
        f"🏆 Win Rate: "
        f"<b>{stats['win_rate']:.2f}%</b>\n"
        f"💵 Total P&L: "
        f"<b>{stats['pnl']:+.2f}%</b>\n"
        f"📐 Total R: "
        f"<b>{stats['r']:+.2f}R</b>"
    )

    send_telegram(message)


# ============================================================
# CREATE TRADE
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
        "status": "OPEN"
    }


# ============================================================
# PROCESS PENDING SIGNAL
# ============================================================

def process_pending_signal(
    symbol,
    df
):

    pending = state[
        "pending_signals"
    ].get(symbol)

    if not pending:
        return False

    if len(df) < 5:
        return False

    confirmation_index = len(df) - 2

    confirmation = df.iloc[
        confirmation_index
    ]

    confirmation_time = (
        candle_time_to_iran(
            int(
                confirmation["timestamp"]
            )
        )
    )

    confirmation_close = float(
        confirmation["close"]
    )

    signal_side = pending[
        "side"
    ]

    signal_high = float(
        pending["signal_high"]
    )

    signal_low = float(
        pending["signal_low"]
    )

    # --------------------------------------------------------
    # CANCEL ON OPPOSITE UT SIGNAL
    # --------------------------------------------------------

    if (
        bool(
            confirmation["ut_buy"]
        )
        and signal_side == "SHORT"
    ):

        del state[
            "pending_signals"
        ][symbol]

        save_state()

        print(
            f"{symbol}: "
            f"SHORT pending cancelled "
            f"by opposite BUY."
        )

        return False

    if (
        bool(
            confirmation["ut_sell"]
        )
        and signal_side == "LONG"
    ):

        del state[
            "pending_signals"
        ][symbol]

        save_state()

        print(
            f"{symbol}: "
            f"LONG pending cancelled "
            f"by opposite SELL."
        )

        return False

    # --------------------------------------------------------
    # LONG CONFIRMATION
    # --------------------------------------------------------

    if signal_side == "LONG":

        if confirmation_close <= signal_high:

            print(
                f"{symbol}: "
                f"LONG waiting. "
                f"Close {confirmation_close} "
                f"<= {signal_high}"
            )

            return False

        swing_low, swing_index = (
            find_last_valid_swing_low(
                df,
                confirmation_index
            )
        )

        if swing_low is None:

            print(
                f"{symbol}: "
                f"LONG confirmed but "
                f"no valid swing low."
            )

            return False

        sl = (
            swing_low
            * (
                1
                - SL_BUFFER_PERCENT / 100
            )
        )

        entry = confirmation_close

        if sl >= entry:

            print(
                f"{symbol}: "
                f"Invalid LONG SL."
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
            signal_time=pending[
                "signal_time"
            ],
            confirmation_time=confirmation_time,
            ut_signal="BUY",
            swing_low=swing_low
        )

        state[
            "open_trades"
        ][symbol] = trade

        del state[
            "pending_signals"
        ][symbol]

        save_state()

        telegram_trade_open(
            trade
        )

        print(
            f"🟢 LONG OPENED: "
            f"{symbol}"
        )

        return True

    # --------------------------------------------------------
    # SHORT CONFIRMATION
    # --------------------------------------------------------

    if signal_side == "SHORT":

        if confirmation_close >= signal_low:

            print(
                f"{symbol}: "
                f"SHORT waiting. "
                f"Close {confirmation_close} "
                f">= {signal_low}"
            )

            return False

        swing_high, swing_index = (
            find_last_valid_swing_high(
                df,
                confirmation_index
            )
        )

        if swing_high is None:

            print(
                f"{symbol}: "
                f"SHORT confirmed but "
                f"no valid swing high."
            )

            return False

        sl = (
            swing_high
            * (
                1
                + SL_BUFFER_PERCENT / 100
            )
        )

        entry = confirmation_close

        if sl <= entry:

            print(
                f"{symbol}: "
                f"Invalid SHORT SL."
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
            signal_time=pending[
                "signal_time"
            ],
            confirmation_time=confirmation_time,
            ut_signal="SELL",
            swing_high=swing_high
        )

        state[
            "open_trades"
        ][symbol] = trade

        del state[
            "pending_signals"
        ][symbol]

        save_state()

        telegram_trade_open(
            trade
        )

        print(
            f"🔴 SHORT OPENED: "
            f"{symbol}"
        )

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

    signal_timestamp = int(
        signal["timestamp"]
    )

    signal_key = str(
        signal_timestamp
    )

    signal_time = (
        candle_time_to_iran(
            signal_timestamp
        )
    )

    # --------------------------------------------------------
    # ALREADY PROCESSED
    # --------------------------------------------------------

    if (
        state[
            "processed_signals"
        ].get(symbol)
        == signal_key
    ):

        return None

    # --------------------------------------------------------
    # CHECK BUY / SELL
    # --------------------------------------------------------

    side = None

    if bool(
        signal["ut_buy"]
    ):

        side = "LONG"

    elif bool(
        signal["ut_sell"]
    ):

        side = "SHORT"

    # --------------------------------------------------------
    # MARK CANDLE PROCESSED
    # --------------------------------------------------------

    state[
        "processed_signals"
    ][symbol] = signal_key

    # --------------------------------------------------------
    # NO SIGNAL
    # --------------------------------------------------------

    if side is None:

        save_state()

        return None

    # --------------------------------------------------------
    # CREATE PENDING
    # --------------------------------------------------------

    pending = {
        "symbol": symbol,
        "side": side,
        "signal_time": signal_time,
        "signal_timestamp": signal_timestamp,
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
        )
    }

    state[
        "pending_signals"
    ][symbol] = pending

    save_state()

    telegram_ut_signal(
        symbol=symbol,
        side=(
            "BUY"
            if side == "LONG"
            else "SELL"
        ),
        signal_price=float(
            signal["close"]
        ),
        signal_high=float(
            signal["high"]
        ),
        signal_low=float(
            signal["low"]
        ),
        ut_stop=float(
            signal["ut_stop"]
        ),
        signal_time=signal_time
    )

    print(
        f"📌 NEW "
        f"{side} SIGNAL: "
        f"{symbol}"
    )

    return pending


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    symbol,
    df
):

    trade = state[
        "open_trades"
    ].get(symbol)

    if not trade:
        return False

    if len(df) < 3:
        return False

    # ONLY CLOSED CANDLE

    candle = df.iloc[
        -2
    ]

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

    side = trade[
        "side"
    ]

    exit_price = None

    result = None

    r_multiple = 0.0

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if side == "LONG":

        hit_sl = (
            low <= sl
        )

        hit_tp = (
            high >= tp
        )

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

        hit_sl = (
            high >= sl
        )

        hit_tp = (
            low <= tp
        )

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
    # STATISTICS
    # --------------------------------------------------------

    stats = state[
        "statistics"
    ]

    stats[
        "total_trades"
    ] += 1

    if result == "WIN":

        stats[
            "wins"
        ] += 1

    elif result == "LOSS":

        stats[
            "losses"
        ] += 1

    else:

        stats[
            "breakeven"
        ] += 1

    stats[
        "total_pnl"
    ] += pnl_pct

    stats[
        "total_r"
    ] += r_multiple

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    closed_trade = dict(
        trade
    )

    closed_trade[
        "exit_price"
    ] = float(
        exit_price
    )

    closed_trade[
        "result"
    ] = result

    closed_trade[
        "pnl_pct"
    ] = float(
        pnl_pct
    )

    closed_trade[
        "r_multiple"
    ] = float(
        r_multiple
    )

    closed_trade[
        "closed_at"
    ] = iso_now_iran()

    closed_trade[
        "duration"
    ] = format_duration(
        trade["opened_at"]
    )

    trade_history.append(
        closed_trade
    )

    # --------------------------------------------------------
    # REMOVE OPEN TRADE
    # --------------------------------------------------------

    del state[
        "open_trades"
    ][symbol]

    save_state()
    save_history()

    telegram_trade_exit(
        trade,
        exit_price,
        result,
        pnl_pct,
        r_multiple
    )

    print(
        f"🏁 TRADE CLOSED: "
        f"{symbol} "
        f"{result}"
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
            "sl": float(
                trade["sl"]
            ),
            "tp": float(
                trade["tp"]
            ),
            "opened_at": trade[
                "opened_at"
            ]
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
        f"🕐 "
        f"{now_iran().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    lines.append(
        f"⏱ Timeframe: "
        f"<b>{TIMEFRAME}</b>"
    )

    lines.append(
        f"🤖 UT Bot: "
        f"<b>Key {UT_KEY} / ATR {UT_ATR_PERIOD}</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "📊 <b>CUMULATIVE PERFORMANCE</b>"
    )

    lines.append(
        f"📈 Total Trades: "
        f"<b>{stats['total']}</b>"
    )

    lines.append(
        f"🟢 Wins: "
        f"<b>{stats['wins']}</b>"
    )

    lines.append(
        f"🔴 Losses: "
        f"<b>{stats['losses']}</b>"
    )

    lines.append(
        f"⚪ Breakeven: "
        f"<b>{stats['breakeven']}</b>"
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

    # --------------------------------------------------------
    # PENDING
    # --------------------------------------------------------

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    pending = state[
        "pending_signals"
    ]

    lines.append(
        f"⏳ Pending UT Signals: "
        f"<b>{len(pending)}</b>"
    )

    for symbol, item in pending.items():

        if item["side"] == "LONG":

            emoji = "🟢"

            direction = "LONG"

            condition = (
                f"Close > "
                f"{fmt_price(item['signal_high'])}"
            )

        else:

            emoji = "🔴"

            direction = "SHORT"

            condition = (
                f"Close < "
                f"{fmt_price(item['signal_low'])}"
            )

        lines.append(
            f"\n{emoji} "
            f"<b>{symbol}</b>"
        )

        lines.append(
            f"UT: <b>{direction}</b>"
        )

        lines.append(
            f"🎯 Signal Close: "
            f"<b>{fmt_price(item['signal_close'])}</b>"
        )

        lines.append(
            f"📌 Confirmation: "
            f"<b>{condition}</b>"
        )

        lines.append(
            f"🕐 Signal: "
            f"<b>{format_iran_time(item['signal_time'])}</b>"
        )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    open_results = (
        calculate_open_pnl()
    )

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

            total_open_pnl += item[
                "pnl"
            ]

            emoji = (
                "🟢"
                if item["side"] == "LONG"
                else "🔴"
            )

            entry = item[
                "entry"
            ]

            sl = item[
                "sl"
            ]

            tp = item[
                "tp"
            ]

            sl_pct = (
                (sl - entry)
                / entry
                * 100
            )

            tp_pct = (
                (tp - entry)
                / entry
                * 100
            )

            lines.append(
                f"\n{emoji} "
                f"<b>{item['symbol']}</b>"
            )

            lines.append(
                f"Side: "
                f"<b>{item['side']}</b>"
            )

            lines.append(
                f"Entry: "
                f"<b>{fmt_price(entry)}</b>"
            )

            lines.append(
                f"Current: "
                f"<b>{fmt_price(item['current'])}</b>"
            )

            lines.append(
                f"P&L: "
                f"<b>{pct(item['pnl'])}</b>"
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

    return "\n".join(
        lines
    )


# ============================================================
# TOP 30 SYMBOLS
# ============================================================

def get_top_symbols():

    try:

        markets = exchange.load_markets()

        candidates = []

        for symbol, market in markets.items():

            try:

                if not market.get(
                    "active",
                    True
                ):
                    continue

                if market.get(
                    "linear"
                ) is not True:
                    continue

                if market.get(
                    "quote"
                ) != "USD":
                    continue

                if market.get(
                    "settle"
                ) != "USD":
                    continue

                if market.get(
                    "swap"
                ) is not True:
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

            ticker = tickers.get(
                symbol
            )

            if not ticker:
                continue

            volume = ticker.get(
                "quoteVolume"
            )

            if volume is None:

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

                    volume = (
                        float(base_volume)
                        * float(last)
                    )

            if volume is None:
                continue

            try:

                volume = float(
                    volume
                )

            except Exception:
                continue

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

        return [
            symbol
            for symbol, volume
            in ranked[:TOP_COINS]
        ]

    except Exception as e:

        print(
            f"Top symbols error: {e}"
        )

        return []


# ============================================================
# SCAN SYMBOL
# ============================================================

def scan_symbol(
    symbol
):

    try:

        print(
            f"Scanning {symbol}"
        )

        df = fetch_ohlcv(
            symbol
        )

        if df is None:
            return

        if len(df) < 50:

            print(
                f"{symbol}: "
                f"Not enough candles."
            )

            return

        df = calculate_ut_bot(
            df
        )

        # ----------------------------------------------------
        # 1. OPEN TRADE
        # ----------------------------------------------------

        if symbol in state[
            "open_trades"
        ]:

            check_open_trade(
                symbol,
                df
            )

            if symbol in state[
                "open_trades"
            ]:

                return

        # ----------------------------------------------------
        # 2. PENDING SIGNAL
        # ----------------------------------------------------

        if symbol in state[
            "pending_signals"
        ]:

            process_pending_signal(
                symbol,
                df
            )

            if symbol in state[
                "pending_signals"
            ]:

                return

            if symbol in state[
                "open_trades"
            ]:

                return

        # ----------------------------------------------------
        # 3. NEW SIGNAL
        # ----------------------------------------------------

        detect_new_ut_signal(
            symbol,
            df
        )

    except Exception as e:

        print(
            f"Scan error "
            f"{symbol}: {e}"
        )

        traceback.print_exc()


# ============================================================
# RUN ONE SCAN
# ============================================================

def run_scan():

    print(
        "\n=========================================="
    )

    print(
        "STARTING ONE COMPLETE SCAN"
    )

    print(
        f"Time: "
        f"{now_iran().strftime('%Y-%m-%d %H:%M:%S')}"
    )

    print(
        "=========================================="
    )

    symbols = get_top_symbols()

    print(
        f"Top symbols found: "
        f"{len(symbols)}"
    )

    if not symbols:

        print(
            "No symbols found."
        )

        return

    # --------------------------------------------------------
    # IMPORTANT:
    # Always scan symbols having active state
    # --------------------------------------------------------

    important_symbols = set(
        state[
            "open_trades"
        ].keys()
    )

    important_symbols.update(
        state[
            "pending_signals"
        ].keys()
    )

    for symbol in important_symbols:

        if symbol not in symbols:

            symbols.append(
                symbol
            )

    for symbol in symbols:

        scan_symbol(
            symbol
        )

    save_state()
    save_history()

    print(
        "=========================================="
    )

    print(
        "SCAN COMPLETED"
    )

    print(
        "=========================================="
    )


# ============================================================
# SEND REPORT
# ============================================================

def send_report():

    message = build_report()

    send_telegram(
        message
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=========================================="
    )

    print(
        "CRYPTO UT BOT SCANNER v12.2"
    )

    print(
        "ONE-SHOT MODE"
    )

    print(
        "GitHub Actions controls the 5-minute schedule"
    )

    print(
        "=========================================="
    )

    try:

        # ----------------------------------------------------
        # LOAD STATE
        # ----------------------------------------------------

        initialize()

        # ----------------------------------------------------
        # RUN ONE SCAN
        # ----------------------------------------------------

        run_scan()

        # ----------------------------------------------------
        # SEND ONE REPORT
        # ----------------------------------------------------

        send_report()

        # ----------------------------------------------------
        # FINAL SAVE
        # ----------------------------------------------------

        save_state()
        save_history()

        print(
            "=========================================="
        )

        print(
            "SCANNER FINISHED SUCCESSFULLY"
        )

        print(
            "=========================================="
        )

    except Exception as e:

        print(
            "=========================================="
        )

        print(
            f"FATAL ERROR: {e}"
        )

        print(
            "=========================================="
        )

        traceback.print_exc()

        # Try to preserve state even on failure

        try:
            save_state()
        except Exception:
            pass

        try:
            save_history()
        except Exception:
            pass

        raise


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
