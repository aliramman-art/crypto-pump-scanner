# ============================================================
# TENKAN + KIJUN + DISTANCE BACKTEST
# TP = 1R TEST
# FULL 30-DAY DATA COVERAGE
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta, timezone


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "PF_DOGEUSD"

DAYS = 30

TICK_TYPE = "trade"

BASE_URL = "https://futures.kraken.com/api/charts/v1"

RESOLUTIONS = {
    "1h": 3600,
    "15m": 900,
    "5m": 300,
}

MAX_CANDLES_PER_REQUEST = 2000


# ============================================================
# ICHIMOKU
# ============================================================

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26


# ============================================================
# STRATEGY FILTERS
# ============================================================

MIN_DISTANCE_PCT = 0.20

MIN_DISTANCE_EXPANSION = 0.00

MAX_TENKAN_DISTANCE_PCT = 0.25

MIN_CONFIRM_BODY_RATIO = 0.20


# ============================================================
# STOP LOSS
# ============================================================

MIN_SL_PCT = 0.20

MAX_SL_PCT = 1.50

SL_BUFFER_PCT = 0.05


# ============================================================
# TAKE PROFIT
# ============================================================
#
# TEST VERSION:
# TP = 1R
#
# Previous version:
# MIN_RR = 1.50
#
# Everything else remains unchanged.
# ============================================================

MIN_RR = 1.00


# ============================================================
# BACKTEST
# ============================================================

INITIAL_BALANCE = 1000.0

RISK_PER_TRADE_PCT = 1.0

MAX_HOLD_BARS = 288


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Tenkan-Kijun-Backtest/1.0"
})


# ============================================================
# DOWNLOAD KRAKEN CANDLES
# ============================================================

