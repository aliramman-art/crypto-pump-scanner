# ============================================================
# DOGE REVERSE-TENKAN v1.4
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
# v1.4 ADDITIONS:
#   1) Minimum Tenkan distance
#   2) Distance contraction
#   3) Tenkan slope filter
#   4) Reversal candle confirmation
#   5) RSI extreme + RSI reversal
#   6) RVOL filter
#   7) Anti-explosion candle filter
#   8) 15M trend confirmation
#   9) Score-based entry
#
# BACKTEST:
#   Baseline vs Filtered v1.4
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

SYMBOL = "PF_DOGEUSD"

TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"

LOOKBACK_DAYS = 30

TENKAN_PERIOD = 9

# Main research threshold
DISTANCE_THRESHOLD = 0.75

# RSI
RSI_PERIOD = 14
RSI_LONG_LEVEL = 35
RSI_SHORT_LEVEL = 65

# Volume
RVOL_PERIOD = 20
RVOL_MIN = 1.20
RVOL_MAX = 3.00

# Candle filters
BODY_AVG_PERIOD = 20
MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00

# 15M confirmation
EMA_15M_PERIOD = 20

# Score
MIN_SCORE = 10

# SL matrix
SL_LEVELS = [
    0.50,
    0.75,
    1.00,
    1.25,
    1.50,
    2.00,
]

# TP is always entry-time Tenkan
TP_MODE = "ENTRY_TENKAN"

# Maximum holding period
MAX_HOLD_CANDLES = 144  # 12 hours on 5m

# Costs
FEE_PER_SIDE = 0.0
SLIPPAGE_PER_SIDE = 0.0

# Conservative same-candle rule
SAME_CANDLE_SL_FIRST = True

# Data
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

def fetch_chunk(url, start_dt, end_dt, chunk_number):

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
    print(f"Epoch from: {params['from']}")
    print(f"Epoch to  : {params['to']}")

    response = requests.get(
        url,
        params=params,
        timeout=REQUEST_TIMEOUT,
    )

    print(f"HTTP status: {response.status_code}")

    response.raise_for_status()

    data = response.json()

    if isinstance(data, dict):
        candles = data.get("candles", [])

        if not candles:
            candles = data.get("data", [])

        if not candles:
            candles = data.get("result", [])

    elif isinstance(data, list):
        candles = data

    else:
        candles = []

    print(f"Received {len(candles)} candles.")

    return candles


def download_data(url, days):

    print()
    print("Downloading Kraken Futures data...")

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    all_candles = []

    current = start_dt
    chunk_number = 1

    while current < end_dt:

        next_dt = min(
            current + timedelta(days=7),
            end_dt,
        )

        candles = fetch_chunk(
            url,
            current,
            next_dt,
            chunk_number,
        )

        all_candles.extend(candles)

        current = next_dt
        chunk_number += 1

        time.sleep(0.15)

    if not all_candles:
        raise RuntimeError("No candles received from Kraken.")

    print()
    print(f"Raw candles received: {len(all_candles)}")

    return all_candles


# ============================================================
# DATAFRAME
# ============================================================

def build_dataframe(candles):

    rows = []

    for candle in candles:

        try:

            if isinstance(candle, dict):

                timestamp = (
                    candle.get("time")
                    or candle.get("timestamp")
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
                volume = candle[5] if len(candle) > 5 else 0

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
        raise RuntimeError("Could not build dataframe.")

    # Kraken timestamps are normally milliseconds.
    # Handle seconds defensively.
    numeric_time = pd.to_numeric(
        df["time"],
        errors="coerce",
    )

    if numeric_time.dropna().max() > 10_000_000_000:
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
        df.dropna()
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    # Closed candles only
    now = pd.Timestamp.now(tz="UTC")

    if TIMEFRAME_5M == "5m":
        candle_close_time = (
            df["time"] + pd.Timedelta(minutes=5)
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
        f"Total valid CLOSED candles: {len(df)}"
    )

    if not df.empty:
        print(
            f"First candle: {df['time'].iloc[0]}"
        )
        print(
            f"Last candle : {df['time'].iloc[-1]}"
        )

    print("=" * 72)

    return df.reset_index(drop=True)


# ============================================================
# INDICATORS
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
        highest_high + lowest_low
    ) / 2

    return df


