import os
import json
import time
from datetime import datetime, timezone

import requests


# =========================================================
# EARLY PUMP / DUMP ENGINE v5.3
# REAL CLOSED 5M TRADE OHLCV
# REAL VOLUME + BREAKOUT + ICHIMOKU
# CONFIRMATION + TRADE MANAGER
# DEFENSIVE STATE/PERFORMANCE
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

KRAKEN_CANDLE_URL = "https://futures.kraken.com/api/charts/v1"

TRADE_FILE = "trade_state.json"
SIGNAL_FILE = "signal_state.json"
PERFORMANCE_FILE = "performance.json"

MIN_SCORE = 75
MAX_ACTIVE_TRADES = 5

SL_PERCENT = 1.20

TP1_R = 1.0
TP2_R = 2.0
TP3_R = 3.0

TRAIL_AFTER_TP3 = 0.75

CONFIRMATIONS_REQUIRED = 2
VOLUME_MIN_SIGNAL = 1.5


# =========================================================
# COINS
# =========================================================

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


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(text):

    if not TELEGRAM_BOT_TOKEN:
        print("Telegram token missing.")
        return False

    if not TELEGRAM_CHAT_ID:
        print("Telegram chat id missing.")
        return False

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
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
            timeout=20,
        )

        response.raise_for_status()

        result = response.json()

        if result.get("ok"):
            print("Telegram sent.")
            return True

        print("Telegram rejected:", result)

    except Exception as e:
        print("Telegram error:", e)

    return False


# =========================================================
# JSON HELPERS
# =========================================================

def load_json(filename, default):

    if not os.path.exists(filename):
        return default

    try:

        with open(
            filename,
            "r",
            encoding="utf-8",
        ) as f:

            return json.load(f)

    except Exception as e:

        print(
            f"{filename} read error:",
            e,
        )

        return default


def save_json(filename, data):

    try:

        temp = filename + ".tmp"

        with open(
            temp,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                ensure_ascii=False,
                indent=2,
            )

        os.replace(
            temp,
            filename,
        )

    except Exception as e:

        print(
            f"{filename} save error:",
            e,
        )


# =========================================================
# STATE NORMALIZATION
# =========================================================

def normalize_trades(data):

    if not isinstance(data, dict):
        return {}

    result = {}

    for key, value in data.items():

        if not isinstance(value, dict):
            continue

        if value.get("symbol") is None:
            continue

        result[str(key)] = value

    return result


def normalize_confirmations(data):

    if not isinstance(data, dict):
        return {}

    result = {}

    for key, value in data.items():

        if not isinstance(value, dict):
            continue

        result[str(key)] = value

    return result


def normalize_performance(data):

    """
    Accept:

    []
    [{"id": ...}]

    or:

    {"trades": [...]}

    Old/broken strings are discarded.
    """

    if isinstance(data, dict):

        data = data.get(
            "trades",
            [],
        )

    if not isinstance(data, list):
        return []

    result = []

    for item in data:

        if isinstance(item, dict):
            result.append(item)

    return result


# =========================================================
# KRAKEN FUTURES TRADE CANDLES
# =========================================================