def download_kraken_candles(symbol, resolution, days):

    print(f"\nDownloading {symbol} {resolution}...")

    seconds_per_candle = RESOLUTIONS[resolution]

    end_time = datetime.now(timezone.utc)

    start_time = end_time - timedelta(days=days)

    start_ts = int(start_time.timestamp())

    end_ts = int(end_time.timestamp())

    all_candles = []

    current_from = start_ts

    request_number = 0

    while current_from < end_ts:

        request_number += 1

        chunk_seconds = (
            MAX_CANDLES_PER_REQUEST
            * seconds_per_candle
        )

        current_to = min(
            current_from + chunk_seconds,
            end_ts
        )

        url = (
            f"{BASE_URL}/"
            f"{TICK_TYPE}/"
            f"{symbol}/"
            f"{resolution}"
        )

        params = {
            "from": current_from,
            "to": current_to,
            "count": MAX_CANDLES_PER_REQUEST,
        }

        print(
            f"  Request {request_number}: "
            f"{datetime.fromtimestamp(current_from, tz=timezone.utc)} "
            f"-> "
            f"{datetime.fromtimestamp(current_to, tz=timezone.utc)}"
        )

        response = session.get(
            url,
            params=params,
            timeout=30
        )

        if response.status_code != 200:

            print(
                f"  ERROR HTTP {response.status_code}"
            )

            print(response.text[:500])

            raise RuntimeError(
                f"Kraken API error "
                f"{response.status_code}"
            )

        data = response.json()

        candles = data.get("candles", [])

        if not candles:

            print("  No candles returned.")

            break

        print(
            f"  received {len(candles)} candles"
        )

        all_candles.extend(candles)

        returned_times = []

        for candle in candles:

            try:

                returned_times.append(
                    int(candle["time"]) // 1000
                )

            except Exception:

                pass

        if not returned_times:

            break

        latest_returned = max(returned_times)

        next_from = (
            latest_returned
            + seconds_per_candle
        )

        if next_from <= current_from:

            next_from = current_to + 1

        current_from = next_from

        time.sleep(0.15)

    if not all_candles:

        raise RuntimeError(
            f"No data returned for "
            f"{symbol} {resolution}"
        )

    # ========================================================
    # PARSE
    # ========================================================

    rows = []

    for candle in all_candles:

        try:

            rows.append({

                "time": pd.to_datetime(
                    int(candle["time"]),
                    unit="ms",
                    utc=True
                ),

                "open": float(candle["open"]),

                "high": float(candle["high"]),

                "low": float(candle["low"]),

                "close": float(candle["close"]),

                "volume": float(candle["volume"]),

            })

        except Exception:

            continue

    df = pd.DataFrame(rows)

    if df.empty:

        raise RuntimeError(
            f"Parsed dataframe empty for "
            f"{symbol} {resolution}"
        )

    # ========================================================
    # REMOVE DUPLICATES
    # ========================================================

    df = (
        df
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    # ========================================================
    # EXACT PERIOD
    # ========================================================

    df = df[
        (df["time"] >= pd.Timestamp(start_time))
        &
        (df["time"] <= pd.Timestamp(end_time))
    ].copy()

    df = (
        df
        .drop_duplicates(subset=["time"])
        .sort_values("time")
        .reset_index(drop=True)
    )

    print(
        f"Final {resolution} candles: {len(df)}"
    )

    if not df.empty:

        print(
            f"Range: "
            f"{df['time'].iloc[0]} "
            f"-> "
            f"{df['time'].iloc[-1]}"
        )

    # ========================================================
    # COVERAGE
    # ========================================================

    expected = int(
        days
        * 86400
        / seconds_per_candle
    )

    actual = len(df)

    coverage = (
        actual / expected * 100
        if expected > 0
        else 0
    )

    print(
        f"Expected ~{expected} candles | "
        f"Coverage: {coverage:.1f}%"
    )

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def add_ichimoku(df):

    df = df.copy()

    # --------------------------------------------------------
    # TENKAN
    # --------------------------------------------------------

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
        highest_high
        +
        lowest_low
    ) / 2

    # --------------------------------------------------------
    # KIJUN
    # --------------------------------------------------------

    highest_high_kijun = (
        df["high"]
        .rolling(KIJUN_PERIOD)
        .max()
    )

    lowest_low_kijun = (
        df["low"]
        .rolling(KIJUN_PERIOD)
        .min()
    )

    df["kijun"] = (
        highest_high_kijun
        +
        lowest_low_kijun
    ) / 2

    # --------------------------------------------------------
    # TENKAN / KIJUN DISTANCE
    # --------------------------------------------------------

    df["tk_distance_pct"] = (

        (
            df["tenkan"]
            -
            df["kijun"]
        ).abs()

        /

        df["kijun"].abs()

        *

        100
    )

    # --------------------------------------------------------
    # DISTANCE EXPANSION
    # --------------------------------------------------------

    df["tk_distance_expansion"] = (

        df["tk_distance_pct"]

        -

        df["tk_distance_pct"].shift(1)

    )

    return df


# ============================================================
# DOWNLOAD DATA
# ============================================================

df_1h = download_kraken_candles(
    SYMBOL,
    "1h",
    DAYS
)

df_15m = download_kraken_candles(
    SYMBOL,
    "15m",
    DAYS
)

df_5m = download_kraken_candles(
    SYMBOL,
    "5m",
    DAYS
)


# ============================================================
# ADD INDICATORS
# ============================================================

df_1h = add_ichimoku(df_1h)

df_15m = add_ichimoku(df_15m)

df_5m = add_ichimoku(df_5m)


# ============================================================
# PREPARE 1H
# ============================================================

htf_1h = df_1h[
    [
        "time",
        "tenkan",
        "kijun",
        "tk_distance_pct",
        "tk_distance_expansion",
    ]
].copy()

htf_1h = htf_1h.rename(
    columns={

        "tenkan":
            "tenkan_1h",

        "kijun":
            "kijun_1h",

        "tk_distance_pct":
            "distance_1h",

        "tk_distance_expansion":
            "distance_expansion_1h",

    }
)

htf_1h[
    [
        "tenkan_1h",
        "kijun_1h",
        "distance_1h",
        "distance_expansion_1h",
    ]
] = htf_1h[
    [
        "tenkan_1h",
        "kijun_1h",
        "distance_1h",
        "distance_expansion_1h",
    ]
].shift(1)


