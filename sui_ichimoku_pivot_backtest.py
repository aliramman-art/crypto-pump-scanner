# ============================================================
# SUI/USDT - ICHIMOKU + PIVOT BACKTEST
# ============================================================
#
# TIMEFRAME: 5 MINUTES
#
# LONG:
#   1. Tenkan crosses above Kijun
#   2. Chikou crosses above price
#   3. Price closes above Kumo
#   4. Kumo is bullish after cloud shift
#   5. SL = last confirmed Pivot Low (5 left / 5 right)
#   6. TP = nearest confirmed Pivot High above entry
#   7. RR must be > 1
#
# SHORT:
#   Exact inverse logic
#
# IMPORTANT:
#   - CLOSED CANDLES ONLY
#   - NO LOOK-AHEAD BIAS
#   - PIVOTS REQUIRE 5 RIGHT-SIDE CANDLES
#   - ENTRY AT SIGNAL CANDLE CLOSE
#
# DATA:
#   Binance USD-M Futures
#   SUIUSDT
#   5m
#
# ============================================================

import io
import os
import zipfile
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "SUIUSDT"
INTERVAL = "5m"

# One year
END_DATE = datetime.now(timezone.utc).date()
START_DATE = END_DATE - timedelta(days=365)

# Ichimoku
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
DISPLACEMENT = 26

# Pivot
PIVOT_LEFT = 5
PIVOT_RIGHT = 5

# Trading
INITIAL_CAPITAL = 1000.0
POSITION_SIZE_PCT = 1.0

# Fees
FEE_RATE = 0.0004

# Slippage
SLIPPAGE_RATE = 0.0001

# Minimum RR
MIN_RR = 1.0

# Output
TRADES_FILE = "sui_ichimoku_pivot_trades.csv"
SUMMARY_FILE = "sui_ichimoku_pivot_summary.csv"


# ============================================================
# BINANCE DATA
# ============================================================

def download_month(year, month):
    """
    Download Binance USD-M Futures monthly 5m klines.
    """

    url = (
        "https://data.binance.vision/data/futures/um/monthly/"
        f"klines/{SYMBOL}/{INTERVAL}/"
        f"{SYMBOL}-{INTERVAL}-{year}-{month:02d}.zip"
    )

    print(f"Downloading: {year}-{month:02d}")

    response = requests.get(url, timeout=60)

    if response.status_code != 200:
        print(
            f"  No monthly file "
            f"(HTTP {response.status_code})"
        )
        return pd.DataFrame()

    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        csv_files = [
            name for name in z.namelist()
            if name.endswith(".csv")
        ]

        if not csv_files:
            return pd.DataFrame()

        with z.open(csv_files[0]) as f:
            df = pd.read_csv(f, header=None)

    return df


