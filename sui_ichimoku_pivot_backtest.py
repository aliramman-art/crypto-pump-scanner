# ============================================================
# SUI/USDT ICHIMOKU + PIVOT BACKTEST
# ============================================================
#
# TIMEFRAME: 5 MINUTES
#
# LONG:
#   1. Tenkan crosses above Kijun
#   2. Chikou crosses above price
#   3. Price above Ichimoku cloud
#   4. Cloud is bullish
#
# SHORT:
#   1. Tenkan crosses below Kijun
#   2. Chikou crosses below price
#   3. Price below Ichimoku cloud
#   4. Cloud is bearish
#
# STOP LOSS:
#   Latest confirmed valid Pivot Low for LONG
#   Latest confirmed valid Pivot High for SHORT
#
# PIVOT:
#   5 candles LEFT
#   5 candles RIGHT
#
# IMPORTANT:
#   A pivot is usable only after the 5 right candles
#   have closed. No look-ahead bias.
#
# TAKE PROFIT:
#   Nearest valid Pivot High above entry for LONG
#   Nearest valid Pivot Low below entry for SHORT
#
# RR:
#   TP distance must be strictly greater than SL distance
#
# DATA:
#   Binance USD-M Futures
#   Monthly archives for completed months
#   Daily archives for current month
#
# ============================================================

import io
import os
import zipfile
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timedelta, timezone
from dateutil.relativedelta import relativedelta


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "SUIUSDT"
INTERVAL = "5m"

END_DATE = datetime.now(timezone.utc).date()
START_DATE = END_DATE - timedelta(days=365)

INITIAL_CAPITAL = 1000.0

# Binance Futures taker fee approximation
FEE_RATE = 0.0004

# Slippage approximation
SLIPPAGE_RATE = 0.0001

# Ichimoku
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
ICHIMOKU_DISPLACEMENT = 26

# Pivot
PIVOT_LEFT = 5
PIVOT_RIGHT = 5

# Minimum RR
MIN_RR = 1.0

# Only one position at a time
MAX_OPEN_POSITIONS = 1


# ============================================================
# BINANCE DATA URL
# ============================================================

BASE_URL = "https://data.binance.vision/data/futures/um"


# ============================================================
# PRINT HEADER
# ============================================================

def print_header():
    print("=" * 70)
    print("SUI/USDT ICHIMOKU + PIVOT BACKTEST")
    print("=" * 70)
    print(f"Symbol       : {SYMBOL}")
    print(f"Interval     : {INTERVAL}")
    print(f"Start        : {START_DATE}")
    print(f"End          : {END_DATE}")
    print(f"Initial Cash : ${INITIAL_CAPITAL:,.2f}")
    print(f"Fee          : {FEE_RATE * 100:.4f}%")
    print(f"Slippage     : {SLIPPAGE_RATE * 100:.4f}%")
    print(f"Pivot        : {PIVOT_LEFT} left / {PIVOT_RIGHT} right")
    print(f"Minimum RR   : > {MIN_RR:.2f}")
    print("=" * 70)


# ============================================================
# DOWNLOAD FILE
# ============================================================

def download_file(url):
    try:
        response = requests.get(
            url,
            timeout=60,
            headers={
                "User-Agent": "Mozilla/5.0"
            }
        )

        if response.status_code != 200:
            return None

        if len(response.content) == 0:
            return None

        return response.content

    except Exception as e:
        print(f"  Download error: {e}")
        return None


# ============================================================
# BINANCE COLUMN NAMES
# ============================================================

BINANCE_COLUMNS = [
    "open_time",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "close_time",
    "quote_volume",
    "trade_count",
    "taker_buy_volume",
    "taker_buy_quote_volume",
    "ignore",
]


# ============================================================
# PARSE CSV FROM ZIP
# ============================================================

