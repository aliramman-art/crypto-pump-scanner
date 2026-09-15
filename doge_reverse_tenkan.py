# ============================================================
# DOGE REVERSE-TENKAN v1.2
# ============================================================
#
# Independent mean-reversion research strategy
#
# SYMBOL       : Kraken Futures PF_DOGEUSD
# TIMEFRAME    : 5M
# LOOKBACK     : 30 days
# TENKAN       : 9
#
# IDEA:
#   Price sufficiently ABOVE Tenkan -> SHORT
#   Price sufficiently BELOW Tenkan -> LONG
#
# TARGET:
#   Return toward the Tenkan level that existed at entry.
#
# NO VOLUME-KHAT CONNECTION
# NO REAL TRADING
# NO TELEGRAM
#
# v1.2:
#   - Kraken Futures Charts API
#   - 7-day chunked data download
#   - Duplicate removal
#   - Closed candles only
#   - Multiple distance thresholds
#   - Re-entry protection
#   - Return-to-Tenkan statistics
# ============================================================

import time
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "PF_DOGEUSD"

RESOLUTION = "5m"

LOOKBACK_DAYS = 30

TENKAN_PERIOD = 9

THRESHOLDS = [
    0.50,
    0.75,
    1.00,
    1.25,
    1.50,
    2.00,
    2.50,
]

RETURN_WINDOWS = [
    5,
    10,
    20,
]

TENKAN_TOUCH_BUFFER_PCT = 0.05

CONFIRMATION_ENABLED = True

BASE_URL = (
    "https://futures.kraken.com/"
    "api/charts/v1/trade"
)

REQUEST_TIMEOUT = 30

CHUNK_DAYS = 7

SUMMARY_FILE = (
    "doge_reverse_tenkan_summary.csv"
)


# ============================================================
# BASIC HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def ts_to_datetime(ts):
    return datetime.fromtimestamp(
        ts,
        timezone.utc
    )


# ============================================================
# HEADER
# ============================================================

def print_header():

    print("=" * 72)
    print("DOGE REVERSE-TENKAN v1.2")
    print("=" * 72)

    print("Independent strategy")
    print("No VOLUME-KHAT connection")
    print("No real trading")
    print("No Telegram")
    print()

    print(f"Symbol       : {SYMBOL}")
    print(f"Timeframe    : {RESOLUTION}")
    print(f"Lookback     : {LOOKBACK_DAYS} days")
    print(f"Tenkan       : {TENKAN_PERIOD}")
    print(
        f"Confirmation : "
        f"{CONFIRMATION_ENABLED}"
    )

    print(
        "Thresholds   : "
        + ", ".join(
            f"{x:.2f}%"
            for x in THRESHOLDS
        )
    )

    print("=" * 72)


# ============================================================
# KRAKEN CHUNK DOWNLOAD
# ============================================================

def fetch_chunk(
    session,
    start_ts,
    end_ts,
):
    """
    Download one chunk of Kraken Futures candles.
    """

    endpoint = (
        f"{BASE_URL}/"
        f"{SYMBOL}/"
        f"{RESOLUTION}"
    )

    params = {
        "from": start_ts,
        "to": end_ts,
    }

    print()
    print("Requesting:")
    print(endpoint)

    print(
        f"From: {ts_to_datetime(start_ts)}"
    )

    print(
        f"To  : {ts_to_datetime(end_ts)}"
    )

    try:

        response = session.get(
            endpoint,
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent": (
                    "DOGE-Reverse-Tenkan/1.2"
                ),
            },
            timeout=REQUEST_TIMEOUT,
        )

        print(
            f"HTTP status: "
            f"{response.status_code}"
        )

        if response.status_code != 200:

            print(
                "Kraken response:"
            )

            print(
                response.text[:2000]
            )

            return []

        data = response.json()

    except requests.RequestException as exc:

        print(
            f"Request error: {exc}"
        )

        return []

    except ValueError as exc:

        print(
            f"JSON decode error: {exc}"
        )

        print(
            response.text[:2000]
        )

        return []

    if not isinstance(data, dict):

        print(
            "Unexpected Kraken response."
        )

        return []

    candles = data.get(
        "candles",
        []
    )

    if not candles:

        print(
            "No candles returned "
            "for this chunk."
        )

        if "error" in data:

            print(
                f"Kraken error: "
                f"{data['error']}"
            )

        return []

    print(
        f"Received {len(candles)} candles."
    )

    return candles