def get_kraken_candles(symbol):

    # IMPORTANT:
    # v5.2 used /spot/
    # v5.3 uses /trade/
    #
    # Kraken documentation supports:
    # spot / mark / trade
    #
    # trade candles provide actual traded volume.

    url = (
        f"{KRAKEN_CANDLE_URL}/trade/"
        f"{symbol}/5m"
    )

    try:

        response = requests.get(
            url,
            params={
                "count": 120,
            },
            headers={
                "User-Agent":
                    "early-pump-engine-v5.3"
            },
            timeout=30,
        )

        print(
            f"{symbol} HTTP:",
            response.status_code,
        )

        response.raise_for_status()

        data = response.json()

        candles = data.get(
            "candles",
            [],
        )

        if not candles:

            print(
                f"{symbol}: no candles"
            )

            return []

        result = []

        for candle in candles:

            try:

                if isinstance(
                    candle,
                    dict,
                ):

                    timestamp = candle.get(
                        "time",
                        candle.get(
                            "timestamp"
                        ),
                    )

                    open_price = candle.get(
                        "open"
                    )

                    high_price = candle.get(
                        "high"
                    )

                    low_price = candle.get(
                        "low"
                    )

                    close_price = candle.get(
                        "close"
                    )

                    volume_value = candle.get(
                        "volume"
                    )

                elif isinstance(
                    candle,
                    list,
                ):

                    if len(candle) < 6:
                        continue

                    timestamp = candle[0]
                    open_price = candle[1]
                    high_price = candle[2]
                    low_price = candle[3]
                    close_price = candle[4]
                    volume_value = candle[5]

                else:

                    continue

                if (
                    timestamp is None
                    or open_price is None
                    or high_price is None
                    or low_price is None
                    or close_price is None
                    or volume_value is None
                ):

                    continue

                timestamp = float(
                    timestamp
                )

                if timestamp > 10_000_000_000:
                    timestamp /= 1000

                volume_value = float(
                    volume_value
                )

                result.append(
                    {
                        "time": int(timestamp),

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

                        "volume": volume_value,
                    }
                )

            except Exception as e:

                print(
                    f"{symbol} parse error:",
                    e,
                )

        result.sort(
            key=lambda x: x["time"]
        )

        # -----------------------------------------------------
        # Remove unfinished 5M candle
        # -----------------------------------------------------

        current_bucket = (
            int(time.time() // 300)
            * 300
        )

        closed = []

        for candle in result:

            if candle["time"] < current_bucket:
                closed.append(candle)

        result = closed

        print(
            f"{symbol}: "
            f"{len(result)} closed candles"
        )

        if result:

            print(
                f"{symbol}: "
                f"close={result[-1]['close']} "
                f"volume={result[-1]['volume']}"
            )

        return result

    except Exception as e:

        print(
            f"{symbol} Kraken error:",
            e,
        )

        return []


# =========================================================
# ALL MARKET DATA
# =========================================================

def get_all_candles():

    market = {}

    for symbol, kraken_symbol in COINS.items():

        candles = get_kraken_candles(
            kraken_symbol
        )

        if candles:
            market[symbol] = candles

    return market


# =========================================================
# BASIC
# =========================================================

def closes(candles):

    return [
        float(x["close"])
        for x in candles
        if isinstance(x, dict)
        and "close" in x
    ]


# =========================================================
# EMA
# =========================================================

def calculate_ema(
    candles,
    period,
):

    prices = closes(candles)

    if len(prices) < period:
        return None

    multiplier = 2 / (
        period + 1
    )

    ema = (
        sum(prices[:period])
        / period
    )

    for price in prices[period:]:

        ema = (
            (
                price - ema
            )
            * multiplier
        ) + ema

    return ema


# =========================================================
# RSI
# =========================================================

def calculate_rsi(
    candles,
    period=14,
):

    prices = closes(candles)

    if len(prices) < period + 1:
        return None

    changes = []

    for i in range(
        1,
        len(prices),
    ):

        changes.append(
            prices[i]
            - prices[i - 1]
        )

    recent = changes[-period:]

    gains = [
        x if x > 0 else 0
        for x in recent
    ]

    losses = [
        abs(x) if x < 0 else 0
        for x in recent
    ]

    avg_gain = (
        sum(gains)
        / period
    )

    avg_loss = (
        sum(losses)
        / period
    )

    if avg_loss == 0:

        if avg_gain == 0:
            return 50.0

        return 100.0

    rs = (
        avg_gain
        / avg_loss
    )

    return (
        100
        - (
            100
            / (1 + rs)
        )
    )


# =========================================================
# PRICE CHANGE
# =========================================================

def candle_change(
    candles,
    minutes,
):

    required = minutes // 5

    if len(candles) <= required:
        return None

    current = float(
        candles[-1]["close"]
    )

    previous = float(
        candles[
            -(required + 1)
        ]["close"]
    )

    if previous <= 0:
        return None

    return (
        (
            current - previous
        )
        / previous
    ) * 100


# =========================================================
# VOLUME RATIO
# =========================================================

def volume_ratio(
    candles,
    lookback=10,
):

    if len(candles) < lookback + 2:
        return None

    try:

        current = float(
            candles[-1]["volume"]
        )

    except Exception:

        return None

    if current <= 0:
        return None

    previous_volumes = []

    for candle in candles[
        -lookback - 1:-1
    ]:

        try:

            volume = float(
                candle.get(
                    "volume",
                    0,
                )
            )

            if volume > 0:
                previous_volumes.append(
                    volume
                )

        except Exception:
            continue

    if len(previous_volumes) < 5:
        return None

    average = (
        sum(previous_volumes)
        / len(previous_volumes)
    )

    if average <= 0:
        return None

    return current / average


# =========================================================
# BREAKOUT / BREAKDOWN
# =========================================================

def breakout_data(candles):

    if len(candles) < 7:

        return {
            "breakout": False,
            "breakdown": False,
            "high": None,
            "low": None,
        }

    current = float(
        candles[-1]["close"]
    )

    previous = candles[-7:-1]

    high = max(
        float(x["high"])
        for x in previous
    )

    low = min(
        float(x["low"])
        for x in previous
    )

    return {
        "breakout":
            current >
            high * 1.0005,

        "breakdown":
            current <
            low * 0.9995,

        "high": high,
        "low": low,
    }


# =========================================================
# ICHIMOKU
# =========================================================

def ichimoku(candles):

    if len(candles) < 52:

        return {
            "ready": False,
            "bullish": False,
            "bearish": False,
        }

    tenkan_data = candles[-9:]
    kijun_data = candles[-26:]
    spanb_data = candles[-52:]

    tenkan = (
        max(
            x["high"]
            for x in tenkan_data
        )
        +
        min(
            x["low"]
            for x in tenkan_data
        )
    ) / 2

    kijun = (
        max(
            x["high"]
            for x in kijun_data
        )
        +
        min(
            x["low"]
            for x in kijun_data
        )
    ) / 2

    span_a = (
        tenkan + kijun
    ) / 2

    span_b = (
        max(
            x["high"]
            for x in spanb_data
        )
        +
        min(
            x["low"]
            for x in spanb_data
        )
    ) / 2

    price = float(
        candles[-1]["close"]
    )

    cloud_top = max(
        span_a,
        span_b,
    )

    cloud_bottom = min(
        span_a,
        span_b,
    )

    return {
        "ready": True,

        "bullish":
            price > cloud_top
            and tenkan > kijun,

        "bearish":
            price < cloud_bottom
            and tenkan < kijun,

        "tenkan": tenkan,
        "kijun": kijun,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
    }


# =========================================================
# BTC REGIME
# =========================================================

def btc_regime(btc_candles):

    if not btc_candles:
        return "UNKNOWN"

    c15 = candle_change(
        btc_candles,
        15,
    )

    c60 = candle_change(
        btc_candles,
        60,
    )

    if (
        c60 is not None
        and c15 is not None
    ):

        if (
            c60 <= -1.0
            and c15 <= -0.5
        ):
            return "BEARISH"

        if (
            c60 >= 1.0
            and c15 >= 0.5
        ):
            return "BULLISH"

    return "NEUTRAL"


# =========================================================
# SCORE ENGINE
# =========================================================

def calculate_scores(
    candles,
    btc_1h,
    regime,
):

    c5 = candle_change(
        candles,
        5,
    )

    c10 = candle_change(
        candles,
        10,
    )

    c15 = candle_change(
        candles,
        15,
    )

    rsi = calculate_rsi(
        candles
    )

    ema5 = calculate_ema(
        candles,
        5,
    )

    ema10 = calculate_ema(
        candles,
        10,
    )

    vol = volume_ratio(
        candles
    )

    levels = breakout_data(
        candles
    )

    ichi = ichimoku(
        candles
    )

    pump = 0
    dump = 0

    pump_reasons = []
    dump_reasons = []

    # -----------------------------------------------------
    # 5M MOMENTUM
    # -----------------------------------------------------

    if c5 is not None:

        if 0.5 <= c5 < 2.5:

            pump += 15
            pump_reasons.append(
                "5m momentum"
            )

        elif 2.5 <= c5 < 5:

            pump += 10

        elif c5 > 0:

            pump += 4

        if -2.5 < c5 <= -0.5:

            dump += 15
            dump_reasons.append(
                "5m selling"
            )

        elif -5 < c5 <= -2.5:

            dump += 10

        elif c5 < 0:

            dump += 4

    # -----------------------------------------------------
    # 10M
    # -----------------------------------------------------

    if c10 is not None:

        if c10 >= 2:

            pump += 10
            pump_reasons.append(
                "10m momentum"
            )

        elif c10 > 0:

            pump += 5

        if c10 <= -2:

            dump += 10
            dump_reasons.append(
                "10m selling"
            )

        elif c10 < 0:

            dump += 5

    # -----------------------------------------------------
    # 15M
    # -----------------------------------------------------

    if c15 is not None:

        if c15 >= 3:

            pump += 10
            pump_reasons.append(
                "15m trend"
            )

        elif c15 > 0:

            pump += 5

        if c15 <= -3:

            dump += 10
            dump_reasons.append(
                "15m bearish"
            )

        elif c15 < 0:

            dump += 5

    # -----------------------------------------------------
    # ACCELERATION
    # -----------------------------------------------------

    if (
        c5 is not None
        and c10 is not None
    ):

        if (
            c5 > 0
            and c10 > 0
            and c5 > c10 / 2
        ):

            pump += 10
            pump_reasons.append(
                "Acceleration"
            )

        if (
            c5 < 0
            and c10 < 0
            and c5 < c10 / 2
        ):

            dump += 10
            dump_reasons.append(
                "Down acceleration"
            )

    # -----------------------------------------------------
    # RSI
    # -----------------------------------------------------

    if rsi is not None:

        if 55 <= rsi <= 70:

            pump += 10
            pump_reasons.append(
                "RSI healthy"
            )

        elif 70 < rsi <= 78:

            pump += 6

        elif rsi > 82:

            pump -= 5
            pump_reasons.append(
                "RSI overheated"
            )

        if 30 <= rsi <= 45:

            dump += 10
            dump_reasons.append(
                "RSI weak"
            )

        elif rsi < 25:

            dump -= 5

    # -----------------------------------------------------
    # EMA
    # -----------------------------------------------------

    if (
        ema5 is not None
        and ema10 is not None
    ):

        if ema5 > ema10:

            pump += 10
            pump_reasons.append(
                "EMA bullish"
            )

        elif ema5 < ema10:

            dump += 10
            dump_reasons.append(
                "EMA bearish"
            )

    # -----------------------------------------------------
    # REAL VOLUME
    # -----------------------------------------------------

    if vol is not None:

        if vol >= 3:

            pump += 10
            dump += 10

            pump_reasons.append(
                f"Volume {vol:.1f}x"
            )

            dump_reasons.append(
                f"Volume {vol:.1f}x"
            )

        elif vol >= 2:

            pump += 8
            dump += 8

        elif vol >= 1.5:

            pump += 5
            dump += 5

            pump_reasons.append(
                f"Volume {vol:.1f}x"
            )

    # -----------------------------------------------------
    # BREAKOUT
    # -----------------------------------------------------

    if levels["breakout"]:

        pump += 15

        pump_reasons.append(
            "BREAKOUT"
        )

    if levels["breakdown"]:

        dump += 15

        dump_reasons.append(
            "BREAKDOWN"
        )

    # -----------------------------------------------------
    # ICHIMOKU
    # -----------------------------------------------------

    if ichi.get("ready"):

        if ichi.get("bullish"):

            pump += 15

            pump_reasons.append(
                "Ichimoku bullish"
            )

        if ichi.get("bearish"):

            dump += 15

            dump_reasons.append(
                "Ichimoku bearish"
            )

    # -----------------------------------------------------
    # BTC FILTER
    # -----------------------------------------------------

    if regime == "BULLISH":

        pump += 5

        pump_reasons.append(
            "BTC bullish"
        )

    elif regime == "BEARISH":

        dump += 5

        dump_reasons.append(
            "BTC bearish"
        )

        pump -= 5

    # -----------------------------------------------------
    # CLAMP
    # -----------------------------------------------------

    pump = max(
        0,
        min(
            100,
            pump,
        )
    )

    dump = max(
        0,
        min(
            100,
            dump,
        )
    )

    if pump >= dump:

        direction = "PUMP"
        score = pump
        reasons = pump_reasons

    else:

        direction = "DUMP"
        score = dump
        reasons = dump_reasons

    return {

        "direction": direction,

        "score": score,

        "pump_score": pump,

        "dump_score": dump,

        "reasons": reasons,

        "c5": c5,
        "c10": c10,
        "c15": c15,

        "rsi": rsi,

        "volume": vol,

        "breakout":
            levels["breakout"],

        "breakdown":
            levels["breakdown"],

        "ichimoku": ichi,

        "btc_1h": btc_1h,

        "regime": regime,

        "price":
            float(
                candles[-1]["close"]
            ),
    }


# =========================================================
# CONFIRMATION
# =========================================================

def signal_key(
    symbol,
    direction,
):

    return (
        f"{symbol}_{direction}"
    )


def confirmation_count(
    state,
    symbol,
    direction,
    score,
    candle_time,
):

    key = signal_key(
        symbol,
        direction,
    )

    if key not in state:

        state[key] = {
            "count": 0,
            "last_score": score,
            "last_time": 0,
            "last_candle_time": 0,
            "status": "WATCH",
        }

    entry = state[key]

    now = int(
        time.time()
    )

    last_time = int(
        entry.get(
            "last_time",
            0,
        )
        or 0
    )

    last_candle_time = int(
        entry.get(
            "last_candle_time",
            0,
        )
        or 0
    )

    # Expire confirmation after 15 minutes.

    if (
        last_time > 0
        and now - last_time > 15 * 60
    ):

        entry["count"] = 0

    # CRITICAL:
    # The same closed candle cannot count twice.

    if candle_time == last_candle_time:

        return int(
            entry.get(
                "count",
                0,
            )
        )

    entry["count"] = (
        int(
            entry.get(
                "count",
                0,
            )
        )
        + 1
    )

    entry["last_score"] = score
    entry["last_time"] = now
    entry["last_candle_time"] = candle_time

    return entry["count"]


# =========================================================
# CREATE TRADE
# =========================================================

def create_trade(
    symbol,
    direction,
    score,
    entry,
    candle_time,
):

    risk = (
        entry
        * SL_PERCENT
        / 100
    )

    if direction == "PUMP":

        sl = entry - risk

        tp1 = (
            entry
            + risk * TP1_R
        )

        tp2 = (
            entry
            + risk * TP2_R
        )

        tp3 = (
            entry
            + risk * TP3_R
        )

    else:

        sl = entry + risk

        tp1 = (
            entry
            - risk * TP1_R
        )

        tp2 = (
            entry
            - risk * TP2_R
        )

        tp3 = (
            entry
            - risk * TP3_R
        )

    now = datetime.now(
        timezone.utc
    )

    trade_id = (
        f"{symbol}-"
        f"{candle_time}-"
        f"{direction}"
    )

    return {

        "id": trade_id,

        "symbol": symbol,

        "direction": direction,

        "score": score,

        "entry": entry,

        "sl": sl,

        "tp1": tp1,

        "tp2": tp2,

        "tp3": tp3,

        "trailing_stop": None,

        "highest": entry,

        "lowest": entry,

        "tp1_hit": False,

        "tp2_hit": False,

        "tp3_hit": False,

        "status": "ACTIVE",

        "opened_at":
            now.isoformat(),

        "signal_candle_time":
            candle_time,

        "closed_at": None,

        "exit_price": None,

        "exit_reason": None,

        "realized_percent": 0,

        "realized_r": 0,

        "performance_recorded":
            False,
    }


# =========================================================
# TRADE PROFIT
# =========================================================

def trade_percent(
    trade,
    price,
):

    entry = trade["entry"]

    if trade["direction"] == "PUMP":

        return (
            (
                price - entry
            )
            / entry
        ) * 100

    return (
        (
            entry - price
        )
        / entry
    ) * 100


def trade_r(
    trade,
    price,
):

    entry = trade["entry"]

    risk = (
        entry
        * SL_PERCENT
        / 100
    )

    if risk <= 0:
        return 0

    if trade["direction"] == "PUMP":

        return (
            price - entry
        ) / risk

    return (
        entry - price
    ) / risk


# =========================================================
# CLOSE TRADE
# =========================================================

def close_trade(
    trade,
    price,
    reason,
):

    trade["status"] = "CLOSED"

    trade["closed_at"] = (
        datetime.now(
            timezone.utc
        ).isoformat()
    )

    trade["exit_price"] = price

    trade["exit_reason"] = reason

    trade["realized_percent"] = (
        trade_percent(
            trade,
            price,
        )
    )

    trade["realized_r"] = (
        trade_r(
            trade,
            price,
        )
    )


# =========================================================
# TRADE MANAGER
# =========================================================

def manage_trade(
    trade,
    price,
):

    if trade.get("status") != "ACTIVE":
        return None

    direction = trade.get(
        "direction"
    )

    if direction == "PUMP":

        if price > trade["highest"]:
            trade["highest"] = price

        # SL

        if price <= trade["sl"]:

            close_trade(
                trade,
                price,
                "STOP LOSS",
            )

            return "SL"

        # TP1

        if (
            not trade["tp1_hit"]
            and price >= trade["tp1"]
        ):

            trade["tp1_hit"] = True

            trade["sl"] = (
                trade["entry"]
                * 1.0005
            )

            return "TP1"

        # TP2

        if (
            trade["tp1_hit"]
            and not trade["tp2_hit"]
            and price >= trade["tp2"]
        ):

            trade["tp2_hit"] = True

            trade["sl"] = (
                trade["entry"]
                +
                (
                    price
                    - trade["entry"]
                ) * 0.50
            )

            return "TP2"

        # TP3

        if (
            trade["tp2_hit"]
            and not trade["tp3_hit"]
            and price >= trade["tp3"]
        ):

            trade["tp3_hit"] = True

            return "TP3"

        # TRAILING

        if trade["tp3_hit"]:

            trailing = (
                trade["highest"]
                *
                (
                    1
                    - TRAIL_AFTER_TP3
                    / 100
                )
            )

            trade[
                "trailing_stop"
            ] = trailing

            if price <= trailing:

                close_trade(
                    trade,
                    price,
                    "TRAILING STOP",
                )

                return "TRAIL"

    else:

        if price < trade["lowest"]:
            trade["lowest"] = price

        # SL

        if price >= trade["sl"]:

            close_trade(
                trade,
                price,
                "STOP LOSS",
            )

            return "SL"

        # TP1

        if (
            not trade["tp1_hit"]
            and price <= trade["tp1"]
        ):

            trade["tp1_hit"] = True

            trade["sl"] = (
                trade["entry"]
                * 0.9995
            )

            return "TP1"

        # TP2

        if (
            trade["tp1_hit"]
            and not trade["tp2_hit"]
            and price <= trade["tp2"]
        ):

            trade["tp2_hit"] = True

            trade["sl"] = (
                trade["entry"]
                -
                (
                    trade["entry"]
                    - price
                ) * 0.50
            )

            return "TP2"

        # TP3

        if (
            trade["tp2_hit"]
            and not trade["tp3_hit"]
            and price <= trade["tp3"]
        ):

            trade["tp3_hit"] = True

            return "TP3"

        # TRAILING

        if trade["tp3_hit"]:

            trailing = (
                trade["lowest"]
                *
                (
                    1
                    + TRAIL_AFTER_TP3
                    / 100
                )
            )

            trade[
                "trailing_stop"
            ] = trailing

            if price >= trailing:

                close_trade(
                    trade,
                    price,
                    "TRAILING STOP",
                )

                return "TRAIL"

    return None


# =========================================================
# ACTIVE TRADE MANAGER
# =========================================================

def manage_active_trades(
    trades,
    market,
):

    events = []

    for trade_id, trade in list(
        trades.items()
    ):

        if not isinstance(
            trade,
            dict,
        ):
            continue

        if trade.get(
            "status"
        ) != "ACTIVE":
            continue

        symbol = trade.get(
            "symbol"
        )

        candles = market.get(
            symbol,
            [],
        )

        if not candles:
            continue

        price = float(
            candles[-1]["close"]
        )

        event = manage_trade(
            trade,
            price,
        )

        if event:

            events.append(
                (
                    event,
                    trade.copy(),
                )
            )

    return events


# =========================================================
# PERFORMANCE
# =========================================================

def update_performance(
    performance,
    trade,
):

    if not isinstance(
        trade,
        dict,
    ):
        return

    if trade.get(
        "status"
    ) != "CLOSED":
        return

    if trade.get(
        "performance_recorded",
        False,
    ):
        return

    performance.append(
        {
            "id":
                trade.get(
                    "id"
                ),

            "symbol":
                trade.get(
                    "symbol"
                ),

            "direction":
                trade.get(
                    "direction"
                ),

            "score":
                trade.get(
                    "score",
                    0,
                ),

            "entry":
                trade.get(
                    "entry",
                    0,
                ),

            "exit":
                trade.get(
                    "exit_price",
                    0,
                ),

            "reason":
                trade.get(
                    "exit_reason"
                ),

            "percent":
                trade.get(
                    "realized_percent",
                    0,
                ),

            "R":
                trade.get(
                    "realized_r",
                    0,
                ),

            "tp1_hit":
                bool(
                    trade.get(
                        "tp1_hit",
                        False,
                    )
                ),

            "tp2_hit":
                bool(
                    trade.get(
                        "tp2_hit",
                        False,
                    )
                ),

            "tp3_hit":
                bool(
                    trade.get(
                        "tp3_hit",
                        False,
                    )
                ),

            "closed_at":
                trade.get(
                    "closed_at"
                ),
        }
    )

    trade[
        "performance_recorded"
    ] = True


def performance_stats(
    performance
):

    performance = normalize_performance(
        performance
    )

    if not performance:

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "avg_r": 0,
            "profit_factor": 0,
            "tp1_rate": 0,
            "tp2_rate": 0,
            "tp3_rate": 0,
        }

    wins = []
    losses = []

    for item in performance:

        try:

            percent = float(
                item.get(
                    "percent",
                    0,
                )
            )

        except Exception:

            percent = 0

        if percent > 0:
            wins.append(item)

        else:
            losses.append(item)

    total = len(performance)

    win_rate = (
        len(wins)
        / total
    ) * 100

    try:

        avg_r = (
            sum(
                float(
                    x.get(
                        "R",
                        0,
                    )
                )
                for x in performance
            )
            / total
        )

    except Exception:

        avg_r = 0

    gross_profit = 0

    for x in wins:

        try:
            gross_profit += float(
                x.get(
                    "percent",
                    0,
                )
            )

        except Exception:
            pass

    gross_loss = 0

    for x in losses:

        try:

            gross_loss += abs(
                float(
                    x.get(
                        "percent",
                        0,
                    )
                )
            )

        except Exception:
            pass

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            / gross_loss
        )

    else:

        profit_factor = 0

    tp1_count = sum(
        1
        for x in performance
        if x.get(
            "tp1_hit",
            False,
        )
    )

    tp2_count = sum(
        1
        for x in performance
        if x.get(
            "tp2_hit",
            False,
        )
    )

    tp3_count = sum(
        1
        for x in performance
        if x.get(
            "tp3_hit",
            False,
        )
    )

    return {

        "trades": total,

        "wins":
            len(wins),

        "losses":
            len(losses),

        "win_rate":
            win_rate,

        "avg_r":
            avg_r,

        "profit_factor":
            profit_factor,

        "tp1_rate":
            (
                tp1_count
                / total
            ) * 100,

        "tp2_rate":
            (
                tp2_count
                / total
            ) * 100,

        "tp3_rate":
            (
                tp3_count
                / total
            ) * 100,
    }


