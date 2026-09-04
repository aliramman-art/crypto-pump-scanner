# ============================================================
# CRYPTO UT BOT SCANNER
# Kraken Futures | 30 Coins | 5M CLOSED CANDLES
#
# UT BOT ALERT:
# Key Value = 3
# ATR Period = 10
# Heikin Ashi = OFF
#
# ENTRY LOGIC:
# LONG:
#   1. UT BUY appears
#   2. Save BUY candle HIGH
#   3. Wait for candle CLOSE > BUY candle HIGH
#   4. Entry = confirmation candle CLOSE
#   5. SL = below latest valid swing low
#   6. TP = Entry + Risk  (RR 1:1)
#
# SHORT:
#   1. UT SELL appears
#   2. Save SELL candle LOW
#   3. Wait for candle CLOSE < SELL candle LOW
#   4. Entry = confirmation candle CLOSE
#   5. SL = above latest valid swing high
#   6. TP = Entry - Risk  (RR 1:1)
#
# FEATURES:
# - 30 Futures symbols
# - UT Bot 3/10
# - Closed candle logic
# - Pending setups
# - Open trades
# - TP / SL monitoring
# - Live P&L
# - R multiple
# - Win/Loss statistics
# - Win Rate
# - Gross Profit / Gross Loss
# - Net P&L
# - Profit Factor
# - Persistent trade history
# ============================================================

import ccxt
import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

TIMEFRAME = "5m"

CANDLE_LIMIT = 250

UT_KEY = 3.0
ATR_PERIOD = 10

RR = 1.0

# فاصله SL از Swing
SL_BUFFER_PERCENT = 0.001

# حداقل فاصله SL از Entry
MIN_RISK_PERCENT = 0.001

# حداکثر تعداد ارز
MAX_SYMBOLS = 30

# فاصله بین اسکن‌ها
SCAN_INTERVAL = 30

HISTORY_FILE = "utbot_trade_history.json"

# تعداد کندل برای تشخیص Swing
SWING_LEFT = 2
SWING_RIGHT = 2

# ============================================================
# KRAKEN
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
})

exchange.load_markets()


# ============================================================
# SYMBOL DISCOVERY
# ============================================================

def get_top_symbols(limit=30):

    markets = exchange.markets

    symbols = []

    for symbol, market in markets.items():

        try:
            if not market.get("active", True):
                continue

            if market.get("swap") is not True:
                continue

            if market.get("quote") != "USD":
                continue

            symbols.append(symbol)

        except Exception:
            continue

    tickers = exchange.fetch_tickers(symbols)

    ranked = []

    for symbol in symbols:

        try:
            ticker = tickers.get(symbol)

            if not ticker:
                continue

            quote_volume = ticker.get("quoteVolume")

            if quote_volume is None:
                base_volume = ticker.get("baseVolume") or 0
                last = ticker.get("last") or 0
                quote_volume = base_volume * last

            ranked.append(
                (symbol, float(quote_volume or 0))
            )

        except Exception:
            continue

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    return [x[0] for x in ranked[:limit]]


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol):

    data = exchange.fetch_ohlcv(
        symbol,
        timeframe=TIMEFRAME,
        limit=CANDLE_LIMIT
    )

    if not data or len(data) < ATR_PERIOD + 20:
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

    # TradingView ta.atr uses RMA
    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# ============================================================
# UT BOT ALERT
# Exact logic based on supplied Pine Script
# ============================================================

