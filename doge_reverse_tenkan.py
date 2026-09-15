# ============================================================
# DOGE REVERSE-TENKAN v1.4.1
# ============================================================
#
# Independent research / backtest only
#
# NO VOLUME-KHAT
# NO REAL TRADING
# NO TELEGRAM
#
# MARKET:
#   Kraken Futures
#   PF_DOGEUSD
#
# TIMEFRAME:
#   5m
#
# CORE IDEA:
#   Mean reversion toward ENTRY-TIME TENKAN
#
# v1.4.1:
#   1) Minimum Tenkan distance
#   2) Distance contraction
#   3) Tenkan slope filter
#   4) Reversal candle confirmation
#   5) RSI extreme + RSI reversal
#   6) RVOL filter
#   7) Anti-explosion candle filter
#   8) 15M trend confirmation
#   9) Score-based entry
#  10) Score comparison: 8 / 9 / 10
#  11) Robust DataFrame handling
#
# BACKTEST:
#   Baseline vs Filtered
#   Score 8 / 9 / 10
#   Multiple SL values
#   TP = ENTRY-TIME TENKAN
#   Same candle SL first
#   Maximum hold = 12 hours
#
# ============================================================

import time
from datetime import datetime, timedelta, timezone

import requests
import pandas as pd
import numpy as np


# ============================================================
# CONFIG
# ============================================================

VERSION = "v1.4.1"

SYMBOL = "PF_DOGEUSD"

TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"

LOOKBACK_DAYS = 30

TENKAN_PERIOD = 9

# Main research threshold
DISTANCE_THRESHOLD = 0.75


# ============================================================
# RSI
# ============================================================

RSI_PERIOD = 14

RSI_LONG_LEVEL = 35
RSI_SHORT_LEVEL = 65


# ============================================================
# VOLUME
# ============================================================

RVOL_PERIOD = 20

RVOL_MIN = 1.20
RVOL_MAX = 3.00


# ============================================================
# CANDLE FILTERS
# ============================================================

BODY_AVG_PERIOD = 20

MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00


# ============================================================
# 15M CONFIRMATION
# ============================================================

EMA_15M_PERIOD = 20


# ============================================================
# SCORE RESEARCH
# ============================================================
#
# v1.4 maximum score = 14
#
# We test:
#   8
#   9
#   10
#
# This allows us to see whether increasing selectivity
# genuinely improves quality or merely reduces sample size.
#
# ============================================================

SCORE_LEVELS = [
    8,
    9,
    10,
]

DEFAULT_MIN_SCORE = 10


# ============================================================
# SL MATRIX
# ============================================================

SL_LEVELS = [
    0.50,
    0.75,
    1.00,
    1.25,
    1.50,
    2.00,
]


# ============================================================
# TP
# ============================================================

TP_MODE = "ENTRY_TENKAN"


# ============================================================
# MAX HOLD
# ============================================================

MAX_HOLD_CANDLES = 144

# 144 x 5m = 720m = 12h


# ============================================================
# COSTS
# ============================================================
#
# Keep zero initially so strategy behavior can be isolated.
#
# Change these values later for realistic exchange testing.
#
# Example:
# FEE_PER_SIDE = 0.02
# SLIPPAGE_PER_SIDE = 0.02
#
# ============================================================

FEE_PER_SIDE = 0.0

SLIPPAGE_PER_SIDE = 0.0


# ============================================================
# SAME CANDLE
# ============================================================

SAME_CANDLE_SL_FIRST = True


# ============================================================
# KRAKEN API
# ============================================================

API_URL_5M = (
    "https://futures.kraken.com/api/charts/v1/"
    "trade/PF_DOGEUSD/5m"
)

API_URL_15M = (
    "https://futures.kraken.com/api/charts/v1/"
    "trade/PF_DOGEUSD/15m"
)

REQUEST_TIMEOUT = 30


# ============================================================
# HELPERS
# ============================================================

def dt_to_seconds(dt):
    return int(dt.timestamp())


def safe_float(value):

    try:
        return float(value)

    except Exception:
        return np.nan


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_chunk(
    url,
    start_dt,
    end_dt,
    chunk_number,
):

    params = {
        "from": dt_to_seconds(start_dt),
        "to": dt_to_seconds(end_dt),
        "count": 2000,
    }

    print()
    print("-" * 72)
    print(f"CHUNK {chunk_number}")
    print()
    print("Requesting:")
    print(url)

    print(f"From: {start_dt}")
    print(f"To  : {end_dt}")

    print(
        f"Epoch from: {params['from']}"
    )

    print(
        f"Epoch to  : {params['to']}"
    )

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    print(
        f"HTTP status: {response.status_code}"
    )

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):

        candles = data.get(
            "candles",
            [],
        )

        if not candles:
            candles = data.get(
                "data",
                [],
            )

        if not candles:
            candles = data.get(
                "result",
                [],
            )

    elif isinstance(data, list):

        candles = data

    else:

        candles = []

    print(
        f"Received {len(candles)} candles."
    )

    return candles


def download_data(
    url,
    days,
):

    print()
    print(
        "Downloading Kraken Futures data..."
    )

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt -
        timedelta(days=days)
    )

    all_candles = []

    current = start_dt

    chunk_number = 1

    while current < end_dt:

        next_dt = min(
            current +
            timedelta(days=7),
            end_dt,
        )

        candles = fetch_chunk(
            url,
            current,
            next_dt,
            chunk_number,
        )

        all_candles.extend(
            candles
        )

        current = next_dt

        chunk_number += 1

        time.sleep(0.15)

    if not all_candles:

        raise RuntimeError(
            "No candles received from Kraken."
        )

    print()
    print(
        f"Raw candles received: "
        f"{len(all_candles)}"
    )

    return all_candles


