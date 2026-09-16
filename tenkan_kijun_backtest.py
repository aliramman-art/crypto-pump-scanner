# ============================================================
# TENKAN + KIJUN + DISTANCE BACKTEST
# Kraken Futures
# ============================================================
#
# STRATEGY
#
# 1H:
#   Tenkan > Kijun  -> LONG bias
#   Tenkan < Kijun  -> SHORT bias
#
# 15M:
#   Tenkan/Kijun distance >= minimum
#   Distance must be expanding
#
# 5M:
#   Price pulls back to Tenkan
#   Candle closes back in trend direction
#
# ENTRY:
#   Close of confirmation candle
#
# SL:
#   Kijun or recent swing
#
# TP:
#   Fixed RR
#
# CLOSED CANDLES ONLY
#
# If SL and TP are both touched on the same candle,
# SL is assumed first.
#
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "PF_DOGEUSD"

DAYS = 30

# ------------------------------------------------------------
# ICHIMOKU
# ------------------------------------------------------------

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

# ------------------------------------------------------------
# 15M DISTANCE
# ------------------------------------------------------------

MIN_DISTANCE_PCT = 0.20

# Current distance must be greater than previous distance
MIN_DISTANCE_EXPANSION_PCT = 0.00

# ------------------------------------------------------------
# 5M ENTRY
# ------------------------------------------------------------

MAX_TENKAN_DISTANCE_PCT = 0.25

MIN_BODY_RATIO = 0.20

# ------------------------------------------------------------
# SL
# ------------------------------------------------------------

SWING_LOOKBACK = 6

MIN_SL_PCT = 0.20
MAX_SL_PCT = 1.50

SL_BUFFER_PCT = 0.05

# ------------------------------------------------------------
# TP
# ------------------------------------------------------------

MIN_RR = 1.50

# ------------------------------------------------------------
# TRADE
# ------------------------------------------------------------

START_BALANCE = 1000.0

RISK_PER_TRADE_PCT = 1.0

MAX_HOLD_BARS = 288
# 288 x 5M = 24 hours

COOLDOWN_BARS = 1

ALLOW_LONG = True
ALLOW_SHORT = True


# ============================================================
# KRAKEN FUTURES CHARTS API
# ============================================================

BASE_URL = (
    "https://futures.kraken.com"
    "/api/charts/v1"
)

TICK_TYPE = "trade"


# ============================================================
# RESOLUTION MAP
# ============================================================

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
# FETCH KRAKEN CANDLES
# ============================================================