def parse_binance_zip(content, source_name=""):
    """
    Parse Binance ZIP archive.

    Handles both:
      - files with header
      - files without header
    """

    try:
        with zipfile.ZipFile(io.BytesIO(content)) as z:
            csv_files = [
                name for name in z.namelist()
                if name.lower().endswith(".csv")
            ]

            if not csv_files:
                print(f"  No CSV inside archive: {source_name}")
                return None

            csv_name = csv_files[0]

            with z.open(csv_name) as f:
                df = pd.read_csv(
                    f,
                    header=None,
                    names=BINANCE_COLUMNS
                )

        # ----------------------------------------------------
        # Remove possible header row
        # ----------------------------------------------------

        if len(df) > 0:
            df = df[
                df["open_time"].astype(str).str.lower() != "open_time"
            ]

        # ----------------------------------------------------
        # Numeric conversion
        # ----------------------------------------------------

        numeric_columns = [
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "close_time",
            "quote_volume",
            "trade_count",
            "taker_buy_volume",
            "taker_buy_quote_volume",
        ]

        for col in numeric_columns:
            df[col] = pd.to_numeric(
                df[col],
                errors="coerce"
            )

        # ----------------------------------------------------
        # Remove malformed rows
        # ----------------------------------------------------

        df = df.dropna(
            subset=[
                "open_time",
                "open",
                "high",
                "low",
                "close"
            ]
        )

        if df.empty:
            return None

        # ----------------------------------------------------
        # Convert timestamp
        # ----------------------------------------------------

        df["open_time"] = pd.to_datetime(
            df["open_time"].astype("int64"),
            unit="ms",
            utc=True
        )

        # ----------------------------------------------------
        # Sort
        # ----------------------------------------------------

        df = df.sort_values("open_time")

        return df

    except Exception as e:
        print(f"  Parse error [{source_name}]: {e}")
        return None


# ============================================================
# DOWNLOAD MONTHLY DATA
# ============================================================

def download_month(year, month):
    filename = (
        f"{SYMBOL}-{INTERVAL}-{year:04d}-{month:02d}.zip"
    )

    url = (
        f"{BASE_URL}/monthly/klines/"
        f"{SYMBOL}/{INTERVAL}/{filename}"
    )

    print(f"Downloading monthly: {year:04d}-{month:02d}")

    content = download_file(url)

    if content is None:
        print("  No monthly file")
        return None

    return parse_binance_zip(
        content,
        source_name=filename
    )


# ============================================================
# DOWNLOAD DAILY DATA
# ============================================================