def calculate_utbot(df):

    df = df.copy()

    src = df["close"].astype(float)

    atr = calculate_atr(
        df,
        ATR_PERIOD
    )

    nloss = UT_KEY * atr

    trailing = np.zeros(len(df))

    pos = np.zeros(len(df))

    for i in range(len(df)):

        if i == 0:

            trailing[i] = src.iloc[i] - nloss.iloc[i]
            pos[i] = 0
            continue

        prev_trailing = trailing[i - 1]

        current_src = src.iloc[i]
        prev_src = src.iloc[i - 1]

        current_loss = nloss.iloc[i]

        # ----------------------------------------------------
        # Pine:
        #
        # xATRTrailingStop := iff(
        #   src > nz(stop[1],0) and src[1] > nz(stop[1],0),
        #   max(stop[1], src-nLoss),
        #
        #   iff(
        #     src < stop[1] and src[1] < stop[1],
        #     min(stop[1], src+nLoss),
        #
        #     iff(
        #       src > stop[1],
        #       src-nLoss,
        #       src+nLoss
        #     )
        #   )
        # )
        # ----------------------------------------------------

        if (
            current_src > prev_trailing
            and prev_src > prev_trailing
        ):

            trailing[i] = max(
                prev_trailing,
                current_src - current_loss
            )

        elif (
            current_src < prev_trailing
            and prev_src < prev_trailing
        ):

            trailing[i] = min(
                prev_trailing,
                current_src + current_loss
            )

        elif current_src > prev_trailing:

            trailing[i] = (
                current_src - current_loss
            )

        else:

            trailing[i] = (
                current_src + current_loss
            )

        # ----------------------------------------------------
        # pos
        # ----------------------------------------------------

        if (
            prev_src < prev_trailing
            and current_src > prev_trailing
        ):

            pos[i] = 1

        elif (
            prev_src > prev_trailing
            and current_src < prev_trailing
        ):

            pos[i] = -1

        else:

            pos[i] = pos[i - 1]

    df["atr"] = atr
    df["ut_stop"] = trailing
    df["ut_pos"] = pos

    # EMA(src,1) == src
    ema = src.copy()

    above = np.zeros(len(df), dtype=bool)
    below = np.zeros(len(df), dtype=bool)

    for i in range(1, len(df)):

        # TradingView crossover(a,b):
        # a > b AND a[1] <= b[1]
        above[i] = (
            ema.iloc[i] > trailing[i]
            and ema.iloc[i - 1] <= trailing[i - 1]
        )

        below[i] = (
            trailing[i] > ema.iloc[i]
            and trailing[i - 1] <= ema.iloc[i - 1]
        )

    df["above"] = above
    df["below"] = below

    df["buy"] = (
        (src.values > trailing)
        & above
    )

    df["sell"] = (
        (src.values < trailing)
        & below
    )

    return df


# ============================================================
# SWING DETECTION
# ============================================================

def find_latest_swing_low(
    df,
    end_index=None
):

    if end_index is None:
        end_index = len(df) - 1

    start = SWING_LEFT + SWING_RIGHT

    if end_index < start:
        return None

    last_found = None

    for i in range(
        start,
        end_index - SWING_RIGHT + 1
    ):

        current = df["low"].iloc[i]

        left = df["low"].iloc[
            i - SWING_LEFT:i
        ]

        right = df["low"].iloc[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if (
            current < left.min()
            and current < right.min()
        ):

            last_found = float(current)

    return last_found


def find_latest_swing_high(
    df,
    end_index=None
):

    if end_index is None:
        end_index = len(df) - 1

    start = SWING_LEFT + SWING_RIGHT

    if end_index < start:
        return None

    last_found = None

    for i in range(
        start,
        end_index - SWING_RIGHT + 1
    ):

        current = df["high"].iloc[i]

        left = df["high"].iloc[
            i - SWING_LEFT:i
        ]

        right = df["high"].iloc[
            i + 1:i + 1 + SWING_RIGHT
        ]

        if (
            current > left.max()
            and current > right.max()
        ):

            last_found = float(current)

    return last_found


# ============================================================
# STATE
# ============================================================

pending_setups = {}

open_trades = {}

trade_history = []


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

            trade_history = json.load(f)

    except Exception:

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
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            "History save error:",
            e
        )


# ============================================================
# FORMAT
# ============================================================

def pct(value):

    return f"{value:+.2f}%"


def price(value):

    if value is None:
        return "-"

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# CREATE LONG
# ============================================================

