# ============================================================
# KRAKEN FUTURES ICHIMOKU MTF BACKTEST
# ============================================================
# ONE SYMBOL VERSION
#
# Symbol:
#   PI_XBTUSD
#
# Period:
#   2025-09-07 -> 2026-09-07
#
# Timeframes:
#   1H  = Main Trend
#   30m = Confirmation
#   15m = Pullback
#   5m  = Entry Trigger
#
# Strategy:
#   Ichimoku MTF
#
# Risk:
#   One trade at a time
#   RR 1:1
#   SL = confirmed swing +/- 0.15%
#   Max holding = 4 hours
#
# ============================================================

import requests
import time
import json
import os
import math
import traceback
from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

SYMBOL = "PI_XBTUSD"

START_DATE = "2025-09-07"
END_DATE = "2026-09-07"

CHUNK_SIZE = 1000

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 5
REQUEST_SLEEP = 0.5

ATR_PERIOD = 14

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26

MAX_PULLBACK_DISTANCE_ATR = 1.50

MIN_1H_SCORE = 7.0
MIN_30M_SCORE = 6.0
MIN_15M_SCORE = 4.0

TARGET_RR = 1.0
SL_BUFFER_PERCENT = 0.15

MAX_HOLDING_HOURS = 4

OUTPUT_DIR = "backtest_data"

RAW_FILE = os.path.join(
    OUTPUT_DIR,
    f"{SYMBOL}_5m.json"
)

RESULT_FILE = os.path.join(
    OUTPUT_DIR,
    f"{SYMBOL}_backtest_results.json"
)

REPORT_FILE = os.path.join(
    OUTPUT_DIR,
    f"{SYMBOL}_backtest_report.txt"
)


# ============================================================
# LOG
# ============================================================

def log(message):
    print(
        f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')} UTC] "
        f"{message}",
        flush=True
    )


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Kraken-Ichimoku-Backtester/1.0"
})


# ============================================================
# DATE / TIMESTAMP
# ============================================================

def date_to_timestamp(date_string):
    dt = datetime.strptime(
        date_string,
        "%Y-%m-%d"
    ).replace(tzinfo=timezone.utc)

    return int(dt.timestamp())


START_TS = date_to_timestamp(START_DATE)
END_TS = date_to_timestamp(END_DATE)


# ============================================================
# KRAKEN REQUEST
# ============================================================

def request_chunk(symbol, resolution, start_ts, end_ts):

    url = f"{BASE_URL}/{symbol}/{resolution}"

    params = {
        "from": start_ts,
        "to": end_ts,
    }

    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):

        try:

            log(
                f"Request {symbol} {resolution}m | "
                f"{datetime.fromtimestamp(start_ts, timezone.utc)} -> "
                f"{datetime.fromtimestamp(end_ts, timezone.utc)} | "
                f"attempt {attempt}/{REQUEST_RETRIES}"
            )

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

            return data

        except Exception as e:

            last_error = e

            log(
                f"ERROR request attempt {attempt}: {repr(e)}"
            )

            if attempt < REQUEST_RETRIES:
                time.sleep(2 * attempt)

    raise RuntimeError(
        f"Kraken request failed after "
        f"{REQUEST_RETRIES} attempts: {last_error}"
    )


# ============================================================
# PARSE KRAKEN DATA
# ============================================================

def parse_ohlcv(data):

    if not data:
        return []

    # Kraken responses can differ depending on endpoint version.
    #
    # Try common structures.

    rows = None

    if isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "result",
            "ohlcv",
        ):

            if key in data:
                rows = data[key]
                break

    elif isinstance(data, list):

        rows = data

    if rows is None:
        return []

    candles = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("t")
                )

                open_price = (
                    row.get("open")
                    or row.get("o")
                )

                high_price = (
                    row.get("high")
                    or row.get("h")
                )

                low_price = (
                    row.get("low")
                    or row.get("l")
                )

                close_price = (
                    row.get("close")
                    or row.get("c")
                )

                volume = (
                    row.get("volume")
                    or row.get("v")
                    or 0
                )

            else:

                if len(row) < 5:
                    continue

                ts = row[0]
                open_price = row[1]
                high_price = row[2]
                low_price = row[3]
                close_price = row[4]

                volume = row[5] if len(row) > 5 else 0

            ts = float(ts)

            # Some APIs return milliseconds.
            if ts > 10_000_000_000:
                ts = ts / 1000.0

            candle = {
                "timestamp": int(ts),
                "open": float(open_price),
                "high": float(high_price),
                "low": float(low_price),
                "close": float(close_price),
                "volume": float(volume),
            }

            candles.append(candle)

        except Exception:
            continue

    return candles