# ============================================================
# DOWNLOAD FULL LOOKBACK
# ============================================================

def fetch_kraken_futures_candles():

    print()
    print(
        "Downloading Kraken Futures data..."
    )

    now_ts = int(
        time.time()
    )

    start_ts = (
        now_ts
        - LOOKBACK_DAYS * 24 * 60 * 60
    )

    chunk_seconds = (
        CHUNK_DAYS
        * 24
        * 60
        * 60
    )

    all_candles = []

    current_start = start_ts

    session = requests.Session()

    chunk_number = 0

    while current_start < now_ts:

        chunk_number += 1

        current_end = min(
            current_start + chunk_seconds,
            now_ts,
        )

        print()
        print(
            "-" * 72
        )

        print(
            f"CHUNK {chunk_number}"
        )

        candles = fetch_chunk(
            session,
            current_start,
            current_end,
        )

        if candles:

            all_candles.extend(
                candles
            )

        current_start = current_end

        time.sleep(0.30)

    if not all_candles:

        raise RuntimeError(
            "No market data available "
            "from Kraken Futures."
        )

    print()
    print(
        f"Raw candles received: "
        f"{len(all_candles)}"
    )

    # --------------------------------------------------------
    # DataFrame
    # --------------------------------------------------------

    df = pd.DataFrame(
        all_candles
    )

    if df.empty:

        raise RuntimeError(
            "Kraken returned an empty "
            "DataFrame."
        )

    print(
        "Returned columns:"
    )

    print(
        list(df.columns)
    )

    required_columns = [
        "time",
        "open",
        "high",
        "low",
        "close",
    ]

    missing = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing:

        raise RuntimeError(
            "Kraken response is missing "
            f"required columns: {missing}"
        )

    # --------------------------------------------------------
    # Numeric conversion
    # --------------------------------------------------------

    df["time"] = pd.to_numeric(
        df["time"],
        errors="coerce",
    )

    for column in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        if column in df.columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    df = df.dropna(
        subset=[
            "time",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    # --------------------------------------------------------
    # Timestamp conversion
    # --------------------------------------------------------

    median_time = df["time"].median()

    if median_time < 10_000_000_000:

        df["time"] = pd.to_datetime(
            df["time"],
            unit="s",
            utc=True,
        )

    else:

        df["time"] = pd.to_datetime(
            df["time"],
            unit="ms",
            utc=True,
        )

    # --------------------------------------------------------
    # Sort and deduplicate
    # --------------------------------------------------------

    df = df.sort_values(
        "time"
    )

    df = df.drop_duplicates(
        subset=["time"],
        keep="last",
    )

    df = df.reset_index(
        drop=True
    )

    # --------------------------------------------------------
    # Lookback filter
    # --------------------------------------------------------

    cutoff = pd.Timestamp(
        ts_to_datetime(start_ts)
    )

    df = df[
        df["time"] >= cutoff
    ].copy()

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    timeframe_seconds = 5 * 60

    current_candle_start_ts = (
        int(
            now_ts
            // timeframe_seconds
        )
        * timeframe_seconds
    )

    current_candle_start = pd.to_datetime(
        current_candle_start_ts,
        unit="s",
        utc=True,
    )

    df = df[
        df["time"]
        < current_candle_start
    ].copy()

    df = df.reset_index(
        drop=True
    )

    # --------------------------------------------------------
    # Diagnostics
    # --------------------------------------------------------

    print()
    print("=" * 72)

    print(
        f"Total valid CLOSED candles: "
        f"{len(df)}"
    )

    if not df.empty:

        print(
            f"First candle: "
            f"{df['time'].iloc[0]}"
        )

        print(
            f"Last candle : "
            f"{df['time'].iloc[-1]}"
        )

    print("=" * 72)

    expected = (
        LOOKBACK_DAYS
        * 24
        * 12
    )

    minimum_expected = int(
        expected * 0.90
    )

    if len(df) < minimum_expected:

        raise RuntimeError(
            "Insufficient market data.\n"
            f"Expected approximately "
            f"{expected} candles.\n"
            f"Received {len(df)} candles.\n"
            "Dataset completeness is below 90%."
        )

    return df


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    df = df.copy()

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

    df["distance_pct"] = (
        (
            df["close"]
            - df["tenkan"]
        )
        / df["tenkan"]
    ) * 100.0

    return df


# ============================================================
# CONFIRMATION
# ============================================================

def confirmation_long(
    df,
    index,
):

    if index <= 0:

        return False

    current = df.iloc[index]
    previous = df.iloc[index - 1]

    current_bullish = (
        current["close"]
        > current["open"]
    )

    previous_bearish = (
        previous["close"]
        < previous["open"]
    )

    higher_close = (
        current["close"]
        > previous["close"]
    )

    return (
        current_bullish
        and previous_bearish
        and higher_close
    )


def confirmation_short(
    df,
    index,
):

    if index <= 0:

        return False

    current = df.iloc[index]
    previous = df.iloc[index - 1]

    current_bearish = (
        current["close"]
        < current["open"]
    )

    previous_bullish = (
        previous["close"]
        > previous["open"]
    )

    lower_close = (
        current["close"]
        < previous["close"]
    )

    return (
        current_bearish
        and previous_bullish
        and lower_close
    )


# ============================================================
# SIGNAL
# ============================================================

def detect_signal(
    df,
    index,
    threshold,
):

    row = df.iloc[index]

    distance = row[
        "distance_pct"
    ]

    if pd.isna(distance):

        return None

    # Price below Tenkan
    # -> LONG

    if distance <= -threshold:

        if not CONFIRMATION_ENABLED:

            return "LONG"

        if confirmation_long(
            df,
            index,
        ):

            return "LONG"

    # Price above Tenkan
    # -> SHORT

    if distance >= threshold:

        if not CONFIRMATION_ENABLED:

            return "SHORT"

        if confirmation_short(
            df,
            index,
        ):

            return "SHORT"

    return None


# ============================================================
# TENKAN RETURN
# ============================================================

def tenkan_returned(
    future_row,
    target_tenkan,
):

    if pd.isna(
        target_tenkan
    ):

        return False

    buffer = (
        abs(target_tenkan)
        * TENKAN_TOUCH_BUFFER_PCT
        / 100.0
    )

    lower = (
        target_tenkan
        - buffer
    )

    upper = (
        target_tenkan
        + buffer
    )

    return (
        future_row["low"]
        <= upper
        and
        future_row["high"]
        >= lower
    )


# ============================================================
# EXCURSION
# ============================================================

def calculate_excursions(
    df,
    entry_index,
    side,
    entry_price,
    target_tenkan,
):

    max_favorable = 0.0

    max_adverse = 0.0

    returned = False

    return_bars = None

    end_index = len(df) - 1

    for j in range(
        entry_index + 1,
        len(df),
    ):

        row = df.iloc[j]

        if side == "LONG":

            favorable = (
                (
                    row["high"]
                    - entry_price
                )
                / entry_price
            ) * 100.0

            adverse = (
                (
                    row["low"]
                    - entry_price
                )
                / entry_price
            ) * 100.0

        else:

            favorable = (
                (
                    entry_price
                    - row["low"]
                )
                / entry_price
            ) * 100.0

            adverse = (
                (
                    entry_price
                    - row["high"]
                )
                / entry_price
            ) * 100.0

        max_favorable = max(
            max_favorable,
            favorable,
        )

        max_adverse = min(
            max_adverse,
            adverse,
        )

        if tenkan_returned(
            row,
            target_tenkan,
        ):

            returned = True

            return_bars = (
                j - entry_index
            )

            end_index = j

            break

    return (
        returned,
        return_bars,
        max_favorable,
        max_adverse,
        end_index,
    )


# ============================================================
# BACKTEST
# ============================================================

def backtest_threshold(
    df,
    threshold,
):

    trades = []

    i = TENKAN_PERIOD

    while i < len(df) - 1:

        signal = detect_signal(
            df,
            i,
            threshold,
        )

        if signal is None:

            i += 1

            continue

        entry_row = df.iloc[i]

        entry_price = float(
            entry_row["close"]
        )

        entry_tenkan = float(
            entry_row["tenkan"]
        )

        entry_distance = float(
            entry_row["distance_pct"]
        )

        (
            returned,
            return_bars,
            mfe,
            mae,
            end_index,
        ) = calculate_excursions(
            df,
            i,
            signal,
            entry_price,
            entry_tenkan,
        )

        within_5 = (
            return_bars is not None
            and return_bars <= 5
        )

        within_10 = (
            return_bars is not None
            and return_bars <= 10
        )

        within_20 = (
            return_bars is not None
            and return_bars <= 20
        )

        exit_row = df.iloc[
            end_index
        ]

        if returned:

            if signal == "LONG":

                return_pct = (
                    (
                        entry_tenkan
                        - entry_price
                    )
                    / entry_price
                ) * 100.0

            else:

                return_pct = (
                    (
                        entry_price
                        - entry_tenkan
                    )
                    / entry_price
                ) * 100.0

        else:

            if signal == "LONG":

                return_pct = (
                    (
                        exit_row["close"]
                        - entry_price
                    )
                    / entry_price
                ) * 100.0

            else:

                return_pct = (
                    (
                        entry_price
                        - exit_row["close"]
                    )
                    / entry_price
                ) * 100.0

        trades.append(
            {
                "threshold_pct": threshold,
                "entry_time": entry_row["time"],
                "exit_time": exit_row["time"],
                "side": signal,
                "entry_price": entry_price,
                "entry_tenkan": entry_tenkan,
                "entry_distance_pct": entry_distance,
                "returned_to_tenkan": returned,
                "return_bars": return_bars,
                "within_5_bars": within_5,
                "within_10_bars": within_10,
                "within_20_bars": within_20,
                "return_pct": return_pct,
                "mfe_pct": mfe,
                "mae_pct": mae,
            }
        )

        # ----------------------------------------------------
        # Re-arm only after price comes back inside
        # the threshold.
        # ----------------------------------------------------

        i += 1

        while i < len(df):

            next_distance = (
                df.iloc[i][
                    "distance_pct"
                ]
            )

            if pd.isna(
                next_distance
            ):

                i += 1

                continue

            if abs(
                next_distance
            ) < threshold:

                break

            i += 1

    return pd.DataFrame(
        trades
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(
    trades,
    threshold,
):

    if trades.empty:

        return {
            "threshold_pct": threshold,
            "signals": 0,
            "longs": 0,
            "shorts": 0,
            "returned_any": 0,
            "return_rate_pct": 0.0,
            "returned_5_bars": 0,
            "return_rate_5_pct": 0.0,
            "returned_10_bars": 0,
            "return_rate_10_pct": 0.0,
            "returned_20_bars": 0,
            "return_rate_20_pct": 0.0,
            "avg_return_bars": np.nan,
            "median_return_bars": np.nan,
            "avg_return_pct": np.nan,
            "median_return_pct": np.nan,
            "avg_mfe_pct": np.nan,
            "avg_mae_pct": np.nan,
        }

    total = len(
        trades
    )

    longs = int(
        (
            trades["side"]
            == "LONG"
        ).sum()
    )

    shorts = int(
        (
            trades["side"]
            == "SHORT"
        ).sum()
    )

    returned_any = int(
        trades[
            "returned_to_tenkan"
        ].sum()
    )

    returned_5 = int(
        trades[
            "within_5_bars"
        ].sum()
    )

    returned_10 = int(
        trades[
            "within_10_bars"
        ].sum()
    )

    returned_20 = int(
        trades[
            "within_20_bars"
        ].sum()
    )

    valid_return_bars = trades[
        "return_bars"
    ].dropna()

    return {
        "threshold_pct": threshold,
        "signals": total,
        "longs": longs,
        "shorts": shorts,
        "returned_any": returned_any,

        "return_rate_pct": (
            returned_any
            / total
            * 100.0
        ),

        "returned_5_bars": returned_5,

        "return_rate_5_pct": (
            returned_5
            / total
            * 100.0
        ),

        "returned_10_bars": returned_10,

        "return_rate_10_pct": (
            returned_10
            / total
            * 100.0
        ),

        "returned_20_bars": returned_20,

        "return_rate_20_pct": (
            returned_20
            / total
            * 100.0
        ),

        "avg_return_bars": (
            valid_return_bars.mean()
            if not valid_return_bars.empty
            else np.nan
        ),

        "median_return_bars": (
            valid_return_bars.median()
            if not valid_return_bars.empty
            else np.nan
        ),

        "avg_return_pct": (
            trades["return_pct"].mean()
        ),

        "median_return_pct": (
            trades["return_pct"].median()
        ),

        "avg_mfe_pct": (
            trades["mfe_pct"].mean()
        ),

        "avg_mae_pct": (
            trades["mae_pct"].mean()
        ),
    }


# ============================================================
# CURRENT STATUS
# ============================================================

def print_current_status(df):

    if df.empty:

        return

    row = df.iloc[-1]

    price = float(
        row["close"]
    )

    tenkan = float(
        row["tenkan"]
    )

    distance = float(
        row["distance_pct"]
    )

    print()
    print("=" * 72)
    print("CURRENT DOGE STATUS")
    print("=" * 72)

    print(
        f"Time     : {row['time']}"
    )

    print(
        f"Price    : {price:.8f}"
    )

    print(
        f"Tenkan   : {tenkan:.8f}"
    )

    print(
        f"Distance : {distance:+.3f}%"
    )

    print()

    for threshold in THRESHOLDS:

        if distance >= threshold:

            print(
                f"{threshold:>5.2f}% "
                f"-> SHORT condition"
            )

        elif distance <= -threshold:

            print(
                f"{threshold:>5.2f}% "
                f"-> LONG condition"
            )

        else:

            print(
                f"{threshold:>5.2f}% "
                f"-> No signal"
            )

    print("=" * 72)


# ============================================================
# MAIN
# ============================================================

def main():

    print_header()

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    df = (
        fetch_kraken_futures_candles()
    )

    if df.empty:

        raise RuntimeError(
            "No market data available."
        )

    # --------------------------------------------------------
    # TENKAN
    # --------------------------------------------------------

    print()
    print(
        "Calculating Tenkan..."
    )

    df = calculate_tenkan(
        df
    )

    valid_tenkan = int(
        df["tenkan"]
        .notna()
        .sum()
    )

    print(
        f"Valid Tenkan candles: "
        f"{valid_tenkan}"
    )

    if valid_tenkan == 0:

        raise RuntimeError(
            "Unable to calculate Tenkan."
        )

    # --------------------------------------------------------
    # CURRENT STATUS
    # --------------------------------------------------------

    print_current_status(
        df
    )

    # --------------------------------------------------------
    # BACKTEST
    # --------------------------------------------------------

    all_summaries = []

    for threshold in THRESHOLDS:

        print()
        print("=" * 72)

        print(
            f"BACKTEST "
            f"THRESHOLD = "
            f"{threshold:.2f}%"
        )

        print("=" * 72)

        trades = (
            backtest_threshold(
                df,
                threshold,
            )
        )

        stats = (
            calculate_statistics(
                trades,
                threshold,
            )
        )

        all_summaries.append(
            stats
        )

        # ----------------------------------------------------
        # Save trade details
        # ----------------------------------------------------

        filename = (
            "doge_reverse_tenkan_"
            f"{threshold:.2f}.csv"
        )

        trades.to_csv(
            filename,
            index=False,
        )

        print(
            f"Signals       : "
            f"{stats['signals']}"
        )

        print(
            f"LONG          : "
            f"{stats['longs']}"
        )

        print(
            f"SHORT         : "
            f"{stats['shorts']}"
        )

        print(
            f"Return any    : "
            f"{stats['return_rate_pct']:.2f}%"
        )

        print(
            f"Return <= 5   : "
            f"{stats['return_rate_5_pct']:.2f}%"
        )

        print(
            f"Return <= 10  : "
            f"{stats['return_rate_10_pct']:.2f}%"
        )

        print(
            f"Return <= 20  : "
            f"{stats['return_rate_20_pct']:.2f}%"
        )

        if not pd.isna(
            stats["avg_return_bars"]
        ):

            print(
                f"Avg bars      : "
                f"{stats['avg_return_bars']:.2f}"
            )

        print(
            f"Avg return    : "
            f"{stats['avg_return_pct']:.3f}%"
        )

        print(
            f"Avg MFE       : "
            f"{stats['avg_mfe_pct']:.3f}%"
        )

        print(
            f"Avg MAE       : "
            f"{stats['avg_mae_pct']:.3f}%"
        )

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    summary = pd.DataFrame(
        all_summaries
    )

    summary.to_csv(
        SUMMARY_FILE,
        index=False,
    )

    # --------------------------------------------------------
    # COMPARISON
    # --------------------------------------------------------

    print()
    print()
    print("=" * 100)
    print(
        "DOGE REVERSE-TENKAN "
        "THRESHOLD COMPARISON"
    )
    print("=" * 100)

    display_columns = [
        "threshold_pct",
        "signals",
        "longs",
        "shorts",
        "return_rate_pct",
        "return_rate_5_pct",
        "return_rate_10_pct",
        "return_rate_20_pct",
        "avg_return_bars",
        "avg_return_pct",
        "avg_mfe_pct",
        "avg_mae_pct",
    ]

    display_df = summary[
        display_columns
    ].copy()

    print(
        display_df.to_string(
            index=False,
            float_format=lambda x:
                f"{x:.3f}"
        )
    )

    print()
    print("=" * 100)

    # --------------------------------------------------------
    # BEST THRESHOLD
    # --------------------------------------------------------

    if not summary.empty:

        best = summary.loc[
            summary[
                "return_rate_10_pct"
            ].idxmax()
        ]

        print(
            "BEST THRESHOLD "
            "BY 10-BAR RETURN RATE"
        )

        print(
            f"Threshold : "
            f"{best['threshold_pct']:.2f}%"
        )

        print(
            f"Signals   : "
            f"{int(best['signals'])}"
        )

        print(
            f"Return <=10 bars : "
            f"{best['return_rate_10_pct']:.2f}%"
        )

        print(
            f"Return any       : "
            f"{best['return_rate_pct']:.2f}%"
        )

    print()
    print(
        f"Summary saved to: "
        f"{SUMMARY_FILE}"
    )

    print()
    print("=" * 72)
    print(
        "BACKTEST COMPLETE"
    )
    print("=" * 72)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
