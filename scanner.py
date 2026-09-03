# ============================================================
# CRYPTO DIVERGENCE + TRENDLINE BREAKOUT SCANNER v8.1
# ============================================================
#
# 5M ONLY
#
# BUY:
#   Regular Bullish RSI Divergence
#   +
#   Downtrend Line Break
#
# SELL:
#   Regular Bearish RSI Divergence
#   +
#   Uptrend Line Break
#
# SL:
#   BUY  = latest swing low  - 0.10%
#   SELL = latest swing high + 0.10%
#
# TP:
#   BUY  = nearest resistance >= 0.30% above entry
#   SELL = nearest support    >= 0.30% below entry
#
# STATISTICS:
#   Open
#   Closed
#   Wins
#   Losses
#   Win Rate
#
# NO ICHIMOKU
# NO HIDDEN DIVERGENCE
# NO ATR
# ============================================================

import os
import time
import json
import requests
import pandas as pd
import numpy as np

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


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
# KRAKEN
# ============================================================

KRAKEN_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)


# ============================================================
# STATE
# ============================================================

STATE_FILE = "divergence_state_v8.json"


# ============================================================
# SETTINGS
# ============================================================

TOP_SIGNAL_LIMIT = 10

MAX_WORKERS = 15

CANDLE_LIMIT = 500

RSI_PERIOD = 14

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MAX_PIVOT_GAP = 60

# Minimum difference between divergence pivots
MIN_PRICE_DIFF_PERCENT = 0.05

# Minimum RSI difference
MIN_RSI_DIFF = 2.0

# Divergence remains valid for 2 hours
MAX_DIVERGENCE_AGE_MINUTES = 120

# SL buffer
SL_BUFFER_PERCENT = 0.10

# IMPORTANT:
# TP must be at least 0.30% away from entry
MIN_TP_DISTANCE_PERCENT = 0.30


# ============================================================
# 30 IMPORTANT KRAKEN FUTURES
# ============================================================

SYMBOLS = {

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
    "TIA": "pf_tiausd"
}


# ============================================================
# SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent":
    "Crypto-Divergence-Scanner/8.1"
})


# ============================================================
# LOAD STATE
# ============================================================

def load_state():

    if not os.path.exists(
        STATE_FILE
    ):

        return {
            "signals": {},
            "trades": {},
            "stats": {
                "closed": 0,
                "wins": 0,
                "losses": 0
            }
        }

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            state = json.load(f)

        state.setdefault(
            "signals",
            {}
        )

        state.setdefault(
            "trades",
            {}
        )

        state.setdefault(
            "stats",
            {
                "closed": 0,
                "wins": 0,
                "losses": 0
            }
        )

        return state

    except Exception as e:

        print(
            "STATE LOAD ERROR:",
            e
        )

        return {
            "signals": {},
            "trades": {},
            "stats": {
                "closed": 0,
                "wins": 0,
                "losses": 0
            }
        }


# ============================================================
# SAVE STATE
# ============================================================