def calculate_rsi(series, period=14):

    delta = series.diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

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

    rs = avg_gain / avg_loss.replace(0, np.nan)

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


def add_5m_indicators(df):

    df = df.copy()

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD,
    )

    df["body"] = (
        df["close"] - df["open"]
    ).abs()

    df["range"] = (
        df["high"] - df["low"]
    )

    df["avg_body"] = (
        df["body"]
        .rolling(BODY_AVG_PERIOD)
        .mean()
    )

    df["avg_range"] = (
        df["range"]
        .rolling(BODY_AVG_PERIOD)
        .mean()
    )

    df["avg_volume"] = (
        df["volume"]
        .rolling(RVOL_PERIOD)
        .mean()
    )

    df["rvol"] = (
        df["volume"] /
        df["avg_volume"].replace(0, np.nan)
    )

    df["distance_pct"] = (
        (df["close"] - df["tenkan"])
        / df["tenkan"]
        * 100
    )

    df["distance_abs"] = (
        df["distance_pct"].abs()
    )

    df["tenkan_slope"] = (
        df["tenkan"] -
        df["tenkan"].shift(1)
    )

    df["distance_contracting"] = (
        df["distance_abs"] <
        df["distance_abs"].shift(1)
    )

    return df


def calculate_15m_indicators(df15):

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
# ALIGN 15M DATA TO 5M
# ============================================================

def merge_15m_context(df5, df15):

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
            "close": "close_15m",
            "ema20": "ema20_15m",
        }
    )

    # Only use completed 15m candles.
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

    return merged.reset_index(drop=True)


# ============================================================
# BASELINE SIGNAL
# ============================================================

def baseline_signal(row):

    distance = row["distance_pct"]

    if not np.isfinite(distance):
        return None

    if distance <= -DISTANCE_THRESHOLD:
        return "LONG"

    if distance >= DISTANCE_THRESHOLD:
        return "SHORT"

    return None


# ============================================================
# V1.4 FILTERED SIGNAL
# ============================================================

