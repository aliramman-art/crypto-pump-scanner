# ============================================================
# CRYPTO DIVERGENCE SCANNER v10.0
# ============================================================
# Kraken Futures
# Closed 5m Candle
# RSI Divergence
# Trendline Break
# UT Bot
# Persistent Trade History
# Cumulative Performance
# OPEN SIGNAL P&L
# ============================================================

import os
import json
import time
import hashlib
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone

# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

INTERVAL = 5
CANDLE_LIMIT = 250

COINS = [
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "ADA",
    "DOGE",
    "AVAX",
    "LINK",
    "DOT",
    "LTC",
    "BCH",
    "ATOM",
    "UNI",
    "AAVE",
    "FIL",
    "ETC",
    "NEAR",
    "APT",
    "ARB",
    "OP",
    "SUI",
    "SEI",
    "INJ",
    "TIA",
    "TRX",
    "XLM",
    "ALGO",
    "VET",
    "MATIC",
    "HBAR",
]

STATE_FILE = "trade_history.json"

ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False

# ============================================================
# INDICATOR CONFIG
# ============================================================

RSI_PERIOD = 14

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MAX_PIVOT_GAP = 60

MIN_RSI_DIFFERENCE = 2.0
MIN_PRICE_DIFFERENCE_PERCENT = 0.10

SL_BUFFER_PERCENT = 0.10
MIN_TP_DISTANCE_PERCENT = 0.30

# UT BOT
UT_KEY_VALUE = 3.0
UT_ATR_PERIOD = 10

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
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram credentials not configured.")
        return

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
            json=payload,
            timeout=15
        )

        if not response.ok:
            print(
                "Telegram error:",
                response.text
            )

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "version": 1,
        "trades": {},
        "last_run": None,
    }


def load_state():

    if not os.path.exists(STATE_FILE):
        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        if "trades" not in state:
            state["trades"] = {}

        if "version" not in state:
            state["version"] = 1

        return state

    except Exception as e:

        print(
            "State load error:",
            e
        )

        return default_state()


def save_state(state):

    try:

        state["last_run"] = (
            datetime.now(
                timezone.utc
            ).isoformat()
        )

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
            "State save error:",
            e
        )


# ============================================================
# SIGNAL ID
# ============================================================

def make_signal_id(
    symbol,
    direction,
    signal_time
):

    raw = (
        f"{symbol}|"
        f"{direction}|"
        f"{signal_time}"
    )

    return hashlib.md5(
        raw.encode()
    ).hexdigest()


# ============================================================
# REGISTER SIGNAL
# ============================================================

def register_signal(
    state,
    signal
):

    symbol = signal["symbol"]

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:

        for trade in state["trades"].values():

            if (
                trade.get("symbol") == symbol
                and
                trade.get("status") == "OPEN"
            ):
                return False

    signal_id = make_signal_id(
        symbol,
        signal["direction"],
        signal["signal_time"]
    )

    if signal_id in state["trades"]:
        return False

    state["trades"][signal_id] = {
        "id": signal_id,
        "symbol": symbol,
        "name": signal["name"],
        "direction": signal["direction"],
        "entry": float(signal["entry"]),
        "sl": float(signal["sl"]),
        "tp": float(signal["tp"]),
        "signal_time": signal["signal_time"],
        "status": "OPEN",
        "result_r": None,
        "close_price": None,
        "close_time": None,
    }

    return True


# ============================================================
# EVALUATE OPEN TRADES
# ============================================================

def evaluate_open_trades(state):

    open_trades = [
        trade
        for trade in state["trades"].values()
        if trade.get("status") == "OPEN"
    ]

    if not open_trades:
        return

    grouped = {}

    for trade in open_trades:

        symbol = trade["symbol"]

        grouped.setdefault(
            symbol,
            []
        ).append(trade)

    for symbol, trades in grouped.items():

        df, error = get_candles(symbol)

        if df is None:

            print(
                "Cannot evaluate:",
                symbol,
                error
            )

            continue

        try:

            for trade in trades:

                signal_time = pd.Timestamp(
                    trade["signal_time"]
                )

                future = df[
                    df["time"] > signal_time
                ]

                if future.empty:
                    continue

                entry = float(
                    trade["entry"]
                )

                sl = float(
                    trade["sl"]
                )

                tp = float(
                    trade["tp"]
                )

                direction = trade[
                    "direction"
                ]

                for _, candle in future.iterrows():

                    high = float(
                        candle["high"]
                    )

                    low = float(
                        candle["low"]
                    )

                    close = float(
                        candle["close"]
                    )

                    candle_time = candle[
                        "time"
                    ]

                    # ------------------------------------------------
                    # BUY
                    # ------------------------------------------------

                    if direction == "BUY":

                        hit_sl = low <= sl
                        hit_tp = high >= tp

                        if hit_sl and hit_tp:

                            # Conservative:
                            # SL assumed first when
                            # both are touched
                            trade["status"] = "CLOSED"
                            trade["result_r"] = -1.0
                            trade["close_price"] = sl
                            trade["close_time"] = (
                                candle_time.isoformat()
                            )

                            break

                        elif hit_sl:

                            trade["status"] = "CLOSED"
                            trade["result_r"] = -1.0
                            trade["close_price"] = sl
                            trade["close_time"] = (
                                candle_time.isoformat()
                            )

                            break

                        elif hit_tp:

                            risk = entry - sl

                            if risk > 0:

                                result_r = (
                                    tp - entry
                                ) / risk

                            else:

                                result_r = 0.0

                            trade["status"] = "CLOSED"
                            trade["result_r"] = float(
                                result_r
                            )
                            trade["close_price"] = tp
                            trade["close_time"] = (
                                candle_time.isoformat()
                            )

                            break

                    # ------------------------------------------------
                    # SELL
                    # ------------------------------------------------

                    else:

                        hit_sl = high >= sl
                        hit_tp = low <= tp

                        if hit_sl and hit_tp:

                            trade["status"] = "CLOSED"
                            trade["result_r"] = -1.0
                            trade["close_price"] = sl
                            trade["close_time"] = (
                                candle_time.isoformat()
                            )

                            break

                        elif hit_sl:

                            trade["status"] = "CLOSED"
                            trade["result_r"] = -1.0
                            trade["close_price"] = sl
                            trade["close_time"] = (
                                candle_time.isoformat()
                            )

                            break

                        elif hit_tp:

                            risk = sl - entry

                            if risk > 0:

                                result_r = (
                                    entry - tp
                                ) / risk

                            else:

                                result_r = 0.0

                            trade["status"] = "CLOSED"
                            trade["result_r"] = float(
                                result_r
                            )
                            trade["close_price"] = tp
                            trade["close_time"] = (
                                candle_time.isoformat()
                            )

                            break

        except Exception as e:

            print(
                "Evaluation error:",
                symbol,
                e
            )