# ============================================================
# DATAFRAME
# ============================================================

def build_dataframe(candles):

    rows = []

    for candle in candles:

        try:

            if isinstance(
                candle,
                dict,
            ):

                timestamp = (
                    candle.get("time")
                    or candle.get(
                        "timestamp"
                    )
                )

                open_price = (
                    candle.get("open")
                    or candle.get("o")
                )

                high_price = (
                    candle.get("high")
                    or candle.get("h")
                )

                low_price = (
                    candle.get("low")
                    or candle.get("l")
                )

                close_price = (
                    candle.get("close")
                    or candle.get("c")
                )

                volume = (
                    candle.get("volume")
                    or candle.get("v")
                    or 0
                )

            else:

                timestamp = candle[0]

                open_price = candle[1]

                high_price = candle[2]

                low_price = candle[3]

                close_price = candle[4]

                volume = (
                    candle[5]
                    if len(candle) > 5
                    else 0
                )

            rows.append([
                timestamp,
                open_price,
                high_price,
                low_price,
                close_price,
                volume,
            ])

        except Exception:

            continue

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

    if df.empty:

        raise RuntimeError(
            "Could not build dataframe."
        )

    numeric_time = pd.to_numeric(
        df["time"],
        errors="coerce",
    )

    if (
        numeric_time
        .dropna()
        .max()
        > 10_000_000_000
    ):

        df["time"] = pd.to_datetime(
            numeric_time,
            unit="ms",
            utc=True,
        )

    else:

        df["time"] = pd.to_datetime(
            numeric_time,
            unit="s",
            utc=True,
        )

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

    df = (
        df
        .dropna()
        .drop_duplicates(
            subset=["time"]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    now = pd.Timestamp.now(
        tz="UTC"
    )

    if TIMEFRAME_5M == "5m":

        candle_close_time = (
            df["time"]
            +
            pd.Timedelta(minutes=5)
        )

        df = df[
            candle_close_time <= now
        ].copy()

    print()
    print(
        f"Returned columns: "
        f"{list(df.columns)}"
    )

    print()
    print("=" * 72)

    print(
        "Total valid CLOSED candles: "
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

    return (
        df
        .reset_index(drop=True)
    )


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    df = df.copy()

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
        highest_high +
        lowest_low
    ) / 2

    return df


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14,
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / period,
            adjust=False,
            min_periods=period,
        )
        .mean()
    )

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan,
        )
    )

    rsi = 100 - (
        100 /
        (1 + rs)
    )

    return rsi


# ============================================================
# 5M INDICATORS
# ============================================================

def add_5m_indicators(df):

    df = df.copy()

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD,
    )

    df["body"] = (
        df["close"] -
        df["open"]
    ).abs()

    df["range"] = (
        df["high"] -
        df["low"]
    )

    df["avg_body"] = (
        df["body"]
        .rolling(
            BODY_AVG_PERIOD
        )
        .mean()
    )

    df["avg_range"] = (
        df["range"]
        .rolling(
            BODY_AVG_PERIOD
        )
        .mean()
    )

    df["avg_volume"] = (
        df["volume"]
        .rolling(
            RVOL_PERIOD
        )
        .mean()
    )

    df["rvol"] = (
        df["volume"] /
        df["avg_volume"]
        .replace(
            0,
            np.nan,
        )
    )

    df["distance_pct"] = (
        (
            df["close"] -
            df["tenkan"]
        )
        /
        df["tenkan"]
        * 100
    )

    df["distance_abs"] = (
        df["distance_pct"]
        .abs()
    )

    df["tenkan_slope"] = (
        df["tenkan"] -
        df["tenkan"].shift(1)
    )

    df[
        "distance_contracting"
    ] = (
        df["distance_abs"]
        <
        df["distance_abs"].shift(1)
    )

    return df


# ============================================================
# 15M INDICATORS
# ============================================================

def calculate_15m_indicators(
    df15,
):

    df15 = df15.copy()

    df15["ema20"] = (
        df15["close"]
        .ewm(
            span=EMA_15M_PERIOD,
            adjust=False,
        )
        .mean()
    )

    return df15


# ============================================================
# MERGE 15M
# ============================================================

def merge_15m_context(
    df5,
    df15,
):

    df5 = df5.copy()

    df15 = df15.copy()

    df15 = df15[
        [
            "time",
            "close",
            "ema20",
        ]
    ].copy()

    df15 = df15.rename(
        columns={
            "close":
                "close_15m",

            "ema20":
                "ema20_15m",
        }
    )

    df15["time"] = pd.to_datetime(
        df15["time"],
        utc=True,
    )

    df5["time"] = pd.to_datetime(
        df5["time"],
        utc=True,
    )

    merged = pd.merge_asof(
        df5.sort_values("time"),
        df15.sort_values("time"),
        on="time",
        direction="backward",
    )

    return (
        merged
        .reset_index(drop=True)
    )


# ============================================================
# BASELINE SIGNAL
# ============================================================

def baseline_signal(row):

    distance = row[
        "distance_pct"
    ]

    if not np.isfinite(
        distance
    ):

        return None

    if (
        distance <=
        -DISTANCE_THRESHOLD
    ):

        return "LONG"

    if (
        distance >=
        DISTANCE_THRESHOLD
    ):

        return "SHORT"

    return None


# ============================================================
# FILTERED SIGNAL
# ============================================================

