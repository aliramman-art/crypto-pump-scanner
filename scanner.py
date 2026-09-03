# ============================================================
# CRYPTO DIVERGENCE SCANNER v1.0
# ============================================================
# Kraken Futures
#
# 30 IMPORTANT COINS
#
# STEP 1:
# Hidden Divergence on 1H
#
# STEP 2:
# Only coins with Hidden Divergence
# are checked for Regular Divergence on 1M
#
# STEP 3:
# ATR BASED SETUP
#
# LONG:
#   Hidden Bullish 1H
#   +
#   Regular Bullish 1M
#
# SHORT:
#   Hidden Bearish 1H
#   +
#   Regular Bearish 1M
#
# ATR:
#   Period = 14
#
# SL  = 1.5 ATR
# TP1 = 1.5 ATR
# TP2 = 3.0 ATR
# TP3 = 4.5 ATR
#
# CLOSED CANDLES ONLY
# PARALLEL SCANNING
# TELEGRAM
# DUPLICATE PROTECTION
# ============================================================

import os
import json
import time
import requests

from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# VERSION
# ============================================================

VERSION = "v1.0"


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

BASE_URL = (
    "https://futures.kraken.com/api/charts/v1"
)


# ============================================================
# STATE
# ============================================================

STATE_FILE = "divergence_state.json"


# ============================================================
# SETTINGS
# ============================================================

ATR_PERIOD = 14

SL_ATR_MULTIPLIER = 1.5

TP1_ATR_MULTIPLIER = 1.5
TP2_ATR_MULTIPLIER = 3.0
TP3_ATR_MULTIPLIER = 4.5


# ============================================================
# DIVERGENCE SETTINGS
# ============================================================

RSI_PERIOD = 14

PIVOT_WINDOW = 3

MIN_PIVOT_DISTANCE = 3

MAX_PIVOT_DISTANCE = 60


# ============================================================
# SCANNER SETTINGS
# ============================================================

MAX_WORKERS = 10

HOUR_CANDLES = 150

MINUTE_CANDLES = 300


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
# JSON
# ============================================================

def load_json(path, default):

    try:

        if not os.path.exists(path):
            return default

        with open(
            path,
            "r",
            encoding="utf-8"
        ) as f:

            return json.load(f)

    except Exception:

        return default


def save_json(path, data):

    try:

        with open(
            path,
            "w",
            encoding="utf-8"
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False
            )

    except Exception as e:

        print(
            "STATE SAVE ERROR:",
            e
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print(
            "\n" + text + "\n"
        )

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {

        "chat_id": TELEGRAM_CHAT_ID,

        "text": text,

        "parse_mode": "HTML",

        "disable_web_page_preview": True,

    }

    try:

        response = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if response.status_code != 200:

            print(
                "TELEGRAM ERROR:",
                response.status_code,
                response.text[:300]
            )

            return False

        print(
            "Telegram: MESSAGE SENT"
        )

        return True

    except Exception as e:

        print(
            "TELEGRAM EXCEPTION:",
            e
        )

        return False


# ============================================================
# CANDLE PARSER
# ============================================================

def parse_candle(item):

    try:

        if isinstance(item, dict):

            t = (
                item.get("time")
                or item.get("timestamp")
                or item.get("ts")
            )

            o = item.get("open")
            h = item.get("high")
            l = item.get("low")
            c = item.get("close")

            v = (
                item.get("volume")
                or item.get("vol")
                or 0
            )

            if None in (
                t,
                o,
                h,
                l,
                c
            ):

                return None

            t = float(t)

            if t > 10_000_000_000:
                t /= 1000

            return {

                "time": int(t),

                "open": float(o),

                "high": float(h),

                "low": float(l),

                "close": float(c),

                "volume": float(v),

            }

        if isinstance(
            item,
            (list, tuple)
        ):

            if len(item) < 5:
                return None

            t = float(item[0])

            if t > 10_000_000_000:
                t /= 1000

            return {

                "time": int(t),

                "open": float(item[1]),

                "high": float(item[2]),

                "low": float(item[3]),

                "close": float(item[4]),

                "volume": (
                    float(item[5])
                    if len(item) > 5
                    else 0.0
                ),

            }

    except Exception:

        return None

    return None


# ============================================================
# EXTRACT CANDLES
# ============================================================

def extract_candles(data):

    candidates = []

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "result",
            "results",
            "ohlcv",
        ):

            value = data.get(key)

            if isinstance(
                value,
                list
            ):

                candidates.extend(value)

            elif isinstance(
                value,
                dict
            ):

                for nested_key in (
                    "candles",
                    "data",
                    "result",
                    "results",
                    "ohlcv",
                ):

                    nested = value.get(
                        nested_key
                    )

                    if isinstance(
                        nested,
                        list
                    ):

                        candidates.extend(
                            nested
                        )

        if not candidates:

            for value in data.values():

                if (
                    isinstance(value, list)
                    and value
                    and isinstance(
                        value[0],
                        (dict, list, tuple)
                    )
                ):

                    candidates.extend(
                        value
                    )

    elif isinstance(data, list):

        candidates = data

    parsed = []

    for item in candidates:

        candle = parse_candle(item)

        if candle:

            parsed.append(candle)

    return parsed


