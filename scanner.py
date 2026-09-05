# ============================================================
# CRYPTO UT BOT SCANNER v4.1
# Kraken Futures
# 15M CLOSED CANDLES
# UT Bot
# RR 1:1
# TOP 30 FUTURES
# ONE OPEN TRADE PER SYMBOL
# TELEGRAM ONLY CONFIRMED SIGNALS
# PERSISTENT STATE + HISTORY
# ONE-TIME RESET
#
# v4.1 FIX:
# - Fast bulk ticker loading
# - Kraken request timeouts
# - Per-symbol scan logging
# - Safe fallback for ticker discovery
# - Prevent scanner hanging indefinitely
# ============================================================

import os
import json
import time
import traceback
import requests
import ccxt
import pandas as pd
import numpy as np

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "15m"

TOP_COINS = 30

UT_KEY = 3
UT_ATR_PERIOD = 10

RR = 1.0

OHLCV_LIMIT = 250

STATE_FILE = "utbot_state.json"
HISTORY_FILE = "utbot_trade_history.json"

RESET_MARKER = ".utbot_15m_reset_done"

# Request settings
KRAKEN_TIMEOUT_MS = 30000
TELEGRAM_TIMEOUT = 20

# Safety limit for fallback ticker requests
MAX_FALLBACK_TICKERS = 100


# ============================================================
# TELEGRAM ENV
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


# ============================================================
# GLOBAL STATE
# ============================================================

open_trades = {}

trade_history = []

processed_signals = {}


# ============================================================
# KRAKEN FUTURES
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
    "timeout": KRAKEN_TIMEOUT_MS
})


# ============================================================
# TIME
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


# ============================================================
# HELPERS
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:

        return float(value)

    except Exception:

        return default


def fmt(value):

    value = safe_float(value)

    if value == 0:

        return "0"

    if abs(value) >= 1000:

        return f"{value:,.2f}"

    if abs(value) >= 1:

        return f"{value:.4f}"

    if abs(value) >= 0.01:

        return f"{value:.6f}"

    if abs(value) >= 0.0001:

        return f"{value:.8f}"

    return f"{value:.10f}"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "❌ TELEGRAM_BOT_TOKEN is missing."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "❌ TELEGRAM_CHAT_ID is missing."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
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
            json=payload,
            timeout=TELEGRAM_TIMEOUT
        )

        print(
            f"📨 Telegram HTTP: "
            f"{response.status_code}"
        )

        if response.ok:

            data = response.json()

            if data.get("ok") is True:

                print(
                    "✅ Telegram message sent."
                )

                return True

            print(
                "❌ Telegram API rejected:",
                data
            )

        else:

            print(
                "❌ Telegram HTTP error:",
                response.text
            )

    except Exception as e:

        print(
            "❌ Telegram exception:",
            repr(e)
        )

    return False


# ============================================================
# STATISTICS
# ============================================================

def get_statistics():

    open_count = len(
        open_trades
    )

    closed_count = len(
        trade_history
    )

    wins = sum(
        1
        for trade in trade_history
        if trade.get("result") == "TP"
    )

    losses = sum(
        1
        for trade in trade_history
        if trade.get("result") == "SL"
    )

    if closed_count > 0:

        win_rate = (
            wins
            / closed_count
            * 100
        )

    else:

        win_rate = 0.0

    total_profit = sum(
        safe_float(
            trade.get(
                "pnl_pct",
                0
            )
        )
        for trade in trade_history
    )

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "total_profit": total_profit
    }


# ============================================================
# STATE LOAD
# ============================================================

def load_state():

    global open_trades
    global processed_signals

    if not os.path.exists(
        STATE_FILE
    ):

        open_trades = {}

        processed_signals = {}

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(
            data,
            dict
        ):

            open_trades = data.get(
                "open_trades",
                {}
            )

            processed_signals = data.get(
                "processed_signals",
                {}
            )

        else:

            open_trades = {}

            processed_signals = {}

    except Exception as e:

        print(
            "❌ State load error:",
            repr(e)
        )

        open_trades = {}

        processed_signals = {}


# ============================================================
# STATE SAVE
# ============================================================

def save_state():

    data = {
        "open_trades": open_trades,
        "processed_signals": processed_signals
    }

    try:

        temp_file = (
            STATE_FILE
            + ".tmp"
        )

        with open(
            temp_file,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
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
            "❌ State save error:",
            repr(e)
        )


# ============================================================
# HISTORY LOAD
# ============================================================