def filtered_signal(row):

    distance = row["distance_pct"]

    if not np.isfinite(distance):
        return None, 0, []

    score = 0
    reasons = []

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if distance <= -DISTANCE_THRESHOLD:

        # Distance itself
        score += 2
        reasons.append("DISTANCE")

        # Distance must contract
        if bool(row["distance_contracting"]):
            score += 2
            reasons.append("CONTRACTION")

        # Tenkan should not be falling
        if (
            np.isfinite(row["tenkan_slope"])
            and row["tenkan_slope"] >= 0
        ):
            score += 2
            reasons.append("TENKAN_SLOPE")

        # Bullish reversal candle
        bullish = (
            row["close"] > row["open"]
            and row["close"] > row["close_prev"]
        )

        candle_range = row["range"]

        if candle_range > 0:
            close_position = (
                row["close"] - row["low"]
            ) / candle_range
        else:
            close_position = 0.5

        if (
            bullish
            and close_position >= 0.55
            and row["high"] > row["high_prev"]
        ):
            score += 2
            reasons.append("REVERSAL_CANDLE")

        # RSI oversold and rising
        if (
            np.isfinite(row["rsi"])
            and np.isfinite(row["rsi_prev"])
            and row["rsi"] < RSI_LONG_LEVEL
            and row["rsi"] > row["rsi_prev"]
        ):
            score += 2
            reasons.append("RSI")

        # RVOL
        if (
            np.isfinite(row["rvol"])
            and RVOL_MIN <= row["rvol"] <= RVOL_MAX
        ):
            score += 1
            reasons.append("RVOL")

        # No explosive candle
        body_ok = (
            np.isfinite(row["avg_body"])
            and row["body"]
            <= row["avg_body"] *
            MAX_BODY_MULTIPLIER
        )

        range_ok = (
            np.isfinite(row["avg_range"])
            and row["range"]
            <= row["avg_range"] *
            MAX_RANGE_MULTIPLIER
        )

        if body_ok and range_ok:
            score += 1
            reasons.append("NO_EXPLOSION")

        # 15m confirmation
        if (
            np.isfinite(row["close_15m"])
            and np.isfinite(row["ema20_15m"])
            and row["close_15m"]
            >= row["ema20_15m"]
        ):
            score += 2
            reasons.append("15M_CONFIRM")

        if score >= MIN_SCORE:
            return "LONG", score, reasons

        return None, score, reasons

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if distance >= DISTANCE_THRESHOLD:

        score += 2
        reasons.append("DISTANCE")

        if bool(row["distance_contracting"]):
            score += 2
            reasons.append("CONTRACTION")

        # Tenkan should not be rising
        if (
            np.isfinite(row["tenkan_slope"])
            and row["tenkan_slope"] <= 0
        ):
            score += 2
            reasons.append("TENKAN_SLOPE")

        bearish = (
            row["close"] < row["open"]
            and row["close"] < row["close_prev"]
        )

        candle_range = row["range"]

        if candle_range > 0:
            close_position = (
                row["close"] - row["low"]
            ) / candle_range
        else:
            close_position = 0.5

        if (
            bearish
            and close_position <= 0.45
            and row["low"] < row["low_prev"]
        ):
            score += 2
            reasons.append("REVERSAL_CANDLE")

        if (
            np.isfinite(row["rsi"])
            and np.isfinite(row["rsi_prev"])
            and row["rsi"] > RSI_SHORT_LEVEL
            and row["rsi"] < row["rsi_prev"]
        ):
            score += 2
            reasons.append("RSI")

        if (
            np.isfinite(row["rvol"])
            and RVOL_MIN <= row["rvol"] <= RVOL_MAX
        ):
            score += 1
            reasons.append("RVOL")

        body_ok = (
            np.isfinite(row["avg_body"])
            and row["body"]
            <= row["avg_body"] *
            MAX_BODY_MULTIPLIER
        )

        range_ok = (
            np.isfinite(row["avg_range"])
            and row["range"]
            <= row["avg_range"] *
            MAX_RANGE_MULTIPLIER
        )

        if body_ok and range_ok:
            score += 1
            reasons.append("NO_EXPLOSION")

        if (
            np.isfinite(row["close_15m"])
            and np.isfinite(row["ema20_15m"])
            and row["close_15m"]
            <= row["ema20_15m"]
        ):
            score += 2
            reasons.append("15M_CONFIRM")

        if score >= MIN_SCORE:
            return "SHORT", score, reasons

        return None, score, reasons

    return None, score, reasons


# ============================================================
# TRADE SIMULATION
# ============================================================

def apply_costs(pnl_pct):

    total_cost = (
        FEE_PER_SIDE +
        SLIPPAGE_PER_SIDE
    ) * 2

    return pnl_pct - total_cost