def download_day(year, month, day):
    filename = (
        f"{SYMBOL}-{INTERVAL}-"
        f"{year:04d}-{month:02d}-{day:02d}.zip"
    )

    url = (
        f"{BASE_URL}/daily/klines/"
        f"{SYMBOL}/{INTERVAL}/{filename}"
    )

    print(f"Downloading daily: {year:04d}-{month:02d}-{day:02d}")

    content = download_file(url)

    if content is None:
        return None

    return parse_binance_zip(
        content,
        source_name=filename
    )


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    print("\nLoading Binance Futures data...")
    print(
        f"Requested range: "
        f"{START_DATE} -> {END_DATE}"
    )

    frames = []

    # --------------------------------------------------------
    # Determine months
    # --------------------------------------------------------

    current = datetime(
        START_DATE.year,
        START_DATE.month,
        1
    )

    end_month = datetime(
        END_DATE.year,
        END_DATE.month,
        1
    )

    while current <= end_month:

        year = current.year
        month = current.month

        # ----------------------------------------------------
        # Current month:
        # Binance monthly archive may not exist yet.
        # Use daily files instead.
        # ----------------------------------------------------

        is_current_month = (
            year == END_DATE.year
            and month == END_DATE.month
        )

        if is_current_month:

            first_day = 1

            if (
                year == START_DATE.year
                and month == START_DATE.month
            ):
                first_day = START_DATE.day

            for day in range(
                first_day,
                END_DATE.day + 1
            ):

                df_day = download_day(
                    year,
                    month,
                    day
                )

                if df_day is not None:
                    frames.append(df_day)

        else:

            # ------------------------------------------------
            # Completed month
            # ------------------------------------------------

            df_month = download_month(
                year,
                month
            )

            if df_month is not None:
                frames.append(df_month)

        current += relativedelta(months=1)

    # --------------------------------------------------------
    # Validate
    # --------------------------------------------------------

    if not frames:
        raise RuntimeError(
            "No Binance data was downloaded."
        )

    print("\nCombining downloaded data...")

    df = pd.concat(
        frames,
        ignore_index=True
    )

    # --------------------------------------------------------
    # Sort and remove duplicates
    # --------------------------------------------------------

    df = df.sort_values(
        "open_time"
    )

    df = df.drop_duplicates(
        subset=["open_time"],
        keep="first"
    )

    # --------------------------------------------------------
    # Exact requested date range
    # --------------------------------------------------------

    start_ts = pd.Timestamp(
        START_DATE,
        tz="UTC"
    )

    # Include the entire END_DATE
    end_ts = (
        pd.Timestamp(END_DATE, tz="UTC")
        + pd.Timedelta(days=1)
    )

    df = df[
        (df["open_time"] >= start_ts)
        & (df["open_time"] < end_ts)
    ].copy()

    # --------------------------------------------------------
    # Sort again
    # --------------------------------------------------------

    df = df.sort_values(
        "open_time"
    ).reset_index(drop=True)

    # --------------------------------------------------------
    # Sanity check
    # --------------------------------------------------------

    if df.empty:
        raise RuntimeError(
            "Data is empty after date filtering."
        )

    print("\nData loaded successfully.")
    print(
        f"Rows        : {len(df):,}"
    )
    print(
        f"First candle : {df['open_time'].iloc[0]}"
    )
    print(
        f"Last candle  : {df['open_time'].iloc[-1]}"
    )

    # --------------------------------------------------------
    # Expected approximate number of 5m candles
    # --------------------------------------------------------

    expected_days = (
        df["open_time"].iloc[-1]
        - df["open_time"].iloc[0]
    ).total_seconds() / 86400

    expected_candles = expected_days * 288

    coverage = (
        len(df) / expected_candles * 100
        if expected_candles > 0
        else 0
    )

    print(
        f"Approx coverage: {coverage:.2f}%"
    )

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(df):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    # --------------------------------------------------------
    # Tenkan
    # --------------------------------------------------------

    tenkan_high = (
        high.rolling(
            TENKAN_PERIOD
        ).max()
    )

    tenkan_low = (
        low.rolling(
            TENKAN_PERIOD
        ).min()
    )

    df["tenkan"] = (
        tenkan_high + tenkan_low
    ) / 2.0

    # --------------------------------------------------------
    # Kijun
    # --------------------------------------------------------

    kijun_high = (
        high.rolling(
            KIJUN_PERIOD
        ).max()
    )

    kijun_low = (
        low.rolling(
            KIJUN_PERIOD
        ).min()
    )

    df["kijun"] = (
        kijun_high + kijun_low
    ) / 2.0

    # --------------------------------------------------------
    # Senkou Span A RAW
    # --------------------------------------------------------

    senkou_a_raw = (
        df["tenkan"] + df["kijun"]
    ) / 2.0

    # --------------------------------------------------------
    # Senkou Span B RAW
    # --------------------------------------------------------

    senkou_b_raw = (
        high.rolling(
            SENKOU_B_PERIOD
        ).max()
        +
        low.rolling(
            SENKOU_B_PERIOD
        ).min()
    ) / 2.0

    # --------------------------------------------------------
    # Visible cloud
    #
    # The cloud visible at current candle was calculated
    # 26 candles earlier.
    #
    # This avoids using future data.
    # --------------------------------------------------------

    df["senkou_a"] = (
        senkou_a_raw.shift(
            ICHIMOKU_DISPLACEMENT
        )
    )

    df["senkou_b"] = (
        senkou_b_raw.shift(
            ICHIMOKU_DISPLACEMENT
        )
    )

    # --------------------------------------------------------
    # Cloud top / bottom
    # --------------------------------------------------------

    df["cloud_top"] = df[
        ["senkou_a", "senkou_b"]
    ].max(axis=1)

    df["cloud_bottom"] = df[
        ["senkou_a", "senkou_b"]
    ].min(axis=1)

    # --------------------------------------------------------
    # Cloud direction
    # --------------------------------------------------------

    df["bullish_cloud"] = (
        df["senkou_a"]
        >
        df["senkou_b"]
    )

    df["bearish_cloud"] = (
        df["senkou_a"]
        <
        df["senkou_b"]
    )

    # --------------------------------------------------------
    # Chikou reference
    #
    # Current Chikou value is current close plotted 26
    # candles backwards.
    #
    # For signal testing we compare current price with
    # the price from 26 candles ago.
    # --------------------------------------------------------

    df["chikou_price"] = (
        close.shift(
            ICHIMOKU_DISPLACEMENT
        )
    )

    return df


# ============================================================
# PIVOT DETECTION
# ============================================================

