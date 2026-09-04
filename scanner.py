# ============================================================
# CRYPTO DIVERGENCE SCANNER v10.0
# Kraken Futures
# 5M CLOSED CANDLES
#
# STRATEGY
#
# BUY:
#   Regular Bullish RSI Divergence
#   +
#   Descending Trendline Breakout
#   +
#   UT BOT 3,10 = BUY DIRECTION
#
# SELL:
#   Regular Bearish RSI Divergence
#   +
#   Ascending Trendline Breakdown
#   +
#   UT BOT 3,10 = SELL DIRECTION
#
# ADDED:
#   - Persistent trade history
#   - Automatic TP / SL evaluation
#   - WIN / LOSS / OPEN
#   - Cumulative Win Rate
#   - Net R
#   - Expectancy
#   - Profit Factor
#   - BUY / SELL statistics
#   - Duplicate signal protection
#
# ============================================================

import os
import json
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

CANDLE_LIMIT = 500

MAX_WORKERS = 15

TOP_SIGNAL_LIMIT = 10

# Persistent trade history
STATE_FILE = "trade_history.json"

# Prevent multiple simultaneous OPEN trades
# on the same coin.
ALLOW_MULTIPLE_OPEN_PER_SYMBOL = False


# ============================================================
# RSI CONFIG
# ============================================================

RSI_PERIOD = 14


# ============================================================
# PIVOT CONFIG
# ============================================================

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MAX_PIVOT_GAP = 60


# ============================================================
# DIVERGENCE CONFIG
# ============================================================

MIN_PRICE_DIFF_PERCENT = 0.05

MIN_RSI_DIFF = 2.0

MAX_DIVERGENCE_AGE_MINUTES = 120


# ============================================================
# STOP LOSS CONFIG
# ============================================================

SL_BUFFER_PERCENT = 0.10


# ============================================================
# TAKE PROFIT CONFIG
# ============================================================

MIN_TP_DISTANCE_PERCENT = 0.30


# ============================================================
# UT BOT CONFIG
# ============================================================

UT_KEY_VALUE = 3.0

UT_ATR_PERIOD = 10

UT_USE_HEIKIN_ASHI = False


# ============================================================
# COINS
# ============================================================

COINS = {

    "BTC": "pf_xbtusd",
    "ETH": "pf_ethusd",
    "BNB": "pf_bnbusd",
    "SOL": "pf_solusd",
    "XRP": "pf_xrpusd",
    "DOGE": "pf_dogeusd",
    "ADA": "pf_adausd",
    "AVAX": "pf_avaxusd",
    "LINK": "pf_linkusd",
    "DOT": "pf_dotusd",
    "TRX": "pf_trxusd",
    "LTC": "pf_ltcusd",
    "BCH": "pf_bchusd",
    "ATOM": "pf_atomusd",
    "UNI": "pf_uniusd",
    "ETC": "pf_etcusd",
    "XLM": "pf_xlmusd",
    "NEAR": "pf_nearusd",
    "APT": "pf_aptusd",
    "FIL": "pf_filusd",
    "ARB": "pf_arbusd",
    "OP": "pf_opusd",
    "SUI": "pf_suiusd",
    "INJ": "pf_injusd",
    "AAVE": "pf_aaveusd",
    "MKR": "pf_mkrusd",
    "ALGO": "pf_algousd",
    "VET": "pf_vetusd",
    "SEI": "pf_seiusd",
    "TIA": "pf_tiausd",

}


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN"
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID"
)


def send_telegram(text):

    if (
        not TELEGRAM_BOT_TOKEN
        or not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials missing."
        )

        return False

    url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": text,

        "parse_mode": "HTML",

    }

    try:

        response = requests.post(

            url,

            json=payload,

            timeout=20

        )

        print(
            "Telegram HTTP:",
            response.status_code
        )

        if response.status_code != 200:

            print(
                response.text
            )

        return (
            response.status_code == 200
        )

    except Exception as e:

        print(
            "Telegram error:",
            e
        )

        return False


# ============================================================
# PERSISTENT STATE
# ============================================================

def default_state():

    return {

        "version": 1,

        "trades": {}

    }


def load_state():

    if not os.path.exists(
        STATE_FILE
    ):

        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        if not isinstance(
            state,
            dict
        ):

            return default_state()

        if "trades" not in state:

            state["trades"] = {}

        return state

    except Exception as e:

        print(
            "State load error:",
            e
        )

        return default_state()


def save_state(state):

    try:

        temp_file = (
            STATE_FILE
            +
            ".tmp"
        )

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

        return True

    except Exception as e:

        print(
            "State save error:",
            e
        )

        return False


# ============================================================
# SIGNAL ID
# ============================================================

def make_signal_id(signal):

    return "|".join([

        signal["name"],

        signal["direction"],

        signal["signal_time"],

        signal["swing_time"],

        signal["trendline"],

        str(
            signal.get(
                "div_p1",
                ""
            )
        ),

        str(
            signal.get(
                "div_p2",
                ""
            )
        ),

    ])


# ============================================================
# REGISTER SIGNAL
# ============================================================

def register_signal(
    state,
    signal
):

    signal_id = make_signal_id(
        signal
    )

    # --------------------------------------------------------
    # Duplicate protection
    # --------------------------------------------------------

    if signal_id in state["trades"]:

        return False

    # --------------------------------------------------------
    # Only one OPEN trade per symbol
    # --------------------------------------------------------

    if not ALLOW_MULTIPLE_OPEN_PER_SYMBOL:

        for trade in state["trades"].values():

            if (

                trade.get("symbol")
                ==
                signal["symbol"]

                and

                trade.get("status")
                ==
                "OPEN"

            ):

                return False

    # --------------------------------------------------------
    # Create trade
    # --------------------------------------------------------

    trade = dict(signal)

    trade["id"] = signal_id

    trade["status"] = "OPEN"

    trade["opened_at"] = (
        signal["signal_time"]
    )

    trade["closed_at"] = None

    trade["result_price"] = None

    trade["result_r"] = None

    trade["result_reason"] = None

    state["trades"][
        signal_id
    ] = trade

    return True