def load_history():

    global trade_history

    if not os.path.exists(
        HISTORY_FILE
    ):

        trade_history = []

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

    except Exception as e:

        print(
            "❌ History load error:",
            repr(e)
        )

        trade_history = []


# ============================================================
# HISTORY SAVE
# ============================================================

def save_history():

    try:

        temp_file = (
            HISTORY_FILE
            + ".tmp"
        )

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
            "❌ History save error:",
            repr(e)
        )


# ============================================================
# ONE-TIME RESET
# ============================================================

def reset_once():

    global open_trades
    global trade_history
    global processed_signals

    if os.path.exists(
        RESET_MARKER
    ):

        print(
            "♻️ Existing 15M state "
            "will be preserved."
        )

        return

    print(
        "🔄 FIRST 15M RUN:"
    )

    print(
        "   Resetting old state "
        "and history..."
    )

    open_trades = {}

    trade_history = []

    processed_signals = {}

    try:

        if os.path.exists(
            STATE_FILE
        ):

            os.remove(
                STATE_FILE
            )

    except Exception as e:

        print(
            "⚠️ Could not remove state:",
            repr(e)
        )

    try:

        if os.path.exists(
            HISTORY_FILE
        ):

            os.remove(
                HISTORY_FILE
            )

    except Exception as e:

        print(
            "⚠️ Could not remove history:",
            repr(e)
        )

    save_state()

    save_history()

    try:

        with open(
            RESET_MARKER,
            "w",
            encoding="utf-8"
        ) as f:

            f.write(
                f"15M initialized: "
                f"{now_utc()}\n"
            )

    except Exception as e:

        print(
            "❌ Reset marker error:",
            repr(e)
        )

    print(
        "✅ Statistics reset to zero."
    )


# ============================================================
# FETCH OHLCV
# ============================================================