def detect_pivots(df):

    n = len(df)

    df["pivot_high"] = np.nan
    df["pivot_low"] = np.nan

    # --------------------------------------------------------
    # Pivot at candle i is confirmed at candle i + 5.
    #
    # Therefore:
    #
    # pivot_high[confirmation_index] = pivot price
    #
    # The pivot becomes available only after the right-side
    # candles have closed.
    # --------------------------------------------------------

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        left_start = i - PIVOT_LEFT
        left_end = i

        right_start = i + 1
        right_end = i + PIVOT_RIGHT + 1

        current_high = df["high"].iloc[i]
        current_low = df["low"].iloc[i]

        left_highs = df["high"].iloc[
            left_start:left_end
        ]

        right_highs = df["high"].iloc[
            right_start:right_end
        ]

        left_lows = df["low"].iloc[
            left_start:left_end
        ]

        right_lows = df["low"].iloc[
            right_start:right_end
        ]

        # ----------------------------------------------------
        # Pivot High
        # ----------------------------------------------------

        is_pivot_high = (
            current_high > left_highs.max()
            and
            current_high > right_highs.max()
        )

        # ----------------------------------------------------
        # Pivot Low
        # ----------------------------------------------------

        is_pivot_low = (
            current_low < left_lows.min()
            and
            current_low < right_lows.min()
        )

        confirmation_index = (
            i + PIVOT_RIGHT
        )

        if is_pivot_high:
            df.loc[
                confirmation_index,
                "pivot_high"
            ] = current_high

        if is_pivot_low:
            df.loc[
                confirmation_index,
                "pivot_low"
            ] = current_low

    return df


# ============================================================
# LONG SIGNAL
# ============================================================

def long_signal(df, i):

    if i < 2:
        return False

    row = df.iloc[i]
    prev = df.iloc[i - 1]

    # --------------------------------------------------------
    # Required indicator values
    # --------------------------------------------------------

    required = [
        row["tenkan"],
        row["kijun"],
        row["senkou_a"],
        row["senkou_b"],
        prev["tenkan"],
        prev["kijun"],
        row["chikou_price"],
        prev["chikou_price"],
    ]

    if any(pd.isna(x) for x in required):
        return False

    # --------------------------------------------------------
    # 1. Tenkan crosses upward through Kijun
    # --------------------------------------------------------

    tenkan_cross_up = (
        prev["tenkan"] <= prev["kijun"]
        and
        row["tenkan"] > row["kijun"]
    )

    if not tenkan_cross_up:
        return False

    # --------------------------------------------------------
    # 2. Chikou crosses upward through price
    #
    # Current close > close 26 candles ago
    # Previous close <= close 27 candles ago
    # --------------------------------------------------------

    if i < 27:
        return False

    current_close = df["close"].iloc[i]
    previous_close = df["close"].iloc[i - 1]

    close_26 = df["close"].iloc[i - 26]
    close_27 = df["close"].iloc[i - 27]

    chikou_cross_up = (
        previous_close <= close_27
        and
        current_close > close_26
    )

    if not chikou_cross_up:
        return False

    # --------------------------------------------------------
    # 3. Price above cloud
    # --------------------------------------------------------

    if row["close"] <= row["cloud_top"]:
        return False

    # --------------------------------------------------------
    # 4. Bullish cloud
    # --------------------------------------------------------

    if not row["bullish_cloud"]:
        return False

    return True


# ============================================================
# SHORT SIGNAL
# ============================================================

def short_signal(df, i):

    if i < 2:
        return False

    row = df.iloc[i]
    prev = df.iloc[i - 1]

    required = [
        row["tenkan"],
        row["kijun"],
        row["senkou_a"],
        row["senkou_b"],
        prev["tenkan"],
        prev["kijun"],
        row["chikou_price"],
        prev["chikou_price"],
    ]

    if any(pd.isna(x) for x in required):
        return False

    # --------------------------------------------------------
    # 1. Tenkan crosses downward through Kijun
    # --------------------------------------------------------

    tenkan_cross_down = (
        prev["tenkan"] >= prev["kijun"]
        and
        row["tenkan"] < row["kijun"]
    )

    if not tenkan_cross_down:
        return False

    # --------------------------------------------------------
    # 2. Chikou crosses downward through price
    # --------------------------------------------------------

    if i < 27:
        return False

    current_close = df["close"].iloc[i]
    previous_close = df["close"].iloc[i - 1]

    close_26 = df["close"].iloc[i - 26]
    close_27 = df["close"].iloc[i - 27]

    chikou_cross_down = (
        previous_close >= close_27
        and
        current_close < close_26
    )

    if not chikou_cross_down:
        return False

    # --------------------------------------------------------
    # 3. Price below cloud
    # --------------------------------------------------------

    if row["close"] >= row["cloud_bottom"]:
        return False

    # --------------------------------------------------------
    # 4. Bearish cloud
    # --------------------------------------------------------

    if not row["bearish_cloud"]:
        return False

    return True


