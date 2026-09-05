# ============================================================
# CRYPTO UT BOT SCANNER v1.0
# Kraken Futures | TOP 30 COINS | 5M CLOSED CANDLES
#
# UT BOT:
# Key Value = 3
# ATR Period = 10
# Heikin Ashi = OFF
#
# LOGIC:
# LONG:
#   UT BUY
#   -> save BUY candle HIGH
#   -> wait for CLOSED candle CLOSE > BUY HIGH
#   -> Entry = confirmation CLOSE
#   -> SL = latest valid swing LOW - buffer
#   -> TP = 1R
#
# SHORT:
#   UT SELL
#   -> save SELL candle LOW
#   -> wait for CLOSED candle CLOSE < SELL LOW
#   -> Entry = confirmation CLOSE
#   -> SL = latest valid swing HIGH + buffer
#   -> TP = 1R
#
# IMPORTANT:
# - ONLY CLOSED 5M CANDLES
# - ONE SCAN PER GITHUB ACTION RUN
# - NO INFINITE LOOP
# - STATE IS PERSISTED
# - TRADE HISTORY IS PERSISTED
# ============================================================

import ccxt
import pandas as pd
import numpy as np
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

SL_BUFFER_PERCENT = 0.001
MIN_RISK_PERCENT = 0.001

MAX_SYMBOLS = 30

SWING_LEFT = 2
SWING_RIGHT = 2

HISTORY_FILE = "utbot_trade_history.json"
STATE_FILE = "utbot_state.json"


# ============================================================
# KRAKEN FUTURES
# ============================================================

exchange = ccxt.krakenfutures({
    "enableRateLimit": True,
})


# ============================================================
# TIME
# ============================================================

def utc_now():

    return datetime.now(
        timezone.utc
    ).isoformat()


# ============================================================
# PRICE FORMAT
# ============================================================

def price(value):

    if value is None:
        return "-"

    value = float(value)

    if value >= 1000:
        return f"{value:,.2f}"

    if value >= 1:
        return f"{value:.4f}"

    if value >= 0.01:
        return f"{value:.6f}"

    return f"{value:.8f}"


def pct(value):

    return f"{float(value):+.2f}%"


# ============================================================
# MARKET LOADING
# ============================================================

def load_exchange():

    print("Loading Kraken Futures markets...")

    exchange.load_markets()

    print(
        f"Markets loaded: {len(exchange.markets)}"
    )


# ============================================================
# TOP 30 SYMBOLS
# ============================================================

def get_top_symbols(limit=30):

    markets = exchange.markets

    candidates = []

    for symbol, market in markets.items():

        try:

            if not market.get("active", True):
                continue

            if market.get("swap") is not True:
                continue

            if market.get("quote") != "USD":
                continue

            candidates.append(symbol)

        except Exception:
            continue

    if not candidates:
        return []

    print(
        f"Checking volume for {len(candidates)} futures symbols..."
    )

    try:

        tickers = exchange.fetch_tickers()

    except Exception as e:

        print(
            "Ticker bulk fetch error:",
            e
        )

        tickers = {}

    ranked = []

    for symbol in candidates:

        try:

            ticker = tickers.get(symbol)

            if not ticker:
                continue

            quote_volume = ticker.get(
                "quoteVolume"
            )

            if quote_volume is None:

                base_volume = (
                    ticker.get("baseVolume")
                    or 0
                )

                last = (
                    ticker.get("last")
                    or 0
                )

                quote_volume = (
                    float(base_volume)
                    * float(last)
                )

            ranked.append(
                (
                    symbol,
                    float(quote_volume or 0)
                )
            )

        except Exception:
            continue

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    symbols = [
        item[0]
        for item in ranked[:limit]
    ]

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

    except Exception as e:

        print(
            f"{symbol} OHLCV error: {e}"
        )

        return None

    if not data:
        return None

    if len(data) < ATR_PERIOD + 20:
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

    # --------------------------------------------------------
    # VERY IMPORTANT:
    #
    # Remove current unfinished candle.
    #
    # Kraken normally returns the current candle as the
    # final OHLCV row. We only work with CLOSED candles.
    # --------------------------------------------------------

    now_ms = int(
        datetime.now(
            timezone.utc
        ).timestamp() * 1000
    )

    timeframe_ms = 5 * 60 * 1000

    if (
        len(df) > 0
        and int(df["timestamp"].iloc[-1])
        + timeframe_ms
        > now_ms
    ):

        df = df.iloc[:-1].copy()

    if len(df) < ATR_PERIOD + 20:
        return None

    df.reset_index(
        drop=True,
        inplace=True
    )

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=10
):

    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)

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

    # TradingView ATR = RMA
    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# ============================================================