# =========================================================
# PROCESS SIGNAL
# =========================================================

def process_signal(
    symbol,
    result,
    confirmations,
    trades,
):

    direction = result[
        "direction"
    ]

    score = result[
        "score"
    ]

    if score < MIN_SCORE:
        return None

    if (
        result["c5"] is None
        or result["c10"] is None
        or result["c15"] is None
    ):
        return None

    # -----------------------------------------------------
    # THREE-TIMEFRAME DIRECTION
    # -----------------------------------------------------

    if direction == "PUMP":

        if not (
            result["c5"] > 0
            and result["c10"] > 0
            and result["c15"] > 0
            and result["c5"] < 5
        ):
            return None

    else:

        if not (
            result["c5"] < 0
            and result["c10"] < 0
            and result["c15"] < 0
        ):
            return None

    # -----------------------------------------------------
    # REAL VOLUME
    # -----------------------------------------------------

    volume = result.get(
        "volume"
    )

    if (
        volume is None
        or volume < VOLUME_MIN_SIGNAL
    ):
        return None

    # -----------------------------------------------------
    # CANDLE TIME
    # -----------------------------------------------------

    candle_time = int(
        result.get(
            "candle_time",
            0,
        )
    )

    if candle_time <= 0:
        return None

    # -----------------------------------------------------
    # CONFIRMATION
    # -----------------------------------------------------

    count = confirmation_count(
        confirmations,
        symbol,
        direction,
        score,
        candle_time,
    )

    print(
        f"{symbol} "
        f"{direction} "
        f"confirmation "
        f"{count}/"
        f"{CONFIRMATIONS_REQUIRED}"
    )

    if (
        count
        < CONFIRMATIONS_REQUIRED
    ):
        return None

    # -----------------------------------------------------
    # NO DUPLICATE ACTIVE TRADE
    # -----------------------------------------------------

    for trade in trades.values():

        if not isinstance(
            trade,
            dict,
        ):
            continue

        if (
            trade.get(
                "status"
            ) == "ACTIVE"
            and trade.get(
                "symbol"
            ) == symbol
        ):

            return None

    # -----------------------------------------------------
    # MAX ACTIVE
    # -----------------------------------------------------

    active_count = sum(
        1
        for x in trades.values()
        if isinstance(
            x,
            dict,
        )
        and x.get(
            "status"
        ) == "ACTIVE"
    )

    if (
        active_count
        >= MAX_ACTIVE_TRADES
    ):
        return None

    # -----------------------------------------------------
    # CREATE
    # -----------------------------------------------------

    return create_trade(
        symbol,
        direction,
        score,
        result["price"],
        candle_time,
    )


