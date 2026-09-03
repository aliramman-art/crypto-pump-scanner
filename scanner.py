# ============================================================
# CRYPTO DIVERGENCE SCANNER v7.2
# GITHUB ACTIONS + KRAKEN DEBUG/FIX
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
import json
import os

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone


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
# KRAKEN
# ============================================================

KRAKEN_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

STATE_FILE = "divergence_state_v7.json"

MAX_WORKERS = 10


# ============================================================
# SETTINGS
# ============================================================

RSI_PERIOD = 14

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

MAX_PIVOT_GAP = 60

MIN_PRICE_DIFF_PERCENT = 0.05
MIN_RSI_DIFF = 2.0

MAX_HIDDEN_AGE_HOURS = 24
MAX_REGULAR_AGE_MINUTES = 5

SL_BUFFER_PERCENT = 0.10


# ============================================================
# TIMEFRAMES
# ============================================================

TF_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "1d": 86400
}


# ============================================================
# SYMBOLS
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
    "User-Agent": "Crypto-Divergence-Scanner/7.2",
    "Accept": "application/json"
})


scan_number = 0


# ============================================================
# STATE
# ============================================================

def load_state():

    if not os.path.exists(STATE_FILE):
        return {}

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

            if isinstance(data, dict):
                return data

            return {}

    except Exception as e:

        print(
            "State load error:",
            e
        )

        return {}


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
            "State save error:",
            e
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token is EMPTY."
        )

        return False

    if not TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID is EMPTY."
        )

        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
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

        if response.status_code != 200:

            print(
                "Telegram error:",
                response.text
            )

            return False

        print(
            "Telegram message sent."
        )

        return True

    except Exception as e:

        print(
            "Telegram connection error:",
            e
        )

        return False


