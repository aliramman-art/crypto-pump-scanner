# ============================================================
# KRAKEN FUTURES PATTERN BACKTEST - 1 YEAR
# ============================================================
#
# DATA:
#   Kraken Futures public market candles
#
# TIMEFRAME:
#   1H
#
# PATTERNS:
#   Double Bottom
#   Double Top
#   Head & Shoulders
#   Inverse Head & Shoulders
#   Triangle
#   Wedge
#   Flag
#
# RESULT:
#   SUCCESS = TP reached before SL
#   FAILURE = SL reached before TP
#
# TP = +2%
# SL = -1%
#
# No API key
# No real trading
#
# IMPORTANT:
#   - Closed candles only
#   - Historical candles downloaded in chunks
#   - Current/incomplete candle removed
#   - Network timeout + retry
#   - Hard failure if data cannot be downloaded
# ============================================================

import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ============================================================
# SETTINGS
# ============================================================

DAYS = 365

INTERVAL = "1h"
RESOLUTION_SECONDS = 3600

# Number of Kraken perpetual contracts to test
TOP_SYMBOLS = 20

LOOKBACK = 120

MAX_HOLD_CANDLES = 48

TP_PCT = 0.020
SL_PCT = 0.010

PATTERN_TOLERANCE = 0.015

MIN_PATTERN_DISTANCE = 5
MAX_PATTERN_DISTANCE = 60

SIGNAL_COOLDOWN = 10

# Kraken request settings
REQUEST_TIMEOUT = 15
MAX_RETRIES = 3

# Number of candles requested per API call
# 1000 x 1h ~= 41.6 days
CANDLES_PER_REQUEST = 1000

KRAKEN_BASE = "https://futures.kraken.com"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 Kraken Pattern Backtest"
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(HEADERS)

retry_strategy = Retry(
    total=MAX_RETRIES,
    connect=MAX_RETRIES,
    read=MAX_RETRIES,
    backoff_factor=1.0,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)

adapter = HTTPAdapter(
    max_retries=retry_strategy,
    pool_connections=10,
    pool_maxsize=10,
)

SESSION.mount("https://", adapter)
SESSION.mount("http://", adapter)


# ============================================================
# LOG
# ============================================================

def log(message=""):
    print(message, flush=True)


# ============================================================
# REQUEST JSON
# ============================================================