# UT BOT
# EXACT LOGIC FROM SUPPLIED PINE SCRIPT
# Key = 3
# ATR = 10
# ============================================================

def calculate_utbot(df):

    df = df.copy()

    src = df["close"].astype(float)

    atr = calculate_atr(
        df,
        ATR_PERIOD
    )

    nloss = UT_KEY * atr

    trailing = np.zeros(
        len(df)
    )

    pos = np.zeros(
        len(df)
    )

    for i in range(len(df)):

        if i == 0:

            trailing[i] = (
                src.iloc[i]
                - nloss.iloc[i]
            )

            pos[i] = 0

            continue

        previous_stop = (
            trailing[i - 1]
        )

        current_src = (
            src.iloc[i]
        )

        previous_src = (
            src.iloc[i - 1]
        )

        current_loss = (
            nloss.iloc[i]
        )

        # ----------------------------------------------------
        # Pine xATRTrailingStop
        # ----------------------------------------------------

        if (
            current_src > previous_stop
            and previous_src > previous_stop
        ):

            trailing[i] = max(
                previous_stop,
                current_src - current_loss
            )

        elif (
            current_src < previous_stop
            and previous_src < previous_stop
        ):

            trailing[i] = min(
                previous_stop,
                current_src + current_loss
            )

        elif current_src > previous_stop:

            trailing[i] = (
                current_src - current_loss
            )

        else:

            trailing[i] = (
                current_src + current_loss
            )

        # ----------------------------------------------------
        # Pine pos
        # ----------------------------------------------------

        if (
            previous_src < previous_stop
            and current_src > previous_stop
        ):

            pos[i] = 1

        elif (
            previous_src > previous_stop
            and current_src < previous_stop
        ):

            pos[i] = -1

        else:

            pos[i] = pos[i - 1]

    df["atr"] = atr
    df["ut_stop"] = trailing
    df["ut_pos"] = pos

    # EMA(src, 1) == src

    above = np.zeros(
        len(df),
        dtype=bool
    )

    below = np.zeros(
        len(df),
        dtype=bool
    )

    for i in range(
        1,
        len(df)
    ):

        above[i] = (
            src.iloc[i] > trailing[i]
            and src.iloc[i - 1]
            <= trailing[i - 1]
        )

        below[i] = (
            trailing[i] > src.iloc[i]
            and trailing[i - 1]
            <= src.iloc[i - 1]
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
# SWING LOW
# ============================================================

def find_latest_swing_low(
    df,
    end_index=None
):

    if end_index is None:
        end_index = len(df) - 1

    last_possible = (
        end_index - SWING_RIGHT
    )

    if last_possible < SWING_LEFT:
        return None

    last_found = None

    for i in range(
        SWING_LEFT,
        last_possible + 1
    ):

        current = float(
            df["low"].iloc[i]
        )

        left = df["low"].iloc[
            i - SWING_LEFT:i
        ]

        right = df["low"].iloc[
            i + 1:
            i + 1 + SWING_RIGHT
        ]

        if (
            current < left.min()
            and current < right.min()
        ):

            last_found = current

    return last_found


# ============================================================
# SWING HIGH
# ============================================================

def find_latest_swing_high(
    df,
    end_index=None
):

    if end_index is None:
        end_index = len(df) - 1

    last_possible = (
        end_index - SWING_RIGHT
    )

    if last_possible < SWING_LEFT:
        return None

    last_found = None

    for i in range(
        SWING_LEFT,
        last_possible + 1
    ):

        current = float(
            df["high"].iloc[i]
        )

        left = df["high"].iloc[
            i - SWING_LEFT:i
        ]

        right = df["high"].iloc[
            i + 1:
            i + 1 + SWING_RIGHT
        ]

        if (
            current > left.max()
            and current > right.max()
        ):

            last_found = current

    return last_found


# ============================================================
# STATE
# ============================================================

pending_setups = {}
open_trades = {}
trade_history = []


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    global pending_setups
    global open_trades

    if not os.path.exists(
        STATE_FILE
    ):

        pending_setups = {}
        open_trades = {}

        return

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        pending_setups = (
            state.get(
                "pending_setups",
                {}
            )
        )

        open_trades = (
            state.get(
                "open_trades",
                {}
            )
        )

        print(
            f"Loaded state: "
            f"{len(pending_setups)} pending, "
            f"{len(open_trades)} open"
        )

    except Exception as e:

        print(
            "State load error:",
            e
        )

        pending_setups = {}
        open_trades = {}


# ============================================================
# SAVE STATE
# ============================================================

def save_state():

    try:

        state = {

            "updated_at": utc_now(),

            "pending_setups":
                pending_setups,

            "open_trades":
                open_trades
        }

        with open(
            STATE_FILE,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                state,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            "State save error:",
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

        return

    try:

        with open(
            HISTORY_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            trade_history = json.load(
                f
            )

        if not isinstance(
            trade_history,
            list
        ):

            trade_history = []

        print(
            f"Trade history loaded: "
            f"{len(trade_history)} trades"
        )

    except Exception as e:

        print(
            "History load error:",
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
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            "History save error:",
            e
        )


# ============================================================
# CREATE LONG TRADE
# ============================================================

def create_long_trade(
    symbol,
    df,
    signal_index
):

    entry = float(
        df["close"].iloc[-1]
    )

    swing_low = (
        find_latest_swing_low(
            df,
            signal_index
        )
    )

    if swing_low is None:
        return None

    sl = (
        swing_low
        * (1 - SL_BUFFER_PERCENT)
    )

    risk = (
        entry - sl
    )

    if risk <= 0:
        return None

    risk_pct = (
        risk / entry
    ) * 100

    if (
        risk_pct
        < MIN_RISK_PERCENT * 100
    ):

        return None

    tp = (
        entry
        + risk * RR
    )

    return {

        "symbol": symbol,

        "side": "LONG",

        "entry": entry,

        "sl": sl,

        "tp": tp,

        "risk": risk,

        "risk_pct": risk_pct,

        "entry_time": utc_now(),

        "status": "OPEN"
    }


# ============================================================
# CREATE SHORT TRADE
# ============================================================

def create_short_trade(
    symbol,
    df,
    signal_index
):

    entry = float(
        df["close"].iloc[-1]
    )

    swing_high = (
        find_latest_swing_high(
            df,
            signal_index
        )
    )

    if swing_high is None:
        return None

    sl = (
        swing_high
        * (1 + SL_BUFFER_PERCENT)
    )

    risk = (
        sl - entry
    )

    if risk <= 0:
        return None

    risk_pct = (
        risk / entry
    ) * 100

    if (
        risk_pct
        < MIN_RISK_PERCENT * 100
    ):

        return None

    tp = (
        entry
        - risk * RR
    )

    return {

        "symbol": symbol,

        "side": "SHORT",

        "entry": entry,

        "sl": sl,

        "tp": tp,

        "risk": risk,

        "risk_pct": risk_pct,

        "entry_time": utc_now(),

        "status": "OPEN"
    }


# ============================================================
# LIVE PNL
# ============================================================

def calculate_live_pnl(
    trade,
    current_price
):

    entry = float(
        trade["entry"]
    )

    current_price = float(
        current_price
    )

    if trade["side"] == "LONG":

        return (
            (
                current_price
                - entry
            )
            / entry
        ) * 100

    return (
        (
            entry
            - current_price
        )
        / entry
    ) * 100


# ============================================================
# R MULTIPLE
# ============================================================

def calculate_r(
    trade,
    current_price
):

    risk_pct = float(
        trade.get(
            "risk_pct",
            0
        )
    )

    if risk_pct <= 0:
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

    trade = (
        open_trades[symbol]
    )

    candle = df.iloc[-1]

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    result = None
    exit_price = None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if trade["side"] == "LONG":

        if low <= float(
            trade["sl"]
        ):

            result = "SL"

            exit_price = float(
                trade["sl"]
            )

        elif high >= float(
            trade["tp"]
        ):

            result = "TP"

            exit_price = float(
                trade["tp"]
            )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        if high >= float(
            trade["sl"]
        ):

            result = "SL"

            exit_price = float(
                trade["sl"]
            )

        elif low <= float(
            trade["tp"]
        ):

            result = "TP"

            exit_price = float(
                trade["tp"]
            )

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

    closed = dict(
        trade
    )

    closed["exit"] = (
        exit_price
    )

    closed["exit_time"] = (
        utc_now()
    )

    closed["result"] = (
        result
    )

    closed["pnl_pct"] = (
        pnl
    )

    closed["r"] = (
        r_result
    )

    closed["status"] = (
        "CLOSED"
    )

    trade_history.append(
        closed
    )

    del open_trades[
        symbol
    ]

    save_history()
    save_state()

    emoji = (
        "🟢"
        if result == "TP"
        else "🔴"
    )

    print()
    print(
        f"{emoji} {result}: {symbol}"
    )

    print(
        f"Exit: {price(exit_price)}"
    )

    print(
        f"P&L: {pnl:+.2f}%"
    )

    print(
        f"R: {r_result:+.2f}R"
    )


# ============================================================
# CHECK PENDING SETUP
# ============================================================

def check_pending_setup(
    symbol,
    df
):

    if symbol not in pending_setups:
        return

    setup = (
        pending_setups[symbol]
    )

    candle = df.iloc[-1]

    close = float(
        candle["close"]
    )

    # --------------------------------------------------------
    # LONG CONFIRMATION
    # --------------------------------------------------------

    if setup["side"] == "LONG":

        signal_high = float(
            setup["signal_high"]
        )

        if close > signal_high:

            trade = create_long_trade(
                symbol,
                df,
                int(
                    setup[
                        "signal_index"
                    ]
                )
            )

            if trade:

                open_trades[
                    symbol
                ] = trade

                del pending_setups[
                    symbol
                ]

                save_state()

                print()
                print(
                    f"🟢 LONG ENTRY: {symbol}"
                )

                print(
                    f"Entry: "
                    f"{price(trade['entry'])}"
                )

                print(
                    f"SL: "
                    f"{price(trade['sl'])} "
                    f"({pct(-trade['risk_pct'])})"
                )

                print(
                    f"TP: "
                    f"{price(trade['tp'])} "
                    f"({pct(trade['risk_pct'])})"
                )

                print(
                    f"RR: 1:{RR:.1f}"
                )

    # --------------------------------------------------------
    # SHORT CONFIRMATION
    # --------------------------------------------------------

    elif setup["side"] == "SHORT":

        signal_low = float(
            setup["signal_low"]
        )

        if close < signal_low:

            trade = create_short_trade(
                symbol,
                df,
                int(
                    setup[
                        "signal_index"
                    ]
                )
            )

            if trade:

                open_trades[
                    symbol
                ] = trade

                del pending_setups[
                    symbol
                ]

                save_state()

                print()
                print(
                    f"🔴 SHORT ENTRY: {symbol}"
                )

                print(
                    f"Entry: "
                    f"{price(trade['entry'])}"
                )

                print(
                    f"SL: "
                    f"{price(trade['sl'])} "
                    f"({pct(-trade['risk_pct'])})"
                )

                print(
                    f"TP: "
                    f"{price(trade['tp'])} "
                    f"({pct(trade['risk_pct'])})"
                )

                print(
                    f"RR: 1:{RR:.1f}"
                )


# ============================================================
# DETECT UT SIGNAL
# ============================================================

def detect_ut_signal(
    symbol,
    df
):

    i = len(df) - 1

    candle = df.iloc[i]

    candle_time = str(
        candle["datetime"]
    )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    if bool(candle["buy"]):

        if (
            symbol not in open_trades
            and symbol not in pending_setups
        ):

            pending_setups[
                symbol
            ] = {

                "symbol": symbol,

                "side": "LONG",

                "signal_index": i,

                "signal_time":
                    candle_time,

                "signal_high":
                    float(
                        candle["high"]
                    ),

                "signal_low":
                    float(
                        candle["low"]
                    )
            }

            save_state()

            print()
            print(
                f"🟢 UT BUY: {symbol}"
            )

            print(
                f"BUY HIGH: "
                f"{price(candle['high'])}"
            )

            print(
                "⏳ Waiting for "
                "CLOSE > BUY HIGH"
            )

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    elif bool(candle["sell"]):

        if (
            symbol not in open_trades
            and symbol not in pending_setups
        ):

            pending_setups[
                symbol
            ] = {

                "symbol": symbol,

                "side": "SHORT",

                "signal_index": i,

                "signal_time":
                    candle_time,

                "signal_high":
                    float(
                        candle["high"]
                    ),

                "signal_low":
                    float(
                        candle["low"]
                    )
            }

            save_state()

            print()
            print(
                f"🔴 UT SELL: {symbol}"
            )

            print(
                f"SELL LOW: "
                f"{price(candle['low'])}"
            )

            print(
                "⏳ Waiting for "
                "CLOSE < SELL LOW"
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
        # 1. Check existing trade
        # ----------------------------------------------------

        if symbol in open_trades:

            check_open_trade(
                symbol,
                df
            )

        # ----------------------------------------------------
        # 2. Check pending setup
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
        # 3. Detect new UT signal
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
# STATISTICS
# ============================================================

def get_statistics():

    total = len(
        trade_history
    )

    wins = sum(
        1
        for t in trade_history
        if t.get("result")
        == "TP"
    )

    losses = sum(
        1
        for t in trade_history
        if t.get("result")
        == "SL"
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
            float(
                t.get(
                    "pnl_pct",
                    0
                )
            ),
            0
        )
        for t in trade_history
    )

    gross_loss = sum(
        abs(
            min(
                float(
                    t.get(
                        "pnl_pct",
                        0
                    )
                ),
                0
            )
        )
        for t in trade_history
    )

    net_pnl = (
        gross_profit
        - gross_loss
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = float(
            "inf"
        )

    avg_tp = (
        gross_profit / wins
        if wins
        else 0
    )

    avg_sl = (
        gross_loss / losses
        if losses
        else 0
    )

    max_win_streak = 0
    max_loss_streak = 0

    current_win = 0
    current_loss = 0

    for trade in trade_history:

        result = trade.get(
            "result"
        )

        if result == "TP":

            current_win += 1
            current_loss = 0

        elif result == "SL":

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

        "win_rate":
            win_rate,

        "loss_rate":
            loss_rate,

        "gross_profit":
            gross_profit,

        "gross_loss":
            gross_loss,

        "net_pnl":
            net_pnl,

        "profit_factor":
            profit_factor,

        "avg_tp":
            avg_tp,

        "avg_sl":
            avg_sl,

        "max_win_streak":
            max_win_streak,

        "max_loss_streak":
            max_loss_streak
    }


# ============================================================
# GET LIVE PRICE
# ============================================================

def get_live_price(
    symbol
):

    try:

        ticker = (
            exchange.fetch_ticker(
                symbol
            )
        )

        last = ticker.get(
            "last"
        )

        if last is None:
            return None

        return float(last)

    except Exception:

        return None


# ============================================================
# PRINT OPEN TRADES
# ============================================================

def print_open_trades():

    print(
        f"🟢 OPEN TRADES: "
        f"{len(open_trades)}"
    )

    if not open_trades:

        print("None")

        return

    for symbol, trade in (
        open_trades.items()
    ):

        current = (
            get_live_price(
                symbol
            )
        )

        if current is None:

            print(
                f"{symbol}: "
                "price unavailable"
            )

            continue

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
                (
                    trade["sl"]
                    - trade["entry"]
                )
                / trade["entry"]
            ) * 100

            tp_pct = (
                (
                    trade["tp"]
                    - trade["entry"]
                )
                / trade["entry"]
            ) * 100

        else:

            sl_pct = (
                (
                    trade["entry"]
                    - trade["sl"]
                )
                / trade["entry"]
            ) * 100

            tp_pct = (
                (
                    trade["entry"]
                    - trade["tp"]
                )
                / trade["entry"]
            ) * 100

        print()
        print(
            f"  {symbol} | "
            f"{trade['side']}"
        )

        print(
            f"  Entry   : "
            f"{price(trade['entry'])}"
        )

        print(
            f"  Current : "
            f"{price(current)}"
        )

        print(
            f"  SL      : "
            f"{price(trade['sl'])} "
            f"({sl_pct:+.2f}%)"
        )

        print(
            f"  TP      : "
            f"{price(trade['tp'])} "
            f"({tp_pct:+.2f}%)"
        )

        print(
            f"  Live P&L: "
            f"{pnl:+.2f}%"
        )

        print(
            f"  R       : "
            f"{r:+.2f}R"
        )


# ============================================================
# PRINT PENDING SETUPS
# ============================================================

def print_pending():

    print()
    print(
        f"⏳ PENDING SETUPS: "
        f"{len(pending_setups)}"
    )

    if not pending_setups:

        print("None")

        return

    for symbol, setup in (
        pending_setups.items()
    ):

        if setup["side"] == "LONG":

            print(
                f"  🟢 {symbol} LONG | "
                f"Close > "
                f"{price(setup['signal_high'])}"
            )

        else:

            print(
                f"  🔴 {symbol} SHORT | "
                f"Close < "
                f"{price(setup['signal_low'])}"
            )


# ============================================================
# DASHBOARD
# ============================================================

def print_dashboard(
    symbols
):

    stats = get_statistics()

    print()
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
        f"🕐 "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
    )

    print(
        f"⏱ Timeframe: "
        f"{TIMEFRAME} CLOSED"
    )

    print(
        f"🤖 UT Bot: "
        f"Key {UT_KEY:g} / ATR {ATR_PERIOD}"
    )

    print(
        f"🪙 Coins: "
        f"{len(symbols)}"
    )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print(
        "📊 AGGREGATED STATISTICS"
    )

    print(
        f"Total Trades : "
        f"{stats['total']}"
    )

    print(
        f"TP           : "
        f"{stats['wins']}"
    )

    print(
        f"SL           : "
        f"{stats['losses']}"
    )

    print(
        f"Win Rate     : "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"Loss Rate    : "
        f"{stats['loss_rate']:.2f}%"
    )

    print(
        f"Gross Profit : "
        f"{stats['gross_profit']:+.2f}%"
    )

    print(
        f"Gross Loss   : "
        f"-{stats['gross_loss']:.2f}%"
    )

    print(
        f"Net P&L      : "
        f"{stats['net_pnl']:+.2f}%"
    )

    if np.isinf(
        stats["profit_factor"]
    ):

        pf = "∞"

    else:

        pf = (
            f"{stats['profit_factor']:.2f}"
        )

    print(
        f"Profit Factor: "
        f"{pf}"
    )

    print(
        f"Avg TP       : "
        f"{stats['avg_tp']:+.2f}%"
    )

    print(
        f"Avg SL       : "
        f"-{stats['avg_sl']:.2f}%"
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

    print_open_trades()

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    print_pending()

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )

    if open_trades:

        print(
            "🏆 ACTIVE TRADES"
        )

        for symbol, trade in (
            open_trades.items()
        ):

            current = (
                get_live_price(
                    symbol
                )
            )

            if current is not None:

                pnl = (
                    calculate_live_pnl(
                        trade,
                        current
                    )
                )

                print(
                    f"{symbol} "
                    f"{trade['side']} | "
                    f"P&L {pnl:+.2f}%"
                )

    elif pending_setups:

        print(
            "🏆 ACTIVE SETUPS"
        )

        for symbol, setup in (
            pending_setups.items()
        ):

            print(
                f"{symbol} "
                f"{setup['side']}"
            )

    else:

        print(
            "🏆 BEST ACTIVE SETUP: NONE"
        )

    print(
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print(
        "======================================"
    )

    print(
        "CRYPTO UT BOT SCANNER"
    )

    print(
        "UT BOT 3 / 10"
    )

    print(
        "ONE-SHOT GITHUB ACTION MODE"
    )

    print(
        "======================================"
    )

    # --------------------------------------------------------
    # Load persistent data
    # --------------------------------------------------------

    load_history()
    load_state()

    # --------------------------------------------------------
    # Exchange
    # --------------------------------------------------------

    load_exchange()

    # --------------------------------------------------------
    # Top 30
    # --------------------------------------------------------

    print()
    print(
        "Loading TOP 30 Futures symbols..."
    )

    symbols = get_top_symbols(
        MAX_SYMBOLS
    )

    if not symbols:

        print(
            "ERROR: No symbols found."
        )

        save_state()
        save_history()

        return

    print()
    print(
        f"Loaded {len(symbols)} symbols:"
    )

    for symbol in symbols:

        print(
            f"  {symbol}"
        )

    print()

    # --------------------------------------------------------
    # Process symbols ONCE
    # --------------------------------------------------------

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"[{number:02d}/{len(symbols):02d}] "
            f"Scanning {symbol}"
        )

        process_symbol(
            symbol
        )

    # --------------------------------------------------------
    # Final dashboard
    # --------------------------------------------------------

    print_dashboard(
        symbols
    )

    # --------------------------------------------------------
    # Persist everything
    # --------------------------------------------------------

    save_state()
    save_history()

    print()
    print(
        "======================================"
    )

    print(
        "SCAN COMPLETE"
    )

    print(
        "State saved."
    )

    print(
        "Trade history saved."
    )

    print(
        "Exiting normally."
    )

    print(
        "======================================"
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