# =========================================================
# TRADE MESSAGE
# =========================================================

def create_trade_message(
    trade,
    result,
):

    if trade["direction"] == "PUMP":

        title = (
            "🟢 <b>"
            "CONFIRMED EARLY PUMP"
            "</b>"
        )

    else:

        title = (
            "🔴 <b>"
            "CONFIRMED EARLY DUMP"
            "</b>"
        )

    volume = result.get(
        "volume"
    )

    volume_text = (
        "N/A"
        if volume is None
        else f"{volume:.2f}x"
    )

    btc = result.get(
        "btc_1h"
    )

    btc_text = (
        "N/A"
        if btc is None
        else f"{btc:+.2f}%"
    )

    reasons = result.get(
        "reasons",
        [],
    )

    return (

        f"{title}\n"

        f"━━━━━━━━━━━━━━━━━━\n"

        f"💎 <b>"
        f"{trade['symbol']}"
        f"</b>\n\n"

        f"⭐ Score: "
        f"<b>{trade['score']}/100</b>\n"

        f"💰 Entry: "
        f"<b>{trade['entry']:.8g}</b>\n\n"

        f"🛑 SL: "
        f"<b>{trade['sl']:.8g}</b>\n\n"

        f"🎯 TP1: "
        f"<b>{trade['tp1']:.8g}</b>\n"

        f"🎯 TP2: "
        f"<b>{trade['tp2']:.8g}</b>\n"

        f"🎯 TP3: "
        f"<b>{trade['tp3']:.8g}</b>\n\n"

        f"📊 Volume: "
        f"<b>{volume_text}</b>\n"

        f"₿ BTC 1H: "
        f"<b>{btc_text}</b>\n"

        f"🌐 BTC Regime: "
        f"<b>{result.get('regime')}</b>\n\n"

        f"📌 "
        f"{', '.join(reasons[:7])}\n\n"

        f"🤖 "
        f"<b>Trade Manager ACTIVE</b>"
    )


