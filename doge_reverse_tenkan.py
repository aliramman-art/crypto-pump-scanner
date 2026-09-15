# ============================================================
# DOGE REVERSE-TENKAN v1.0
# ============================================================
#
# PURPOSE:
#   Independent strategy.
#   NOT connected to VOLUME-KHAT 100.
#
# IDEA:
#   When DOGE moves far enough away from Tenkan-sen,
#   look for a reversal back toward Tenkan.
#
# MARKET:
#   Kraken Futures
#   DOGE/USD
#   5M
#
# TEST:
#   Last 30 days
#   Multiple Tenkan-distance thresholds
#
# CLOSED CANDLES ONLY
# ============================================================

import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "PF_DOGEUSD"
INTERVAL_MINUTES = 5

LOOKBACK_DAYS = 30

# Ichimoku Tenkan period
TENKAN_PERIOD = 9

# Thresholds to test
DISTANCE_THRESHOLDS = [
    0.50,
    0.75,
    1.00,
    1.25,
    1.50,
    2.00,
    2.50,
]

# After extreme distance is reached,
# require a reversal candle before entry.
USE_REVERSAL_CONFIRMATION = True

# Maximum candles allowed for price to return to Tenkan
RETURN_WINDOWS = [
    5,
    10,
    20,
]

# Maximum candles to wait for an eventual result
MAX_HOLD_CANDLES = 20

# Small buffer around Tenkan to count as "reached"
TENKAN_TOUCH_BUFFER_PCT = 0.05

# Prevent duplicate signals during the same excursion
MIN_BARS_BETWEEN_SIGNALS = 3


# ============================================================
# KRAKEN FUTURES API
# ============================================================

BASE_URL = "https://futures.kraken.com/derivatives/api/v3"


def get_ohlc(symbol, interval, since, until):
    """
    Download Kraken Futures OHLC data.

    Kraken may return data in chunks, so pagination is handled
    by repeatedly requesting subsequent periods.
    """

    all_rows = []

    current_since = int(since)
    final_until = int(until)

    while current_since < final_until:

        params = {
            "symbol": symbol,
            "interval": interval,
            "since": current_since,
        }

        response = requests.get(
            f"{BASE_URL}/ohlc",
            params=params,
            timeout=20,
        )

        response.raise_for_status()
        data = response.json()

        candles = data.get("candles", [])

        if not candles:
            break

        for candle in candles:

            # Kraken Futures candle format can vary.
            # Handle the standard fields defensively.

            ts = candle.get("time")

            if ts is None:
                ts = candle.get("timestamp")

            if ts is None:
                continue

            ts = int(ts)

            if ts > final_until:
                continue

            all_rows.append({
                "timestamp": ts,
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(candle.get("volume", 0)),
            })

        last_ts = max(
            int(c.get("time", c.get("timestamp", 0)))
            for c in candles
        )

        if last_ts <= current_since:
            break

        current_since = last_ts + INTERVAL_MINUTES * 60

        time.sleep(0.15)

    if not all_rows:
        raise RuntimeError("No OHLC data returned from Kraken.")

    df = pd.DataFrame(all_rows)

    df = df.drop_duplicates("timestamp")
    df = df.sort_values("timestamp")

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True,
    )

    df = df.set_index("datetime")

    # Keep only requested period
    df = df[
        (df["timestamp"] >= since) &
        (df["timestamp"] <= until)
    ]

    return df


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    highest_high = (
        df["high"]
        .rolling(TENKAN_PERIOD)
        .max()
    )

    lowest_low = (
        df["low"]
        .rolling(TENKAN_PERIOD)
        .min()
    )

    df["tenkan"] = (
        highest_high + lowest_low
    ) / 2.0

    return df


# ============================================================
# DISTANCE
# ============================================================

def calculate_distance(df):

    df["distance_pct"] = (
        (df["close"] - df["tenkan"])
        .abs()
        / df["tenkan"]
        * 100.0
    )

    df["side"] = np.where(
        df["close"] > df["tenkan"],
        "SHORT",
        np.where(
            df["close"] < df["tenkan"],
            "LONG",
            "NONE",
        ),
    )

    return df


# ============================================================
# REVERSAL CONFIRMATION
# ============================================================

def reversal_confirmed(df, i, side):

    if i < 1:
        return False

    current = df.iloc[i]
    previous = df.iloc[i - 1]

    # LONG:
    # Price is below Tenkan and current candle shows upward
    # reversal pressure.

    if side == "LONG":

        current_bullish = current["close"] > current["open"]

        previous_bearish = previous["close"] < previous["open"]

        close_recovered = (
            current["close"] > previous["close"]
        )

        return (
            current_bullish
            and previous_bearish
            and close_recovered
        )

    # SHORT:
    # Price is above Tenkan and current candle shows downward
    # reversal pressure.

    if side == "SHORT":

        current_bearish = current["close"] < current["open"]

        previous_bullish = previous["close"] > previous["open"]

        close_rejected = (
            current["close"] < previous["close"]
        )

        return (
            current_bearish
            and previous_bullish
            and close_rejected
        )

    return False