def fetch_ohlcv(symbol):

    print(
        f"   📡 Fetching "
        f"{TIMEFRAME} candles..."
    )

    started = time.time()

    try:

        data = exchange.fetch_ohlcv(
            symbol,
            timeframe=TIMEFRAME,
            limit=OHLCV_LIMIT
        )

        elapsed = (
            time.time()
            - started
        )

        if not data:

            print(
                f"   ⚠️ {symbol}: "
                f"empty OHLCV"
            )

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

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True
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

        df = df.dropna().reset_index(
            drop=True
        )

        print(
            f"   ✅ OHLCV OK "
            f"({len(df)} candles, "
            f"{elapsed:.2f}s)"
        )

        return df

    except Exception as e:

        elapsed = (
            time.time()
            - started
        )

        print(
            f"   ❌ OHLCV ERROR "
            f"{symbol} "
            f"after {elapsed:.2f}s:"
        )

        print(
            f"      {repr(e)}"
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

    prev_close = close.shift(1)

    tr1 = (
        high - low
    )

    tr2 = (
        high - prev_close
    ).abs()

    tr3 = (
        low - prev_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(axis=1)

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# ============================================================
# UT BOT
# ============================================================

def calculate_utbot(df):

    df = df.copy()

    df["ATR"] = calculate_atr(
        df,
        UT_ATR_PERIOD
    )

    df["nLoss"] = (
        UT_KEY
        * df["ATR"]
    )

    close = df["close"].to_numpy(
        dtype=float
    )

    nloss = df["nLoss"].to_numpy(
        dtype=float
    )

    trailing_stop = np.zeros(
        len(df),
        dtype=float
    )

    for i in range(
        len(df)
    ):

        if i == 0:

            trailing_stop[i] = (
                close[i]
            )

            continue

        prev_stop = (
            trailing_stop[i - 1]
        )

        if (
            close[i] > prev_stop
            and
            close[i - 1] > prev_stop
        ):

            trailing_stop[i] = max(
                prev_stop,
                close[i] - nloss[i]
            )

        elif (
            close[i] < prev_stop
            and
            close[i - 1] < prev_stop
        ):

            trailing_stop[i] = min(
                prev_stop,
                close[i] + nloss[i]
            )

        elif (
            close[i] > prev_stop
        ):

            trailing_stop[i] = (
                close[i] - nloss[i]
            )

        else:

            trailing_stop[i] = (
                close[i] + nloss[i]
            )

    df["TrailingStop"] = (
        trailing_stop
    )

    position = np.zeros(
        len(df),
        dtype=int
    )

    for i in range(
        1,
        len(df)
    ):

        if (
            close[i - 1]
            <= trailing_stop[i - 1]
            and
            close[i]
            > trailing_stop[i]
        ):

            position[i] = 1

        elif (
            close[i - 1]
            >= trailing_stop[i - 1]
            and
            close[i]
            < trailing_stop[i]
        ):

            position[i] = -1

        else:

            position[i] = (
                position[i - 1]
            )

    df["Position"] = position

    return df


# ============================================================
# GET CONFIRMED SIGNAL
# ============================================================

def get_signal(df):

    if df is None:

        return None

    if len(df) < (
        UT_ATR_PERIOD + 20
    ):

        return None

    df = calculate_utbot(
        df
    )

    # Only closed candles
    current = df.iloc[-2]

    previous = df.iloc[-3]

    current_close = safe_float(
        current["close"]
    )

    previous_close = safe_float(
        previous["close"]
    )

    current_stop = safe_float(
        current["TrailingStop"]
    )

    previous_stop = safe_float(
        previous["TrailingStop"]
    )

    atr = safe_float(
        current["ATR"]
    )

    candle_time = str(
        current["timestamp"]
    )

    if (
        current_close <= 0
        or
        previous_close <= 0
        or
        atr <= 0
    ):

        return None

    # ========================================================
    # LONG
    # ========================================================

    long_signal = (
        previous_close
        <= previous_stop
        and
        current_close
        > current_stop
    )

    # ========================================================
    # SHORT
    # ========================================================

    short_signal = (
        previous_close
        >= previous_stop
        and
        current_close
        < current_stop
    )

    if long_signal:

        return {
            "side": "LONG",
            "entry": current_close,
            "atr": atr,
            "candle_time": candle_time
        }

    if short_signal:

        return {
            "side": "SHORT",
            "entry": current_close,
            "atr": atr,
            "candle_time": candle_time
        }

    return None


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    symbol,
    signal
):

    entry = safe_float(
        signal["entry"]
    )

    atr = safe_float(
        signal["atr"]
    )

    if (
        entry <= 0
        or
        atr <= 0
    ):

        return None

    if signal["side"] == "LONG":

        sl = (
            entry
            - atr * UT_KEY
        )

        risk = (
            entry - sl
        )

        tp = (
            entry
            + risk * RR
        )

    else:

        sl = (
            entry
            + atr * UT_KEY
        )

        risk = (
            sl - entry
        )

        tp = (
            entry
            - risk * RR
        )

    if risk <= 0:

        return None

    sl_pct = (
        abs(sl - entry)
        / entry
        * 100
    )

    tp_pct = (
        abs(tp - entry)
        / entry
        * 100
    )

    return {
        "symbol": symbol,
        "side": signal["side"],
        "timeframe": TIMEFRAME,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "atr": atr,
        "risk_pct": sl_pct,
        "sl_pct": sl_pct,
        "tp_pct": tp_pct,
        "rr": RR,
        "signal_candle": signal[
            "candle_time"
        ],
        "opened_at": now_utc()
    }


# ============================================================
# TELEGRAM ENTRY
# ============================================================

def telegram_entry(trade):

    stats = get_statistics()

    emoji = (
        "🟢"
        if trade["side"] == "LONG"
        else "🔴"
    )

    message = (

        f"{emoji} "
        f"<b>CONFIRMED UT SIGNAL</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💎 <b>{trade['symbol']}</b>\n"

        f"📊 Side: "
        f"<b>{trade['side']}</b>\n"

        f"⏱ Timeframe: "
        f"<b>{TIMEFRAME}</b>\n"

        f"🎯 Entry: "
        f"<b>{fmt(trade['entry'])}</b>\n"

        f"🛑 SL: "
        f"<b>{fmt(trade['sl'])} "
        f"(-{trade['sl_pct']:.2f}%)</b>\n"

        f"💰 TP: "
        f"<b>{fmt(trade['tp'])} "
        f"(+{trade['tp_pct']:.2f}%)</b>\n"

        f"⚖️ RR: "
        f"<b>1:{RR:g}</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"📊 <b>STATISTICS</b>\n"

        f"🟢 Open Signals: "
        f"<b>{stats['open']}</b>\n"

        f"⚪ Closed Signals: "
        f"<b>{stats['closed']}</b>\n"

        f"🏆 Win Rate: "
        f"<b>{stats['win_rate']:.2f}%</b>\n"

        f"💵 Total Profit: "
        f"<b>{stats['total_profit']:+.2f}%</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"🕐 {now_utc()}"
    )

    return telegram_send(
        message
    )


# ============================================================
# TELEGRAM EXIT
# ============================================================

def telegram_exit(
    trade,
    result,
    exit_price,
    pnl_pct,
    r_multiple
):

    stats = get_statistics()

    title = (
        "🟢 TAKE PROFIT"
        if result == "TP"
        else
        "🔴 STOP LOSS"
    )

    message = (

        f"{title}\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💎 <b>{trade['symbol']}</b>\n"

        f"📊 Side: "
        f"<b>{trade['side']}</b>\n"

        f"⏱ Timeframe: "
        f"<b>{TIMEFRAME}</b>\n"

        f"🎯 Entry: "
        f"<b>{fmt(trade['entry'])}</b>\n"

        f"🚪 Exit: "
        f"<b>{fmt(exit_price)}</b>\n"

        f"📈 P&L: "
        f"<b>{pnl_pct:+.2f}%</b>\n"

        f"⚖️ R: "
        f"<b>{r_multiple:+.2f}R</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"📊 <b>STATISTICS</b>\n"

        f"🟢 Open Signals: "
        f"<b>{stats['open']}</b>\n"

        f"⚪ Closed Signals: "
        f"<b>{stats['closed']}</b>\n"

        f"🏆 Win Rate: "
        f"<b>{stats['win_rate']:.2f}%</b>\n"

        f"💵 Total Profit: "
        f"<b>{stats['total_profit']:+.2f}%</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"🕐 {now_utc()}"
    )

    return telegram_send(
        message
    )


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    symbol,
    df
):

    if symbol not in open_trades:

        return

    if df is None or len(df) < 2:

        return

    trade = open_trades[
        symbol
    ]

    candle = df.iloc[-2]

    high = safe_float(
        candle["high"]
    )

    low = safe_float(
        candle["low"]
    )

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    side = trade["side"]

    result = None

    exit_price = None

    if side == "LONG":

        if low <= sl:

            result = "SL"

            exit_price = sl

        elif high >= tp:

            result = "TP"

            exit_price = tp

    else:

        if high >= sl:

            result = "SL"

            exit_price = sl

        elif low <= tp:

            result = "TP"

            exit_price = tp

    if result is None:

        return

    if side == "LONG":

        pnl_pct = (
            (exit_price - entry)
            / entry
            * 100
        )

    else:

        pnl_pct = (
            (entry - exit_price)
            / entry
            * 100
        )

    risk_pct = safe_float(
        trade.get(
            "risk_pct",
            0
        )
    )

    if risk_pct > 0:

        r_multiple = (
            pnl_pct
            / risk_pct
        )

    else:

        r_multiple = 0.0

    trade["exit"] = exit_price

    trade["closed_at"] = now_utc()

    trade["result"] = result

    trade["pnl_pct"] = pnl_pct

    trade["r_multiple"] = r_multiple

    trade_history.append(
        trade.copy()
    )

    del open_trades[
        symbol
    ]

    save_state()

    save_history()

    telegram_exit(
        trade,
        result,
        exit_price,
        pnl_pct,
        r_multiple
    )

    print(
        f"🏁 {symbol} "
        f"{result} "
        f"{pnl_pct:+.2f}%"
    )


