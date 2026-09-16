# ============================================================
# DOGE ICHIMOKU EQUILIBRIUM
# 12-MONTH HISTORICAL BACKTEST
# ============================================================
#
# ARCHITECTURE
#
# 1H  = MARKET REGIME
# 15M = KIJUN EQUILIBRIUM + REACTION
# 5M  = ENTRY TRIGGER
# SL  = STRUCTURAL
# TP  = STRUCTURAL
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
#
# Kraken Futures
# LBank fee model
# Real trading: DISABLED
#
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
import json
import os
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIG
# ============================================================

SYMBOL = "PF_DOGEUSD"

BASE_URL = "https://futures.kraken.com/api/charts/v1"

RES_5M = "5m"
RES_15M = "15m"
RES_1H = "1h"

# ------------------------------------------------------------
# BACKTEST PERIOD
# ------------------------------------------------------------

BACKTEST_DAYS = 365

# Extra historical warmup
WARMUP_DAYS = 10

# ------------------------------------------------------------
# ICHIMOKU
# ------------------------------------------------------------

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
KUMO_SHIFT = 26

# ------------------------------------------------------------
# ATR
# ------------------------------------------------------------

ATR_PERIOD = 14

# ------------------------------------------------------------
# EQUILIBRIUM
# ------------------------------------------------------------

EQUILIBRIUM_ATR_MULT = 0.75

# ------------------------------------------------------------
# STRUCTURAL SL
# ------------------------------------------------------------

SL_ATR_BUFFER = 0.25
SWING_LOOKBACK_5M = 12
SWING_LOOKBACK_15M = 40

MAX_SL_PCT = 3.00

# ------------------------------------------------------------
# TP
# ------------------------------------------------------------

MIN_RR = 1.50
MAX_TP_PCT = 8.00

# ------------------------------------------------------------
# FEES
# ------------------------------------------------------------

LBANK_TAKER_FEE = 0.0006

# ------------------------------------------------------------
# CAPITAL
# ------------------------------------------------------------

INITIAL_CAPITAL = 100.0

# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "Ichimoku-Equilibrium-Backtester/1.0"
    }
)

# ============================================================
# HELPERS
# ============================================================


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return np.nan


def interval_seconds(resolution):
    mapping = {
        "5m": 300,
        "15m": 900,
        "1h": 3600,
    }
    return mapping[resolution]


def utc_now():
    return datetime.now(timezone.utc)


# ============================================================
# FETCH KRAKEN DATA
# ============================================================


def fetch_kraken_range(symbol, resolution, start_ts, end_ts):
    """
    Download historical Kraken Futures candles.

    Kraken chart endpoint returns OHLCV candles.
    Data is downloaded in chunks to cover the full year.
    """

    rows = []

    step = interval_seconds(resolution)

    # Conservative chunk sizes
    if resolution == "5m":
        chunk_seconds = 7 * 24 * 3600
    elif resolution == "15m":
        chunk_seconds = 20 * 24 * 3600
    else:
        chunk_seconds = 60 * 24 * 3600

    current = int(start_ts)

    while current < end_ts:

        chunk_end = min(
            current + chunk_seconds,
            int(end_ts)
        )

        url = (
            f"{BASE_URL}/{symbol}/{resolution}"
            f"?from={current}&to={chunk_end}"
        )

        print(
            f"Downloading {resolution}: "
            f"{datetime.fromtimestamp(current, timezone.utc)} "
            f"-> "
            f"{datetime.fromtimestamp(chunk_end, timezone.utc)}"
        )

        try:
            response = SESSION.get(
                url,
                timeout=30
            )

            response.raise_for_status()

            payload = response.json()

        except Exception as exc:
            print(
                f"Download error {resolution}: {exc}"
            )

            time.sleep(3)
            continue

        data = payload.get("candles", [])

        if not data:
            print("No candles returned.")
            current = chunk_end + step
            continue

        for candle in data:

            try:

                if isinstance(candle, dict):

                    ts = (
                        candle.get("time")
                        or candle.get("timestamp")
                        or candle.get("t")
                    )

                    open_price = (
                        candle.get("open")
                        or candle.get("o")
                    )

                    high = (
                        candle.get("high")
                        or candle.get("h")
                    )

                    low = (
                        candle.get("low")
                        or candle.get("l")
                    )

                    close = (
                        candle.get("close")
                        or candle.get("c")
                    )

                    volume = (
                        candle.get("volume")
                        or candle.get("v")
                        or 0
                    )

                else:
                    # Fallback for array format
                    ts = candle[0]
                    open_price = candle[1]
                    high = candle[2]
                    low = candle[3]
                    close = candle[4]
                    volume = candle[5] if len(candle) > 5 else 0

                ts = float(ts)

                # Kraken may return milliseconds
                if ts > 10_000_000_000:
                    ts /= 1000.0

                rows.append(
                    [
                        pd.to_datetime(
                            ts,
                            unit="s",
                            utc=True
                        ),
                        safe_float(open_price),
                        safe_float(high),
                        safe_float(low),
                        safe_float(close),
                        safe_float(volume),
                    ]
                )

            except Exception:
                continue

        current = chunk_end + step

        # Avoid hammering API
        time.sleep(0.15)

    if not rows:
        raise RuntimeError(
            f"No historical data received for {resolution}"
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    )

    df = df.reset_index(drop=True)

    return df