# ============================================================
# DOWNLOAD ONE YEAR OF 5M DATA
# ============================================================

def download_symbol():

    os.makedirs(
        OUTPUT_DIR,
        exist_ok=True
    )

    log("=" * 70)
    log("KRAKEN ICHIMOKU BACKTEST DATA DOWNLOAD")
    log("=" * 70)

    log(f"Symbol: {SYMBOL}")
    log(f"Start : {START_DATE}")
    log(f"End   : {END_DATE}")
    log(f"TF    : 5m")

    all_candles = []

    # 1000 five-minute candles
    # approximately 3.47 days.
    chunk_seconds = CHUNK_SIZE * 5 * 60

    current = START_TS

    chunk_number = 0

    while current < END_TS:

        chunk_number += 1

        chunk_end = min(
            current + chunk_seconds,
            END_TS
        )

        try:

            raw = request_chunk(
                SYMBOL,
                5,
                current,
                chunk_end
            )

            candles = parse_ohlcv(raw)

            log(
                f"Chunk {chunk_number}: "
                f"{len(candles)} candles"
            )

            all_candles.extend(candles)

        except Exception as e:

            log(
                f"FATAL chunk error: {repr(e)}"
            )

            raise

        current = chunk_end

        time.sleep(REQUEST_SLEEP)

    # Remove duplicates
    unique = {}

    for candle in all_candles:
        unique[candle["timestamp"]] = candle

    candles = list(unique.values())

    candles.sort(
        key=lambda x: x["timestamp"]
    )

    # Keep exact requested range
    candles = [
        c for c in candles
        if START_TS <= c["timestamp"] < END_TS
    ]

    with open(
        RAW_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            candles,
            f,
            ensure_ascii=False
        )

    log("=" * 70)
    log("DOWNLOAD COMPLETE")
    log(f"Total candles: {len(candles):,}")
    log(f"Saved: {RAW_FILE}")
    log("=" * 70)

    return candles


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    if os.path.exists(RAW_FILE):

        log(
            f"Loading existing data: {RAW_FILE}"
        )

        with open(
            RAW_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            candles = json.load(f)

        candles.sort(
            key=lambda x: x["timestamp"]
        )

        log(
            f"Loaded {len(candles):,} candles"
        )

        return candles

    return download_symbol()


# ============================================================
# RESAMPLE
# ============================================================

def resample(candles, minutes):

    if minutes == 5:
        return candles[:]

    interval = minutes * 60

    buckets = {}

    for c in candles:

        bucket = (
            c["timestamp"] // interval
        ) * interval

        if bucket not in buckets:

            buckets[bucket] = {
                "timestamp": bucket,
                "open": c["open"],
                "high": c["high"],
                "low": c["low"],
                "close": c["close"],
                "volume": c["volume"],
            }

        else:

            x = buckets[bucket]

            x["high"] = max(
                x["high"],
                c["high"]
            )

            x["low"] = min(
                x["low"],
                c["low"]
            )

            x["close"] = c["close"]

            x["volume"] += c["volume"]

    result = list(buckets.values())

    result.sort(
        key=lambda x: x["timestamp"]
    )

    return result


# ============================================================
# TRUE RANGE
# ============================================================

def true_range(candles, index):

    if index == 0:

        return (
            candles[index]["high"]
            - candles[index]["low"]
        )

    current = candles[index]

    previous = candles[index - 1]

    return max(
        current["high"] - current["low"],
        abs(
            current["high"]
            - previous["close"]
        ),
        abs(
            current["low"]
            - previous["close"]
        ),
    )


# ============================================================
# ATR
# ============================================================

def atr(candles, index, period=14):

    if index < period:
        return None

    values = []

    start = index - period + 1

    for i in range(start, index + 1):

        values.append(
            true_range(candles, i)
        )

    if not values:
        return None

    return sum(values) / len(values)


# ============================================================
# DONCHIAN
# ============================================================

def donchian(candles, index, period):

    if index < period - 1:
        return None

    start = index - period + 1

    window = candles[start:index + 1]

    highest = max(
        x["high"]
        for x in window
    )

    lowest = min(
        x["low"]
        for x in window
    )

    return (
        highest + lowest
    ) / 2.0


# ============================================================
# ICHIMOKU
# ============================================================

def ichimoku_at(candles, index):

    tenkan = donchian(
        candles,
        index,
        TENKAN_PERIOD
    )

    kijun = donchian(
        candles,
        index,
        KIJUN_PERIOD
    )

    senkou_b = donchian(
        candles,
        index,
        SENKOU_B_PERIOD
    )

    if (
        tenkan is None
        or kijun is None
        or senkou_b is None
    ):
        return None

    senkou_a = (
        tenkan + kijun
    ) / 2.0

    cloud_top = max(
        senkou_a,
        senkou_b
    )

    cloud_bottom = min(
        senkou_a,
        senkou_b
    )

    return {
        "tenkan": tenkan,
        "kijun": kijun,
        "senkou_a": senkou_a,
        "senkou_b": senkou_b,
        "cloud_top": cloud_top,
        "cloud_bottom": cloud_bottom,
    }


# ============================================================
# MARKET STATE
# ============================================================

def market_state(candles, index):

    if index < 60:
        return None

    ichi = ichimoku_at(
        candles,
        index
    )

    previous = ichimoku_at(
        candles,
        index - 1
    )

    if ichi is None or previous is None:
        return None

    current = candles[index]

    close = current["close"]

    current_atr = atr(
        candles,
        index,
        ATR_PERIOD
    )

    if (
        current_atr is None
        or current_atr <= 0
    ):
        return None

    # --------------------------------------------------------
    # Cloud position
    # --------------------------------------------------------

    above_cloud = (
        close > ichi["cloud_top"]
    )

    below_cloud = (
        close < ichi["cloud_bottom"]
    )

    # --------------------------------------------------------
    # Tenkan / Kijun
    # --------------------------------------------------------

    bullish_tk = (
        ichi["tenkan"]
        > ichi["kijun"]
    )

    bearish_tk = (
        ichi["tenkan"]
        < ichi["kijun"]
    )

    # --------------------------------------------------------
    # Kijun slope
    # --------------------------------------------------------

    kijun_slope = (
        ichi["kijun"]
        - previous["kijun"]
    )

    slope_percent = (
        kijun_slope
        / ichi["kijun"]
        * 100
    )

    bullish_slope = (
        kijun_slope > 0
    )

    bearish_slope = (
        kijun_slope < 0
    )

    # --------------------------------------------------------
    # Future Kumo
    # --------------------------------------------------------

    future_kumo_bullish = (
        ichi["senkou_a"]
        > ichi["senkou_b"]
    )

    future_kumo_bearish = (
        ichi["senkou_a"]
        < ichi["senkou_b"]
    )

    # --------------------------------------------------------
    # Chikou
    # --------------------------------------------------------

    chikou_bullish = False
    chikou_bearish = False

    chikou_index = (
        index - DISPLACEMENT
    )

    if chikou_index >= 0:

        chikou_close = candles[
            chikou_index
        ]["close"]

        chikou_bullish = (
            close > chikou_close
        )

        chikou_bearish = (
            close < chikou_close
        )

    # --------------------------------------------------------
    # Distance
    # --------------------------------------------------------

    distance_tenkan = (
        abs(close - ichi["tenkan"])
        / current_atr
    )

    distance_kijun = (
        abs(close - ichi["kijun"])
        / current_atr
    )

    return {
        "close": close,
        "atr": current_atr,

        "tenkan": ichi["tenkan"],
        "kijun": ichi["kijun"],

        "cloud_top": ichi["cloud_top"],
        "cloud_bottom": ichi["cloud_bottom"],

        "above_cloud": above_cloud,
        "below_cloud": below_cloud,

        "bullish_tk": bullish_tk,
        "bearish_tk": bearish_tk,

        "kijun_slope": kijun_slope,
        "slope_percent": slope_percent,

        "bullish_slope": bullish_slope,
        "bearish_slope": bearish_slope,

        "future_kumo_bullish":
            future_kumo_bullish,

        "future_kumo_bearish":
            future_kumo_bearish,

        "chikou_bullish":
            chikou_bullish,

        "chikou_bearish":
            chikou_bearish,

        "distance_tenkan":
            distance_tenkan,

        "distance_kijun":
            distance_kijun,
    }


# ============================================================
# 1H SCORE
# ============================================================

def score_1h(state):

    bull = 0
    bear = 0

    # Bull
    if state["above_cloud"]:
        bull += 2

    if state["bullish_tk"]:
        bull += 2

    if state["bullish_slope"]:
        bull += 1

    if state["future_kumo_bullish"]:
        bull += 2

    if state["chikou_bullish"]:
        bull += 1

    if (
        state["distance_tenkan"]
        <= MAX_PULLBACK_DISTANCE_ATR
        and state["bullish_tk"]
    ):
        bull += 2

    # Bear
    if state["below_cloud"]:
        bear += 2

    if state["bearish_tk"]:
        bear += 2

    if state["bearish_slope"]:
        bear += 1

    if state["future_kumo_bearish"]:
        bear += 2

    if state["chikou_bearish"]:
        bear += 1

    if (
        state["distance_tenkan"]
        <= MAX_PULLBACK_DISTANCE_ATR
        and state["bearish_tk"]
    ):
        bear += 2

    return (
        min(bull, 10),
        min(bear, 10)
    )


# ============================================================
# 30M SCORE
# ============================================================

def score_30m(state):

    bull = 0
    bear = 0

    if state["above_cloud"]:
        bull += 3

    if state["bullish_tk"]:
        bull += 2

    if state["bullish_slope"]:
        bull += 2

    if state["future_kumo_bullish"]:
        bull += 2

    if (
        state["distance_tenkan"]
        <= MAX_PULLBACK_DISTANCE_ATR
        and state["bullish_tk"]
    ):
        bull += 1

    if state["below_cloud"]:
        bear += 3

    if state["bearish_tk"]:
        bear += 2

    if state["bearish_slope"]:
        bear += 2

    if state["future_kumo_bearish"]:
        bear += 2

    if (
        state["distance_tenkan"]
        <= MAX_PULLBACK_DISTANCE_ATR
        and state["bearish_tk"]
    ):
        bear += 1

    return (
        min(bull, 10),
        min(bear, 10)
    )


# ============================================================
# 15M SCORE
# ============================================================

def score_15m(state):

    bull = 0
    bear = 0

    min_distance = min(
        state["distance_tenkan"],
        state["distance_kijun"]
    )

    # Bull
    if state["above_cloud"]:
        bull += 2

    if state["bullish_tk"]:
        bull += 2

    if state["bullish_slope"]:
        bull += 1

    if min_distance <= 0.75:
        bull += 3

    elif min_distance <= 1.50:
        bull += 2

    if (
        state["distance_tenkan"]
        <= MAX_PULLBACK_DISTANCE_ATR
        and state["bullish_tk"]
    ):
        bull += 2

    # Bear
    if state["below_cloud"]:
        bear += 2

    if state["bearish_tk"]:
        bear += 2

    if state["bearish_slope"]:
        bear += 1

    if min_distance <= 0.75:
        bear += 3

    elif min_distance <= 1.50:
        bear += 2

    if (
        state["distance_tenkan"]
        <= MAX_PULLBACK_DISTANCE_ATR
        and state["bearish_tk"]
    ):
        bear += 2

    return (
        min(bull, 10),
        min(bear, 10)
    )


# ============================================================
# 5M CROSS
# ============================================================

def five_min_trigger(
    candles,
    index,
    direction
):

    if index <= 0:
        return False

    current = market_state(
        candles,
        index
    )

    previous = market_state(
        candles,
        index - 1
    )

    if (
        current is None
        or previous is None
    ):
        return False

    if direction == "LONG":

        return (
            previous["close"]
            <= previous["tenkan"]
            and
            current["close"]
            > current["tenkan"]
            and
            current["bullish_tk"]
        )

    if direction == "SHORT":

        return (
            previous["close"]
            >= previous["tenkan"]
            and
            current["close"]
            < current["tenkan"]
            and
            current["bearish_tk"]
        )

    return False


# ============================================================
# SWING DETECTION
# ============================================================

def is_swing_low(
    candles,
    index,
    left=2,
    right=2
):

    if (
        index < left
        or index + right >= len(candles)
    ):
        return False

    value = candles[index]["low"]

    for i in range(
        index - left,
        index + right + 1
    ):

        if i == index:
            continue

        if candles[i]["low"] <= value:
            return False

    return True


def is_swing_high(
    candles,
    index,
    left=2,
    right=2
):

    if (
        index < left
        or index + right >= len(candles)
    ):
        return False

    value = candles[index]["high"]

    for i in range(
        index - left,
        index + right + 1
    ):

        if i == index:
            continue

        if candles[i]["high"] >= value:
            return False

    return True


# ============================================================
# LATEST CONFIRMED SWING
# ============================================================

def latest_swing(
    candles,
    current_index,
    direction
):

    # The latest usable swing must already be confirmed.
    max_index = current_index - 2

    if max_index < 2:
        return None

    start = max(
        2,
        max_index - 200
    )

    for i in range(
        max_index,
        start - 1,
        -1
    ):

        if direction == "LONG":

            if is_swing_low(
                candles,
                i,
                2,
                2
            ):
                return candles[i]["low"]

        else:

            if is_swing_high(
                candles,
                i,
                2,
                2
            ):
                return candles[i]["high"]

    return None


# ============================================================
# ALIGN HIGHER TF
# ============================================================

def build_time_map(candles):

    return {
        c["timestamp"]: i
        for i, c in enumerate(candles)
    }


def latest_closed_index(
    candles,
    timestamp
):

    # Binary search without importing bisect.
    lo = 0
    hi = len(candles) - 1

    result = None

    while lo <= hi:

        mid = (
            lo + hi
        ) // 2

        if (
            candles[mid]["timestamp"]
            <= timestamp
        ):

            result = mid
            lo = mid + 1

        else:

            hi = mid - 1

    return result


# ============================================================
# BUILD MTF STATES
# ============================================================

def prepare_states(
    candles_5m,
    candles_15m,
    candles_30m,
    candles_1h
):

    log("Preparing 1H states...")

    states_1h = {}

    for i, c in enumerate(candles_1h):

        state = market_state(
            candles_1h,
            i
        )

        if state is not None:

            states_1h[c["timestamp"]] = {
                "state": state,
                "score": score_1h(state),
            }

    log(
        f"1H states: {len(states_1h):,}"
    )

    log("Preparing 30m states...")

    states_30m = {}

    for i, c in enumerate(candles_30m):

        state = market_state(
            candles_30m,
            i
        )

        if state is not None:

            states_30m[c["timestamp"]] = {
                "state": state,
                "score": score_30m(state),
            }

    log(
        f"30m states: {len(states_30m):,}"
    )

    log("Preparing 15m states...")

    states_15m = {}

    for i, c in enumerate(candles_15m):

        state = market_state(
            candles_15m,
            i
        )

        if state is not None:

            states_15m[c["timestamp"]] = {
                "state": state,
                "score": score_15m(state),
            }

    log(
        f"15m states: {len(states_15m):,}"
    )

    return (
        states_1h,
        states_30m,
        states_15m
    )


# ============================================================
# GET LATEST STATE
# ============================================================

def latest_state(
    states,
    timestamps,
    timestamp
):

    idx = latest_closed_index(
        timestamps,
        timestamp
    )

    if idx is None:
        return None

    ts = timestamps[idx]

    return states.get(ts)


# ============================================================
# TRADE ENGINE
# ============================================================

def run_backtest(candles_5m):

    log("=" * 70)
    log("BUILDING MULTI-TIMEFRAME DATA")
    log("=" * 70)

    candles_15m = resample(
        candles_5m,
        15
    )

    candles_30m = resample(
        candles_5m,
        30
    )

    candles_1h = resample(
        candles_5m,
        60
    )

    log(
        f"5m : {len(candles_5m):,}"
    )

    log(
        f"15m: {len(candles_15m):,}"
    )

    log(
        f"30m: {len(candles_30m):,}"
    )

    log(
        f"1H : {len(candles_1h):,}"
    )

    (
        states_1h,
        states_30m,
        states_15m
    ) = prepare_states(
        candles_5m,
        candles_15m,
        candles_30m,
        candles_1h
    )

    timestamps_1h = [
        x["timestamp"]
        for x in candles_1h
    ]

    timestamps_30m = [
        x["timestamp"]
        for x in candles_30m
    ]

    timestamps_15m = [
        x["timestamp"]
        for x in candles_15m
    ]

    trades = []

    open_trade = None

    signal_count = 0

    start_index = 100

    log("=" * 70)
    log("STARTING BACKTEST")
    log("=" * 70)

    for i in range(
        start_index,
        len(candles_5m)
    ):

        candle = candles_5m[i]

        timestamp = candle["timestamp"]

        close = candle["close"]

        # ====================================================
        # MANAGE OPEN TRADE
        # ====================================================

        if open_trade is not None:

            trade = open_trade

            trade["bars_held"] += 1

            direction = trade["direction"]

            entry = trade["entry"]

            stop = trade["stop"]

            target = trade["target"]

            exit_reason = None
            exit_price = None

            if direction == "LONG":

                # SL first if both hit.
                if candle["low"] <= stop:

                    exit_price = stop
                    exit_reason = "SL"

                elif candle["high"] >= target:

                    exit_price = target
                    exit_reason = "TP"

            else:

                if candle["high"] >= stop:

                    exit_price = stop
                    exit_reason = "SL"

                elif candle["low"] <= target:

                    exit_price = target
                    exit_reason = "TP"

            # Max 4 hours = 48 x 5m candles
            max_bars = int(
                MAX_HOLDING_HOURS * 60 / 5
            )

            if (
                exit_reason is None
                and trade["bars_held"]
                >= max_bars
            ):

                exit_price = close
                exit_reason = "TIMEOUT"

            if exit_reason is not None:

                if direction == "LONG":

                    pnl_percent = (
                        (
                            exit_price
                            - entry
                        )
                        / entry
                    ) * 100

                else:

                    pnl_percent = (
                        (
                            entry
                            - exit_price
                        )
                        / entry
                    ) * 100

                trade["exit_timestamp"] = timestamp
                trade["exit"] = exit_price
                trade["exit_reason"] = exit_reason
                trade["pnl_percent"] = pnl_percent

                trade["duration_minutes"] = (
                    (
                        timestamp
                        - trade["entry_timestamp"]
                    ) / 60
                )

                trades.append(
                    trade
                )

                open_trade = None

        # ====================================================
        # IF TRADE STILL OPEN, NO NEW ENTRY
        # ====================================================

        if open_trade is not None:
            continue

        # ====================================================
        # MTF STATES
        # ====================================================

        state_1h = latest_state(
            states_1h,
            timestamps_1h,
            timestamp
        )

        state_30m = latest_state(
            states_30m,
            timestamps_30m,
            timestamp
        )

        state_15m = latest_state(
            states_15m,
            timestamps_15m,
            timestamp
        )

        if (
            state_1h is None
            or state_30m is None
            or state_15m is None
        ):
            continue

        s1h = state_1h["state"]
        s30 = state_30m["state"]
        s15 = state_15m["state"]

        score1h_bull, score1h_bear = (
            state_1h["score"]
        )

        score30_bull, score30_bear = (
            state_30m["score"]
        )

        score15_bull, score15_bear = (
            state_15m["score"]
        )

        # ====================================================
        # LONG CONDITIONS
        # ====================================================

        long_conditions = (

            score1h_bull
            >= MIN_1H_SCORE

            and s1h["above_cloud"]

            and s1h["bullish_tk"]

            and s1h["bullish_slope"]

            and s1h["future_kumo_bullish"]

            and score30_bull
            >= MIN_30M_SCORE

            and s30["above_cloud"]

            and s30["bullish_tk"]

            and s30["bullish_slope"]

            and score15_bull
            >= MIN_15M_SCORE

            and s15["above_cloud"]

            and s15["bullish_tk"]

            and (
                s15["bullish_slope"]
                or abs(
                    s15["kijun_slope"]
                ) < 1e-12
            )

            and min(
                s15["distance_tenkan"],
                s15["distance_kijun"]
            )
            <= MAX_PULLBACK_DISTANCE_ATR

            and five_min_trigger(
                candles_5m,
                i,
                "LONG"
            )
        )

        # ====================================================
        # SHORT CONDITIONS
        # ====================================================

        short_conditions = (

            score1h_bear
            >= MIN_1H_SCORE

            and s1h["below_cloud"]

            and s1h["bearish_tk"]

            and s1h["bearish_slope"]

            and s1h["future_kumo_bearish"]

            and score30_bear
            >= MIN_30M_SCORE

            and s30["below_cloud"]

            and s30["bearish_tk"]

            and s30["bearish_slope"]

            and score15_bear
            >= MIN_15M_SCORE

            and s15["below_cloud"]

            and s15["bearish_tk"]

            and (
                s15["bearish_slope"]
                or abs(
                    s15["kijun_slope"]
                ) < 1e-12
            )

            and min(
                s15["distance_tenkan"],
                s15["distance_kijun"]
            )
            <= MAX_PULLBACK_DISTANCE_ATR

            and five_min_trigger(
                candles_5m,
                i,
                "SHORT"
            )
        )

        # ====================================================
        # SIGNAL
        # ====================================================

        direction = None

        if long_conditions:
            direction = "LONG"

        elif short_conditions:
            direction = "SHORT"

        if direction is None:
            continue

        # ====================================================
        # SWING
        # ====================================================

        swing = latest_swing(
            candles_5m,
            i,
            direction
        )

        if swing is None:
            continue

        entry = close

        if direction == "LONG":

            stop = (
                swing
                * (
                    1
                    - SL_BUFFER_PERCENT / 100
                )
            )

            risk = entry - stop

            if risk <= 0:
                continue

            target = (
                entry
                + risk * TARGET_RR
            )

        else:

            stop = (
                swing
                * (
                    1
                    + SL_BUFFER_PERCENT / 100
                )
            )

            risk = stop - entry

            if risk <= 0:
                continue

            target = (
                entry
                - risk * TARGET_RR
            )

        # ====================================================
        # CREATE TRADE
        # ====================================================

        combined_score = (
            score1h_bull
            if direction == "LONG"
            else score1h_bear
        ) * 0.50 + (
            score30_bull
            if direction == "LONG"
            else score30_bear
        ) * 0.30 + (
            score15_bull
            if direction == "LONG"
            else score15_bear
        ) * 0.20

        signal_count += 1

        open_trade = {
            "symbol": SYMBOL,
            "direction": direction,

            "entry_timestamp":
                timestamp,

            "entry_datetime":
                datetime.fromtimestamp(
                    timestamp,
                    timezone.utc
                ).isoformat(),

            "entry": entry,

            "stop": stop,

            "target": target,

            "risk_percent": (
                abs(entry - stop)
                / entry
                * 100
            ),

            "rr": TARGET_RR,

            "score_1h": (
                score1h_bull
                if direction == "LONG"
                else score1h_bear
            ),

            "score_30m": (
                score30_bull
                if direction == "LONG"
                else score30_bear
            ),

            "score_15m": (
                score15_bull
                if direction == "LONG"
                else score15_bear
            ),

            "combined_score":
                combined_score,

            "swing":
                swing,

            "bars_held": 0,
        }

        log(
            f"ENTRY {direction} | "
            f"{datetime.fromtimestamp(timestamp, timezone.utc)} | "
            f"Entry={entry:.2f} | "
            f"SL={stop:.2f} | "
            f"TP={target:.2f} | "
            f"Score={combined_score:.2f}"
        )

    # ========================================================
    # CLOSE TRADE AT END OF DATA
    # ========================================================

    if open_trade is not None:

        candle = candles_5m[-1]

        exit_price = candle["close"]

        if open_trade["direction"] == "LONG":

            pnl_percent = (
                (
                    exit_price
                    - open_trade["entry"]
                )
                / open_trade["entry"]
            ) * 100

        else:

            pnl_percent = (
                (
                    open_trade["entry"]
                    - exit_price
                )
                / open_trade["entry"]
            ) * 100

        open_trade["exit_timestamp"] = (
            candle["timestamp"]
        )

        open_trade["exit"] = exit_price

        open_trade["exit_reason"] = (
            "END_OF_DATA"
        )

        open_trade["pnl_percent"] = (
            pnl_percent
        )

        open_trade["duration_minutes"] = (
            (
                candle["timestamp"]
                - open_trade["entry_timestamp"]
            ) / 60
        )

        trades.append(
            open_trade
        )

    log(
        f"Signals: {signal_count}"
    )

    log(
        f"Completed trades: {len(trades)}"
    )

    return trades


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(trades):

    if not trades:

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "timeouts": 0,
            "win_rate": 0,
            "total_pnl_percent": 0,
            "average_pnl_percent": 0,
            "profit_factor": 0,
            "max_drawdown_percent": 0,
            "long_trades": 0,
            "short_trades": 0,
            "long_wins": 0,
            "short_wins": 0,
        }

    wins = [
        t for t in trades
        if t["pnl_percent"] > 0
    ]

    losses = [
        t for t in trades
        if t["pnl_percent"] < 0
    ]

    timeouts = [
        t for t in trades
        if t["exit_reason"] == "TIMEOUT"
    ]

    long_trades = [
        t for t in trades
        if t["direction"] == "LONG"
    ]

    short_trades = [
        t for t in trades
        if t["direction"] == "SHORT"
    ]

    long_wins = [
        t for t in long_trades
        if t["pnl_percent"] > 0
    ]

    short_wins = [
        t for t in short_trades
        if t["pnl_percent"] > 0
    ]

    gross_profit = sum(
        t["pnl_percent"]
        for t in wins
    )

    gross_loss = abs(
        sum(
            t["pnl_percent"]
            for t in losses
        )
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            / gross_loss
        )
    else:
        profit_factor = (
            float("inf")
            if gross_profit > 0
            else 0
        )

    equity = 0
    peak = 0
    max_drawdown = 0

    for trade in trades:

        equity += trade["pnl_percent"]

        if equity > peak:
            peak = equity

        drawdown = peak - equity

        if drawdown > max_drawdown:
            max_drawdown = drawdown

    total_pnl = sum(
        t["pnl_percent"]
        for t in trades
    )

    average_pnl = (
        total_pnl
        / len(trades)
    )

    return {
        "trades": len(trades),

        "wins": len(wins),

        "losses": len(losses),

        "timeouts": len(timeouts),

        "win_rate":
            len(wins)
            / len(trades)
            * 100,

        "total_pnl_percent":
            total_pnl,

        "average_pnl_percent":
            average_pnl,

        "profit_factor":
            profit_factor,

        "max_drawdown_percent":
            max_drawdown,

        "long_trades":
            len(long_trades),

        "short_trades":
            len(short_trades),

        "long_wins":
            len(long_wins),

        "short_wins":
            len(short_wins),
    }