def fetch_kraken_ohlc(
    symbol,
    resolution,
    days
):

    if resolution not in RESOLUTION_SECONDS:
        raise ValueError(
            f"Unsupported resolution: {resolution}"
        )

    step = RESOLUTION_SECONDS[
        resolution
    ]

    now = int(time.time())

    start = (
        now
        -
        days * 86400
    )

    print(
        f"\nDownloading "
        f"{symbol} {resolution}..."
    )

    rows = []

    # --------------------------------------------------------
    # Kraken chart endpoint supports from/to/count.
    # We use chunks to avoid requesting an excessively large
    # time range in one call.
    # --------------------------------------------------------

    chunk_seconds = (
        step * 5000
    )

    current = start

    session = requests.Session()

    session.headers.update({
        "Accept": "application/json",
        "User-Agent":
            "Tenkan-Kijun-Backtest/1.0"
    })

    while current < now:

        chunk_end = min(
            current + chunk_seconds,
            now
        )

        url = (
            f"{BASE_URL}/"
            f"{TICK_TYPE}/"
            f"{symbol}/"
            f"{resolution}"
        )

        params = {
            "from": current,
            "to": chunk_end,
        }

        try:

            response = session.get(
                url,
                params=params,
                timeout=30
            )

        except requests.RequestException as e:

            print(
                "Network error:",
                e
            )

            time.sleep(5)

            continue

        # ----------------------------------------------------
        # IMPORTANT:
        # Do NOT retry 404 forever.
        # ----------------------------------------------------

        if response.status_code == 404:

            print(
                "\nERROR 404:"
            )

            print(
                "Kraken did not find "
                f"symbol/resolution: "
                f"{symbol} / {resolution}"
            )

            print(
                "URL:",
                response.url
            )

            raise RuntimeError(
                "Kraken Charts API returned 404. "
                "Check the Futures symbol."
            )

        if response.status_code != 200:

            print(
                f"HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )

            raise RuntimeError(
                "Kraken Charts API request failed."
            )

        try:

            data = response.json()

        except Exception:

            print(
                "Invalid JSON response:"
            )

            print(
                response.text[:500]
            )

            raise

        candles = data.get(
            "candles",
            []
        )

        if not candles:

            # No candles in this chunk.
            # Move forward instead of looping.
            current = (
                chunk_end + step
            )

            continue

        rows.extend(
            candles
        )

        # ----------------------------------------------------
        # Move to next chunk.
        # ----------------------------------------------------

        current = (
            chunk_end + step
        )

        print(
            f"  received "
            f"{len(candles)} candles"
        )

        time.sleep(0.15)

    if not rows:

        raise RuntimeError(
            f"No candles returned "
            f"for {symbol} {resolution}"
        )

    # ========================================================
    # BUILD DATAFRAME
    # ========================================================

    df = pd.DataFrame(rows)

    required = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    missing = [
        x for x in required
        if x not in df.columns
    ]

    if missing:

        raise RuntimeError(
            "Kraken response is missing "
            f"columns: {missing}"
        )

    df = df[
        required
    ].copy()

    # Kraken returns epoch milliseconds
    df["time"] = pd.to_numeric(
        df["time"],
        errors="coerce"
    )

    df["datetime"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna()

    df = df.sort_values(
        "datetime"
    )

    df = df.drop_duplicates(
        subset=["datetime"]
    )

    df = df.reset_index(
        drop=True
    )

    # Keep only requested period
    cutoff = pd.Timestamp.now(
        tz="UTC"
    ) - pd.Timedelta(
        days=days
    )

    df = df[
        df["datetime"] >= cutoff
    ].copy()

    df = df.reset_index(
        drop=True
    )

    print(
        f"Final {resolution} candles: "
        f"{len(df)}"
    )

    if not df.empty:

        print(
            f"Range: "
            f"{df['datetime'].iloc[0]} "
            f"-> "
            f"{df['datetime'].iloc[-1]}"
        )

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def add_ichimoku(df):

    df = df.copy()

    df["tenkan"] = (
        df["high"]
        .rolling(
            TENKAN_PERIOD
        )
        .max()
        +
        df["low"]
        .rolling(
            TENKAN_PERIOD
        )
        .min()
    ) / 2

    df["kijun"] = (
        df["high"]
        .rolling(
            KIJUN_PERIOD
        )
        .max()
        +
        df["low"]
        .rolling(
            KIJUN_PERIOD
        )
        .min()
    ) / 2

    df["tk_distance_pct"] = (
        abs(
            df["tenkan"]
            -
            df["kijun"]
        )
        /
        df["kijun"].replace(
            0,
            np.nan
        )
        *
        100
    )

    df["tk_distance_prev"] = (
        df["tk_distance_pct"]
        .shift(1)
    )

    df["tk_distance_expansion"] = (
        df["tk_distance_pct"]
        -
        df["tk_distance_prev"]
    )

    return df


# ============================================================
# PREPARE MULTI TIMEFRAME DATA
# ============================================================

def prepare_data(
    df_1h,
    df_15m,
    df_5m
):

    df_1h = add_ichimoku(
        df_1h
    )

    df_15m = add_ichimoku(
        df_15m
    )

    df_5m = add_ichimoku(
        df_5m
    )

    # --------------------------------------------------------
    # IMPORTANT:
    # Shift higher timeframe data by one candle before merge.
    #
    # This prevents using a higher-TF candle that is still
    # forming when the 5M candle is being evaluated.
    # --------------------------------------------------------

    h1 = df_1h[
        [
            "datetime",
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion",
        ]
    ].copy()

    h1[
        [
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion",
        ]
    ] = h1[
        [
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion",
        ]
    ].shift(1)

    h1.columns = [
        "datetime",
        "h1_tenkan",
        "h1_kijun",
        "h1_distance",
        "h1_expansion",
    ]

    m15 = df_15m[
        [
            "datetime",
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion",
        ]
    ].copy()

    m15[
        [
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion",
        ]
    ] = m15[
        [
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion",
        ]
    ].shift(1)

    m15.columns = [
        "datetime",
        "m15_tenkan",
        "m15_kijun",
        "m15_distance",
        "m15_expansion",
    ]

    m5 = df_5m.copy()

    # --------------------------------------------------------
    # Merge 1H
    # --------------------------------------------------------

    result = pd.merge_asof(
        m5.sort_values(
            "datetime"
        ),
        h1.sort_values(
            "datetime"
        ),
        on="datetime",
        direction="backward",
    )

    # --------------------------------------------------------
    # Merge 15M
    # --------------------------------------------------------

    result = pd.merge_asof(
        result.sort_values(
            "datetime"
        ),
        m15.sort_values(
            "datetime"
        ),
        on="datetime",
        direction="backward",
    )

    result = result.reset_index(
        drop=True
    )

    return result


# ============================================================
# BODY RATIO
# ============================================================

def body_ratio(row):

    candle_range = (
        row["high"]
        -
        row["low"]
    )

    if candle_range <= 0:

        return 0.0

    body = abs(
        row["close"]
        -
        row["open"]
    )

    return (
        body
        /
        candle_range
    )


# ============================================================
# LONG CONFIRMATION
# ============================================================

def is_long_confirmation(
    row
):

    if row["close"] <= row["open"]:

        return False

    return (
        body_ratio(row)
        >=
        MIN_BODY_RATIO
    )


# ============================================================
# SHORT CONFIRMATION
# ============================================================

def is_short_confirmation(
    row
):

    if row["close"] >= row["open"]:

        return False

    return (
        body_ratio(row)
        >=
        MIN_BODY_RATIO
    )


# ============================================================
# LONG SIGNAL
# ============================================================

def long_signal(
    df,
    i
):

    row = df.iloc[i]

    required = [
        "h1_tenkan",
        "h1_kijun",
        "m15_distance",
        "m15_expansion",
        "tenkan",
    ]

    for col in required:

        if pd.isna(
            row[col]
        ):

            return False

    # --------------------------------------------------------
    # 1H DIRECTION
    # --------------------------------------------------------

    if (
        row["h1_tenkan"]
        <=
        row["h1_kijun"]
    ):

        return False

    # --------------------------------------------------------
    # 15M DISTANCE
    # --------------------------------------------------------

    if (
        row["m15_distance"]
        <
        MIN_DISTANCE_PCT
    ):

        return False

    # --------------------------------------------------------
    # DISTANCE EXPANSION
    # --------------------------------------------------------

    if (
        row["m15_expansion"]
        <
        MIN_DISTANCE_EXPANSION_PCT
    ):

        return False

    # --------------------------------------------------------
    # 5M TENKAN
    # --------------------------------------------------------

    tenkan = row["tenkan"]

    if tenkan <= 0:

        return False

    distance = (
        abs(
            row["close"]
            -
            tenkan
        )
        /
        tenkan
        *
        100
    )

    if (
        distance
        >
        MAX_TENKAN_DISTANCE_PCT
    ):

        return False

    # Price closes above Tenkan
    if (
        row["close"]
        <=
        tenkan
    ):

        return False

    # Confirmation candle
    if not is_long_confirmation(
        row
    ):

        return False

    return True


# ============================================================
# SHORT SIGNAL
# ============================================================

def short_signal(
    df,
    i
):

    row = df.iloc[i]

    required = [
        "h1_tenkan",
        "h1_kijun",
        "m15_distance",
        "m15_expansion",
        "tenkan",
    ]

    for col in required:

        if pd.isna(
            row[col]
        ):

            return False

    # --------------------------------------------------------
    # 1H DIRECTION
    # --------------------------------------------------------

    if (
        row["h1_tenkan"]
        >=
        row["h1_kijun"]
    ):

        return False

    # --------------------------------------------------------
    # 15M DISTANCE
    # --------------------------------------------------------

    if (
        row["m15_distance"]
        <
        MIN_DISTANCE_PCT
    ):

        return False

    # --------------------------------------------------------
    # DISTANCE EXPANSION
    # --------------------------------------------------------

    if (
        row["m15_expansion"]
        <
        MIN_DISTANCE_EXPANSION_PCT
    ):

        return False

    # --------------------------------------------------------
    # 5M TENKAN
    # --------------------------------------------------------

    tenkan = row["tenkan"]

    if tenkan <= 0:

        return False

    distance = (
        abs(
            row["close"]
            -
            tenkan
        )
        /
        tenkan
        *
        100
    )

    if (
        distance
        >
        MAX_TENKAN_DISTANCE_PCT
    ):

        return False

    # Price closes below Tenkan
    if (
        row["close"]
        >=
        tenkan
    ):

        return False

    # Confirmation candle
    if not is_short_confirmation(
        row
    ):

        return False

    return True


# ============================================================
# LONG SL
# ============================================================

def calculate_long_sl(
    df,
    i
):

    row = df.iloc[i]

    start = max(
        0,
        i - SWING_LOOKBACK
    )

    recent_low = (
        df.iloc[
            start:i + 1
        ]["low"]
        .min()
    )

    candidates = []

    if pd.notna(
        row["kijun"]
    ):

        candidates.append(
            row["kijun"]
        )

    candidates.append(
        recent_low
    )

    valid = [
        x
        for x in candidates
        if x < row["close"]
    ]

    if not valid:

        return None

    sl = max(valid)

    sl *= (
        1
        -
        SL_BUFFER_PCT / 100
    )

    return sl


# ============================================================
# SHORT SL
# ============================================================

def calculate_short_sl(
    df,
    i
):

    row = df.iloc[i]

    start = max(
        0,
        i - SWING_LOOKBACK
    )

    recent_high = (
        df.iloc[
            start:i + 1
        ]["high"]
        .max()
    )

    candidates = []

    if pd.notna(
        row["kijun"]
    ):

        candidates.append(
            row["kijun"]
        )

    candidates.append(
        recent_high
    )

    valid = [
        x
        for x in candidates
        if x > row["close"]
    ]

    if not valid:

        return None

    sl = min(valid)

    sl *= (
        1
        +
        SL_BUFFER_PCT / 100
    )

    return sl


# ============================================================
# SIMULATE TRADE
# ============================================================

def simulate_trade(
    df,
    entry_index,
    direction,
    entry,
    sl,
    tp
):

    max_index = min(
        len(df) - 1,
        entry_index
        +
        MAX_HOLD_BARS
    )

    for j in range(
        entry_index + 1,
        max_index + 1
    ):

        row = df.iloc[j]

        high = row["high"]
        low = row["low"]

        if direction == "LONG":

            hit_sl = (
                low <= sl
            )

            hit_tp = (
                high >= tp
            )

            # Conservative assumption
            if hit_sl:

                return {
                    "exit_index": j,
                    "exit_time":
                        row["datetime"],
                    "exit_price": sl,
                    "result": "LOSS",
                }

            if hit_tp:

                return {
                    "exit_index": j,
                    "exit_time":
                        row["datetime"],
                    "exit_price": tp,
                    "result": "WIN",
                }

        else:

            hit_sl = (
                high >= sl
            )

            hit_tp = (
                low <= tp
            )

            # Conservative assumption
            if hit_sl:

                return {
                    "exit_index": j,
                    "exit_time":
                        row["datetime"],
                    "exit_price": sl,
                    "result": "LOSS",
                }

            if hit_tp:

                return {
                    "exit_index": j,
                    "exit_time":
                        row["datetime"],
                    "exit_price": tp,
                    "result": "WIN",
                }

    # --------------------------------------------------------
    # TIME EXIT
    # --------------------------------------------------------

    last_row = df.iloc[
        max_index
    ]

    return {
        "exit_index":
            max_index,

        "exit_time":
            last_row["datetime"],

        "exit_price":
            last_row["close"],

        "result":
            "TIME",
    }


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df):

    balance = START_BALANCE

    equity_curve = [
        balance
    ]

    trades = []

    i = 1

    cooldown_until = -1

    while (
        i <
        len(df) - 1
    ):

        if (
            i
            <=
            cooldown_until
        ):

            i += 1
            continue

        row = df.iloc[i]

        direction = None

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        if (
            ALLOW_LONG
            and
            long_signal(
                df,
                i
            )
        ):

            direction = "LONG"

        elif (
            ALLOW_SHORT
            and
            short_signal(
                df,
                i
            )
        ):

            direction = "SHORT"

        if direction is None:

            i += 1
            continue

        entry = row["close"]

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        if direction == "LONG":

            sl = calculate_long_sl(
                df,
                i
            )

        else:

            sl = calculate_short_sl(
                df,
                i
            )

        if sl is None:

            i += 1
            continue

        # ----------------------------------------------------
        # SL DISTANCE
        # ----------------------------------------------------

        sl_distance_pct = (
            abs(
                entry - sl
            )
            /
            entry
            *
            100
        )

        if (
            sl_distance_pct
            <
            MIN_SL_PCT
        ):

            i += 1
            continue

        if (
            sl_distance_pct
            >
            MAX_SL_PCT
        ):

            i += 1
            continue

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        risk = abs(
            entry - sl
        )

        if direction == "LONG":

            tp = (
                entry
                +
                risk * MIN_RR
            )

        else:

            tp = (
                entry
                -
                risk * MIN_RR
            )

        tp_distance_pct = (
            abs(
                tp - entry
            )
            /
            entry
            *
            100
        )

        # ----------------------------------------------------
        # SIMULATE
        # ----------------------------------------------------

        outcome = simulate_trade(
            df,
            i,
            direction,
            entry,
            sl,
            tp
        )

        exit_price = (
            outcome["exit_price"]
        )

        # ----------------------------------------------------
        # PNL
        # ----------------------------------------------------

        if direction == "LONG":

            pnl_pct = (
                exit_price
                -
                entry
            ) / entry * 100

            risk_price = (
                entry - sl
            )

            reward_price = (
                exit_price - entry
            )

        else:

            pnl_pct = (
                entry
                -
                exit_price
            ) / entry * 100

            risk_price = (
                sl - entry
            )

            reward_price = (
                entry - exit_price
            )

        if risk_price > 0:

            r_multiple = (
                reward_price
                /
                risk_price
            )

        else:

            r_multiple = 0.0

        # ----------------------------------------------------
        # ACCOUNT PNL
        # ----------------------------------------------------

        account_pnl = (
            balance
            *
            (
                RISK_PER_TRADE_PCT
                /
                100
            )
            *
            r_multiple
        )

        balance += account_pnl

        equity_curve.append(
            balance
        )

        # ----------------------------------------------------
        # SAVE TRADE
        # ----------------------------------------------------

        trades.append({

            "entry_time":
                row["datetime"],

            "exit_time":
                outcome["exit_time"],

            "direction":
                direction,

            "entry":
                entry,

            "sl":
                sl,

            "tp":
                tp,

            "exit":
                exit_price,

            "sl_pct":
                sl_distance_pct,

            "tp_pct":
                tp_distance_pct,

            "rr":
                MIN_RR,

            "result":
                outcome["result"],

            "pnl_pct":
                pnl_pct,

            "R":
                r_multiple,

            "account_pnl":
                account_pnl,

            "balance":
                balance,

        })

        # ----------------------------------------------------
        # MOVE TO AFTER EXIT
        # ----------------------------------------------------

        cooldown_until = (
            outcome["exit_index"]
            +
            COOLDOWN_BARS
        )

        i = (
            outcome["exit_index"]
            +
            1
        )

    return (
        pd.DataFrame(trades),
        equity_curve
    )


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    trades,
    equity_curve
):

    print("\n")
    print("=" * 70)
    print(
        "TENKAN + KIJUN + DISTANCE "
        "BACKTEST RESULTS"
    )
    print("=" * 70)

    if trades.empty:

        print(
            "\nNO TRADES FOUND."
        )

        print(
            "The strategy produced "
            "zero valid entries."
        )

        return

    total = len(trades)

    wins = len(
        trades[
            trades["result"]
            ==
            "WIN"
        ]
    )

    losses = len(
        trades[
            trades["result"]
            ==
            "LOSS"
        ]
    )

    time_exits = len(
        trades[
            trades["result"]
            ==
            "TIME"
        ]
    )

    win_rate = (
        wins
        /
        total
        *
        100
    )

    gross_profit = trades.loc[
        trades["account_pnl"] > 0,
        "account_pnl"
    ].sum()

    gross_loss = abs(
        trades.loc[
            trades["account_pnl"] < 0,
            "account_pnl"
        ].sum()
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = np.inf

    final_balance = (
        equity_curve[-1]
    )

    net_pnl = (
        final_balance
        -
        START_BALANCE
    )

    net_pnl_pct = (
        net_pnl
        /
        START_BALANCE
        *
        100
    )

    # --------------------------------------------------------
    # DRAWDOWN
    # --------------------------------------------------------

    equity = np.array(
        equity_curve,
        dtype=float
    )

    peaks = np.maximum.accumulate(
        equity
    )

    drawdowns = (
        equity
        -
        peaks
    ) / peaks * 100

    max_drawdown = (
        drawdowns.min()
    )

    average_r = (
        trades["R"].mean()
    )

    print(
        f"Symbol              : "
        f"{SYMBOL}"
    )

    print(
        f"Period              : "
        f"{DAYS} days"
    )

    print(
        f"Initial Balance     : "
        f"${START_BALANCE:.2f}"
    )

    print(
        f"Final Balance       : "
        f"${final_balance:.2f}"
    )

    print("-" * 70)

    print(
        f"Trades              : "
        f"{total}"
    )

    print(
        f"Wins                : "
        f"{wins}"
    )

    print(
        f"Losses              : "
        f"{losses}"
    )

    print(
        f"Time Exits          : "
        f"{time_exits}"
    )

    print(
        f"Win Rate            : "
        f"{win_rate:.2f}%"
    )

    print(
        f"Profit Factor       : "
        f"{profit_factor:.3f}"
    )

    print(
        f"Net PnL             : "
        f"{net_pnl_pct:+.2f}%"
    )

    print(
        f"Max Drawdown        : "
        f"{max_drawdown:.2f}%"
    )

    print(
        f"Average R           : "
        f"{average_r:.3f}"
    )

    print("=" * 70)

    # --------------------------------------------------------
    # LONG / SHORT
    # --------------------------------------------------------

    print(
        "\nDIRECTION STATISTICS"
    )

    print("-" * 70)

    for direction in [
        "LONG",
        "SHORT"
    ]:

        d = trades[
            trades["direction"]
            ==
            direction
        ]

        if d.empty:

            print(
                f"{direction}: "
                f"0 trades"
            )

            continue

        dw = len(
            d[
                d["result"]
                ==
                "WIN"
            ]
        )

        dwr = (
            dw
            /
            len(d)
            *
            100
        )

        dpnl = d[
            "account_pnl"
        ].sum()

        print(
            f"{direction:<8} "
            f"Trades={len(d):<5} "
            f"WR={dwr:>6.2f}% "
            f"PnL=${dpnl:+.2f}"
        )

    print("=" * 70)

    # --------------------------------------------------------
    # LAST 20
    # --------------------------------------------------------

    print(
        "\nLAST 20 TRADES"
    )

    print("-" * 70)

    cols = [
        "entry_time",
        "direction",
        "entry",
        "sl",
        "tp",
        "exit",
        "result",
        "pnl_pct",
        "R",
    ]

    print(
        trades[
            cols
        ].tail(20).to_string(
            index=False
        )
    )

    print("=" * 70)


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(
    trades
):

    if trades.empty:

        print(
            "\nNo trade CSV created."
        )

        return

    filename = (
        "tenkan_kijun_backtest.csv"
    )

    trades.to_csv(
        filename,
        index=False
    )

    print(
        f"\nTrade history saved: "
        f"{filename}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print(
        "TENKAN + KIJUN + DISTANCE "
        "BACKTEST"
    )
    print("=" * 70)

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Days: {DAYS}"
    )

    print(
        "Data: Kraken Futures Charts API"
    )

    print(
        "1H: Tenkan/Kijun direction"
    )

    print(
        f"15M minimum distance: "
        f"{MIN_DISTANCE_PCT:.2f}%"
    )

    print(
        f"15M minimum expansion: "
        f"{MIN_DISTANCE_EXPANSION_PCT:.2f}%"
    )

    print(
        f"5M maximum Tenkan distance: "
        f"{MAX_TENKAN_DISTANCE_PCT:.2f}%"
    )

    print(
        f"Minimum RR: "
        f"{MIN_RR:.2f}"
    )

    print("=" * 70)

    # ========================================================
    # DOWNLOAD
    # ========================================================

    df_1h = fetch_kraken_ohlc(
        SYMBOL,
        "1h",
        DAYS
    )

    df_15m = fetch_kraken_ohlc(
        SYMBOL,
        "15m",
        DAYS
    )

    df_5m = fetch_kraken_ohlc(
        SYMBOL,
        "5m",
        DAYS
    )

    # ========================================================
    # VALIDATION
    # ========================================================

    if len(df_1h) < 50:

        raise RuntimeError(
            "Not enough 1H candles."
        )

    if len(df_15m) < 100:

        raise RuntimeError(
            "Not enough 15M candles."
        )

    if len(df_5m) < 200:

        raise RuntimeError(
            "Not enough 5M candles."
        )

    # ========================================================
    # PREPARE
    # ========================================================

    df = prepare_data(
        df_1h,
        df_15m,
        df_5m
    )

    # --------------------------------------------------------
    # Remove latest 5M candle.
    # --------------------------------------------------------

    if len(df) > 1:

        df = df.iloc[:-1].copy()

    df = df.reset_index(
        drop=True
    )

    print(
        f"\nBacktest 5M candles: "
        f"{len(df)}"
    )

    if df.empty:

        raise RuntimeError(
            "Prepared dataframe is empty."
        )

    # ========================================================
    # BACKTEST
    # ========================================================

    trades, equity_curve = (
        run_backtest(df)
    )

    # ========================================================
    # RESULTS
    # ========================================================

    calculate_performance(
        trades,
        equity_curve
    )

    save_results(
        trades
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":

    main()