# ============================================================
# KRAKEN DATA
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit=500
):

    url = (
        f"{KRAKEN_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    try:

        response = session.get(
            url,
            timeout=20
        )

        print(
            f"KRAKEN {symbol} {resolution} "
            f"HTTP={response.status_code}"
        )

        if response.status_code != 200:

            print(
                f"KRAKEN ERROR "
                f"{symbol} {resolution}: "
                f"{response.text[:500]}"
            )

            return None

        try:

            data = response.json()

        except Exception:

            print(
                f"KRAKEN INVALID JSON "
                f"{symbol} {resolution}: "
                f"{response.text[:500]}"
            )

            return None


        # ----------------------------------------------------
        # Detect common response formats
        # ----------------------------------------------------

        candles = None


        if isinstance(data, list):

            candles = data


        elif isinstance(data, dict):

            if "candles" in data:

                candles = data["candles"]

            elif "data" in data:

                candles = data["data"]

            elif "result" in data:

                result = data["result"]

                if isinstance(result, list):

                    candles = result

                elif isinstance(result, dict):

                    if "candles" in result:
                        candles = result["candles"]

                    elif "data" in result:
                        candles = result["data"]


        if candles is None:

            print(
                f"KRAKEN UNKNOWN FORMAT "
                f"{symbol} {resolution}: "
                f"{str(data)[:1000]}"
            )

            return None


        if not candles:

            print(
                f"KRAKEN EMPTY DATA "
                f"{symbol} {resolution}"
            )

            return None


        rows = []


        for item in candles[-limit:]:


            # ----------------------------------------------
            # Dictionary format
            # ----------------------------------------------

            if isinstance(
                item,
                dict
            ):

                t = (
                    item.get("time")
                    or
                    item.get("timestamp")
                    or
                    item.get("ts")
                )

                o = item.get("open")
                h = item.get("high")
                l = item.get("low")
                c = item.get("close")
                v = (
                    item.get("volume")
                    or
                    item.get("vol")
                    or
                    0
                )

                if (
                    t is not None
                    and
                    o is not None
                    and
                    h is not None
                    and
                    l is not None
                    and
                    c is not None
                ):

                    rows.append({
                        "time": t,
                        "open": o,
                        "high": h,
                        "low": l,
                        "close": c,
                        "volume": v
                    })


            # ----------------------------------------------
            # Array format
            # ----------------------------------------------

            elif isinstance(
                item,
                (list, tuple)
            ):

                if len(item) >= 6:

                    rows.append({
                        "time": item[0],
                        "open": item[1],
                        "high": item[2],
                        "low": item[3],
                        "close": item[4],
                        "volume": item[5]
                    })


        if not rows:

            print(
                f"KRAKEN NO PARSED CANDLES "
                f"{symbol} {resolution}"
            )

            print(
                "RAW:",
                str(candles[:2])[:1000]
            )

            return None


        df = pd.DataFrame(rows)


        # ----------------------------------------------------
        # Numeric conversion
        # ----------------------------------------------------

        for column in [
            "time",
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


        df = df.dropna(
            subset=[
                "time",
                "open",
                "high",
                "low",
                "close"
            ]
        )


        if df.empty:

            print(
                f"KRAKEN DATAFRAME EMPTY "
                f"{symbol} {resolution}"
            )

            return None


        # ----------------------------------------------------
        # Detect milliseconds
        # ----------------------------------------------------

        if df["time"].median() > 100000000000:

            df["time"] = (
                df["time"] / 1000
            )


        df["time"] = (
            df["time"]
            .astype(np.int64)
        )


        # ----------------------------------------------------
        # Sort
        # ----------------------------------------------------

        df = df.sort_values(
            "time"
        )

        df = df.drop_duplicates(
            subset=["time"]
        )

        df = df.reset_index(
            drop=True
        )


        # ----------------------------------------------------
        # Remove currently open candle
        # ----------------------------------------------------

        tf_seconds = TF_SECONDS[
            resolution
        ]

        current_bucket = (
            int(time.time())
            //
            tf_seconds
        ) * tf_seconds


        df = df[
            df["time"]
            <
            current_bucket
        ]


        df = df.reset_index(
            drop=True
        )


        # ----------------------------------------------------
        # Final check
        # ----------------------------------------------------

        if len(df) == 0:

            print(
                f"KRAKEN NO CLOSED CANDLES "
                f"{symbol} {resolution}"
            )

            return None


        print(
            f"KRAKEN OK "
            f"{symbol} {resolution} "
            f"candles={len(df)}"
        )


        return df


    except requests.exceptions.Timeout:

        print(
            f"KRAKEN TIMEOUT "
            f"{symbol} {resolution}"
        )

        return None


    except requests.exceptions.ConnectionError as e:

        print(
            f"KRAKEN CONNECTION ERROR "
            f"{symbol} {resolution}: "
            f"{e}"
        )

        return None


    except Exception as e:

        print(
            f"KRAKEN EXCEPTION "
            f"{symbol} {resolution}: "
            f"{e}"
        )

        return None


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
# PIVOTS
# ============================================================

def find_pivot_lows(
    series,
    left=2,
    right=2
):

    values = series.values

    pivots = []

    for i in range(
        left,
        len(values) - right
    ):

        current = values[i]

        left_side = values[
            i-left:i
        ]

        right_side = values[
            i+1:i+right+1
        ]

        if (
            current
            <
            left_side.min()
            and
            current
            <
            right_side.min()
        ):

            pivots.append(i)

    return pivots


def find_pivot_highs(
    series,
    left=2,
    right=2
):

    values = series.values

    pivots = []

    for i in range(
        left,
        len(values) - right
    ):

        current = values[i]

        left_side = values[
            i-left:i
        ]

        right_side = values[
            i+1:i+right+1
        ]

        if (
            current
            >
            left_side.max()
            and
            current
            >
            right_side.max()
        ):

            pivots.append(i)

    return pivots


# ============================================================
# DIVERGENCE OBJECT
# ============================================================

def make_divergence(
    kind,
    i1,
    i2,
    df,
    rsi
):

    return {

        "kind": kind,

        "pivot1": int(i1),

        "pivot2": int(i2),

        "time1": int(
            df.iloc[i1]["time"]
        ),

        "time2": int(
            df.iloc[i2]["time"]
        ),

        "price1": float(
            df.iloc[i1]["close"]
        ),

        "price2": float(
            df.iloc[i2]["close"]
        ),

        "rsi1": float(
            rsi.iloc[i1]
        ),

        "rsi2": float(
            rsi.iloc[i2]
        )
    }


# ============================================================
# HIDDEN BEARISH
# ============================================================

def find_hidden_bearish(df):

    rsi = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    pivots = find_pivot_highs(
        df["close"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(pivots) < 2:
        return []

    results = []

    for n in range(
        len(pivots) - 1
    ):

        i1 = pivots[n]
        i2 = pivots[n + 1]

        if (
            i2 - i1
            >
            MAX_PIVOT_GAP
        ):
            continue

        price1 = float(
            df.iloc[i1]["close"]
        )

        price2 = float(
            df.iloc[i2]["close"]
        )

        rsi1 = float(
            rsi.iloc[i1]
        )

        rsi2 = float(
            rsi.iloc[i2]
        )

        price_condition = (
            price2
            <
            price1
            *
            (
                1
                -
                MIN_PRICE_DIFF_PERCENT
                / 100
            )
        )

        rsi_condition = (
            rsi2
            >
            rsi1
            +
            MIN_RSI_DIFF
        )

        if (
            price_condition
            and
            rsi_condition
        ):

            results.append(
                make_divergence(
                    "HIDDEN_BEARISH",
                    i1,
                    i2,
                    df,
                    rsi
                )
            )

    return results


# ============================================================
# HIDDEN BULLISH
# ============================================================

def find_hidden_bullish(df):

    rsi = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    pivots = find_pivot_lows(
        df["close"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(pivots) < 2:
        return []

    results = []

    for n in range(
        len(pivots) - 1
    ):

        i1 = pivots[n]
        i2 = pivots[n + 1]

        if (
            i2 - i1
            >
            MAX_PIVOT_GAP
        ):
            continue

        price1 = float(
            df.iloc[i1]["close"]
        )

        price2 = float(
            df.iloc[i2]["close"]
        )

        rsi1 = float(
            rsi.iloc[i1]
        )

        rsi2 = float(
            rsi.iloc[i2]
        )

        price_condition = (
            price2
            >
            price1
            *
            (
                1
                +
                MIN_PRICE_DIFF_PERCENT
                / 100
            )
        )

        rsi_condition = (
            rsi2
            <
            rsi1
            -
            MIN_RSI_DIFF
        )

        if (
            price_condition
            and
            rsi_condition
        ):

            results.append(
                make_divergence(
                    "HIDDEN_BULLISH",
                    i1,
                    i2,
                    df,
                    rsi
                )
            )

    return results


# ============================================================
# REGULAR BULLISH
# ============================================================

def find_regular_bullish(
    df,
    after_timestamp
):

    rsi = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    pivots = find_pivot_lows(
        df["close"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(pivots) < 2:
        return []

    results = []

    for n in range(
        len(pivots) - 1
    ):

        i1 = pivots[n]
        i2 = pivots[n + 1]

        if (
            i2 - i1
            >
            MAX_PIVOT_GAP
        ):
            continue

        time2 = int(
            df.iloc[i2]["time"]
        )

        if time2 <= after_timestamp:
            continue

        price1 = float(
            df.iloc[i1]["close"]
        )

        price2 = float(
            df.iloc[i2]["close"]
        )

        rsi1 = float(
            rsi.iloc[i1]
        )

        rsi2 = float(
            rsi.iloc[i2]
        )

        price_condition = (
            price2
            <
            price1
            *
            (
                1
                -
                MIN_PRICE_DIFF_PERCENT
                / 100
            )
        )

        rsi_condition = (
            rsi2
            >
            rsi1
            +
            MIN_RSI_DIFF
        )

        if (
            price_condition
            and
            rsi_condition
        ):

            results.append(
                make_divergence(
                    "REGULAR_BULLISH",
                    i1,
                    i2,
                    df,
                    rsi
                )
            )

    return results


# ============================================================
# REGULAR BEARISH
# ============================================================

def find_regular_bearish(
    df,
    after_timestamp
):

    rsi = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    pivots = find_pivot_highs(
        df["close"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if len(pivots) < 2:
        return []

    results = []

    for n in range(
        len(pivots) - 1
    ):

        i1 = pivots[n]
        i2 = pivots[n + 1]

        if (
            i2 - i1
            >
            MAX_PIVOT_GAP
        ):
            continue

        time2 = int(
            df.iloc[i2]["time"]
        )

        if time2 <= after_timestamp:
            continue

        price1 = float(
            df.iloc[i1]["close"]
        )

        price2 = float(
            df.iloc[i2]["close"]
        )

        rsi1 = float(
            rsi.iloc[i1]
        )

        rsi2 = float(
            rsi.iloc[i2]
        )

        price_condition = (
            price2
            >
            price1
            *
            (
                1
                +
                MIN_PRICE_DIFF_PERCENT
                / 100
            )
        )

        rsi_condition = (
            rsi2
            <
            rsi1
            -
            MIN_RSI_DIFF
        )

        if (
            price_condition
            and
            rsi_condition
        ):

            results.append(
                make_divergence(
                    "REGULAR_BEARISH",
                    i1,
                    i2,
                    df,
                    rsi
                )
            )

    return results


# ============================================================
# 1H TREND
# ============================================================

def determine_1h_trend(df):

    if len(df) < 210:
        return "NEUTRAL"

    close = df["close"]

    ema50 = close.ewm(
        span=50,
        adjust=False
    ).mean()

    ema200 = close.ewm(
        span=200,
        adjust=False
    ).mean()

    last_close = float(
        close.iloc[-1]
    )

    last_ema50 = float(
        ema50.iloc[-1]
    )

    last_ema200 = float(
        ema200.iloc[-1]
    )

    if (
        last_close > last_ema50
        and
        last_ema50 > last_ema200
    ):

        return "BULLISH"

    if (
        last_close < last_ema50
        and
        last_ema50 < last_ema200
    ):

        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# VALID HIDDEN
# ============================================================

def get_valid_hidden(
    df,
    trend
):

    now = int(
        time.time()
    )

    if trend == "BULLISH":

        candidates = find_hidden_bearish(
            df
        )

    elif trend == "BEARISH":

        candidates = find_hidden_bullish(
            df
        )

    else:

        return None


    if not candidates:
        return None


    candidates.sort(
        key=lambda x: x["time2"],
        reverse=True
    )


    for hidden in candidates:

        age_hours = (
            now
            -
            hidden["time2"]
        ) / 3600


        if (
            0
            <= age_hours
            <= MAX_HIDDEN_AGE_HOURS
        ):

            hidden[
                "age_hours"
            ] = age_hours

            return hidden


    return None


# ============================================================
# SWING LOW
# ============================================================

def get_last_swing_low(df):

    pivots = find_pivot_lows(
        df["low"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if not pivots:
        return None

    valid = [
        p
        for p in pivots
        if p < len(df) - 1
    ]

    if not valid:
        return None

    p = valid[-1]

    return {
        "index": p,
        "time": int(
            df.iloc[p]["time"]
        ),
        "price": float(
            df.iloc[p]["low"]
        )
    }


# ============================================================
# SWING HIGH
# ============================================================

def get_last_swing_high(df):

    pivots = find_pivot_highs(
        df["high"],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    if not pivots:
        return None

    valid = [
        p
        for p in pivots
        if p < len(df) - 1
    ]

    if not valid:
        return None

    p = valid[-1]

    return {
        "index": p,
        "time": int(
            df.iloc[p]["time"]
        ),
        "price": float(
            df.iloc[p]["high"]
        )
    }


# ============================================================
# LONG SETUP
# ============================================================

def build_long_setup(df):

    entry = float(
        df.iloc[-1]["close"]
    )

    swing = get_last_swing_low(
        df
    )

    if swing is None:
        return None

    swing_low = swing["price"]

    sl = (
        swing_low
        *
        (
            1
            -
            SL_BUFFER_PERCENT
            / 100
        )
    )

    if sl >= entry:
        return None

    risk = entry - sl

    if risk <= 0:
        return None

    tp = entry + risk

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "swing_price": swing_low,
        "swing_time": swing["time"]
    }


# ============================================================
# SHORT SETUP
# ============================================================

def build_short_setup(df):

    entry = float(
        df.iloc[-1]["close"]
    )

    swing = get_last_swing_high(
        df
    )

    if swing is None:
        return None

    swing_high = swing["price"]

    sl = (
        swing_high
        *
        (
            1
            +
            SL_BUFFER_PERCENT
            / 100
        )
    )

    if sl <= entry:
        return None

    risk = sl - entry

    if risk <= 0:
        return None

    tp = entry - risk

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "risk": risk,
        "swing_price": swing_high,
        "swing_time": swing["time"]
    }


# ============================================================
# PROCESS COIN
# ============================================================

def process_coin(
    name,
    symbol
):

    result = {
        "coin": name,
        "trend": "ERROR",
        "hidden": None,
        "regular": None,
        "direction": None,
        "setup": None,
        "status": "ERROR",
        "error": None
    }


    # --------------------------------------------------------
    # 1H
    # --------------------------------------------------------

    df1h = get_candles(
        symbol,
        "1h",
        300
    )


    if (
        df1h is None
        or
        len(df1h) < 210
    ):

        result["status"] = (
            "1H_DATA_ERROR"
        )

        return result


    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    trend = determine_1h_trend(
        df1h
    )

    result["trend"] = trend


    if trend == "NEUTRAL":

        result["status"] = (
            "NEUTRAL_TREND"
        )

        return result


    # --------------------------------------------------------
    # HIDDEN
    # --------------------------------------------------------

    hidden = get_valid_hidden(
        df1h,
        trend
    )


    if hidden is None:

        result["status"] = (
            "NO_VALID_HIDDEN"
        )

        return result


    result["hidden"] = hidden


    # --------------------------------------------------------
    # 1M
    # --------------------------------------------------------

    df1m = get_candles(
        symbol,
        "1m",
        500
    )


    if (
        df1m is None
        or
        len(df1m) < 50
    ):

        result["status"] = (
            "1M_DATA_ERROR"
        )

        return result


    hidden_time = hidden[
        "time2"
    ]


    now = int(
        time.time()
    )


    # --------------------------------------------------------
    # REGULAR
    # --------------------------------------------------------

    if trend == "BULLISH":

        direction = "LONG"

        regulars = find_regular_bullish(
            df1m,
            hidden_time
        )

    else:

        direction = "SHORT"

        regulars = find_regular_bearish(
            df1m,
            hidden_time
        )


    result["direction"] = direction


    valid_regulars = []


    for regular in regulars:

        age_minutes = (
            now
            -
            regular["time2"]
        ) / 60


        if (
            0
            <= age_minutes
            <= MAX_REGULAR_AGE_MINUTES
        ):

            regular[
                "age_minutes"
            ] = age_minutes

            valid_regulars.append(
                regular
            )


    if not valid_regulars:

        result["status"] = (
            "WAITING_1M"
        )

        return result


    valid_regulars.sort(
        key=lambda x: x["time2"],
        reverse=True
    )


    regular = valid_regulars[0]

    result["regular"] = regular


    # --------------------------------------------------------
    # SETUP
    # --------------------------------------------------------

    if direction == "LONG":

        setup = build_long_setup(
            df1m
        )

    else:

        setup = build_short_setup(
            df1m
        )


    if setup is None:

        result["status"] = (
            "SETUP_ERROR"
        )

        return result


    result["setup"] = setup

    result["status"] = (
        "VALID_SIGNAL"
    )


    return result


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(value):

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

def make_signal_key(result):

    hidden = result["hidden"]
    regular = result["regular"]

    return (
        f'{result["coin"]}_'
        f'{result["direction"]}_'
        f'{hidden["time2"]}_'
        f'{regular["time2"]}'
    )


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def build_signal_message(result):

    coin = result["coin"]
    trend = result["trend"]
    direction = result["direction"]

    hidden = result["hidden"]
    regular = result["regular"]
    setup = result["setup"]


    emoji = (
        "🟢"
        if direction == "LONG"
        else
        "🔴"
    )


    return f"""
🚨 <b>NEW DIVERGENCE SIGNAL</b>
━━━━━━━━━━━━━━━━━━

<b>#{coin}/USDT</b>

{emoji} <b>{direction}</b>

📈 1H Trend:
<b>{trend}</b>

━━━━━━━━━━━━━━━━━━

📌 1H Hidden:
<b>{hidden["kind"]}</b>

Age:
{hidden["age_hours"]:.1f} hours

📌 1M Regular:
<b>{regular["kind"]}</b>

Age:
{regular["age_minutes"]:.1f} minutes

━━━━━━━━━━━━━━━━━━

💰 Entry:
<b>{fmt_price(setup["entry"])}</b>

🛑 Stop Loss:
<b>{fmt_price(setup["sl"])}</b>

🎯 Take Profit:
<b>{fmt_price(setup["tp"])}</b>

📐 Risk:
{fmt_price(setup["risk"])}

⚖️ R:R:
<b>1 : 1</b>

━━━━━━━━━━━━━━━━━━

✅ 1H Trend
✅ Hidden 1H
✅ Regular 1M
✅ Same direction
✅ 1M AFTER 1H
✅ Closed candles only

━━━━━━━━━━━━━━━━━━
""".strip()


# ============================================================
# REPORT
# ============================================================

def build_report(
    results,
    scan_number,
    elapsed
):

    now = datetime.now(
        timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


    bullish = [
        r for r in results
        if r["trend"] == "BULLISH"
    ]

    bearish = [
        r for r in results
        if r["trend"] == "BEARISH"
    ]

    neutral = [
        r for r in results
        if r["trend"] == "NEUTRAL"
    ]

    hidden = [
        r for r in results
        if r["hidden"] is not None
    ]

    waiting = [
        r for r in results
        if r["status"] == "WAITING_1M"
    ]

    valid = [
        r for r in results
        if r["status"] == "VALID_SIGNAL"
    ]

    errors = [
        r for r in results
        if "ERROR" in r["status"]
    ]


    msg = f"""
<b>📊 DIVERGENCE SCANNER v7.2</b>
━━━━━━━━━━━━━━━━━━

🕐 UTC:
{now}

🔢 Scan:
#{scan_number}

🪙 Coins:
<b>{len(results)}/30</b>

━━━━━━━━━━━━━━━━━━

📈 1H BULLISH:
<b>{len(bullish)}</b>

📉 1H BEARISH:
<b>{len(bearish)}</b>

⚪ 1H NEUTRAL:
<b>{len(neutral)}</b>

━━━━━━━━━━━━━━━━━━

🟡 Valid Hidden 1H:
<b>{len(hidden)}</b>

⏳ Waiting for Regular 1M:
<b>{len(waiting)}</b>

🚨 Active Signals:
<b>{len(valid)}</b>

❌ Data Errors:
<b>{len(errors)}</b>

━━━━━━━━━━━━━━━━━━

<b>STRATEGY</b>

🟢 Bullish 1H
→ Hidden Bearish 1H
→ Regular Bullish 1M
→ LONG

🔴 Bearish 1H
→ Hidden Bullish 1H
→ Regular Bearish 1M
→ SHORT

━━━━━━━━━━━━━━━━━━

🛑 SL:
Last Swing ± {SL_BUFFER_PERCENT}%

🎯 TP:
1 × Risk

⚖️ R:R:
<b>1 : 1</b>

━━━━━━━━━━━━━━━━━━

⚡ Scan:
{elapsed:.2f}s

🤖 GitHub Actions
""".strip()


    if waiting:

        msg += (
            "\n\n⏳ "
            "<b>WAITING FOR 1M</b>\n"
        )

        for r in waiting:

            msg += (
                f'• {r["coin"]} '
                f'→ {r["direction"]}\n'
            )


    if valid:

        msg += (
            "\n\n🚨 "
            "<b>ACTIVE SIGNALS</b>\n"
        )

        for r in valid:

            emoji = (
                "🟢"
                if r["direction"] == "LONG"
                else
                "🔴"
            )

            msg += (
                f'{emoji} '
                f'<b>{r["coin"]}</b> '
                f'{r["direction"]}\n'
            )

    else:

        msg += (
            "\n\n⚪ "
            "<b>NO ACTIVE SIGNAL</b>"
        )


    return msg


# ============================================================
# RUN SCAN
# ============================================================

def run_scan():

    global scan_number

    scan_number += 1

    start = time.time()

    results = []


    print()
    print(
        "=============================================="
    )

    print(
        f"STARTING SCAN #{scan_number}"
    )

    print(
        "=============================================="
    )


    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:


        futures = {

            executor.submit(
                process_coin,
                name,
                symbol
            ): name

            for name, symbol
            in SYMBOLS.items()
        }


        for future in as_completed(
            futures
        ):

            name = futures[
                future
            ]


            try:

                result = future.result()

                results.append(
                    result
                )

                print(
                    f"{name}: "
                    f"{result['status']} "
                    f"| "
                    f"{result['trend']}"
                )


            except Exception as e:

                print(
                    f"{name}: ERROR {e}"
                )

                results.append({

                    "coin": name,
                    "trend": "ERROR",
                    "hidden": None,
                    "regular": None,
                    "direction": None,
                    "setup": None,
                    "status": "ERROR",
                    "error": str(e)
                })


    order = {
        coin: index
        for index, coin
        in enumerate(
            SYMBOLS.keys()
        )
    }


    results.sort(
        key=lambda x:
        order.get(
            x["coin"],
            999
        )
    )


    elapsed = (
        time.time()
        -
        start
    )


    # ========================================================
    # STATE
    # ========================================================

    state = load_state()

    new_signals = []


    for result in results:

        if (
            result["status"]
            !=
            "VALID_SIGNAL"
        ):
            continue


        key = make_signal_key(
            result
        )


        if key not in state:

            state[key] = {

                "created":
                int(time.time()),

                "coin":
                result["coin"],

                "direction":
                result["direction"]
            }


            new_signals.append(
                result
            )


    save_state(
        state
    )


    # ========================================================
    # NEW SIGNALS
    # ========================================================

    for result in new_signals:

        message = (
            build_signal_message(
                result
            )
        )

        sent = send_telegram(
            message
        )

        print(
            "NEW SIGNAL:",
            result["coin"],
            result["direction"],
            "Telegram:",
            sent
        )


    print()

    print(
        f"SCAN #{scan_number} "
        f"FINISHED IN "
        f"{elapsed:.2f}s"
    )

    print(
        f"New signals: "
        f"{len(new_signals)}"
    )


    return {
        "results": results,
        "new_signals": new_signals,
        "elapsed": elapsed
    }


# ============================================================
# REPORT TIME
# ============================================================

def should_send_report():

    now = datetime.now(
        timezone.utc
    )


    # Manual workflow
    if os.getenv(
        "GITHUB_EVENT_NAME"
    ) == "workflow_dispatch":

        return True


    # Every 5 minutes
    if now.minute % 5 == 0:

        return True


    return False


# ============================================================
# MAIN
# ============================================================

def main():

    print()

    print(
        "================================================"
    )

    print(
        " CRYPTO DIVERGENCE SCANNER v7.2"
    )

    print(
        " GITHUB ACTIONS MODE"
    )

    print(
        "================================================"
    )


    print(
        f"Coins: {len(SYMBOLS)}"
    )

    print(
        "Execution: ONE SCAN"
    )

    print(
        "Signal detection: 1M"
    )

    print(
        "Normal report: EVERY 5 MINUTES"
    )

    print(
        "Strategy:"
        " 1H Trend -> Hidden -> 1M Regular"
    )

    print(
        "ATR: DISABLED"
    )

    print(
        "Telegram Buttons: DISABLED"
    )

    print()


    # --------------------------------------------------------
    # TELEGRAM CONFIG CHECK
    # --------------------------------------------------------

    if TELEGRAM_BOT_TOKEN:

        print(
            "Telegram token: CONFIGURED"
        )

    else:

        print(
            "Telegram token: MISSING"
        )


    if TELEGRAM_CHAT_ID:

        print(
            "Telegram chat ID: CONFIGURED"
        )

    else:

        print(
            "Telegram chat ID: MISSING"
        )


    print()


    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    result = run_scan()


    if result is None:

        print(
            "Scan failed."
        )

        return


    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    if should_send_report():

        print(
            "5-minute/manual report time."
        )


        report = build_report(
            result["results"],
            scan_number,
            result["elapsed"]
        )


        send_telegram(
            report
        )


    else:

        print(
            "No 5-minute report "
            "this execution."
        )


    print()

    print(
        "GitHub Actions scan finished."
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