def filtered_signal(
    row,
    min_score,
):

    distance = row[
        "distance_pct"
    ]

    if not np.isfinite(
        distance
    ):

        return (
            None,
            0,
            [],
        )

    score = 0

    reasons = []

    # ========================================================
    # LONG
    # ========================================================

    if (
        distance <=
        -DISTANCE_THRESHOLD
    ):

        # ----------------------------------------------------
        # Distance
        # ----------------------------------------------------

        score += 2

        reasons.append(
            "DISTANCE"
        )

        # ----------------------------------------------------
        # Distance contraction
        # ----------------------------------------------------

        if bool(
            row[
                "distance_contracting"
            ]
        ):

            score += 2

            reasons.append(
                "CONTRACTION"
            )

        # ----------------------------------------------------
        # Tenkan slope
        # ----------------------------------------------------

        if (
            np.isfinite(
                row[
                    "tenkan_slope"
                ]
            )
            and
            row[
                "tenkan_slope"
            ] >= 0
        ):

            score += 2

            reasons.append(
                "TENKAN_SLOPE"
            )

        # ----------------------------------------------------
        # Reversal candle
        # ----------------------------------------------------

        bullish = (
            row["close"] >
            row["open"]
            and
            row["close"] >
            row["close_prev"]
        )

        candle_range = (
            row["range"]
        )

        if candle_range > 0:

            close_position = (
                row["close"] -
                row["low"]
            ) / candle_range

        else:

            close_position = 0.5

        if (
            bullish
            and
            close_position >= 0.55
            and
            row["high"] >
            row["high_prev"]
        ):

            score += 2

            reasons.append(
                "REVERSAL_CANDLE"
            )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if (
            np.isfinite(
                row["rsi"]
            )
            and
            np.isfinite(
                row["rsi_prev"]
            )
            and
            row["rsi"] <
            RSI_LONG_LEVEL
            and
            row["rsi"] >
            row["rsi_prev"]
        ):

            score += 2

            reasons.append(
                "RSI"
            )

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        if (
            np.isfinite(
                row["rvol"]
            )
            and
            RVOL_MIN <=
            row["rvol"] <=
            RVOL_MAX
        ):

            score += 1

            reasons.append(
                "RVOL"
            )

        # ----------------------------------------------------
        # Anti explosion
        # ----------------------------------------------------

        body_ok = (
            np.isfinite(
                row["avg_body"]
            )
            and
            row["body"]
            <=
            row["avg_body"]
            *
            MAX_BODY_MULTIPLIER
        )

        range_ok = (
            np.isfinite(
                row["avg_range"]
            )
            and
            row["range"]
            <=
            row["avg_range"]
            *
            MAX_RANGE_MULTIPLIER
        )

        if (
            body_ok
            and
            range_ok
        ):

            score += 1

            reasons.append(
                "NO_EXPLOSION"
            )

        # ----------------------------------------------------
        # 15M confirmation
        # ----------------------------------------------------

        if (
            np.isfinite(
                row["close_15m"]
            )
            and
            np.isfinite(
                row["ema20_15m"]
            )
            and
            row["close_15m"]
            >=
            row["ema20_15m"]
        ):

            score += 2

            reasons.append(
                "15M_CONFIRM"
            )

        if score >= min_score:

            return (
                "LONG",
                score,
                reasons,
            )

        return (
            None,
            score,
            reasons,
        )

    # ========================================================
    # SHORT
    # ========================================================

    if (
        distance >=
        DISTANCE_THRESHOLD
    ):

        # ----------------------------------------------------
        # Distance
        # ----------------------------------------------------

        score += 2

        reasons.append(
            "DISTANCE"
        )

        # ----------------------------------------------------
        # Distance contraction
        # ----------------------------------------------------

        if bool(
            row[
                "distance_contracting"
            ]
        ):

            score += 2

            reasons.append(
                "CONTRACTION"
            )

        # ----------------------------------------------------
        # Tenkan slope
        # ----------------------------------------------------

        if (
            np.isfinite(
                row[
                    "tenkan_slope"
                ]
            )
            and
            row[
                "tenkan_slope"
            ] <= 0
        ):

            score += 2

            reasons.append(
                "TENKAN_SLOPE"
            )

        # ----------------------------------------------------
        # Reversal candle
        # ----------------------------------------------------

        bearish = (
            row["close"] <
            row["open"]
            and
            row["close"] <
            row["close_prev"]
        )

        candle_range = (
            row["range"]
        )

        if candle_range > 0:

            close_position = (
                row["close"] -
                row["low"]
            ) / candle_range

        else:

            close_position = 0.5

        if (
            bearish
            and
            close_position <= 0.45
            and
            row["low"] <
            row["low_prev"]
        ):

            score += 2

            reasons.append(
                "REVERSAL_CANDLE"
            )

        # ----------------------------------------------------
        # RSI
        # ----------------------------------------------------

        if (
            np.isfinite(
                row["rsi"]
            )
            and
            np.isfinite(
                row["rsi_prev"]
            )
            and
            row["rsi"] >
            RSI_SHORT_LEVEL
            and
            row["rsi"] <
            row["rsi_prev"]
        ):

            score += 2

            reasons.append(
                "RSI"
            )

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        if (
            np.isfinite(
                row["rvol"]
            )
            and
            RVOL_MIN <=
            row["rvol"] <=
            RVOL_MAX
        ):

            score += 1

            reasons.append(
                "RVOL"
            )

        # ----------------------------------------------------
        # Anti explosion
        # ----------------------------------------------------

        body_ok = (
            np.isfinite(
                row["avg_body"]
            )
            and
            row["body"]
            <=
            row["avg_body"]
            *
            MAX_BODY_MULTIPLIER
        )

        range_ok = (
            np.isfinite(
                row["avg_range"]
            )
            and
            row["range"]
            <=
            row["avg_range"]
            *
            MAX_RANGE_MULTIPLIER
        )

        if (
            body_ok
            and
            range_ok
        ):

            score += 1

            reasons.append(
                "NO_EXPLOSION"
            )

        # ----------------------------------------------------
        # 15M confirmation
        # ----------------------------------------------------

        if (
            np.isfinite(
                row["close_15m"]
            )
            and
            np.isfinite(
                row["ema20_15m"]
            )
            and
            row["close_15m"]
            <=
            row["ema20_15m"]
        ):

            score += 2

            reasons.append(
                "15M_CONFIRM"
            )

        if score >= min_score:

            return (
                "SHORT",
                score,
                reasons,
            )

        return (
            None,
            score,
            reasons,
        )

    return (
        None,
        score,
        reasons,
    )


