# ============================================================
# CRYPTO UT BOT SCANNER v3.1
# Kraken Futures | Closed 5m Candles
#
# FEATURES
# ------------------------------------------------------------
# - UT Bot
# - Closed 5m candles only
# - Top 30 Kraken Futures
# - One open trade per symbol
# - CONFIRMED signals only on Telegram
# - NO pending / near Telegram messages
# - Statistics included in Telegram
# - Statistics reset ONCE using new state/history files
# - Then statistics remain cumulative
# - Correct Open / Closed signal counting
# - TP / SL percentage shown beside price in parentheses
# ============================================================

import os
import json
import time
import requests
import ccxt
import pandas as pd
import numpy as np
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "5m"

TOP_COINS = 30

UT_KEY = 3
UT_ATR_PERIOD = 10

RR = 1.0

OHLCV_LIMIT = 250


# ============================================================
# NEW STATE / HISTORY FILES
# ============================================================
#
# Old files are intentionally ignored.
#
# First execution:
#
# Open Signals   = 0
# Closed Signals = 0
# Win Rate       = 0%
# Total Profit   = 0%
#
# After first execution:
# statistics continue cumulatively.
#
# ============================================================

STATE_FILE = "utbot_state_v3.json"

HISTORY_FILE = "utbot_trade_history_v3.json"


# ============================================================
# TELEGRAM
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

pending_setups = {}

open_trades = {}

trade_history = []

last_processed_candle = {}


# ============================================================
# KRAKEN FUTURES
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True
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
# SAFE FLOAT
# ============================================================

def safe_float(
    value,
    default=0.0
):

    try:

        return float(value)

    except Exception:

        return default


# ============================================================
# FORMAT PRICE
# ============================================================

def fmt(
    value,
    decimals=6
):

    try:

        return f"{float(value):.{decimals}f}"

    except Exception:

        return "N/A"


# ============================================================
# TELEGRAM SEND
# ============================================================

def telegram_send(message):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "⚠️ Telegram credentials not configured"
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendMessage"
    )

    payload = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": message,

        "parse_mode": "HTML"
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
            "⚠️ Telegram error:",
            response.text
        )

    except Exception as e:

        print(
            "⚠️ Telegram exception:",
            e
        )

    return False


# ============================================================
# STATISTICS
# ============================================================

def get_statistics():

    total = len(
        trade_history
    )

    wins = sum(
        1
        for trade in trade_history
        if str(
            trade.get(
                "result",
                ""
            )
        ).upper() == "TP"
    )

    losses = sum(
        1
        for trade in trade_history
        if str(
            trade.get(
                "result",
                ""
            )
        ).upper() == "SL"
    )

    total_profit = sum(
        safe_float(
            trade.get(
                "pnl_pct",
                0
            )
        )
        for trade in trade_history
    )

    if total > 0:

        win_rate = (
            wins / total
        ) * 100

    else:

        win_rate = 0.0

    return {

        "open": len(
            open_trades
        ),

        "closed": total,

        "wins": wins,

        "losses": losses,

        "win_rate": win_rate,

        "total_profit": total_profit
    }


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    global pending_setups
    global open_trades
    global last_processed_candle

    if not os.path.exists(
        STATE_FILE
    ):

        pending_setups = {}

        open_trades = {}

        last_processed_candle = {}

        print(
            "🆕 NEW STATE CREATED"
        )

        print(
            "🟢 Open Signals reset to 0"
        )

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        pending_setups = data.get(
            "pending_setups",
            {}
        )

        open_trades = data.get(
            "open_trades",
            {}
        )

        last_processed_candle = data.get(
            "last_processed_candle",
            {}
        )

        print(
            "📂 STATE LOADED"
        )

        print(
            f"🟢 Open Signals: "
            f"{len(open_trades)}"
        )

    except Exception as e:

        print(
            "⚠️ State load error:",
            e
        )

        pending_setups = {}

        open_trades = {}

        last_processed_candle = {}