# ============================================================
# OPEN TRADE PERFORMANCE
# ============================================================

def calculate_open_trade_performance(state):

    """
    Calculate current P&L, R, MFE and MAE
    for currently OPEN trades.

    Current price:
    latest CLOSED 5m candle close.

    MFE / MAE:
    candles AFTER signal candle only.
    """

    open_trades = [
        trade
        for trade in state["trades"].values()
        if trade.get("status") == "OPEN"
    ]

    if not open_trades:
        return []

    grouped = {}

    for trade in open_trades:

        symbol = trade["symbol"]

        grouped.setdefault(
            symbol,
            []
        ).append(trade)

    performance = []

    for symbol, trades in grouped.items():

        df, error = get_candles(symbol)

        if df is None:

            print(
                "Cannot calculate open P&L:",
                symbol,
                error
            )

            continue

        try:

            # Latest CLOSED candle
            current_price = float(
                df.iloc[-1]["close"]
            )

            current_time = df.iloc[-1]["time"]

            for trade in trades:

                entry = float(
                    trade["entry"]
                )

                sl = float(
                    trade["sl"]
                )

                tp = float(
                    trade["tp"]
                )

                direction = trade[
                    "direction"
                ]

                signal_time = pd.Timestamp(
                    trade["signal_time"]
                )

                # ------------------------------------------------
                # Candles AFTER entry candle
                # ------------------------------------------------

                future = df[
                    df["time"] > signal_time
                ]

                # ------------------------------------------------
                # P&L
                # ------------------------------------------------

                if direction == "BUY":

                    pnl_percent = (
                        (
                            current_price
                            -
                            entry
                        )
                        /
                        entry
                        *
                        100
                    )

                    risk_distance = (
                        entry - sl
                    )

                    if not future.empty:

                        max_high = float(
                            future["high"].max()
                        )

                        min_low = float(
                            future["low"].min()
                        )

                        mfe_percent = (
                            (
                                max_high
                                -
                                entry
                            )
                            /
                            entry
                            *
                            100
                        )

                        mae_percent = (
                            (
                                min_low
                                -
                                entry
                            )
                            /
                            entry
                            *
                            100
                        )

                    else:

                        mfe_percent = 0.0
                        mae_percent = 0.0

                else:

                    # SELL

                    pnl_percent = (
                        (
                            entry
                            -
                            current_price
                        )
                        /
                        entry
                        *
                        100
                    )

                    risk_distance = (
                        sl - entry
                    )

                    if not future.empty:

                        max_high = float(
                            future["high"].max()
                        )

                        min_low = float(
                            future["low"].min()
                        )

                        # Falling price = profit
                        mfe_percent = (
                            (
                                entry
                                -
                                min_low
                            )
                            /
                            entry
                            *
                            100
                        )

                        # Rising price = loss
                        mae_percent = (
                            (
                                entry
                                -
                                max_high
                            )
                            /
                            entry
                            *
                            100
                        )

                    else:

                        mfe_percent = 0.0
                        mae_percent = 0.0

                # ------------------------------------------------
                # Current R
                # ------------------------------------------------

                if risk_distance > 0:

                    if direction == "BUY":

                        current_r = (
                            current_price
                            -
                            entry
                        ) / risk_distance

                    else:

                        current_r = (
                            entry
                            -
                            current_price
                        ) / risk_distance

                else:

                    current_r = 0.0

                # ------------------------------------------------
                # Duration
                # ------------------------------------------------

                duration = (
                    current_time
                    -
                    signal_time
                )

                total_minutes = int(
                    duration.total_seconds()
                    / 60
                )

                hours = (
                    total_minutes // 60
                )

                minutes = (
                    total_minutes % 60
                )

                if hours > 0:

                    duration_text = (
                        f"{hours}h "
                        f"{minutes}m"
                    )

                else:

                    duration_text = (
                        f"{minutes}m"
                    )

                performance.append({
                    "id": trade["id"],
                    "name": trade["name"],
                    "symbol": symbol,
                    "direction": direction,
                    "entry": entry,
                    "current_price": current_price,
                    "sl": sl,
                    "tp": tp,
                    "pnl_percent": float(
                        pnl_percent
                    ),
                    "current_r": float(
                        current_r
                    ),
                    "mfe_percent": float(
                        mfe_percent
                    ),
                    "mae_percent": float(
                        mae_percent
                    ),
                    "duration": duration_text,
                    "current_time":
                        current_time.isoformat(),
                })

        except Exception as e:

            print(
                "Open trade performance error:",
                symbol,
                e
            )

    return performance


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(state):

    trades = list(
        state["trades"].values()
    )

    total = len(trades)

    open_trades = [
        t
        for t in trades
        if t.get("status") == "OPEN"
    ]

    closed_trades = [
        t
        for t in trades
        if t.get("status") == "CLOSED"
    ]

    wins = [
        t
        for t in closed_trades
        if float(
            t.get("result_r", 0)
        ) > 0
    ]

    losses = [
        t
        for t in closed_trades
        if float(
            t.get("result_r", 0)
        ) <= 0
    ]

    total_closed = len(
        closed_trades
    )

    win_count = len(wins)
    loss_count = len(losses)

    if total_closed > 0:

        win_rate = (
            win_count
            /
            total_closed
            *
            100
        )

    else:

        win_rate = 0.0

    net_r = sum(
        float(
            t.get("result_r", 0)
        )
        for t in closed_trades
    )

    if total_closed > 0:

        expectancy = (
            net_r
            /
            total_closed
        )

    else:

        expectancy = 0.0

    gross_profit = sum(
        float(
            t.get("result_r", 0)
        )
        for t in wins
    )

    gross_loss = abs(
        sum(
            float(
                t.get("result_r", 0)
            )
            for t in losses
        )
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    # --------------------------------------------------------
    # BUY
    # --------------------------------------------------------

    buy_closed = [
        t
        for t in closed_trades
        if t["direction"] == "BUY"
    ]

    buy_wins = [
        t
        for t in buy_closed
        if float(
            t.get("result_r", 0)
        ) > 0
    ]

    buy_r = sum(
        float(
            t.get("result_r", 0)
        )
        for t in buy_closed
    )

    if len(buy_closed) > 0:

        buy_win_rate = (
            len(buy_wins)
            /
            len(buy_closed)
            *
            100
        )

    else:

        buy_win_rate = 0.0

    # --------------------------------------------------------
    # SELL
    # --------------------------------------------------------

    sell_closed = [
        t
        for t in closed_trades
        if t["direction"] == "SELL"
    ]

    sell_wins = [
        t
        for t in sell_closed
        if float(
            t.get("result_r", 0)
        ) > 0
    ]

    sell_r = sum(
        float(
            t.get("result_r", 0)
        )
        for t in sell_closed
    )

    if len(sell_closed) > 0:

        sell_win_rate = (
            len(sell_wins)
            /
            len(sell_closed)
            *
            100
        )

    else:

        sell_win_rate = 0.0

    return {
        "total": total,
        "open": len(open_trades),
        "closed": total_closed,
        "wins": win_count,
        "losses": loss_count,
        "win_rate": win_rate,
        "net_r": net_r,
        "expectancy": expectancy,
        "profit_factor": profit_factor,
        "buy_closed": len(buy_closed),
        "buy_wins": len(buy_wins),
        "buy_win_rate": buy_win_rate,
        "buy_r": buy_r,
        "sell_closed": len(sell_closed),
        "sell_wins": len(sell_wins),
        "sell_win_rate": sell_win_rate,
        "sell_r": sell_r,
    }


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )

    return rsi.fillna(50)


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(symbol):

    try:

        url = (
            f"{BASE_URL}/"
            f"{symbol}/"
            f"USD/"
            f"{INTERVAL}"
        )

        response = requests.get(
            url,
            timeout=15
        )

        if not response.ok:

            return None, (
                f"HTTP {response.status_code}"
            )

        data = response.json()

        if isinstance(data, dict):

            candles = (
                data.get("candles")
                or data.get("data")
                or []
            )

        else:

            candles = data

        if not candles:

            return None, "No candle data"

        rows = []

        for candle in candles:

            if isinstance(candle, dict):

                timestamp = (
                    candle.get("time")
                    or candle.get("timestamp")
                    or candle.get("t")
                )

                open_price = (
                    candle.get("open")
                    or candle.get("o")
                )

                high_price = (
                    candle.get("high")
                    or candle.get("h")
                )

                low_price = (
                    candle.get("low")
                    or candle.get("l")
                )

                close_price = (
                    candle.get("close")
                    or candle.get("c")
                )

                volume = (
                    candle.get("volume")
                    or candle.get("v")
                    or 0
                )

            else:

                timestamp = candle[0]
                open_price = candle[1]
                high_price = candle[2]
                low_price = candle[3]
                close_price = candle[4]

                volume = (
                    candle[5]
                    if len(candle) > 5
                    else 0
                )

            rows.append({
                "time": pd.to_datetime(
                    timestamp,
                    unit="s",
                    utc=True
                ),
                "open": float(
                    open_price
                ),
                "high": float(
                    high_price
                ),
                "low": float(
                    low_price
                ),
                "close": float(
                    close_price
                ),
                "volume": float(
                    volume
                ),
            })

        df = pd.DataFrame(
            rows
        )

        if df.empty:

            return None, "Empty dataframe"

        df = df.sort_values(
            "time"
        ).drop_duplicates(
            "time"
        )

        # --------------------------------------------------------
        # Remove currently incomplete candle
        # --------------------------------------------------------

        now = pd.Timestamp.now(
            tz="UTC"
        )

        current_bucket = (
            now.floor(
                f"{INTERVAL}min"
            )
        )

        df = df[
            df["time"]
            <
            current_bucket
        ]

        if len(df) < 50:

            return None, (
                "Not enough closed candles"
            )

        df = df.tail(
            CANDLE_LIMIT
        ).reset_index(
            drop=True
        )

        return df, None

    except Exception as e:

        return None, str(e)