# =========================================================
# EVENT MESSAGE
# =========================================================

def event_message(
    event,
    trade,
):

    symbol = trade.get(
        "symbol",
        "UNKNOWN",
    )

    price = (
        trade.get(
            "exit_price"
        )
        or trade.get(
            "entry"
        )
    )

    if event == "TP1":

        return (
            "🎯 <b>TP1 HIT</b>\n\n"
            f"{symbol}\n"
            f"Price: {price:.8g}\n"
            "🛡 SL moved to BE"
        )

    if event == "TP2":

        return (
            "🔥 <b>TP2 HIT</b>\n\n"
            f"{symbol}\n"
            f"Price: {price:.8g}\n"
            "🛡 Trailing protection"
        )

    if event == "TP3":

        return (
            "🚀 <b>TP3 HIT</b>\n\n"
            f"{symbol}\n"
            f"Price: {price:.8g}\n"
            "🛡 Trailing Stop ACTIVE"
        )

    if event == "SL":

        return (
            "🛑 <b>STOP LOSS</b>\n\n"
            f"{symbol}\n"
            f"Exit: {price:.8g}\n"
            f"Result: "
            f"<b>"
            f"{trade.get('realized_percent', 0):+.2f}%"
            f"</b>\n"
            f"R: "
            f"<b>"
            f"{trade.get('realized_r', 0):+.2f}R"
            f"</b>"
        )

    if event == "TRAIL":

        return (
            "🚪 <b>TRAILING EXIT</b>\n\n"
            f"{symbol}\n"
            f"Exit: {price:.8g}\n"
            f"Result: "
            f"<b>"
            f"{trade.get('realized_percent', 0):+.2f}%"
            f"</b>\n"
            f"R: "
            f"<b>"
            f"{trade.get('realized_r', 0):+.2f}R"
            f"</b>"
        )

    return None