# ============================================================
# FIND VALID PIVOT LOW FOR LONG SL
# ============================================================

def get_long_stop(df, i, entry):

    values = df.loc[
        :i,
        "pivot_low"
    ].dropna()

    if values.empty:
        return None

    # --------------------------------------------------------
    # Stop must be below entry.
    # --------------------------------------------------------

    valid = values[
        values < entry
    ]

    if valid.empty:
        return None

    # Latest confirmed valid pivot low
    return float(valid.iloc[-1])


# ============================================================
# FIND VALID PIVOT HIGH FOR SHORT SL
# ============================================================

def get_short_stop(df, i, entry):

    values = df.loc[
        :i,
        "pivot_high"
    ].dropna()

    if values.empty:
        return None

    valid = values[
        values > entry
    ]

    if valid.empty:
        return None

    # Latest confirmed valid pivot high
    return float(valid.iloc[-1])


# ============================================================
# FIND NEAREST PIVOT HIGH ABOVE ENTRY
# ============================================================

def get_long_target(df, i, entry):

    values = df.loc[
        :i,
        "pivot_high"
    ].dropna()

    if values.empty:
        return None

    # Only resistance above entry
    valid = values[
        values > entry
    ]

    if valid.empty:
        return None

    # --------------------------------------------------------
    # IMPORTANT FIX:
    #
    # Choose nearest resistance by PRICE,
    # not simply the latest confirmed pivot.
    # --------------------------------------------------------

    nearest = valid.min()

    return float(nearest)


# ============================================================
# FIND NEAREST PIVOT LOW BELOW ENTRY
# ============================================================

def get_short_target(df, i, entry):

    values = df.loc[
        :i,
        "pivot_low"
    ].dropna()

    if values.empty:
        return None

    # Only support below entry
    valid = values[
        values < entry
    ]

    if valid.empty:
        return None

    # --------------------------------------------------------
    # IMPORTANT FIX:
    #
    # Choose nearest support by PRICE.
    # --------------------------------------------------------

    nearest = valid.max()

    return float(nearest)


# ============================================================
# APPLY ENTRY SLIPPAGE
# ============================================================

def apply_entry_slippage(price, side):

    if side == "LONG":
        return price * (
            1.0 + SLIPPAGE_RATE
        )

    return price * (
        1.0 - SLIPPAGE_RATE
    )


# ============================================================
# APPLY EXIT SLIPPAGE
# ============================================================

