# ============================================================
# CRYPTO UT BOT SCANNER v3.2
# Kraken Futures
# 15M CLOSED CANDLES
# UT Bot
# RR 1:1
# ONE OPEN TRADE PER SYMBOL
# TELEGRAM ONLY CONFIRMED SIGNALS
# RESET OLD v3 STATE/HISTORY ON FIRST RUN
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

TIMEFRAME = "15m"

TOP_COINS = 30

UT_KEY = 3
UT_ATR_PERIOD = 10

RR = 1.0

OHLCV_LIMIT = 250

STATE_FILE = "utbot_state_v3.json"
HISTORY_FILE = "utbot_trade_history_v3.json"

# فقط برای اجرای اول نسخه 15M
# بعد از اجرا خودکار False می‌شود
RESET_STATS_ON_START = True

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


# ============================================================
# GLOBAL STATE
# ============================================================

open_trades = {}
trade_history = []


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
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=0.0):
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

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram credentials are missing.")
        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
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

        print("Telegram error:", response.text)

    except Exception as e:
        print("Telegram exception:", e)

    return False


# ============================================================
# STATISTICS
# ============================================================

def get_statistics():

    open_count = len(open_trades)

    closed_count = len(trade_history)

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
        win_rate = wins / closed_count * 100
    else:
        win_rate = 0.0

    total_profit = sum(
        safe_float(trade.get("pnl_pct"))
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
# STATE
# ============================================================

def load_state():

    global open_trades

    if not os.path.exists(STATE_FILE):
        open_trades = {}
        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        if isinstance(data, dict):
            open_trades = data
        else:
            open_trades = {}

    except Exception as e:

        print("⚠️ State load error:", e)
        open_trades = {}


def save_state():

    try:

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                open_trades,
                f,
                ensure_ascii=False,
                indent=2
            )

    except Exception as e:

        print("⚠️ State save error:", e)


# ============================================================
# HISTORY
# ============================================================

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

        print("⚠️ History load error:", e)
        trade_history = []


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

        print("⚠️ History save error:", e)


# ============================================================
# RESET OLD STATS
# ============================================================

def reset_statistics_once():

    global open_trades
    global trade_history

    if not RESET_STATS_ON_START:
        return

    print("🔄 RESETTING OLD 15M STATE/HISTORY...")

    open_trades = {}
    trade_history = []

    try:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)

    except Exception as e:
        print("State reset error:", e)

    try:
        if os.path.exists(HISTORY_FILE):
            os.remove(HISTORY_FILE)

    except Exception as e:
        print("History reset error:", e)

    save_state()
    save_history()

    print("✅ Statistics reset to zero.")


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

        df["timestamp"] = pd.to_datetime(
            df["timestamp"],
            unit="ms",
            utc=True
        )

        return df

    except Exception as e:

        print(f"❌ OHLCV error {symbol}: {e}")
        return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=10):

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

    df["nLoss"] = UT_KEY * df["ATR"]

    close = df["close"].values
    nloss = df["nLoss"].values

    trailing_stop = np.zeros(len(df))

    for i in range(len(df)):

        if i == 0:

            trailing_stop[i] = close[i]

            continue

        prev_stop = trailing_stop[i - 1]

        if (
            close[i] > prev_stop
            and close[i - 1] > prev_stop
        ):

            trailing_stop[i] = max(
                prev_stop,
                close[i] - nloss[i]
            )

        elif (
            close[i] < prev_stop
            and close[i - 1] < prev_stop
        ):

            trailing_stop[i] = min(
                prev_stop,
                close[i] + nloss[i]
            )

        elif close[i] > prev_stop:

            trailing_stop[i] = (
                close[i] - nloss[i]
            )

        else:

            trailing_stop[i] = (
                close[i] + nloss[i]
            )

    df["TrailingStop"] = trailing_stop

    position = np.zeros(len(df))

    for i in range(1, len(df)):

        if (
            close[i - 1] <= trailing_stop[i - 1]
            and close[i] > trailing_stop[i]
        ):

            position[i] = 1

        elif (
            close[i - 1] >= trailing_stop[i - 1]
            and close[i] < trailing_stop[i]
        ):

            position[i] = -1

        else:

            position[i] = position[i - 1]

    df["Position"] = position

    return df