def create_long_trade(
    symbol,
    df,
    signal_index
):

    entry = float(
        df["close"].iloc[-1]
    )

    swing_low = find_latest_swing_low(
        df,
        signal_index
    )

    if swing_low is None:
        return None

    sl = (
        swing_low
        * (1 - SL_BUFFER_PERCENT)
    )

    risk = entry - sl

    if risk <= 0:
        return None

    risk_pct = (
        risk / entry
    ) * 100

    if risk_pct < MIN_RISK_PERCENT * 100:
        return None

    tp = entry + (
        risk * RR
    )

    return {
        "symbol": symbol,
        "side": "LONG",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "risk_pct": risk_pct,
        "entry_time": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "OPEN"
    }


# ============================================================
# CREATE SHORT
# ============================================================

def create_short_trade(
    symbol,
    df,
    signal_index
):

    entry = float(
        df["close"].iloc[-1]
    )

    swing_high = find_latest_swing_high(
        df,
        signal_index
    )

    if swing_high is None:
        return None

    sl = (
        swing_high
        * (1 + SL_BUFFER_PERCENT)
    )

    risk = sl - entry

    if risk <= 0:
        return None

    risk_pct = (
        risk / entry
    ) * 100

    if risk_pct < MIN_RISK_PERCENT * 100:
        return None

    tp = entry - (
        risk * RR
    )

    return {
        "symbol": symbol,
        "side": "SHORT",
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "risk_pct": risk_pct,
        "entry_time": datetime.now(
            timezone.utc
        ).isoformat(),
        "status": "OPEN"
    }


# ============================================================
# LIVE P&L
# ============================================================

def calculate_live_pnl(
    trade,
    current_price
):

    entry = trade["entry"]

    if trade["side"] == "LONG":

        pnl = (
            (current_price - entry)
            / entry
        ) * 100

    else:

        pnl = (
            (entry - current_price)
            / entry
        ) * 100

    return pnl


def calculate_r(
    trade,
    current_price
):

    risk_pct = trade["risk_pct"]

    if risk_pct == 0:
        return 0

    pnl = calculate_live_pnl(
        trade,
        current_price
    )

    return pnl / risk_pct


# ============================================================
# CHECK OPEN TRADE
# ============================================================

def check_open_trade(
    symbol,
    df
):

    if symbol not in open_trades:
        return

    trade = open_trades[symbol]

    current = float(
        df["close"].iloc[-1]
    )

    high = float(
        df["high"].iloc[-1]
    )

    low = float(
        df["low"].iloc[-1]
    )

    result = None
    exit_price = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if trade["side"] == "LONG":

        # اگر هر دو در یک کندل لمس شوند
        # محافظه کارانه SL را اول حساب می‌کنیم.
        if low <= trade["sl"]:

            result = "SL"
            exit_price = trade["sl"]

        elif high >= trade["tp"]:

            result = "TP"
            exit_price = trade["tp"]

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        if high >= trade["sl"]:

            result = "SL"
            exit_price = trade["sl"]

        elif low <= trade["tp"]:

            result = "TP"
            exit_price = trade["tp"]

    if result is None:
        return

    pnl = calculate_live_pnl(
        trade,
        exit_price
    )

    if result == "TP":
        r_result = RR
    else:
        r_result = -1.0

    closed = dict(trade)

    closed["exit"] = exit_price
    closed["exit_time"] = datetime.now(
        timezone.utc
    ).isoformat()

    closed["result"] = result
    closed["pnl_pct"] = pnl
    closed["r"] = r_result
    closed["status"] = "CLOSED"

    trade_history.append(
        closed
    )

    del open_trades[symbol]

    save_history()


# ============================================================
# CHECK PENDING SETUP
# ============================================================

