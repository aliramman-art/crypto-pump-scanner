# ============================================================
# NDS ALGORITHMIC v1
# Nodal Displacement Sequencing - Algorithmic Prototype
#
# IMPORTANT:
# This is an NDS-INSPIRED mechanical backtest.
# It is NOT claimed to be the official/proprietary NDS system.
#
# MARKET:
# Kraken Futures
#
# TIMEFRAMES:
# 1H  = Market Structure
# 15M = Displacement + Retracement
# 5M  = Flag + Breakout Confirmation
#
# FEATURES:
# - Full Kraken historical pagination
# - 365-day backtest
# - 30-day warm-up
# - Closed candles only
# - No incomplete 1H / 15M candle usage
# - No look-ahead in higher-timeframe structure
# - LONG / SHORT
# - One active trade
# - Fixed 1.5R target
# - ATR-based stop
# - 0.06% fee per side
# - Real trading disabled
#
# OUTPUT:
# doge_nds_algorithmic_v1_trades.csv
# doge_nds_algorithmic_v1_equity.csv
# doge_nds_algorithmic_v1_report.txt
# ============================================================

import os
import time
import math
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone


# ============================================================
# SETTINGS
# ============================================================

VERSION = "NDS Algorithmic v1"

SYMBOL = "pf_dogeusd"

INITIAL_CAPITAL = 100.0

REAL_TRADING = False

BACKTEST_DAYS = 365
WARMUP_DAYS = 30

FEE_RATE = 0.0006
ROUND_TRIP_FEE = FEE_RATE * 2

# ------------------------------------------------------------
# NDS PARAMETERS
# ------------------------------------------------------------

ATR_PERIOD = 14

HTF_PIVOT_SWING = 3

DISPLACEMENT_LOOKBACK = 20
DISPLACEMENT_ATR_MULT = 1.0

RETRACEMENT_LOOKBACK = 12
MIN_RETRACEMENT = 0.25
MAX_RETRACEMENT = 0.75

FLAG_LOOKBACK = 12
FLAG_MAX_RANGE_ATR = 1.50

BREAKOUT_LOOKBACK = 3

SL_ATR_BUFFER = 0.25

MIN_RR = 1.50

# One active trade only
MAX_ACTIVE_TRADES = 1


# ============================================================
# OUTPUT FILES
# ============================================================

TRADES_FILE = "doge_nds_algorithmic_v1_trades.csv"
EQUITY_FILE = "doge_nds_algorithmic_v1_equity.csv"
REPORT_FILE = "doge_nds_algorithmic_v1_report.txt"


# ============================================================
# KRAKEN API
# ============================================================

KRAKEN_URL = (
    "https://futures.kraken.com/api/charts/v1/"
    "trade/{symbol}/{resolution}"
)


RESOLUTION_SECONDS = {
    "1m": 60,
    "5m": 300,
    "15m": 900,
    "30m": 1800,
    "1h": 3600,
    "4h": 14400,
    "12h": 43200,
    "1d": 86400,
    "1w": 604800,
}


# ============================================================
# GLOBAL SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "Accept": "application/json",
        "User-Agent": "NDS-Algorithmic-v1/1.0",
    }
)


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return np.nan


