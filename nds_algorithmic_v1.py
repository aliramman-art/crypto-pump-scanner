# ============================================================
# KRAKEN FUTURES - NDS ALGORITHMIC v1
# ============================================================
#
# NDS = Nodal Displacement Sequencing
#
# THIS IS AN ALGORITHMIC NDS-INSPIRED IMPLEMENTATION.
# IT IS NOT CLAIMED TO BE THE OFFICIAL / PROPRIETARY
# IMPLEMENTATION OF DR. IRAJ JAFARIAN'S NDS.
#
# CORE IDEA:
#
# 1H  = FRACTAL MARKET STRUCTURE
# 15M = DISPLACEMENT -> RETRACEMENT
# 5M  = 123 FLAG -> BREAKOUT
#
# LONG:
#   Higher TF bullish structure
#   Strong bullish displacement
#   Retracement
#   Flag
#   Breakout above flag
#
# SHORT:
#   Higher TF bearish structure
#   Strong bearish displacement
#   Retracement
#   Flag
#   Breakout below flag
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
# REAL TRADING DISABLED
#
# ============================================================

import time
import math
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta

# ============================================================
# CONFIG
# ============================================================

VERSION = "NDS ALGORITHMIC v1"

SYMBOL = "pf_dogeusd"
DISPLAY_SYMBOL = "DOGE/USDT"

INITIAL_CAPITAL = 100.0

REAL_TRADING = False

# ------------------------------------------------------------
# Kraken Charts API
# ------------------------------------------------------------

BASE_URL = "https://futures.kraken.com/api/charts/v1/trade"

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

# ------------------------------------------------------------
# Backtest
# ------------------------------------------------------------

BACKTEST_DAYS = 365
WARMUP_DAYS = 30

# ------------------------------------------------------------
# Fees
# ------------------------------------------------------------

FEE_PER_SIDE = 0.00060
ROUND_TRIP_FEE = FEE_PER_SIDE * 2

# ------------------------------------------------------------
# Structure
# ------------------------------------------------------------

PIVOT_LEFT = 3
PIVOT_RIGHT = 3

# Higher TF structure
STRUCTURE_LOOKBACK = 80

# 15M displacement
DISPLACEMENT_LOOKBACK = 30

# Minimum displacement relative to ATR
MIN_DISPLACEMENT_ATR = 1.20

# Retracement must give back part of displacement
MIN_RETRACEMENT = 0.25
MAX_RETRACEMENT = 0.75

# Flag
FLAG_LOOKBACK = 12

# Flag must not be too large relative to displacement
MAX_FLAG_TO_DISPLACEMENT = 0.60

# 5M breakout buffer
BREAKOUT_BUFFER_PCT = 0.02

# ------------------------------------------------------------
# Risk management
# ------------------------------------------------------------

ATR_PERIOD = 14

SL_BUFFER_ATR = 0.20

MIN_SL_PCT = 0.25
MAX_SL_PCT = 2.50

MIN_RR = 1.50

MAX_TP_PCT = 8.00

# One active position at a time
MAX_OPEN_TRADES = 1

# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": "NDS-Algorithmic-Backtest/1.0"
    }
)


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    start_ts,
    end_ts
):
    """
    Fetch Kraken Futures chart candles.

    Kraken chart endpoint has a practical candle limit,
    therefore data is fetched in chunks.
    """

    all_rows = []

    cursor = int(start_ts)

    # Approximate number of seconds per candle
    resolution_seconds = {
        "1h": 3600,
        "15m": 900,
        "5m": 300
    }[resolution]

    while cursor < end_ts:

        url = f"{BASE_URL}/{symbol}/{resolution}"

        params = {
            "from": cursor,
            "to": int(end_ts)
        }

        try:
            r = SESSION.get(
                url,
                params=params,
                timeout=30
            )

            r.raise_for_status()

            data = r.json()

        except Exception as e:

            print(
                f"ERROR fetching {resolution}: {e}"
            )

            time.sleep(2)

            continue

        candles = data.get("candles", [])

        if not candles:
            break

        for c in candles:

            try:

                ts = int(
                    c.get("time")
                    or c.get("timestamp")
                    or c.get("ts")
                )

                o = float(c["open"])
                h = float(c["high"])
                l = float(c["low"])
                cl = float(c["close"])

                volume = float(
                    c.get("volume", 0)
                )

                all_rows.append(
                    [
                        ts,
                        o,
                        h,
                        l,
                        cl,
                        volume
                    ]
                )

            except Exception:
                continue

        last_ts = max(
            row[0]
            for row in all_rows
        )

        next_cursor = (
            last_ts
            + resolution_seconds
        )

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        time.sleep(0.05)

    if not all_rows:
        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

    df = pd.DataFrame(
        all_rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df = (
        df
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    return df


# ============================================================
# ATR
# ============================================================

def add_atr(df, period=14):

    df = df.copy()

    prev_close = df["close"].shift(1)

    tr1 = df["high"] - df["low"]

    tr2 = (
        df["high"]
        - prev_close
    ).abs()

    tr3 = (
        df["low"]
        - prev_close
    ).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    df["atr"] = (
        tr
        .rolling(period)
        .mean()
    )

    return df


# ============================================================
# PIVOTS
# ============================================================

def is_pivot_high(
    highs,
    i,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT
):

    if i < left:
        return False

    if i + right >= len(highs):
        return False

    center = highs[i]

    left_values = highs[
        i-left:i
    ]

    right_values = highs[
        i+1:i+right+1
    ]

    return (
        center > left_values.max()
        and
        center >= right_values.max()
    )


def is_pivot_low(
    lows,
    i,
    left=PIVOT_LEFT,
    right=PIVOT_RIGHT
):

    if i < left:
        return False

    if i + right >= len(lows):
        return False

    center = lows[i]

    left_values = lows[
        i-left:i
    ]

    right_values = lows[
        i+1:i+right+1
    ]

    return (
        center < left_values.min()
        and
        center <= right_values.min()
    )


def get_recent_pivots(
    df,
    end_index,
    lookback=80
):

    start = max(
        0,
        end_index - lookback
    )

    highs = []
    lows = []

    for i in range(
        start,
        end_index
    ):

        if is_pivot_high(
            df["high"].values,
            i
        ):

            highs.append(
                (
                    i,
                    float(df.iloc[i]["high"])
                )
            )

        if is_pivot_low(
            df["low"].values,
            i
        ):

            lows.append(
                (
                    i,
                    float(df.iloc[i]["low"])
                )
            )

    return highs, lows


# ============================================================
# 1H FRACTAL STRUCTURE
# ============================================================

def get_market_structure(
    df,
    index
):

    highs, lows = get_recent_pivots(
        df,
        index,
        STRUCTURE_LOOKBACK
    )

    if len(highs) < 2 or len(lows) < 2:

        return "NEUTRAL"

    h1 = highs[-2][1]
    h2 = highs[-1][1]

    l1 = lows[-2][1]
    l2 = lows[-1][1]

    bullish = (
        h2 > h1
        and
        l2 > l1
    )

    bearish = (
        h2 < h1
        and
        l2 < l1
    )

    if bullish:
        return "BULLISH"

    if bearish:
        return "BEARISH"

    return "NEUTRAL"


# ============================================================
# 15M DISPLACEMENT
# ============================================================

def detect_displacement(
    df,
    index,
    direction
):

    if index < DISPLACEMENT_LOOKBACK + 2:
        return None

    current_close = float(
        df.iloc[index]["close"]
    )

    current_atr = float(
        df.iloc[index]["atr"]
    )

    if not np.isfinite(current_atr):
        return None

    lookback_df = df.iloc[
        index - DISPLACEMENT_LOOKBACK:index
    ]

    if direction == "LONG":

        origin = float(
            lookback_df["low"].min()
        )

        displacement = (
            current_close - origin
        )

        if displacement <= 0:
            return None

    else:

        origin = float(
            lookback_df["high"].max()
        )

        displacement = (
            origin - current_close
        )

        if displacement <= 0:
            return None

    displacement_atr = (
        displacement / current_atr
    )

    if displacement_atr < MIN_DISPLACEMENT_ATR:

        return None

    return {
        "origin": origin,
        "current": current_close,
        "displacement": displacement,
        "displacement_atr": displacement_atr
    }


# ============================================================
# RETRACEMENT
# ============================================================

def detect_retracement(
    df,
    index,
    displacement,
    direction
):

    origin = displacement["origin"]

    current = displacement["current"]

    total = displacement["displacement"]

    if total <= 0:
        return None

    if direction == "LONG":

        highest = current

        retracement_low = float(
            df.iloc[
                max(0, index - 12):index + 1
            ]["low"].min()
        )

        retracement = (
            highest - retracement_low
        )

        ratio = (
            retracement / total
        )

        if (
            ratio < MIN_RETRACEMENT
            or
            ratio > MAX_RETRACEMENT
        ):
            return None

        return {
            "retracement_ratio": ratio,
            "retracement_extreme": retracement_low
        }

    else:

        lowest = current

        retracement_high = float(
            df.iloc[
                max(0, index - 12):index + 1
            ]["high"].max()
        )

        retracement = (
            retracement_high - lowest
        )

        ratio = (
            retracement / total
        )

        if (
            ratio < MIN_RETRACEMENT
            or
            ratio > MAX_RETRACEMENT
        ):
            return None

        return {
            "retracement_ratio": ratio,
            "retracement_extreme": retracement_high
        }


# ============================================================
# 5M FLAG
# ============================================================

def detect_flag(
    df,
    index,
    direction
):

    if index < FLAG_LOOKBACK + 2:
        return None

    flag = df.iloc[
        index - FLAG_LOOKBACK:index
    ]

    flag_high = float(
        flag["high"].max()
    )

    flag_low = float(
        flag["low"].min()
    )

    flag_range = (
        flag_high - flag_low
    )

    atr = float(
        df.iloc[index]["atr"]
    )

    if not np.isfinite(atr) or atr <= 0:
        return None

    # We use the displacement visible in the recent move
    recent_high = float(
        df.iloc[
            max(0, index - 30):index
        ]["high"].max()
    )

    recent_low = float(
        df.iloc[
            max(0, index - 30):index
        ]["low"].min()
    )

    displacement_range = (
        recent_high - recent_low
    )

    if displacement_range <= 0:
        return None

    if (
        flag_range
        >
        displacement_range
        * MAX_FLAG_TO_DISPLACEMENT
    ):
        return None

    # A flag should be relatively compressed
    if flag_range > atr * 5.0:
        return None

    return {
        "flag_high": flag_high,
        "flag_low": flag_low,
        "flag_range": flag_range
    }


# ============================================================
# 123 FLAG BREAKOUT
# ============================================================

def check_breakout(
    df,
    index,
    flag,
    direction
):

    current = float(
        df.iloc[index]["close"]
    )

    previous = float(
        df.iloc[index - 1]["close"]
    )

    if direction == "LONG":

        breakout_level = (
            flag["flag_high"]
            *
            (1 + BREAKOUT_BUFFER_PCT / 100)
        )

        if (
            previous <= flag["flag_high"]
            and
            current > breakout_level
        ):
            return True

    else:

        breakout_level = (
            flag["flag_low"]
            *
            (1 - BREAKOUT_BUFFER_PCT / 100)
        )

        if (
            previous >= flag["flag_low"]
            and
            current < breakout_level
        ):
            return True

    return False


# ============================================================
# SETUP DETECTION
# ============================================================

def detect_setup(
    df1h,
    df15,
    df5,
    i5
):

    time5 = df5.iloc[i5]["datetime"]

    # --------------------------------------------------------
    # Map 5M time to latest CLOSED 1H and 15M candles
    # --------------------------------------------------------

    i1h = (
        df1h["datetime"]
        .searchsorted(
            time5,
            side="right"
        )
        - 1
    )

    i15 = (
        df15["datetime"]
        .searchsorted(
            time5,
            side="right"
        )
        - 1
    )

    if i1h < 0 or i15 < 0:
        return None

    # We only use fully closed candles.
    # Current 5M candle itself is closed because the
    # backtest processes completed candles sequentially.

    structure = get_market_structure(
        df1h,
        i1h
    )

    if structure not in (
        "BULLISH",
        "BEARISH"
    ):
        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if structure == "BULLISH":

        direction = "LONG"

        displacement = detect_displacement(
            df15,
            i15,
            direction
        )

        if displacement is None:
            return None

        retracement = detect_retracement(
            df15,
            i15,
            displacement,
            direction
        )

        if retracement is None:
            return None

        flag = detect_flag(
            df5,
            i5,
            direction
        )

        if flag is None:
            return None

        if not check_breakout(
            df5,
            i5,
            flag,
            direction
        ):
            return None

        return {
            "direction": direction,
            "flag_high": flag["flag_high"],
            "flag_low": flag["flag_low"],
            "displacement_atr":
                displacement["displacement_atr"],
            "retracement_ratio":
                retracement["retracement_ratio"]
        }

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if structure == "BEARISH":

        direction = "SHORT"

        displacement = detect_displacement(
            df15,
            i15,
            direction
        )

        if displacement is None:
            return None

        retracement = detect_retracement(
            df15,
            i15,
            displacement,
            direction
        )

        if retracement is None:
            return None

        flag = detect_flag(
            df5,
            i5,
            direction
        )

        if flag is None:
            return None

        if not check_breakout(
            df5,
            i5,
            flag,
            direction
        ):
            return None

        return {
            "direction": direction,
            "flag_high": flag["flag_high"],
            "flag_low": flag["flag_low"],
            "displacement_atr":
                displacement["displacement_atr"],
            "retracement_ratio":
                retracement["retracement_ratio"]
        }

    return None


# ============================================================
# TRADE CREATION
# ============================================================

def create_trade(
    setup,
    df5,
    index,
    capital
):

    direction = setup["direction"]

    entry = float(
        df5.iloc[index]["close"]
    )

    atr = float(
        df5.iloc[index]["atr"]
    )

    if not np.isfinite(atr):
        return None

    if direction == "LONG":

        structural_sl = (
            setup["flag_low"]
            -
            atr * SL_BUFFER_ATR
        )

        sl_distance = (
            entry - structural_sl
        )

        if sl_distance <= 0:
            return None

        stop_loss = structural_sl

        tp_distance = (
            sl_distance * MIN_RR
        )

        take_profit = (
            entry + tp_distance
        )

    else:

        structural_sl = (
            setup["flag_high"]
            +
            atr * SL_BUFFER_ATR
        )

        sl_distance = (
            structural_sl - entry
        )

        if sl_distance <= 0:
            return None

        stop_loss = structural_sl

        tp_distance = (
            sl_distance * MIN_RR
        )

        take_profit = (
            entry - tp_distance
        )

    sl_pct = (
        sl_distance
        / entry
        * 100
    )

    tp_pct = (
        tp_distance
        / entry
        * 100
    )

    if sl_pct < MIN_SL_PCT:
        return None

    if sl_pct > MAX_SL_PCT:
        return None

    if tp_pct > MAX_TP_PCT:
        return None

    return {
        "entry_time":
            df5.iloc[index]["datetime"],

        "direction":
            direction,

        "entry":
            entry,

        "stop_loss":
            stop_loss,

        "take_profit":
            take_profit,

        "capital_before":
            capital,

        "sl_pct":
            sl_pct,

        "tp_pct":
            tp_pct,

        "rr":
            MIN_RR,

        "displacement_atr":
            setup["displacement_atr"],

        "retracement_ratio":
            setup["retracement_ratio"],

        "flag_high":
            setup["flag_high"],

        "flag_low":
            setup["flag_low"]
    }


# ============================================================
# TRADE MANAGEMENT
# ============================================================

def close_trade(
    trade,
    exit_price,
    exit_time,
    reason
):

    direction = trade["direction"]

    entry = trade["entry"]

    if direction == "LONG":

        gross_return = (
            exit_price - entry
        ) / entry

    else:

        gross_return = (
            entry - exit_price
        ) / entry

    net_return = (
        gross_return
        -
        ROUND_TRIP_FEE
    )

    pnl = (
        trade["capital_before"]
        * net_return
    )

    capital_after = (
        trade["capital_before"]
        + pnl
    )

    result = {
        "entry_time":
            trade["entry_time"],

        "exit_time":
            exit_time,

        "direction":
            direction,

        "entry":
            entry,

        "stop_loss":
            trade["stop_loss"],

        "take_profit":
            trade["take_profit"],

        "exit":
            exit_price,

        "return_pct":
            net_return * 100,

        "pnl":
            pnl,

        "capital_after":
            capital_after,

        "exit_reason":
            reason,

        "rr":
            trade["rr"],

        "sl_pct":
            trade["sl_pct"],

        "tp_pct":
            trade["tp_pct"],

        "displacement_atr":
            trade["displacement_atr"],

        "retracement_ratio":
            trade["retracement_ratio"]
    }

    return result


# ============================================================
# BACKTEST
# ============================================================

def run_backtest():

    now = datetime.now(
        timezone.utc
    )

    backtest_start = (
        now
        -
        timedelta(
            days=BACKTEST_DAYS
        )
    )

    data_start = (
        backtest_start
        -
        timedelta(
            days=WARMUP_DAYS
        )
    )

    end_ts = now.timestamp()
    start_ts = data_start.timestamp()

    print()
    print("=" * 64)
    print(VERSION)
    print("=" * 64)

    print(
        f"Backtest start : {backtest_start.isoformat()}"
    )

    print(
        f"Data start     : {data_start.isoformat()}"
    )

    print(
        f"Backtest end   : {now.isoformat()}"
    )

    print()
    print("Downloading Kraken data...")

    df1h = fetch_kraken_candles(
        SYMBOL,
        TF_1H,
        start_ts,
        end_ts
    )

    df15 = fetch_kraken_candles(
        SYMBOL,
        TF_15M,
        start_ts,
        end_ts
    )

    df5 = fetch_kraken_candles(
        SYMBOL,
        TF_5M,
        start_ts,
        end_ts
    )

    if (
        df1h.empty
        or df15.empty
        or df5.empty
    ):

        raise RuntimeError(
            "Kraken data download failed."
        )

    df1h = add_atr(
        df1h,
        ATR_PERIOD
    )

    df15 = add_atr(
        df15,
        ATR_PERIOD
    )

    df5 = add_atr(
        df5,
        ATR_PERIOD
    )

    # --------------------------------------------------------
    # Only candles inside actual test period
    # --------------------------------------------------------

    df5_test = df5[
        df5["datetime"] >=
        pd.Timestamp(
            backtest_start
        )
    ].copy()

    print()
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
        f"Test 5M     : {len(df5_test):,}"
    )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    capital = INITIAL_CAPITAL

    open_trade = None

    trades = []

    equity_rows = []

    last_entry_index = -999999

    for i in range(
        max(
            PIVOT_LEFT + PIVOT_RIGHT + 20,
            100
        ),
        len(df5_test)
    ):

        # Map test index to full df5 index
        current_time = (
            df5_test.iloc[i]["datetime"]
        )

        full_i5 = (
            df5["datetime"]
            .searchsorted(
                current_time
            )
        )

        if full_i5 >= len(df5):
            continue

        candle = df5.iloc[
            full_i5
        ]

        high = float(
            candle["high"]
        )

        low = float(
            candle["low"]
        )

        close = float(
            candle["close"]
        )

        current_time = candle["datetime"]

        # ----------------------------------------------------
        # Manage existing trade
        # ----------------------------------------------------

        if open_trade is not None:

            direction = (
                open_trade["direction"]
            )

            if direction == "LONG":

                sl_hit = (
                    low
                    <=
                    open_trade["stop_loss"]
                )

                tp_hit = (
                    high
                    >=
                    open_trade["take_profit"]
                )

                # Conservative assumption:
                # if both happen in same candle,
                # SL is counted first.
                if sl_hit:

                    result = close_trade(
                        open_trade,
                        open_trade["stop_loss"],
                        current_time,
                        "SL"
                    )

                    trades.append(result)

                    capital = result[
                        "capital_after"
                    ]

                    open_trade = None

                elif tp_hit:

                    result = close_trade(
                        open_trade,
                        open_trade["take_profit"],
                        current_time,
                        "TP"
                    )

                    trades.append(result)

                    capital = result[
                        "capital_after"
                    ]

                    open_trade = None

            else:

                sl_hit = (
                    high
                    >=
                    open_trade["stop_loss"]
                )

                tp_hit = (
                    low
                    <=
                    open_trade["take_profit"]
                )

                if sl_hit:

                    result = close_trade(
                        open_trade,
                        open_trade["stop_loss"],
                        current_time,
                        "SL"
                    )

                    trades.append(result)

                    capital = result[
                        "capital_after"
                    ]

                    open_trade = None

                elif tp_hit:

                    result = close_trade(
                        open_trade,
                        open_trade["take_profit"],
                        current_time,
                        "TP"
                    )

                    trades.append(result)

                    capital = result[
                        "capital_after"
                    ]

                    open_trade = None

        # ----------------------------------------------------
        # Search new setup only if flat
        # ----------------------------------------------------

        if open_trade is None:

            # Prevent immediate repeated entry
            if (
                full_i5
                <=
                last_entry_index + 1
            ):
                continue

            setup = detect_setup(
                df1h,
                df15,
                df5,
                full_i5
            )

            if setup is not None:

                new_trade = create_trade(
                    setup,
                    df5,
                    full_i5,
                    capital
                )

                if new_trade is not None:

                    open_trade = new_trade

                    last_entry_index = (
                        full_i5
                    )

        equity_rows.append(
            {
                "time": current_time,
                "capital": capital
            }
        )

    # --------------------------------------------------------
    # Close open trade at end
    # --------------------------------------------------------

    if open_trade is not None:

        final_candle = df5.iloc[-1]

        result = close_trade(
            open_trade,
            float(
                final_candle["close"]
            ),
            final_candle["datetime"],
            "END_OF_BACKTEST"
        )

        trades.append(result)

        capital = result[
            "capital_after"
        ]

        open_trade = None

    trades_df = pd.DataFrame(
        trades
    )

    equity_df = pd.DataFrame(
        equity_rows
    )

    return (
        trades_df,
        equity_df,
        capital
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    trades_df,
    final_capital
):

    if trades_df.empty:

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "wr": 0,
            "pf": 0,
            "net_return": 0,
            "avg_trade": 0,
            "max_dd": 0
        }

    wins = trades_df[
        trades_df["pnl"] > 0
    ]

    losses = trades_df[
        trades_df["pnl"] < 0
    ]

    gross_profit = wins[
        "pnl"
    ].sum()

    gross_loss = abs(
        losses["pnl"].sum()
    )

    if gross_loss > 0:

        pf = (
            gross_profit
            /
            gross_loss
        )

    else:

        pf = float("inf")

    capital_series = (
        trades_df[
            "capital_after"
        ]
        .astype(float)
    )

    running_max = (
        capital_series
        .cummax()
    )

    drawdown = (
        capital_series
        /
        running_max
        - 1
    )

    max_dd = (
        drawdown.min()
        * 100
    )

    stats = {

        "trades":
            len(trades_df),

        "wins":
            len(wins),

        "losses":
            len(losses),

        "wr":
            len(wins)
            /
            len(trades_df)
            * 100,

        "pf":
            pf,

        "gross_profit":
            gross_profit,

        "gross_loss":
            gross_loss,

        "net_pnl":
            final_capital
            -
            INITIAL_CAPITAL,

        "net_return":
            (
                final_capital
                /
                INITIAL_CAPITAL
                - 1
            )
            * 100,

        "avg_trade":
            trades_df[
                "return_pct"
            ].mean(),

        "max_dd":
            max_dd
    }

    return stats