# ============================================================
# TENKAN RETURN TEST
# ============================================================

def test_signal(df, entry_index, side):

    entry = df.iloc[entry_index]
    entry_price = entry["close"]
    entry_tenkan = entry["tenkan"]

    result = {
        "entry_index": entry_index,
        "entry_time": df.index[entry_index],
        "side": side,
        "entry_price": entry_price,
        "entry_tenkan": entry_tenkan,
        "max_favorable_pct": 0.0,
        "max_adverse_pct": 0.0,
        "return_5": False,
        "return_10": False,
        "return_20": False,
        "eventual_return": False,
        "bars_to_tenkan": None,
    }

    end = min(
        len(df),
        entry_index + MAX_HOLD_CANDLES + 1,
    )

    for j in range(entry_index + 1, end):

        candle = df.iloc[j]

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "LONG":

            favorable = (
                (candle["high"] - entry_price)
                / entry_price
                * 100
            )

            adverse = (
                (candle["low"] - entry_price)
                / entry_price
                * 100
            )

            result["max_favorable_pct"] = max(
                result["max_favorable_pct"],
                favorable,
            )

            result["max_adverse_pct"] = min(
                result["max_adverse_pct"],
                adverse,
            )

            tenkan_touch = (
                candle["high"]
                >= candle["tenkan"]
                * (
                    1
                    - TENKAN_TOUCH_BUFFER_PCT / 100
                )
            )

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            favorable = (
                (entry_price - candle["low"])
                / entry_price
                * 100
            )

            adverse = (
                (entry_price - candle["high"])
                / entry_price
                * 100
            )

            result["max_favorable_pct"] = max(
                result["max_favorable_pct"],
                favorable,
            )

            result["max_adverse_pct"] = min(
                result["max_adverse_pct"],
                adverse,
            )

            tenkan_touch = (
                candle["low"]
                <= candle["tenkan"]
                * (
                    1
                    + TENKAN_TOUCH_BUFFER_PCT / 100
                )
            )

        # ----------------------------------------------------
        # RETURN TO TENKAN
        # ----------------------------------------------------

        if tenkan_touch:

            bars = j - entry_index

            result["eventual_return"] = True

            result["bars_to_tenkan"] = bars

            if bars <= 5:
                result["return_5"] = True

            if bars <= 10:
                result["return_10"] = True

            if bars <= 20:
                result["return_20"] = True

            break

    return result


# ============================================================
# BACKTEST ONE THRESHOLD
# ============================================================

def backtest_threshold(df, threshold):

    signals = []

    last_signal_index = -999999

    for i in range(1, len(df)):

        row = df.iloc[i]

        if pd.isna(row["tenkan"]):
            continue

        distance = row["distance_pct"]

        if distance < threshold:
            continue

        side = row["side"]

        if side == "NONE":
            continue

        # Avoid repeatedly entering during the same excursion.
        if (
            i - last_signal_index
            < MIN_BARS_BETWEEN_SIGNALS
        ):
            continue

        # ----------------------------------------------------
        # Reversal confirmation
        # ----------------------------------------------------

        if USE_REVERSAL_CONFIRMATION:

            if not reversal_confirmed(
                df,
                i,
                side,
            ):
                continue

        result = test_signal(
            df,
            i,
            side,
        )

        result["threshold"] = threshold

        signals.append(result)

        last_signal_index = i

    return pd.DataFrame(signals)


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(results):

    if results.empty:

        return {
            "signals": 0,
            "return_5": 0.0,
            "return_10": 0.0,
            "return_20": 0.0,
            "eventual_return": 0.0,
            "avg_bars": None,
            "avg_favorable": 0.0,
            "avg_adverse": 0.0,
        }

    count = len(results)

    return {
        "signals": count,

        "return_5":
            results["return_5"].mean() * 100,

        "return_10":
            results["return_10"].mean() * 100,

        "return_20":
            results["return_20"].mean() * 100,

        "eventual_return":
            results["eventual_return"].mean() * 100,

        "avg_bars":
            results["bars_to_tenkan"]
            .dropna()
            .mean(),

        "avg_favorable":
            results["max_favorable_pct"].mean(),

        "avg_adverse":
            results["max_adverse_pct"].mean(),
    }


# ============================================================
# MAIN
# ============================================================