# ============================================================
# RESAMPLE / CLEAN
# ============================================================


def clean_candles(df):

    df = df.copy()

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        utc=True
    )

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in numeric_cols:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.sort_values(
        "timestamp"
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.reset_index(drop=True)

    return df


# ============================================================
# ATR
# ============================================================


def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    atr = tr.rolling(
        period
    ).mean()

    return atr


# ============================================================
# ICHIMOKU
# ============================================================


def add_ichimoku(df):

    df = df.copy()

    high = df["high"]
    low = df["low"]

    df["tenkan"] = (
        high.rolling(
            TENKAN_PERIOD
        ).max()
        +
        low.rolling(
            TENKAN_PERIOD
        ).min()
    ) / 2.0

    df["kijun"] = (
        high.rolling(
            KIJUN_PERIOD
        ).max()
        +
        low.rolling(
            KIJUN_PERIOD
        ).min()
    ) / 2.0

    senkou_a_raw = (
        df["tenkan"] +
        df["kijun"]
    ) / 2.0

    senkou_b_raw = (
        high.rolling(
            SENKOU_B_PERIOD
        ).max()
        +
        low.rolling(
            SENKOU_B_PERIOD
        ).min()
    ) / 2.0

    # IMPORTANT:
    #
    # The cloud visible NOW was calculated
    # 26 candles earlier.
    #
    # Therefore shift the calculated values
    # backwards in the dataframe to align them
    # with the candle where they become visible.
    #
    df["senkou_a"] = senkou_a_raw.shift(
        KUMO_SHIFT
    )

    df["senkou_b"] = senkou_b_raw.shift(
        KUMO_SHIFT
    )

    df["kumo_top"] = df[
        ["senkou_a", "senkou_b"]
    ].max(axis=1)

    df["kumo_bottom"] = df[
        ["senkou_a", "senkou_b"]
    ].min(axis=1)

    df["atr"] = calculate_atr(
        df,
        ATR_PERIOD
    )

    return df


# ============================================================
# 1H REGIME
# ============================================================


def get_regime(row, previous_row):

    required = [
        "close",
        "tenkan",
        "kijun",
        "kumo_top",
        "kumo_bottom",
    ]

    if any(
        pd.isna(row[x])
        for x in required
    ):
        return None

    if pd.isna(previous_row["kijun"]):
        return None

    price = row["close"]

    tenkan = row["tenkan"]
    kijun = row["kijun"]

    kijun_slope = (
        kijun -
        previous_row["kijun"]
    )

    # LONG regime
    if (
        price > row["kumo_top"]
        and tenkan > kijun
        and kijun_slope > 0
    ):
        return "LONG"

    # SHORT regime
    if (
        price < row["kumo_bottom"]
        and tenkan < kijun
        and kijun_slope < 0
    ):
        return "SHORT"

    return None


# ============================================================
# TIME ALIGNMENT HELPERS
# ============================================================


def get_latest_closed_row(
    df,
    timestamp,
):
    """
    Return latest candle whose timestamp
    is <= the current 5M candle timestamp.
    """

    subset = df[
        df["timestamp"] <= timestamp
    ]

    if subset.empty:
        return None

    return subset.iloc[-1]


def get_previous_row(
    df,
    timestamp,
):

    subset = df[
        df["timestamp"] < timestamp
    ]

    if subset.empty:
        return None

    return subset.iloc[-1]


# ============================================================
# 15M EQUILIBRIUM
# ============================================================


def check_15m_equilibrium(
    df15,
    current_time,
    direction,
):

    row = get_latest_closed_row(
        df15,
        current_time
    )

    if row is None:
        return None

    previous = get_previous_row(
        df15,
        row["timestamp"]
    )

    if previous is None:
        return None

    if pd.isna(row["kijun"]) or pd.isna(row["atr"]):
        return None

    kijun = row["kijun"]
    atr = row["atr"]

    if atr <= 0:
        return None

    distance = abs(
        row["close"] - kijun
    )

    equilibrium_limit = (
        atr *
        EQUILIBRIUM_ATR_MULT
    )

    near_equilibrium = (
        distance <= equilibrium_limit
    )

    if not near_equilibrium:
        return None

    bullish_reaction = (
        row["close"] > row["open"]
        and row["close"] >= previous["close"]
    )

    bearish_reaction = (
        row["close"] < row["open"]
        and row["close"] <= previous["close"]
    )

    if direction == "LONG" and bullish_reaction:

        return {
            "timestamp": row["timestamp"],
            "kijun": kijun,
            "atr": atr,
            "distance": distance,
            "row": row,
        }

    if direction == "SHORT" and bearish_reaction:

        return {
            "timestamp": row["timestamp"],
            "kijun": kijun,
            "atr": atr,
            "distance": distance,
            "row": row,
        }

    return None


# ============================================================
# 5M TRIGGER
# ============================================================


def check_5m_trigger(
    df5,
    current_time,
    direction,
    equilibrium,
):

    row = get_latest_closed_row(
        df5,
        current_time
    )

    if row is None:
        return None

    previous = get_previous_row(
        df5,
        row["timestamp"]
    )

    if previous is None:
        return None

    if pd.isna(row["tenkan"]):
        return None

    # Current 5M candle must interact
    # with the 15M equilibrium area.

    equilibrium_price = equilibrium["kijun"]

    equilibrium_atr = equilibrium["atr"]

    zone = (
        equilibrium_atr *
        EQUILIBRIUM_ATR_MULT
    )

    interacts = (
        row["low"] <= equilibrium_price + zone
        and
        row["high"] >= equilibrium_price - zone
    )

    if not interacts:
        return None

    bullish = (
        row["close"] > row["open"]
    )

    bearish = (
        row["close"] < row["open"]
    )

    if direction == "LONG":

        tenkan_reclaim = (
            row["close"] >
            row["tenkan"]
        )

        structure_break = (
            row["close"] >
            previous["high"]
        )

        if bullish and (
            tenkan_reclaim
            or structure_break
        ):

            return {
                "timestamp": row["timestamp"],
                "entry": row["close"],
                "row": row,
            }

    if direction == "SHORT":

        tenkan_loss = (
            row["close"] <
            row["tenkan"]
        )

        structure_break = (
            row["close"] <
            previous["low"]
        )

        if bearish and (
            tenkan_loss
            or structure_break
        ):

            return {
                "timestamp": row["timestamp"],
                "entry": row["close"],
                "row": row,
            }

    return None


# ============================================================
# STRUCTURAL SL
# ============================================================


def calculate_structural_sl(
    df5,
    df15,
    signal_time,
    direction,
    entry,
):

    row15 = get_latest_closed_row(
        df15,
        signal_time
    )

    if row15 is None:
        return None

    if pd.isna(row15["kijun"]) or pd.isna(row15["atr"]):
        return None

    kijun = row15["kijun"]
    atr = row15["atr"]

    df5_before = df5[
        df5["timestamp"] <= signal_time
    ]

    if len(df5_before) < SWING_LOOKBACK_5M:
        return None

    recent5 = df5_before.tail(
        SWING_LOOKBACK_5M
    )

    swing_low = recent5["low"].min()
    swing_high = recent5["high"].max()

    buffer = atr * SL_ATR_BUFFER

    if direction == "LONG":

        # Structural protection must be BELOW entry.
        candidates = [
            kijun,
            swing_low,
        ]

        candidates = [
            x for x in candidates
            if pd.notna(x)
            and x < entry
        ]

        if not candidates:
            return None

        base_sl = max(candidates)

        sl = base_sl - buffer

        if sl <= 0:
            return None

        distance_pct = (
            (entry - sl)
            / entry
            * 100
        )

    else:

        candidates = [
            kijun,
            swing_high,
        ]

        candidates = [
            x for x in candidates
            if pd.notna(x)
            and x > entry
        ]

        if not candidates:
            return None

        base_sl = min(candidates)

        sl = base_sl + buffer

        distance_pct = (
            (sl - entry)
            / entry
            * 100
        )

    if distance_pct <= 0:
        return None

    if distance_pct > MAX_SL_PCT:
        return None

    return float(sl)


# ============================================================
# STRUCTURAL PIVOT LEVELS
# ============================================================


def find_confirmed_pivot_levels(
    df,
    current_time,
):

    """
    IMPORTANT:
    A pivot is only available after the
    candles to its RIGHT have closed.

    This prevents future leakage.
    """

    data = df[
        df["timestamp"] <= current_time
    ].copy()

    if len(data) < 10:
        return []

    levels = []

    # Need two candles on each side.
    for i in range(
        2,
        len(data) - 2
    ):

        center = data.iloc[i]

        left1 = data.iloc[i - 1]
        left2 = data.iloc[i - 2]

        right1 = data.iloc[i + 1]
        right2 = data.iloc[i + 2]

        # Confirmed swing high
        if (
            center["high"] > left1["high"]
            and
            center["high"] > left2["high"]
            and
            center["high"] >= right1["high"]
            and
            center["high"] >= right2["high"]
        ):

            levels.append(
                float(center["high"])
            )

        # Confirmed swing low
        if (
            center["low"] < left1["low"]
            and
            center["low"] < left2["low"]
            and
            center["low"] <= right1["low"]
            and
            center["low"] <= right2["low"]
        ):

            levels.append(
                float(center["low"])
            )

    return levels


# ============================================================
# TP
# ============================================================


def calculate_structural_tp(
    df15,
    signal_time,
    direction,
    entry,
    sl,
):

    levels = find_confirmed_pivot_levels(
        df15,
        signal_time
    )

    if not levels:
        return None

    sl_distance = abs(
        entry - sl
    )

    if sl_distance <= 0:
        return None

    # Remove duplicate / almost identical levels
    cleaned = []

    for level in sorted(levels):

        if not cleaned:
            cleaned.append(level)
            continue

        if abs(level - cleaned[-1]) / level > 0.001:
            cleaned.append(level)

    if direction == "LONG":

        candidates = sorted(
            [
                x for x in cleaned
                if x > entry
            ]
        )

    else:

        candidates = sorted(
            [
                x for x in cleaned
                if x < entry
            ],
            reverse=True
        )

    for tp in candidates:

        tp_distance = abs(
            tp - entry
        )

        tp_pct = (
            tp_distance /
            entry *
            100
        )

        if tp_pct <= 0:
            continue

        if tp_pct > MAX_TP_PCT:
            continue

        rr = (
            tp_distance /
            sl_distance
        )

        if rr >= MIN_RR:

            return {
                "tp": float(tp),
                "rr": float(rr),
                "distance_pct": float(tp_pct),
            }

    return None


# ============================================================
# TRADE PNL
# ============================================================


def calculate_gross_pnl(
    direction,
    entry,
    exit_price,
):

    if direction == "LONG":

        return (
            exit_price - entry
        ) / entry * 100

    return (
        entry - exit_price
    ) / entry * 100


def calculate_net_pnl(
    direction,
    entry,
    exit_price,
):

    gross = calculate_gross_pnl(
        direction,
        entry,
        exit_price
    )

    # Entry + exit taker fee
    total_fee_pct = (
        LBANK_TAKER_FEE * 2 * 100
    )

    return gross - total_fee_pct


# ============================================================
# BACKTEST ENGINE
# ============================================================


def run_backtest(
    df5,
    df15,
    df1h,
):

    trades = []

    active_trade = None

    capital = INITIAL_CAPITAL

    equity_curve = []

    start_time = df5["timestamp"].iloc[0]
    end_time = df5["timestamp"].iloc[-1]

    print()
    print("=" * 70)
    print("STARTING BACKTEST")
    print("=" * 70)
    print(f"Start: {start_time}")
    print(f"End:   {end_time}")
    print()

    for idx in range(
        len(df5)
    ):

        row5 = df5.iloc[idx]

        current_time = row5["timestamp"]

        # ----------------------------------------------------
        # ACTIVE TRADE
        # ----------------------------------------------------

        if active_trade is not None:

            high = row5["high"]
            low = row5["low"]

            direction = active_trade["direction"]

            exit_price = None
            exit_reason = None

            if direction == "LONG":

                hit_sl = (
                    low <=
                    active_trade["sl"]
                )

                hit_tp = (
                    high >=
                    active_trade["tp"]
                )

                # Conservative rule:
                # if both are touched in the same candle,
                # assume SL happened first.
                if hit_sl:

                    exit_price = active_trade["sl"]
                    exit_reason = "SL"

                elif hit_tp:

                    exit_price = active_trade["tp"]
                    exit_reason = "TP"

            else:

                hit_sl = (
                    high >=
                    active_trade["sl"]
                )

                hit_tp = (
                    low <=
                    active_trade["tp"]
                )

                if hit_sl:

                    exit_price = active_trade["sl"]
                    exit_reason = "SL"

                elif hit_tp:

                    exit_price = active_trade["tp"]
                    exit_reason = "TP"

            if exit_price is not None:

                gross_pnl = calculate_gross_pnl(
                    direction,
                    active_trade["entry"],
                    exit_price
                )

                net_pnl = calculate_net_pnl(
                    direction,
                    active_trade["entry"],
                    exit_price
                )

                capital *= (
                    1 +
                    net_pnl / 100
                )

                trade = {
                    "entry_time":
                        active_trade["entry_time"],

                    "exit_time":
                        current_time,

                    "direction":
                        direction,

                    "entry":
                        active_trade["entry"],

                    "sl":
                        active_trade["sl"],

                    "tp":
                        active_trade["tp"],

                    "exit":
                        exit_price,

                    "rr":
                        active_trade["rr"],

                    "exit_reason":
                        exit_reason,

                    "gross_pnl_pct":
                        gross_pnl,

                    "net_pnl_pct":
                        net_pnl,

                    "capital_after":
                        capital,

                    "regime":
                        active_trade["regime"],
                }

                trades.append(trade)

                print(
                    f"{current_time} | "
                    f"{direction:<5} | "
                    f"{exit_reason:<3} | "
                    f"Net {net_pnl:+.3f}% | "
                    f"Capital {capital:.2f}"
                )

                active_trade = None

        # ----------------------------------------------------
        # EQUITY
        # ----------------------------------------------------

        equity_curve.append(
            {
                "timestamp":
                    current_time,

                "capital":
                    capital,
            }
        )

        # ----------------------------------------------------
        # DON'T OPEN NEW TRADE
        # ----------------------------------------------------

        if active_trade is not None:
            continue

        # ----------------------------------------------------
        # 1H REGIME
        # ----------------------------------------------------

        row1h = get_latest_closed_row(
            df1h,
            current_time
        )

        if row1h is None:
            continue

        previous1h = get_previous_row(
            df1h,
            row1h["timestamp"]
        )

        if previous1h is None:
            continue

        regime = get_regime(
            row1h,
            previous1h
        )

        if regime is None:
            continue

        # ----------------------------------------------------
        # 15M EQUILIBRIUM
        # ----------------------------------------------------

        equilibrium = check_15m_equilibrium(
            df15,
            current_time,
            regime
        )

        if equilibrium is None:
            continue

        # ----------------------------------------------------
        # 5M TRIGGER
        # ----------------------------------------------------

        trigger = check_5m_trigger(
            df5,
            current_time,
            regime,
            equilibrium
        )

        if trigger is None:
            continue

        entry = trigger["entry"]

        # ----------------------------------------------------
        # STRUCTURAL SL
        # ----------------------------------------------------

        sl = calculate_structural_sl(
            df5,
            df15,
            current_time,
            regime,
            entry
        )

        if sl is None:
            continue

        # ----------------------------------------------------
        # STRUCTURAL TP
        # ----------------------------------------------------

        tp_data = calculate_structural_tp(
            df15,
            current_time,
            regime,
            entry,
            sl
        )

        if tp_data is None:
            continue

        tp = tp_data["tp"]
        rr = tp_data["rr"]

        # ----------------------------------------------------
        # CREATE TRADE
        # ----------------------------------------------------

        active_trade = {

            "entry_time":
                current_time,

            "direction":
                regime,

            "entry":
                entry,

            "sl":
                sl,

            "tp":
                tp,

            "rr":
                rr,

            "regime":
                regime,
        }

        print()
        print(
            f"{current_time} | "
            f"NEW {regime} | "
            f"Entry {entry:.8f} | "
            f"SL {sl:.8f} | "
            f"TP {tp:.8f} | "
            f"RR {rr:.2f}"
        )

    # ========================================================
    # CLOSE REMAINING TRADE AT LAST CLOSE
    # ========================================================

    if active_trade is not None:

        last_row = df5.iloc[-1]

        exit_price = last_row["close"]

        direction = active_trade["direction"]

        gross_pnl = calculate_gross_pnl(
            direction,
            active_trade["entry"],
            exit_price
        )

        net_pnl = calculate_net_pnl(
            direction,
            active_trade["entry"],
            exit_price
        )

        capital *= (
            1 +
            net_pnl / 100
        )

        trades.append(
            {
                "entry_time":
                    active_trade["entry_time"],

                "exit_time":
                    last_row["timestamp"],

                "direction":
                    direction,

                "entry":
                    active_trade["entry"],

                "sl":
                    active_trade["sl"],

                "tp":
                    active_trade["tp"],

                "exit":
                    exit_price,

                "rr":
                    active_trade["rr"],

                "exit_reason":
                    "END_OF_BACKTEST",

                "gross_pnl_pct":
                    gross_pnl,

                "net_pnl_pct":
                    net_pnl,

                "capital_after":
                    capital,

                "regime":
                    active_trade["regime"],
            }
        )

    return (
        pd.DataFrame(trades),
        pd.DataFrame(equity_curve)
    )


# ============================================================
# PERFORMANCE REPORT
# ============================================================


def performance_report(
    trades,
    equity,
):

    print()
    print("=" * 70)
    print("ICHIMOKU EQUILIBRIUM")
    print("12-MONTH BACKTEST REPORT")
    print("=" * 70)

    if trades.empty:

        print()
        print("NO TRADES")
        print("=" * 70)

        return

    total = len(trades)

    wins = (
        trades["net_pnl_pct"] > 0
    ).sum()

    losses = (
        trades["net_pnl_pct"] <= 0
    ).sum()

    win_rate = (
        wins / total * 100
    )

    net_sum = trades[
        "net_pnl_pct"
    ].sum()

    gross_profit = trades.loc[
        trades["net_pnl_pct"] > 0,
        "net_pnl_pct"
    ].sum()

    gross_loss = abs(
        trades.loc[
            trades["net_pnl_pct"] < 0,
            "net_pnl_pct"
        ].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit /
            gross_loss
        )
    else:
        profit_factor = np.inf

    average_trade = (
        trades["net_pnl_pct"].mean()
    )

    average_win = (
        trades.loc[
            trades["net_pnl_pct"] > 0,
            "net_pnl_pct"
        ].mean()
    )

    average_loss = (
        trades.loc[
            trades["net_pnl_pct"] < 0,
            "net_pnl_pct"
        ].mean()
    )

    # --------------------------------------------------------
    # MAX DRAWDOWN
    # --------------------------------------------------------

    if not equity.empty:

        equity["peak"] = (
            equity["capital"]
            .cummax()
        )

        equity["drawdown_pct"] = (
            (
                equity["capital"]
                -
                equity["peak"]
            )
            /
            equity["peak"]
            * 100
        )

        max_dd = (
            equity["drawdown_pct"]
            .min()
        )

    else:

        max_dd = 0.0

    # --------------------------------------------------------
    # FINAL CAPITAL
    # --------------------------------------------------------

    final_capital = (
        equity["capital"].iloc[-1]
        if not equity.empty
        else INITIAL_CAPITAL
    )

    total_return = (
        (
            final_capital /
            INITIAL_CAPITAL
        )
        - 1
    ) * 100

    print()
    print(
        f"Initial Capital : "
        f"{INITIAL_CAPITAL:.2f}"
    )

    print(
        f"Final Capital   : "
        f"{final_capital:.2f}"
    )

    print(
        f"Net Return      : "
        f"{total_return:+.2f}%"
    )

    print()

    print(
        f"Trades          : "
        f"{total}"
    )

    print(
        f"Wins            : "
        f"{wins}"
    )

    print(
        f"Losses          : "
        f"{losses}"
    )

    print(
        f"Win Rate        : "
        f"{win_rate:.2f}%"
    )

    print()

    print(
        f"Gross Profit    : "
        f"{gross_profit:+.3f}%"
    )

    print(
        f"Gross Loss      : "
        f"{-gross_loss:+.3f}%"
    )

    print(
        f"Net P&L         : "
        f"{net_sum:+.3f}%"
    )

    print(
        f"Profit Factor   : "
        f"{profit_factor:.3f}"
    )

    print(
        f"Average Trade   : "
        f"{average_trade:+.3f}%"
    )

    print(
        f"Average Win     : "
        f"{average_win:+.3f}%"
    )

    print(
        f"Average Loss    : "
        f"{average_loss:+.3f}%"
    )

    print(
        f"Max Drawdown    : "
        f"{max_dd:.3f}%"
    )

    # --------------------------------------------------------
    # LONG / SHORT
    # --------------------------------------------------------

    print()
    print("-" * 70)
    print("LONG / SHORT")
    print("-" * 70)

    for side in ["LONG", "SHORT"]:

        subset = trades[
            trades["direction"] == side
        ]

        if subset.empty:

            print(
                f"{side}: No trades"
            )

            continue

        side_wins = (
            subset["net_pnl_pct"] > 0
        ).sum()

        side_wr = (
            side_wins /
            len(subset) *
            100
        )

        side_pnl = (
            subset["net_pnl_pct"].sum()
        )

        print(
            f"{side:<5} | "
            f"Trades {len(subset):<4} | "
            f"WR {side_wr:6.2f}% | "
            f"P&L {side_pnl:+.3f}%"
        )

    # --------------------------------------------------------
    # EXIT TYPES
    # --------------------------------------------------------

    print()
    print("-" * 70)
    print("EXIT TYPES")
    print("-" * 70)

    print(
        trades[
            "exit_reason"
        ].value_counts()
    )

    # --------------------------------------------------------
    # MONTHLY
    # --------------------------------------------------------

    trades["exit_time"] = pd.to_datetime(
        trades["exit_time"],
        utc=True
    )

    trades["month"] = (
        trades["exit_time"]
        .dt.to_period("M")
    )

    print()
    print("-" * 70)
    print("MONTHLY PERFORMANCE")
    print("-" * 70)

    monthly = (
        trades
        .groupby("month")
        .agg(
            Trades=("net_pnl_pct", "count"),
            Net_PnL=("net_pnl_pct", "sum"),
            Avg_Trade=("net_pnl_pct", "mean"),
        )
    )

    monthly["Wins"] = (
        trades
        .assign(
            win=(
                trades["net_pnl_pct"] > 0
            )
        )
        .groupby("month")["win"]
        .sum()
    )

    monthly["WR"] = (
        monthly["Wins"]
        /
        monthly["Trades"]
        * 100
    )

    for month, row in monthly.iterrows():

        print(
            f"{str(month)} | "
            f"Trades {int(row['Trades']):3d} | "
            f"WR {row['WR']:6.2f}% | "
            f"Net {row['Net_PnL']:+8.3f}%"
        )

    print()
    print("=" * 70)


# ============================================================
# SAVE RESULTS
# ============================================================


def save_results(
    trades,
    equity,
):

    trades_file = (
        "doge_ichimoku_equilibrium_"
        "12m_trades.csv"
    )

    equity_file = (
        "doge_ichimoku_equilibrium_"
        "12m_equity.csv"
    )

    report_file = (
        "doge_ichimoku_equilibrium_"
        "12m_report.txt"
    )

    trades.to_csv(
        trades_file,
        index=False
    )

    equity.to_csv(
        equity_file,
        index=False
    )

    # --------------------------------------------------------
    # TEXT REPORT
    # --------------------------------------------------------

    lines = []

    lines.append(
        "DOGE ICHIMOKU EQUILIBRIUM"
    )

    lines.append(
        "12 MONTH BACKTEST"
    )

    lines.append(
        "=" * 60
    )

    if trades.empty:

        lines.append(
            "NO TRADES"
        )

    else:

        total = len(trades)

        wins = (
            trades["net_pnl_pct"] > 0
        ).sum()

        losses = (
            trades["net_pnl_pct"] <= 0
        ).sum()

        wr = (
            wins /
            total *
            100
        )

        net = (
            trades["net_pnl_pct"].sum()
        )

        gp = trades.loc[
            trades["net_pnl_pct"] > 0,
            "net_pnl_pct"
        ].sum()

        gl = abs(
            trades.loc[
                trades["net_pnl_pct"] < 0,
                "net_pnl_pct"
            ].sum()
        )

        pf = (
            gp / gl
            if gl > 0
            else float("inf")
        )

        final_capital = (
            equity["capital"].iloc[-1]
            if not equity.empty
            else INITIAL_CAPITAL
        )

        if not equity.empty:

            peak = (
                equity["capital"]
                .cummax()
            )

            dd = (
                (
                    equity["capital"]
                    - peak
                )
                /
                peak
                * 100
            )

            max_dd = dd.min()

        else:

            max_dd = 0

        lines.extend(
            [
                f"Trades: {total}",
                f"Wins: {wins}",
                f"Losses: {losses}",
                f"Win Rate: {wr:.2f}%",
                f"Net P&L: {net:+.3f}%",
                f"Profit Factor: {pf:.3f}",
                f"Final Capital: {final_capital:.4f}",
                f"Max Drawdown: {max_dd:.3f}%",
            ]
        )

    with open(
        report_file,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "\n".join(lines)
        )

    print()
    print(
        f"Saved: {trades_file}"
    )

    print(
        f"Saved: {equity_file}"
    )

    print(
        f"Saved: {report_file}"
    )


# ============================================================
# MAIN
# ============================================================


def main():

    print()
    print("=" * 70)
    print(
        "DOGE ICHIMOKU EQUILIBRIUM"
    )
    print(
        "12-MONTH BACKTEST"
    )
    print("=" * 70)

    end_time = utc_now()

    start_time = (
        end_time -
        timedelta(
            days=BACKTEST_DAYS
        )
    )

    download_start = (
        start_time -
        timedelta(
            days=WARMUP_DAYS
        )
    )

    start_ts = (
        download_start.timestamp()
    )

    end_ts = (
        end_time.timestamp()
    )

    print()
    print(
        f"Backtest start : "
        f"{start_time}"
    )

    print(
        f"Backtest end   : "
        f"{end_time}"
    )

    print(
        f"Warmup         : "
        f"{WARMUP_DAYS} days"
    )

    print()

    # ========================================================
    # DOWNLOAD
    # ========================================================

    df5 = fetch_kraken_range(
        SYMBOL,
        RES_5M,
        start_ts,
        end_ts
    )

    df15 = fetch_kraken_range(
        SYMBOL,
        RES_15M,
        start_ts,
        end_ts
    )

    df1h = fetch_kraken_range(
        SYMBOL,
        RES_1H,
        start_ts,
        end_ts
    )

    # ========================================================
    # CLEAN
    # ========================================================

    df5 = clean_candles(df5)
    df15 = clean_candles(df15)
    df1h = clean_candles(df1h)

    # ========================================================
    # REMOVE INCOMPLETE LAST CANDLE
    # ========================================================

    now = utc_now()

    def remove_incomplete(df, resolution):

        seconds = interval_seconds(
            resolution
        )

        cutoff = (
            int(now.timestamp())
            // seconds
        ) * seconds

        cutoff_dt = pd.to_datetime(
            cutoff,
            unit="s",
            utc=True
        )

        return df[
            df["timestamp"] < cutoff_dt
        ].copy()

    df5 = remove_incomplete(
        df5,
        RES_5M
    )

    df15 = remove_incomplete(
        df15,
        RES_15M
    )

    df1h = remove_incomplete(
        df1h,
        RES_1H
    )

    # ========================================================
    # INDICATORS
    # ========================================================

    print()
    print(
        "Calculating Ichimoku..."
    )

    df5 = add_ichimoku(
        df5
    )

    df15 = add_ichimoku(
        df15
    )

    df1h = add_ichimoku(
        df1h
    )

    # ========================================================
    # CUT TO EXACT BACKTEST PERIOD
    # ========================================================

    df5 = df5[
        df5["timestamp"] >=
        pd.Timestamp(
            start_time
        )
    ].reset_index(drop=True)

    # ========================================================
    # DATA SUMMARY
    # ========================================================

    print()
    print("-" * 70)
    print("DATA")
    print("-" * 70)

    print(
        f"5M candles : {len(df5):,}"
    )

    print(
        f"15M candles: {len(df15):,}"
    )

    print(
        f"1H candles : {len(df1h):,}"
    )

    if df5.empty:

        raise RuntimeError(
            "No 5M candles available."
        )

    # ========================================================
    # RUN
    # ========================================================

    trades, equity = run_backtest(
        df5,
        df15,
        df1h
    )

    # ========================================================
    # REPORT
    # ========================================================

    performance_report(
        trades,
        equity
    )

    # ========================================================
    # SAVE
    # ========================================================

    save_results(
        trades,
        equity
    )


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":
    main()