# ============================================================
# EVALUATE OPEN TRADES
# ============================================================

def evaluate_open_trades(
    state
):

    open_trades = [

        trade

        for trade in
        state["trades"].values()

        if trade.get("status")
        ==
        "OPEN"

    ]

    if not open_trades:

        return 0

    grouped = {}

    for trade in open_trades:

        symbol = trade["symbol"]

        grouped.setdefault(
            symbol,
            []
        ).append(trade)

    closed_count = 0

    symbol_to_name = {

        symbol: name

        for name, symbol
        in COINS.items()

    }

    for symbol, trades in grouped.items():

        df, error = get_candles(
            symbol
        )

        if df is None:

            print(

                "Cannot evaluate",
                symbol,
                error

            )

            continue

        for trade in trades:

            try:

                signal_time = pd.Timestamp(

                    trade["signal_time"]

                )

                # ------------------------------------------------
                # IMPORTANT:
                #
                # The signal entry is the CLOSE of the
                # signal candle.
                #
                # Therefore evaluation starts from the
                # NEXT closed candle.
                # ------------------------------------------------

                future = df[

                    df["time"]
                    >
                    signal_time

                ].sort_values(
                    "time"
                )

                if future.empty:

                    continue

                direction = (
                    trade["direction"]
                )

                sl = float(
                    trade["sl"]
                )

                tp = float(
                    trade["tp"]
                )

                rr = float(
                    trade["rr"]
                )

                for _, candle in future.iterrows():

                    candle_time = (
                        candle["time"]
                    )

                    candle_high = float(
                        candle["high"]
                    )

                    candle_low = float(
                        candle["low"]
                    )

                    # ====================================================
                    # BUY
                    # ====================================================

                    if direction == "BUY":

                        hit_sl = (
                            candle_low
                            <=
                            sl
                        )

                        hit_tp = (
                            candle_high
                            >=
                            tp
                        )

                        # ------------------------------------------------
                        # Both TP and SL inside same candle.
                        #
                        # OHLC cannot tell us which happened first.
                        #
                        # Conservative classification = LOSS.
                        # ------------------------------------------------

                        if hit_sl and hit_tp:

                            trade["status"] = "LOSS"

                            trade["closed_at"] = (
                                candle_time.isoformat()
                            )

                            trade["result_price"] = sl

                            trade["result_r"] = -1.0

                            trade["result_reason"] = (
                                "both_hit_same_candle_"
                                "conservative_loss"
                            )

                            closed_count += 1

                            print(

                                f"LOSS "
                                f"{trade['name']} BUY "
                                f"both TP/SL same candle"

                            )

                            break

                        if hit_sl:

                            trade["status"] = "LOSS"

                            trade["closed_at"] = (
                                candle_time.isoformat()
                            )

                            trade["result_price"] = sl

                            trade["result_r"] = -1.0

                            trade["result_reason"] = (
                                "SL"
                            )

                            closed_count += 1

                            print(

                                f"LOSS "
                                f"{trade['name']} BUY "
                                f"SL"

                            )

                            break

                        if hit_tp:

                            trade["status"] = "WIN"

                            trade["closed_at"] = (
                                candle_time.isoformat()
                            )

                            trade["result_price"] = tp

                            trade["result_r"] = rr

                            trade["result_reason"] = (
                                "TP"
                            )

                            closed_count += 1

                            print(

                                f"WIN "
                                f"{trade['name']} BUY "
                                f"TP +{rr:.2f}R"

                            )

                            break

                    # ====================================================
                    # SELL
                    # ====================================================

                    elif direction == "SELL":

                        hit_sl = (
                            candle_high
                            >=
                            sl
                        )

                        hit_tp = (
                            candle_low
                            <=
                            tp
                        )

                        if hit_sl and hit_tp:

                            trade["status"] = "LOSS"

                            trade["closed_at"] = (
                                candle_time.isoformat()
                            )

                            trade["result_price"] = sl

                            trade["result_r"] = -1.0

                            trade["result_reason"] = (
                                "both_hit_same_candle_"
                                "conservative_loss"
                            )

                            closed_count += 1

                            print(

                                f"LOSS "
                                f"{trade['name']} SELL "
                                f"both TP/SL same candle"

                            )

                            break

                        if hit_sl:

                            trade["status"] = "LOSS"

                            trade["closed_at"] = (
                                candle_time.isoformat()
                            )

                            trade["result_price"] = sl

                            trade["result_r"] = -1.0

                            trade["result_reason"] = (
                                "SL"
                            )

                            closed_count += 1

                            print(

                                f"LOSS "
                                f"{trade['name']} SELL "
                                f"SL"

                            )

                            break

                        if hit_tp:

                            trade["status"] = "WIN"

                            trade["closed_at"] = (
                                candle_time.isoformat()
                            )

                            trade["result_price"] = tp

                            trade["result_r"] = rr

                            trade["result_reason"] = (
                                "TP"
                            )

                            closed_count += 1

                            print(

                                f"WIN "
                                f"{trade['name']} SELL "
                                f"TP +{rr:.2f}R"

                            )

                            break

            except Exception as e:

                print(

                    "Trade evaluation error:",
                    trade.get("name"),
                    e

                )

    return closed_count


# ============================================================
# CUMULATIVE STATISTICS
# ============================================================

