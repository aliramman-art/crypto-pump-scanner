# ============================================================
# KRAKEN FUTURES PATTERN BACKTEST - 1 YEAR
# ============================================================
#
# DATA:
#   Kraken Futures public candles
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
# No real trading
# No API key
# ============================================================

import time
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

DAYS = 365
INTERVAL = "1h"

# Kraken Futures perpetual symbols
TOP_SYMBOLS = 30

LOOKBACK = 120

MAX_HOLD_CANDLES = 48

TP_PCT = 0.020
SL_PCT = 0.010

PATTERN_TOLERANCE = 0.015

MIN_PATTERN_DISTANCE = 5
MAX_PATTERN_DISTANCE = 60

SIGNAL_COOLDOWN = 10

REQUEST_TIMEOUT = 30

KRAKEN_BASE = "https://futures.kraken.com"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0"
}


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# ============================================================
# GET KRAKEN FUTURES INSTRUMENTS
# ============================================================

def get_symbols():

    url = f"{KRAKEN_BASE}/derivatives/api/v3/instruments"

    try:
        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:
        raise RuntimeError(
            f"Kraken instruments request failed: {e}"
        )

    instruments = data.get("instruments", [])

    if not instruments:
        raise RuntimeError(
            "Kraken returned no instruments."
        )

    symbols = []

    for item in instruments:

        symbol = item.get("symbol", "")
        tradeable = item.get("tradeable", True)

        if not symbol:
            continue

        if tradeable is False:
            continue

        # Only perpetual futures
        if not symbol.startswith("PF_"):
            continue

        # Exclude inverse USD contracts
        if symbol.endswith("USD"):
            continue

        # Prefer USDT perpetuals
        if symbol.endswith("USDT"):
            symbols.append(symbol)

    # If Kraken does not expose USDT symbols,
    # use USD perpetuals as fallback.
    if not symbols:

        for item in instruments:

            symbol = item.get("symbol", "")
            tradeable = item.get("tradeable", True)

            if not symbol:
                continue

            if tradeable is False:
                continue

            if symbol.startswith("PF_") and symbol.endswith("USD"):
                symbols.append(symbol)

    symbols = sorted(set(symbols))

    if not symbols:
        raise RuntimeError(
            "No suitable Kraken Futures perpetual symbols found."
        )

    return symbols


# ============================================================
# GET 24H TICKER DATA
# ============================================================

def get_tickers():

    url = f"{KRAKEN_BASE}/derivatives/api/v3/tickers"

    try:

        response = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:

        raise RuntimeError(
            f"Kraken ticker request failed: {e}"
        )

    tickers = data.get("tickers", [])

    result = {}

    for item in tickers:

        symbol = item.get("symbol")

        if not symbol:
            continue

        volume = (
            item.get("vol24h")
            or item.get("volume24h")
            or item.get("volume")
            or 0
        )

        try:
            volume = float(volume)
        except Exception:
            volume = 0.0

        result[symbol] = volume

    return result


# ============================================================
# SELECT SYMBOLS
# ============================================================

def select_symbols():

    all_symbols = get_symbols()

    tickers = get_tickers()

    ranked = []

    for symbol in all_symbols:

        volume = tickers.get(symbol, 0.0)

        ranked.append(
            (
                symbol,
                volume
            )
        )

    ranked.sort(
        key=lambda x: x[1],
        reverse=True
    )

    selected = [
        symbol
        for symbol, volume in ranked[:TOP_SYMBOLS]
    ]

    if not selected:

        raise RuntimeError(
            "No symbols selected from Kraken Futures."
        )

    return selected


# ============================================================
# GET KRAKEN CANDLES
# ============================================================