# ============================================================
# SAVE STATE
# ============================================================

def save_state():

    data = {

        "pending_setups":
            pending_setups,

        "open_trades":
            open_trades,

        "last_processed_candle":
            last_processed_candle
    }

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print(
            "⚠️ State save error:",
            e
        )


# ============================================================
# LOAD HISTORY
# ============================================================

def load_history():

    global trade_history

    if not os.path.exists(
        HISTORY_FILE
    ):

        trade_history = []

        print(
            "🆕 NEW HISTORY CREATED"
        )

        print(
            "⚪ Closed Signals reset to 0"
        )

        print(
            "🏆 Win Rate reset to 0%"
        )

        print(
            "💰 Total Profit reset to 0%"
        )

        return

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            trade_history = json.load(f)

        if not isinstance(
            trade_history,
            list
        ):

            trade_history = []

        print(
            "📊 HISTORY LOADED"
        )

        print(
            f"⚪ Closed Signals: "
            f"{len(trade_history)}"
        )

    except Exception as e:

        print(
            "⚠️ History load error:",
            e
        )

        trade_history = []


# ============================================================
# SAVE HISTORY
# ============================================================

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

        print(
            "⚠️ History save error:",
            e
        )


# ============================================================
# FETCH OHLCV
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

        df["datetime"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True
        )

        return df

    except Exception as e:

        print(
            f"⚠️ OHLCV error "
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

    prev_close = close.shift(1)

    tr1 = high - low

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
        UT_KEY * df["ATR"]
    )

    close = (
        df["close"]
        .values
    )

    loss = (
        df["nLoss"]
        .values
    )

    trailing_stop = np.zeros(
        len(df)
    )

    for i in range(
        len(df)
    ):

        if i == 0:

            trailing_stop[i] = (
                close[i]
                - loss[i]
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
                close[i] - loss[i]
            )

        elif (
            close[i] < prev_stop
            and
            close[i - 1] < prev_stop
        ):

            trailing_stop[i] = min(
                prev_stop,
                close[i] + loss[i]
            )

        elif (
            close[i] > prev_stop
        ):

            trailing_stop[i] = (
                close[i] - loss[i]
            )

        else:

            trailing_stop[i] = (
                close[i] + loss[i]
            )

    df["TrailingStop"] = (
        trailing_stop
    )

    df["EMA1"] = (
        df["close"]
        .ewm(
            span=1,
            adjust=False
        )
        .mean()
    )

    df["above"] = (

        (
            df["EMA1"]
            >
            df["TrailingStop"]
        )

        &

        (
            df["EMA1"].shift(1)
            <=
            df["TrailingStop"].shift(1)
        )
    )

    df["below"] = (

        (
            df["EMA1"]
            <
            df["TrailingStop"]
        )

        &

        (
            df["EMA1"].shift(1)
            >=
            df["TrailingStop"].shift(1)
        )
    )

    df["BUY"] = df["above"]

    df["SELL"] = df["below"]

    return df


# ============================================================
# GET UT SIGNAL
# ============================================================