# ============================================================
# COSTS
# ============================================================

def apply_costs(
    pnl_pct,
):

    total_cost = (
        FEE_PER_SIDE +
        SLIPPAGE_PER_SIDE
    ) * 2

    return (
        pnl_pct -
        total_cost
    )


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df,
    entry_index,
    side,
    sl_pct,
):

    entry_row = df.iloc[
        entry_index
    ]

    entry_price = float(
        entry_row["close"]
    )

    tenkan = float(
        entry_row["tenkan"]
    )

    if side == "LONG":

        tp_price = tenkan

        sl_price = (
            entry_price *
            (
                1 -
                sl_pct / 100
            )
        )

    else:

        tp_price = tenkan

        sl_price = (
            entry_price *
            (
                1 +
                sl_pct / 100
            )
        )

    max_index = min(
        entry_index +
        MAX_HOLD_CANDLES,
        len(df) - 1,
    )

    mfe = 0.0

    mae = 0.0

    exit_reason = None

    exit_price = None

    hold_bars = None

    for i in range(
        entry_index + 1,
        max_index + 1,
    ):

        row = df.iloc[i]

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        if side == "LONG":

            favorable = (
                (
                    high -
                    entry_price
                )
                /
                entry_price
                *
                100
            )

            adverse = (
                (
                    low -
                    entry_price
                )
                /
                entry_price
                *
                100
            )

            mfe = max(
                mfe,
                favorable,
            )

            mae = min(
                mae,
                adverse,
            )

            hit_tp = (
                high >= tp_price
            )

            hit_sl = (
                low <= sl_price
            )

        else:

            favorable = (
                (
                    entry_price -
                    low
                )
                /
                entry_price
                *
                100
            )

            adverse = (
                (
                    entry_price -
                    high
                )
                /
                entry_price
                *
                100
            )

            mfe = max(
                mfe,
                favorable,
            )

            mae = min(
                mae,
                -adverse,
            )

            hit_tp = (
                low <= tp_price
            )

            hit_sl = (
                high >= sl_price
            )

        # ----------------------------------------------------
        # Same candle
        # ----------------------------------------------------

        if hit_tp and hit_sl:

            if SAME_CANDLE_SL_FIRST:

                exit_reason = "SL"

                exit_price = (
                    sl_price
                )

            else:

                exit_reason = "TP"

                exit_price = (
                    tp_price
                )

            hold_bars = (
                i -
                entry_index
            )

            break

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        if hit_sl:

            exit_reason = "SL"

            exit_price = (
                sl_price
            )

            hold_bars = (
                i -
                entry_index
            )

            break

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        if hit_tp:

            exit_reason = "TP"

            exit_price = (
                tp_price
            )

            hold_bars = (
                i -
                entry_index
            )

            break

    # --------------------------------------------------------
    # Timeout
    # --------------------------------------------------------

    if exit_reason is None:

        exit_reason = "TIMEOUT"

        exit_price = float(
            df.iloc[
                max_index
            ]["close"]
        )

        hold_bars = (
            max_index -
            entry_index
        )

    # --------------------------------------------------------
    # P&L
    # --------------------------------------------------------

    if side == "LONG":

        pnl_pct = (
            (
                exit_price -
                entry_price
            )
            /
            entry_price
            *
            100
        )

    else:

        pnl_pct = (
            (
                entry_price -
                exit_price
            )
            /
            entry_price
            *
            100
        )

    pnl_pct = apply_costs(
        pnl_pct
    )

    return {
        "entry_time":
            entry_row["time"],

        "exit_time":
            df.iloc[
                entry_index +
                hold_bars
            ]["time"],

        "side":
            side,

        "entry_price":
            entry_price,

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
            hold_bars *
            5 /
            60,

        "pnl_pct":
            pnl_pct,

        "mfe_pct":
            mfe,

        "mae_pct":
            mae,

        "score":
            entry_row.get(
                "score",
                np.nan,
            ),
    }


# ============================================================
# BASELINE SIGNAL GENERATION
# ============================================================

def generate_baseline_trades(
    df,
):

    trades = []

    was_active = False

    for i in range(
        1,
        len(df),
    ):

        signal = (
            baseline_signal(
                df.iloc[i]
            )
        )

        if signal is None:

            was_active = False

            continue

        if was_active:

            continue

        was_active = True

        df.loc[
            i,
            "score"
        ] = np.nan

        trades.append(
            (
                i,
                signal,
            )
        )

    return trades


# ============================================================
# FILTERED SIGNAL GENERATION
# ============================================================

