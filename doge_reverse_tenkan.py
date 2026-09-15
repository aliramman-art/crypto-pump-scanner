# ============================================================
# DOGE REVERSE-TENKAN v1.1
# ============================================================
#
# INDEPENDENT STRATEGY
#
# NOT CONNECTED TO VOLUME-KHAT
#
# MARKET:
#   Kraken Futures
#   DOGE/USD
#
# TIMEFRAME:
#   5 minutes
#
# LOOKBACK:
#   30 days
#
# IDEA:
#   When price moves unusually far away from Tenkan,
#   look for a reversal back toward Tenkan.
#
# CLOSED CANDLES ONLY
#
# NO REAL TRADING
# NO ORDERS
# NO TELEGRAM
#
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

RESOLUTION = "5m"

LOOKBACK_DAYS = 30

TENKAN_PERIOD = 9


# ------------------------------------------------------------
# DISTANCE THRESHOLDS TO TEST
# ------------------------------------------------------------

DISTANCE_THRESHOLDS = [
    0.50,
    0.75,
    1.00,
    1.25,
    1.50,
    2.00,
    2.50,
]


# ------------------------------------------------------------
# REVERSAL CONFIRMATION
# ------------------------------------------------------------

USE_REVERSAL_CONFIRMATION = True


# ------------------------------------------------------------
# RETURN WINDOWS
# ------------------------------------------------------------

RETURN_WINDOWS = [
    5,
    10,
    20,
]


MAX_HOLD_CANDLES = 20


# ------------------------------------------------------------
# TENKAN TOUCH BUFFER
# ------------------------------------------------------------

TENKAN_TOUCH_BUFFER_PCT = 0.05


# ------------------------------------------------------------
# SIGNAL COOLDOWN
# ------------------------------------------------------------

MIN_BARS_BETWEEN_SIGNALS = 3


# ============================================================
# KRAKEN FUTURES
# ============================================================

BASE_URL = "https://futures.kraken.com"


# ============================================================
# DOWNLOAD KRAKEN FUTURES CANDLES
# ============================================================

def get_ohlc(symbol, resolution, since, until):

    url = (
        f"{BASE_URL}"
        f"/api/charts/v1/trade/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "from": int(since),
        "to": int(until),
    }

    print()
    print("Requesting:")
    print(url)

    print()
    print("From:")
    print(
        datetime.fromtimestamp(
            since,
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    )

    print("To:")
    print(
        datetime.fromtimestamp(
            until,
            timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")
    )

    response = requests.get(
        url,
        params=params,
        timeout=60,
    )

    if response.status_code != 200:

        print()
        print("Kraken HTTP error:")
        print(response.status_code)

        print()
        print("Response:")
        print(response.text[:2000])

        response.raise_for_status()

    data = response.json()

    # --------------------------------------------------------
    # Kraken response
    # --------------------------------------------------------

    candles = data.get("candles", [])

    if not candles:

        raise RuntimeError(
            "Kraken returned no candles."
        )

    rows = []

    for candle in candles:

        try:

            timestamp = candle.get("time")

            if timestamp is None:
                continue

            rows.append({
                "timestamp": int(timestamp),
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": float(
                    candle.get("volume", 0)
                ),
            })

        except (
            KeyError,
            TypeError,
            ValueError
        ):

            continue

    if not rows:

        raise RuntimeError(
            "Kraken returned candles, "
            "but none could be parsed."
        )

    df = pd.DataFrame(rows)

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    )

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True,
    )

    df = df.set_index(
        "datetime"
    )

    # --------------------------------------------------------
    # Remove candles outside requested range
    # --------------------------------------------------------

    df = df[
        (df["timestamp"] >= since)
        &
        (df["timestamp"] <= until)
    ]

    return df


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    highest_high = (
        df["high"]
        .rolling(
            TENKAN_PERIOD
        )
        .max()
    )

    lowest_low = (
        df["low"]
        .rolling(
            TENKAN_PERIOD
        )
        .min()
    )

    df["tenkan"] = (
        highest_high
        + lowest_low
    ) / 2.0

    return df