# ============================================================
# PROCESS SIGNAL
# ============================================================

def process_signal(
    symbol,
    signal
):

    if signal is None:

        return

    candle_time = signal[
        "candle_time"
    ]

    if (
        processed_signals.get(
            symbol
        )
        ==
        candle_time
    ):

        print(
            f"   ⏭️ {symbol}: "
            f"same candle already "
            f"processed."
        )

        return

    processed_signals[
        symbol
    ] = candle_time

    save_state()

    if symbol in open_trades:

        print(
            f"   ⏭️ {symbol}: "
            f"open trade already exists."
        )

        return

    trade = create_trade(
        symbol,
        signal
    )

    if trade is None:

        print(
            f"   ⚠️ {symbol}: "
            f"could not create trade."
        )

        return

    open_trades[
        symbol
    ] = trade

    save_state()

    print(
        f"   🚨 CONFIRMED "
        f"{trade['side']} "
        f"{symbol}"
    )

    print(
        f"      Entry = "
        f"{fmt(trade['entry'])}"
    )

    print(
        f"      SL = "
        f"{fmt(trade['sl'])} "
        f"(-{trade['sl_pct']:.2f}%)"
    )

    print(
        f"      TP = "
        f"{fmt(trade['tp'])} "
        f"(+{trade['tp_pct']:.2f}%)"
    )

    sent = telegram_entry(
        trade
    )

    if not sent:

        print(
            f"   ⚠️ Telegram failed "
            f"for {symbol}, "
            f"but trade is stored."
        )