# =========================================================
# WATCHLIST
# =========================================================

def watchlist_message(
    results,
    active_trades,
    regime,
    stats,
):

    lines = [
        "👀 <b>TOP 5 WATCHLIST v5.3</b>",
        "━━━━━━━━━━━━━━━━━━",
    ]

    for i, item in enumerate(
        results[:5],
        1,
    ):

        emoji = (
            "🟢"
            if item["direction"]
            == "PUMP"
            else "🔴"
        )

        c5 = (
            "N/A"
            if item["c5"] is None
            else f"{item['c5']:+.2f}%"
        )

        c10 = (
            "N/A"
            if item["c10"] is None
            else f"{item['c10']:+.2f}%"
        )

        c15 = (
            "N/A"
            if item["c15"] is None
            else f"{item['c15']:+.2f}%"
        )

        rsi = (
            "N/A"
            if item["rsi"] is None
            else f"{item['rsi']:.1f}"
        )

        vol = (
            "N/A"
            if item["volume"] is None
            else f"{item['volume']:.2f}x"
        )

        lines.append(
            f"{i}. {emoji} "
            f"<b>{item['symbol']}</b> "
            f"⭐ "
            f"{item['score']}/100"
        )

        lines.append(
            f"5m {c5} | "
            f"10m {c10} | "
            f"15m {c15}"
        )

        lines.append(
            f"RSI {rsi} | "
            f"Vol {vol}"
        )

        features = []

        if item["breakout"]:

            features.append(
                "BREAKOUT"
            )

        if item["breakdown"]:

            features.append(
                "BREAKDOWN"
            )

        if item[
            "ichimoku"
        ].get("bullish"):

            features.append(
                "Ichimoku 🟢"
            )

        elif item[
            "ichimoku"
        ].get("bearish"):

            features.append(
                "Ichimoku 🔴"
            )

        if features:

            lines.append(
                "📌 "
                + " | ".join(
                    features
                )
            )

    lines.extend(
        [

            "",

            "━━━━━━━━━━━━━━━━━━",

            f"₿ BTC Regime: "
            f"<b>{regime}</b>",

            f"📌 Active Trades: "
            f"<b>{active_trades}</b>",

            f"📊 Trades: "
            f"<b>{stats['trades']}</b>",

            f"🎯 Win Rate: "
            f"<b>{stats['win_rate']:.1f}%</b>",

            f"📈 Avg R: "
            f"<b>{stats['avg_r']:+.2f}R</b>",

            f"💰 PF: "
            f"<b>{stats['profit_factor']:.2f}</b>",

            f"🎯 TP1 Rate: "
            f"<b>{stats['tp1_rate']:.1f}%</b>",

            f"🎯 TP2 Rate: "
            f"<b>{stats['tp2_rate']:.1f}%</b>",

            f"🚀 TP3 Rate: "
            f"<b>{stats['tp3_rate']:.1f}%</b>",

            "📡 <b>Closed 5M TRADE OHLCV</b>",

            "📊 <b>REAL VOLUME</b>",

            "🤖 <b>Engine v5.3</b>",
        ]
    )

    return "\n".join(lines)