def get_candles(symbol, start_ts, end_ts):

    url = (
        f"{KRAKEN_BASE}/api/charts/v1/"
        f"trade/{symbol}/{INTERVAL}"
    )

    params = {
        "from": int(start_ts),
        "to": int(end_ts),
        "count": 5000
    }

    try:

        response = SESSION.get(
            url,
            params=params,
            timeout=REQUEST_TIMEOUT
        )

        response.raise_for_status()

        data = response.json()

    except Exception as e:

        print(
            f"[ERROR] {symbol}: candle request failed: {e}"
        )

        return pd.DataFrame()

    candles = data.get("candles", [])

    if not candles:

        return pd.DataFrame()

    rows = []

    for candle in candles:

        try:

            rows.append({
                "time": pd.to_datetime(
                    int(candle["time"]),
                    unit="ms",
                    utc=True
                ),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", 0))
            })

        except Exception:
            continue

    if not rows:

        return pd.DataFrame()

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["time"]
    )

    df = df.sort_values(
        "time"
    ).reset_index(drop=True)

    return df


# ============================================================
# SWING HIGH
# ============================================================

def find_swing_highs(df, left=2, right=2):

    highs = df["high"].values

    result = []

    for i in range(left, len(df) - right):

        current = highs[i]

        left_side = highs[
            i - left:i
        ]

        right_side = highs[
            i + 1:i + right + 1
        ]

        if (
            current >= left_side.max()
            and current >= right_side.max()
        ):
            result.append(i)

    return result


# ============================================================
# SWING LOW
# ============================================================