# ============================================================
# FILTER FUTURES MARKETS
# ============================================================

def get_candidate_symbols():

    print(
        "📡 Loading Kraken Futures markets..."
    )

    started = time.time()

    markets = exchange.load_markets()

    elapsed = (
        time.time()
        - started
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
                "linear",
                False
            ):

                continue

            if market.get(
                "quote"
            ) != "USD":

                continue

            if market.get(
                "settle"
            ) != "USD":

                continue

            candidates.append(
                symbol
            )

        except Exception:

            continue

    print(
        f"📊 Found "
        f"{len(candidates)} "
        f"USD linear futures "
        f"in {elapsed:.2f}s."
    )

    return candidates


# ============================================================
# GET TOP 30
# ============================================================

def get_top_symbols():

    try:

        candidates = (
            get_candidate_symbols()
        )

        if not candidates:

            print(
                "❌ No futures candidates."
            )

            return []

        print(
            f"📡 Loading tickers "
            f"for {len(candidates)} "
            f"contracts..."
        )

        started = time.time()

        ranked = []

        # ====================================================
        # PRIMARY METHOD:
        # ONE BULK REQUEST
        # ====================================================

        try:

            tickers = (
                exchange.fetch_tickers()
            )

            elapsed = (
                time.time()
                - started
            )

            print(
                f"✅ Bulk tickers received "
                f"in {elapsed:.2f}s."
            )

            for symbol in candidates:

                ticker = tickers.get(
                    symbol
                )

                if not ticker:

                    continue

                quote_volume = safe_float(
                    ticker.get(
                        "quoteVolume"
                    )
                )

                if quote_volume <= 0:

                    base_volume = safe_float(
                        ticker.get(
                            "baseVolume"
                        )
                    )

                    last_price = safe_float(
                        ticker.get(
                            "last"
                        )
                    )

                    quote_volume = (
                        base_volume
                        * last_price
                    )

                if quote_volume > 0:

                    ranked.append(
                        (
                            symbol,
                            quote_volume
                        )
                    )

        except Exception as e:

            print(
                "⚠️ Bulk ticker request "
                "failed:"
            )

            print(
                f"   {repr(e)}"
            )

        # ====================================================
        # FALLBACK
        # ====================================================

        if not ranked:

            print(
                "⚠️ Using fallback "
                "ticker requests."
            )

            fallback_candidates = (
                candidates[
                    :MAX_FALLBACK_TICKERS
                ]
            )

            for index, symbol in enumerate(
                fallback_candidates,
                start=1
            ):

                try:

                    print(
                        f"   Ticker "
                        f"[{index}/"
                        f"{len(fallback_candidates)}] "
                        f"{symbol}"
                    )

                    ticker = (
                        exchange.fetch_ticker(
                            symbol
                        )
                    )

                    quote_volume = safe_float(
                        ticker.get(
                            "quoteVolume"
                        )
                    )

                    if quote_volume <= 0:

                        base_volume = safe_float(
                            ticker.get(
                                "baseVolume"
                            )
                        )

                        last_price = safe_float(
                            ticker.get(
                                "last"
                            )
                        )

                        quote_volume = (
                            base_volume
                            * last_price
                        )

                    if quote_volume > 0:

                        ranked.append(
                            (
                                symbol,
                                quote_volume
                            )
                        )

                except Exception as e:

                    print(
                        f"   ⚠️ Ticker error "
                        f"{symbol}: "
                        f"{repr(e)}"
                    )

        ranked.sort(
            key=lambda x: x[1],
            reverse=True
        )

        top = [
            symbol
            for symbol, volume
            in ranked[:TOP_COINS]
        ]

        print(
            f"🎯 Top {len(top)} "
            f"symbols selected."
        )

        if top:

            print(
                "🏆 Selected symbols:"
            )

            for i, symbol in enumerate(
                top,
                start=1
            ):

                print(
                    f"   {i:02d}. "
                    f"{symbol}"
                )

        return top

    except Exception as e:

        print(
            "❌ Symbol discovery error:",
            repr(e)
        )

        traceback.print_exc()

        return []


# ============================================================
# DASHBOARD
# ============================================================