def check_pending_setup(
    symbol,
    df
):

    if symbol not in pending_setups:
        return

    setup = pending_setups[symbol]

    # آخرین کندل بسته‌شده
    candle = df.iloc[-1]

    close = float(
        candle["close"]
    )

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    signal_high = setup["signal_high"]
    signal_low = setup["signal_low"]

    # --------------------------------------------------------
    # LONG CONFIRMATION
    # --------------------------------------------------------

    if setup["side"] == "LONG":

        if close > signal_high:

            trade = create_long_trade(
                symbol,
                df,
                setup["signal_index"]
            )

            if trade:

                open_trades[symbol] = trade

                del pending_setups[symbol]

                print(
                    f"\n🟢 LONG ENTRY: {symbol}"
                )

                print(
                    f"Entry: {price(trade['entry'])}"
                )

                print(
                    f"SL: {price(trade['sl'])} "
                    f"({pct(-trade['risk_pct'])})"
                )

                print(
                    f"TP: {price(trade['tp'])} "
                    f"({pct(trade['risk_pct'])})"
                )

                print(
                    f"RR: 1:{RR}"
                )

    # --------------------------------------------------------
    # SHORT CONFIRMATION
    # --------------------------------------------------------

    elif setup["side"] == "SHORT":

        if close < signal_low:

            trade = create_short_trade(
                symbol,
                df,
                setup["signal_index"]
            )

            if trade:

                open_trades[symbol] = trade

                del pending_setups[symbol]

                print(
                    f"\n🔴 SHORT ENTRY: {symbol}"
                )

                print(
                    f"Entry: {price(trade['entry'])}"
                )

                print(
                    f"SL: {price(trade['sl'])} "
                    f"({pct(trade['risk_pct'])})"
                )

                print(
                    f"TP: {price(trade['tp'])} "
                    f"({pct(-trade['risk_pct'])})"
                )

                print(
                    f"RR: 1:{RR}"
                )


# ============================================================
# DETECT NEW UT SIGNAL
# ============================================================