def generate_filtered_trades(
    df,
    min_score,
):

    trades = []

    was_active = False

    score_column = (
        f"score_{min_score}"
    )

    df[
        score_column
    ] = np.nan

    for i in range(
        1,
        len(df),
    ):

        signal, score, reasons = (
            filtered_signal(
                df.iloc[i],
                min_score,
            )
        )

        df.loc[
            i,
            score_column
        ] = score

        if signal is None:

            distance = df.iloc[i][
                "distance_pct"
            ]

            # Re-arm when price exits
            # the extreme zone.
            if (
                np.isfinite(
                    distance
                )
                and
                abs(distance)
                <
                DISTANCE_THRESHOLD
            ):

                was_active = False

            continue

        if was_active:

            continue

        was_active = True

        trades.append(
            (
                i,
                signal,
            )
        )

    return trades


# ============================================================
# PROFIT FACTOR
# ============================================================

def profit_factor(
    trades,
):

    if trades is None:

        return 0.0

    if isinstance(
        trades,
        pd.DataFrame,
    ):

        if trades.empty:

            return 0.0

        pnls = (
            trades["pnl_pct"]
            .astype(float)
            .tolist()
        )

    else:

        if len(trades) == 0:

            return 0.0

        pnls = [
            float(
                t["pnl_pct"]
            )
            for t in trades
        ]

    wins = sum(
        max(
            pnl,
            0
        )
        for pnl in pnls
    )

    losses = abs(
        sum(
            min(
                pnl,
                0
            )
            for pnl in pnls
        )
    )

    if losses == 0:

        if wins > 0:

            return np.inf

        return 0.0

    return (
        wins /
        losses
    )


# ============================================================
# MAX DRAWDOWN
# ============================================================

def max_drawdown(
    trades,
):

    if trades is None:

        return 0.0

    if isinstance(
        trades,
        pd.DataFrame,
    ):

        if trades.empty:

            return 0.0

        pnls = (
            trades["pnl_pct"]
            .astype(float)
            .tolist()
        )

    else:

        if len(trades) == 0:

            return 0.0

        pnls = [
            float(
                t["pnl_pct"]
            )
            for t in trades
        ]

    equity = 0.0

    peak = 0.0

    max_dd = 0.0

    for pnl in pnls:

        equity += pnl

        peak = max(
            peak,
            equity,
        )

        dd = (
            equity -
            peak
        )

        max_dd = min(
            max_dd,
            dd,
        )

    return max_dd


# ============================================================
# SUMMARY
# ============================================================

def summarize_trades(
    trades,
    name,
):

    if trades is None:

        trades = []

    if isinstance(
        trades,
        pd.DataFrame,
    ):

        if trades.empty:

            trades = []

        else:

            trades = (
                trades
                .to_dict("records")
            )

    if len(trades) == 0:

        return {
            "strategy":
                name,

            "trades":
                0,

            "wins":
                0,

            "losses":
                0,

            "timeouts":
                0,

            "win_rate_pct":
                0.0,

            "net_pnl_pct":
                0.0,

            "profit_factor":
                0.0,

            "max_drawdown_pct":
                0.0,

            "avg_pnl_pct":
                0.0,

            "avg_hold_hours":
                0.0,

            "avg_mfe_pct":
                0.0,

            "avg_mae_pct":
                0.0,
        }

    wins = sum(
        t["exit_reason"] ==
        "TP"
        for t in trades
    )

    losses = sum(
        t["exit_reason"] ==
        "SL"
        for t in trades
    )

    timeouts = sum(
        t["exit_reason"] ==
        "TIMEOUT"
        for t in trades
    )

    pnls = [
        float(
            t["pnl_pct"]
        )
        for t in trades
    ]

    return {
        "strategy":
            name,

        "trades":
            len(trades),

        "wins":
            wins,

        "losses":
            losses,

        "timeouts":
            timeouts,

        "win_rate_pct":
            (
                wins /
                len(trades) *
                100
            ),

        "net_pnl_pct":
            sum(pnls),

        "profit_factor":
            profit_factor(
                trades
            ),

        "max_drawdown_pct":
            max_drawdown(
                trades
            ),

        "avg_pnl_pct":
            np.mean(pnls),

        "avg_hold_hours":
            np.mean([
                t["hold_hours"]
                for t in trades
            ]),

        "avg_mfe_pct":
            np.mean([
                t["mfe_pct"]
                for t in trades
            ]),

        "avg_mae_pct":
            np.mean([
                t["mae_pct"]
                for t in trades
            ]),
    }


# ============================================================
# MATRIX
# ============================================================

def run_matrix(
    df,
    signal_type,
    signal_list,
):

    rows = []

    all_trades = []

    for sl in SL_LEVELS:

        trades = []

        for entry_index, side in (
            signal_list
        ):

            trade = simulate_trade(
                df,
                entry_index,
                side,
                sl,
            )

            trade["strategy"] = (
                signal_type
            )

            trade["sl_pct"] = sl

            trades.append(
                trade
            )

        stats = summarize_trades(
            trades,
            (
                f"{signal_type} | "
                f"SL {sl:.2f}%"
            ),
        )

        stats["sl_pct"] = sl

        rows.append(
            stats
        )

        all_trades.extend(
            trades
        )

        print()
        print("-" * 72)

        print(
            f"{signal_type} | "
            f"SL = {sl:.2f}%"
        )

        print(
            f"Trades       : "
            f"{stats['trades']}"
        )

        print(
            f"TP           : "
            f"{stats['wins']}"
        )

        print(
            f"SL           : "
            f"{stats['losses']}"
        )

        print(
            f"Timeout      : "
            f"{stats['timeouts']}"
        )

        print(
            f"Win Rate     : "
            f"{stats['win_rate_pct']:.2f}%"
        )

        print(
            f"Net P&L      : "
            f"{stats['net_pnl_pct']:.3f}%"
        )

        print(
            f"Profit Factor: "
            f"{stats['profit_factor']:.3f}"
        )

        print(
            f"Max Drawdown : "
            f"{stats['max_drawdown_pct']:.3f}%"
        )

        print(
            f"Avg P&L      : "
            f"{stats['avg_pnl_pct']:.3f}%"
        )

        print(
            f"Avg Hold     : "
            f"{stats['avg_hold_hours']:.2f} h"
        )

        print(
            f"Avg MFE      : "
            f"{stats['avg_mfe_pct']:.3f}%"
        )

        print(
            f"Avg MAE      : "
            f"{stats['avg_mae_pct']:.3f}%"
        )

    return (
        pd.DataFrame(rows),
        pd.DataFrame(all_trades),
    )