def load_data():
    columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_volume",
        "taker_buy_quote_volume",
        "ignore"
    ]

    frames = []

    current = START_DATE.replace(day=1)
    last = END_DATE.replace(day=1)

    while current <= last:

        df = download_month(
            current.year,
            current.month
        )

        if not df.empty:
            df.columns = columns
            frames.append(df)

        if current.month == 12:
            current = current.replace(
                year=current.year + 1,
                month=1
            )
        else:
            current = current.replace(
                month=current.month + 1
            )

    if not frames:
        raise RuntimeError(
            "No Binance data was downloaded."
        )

    df = pd.concat(
        frames,
        ignore_index=True
    )

    # --------------------------------------------------------
    # CLEAN
    # --------------------------------------------------------

    df["open_time"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True
    )

    df["close_time"] = pd.to_datetime(
        df["close_time"],
        unit="ms",
        utc=True
    )

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]

    for col in numeric_columns:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=numeric_columns
    )

    # Remove duplicate candles
    df = df.drop_duplicates(
        subset=["open_time"]
    )

    # Sort
    df = df.sort_values(
        "open_time"
    ).reset_index(drop=True)

    # Requested period
    start_ts = pd.Timestamp(
        START_DATE,
        tz="UTC"
    )

    end_ts = (
        pd.Timestamp(
            END_DATE,
            tz="UTC"
        )
        + pd.Timedelta(days=1)
    )

    df = df[
        (df["open_time"] >= start_ts)
        & (df["open_time"] < end_ts)
    ].copy()

    df.reset_index(
        drop=True,
        inplace=True
    )

    print()
    print("=" * 70)
    print("DATA")
    print("=" * 70)
    print(f"Symbol       : {SYMBOL}")
    print(f"Interval     : {INTERVAL}")
    print(f"Start        : {df['open_time'].iloc[0]}")
    print(f"End          : {df['open_time'].iloc[-1]}")
    print(f"Candles      : {len(df):,}")
    print("=" * 70)

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(df):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # Tenkan
    df["tenkan"] = (
        high.rolling(TENKAN_PERIOD).max()
        +
        low.rolling(TENKAN_PERIOD).min()
    ) / 2

    # Kijun
    df["kijun"] = (
        high.rolling(KIJUN_PERIOD).max()
        +
        low.rolling(KIJUN_PERIOD).min()
    ) / 2

    # Senkou A
    df["senkou_a_raw"] = (
        df["tenkan"] + df["kijun"]
    ) / 2

    # Senkou B
    df["senkou_b_raw"] = (
        high.rolling(SENKOU_B_PERIOD).max()
        +
        low.rolling(SENKOU_B_PERIOD).min()
    ) / 2

    # --------------------------------------------------------
    # IMPORTANT
    #
    # These are the values that are plotted 26 candles ahead.
    # For a real-time signal at candle i, we need the cloud
    # that is currently visible at candle i.
    #
    # Therefore:
    # current cloud = raw cloud calculated 26 candles ago.
    # --------------------------------------------------------

    df["senkou_a"] = (
        df["senkou_a_raw"]
        .shift(DISPLACEMENT)
    )

    df["senkou_b"] = (
        df["senkou_b_raw"]
        .shift(DISPLACEMENT)
    )

    # Chikou:
    # plotted 26 candles backward.
    # At current candle i, compare current close with
    # the price 26 candles ago.
    df["chikou_price"] = (
        df["close"].shift(DISPLACEMENT)
    )

    # Bullish / bearish cloud
    df["bullish_cloud"] = (
        df["senkou_a"] > df["senkou_b"]
    )

    df["bearish_cloud"] = (
        df["senkou_a"] < df["senkou_b"]
    )

    return df


# ============================================================
# PIVOTS
# ============================================================