def calculate_statistics(
    state
):

    trades = list(
        state["trades"].values()
    )

    total = len(trades)

    open_count = sum(

        1

        for t in trades

        if t.get("status")
        ==
        "OPEN"

    )

    closed = [

        t

        for t in trades

        if t.get("status")
        in
        (
            "WIN",
            "LOSS"
        )

    ]

    wins = [

        t

        for t in closed

        if t.get("status")
        ==
        "WIN"

    ]

    losses = [

        t

        for t in closed

        if t.get("status")
        ==
        "LOSS"

    ]

    closed_count = len(closed)

    win_count = len(wins)

    loss_count = len(losses)

    if closed_count > 0:

        win_rate = (
            win_count
            /
            closed_count
            *
            100
        )

    else:

        win_rate = 0.0

    gross_profit_r = sum(

        float(
            t.get(
                "result_r",
                0
            )
            or 0
        )

        for t in wins

    )

    gross_loss_r = abs(

        sum(

            float(
                t.get(
                    "result_r",
                    0
                )
                or 0
            )

            for t in losses

        )

    )

    net_r = (
        gross_profit_r
        -
        gross_loss_r
    )

    if gross_loss_r > 0:

        profit_factor = (
            gross_profit_r
            /
            gross_loss_r
        )

    else:

        profit_factor = (
            float("inf")
            if gross_profit_r > 0
            else 0.0
        )

    if closed_count > 0:

        expectancy = (
            net_r
            /
            closed_count
        )

    else:

        expectancy = 0.0

    buy_trades = [

        t

        for t in closed

        if t.get("direction")
        ==
        "BUY"

    ]

    sell_trades = [

        t

        for t in closed

        if t.get("direction")
        ==
        "SELL"

    ]

    buy_wins = sum(

        1

        for t in buy_trades

        if t.get("status")
        ==
        "WIN"

    )

    buy_losses = sum(

        1

        for t in buy_trades

        if t.get("status")
        ==
        "LOSS"

    )

    sell_wins = sum(

        1

        for t in sell_trades

        if t.get("status")
        ==
        "WIN"

    )

    sell_losses = sum(

        1

        for t in sell_trades

        if t.get("status")
        ==
        "LOSS"

    )

    buy_closed = len(
        buy_trades
    )

    sell_closed = len(
        sell_trades
    )

    buy_rate = (

        buy_wins
        /
        buy_closed
        *
        100

        if buy_closed > 0
        else 0.0

    )

    sell_rate = (

        sell_wins
        /
        sell_closed
        *
        100

        if sell_closed > 0
        else 0.0

    )

    return {

        "total":
            total,

        "open":
            open_count,

        "closed":
            closed_count,

        "wins":
            win_count,

        "losses":
            loss_count,

        "win_rate":
            win_rate,

        "gross_profit_r":
            gross_profit_r,

        "gross_loss_r":
            gross_loss_r,

        "net_r":
            net_r,

        "profit_factor":
            profit_factor,

        "expectancy":
            expectancy,

        "buy_wins":
            buy_wins,

        "buy_losses":
            buy_losses,

        "buy_rate":
            buy_rate,

        "sell_wins":
            sell_wins,

        "sell_losses":
            sell_losses,

        "sell_rate":
            sell_rate,

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
# KRAKEN DATA
# ============================================================

def get_candles(
    symbol,
    limit=CANDLE_LIMIT
):

    url = (
        f"{BASE_URL}/trade/"
        f"{symbol}/5m"
    )

    try:

        response = requests.get(

            url,

            params={
                "count": limit
            },

            headers={
                "Accept":
                "application/json"
            },

            timeout=20

        )

        if response.status_code != 200:

            return None, (
                f"HTTP "
                f"{response.status_code}"
            )

        data = response.json()

        candles = data.get(
            "candles"
        )

        if not candles:

            return None, (
                "No candles returned"
            )

        rows = []

        for c in candles:

            if isinstance(c, dict):

                rows.append({

                    "time":
                        c.get("time"),

                    "open":
                        c.get("open"),

                    "high":
                        c.get("high"),

                    "low":
                        c.get("low"),

                    "close":
                        c.get("close"),

                    "volume":
                        c.get("volume")

                })

            elif isinstance(c, list):

                if len(c) >= 6:

                    rows.append({

                        "time":
                            c[0],

                        "open":
                            c[1],

                        "high":
                            c[2],

                        "low":
                            c[3],

                        "close":
                            c[4],

                        "volume":
                            c[5]

                    })

        if not rows:

            return None, (
                "Invalid candle format"
            )

        df = pd.DataFrame(
            rows
        )

        required = [

            "time",
            "open",
            "high",
            "low",
            "close",
            "volume"

        ]

        for col in required:

            if col not in df.columns:

                return None, (
                    f"Missing column: "
                    f"{col}"
                )

        numeric_columns = [

            "open",
            "high",
            "low",
            "close",
            "volume"

        ]

        for col in numeric_columns:

            df[col] = pd.to_numeric(

                df[col],

                errors="coerce"

            )

        df["time"] = pd.to_datetime(

            df["time"],

            unit="ms",

            utc=True,

            errors="coerce"

        )

        df = df.dropna()

        df = df.sort_values(
            "time"
        )

        df = df.drop_duplicates(

            subset=["time"]

        )

        now = pd.Timestamp.now(
            tz="UTC"
        )

        current_bucket = (
            now.floor("5min")
        )

        df = df[
            df["time"]
            <
            current_bucket
        ]

        if len(df) < 100:

            return None, (

                f"Only "
                f"{len(df)} "
                f"closed candles"

            )

        return (

            df.reset_index(
                drop=True
            ),

            None

        )

    except Exception as e:

        return None, str(e)


# ============================================================
# PIVOT LOWS
# ============================================================

def pivot_lows(df):

    lows = (
        df["low"]
        .values
    )

    pivots = []

    for i in range(

        PIVOT_LEFT,

        len(df) - PIVOT_RIGHT

    ):

        left = lows[

            i - PIVOT_LEFT:i

        ]

        right = lows[

            i + 1:
            i + 1 + PIVOT_RIGHT

        ]

        if (

            lows[i] <= left.min()

            and

            lows[i] <= right.min()

        ):

            pivots.append(i)

    return pivots


# ============================================================
# PIVOT HIGHS
# ============================================================

def pivot_highs(df):

    highs = (
        df["high"]
        .values
    )

    pivots = []

    for i in range(

        PIVOT_LEFT,

        len(df) - PIVOT_RIGHT

    ):

        left = highs[

            i - PIVOT_LEFT:i

        ]

        right = highs[

            i + 1:
            i + 1 + PIVOT_RIGHT

        ]

        if (

            highs[i] >= left.max()

            and

            highs[i] >= right.max()

        ):

            pivots.append(i)

    return pivots


# ============================================================
# BULLISH DIVERGENCE
# ============================================================

def find_bullish_divergence(df):

    pivots = pivot_lows(df)

    if len(pivots) < 2:

        return None

    rsi = df["rsi"]

    for j in range(

        len(pivots) - 1,

        0,

        -1

    ):

        p1 = pivots[j - 1]

        p2 = pivots[j]

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
            rsi.iloc[p1]
        )

        rsi2 = float(
            rsi.iloc[p2]
        )

        if price1 <= 0:

            continue

        price_change = (

            (
                price2
                -
                price1
            )

            /

            price1

            *

            100

        )

        if (

            price_change
            <=
            -MIN_PRICE_DIFF_PERCENT

            and

            rsi2 - rsi1
            >=
            MIN_RSI_DIFF

        ):

            return {

                "type":
                    "BULLISH",

                "p1":
                    p1,

                "p2":
                    p2,

                "price1":
                    price1,

                "price2":
                    price2,

                "rsi1":
                    rsi1,

                "rsi2":
                    rsi2

            }

    return None