# ============================================================
# PREPARE 15M
# ============================================================

htf_15m = df_15m[
    [
        "time",
        "tenkan",
        "kijun",
        "tk_distance_pct",
        "tk_distance_expansion",
    ]
].copy()

htf_15m = htf_15m.rename(
    columns={

        "tenkan":
            "tenkan_15m",

        "kijun":
            "kijun_15m",

        "tk_distance_pct":
            "distance_15m",

        "tk_distance_expansion":
            "distance_expansion_15m",

    }
)

htf_15m[
    [
        "tenkan_15m",
        "kijun_15m",
        "distance_15m",
        "distance_expansion_15m",
    ]
] = htf_15m[
    [
        "tenkan_15m",
        "kijun_15m",
        "distance_15m",
        "distance_expansion_15m",
    ]
].shift(1)


# ============================================================
# MERGE 1H
# ============================================================

df = pd.merge_asof(

    df_5m.sort_values("time"),

    htf_1h.sort_values("time"),

    on="time",

    direction="backward"

)


# ============================================================
# MERGE 15M
# ============================================================

df = pd.merge_asof(

    df.sort_values("time"),

    htf_15m.sort_values("time"),

    on="time",

    direction="backward"

)


# ============================================================
# REMOVE INVALID DATA
# ============================================================

df = df.dropna(

    subset=[

        "tenkan",

        "kijun",

        "tenkan_1h",

        "kijun_1h",

        "tenkan_15m",

        "kijun_15m",

        "distance_15m",

        "distance_expansion_15m",

    ]

).reset_index(drop=True)


print()

print("=" * 70)

print(
    f"Backtest 5M candles: {len(df)}"
)

print("=" * 70)


# ============================================================
# BACKTEST
# ============================================================

trades = []

balance = INITIAL_BALANCE

equity_curve = []

i = 0