def simulate_trade(
    df,
    entry_index,
    side,
    sl_pct,
):

    entry_row = df.iloc[entry_index]

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
            (1 - sl_pct / 100)
        )

    else:

        tp_price = tenkan
        sl_price = (
            entry_price *
            (1 + sl_pct / 100)
        )

    max_index = min(
        entry_index + MAX_HOLD_CANDLES,
        len(df) - 1,
    )

    mfe = 0.0
    mae = 0.0

    for i in range(
        entry_index + 1,
        max_index + 1,
    ):

        row = df.iloc[i]

        high = float(row["high"])
        low = float(row["low"])

        if side == "LONG":

            favorable = (
                (high - entry_price)
                / entry_price
                * 100
            )

            adverse = (
                (low - entry_price)
                / entry_price
                * 100
            )

            mfe = max(mfe, favorable)
            mae = min(mae, adverse)

            hit_tp = high >= tp_price
            hit_sl = low <= sl_price

        else:

            favorable = (
                (entry_price - low)
                / entry_price
                * 100
            )

            adverse = (
                (entry_price - high)
                / entry_price
                * 100
            )

            mfe = max(mfe, favorable)
            mae = min(mae, -adverse)

            hit_tp = low <= tp_price
            hit_sl = high >= sl_price

        # Same candle
        if hit_tp and hit_sl:

            if SAME_CANDLE_SL_FIRST:

                exit_reason = "SL"
                exit_price = sl_price

            else:

                exit_reason = "TP"
                exit_price = tp_price

            hold_bars = i - entry_index

            break

        if hit_sl:

            exit_reason = "SL"
            exit_price = sl_price
            hold_bars = i - entry_index
            break

        if hit_tp:

            exit_reason = "TP"
            exit_price = tp_price
            hold_bars = i - entry_index
            break

    else:

        exit_reason = "TIMEOUT"
        exit_price = float(
            df.iloc[max_index]["close"]
        )

        hold_bars = (
            max_index - entry_index
        )

    if side == "LONG":

        pnl_pct = (
            (exit_price - entry_price)
            / entry_price
            * 100
        )

    else:

        pnl_pct = (
            (entry_price - exit_price)
            / entry_price
            * 100
        )

    pnl_pct = apply_costs(pnl_pct)

    return {
        "entry_time": entry_row["time"],
        "exit_time": df.iloc[
            entry_index + hold_bars
        ]["time"],
        "side": side,
        "entry_price": entry_price,
        "tp_price": tp_price,
        "sl_price": sl_price,
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "hold_bars": hold_bars,
        "hold_hours": hold_bars * 5 / 60,
        "pnl_pct": pnl_pct,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "score": entry_row.get(
            "score",
            np.nan,
        ),
    }


# ============================================================
# BACKTEST ENGINE
# ============================================================

def generate_baseline_trades(df):

    trades = []

    was_active = False

    for i in range(1, len(df)):

        signal = baseline_signal(
            df.iloc[i]
        )

        if signal is None:

            was_active = False
            continue

        if was_active:
            continue

        was_active = True

        row = df.iloc[i].copy()
        row["score"] = np.nan

        df.loc[i, "score"] = np.nan

        trades.append(
            (
                i,
                signal,
            )
        )

    return trades