# ============================================================
# SIDE STATS
# ============================================================

def side_stats(
    trades,
):

    # --------------------------------------------------------
    # FIX FOR ORIGINAL v1.4 ERROR
    # --------------------------------------------------------

    if trades is None:

        return pd.DataFrame()

    if isinstance(
        trades,
        pd.DataFrame,
    ):

        if trades.empty:

            return pd.DataFrame()

        records = (
            trades
            .to_dict("records")
        )

    else:

        if len(trades) == 0:

            return pd.DataFrame()

        records = trades

    rows = []

    for side in [
        "LONG",
        "SHORT",
    ]:

        subset = [
            t
            for t in records
            if t["side"] == side
        ]

        if len(subset) == 0:

            continue

        wins = sum(
            t["exit_reason"] ==
            "TP"
            for t in subset
        )

        losses = sum(
            t["exit_reason"] ==
            "SL"
            for t in subset
        )

        timeouts = sum(
            t["exit_reason"] ==
            "TIMEOUT"
            for t in subset
        )

        pnls = [
            float(
                t["pnl_pct"]
            )
            for t in subset
        ]

        rows.append({

            "side":
                side,

            "trades":
                len(subset),

            "wins":
                wins,

            "losses":
                losses,

            "timeouts":
                timeouts,

            "win_rate_pct":
                (
                    wins /
                    len(subset) *
                    100
                ),

            "net_pnl_pct":
                sum(pnls),

            "avg_pnl_pct":
                np.mean(pnls),

            "avg_hold_hours":
                np.mean([
                    t["hold_hours"]
                    for t in subset
                ]),
        })

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

    print()
    print("=" * 72)
    print(
        f"CURRENT DOGE STATUS | "
        f"{VERSION}"
    )
    print("=" * 72)

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

    baseline = (
        baseline_signal(row)
    )

    print()

    print(
        f"Baseline "
        f"{DISTANCE_THRESHOLD:.2f}% : "
        f"{baseline or 'No signal'}"
    )

    print()

    print(
        "Filtered score tests:"
    )

    for score_level in (
        SCORE_LEVELS
    ):

        filtered, score, reasons = (
            filtered_signal(
                row,
                score_level,
            )
        )

        print(
            f"Score {score_level:2d}: "
            f"{filtered or 'No signal'} "
            f"(current score "
            f"{score}/14)"
        )

        if reasons:

            print(
                " " * 12 +
                ", ".join(reasons)
            )

    print("=" * 72)


# ============================================================
# BEST CANDIDATES
# ============================================================