# ============================================================
# REPORT
# ============================================================

def print_report(
    trades_df,
    final_capital
):

    stats = calculate_stats(
        trades_df,
        final_capital
    )

    print()
    print("=" * 64)
    print("NDS ALGORITHMIC v1 BACKTEST RESULTS")
    print("=" * 64)

    print(
        f"Initial Capital : "
        f"${INITIAL_CAPITAL:.2f}"
    )

    print(
        f"Final Capital   : "
        f"${final_capital:.2f}"
    )

    print(
        f"Net Return      : "
        f"{stats['net_return']:.3f}%"
    )

    print(
        f"Trades          : "
        f"{stats['trades']}"
    )

    print(
        f"Wins            : "
        f"{stats['wins']}"
    )

    print(
        f"Losses          : "
        f"{stats['losses']}"
    )

    print(
        f"Win Rate        : "
        f"{stats['wr']:.2f}%"
    )

    print(
        f"Profit Factor   : "
        f"{stats['pf']:.3f}"
    )

    print(
        f"Gross Profit    : "
        f"${stats['gross_profit']:.4f}"
    )

    print(
        f"Gross Loss      : "
        f"${stats['gross_loss']:.4f}"
    )

    print(
        f"Avg Trade       : "
        f"{stats['avg_trade']:.4f}%"
    )

    print(
        f"Max Drawdown    : "
        f"{stats['max_dd']:.3f}%"
    )

    # --------------------------------------------------------
    # Direction stats
    # --------------------------------------------------------

    for direction in [
        "LONG",
        "SHORT"
    ]:

        d = trades_df[
            trades_df["direction"]
            == direction
        ]

        if d.empty:
            continue

        wins = d[
            d["pnl"] > 0
        ]

        gross_profit = wins[
            "pnl"
        ].sum()

        gross_loss = abs(
            d[
                d["pnl"] < 0
            ]["pnl"].sum()
        )

        pf = (
            gross_profit
            /
            gross_loss
            if gross_loss > 0
            else float("inf")
        )

        print()
        print(
            f"{direction}"
        )

        print(
            f"  Trades : {len(d)}"
        )

        print(
            f"  WR     : "
            f"{len(wins)/len(d)*100:.2f}%"
        )

        print(
            f"  PnL    : "
            f"${d['pnl'].sum():.4f}"
        )

        print(
            f"  PF     : "
            f"{pf:.3f}"
        )

    print()
    print("=" * 64)