# =========================================================
# MAIN
# =========================================================

def scan():

    print(
        "========================================"
    )

    print(
        "EARLY PUMP/DUMP ENGINE v5.3"
    )

    print(
        "REAL CLOSED 5M TRADE OHLCV"
    )

    print(
        "REAL VOLUME + BREAKOUT + ICHIMOKU"
    )

    print(
        "CONFIRMATION + TRADE MANAGER"
    )

    print(
        "DEFENSIVE STATE/PERFORMANCE"
    )

    print(
        "========================================"
    )

    # -----------------------------------------------------
    # LOAD STATE
    # -----------------------------------------------------

    trades = normalize_trades(
        load_json(
            TRADE_FILE,
            {},
        )
    )

    confirmations = normalize_confirmations(
        load_json(
            SIGNAL_FILE,
            {},
        )
    )

    performance = normalize_performance(
        load_json(
            PERFORMANCE_FILE,
            [],
        )
    )

    # -----------------------------------------------------
    # MARKET
    # -----------------------------------------------------

    market = get_all_candles()

    if not market:

        send_telegram(
            "🔴 <b>SCANNER ERROR</b>\n\n"
            "No Kraken 5M candle data."
        )

        return

    # -----------------------------------------------------
    # BTC
    # -----------------------------------------------------

    btc = market.get(
        "BTC",
        [],
    )

    if not btc:

        send_telegram(
            "🔴 <b>SCANNER ERROR</b>\n\n"
            "BTC 5M data unavailable."
        )

        return

    regime = btc_regime(
        btc
    )

    btc_1h = candle_change(
        btc,
        60,
    )

    print(
        "BTC regime:",
        regime,
    )

    print(
        "BTC 1H:",
        btc_1h,
    )

    # -----------------------------------------------------
    # MANAGE ACTIVE
    # -----------------------------------------------------

    events = manage_active_trades(
        trades,
        market,
    )

    for event, trade in events:

        message = event_message(
            event,
            trade,
        )

        if message:

            send_telegram(
                message
            )

        if trade.get(
            "status"
        ) == "CLOSED":

            # Find actual object in state
            trade_id = trade.get(
                "id"
            )

            actual_trade = trades.get(
                trade_id,
                trade,
            )

            update_performance(
                performance,
                actual_trade,
            )

    # -----------------------------------------------------
    # ANALYZE
    # -----------------------------------------------------

    results = []

    for symbol, candles in market.items():

        if len(candles) < 60:

            print(
                symbol,
                "not enough candles:",
                len(candles),
            )

            continue

        result = calculate_scores(
            candles,
            btc_1h,
            regime,
        )

        result["symbol"] = symbol

        result["candle_time"] = int(
            candles[-1]["time"]
        )

        results.append(
            result
        )

    # -----------------------------------------------------
    # SORT
    # -----------------------------------------------------

    results.sort(
        key=lambda x:
            x["score"],
        reverse=True,
    )

    # -----------------------------------------------------
    # CONFIRMED SIGNALS
    # -----------------------------------------------------

    new_trades = []

    for result in results:

        trade = process_signal(
            result["symbol"],
            result,
            confirmations,
            trades,
        )

        if trade:

            trades[
                trade["id"]
            ] = trade

            new_trades.append(
                (
                    trade,
                    result,
                )
            )

    # -----------------------------------------------------
    # SEND SIGNALS
    # -----------------------------------------------------

    for trade, result in new_trades:

        send_telegram(
            create_trade_message(
                trade,
                result,
            )
        )

    # -----------------------------------------------------
    # CLEAN OLD CONFIRMATIONS
    # -----------------------------------------------------

    now = int(
        time.time()
    )

    for key in list(
        confirmations.keys()
    ):

        item = confirmations.get(
            key,
            {},
        )

        if not isinstance(
            item,
            dict,
        ):

            del confirmations[key]
            continue

        last_time = int(
            item.get(
                "last_time",
                0,
            )
            or 0
        )

        if (
            last_time > 0
            and now - last_time > 30 * 60
        ):

            del confirmations[key]

    # -----------------------------------------------------
    # SAVE
    # -----------------------------------------------------

    save_json(
        TRADE_FILE,
        trades,
    )

    save_json(
        SIGNAL_FILE,
        confirmations,
    )

    save_json(
        PERFORMANCE_FILE,
        performance,
    )

    # -----------------------------------------------------
    # STATS
    # -----------------------------------------------------

    stats = performance_stats(
        performance
    )

    active_count = sum(
        1
        for x in trades.values()
        if isinstance(
            x,
            dict,
        )
        and x.get(
            "status"
        ) == "ACTIVE"
    )

    # -----------------------------------------------------
    # WATCHLIST
    # -----------------------------------------------------

    message = watchlist_message(
        results,
        active_count,
        regime,
        stats,
    )

    print(
        message
    )

    # Watchlist is still sent every run,
    # even when there is no confirmed signal.

    send_telegram(
        message
    )

    # -----------------------------------------------------
    # FINAL LOG
    # -----------------------------------------------------

    print(
        "========================================"
    )

    print(
        "Scanner finished."
    )

    print(
        "Coins:",
        len(market),
    )

    print(
        "Results:",
        len(results),
    )

    print(
        "Active trades:",
        active_count,
    )

    print(
        "New confirmed:",
        len(new_trades),
    )

    print(
        "Performance trades:",
        stats["trades"],
    )

    print(
        "Win rate:",
        f"{stats['win_rate']:.1f}%",
    )

    print(
        "Avg R:",
        f"{stats['avg_r']:+.2f}R",
    )

    print(
        "Profit Factor:",
        f"{stats['profit_factor']:.2f}",
    )

    print(
        "========================================"
    )


# =========================================================
# ENTRY POINT
# =========================================================

if __name__ == "__main__":

    try:

        scan()

    except Exception as e:

        print(
            "FATAL SCANNER ERROR:",
            repr(e),
        )

        try:

            send_telegram(
                "🔴 <b>SCANNER FATAL ERROR</b>\n\n"
                f"<code>{str(e)[:1000]}</code>"
            )

        except Exception:
            pass

        raise