def print_best_candidates(
    comparison,
):

    valid = comparison[
        comparison["trades"] >= 10
    ].copy()

    if valid.empty:

        print()
        print(
            "No candidate has at least "
            "10 trades."
        )

        return

    # --------------------------------------------------------
    # Best PF
    # --------------------------------------------------------

    best_pf = valid.loc[
        valid[
            "profit_factor"
        ].idxmax()
    ]

    # --------------------------------------------------------
    # Best Net
    # --------------------------------------------------------

    best_pnl = valid.loc[
        valid[
            "net_pnl_pct"
        ].idxmax()
    ]

    # --------------------------------------------------------
    # Lowest DD
    #
    # Since DD values are negative,
    # the largest value is the
    # least negative drawdown.
    # --------------------------------------------------------

    lowest_dd = valid.loc[
        valid[
            "max_drawdown_pct"
        ].idxmax()
    ]

    print()
    print("=" * 90)
    print(
        "BEST CANDIDATES "
        "(MINIMUM 10 TRADES)"
    )
    print("=" * 90)

    print()
    print(
        "BEST PROFIT FACTOR:"
    )

    print(
        f"{best_pf['strategy']} | "
        f"SL {best_pf['sl_pct']:.2f}% | "
        f"PF {best_pf['profit_factor']:.3f} | "
        f"Net {best_pf['net_pnl_pct']:.3f}% | "
        f"WR {best_pf['win_rate_pct']:.2f}% | "
        f"DD {best_pf['max_drawdown_pct']:.3f}% | "
        f"Trades {int(best_pf['trades'])}"
    )

    print()
    print(
        "BEST NET P&L:"
    )

    print(
        f"{best_pnl['strategy']} | "
        f"SL {best_pnl['sl_pct']:.2f}% | "
        f"PF {best_pnl['profit_factor']:.3f} | "
        f"Net {best_pnl['net_pnl_pct']:.3f}% | "
        f"WR {best_pnl['win_rate_pct']:.2f}% | "
        f"DD {best_pnl['max_drawdown_pct']:.3f}% | "
        f"Trades {int(best_pnl['trades'])}"
    )

    print()
    print(
        "LOWEST DRAWdown:"
    )

    print(
        f"{lowest_dd['strategy']} | "
        f"SL {lowest_dd['sl_pct']:.2f}% | "
        f"PF {lowest_dd['profit_factor']:.3f} | "
        f"Net {lowest_dd['net_pnl_pct']:.3f}% | "
        f"WR {lowest_dd['win_rate_pct']:.2f}% | "
        f"DD {lowest_dd['max_drawdown_pct']:.3f}% | "
        f"Trades {int(lowest_dd['trades'])}"
    )


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(
    comparison,
    matrices,
    trades,
    side_stats_df,
):

    comparison_file = (
        "doge_reverse_tenkan_v141_comparison.csv"
    )

    comparison.to_csv(
        comparison_file,
        index=False,
    )

    for name, matrix in (
        matrices.items()
    ):

        filename = (
            "doge_reverse_tenkan_v141_"
            f"{name}_matrix.csv"
        )

        matrix.to_csv(
            filename,
            index=False,
        )

    for name, trade_df in (
        trades.items()
    ):

        filename = (
            "doge_reverse_tenkan_v141_"
            f"{name}_trades.csv"
        )

        trade_df.to_csv(
            filename,
            index=False,
        )

    side_stats_df.to_csv(
        "doge_reverse_tenkan_v141_side_stats.csv",
        index=False,
    )

    print()
    print("=" * 72)
    print(
        "RESULT FILES SAVED"
    )
    print("=" * 72)

    print(
        comparison_file
    )

    for name in matrices:

        print(
            "doge_reverse_tenkan_v141_"
            f"{name}_matrix.csv"
        )

    for name in trades:

        print(
            "doge_reverse_tenkan_v141_"
            f"{name}_trades.csv"
        )

    print(
        "doge_reverse_tenkan_v141_side_stats.csv"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 72)

    print(
        f"DOGE REVERSE-TENKAN "
        f"{VERSION}"
    )

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

    print(
        "No Telegram"
    )

    print()

    print(
        f"Symbol       : {SYMBOL}"
    )

    print(
        f"Timeframe    : {TIMEFRAME_5M}"
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
        f"Threshold    : "
        f"{DISTANCE_THRESHOLD:.2f}%"
    )

    print()

    print(
        "FILTERS:"
    )

    print(
        "Distance contraction"
    )

    print(
        "Tenkan slope"
    )

    print(
        "Reversal candle"
    )

    print(
        "RSI extreme + reversal"
    )

    print(
        "RVOL"
    )

    print(
        "Anti-explosion candle"
    )

    print(
        "15M EMA20 confirmation"
    )

    print()

    print(
        "Score tests: "
        +
        ", ".join(
            str(x)
            for x in SCORE_LEVELS
        )
        +
        " / 14"
    )

    print()

    print(
        "SL levels: "
        +
        ", ".join(
            f"{x:.2f}%"
            for x in SL_LEVELS
        )
    )

    print(
        f"TP           : "
        f"{TP_MODE}"
    )

    print(
        f"Max hold     : "
        f"{MAX_HOLD_CANDLES} candles "
        f"("
        f"{MAX_HOLD_CANDLES * 5 / 60:.1f}"
        f"h)"
    )

    print(
        "Same candle  : "
        "SL first"
    )

    print(
        f"Fee/side     : "
        f"{FEE_PER_SIDE:.4f}%"
    )

    print(
        f"Slippage/side: "
        f"{SLIPPAGE_PER_SIDE:.4f}%"
    )


    # ========================================================
    # DOWNLOAD 5M
    # ========================================================

    candles5 = download_data(
        API_URL_5M,
        LOOKBACK_DAYS,
    )

    df5 = build_dataframe(
        candles5
    )


    # ========================================================
    # DOWNLOAD 15M
    # ========================================================

    print()
    print(
        "Downloading 15M "
        "confirmation data..."
    )

    candles15 = download_data(
        API_URL_15M,
        LOOKBACK_DAYS,
    )

    df15 = build_dataframe(
        candles15
    )


    # ========================================================
    # INDICATORS
    # ========================================================

    print()
    print(
        "Calculating indicators..."
    )

    df5 = calculate_tenkan(
        df5
    )

    df5 = add_5m_indicators(
        df5
    )

    df15 = calculate_15m_indicators(
        df15
    )

    df = merge_15m_context(
        df5,
        df15,
    )


    # ========================================================
    # PREVIOUS VALUES
    # ========================================================

    df["close_prev"] = (
        df["close"].shift(1)
    )

    df["high_prev"] = (
        df["high"].shift(1)
    )

    df["low_prev"] = (
        df["low"].shift(1)
    )

    df["rsi_prev"] = (
        df["rsi"].shift(1)
    )

    df = df.dropna(
        subset=[
            "tenkan",
            "distance_pct",
        ]
    ).reset_index(
        drop=True
    )

    print()

    print(
        f"Valid Tenkan candles: "
        f"{len(df)}"
    )


    # ========================================================
    # CURRENT STATUS
    # ========================================================

    print_current_status(
        df
    )


    # ========================================================
    # BASELINE
    # ========================================================

    print()
    print("=" * 72)

    print(
        "GENERATING BASELINE "
        f"{DISTANCE_THRESHOLD:.2f}% SIGNALS"
    )

    print("=" * 72)

    baseline_signals = (
        generate_baseline_trades(
            df
        )
    )

    print(
        f"Baseline signals: "
        f"{len(baseline_signals)}"
    )


    # ========================================================
    # BASELINE MATRIX
    # ========================================================

    print()
    print("=" * 96)

    print(
        "BASELINE SL / TP MATRIX"
    )

    print("=" * 96)

    baseline_matrix, baseline_trades = (
        run_matrix(
            df,
            (
                f"BASELINE "
                f"{DISTANCE_THRESHOLD:.2f}%"
            ),
            baseline_signals,
        )
    )


    # ========================================================
    # FILTERED SCORE TESTS
    # ========================================================

    matrices = {
        "baseline":
            baseline_matrix,
    }

    trades = {
        "baseline":
            baseline_trades,
    }

    all_comparisons = [
        baseline_matrix
    ]

    side_frames = []


    for score_level in (
        SCORE_LEVELS
    ):

        print()
        print("=" * 96)

        print(
            f"GENERATING FILTERED "
            f"SCORE {score_level}/14"
        )

        print("=" * 96)

        filtered_signals = (
            generate_filtered_trades(
                df,
                score_level,
            )
        )

        print(
            f"Score {score_level} "
            f"signals: "
            f"{len(filtered_signals)}"
        )

        strategy_name = (
            f"V1.4.1 SCORE "
            f"{score_level}/14"
        )

        print()
        print("=" * 96)

        print(
            f"{strategy_name} "
            "SL / TP MATRIX"
        )

        print("=" * 96)

        filtered_matrix, filtered_trades = (
            run_matrix(
                df,
                strategy_name,
                filtered_signals,
            )
        )

        matrices[
            f"score_{score_level}"
        ] = filtered_matrix

        trades[
            f"score_{score_level}"
        ] = filtered_trades

        all_comparisons.append(
            filtered_matrix
        )

        # ----------------------------------------------------
        # Side statistics
        # ----------------------------------------------------

        side_df = side_stats(
            filtered_trades
        )

        if not side_df.empty:

            side_df = side_df.copy()

            side_df[
                "score_level"
            ] = score_level

            side_frames.append(
                side_df
            )


    # ========================================================
    # COMPARISON
    # ========================================================

    comparison = pd.concat(
        all_comparisons,
        ignore_index=True,
    )

    print()
    print("=" * 120)

    print(
        "BASELINE vs SCORE 8 "
        "vs SCORE 9 "
        "vs SCORE 10"
    )

    print("=" * 120)

    display_columns = [
        "strategy",
        "sl_pct",
        "trades",
        "wins",
        "losses",
        "timeouts",
        "win_rate_pct",
        "net_pnl_pct",
        "profit_factor",
        "max_drawdown_pct",
        "avg_pnl_pct",
        "avg_hold_hours",
    ]

    print(
        comparison[
            display_columns
        ].to_string(
            index=False,
            float_format=lambda x:
                f"{x:.3f}",
        )
    )


    # ========================================================
    # BEST CANDIDATES
    # ========================================================

    print_best_candidates(
        comparison
    )


    # ========================================================
    # SIDE STATS
    # ========================================================

    if side_frames:

        side_df = pd.concat(
            side_frames,
            ignore_index=True,
        )

        print()
        print("=" * 90)

        print(
            "FILTERED SIDE STATISTICS"
        )

        print("=" * 90)

        print(
            side_df.to_string(
                index=False,
                float_format=lambda x:
                    f"{x:.3f}",
            )
        )

    else:

        side_df = pd.DataFrame()

        print()
        print(
            "No filtered side statistics."
        )


    # ========================================================
    # SCORE SUMMARY
    # ========================================================

    print()
    print("=" * 110)

    print(
        "SCORE QUALITY SUMMARY"
    )

    print("=" * 110)

    score_summary = []

    for score_level in (
        SCORE_LEVELS
    ):

        score_data = comparison[
            comparison["strategy"]
            ==
            (
                f"V1.4.1 SCORE "
                f"{score_level}/14"
            )
        ].copy()

        valid_score = score_data[
            score_data["trades"] >= 10
        ].copy()

        if valid_score.empty:

            score_summary.append({
                "score":
                    score_level,

                "status":
                    "LESS THAN 10 TRADES",

                "best_pf":
                    np.nan,

                "best_net":
                    np.nan,

                "lowest_dd":
                    np.nan,
            })

            continue

        best_pf_row = (
            valid_score.loc[
                valid_score[
                    "profit_factor"
                ].idxmax()
            ]
        )

        best_net_row = (
            valid_score.loc[
                valid_score[
                    "net_pnl_pct"
                ].idxmax()
            ]
        )

        lowest_dd_row = (
            valid_score.loc[
                valid_score[
                    "max_drawdown_pct"
                ].idxmax()
            ]
        )

        score_summary.append({

            "score":
                score_level,

            "status":
                "VALID",

            "best_pf":
                best_pf_row[
                    "profit_factor"
                ],

            "best_net":
                best_net_row[
                    "net_pnl_pct"
                ],

            "lowest_dd":
                lowest_dd_row[
                    "max_drawdown_pct"
                ],
        })

    score_summary_df = pd.DataFrame(
        score_summary
    )

    print(
        score_summary_df.to_string(
            index=False,
            float_format=lambda x:
                f"{x:.3f}",
        )
    )


    # ========================================================
    # SAVE
    # ========================================================

    save_results(
        comparison,
        matrices,
        trades,
        side_df,
    )


    # ========================================================
    # COMPLETE
    # ========================================================

    print()
    print("=" * 72)

    print(
        "BACKTEST COMPLETE"
    )

    print(
        f"Version: {VERSION}"
    )

    print("=" * 72)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    main()