def detect_ut_signal(
    symbol,
    df
):

    # آخرین کندل بسته‌شده
    i = len(df) - 1

    candle = df.iloc[i]

    candle_time = str(
        candle["datetime"]
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if bool(candle["buy"]):

        # اگر معامله باز نداریم
        if symbol not in open_trades:

            pending_setups[symbol] = {

                "symbol": symbol,

                "side": "LONG",

                "signal_index": i,

                "signal_time": candle_time,

                "signal_high": float(
                    candle["high"]
                ),

                "signal_low": float(
                    candle["low"]
                )
            }

            print(
                f"\n🟢 UT BUY: {symbol}"
            )

            print(
                f"BUY Candle High: "
                f"{price(candle['high'])}"
            )

            print(
                "⏳ Waiting for CLOSE above BUY candle HIGH"
            )

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    elif bool(candle["sell"]):

        if symbol not in open_trades:

            pending_setups[symbol] = {

                "symbol": symbol,

                "side": "SHORT",

                "signal_index": i,

                "signal_time": candle_time,

                "signal_high": float(
                    candle["high"]
                ),

                "signal_low": float(
                    candle["low"]
                )
            }

            print(
                f"\n🔴 UT SELL: {symbol}"
            )

            print(
                f"SELL Candle Low: "
                f"{price(candle['low'])}"
            )

            print(
                "⏳ Waiting for CLOSE below SELL candle LOW"
            )


# ============================================================
# STATISTICS
# ============================================================

def get_statistics():

    total = len(trade_history)

    wins = sum(
        1
        for t in trade_history
        if t.get("result") == "TP"
    )

    losses = sum(
        1
        for t in trade_history
        if t.get("result") == "SL"
    )

    win_rate = (
        wins / total * 100
        if total
        else 0
    )

    loss_rate = (
        losses / total * 100
        if total
        else 0
    )

    gross_profit = sum(
        max(
            float(t.get("pnl_pct", 0)),
            0
        )
        for t in trade_history
    )

    gross_loss = sum(
        abs(
            min(
                float(t.get("pnl_pct", 0)),
                0
            )
        )
        for t in trade_history
    )

    net_pnl = (
        gross_profit
        - gross_loss
    )

    profit_factor = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    avg_profit = (
        gross_profit / wins
        if wins
        else 0
    )

    avg_loss = (
        gross_loss / losses
        if losses
        else 0
    )

    # --------------------------------------------------------
    # Consecutive
    # --------------------------------------------------------

    max_win_streak = 0
    max_loss_streak = 0

    current_win = 0
    current_loss = 0

    for t in trade_history:

        if t.get("result") == "TP":

            current_win += 1
            current_loss = 0

        elif t.get("result") == "SL":

            current_loss += 1
            current_win = 0

        max_win_streak = max(
            max_win_streak,
            current_win
        )

        max_loss_streak = max(
            max_loss_streak,
            current_loss
        )

    return {

        "total": total,

        "wins": wins,

        "losses": losses,

        "win_rate": win_rate,

        "loss_rate": loss_rate,

        "gross_profit": gross_profit,

        "gross_loss": gross_loss,

        "net_pnl": net_pnl,

        "profit_factor": profit_factor,

        "avg_profit": avg_profit,

        "avg_loss": avg_loss,

        "max_win_streak": max_win_streak,

        "max_loss_streak": max_loss_streak
    }


# ============================================================
# PRINT DASHBOARD
# ============================================================

def print_dashboard(
    symbols
):

    os.system(
        "cls"
        if os.name == "nt"
        else "clear"
    )

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )

    stats = get_statistics()

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        "📡 CRYPTO UT BOT SCANNER"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        f"🕐 {now}"
    )

    print(
        f"⏱ Timeframe: {TIMEFRAME} CLOSED"
    )

    print(
        f"🤖 UT Bot: Key {UT_KEY:g} / ATR {ATR_PERIOD}"
    )

    print(
        f"🪙 Coins: {len(symbols)}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # STATISTICS
    # --------------------------------------------------------

    print(
        "📊 AGGREGATED STATISTICS"
    )

    print(
        f"Total Trades : {stats['total']}"
    )

    print(
        f"TP           : {stats['wins']}"
    )

    print(
        f"SL           : {stats['losses']}"
    )

    print(
        f"Win Rate     : {stats['win_rate']:.2f}%"
    )

    print(
        f"Loss Rate    : {stats['loss_rate']:.2f}%"
    )

    print(
        f"Gross Profit : {stats['gross_profit']:+.2f}%"
    )

    print(
        f"Gross Loss   : -{stats['gross_loss']:.2f}%"
    )

    print(
        f"Net P&L      : {stats['net_pnl']:+.2f}%"
    )

    if np.isinf(stats["profit_factor"]):

        pf = "∞"

    else:

        pf = f"{stats['profit_factor']:.2f}"

    print(
        f"Profit Factor: {pf}"
    )

    print(
        f"Avg TP       : {stats['avg_profit']:+.2f}%"
    )

    print(
        f"Avg SL       : -{stats['avg_loss']:.2f}%"
    )

    print(
        f"Max Win Streak : {stats['max_win_streak']}"
    )

    print(
        f"Max Loss Streak: {stats['max_loss_streak']}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # OPEN TRADES
    # --------------------------------------------------------

    print(
        f"🟢 OPEN TRADES: {len(open_trades)}"
    )

    if not open_trades:

        print("None")

    for symbol, trade in open_trades.items():

        try:

            ticker = exchange.fetch_ticker(
                symbol
            )

            current = float(
                ticker["last"]
            )

            pnl = calculate_live_pnl(
                trade,
                current
            )

            r = calculate_r(
                trade,
                current
            )

            if trade["side"] == "LONG":

                sl_pct = (
                    (trade["sl"] - trade["entry"])
                    / trade["entry"]
                ) * 100

                tp_pct = (
                    (trade["tp"] - trade["entry"])
                    / trade["entry"]
                ) * 100

            else:

                sl_pct = (
                    (trade["entry"] - trade["sl"])
                    / trade["entry"]
                ) * 100

                tp_pct = (
                    (trade["entry"] - trade["tp"])
                    / trade["entry"]
                ) * 100

            print()

            print(
                f"{symbol} | {trade['side']}"
            )

            print(
                f"Entry   : {price(trade['entry'])}"
            )

            print(
                f"Current : {price(current)}"
            )

            print(
                f"SL      : {price(trade['sl'])} "
                f"({sl_pct:+.2f}%)"
            )

            print(
                f"TP      : {price(trade['tp'])} "
                f"({tp_pct:+.2f}%)"
            )

            print(
                f"P&L     : {pnl:+.2f}%"
            )

            print(
                f"R       : {r:+.2f}R"
            )

        except Exception as e:

            print(
                f"{symbol}: price error {e}"
            )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # PENDING
    # --------------------------------------------------------

    print(
        f"⏳ PENDING SETUPS: {len(pending_setups)}"
    )

    for symbol, setup in pending_setups.items():

        if setup["side"] == "LONG":

            print(
                f"🟢 {symbol} LONG | "
                f"Wait Close > "
                f"{price(setup['signal_high'])}"
            )

        else:

            print(
                f"🔴 {symbol} SHORT | "
                f"Wait Close < "
                f"{price(setup['signal_low'])}"
            )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    # --------------------------------------------------------
    # BEST ACTIVE SETUP
    # --------------------------------------------------------

    candidates = []

    for symbol, trade in open_trades.items():

        try:

            ticker = exchange.fetch_ticker(
                symbol
            )

            current = float(
                ticker["last"]
            )

            pnl = calculate_live_pnl(
                trade,
                current
            )

            candidates.append(
                (
                    symbol,
                    trade,
                    pnl
                )
            )

        except Exception:
            pass

    if candidates:

        best = max(
            candidates,
            key=lambda x: x[2]
        )

        symbol, trade, pnl = best

        print(
            "🏆 BEST ACTIVE TRADE"
        )

        print(
            f"{symbol} {trade['side']}"
        )

        print(
            f"Entry: {price(trade['entry'])}"
        )

        print(
            f"SL: {price(trade['sl'])}"
        )

        print(
            f"TP: {price(trade['tp'])}"
        )

        print(
            f"Live P&L: {pnl:+.2f}%"
        )

    elif pending_setups:

        print(
            "🏆 ACTIVE SETUPS"
        )

        for symbol, setup in pending_setups.items():

            print(
                f"{symbol} {setup['side']}"
            )

    else:

        print(
            "🏆 BEST ACTIVE SETUP: NONE"
        )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# PROCESS SYMBOL
# ============================================================

def process_symbol(
    symbol
):

    try:

        df = fetch_ohlcv(
            symbol
        )

        if df is None:
            return

        df = calculate_utbot(
            df
        )

        # ----------------------------------------------------
        # First manage existing trade
        # ----------------------------------------------------

        if symbol in open_trades:

            check_open_trade(
                symbol,
                df
            )

        # ----------------------------------------------------
        # Pending setup
        # ----------------------------------------------------

        if (
            symbol in pending_setups
            and symbol not in open_trades
        ):

            check_pending_setup(
                symbol,
                df
            )

        # ----------------------------------------------------
        # New UT signal
        # ----------------------------------------------------

        if (
            symbol not in open_trades
            and symbol not in pending_setups
        ):

            detect_ut_signal(
                symbol,
                df
            )

    except Exception as e:

        print(
            f"{symbol} ERROR: {e}"
        )


# ============================================================
# MAIN LOOP
# ============================================================

def main():

    load_history()

    print(
        "Loading top 30 Futures symbols..."
    )

    symbols = get_top_symbols(
        MAX_SYMBOLS
    )

    print(
        f"Loaded {len(symbols)} symbols."
    )

    time.sleep(2)

    while True:

        cycle_start = time.time()

        # ----------------------------------------------------
        # Refresh top symbols periodically
        # ----------------------------------------------------

        try:

            new_symbols = get_top_symbols(
                MAX_SYMBOLS
            )

            if new_symbols:

                symbols = new_symbols

        except Exception as e:

            print(
                "Symbol refresh error:",
                e
            )

        # ----------------------------------------------------
        # Process all symbols
        # ----------------------------------------------------

        for symbol in symbols:

            process_symbol(
                symbol
            )

        # ----------------------------------------------------
        # Dashboard
        # ----------------------------------------------------

        print_dashboard(
            symbols
        )

        elapsed = (
            time.time()
            - cycle_start
        )

        sleep_time = max(
            1,
            SCAN_INTERVAL - elapsed
        )

        time.sleep(
            sleep_time
        )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