def find_swing_lows(df, left=2, right=2):

    lows = df["low"].values

    result = []

    for i in range(left, len(df) - right):

        current = lows[i]

        left_side = lows[
            i - left:i
        ]

        right_side = lows[
            i + 1:i + right + 1
        ]

        if (
            current <= left_side.min()
            and current <= right_side.min()
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

    a = lows[-2]
    b = lows[-1]

    distance = b - a

    if not (
        MIN_PATTERN_DISTANCE
        <= distance
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    p1 = df.iloc[a]["low"]
    p2 = df.iloc[b]["low"]

    if p1 == 0:
        return None

    difference = abs(p1 - p2) / p1

    if difference > PATTERN_TOLERANCE:
        return None

    middle = df.iloc[
        a:b + 1
    ]

    neckline = middle["high"].max()

    current_close = df.iloc[-1]["close"]

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

    a = highs[-2]
    b = highs[-1]

    distance = b - a

    if not (
        MIN_PATTERN_DISTANCE
        <= distance
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    p1 = df.iloc[a]["high"]
    p2 = df.iloc[b]["high"]

    if p1 == 0:
        return None

    difference = abs(p1 - p2) / p1

    if difference > PATTERN_TOLERANCE:
        return None

    middle = df.iloc[
        a:b + 1
    ]

    neckline = middle["low"].min()

    current_close = df.iloc[-1]["close"]

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

    if not (
        MIN_PATTERN_DISTANCE <= b - a
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    if not (
        MIN_PATTERN_DISTANCE <= c - b
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    left = df.iloc[a]["high"]
    head = df.iloc[b]["high"]
    right = df.iloc[c]["high"]

    if head <= left or head <= right:
        return None

    shoulder_difference = (
        abs(left - right) / left
    )

    if shoulder_difference > PATTERN_TOLERANCE:
        return None

    neckline = df.iloc[a:c + 1]["low"].min()

    current_close = df.iloc[-1]["close"]

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

    if not (
        MIN_PATTERN_DISTANCE <= b - a
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    if not (
        MIN_PATTERN_DISTANCE <= c - b
        <= MAX_PATTERN_DISTANCE
    ):
        return None

    left = df.iloc[a]["low"]
    head = df.iloc[b]["low"]
    right = df.iloc[c]["low"]

    if head >= left or head >= right:
        return None

    shoulder_difference = (
        abs(left - right) / abs(left)
    )

    if shoulder_difference > PATTERN_TOLERANCE:
        return None

    neckline = df.iloc[a:c + 1]["high"].max()

    current_close = df.iloc[-1]["close"]

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

    if len(highs) < 3 or len(lows) < 3:
        return None

    recent_highs = highs[-3:]
    recent_lows = lows[-3:]

    high_values = [
        df.iloc[i]["high"]
        for i in recent_highs
    ]

    low_values = [
        df.iloc[i]["low"]
        for i in recent_lows
    ]

    high_slope = np.polyfit(
        range(len(high_values)),
        high_values,
        1
    )[0]

    low_slope = np.polyfit(
        range(len(low_values)),
        low_values,
        1
    )[0]

    avg_price = df.iloc[-1]["close"]

    if avg_price <= 0:
        return None

    high_pct = high_slope / avg_price
    low_pct = low_slope / avg_price

    current = df.iloc[-1]["close"]

    resistance = max(high_values)
    support = min(low_values)

    if (
        high_pct < 0
        and low_pct > 0
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

    if len(highs) < 3 or len(lows) < 3:
        return None

    hv = [
        df.iloc[i]["high"]
        for i in highs[-3:]
    ]

    lv = [
        df.iloc[i]["low"]
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

    avg = df.iloc[-1]["close"]

    if avg <= 0:
        return None

    hs_pct = hs / avg
    ls_pct = ls / avg

    current = df.iloc[-1]["close"]

    upper = max(hv)
    lower = min(lv)

    # Rising wedge -> bearish
    if hs_pct > 0 and ls_pct > 0 and hs_pct > ls_pct:

        if current < lower:
            return {
                "pattern": "Wedge",
                "direction": "SHORT"
            }

    # Falling wedge -> bullish
    if hs_pct < 0 and ls_pct < 0 and ls_pct < hs_pct:

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

    if len(df) < 30:
        return None

    recent = df.iloc[-20:]
    previous = df.iloc[-40:-20]

    if len(previous) < 20:
        return None

    prev_move = (
        previous["close"].iloc[-1]
        / previous["close"].iloc[0]
        - 1
    )

    recent_move = (
        recent["close"].iloc[-1]
        / recent["close"].iloc[0]
        - 1
    )

    current = df.iloc[-1]["close"]

    recent_high = recent["high"].max()
    recent_low = recent["low"].min()

    # Bull flag
    if prev_move > 0.03 and recent_move < 0:

        if current > recent_high:
            return {
                "pattern": "Flag",
                "direction": "LONG"
            }

    # Bear flag
    if prev_move < -0.03 and recent_move > 0:

        if current < recent_low:
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

def simulate_trade(df, entry_index, direction):

    entry = float(
        df.iloc[entry_index]["close"]
    )

    if direction == "LONG":

        tp = entry * (1 + TP_PCT)
        sl = entry * (1 - SL_PCT)

    else:

        tp = entry * (1 - TP_PCT)
        sl = entry * (1 + SL_PCT)

    end = min(
        len(df),
        entry_index + MAX_HOLD_CANDLES + 1
    )

    for i in range(
        entry_index + 1,
        end
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

        # Conservative assumption:
        # If TP and SL occur inside the same candle,
        # assume SL happened first.

        if hit_tp and hit_sl:
            return "FAILURE"

        if hit_sl:
            return "FAILURE"

        if hit_tp:
            return "SUCCESS"

    # Trade did not hit either level.
    # Exclude it from success/failure statistics.
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
        - MAX_HOLD_CANDLES
        - 1
    )

    for index in range(
        start_index,
        end_index
    ):

        if (
            index - last_signal_index
            < SIGNAL_COOLDOWN
        ):
            continue

        window = df.iloc[
            index - LOOKBACK:index
        ].copy()

        pattern = detect_pattern(window)

        if pattern is None:
            continue

        direction = pattern["direction"]

        result = simulate_trade(
            df,
            index,
            direction
        )

        if result is None:
            continue

        results.append({
            "pattern": pattern["pattern"],
            "direction": direction,
            "result": result
        })

        last_signal_index = index

    return results


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("KRAKEN FUTURES PATTERN BACKTEST - 1 YEAR")
    print("=" * 70)

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        - pd.Timedelta(days=DAYS)
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    print(
        f"Period: "
        f"{start_dt.strftime('%Y-%m-%d')} → "
        f"{end_dt.strftime('%Y-%m-%d')}"
    )

    print(
        f"Timeframe: {INTERVAL}"
    )

    print(
        "Data source: Kraken Futures"
    )

    print()

    # --------------------------------------------------------
    # SYMBOLS
    # --------------------------------------------------------

    print(
        "Selecting Kraken Futures symbols..."
    )

    symbols = select_symbols()

    print(
        f"Selected symbols: {len(symbols)}"
    )

    print()

    all_results = []

    # --------------------------------------------------------
    # BACKTEST
    # --------------------------------------------------------

    for number, symbol in enumerate(
        symbols,
        start=1
    ):

        print(
            f"[{number}/{len(symbols)}] "
            f"{symbol}"
        )

        df = get_candles(
            symbol,
            start_ts,
            end_ts
        )

        if df.empty:

            print(
                "  No candle data."
            )

            continue

        print(
            f"  Candles: {len(df)}"
        )

        if len(df) < LOOKBACK + MAX_HOLD_CANDLES:

            print(
                "  Not enough candles."
            )

            continue

        results = backtest_symbol(df)

        print(
            f"  Trades: {len(results)}"
        )

        for result in results:

            result["symbol"] = symbol

        all_results.extend(results)

        time.sleep(0.20)

    # --------------------------------------------------------
    # FINAL RESULTS
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("FINAL RESULTS")
    print("=" * 70)

    total = len(all_results)

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

    completed = success + failure

    if completed > 0:

        success_rate = (
            success
            / completed
            * 100
        )

        failure_rate = (
            failure
            / completed
            * 100
        )

    else:

        success_rate = 0.0
        failure_rate = 0.0

    print(
        f"TOTAL TRADES: {total}"
    )

    print(
        f"SUCCESS: {success}"
    )

    print(
        f"FAILURE: {failure}"
    )

    print(
        f"SUCCESS RATE: {success_rate:.2f}%"
    )

    print(
        f"FAILURE RATE: {failure_rate:.2f}%"
    )

    print()

    # --------------------------------------------------------
    # PATTERN STATISTICS
    # --------------------------------------------------------

    patterns = [
        "Double Bottom",
        "Double Top",
        "Head & Shoulders",
        "Inverse H&S",
        "Triangle",
        "Wedge",
        "Flag"
    ]

    print("=" * 70)
    print("PATTERN RESULTS")
    print("=" * 70)

    for pattern_name in patterns:

        pattern_results = [
            x
            for x in all_results
            if x["pattern"] == pattern_name
        ]

        p_total = len(pattern_results)

        p_success = sum(
            1
            for x in pattern_results
            if x["result"] == "SUCCESS"
        )

        p_failure = sum(
            1
            for x in pattern_results
            if x["result"] == "FAILURE"
        )

        p_completed = (
            p_success + p_failure
        )

        if p_completed > 0:

            p_success_rate = (
                p_success
                / p_completed
                * 100
            )

            p_failure_rate = (
                p_failure
                / p_completed
                * 100
            )

        else:

            p_success_rate = 0.0
            p_failure_rate = 0.0

        print()
        print(pattern_name)

        print(
            f"  Trades: {p_total}"
        )

        print(
            f"  Success: {p_success}"
        )

        print(
            f"  Failure: {p_failure}"
        )

        print(
            f"  Success Rate: "
            f"{p_success_rate:.2f}%"
        )

        print(
            f"  Failure Rate: "
            f"{p_failure_rate:.2f}%"
        )

    print()
    print("=" * 70)
    print("BACKTEST COMPLETED")
    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