while i < len(df) - 1:

    row = df.iloc[i]

    close = row["close"]

    high = row["high"]

    low = row["low"]

    tenkan = row["tenkan"]

    kijun = row["kijun"]


    # ========================================================
    # 1H DIRECTION
    # ========================================================

    if row["tenkan_1h"] > row["kijun_1h"]:

        direction = "LONG"

    elif row["tenkan_1h"] < row["kijun_1h"]:

        direction = "SHORT"

    else:

        i += 1

        continue


    # ========================================================
    # 15M DISTANCE
    # ========================================================

    distance_15m = row["distance_15m"]

    expansion_15m = (
        row["distance_expansion_15m"]
    )

    if (

        pd.isna(distance_15m)

        or

        distance_15m < MIN_DISTANCE_PCT

    ):

        i += 1

        continue


    # ========================================================
    # 15M EXPANSION
    # ========================================================

    if (

        pd.isna(expansion_15m)

        or

        expansion_15m < MIN_DISTANCE_EXPANSION

    ):

        i += 1

        continue


    # ========================================================
    # 5M TENKAN DISTANCE
    # ========================================================

    if pd.isna(tenkan):

        i += 1

        continue


    tenkan_distance_pct = (

        abs(close - tenkan)

        /

        abs(tenkan)

        *

        100

    )


    if (

        tenkan_distance_pct
        >
        MAX_TENKAN_DISTANCE_PCT

    ):

        i += 1

        continue


    # ========================================================
    # CONFIRMATION CANDLE
    # ========================================================

    candle_range = high - low

    if candle_range <= 0:

        i += 1

        continue


    body = abs(
        close - row["open"]
    )

    body_ratio = (
        body
        /
        candle_range
    )


    if (
        body_ratio
        <
        MIN_CONFIRM_BODY_RATIO
    ):

        i += 1

        continue


    # ========================================================
    # TENKAN CONFIRMATION
    # ========================================================

    if direction == "LONG":

        if close <= tenkan:

            i += 1

            continue

    else:

        if close >= tenkan:

            i += 1

            continue


    # ========================================================
    # ENTRY
    # ========================================================

    entry = close


    # ========================================================
    # STOP LOSS
    # ========================================================

    if direction == "LONG":

        sl = kijun

        if (
            pd.isna(sl)
            or
            sl >= entry
        ):

            recent_low = (

                df["low"]
                .iloc[
                    max(0, i - 10):
                    i + 1
                ]
                .min()

            )

            sl = recent_low


        sl = sl * (
            1
            -
            SL_BUFFER_PCT / 100
        )


        sl_distance_pct = (

            (entry - sl)
            /
            entry
            *
            100

        )

    else:

        sl = kijun

        if (
            pd.isna(sl)
            or
            sl <= entry
        ):

            recent_high = (

                df["high"]
                .iloc[
                    max(0, i - 10):
                    i + 1
                ]
                .max()

            )

            sl = recent_high


        sl = sl * (
            1
            +
            SL_BUFFER_PCT / 100
        )


        sl_distance_pct = (

            (sl - entry)
            /
            entry
            *
            100

        )


    # ========================================================
    # SL VALIDATION
    # ========================================================

    if (

        sl_distance_pct
        <
        MIN_SL_PCT

        or

        sl_distance_pct
        >
        MAX_SL_PCT

    ):

        i += 1

        continue


    # ========================================================
    # TP
    # ========================================================
    #
    # NOW = 1R
    # ========================================================

    if direction == "LONG":

        tp = (

            entry

            +

            (entry - sl)
            *
            MIN_RR

        )

    else:

        tp = (

            entry

            -

            (sl - entry)
            *
            MIN_RR

        )


    # ========================================================
    # SIMULATE TRADE
    # ========================================================

    exit_price = None

    exit_time = None

    result = None

    R = None


    max_j = min(

        len(df),

        i + 1 + MAX_HOLD_BARS

    )


    for j in range(i + 1, max_j):

        future = df.iloc[j]

        future_high = future["high"]

        future_low = future["low"]


        # ====================================================
        # LONG
        # ====================================================

        if direction == "LONG":

            hit_sl = (
                future_low <= sl
            )

            hit_tp = (
                future_high >= tp
            )


            # Conservative:
            # if both happen in same candle,
            # SL first.
            if hit_sl and hit_tp:

                exit_price = sl

                result = "LOSS"

                R = -1.0


            elif hit_sl:

                exit_price = sl

                result = "LOSS"

                R = -1.0


            elif hit_tp:

                exit_price = tp

                result = "WIN"

                R = MIN_RR


        # ====================================================
        # SHORT
        # ====================================================

        else:

            hit_sl = (
                future_high >= sl
            )

            hit_tp = (
                future_low <= tp
            )


            if hit_sl and hit_tp:

                exit_price = sl

                result = "LOSS"

                R = -1.0


            elif hit_sl:

                exit_price = sl

                result = "LOSS"

                R = -1.0


            elif hit_tp:

                exit_price = tp

                result = "WIN"

                R = MIN_RR


        # ====================================================
        # EXIT FOUND
        # ====================================================

        if result is not None:

            exit_time = future["time"]

            break


    # ========================================================
    # TIME EXIT
    # ========================================================

    if result is None:

        j = max_j - 1

        future = df.iloc[j]

        exit_price = future["close"]

        exit_time = future["time"]

        result = "TIME"


        if direction == "LONG":

            R = (

                (exit_price - entry)

                /

                (entry - sl)

            )

        else:

            R = (

                (entry - exit_price)

                /

                (sl - entry)

            )


    # ========================================================
    # PNL %
    # ========================================================

    if direction == "LONG":

        pnl_pct = (

            (exit_price - entry)
            /
            entry
            *
            100

        )

    else:

        pnl_pct = (

            (entry - exit_price)
            /
            entry
            *
            100

        )


    # ========================================================
    # BALANCE
    # ========================================================

    risk_amount = (

        balance

        *

        RISK_PER_TRADE_PCT

        /

        100

    )

    balance += (
        risk_amount
        *
        R
    )

    equity_curve.append(balance)


    # ========================================================
    # SAVE TRADE
    # ========================================================

    trades.append({

        "entry_time":
            row["time"],

        "direction":
            direction,

        "entry":
            entry,

        "sl":
            sl,

        "tp":
            tp,

        "exit_time":
            exit_time,

        "exit":
            exit_price,

        "result":
            result,

        "pnl_pct":
            pnl_pct,

        "R":
            R,

        "balance":
            balance,

    })


    # ========================================================
    # NEXT TRADE
    # ========================================================

    i = j + 1