def print_dashboard():

    stats = get_statistics()

    print()

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        "📡 CRYPTO UT BOT SCANNER v4.1"
    )

    print(
        f"⏱ Timeframe: {TIMEFRAME}"
    )

    print(
        f"🤖 UT Bot: "
        f"Key {UT_KEY} / ATR "
        f"{UT_ATR_PERIOD}"
    )

    print(
        f"⚖️ RR: 1:{RR:g}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        f"🟢 Open Signals: "
        f"{stats['open']}"
    )

    print(
        f"⚪ Closed Signals: "
        f"{stats['closed']}"
    )

    print(
        f"🏆 Win Rate: "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"💵 Total Profit: "
        f"{stats['total_profit']:+.2f}%"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    if open_trades:

        print(
            "📂 OPEN TRADES:"
        )

        for symbol, trade in (
            open_trades.items()
        ):

            print(
                f"  {symbol} "
                f"{trade['side']} "
                f"Entry={fmt(trade['entry'])} "
                f"SL={fmt(trade['sl'])} "
                f"TP={fmt(trade['tp'])}"
            )

    else:

        print(
            "📂 OPEN TRADES: NONE"
        )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan():

    print()

    print(
        "🚀 Starting "
        "CRYPTO UT BOT SCANNER v4.1"
    )

    print(
        f"⏱ Timeframe = "
        f"{TIMEFRAME}"
    )

    print(
        f"🎯 Top Coins = "
        f"{TOP_COINS}"
    )

    print(
        f"🕐 {now_utc()}"
    )

    print()

    # ========================================================
    # RESET
    # ========================================================

    reset_once()

    # ========================================================
    # LOAD STATE
    # ========================================================

    load_state()

    load_history()

    print(
        f"📂 Open trades loaded: "
        f"{len(open_trades)}"
    )

    print(
        f"📂 Closed trades loaded: "
        f"{len(trade_history)}"
    )

    print()

    # ========================================================
    # TOP 30
    # ========================================================

    symbols = get_top_symbols()

    if not symbols:

        print(
            "❌ No symbols found."
        )

        return

    print()

    print(
        "======================================"
    )

    print(
        f"🔎 STARTING "
        f"{len(symbols)}-SYMBOL SCAN"
    )

    print(
        "======================================"
    )

    # ========================================================
    # SCAN
    # ========================================================

    for index, symbol in enumerate(
        symbols,
        start=1
    ):

        print()

        print(
            "--------------------------------------"
        )

        print(
            f"[{index}/{len(symbols)}] "
            f"🔎 {symbol}"
        )

        print(
            "--------------------------------------"
        )

        started = time.time()

        try:

            df = fetch_ohlcv(
                symbol
            )

            if df is None:

                continue

            if len(df) < (
                UT_ATR_PERIOD + 20
            ):

                print(
                    f"   ⚠️ {symbol}: "
                    f"not enough candles."
                )

                continue

            # =================================================
            # EXISTING TRADE
            # =================================================

            if symbol in open_trades:

                print(
                    "   📂 Checking "
                    "existing trade..."
                )

                check_open_trade(
                    symbol,
                    df
                )

            # =================================================
            # SIGNAL
            # =================================================

            signal = get_signal(
                df
            )

            if signal:

                print(
                    f"   🚨 {symbol}: "
                    f"{signal['side']} "
                    f"CONFIRMED"
                )

                process_signal(
                    symbol,
                    signal
                )

            else:

                print(
                    f"   ⚪ {symbol}: "
                    f"no confirmed signal"
                )

            elapsed = (
                time.time()
                - started
            )

            print(
                f"   ⏱ Symbol scan time: "
                f"{elapsed:.2f}s"
            )

        except Exception as e:

            print(
                f"   ❌ {symbol} ERROR:"
            )

            print(
                f"      {repr(e)}"
            )

            traceback.print_exc()

        # Small pause between symbols
        time.sleep(
            0.2
        )

    # ========================================================
    # SAVE
    # ========================================================

    save_state()

    save_history()

    # ========================================================
    # DASHBOARD
    # ========================================================

    print_dashboard()

    print()

    print(
        "======================================"
    )

    print(
        f"🕐 Scan finished: "
        f"{now_utc()}"
    )

    print(
        "======================================"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()

    except KeyboardInterrupt:

        print(
            "🛑 Scanner interrupted."
        )

    except Exception as e:

        print(
            "🔥 FATAL SCANNER ERROR:"
        )

        print(
            repr(e)
        )

        traceback.print_exc()

        raise