def get_ut_signal(df):

    if (
        df is None
        or len(df) < 30
    ):

        return None

    # Last candle may still be forming.
    # Use previous closed candle.

    idx = len(df) - 2

    row = df.iloc[idx]

    candle_time = str(
        row["datetime"]
    )

    price = safe_float(
        row["close"]
    )

    atr = safe_float(
        row["ATR"]
    )

    if bool(
        row["BUY"]
    ):

        return {

            "side":
                "LONG",

            "candle_time":
                candle_time,

            "price":
                price,

            "atr":
                atr
        }

    if bool(
        row["SELL"]
    ):

        return {

            "side":
                "SHORT",

            "candle_time":
                candle_time,

            "price":
                price,

            "atr":
                atr
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
        signal["price"]
    )

    atr = safe_float(
        signal["atr"]
    )

    if (
        entry <= 0
        or atr <= 0
    ):

        return None

    # ========================================================
    # LONG
    # ========================================================

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

    # ========================================================
    # SHORT
    # ========================================================

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

    # ========================================================
    # PERCENTAGES
    # ========================================================

    sl_pct = (
        abs(sl - entry)
        / entry
    ) * 100

    tp_pct = (
        abs(tp - entry)
        / entry
    ) * 100

    risk_pct = sl_pct

    trade = {

        "symbol":
            symbol,

        "side":
            signal["side"],

        "signal_time":
            signal["candle_time"],

        "confirmation_time":
            now_utc(),

        "entry":
            entry,

        "sl":
            sl,

        "tp":
            tp,

        "sl_pct":
            sl_pct,

        "tp_pct":
            tp_pct,

        "risk_pct":
            risk_pct,

        "rr":
            RR,

        "status":
            "OPEN",

        "opened_at":
            now_utc()
    }

    return trade


# ============================================================
# TELEGRAM CONFIRMED ENTRY
# ============================================================

