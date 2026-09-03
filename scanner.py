# ============================================================
# CRYPTO DIVERGENCE SCANNER v9.0
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
# FEATURES
#   - 30 coins
#   - 5M timeframe
#   - Closed candles only
#   - Regular RSI divergence
#   - Trendline breakout / breakdown
#   - UT Bot 3,10 directional filter
#   - Swing-based SL
#   - Nearest S/R TP
#   - Minimum TP distance: 0.30%
#   - SL percentage
#   - TP percentage
#   - RR
#   - Telegram
#
# IMPORTANT
#   No cumulative statistics
#   No persistent trade history
#   No state file
# ============================================================


import os
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

# Exact values requested:
#
# Key Value = 3
# ATR Period = 10

UT_KEY_VALUE = 3.0

UT_ATR_PERIOD = 10

# Original Pine:
#
# h = input(false)
#
# Therefore:
# False = normal candle close
# True  = Heikin Ashi close
#
# We keep it False to match the source defaults.

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

            # ------------------------------------------------
            # Kraken dictionary format
            # ------------------------------------------------

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

            # ------------------------------------------------
            # Kraken list format
            # ------------------------------------------------

            elif isinstance(c, list):

                if len(c) >= 6:

                    rows.append({

                        "time": c[0],

                        "open": c[1],

                        "high": c[2],

                        "low": c[3],

                        "close": c[4],

                        "volume": c[5]

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

        # ----------------------------------------------------
        # Numeric conversion
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Time conversion
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Remove currently forming candle
        # ----------------------------------------------------

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
# REGULAR BULLISH DIVERGENCE
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

        # ----------------------------------------------------
        # Regular bullish divergence
        #
        # Price = Lower Low
        # RSI   = Higher Low
        # ----------------------------------------------------

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
# REGULAR BEARISH DIVERGENCE
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

        # ----------------------------------------------------
        # Regular bearish divergence
        #
        # Price = Higher High
        # RSI   = Lower High
        # ----------------------------------------------------

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
# DESCENDING TRENDLINE BREAKOUT
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

        # ----------------------------------------------------
        # Descending highs
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Breakout:
        #
        # Previous close below/equal line
        # Current close above line
        # ----------------------------------------------------

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
# ASCENDING TRENDLINE BREAKDOWN
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

        # ----------------------------------------------------
        # Ascending lows
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # Breakdown:
        #
        # Previous close above/equal line
        # Current close below line
        # ----------------------------------------------------

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
        (high - previous_close)
        .abs()
    )

    tr3 = (
        (low - previous_close)
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

    # TradingView Pine atr()
    # uses Wilder/RMA smoothing.
    #
    # RMA equivalent:
    # ewm(alpha=1/period)

    atr = true_range.ewm(

        alpha=1 / period,

        adjust=False

    ).mean()

    return atr


# ============================================================
# UT BOT
# EXACT LOGIC FROM PROVIDED PINE SCRIPT
#
# Pine:
#
# a = 3
# c = 10
# h = false
#
# xATR  = atr(c)
# nLoss = a * xATR
#
# src = close
#
# xATRTrailingStop := iff(
#     src > nz(stop[1],0)
#     and
#     src[1] > nz(stop[1],0),
#     max(stop[1], src - nLoss),
#
#     iff(
#         src < nz(stop[1],0)
#         and
#         src[1] < nz(stop[1],0),
#         min(stop[1], src + nLoss),
#
#         iff(
#             src > nz(stop[1],0),
#             src - nLoss,
#             src + nLoss
#         )
#     )
# )
#
# pos := iff(
#     src[1] < stop[1]
#     and
#     src > stop[1],
#     1,
#
#     iff(
#         src[1] > stop[1]
#         and
#         src < stop[1],
#         -1,
#         pos[1]
#     )
# )
#
# buy = src > stop and crossover(ema(src,1), stop)
#
# sell = src < stop and crossover(stop, ema(src,1))
#
# For filtering direction:
#
# BUY  = src > trailing stop
# SELL = src < trailing stop
#
# ============================================================

def calculate_ut_bot(
    df,
    key_value=3.0,
    atr_period=10
):

    work = df.copy()

    # --------------------------------------------------------
    # Source
    #
    # h=False in original script.
    # Therefore source = normal close.
    # --------------------------------------------------------

    src = (
        work["close"]
        .astype(float)
        .values
    )

    # --------------------------------------------------------
    # ATR
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # Exact recursive UT Bot logic
    # --------------------------------------------------------

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

        # ----------------------------------------------------
        # Pine:
        #
        # iff(
        #   src > stop[1]
        #   and src[1] > stop[1],
        #   max(stop[1], src-nLoss),
        #
        #   iff(
        #     src < stop[1]
        #     and src[1] < stop[1],
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

        # ----------------------------------------------------
        # Pine pos logic
        # ----------------------------------------------------

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

    # --------------------------------------------------------
    # Direction
    #
    # We use the same basic directional state:
    #
    # src > trailing stop = BUY PATH
    # src < trailing stop = SELL PATH
    # --------------------------------------------------------

    direction = []

    for i in range(n):

        if (

            src[i]
            >
            trailing_stop[i]

        ):

            direction.append(
                "BUY"
            )

        elif (

            src[i]
            <
            trailing_stop[i]

        ):

            direction.append(
                "SELL"
            )

        else:

            direction.append(
                "NEUTRAL"
            )

    work["ut_direction"] = (
        direction
    )

    # --------------------------------------------------------
    # Exact UT Bot buy/sell event
    #
    # EMA(src,1) = src
    #
    # crossover(a,b):
    #
    # a > b AND previous a <= previous b
    #
    # --------------------------------------------------------

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

        # Pine:
        # buy = src > stop and crossover(src, stop)

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

        # Pine:
        # sell = src < stop and crossover(stop, src)

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

        # ====================================================
        # RSI
        # ====================================================

        df["rsi"] = calculate_rsi(

            df["close"],

            RSI_PERIOD

        )

        # ====================================================
        # UT BOT 3,10
        # ====================================================

        df = calculate_ut_bot(

            df,

            key_value=UT_KEY_VALUE,

            atr_period=UT_ATR_PERIOD

        )

        # ====================================================
        # DIVERGENCES
        # ====================================================

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

        # ====================================================
        # TRENDLINE BREAKS
        # ====================================================

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

        # ====================================================
        # LAST CLOSED CANDLE
        # ====================================================

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
        # BUY SETUP
        # ====================================================

        if (

            bullish

            and

            down_break

        ):

            div_index = (
                bullish["p2"]
            )

            break_index = (
                last_index
            )

            age_bars = (

                break_index
                -
                div_index

            )

            age_minutes = (
                age_bars * 5
            )

            # ------------------------------------------------
            # Divergence must be fresh
            # ------------------------------------------------

            if (

                0
                <=
                age_minutes
                <=
                MAX_DIVERGENCE_AGE_MINUTES

            ):

                # ------------------------------------------------
                # UT Bot directional filter
                #
                # BUY only when UT Bot is in BUY path.
                # ------------------------------------------------

                if (

                    ut_direction
                    ==
                    "BUY"

                ):

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

                    tp = (
                        nearest_resistance(
                            df,
                            entry
                        )
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
                                    ut_buy_event

                            })

        # ====================================================
        # SELL SETUP
        # ====================================================

        if (

            bearish

            and

            up_break

        ):

            div_index = (
                bearish["p2"]
            )

            break_index = (
                last_index
            )

            age_bars = (

                break_index
                -
                div_index

            )

            age_minutes = (
                age_bars * 5
            )

            # ------------------------------------------------
            # Divergence must be fresh
            # ------------------------------------------------

            if (

                0
                <=
                age_minutes
                <=
                MAX_DIVERGENCE_AGE_MINUTES

            ):

                # ------------------------------------------------
                # UT Bot directional filter
                #
                # SELL only when UT Bot is in SELL path.
                # ------------------------------------------------

                if (

                    ut_direction
                    ==
                    "SELL"

                ):

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

                    tp = (
                        nearest_support(
                            df,
                            entry
                        )
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
                                    ut_sell_event

                            })

        # ====================================================
        # NO SIGNAL
        # ====================================================

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

        # ====================================================
        # RETURN SIGNAL
        # ====================================================

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
    new_signals
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
        "SCANNER v9.0</b>"

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
    # SIGNALS
    # ========================================================

    if new_signals:

        lines.append(
            "🚨 <b>CONFIRMED SIGNALS</b>"
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
            "👀 <b>NO SIGNAL</b>"
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
        "CRYPTO DIVERGENCE SCANNER v9.0"
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
    # COLLECT SIGNALS
    # ========================================================

    new_signals = []

    for result in results:

        signal = result.get(
            "signal"
        )

        if signal:

            new_signals.append(
                signal
            )

            print(

                "SIGNAL:",
                signal["name"],
                signal["direction"]

            )

    # ========================================================
    # SORT SIGNALS
    #
    # Highest RR first
    # ========================================================

    new_signals.sort(

        key=lambda x:
        x["rr"],

        reverse=True

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
        "CONFIRMED SIGNALS:",
        len(new_signals)
    )

    # ========================================================
    # PRINT SIGNALS
    # ========================================================

    if new_signals:

        print("")

        print(
            "CONFIRMED SIGNALS"
        )

        print(
            "------------------"
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

        new_signals

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
