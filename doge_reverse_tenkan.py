# ============================================================
# DOGE REVERSE-TENKAN v1.3.1
# ============================================================
#
# Independent research strategy
#
# Kraken Futures
# Symbol       : PF_DOGEUSD
# Timeframe    : 5m
# Lookback     : 30 days
#
# ENTRY LOGIC
#   LONG  = price sufficiently BELOW entry-time Tenkan
#   SHORT = price sufficiently ABOVE entry-time Tenkan
#
# TARGET
#   Entry-time Tenkan
#
# v1.3.1
# ------------------------------------------------------------
# - Kraken Charts API fixed
# - from/to sent as Unix seconds
# - 7-day chunk download
# - CLOSED candles only
# - Original reverse-Tenkan backtest preserved
# - SL/TP matrix
# - P&L
# - Profit Factor
# - Max Drawdown
# - TP / SL / TIMEOUT
# - LONG / SHORT statistics
# - MFE / MAE
# - Maximum holding period
# - Conservative same-candle rule:
#     SL first if both TP and SL are touched
#
# NO REAL TRADING
# NO TELEGRAM
# NO VOLUME-KHAT CONNECTION
# ============================================================

import time
from datetime import datetime, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIGURATION
# ============================================================

SYMBOL = "PF_DOGEUSD"
RESOLUTION = "5m"

LOOKBACK_DAYS = 30
TENKAN_PERIOD = 9

# ------------------------------------------------------------
# Entry thresholds
# ------------------------------------------------------------

THRESHOLDS = [
    0.50,
    0.75,
    1.00,
    1.25,
    1.50,
    2.00,
    2.50,
]

# ------------------------------------------------------------
# Tenkan return tolerance
# ------------------------------------------------------------

TENKAN_TOUCH_BUFFER_PCT = 0.05

# ------------------------------------------------------------
# Entry confirmation
# ------------------------------------------------------------

CONFIRMATION_ENABLED = True

# ------------------------------------------------------------
# Kraken Futures Charts API
# ------------------------------------------------------------

BASE_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

# ------------------------------------------------------------
# Download chunks
# ------------------------------------------------------------

CHUNK_DAYS = 7
MAX_CANDLES_PER_REQUEST = 2000

# ------------------------------------------------------------
# Data validation
# ------------------------------------------------------------

EXPECTED_CANDLES = (
    LOOKBACK_DAYS * 24 * 12
)

MIN_DATA_COMPLETENESS = 0.90

# ============================================================
# v1.3 BACKTEST SETTINGS
# ============================================================

# SL levels to test
SL_LEVELS = [
    0.50,
    1.00,
    1.50,
    2.00,
    2.50,
    3.00,
    4.00,
]

# Maximum holding period
#
# 144 x 5-minute candles
# = 720 minutes
# = 12 hours
#
MAX_HOLD_BARS = 144

# ------------------------------------------------------------
# Same candle TP + SL handling
# ------------------------------------------------------------
#
# If one candle touches both TP and SL,
# OHLC data cannot tell us which happened first.
#
# Conservative assumption:
# SL happens first.
#
CONSERVATIVE_SAME_CANDLE = True

# ============================================================
# COST MODEL
# ============================================================
#
# Keep these at zero for the first structural test.
#
# Later, realistic Kraken fees/slippage can be added.
# ============================================================

FEE_PCT_PER_SIDE = 0.0
SLIPPAGE_PCT_PER_SIDE = 0.0


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def dt_to_seconds(dt):
    """
    Kraken Charts API expects from/to
    as Unix epoch SECONDS.
    """

    return int(
        dt.timestamp()
    )


# ============================================================
# DOWNLOAD ONE CHUNK
# ============================================================

def fetch_chunk(
    start_dt,
    end_dt,
):

    url = (
        f"{BASE_URL}/{SYMBOL}/{RESOLUTION}"
    )

    params = {
        "from": dt_to_seconds(start_dt),
        "to": dt_to_seconds(end_dt),
        "count": MAX_CANDLES_PER_REQUEST,
    }

    print("\nRequesting:")
    print(url)

    print(
        f"From: {start_dt}"
    )

    print(
        f"To  : {end_dt}"
    )

    print(
        f"Epoch from: {params['from']}"
    )

    print(
        f"Epoch to  : {params['to']}"
    )

    try:

        response = requests.get(
            url,
            params=params,
            headers={
                "Accept": "application/json",
                "User-Agent":
                    "DOGE-Reverse-Tenkan/1.3.1",
            },
            timeout=30,
        )

    except requests.RequestException as exc:

        raise RuntimeError(
            f"Kraken request failed: {exc}"
        )

    print(
        f"HTTP status: {response.status_code}"
    )

    if response.status_code != 200:

        print(
            "Response text:"
        )

        print(
            response.text[:2000]
        )

        response.raise_for_status()

    try:

        data = response.json()

    except ValueError:

        raise RuntimeError(
            "Kraken returned non-JSON response."
        )

    candles = data.get(
        "candles",
        [],
    )

    print(
        f"Received {len(candles)} candles."
    )

    if not candles:

        print(
            "WARNING: Kraken returned HTTP 200 "
            "but zero candles."
        )

        print(
            "Response keys:",
            list(data.keys()),
        )

        print(
            "Response:"
        )

        print(
            data
        )

    return candles