def main():

    now = datetime.now(timezone.utc)

    start = now - timedelta(
        days=LOOKBACK_DAYS
    )

    since = int(start.timestamp())
    until = int(now.timestamp())

    print()
    print("=" * 72)
    print("DOGE REVERSE-TENKAN v1.0")
    print("=" * 72)

    print(f"Symbol       : {SYMBOL}")
    print(f"Timeframe    : {INTERVAL_MINUTES}M")
    print(f"Lookback     : {LOOKBACK_DAYS} days")
    print(f"Tenkan       : {TENKAN_PERIOD}")
    print(
        f"Confirmation : "
        f"{USE_REVERSAL_CONFIRMATION}"
    )

    print()
    print("Downloading Kraken Futures data...")

    df = get_ohlc(
        SYMBOL,
        INTERVAL_MINUTES,
        since,
        until,
    )

    print(
        f"Candles received: {len(df):,}"
    )

    df = calculate_tenkan(df)
    df = calculate_distance(df)

    print()

    # ========================================================
    # TEST ALL THRESHOLDS
    # ========================================================

    summary = []

    all_results = {}

    for threshold in DISTANCE_THRESHOLDS:

        results = backtest_threshold(
            df,
            threshold,
        )

        all_results[threshold] = results

        stats = calculate_stats(results)

        summary.append({
            "threshold": threshold,
            **stats,
        })

    summary_df = pd.DataFrame(summary)

    # ========================================================
    # DISPLAY
    # ========================================================

    print("=" * 72)
    print("THRESHOLD COMPARISON")
    print("=" * 72)

    print(
        f"{'Dist':>7} "
        f"{'Signals':>8} "
        f"{'5B':>8} "
        f"{'10B':>8} "
        f"{'20B':>8} "
        f"{'Return':>9} "
        f"{'AvgFav':>9} "
        f"{'AvgAdv':>9}"
    )

    print("-" * 72)

    for _, row in summary_df.iterrows():

        print(
            f"{row['threshold']:>6.2f}% "
            f"{int(row['signals']):>8} "
            f"{row['return_5']:>7.1f}% "
            f"{row['return_10']:>7.1f}% "
            f"{row['return_20']:>7.1f}% "
            f"{row['eventual_return']:>8.1f}% "
            f"{row['avg_favorable']:>8.2f}% "
            f"{row['avg_adverse']:>8.2f}%"
        )

    # ========================================================
    # BEST THRESHOLD
    # ========================================================

    valid = summary_df[
        summary_df["signals"] >= 5
    ]

    if not valid.empty:

        best = valid.sort_values(
            [
                "return_10",
                "eventual_return",
            ],
            ascending=False,
        ).iloc[0]

        print()
        print("=" * 72)
        print("BEST CANDIDATE")
        print("=" * 72)

        print(
            f"Threshold       : "
            f"{best['threshold']:.2f}%"
        )

        print(
            f"Signals         : "
            f"{int(best['signals'])}"
        )

        print(
            f"Return <= 5B    : "
            f"{best['return_5']:.1f}%"
        )

        print(
            f"Return <= 10B   : "
            f"{best['return_10']:.1f}%"
        )

        print(
            f"Return <= 20B   : "
            f"{best['return_20']:.1f}%"
        )

        print(
            f"Eventual return : "
            f"{best['eventual_return']:.1f}%"
        )

        if pd.notna(best["avg_bars"]):

            print(
                f"Avg bars        : "
                f"{best['avg_bars']:.2f}"
            )

        print(
            f"Avg favorable   : "
            f"{best['avg_favorable']:.2f}%"
        )

        print(
            f"Avg adverse     : "
            f"{best['avg_adverse']:.2f}%"
        )

    # ========================================================
    # SAVE RESULTS
    # ========================================================

    summary_df.to_csv(
        "doge_reverse_tenkan_summary.csv",
        index=False,
    )

    for threshold, results in all_results.items():

        filename = (
            "doge_reverse_tenkan_"
            f"{threshold:.2f}.csv"
        )

        results.to_csv(
            filename,
            index=False,
        )

    print()
    print("Files saved:")
    print("  doge_reverse_tenkan_summary.csv")
    print("  doge_reverse_tenkan_*.csv")
    print()

    # ========================================================
    # CURRENT LIVE SETUP
    # ========================================================

    latest = df.iloc[-1]

    print("=" * 72)
    print("CURRENT DOGE STATUS")
    print("=" * 72)

    print(
        f"Price      : "
        f"{latest['close']:.8f}"
    )

    print(
        f"Tenkan     : "
        f"{latest['tenkan']:.8f}"
    )

    print(
        f"Distance   : "
        f"{latest['distance_pct']:.3f}%"
    )

    print(
        f"Direction   : "
        f"{latest['side']}"
    )

    print("=" * 72)


if __name__ == "__main__":
    main()