def generate_filtered_trades(df):

    trades = []

    was_active = False

    for i in range(1, len(df)):

        signal, score, reasons = (
            filtered_signal(
                df.iloc[i]
            )
        )

        df.loc[i, "score"] = score

        if signal is None:

            # Re-arm only when price is
            # no longer in the extreme zone.
            distance = df.iloc[i][
                "distance_pct"
            ]

            if (
                np.isfinite(distance)
                and abs(distance)
                < DISTANCE_THRESHOLD
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
# STATISTICS
# ============================================================

def profit_factor(trades):

    if not trades:
        return 0.0

    wins = sum(
        max(t["pnl_pct"], 0)
        for t in trades
    )

    losses = abs(
        sum(
            min(t["pnl_pct"], 0)
            for t in trades
        )
    )

    if losses == 0:
        return np.inf if wins > 0 else 0.0

    return wins / losses


def max_drawdown(trades):

    if not trades:
        return 0.0

    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for trade in trades:

        equity += trade["pnl_pct"]

        peak = max(
            peak,
            equity,
        )

        dd = equity - peak

        max_dd = min(
            max_dd,
            dd,
        )

    return max_dd


def summarize_trades(
    trades,
    name,
):

    if not trades:

        return {
            "strategy": name,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "timeouts": 0,
            "win_rate_pct": 0,
            "net_pnl_pct": 0,
            "profit_factor": 0,
            "max_drawdown_pct": 0,
            "avg_pnl_pct": 0,
            "avg_hold_hours": 0,
            "avg_mfe_pct": 0,
            "avg_mae_pct": 0,
        }

    wins = sum(
        t["exit_reason"] == "TP"
        for t in trades
    )

    losses = sum(
        t["exit_reason"] == "SL"
        for t in trades
    )

    timeouts = sum(
        t["exit_reason"] == "TIMEOUT"
        for t in trades
    )

    pnls = [
        t["pnl_pct"]
        for t in trades
    ]

    return {
        "strategy": name,
        "trades": len(trades),
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "win_rate_pct": (
            wins / len(trades) * 100
        ),
        "net_pnl_pct": sum(pnls),
        "profit_factor": profit_factor(
            trades
        ),
        "max_drawdown_pct": max_drawdown(
            trades
        ),
        "avg_pnl_pct": np.mean(pnls),
        "avg_hold_hours": np.mean([
            t["hold_hours"]
            for t in trades
        ]),
        "avg_mfe_pct": np.mean([
            t["mfe_pct"]
            for t in trades
        ]),
        "avg_mae_pct": np.mean([
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

        for entry_index, side in signal_list:

            trade = simulate_trade(
                df,
                entry_index,
                side,
                sl,
            )

            trade["strategy"] = signal_type
            trade["sl_pct"] = sl

            trades.append(trade)

        stats = summarize_trades(
            trades,
            f"{signal_type} | SL {sl:.2f}%",
        )

        stats["sl_pct"] = sl

        rows.append(stats)

        all_trades.extend(trades)

        print()
        print("-" * 72)
        print(
            f"{signal_type} | SL = {sl:.2f}%"
        )
        print(
            f"Trades       : {stats['trades']}"
        )
        print(
            f"TP           : {stats['wins']}"
        )
        print(
            f"SL           : {stats['losses']}"
        )
        print(
            f"Timeout      : {stats['timeouts']}"
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

def side_stats(trades):

    if not trades:
        return pd.DataFrame()

    rows = []

    for side in ["LONG", "SHORT"]:

        subset = [
            t for t in trades
            if t["side"] == side
        ]

        if not subset:
            continue

        rows.append({
            "side": side,
            "trades": len(subset),
            "wins": sum(
                t["exit_reason"] == "TP"
                for t in subset
            ),
            "losses": sum(
                t["exit_reason"] == "SL"
                for t in subset
            ),
            "timeouts": sum(
                t["exit_reason"] == "TIMEOUT"
                for t in subset
            ),
            "win_rate_pct": (
                sum(
                    t["exit_reason"] == "TP"
                    for t in subset
                )
                / len(subset)
                * 100
            ),
            "net_pnl_pct": sum(
                t["pnl_pct"]
                for t in subset
            ),
            "avg_pnl_pct": np.mean([
                t["pnl_pct"]
                for t in subset
            ]),
            "avg_hold_hours": np.mean([
                t["hold_hours"]
                for t in subset
            ]),
        })

    return pd.DataFrame(rows)


# ============================================================
# CURRENT STATUS
# ============================================================

def print_current_status(df):

    row = df.iloc[-1]

    price = row["close"]
    tenkan = row["tenkan"]
    distance = row["distance_pct"]

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
        f"Distance : {distance:.3f}%"
    )

    baseline = baseline_signal(row)

    filtered, score, reasons = (
        filtered_signal(row)
    )

    print()
    print(
        f"Baseline 0.75% : "
        f"{baseline or 'No signal'}"
    )

    print(
        f"v1.4 Score     : {score}/{14}"
    )

    print(
        f"v1.4 Signal    : "
        f"{filtered or 'No signal'}"
    )

    if reasons:
        print(
            "Reasons        : "
            + ", ".join(reasons)
        )

    print("=" * 72)


# ============================================================
# SAVE
# ============================================================

def save_results(
    comparison,
    baseline_matrix,
    filtered_matrix,
    baseline_trades,
    filtered_trades,
    side_df,
):

    comparison.to_csv(
        "doge_reverse_tenkan_v14_comparison.csv",
        index=False,
    )

    baseline_matrix.to_csv(
        "doge_reverse_tenkan_v14_baseline_matrix.csv",
        index=False,
    )

    filtered_matrix.to_csv(
        "doge_reverse_tenkan_v14_filtered_matrix.csv",
        index=False,
    )

    baseline_trades.to_csv(
        "doge_reverse_tenkan_v14_baseline_trades.csv",
        index=False,
    )

    filtered_trades.to_csv(
        "doge_reverse_tenkan_v14_filtered_trades.csv",
        index=False,
    )

    side_df.to_csv(
        "doge_reverse_tenkan_v14_side_stats.csv",
        index=False,
    )

    print()
    print("=" * 72)
    print("RESULT FILES SAVED")
    print("=" * 72)

    print(
        "doge_reverse_tenkan_v14_comparison.csv"
    )

    print(
        "doge_reverse_tenkan_v14_baseline_matrix.csv"
    )

    print(
        "doge_reverse_tenkan_v14_filtered_matrix.csv"
    )

    print(
        "doge_reverse_tenkan_v14_baseline_trades.csv"
    )

    print(
        "doge_reverse_tenkan_v14_filtered_trades.csv"
    )

    print(
        "doge_reverse_tenkan_v14_side_stats.csv"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 72)
    print("DOGE REVERSE-TENKAN v1.4")
    print("=" * 72)

    print("Independent strategy")
    print("No VOLUME-KHAT connection")
    print("No real trading")
    print("No Telegram")

    print()
    print(f"Symbol       : {SYMBOL}")
    print(f"Timeframe    : {TIMEFRAME_5M}")
    print(f"Lookback     : {LOOKBACK_DAYS} days")
    print(f"Tenkan       : {TENKAN_PERIOD}")
    print(
        f"Main threshold: "
        f"{DISTANCE_THRESHOLD:.2f}%"
    )

    print()
    print("v1.4 filters:")
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
    print(
        f"Minimum score: {MIN_SCORE}/14"
    )

    print()
    print(
        "SL levels: "
        + ", ".join(
            f"{x:.2f}%"
            for x in SL_LEVELS
        )
    )

    print(
        f"TP           : {TP_MODE}"
    )

    print(
        f"Max hold     : "
        f"{MAX_HOLD_CANDLES} candles "
        f"({MAX_HOLD_CANDLES * 5 / 60:.1f}h)"
    )

    print(
        "Same candle  : SL first"
    )

    print(
        f"Fee/side     : "
        f"{FEE_PER_SIDE:.4f}%"
    )

    print(
        f"Slippage/side: "
        f"{SLIPPAGE_PER_SIDE:.4f}%"
    )

    # --------------------------------------------------------
    # DOWNLOAD 5M
    # --------------------------------------------------------

    candles5 = download_data(
        API_URL_5M,
        LOOKBACK_DAYS,
    )

    df5 = build_dataframe(
        candles5
    )

    # --------------------------------------------------------
    # DOWNLOAD 15M
    # --------------------------------------------------------

    print()
    print("Downloading 15M confirmation data...")

    candles15 = download_data(
        API_URL_15M,
        LOOKBACK_DAYS,
    )

    df15 = build_dataframe(
        candles15
    )

    print()
    print("Calculating indicators...")

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

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

    # --------------------------------------------------------
    # PREVIOUS VALUES
    # --------------------------------------------------------

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
    ).reset_index(drop=True)

    print(
        f"Valid Tenkan candles: {len(df)}"
    )

    # --------------------------------------------------------
    # CURRENT
    # --------------------------------------------------------

    print_current_status(
        df
    )

    # --------------------------------------------------------
    # GENERATE SIGNALS
    # --------------------------------------------------------

    print()
    print("=" * 72)
    print("GENERATING BASELINE SIGNALS")
    print("=" * 72)

    baseline_signals = (
        generate_baseline_trades(df)
    )

    print(
        f"Baseline signals: "
        f"{len(baseline_signals)}"
    )

    print()
    print("=" * 72)
    print("GENERATING v1.4 FILTERED SIGNALS")
    print("=" * 72)

    filtered_signals = (
        generate_filtered_trades(df)
    )

    print(
        f"v1.4 signals: "
        f"{len(filtered_signals)}"
    )

    # --------------------------------------------------------
    # BASELINE MATRIX
    # --------------------------------------------------------

    print()
    print("=" * 96)
    print("BASELINE 0.75% SL / TP MATRIX")
    print("=" * 96)

    baseline_matrix, baseline_trades = (
        run_matrix(
            df,
            "BASELINE 0.75%",
            baseline_signals,
        )
    )

    # --------------------------------------------------------
    # FILTERED MATRIX
    # --------------------------------------------------------

    print()
    print("=" * 96)
    print("v1.4 FILTERED SL / TP MATRIX")
    print("=" * 96)

    filtered_matrix, filtered_trades = (
        run_matrix(
            df,
            "V1.4 FILTERED",
            filtered_signals,
        )
    )

    # --------------------------------------------------------
    # COMPARISON
    # --------------------------------------------------------

    comparison = pd.concat(
        [
            baseline_matrix,
            filtered_matrix,
        ],
        ignore_index=True,
    )

    print()
    print("=" * 110)
    print("BASELINE vs v1.4 COMPARISON")
    print("=" * 110)

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
                f"{x:.3f}"
        )
    )

    # --------------------------------------------------------
    # BEST BY PROFIT FACTOR
    # --------------------------------------------------------

    valid = comparison[
        comparison["trades"] >= 10
    ].copy()

    if not valid.empty:

        best_pf = valid.loc[
            valid["profit_factor"].idxmax()
        ]

        best_pnl = valid.loc[
            valid["net_pnl_pct"].idxmax()
        ]

        lowest_dd = valid.loc[
            valid["max_drawdown_pct"].idxmax()
        ]

        print()
        print("=" * 72)
        print("BEST CANDIDATES")
        print("=" * 72)

        print()
        print(
            "Best Profit Factor "
            "(min 10 trades):"
        )

        print(
            f"{best_pf['strategy']} | "
            f"SL {best_pf['sl_pct']:.2f}% | "
            f"PF {best_pf['profit_factor']:.3f} | "
            f"Net {best_pf['net_pnl_pct']:.3f}% | "
            f"WR {best_pf['win_rate_pct']:.2f}% | "
            f"DD {best_pf['max_drawdown_pct']:.3f}%"
        )

        print()
        print(
            "Best Net P&L "
            "(min 10 trades):"
        )

        print(
            f"{best_pnl['strategy']} | "
            f"SL {best_pnl['sl_pct']:.2f}% | "
            f"PF {best_pnl['profit_factor']:.3f} | "
            f"Net {best_pnl['net_pnl_pct']:.3f}% | "
            f"WR {best_pnl['win_rate_pct']:.2f}% | "
            f"DD {best_pnl['max_drawdown_pct']:.3f}%"
        )

        print()
        print(
            "Lowest Drawdown "
            "(min 10 trades):"
        )

        print(
            f"{lowest_dd['strategy']} | "
            f"SL {lowest_dd['sl_pct']:.2f}% | "
            f"PF {lowest_dd['profit_factor']:.3f} | "
            f"Net {lowest_dd['net_pnl_pct']:.3f}% | "
            f"WR {lowest_dd['win_rate_pct']:.2f}% | "
            f"DD {lowest_dd['max_drawdown_pct']:.3f}%"
        )

    # --------------------------------------------------------
    # SIDE STATS
    # --------------------------------------------------------

    side_df = side_stats(
        filtered_trades
    )

    if not side_df.empty:

        print()
        print("=" * 72)
        print("v1.4 SIDE STATISTICS")
        print("=" * 72)

        print(
            side_df.to_string(
                index=False,
                float_format=lambda x:
                    f"{x:.3f}"
            )
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_results(
        comparison,
        baseline_matrix,
        filtered_matrix,
        baseline_trades,
        filtered_trades,
        side_df,
    )

    print()
    print("=" * 72)
    print("BACKTEST COMPLETE")
    print("=" * 72)


if __name__ == "__main__":
    main()