# ============================================================
# PIVOT LOWS
# ============================================================

def pivot_lows(
    series,
    left=2,
    right=2
):

    pivots = []

    values = series.values

    for i in range(
        left,
        len(values) - right
    ):

        window = values[
            i - left:
            i + right + 1
        ]

        if (
            values[i]
            ==
            np.min(window)
        ):

            pivots.append(i)

    return pivots


# ============================================================
# PIVOT HIGHS
# ============================================================

def pivot_highs(
    series,
    left=2,
    right=2
):

    pivots = []

    values = series.values

    for i in range(
        left,
        len(values) - right
    ):

        window = values[
            i - left:
            i + right + 1
        ]

        if (
            values[i]
            ==
            np.max(window)
        ):

            pivots.append(i)

    return pivots


# ============================================================
# BULLISH DIVERGENCE
# ============================================================

def find_bullish_divergence(
    df
):

    price_pivots = pivot_lows(
        df["low"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(price_pivots) < 2:
        return None

    for i in range(
        len(price_pivots) - 1,
        0,
        -1
    ):

        p2 = price_pivots[i]
        p1 = price_pivots[i - 1]

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        price1 = float(
            df.iloc[p1]["low"]
        )

        price2 = float(
            df.iloc[p2]["low"]
        )

        rsi1 = float(
            df.iloc[p1]["rsi"]
        )

        rsi2 = float(
            df.iloc[p2]["rsi"]
        )

        price_diff = (
            (
                price2 - price1
            )
            /
            price1
            *
            100
        )

        # Lower low in price
        # Higher low in RSI

        if (
            price2 < price1
            and
            rsi2 > rsi1
            and
            (rsi2 - rsi1)
            >= MIN_RSI_DIFFERENCE
            and
            abs(price_diff)
            >= MIN_PRICE_DIFFERENCE_PERCENT
        ):

            return {
                "pivot1": p1,
                "pivot2": p2,
                "price1": price1,
                "price2": price2,
                "rsi1": rsi1,
                "rsi2": rsi2,
            }

    return None


# ============================================================
# BEARISH DIVERGENCE
# ============================================================

def find_bearish_divergence(
    df
):

    price_pivots = pivot_highs(
        df["high"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(price_pivots) < 2:
        return None

    for i in range(
        len(price_pivots) - 1,
        0,
        -1
    ):

        p2 = price_pivots[i]
        p1 = price_pivots[i - 1]

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:
            continue

        price1 = float(
            df.iloc[p1]["high"]
        )

        price2 = float(
            df.iloc[p2]["high"]
        )

        rsi1 = float(
            df.iloc[p1]["rsi"]
        )

        rsi2 = float(
            df.iloc[p2]["rsi"]
        )

        price_diff = (
            (
                price2 - price1
            )
            /
            price1
            *
            100
        )

        # Higher high in price
        # Lower high in RSI

        if (
            price2 > price1
            and
            rsi2 < rsi1
            and
            (rsi1 - rsi2)
            >= MIN_RSI_DIFFERENCE
            and
            abs(price_diff)
            >= MIN_PRICE_DIFFERENCE_PERCENT
        ):

            return {
                "pivot1": p1,
                "pivot2": p2,
                "price1": price1,
                "price2": price2,
                "rsi1": rsi1,
                "rsi2": rsi2,
            }

    return None


# ============================================================
# DESCENDING TRENDLINE BREAK
# ============================================================

def descending_trendline_break(
    df
):

    pivots = pivot_highs(
        df["high"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(pivots) < 2:
        return False

    p1 = pivots[-2]
    p2 = pivots[-1]

    if p2 - p1 > MAX_PIVOT_GAP:
        return False

    high1 = float(
        df.iloc[p1]["high"]
    )

    high2 = float(
        df.iloc[p2]["high"]
    )

    if high2 >= high1:
        return False

    slope = (
        high2 - high1
    ) / (
        p2 - p1
    )

    current_index = len(df) - 1

    trendline_value = (
        high2
        +
        slope
        *
        (
            current_index
            -
            p2
        )
    )

    current_close = float(
        df.iloc[-1]["close"]
    )

    previous_close = float(
        df.iloc[-2]["close"]
    )

    return (
        previous_close
        <=
        trendline_value
        and
        current_close
        >
        trendline_value
    )


# ============================================================
# ASCENDING TRENDLINE BREAK
# ============================================================

def ascending_trendline_break(
    df
):

    pivots = pivot_lows(
        df["low"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(pivots) < 2:
        return False

    p1 = pivots[-2]
    p2 = pivots[-1]

    if p2 - p1 > MAX_PIVOT_GAP:
        return False

    low1 = float(
        df.iloc[p1]["low"]
    )

    low2 = float(
        df.iloc[p2]["low"]
    )

    if low2 <= low1:
        return False

    slope = (
        low2 - low1
    ) / (
        p2 - p1
    )

    current_index = len(df) - 1

    trendline_value = (
        low2
        +
        slope
        *
        (
            current_index
            -
            p2
        )
    )

    current_close = float(
        df.iloc[-1]["close"]
    )

    previous_close = float(
        df.iloc[-2]["close"]
    )

    return (
        previous_close
        >=
        trendline_value
        and
        current_close
        <
        trendline_value
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=14
):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high
        -
        previous_close
    ).abs()

    tr3 = (
        low
        -
        previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    atr = tr.ewm(
        alpha=1 / period,
        adjust=False
    ).mean()

    return atr


# ============================================================
# UT BOT
# ============================================================

def calculate_ut_bot(
    df,
    key_value=3.0,
    atr_period=10
):

    src = df["close"]

    atr = calculate_atr(
        df,
        atr_period
    )

    n_loss = (
        key_value
        *
        atr
    )

    trailing_stop = pd.Series(
        index=df.index,
        dtype=float
    )

    trailing_stop.iloc[0] = (
        src.iloc[0]
        -
        n_loss.iloc[0]
    )

    for i in range(
        1,
        len(df)
    ):

        prev_stop = (
            trailing_stop.iloc[i - 1]
        )

        prev_src = (
            src.iloc[i - 1]
        )

        current_src = (
            src.iloc[i]
        )

        current_loss = (
            n_loss.iloc[i]
        )

        if (
            current_src > prev_stop
            and
            prev_src > prev_stop
        ):

            trailing_stop.iloc[i] = max(
                prev_stop,
                current_src - current_loss
            )

        elif (
            current_src < prev_stop
            and
            prev_src < prev_stop
        ):

            trailing_stop.iloc[i] = min(
                prev_stop,
                current_src + current_loss
            )

        elif current_src > prev_stop:

            trailing_stop.iloc[i] = (
                current_src
                -
                current_loss
            )

        else:

            trailing_stop.iloc[i] = (
                current_src
                +
                current_loss
            )

    position = pd.Series(
        0,
        index=df.index
    )

    for i in range(
        1,
        len(df)
    ):

        if (
            src.iloc[i - 1]
            <
            trailing_stop.iloc[i - 1]
            and
            src.iloc[i]
            >
            trailing_stop.iloc[i]
        ):

            position.iloc[i] = 1

        elif (
            src.iloc[i - 1]
            >
            trailing_stop.iloc[i - 1]
            and
            src.iloc[i]
            <
            trailing_stop.iloc[i]
        ):

            position.iloc[i] = -1

        else:

            position.iloc[i] = (
                position.iloc[i - 1]
            )

    return (
        trailing_stop,
        position
    )


# ============================================================
# RESISTANCE
# ============================================================

def nearest_resistance(
    df,
    price
):

    levels = []

    for i in pivot_highs(
        df["high"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    ):

        level = float(
            df.iloc[i]["high"]
        )

        if level > price:
            levels.append(level)

    if not levels:
        return None

    return min(levels)


# ============================================================
# SUPPORT
# ============================================================

def nearest_support(
    df,
    price
):

    levels = []

    for i in pivot_lows(
        df["low"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    ):

        level = float(
            df.iloc[i]["low"]
        )

        if level < price:
            levels.append(level)

    if not levels:
        return None

    return max(levels)


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(
    symbol
):

    df, error = get_candles(
        symbol
    )

    if df is None:

        return None, error

    try:

        # --------------------------------------------------------
        # RSI
        # --------------------------------------------------------

        df["rsi"] = calculate_rsi(
            df["close"],
            RSI_PERIOD
        )

        # --------------------------------------------------------
        # UT BOT
        # --------------------------------------------------------

        (
            df["ut_stop"],
            df["ut_position"]
        ) = calculate_ut_bot(
            df,
            UT_KEY_VALUE,
            UT_ATR_PERIOD
        )

        # --------------------------------------------------------
        # Latest CLOSED candle
        # --------------------------------------------------------

        current = df.iloc[-1]

        previous = df.iloc[-2]

        price = float(
            current["close"]
        )

        signal_time = (
            current["time"]
            .isoformat()
        )

        # --------------------------------------------------------
        # Divergence
        # --------------------------------------------------------

        bullish_div = (
            find_bullish_divergence(
                df
            )
        )

        bearish_div = (
            find_bearish_divergence(
                df
            )
        )

        # --------------------------------------------------------
        # Trendline
        # --------------------------------------------------------

        bullish_break = (
            descending_trendline_break(
                df
            )
        )

        bearish_break = (
            ascending_trendline_break(
                df
            )
        )

        # --------------------------------------------------------
        # UT
        # --------------------------------------------------------

        ut_position = int(
            current["ut_position"]
        )

        ut_buy = (
            ut_position == 1
        )

        ut_sell = (
            ut_position == -1
        )

        # --------------------------------------------------------
        # BUY
        # --------------------------------------------------------

        buy_conditions = 0

        if bullish_div:
            buy_conditions += 1

        if bullish_break:
            buy_conditions += 1

        if ut_buy:
            buy_conditions += 1

        # --------------------------------------------------------
        # SELL
        # --------------------------------------------------------

        sell_conditions = 0

        if bearish_div:
            sell_conditions += 1

        if bearish_break:
            sell_conditions += 1

        if ut_sell:
            sell_conditions += 1

        direction = None

        if buy_conditions >= 2:

            direction = "BUY"

        elif sell_conditions >= 2:

            direction = "SELL"

        if direction is None:

            return {
                "symbol": symbol,
                "name": symbol,
                "signal": False,
                "signal_time": signal_time,
                "price": price,
                "rsi": float(
                    current["rsi"]
                ),
                "bullish_divergence":
                    bool(bullish_div),
                "bearish_divergence":
                    bool(bearish_div),
                "bullish_break":
                    bool(bullish_break),
                "bearish_break":
                    bool(bearish_break),
                "ut_position":
                    ut_position,
            }, None

        # --------------------------------------------------------
        # ATR
        # --------------------------------------------------------

        atr = float(
            calculate_atr(
                df
            ).iloc[-1]
        )

        if atr <= 0:

            return None, (
                "Invalid ATR"
            )

        # --------------------------------------------------------
        # SL / TP
        # --------------------------------------------------------

        if direction == "BUY":

            support = (
                nearest_support(
                    df,
                    price
                )
            )

            if support is None:
                support = price - atr

            sl = (
                support
                *
                (
                    1
                    -
                    SL_BUFFER_PERCENT
                    /
                    100
                )
            )

            resistance = (
                nearest_resistance(
                    df,
                    price
                )
            )

            if (
                resistance is None
                or
                resistance
                <=
                price
            ):

                resistance = (
                    price
                    +
                    atr
                )

            tp = resistance

            minimum_tp = (
                price
                *
                (
                    1
                    +
                    MIN_TP_DISTANCE_PERCENT
                    /
                    100
                )
            )

            if tp < minimum_tp:
                tp = minimum_tp

            if sl >= price:

                sl = (
                    price
                    -
                    atr
                )

        else:

            resistance = (
                nearest_resistance(
                    df,
                    price
                )
            )

            if resistance is None:
                resistance = price + atr

            sl = (
                resistance
                *
                (
                    1
                    +
                    SL_BUFFER_PERCENT
                    /
                    100
                )
            )

            support = (
                nearest_support(
                    df,
                    price
                )
            )

            if (
                support is None
                or
                support
                >=
                price
            ):

                support = (
                    price
                    -
                    atr
                )

            tp = support

            minimum_tp = (
                price
                *
                (
                    1
                    -
                    MIN_TP_DISTANCE_PERCENT
                    /
                    100
                )
            )

            if tp > minimum_tp:
                tp = minimum_tp

            if sl <= price:

                sl = (
                    price
                    +
                    atr
                )

        # --------------------------------------------------------
        # Risk / Reward
        # --------------------------------------------------------

        if direction == "BUY":

            risk = price - sl
            reward = tp - price

        else:

            risk = sl - price
            reward = price - tp

        if risk <= 0:

            return None, (
                "Invalid risk"
            )

        rr = (
            reward
            /
            risk
        )

        return {
            "symbol": symbol,
            "name": symbol,
            "signal": True,
            "signal_time": signal_time,
            "direction": direction,
            "entry": price,
            "sl": sl,
            "tp": tp,
            "rr": rr,
            "rsi": float(
                current["rsi"]
            ),
            "atr": atr,
            "bullish_divergence":
                bool(bullish_div),
            "bearish_divergence":
                bool(bearish_div),
            "bullish_break":
                bool(bullish_break),
            "bearish_break":
                bool(bearish_break),
            "ut_position":
                ut_position,
            "buy_conditions":
                buy_conditions,
            "sell_conditions":
                sell_conditions,
        }, None

    except Exception as e:

        return None, str(e)


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(
    price
):

    price = float(price)

    if price >= 1000:

        return f"{price:,.2f}"

    elif price >= 100:

        return f"{price:,.3f}"

    elif price >= 1:

        return f"{price:.4f}"

    elif price >= 0.1:

        return f"{price:.5f}"

    elif price >= 0.01:

        return f"{price:.6f}"

    else:

        return f"{price:.8f}"


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal
):

    direction = signal[
        "direction"
    ]

    if direction == "BUY":

        emoji = "🟢"

    else:

        emoji = "🔴"

    return (
        f"{emoji} "
        f"<b>#{signal['name']}/USD "
        f"- {direction}</b>\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎯 Entry: "
        f"<code>"
        f"{format_price(signal['entry'])}"
        f"</code>\n"
        f"🛑 SL: "
        f"<code>"
        f"{format_price(signal['sl'])}"
        f"</code>\n"
        f"🎯 TP: "
        f"<code>"
        f"{format_price(signal['tp'])}"
        f"</code>\n"
        f"📊 R/R: "
        f"<b>"
        f"{signal['rr']:.2f}"
        f"</b>\n"
        f"📈 RSI: "
        f"<b>"
        f"{signal['rsi']:.1f}"
        f"</b>\n"
        f"📡 UT Bot: "
        f"<b>"
        f"{signal['ut_position']}"
        f"</b>"
    )


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    results,
    new_signals,
    stats,
    open_performance
):

    lines = []

    lines.append(
        "📡 <b>CRYPTO DIVERGENCE "
        "SCANNER v10.0</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        "🕐 "
        +
        datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S UTC"
        )
    )

    lines.append(
        "⏱ Timeframe: "
        "<b>5M CLOSED</b>"
    )

    lines.append("")

    # ========================================================
    # DATA STATUS
    # ========================================================

    total_results = len(
        results
    )

    data_ok = sum(
        1
        for r in results
        if r.get("error") is None
    )

    data_error = (
        total_results
        -
        data_ok
    )

    analysis_error = sum(
        1
        for r in results
        if (
            r.get("error")
            and
            r.get("data_error") is False
        )
    )

    lines.append(
        "📊 <b>DATA STATUS</b>"
    )

    lines.append(
        f"DATA OK: "
        f"<b>{data_ok}/{total_results}</b>"
    )

    lines.append(
        f"⚠️ DATA ERROR: "
        f"<b>{data_error}</b>"
    )

    lines.append(
        f"⚠️ ANALYSIS ERROR: "
        f"<b>{analysis_error}</b>"
    )

    # ========================================================
    # CUMULATIVE PERFORMANCE
    # ========================================================

    lines.append("")

    lines.append(
        "📊 <b>CUMULATIVE PERFORMANCE</b>"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(
        f"📈 Total Trades: "
        f"<b>{stats['total']}</b>"
    )

    lines.append(
        f"🟡 Open: "
        f"<b>{stats['open']}</b>"
    )

    lines.append(
        f"⚪ Closed: "
        f"<b>{stats['closed']}</b>"
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
        f"🎯 Win Rate: "
        f"<b>{stats['win_rate']:.2f}%</b>"
    )

    lines.append(
        f"💰 Net R: "
        f"<b>{stats['net_r']:+.2f}R</b>"
    )

    lines.append(
        f"📐 Expectancy: "
        f"<b>{stats['expectancy']:+.2f}R</b>"
    )

    if np.isinf(
        stats["profit_factor"]
    ):

        pf_text = "∞"

    else:

        pf_text = (
            f"{stats['profit_factor']:.2f}"
        )

    lines.append(
        f"⚖️ Profit Factor: "
        f"<b>{pf_text}</b>"
    )

    # ========================================================
    # BUY / SELL
    # ========================================================

    lines.append("")

    lines.append(
        "🟢 <b>BUY STATS</b>"
    )

    lines.append(
        f"Trades: "
        f"<b>{stats['buy_closed']}</b> | "
        f"Wins: "
        f"<b>{stats['buy_wins']}</b> | "
        f"WR: "
        f"<b>{stats['buy_win_rate']:.2f}%</b> | "
        f"R: "
        f"<b>{stats['buy_r']:+.2f}</b>"
    )

    lines.append("")

    lines.append(
        "🔴 <b>SELL STATS</b>"
    )

    lines.append(
        f"Trades: "
        f"<b>{stats['sell_closed']}</b> | "
        f"Wins: "
        f"<b>{stats['sell_wins']}</b> | "
        f"WR: "
        f"<b>{stats['sell_win_rate']:.2f}%</b> | "
        f"R: "
        f"<b>{stats['sell_r']:+.2f}</b>"
    )

    # ========================================================
    # OPEN TRADE PERFORMANCE
    # ========================================================

    if open_performance:

        lines.append("")

        lines.append(
            "📈 <b>OPEN SIGNAL P&L</b>"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for trade in open_performance:

            direction = (
                trade["direction"]
            )

            if direction == "BUY":

                emoji = "🟢"

            else:

                emoji = "🔴"

            pnl = trade[
                "pnl_percent"
            ]

            current_r = trade[
                "current_r"
            ]

            mfe = trade[
                "mfe_percent"
            ]

            mae = trade[
                "mae_percent"
            ]

            if pnl >= 0:

                pnl_emoji = "📈"

            else:

                pnl_emoji = "📉"

            if current_r >= 0:

                r_emoji = "🟢"

            else:

                r_emoji = "🔴"

            lines.append(
                f"{emoji} "
                f"<b>"
                f"{trade['name']}/USD "
                f"{direction}"
                f"</b>"
            )

            lines.append(
                f"💵 Entry: "
                f"<code>"
                f"{format_price(trade['entry'])}"
                f"</code>"
            )

            lines.append(
                f"📍 Current: "
                f"<code>"
                f"{format_price(trade['current_price'])}"
                f"</code>"
            )

            lines.append(
                f"🛑 SL: "
                f"<code>"
                f"{format_price(trade['sl'])}"
                f"</code>"
            )

            lines.append(
                f"🎯 TP: "
                f"<code>"
                f"{format_price(trade['tp'])}"
                f"</code>"
            )

            lines.append(
                f"{pnl_emoji} "
                f"P&L: "
                f"<b>"
                f"{pnl:+.2f}%"
                f"</b>"
            )

            lines.append(
                f"{r_emoji} "
                f"Current R: "
                f"<b>"
                f"{current_r:+.2f}R"
                f"</b>"
            )

            lines.append(
                f"🔝 MFE: "
                f"<b>"
                f"+{mfe:.2f}%"
                f"</b>"
            )

            lines.append(
                f"🔻 MAE: "
                f"<b>"
                f"{mae:.2f}%"
                f"</b>"
            )

            lines.append(
                f"⏱ Duration: "
                f"<b>"
                f"{trade['duration']}"
                f"</b>"
            )

            lines.append("")

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    lines.append("")

    if new_signals:

        lines.append(
            "🚨 <b>NEW CONFIRMED SIGNALS</b>"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for signal in new_signals:

            lines.append(
                format_signal(
                    signal
                )
            )

            lines.append("")

    else:

        lines.append(
            "👀 <b>NEW SIGNALS</b>"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        lines.append(
            "فعلاً سیگنال تأییدشده جدیدی نداریم."
        )

    # ========================================================
    # ERRORS
    # ========================================================

    errors = [
        r
        for r in results
        if r.get("error")
    ]

    if errors:

        lines.append("")

        lines.append(
            "⚠️ <b>ERRORS</b>"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for item in errors:

            lines.append(
                f"• "
                f"{item.get('symbol', '?')}: "
                f"{item.get('error')}"
            )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        "============================================================"
    )

    print(
        "CRYPTO DIVERGENCE SCANNER v10.0"
    )

    print(
        "============================================================"
    )

    # ========================================================
    # LOAD STATE
    # ========================================================

    state = load_state()

    # ========================================================
    # EVALUATE PREVIOUS OPEN TRADES
    # ========================================================

    evaluate_open_trades(
        state
    )

    # ========================================================
    # ANALYZE COINS
    # ========================================================

    results = []

    for symbol in COINS:

        print(
            f"Analyzing {symbol}..."
        )

        signal, error = (
            analyze_coin(symbol)
        )

        if error:

            results.append({
                "symbol": symbol,
                "error": error,
                "data_error": True,
            })

            continue

        signal["error"] = None

        results.append(
            signal
        )

    # ========================================================
    # REGISTER NEW SIGNALS
    # ========================================================

    new_signals = []

    for signal in results:

        if signal.get(
            "error"
        ):

            continue

        if not signal.get(
            "signal",
            False
        ):

            continue

        registered = (
            register_signal(
                state,
                signal
            )
        )

        if registered:

            new_signals.append(
                signal
            )

    # ========================================================
    # SAVE
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # EVALUATE AGAIN
    # ========================================================

    evaluate_open_trades(
        state
    )

    save_state(
        state
    )

    # ========================================================
    # STATISTICS
    # ========================================================

    stats = calculate_statistics(
        state
    )

    # ========================================================
    # OPEN PERFORMANCE
    # ========================================================

    open_performance = (
        calculate_open_trade_performance(
            state
        )
    )

    # ========================================================
    # CONSOLE
    # ========================================================

    print(
        "\n"
        "================ PERFORMANCE ================"
    )

    print(
        f"Total Trades: "
        f"{stats['total']}"
    )

    print(
        f"Open: "
        f"{stats['open']}"
    )

    print(
        f"Closed: "
        f"{stats['closed']}"
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
        f"Win Rate: "
        f"{stats['win_rate']:.2f}%"
    )

    print(
        f"Net R: "
        f"{stats['net_r']:+.2f}R"
    )

    print(
        f"Expectancy: "
        f"{stats['expectancy']:+.2f}R"
    )

    print(
        f"Profit Factor: "
        f"{stats['profit_factor']}"
    )

    # ========================================================
    # OPEN SIGNAL CONSOLE
    # ========================================================

    if open_performance:

        print(
            "\n"
            "================ OPEN SIGNALS ================"
        )

        for trade in open_performance:

            print(
                f"{trade['name']} "
                f"{trade['direction']} | "
                f"Entry={format_price(trade['entry'])} | "
                f"Current={format_price(trade['current_price'])} | "
                f"P&L={trade['pnl_percent']:+.2f}% | "
                f"R={trade['current_r']:+.2f} | "
                f"MFE={trade['mfe_percent']:+.2f}% | "
                f"MAE={trade['mae_percent']:+.2f}% | "
                f"Duration={trade['duration']}"
            )

    # ========================================================
    # REPORT
    # ========================================================

    report = format_report(
        results,
        new_signals,
        stats,
        open_performance
    )

    print(
        "\n"
        "================ REPORT ================"
    )

    print(
        report
    )

    # ========================================================
    # TELEGRAM
    # ========================================================

    send_telegram(
        report
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