# ============================================================
# GET CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit
):

    url = (
        f"{BASE_URL}/trade/"
        f"{symbol}/{resolution}"
    )

    try:

        response = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent":
                f"crypto-divergence-scanner/{VERSION}"
            }
        )

        if response.status_code != 200:

            print(
                f"{symbol} {resolution} HTTP:",
                response.status_code
            )

            return []

        try:

            data = response.json()

        except Exception:

            print(
                f"{symbol} {resolution}: JSON ERROR"
            )

            return []

        candles = extract_candles(data)

        if not candles:

            print(
                f"{symbol} {resolution}: "
                f"NO CANDLES"
            )

            return []

        candles.sort(
            key=lambda x: x["time"]
        )

        unique = {}

        for candle in candles:

            unique[
                candle["time"]
            ] = candle

        candles = list(
            unique.values()
        )

        candles.sort(
            key=lambda x: x["time"]
        )

        # ----------------------------------------------------
        # REMOVE CURRENT OPEN CANDLE
        # ----------------------------------------------------

        if resolution == "1m":

            bucket_seconds = 60

        elif resolution == "5m":

            bucket_seconds = 300

        elif resolution == "15m":

            bucket_seconds = 900

        elif resolution == "30m":

            bucket_seconds = 1800

        elif resolution == "1h":

            bucket_seconds = 3600

        else:

            bucket_seconds = 60

        current_bucket = (
            int(time.time() // bucket_seconds)
            * bucket_seconds
        )

        candles = [
            c
            for c in candles
            if c["time"] < current_bucket
        ]

        candles = candles[-limit:]

        return candles

    except requests.exceptions.Timeout:

        print(
            f"{symbol} {resolution}: TIMEOUT"
        )

        return []

    except requests.exceptions.RequestException as e:

        print(
            f"{symbol} {resolution}: REQUEST ERROR",
            e
        )

        return []

    except Exception as e:

        print(
            f"{symbol} {resolution}: ERROR",
            e
        )

        return []


# ============================================================
# RSI SERIES
# ============================================================

def calculate_rsi_series(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return []

    closes = [
        c["close"]
        for c in candles
    ]

    rsi = [
        None
    ] * len(closes)

    gains = []
    losses = []

    for i in range(
        1,
        len(closes)
    ):

        diff = (
            closes[i]
            - closes[i - 1]
        )

        gains.append(
            max(diff, 0)
        )

        losses.append(
            max(-diff, 0)
        )

    if len(gains) < period:

        return rsi

    avg_gain = (
        sum(
            gains[:period]
        )
        / period
    )

    avg_loss = (
        sum(
            losses[:period]
        )
        / period
    )

    index = period

    if avg_loss == 0:

        rsi[index] = 100.0

    else:

        rs = (
            avg_gain
            / avg_loss
        )

        rsi[index] = (
            100
            - (
                100
                / (1 + rs)
            )
        )

    for i in range(
        period + 1,
        len(closes)
    ):

        avg_gain = (
            (
                avg_gain
                * (period - 1)
            )
            + gains[i - 1]
        ) / period

        avg_loss = (
            (
                avg_loss
                * (period - 1)
            )
            + losses[i - 1]
        ) / period

        if avg_loss == 0:

            rsi[i] = 100.0

        else:

            rs = (
                avg_gain
                / avg_loss
            )

            rsi[i] = (
                100
                - (
                    100
                    / (1 + rs)
                )
            )

    return rsi


# ============================================================
# PIVOT LOWS
# ============================================================

def find_pivot_lows(
    candles,
    window=3
):

    pivots = []

    if len(candles) < (
        window * 2 + 1
    ):

        return pivots

    for i in range(
        window,
        len(candles) - window
    ):

        value = candles[i]["low"]

        is_pivot = True

        for j in range(
            i - window,
            i + window + 1
        ):

            if j == i:
                continue

            if candles[j]["low"] < value:

                is_pivot = False

                break

        if is_pivot:

            pivots.append(i)

    return pivots


# ============================================================
# PIVOT HIGHS
# ============================================================

def find_pivot_highs(
    candles,
    window=3
):

    pivots = []

    if len(candles) < (
        window * 2 + 1
    ):

        return pivots

    for i in range(
        window,
        len(candles) - window
    ):

        value = candles[i]["high"]

        is_pivot = True

        for j in range(
            i - window,
            i + window + 1
        ):

            if j == i:
                continue

            if candles[j]["high"] > value:

                is_pivot = False

                break

        if is_pivot:

            pivots.append(i)

    return pivots


# ============================================================
# HIDDEN DIVERGENCE 1H
# ============================================================

def find_hidden_divergence(
    candles,
    rsi_period=14
):

    if len(candles) < 80:

        return None

    rsi = calculate_rsi_series(
        candles,
        rsi_period
    )

    if not rsi:

        return None

    lows = find_pivot_lows(
        candles,
        PIVOT_WINDOW
    )

    highs = find_pivot_highs(
        candles,
        PIVOT_WINDOW
    )

    # --------------------------------------------------------
    # HIDDEN BULLISH
    #
    # Price:
    # Higher Low
    #
    # RSI:
    # Lower Low
    # --------------------------------------------------------

    valid_lows = [
        i
        for i in lows
        if rsi[i] is not None
    ]

    if len(valid_lows) >= 2:

        i1 = valid_lows[-2]
        i2 = valid_lows[-1]

        distance = (
            i2 - i1
        )

        if (
            MIN_PIVOT_DISTANCE
            <= distance
            <= MAX_PIVOT_DISTANCE
        ):

            price1 = candles[i1]["low"]
            price2 = candles[i2]["low"]

            rsi1 = rsi[i1]
            rsi2 = rsi[i2]

            if (
                price2 > price1
                and rsi2 < rsi1
            ):

                return {

                    "type": "HIDDEN_BULLISH",

                    "direction": "LONG",

                    "pivot1": i1,

                    "pivot2": i2,

                    "price1": price1,

                    "price2": price2,

                    "rsi1": rsi1,

                    "rsi2": rsi2,

                    "time1": candles[i1]["time"],

                    "time2": candles[i2]["time"],

                }

    # --------------------------------------------------------
    # HIDDEN BEARISH
    #
    # Price:
    # Lower High
    #
    # RSI:
    # Higher High
    # --------------------------------------------------------

    valid_highs = [
        i
        for i in highs
        if rsi[i] is not None
    ]

    if len(valid_highs) >= 2:

        i1 = valid_highs[-2]
        i2 = valid_highs[-1]

        distance = (
            i2 - i1
        )

        if (
            MIN_PIVOT_DISTANCE
            <= distance
            <= MAX_PIVOT_DISTANCE
        ):

            price1 = candles[i1]["high"]
            price2 = candles[i2]["high"]

            rsi1 = rsi[i1]
            rsi2 = rsi[i2]

            if (
                price2 < price1
                and rsi2 > rsi1
            ):

                return {

                    "type": "HIDDEN_BEARISH",

                    "direction": "SHORT",

                    "pivot1": i1,

                    "pivot2": i2,

                    "price1": price1,

                    "price2": price2,

                    "rsi1": rsi1,

                    "rsi2": rsi2,

                    "time1": candles[i1]["time"],

                    "time2": candles[i2]["time"],

                }

    return None


# ============================================================
# REGULAR DIVERGENCE 1M
# ============================================================

def find_regular_divergence(
    candles,
    expected_direction,
    rsi_period=14
):

    if len(candles) < 100:

        return None

    rsi = calculate_rsi_series(
        candles,
        rsi_period
    )

    if not rsi:

        return None

    lows = find_pivot_lows(
        candles,
        PIVOT_WINDOW
    )

    highs = find_pivot_highs(
        candles,
        PIVOT_WINDOW
    )

    # --------------------------------------------------------
    # REGULAR BULLISH
    #
    # Price:
    # Lower Low
    #
    # RSI:
    # Higher Low
    # --------------------------------------------------------

    if expected_direction == "LONG":

        valid_lows = [
            i
            for i in lows
            if rsi[i] is not None
        ]

        if len(valid_lows) >= 2:

            i1 = valid_lows[-2]
            i2 = valid_lows[-1]

            distance = (
                i2 - i1
            )

            if (
                MIN_PIVOT_DISTANCE
                <= distance
                <= MAX_PIVOT_DISTANCE
            ):

                price1 = candles[i1]["low"]
                price2 = candles[i2]["low"]

                rsi1 = rsi[i1]
                rsi2 = rsi[i2]

                if (
                    price2 < price1
                    and rsi2 > rsi1
                ):

                    return {

                        "type": "REGULAR_BULLISH",

                        "direction": "LONG",

                        "pivot1": i1,

                        "pivot2": i2,

                        "price1": price1,

                        "price2": price2,

                        "rsi1": rsi1,

                        "rsi2": rsi2,

                        "time1": candles[i1]["time"],

                        "time2": candles[i2]["time"],

                    }

    # --------------------------------------------------------
    # REGULAR BEARISH
    #
    # Price:
    # Higher High
    #
    # RSI:
    # Lower High
    # --------------------------------------------------------

    if expected_direction == "SHORT":

        valid_highs = [
            i
            for i in highs
            if rsi[i] is not None
        ]

        if len(valid_highs) >= 2:

            i1 = valid_highs[-2]
            i2 = valid_highs[-1]

            distance = (
                i2 - i1
            )

            if (
                MIN_PIVOT_DISTANCE
                <= distance
                <= MAX_PIVOT_DISTANCE
            ):

                price1 = candles[i1]["high"]
                price2 = candles[i2]["high"]

                rsi1 = rsi[i1]
                rsi2 = rsi[i2]

                if (
                    price2 > price1
                    and rsi2 < rsi1
                ):

                    return {

                        "type": "REGULAR_BEARISH",

                        "direction": "SHORT",

                        "pivot1": i1,

                        "pivot2": i2,

                        "price1": price1,

                        "price2": price2,

                        "rsi1": rsi1,

                        "rsi2": rsi2,

                        "time1": candles[i1]["time"],

                        "time2": candles[i2]["time"],

                    }

    return None


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:

        return 0.0

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]

        low = candles[i]["low"]

        prev_close = candles[
            i - 1
        ]["close"]

        tr = max(

            high - low,

            abs(
                high - prev_close
            ),

            abs(
                low - prev_close
            )

        )

        trs.append(tr)

    if len(trs) < period:

        return 0.0

    return (
        sum(
            trs[-period:]
        )
        / period
    )


# ============================================================
# ATR LEVELS
# ============================================================

def calculate_levels(
    candles,
    direction
):

    entry = candles[-1]["close"]

    atr = calculate_atr(
        candles,
        ATR_PERIOD
    )

    if atr <= 0:

        return None

    risk = (
        atr
        * SL_ATR_MULTIPLIER
    )

    if direction == "LONG":

        sl = (
            entry
            - risk
        )

        tp1 = (
            entry
            + atr
            * TP1_ATR_MULTIPLIER
        )

        tp2 = (
            entry
            + atr
            * TP2_ATR_MULTIPLIER
        )

        tp3 = (
            entry
            + atr
            * TP3_ATR_MULTIPLIER
        )

    else:

        sl = (
            entry
            + risk
        )

        tp1 = (
            entry
            - atr
            * TP1_ATR_MULTIPLIER
        )

        tp2 = (
            entry
            - atr
            * TP2_ATR_MULTIPLIER
        )

        tp3 = (
            entry
            - atr
            * TP3_ATR_MULTIPLIER
        )

    return {

        "entry": entry,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "atr": atr,

        "risk": risk,

    }


# ============================================================
# PRICE FORMAT
# ============================================================

def fmt_price(price):

    price = float(price)

    if price >= 1000:

        return f"{price:.2f}"

    if price >= 100:

        return f"{price:.3f}"

    if price >= 1:

        return f"{price:.4f}"

    if price >= 0.1:

        return f"{price:.5f}"

    if price >= 0.01:

        return f"{price:.6f}"

    return f"{price:.8f}"


# ============================================================
# PERCENT
# ============================================================

def pct_move(
    entry,
    target,
    direction
):

    if entry <= 0:

        return 0.0

    if direction == "LONG":

        return (
            (target - entry)
            / entry
        ) * 100

    return (
        (entry - target)
        / entry
    ) * 100


# ============================================================
# SIGNAL ID
# ============================================================

def signal_id(
    symbol,
    hidden,
    regular
):

    return (
        f"{symbol}_"
        f"{hidden['type']}_"
        f"{hidden['time2']}_"
        f"{regular['type']}_"
        f"{regular['time2']}"
    )


# ============================================================
# SIGNAL MESSAGE
# ============================================================

def create_signal_message(
    symbol,
    hidden,
    regular,
    levels
):

    direction = (
        levels.get(
            "direction"
        )
    )

    if direction == "LONG":

        title = "🟢 DIVERGENCE LONG SETUP"

    else:

        title = "🔴 DIVERGENCE SHORT SETUP"

    entry = levels["entry"]

    sl = levels["sl"]

    tp1 = levels["tp1"]

    tp2 = levels["tp2"]

    tp3 = levels["tp3"]

    sl_pct = pct_move(
        entry,
        sl,
        direction
    )

    tp1_pct = pct_move(
        entry,
        tp1,
        direction
    )

    tp2_pct = pct_move(
        entry,
        tp2,
        direction
    )

    tp3_pct = pct_move(
        entry,
        tp3,
        direction
    )

    return f"""
🚨 <b>{title}</b>

━━━━━━━━━━━━━━━━━━━━

<b>{symbol}/USDT</b>

📌 <b>Direction:</b>
{direction}


🕐 <b>1H Hidden Divergence</b>

{hidden["type"]}

Price:
{fmt_price(hidden["price1"])}
→
{fmt_price(hidden["price2"])}

RSI:
{hidden["rsi1"]:.1f}
→
{hidden["rsi2"]:.1f}


⚡ <b>1M Regular Divergence</b>

{regular["type"]}

Price:
{fmt_price(regular["price1"])}
→
{fmt_price(regular["price2"])}

RSI:
{regular["rsi1"]:.1f}
→
{regular["rsi2"]:.1f}


━━━━━━━━━━━━━━━━━━━━

💰 <b>ENTRY</b>

{fmt_price(entry)}


🛑 <b>STOP LOSS</b>

{fmt_price(sl)}

{sl_pct:+.2f}%


🎯 <b>TP1</b>

{fmt_price(tp1)}

{tp1_pct:+.2f}%
📐 1R


🎯 <b>TP2</b>

{fmt_price(tp2)}

{tp2_pct:+.2f}%
📐 2R


🚀 <b>TP3</b>

{fmt_price(tp3)}

{tp3_pct:+.2f}%
📐 3R


━━━━━━━━━━━━━━━━━━━━

📊 <b>ATR 1M</b>

{fmt_price(levels["atr"])}


📐 <b>Risk</b>

{fmt_price(levels["risk"])}


🕐 <b>Signal Candle</b>

1M CLOSED


🤖 <b>Divergence Engine {VERSION}</b>
""".strip()


# ============================================================
# SCAN ONE SYMBOL 1H
# ============================================================

def scan_1h(
    symbol,
    kraken_symbol
):

    candles = get_candles(
        kraken_symbol,
        "1h",
        HOUR_CANDLES
    )

    if len(candles) < 80:

        return {

            "symbol": symbol,

            "status": "NO_DATA",

            "hidden": None,

        }

    hidden = find_hidden_divergence(
        candles
    )

    return {

        "symbol": symbol,

        "status": "OK",

        "hidden": hidden,

        "candles": candles,

    }


# ============================================================
# SCAN ONE SYMBOL 1M
# ============================================================

def scan_1m(
    symbol,
    kraken_symbol,
    hidden
):

    candles = get_candles(
        kraken_symbol,
        "1m",
        MINUTE_CANDLES
    )

    if len(candles) < 100:

        return {

            "symbol": symbol,

            "status": "NO_DATA",

            "regular": None,

        }

    regular = find_regular_divergence(
        candles,
        hidden["direction"]
    )

    return {

        "symbol": symbol,

        "status": "OK",

        "hidden": hidden,

        "regular": regular,

        "candles": candles,

    }


# ============================================================
# DUPLICATE PROTECTION
# ============================================================

def already_sent(
    sid
):

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(
        state,
        dict
    ):

        state = {}

    return sid in state.get(
        "signals",
        {}
    )


def save_signal(
    sid,
    data
):

    state = load_json(
        STATE_FILE,
        {}
    )

    if not isinstance(
        state,
        dict
    ):

        state = {}

    state.setdefault(
        "signals",
        {}
    )

    state["signals"][sid] = {

        "symbol": data["symbol"],

        "direction": data["direction"],

        "hidden_type": data["hidden"]["type"],

        "regular_type": data["regular"]["type"],

        "entry": data["levels"]["entry"],

        "sl": data["levels"]["sl"],

        "tp1": data["levels"]["tp1"],

        "tp2": data["levels"]["tp2"],

        "tp3": data["levels"]["tp3"],

        "atr": data["levels"]["atr"],

        "time": int(
            time.time()
        ),

    }

    save_json(
        STATE_FILE,
        state
    )


# ============================================================
# SCAN 1H
# ============================================================

def scan_all_1h():

    print(
        "\n"
        + "=" * 60
    )

    print(
        "STEP 1/2"
    )

    print(
        "SCANNING HIDDEN DIVERGENCE 1H"
    )

    print(
        "=" * 60
    )

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {

            executor.submit(
                scan_1h,
                symbol,
                kraken_symbol
            ): symbol

            for symbol, kraken_symbol
            in COINS.items()

        }

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                result = future.result()

                results.append(
                    result
                )

                hidden = result.get(
                    "hidden"
                )

                if hidden:

                    print(
                        f"🔥 {symbol}: "
                        f"{hidden['type']}"
                    )

                else:

                    print(
                        f"⚪ {symbol}: "
                        f"No Hidden Divergence"
                    )

            except Exception as e:

                print(
                    f"{symbol}: "
                    f"SCAN ERROR:",
                    e
                )

    return results


# ============================================================
# SCAN 1M CANDIDATES
# ============================================================

def scan_hidden_candidates(
    hidden_results
):

    candidates = [
        item
        for item in hidden_results
        if item.get("hidden")
    ]

    print(
        "\n"
        + "=" * 60
    )

    print(
        "STEP 2/2"
    )

    print(
        f"1M REGULAR DIVERGENCE"
    )

    print(
        f"CANDIDATES: {len(candidates)}"
    )

    print(
        "=" * 60
    )

    if not candidates:

        return []

    results = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:

        futures = {

            executor.submit(
                scan_1m,
                item["symbol"],
                COINS[item["symbol"]],
                item["hidden"]
            ): item["symbol"]

            for item in candidates

        }

        for future in as_completed(
            futures
        ):

            symbol = futures[
                future
            ]

            try:

                result = future.result()

                results.append(
                    result
                )

                regular = result.get(
                    "regular"
                )

                if regular:

                    print(
                        f"🚨 {symbol}: "
                        f"{regular['type']}"
                    )

                else:

                    print(
                        f"⚪ {symbol}: "
                        f"No Regular Divergence"
                    )

            except Exception as e:

                print(
                    f"{symbol}: "
                    f"1M ERROR:",
                    e
                )

    return results


# ============================================================
# BUILD SIGNALS
# ============================================================

def build_signals(
    results
):

    signals = []

    for result in results:

        hidden = result.get(
            "hidden"
        )

        regular = result.get(
            "regular"
        )

        candles = result.get(
            "candles"
        )

        if not hidden:

            continue

        if not regular:

            continue

        if not candles:

            continue

        direction = hidden[
            "direction"
        ]

        if regular[
            "direction"
        ] != direction:

            continue

        levels = calculate_levels(
            candles,
            direction
        )

        if not levels:

            continue

        levels[
            "direction"
        ] = direction

        sid = signal_id(
            result["symbol"],
            hidden,
            regular
        )

        signals.append({

            "id": sid,

            "symbol": result["symbol"],

            "direction": direction,

            "hidden": hidden,

            "regular": regular,

            "levels": levels,

        })

    return signals


# ============================================================
# WATCHLIST MESSAGE
# ============================================================

def create_watchlist(
    hidden_results
):

    hidden_items = [
        item
        for item in hidden_results
        if item.get("hidden")
    ]

    lines = [

        "🔎 <b>DIVERGENCE SCANNER</b>",

        "━━━━━━━━━━━━━━━━━━━━",

        "🕐 <b>1H Hidden Divergence</b>",

        "",

    ]

    if not hidden_items:

        lines.append(
            "📭 No Hidden Divergence found."
        )

    else:

        for index, item in enumerate(
            hidden_items,
            1
        ):

            hidden = item[
                "hidden"
            ]

            if hidden[
                "direction"
            ] == "LONG":

                emoji = "🟢"

            else:

                emoji = "🔴"

            lines.append(

                f"{index}. {emoji} "
                f"<b>{item['symbol']}</b> "
                f"{hidden['type']}"

            )

    lines.extend([

        "",

        "━━━━━━━━━━━━━━━━━━━━",

        f"📊 Checked: {len(COINS)} coins",

        "🤖 Divergence Engine "
        f"{VERSION}",

    ])

    return "\n".join(
        lines
    )


# ============================================================
# MAIN SCAN
# ============================================================

def main():

    start_time = time.time()

    print(
        "\n"
        + "=" * 60
    )

    print(
        f"CRYPTO DIVERGENCE SCANNER {VERSION}"
    )

    print(
        "=" * 60
    )

    print(
        f"Coins: {len(COINS)}"
    )

    print(
        "1H: Hidden Divergence"
    )

    print(
        "1M: Regular Divergence"
    )

    print(
        "ATR: 14"
    )

    print(
        "SL: 1.5 ATR"
    )

    print(
        "TP1: 1.5 ATR"
    )

    print(
        "TP2: 3 ATR"
    )

    print(
        "TP3: 4.5 ATR"
    )

    print(
        "=" * 60
    )

    # ========================================================
    # STEP 1
    # ========================================================

    hidden_results = scan_all_1h()

    # ========================================================
    # TELEGRAM WATCHLIST
    # ========================================================

    watchlist = create_watchlist(
        hidden_results
    )

    print(
        "\n" + watchlist
    )

    send_telegram(
        watchlist
    )

    # ========================================================
    # STEP 2
    # ========================================================

    regular_results = scan_hidden_candidates(
        hidden_results
    )

    # ========================================================
    # BUILD FINAL SIGNALS
    # ========================================================

    signals = build_signals(
        regular_results
    )

    # ========================================================
    # FINAL RESULTS
    # ========================================================

    print(
        "\n"
        + "=" * 60
    )

    print(
        f"FINAL SIGNALS: {len(signals)}"
    )

    print(
        "=" * 60
    )

    if not signals:

        no_signal = """
📭 <b>NO COMPLETE SETUP</b>

━━━━━━━━━━━━━━━━━━━━

شرایط کامل نشد:

1️⃣ Hidden Divergence در 1H
2️⃣ Regular Divergence هم‌جهت در 1M
3️⃣ ATR Setup

━━━━━━━━━━━━━━━━━━━━
""".strip()

        print(
            no_signal
        )

        send_telegram(
            no_signal
        )

    else:

        for signal in signals:

            sid = signal[
                "id"
            ]

            symbol = signal[
                "symbol"
            ]

            if already_sent(
                sid
            ):

                print(
                    f"{symbol}: "
                    f"DUPLICATE SIGNAL"
                )

                continue

            message = create_signal_message(
                symbol,
                signal["hidden"],
                signal["regular"],
                signal["levels"]
            )

            print(
                "\n" + message
            )

            sent = send_telegram(
                message
            )

            if sent:

                save_signal(
                    sid,
                    signal
                )

    # ========================================================
    # SUMMARY
    # ========================================================

    elapsed = (
        time.time()
        - start_time
    )

    summary = f"""
📊 <b>SCAN SUMMARY</b>

━━━━━━━━━━━━━━━━━━━━

🔍 Coins scanned:
{len(COINS)}

🕐 1H Hidden:
{len([
    x for x in hidden_results
    if x.get("hidden")
])}

⚡ 1M Regular:
{len([
    x for x in regular_results
    if x.get("regular")
])}

🚨 Complete Setups:
{len(signals)}

⏱ Scan time:
{elapsed:.1f} sec

━━━━━━━━━━━━━━━━━━━━

🤖 Divergence Engine {VERSION}
""".strip()

    print(
        "\n" + summary
    )

    send_telegram(
        summary
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