def detect_pivots(df):

    highs = df["high"].values
    lows = df["low"].values

    pivot_high = np.full(
        len(df),
        np.nan
    )

    pivot_low = np.full(
        len(df),
        np.nan
    )

    # --------------------------------------------------------
    # A pivot at index i is only known at:
    #
    # i + PIVOT_RIGHT
    #
    # This prevents look-ahead bias.
    # --------------------------------------------------------

    for i in range(
        PIVOT_LEFT,
        len(df) - PIVOT_RIGHT
    ):

        left_highs = highs[
            i - PIVOT_LEFT:i
        ]

        right_highs = highs[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        left_lows = lows[
            i - PIVOT_LEFT:i
        ]

        right_lows = lows[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        if (
            highs[i] > left_highs.max()
            and
            highs[i] > right_highs.max()
        ):
            pivot_high[
                i + PIVOT_RIGHT
            ] = highs[i]

        if (
            lows[i] < left_lows.min()
            and
            lows[i] < right_lows.min()
        ):
            pivot_low[
                i + PIVOT_RIGHT
            ] = lows[i]

    df["confirmed_pivot_high"] = pivot_high
    df["confirmed_pivot_low"] = pivot_low

    return df


# ============================================================
# SIGNAL LOGIC
# ============================================================

def long_signal(df, i):

    if i < 1:
        return False

    # Required data
    values = [
        df.at[i, "tenkan"],
        df.at[i, "kijun"],
        df.at[i, "senkou_a"],
        df.at[i, "senkou_b"],
        df.at[i, "chikou_price"]
    ]

    if any(pd.isna(x) for x in values):
        return False

    # --------------------------------------------------------
    # 1. Tenkan crosses ABOVE Kijun
    # --------------------------------------------------------

    tenkan_cross = (
        df.at[i - 1, "tenkan"]
        <=
        df.at[i - 1, "kijun"]
        and
        df.at[i, "tenkan"]
        >
        df.at[i, "kijun"]
    )

    if not tenkan_cross:
        return False

    # --------------------------------------------------------
    # 2. Chikou crosses ABOVE price
    #
    # Current Chikou = current close projected backward.
    # Therefore compare current close with close 26 candles ago.
    #
    # We require the crossover to happen on this candle.
    # --------------------------------------------------------

    if i < DISPLACEMENT + 1:
        return False

    previous_chikou = df.at[
        i - 1,
        "close"
    ]

    previous_price_reference = df.at[
        i - DISPLACEMENT - 1,
        "close"
    ]

    current_chikou = df.at[
        i,
        "close"
    ]

    current_price_reference = df.at[
        i - DISPLACEMENT,
        "close"
    ]

    chikou_cross = (
        previous_chikou
        <=
        previous_price_reference
        and
        current_chikou
        >
        current_price_reference
    )

    if not chikou_cross:
        return False

    # --------------------------------------------------------
    # 3. Price above Kumo
    # --------------------------------------------------------

    cloud_top = max(
        df.at[i, "senkou_a"],
        df.at[i, "senkou_b"]
    )

    if df.at[i, "close"] <= cloud_top:
        return False

    # --------------------------------------------------------
    # 4. Bullish Kumo
    # --------------------------------------------------------

    if not df.at[i, "bullish_cloud"]:
        return False

    return True


def short_signal(df, i):

    if i < 1:
        return False

    values = [
        df.at[i, "tenkan"],
        df.at[i, "kijun"],
        df.at[i, "senkou_a"],
        df.at[i, "senkou_b"],
        df.at[i, "chikou_price"]
    ]

    if any(pd.isna(x) for x in values):
        return False

    # --------------------------------------------------------
    # 1. Tenkan crosses BELOW Kijun
    # --------------------------------------------------------

    tenkan_cross = (
        df.at[i - 1, "tenkan"]
        >=
        df.at[i - 1, "kijun"]
        and
        df.at[i, "tenkan"]
        <
        df.at[i, "kijun"]
    )

    if not tenkan_cross:
        return False

    # --------------------------------------------------------
    # 2. Chikou crosses BELOW price
    # --------------------------------------------------------

    if i < DISPLACEMENT + 1:
        return False

    previous_chikou = df.at[
        i - 1,
        "close"
    ]

    previous_price_reference = df.at[
        i - DISPLACEMENT - 1,
        "close"
    ]

    current_chikou = df.at[
        i,
        "close"
    ]

    current_price_reference = df.at[
        i - DISPLACEMENT,
        "close"
    ]

    chikou_cross = (
        previous_chikou
        >=
        previous_price_reference
        and
        current_chikou
        <
        current_price_reference
    )

    if not chikou_cross:
        return False

    # --------------------------------------------------------
    # 3. Price below Kumo
    # --------------------------------------------------------

    cloud_bottom = min(
        df.at[i, "senkou_a"],
        df.at[i, "senkou_b"]
    )

    if df.at[i, "close"] >= cloud_bottom:
        return False

    # --------------------------------------------------------
    # 4. Bearish Kumo
    # --------------------------------------------------------

    if not df.at[i, "bearish_cloud"]:
        return False

    return True


# ============================================================
# PIVOT SELECTION
# ============================================================

def get_last_pivot_low(df, i):

    values = df.loc[
        :i,
        "confirmed_pivot_low"
    ].dropna()

    if values.empty:
        return None

    return float(values.iloc[-1])


def get_last_pivot_high(df, i):

    values = df.loc[
        :i,
        "confirmed_pivot_high"
    ].dropna()

    if values.empty:
        return None

    return float(values.iloc[-1])


def get_next_pivot_high(df, i, entry):

    values = df.loc[
        :i,
        "confirmed_pivot_high"
    ].dropna()

    values = values[
        values > entry
    ]

    if values.empty:
        return None

    # nearest valid resistance
    return float(values.iloc[-1])


def get_next_pivot_low(df, i, entry):

    values = df.loc[
        :i,
        "confirmed_pivot_low"
    ].dropna()

    values = values[
        values < entry
    ]

    if values.empty:
        return None

    # nearest valid support
    return float(values.iloc[-1])


# ============================================================
# EXECUTION
# ============================================================

def apply_entry_slippage(price, side):

    if side == "LONG":
        return price * (
            1 + SLIPPAGE_RATE
        )

    return price * (
        1 - SLIPPAGE_RATE
    )


def apply_exit_slippage(price, side):

    if side == "LONG":
        return price * (
            1 - SLIPPAGE_RATE
        )

    return price * (
        1 + SLIPPAGE_RATE
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df):

    trades = []

    capital = INITIAL_CAPITAL

    in_position = False
    position = None

    for i in range(len(df)):

        # ----------------------------------------------------
        # MANAGE OPEN POSITION
        # ----------------------------------------------------

        if in_position:

            candle_high = df.at[
                i,
                "high"
            ]

            candle_low = df.at[
                i,
                "low"
            ]

            side = position["side"]

            sl = position["sl"]
            tp = position["tp"]

            exit_price = None
            exit_reason = None

            # -----------------------------------------------
            # LONG
            # -----------------------------------------------

            if side == "LONG":

                hit_sl = (
                    candle_low <= sl
                )

                hit_tp = (
                    candle_high >= tp
                )

                # Conservative:
                # if both occur in same candle,
                # assume SL first.
                if hit_sl and hit_tp:
                    exit_price = sl
                    exit_reason = "SL_AND_TP_SAME_CANDLE"

                elif hit_sl:
                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:
                    exit_price = tp
                    exit_reason = "TP"

            # -----------------------------------------------
            # SHORT
            # -----------------------------------------------

            else:

                hit_sl = (
                    candle_high >= sl
                )

                hit_tp = (
                    candle_low <= tp
                )

                if hit_sl and hit_tp:
                    exit_price = sl
                    exit_reason = "SL_AND_TP_SAME_CANDLE"

                elif hit_sl:
                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:
                    exit_price = tp
                    exit_reason = "TP"

            # -----------------------------------------------
            # CLOSE TRADE
            # -----------------------------------------------

            if exit_price is not None:

                exit_price = apply_exit_slippage(
                    exit_price,
                    side
                )

                entry_price = position[
                    "entry"
                ]

                if side == "LONG":

                    gross_pct = (
                        exit_price
                        -
                        entry_price
                    ) / entry_price

                else:

                    gross_pct = (
                        entry_price
                        -
                        exit_price
                    ) / entry_price

                # Fees
                total_fee = (
                    FEE_RATE * 2
                )

                net_pct = (
                    gross_pct
                    -
                    total_fee
                )

                pnl = (
                    capital
                    *
                    POSITION_SIZE_PCT
                    *
                    net_pct
                )

                capital += pnl

                trades.append({
                    "entry_time":
                        position["entry_time"],

                    "exit_time":
                        df.at[i, "open_time"],

                    "side":
                        side,

                    "entry":
                        entry_price,

                    "sl":
                        position["sl"],

                    "tp":
                        position["tp"],

                    "exit":
                        exit_price,

                    "gross_pct":
                        gross_pct * 100,

                    "net_pct":
                        net_pct * 100,

                    "pnl":
                        pnl,

                    "rr":
                        position["rr"],

                    "exit_reason":
                        exit_reason,

                    "capital":
                        capital
                })

                in_position = False
                position = None

                # Do not open a new position
                # on the same candle.
                continue

        # ----------------------------------------------------
        # SEARCH FOR NEW SIGNAL
        # ----------------------------------------------------

        if in_position:
            continue

        if i < 100:
            continue

        entry_raw = df.at[
            i,
            "close"
        ]

        # ====================================================
        # LONG
        # ====================================================

        if long_signal(df, i):

            sl = get_last_pivot_low(
                df,
                i
            )

            if sl is not None:

                entry = apply_entry_slippage(
                    entry_raw,
                    "LONG"
                )

                if sl < entry:

                    tp = get_next_pivot_high(
                        df,
                        i,
                        entry
                    )

                    if tp is not None:

                        risk = (
                            entry - sl
                        )

                        reward = (
                            tp - entry
                        )

                        if risk > 0:

                            rr = (
                                reward / risk
                            )

                            if rr > MIN_RR:

                                position = {
                                    "side":
                                        "LONG",

                                    "entry":
                                        entry,

                                    "sl":
                                        sl,

                                    "tp":
                                        tp,

                                    "rr":
                                        rr,

                                    "entry_time":
                                        df.at[
                                            i,
                                            "open_time"
                                        ]
                                }

                                in_position = True

                                continue

        # ====================================================
        # SHORT
        # ====================================================

        if short_signal(df, i):

            sl = get_last_pivot_high(
                df,
                i
            )

            if sl is not None:

                entry = apply_entry_slippage(
                    entry_raw,
                    "SHORT"
                )

                if sl > entry:

                    tp = get_next_pivot_low(
                        df,
                        i,
                        entry
                    )

                    if tp is not None:

                        risk = (
                            sl - entry
                        )

                        reward = (
                            entry - tp
                        )

                        if risk > 0:

                            rr = (
                                reward / risk
                            )

                            if rr > MIN_RR:

                                position = {
                                    "side":
                                        "SHORT",

                                    "entry":
                                        entry,

                                    "sl":
                                        sl,

                                    "tp":
                                        tp,

                                    "rr":
                                        rr,

                                    "entry_time":
                                        df.at[
                                            i,
                                            "open_time"
                                        ]
                                }

                                in_position = True

                                continue

    return pd.DataFrame(trades), capital


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(trades, final_capital):

    if trades.empty:

        print()
        print("NO TRADES")
        return

    total = len(trades)

    wins = (
        trades["net_pct"] > 0
    ).sum()

    losses = (
        trades["net_pct"] <= 0
    ).sum()

    win_rate = (
        wins / total * 100
    )

    gross_profit = trades.loc[
        trades["pnl"] > 0,
        "pnl"
    ].sum()

    gross_loss = abs(
        trades.loc[
            trades["pnl"] < 0,
            "pnl"
        ].sum()
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit /
            gross_loss
        )
    else:
        profit_factor = np.inf

    net_profit = (
        final_capital
        -
        INITIAL_CAPITAL
    )

    net_pct = (
        net_profit /
        INITIAL_CAPITAL
        * 100
    )

    # --------------------------------------------------------
    # EQUITY / MAX DD
    # --------------------------------------------------------

    equity = (
        INITIAL_CAPITAL
        +
        trades["pnl"].cumsum()
    )

    peak = equity.cummax()

    drawdown = (
        equity - peak
    ) / peak * 100

    max_dd = drawdown.min()

    avg_rr = trades["rr"].mean()

    long_trades = (
        trades["side"] == "LONG"
    ).sum()

    short_trades = (
        trades["side"] == "SHORT"
    ).sum()

    avg_win = trades.loc[
        trades["net_pct"] > 0,
        "net_pct"
    ].mean()

    avg_loss = trades.loc[
        trades["net_pct"] <= 0,
        "net_pct"
    ].mean()

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("SUI ICHIMOKU + PIVOT BACKTEST")
    print("=" * 70)

    print(f"Period          : {START_DATE} -> {END_DATE}")
    print(f"Timeframe       : {INTERVAL}")

    print()
    print(f"Initial Capital : ${INITIAL_CAPITAL:,.2f}")
    print(f"Final Capital   : ${final_capital:,.2f}")
    print(f"Net PnL         : ${net_profit:,.2f}")
    print(f"Net PnL %       : {net_pct:.2f}%")

    print()
    print(f"Trades          : {total}")
    print(f"Long            : {long_trades}")
    print(f"Short           : {short_trades}")

    print()
    print(f"Wins            : {wins}")
    print(f"Losses          : {losses}")
    print(f"Win Rate        : {win_rate:.2f}%")

    print()
    print(f"Profit Factor   : {profit_factor:.3f}")
    print(f"Average RR      : {avg_rr:.2f}")
    print(f"Average Win     : {avg_win:.2f}%")
    print(f"Average Loss    : {avg_loss:.2f}%")

    print()
    print(f"Max Drawdown    : {max_dd:.2f}%")

    print("=" * 70)

    # --------------------------------------------------------
    # EXIT BREAKDOWN
    # --------------------------------------------------------

    print()
    print("EXIT BREAKDOWN")
    print("-" * 70)

    print(
        trades["exit_reason"]
        .value_counts()
        .to_string()
    )

    # --------------------------------------------------------
    # SAVE SUMMARY
    # --------------------------------------------------------

    summary = pd.DataFrame([{
        "symbol":
            SYMBOL,

        "interval":
            INTERVAL,

        "start_date":
            str(START_DATE),

        "end_date":
            str(END_DATE),

        "trades":
            total,

        "long":
            long_trades,

        "short":
            short_trades,

        "wins":
            wins,

        "losses":
            losses,

        "win_rate_pct":
            win_rate,

        "profit_factor":
            profit_factor,

        "net_pnl":
            net_profit,

        "net_pnl_pct":
            net_pct,

        "max_drawdown_pct":
            max_dd,

        "average_rr":
            avg_rr,

        "average_win_pct":
            avg_win,

        "average_loss_pct":
            avg_loss,

        "final_capital":
            final_capital
    }])

    summary.to_csv(
        SUMMARY_FILE,
        index=False
    )

    print()
    print(f"Saved: {TRADES_FILE}")
    print(f"Saved: {SUMMARY_FILE}")


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("SUI/USDT ICHIMOKU + PIVOT BACKTEST")
    print("=" * 70)

    df = load_data()

    print()
    print("Calculating Ichimoku...")

    df = calculate_ichimoku(
        df
    )

    print("Calculating confirmed pivots...")

    df = detect_pivots(
        df
    )

    print("Running backtest...")

    trades, final_capital = run_backtest(
        df
    )

    if not trades.empty:

        trades.to_csv(
            TRADES_FILE,
            index=False
        )

    calculate_statistics(
        trades,
        final_capital
    )


if __name__ == "__main__":
    main()