# ============================================================
# SIGNAL
# ============================================================

def get_signal(df):

    if df is None:
        return None

    if len(df) < UT_ATR_PERIOD + 20:
        return None

    df = calculate_utbot(df)

    # فقط کندل بسته‌شده
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

    if atr <= 0:
        return None

    # ========================================================
    # LONG
    # ========================================================

    long_signal = (
        previous_close <= previous_stop
        and current_close > current_stop
    )

    # ========================================================
    # SHORT
    # ========================================================

    short_signal = (
        previous_close >= previous_stop
        and current_close < current_stop
    )

    if long_signal:

        return {
            "side": "LONG",
            "entry": current_close,
            "atr": atr,
            "timestamp": str(
                current["timestamp"]
            )
        }

    if short_signal:

        return {
            "side": "SHORT",
            "entry": current_close,
            "atr": atr,
            "timestamp": str(
                current["timestamp"]
            )
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

    if entry <= 0 or atr <= 0:
        return None

    # ========================================================
    # LONG
    # ========================================================

    if signal["side"] == "LONG":

        sl = entry - atr * UT_KEY

        risk = entry - sl

        tp = entry + risk * RR

    # ========================================================
    # SHORT
    # ========================================================

    else:

        sl = entry + atr * UT_KEY

        risk = sl - entry

        tp = entry - risk * RR

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

    trade = {

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

        "signal_time": signal["timestamp"],

        "opened_at": now_utc()
    }

    return trade


# ============================================================
# TELEGRAM ENTRY
# ============================================================

def telegram_entry(trade):

    stats = get_statistics()

    side_emoji = (
        "🟢"
        if trade["side"] == "LONG"
        else "🔴"
    )

    sl_sign = "-"

    tp_sign = "+"

    message = (

        f"{side_emoji} <b>CONFIRMED UT SIGNAL</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💎 <b>{trade['symbol']}</b>\n"

        f"📊 Side: <b>{trade['side']}</b>\n"

        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"

        f"🎯 Entry: "
        f"<b>{fmt(trade['entry'])}</b>\n"

        f"🛑 SL: "
        f"<b>{fmt(trade['sl'])} "
        f"({sl_sign}{trade['sl_pct']:.2f}%)</b>\n"

        f"💰 TP: "
        f"<b>{fmt(trade['tp'])} "
        f"({tp_sign}{trade['tp_pct']:.2f}%)</b>\n"

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

    telegram_send(message)


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

    if result == "TP":

        result_text = "🟢 TAKE PROFIT"

    else:

        result_text = "🔴 STOP LOSS"

    message = (

        f"{result_text}\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💎 <b>{trade['symbol']}</b>\n"

        f"📊 Side: <b>{trade['side']}</b>\n"

        f"⏱ Timeframe: <b>{TIMEFRAME}</b>\n"

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

    telegram_send(message)


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    symbol,
    current_price
):

    if symbol not in open_trades:
        return

    trade = open_trades[symbol]

    side = trade["side"]

    entry = safe_float(
        trade["entry"]
    )

    sl = safe_float(
        trade["sl"]
    )

    tp = safe_float(
        trade["tp"]
    )

    price = safe_float(
        current_price
    )

    if price <= 0:
        return

    result = None
    exit_price = None

    # ========================================================
    # LONG
    # ========================================================

    if side == "LONG":

        if price <= sl:

            result = "SL"
            exit_price = sl

        elif price >= tp:

            result = "TP"
            exit_price = tp

    # ========================================================
    # SHORT
    # ========================================================

    else:

        if price >= sl:

            result = "SL"
            exit_price = sl

        elif price <= tp:

            result = "TP"
            exit_price = tp

    if result is None:
        return

    # ========================================================
    # PNL
    # ========================================================

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

    # ========================================================
    # FINALIZE TRADE
    # ========================================================

    trade["exit"] = exit_price

    trade["closed_at"] = now_utc()

    trade["result"] = result

    trade["pnl_pct"] = pnl_pct

    trade["r_multiple"] = r_multiple

    # اول تاریخچه
    trade_history.append(
        trade.copy()
    )

    # بعد حذف معامله باز
    del open_trades[symbol]

    # ذخیره state
    save_state()

    # ذخیره history
    save_history()

    # بعد تلگرام
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
# PROCESS CONFIRMED SIGNAL
# ============================================================

def process_signal(
    symbol,
    signal
):

    if signal is None:
        return

    # یک معامله باز برای هر ارز
    if symbol in open_trades:

        print(
            f"⏭️ {symbol}: "
            f"open trade already exists."
        )

        return

    trade = create_trade(
        symbol,
        signal
    )

    if trade is None:
        return

    open_trades[symbol] = trade

    save_state()

    print(
        f"🚨 CONFIRMED "
        f"{trade['side']} "
        f"{symbol} "
        f"Entry={fmt(trade['entry'])} "
        f"SL={fmt(trade['sl'])} "
        f"TP={fmt(trade['tp'])}"
    )

    telegram_entry(trade)


# ============================================================
# GET TOP FUTURES COINS
# ============================================================

def get_top_symbols():

    try:

        markets = exchange.load_markets()

        candidates = []

        for symbol, market in markets.items():

            try:

                if not market.get("active"):
                    continue

                if not market.get("linear"):
                    continue

                if market.get("quote") != "USD":
                    continue

                if market.get("settle") != "USD":
                    continue

                ticker = exchange.fetch_ticker(
                    symbol
                )

                quote_volume = safe_float(
                    ticker.get("quoteVolume")
                )

                if quote_volume <= 0:
                    continue

                candidates.append(
                    (
                        symbol,
                        quote_volume
                    )
                )

            except Exception:
                continue

        candidates.sort(
            key=lambda x: x[1],
            reverse=True
        )

        return [
            symbol
            for symbol, volume
            in candidates[:TOP_COINS]
        ]

    except Exception as e:

        print(
            "❌ Symbol discovery error:",
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
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        f"📡 CRYPTO UT BOT SCANNER v3.2"
    )

    print(
        f"⏱ Timeframe: {TIMEFRAME}"
    )

    print(
        f"🤖 UT Bot: "
        f"Key {UT_KEY} / ATR {UT_ATR_PERIOD}"
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

        print("📂 OPEN TRADES:")

        for symbol, trade in open_trades.items():

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

    global open_trades
    global trade_history

    print()
    print(
        "🚀 Starting CRYPTO UT BOT SCANNER v3.2"
    )

    print(
        f"⏱ Timeframe = {TIMEFRAME}"
    )

    print(
        f"🎯 Top Coins = {TOP_COINS}"
    )

    print()

    # ========================================================
    # RESET ON FIRST RUN
    # ========================================================

    reset_statistics_once()

    # ========================================================
    # LOAD STATE
    # ========================================================

    if not RESET_STATS_ON_START:

        load_state()
        load_history()

    else:

        # همین اجرای اول از صفر شروع شده
        # فایل‌های خالی را داریم
        load_state()
        load_history()

    # ========================================================
    # SYMBOLS
    # ========================================================

    symbols = get_top_symbols()

    if not symbols:

        print(
            "❌ No symbols found."
        )

        return

    print(
        f"📊 Scanning {len(symbols)} symbols..."
    )

    print()

    # ========================================================
    # SCAN
    # ========================================================

    for symbol in symbols:

        try:

            df = fetch_ohlcv(symbol)

            if df is None:
                continue

            if len(df) < UT_ATR_PERIOD + 20:
                continue

            # =================================================
            # CURRENT PRICE
            # =================================================

            current_price = safe_float(
                df.iloc[-1]["close"]
            )

            # =================================================
            # CHECK EXISTING TRADE
            # =================================================

            if symbol in open_trades:

                check_open_trade(
                    symbol,
                    current_price
                )

            # =================================================
            # NEW SIGNAL
            # =================================================

            signal = get_signal(df)

            if signal:

                process_signal(
                    symbol,
                    signal
                )

            else:

                print(
                    f"⚪ {symbol}: no confirmed signal"
                )

            time.sleep(
                exchange.rateLimit / 1000
            )

        except Exception as e:

            print(
                f"❌ {symbol} error: {e}"
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

    print(
        f"🕐 Scan finished: {now_utc()}"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    run_scan()