# ============================================================
# BEARISH DIVERGENCE
# ============================================================

def find_bearish_divergence(df):

    pivots = pivot_highs(df)

    if len(pivots) < 2:

        return None

    rsi = df["rsi"]

    for j in range(

        len(pivots) - 1,

        0,

        -1

    ):

        p1 = pivots[j - 1]

        p2 = pivots[j]

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
            rsi.iloc[p1]
        )

        rsi2 = float(
            rsi.iloc[p2]
        )

        if price1 <= 0:

            continue

        price_change = (

            (
                price2
                -
                price1
            )

            /

            price1

            *

            100

        )

        if (

            price_change
            >=
            MIN_PRICE_DIFF_PERCENT

            and

            rsi1 - rsi2
            >=
            MIN_RSI_DIFF

        ):

            return {

                "type":
                    "BEARISH",

                "p1":
                    p1,

                "p2":
                    p2,

                "price1":
                    price1,

                "price2":
                    price2,

                "rsi1":
                    rsi1,

                "rsi2":
                    rsi2

            }

    return None


# ============================================================
# DESCENDING TRENDLINE BREAK
# ============================================================

def descending_trendline_break(df):

    pivots = pivot_highs(df)

    if len(pivots) < 2:

        return None

    closes = (
        df["close"]
        .values
    )

    last_index = (
        len(df) - 1
    )

    prev_i = (
        last_index - 1
    )

    curr_i = last_index

    for j in range(

        len(pivots) - 1,

        0,

        -1

    ):

        p1 = pivots[j - 1]

        p2 = pivots[j]

        if p2 <= p1:

            continue

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:

            continue

        h1 = float(
            df.iloc[p1]["high"]
        )

        h2 = float(
            df.iloc[p2]["high"]
        )

        if h2 >= h1:

            continue

        slope = (

            h2 - h1

        ) / (

            p2 - p1

        )

        line_prev = (

            h2
            +
            slope
            *
            (
                prev_i - p2
            )

        )

        line_curr = (

            h2
            +
            slope
            *
            (
                curr_i - p2
            )

        )

        if (

            closes[prev_i]
            <=
            line_prev

            and

            closes[curr_i]
            >
            line_curr

        ):

            return {

                "p1":
                    p1,

                "p2":
                    p2,

                "line":
                    line_curr

            }

    return None


# ============================================================
# ASCENDING TRENDLINE BREAK
# ============================================================