# ============================================================
# DISTANCE FROM TENKAN
# ============================================================

def calculate_distance(df):

    df["distance_pct"] = (
        (
            df["close"]
            - df["tenkan"]
        ).abs()
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

def reversal_confirmed(
    df,
    index,
    side
):

    if index < 1:
        return False

    current = df.iloc[index]

    previous = df.iloc[index - 1]

    # ========================================================
    # LONG
    # ========================================================

    if side == "LONG":

        current_bullish = (
            current["close"]
            > current["open"]
        )

        previous_bearish = (
            previous["close"]
            < previous["open"]
        )

        close_recovered = (
            current["close"]
            > previous["close"]
        )

        return (
            current_bullish
            and previous_bearish
            and close_recovered
        )

    # ========================================================
    # SHORT
    # ========================================================

    if side == "SHORT":

        current_bearish = (
            current["close"]
            < current["open"]
        )

        previous_bullish = (
            previous["close"]
            > previous["open"]
        )

        close_rejected = (
            current["close"]
            < previous["close"]
        )

        return (
            current_bearish
            and previous_bullish
            and close_rejected
        )

    return False


# ============================================================
# TEST RETURN TO TENKAN
# ============================================================

def test_signal(
    df,
    entry_index,
    side
):

    entry = df.iloc[
        entry_index
    ]

    entry_price = (
        entry["close"]
    )

    entry_tenkan = (
        entry["tenkan"]
    )

    result = {

        "entry_index":
            entry_index,

        "entry_time":
            df.index[entry_index],

        "side":
            side,

        "entry_price":
            entry_price,

        "entry_tenkan":
            entry_tenkan,

        "max_favorable_pct":
            0.0,

        "max_adverse_pct":
            0.0,

        "return_5":
            False,

        "return_10":
            False,

        "return_20":
            False,

        "eventual_return":
            False,

        "bars_to_tenkan":
            None,
    }

    end_index = min(
        len(df),
        entry_index
        + MAX_HOLD_CANDLES
        + 1,
    )

    for j in range(
        entry_index + 1,
        end_index
    ):

        candle = df.iloc[j]

        # ====================================================
        # LONG
        # ====================================================

        if side == "LONG":

            favorable = (
                (
                    candle["high"]
                    - entry_price
                )
                / entry_price
                * 100.0
            )

            adverse = (
                (
                    candle["low"]
                    - entry_price
                )
                / entry_price
                * 100.0
            )

            result[
                "max_favorable_pct"
            ] = max(
                result[
                    "max_favorable_pct"
                ],
                favorable,
            )

            result[
                "max_adverse_pct"
            ] = min(
                result[
                    "max_adverse_pct"
                ],
                adverse,
            )

            tenkan_touch = (
                candle["high"]
                >= candle["tenkan"]
                * (
                    1
                    - TENKAN_TOUCH_BUFFER_PCT
                    / 100.0
                )
            )

        # ====================================================
        # SHORT
        # ====================================================

        else:

            favorable = (
                (
                    entry_price
                    - candle["low"]
                )
                / entry_price
                * 100.0
            )

            adverse = (
                (
                    entry_price
                    - candle["high"]
                )
                / entry_price
                * 100.0
            )

            result[
                "max_favorable_pct"
            ] = max(
                result[
                    "max_favorable_pct"
                ],
                favorable,
            )

            result[
                "max_adverse_pct"
            ] = min(
                result[
                    "max_adverse_pct"
                ],
                adverse,
            )

            tenkan_touch = (
                candle["low"]
                <= candle["tenkan"]
                * (
                    1
                    + TENKAN_TOUCH_BUFFER_PCT
                    / 100.0
                )
            )

        # ====================================================
        # TENKAN RETURN
        # ====================================================

        if tenkan_touch:

            bars = (
                j
                - entry_index
            )

            result[
                "eventual_return"
            ] = True

            result[
                "bars_to_tenkan"
            ] = bars

            if bars <= 5:

                result[
                    "return_5"
                ] = True

            if bars <= 10:

                result[
                    "return_10"
                ] = True

            if bars <= 20:

                result[
                    "return_20"
                ] = True

            break

    return result


# ============================================================
# BACKTEST ONE DISTANCE THRESHOLD
# ============================================================

def backtest_threshold(
    df,
    threshold
):

    signals = []

    last_signal_index = (
        -999999
    )

    for i in range(
        1,
        len(df)
    ):

        row = df.iloc[i]

        if pd.isna(
            row["tenkan"]
        ):
            continue

        distance = (
            row["distance_pct"]
        )

        if distance < threshold:
            continue

        side = row["side"]

        if side == "NONE":
            continue

        # ----------------------------------------------------
        # Prevent repeated signals
        # ----------------------------------------------------

        if (
            i
            - last_signal_index
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

        result[
            "threshold"
        ] = threshold

        signals.append(
            result
        )

        last_signal_index = i

    if not signals:

        return pd.DataFrame()

    return pd.DataFrame(
        signals
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    results
):

    if results.empty:

        return {

            "signals":
                0,

            "longs":
                0,

            "shorts":
                0,

            "return_5":
                0.0,

            "return_10":
                0.0,

            "return_20":
                0.0,

            "eventual_return":
                0.0,

            "avg_bars":
                None,

            "avg_favorable":
                0.0,

            "avg_adverse":
                0.0,
        }

    count = len(
        results
    )

    return {

        "signals":
            count,

        "longs":
            int(
                (
                    results["side"]
                    == "LONG"
                ).sum()
            ),

        "shorts":
            int(
                (
                    results["side"]
                    == "SHORT"
                ).sum()
            ),

        "return_5":
            results[
                "return_5"
            ].mean() * 100.0,

        "return_10":
            results[
                "return_10"
            ].mean() * 100.0,

        "return_20":
            results[
                "return_20"
            ].mean() * 100.0,

        "eventual_return":
            results[
                "eventual_return"
            ].mean() * 100.0,

        "avg_bars":
            results[
                "bars_to_tenkan"
            ]
            .dropna()
            .mean(),

        "avg_favorable":
            results[
                "max_favorable_pct"
            ].mean(),

        "avg_adverse":
            results[
                "max_adverse_pct"
            ].mean(),
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print()
    print("=" * 72)
    print("DOGE REVERSE-TENKAN v1.1")
    print("=" * 72)

    print(
        "Independent strategy"
    )

    print(
        "No VOLUME-KHAT connection"
    )

    print(
        "No real trading"
    )

    # ========================================================
    # TIME RANGE
    # ========================================================

    now = datetime.now(
        timezone.utc
    )

    start = (
        now
        - timedelta(
            days=LOOKBACK_DAYS
        )
    )

    since = int(
        start.timestamp()
    )

    until = int(
        now.timestamp()
    )

    print()
    print(
        f"Symbol       : {SYMBOL}"
    )

    print(
        f"Timeframe    : {RESOLUTION}"
    )

    print(
        f"Lookback     : "
        f"{LOOKBACK_DAYS} days"
    )

    print(
        f"Tenkan       : "
        f"{TENKAN_PERIOD}"
    )

    print(
        f"Confirmation : "
        f"{USE_REVERSAL_CONFIRMATION}"
    )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    print()
    print(
        "Downloading Kraken Futures data..."
    )

    df = get_ohlc(
        SYMBOL,
        RESOLUTION,
        since,
        until,
    )

    if df.empty:

        raise RuntimeError(
            "No market data available."
        )

    print()
    print(
        f"Candles received: "
        f"{len(df):,}"
    )

    # ========================================================
    # REMOVE POSSIBLE OPEN CANDLE
    # ========================================================

    current_time = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )

    candle_seconds = (
        5 * 60
    )

    if len(df) > 0:

        last_timestamp = int(
            df.iloc[-1]["timestamp"]
        )

        if (
            current_time
            - last_timestamp
            < candle_seconds
        ):

            print(
                "Removing current "
                "possibly-open candle."
            )

            df = df.iloc[:-1]

    # ========================================================
    # INDICATORS
    # ========================================================

    df = calculate_tenkan(
        df
    )

    df = calculate_distance(
        df
    )

    # ========================================================
    # THRESHOLD TEST
    # ========================================================

    summary = []

    all_results = {}

    for threshold in (
        DISTANCE_THRESHOLDS
    ):

        print()
        print(
            f"Testing threshold "
            f"{threshold:.2f}%..."
        )

        results = (
            backtest_threshold(
                df,
                threshold,
            )
        )

        all_results[
            threshold
        ] = results

        stats = (
            calculate_stats(
                results
            )
        )

        summary.append({

            "threshold":
                threshold,

            **stats,
        })

    summary_df = (
        pd.DataFrame(
            summary
        )
    )

    # ========================================================
    # SUMMARY TABLE
    # ========================================================

    print()
    print("=" * 72)
    print(
        "THRESHOLD COMPARISON"
    )
    print("=" * 72)

    print(
        f"{'Dist':>7} "
        f"{'Signals':>8} "
        f"{'LONG':>7} "
        f"{'SHORT':>7} "
        f"{'5B':>8} "
        f"{'10B':>8} "
        f"{'20B':>8} "
        f"{'Return':>9}"
    )

    print(
        "-" * 72
    )

    for _, row in (
        summary_df.iterrows()
    ):

        print(
            f"{row['threshold']:>6.2f}% "
            f"{int(row['signals']):>8} "
            f"{int(row['longs']):>7} "
            f"{int(row['shorts']):>7} "
            f"{row['return_5']:>7.1f}% "
            f"{row['return_10']:>7.1f}% "
            f"{row['return_20']:>7.1f}% "
            f"{row['eventual_return']:>8.1f}%"
        )

    # ========================================================
    # BEST THRESHOLD
    # ========================================================

    valid = summary_df[
        summary_df["signals"]
        >= 5
    ]

    if not valid.empty:

        best = (
            valid
            .sort_values(
                [
                    "return_10",
                    "eventual_return",
                ],
                ascending=False,
            )
            .iloc[0]
        )

        print()
        print("=" * 72)
        print(
            "BEST CANDIDATE"
        )
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
            f"LONG            : "
            f"{int(best['longs'])}"
        )

        print(
            f"SHORT           : "
            f"{int(best['shorts'])}"
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

        if pd.notna(
            best["avg_bars"]
        ):

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

    else:

        print()
        print("=" * 72)
        print(
            "NO VALID THRESHOLD"
        )
        print("=" * 72)

        print(
            "No threshold produced "
            "at least 5 signals."
        )

    # ========================================================
    # SAVE SUMMARY
    # ========================================================

    summary_file = (
        "doge_reverse_tenkan_summary.csv"
    )

    summary_df.to_csv(
        summary_file,
        index=False,
    )

    # ========================================================
    # SAVE INDIVIDUAL RESULTS
    # ========================================================

    for threshold, results in (
        all_results.items()
    ):

        filename = (
            "doge_reverse_tenkan_"
            f"{threshold:.2f}.csv"
        )

        results.to_csv(
            filename,
            index=False,
        )

    # ========================================================
    # CURRENT STATUS
    # ========================================================

    if not df.empty:

        latest = df.iloc[-1]

        print()
        print("=" * 72)
        print(
            "CURRENT DOGE STATUS"
        )
        print("=" * 72)

        print(
            f"Time       : "
            f"{df.index[-1]}"
        )

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
            f"Direction  : "
            f"{latest['side']}"
        )

    # ========================================================
    # FINISHED
    # ========================================================

    print()
    print("=" * 72)
    print(
        "BACKTEST COMPLETE"
    )
    print("=" * 72)

    print()
    print(
        "Saved:"
    )

    print(
        f"  {summary_file}"
    )

    print(
        "  doge_reverse_tenkan_*.csv"
    )

    print()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