# ============================================================
# DOWNLOAD FULL DATASET
# ============================================================

def download_data():

    print(
        "\nDownloading Kraken Futures data..."
    )

    end_dt = utc_now()

    start_dt = (
        end_dt
        - pd.Timedelta(
            days=LOOKBACK_DAYS
        )
    )

    all_candles = []

    current_start = start_dt

    chunk_number = 1

    while current_start < end_dt:

        current_end = min(
            current_start
            + pd.Timedelta(
                days=CHUNK_DAYS
            ),
            end_dt,
        )

        print(
            "\n"
            + "-" * 72
        )

        print(
            f"CHUNK {chunk_number}"
        )

        candles = fetch_chunk(
            current_start,
            current_end,
        )

        all_candles.extend(
            candles
        )

        current_start = current_end

        chunk_number += 1

        time.sleep(0.25)

    if not all_candles:

        raise RuntimeError(
            "No candles returned from Kraken."
        )

    print(
        "\nRaw candles received:",
        len(all_candles),
    )

    return all_candles


# ============================================================
# PARSE KRAKEN DATA
# ============================================================

def build_dataframe(
    raw_candles,
):

    rows = []

    for candle in raw_candles:

        # ----------------------------------------------------
        # Dictionary format
        # ----------------------------------------------------

        if isinstance(
            candle,
            dict,
        ):

            timestamp = (
                candle.get("time")
                or candle.get("timestamp")
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

            volume = candle.get(
                "volume",
                0,
            )

        # ----------------------------------------------------
        # List format
        # ----------------------------------------------------

        else:

            if len(candle) < 6:
                continue

            timestamp = candle[0]
            open_price = candle[1]
            high_price = candle[2]
            low_price = candle[3]
            close_price = candle[4]
            volume = candle[5]

        if timestamp is None:
            continue

        rows.append(
            [
                timestamp,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
            ]
        )

    if not rows:

        raise RuntimeError(
            "Kraken returned candles, "
            "but none could be parsed."
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    # --------------------------------------------------------
    # Timestamp
    # --------------------------------------------------------

    df["time"] = pd.to_numeric(
        df["time"],
        errors="coerce",
    )

    df = df.dropna(
        subset=["time"]
    )

    # Kraken candle timestamps are milliseconds.
    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True,
    )

    # --------------------------------------------------------
    # Numeric columns
    # --------------------------------------------------------

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:

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
    # Remove duplicates
    # --------------------------------------------------------

    df = df.drop_duplicates(
        subset=["time"],
        keep="last",
    )

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

    df = (
        df.sort_values(
            "time"
        )
        .reset_index(
            drop=True
        )
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    now = pd.Timestamp.now(
        tz="UTC"
    )

    candle_duration = (
        pd.Timedelta(
            minutes=5
        )
    )

    df["candle_close_time"] = (
        df["time"]
        + candle_duration
    )

    df = df[
        df["candle_close_time"]
        <= now
    ].copy()

    df = df.reset_index(
        drop=True
    )

    print(
        "Returned columns:",
        [
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    print(
        "\n"
        + "=" * 72
    )

    print(
        "Total valid CLOSED candles:",
        len(df),
    )

    if not df.empty:

        print(
            "First candle:",
            df.iloc[0]["time"],
        )

        print(
            "Last candle :",
            df.iloc[-1]["time"],
        )

    print(
        "=" * 72
    )

    # --------------------------------------------------------
    # Data completeness
    # --------------------------------------------------------

    minimum_required = int(
        EXPECTED_CANDLES
        * MIN_DATA_COMPLETENESS
    )

    if len(df) < minimum_required:

        raise RuntimeError(
            "Insufficient data. "
            f"Received {len(df)} candles, "
            f"minimum required "
            f"{minimum_required}."
        )

    return df


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(
    df,
):

    print(
        "\nCalculating Tenkan..."
    )

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

    valid = int(
        df["tenkan"]
        .notna()
        .sum()
    )

    print(
        f"Valid Tenkan candles: {valid}"
    )

    return df


# ============================================================
# CONFIRMATION
# ============================================================

def long_confirmation(
    df,
    i,
):

    if not CONFIRMATION_ENABLED:
        return True

    if i < 1:
        return False

    current = df.iloc[i]
    previous = df.iloc[i - 1]

    current_bullish = (
        current["close"]
        > current["open"]
    )

    previous_bearish = (
        previous["close"]
        < previous["open"]
    )

    close_higher = (
        current["close"]
        > previous["close"]
    )

    return (
        current_bullish
        and previous_bearish
        and close_higher
    )


def short_confirmation(
    df,
    i,
):

    if not CONFIRMATION_ENABLED:
        return True

    if i < 1:
        return False

    current = df.iloc[i]
    previous = df.iloc[i - 1]

    current_bearish = (
        current["close"]
        < current["open"]
    )

    previous_bullish = (
        previous["close"]
        > previous["open"]
    )

    close_lower = (
        current["close"]
        < previous["close"]
    )

    return (
        current_bearish
        and previous_bullish
        and close_lower
    )


# ============================================================
# SIGNAL DETECTION
# ============================================================

def detect_signal(
    df,
    i,
    threshold_pct,
):

    row = df.iloc[i]

    if pd.isna(
        row["tenkan"]
    ):
        return None

    distance = float(
        row["distance_pct"]
    )

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if distance <= -threshold_pct:

        if long_confirmation(
            df,
            i,
        ):

            return "LONG"

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if distance >= threshold_pct:

        if short_confirmation(
            df,
            i,
        ):

            return "SHORT"

    return None


# ============================================================
# TENKAN RETURN
# ============================================================

def tenkan_returned(
    future_row,
    entry_tenkan,
    side,
):

    buffer = (
        TENKAN_TOUCH_BUFFER_PCT
        / 100.0
    )

    upper = (
        entry_tenkan
        * (1.0 + buffer)
    )

    lower = (
        entry_tenkan
        * (1.0 - buffer)
    )

    if side == "LONG":

        return (
            future_row["high"]
            >= lower
        )

    if side == "SHORT":

        return (
            future_row["low"]
            <= upper
        )

    return False


# ============================================================
# ORIGINAL TENKAN BACKTEST
# ============================================================

def backtest_tenkan_return(
    df,
    threshold_pct,
):

    signals = []

    was_active = False

    i = TENKAN_PERIOD

    while i < len(df):

        # ----------------------------------------------------
        # Re-arm
        # ----------------------------------------------------

        if was_active:

            distance = abs(
                float(
                    df.iloc[i][
                        "distance_pct"
                    ]
                )
            )

            if distance < threshold_pct:

                was_active = False

            else:

                i += 1
                continue

        # ----------------------------------------------------
        # Signal
        # ----------------------------------------------------

        signal = detect_signal(
            df,
            i,
            threshold_pct,
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

        returned = False
        return_bars = None

        mfe = 0.0
        mae = 0.0

        for j in range(
            i + 1,
            len(df),
        ):

            future = df.iloc[j]

            if signal == "LONG":

                favorable = (
                    (
                        future["high"]
                        - entry_price
                    )
                    / entry_price
                ) * 100.0

                adverse = (
                    (
                        future["low"]
                        - entry_price
                    )
                    / entry_price
                ) * 100.0

            else:

                favorable = (
                    (
                        entry_price
                        - future["low"]
                    )
                    / entry_price
                ) * 100.0

                adverse = (
                    (
                        entry_price
                        - future["high"]
                    )
                    / entry_price
                ) * 100.0

            mfe = max(
                mfe,
                favorable,
            )

            mae = min(
                mae,
                adverse,
            )

            if tenkan_returned(
                future,
                entry_tenkan,
                signal,
            ):

                returned = True

                return_bars = (
                    j - i
                )

                break

        signals.append(
            {
                "entry_time":
                    entry_row["time"],
                "side":
                    signal,
                "entry_price":
                    entry_price,
                "entry_tenkan":
                    entry_tenkan,
                "returned":
                    returned,
                "return_bars":
                    return_bars,
                "mfe_pct":
                    mfe,
                "mae_pct":
                    mae,
            }
        )

        was_active = True

        i += 1

    return signals


# ============================================================
# ORIGINAL RESULTS
# ============================================================

def print_original_results(
    df,
):

    comparison = []

    for threshold in THRESHOLDS:

        signals = (
            backtest_tenkan_return(
                df,
                threshold,
            )
        )

        total = len(
            signals
        )

        if total == 0:

            print(
                f"\nNo signals for "
                f"{threshold:.2f}%"
            )

            continue

        returned = sum(
            x["returned"]
            for x in signals
        )

        return_5 = sum(
            (
                x["returned"]
                and x["return_bars"]
                is not None
                and x["return_bars"] <= 5
            )
            for x in signals
        )

        return_10 = sum(
            (
                x["returned"]
                and x["return_bars"]
                is not None
                and x["return_bars"] <= 10
            )
            for x in signals
        )

        return_20 = sum(
            (
                x["returned"]
                and x["return_bars"]
                is not None
                and x["return_bars"] <= 20
            )
            for x in signals
        )

        returned_trades = [
            x
            for x in signals
            if (
                x["returned"]
                and x["return_bars"]
                is not None
            )
        ]

        if returned_trades:

            avg_bars = np.mean(
                [
                    x["return_bars"]
                    for x in returned_trades
                ]
            )

            avg_return = np.mean(
                [
                    abs(
                        (
                            x["entry_price"]
                            - x["entry_tenkan"]
                        )
                        / x["entry_price"]
                    )
                    * 100.0
                    for x in returned_trades
                ]
            )

        else:

            avg_bars = np.nan
            avg_return = np.nan

        avg_mfe = np.mean(
            [
                x["mfe_pct"]
                for x in signals
            ]
        )

        avg_mae = np.mean(
            [
                x["mae_pct"]
                for x in signals
            ]
        )

        longs = sum(
            x["side"] == "LONG"
            for x in signals
        )

        shorts = sum(
            x["side"] == "SHORT"
            for x in signals
        )

        print(
            "\n"
            + "=" * 72
        )

        print(
            f"BACKTEST THRESHOLD = "
            f"{threshold:.2f}%"
        )

        print(
            "=" * 72
        )

        print(
            f"Signals       : {total}"
        )

        print(
            f"LONG          : {longs}"
        )

        print(
            f"SHORT         : {shorts}"
        )

        print(
            f"Return any    : "
            f"{returned / total * 100:.2f}%"
        )

        print(
            f"Return <= 5   : "
            f"{return_5 / total * 100:.2f}%"
        )

        print(
            f"Return <= 10  : "
            f"{return_10 / total * 100:.2f}%"
        )

        print(
            f"Return <= 20  : "
            f"{return_20 / total * 100:.2f}%"
        )

        print(
            f"Avg bars      : "
            f"{avg_bars:.2f}"
        )

        print(
            f"Avg return    : "
            f"{avg_return:.3f}%"
        )

        print(
            f"Avg MFE       : "
            f"{avg_mfe:.3f}%"
        )

        print(
            f"Avg MAE       : "
            f"{avg_mae:.3f}%"
        )

        comparison.append(
            {
                "threshold_pct":
                    threshold,
                "signals":
                    total,
                "longs":
                    longs,
                "shorts":
                    shorts,
                "return_rate_pct":
                    returned
                    / total
                    * 100.0,
                "return_rate_5_pct":
                    return_5
                    / total
                    * 100.0,
                "return_rate_10_pct":
                    return_10
                    / total
                    * 100.0,
                "return_rate_20_pct":
                    return_20
                    / total
                    * 100.0,
                "avg_return_bars":
                    avg_bars,
                "avg_return_pct":
                    avg_return,
                "avg_mfe_pct":
                    avg_mfe,
                "avg_mae_pct":
                    avg_mae,
            }
        )

    comparison_df = pd.DataFrame(
        comparison
    )

    print(
        "\n"
        + "=" * 100
    )

    print(
        "DOGE REVERSE-TENKAN "
        "THRESHOLD COMPARISON"
    )

    print(
        "=" * 100
    )

    if not comparison_df.empty:

        print(
            comparison_df.to_string(
                index=False
            )
        )

    return comparison_df


# ============================================================
# PRICE LEVELS
# ============================================================

def calculate_levels(
    entry_price,
    side,
    entry_tenkan,
    sl_pct,
):

    sl_fraction = (
        sl_pct / 100.0
    )

    if side == "LONG":

        tp_price = (
            entry_tenkan
        )

        sl_price = (
            entry_price
            * (1.0 - sl_fraction)
        )

    else:

        tp_price = (
            entry_tenkan
        )

        sl_price = (
            entry_price
            * (1.0 + sl_fraction)
        )

    return (
        tp_price,
        sl_price,
    )


# ============================================================
# COST MODEL
# ============================================================

def apply_costs(
    raw_return_pct,
):

    total_cost = (
        2.0
        * (
            FEE_PCT_PER_SIDE
            + SLIPPAGE_PCT_PER_SIDE
        )
    )

    return (
        raw_return_pct
        - total_cost
    )


# ============================================================
# SINGLE TRADE SIMULATION
# ============================================================

def simulate_trade(
    df,
    entry_index,
    side,
    threshold_pct,
    sl_pct,
):

    entry_row = df.iloc[
        entry_index
    ]

    entry_price = float(
        entry_row["close"]
    )

    entry_tenkan = float(
        entry_row["tenkan"]
    )

    entry_time = (
        entry_row["time"]
    )

    tp_price, sl_price = (
        calculate_levels(
            entry_price,
            side,
            entry_tenkan,
            sl_pct,
        )
    )

    # --------------------------------------------------------
    # Sanity check
    # --------------------------------------------------------

    if side == "LONG":

        if tp_price <= entry_price:

            return None

    else:

        if tp_price >= entry_price:

            return None

    max_index = min(
        len(df) - 1,
        entry_index
        + MAX_HOLD_BARS,
    )

    mfe = 0.0
    mae = 0.0

    exit_index = None
    exit_reason = None
    exit_price = None

    # --------------------------------------------------------
    # Walk forward
    # --------------------------------------------------------

    for j in range(
        entry_index + 1,
        max_index + 1,
    ):

        row = df.iloc[j]

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        # ----------------------------------------------------
        # MFE / MAE
        # ----------------------------------------------------

        if side == "LONG":

            favorable = (
                (
                    high
                    - entry_price
                )
                / entry_price
            ) * 100.0

            adverse = (
                (
                    low
                    - entry_price
                )
                / entry_price
            ) * 100.0

        else:

            favorable = (
                (
                    entry_price
                    - low
                )
                / entry_price
            ) * 100.0

            adverse = (
                (
                    entry_price
                    - high
                )
                / entry_price
            ) * 100.0

        mfe = max(
            mfe,
            favorable,
        )

        mae = min(
            mae,
            adverse,
        )

        # ----------------------------------------------------
        # TP / SL hit
        # ----------------------------------------------------

        if side == "LONG":

            tp_hit = (
                high >= tp_price
            )

            sl_hit = (
                low <= sl_price
            )

        else:

            tp_hit = (
                low <= tp_price
            )

            sl_hit = (
                high >= sl_price
            )

        # ----------------------------------------------------
        # Both hit in same candle
        # ----------------------------------------------------

        if (
            tp_hit
            and sl_hit
        ):

            if (
                CONSERVATIVE_SAME_CANDLE
            ):

                exit_index = j
                exit_reason = "SL"
                exit_price = sl_price

            else:

                exit_index = j
                exit_reason = "TP"
                exit_price = tp_price

            break

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        if tp_hit:

            exit_index = j
            exit_reason = "TP"
            exit_price = tp_price

            break

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        if sl_hit:

            exit_index = j
            exit_reason = "SL"
            exit_price = sl_price

            break

    # --------------------------------------------------------
    # TIMEOUT
    # --------------------------------------------------------

    if exit_index is None:

        exit_index = (
            max_index
        )

        exit_price = float(
            df.iloc[
                exit_index
            ]["close"]
        )

        exit_reason = "TIMEOUT"

    exit_time = (
        df.iloc[
            exit_index
        ]["time"]
    )

    hold_bars = (
        exit_index
        - entry_index
    )

    # --------------------------------------------------------
    # Return
    # --------------------------------------------------------

    if side == "LONG":

        raw_return_pct = (
            (
                exit_price
                - entry_price
            )
            / entry_price
        ) * 100.0

    else:

        raw_return_pct = (
            (
                entry_price
                - exit_price
            )
            / entry_price
        ) * 100.0

    net_return_pct = (
        apply_costs(
            raw_return_pct
        )
    )

    return {
        "entry_time":
            entry_time,
        "exit_time":
            exit_time,
        "side":
            side,
        "threshold_pct":
            threshold_pct,
        "sl_pct":
            sl_pct,
        "entry_price":
            entry_price,
        "entry_tenkan":
            entry_tenkan,
        "tp_price":
            tp_price,
        "sl_price":
            sl_price,
        "exit_price":
            exit_price,
        "exit_reason":
            exit_reason,
        "hold_bars":
            hold_bars,
        "hold_hours":
            hold_bars * 5.0 / 60.0,
        "raw_return_pct":
            raw_return_pct,
        "net_return_pct":
            net_return_pct,
        "mfe_pct":
            mfe,
        "mae_pct":
            mae,
    }


# ============================================================
# COMBINATION BACKTEST
# ============================================================

def backtest_combination(
    df,
    threshold_pct,
    sl_pct,
):

    trades = []

    was_active = False

    i = TENKAN_PERIOD

    while i < len(df):

        # ----------------------------------------------------
        # Re-arm
        # ----------------------------------------------------

        if was_active:

            distance = abs(
                float(
                    df.iloc[i][
                        "distance_pct"
                    ]
                )
            )

            if distance < threshold_pct:

                was_active = False

            else:

                i += 1
                continue

        # ----------------------------------------------------
        # Signal
        # ----------------------------------------------------

        signal = detect_signal(
            df,
            i,
            threshold_pct,
        )

        if signal is None:

            i += 1
            continue

        # ----------------------------------------------------
        # Simulate trade
        # ----------------------------------------------------

        trade = simulate_trade(
            df=df,
            entry_index=i,
            side=signal,
            threshold_pct=threshold_pct,
            sl_pct=sl_pct,
        )

        if trade is not None:

            trades.append(
                trade
            )

        # ----------------------------------------------------
        # Prevent repeated entries
        # during same excursion.
        # ----------------------------------------------------

        was_active = True

        i += 1

    return trades


# ============================================================
# PROFIT FACTOR
# ============================================================

def calculate_profit_factor(
    trades,
):

    if not trades:

        return 0.0

    returns = np.array(
        [
            t["net_return_pct"]
            for t in trades
        ],
        dtype=float,
    )

    gross_profit = (
        returns[
            returns > 0
        ].sum()
    )

    gross_loss = abs(
        returns[
            returns < 0
        ].sum()
    )

    if gross_loss == 0:

        if gross_profit > 0:
            return float("inf")

        return 0.0

    return (
        gross_profit
        / gross_loss
    )


# ============================================================
# MAX DRAWDOWN
# ============================================================

def calculate_drawdown(
    trades,
):

    if not trades:
        return 0.0

    returns = np.array(
        [
            t["net_return_pct"]
            for t in trades
        ],
        dtype=float,
    )

    equity = np.cumsum(
        returns
    )

    running_max = np.maximum.accumulate(
        equity
    )

    drawdown = (
        equity
        - running_max
    )

    return float(
        drawdown.min()
    )


# ============================================================
# TRADE STATISTICS
# ============================================================

def calculate_trade_statistics(
    trades,
    threshold_pct,
    sl_pct,
):

    if not trades:

        return {
            "threshold_pct":
                threshold_pct,
            "sl_pct":
                sl_pct,
            "trades":
                0,
            "tp":
                0,
            "sl":
                0,
            "timeout":
                0,
            "wins":
                0,
            "losses":
                0,
            "win_rate_pct":
                0.0,
            "net_pnl_pct":
                0.0,
            "avg_pnl_pct":
                0.0,
            "profit_factor":
                0.0,
            "max_drawdown_pct":
                0.0,
            "avg_hold_bars":
                0.0,
            "avg_hold_hours":
                0.0,
            "avg_mfe_pct":
                0.0,
            "avg_mae_pct":
                0.0,
        }

    trades_df = pd.DataFrame(
        trades
    )

    total = len(
        trades_df
    )

    tp_count = int(
        (
            trades_df[
                "exit_reason"
            ]
            == "TP"
        ).sum()
    )

    sl_count = int(
        (
            trades_df[
                "exit_reason"
            ]
            == "SL"
        ).sum()
    )

    timeout_count = int(
        (
            trades_df[
                "exit_reason"
            ]
            == "TIMEOUT"
        ).sum()
    )

    wins = int(
        (
            trades_df[
                "net_return_pct"
            ]
            > 0
        ).sum()
    )

    losses = int(
        (
            trades_df[
                "net_return_pct"
            ]
            < 0
        ).sum()
    )

    win_rate = (
        wins
        / total
        * 100.0
    )

    net_pnl = float(
        trades_df[
            "net_return_pct"
        ].sum()
    )

    avg_pnl = float(
        trades_df[
            "net_return_pct"
        ].mean()
    )

    avg_hold_bars = float(
        trades_df[
            "hold_bars"
        ].mean()
    )

    avg_hold_hours = float(
        trades_df[
            "hold_hours"
        ].mean()
    )

    avg_mfe = float(
        trades_df[
            "mfe_pct"
        ].mean()
    )

    avg_mae = float(
        trades_df[
            "mae_pct"
        ].mean()
    )

    profit_factor = (
        calculate_profit_factor(
            trades
        )
    )

    max_drawdown = (
        calculate_drawdown(
            trades
        )
    )

    return {
        "threshold_pct":
            threshold_pct,
        "sl_pct":
            sl_pct,
        "trades":
            total,
        "tp":
            tp_count,
        "sl":
            sl_count,
        "timeout":
            timeout_count,
        "wins":
            wins,
        "losses":
            losses,
        "win_rate_pct":
            win_rate,
        "net_pnl_pct":
            net_pnl,
        "avg_pnl_pct":
            avg_pnl,
        "profit_factor":
            profit_factor,
        "max_drawdown_pct":
            max_drawdown,
        "avg_hold_bars":
            avg_hold_bars,
        "avg_hold_hours":
            avg_hold_hours,
        "avg_mfe_pct":
            avg_mfe,
        "avg_mae_pct":
            avg_mae,
    }


# ============================================================
# SL / TP MATRIX
# ============================================================

def run_sl_matrix(
    df,
):

    print(
        "\n\n"
        + "=" * 110
    )

    print(
        "DOGE REVERSE-TENKAN v1.3.1"
    )

    print(
        "SL / TP P&L MATRIX"
    )

    print(
        "=" * 110
    )

    all_stats = []
    all_trades = []

    # --------------------------------------------------------
    # First three thresholds only
    #
    # These have enough observations to be useful.
    # --------------------------------------------------------

    matrix_thresholds = [
        0.50,
        0.75,
        1.00,
    ]

    for threshold in matrix_thresholds:

        for sl_pct in SL_LEVELS:

            trades = (
                backtest_combination(
                    df=df,
                    threshold_pct=threshold,
                    sl_pct=sl_pct,
                )
            )

            stats = (
                calculate_trade_statistics(
                    trades=trades,
                    threshold_pct=threshold,
                    sl_pct=sl_pct,
                )
            )

            all_stats.append(
                stats
            )

            all_trades.extend(
                trades
            )

            print(
                "\n"
                + "-" * 110
            )

            print(
                f"THRESHOLD = "
                f"{threshold:.2f}% | "
                f"SL = "
                f"{sl_pct:.2f}%"
            )

            print(
                f"Trades        : "
                f"{stats['trades']}"
            )

            print(
                f"TP            : "
                f"{stats['tp']}"
            )

            print(
                f"SL            : "
                f"{stats['sl']}"
            )

            print(
                f"Timeout       : "
                f"{stats['timeout']}"
            )

            print(
                f"Win Rate      : "
                f"{stats['win_rate_pct']:.2f}%"
            )

            print(
                f"Net P&L       : "
                f"{stats['net_pnl_pct']:.3f}%"
            )

            pf = (
                stats["profit_factor"]
            )

            if np.isinf(pf):

                pf_text = "INF"

            else:

                pf_text = (
                    f"{pf:.3f}"
                )

            print(
                f"Profit Factor : "
                f"{pf_text}"
            )

            print(
                f"Max Drawdown  : "
                f"{stats['max_drawdown_pct']:.3f}%"
            )

            print(
                f"Avg P&L       : "
                f"{stats['avg_pnl_pct']:.3f}%"
            )

            print(
                f"Avg Hold      : "
                f"{stats['avg_hold_hours']:.2f} h"
            )

            print(
                f"Avg MFE       : "
                f"{stats['avg_mfe_pct']:.3f}%"
            )

            print(
                f"Avg MAE       : "
                f"{stats['avg_mae_pct']:.3f}%"
            )

    stats_df = pd.DataFrame(
        all_stats
    )

    trades_df = pd.DataFrame(
        all_trades
    )

    return (
        stats_df,
        trades_df,
    )


# ============================================================
# LONG / SHORT BREAKDOWN
# ============================================================

def side_statistics(
    trades_df,
):

    if trades_df.empty:

        return pd.DataFrame()

    rows = []

    for side in [
        "LONG",
        "SHORT",
    ]:

        side_df = trades_df[
            trades_df["side"]
            == side
        ]

        if side_df.empty:
            continue

        total = len(
            side_df
        )

        wins = int(
            (
                side_df[
                    "net_return_pct"
                ]
                > 0
            ).sum()
        )

        losses = int(
            (
                side_df[
                    "net_return_pct"
                ]
                < 0
            ).sum()
        )

        gross_profit = (
            side_df[
                side_df[
                    "net_return_pct"
                ] > 0
            ][
                "net_return_pct"
            ].sum()
        )

        gross_loss = abs(
            side_df[
                side_df[
                    "net_return_pct"
                ] < 0
            ][
                "net_return_pct"
            ].sum()
        )

        if gross_loss == 0:

            if gross_profit > 0:

                pf = float(
                    "inf"
                )

            else:

                pf = 0.0

        else:

            pf = (
                gross_profit
                / gross_loss
            )

        rows.append(
            {
                "side":
                    side,
                "trades":
                    total,
                "wins":
                    wins,
                "losses":
                    losses,
                "win_rate_pct":
                    wins
                    / total
                    * 100.0,
                "net_pnl_pct":
                    side_df[
                        "net_return_pct"
                    ].sum(),
                "avg_pnl_pct":
                    side_df[
                        "net_return_pct"
                    ].mean(),
                "profit_factor":
                    pf,
            }
        )

    return pd.DataFrame(
        rows
    )


# ============================================================
# CURRENT STATUS
# ============================================================

def print_current_status(
    df,
):

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

    print(
        "\n"
        + "=" * 72
    )

    print(
        "CURRENT DOGE STATUS"
    )

    print(
        "=" * 72
    )

    print(
        f"Time     : "
        f"{row['time']}"
    )

    print(
        f"Price    : "
        f"{price:.8f}"
    )

    print(
        f"Tenkan   : "
        f"{tenkan:.8f}"
    )

    print(
        f"Distance : "
        f"{distance:.3f}%"
    )

    print()

    for threshold in THRESHOLDS:

        signal = detect_signal(
            df,
            len(df) - 1,
            threshold,
        )

        if signal:

            print(
                f"{threshold:.2f}% -> "
                f"{signal}"
            )

        else:

            print(
                f"{threshold:.2f}% -> "
                f"No signal"
            )

    print(
        "=" * 72
    )


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(
    comparison_df,
    stats_df,
    trades_df,
    side_df,
):

    comparison_df.to_csv(
        "doge_reverse_tenkan_summary.csv",
        index=False,
    )

    stats_df.to_csv(
        "doge_reverse_tenkan_sl_matrix.csv",
        index=False,
    )

    if not trades_df.empty:

        trades_df.to_csv(
            "doge_reverse_tenkan_trades.csv",
            index=False,
        )

    if not side_df.empty:

        side_df.to_csv(
            "doge_reverse_tenkan_side_stats.csv",
            index=False,
        )

    print(
        "\nResults saved:"
    )

    print(
        " - doge_reverse_tenkan_summary.csv"
    )

    print(
        " - doge_reverse_tenkan_sl_matrix.csv"
    )

    print(
        " - doge_reverse_tenkan_trades.csv"
    )

    print(
        " - doge_reverse_tenkan_side_stats.csv"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=" * 72
    )

    print(
        "DOGE REVERSE-TENKAN v1.3.1"
    )

    print(
        "=" * 72
    )

    print(
        "Independent strategy"
    )

    print(
        "No VOLUME-KHAT connection"
    )

    print(
        "No real trading"
    )

    print(
        "No Telegram"
    )

    print(
        "\nSymbol       : "
        f"{SYMBOL}"
    )

    print(
        "Timeframe    : "
        f"{RESOLUTION}"
    )

    print(
        "Lookback     : "
        f"{LOOKBACK_DAYS} days"
    )

    print(
        "Tenkan       : "
        f"{TENKAN_PERIOD}"
    )

    print(
        "Confirmation : "
        f"{CONFIRMATION_ENABLED}"
    )

    print(
        "Thresholds   : "
        + ", ".join(
            f"{x:.2f}%"
            for x in THRESHOLDS
        )
    )

    print(
        "SL levels    : "
        + ", ".join(
            f"{x:.2f}%"
            for x in SL_LEVELS
        )
    )

    print(
        "Max hold     : "
        f"{MAX_HOLD_BARS} candles "
        f"("
        f"{MAX_HOLD_BARS * 5 / 60:.1f}"
        f"h)"
    )

    print(
        "Same candle  : "
        + (
            "SL first"
            if CONSERVATIVE_SAME_CANDLE
            else "TP first"
        )
    )

    print(
        "Fee/side     : "
        f"{FEE_PCT_PER_SIDE:.4f}%"
    )

    print(
        "Slippage/side: "
        f"{SLIPPAGE_PCT_PER_SIDE:.4f}%"
    )

    # ========================================================
    # DOWNLOAD
    # ========================================================

    raw_candles = (
        download_data()
    )

    # ========================================================
    # DATAFRAME
    # ========================================================

    df = build_dataframe(
        raw_candles
    )

    # ========================================================
    # TENKAN
    # ========================================================

    df = calculate_tenkan(
        df
    )

    df = df.dropna(
        subset=["tenkan"]
    ).reset_index(
        drop=True
    )

    if df.empty:

        raise RuntimeError(
            "No valid candles remain "
            "after Tenkan calculation."
        )

    # ========================================================
    # CURRENT STATUS
    # ========================================================

    print_current_status(
        df
    )

    # ========================================================
    # ORIGINAL RESEARCH
    # ========================================================

    comparison_df = (
        print_original_results(
            df
        )
    )

    # ========================================================
    # SL / TP MATRIX
    # ========================================================

    (
        stats_df,
        trades_df,
    ) = run_sl_matrix(
        df
    )

    # ========================================================
    # FINAL MATRIX
    # ========================================================

    print(
        "\n\n"
        + "=" * 120
    )

    print(
        "FINAL SL / TP COMPARISON"
    )

    print(
        "=" * 120
    )

    if not stats_df.empty:

        columns = [
            "threshold_pct",
            "sl_pct",
            "trades",
            "tp",
            "sl",
            "timeout",
            "win_rate_pct",
            "net_pnl_pct",
            "profit_factor",
            "max_drawdown_pct",
            "avg_pnl_pct",
            "avg_hold_hours",
        ]

        display_df = (
            stats_df[
                columns
            ]
            .copy()
        )

        print(
            display_df.to_string(
                index=False
            )
        )

    # ========================================================
    # BEST CANDIDATES
    # ========================================================

    print(
        "\n"
        + "=" * 120
    )

    print(
        "BEST CANDIDATES"
    )

    print(
        "=" * 120
    )

    if not stats_df.empty:

        # ----------------------------------------------------
        # Do not rank tiny samples.
        # Minimum 10 trades.
        # ----------------------------------------------------

        eligible = stats_df[
            stats_df["trades"]
            >= 10
        ].copy()

        if not eligible.empty:

            eligible = (
                eligible.sort_values(
                    by=[
                        "profit_factor",
                        "net_pnl_pct",
                    ],
                    ascending=[
                        False,
                        False,
                    ],
                )
            )

            print(
                eligible[
                    [
                        "threshold_pct",
                        "sl_pct",
                        "trades",
                        "win_rate_pct",
                        "net_pnl_pct",
                        "profit_factor",
                        "max_drawdown_pct",
                        "avg_hold_hours",
                    ]
                ]
                .head(10)
                .to_string(
                    index=False
                )
            )

        else:

            print(
                "No combination has "
                "at least 10 trades."
            )

    # ========================================================
    # LONG / SHORT
    # ========================================================

    side_df = side_statistics(
        trades_df
    )

    print(
        "\n"
        + "=" * 100
    )

    print(
        "LONG / SHORT BREAKDOWN"
    )

    print(
        "=" * 100
    )

    if not side_df.empty:

        print(
            side_df.to_string(
                index=False
            )
        )

    else:

        print(
            "No trades available."
        )

    # ========================================================
    # SAVE
    # ========================================================

    save_results(
        comparison_df=
            comparison_df,
        stats_df=
            stats_df,
        trades_df=
            trades_df,
        side_df=
            side_df,
    )

    # ========================================================
    # COMPLETE
    # ========================================================

    print(
        "\n"
        + "=" * 72
    )

    print(
        "BACKTEST COMPLETE"
    )

    print(
        "=" * 72
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