def save_state(state):

    try:

        temp_file = (
            STATE_FILE +
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

    except Exception as e:

        print(
            "STATE SAVE ERROR:",
            e
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        print(
            "Telegram token missing."
        )
        return False

    if not TELEGRAM_CHAT_ID:
        print(
            "Telegram chat ID missing."
        )
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id":
        TELEGRAM_CHAT_ID,

        "text":
        message,

        "parse_mode":
        "HTML",

        "disable_web_page_preview":
        True
    }

    try:

        response = session.post(
            url,
            json=payload,
            timeout=20
        )

        print(
            "Telegram HTTP:",
            response.status_code
        )

        if response.status_code == 200:
            return True

        print(
            "Telegram ERROR:",
            response.text
        )

        return False

    except Exception as e:

        print(
            "Telegram EXCEPTION:",
            e
        )

        return False


# ============================================================
# KRAKEN CANDLES
# ============================================================

def get_candles(
    symbol,
    limit=CANDLE_LIMIT
):

    url = (
        f"{KRAKEN_URL}/"
        f"{symbol}/5m"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        print(
            f"KRAKEN {symbol} "
            f"HTTP={response.status_code}"
        )

        if response.status_code != 200:
            return None

        data = response.json()

        if isinstance(
            data,
            dict
        ):

            if "candles" in data:

                data = data[
                    "candles"
                ]

            elif "data" in data:

                data = data[
                    "data"
                ]

            else:

                return None

        if not data:
            return None

        rows = []

        for candle in data[-limit:]:

            if isinstance(
                candle,
                dict
            ):

                rows.append({

                    "time":
                    candle.get(
                        "time"
                    ),

                    "open":
                    candle.get(
                        "open"
                    ),

                    "high":
                    candle.get(
                        "high"
                    ),

                    "low":
                    candle.get(
                        "low"
                    ),

                    "close":
                    candle.get(
                        "close"
                    ),

                    "volume":
                    candle.get(
                        "volume",
                        0
                    )
                })

            elif (
                isinstance(
                    candle,
                    list
                )
                and
                len(candle) >= 6
            ):

                rows.append({

                    "time":
                    candle[0],

                    "open":
                    candle[1],

                    "high":
                    candle[2],

                    "low":
                    candle[3],

                    "close":
                    candle[4],

                    "volume":
                    candle[5]
                })

        if len(rows) < 100:
            return None

        df = pd.DataFrame(
            rows
        )

        for col in [
            "time",
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

        df = df.dropna()

        df = df.sort_values(
            "time"
        )

        df = df.drop_duplicates(
            subset=["time"]
        )

        # ====================================================
        # CLOSED CANDLES ONLY
        # ====================================================

        current_bucket = (
            int(time.time())
            // 300
        ) * 300

        df = df[
            df["time"]
            <
            current_bucket
        ]

        df = df.reset_index(
            drop=True
        )

        if len(df) < 100:
            return None

        return df

    except Exception as e:

        print(
            f"KRAKEN ERROR "
            f"{symbol}: {e}"
        )

        return None


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=RSI_PERIOD
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
# PIVOT LOWS
# ============================================================

def pivot_lows(series):

    values = series.values

    result = []

    for i in range(
        PIVOT_LEFT,
        len(values) -
        PIVOT_RIGHT
    ):

        current = values[i]

        left = values[
            i - PIVOT_LEFT:i
        ]

        right = values[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        if (
            current < left.min()
            and
            current < right.min()
        ):

            result.append(i)

    return result


# ============================================================
# PIVOT HIGHS
# ============================================================

def pivot_highs(series):

    values = series.values

    result = []

    for i in range(
        PIVOT_LEFT,
        len(values) -
        PIVOT_RIGHT
    ):

        current = values[i]

        left = values[
            i - PIVOT_LEFT:i
        ]

        right = values[
            i + 1:
            i + PIVOT_RIGHT + 1
        ]

        if (
            current > left.max()
            and
            current > right.max()
        ):

            result.append(i)

    return result


# ============================================================
# REGULAR BULLISH DIVERGENCE
# ============================================================

def bullish_divergences(df):

    rsi = calculate_rsi(
        df["close"]
    )

    pivots = pivot_lows(
        df["low"]
    )

    results = []

    if len(pivots) < 2:
        return results

    for n in range(
        len(pivots) - 1
    ):

        i1 = pivots[n]
        i2 = pivots[n + 1]

        if (
            i2 - i1
            > MAX_PIVOT_GAP
        ):
            continue

        price1 = float(
            df.iloc[i1]["low"]
        )

        price2 = float(
            df.iloc[i2]["low"]
        )

        rsi1 = float(
            rsi.iloc[i1]
        )

        rsi2 = float(
            rsi.iloc[i2]
        )

        lower_low = (
            price2
            <
            price1 *
            (
                1 -
                MIN_PRICE_DIFF_PERCENT
                / 100
            )
        )

        higher_rsi = (
            rsi2
            >
            rsi1 +
            MIN_RSI_DIFF
        )

        if (
            lower_low
            and
            higher_rsi
        ):

            results.append({

                "kind":
                "REGULAR_BULLISH",

                "pivot1":
                i1,

                "pivot2":
                i2,

                "time1":
                int(
                    df.iloc[i1]["time"]
                ),

                "time2":
                int(
                    df.iloc[i2]["time"]
                ),

                "price1":
                price1,

                "price2":
                price2,

                "rsi1":
                rsi1,

                "rsi2":
                rsi2
            })

    return results


# ============================================================
# REGULAR BEARISH DIVERGENCE
# ============================================================

def bearish_divergences(df):

    rsi = calculate_rsi(
        df["close"]
    )

    pivots = pivot_highs(
        df["high"]
    )

    results = []

    if len(pivots) < 2:
        return results

    for n in range(
        len(pivots) - 1
    ):

        i1 = pivots[n]
        i2 = pivots[n + 1]

        if (
            i2 - i1
            > MAX_PIVOT_GAP
        ):
            continue

        price1 = float(
            df.iloc[i1]["high"]
        )

        price2 = float(
            df.iloc[i2]["high"]
        )

        rsi1 = float(
            rsi.iloc[i1]
        )

        rsi2 = float(
            rsi.iloc[i2]
        )

        higher_high = (
            price2
            >
            price1 *
            (
                1 +
                MIN_PRICE_DIFF_PERCENT
                / 100
            )
        )

        lower_rsi = (
            rsi2
            <
            rsi1 -
            MIN_RSI_DIFF
        )

        if (
            higher_high
            and
            lower_rsi
        ):

            results.append({

                "kind":
                "REGULAR_BEARISH",

                "pivot1":
                i1,

                "pivot2":
                i2,

                "time1":
                int(
                    df.iloc[i1]["time"]
                ),

                "time2":
                int(
                    df.iloc[i2]["time"]
                ),

                "price1":
                price1,

                "price2":
                price2,

                "rsi1":
                rsi1,

                "rsi2":
                rsi2
            })

    return results


# ============================================================
# LINE VALUE
# ============================================================

def line_value(
    x1,
    y1,
    x2,
    y2,
    x
):

    if x2 == x1:
        return y2

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return (
        y1 +
        slope *
        (x - x1)
    )


# ============================================================
# DESCENDING TRENDLINE
# ============================================================

def descending_trendline(df):

    pivots = pivot_highs(
        df["high"]
    )

    if len(pivots) < 2:
        return None

    # newest combinations first
    for b in range(
        len(pivots) - 1,
        0,
        -1
    ):

        i2 = pivots[b]

        for a in range(
            b - 1,
            -1,
            -1
        ):

            i1 = pivots[a]

            if (
                i2 - i1
                > MAX_PIVOT_GAP
            ):
                continue

            y1 = float(
                df.iloc[i1]["high"]
            )

            y2 = float(
                df.iloc[i2]["high"]
            )

            # descending highs
            if y2 < y1:

                return {
                    "i1": i1,
                    "i2": i2,
                    "y1": y1,
                    "y2": y2
                }

    return None


# ============================================================
# ASCENDING TRENDLINE
# ============================================================

def ascending_trendline(df):

    pivots = pivot_lows(
        df["low"]
    )

    if len(pivots) < 2:
        return None

    for b in range(
        len(pivots) - 1,
        0,
        -1
    ):

        i2 = pivots[b]

        for a in range(
            b - 1,
            -1,
            -1
        ):

            i1 = pivots[a]

            if (
                i2 - i1
                > MAX_PIVOT_GAP
            ):
                continue

            y1 = float(
                df.iloc[i1]["low"]
            )

            y2 = float(
                df.iloc[i2]["low"]
            )

            # ascending lows
            if y2 > y1:

                return {
                    "i1": i1,
                    "i2": i2,
                    "y1": y1,
                    "y2": y2
                }

    return None


# ============================================================
# BULLISH TRENDLINE BREAK
# ============================================================

def bullish_break(
    df,
    divergence
):

    line = descending_trendline(
        df
    )

    if line is None:
        return None

    start = max(
        line["i2"] + 1,
        divergence["pivot2"] + 1
    )

    last = len(df) - 1

    if start > last:
        return None

    for i in range(
        start,
        last + 1
    ):

        previous = i - 1

        previous_line = line_value(
            line["i1"],
            line["y1"],
            line["i2"],
            line["y2"],
            previous
        )

        current_line = line_value(
            line["i1"],
            line["y1"],
            line["i2"],
            line["y2"],
            i
        )

        previous_close = float(
            df.iloc[previous]["close"]
        )

        current_close = float(
            df.iloc[i]["close"]
        )

        if (
            previous_close
            <= previous_line
            and
            current_close
            > current_line
        ):

            return {

                "index":
                i,

                "time":
                int(
                    df.iloc[i]["time"]
                ),

                "line":
                current_line
            }

    return None


# ============================================================
# BEARISH TRENDLINE BREAK
# ============================================================

def bearish_break(
    df,
    divergence
):

    line = ascending_trendline(
        df
    )

    if line is None:
        return None

    start = max(
        line["i2"] + 1,
        divergence["pivot2"] + 1
    )

    last = len(df) - 1

    if start > last:
        return None

    for i in range(
        start,
        last + 1
    ):

        previous = i - 1

        previous_line = line_value(
            line["i1"],
            line["y1"],
            line["i2"],
            line["y2"],
            previous
        )

        current_line = line_value(
            line["i1"],
            line["y1"],
            line["i2"],
            line["y2"],
            i
        )

        previous_close = float(
            df.iloc[previous]["close"]
        )

        current_close = float(
            df.iloc[i]["close"]
        )

        if (
            previous_close
            >= previous_line
            and
            current_close
            < current_line
        ):

            return {

                "index":
                i,

                "time":
                int(
                    df.iloc[i]["time"]
                ),

                "line":
                current_line
            }

    return None


# ============================================================
# LAST SWING LOW
# ============================================================

def latest_swing_low(df):

    pivots = pivot_lows(
        df["low"]
    )

    valid = [
        p
        for p in pivots
        if p < len(df) - 1
    ]

    if not valid:
        return None

    p = valid[-1]

    return {

        "index":
        p,

        "time":
        int(
            df.iloc[p]["time"]
        ),

        "price":
        float(
            df.iloc[p]["low"]
        )
    }


# ============================================================
# LAST SWING HIGH
# ============================================================

def latest_swing_high(df):

    pivots = pivot_highs(
        df["high"]
    )

    valid = [
        p
        for p in pivots
        if p < len(df) - 1
    ]

    if not valid:
        return None

    p = valid[-1]

    return {

        "index":
        p,

        "time":
        int(
            df.iloc[p]["time"]
        ),

        "price":
        float(
            df.iloc[p]["high"]
        )
    }


# ============================================================
# NEAREST RESISTANCE
# ============================================================

def nearest_resistance(
    df,
    entry
):

    pivots = pivot_highs(
        df["high"]
    )

    levels = []

    minimum_price = (
        entry *
        (
            1 +
            MIN_TP_DISTANCE_PERCENT
            / 100
        )
    )

    for p in pivots:

        level = float(
            df.iloc[p]["high"]
        )

        if level >= minimum_price:

            levels.append(
                level
            )

    if not levels:
        return None

    return min(
        levels
    )


# ============================================================
# NEAREST SUPPORT
# ============================================================

def nearest_support(
    df,
    entry
):

    pivots = pivot_lows(
        df["low"]
    )

    levels = []

    maximum_price = (
        entry *
        (
            1 -
            MIN_TP_DISTANCE_PERCENT
            / 100
        )
    )

    for p in pivots:

        level = float(
            df.iloc[p]["low"]
        )

        if level <= maximum_price:

            levels.append(
                level
            )

    if not levels:
        return None

    return max(
        levels
    )


# ============================================================
# BUILD BUY
# ============================================================

def build_buy(
    df
):

    entry = float(
        df.iloc[-1]["close"]
    )

    swing = latest_swing_low(
        df
    )

    if swing is None:
        return None

    # SL 0.10% below latest swing low
    sl = (
        swing["price"]
        *
        (
            1 -
            SL_BUFFER_PERCENT
            / 100
        )
    )

    # nearest resistance
    tp = nearest_resistance(
        df,
        entry
    )

    if tp is None:
        return None

    risk = (
        entry - sl
    )

    reward = (
        tp - entry
    )

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    tp_distance = (
        reward
        /
        entry
        *
        100
    )

    # Safety check
    if (
        tp_distance
        <
        MIN_TP_DISTANCE_PERCENT
    ):
        return None

    rr = (
        reward
        /
        risk
    )

    return {

        "entry":
        entry,

        "sl":
        sl,

        "tp":
        tp,

        "risk":
        risk,

        "reward":
        reward,

        "rr":
        rr,

        "swing":
        swing["price"]
    }


# ============================================================
# BUILD SELL
# ============================================================

def build_sell(
    df
):

    entry = float(
        df.iloc[-1]["close"]
    )

    swing = latest_swing_high(
        df
    )

    if swing is None:
        return None

    # SL 0.10% above latest swing high
    sl = (
        swing["price"]
        *
        (
            1 +
            SL_BUFFER_PERCENT
            / 100
        )
    )

    # nearest support
    tp = nearest_support(
        df,
        entry
    )

    if tp is None:
        return None

    risk = (
        sl - entry
    )

    reward = (
        entry - tp
    )

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    tp_distance = (
        reward
        /
        entry
        *
        100
    )

    if (
        tp_distance
        <
        MIN_TP_DISTANCE_PERCENT
    ):
        return None

    rr = (
        reward
        /
        risk
    )

    return {

        "entry":
        entry,

        "sl":
        sl,

        "tp":
        tp,

        "risk":
        risk,

        "reward":
        reward,

        "rr":
        rr,

        "swing":
        swing["price"]
    }


# ============================================================
# PROCESS COIN
# ============================================================

def process_coin(
    coin,
    symbol
):

    result = {

        "coin":
        coin,

        "status":
        "ERROR",

        "direction":
        None,

        "divergence":
        None,

        "breakout":
        None,

        "setup":
        None
    }

    df = get_candles(
        symbol
    )

    if (
        df is None
        or
        len(df) < 100
    ):

        result[
            "status"
        ] = "DATA_ERROR"

        return result

    bullish = (
        bullish_divergences(
            df
        )
    )

    bearish = (
        bearish_divergences(
            df
        )
    )

    now = int(
        time.time()
    )

    # ========================================================
    # VALID BULLISH DIVERGENCES
    # ========================================================

    bullish_valid = []

    for div in bullish:

        age = (
            now -
            div["time2"]
        ) / 60

        if (
            0 <= age
            <=
            MAX_DIVERGENCE_AGE_MINUTES
        ):

            div[
                "age_minutes"
            ] = age

            bullish_valid.append(
                div
            )

    # ========================================================
    # VALID BEARISH DIVERGENCES
    # ========================================================

    bearish_valid = []

    for div in bearish:

        age = (
            now -
            div["time2"]
        ) / 60

        if (
            0 <= age
            <=
            MAX_DIVERGENCE_AGE_MINUTES
        ):

            div[
                "age_minutes"
            ] = age

            bearish_valid.append(
                div
            )

    bullish_valid.sort(
        key=lambda x:
        x["time2"],
        reverse=True
    )

    bearish_valid.sort(
        key=lambda x:
        x["time2"],
        reverse=True
    )

    # ========================================================
    # BULLISH SETUP
    # ========================================================

    for div in bullish_valid:

        breakout = (
            bullish_break(
                df,
                div
            )
        )

        if breakout is None:
            continue

        setup = build_buy(
            df
        )

        if setup is None:
            continue

        result[
            "status"
        ] = "VALID_SIGNAL"

        result[
            "direction"
        ] = "BUY"

        result[
            "divergence"
        ] = div

        result[
            "breakout"
        ] = breakout

        result[
            "setup"
        ] = setup

        return result

    # ========================================================
    # BEARISH SETUP
    # ========================================================

    for div in bearish_valid:

        breakout = (
            bearish_break(
                df,
                div
            )
        )

        if breakout is None:
            continue

        setup = build_sell(
            df
        )

        if setup is None:
            continue

        result[
            "status"
        ] = "VALID_SIGNAL"

        result[
            "direction"
        ] = "SELL"

        result[
            "divergence"
        ] = div

        result[
            "breakout"
        ] = breakout

        result[
            "setup"
        ] = setup

        return result

    # ========================================================
    # WAITING
    # ========================================================

    if (
        bullish_valid
        or
        bearish_valid
    ):

        result[
            "status"
        ] = "WAITING_BREAKOUT"

        if bullish_valid:

            result[
                "direction"
            ] = "BUY"

            result[
                "divergence"
            ] = bullish_valid[0]

        else:

            result[
                "direction"
            ] = "SELL"

            result[
                "divergence"
            ] = bearish_valid[0]

        return result

    result[
        "status"
    ] = "NO_DIVERGENCE"

    return result


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(
    value
):

    value = float(
        value
    )

    if value >= 10000:
        return f"{value:,.2f}"

    if value >= 1000:
        return f"{value:,.3f}"

    if value >= 100:
        return f"{value:.3f}"

    if value >= 1:
        return f"{value:.5f}"

    if value >= 0.1:
        return f"{value:.6f}"

    return f"{value:.8f}"


# ============================================================
# SIGNAL KEY
# ============================================================

def signal_key(
    result
):

    div = result[
        "divergence"
    ]

    breakout = result[
        "breakout"
    ]

    return (
        f'{result["coin"]}_'
        f'{result["direction"]}_'
        f'{div["time2"]}_'
        f'{breakout["time"]}'
    )


# ============================================================
# NEW SIGNAL MESSAGE
# ============================================================

def signal_message(
    result
):

    direction = result[
        "direction"
    ]

    div = result[
        "divergence"
    ]

    setup = result[
        "setup"
    ]

    if direction == "BUY":

        emoji = "🟢"

    else:

        emoji = "🔴"

    return f"""
🚨 <b>NEW SETUP v8.1</b>
━━━━━━━━━━━━━━━━━━

<b>#{result["coin"]}/USDT</b>

{emoji} <b>{direction}</b>

⏱ Timeframe:
<b>5M</b>

━━━━━━━━━━━━━━━━━━

📊 <b>REGULAR DIVERGENCE</b>

{div["kind"]}

Price:
{fmt_price(div["price1"])}
 → {fmt_price(div["price2"])}

RSI:
{div["rsi1"]:.1f}
 → {div["rsi2"]:.1f}

━━━━━━━━━━━━━━━━━━

📐 <b>TRENDLINE BREAK</b>

✅ Confirmed
✅ Closed candle

━━━━━━━━━━━━━━━━━━

💰 Entry:
<b>{fmt_price(setup["entry"])}</b>

🛑 SL:
<b>{fmt_price(setup["sl"])}</b>

🎯 TP:
<b>{fmt_price(setup["tp"])}</b>

━━━━━━━━━━━━━━━━━━

📊 TP Distance:
<b>{(setup["reward"] / setup["entry"] * 100):.2f}%</b>

⚖️ R:R:
<b>{setup["rr"]:.2f}:1</b>

━━━━━━━━━━━━━━━━━━

🛑 SL Buffer:
{SL_BUFFER_PERCENT:.2f}%

🎯 Minimum TP:
{MIN_TP_DISTANCE_PERCENT:.2f}%

━━━━━━━━━━━━━━━━━━

✅ Regular Divergence
✅ Trendline Break
✅ Closed Candle
✅ Swing SL
✅ Nearest S/R TP
""".strip()


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_trades(
    state
):

    closed = []

    for key, trade in list(
        state["trades"].items()
    ):

        if (
            trade.get("status")
            !=
            "OPEN"
        ):

            continue

        coin = trade[
            "coin"
        ]

        symbol = SYMBOLS.get(
            coin
        )

        if not symbol:
            continue

        df = get_candles(
            symbol,
            100
        )

        if df is None:
            continue

        candle = df.iloc[-1]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        direction = trade[
            "direction"
        ]

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        result = None

        # ====================================================
        # BUY
        # ====================================================

        if direction == "BUY":

            if low <= sl:

                result = "LOSS"

            elif high >= tp:

                result = "WIN"

        # ====================================================
        # SELL
        # ====================================================

        else:

            if high >= sl:

                result = "LOSS"

            elif low <= tp:

                result = "WIN"

        if result is None:
            continue

        trade[
            "status"
        ] = "CLOSED"

        trade[
            "result"
        ] = result

        trade[
            "close_time"
        ] = int(
            time.time()
        )

        trade[
            "close_price"
        ] = (
            sl
            if result == "LOSS"
            else tp
        )

        state[
            "stats"
        ]["closed"] += 1

        if result == "WIN":

            state[
                "stats"
            ]["wins"] += 1

        else:

            state[
                "stats"
            ]["losses"] += 1

        closed.append(
            trade
        )

    return closed


# ============================================================
# CLOSED TRADE MESSAGE
# ============================================================

def closed_message(
    trade
):

    if trade["result"] == "WIN":

        emoji = "✅"

    else:

        emoji = "❌"

    return f"""
{emoji} <b>TRADE CLOSED</b>
━━━━━━━━━━━━━━━━━━

<b>#{trade["coin"]}/USDT</b>

Direction:
<b>{trade["direction"]}</b>

Result:
<b>{trade["result"]}</b>

Entry:
{fmt_price(trade["entry"])}

Exit:
{fmt_price(trade["close_price"])}

━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# REPORT
# ============================================================

def report_message(
    results,
    scan_number,
    elapsed,
    state
):

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    new_signals = [
        r
        for r in results
        if r["status"]
        ==
        "VALID_SIGNAL"
    ]

    waiting = [
        r
        for r in results
        if r["status"]
        ==
        "WAITING_BREAKOUT"
    ]

    data_errors = [
        r
        for r in results
        if r["status"]
        ==
        "DATA_ERROR"
    ]

    open_trades = [
        t
        for t in state[
            "trades"
        ].values()
        if t.get("status")
        ==
        "OPEN"
    ]

    stats = state[
        "stats"
    ]

    closed = int(
        stats["closed"]
    )

    wins = int(
        stats["wins"]
    )

    losses = int(
        stats["losses"]
    )

    if closed > 0:

        win_rate = (
            wins
            /
            closed
            *
            100
        )

    else:

        win_rate = 0

    message = f"""
📊 <b>DIVERGENCE SCANNER v8.1</b>
━━━━━━━━━━━━━━━━━━

🕐 UTC:
{now}

🔢 Scan:
<b>#{scan_number}</b>

🪙 Coins:
<b>{len(results)}/30</b>

⏱ Timeframe:
<b>5M</b>

━━━━━━━━━━━━━━━━━━

📊 Divergence:
<b>{len(waiting) + len(new_signals)}</b>

⏳ Waiting Break:
<b>{len(waiting)}</b>

🚨 New Setups:
<b>{len(new_signals)}</b>

⚠️ Data Errors:
<b>{len(data_errors)}</b>

━━━━━━━━━━━━━━━━━━

📂 <b>TRADE STATISTICS</b>

🟢 Open:
<b>{len(open_trades)}</b>

📁 Closed:
<b>{closed}</b>

✅ Wins:
<b>{wins}</b>

❌ Losses:
<b>{losses}</b>

🎯 Win Rate:
<b>{win_rate:.1f}%</b>

━━━━━━━━━━━━━━━━━━

<b>SETUP RULES</b>

🟢 BUY
Bullish Divergence
+
Downtrend Break

🔴 SELL
Bearish Divergence
+
Uptrend Break

━━━━━━━━━━━━━━━━━━

🛑 SL:
Swing ± {SL_BUFFER_PERCENT:.2f}%

🎯 TP:
Nearest S/R

📏 Minimum TP:
{MIN_TP_DISTANCE_PERCENT:.2f}%

━━━━━━━━━━━━━━━━━━

⚡ Scan Time:
{elapsed:.2f}s

🤖 GitHub Actions
""".strip()

    if new_signals:

        message += (
            "\n\n🚨 <b>NEW SIGNALS</b>\n"
        )

        for result in new_signals:

            emoji = (
                "🟢"
                if result["direction"]
                ==
                "BUY"
                else
                "🔴"
            )

            setup = result[
                "setup"
            ]

            message += (
                f"{emoji} "
                f"<b>{result['coin']}</b> "
                f"{result['direction']} "
                f"| R:R "
                f"{setup['rr']:.2f}:1\n"
            )

    else:

        message += (
            "\n\n⚪ "
            "<b>NO NEW SETUP</b>"
        )

    return message


# ============================================================
# MAIN SCAN
# ============================================================

def run_scan(
    scan_number
):

    start = time.time()

    results = []

    print()
    print(
        "=============================================="
    )

    print(
        f"SCAN #{scan_number}"
    )

    print(
        "=============================================="
    )

    # ========================================================
    # 30 COINS PARALLEL
    # ========================================================

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {

            executor.submit(
                process_coin,
                coin,
                symbol
            ):
            coin

            for coin, symbol
            in SYMBOLS.items()
        }

        for future in as_completed(
            futures
        ):

            coin = futures[
                future
            ]

            try:

                result = (
                    future.result()
                )

                results.append(
                    result
                )

                print(
                    coin,
                    "=>",
                    result["status"]
                )

            except Exception as e:

                print(
                    coin,
                    "ERROR:",
                    e
                )

                results.append({

                    "coin":
                    coin,

                    "status":
                    "DATA_ERROR",

                    "direction":
                    None,

                    "divergence":
                    None,

                    "breakout":
                    None,

                    "setup":
                    None
                })

    # ========================================================
    # STATE
    # ========================================================

    state = load_state()

    # ========================================================
    # UPDATE TRADES
    # ========================================================

    closed_trades = (
        update_trades(
            state
        )
    )

    for trade in closed_trades:

        send_telegram(
            closed_message(
                trade
            )
        )

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    new_signals = []

    for result in results:

        if (
            result["status"]
            !=
            "VALID_SIGNAL"
        ):
            continue

        key = signal_key(
            result
        )

        # Already sent
        if key in state[
            "signals"
        ]:

            continue

        # Save signal
        state[
            "signals"
        ][key] = {

            "coin":
            result["coin"],

            "direction":
            result["direction"],

            "created":
            int(time.time())
        }

        setup = result[
            "setup"
        ]

        # Create trade
        state[
            "trades"
        ][key] = {

            "id":
            key,

            "coin":
            result["coin"],

            "direction":
            result["direction"],

            "entry":
            setup["entry"],

            "sl":
            setup["sl"],

            "tp":
            setup["tp"],

            "rr":
            setup["rr"],

            "created":
            int(time.time()),

            "status":
            "OPEN"
        }

        new_signals.append(
            result
        )

    # ========================================================
    # SAVE
    # ========================================================

    save_state(
        state
    )

    # ========================================================
    # SEND NEW SIGNALS
    # ========================================================

    for result in new_signals:

        send_telegram(
            signal_message(
                result
            )
        )

    # ========================================================
    # REPORT
    # ========================================================

    elapsed = (
        time.time()
        -
        start
    )

    report = report_message(
        results,
        scan_number,
        elapsed,
        state
    )

    send_telegram(
        report
    )

    print()
    print(
        f"SCAN #{scan_number} "
        f"FINISHED "
        f"in {elapsed:.2f}s"
    )

    return results


# ============================================================
# ENTRY
# ============================================================

def main():

    # One execution = one scan.
    # GitHub Actions runs this every 5 minutes.

    print(
        "=============================================="
    )

    print(
        "CRYPTO DIVERGENCE + TRENDLINE SCANNER v8.1"
    )

    print(
        "=============================================="
    )

    print(
        "30 Coins"
    )

    print(
        "5M Timeframe"
    )

    print(
        "Regular Divergence ONLY"
    )

    print(
        "Trendline Break"
    )

    print(
        f"SL Buffer: {SL_BUFFER_PERCENT}%"
    )

    print(
        f"Minimum TP: {MIN_TP_DISTANCE_PERCENT}%"
    )

    print(
        "Ichimoku: OFF"
    )

    print()

    # Use timestamp-based scan number
    scan_number = int(
        time.time()
        // 300
    )

    run_scan(
        scan_number
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