# ============================================================
# SAVE OUTPUTS
# ============================================================

def save_outputs(
    trades_df,
    equity_df
):

    trades_file = (
        "doge_nds_algorithmic_v1_trades.csv"
    )

    equity_file = (
        "doge_nds_algorithmic_v1_equity.csv"
    )

    report_file = (
        "doge_nds_algorithmic_v1_report.txt"
    )

    trades_df.to_csv(
        trades_file,
        index=False
    )

    equity_df.to_csv(
        equity_file,
        index=False
    )

    with open(
        report_file,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            f"{VERSION}\n"
        )

        f.write(
            "=" * 64
            + "\n"
        )

        f.write(
            "NDS-inspired algorithmic "
            "implementation.\n"
        )

        f.write(
            "Not claimed to be the official "
            "proprietary NDS implementation.\n\n"
        )

        if trades_df.empty:

            f.write(
                "No trades.\n"
            )

        else:

            stats = calculate_stats(
                trades_df,
                float(
                    trades_df.iloc[-1][
                        "capital_after"
                    ]
                )
            )

            for key, value in stats.items():

                f.write(
                    f"{key}: {value}\n"
                )

    print()
    print("OUTPUT FILES")
    print("=" * 64)
    print(trades_file)
    print(equity_file)
    print(report_file)


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":

    print()
    print("=" * 64)
    print(VERSION)
    print("=" * 64)
    print(
        "REAL TRADING: DISABLED"
    )

    trades_df, equity_df, final_capital = (
        run_backtest()
    )

    print_report(
        trades_df,
        final_capital
    )

    save_outputs(
        trades_df,
        equity_df
    )

    print()
    print(
        "BACKTEST COMPLETE"
    )