def ascending_trendline_break(df):

    pivots = pivot_lows(df)

    if len(pivots) < 2:

        return None

    closes = (
        df["close"]
        .values
    )

    last_index = (
        len(df) - 1
    )

    prev_i = (
        last_index - 1
    )

    curr_i = last_index

    for j in range(

        len(pivots) - 1,

        0,

        -1

    ):

        p1 = pivots[j - 1]

        p2 = pivots[j]

        if p2 <= p1:

            continue

        gap = p2 - p1

        if gap > MAX_PIVOT_GAP:

            continue

        l1 = float(
            df.iloc[p1]["low"]
        )

        l2 = float(
            df.iloc[p2]["low"]
        )

        if l2 <= l1:

            continue

        slope = (

            l2 - l1

        ) / (

            p2 - p1

        )

        line_prev = (

            l2
            +
            slope
            *
            (
                prev_i - p2
            )

        )

        line_curr = (

            l2
            +
            slope
            *
            (
                curr_i - p2
            )

        )

        if (

            closes[prev_i]
            >=
            line_prev

            and

            closes[curr_i]
            <
            line_curr

        ):

            return {

                "p1":
                    p1,

                "p2":
                    p2,

                "line":
                    line_curr

            }

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

    previous_close = (
        close.shift(1)
    )

    tr1 = (
        high - low
    )

    tr2 = (
        (
            high
            -
            previous_close
        )
        .abs()
    )

    tr3 = (
        (
            low
            -
            previous_close
        )
        .abs()
    )

    true_range = pd.concat(

        [
            tr1,
            tr2,
            tr3
        ],

        axis=1

    ).max(axis=1)

    atr = true_range.ewm(

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

    work = df.copy()

    src = (
        work["close"]
        .astype(float)
        .values
    )

    atr = calculate_atr(

        work,

        atr_period

    )

    atr_values = (
        atr.astype(float)
        .values
    )

    n = len(work)

    trailing_stop = np.zeros(
        n,
        dtype=float
    )

    pos = np.zeros(
        n,
        dtype=int
    )

    for i in range(n):

        current_src = src[i]

        current_atr = atr_values[i]

        n_loss = (
            key_value
            *
            current_atr
        )

        if i == 0:

            previous_stop = 0.0

            previous_src = (
                current_src
            )

            previous_pos = 0

        else:

            previous_stop = (
                trailing_stop[i - 1]
            )

            previous_src = (
                src[i - 1]
            )

            previous_pos = (
                pos[i - 1]
            )

        if (

            current_src
            >
            previous_stop

            and

            previous_src
            >
            previous_stop

        ):

            trailing_stop[i] = max(

                previous_stop,

                current_src
                -
                n_loss

            )

        elif (

            current_src
            <
            previous_stop

            and

            previous_src
            <
            previous_stop

        ):

            trailing_stop[i] = min(

                previous_stop,

                current_src
                +
                n_loss

            )

        else:

            if current_src > previous_stop:

                trailing_stop[i] = (

                    current_src
                    -
                    n_loss

                )

            else:

                trailing_stop[i] = (

                    current_src
                    +
                    n_loss

                )

        if i == 0:

            pos[i] = 0

        else:

            if (

                src[i - 1]
                <
                trailing_stop[i - 1]

                and

                src[i]
                >
                trailing_stop[i - 1]

            ):

                pos[i] = 1

            elif (

                src[i - 1]
                >
                trailing_stop[i - 1]

                and

                src[i]
                <
                trailing_stop[i - 1]

            ):

                pos[i] = -1

            else:

                pos[i] = previous_pos

    work["ut_atr"] = atr

    work["ut_trailing_stop"] = (
        trailing_stop
    )

    work["ut_pos"] = pos

    direction = []

    for i in range(n):

        if src[i] > trailing_stop[i]:

            direction.append(
                "BUY"
            )

        elif src[i] < trailing_stop[i]:

            direction.append(
                "SELL"
            )

        else:

            direction.append(
                "NEUTRAL"
            )

    work["ut_direction"] = direction

    buy_signal = np.zeros(
        n,
        dtype=bool
    )

    sell_signal = np.zeros(
        n,
        dtype=bool
    )

    for i in range(1, n):

        ema_current = src[i]

        ema_previous = src[i - 1]

        stop_current = (
            trailing_stop[i]
        )

        stop_previous = (
            trailing_stop[i - 1]
        )

        if (

            ema_current
            >
            stop_current

            and

            ema_previous
            <=
            stop_previous

        ):

            buy_signal[i] = True

        if (

            ema_current
            <
            stop_current

            and

            stop_previous
            <=
            ema_previous

        ):

            sell_signal[i] = True

    work["ut_buy_signal"] = (
        buy_signal
    )

    work["ut_sell_signal"] = (
        sell_signal
    )

    return work


# ============================================================
# SUPPORT / RESISTANCE
# ============================================================

def nearest_resistance(
    df,
    entry
):

    pivots = pivot_highs(df)

    candidates = []

    for p in pivots:

        price = float(
            df.iloc[p]["high"]
        )

        if price <= entry:

            continue

        distance = (

            (
                price
                -
                entry
            )
            /
            entry
            *
            100

        )

        if (

            distance
            >=
            MIN_TP_DISTANCE_PERCENT

        ):

            candidates.append(
                price
            )

    if not candidates:

        return None

    return min(
        candidates
    )


def nearest_support(
    df,
    entry
):

    pivots = pivot_lows(df)

    candidates = []

    for p in pivots:

        price = float(
            df.iloc[p]["low"]
        )

        if price >= entry:

            continue

        distance = (

            (
                entry
                -
                price
            )
            /
            entry
            *
            100

        )

        if (

            distance
            >=
            MIN_TP_DISTANCE_PERCENT

        ):

            candidates.append(
                price
            )

    if not candidates:

        return None

    return max(
        candidates
    )


# ============================================================
# ANALYZE COIN
# ============================================================

def analyze_coin(
    name,
    symbol
):

    df, error = get_candles(
        symbol
    )

    if df is None:

        return {

            "status":
                "DATA_ERROR",

            "name":
                name,

            "symbol":
                symbol,

            "error":
                error

        }

    try:

        df["rsi"] = calculate_rsi(

            df["close"],

            RSI_PERIOD

        )

        df = calculate_ut_bot(

            df,

            key_value=UT_KEY_VALUE,

            atr_period=UT_ATR_PERIOD

        )

        bullish = (
            find_bullish_divergence(
                df
            )
        )

        bearish = (
            find_bearish_divergence(
                df
            )
        )

        down_break = (
            descending_trendline_break(
                df
            )
        )

        up_break = (
            ascending_trendline_break(
                df
            )
        )

        last_index = (
            len(df) - 1
        )

        last = df.iloc[
            last_index
        ]

        entry = float(
            last["close"]
        )

        signal_time = (
            last["time"]
        )

        ut_direction = (
            last["ut_direction"]
        )

        ut_stop = float(
            last["ut_trailing_stop"]
        )

        ut_pos = int(
            last["ut_pos"]
        )

        ut_buy_event = bool(
            last["ut_buy_signal"]
        )

        ut_sell_event = bool(
            last["ut_sell_signal"]
        )

        candidates = []

        # ====================================================
        # BUY
        # ====================================================

        if (

            bullish

            and

            down_break

        ):

            div_index = (
                bullish["p2"]
            )

            age_bars = (

                last_index
                -
                div_index

            )

            age_minutes = (
                age_bars * 5
            )

            if (

                0
                <=
                age_minutes
                <=
                MAX_DIVERGENCE_AGE_MINUTES

            ):

                if ut_direction == "BUY":

                    swing_low = float(

                        df.iloc[
                            bullish["p2"]
                        ]["low"]

                    )

                    sl = (

                        swing_low
                        *
                        (
                            1
                            -
                            SL_BUFFER_PERCENT
                            /
                            100
                        )

                    )

                    tp = nearest_resistance(

                        df,

                        entry

                    )

                    if tp is not None:

                        risk = (
                            entry
                            -
                            sl
                        )

                        reward = (
                            tp
                            -
                            entry
                        )

                        if (

                            risk > 0
                            and
                            reward > 0

                        ):

                            sl_percent = (

                                (
                                    entry
                                    -
                                    sl
                                )
                                /
                                entry
                                *
                                100

                            )

                            tp_percent = (

                                (
                                    tp
                                    -
                                    entry
                                )
                                /
                                entry
                                *
                                100

                            )

                            rr = (
                                reward
                                /
                                risk
                            )

                            candidates.append({

                                "direction":
                                    "BUY",

                                "name":
                                    name,

                                "symbol":
                                    symbol,

                                "entry":
                                    entry,

                                "sl":
                                    float(sl),

                                "tp":
                                    float(tp),

                                "sl_percent":
                                    float(
                                        sl_percent
                                    ),

                                "tp_percent":
                                    float(
                                        tp_percent
                                    ),

                                "rr":
                                    float(rr),

                                "signal_time":
                                    signal_time.isoformat(),

                                "swing_time":
                                    df.iloc[
                                        bullish["p2"]
                                    ]["time"].isoformat(),

                                "divergence":
                                    "REGULAR BULLISH",

                                "trendline":
                                    "DESCENDING BREAKOUT",

                                "rsi":
                                    float(
                                        last["rsi"]
                                    ),

                                "div_age_minutes":
                                    age_minutes,

                                "ut_direction":
                                    "BUY",

                                "ut_pos":
                                    ut_pos,

                                "ut_trailing_stop":
                                    ut_stop,

                                "ut_event":
                                    ut_buy_event,

                                "div_p1":
                                    bullish["p1"],

                                "div_p2":
                                    bullish["p2"]

                            })

        # ====================================================
        # SELL
        # ====================================================

        if (

            bearish

            and

            up_break

        ):

            div_index = (
                bearish["p2"]
            )

            age_bars = (

                last_index
                -
                div_index

            )

            age_minutes = (
                age_bars * 5
            )

            if (

                0
                <=
                age_minutes
                <=
                MAX_DIVERGENCE_AGE_MINUTES

            ):

                if ut_direction == "SELL":

                    swing_high = float(

                        df.iloc[
                            bearish["p2"]
                        ]["high"]

                    )

                    sl = (

                        swing_high
                        *
                        (
                            1
                            +
                            SL_BUFFER_PERCENT
                            /
                            100
                        )

                    )

                    tp = nearest_support(

                        df,

                        entry

                    )

                    if tp is not None:

                        risk = (
                            sl
                            -
                            entry
                        )

                        reward = (
                            entry
                            -
                            tp
                        )

                        if (

                            risk > 0
                            and
                            reward > 0

                        ):

                            sl_percent = (

                                (
                                    sl
                                    -
                                    entry
                                )
                                /
                                entry
                                *
                                100

                            )

                            tp_percent = (

                                (
                                    entry
                                    -
                                    tp
                                )
                                /
                                entry
                                *
                                100

                            )

                            rr = (
                                reward
                                /
                                risk
                            )

                            candidates.append({

                                "direction":
                                    "SELL",

                                "name":
                                    name,

                                "symbol":
                                    symbol,

                                "entry":
                                    entry,

                                "sl":
                                    float(sl),

                                "tp":
                                    float(tp),

                                "sl_percent":
                                    float(
                                        sl_percent
                                    ),

                                "tp_percent":
                                    float(
                                        tp_percent
                                    ),

                                "rr":
                                    float(rr),

                                "signal_time":
                                    signal_time.isoformat(),

                                "swing_time":
                                    df.iloc[
                                        bearish["p2"]
                                    ]["time"].isoformat(),

                                "divergence":
                                    "REGULAR BEARISH",

                                "trendline":
                                    "ASCENDING BREAKDOWN",

                                "rsi":
                                    float(
                                        last["rsi"]
                                    ),

                                "div_age_minutes":
                                    age_minutes,

                                "ut_direction":
                                    "SELL",

                                "ut_pos":
                                    ut_pos,

                                "ut_trailing_stop":
                                    ut_stop,

                                "ut_event":
                                    ut_sell_event,

                                "div_p1":
                                    bearish["p1"],

                                "div_p2":
                                    bearish["p2"]

                            })

        if not candidates:

            return {

                "status":
                    "OK",

                "name":
                    name,

                "symbol":
                    symbol,

                "signal":
                    None,

                "price":
                    entry,

                "rsi":
                    float(
                        last["rsi"]
                    ),

                "ut_direction":
                    ut_direction,

                "ut_pos":
                    ut_pos

            }

        signal = candidates[0]

        return {

            "status":
                "OK",

            "name":
                name,

            "symbol":
                symbol,

            "signal":
                signal,

            "price":
                entry,

            "rsi":
                float(
                    last["rsi"]
                ),

            "ut_direction":
                ut_direction,

            "ut_pos":
                ut_pos

        }

    except Exception as e:

        return {

            "status":
                "ANALYSIS_ERROR",

            "name":
                name,

            "symbol":
                symbol,

            "error":
                str(e)

        }


# ============================================================
# FORMAT PRICE
# ============================================================

def format_price(
    value
):

    try:

        value = float(value)

        if value >= 1000:

            return f"{value:.2f}"

        if value >= 1:

            return f"{value:.5f}"

        if value >= 0.01:

            return f"{value:.6f}"

        return f"{value:.8g}"

    except Exception:

        return str(value)


# ============================================================
# FORMAT SIGNAL
# ============================================================

def format_signal(
    signal
):

    direction = (
        signal["direction"]
    )

    emoji = (

        "🟢"
        if direction == "BUY"
        else
        "🔴"

    )

    return (

        f"{emoji} "
        f"<b>{signal['name']}/USD "
        f"{direction}</b>\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💵 Entry: "
        f"<code>"
        f"{format_price(signal['entry'])}"
        f"</code>\n"

        f"🛑 SL: "
        f"<code>"
        f"{format_price(signal['sl'])}"
        f"</code> "
        f"(<b>"
        f"-{signal['sl_percent']:.2f}%"
        f"</b>)\n"

        f"🎯 TP: "
        f"<code>"
        f"{format_price(signal['tp'])}"
        f"</code> "
        f"(<b>"
        f"+{signal['tp_percent']:.2f}%"
        f"</b>)\n"

        f"⚖️ RR: "
        f"<b>1:{signal['rr']:.2f}</b>\n"

        f"📊 RSI: "
        f"{signal['rsi']:.2f}\n"

        f"📌 "
        f"{signal['divergence']}\n"

        f"📐 "
        f"{signal['trendline']}\n"

        f"🤖 UT Bot: "
        f"<b>{signal['ut_direction']}</b>\n"

        f"📍 UT Stop: "
        f"<code>"
        f"{format_price(signal['ut_trailing_stop'])}"
        f"</code>\n"

        f"⏱ Divergence age: "
        f"{signal['div_age_minutes']}m"

    )


# ============================================================
# FORMAT REPORT
# ============================================================

def format_report(
    results,
    new_signals,
    stats
):

    data_ok = sum(

        1

        for r in results

        if r.get("status")
        ==
        "OK"

    )

    data_errors = [

        r

        for r in results

        if r.get("status")
        ==
        "DATA_ERROR"

    ]

    analysis_errors = [

        r

        for r in results

        if r.get("status")
        ==
        "ANALYSIS_ERROR"

    ]

    lines = []

    # ========================================================
    # HEADER
    # ========================================================

    lines.append(

        "📡 "
        "<b>CRYPTO DIVERGENCE "
        "SCANNER v10.0</b>"

    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    lines.append(

        f"🕐 "
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}"
        f" UTC"

    )

    lines.append(

        "⏱ Timeframe: "
        "<b>5M CLOSED</b>"

    )

    lines.append(

        "🤖 UT Bot: "
        "<b>3 / 10</b>"

    )

    lines.append(

        f"📊 DATA OK: "
        f"{data_ok}/{len(COINS)}"

    )

    lines.append(

        f"⚠️ DATA ERROR: "
        f"{len(data_errors)}"

    )

    lines.append(

        f"⚠️ ANALYSIS ERROR: "
        f"{len(analysis_errors)}"

    )

    lines.append("")

    # ========================================================
    # CUMULATIVE PERFORMANCE
    # ========================================================

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

        f"⏳ Open: "
        f"<b>{stats['open']}</b>"

    )

    lines.append(

        f"📁 Closed: "
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

    net_r = stats["net_r"]

    net_emoji = (
        "🟢"
        if net_r >= 0
        else
        "🔴"
    )

    lines.append(

        f"{net_emoji} Net R: "
        f"<b>{net_r:+.2f}R</b>"

    )

    lines.append(

        f"📐 Expectancy: "
        f"<b>{stats['expectancy']:+.3f}R</b>"

    )

    if stats["profit_factor"] == float("inf"):

        pf_text = "∞"

    else:

        pf_text = (
            f"{stats['profit_factor']:.2f}"
        )

    lines.append(

        f"💰 Profit Factor: "
        f"<b>{pf_text}</b>"

    )

    lines.append("")

    lines.append(

        f"🟢 BUY: "
        f"{stats['buy_wins']}W / "
        f"{stats['buy_losses']}L "
        f"({stats['buy_rate']:.2f}%)"

    )

    lines.append(

        f"🔴 SELL: "
        f"{stats['sell_wins']}W / "
        f"{stats['sell_losses']}L "
        f"({stats['sell_rate']:.2f}%)"

    )

    lines.append("")

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    if new_signals:

        lines.append(
            "🚨 <b>NEW CONFIRMED SIGNALS</b>"
        )

        lines.append(
            "━━━━━━━━━━━━━━━━━━"
        )

        for signal in new_signals[
            :TOP_SIGNAL_LIMIT
        ]:

            lines.append(
                format_signal(
                    signal
                )
            )

            lines.append("")

    else:

        lines.append(
            "👀 <b>NO NEW SIGNAL</b>"
        )

        lines.append("")

        lines.append(

            "Conditions required:"

        )

        lines.append(

            "🟢 BUY = "
            "Bullish Divergence + "
            "Descending Breakout + "
            "UT Bot BUY"

        )

        lines.append(

            "🔴 SELL = "
            "Bearish Divergence + "
            "Ascending Breakdown + "
            "UT Bot SELL"

        )

        lines.append("")

        lines.append(

            "❌ If UT Bot direction "
            "does not match the setup, "
            "the signal is rejected."

        )

    # ========================================================
    # DATA ERRORS
    # ========================================================

    if data_errors:

        lines.append("")

        lines.append(
            "⚠️ <b>DATA PROBLEMS</b>"
        )

        for r in data_errors[:10]:

            lines.append(

                f"• {r['name']}: "
                f"{r.get('error', 'Unknown')}"

            )

    # ========================================================
    # ANALYSIS ERRORS
    # ========================================================

    if analysis_errors:

        lines.append("")

        lines.append(
            "⚠️ <b>ANALYSIS PROBLEMS</b>"
        )

        for r in analysis_errors[:10]:

            lines.append(

                f"• {r['name']}: "
                f"{r.get('error', 'Unknown')}"

            )

    return "\n".join(
        lines
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 60
    )

    print(
        "CRYPTO DIVERGENCE SCANNER v10.0"
    )

    print(
        "Kraken Futures | 5M CLOSED"
    )

    print(
        "UT Bot 3,10 FILTER"
    )

    print(
        "=" * 60
    )

    print(
        "Coins:",
        len(COINS)
    )

    print(
        "UT Key Value:",
        UT_KEY_VALUE
    )

    print(
        "UT ATR Period:",
        UT_ATR_PERIOD
    )

    print(
        "Minimum TP distance:",
        f"{MIN_TP_DISTANCE_PERCENT}%"
    )

    print("")

    # ========================================================
    # LOAD HISTORY
    # ========================================================

    state = load_state()

    print(
        "Historical trades:",
        len(state["trades"])
    )

    # ========================================================
    # EVALUATE OLD OPEN TRADES
    # ========================================================

    closed_before_scan = (
        evaluate_open_trades(
            state
        )
    )

    if closed_before_scan:

        print(

            "Trades closed:",
            closed_before_scan

        )

    save_state(
        state
    )

    # ========================================================
    # ANALYZE ALL COINS
    # ========================================================

    results = []

    with ThreadPoolExecutor(

        max_workers=MAX_WORKERS

    ) as executor:

        futures = {

            executor.submit(

                analyze_coin,

                name,

                symbol

            ):
                name

            for name, symbol
            in COINS.items()

        }

        for future in as_completed(
            futures
        ):

            name = futures[
                future
            ]

            try:

                result = (
                    future.result()
                )

                results.append(
                    result
                )

            except Exception as e:

                results.append({

                    "status":
                        "ANALYSIS_ERROR",

                    "name":
                        name,

                    "symbol":
                        COINS[name],

                    "error":
                        str(e)

                })

    # ========================================================
    # DETERMINISTIC ORDER
    # ========================================================

    results.sort(

        key=lambda x:
        x["name"]

    )

    # ========================================================
    # REGISTER NEW SIGNALS
    # ========================================================

    new_signals = []

    for result in results:

        signal = result.get(
            "signal"
        )

        if not signal:

            continue

        registered = register_signal(

            state,

            signal

        )

        if registered:

            new_signals.append(
                signal
            )

            print(

                "NEW SIGNAL:",
                signal["name"],
                signal["direction"]

            )

        else:

            print(

                "DUPLICATE / BLOCKED:",
                signal["name"],
                signal["direction"]

            )

    # ========================================================
    # SORT NEW SIGNALS
    # ========================================================

    new_signals.sort(

        key=lambda x:
        x["rr"],

        reverse=True

    )

    # ========================================================
    # SAVE NEW SIGNALS
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # EVALUATE AGAIN
    #
    # Normally newly registered signals remain OPEN because
    # there are no candles after the signal candle yet.
    #
    # This second evaluation also protects against edge cases.
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
    # CONSOLE REPORT
    # ========================================================

    print("")

    print(
        "TOTAL COINS:",
        len(COINS)
    )

    print(
        "DATA OK:",
        sum(

            1

            for r in results

            if r.get("status")
            ==
            "OK"

        )

    )

    print(
        "DATA ERRORS:",
        sum(

            1

            for r in results

            if r.get("status")
            ==
            "DATA_ERROR"

        )

    )

    print(
        "ANALYSIS ERRORS:",
        sum(

            1

            for r in results

            if r.get("status")
            ==
            "ANALYSIS_ERROR"

        )

    )

    print(
        "NEW SIGNALS:",
        len(new_signals)
    )

    print("")

    print(
        "CUMULATIVE PERFORMANCE"
    )

    print(
        "----------------------"
    )

    print(
        "Total trades:",
        stats["total"]
    )

    print(
        "Open:",
        stats["open"]
    )

    print(
        "Closed:",
        stats["closed"]
    )

    print(
        "Wins:",
        stats["wins"]
    )

    print(
        "Losses:",
        stats["losses"]
    )

    print(
        "Win rate:",
        f"{stats['win_rate']:.2f}%"
    )

    print(
        "Net R:",
        f"{stats['net_r']:+.2f}R"
    )

    print(
        "Expectancy:",
        f"{stats['expectancy']:+.3f}R"
    )

    if stats["profit_factor"] == float("inf"):

        print(
            "Profit Factor: ∞"
        )

    else:

        print(

            "Profit Factor:",
            f"{stats['profit_factor']:.2f}"

        )

    print(

        "BUY:",
        f"{stats['buy_wins']}W / "
        f"{stats['buy_losses']}L "
        f"({stats['buy_rate']:.2f}%)"

    )

    print(

        "SELL:",
        f"{stats['sell_wins']}W / "
        f"{stats['sell_losses']}L "
        f"({stats['sell_rate']:.2f}%)"

    )

    # ========================================================
    # PRINT NEW SIGNALS
    # ========================================================

    if new_signals:

        print("")

        print(
            "NEW CONFIRMED SIGNALS"
        )

        print(
            "----------------------"
        )

        for signal in new_signals:

            print("")

            print(
                signal["name"],
                signal["direction"]
            )

            print(
                "Entry:",
                format_price(
                    signal["entry"]
                )
            )

            print(
                "SL:",
                format_price(
                    signal["sl"]
                ),
                f"(-{signal['sl_percent']:.2f}%)"
            )

            print(
                "TP:",
                format_price(
                    signal["tp"]
                ),
                f"(+{signal['tp_percent']:.2f}%)"
            )

            print(
                "RR:",
                f"1:{signal['rr']:.2f}"
            )

            print(
                "RSI:",
                f"{signal['rsi']:.2f}"
            )

            print(
                "Divergence:",
                signal["divergence"]
            )

            print(
                "Trendline:",
                signal["trendline"]
            )

            print(
                "UT Bot:",
                signal["ut_direction"]
            )

    # ========================================================
    # TELEGRAM
    # ========================================================

    report = format_report(

        results,

        new_signals,

        stats

    )

    send_telegram(
        report
    )

    print("")

    print(
        "SCAN COMPLETE"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