# ============================================================
# FETCH KRAKEN CANDLES
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    start_ts,
    end_ts,
):
    """
    Download complete Kraken Futures historical candles.

    Kraken Charts API:
        /api/charts/v1/trade/{symbol}/{resolution}

    Query:
        from = epoch seconds
        to   = epoch seconds
        count = number of candles

    Response:
        candle time is epoch milliseconds.

    Pagination is performed using the timestamp of the
    last candle actually returned.

    This fixes the previous 2,000-candle limitation.
    """

    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(
            f"Unsupported resolution: {resolution}"
        )

    step = RESOLUTION_SECONDS[resolution]

    url = KRAKEN_URL.format(
        symbol=symbol,
        resolution=resolution,
    )

    cursor = int(start_ts)

    all_candles = []

    request_no = 0

    print()
    print(
        f"Downloading {resolution} candles..."
    )

    while cursor < int(end_ts):

        request_no += 1

        params = {
            "from": int(cursor),
            "to": int(end_ts),
            "count": 2000,
        }

        print(
            f"  {resolution} request #{request_no} "
            f"from "
            f"{pd.to_datetime(cursor, unit='s', utc=True)}"
        )

        response = SESSION.get(
            url,
            params=params,
            timeout=60,
        )

        response.raise_for_status()

        data = response.json()

        candles = data.get("candles", [])

        if not candles:
            print(
                f"  {resolution}: no more candles."
            )
            break

        all_candles.extend(candles)

        # ----------------------------------------------------
        # Find latest candle timestamp
        # ----------------------------------------------------

        timestamps_ms = []

        for candle in candles:
            try:
                timestamps_ms.append(
                    int(candle["time"])
                )
            except (
                KeyError,
                TypeError,
                ValueError,
            ):
                continue

        if not timestamps_ms:
            raise RuntimeError(
                f"No valid timestamps returned for "
                f"{resolution}"
            )

        latest_ms = max(timestamps_ms)

        latest_ts = latest_ms // 1000

        # ----------------------------------------------------
        # Check pagination flag
        # ----------------------------------------------------

        more_candles = bool(
            data.get("more_candles", False)
        )

        if not more_candles:
            break

        # ----------------------------------------------------
        # Move strictly beyond latest candle
        # ----------------------------------------------------

        next_cursor = latest_ts + step

        if next_cursor <= cursor:
            raise RuntimeError(
                f"Kraken pagination failed for {resolution}. "
                f"Current cursor={cursor}, "
                f"next={next_cursor}"
            )

        cursor = next_cursor

        # Small pause to be polite to the API
        time.sleep(0.10)

        # Safety
        if request_no > 1000:
            raise RuntimeError(
                f"Too many API requests for {resolution}."
            )

    # ========================================================
    # NO DATA
    # ========================================================

    if not all_candles:
        raise RuntimeError(
            f"No Kraken data received for "
            f"{symbol} {resolution}"
        )

    # ========================================================
    # DATAFRAME
    # ========================================================

    df = pd.DataFrame(all_candles)

    required = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    missing = [
        col for col in required
        if col not in df.columns
    ]

    if missing:
        raise RuntimeError(
            f"Kraken response missing columns: {missing}"
        )

    df = df[required].copy()

    # ========================================================
    # TIME
    # ========================================================

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True,
    )

    # ========================================================
    # NUMERIC
    # ========================================================

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce",
        )

    df = df.dropna(
        subset=[
            "time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    # ========================================================
    # REMOVE DUPLICATES
    # ========================================================

    df = (
        df
        .drop_duplicates(
            subset=["time"],
            keep="last",
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    # ========================================================
    # EXACT RANGE
    # ========================================================

    start_dt = pd.to_datetime(
        start_ts,
        unit="s",
        utc=True,
    )

    end_dt = pd.to_datetime(
        end_ts,
        unit="s",
        utc=True,
    )

    df = df[
        (df["time"] >= start_dt)
        &
        (df["time"] <= end_dt)
    ].copy()

    df = (
        df
        .drop_duplicates(
            subset=["time"],
            keep="last",
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    # ========================================================
    # EXPECTED DATA CHECK
    # ========================================================

    expected = int(
        (end_ts - start_ts) / step
    )

    actual = len(df)

    print(
        f"  {resolution} complete: "
        f"{actual:,} candles"
    )

    if actual > 0:

        print(
            f"  Actual range: "
            f"{df['time'].iloc[0]} "
            f"-> "
            f"{df['time'].iloc[-1]}"
        )

    print(
        f"  Expected approximately: "
        f"{expected:,}"
    )

    # Require at least 90% of expected data
    if actual < expected * 0.90:

        raise RuntimeError(
            f"INSUFFICIENT DATA for {resolution}: "
            f"received {actual:,}, "
            f"expected approximately {expected:,}"
        )

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (
        high - previous_close
    ).abs()

    tr3 = (
        low - previous_close
    ).abs()

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    atr = (
        true_range
        .rolling(period)
        .mean()
    )

    return atr


# ============================================================
# PIVOTS
# ============================================================

def calculate_confirmed_pivots(
    df,
    swing=3,
):
    """
    A pivot is only considered confirmed after the required
    candles to its right have closed.

    This prevents future candles from being used as if they
    were already known.
    """

    n = len(df)

    pivot_high = np.full(
        n,
        np.nan,
    )

    pivot_low = np.full(
        n,
        np.nan,
    )

    for i in range(
        swing,
        n - swing,
    ):

        current_high = df["high"].iloc[i]
        current_low = df["low"].iloc[i]

        left_high = df["high"].iloc[
            i - swing:i
        ]

        right_high = df["high"].iloc[
            i + 1:i + swing + 1
        ]

        left_low = df["low"].iloc[
            i - swing:i
        ]

        right_low = df["low"].iloc[
            i + 1:i + swing + 1
        ]

        if (
            current_high > left_high.max()
            and
            current_high > right_high.max()
        ):
            pivot_high[i] = current_high

        if (
            current_low < left_low.min()
            and
            current_low < right_low.min()
        ):
            pivot_low[i] = current_low

    df = df.copy()

    df["pivot_high"] = pivot_high
    df["pivot_low"] = pivot_low

    # Pivot becomes usable only after right-side candles close.
    df["confirmed_pivot_high"] = (
        df["pivot_high"].shift(swing)
    )

    df["confirmed_pivot_low"] = (
        df["pivot_low"].shift(swing)
    )

    return df


# ============================================================
# MARKET STRUCTURE
# ============================================================

def get_market_structure(
    df,
    current_index,
):
    """
    Uses only confirmed pivots available by current_index.
    """

    if current_index < 1:
        return "NEUTRAL"

    available = df.iloc[
        :current_index + 1
    ]

    highs = (
        available[
            available["confirmed_pivot_high"]
            .notna()
        ]
        ["confirmed_pivot_high"]
        .tolist()
    )

    lows = (
        available[
            available["confirmed_pivot_low"]
            .notna()
        ]
        ["confirmed_pivot_low"]
        .tolist()
    )

    if len(highs) < 2 or len(lows) < 2:
        return "NEUTRAL"

    last_high = highs[-1]
    previous_high = highs[-2]

    last_low = lows[-1]
    previous_low = lows[-2]

    bullish = (
        last_high > previous_high
        and
        last_low > previous_low
    )

    bearish = (
        last_high < previous_high
        and
        last_low < previous_low
    )

    if bullish:
        return "BULLISH"

    if bearish:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# DISPLACEMENT
# ============================================================

def detect_displacement(
    df15,
    idx,
):
    """
    Detect strong directional displacement on 15M.

    Current candle is already closed when evaluated.
    """

    if idx < DISPLACEMENT_LOOKBACK:
        return None

    atr = df15["atr"].iloc[idx]

    if pd.isna(atr) or atr <= 0:
        return None

    current = df15.iloc[idx]

    previous = df15.iloc[
        idx - DISPLACEMENT_LOOKBACK:idx
    ]

    previous_high = previous["high"].max()
    previous_low = previous["low"].min()

    bullish_range = (
        current["close"]
        - previous_high
    )

    bearish_range = (
        previous_low
        - current["close"]
    )

    bullish_body = (
        current["close"]
        - current["open"]
    )

    bearish_body = (
        current["open"]
        - current["close"]
    )

    if (
        bullish_body > 0
        and
        bullish_range >= DISPLACEMENT_ATR_MULT * atr
    ):
        return "BULLISH"

    if (
        bearish_body > 0
        and
        bearish_range >= DISPLACEMENT_ATR_MULT * atr
    ):
        return "BEARISH"

    return None


# ============================================================
# RETRACEMENT
# ============================================================

def detect_retracement(
    df15,
    idx,
    direction,
):
    """
    Detect controlled retracement after displacement.

    This is intentionally mechanical.
    """

    if idx < RETRACEMENT_LOOKBACK:
        return False

    window = df15.iloc[
        idx - RETRACEMENT_LOOKBACK:idx + 1
    ]

    if direction == "LONG":

        displacement_low = window["low"].min()
        displacement_high = window["high"].max()

        total_range = (
            displacement_high
            - displacement_low
        )

        if total_range <= 0:
            return False

        current_price = df15["close"].iloc[idx]

        retracement = (
            displacement_high
            - current_price
        ) / total_range

    else:

        displacement_high = window["high"].max()
        displacement_low = window["low"].min()

        total_range = (
            displacement_high
            - displacement_low
        )

        if total_range <= 0:
            return False

        current_price = df15["close"].iloc[idx]

        retracement = (
            current_price
            - displacement_low
        ) / total_range

    return (
        MIN_RETRACEMENT
        <= retracement
        <= MAX_RETRACEMENT
    )


# ============================================================
# FLAG
# ============================================================

def detect_flag(
    df5,
    idx,
    direction,
):
    """
    Detect a compact consolidation / flag before breakout.
    """

    if idx < FLAG_LOOKBACK + 1:
        return None

    window = df5.iloc[
        idx - FLAG_LOOKBACK:idx
    ]

    atr = df5["atr"].iloc[idx]

    if pd.isna(atr) or atr <= 0:
        return None

    flag_high = window["high"].max()
    flag_low = window["low"].min()

    flag_range = (
        flag_high
        - flag_low
    )

    if flag_range > FLAG_MAX_RANGE_ATR * atr:
        return None

    current = df5.iloc[idx]

    if direction == "LONG":

        breakout = (
            current["close"]
            > flag_high
        )

        if breakout:
            return {
                "flag_high": flag_high,
                "flag_low": flag_low,
            }

    else:

        breakout = (
            current["close"]
            < flag_low
        )

        if breakout:
            return {
                "flag_high": flag_high,
                "flag_low": flag_low,
            }

    return None


# ============================================================
# CREATE TRADE
# ============================================================

def create_trade(
    direction,
    entry_time,
    entry_price,
    flag,
    atr,
):
    """
    Creates fixed-risk 1.5R trade.
    """

    if (
        pd.isna(atr)
        or
        atr <= 0
    ):
        return None

    if direction == "LONG":

        stop_loss = (
            flag["flag_low"]
            - SL_ATR_BUFFER * atr
        )

        risk = (
            entry_price
            - stop_loss
        )

        if risk <= 0:
            return None

        take_profit = (
            entry_price
            + MIN_RR * risk
        )

    else:

        stop_loss = (
            flag["flag_high"]
            + SL_ATR_BUFFER * atr
        )

        risk = (
            stop_loss
            - entry_price
        )

        if risk <= 0:
            return None

        take_profit = (
            entry_price
            - MIN_RR * risk
        )

    if direction == "LONG":

        if (
            stop_loss >= entry_price
            or
            take_profit <= entry_price
        ):
            return None

    else:

        if (
            stop_loss <= entry_price
            or
            take_profit >= entry_price
        ):
            return None

    return {
        "entry_time": entry_time,
        "direction": direction,
        "entry": float(entry_price),
        "stop_loss": float(stop_loss),
        "take_profit": float(take_profit),
        "rr": float(MIN_RR),
    }


# ============================================================
# EXIT TRADE
# ============================================================

def check_exit(
    trade,
    candle,
):
    """
    Conservative rule:
    If both SL and TP are touched in the same candle,
    SL is assumed to happen first.
    """

    direction = trade["direction"]

    high = candle["high"]
    low = candle["low"]

    if direction == "LONG":

        stop_hit = (
            low <= trade["stop_loss"]
        )

        tp_hit = (
            high >= trade["take_profit"]
        )

        if stop_hit:
            return (
                trade["stop_loss"],
                "SL",
            )

        if tp_hit:
            return (
                trade["take_profit"],
                "TP",
            )

    else:

        stop_hit = (
            high >= trade["stop_loss"]
        )

        tp_hit = (
            low <= trade["take_profit"]
        )

        if stop_hit:
            return (
                trade["stop_loss"],
                "SL",
            )

        if tp_hit:
            return (
                trade["take_profit"],
                "TP",
            )

    return None, None


# ============================================================
# TRADE PNL
# ============================================================

def calculate_trade_return(
    trade,
    exit_price,
):
    entry = trade["entry"]

    if trade["direction"] == "LONG":

        gross_return = (
            exit_price - entry
        ) / entry

    else:

        gross_return = (
            entry - exit_price
        ) / entry

    net_return = (
        gross_return
        - ROUND_TRIP_FEE
    )

    return net_return


# ============================================================
# SAVE TRADE
# ============================================================

def finalize_trade(
    trade,
    exit_time,
    exit_price,
    exit_reason,
    capital,
):
    return_pct = calculate_trade_return(
        trade,
        exit_price,
    )

    pnl = (
        capital
        * return_pct
    )

    capital_after = (
        capital
        + pnl
    )

    return {
        "entry_time": trade["entry_time"],
        "exit_time": exit_time,
        "direction": trade["direction"],
        "entry": trade["entry"],
        "stop_loss": trade["stop_loss"],
        "take_profit": trade["take_profit"],
        "exit": float(exit_price),
        "return_pct": return_pct * 100.0,
        "pnl": pnl,
        "capital_after": capital_after,
        "exit_reason": exit_reason,
        "rr": trade["rr"],
    }


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    trades_df,
    initial_capital,
    final_capital,
    equity_df,
):
    if trades_df.empty:

        return {
            "initial_capital": initial_capital,
            "final_capital": final_capital,
            "net_return": (
                final_capital
                / initial_capital
                - 1
            ) * 100,
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "gross_profit": 0,
            "gross_loss": 0,
            "avg_trade": 0,
            "max_drawdown": 0,
        }

    wins_df = trades_df[
        trades_df["pnl"] > 0
    ]

    losses_df = trades_df[
        trades_df["pnl"] < 0
    ]

    wins = len(wins_df)
    losses = len(losses_df)

    gross_profit = (
        wins_df["pnl"].sum()
        if not wins_df.empty
        else 0.0
    )

    gross_loss = (
        abs(losses_df["pnl"].sum())
        if not losses_df.empty
        else 0.0
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
            else 0.0
        )

    trades = len(trades_df)

    win_rate = (
        wins / trades * 100
        if trades > 0
        else 0
    )

    avg_trade = (
        trades_df["return_pct"].mean()
        if trades > 0
        else 0
    )

    if (
        equity_df is not None
        and
        not equity_df.empty
    ):

        equity = (
            equity_df["capital"]
            .astype(float)
        )

        running_max = equity.cummax()

        drawdown = (
            equity
            / running_max
            - 1
        )

        max_drawdown = (
            drawdown.min()
            * 100
        )

    else:
        max_drawdown = 0.0

    return {
        "initial_capital": initial_capital,
        "final_capital": final_capital,
        "net_return": (
            final_capital
            / initial_capital
            - 1
        ) * 100,
        "trades": trades,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "avg_trade": avg_trade,
        "max_drawdown": max_drawdown,
    }


# ============================================================
# DIRECTION STATISTICS
# ============================================================

def direction_stats(
    trades_df,
    direction,
):
    df = trades_df[
        trades_df["direction"] == direction
    ]

    if df.empty:
        return {
            "trades": 0,
            "wr": 0.0,
            "pnl": 0.0,
            "pf": 0.0,
        }

    wins = df[
        df["pnl"] > 0
    ]

    losses = df[
        df["pnl"] < 0
    ]

    gross_profit = (
        wins["pnl"].sum()
        if not wins.empty
        else 0.0
    )

    gross_loss = (
        abs(losses["pnl"].sum())
        if not losses.empty
        else 0.0
    )

    if gross_loss > 0:
        pf = (
            gross_profit
            / gross_loss
        )
    else:
        pf = (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    return {
        "trades": len(df),
        "wr": (
            len(wins)
            / len(df)
            * 100
        ),
        "pnl": df["pnl"].sum(),
        "pf": pf,
    }


# ============================================================
# FORMAT PF
# ============================================================

def format_pf(value):
    if math.isinf(value):
        return "INF"

    return f"{value:.3f}"


# ============================================================
# MAIN BACKTEST
# ============================================================

def run_backtest():

    print("=" * 62)
    print(VERSION.upper())
    print("=" * 62)

    print(
        f"REAL TRADING: "
        f"{'ENABLED' if REAL_TRADING else 'DISABLED'}"
    )

    if REAL_TRADING:
        raise RuntimeError(
            "REAL_TRADING must remain DISABLED "
            "for this backtest."
        )

    # ========================================================
    # TIME RANGE
    # ========================================================

    now = utc_now()

    backtest_end = now

    backtest_start = (
        now
        - timedelta(days=BACKTEST_DAYS)
    )

    data_start = (
        backtest_start
        - timedelta(days=WARMUP_DAYS)
    )

    backtest_start_ts = int(
        backtest_start.timestamp()
    )

    backtest_end_ts = int(
        backtest_end.timestamp()
    )

    data_start_ts = int(
        data_start.timestamp()
    )

    print()
    print("=" * 62)
    print(VERSION.upper())
    print("=" * 62)

    print(
        f"Backtest start : "
        f"{backtest_start.isoformat()}"
    )

    print(
        f"Data start     : "
        f"{data_start.isoformat()}"
    )

    print(
        f"Backtest end   : "
        f"{backtest_end.isoformat()}"
    )

    # ========================================================
    # DOWNLOAD DATA
    # ========================================================

    print()
    print("Downloading Kraken data...")

    df1h = fetch_kraken_candles(
        SYMBOL,
        "1h",
        data_start_ts,
        backtest_end_ts,
    )

    df15 = fetch_kraken_candles(
        SYMBOL,
        "15m",
        data_start_ts,
        backtest_end_ts,
    )

    df5 = fetch_kraken_candles(
        SYMBOL,
        "5m",
        data_start_ts,
        backtest_end_ts,
    )

    # ========================================================
    # INDICATORS
    # ========================================================

    df1h["atr"] = calculate_atr(
        df1h,
        ATR_PERIOD,
    )

    df15["atr"] = calculate_atr(
        df15,
        ATR_PERIOD,
    )

    df5["atr"] = calculate_atr(
        df5,
        ATR_PERIOD,
    )

    # ========================================================
    # CONFIRMED PIVOTS
    # ========================================================

    df1h = calculate_confirmed_pivots(
        df1h,
        HTF_PIVOT_SWING,
    )

    # ========================================================
    # BACKTEST 5M RANGE
    # ========================================================

    test_mask = (
        df5["time"]
        >= pd.to_datetime(
            backtest_start_ts,
            unit="s",
            utc=True,
        )
    )

    test_df = df5[
        test_mask
    ].copy()

    print()
    print("=" * 62)
    print("DATA SUMMARY")
    print("=" * 62)

    print(
        f"1H candles  : {len(df1h):,}"
    )

    print(
        f"15M candles : {len(df15):,}"
    )

    print(
        f"5M candles  : {len(df5):,}"
    )

    print(
        f"Test 5M     : {len(test_df):,}"
    )

    # ========================================================
    # EXPECTED APPROXIMATE COUNTS
    # ========================================================

    expected_1h = BACKTEST_DAYS * 24
    expected_15m = BACKTEST_DAYS * 24 * 4
    expected_5m = BACKTEST_DAYS * 24 * 12

    print()
    print(
        "Expected approximate test-period candles:"
    )

    print(
        f"  1H  : {expected_1h:,}"
    )

    print(
        f"  15M : {expected_15m:,}"
    )

    print(
        f"  5M  : {expected_5m:,}"
    )

    # ========================================================
    # STATE
    # ========================================================

    capital = INITIAL_CAPITAL

    active_trade = None

    trades = []

    equity_records = []

    # ========================================================
    # HIGHER TF POINTERS
    #
    # IMPORTANT:
    # We use only HTF candles whose START TIME is strictly
    # before the current 5M candle.
    #
    # Therefore:
    #
    # 5M 10:10
    #
    # does NOT use:
    # 15M 10:00 candle
    #
    # because 10:00-10:15 is still incomplete.
    #
    # It uses the previous closed 15M candle.
    #
    # Same logic applies to 1H.
    # ========================================================

    times1h = (
        df1h["time"]
        .astype("int64")
        .values
    )

    times15 = (
        df15["time"]
        .astype("int64")
        .values
    )

    # ========================================================
    # LOOP
    # ========================================================

    for idx5, row5 in test_df.iterrows():

        current_time = row5["time"]

        current_ns = (
            current_time.value
        )

        # ----------------------------------------------------
        # 1H CLOSED CANDLE
        # ----------------------------------------------------

        idx1h = (
            np.searchsorted(
                times1h,
                current_ns,
                side="left",
            )
            - 1
        )

        if idx1h < 0:
            continue

        # ----------------------------------------------------
        # 15M CLOSED CANDLE
        # ----------------------------------------------------

        idx15 = (
            np.searchsorted(
                times15,
                current_ns,
                side="left",
            )
            - 1
        )

        if idx15 < 0:
            continue

        # ----------------------------------------------------
        # ACTIVE TRADE
        # ----------------------------------------------------

        if active_trade is not None:

            exit_price, exit_reason = (
                check_exit(
                    active_trade,
                    row5,
                )
            )

            if exit_price is not None:

                result = finalize_trade(
                    active_trade,
                    current_time,
                    exit_price,
                    exit_reason,
                    capital,
                )

                capital = (
                    result["capital_after"]
                )

                trades.append(result)

                equity_records.append(
                    {
                        "time": current_time,
                        "capital": capital,
                    }
                )

                active_trade = None

                # No new trade on same candle
                continue

        # ----------------------------------------------------
        # DO NOT OPEN ANOTHER TRADE
        # ----------------------------------------------------

        if active_trade is not None:
            continue

        # ----------------------------------------------------
        # NEED ENOUGH HISTORY
        # ----------------------------------------------------

        if idx1h < (
            HTF_PIVOT_SWING * 2
            + 10
        ):
            continue

        if idx15 < (
            DISPLACEMENT_LOOKBACK
            + RETRACEMENT_LOOKBACK
            + 5
        ):
            continue

        # ----------------------------------------------------
        # 1H MARKET STRUCTURE
        # ----------------------------------------------------

        structure = get_market_structure(
            df1h,
            idx1h,
        )

        if structure not in (
            "BULLISH",
            "BEARISH",
        ):
            continue

        # ----------------------------------------------------
        # 15M DISPLACEMENT
        # ----------------------------------------------------

        displacement = detect_displacement(
            df15,
            idx15,
        )

        if displacement is None:
            continue

        # ----------------------------------------------------
        # STRUCTURE + DISPLACEMENT
        # ----------------------------------------------------

        if (
            structure == "BULLISH"
            and
            displacement != "BULLISH"
        ):
            continue

        if (
            structure == "BEARISH"
            and
            displacement != "BEARISH"
        ):
            continue

        # ----------------------------------------------------
        # DIRECTION
        # ----------------------------------------------------

        if structure == "BULLISH":

            direction = "LONG"

        elif structure == "BEARISH":

            direction = "SHORT"

        else:
            continue

        # ----------------------------------------------------
        # RETRACEMENT
        # ----------------------------------------------------

        if not detect_retracement(
            df15,
            idx15,
            direction,
        ):
            continue

        # ----------------------------------------------------
        # 5M FLAG + BREAKOUT
        # ----------------------------------------------------

        flag = detect_flag(
            df5,
            idx5,
            direction,
        )

        if flag is None:
            continue

        # ----------------------------------------------------
        # ATR
        # ----------------------------------------------------

        atr = row5["atr"]

        if pd.isna(atr) or atr <= 0:
            continue

        # ----------------------------------------------------
        # ENTRY
        #
        # Signal is generated at the CLOSE of the closed
        # 5M candle.
        # ----------------------------------------------------

        entry_price = float(
            row5["close"]
        )

        # ----------------------------------------------------
        # CREATE TRADE
        # ----------------------------------------------------

        trade = create_trade(
            direction,
            current_time,
            entry_price,
            flag,
            atr,
        )

        if trade is None:
            continue

        active_trade = trade

    # ========================================================
    # CLOSE OPEN TRADE AT END
    # ========================================================

    if active_trade is not None:

        last_row = test_df.iloc[-1]

        exit_price = float(
            last_row["close"]
        )

        result = finalize_trade(
            active_trade,
            last_row["time"],
            exit_price,
            "END_OF_BACKTEST",
            capital,
        )

        capital = (
            result["capital_after"]
        )

        trades.append(result)

        equity_records.append(
            {
                "time": last_row["time"],
                "capital": capital,
            }
        )

        active_trade = None

    # ========================================================
    # DATAFRAMES
    # ========================================================

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_records
    )

    # ========================================================
    # SAVE TRADES
    # ========================================================

    trade_columns = [
        "entry_time",
        "exit_time",
        "direction",
        "entry",
        "stop_loss",
        "take_profit",
        "exit",
        "return_pct",
        "pnl",
        "capital_after",
        "exit_reason",
        "rr",
    ]

    if trades_df.empty:

        trades_df = pd.DataFrame(
            columns=trade_columns
        )

    else:

        trades_df = trades_df[
            trade_columns
        ]

    trades_df.to_csv(
        TRADES_FILE,
        index=False,
    )

    # ========================================================
    # SAVE EQUITY
    # ========================================================

    if equity_df.empty:

        equity_df = pd.DataFrame(
            columns=[
                "time",
                "capital",
            ]
        )

    equity_df.to_csv(
        EQUITY_FILE,
        index=False,
    )

    # ========================================================
    # STATISTICS
    # ========================================================

    stats = calculate_stats(
        trades_df,
        INITIAL_CAPITAL,
        capital,
        equity_df,
    )

    long_stats = direction_stats(
        trades_df,
        "LONG",
    )

    short_stats = direction_stats(
        trades_df,
        "SHORT",
    )

    # ========================================================
    # REPORT
    # ========================================================

    report_lines = []

    report_lines.append(
        "=" * 62
    )

    report_lines.append(
        "NDS ALGORITHMIC v1 BACKTEST RESULTS"
    )

    report_lines.append(
        "=" * 62
    )

    report_lines.append(
        f"Initial Capital : "
        f"${stats['initial_capital']:.2f}"
    )

    report_lines.append(
        f"Final Capital   : "
        f"${stats['final_capital']:.2f}"
    )

    report_lines.append(
        f"Net Return      : "
        f"{stats['net_return']:.3f}%"
    )

    report_lines.append(
        f"Trades          : "
        f"{stats['trades']}"
    )

    report_lines.append(
        f"Wins            : "
        f"{stats['wins']}"
    )

    report_lines.append(
        f"Losses          : "
        f"{stats['losses']}"
    )

    report_lines.append(
        f"Win Rate        : "
        f"{stats['win_rate']:.2f}%"
    )

    report_lines.append(
        f"Profit Factor   : "
        f"{format_pf(stats['profit_factor'])}"
    )

    report_lines.append(
        f"Gross Profit    : "
        f"${stats['gross_profit']:.4f}"
    )

    report_lines.append(
        f"Gross Loss      : "
        f"${stats['gross_loss']:.4f}"
    )

    report_lines.append(
        f"Avg Trade       : "
        f"{stats['avg_trade']:.4f}%"
    )

    report_lines.append(
        f"Max Drawdown    : "
        f"{stats['max_drawdown']:.3f}%"
    )

    report_lines.append("")
    report_lines.append("LONG")

    report_lines.append(
        f"  Trades : "
        f"{long_stats['trades']}"
    )

    report_lines.append(
        f"  WR     : "
        f"{long_stats['wr']:.2f}%"
    )

    report_lines.append(
        f"  PnL    : "
        f"${long_stats['pnl']:.4f}"
    )

    report_lines.append(
        f"  PF     : "
        f"{format_pf(long_stats['pf'])}"
    )

    report_lines.append("")
    report_lines.append("SHORT")

    report_lines.append(
        f"  Trades : "
        f"{short_stats['trades']}"
    )

    report_lines.append(
        f"  WR     : "
        f"{short_stats['wr']:.2f}%"
    )

    report_lines.append(
        f"  PnL    : "
        f"${short_stats['pnl']:.4f}"
    )

    report_lines.append(
        f"  PF     : "
        f"{format_pf(short_stats['pf'])}"
    )

    report_lines.append("")
    report_lines.append(
        "=" * 62
    )

    report_lines.append(
        "DATA VALIDATION"
    )

    report_lines.append(
        "=" * 62
    )

    report_lines.append(
        f"1H candles  : {len(df1h):,}"
    )

    report_lines.append(
        f"15M candles : {len(df15):,}"
    )

    report_lines.append(
        f"5M candles  : {len(df5):,}"
    )

    report_lines.append(
        f"Test 5M     : {len(test_df):,}"
    )

    report_lines.append("")
    report_lines.append(
        "REAL TRADING: DISABLED"
    )

    report_lines.append(
        "Closed candles only: YES"
    )

    report_lines.append(
        "Higher-TF incomplete candles: NOT USED"
    )

    report_lines.append(
        "Look-ahead protection: YES"
    )

    report_lines.append(
        "NDS type: Algorithmic / Inspired"
    )

    report_lines.append(
        "=" * 62
    )

    report = "\n".join(
        report_lines
    )

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8",
    ) as f:

        f.write(report)

    # ========================================================
    # PRINT RESULTS
    # ========================================================

    print()
    print(report)

    print()
    print("=" * 62)
    print("OUTPUT FILES")
    print("=" * 62)

    print(
        TRADES_FILE
    )

    print(
        EQUITY_FILE
    )

    print(
        REPORT_FILE
    )

    print()
    print("BACKTEST COMPLETE")


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_backtest()