def apply_exit_slippage(price, side):

    if side == "LONG":
        return price * (
            1.0 - SLIPPAGE_RATE
        )

    return price * (
        1.0 + SLIPPAGE_RATE
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df):

    capital = INITIAL_CAPITAL

    trades = []

    position = None

    signal_count = 0
    rejected_no_sl = 0
    rejected_no_tp = 0
    rejected_rr = 0

    n = len(df)

    for i in range(n):

        row = df.iloc[i]

        # ====================================================
        # MANAGE EXISTING POSITION
        # ====================================================

        if position is not None:

            side = position["side"]
            sl = position["sl"]
            tp = position["tp"]

            high = row["high"]
            low = row["low"]

            exit_price = None
            exit_reason = None

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                hit_sl = low <= sl
                hit_tp = high >= tp

                # Conservative:
                # if both occur in same candle,
                # assume SL first.
                if hit_sl:
                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:
                    exit_price = tp
                    exit_reason = "TP"

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                hit_sl = high >= sl
                hit_tp = low <= tp

                # Conservative:
                # if both occur in same candle,
                # assume SL first.
                if hit_sl:
                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:
                    exit_price = tp
                    exit_reason = "TP"

            # ------------------------------------------------
            # EXIT
            # ------------------------------------------------

            if exit_price is not None:

                actual_exit = apply_exit_slippage(
                    exit_price,
                    side
                )

                entry_price = position[
                    "entry_price"
                ]

                if side == "LONG":

                    gross_pct = (
                        actual_exit
                        -
                        entry_price
                    ) / entry_price

                else:

                    gross_pct = (
                        entry_price
                        -
                        actual_exit
                    ) / entry_price

                # ------------------------------------------------
                # Fees
                # ------------------------------------------------

                entry_fee = (
                    position["capital_at_entry"]
                    *
                    FEE_RATE
                )

                exit_value = (
                    position["capital_at_entry"]
                    *
                    (1.0 + gross_pct)
                )

                exit_fee = (
                    exit_value
                    *
                    FEE_RATE
                )

                net_profit = (
                    exit_value
                    -
                    position["capital_at_entry"]
                    -
                    entry_fee
                    -
                    exit_fee
                )

                net_pct = (
                    net_profit
                    /
                    position["capital_at_entry"]
                )

                capital += net_profit

                trades.append({
                    "entry_time":
                        position["entry_time"],
                    "exit_time":
                        row["open_time"],
                    "side":
                        side,
                    "entry":
                        entry_price,
                    "sl":
                        sl,
                    "tp":
                        tp,
                    "exit":
                        actual_exit,
                    "exit_reason":
                        exit_reason,
                    "sl_pct":
                        position["sl_pct"],
                    "tp_pct":
                        position["tp_pct"],
                    "rr":
                        position["rr"],
                    "gross_pct":
                        gross_pct * 100.0,
                    "net_pct":
                        net_pct * 100.0,
                    "profit":
                        net_profit,
                    "capital_after":
                        capital,
                })

                position = None

        # ====================================================
        # NO NEW POSITION IF ONE IS OPEN
        # ====================================================

        if position is not None:
            continue

        # ====================================================
        # SIGNAL
        # ====================================================

        side = None

        if long_signal(df, i):
            side = "LONG"

        elif short_signal(df, i):
            side = "SHORT"

        if side is None:
            continue

        signal_count += 1

        # ====================================================
        # ENTRY
        # ====================================================

        raw_entry = float(
            row["close"]
        )

        entry = apply_entry_slippage(
            raw_entry,
            side
        )

        # ====================================================
        # STOP
        # ====================================================

        if side == "LONG":

            sl = get_long_stop(
                df,
                i,
                entry
            )

            if sl is None:
                rejected_no_sl += 1
                continue

            # SL must be below entry
            if sl >= entry:
                rejected_no_sl += 1
                continue

            sl_distance = (
                entry - sl
            )

            sl_pct = (
                sl_distance
                /
                entry
            )

            tp = get_long_target(
                df,
                i,
                entry
            )

            if tp is None:
                rejected_no_tp += 1
                continue

            if tp <= entry:
                rejected_no_tp += 1
                continue

            tp_distance = (
                tp - entry
            )

        else:

            sl = get_short_stop(
                df,
                i,
                entry
            )

            if sl is None:
                rejected_no_sl += 1
                continue

            if sl <= entry:
                rejected_no_sl += 1
                continue

            sl_distance = (
                sl - entry
            )

            sl_pct = (
                sl_distance
                /
                entry
            )

            tp = get_short_target(
                df,
                i,
                entry
            )

            if tp is None:
                rejected_no_tp += 1
                continue

            if tp >= entry:
                rejected_no_tp += 1
                continue

            tp_distance = (
                entry - tp
            )

        # ====================================================
        # RR
        # ====================================================

        tp_pct = (
            tp_distance
            /
            entry
        )

        rr = (
            tp_distance
            /
            sl_distance
        )

        if rr <= MIN_RR:
            rejected_rr += 1
            continue

        # ====================================================
        # OPEN POSITION
        # ====================================================

        position = {
            "side":
                side,
            "entry_time":
                row["open_time"],
            "entry_price":
                entry,
            "sl":
                sl,
            "tp":
                tp,
            "sl_pct":
                sl_pct * 100.0,
            "tp_pct":
                tp_pct * 100.0,
            "rr":
                rr,
            "capital_at_entry":
                capital,
        }

    # ========================================================
    # IMPORTANT:
    # Do NOT artificially close an open trade at the end.
    # It is excluded from completed-trade statistics.
    # ========================================================

    return (
        trades,
        capital,
        signal_count,
        rejected_no_sl,
        rejected_no_tp,
        rejected_rr,
        position
    )


# ============================================================
# MAX DRAW DOWN
# ============================================================

def calculate_max_drawdown(equity):

    if not equity:
        return 0.0

    series = pd.Series(
        equity,
        dtype=float
    )

    peak = series.cummax()

    drawdown = (
        series - peak
    ) / peak

    return float(
        drawdown.min() * 100.0
    )


# ============================================================
# STATISTICS
# ============================================================