# ============================================================
# RESULTS
# ============================================================

trades_df = pd.DataFrame(trades)


print()

print("=" * 70)

print(
    "TENKAN + KIJUN + DISTANCE BACKTEST RESULTS"
)

print("=" * 70)


if trades_df.empty:

    print("No trades generated.")

else:

    total_trades = len(
        trades_df
    )

    wins = (
        trades_df["result"]
        ==
        "WIN"
    ).sum()

    losses = (
        trades_df["result"]
        ==
        "LOSS"
    ).sum()

    time_exits = (
        trades_df["result"]
        ==
        "TIME"
    ).sum()


    win_rate = (

        wins
        /
        total_trades
        *
        100

    )


    gross_profit = (

        trades_df.loc[
            trades_df["R"] > 0,
            "R"
        ].sum()

    )


    gross_loss = abs(

        trades_df.loc[
            trades_df["R"] < 0,
            "R"
        ].sum()

    )


    profit_factor = (

        gross_profit
        /
        gross_loss

        if gross_loss > 0

        else np.inf

    )


    net_r = trades_df["R"].sum()


    net_pnl_pct = (

        (
            balance
            -
            INITIAL_BALANCE
        )
        /
        INITIAL_BALANCE
        *
        100

    )


    average_r = (
        trades_df["R"].mean()
    )


    # ========================================================
    # MAX DRAWDOWN
    # ========================================================

    equity = pd.Series(

        [INITIAL_BALANCE]
        +
        equity_curve

    )

    peak = equity.cummax()

    drawdown = (

        (equity - peak)
        /
        peak
        *
        100

    )

    max_drawdown = drawdown.min()


    # ========================================================
    # MAIN REPORT
    # ========================================================

    print(
        f"Symbol              : {SYMBOL}"
    )

    print(
        f"Period              : {DAYS} days"
    )

    print(
        f"TP RR                : {MIN_RR:.2f}R"
    )

    print(
        f"Initial Balance     : "
        f"${INITIAL_BALANCE:.2f}"
    )

    print(
        f"Final Balance       : "
        f"${balance:.2f}"
    )

    print("-" * 70)

    print(
        f"Trades              : "
        f"{total_trades}"
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


    # ========================================================
    # DIRECTION STATISTICS
    # ========================================================

    print()

    print(
        "DIRECTION STATISTICS"
    )

    print("-" * 70)


    for direction in [
        "LONG",
        "SHORT"
    ]:

        d = trades_df[
            trades_df["direction"]
            ==
            direction
        ]


        if len(d) == 0:

            continue


        d_wins = (
            d["result"]
            ==
            "WIN"
        ).sum()


        d_wr = (

            d_wins
            /
            len(d)
            *
            100

        )


        d_r = d["R"].sum()


        print(

            f"{direction:<8} "
            f"Trades={len(d):<5} "
            f"WR={d_wr:6.2f}% "
            f"R={d_r:+.3f}"

        )


    # ========================================================
    # LAST 20 TRADES
    # ========================================================

    print()

    print(
        "LAST 20 TRADES"
    )

    print("-" * 70)


    print(

        trades_df[
            [
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
        ]
        .tail(20)
        .to_string(index=False)

    )


    # ========================================================
    # SAVE CSV
    # ========================================================

    trades_df.to_csv(

        "tenkan_kijun_backtest.csv",

        index=False

    )


    print()

    print(
        "Trade history saved: "
        "tenkan_kijun_backtest.csv"
    )