def telegram_entry(trade):

    stats = get_statistics()

    if trade["side"] == "LONG":

        side_emoji = "🟢"

    else:

        side_emoji = "🔴"

    message = (

        f"{side_emoji} "
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

    telegram_send(
        message
    )


# ============================================================
# TELEGRAM EXIT
# ============================================================

def telegram_exit(trade):

    stats = get_statistics()

    result = str(
        trade.get(
            "result",
            ""
        )
    ).upper()

    if result == "TP":

        title = "✅ TAKE PROFIT"

    elif result == "SL":

        title = "❌ STOP LOSS"

    else:

        title = "⚪ TRADE CLOSED"

    message = (

        f"<b>{title}</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💎 <b>{trade['symbol']}</b>\n"

        f"📊 Side: "
        f"<b>{trade['side']}</b>\n"

        f"🎯 Entry: "
        f"<b>{fmt(trade['entry'])}</b>\n"

        f"🏁 Exit: "
        f"<b>{fmt(trade['exit_price'])}</b>\n"

        f"📈 P&L: "
        f"<b>{trade['pnl_pct']:+.2f}%</b>\n"

        f"⚖️ R: "
        f"<b>{trade.get('r_multiple', 0):+.2f}</b>\n"

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

    telegram_send(
        message
    )


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    symbol,
    current_price
):

    if symbol not in open_trades:

        return

    trade = open_trades[
        symbol
    ]

    side = trade[
        "side"
    ]

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    exit_price = None

    result = None

    # ========================================================
    # LONG
    # ========================================================

    if side == "LONG":

        if current_price <= sl:

            exit_price = sl

            result = "SL"

        elif current_price >= tp:

            exit_price = tp

            result = "TP"

    # ========================================================
    # SHORT
    # ========================================================

    elif side == "SHORT":

        if current_price >= sl:

            exit_price = sl

            result = "SL"

        elif current_price <= tp:

            exit_price = tp

            result = "TP"

    if result is None:

        return

    # ========================================================
    # PNL
    # ========================================================

    if side == "LONG":

        pnl_pct = (

            (
                exit_price
                - entry
            )
            / entry

        ) * 100

    else:

        pnl_pct = (

            (
                entry
                - exit_price
            )
            / entry

        ) * 100

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

    # ========================================================
    # FINALIZE
    # ========================================================

    trade["exit_price"] = (
        exit_price
    )

    trade["result"] = (
        result
    )

    trade["pnl_pct"] = (
        pnl_pct
    )

    trade["r_multiple"] = (
        r_multiple
    )

    trade["closed_at"] = (
        now_utc()
    )

    trade["status"] = (
        "CLOSED"
    )

    # ========================================================
    # IMPORTANT ORDER
    # ========================================================
    #
    # 1. Add to history
    # 2. Remove from open
    # 3. Save
    # 4. Telegram
    #
    # Therefore statistics are always correct.
    #
    # ========================================================

    trade_history.append(
        trade.copy()
    )

    del open_trades[
        symbol
    ]

    save_history()

    save_state()

    telegram_exit(
        trade
    )

    print(
        f"🏁 CLOSED | "
        f"{symbol} | "
        f"{result} | "
        f"P&L {pnl_pct:+.2f}%"
    )


# ============================================================
# CONFIRM SIGNAL
# ============================================================

def confirm_signal(
    symbol,
    signal
):

    # --------------------------------------------------------
    # One open trade per symbol
    # --------------------------------------------------------

    if symbol in open_trades:

        return

    trade = create_trade(
        symbol,
        signal
    )

    if trade is None:

        return

    # --------------------------------------------------------
    # Add trade to open trades
    # --------------------------------------------------------

    open_trades[
        symbol
    ] = trade

    # --------------------------------------------------------
    # Remove internal pending state
    # --------------------------------------------------------

    pending_setups.pop(
        symbol,
        None
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_state()

    # --------------------------------------------------------
    # Telegram ONLY for confirmed signal
    # --------------------------------------------------------

    telegram_entry(
        trade
    )

    print(
        f"🚨 CONFIRMED | "
        f"{symbol} | "
        f"{trade['side']} | "
        f"Entry {trade['entry']} | "
        f"SL {trade['sl']} "
        f"(-{trade['sl_pct']:.2f}%) | "
        f"TP {trade['tp']} "
        f"(+{trade['tp_pct']:.2f}%)"
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

    candle_time = (
        signal[
            "candle_time"
        ]
    )

    # --------------------------------------------------------
    # Prevent duplicate signal
    # --------------------------------------------------------

    if (
        last_processed_candle.get(
            symbol
        )
        == candle_time
    ):

        return

    last_processed_candle[
        symbol
    ] = candle_time

    # --------------------------------------------------------
    # Existing trade
    # --------------------------------------------------------

    if symbol in open_trades:

        return

    # --------------------------------------------------------
    # CONFIRMED SIGNAL
    # --------------------------------------------------------

    confirm_signal(
        symbol,
        signal
    )


# ============================================================
# GET TOP COINS
# ============================================================

def get_top_coins():

    try:

        markets = (
            exchange.load_markets()
        )

        futures = []

        for symbol, market in (
            markets.items()
        ):

            if not market.get(
                "active"
            ):

                continue

            if market.get(
                "type"
            ) not in (
                "swap",
                "future"
            ):

                continue

            quote = market.get(
                "quote",
                ""
            )

            if quote not in (
                "USD",
                "USDT"
            ):

                continue

            futures.append(
                symbol
            )

        if not futures:

            return []

        tickers = (
            exchange.fetch_tickers(
                futures
            )
        )

        ranked = []

        for symbol in futures:

            ticker = (
                tickers.get(
                    symbol
                )
            )

            if not ticker:

                continue

            quote_volume = safe_float(
                ticker.get(
                    "quoteVolume",
                    0
                )
            )

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
            item[0]
            for item in ranked[
                :TOP_COINS
            ]
        ]

    except Exception as e:

        print(
            "⚠️ Top coins error:",
            e
        )

        return []


# ============================================================
# DASHBOARD
# ============================================================

def print_dashboard():

    stats = get_statistics()

    print()

    print(
        "=" * 65
    )

    print(
        "📡 CRYPTO UT BOT SCANNER v3.1"
    )

    print(
        "=" * 65
    )

    print(
        f"🕐 {now_utc()}"
    )

    print(
        f"⏱ Timeframe: "
        f"{TIMEFRAME}"
    )

    print(
        f"🤖 UT Bot: "
        f"Key {UT_KEY} / ATR {UT_ATR_PERIOD}"
    )

    print(
        "-" * 65
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
        f"🏆 Wins: "
        f"{stats['wins']}"
    )

    print(
        f"❌ Losses: "
        f"{stats['losses']}"
    )

    print(
        f"📊 Win Rate: "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"💰 Total Profit: "
        f"{stats['total_profit']:+.2f}%"
    )

    print(
        "-" * 65
    )

    if open_trades:

        print(
            "📂 OPEN TRADES"
        )

        for (
            symbol,
            trade
        ) in open_trades.items():

            print(

                f"  {symbol} | "
                f"{trade['side']} | "
                f"Entry {fmt(trade['entry'])} | "
                f"SL {fmt(trade['sl'])} "
                f"(-{trade['sl_pct']:.2f}%) | "
                f"TP {fmt(trade['tp'])} "
                f"(+{trade['tp_pct']:.2f}%)"
            )

    else:

        print(
            "📂 OPEN TRADES: NONE"
        )

    print(
        "=" * 65
    )


# ============================================================
# RUN SCAN
# ============================================================

def run_scan():

    print()

    print(
        "🚀 Starting scanner..."
    )

    print(
        f"🕐 {now_utc()}"
    )

    # ========================================================
    # LOAD
    # ========================================================

    load_state()

    load_history()

    print()

    # ========================================================
    # CURRENT STATISTICS
    # ========================================================

    initial_stats = (
        get_statistics()
    )

    print(
        "📊 CURRENT STATISTICS"
    )

    print(
        f"🟢 Open: "
        f"{initial_stats['open']}"
    )

    print(
        f"⚪ Closed: "
        f"{initial_stats['closed']}"
    )

    print(
        f"🏆 Win Rate: "
        f"{initial_stats['win_rate']:.2f}%"
    )

    print(
        f"💰 Profit: "
        f"{initial_stats['total_profit']:+.2f}%"
    )

    # ========================================================
    # TOP COINS
    # ========================================================

    symbols = (
        get_top_coins()
    )

    if not symbols:

        print(
            "❌ No symbols found"
        )

        return

    print()

    print(
        f"📊 Scanning "
        f"{len(symbols)}/{TOP_COINS} coins"
    )

    print()

    data_ok = 0

    data_error = 0

    analysis_error = 0

    # ========================================================
    # SCAN
    # ========================================================

    for symbol in symbols:

        try:

            df = fetch_ohlcv(
                symbol
            )

            if df is None:

                data_error += 1

                continue

            data_ok += 1

            # ------------------------------------------------
            # UT BOT
            # ------------------------------------------------

            df = calculate_utbot(
                df
            )

            # ------------------------------------------------
            # CURRENT PRICE
            # ------------------------------------------------

            current_price = safe_float(
                df.iloc[-1][
                    "close"
                ]
            )

            # ------------------------------------------------
            # EXISTING TRADE
            # ------------------------------------------------

            check_open_trade(
                symbol,
                current_price
            )

            # ------------------------------------------------
            # NEW SIGNAL
            # ------------------------------------------------

            signal = (
                get_ut_signal(
                    df
                )
            )

            if signal:

                process_signal(
                    symbol,
                    signal
                )

        except Exception as e:

            analysis_error += 1

            print(
                f"⚠️ Analysis error "
                f"{symbol}: {e}"
            )

        # ----------------------------------------------------
        # Kraken rate limit
        # ----------------------------------------------------

        time.sleep(
            exchange.rateLimit
            / 1000
        )

    # ========================================================
    # SAVE FINAL STATE
    # ========================================================

    save_state()

    save_history()

    # ========================================================
    # RESULTS
    # ========================================================

    print()

    print(
        f"📊 DATA OK: "
        f"{data_ok}/{len(symbols)}"
    )

    print(
        f"⚠️ DATA ERROR: "
        f"{data_error}"
    )

    print(
        f"⚠️ ANALYSIS ERROR: "
        f"{analysis_error}"
    )

    # ========================================================
    # FINAL DASHBOARD
    # ========================================================

    print_dashboard()


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    try:

        run_scan()

    except Exception as e:

        print()

        print(
            "❌ FATAL ERROR:",
            e
        )