# ============================================================
# REPORT
# ============================================================

def generate_report(
    candles,
    trades,
    stats
):

    lines = []

    lines.append(
        "=" * 70
    )

    lines.append(
        "KRAKEN FUTURES ICHIMOKU MTF BACKTEST"
    )

    lines.append(
        "=" * 70
    )

    lines.append(
        f"Symbol: {SYMBOL}"
    )

    lines.append(
        f"Period: {START_DATE} -> {END_DATE}"
    )

    lines.append(
        f"5m candles: {len(candles):,}"
    )

    lines.append("")

    lines.append(
        "STRATEGY"
    )

    lines.append(
        "1H  = Main Trend"
    )

    lines.append(
        "30m = Confirmation"
    )

    lines.append(
        "15m = Pullback"
    )

    lines.append(
        "5m  = Entry Trigger"
    )

    lines.append(
        "Ichimoku = 9 / 26 / 52"
    )

    lines.append(
        f"RR = {TARGET_RR}:1"
    )

    lines.append(
        f"SL Buffer = {SL_BUFFER_PERCENT}%"
    )

    lines.append(
        f"Max Holding = {MAX_HOLDING_HOURS} hours"
    )

    lines.append("")

    lines.append(
        "PERFORMANCE"
    )

    lines.append(
        f"Trades       : {stats['trades']}"
    )

    lines.append(
        f"Wins         : {stats['wins']}"
    )

    lines.append(
        f"Losses       : {stats['losses']}"
    )

    lines.append(
        f"Timeouts     : {stats['timeouts']}"
    )

    lines.append(
        f"Win Rate     : {stats['win_rate']:.2f}%"
    )

    lines.append(
        f"Total PnL    : {stats['total_pnl_percent']:.2f}%"
    )

    lines.append(
        f"Average PnL  : {stats['average_pnl_percent']:.3f}%"
    )

    pf = stats["profit_factor"]

    if math.isinf(pf):

        pf_text = "INF"

    else:

        pf_text = f"{pf:.3f}"

    lines.append(
        f"Profit Factor: {pf_text}"
    )

    lines.append(
        f"Max Drawdown : {stats['max_drawdown_percent']:.2f}%"
    )

    lines.append("")

    lines.append(
        "LONG / SHORT"
    )

    lines.append(
        f"Long Trades  : {stats['long_trades']}"
    )

    lines.append(
        f"Long Wins    : {stats['long_wins']}"
    )

    lines.append(
        f"Short Trades : {stats['short_trades']}"
    )

    lines.append(
        f"Short Wins   : {stats['short_wins']}"
    )

    lines.append("")

    lines.append(
        "TRADE LIST"
    )

    lines.append(
        "-" * 70
    )

    for number, trade in enumerate(
        trades,
        1
    ):

        entry_dt = datetime.fromtimestamp(
            trade["entry_timestamp"],
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

        exit_dt = datetime.fromtimestamp(
            trade["exit_timestamp"],
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M"
        )

        lines.append(
            f"{number:03d} | "
            f"{trade['direction']:5s} | "
            f"Entry {entry_dt} | "
            f"{trade['entry']:.2f} | "
            f"SL {trade['stop']:.2f} | "
            f"TP {trade['target']:.2f} | "
            f"Exit {exit_dt} | "
            f"{trade['exit']:.2f} | "
            f"{trade['exit_reason']:10s} | "
            f"PnL {trade['pnl_percent']:.3f}%"
        )

    report = "\n".join(lines)

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(report)

    with open(
        RESULT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            {
                "symbol": SYMBOL,
                "start": START_DATE,
                "end": END_DATE,
                "statistics": stats,
                "trades": trades,
            },
            f,
            ensure_ascii=False,
            indent=2
        )

    return report


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    log("=" * 70)
    log("KRAKEN FUTURES ICHIMOKU MTF BACKTEST")
    log("=" * 70)

    log(
        f"Symbol: {SYMBOL}"
    )

    log(
        f"Period: {START_DATE} -> {END_DATE}"
    )

    try:

        candles = load_data()

        if len(candles) < 1000:

            raise RuntimeError(
                f"Not enough candles: {len(candles)}"
            )

        trades = run_backtest(
            candles
        )

        stats = calculate_statistics(
            trades
        )

        report = generate_report(
            candles,
            trades,
            stats
        )

        elapsed = (
            time.time()
            - start_time
        )

        print("")
        print(report)
        print("")
        print(
            f"Runtime: {elapsed / 60:.2f} minutes"
        )

        log("=" * 70)
        log("BACKTEST FINISHED SUCCESSFULLY")
        log("=" * 70)

    except Exception as e:

        log("=" * 70)
        log("BACKTEST FAILED")
        log("=" * 70)

        log(
            f"ERROR: {repr(e)}"
        )

        traceback.print_exc()

        raise


if __name__ == "__main__":
    main()