def request_json(url, params=None):

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        if response.status_code != 200:

            raise RuntimeError(
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

        data = response.json()

        return data

    except Exception as exc:

        raise RuntimeError(
            f"Request failed: {url} | {exc}"
        ) from exc


# ============================================================
# KRAKEN CONNECTION TEST
# ============================================================

def test_connection():

    log()
    log("Testing Kraken Futures connection...")

    url = (
        f"{KRAKEN_BASE}/derivatives/api/v3/tickers"
    )

    data = request_json(url)

    if data.get("result") != "success":

        raise RuntimeError(
            f"Kraken ticker response invalid: {data}"
        )

    tickers = data.get("tickers", [])

    if not tickers:

        raise RuntimeError(
            "Kraken returned zero tickers."
        )

    log(
        f"Kraken connection OK - "
        f"{len(tickers)} ticker records received."
    )


# ============================================================
# GET PERPETUAL SYMBOLS
# ============================================================

def get_perpetual_symbols():

    url = (
        f"{KRAKEN_BASE}/derivatives/api/v3/tickers"
    )

    data = request_json(url)

    if data.get("result") != "success":

        raise RuntimeError(
            "Kraken ticker endpoint did not return success."
        )

    tickers = data.get("tickers", [])

    candidates = []

    for item in tickers:

        symbol = str(
            item.get("symbol", "")
        ).upper()

        tag = str(
            item.get("tag", "")
        ).lower()

        if not symbol:
            continue

        # Kraken perpetual futures are currently
        # represented by PI_ symbols.
        if not symbol.startswith("PI_"):
            continue

        # Must be perpetual.
        if tag != "perpetual":
            continue

        # Skip suspended contracts.
        if item.get("suspended") is True:
            continue

        # Current quote volume.
        volume_quote = item.get(
            "volumeQuote",
            0
        )

        try:
            volume_quote = float(volume_quote)
        except Exception:
            volume_quote = 0.0

        candidates.append(
            {
                "symbol": symbol,
                "volume_quote": volume_quote
            }
        )

    if not candidates:

        raise RuntimeError(
            "No Kraken perpetual futures symbols found."
        )

    # Current-volume ranking is only used to define
    # the test universe.
    candidates.sort(
        key=lambda x: x["volume_quote"],
        reverse=True
    )

    selected = candidates[
        :TOP_SYMBOLS
    ]

    return selected


# ============================================================
# GET HISTORICAL CANDLES
# ============================================================

def get_historical_candles(
    symbol,
    start_ts,
    end_ts
):

    url = (
        f"{KRAKEN_BASE}/api/charts/v1/"
        f"trade/{symbol}/{INTERVAL}"
    )

    all_rows = []

    cursor = int(start_ts)

    # Safety limit prevents accidental infinite loops.
    max_requests = 30

    requests_done = 0

    while cursor < end_ts:

        requests_done += 1

        if requests_done > max_requests:

            raise RuntimeError(
                f"{symbol}: pagination safety limit reached."
            )

        params = {
            "from": cursor,
            "to": int(end_ts),
            "count": CANDLES_PER_REQUEST
        }

        data = request_json(
            url,
            params=params
        )

        candles = data.get(
            "candles",
            []
        )

        if not candles:
            break

        batch = []

        for candle in candles:

            try:

                ts_ms = int(
                    candle["time"]
                )

                ts_seconds = (
                    ts_ms // 1000
                )

                batch.append(
                    {
                        "time": pd.to_datetime(
                            ts_ms,
                            unit="ms",
                            utc=True
                        ),
                        "timestamp": ts_seconds,
                        "open": float(
                            candle["open"]
                        ),
                        "high": float(
                            candle["high"]
                        ),
                        "low": float(
                            candle["low"]
                        ),
                        "close": float(
                            candle["close"]
                        ),
                        "volume": float(
                            candle.get(
                                "volume",
                                0
                            )
                        )
                    }
                )

            except (
                KeyError,
                TypeError,
                ValueError
            ):
                continue

        if not batch:
            break

        all_rows.extend(batch)

        last_timestamp = max(
            x["timestamp"]
            for x in batch
        )

        # Prevent infinite loops if API repeats data.
        if last_timestamp < cursor:

            raise RuntimeError(
                f"{symbol}: candle pagination moved backwards."
            )

        next_cursor = (
            last_timestamp
            + RESOLUTION_SECONDS
        )

        if next_cursor <= cursor:

            raise RuntimeError(
                f"{symbol}: candle pagination did not advance."
            )

        cursor = next_cursor

        # If Kraken says there are no more candles,
        # we can stop immediately.
        if data.get(
            "more_candles",
            False
        ) is False:

            break

        # Small delay to avoid hammering the API.
        time.sleep(0.10)

    if not all_rows:

        return pd.DataFrame()

    df = pd.DataFrame(
        all_rows
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    # Keep requested range.
    df = df[
        (df["timestamp"] >= start_ts)
        &
        (df["timestamp"] <= end_ts)
    ].copy()

    # --------------------------------------------------------
    # REMOVE INCOMPLETE CURRENT CANDLE
    # --------------------------------------------------------

    now_ts = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    if not df.empty:

        last_ts = int(
            df.iloc[-1]["timestamp"]
        )

        if (
            last_ts + RESOLUTION_SECONDS
            > now_ts
        ):

            df = df.iloc[:-1].copy()

    df = df.reset_index(
        drop=True
    )

    return df


# ============================================================
# SWING HIGH
# ============================================================

def find_swing_highs(
    df,
    left=2,
    right=2
):

    if len(df) < left + right + 1:
        return []

    highs = df["high"].values

    result = []

    for i in range(
        left,
        len(df) - right
    ):

        current = highs[i]

        left_side = highs[
            i - left:i
        ]

        right_side = highs[
            i + 1:i + right + 1
        ]

        if (
            current >= left_side.max()
            and
            current >= right_side.max()
        ):

            result.append(i)

    return result


# ============================================================
# SWING LOW
# ============================================================

def find_swing_lows(
    df,
    left=2,
    right=2
):

    if len(df) < left + right + 1:
        return []

    lows = df["low"].values

    result = []

    for i in range(
        left,
        len(df) - right
    ):

        current = lows[i]

        left_side = lows[
            i - left:i
        ]

        right_side = lows[
            i + 1:i + right + 1
        ]

        if (
            current <= left_side.min()
            and
            current <= right_side.min()
        ):

            result.append(i)

    return result


# ============================================================
# DOUBLE BOTTOM
# ============================================================

def detect_double_bottom(df):

    lows = find_swing_lows(df)

    if len(lows) < 2:
        return None

    a, b = lows[-2:]

    distance = b - a

    if not (
        MIN_PATTERN_DISTANCE
        <= distance
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    p1 = float(
        df.iloc[a]["low"]
    )

    p2 = float(
        df.iloc[b]["low"]
    )

    if p1 <= 0:
        return None

    difference = (
        abs(p1 - p2)
        / p1
    )

    if difference > PATTERN_TOLERANCE:
        return None

    neckline = float(
        df.iloc[a:b + 1]["high"].max()
    )

    current_close = float(
        df.iloc[-1]["close"]
    )

    if current_close <= neckline:
        return None

    return {
        "pattern": "Double Bottom",
        "direction": "LONG"
    }


# ============================================================
# DOUBLE TOP
# ============================================================

def detect_double_top(df):

    highs = find_swing_highs(df)

    if len(highs) < 2:
        return None

    a, b = highs[-2:]

    distance = b - a

    if not (
        MIN_PATTERN_DISTANCE
        <= distance
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    p1 = float(
        df.iloc[a]["high"]
    )

    p2 = float(
        df.iloc[b]["high"]
    )

    if p1 <= 0:
        return None

    difference = (
        abs(p1 - p2)
        / p1
    )

    if difference > PATTERN_TOLERANCE:
        return None

    neckline = float(
        df.iloc[a:b + 1]["low"].min()
    )

    current_close = float(
        df.iloc[-1]["close"]
    )

    if current_close >= neckline:
        return None

    return {
        "pattern": "Double Top",
        "direction": "SHORT"
    }


# ============================================================
# HEAD & SHOULDERS
# ============================================================

def detect_head_shoulders(df):

    highs = find_swing_highs(df)

    if len(highs) < 3:
        return None

    a, b, c = highs[-3:]

    d1 = b - a
    d2 = c - b

    if not (
        MIN_PATTERN_DISTANCE
        <= d1
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    if not (
        MIN_PATTERN_DISTANCE
        <= d2
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    left = float(
        df.iloc[a]["high"]
    )

    head = float(
        df.iloc[b]["high"]
    )

    right = float(
        df.iloc[c]["high"]
    )

    if head <= left or head <= right:
        return None

    if left <= 0:
        return None

    shoulder_difference = (
        abs(left - right)
        / left
    )

    if (
        shoulder_difference
        > PATTERN_TOLERANCE
    ):
        return None

    neckline = float(
        df.iloc[a:c + 1]["low"].min()
    )

    current_close = float(
        df.iloc[-1]["close"]
    )

    if current_close >= neckline:
        return None

    return {
        "pattern": "Head & Shoulders",
        "direction": "SHORT"
    }


# ============================================================
# INVERSE HEAD & SHOULDERS
# ============================================================

def detect_inverse_head_shoulders(df):

    lows = find_swing_lows(df)

    if len(lows) < 3:
        return None

    a, b, c = lows[-3:]

    d1 = b - a
    d2 = c - b

    if not (
        MIN_PATTERN_DISTANCE
        <= d1
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    if not (
        MIN_PATTERN_DISTANCE
        <= d2
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    left = float(
        df.iloc[a]["low"]
    )

    head = float(
        df.iloc[b]["low"]
    )

    right = float(
        df.iloc[c]["low"]
    )

    if head >= left or head >= right:
        return None

    if left <= 0:
        return None

    shoulder_difference = (
        abs(left - right)
        / left
    )

    if (
        shoulder_difference
        > PATTERN_TOLERANCE
    ):
        return None

    neckline = float(
        df.iloc[a:c + 1]["high"].max()
    )

    current_close = float(
        df.iloc[-1]["close"]
    )

    if current_close <= neckline:
        return None

    return {
        "pattern": "Inverse H&S",
        "direction": "LONG"
    }


# ============================================================
# TRIANGLE
# ============================================================

def detect_triangle(df):

    highs = find_swing_highs(df)
    lows = find_swing_lows(df)

    if len(highs) < 3:
        return None

    if len(lows) < 3:
        return None

    recent_highs = highs[-3:]
    recent_lows = lows[-3:]

    high_values = [
        float(df.iloc[i]["high"])
        for i in recent_highs
    ]

    low_values = [
        float(df.iloc[i]["low"])
        for i in recent_lows
    ]

    high_slope = np.polyfit(
        range(3),
        high_values,
        1
    )[0]

    low_slope = np.polyfit(
        range(3),
        low_values,
        1
    )[0]

    current = float(
        df.iloc[-1]["close"]
    )

    if current <= 0:
        return None

    resistance = max(
        high_values
    )

    support = min(
        low_values
    )

    # Contracting triangle
    if (
        high_slope < 0
        and
        low_slope > 0
    ):

        if current > resistance:
            return {
                "pattern": "Triangle",
                "direction": "LONG"
            }

        if current < support:
            return {
                "pattern": "Triangle",
                "direction": "SHORT"
            }

    return None


# ============================================================
# WEDGE
# ============================================================

def detect_wedge(df):

    highs = find_swing_highs(df)
    lows = find_swing_lows(df)

    if len(highs) < 3:
        return None

    if len(lows) < 3:
        return None

    hv = [
        float(df.iloc[i]["high"])
        for i in highs[-3:]
    ]

    lv = [
        float(df.iloc[i]["low"])
        for i in lows[-3:]
    ]

    hs = np.polyfit(
        range(3),
        hv,
        1
    )[0]

    ls = np.polyfit(
        range(3),
        lv,
        1
    )[0]

    current = float(
        df.iloc[-1]["close"]
    )

    upper = max(hv)
    lower = min(lv)

    if current <= 0:
        return None

    # Rising wedge
    if (
        hs > 0
        and
        ls > 0
        and
        hs > ls
    ):

        if current < lower:

            return {
                "pattern": "Wedge",
                "direction": "SHORT"
            }

    # Falling wedge
    if (
        hs < 0
        and
        ls < 0
        and
        ls < hs
    ):

        if current > upper:

            return {
                "pattern": "Wedge",
                "direction": "LONG"
            }

    return None


# ============================================================
# FLAG
# ============================================================

def detect_flag(df):

    if len(df) < 40:
        return None

    previous = df.iloc[-40:-20]
    recent = df.iloc[-20:]

    if (
        len(previous) < 20
        or
        len(recent) < 20
    ):
        return None

    previous_start = float(
        previous["close"].iloc[0]
    )

    previous_end = float(
        previous["close"].iloc[-1]
    )

    recent_start = float(
        recent["close"].iloc[0]
    )

    recent_end = float(
        recent["close"].iloc[-1]
    )

    if previous_start <= 0:
        return None

    prev_move = (
        previous_end
        /
        previous_start
        - 1
    )

    if recent_start <= 0:
        return None

    recent_move = (
        recent_end
        /
        recent_start
        - 1
    )

    current = float(
        df.iloc[-1]["close"]
    )

    recent_high = float(
        recent["high"].max()
    )

    recent_low = float(
        recent["low"].min()
    )

    # Bull flag
    if (
        prev_move >= 0.03
        and
        recent_move < 0
        and
        current > recent_high
    ):

        return {
            "pattern": "Flag",
            "direction": "LONG"
        }

    # Bear flag
    if (
        prev_move <= -0.03
        and
        recent_move > 0
        and
        current < recent_low
    ):

        return {
            "pattern": "Flag",
            "direction": "SHORT"
        }

    return None


# ============================================================
# DETECT PATTERN
# ============================================================

def detect_pattern(df):

    detectors = [
        detect_double_bottom,
        detect_double_top,
        detect_head_shoulders,
        detect_inverse_head_shoulders,
        detect_triangle,
        detect_wedge,
        detect_flag
    ]

    for detector in detectors:

        try:

            result = detector(df)

            if result is not None:
                return result

        except Exception:

            continue

    return None


# ============================================================
# SIMULATE TRADE
# ============================================================

def simulate_trade(
    df,
    entry_index,
    direction
):

    entry = float(
        df.iloc[entry_index]["close"]
    )

    if entry <= 0:
        return None

    if direction == "LONG":

        tp = entry * (
            1 + TP_PCT
        )

        sl = entry * (
            1 - SL_PCT
        )

    else:

        tp = entry * (
            1 - TP_PCT
        )

        sl = entry * (
            1 + SL_PCT
        )

    end_index = min(
        len(df),
        entry_index
        + MAX_HOLD_CANDLES
        + 1
    )

    for i in range(
        entry_index + 1,
        end_index
    ):

        high = float(
            df.iloc[i]["high"]
        )

        low = float(
            df.iloc[i]["low"]
        )

        if direction == "LONG":

            hit_tp = high >= tp
            hit_sl = low <= sl

        else:

            hit_tp = low <= tp
            hit_sl = high >= sl

        # Conservative rule:
        # if both are inside the same candle,
        # SL is considered first.
        if hit_tp and hit_sl:
            return "FAILURE"

        if hit_sl:
            return "FAILURE"

        if hit_tp:
            return "SUCCESS"

    # Neither TP nor SL was reached.
    # It is not counted.
    return None


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(df):

    results = []

    last_signal_index = -999999

    start_index = LOOKBACK

    end_index = (
        len(df)
        -
        MAX_HOLD_CANDLES
        -
        1
    )

    if end_index <= start_index:
        return results

    for index in range(
        start_index,
        end_index
    ):

        if (
            index
            -
            last_signal_index
            <
            SIGNAL_COOLDOWN
        ):
            continue

        # Only candles BEFORE the signal candle
        # are used to identify the setup.
        window = df.iloc[
            index - LOOKBACK:index
        ].copy()

        pattern = detect_pattern(
            window
        )

        if pattern is None:
            continue

        direction = pattern[
            "direction"
        ]

        result = simulate_trade(
            df,
            index,
            direction
        )

        if result is None:
            continue

        results.append(
            {
                "pattern": pattern[
                    "pattern"
                ],
                "direction": direction,
                "result": result
            }
        )

        last_signal_index = index

    return results


# ============================================================
# FINAL STATISTICS
# ============================================================

def print_statistics(
    all_results
):

    log()
    log("=" * 70)
    log("FINAL RESULTS")
    log("=" * 70)

    total = len(
        all_results
    )

    success = sum(
        1
        for x in all_results
        if x["result"] == "SUCCESS"
    )

    failure = sum(
        1
        for x in all_results
        if x["result"] == "FAILURE"
    )

    completed = (
        success
        +
        failure
    )

    if completed > 0:

        success_rate = (
            success
            /
            completed
            *
            100
        )

        failure_rate = (
            failure
            /
            completed
            *
            100
        )

    else:

        success_rate = 0.0
        failure_rate = 0.0

    log(
        f"TOTAL TRADES: {total}"
    )

    log(
        f"SUCCESS: {success}"
    )

    log(
        f"FAILURE: {failure}"
    )

    log(
        f"SUCCESS RATE: "
        f"{success_rate:.2f}%"
    )

    log(
        f"FAILURE RATE: "
        f"{failure_rate:.2f}%"
    )

    # --------------------------------------------------------
    # PATTERN RESULTS
    # --------------------------------------------------------

    log()
    log("=" * 70)
    log("PATTERN RESULTS")
    log("=" * 70)

    patterns = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag"
    ]

    for pattern_name in patterns:

        items = [
            x
            for x in all_results
            if x["pattern"]
            ==
            pattern_name
        ]

        p_total = len(items)

        p_success = sum(
            1
            for x in items
            if x["result"]
            ==
            "SUCCESS"
        )

        p_failure = sum(
            1
            for x in items
            if x["result"]
            ==
            "FAILURE"
        )

        p_completed = (
            p_success
            +
            p_failure
        )

        if p_completed > 0:

            p_success_rate = (
                p_success
                /
                p_completed
                *
                100
            )

            p_failure_rate = (
                p_failure
                /
                p_completed
                *
                100
            )

        else:

            p_success_rate = 0.0
            p_failure_rate = 0.0

        log(
            f"{pattern_name}: "
            f"Trades={p_total} | "
            f"Success={p_success} | "
            f"Failure={p_failure} | "
            f"Success Rate={p_success_rate:.2f}% | "
            f"Failure Rate={p_failure_rate:.2f}%"
        )


# ============================================================
# MAIN
# ============================================================

def main():

    start_time = time.time()

    log("=" * 70)
    log("KRAKEN FUTURES PATTERN BACKTEST - 1 YEAR")
    log("=" * 70)

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        -
        pd.Timedelta(
            days=DAYS
        )
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    log(
        f"Period: "
        f"{start_dt.strftime('%Y-%m-%d')} -> "
        f"{end_dt.strftime('%Y-%m-%d')}"
    )

    log(
        f"Timeframe: {INTERVAL}"
    )

    log(
        f"TP: {TP_PCT * 100:.2f}%"
    )

    log(
        f"SL: {SL_PCT * 100:.2f}%"
    )

    log(
        f"Symbols: {TOP_SYMBOLS}"
    )

    # --------------------------------------------------------
    # CONNECTION
    # --------------------------------------------------------

    test_connection()

    # --------------------------------------------------------
    # SYMBOL SELECTION
    # --------------------------------------------------------

    log()
    log(
        "Selecting Kraken perpetual futures..."
    )

    selected = get_perpetual_symbols()

    if not selected:

        raise RuntimeError(
            "No symbols selected."
        )

    log(
        f"Selected {len(selected)} symbols:"
    )

    for item in selected:

        log(
            f"  {item['symbol']}"
        )

    # --------------------------------------------------------
    # BACKTEST
    # --------------------------------------------------------

    all_results = []

    log()
    log("=" * 70)
    log("DOWNLOADING DATA AND RUNNING BACKTEST")
    log("=" * 70)

    for number, item in enumerate(
        selected,
        start=1
    ):

        symbol = item["symbol"]

        log()
        log(
            f"[{number}/{len(selected)}] "
            f"{symbol}"
        )

        try:

            df = get_historical_candles(
                symbol,
                start_ts,
                end_ts
            )

        except Exception as exc:

            log(
                f"  ERROR: {exc}"
            )

            # One symbol failing should not silently
            # produce a fake successful backtest.
            continue

        if df.empty:

            log(
                "  ERROR: No candles returned."
            )

            continue

        log(
            f"  Candles: {len(df)}"
        )

        minimum_required = (
            LOOKBACK
            +
            MAX_HOLD_CANDLES
            +
            10
        )

        if len(df) < minimum_required:

            log(
                "  ERROR: Not enough candles."
            )

            continue

        try:

            results = backtest_symbol(
                df
            )

        except Exception as exc:

            log(
                f"  ERROR during backtest: {exc}"
            )

            continue

        log(
            f"  Completed trades: "
            f"{len(results)}"
        )

        for result in results:

            result["symbol"] = symbol

        all_results.extend(
            results
        )

    # --------------------------------------------------------
    # DATA VALIDATION
    # --------------------------------------------------------

    if not all_results:

        raise RuntimeError(
            "BACKTEST FAILED: "
            "No completed trades were produced. "
            "The program will not report a fake SUCCESS."
        )

    # --------------------------------------------------------
    # RESULTS
    # --------------------------------------------------------

    print_statistics(
        all_results
    )

    elapsed = (
        time.time()
        -
        start_time
    )

    log()
    log(
        f"Runtime: {elapsed:.1f} seconds"
    )

    log()
    log("=" * 70)
    log("BACKTEST COMPLETED SUCCESSFULLY")
    log("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except KeyboardInterrupt:

        log()
        log(
            "Interrupted by user."
        )

        sys.exit(130)

    except Exception as exc:

        log()
        log("=" * 70)
        log("BACKTEST FAILED")
        log("=" * 70)
        log(
            f"ERROR: {exc}"
        )

        sys.exit(1)