def calculate_statistics(
    trades,
    final_capital,
    signal_count,
    rejected_no_sl,
    rejected_no_tp,
    rejected_rr,
    open_position
):

    print("\n")
    print("=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

    total = len(trades)

    if total == 0:

        print("Completed trades : 0")
        print(
            f"Raw signals      : {signal_count}"
        )
        print(
            f"Rejected no SL   : {rejected_no_sl}"
        )
        print(
            f"Rejected no TP   : {rejected_no_tp}"
        )
        print(
            f"Rejected RR      : {rejected_rr}"
        )

        summary = pd.DataFrame([
            {
                "Trades": 0,
                "Long": 0,
                "Short": 0,
                "Wins": 0,
                "Losses": 0,
                "WinRate_%": 0.0,
                "ProfitFactor": 0.0,
                "NetPnL_%": (
                    final_capital
                    /
                    INITIAL_CAPITAL
                    -
                    1.0
                ) * 100.0,
                "FinalCapital": final_capital,
                "MaxDD_%": 0.0,
                "AvgRR": 0.0,
                "RawSignals": signal_count,
                "Rejected_NoSL": rejected_no_sl,
                "Rejected_NoTP": rejected_no_tp,
                "Rejected_RR": rejected_rr,
                "OpenPosition": (
                    1
                    if open_position
                    else 0
                ),
            }
        ])

        return summary

    trades_df = pd.DataFrame(
        trades
    )

    # --------------------------------------------------------
    # Wins / losses
    # --------------------------------------------------------

    wins = trades_df[
        trades_df["profit"] > 0
    ]

    losses = trades_df[
        trades_df["profit"] < 0
    ]

    win_count = len(wins)
    loss_count = len(losses)

    win_rate = (
        win_count / total * 100.0
    )

    # --------------------------------------------------------
    # Profit factor
    # --------------------------------------------------------

    gross_profit = (
        wins["profit"].sum()
        if not wins.empty
        else 0.0
    )

    gross_loss = (
        abs(losses["profit"].sum())
        if not losses.empty
        else 0.0
    )

    if gross_loss > 0:
        profit_factor = (
            gross_profit
            /
            gross_loss
        )
    else:
        profit_factor = np.inf

    # --------------------------------------------------------
    # Equity curve
    # --------------------------------------------------------

    equity = [
        INITIAL_CAPITAL
    ]

    equity.extend(
        trades_df[
            "capital_after"
        ].tolist()
    )

    max_dd = calculate_max_drawdown(
        equity
    )

    # --------------------------------------------------------
    # Long / Short
    # --------------------------------------------------------

    long_count = len(
        trades_df[
            trades_df["side"] == "LONG"
        ]
    )

    short_count = len(
        trades_df[
            trades_df["side"] == "SHORT"
        ]
    )

    # --------------------------------------------------------
    # Exit reasons
    # --------------------------------------------------------

    tp_count = len(
        trades_df[
            trades_df["exit_reason"] == "TP"
        ]
    )

    sl_count = len(
        trades_df[
            trades_df["exit_reason"] == "SL"
        ]
    )

    # --------------------------------------------------------
    # Average RR
    # --------------------------------------------------------

    avg_rr = trades_df[
        "rr"
    ].mean()

    avg_win = (
        wins["net_pct"].mean()
        if not wins.empty
        else 0.0
    )

    avg_loss = (
        losses["net_pct"].mean()
        if not losses.empty
        else 0.0
    )

    net_pnl_pct = (
        final_capital
        /
        INITIAL_CAPITAL
        -
        1.0
    ) * 100.0

    # --------------------------------------------------------
    # Print
    # --------------------------------------------------------

    print(
        f"Completed trades : {total}"
    )

    print(
        f"Long trades      : {long_count}"
    )

    print(
        f"Short trades     : {short_count}"
    )

    print(
        f"Wins             : {win_count}"
    )

    print(
        f"Losses           : {loss_count}"
    )

    print(
        f"Win Rate         : {win_rate:.2f}%"
    )

    if np.isinf(profit_factor):
        print(
            "Profit Factor    : INF"
        )
    else:
        print(
            f"Profit Factor    : "
            f"{profit_factor:.3f}"
        )

    print(
        f"Net PnL          : "
        f"{net_pnl_pct:.2f}%"
    )

    print(
        f"Final Capital    : "
        f"${final_capital:,.2f}"
    )

    print(
        f"Max Drawdown     : "
        f"{max_dd:.2f}%"
    )

    print(
        f"Average RR       : "
        f"{avg_rr:.2f}"
    )

    print(
        f"Average Win      : "
        f"{avg_win:.2f}%"
    )

    print(
        f"Average Loss     : "
        f"{avg_loss:.2f}%"
    )

    print(
        f"TP exits         : "
        f"{tp_count}"
    )

    print(
        f"SL exits         : "
        f"{sl_count}"
    )

    print("\nSignal diagnostics")

    print(
        f"Raw signals      : "
        f"{signal_count}"
    )

    print(
        f"No valid SL      : "
        f"{rejected_no_sl}"
    )

    print(
        f"No valid TP      : "
        f"{rejected_no_tp}"
    )

    print(
        f"RR <= {MIN_RR:.2f}        : "
        f"{rejected_rr}"
    )

    if open_position is not None:

        print("\nOpen position at end:")
        print(
            f"Side             : "
            f"{open_position['side']}"
        )
        print(
            f"Entry            : "
            f"{open_position['entry_price']:.8f}"
        )
        print(
            f"SL               : "
            f"{open_position['sl']:.8f}"
        )
        print(
            f"TP               : "
            f"{open_position['tp']:.8f}"
        )
        print(
            f"RR               : "
            f"{open_position['rr']:.2f}"
        )

    # --------------------------------------------------------
    # Summary dataframe
    # --------------------------------------------------------

    summary = pd.DataFrame([
        {
            "Trades":
                total,

            "Long":
                long_count,

            "Short":
                short_count,

            "Wins":
                win_count,

            "Losses":
                loss_count,

            "WinRate_%":
                win_rate,

            "ProfitFactor":
                profit_factor,

            "NetPnL_%":
                net_pnl_pct,

            "FinalCapital":
                final_capital,

            "MaxDD_%":
                max_dd,

            "AvgRR":
                avg_rr,

            "AvgWin_%":
                avg_win,

            "AvgLoss_%":
                avg_loss,

            "TP_Exits":
                tp_count,

            "SL_Exits":
                sl_count,

            "RawSignals":
                signal_count,

            "Rejected_NoSL":
                rejected_no_sl,

            "Rejected_NoTP":
                rejected_no_tp,

            "Rejected_RR":
                rejected_rr,

            "OpenPosition":
                (
                    1
                    if open_position
                    else 0
                ),
        }
    ])

    return summary


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(
    trades,
    summary
):

    trades_file = (
        "sui_ichimoku_pivot_trades.csv"
    )

    summary_file = (
        "sui_ichimoku_pivot_summary.csv"
    )

    # --------------------------------------------------------
    # Trades
    # --------------------------------------------------------

    if trades:

        trades_df = pd.DataFrame(
            trades
        )

        trades_df.to_csv(
            trades_file,
            index=False
        )

        print(
            f"\nSaved trades: "
            f"{trades_file}"
        )

    else:

        pd.DataFrame().to_csv(
            trades_file,
            index=False
        )

        print(
            f"\nNo completed trades."
        )

    # --------------------------------------------------------
    # Summary
    # --------------------------------------------------------

    summary.to_csv(
        summary_file,
        index=False
    )

    print(
        f"Saved summary: "
        f"{summary_file}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print_header()

    # --------------------------------------------------------
    # Load data
    # --------------------------------------------------------

    df = load_data()

    # --------------------------------------------------------
    # Ichimoku
    # --------------------------------------------------------

    print("\nCalculating Ichimoku...")

    df = calculate_ichimoku(
        df
    )

    # --------------------------------------------------------
    # Pivots
    # --------------------------------------------------------

    print("Detecting pivots...")

    df = detect_pivots(
        df
    )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    print("\nRunning backtest...")

    (
        trades,
        final_capital,
        signal_count,
        rejected_no_sl,
        rejected_no_tp,
        rejected_rr,
        open_position
    ) = run_backtest(
        df
    )

    # --------------------------------------------------------
    # Statistics
    # --------------------------------------------------------

    summary = calculate_statistics(
        trades=trades,
        final_capital=final_capital,
        signal_count=signal_count,
        rejected_no_sl=rejected_no_sl,
        rejected_no_tp=rejected_no_tp,
        rejected_rr=rejected_rr,
        open_position=open_position
    )

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    save_results(
        trades,
        summary
    )

    # --------------------------------------------------------
    # Final
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("BACKTEST FINISHED")
    print("=" * 70)


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
