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
#   4. Bullish cloud
#
# SHORT:
#   1. Tenkan crosses below Kijun
#   2. Chikou crosses below price
#   3. Price below Ichimoku cloud
#   4. Bearish cloud
#
# STOP LOSS:
#   Latest confirmed valid Pivot Low for LONG
#   Latest confirmed valid Pivot High for SHORT
#
# PIVOT:
#   5 candles LEFT
#   5 candles RIGHT
#
# TAKE PROFIT:
#   Nearest confirmed Pivot High above entry for LONG
#   Nearest confirmed Pivot Low below entry for SHORT
#
# TP FILTER:
#   Maximum TP distance = 3.00%
#
# RR:
#   TP distance must be strictly greater than SL distance
#
# DATA:
#   Binance USD-M Futures
#   Monthly archives for completed months
#   Daily archives for current month
#
# NO LOOK-AHEAD:
#   Pivot becomes usable only after 5 right candles close.
#
# ============================================================

import io
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

# ============================================================
# NEW TP FILTER
# Maximum allowed TP distance from entry
# ============================================================

MAX_TP_DISTANCE_PCT = 3.0

# One position at a time
MAX_OPEN_POSITIONS = 1

# Binance archive
BASE_URL = "https://data.binance.vision/data/futures/um"


# ============================================================
# BINANCE COLUMNS
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
# HEADER
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
    print(
        f"Pivot        : "
        f"{PIVOT_LEFT} left / {PIVOT_RIGHT} right"
    )
    print(
        f"Minimum RR   : > {MIN_RR:.2f}"
    )
    print(
        f"Max TP Dist  : "
        f"{MAX_TP_DISTANCE_PCT:.2f}%"
    )

    print("=" * 70)


# ============================================================
# DOWNLOAD
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

        print(
            f"  Download error: {e}"
        )

        return None


# ============================================================
# PARSE BINANCE ZIP
# ============================================================

def parse_binance_zip(
    content,
    source_name=""
):

    try:

        with zipfile.ZipFile(
            io.BytesIO(content)
        ) as z:

            csv_files = [
                name
                for name in z.namelist()
                if name.lower().endswith(".csv")
            ]

            if not csv_files:

                print(
                    f"  No CSV inside archive: "
                    f"{source_name}"
                )

                return None

            csv_name = csv_files[0]

            with z.open(csv_name) as f:

                df = pd.read_csv(
                    f,
                    header=None,
                    names=BINANCE_COLUMNS
                )

        # ----------------------------------------------------
        # Remove header row if present
        # ----------------------------------------------------

        df = df[
            df["open_time"]
            .astype(str)
            .str.lower()
            != "open_time"
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
        # Remove invalid rows
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
        # Timestamp
        # ----------------------------------------------------

        df["open_time"] = pd.to_datetime(
            df["open_time"].astype("int64"),
            unit="ms",
            utc=True
        )

        df = df.sort_values(
            "open_time"
        )

        return df

    except Exception as e:

        print(
            f"  Parse error [{source_name}]: {e}"
        )

        return None


# ============================================================
# MONTHLY DOWNLOAD
# ============================================================

def download_month(
    year,
    month
):

    filename = (
        f"{SYMBOL}-{INTERVAL}-"
        f"{year:04d}-{month:02d}.zip"
    )

    url = (
        f"{BASE_URL}/monthly/klines/"
        f"{SYMBOL}/{INTERVAL}/"
        f"{filename}"
    )

    print(
        f"Downloading monthly: "
        f"{year:04d}-{month:02d}"
    )

    content = download_file(url)

    if content is None:

        print("  No monthly file")

        return None

    return parse_binance_zip(
        content,
        filename
    )


# ============================================================
# DAILY DOWNLOAD
# ============================================================

def download_day(
    year,
    month,
    day
):

    filename = (
        f"{SYMBOL}-{INTERVAL}-"
        f"{year:04d}-{month:02d}-{day:02d}.zip"
    )

    url = (
        f"{BASE_URL}/daily/klines/"
        f"{SYMBOL}/{INTERVAL}/"
        f"{filename}"
    )

    print(
        f"Downloading daily: "
        f"{year:04d}-{month:02d}-{day:02d}"
    )

    content = download_file(url)

    if content is None:
        return None

    return parse_binance_zip(
        content,
        filename
    )


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    print(
        "\nLoading Binance Futures data..."
    )

    print(
        f"Requested range: "
        f"{START_DATE} -> {END_DATE}"
    )

    frames = []

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

        is_current_month = (
            year == END_DATE.year
            and
            month == END_DATE.month
        )

        # ----------------------------------------------------
        # Current month = daily files
        # ----------------------------------------------------

        if is_current_month:

            first_day = 1

            if (
                year == START_DATE.year
                and
                month == START_DATE.month
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

                    frames.append(
                        df_day
                    )

        # ----------------------------------------------------
        # Completed month = monthly file
        # ----------------------------------------------------

        else:

            df_month = download_month(
                year,
                month
            )

            if df_month is not None:

                frames.append(
                    df_month
                )

        current += relativedelta(
            months=1
        )

    if not frames:

        raise RuntimeError(
            "No Binance data was downloaded."
        )

    print(
        "\nCombining downloaded data..."
    )

    df = pd.concat(
        frames,
        ignore_index=True
    )

    # --------------------------------------------------------
    # Sort
    # --------------------------------------------------------

    df = df.sort_values(
        "open_time"
    )

    # --------------------------------------------------------
    # Remove duplicate candles
    # --------------------------------------------------------

    df = df.drop_duplicates(
        subset=["open_time"],
        keep="first"
    )

    # --------------------------------------------------------
    # Exact requested range
    # --------------------------------------------------------

    start_ts = pd.Timestamp(
        START_DATE,
        tz="UTC"
    )

    end_ts = (
        pd.Timestamp(
            END_DATE,
            tz="UTC"
        )
        +
        pd.Timedelta(days=1)
    )

    df = df[
        (df["open_time"] >= start_ts)
        &
        (df["open_time"] < end_ts)
    ].copy()

    df = df.sort_values(
        "open_time"
    ).reset_index(
        drop=True
    )

    if df.empty:

        raise RuntimeError(
            "Data is empty after filtering."
        )

    print(
        "\nData loaded successfully."
    )

    print(
        f"Rows        : {len(df):,}"
    )

    print(
        f"First candle : "
        f"{df['open_time'].iloc[0]}"
    )

    print(
        f"Last candle  : "
        f"{df['open_time'].iloc[-1]}"
    )

    expected_days = (
        (
            df["open_time"].iloc[-1]
            -
            df["open_time"].iloc[0]
        )
        .total_seconds()
        / 86400
    )

    expected_candles = (
        expected_days * 288
    )

    coverage = (
        len(df)
        /
        expected_candles
        *
        100
        if expected_candles > 0
        else 0
    )

    print(
        f"Approx coverage: "
        f"{coverage:.2f}%"
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
        high
        .rolling(
            TENKAN_PERIOD
        )
        .max()
    )

    tenkan_low = (
        low
        .rolling(
            TENKAN_PERIOD
        )
        .min()
    )

    df["tenkan"] = (
        tenkan_high
        +
        tenkan_low
    ) / 2.0

    # --------------------------------------------------------
    # Kijun
    # --------------------------------------------------------

    kijun_high = (
        high
        .rolling(
            KIJUN_PERIOD
        )
        .max()
    )

    kijun_low = (
        low
        .rolling(
            KIJUN_PERIOD
        )
        .min()
    )

    df["kijun"] = (
        kijun_high
        +
        kijun_low
    ) / 2.0

    # --------------------------------------------------------
    # Senkou A
    # --------------------------------------------------------

    senkou_a_raw = (
        df["tenkan"]
        +
        df["kijun"]
    ) / 2.0

    # --------------------------------------------------------
    # Senkou B
    # --------------------------------------------------------

    senkou_b_raw = (
        high
        .rolling(
            SENKOU_B_PERIOD
        )
        .max()
        +
        low
        .rolling(
            SENKOU_B_PERIOD
        )
        .min()
    ) / 2.0

    # --------------------------------------------------------
    # Visible cloud
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
    # Cloud boundaries
    # --------------------------------------------------------

    df["cloud_top"] = df[
        [
            "senkou_a",
            "senkou_b"
        ]
    ].max(axis=1)

    df["cloud_bottom"] = df[
        [
            "senkou_a",
            "senkou_b"
        ]
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

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT
    ):

        left_start = (
            i - PIVOT_LEFT
        )

        left_end = i

        right_start = i + 1

        right_end = (
            i
            +
            PIVOT_RIGHT
            +
            1
        )

        current_high = (
            df["high"].iloc[i]
        )

        current_low = (
            df["low"].iloc[i]
        )

        left_highs = df[
            "high"
        ].iloc[
            left_start:left_end
        ]

        right_highs = df[
            "high"
        ].iloc[
            right_start:right_end
        ]

        left_lows = df[
            "low"
        ].iloc[
            left_start:left_end
        ]

        right_lows = df[
            "low"
        ].iloc[
            right_start:right_end
        ]

        is_pivot_high = (
            current_high
            >
            left_highs.max()
            and
            current_high
            >
            right_highs.max()
        )

        is_pivot_low = (
            current_low
            <
            left_lows.min()
            and
            current_low
            <
            right_lows.min()
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

def long_signal(
    df,
    i
):

    if i < 27:
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
    ]

    if any(
        pd.isna(x)
        for x in required
    ):
        return False

    # --------------------------------------------------------
    # Tenkan crosses above Kijun
    # --------------------------------------------------------

    tenkan_cross_up = (
        prev["tenkan"]
        <=
        prev["kijun"]
        and
        row["tenkan"]
        >
        row["kijun"]
    )

    if not tenkan_cross_up:
        return False

    # --------------------------------------------------------
    # Chikou crosses upward through price
    # --------------------------------------------------------

    current_close = (
        df["close"].iloc[i]
    )

    previous_close = (
        df["close"].iloc[i - 1]
    )

    close_26 = (
        df["close"].iloc[i - 26]
    )

    close_27 = (
        df["close"].iloc[i - 27]
    )

    chikou_cross_up = (
        previous_close
        <=
        close_27
        and
        current_close
        >
        close_26
    )

    if not chikou_cross_up:
        return False

    # --------------------------------------------------------
    # Price above cloud
    # --------------------------------------------------------

    if (
        row["close"]
        <=
        row["cloud_top"]
    ):
        return False

    # --------------------------------------------------------
    # Bullish cloud
    # --------------------------------------------------------

    if not row["bullish_cloud"]:
        return False

    return True


# ============================================================
# SHORT SIGNAL
# ============================================================

def short_signal(
    df,
    i
):

    if i < 27:
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
    ]

    if any(
        pd.isna(x)
        for x in required
    ):
        return False

    # --------------------------------------------------------
    # Tenkan crosses below Kijun
    # --------------------------------------------------------

    tenkan_cross_down = (
        prev["tenkan"]
        >=
        prev["kijun"]
        and
        row["tenkan"]
        <
        row["kijun"]
    )

    if not tenkan_cross_down:
        return False

    # --------------------------------------------------------
    # Chikou crosses downward
    # --------------------------------------------------------

    current_close = (
        df["close"].iloc[i]
    )

    previous_close = (
        df["close"].iloc[i - 1]
    )

    close_26 = (
        df["close"].iloc[i - 26]
    )

    close_27 = (
        df["close"].iloc[i - 27]
    )

    chikou_cross_down = (
        previous_close
        >=
        close_27
        and
        current_close
        <
        close_26
    )

    if not chikou_cross_down:
        return False

    # --------------------------------------------------------
    # Price below cloud
    # --------------------------------------------------------

    if (
        row["close"]
        >=
        row["cloud_bottom"]
    ):
        return False

    # --------------------------------------------------------
    # Bearish cloud
    # --------------------------------------------------------

    if not row["bearish_cloud"]:
        return False

    return True


# ============================================================
# LONG SL
# ============================================================

def get_long_stop(
    df,
    i,
    entry
):

    values = df.loc[
        :i,
        "pivot_low"
    ].dropna()

    if values.empty:
        return None

    valid = values[
        values < entry
    ]

    if valid.empty:
        return None

    # Latest confirmed pivot below entry
    return float(
        valid.iloc[-1]
    )


# ============================================================
# SHORT SL
# ============================================================

def get_short_stop(
    df,
    i,
    entry
):

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

    # Latest confirmed pivot above entry
    return float(
        valid.iloc[-1]
    )


# ============================================================
# LONG TP
# ============================================================

def get_long_target(
    df,
    i,
    entry
):

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

    # Nearest resistance by price
    return float(
        valid.min()
    )


# ============================================================
# SHORT TP
# ============================================================

def get_short_target(
    df,
    i,
    entry
):

    values = df.loc[
        :i,
        "pivot_low"
    ].dropna()

    if values.empty:
        return None

    valid = values[
        values < entry
    ]

    if valid.empty:
        return None

    # Nearest support by price
    return float(
        valid.max()
    )


# ============================================================
# ENTRY SLIPPAGE
# ============================================================

def apply_entry_slippage(
    price,
    side
):

    if side == "LONG":

        return price * (
            1.0 + SLIPPAGE_RATE
        )

    return price * (
        1.0 - SLIPPAGE_RATE
    )


# ============================================================
# EXIT SLIPPAGE
# ============================================================

def apply_exit_slippage(
    price,
    side
):

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

    # --------------------------------------------------------
    # Diagnostics
    # --------------------------------------------------------

    signal_count = 0

    rejected_no_sl = 0
    rejected_no_tp = 0
    rejected_tp_distance = 0
    rejected_rr = 0

    n = len(df)

    # ========================================================
    # LOOP
    # ========================================================

    for i in range(n):

        row = df.iloc[i]

        # ====================================================
        # MANAGE OPEN POSITION
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

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp
                )

                # Conservative:
                # SL first if both happen in same candle
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

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp
                )

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:

                    exit_price = tp
                    exit_reason = "TP"

            # ------------------------------------------------
            # CLOSE TRADE
            # ------------------------------------------------

            if exit_price is not None:

                actual_exit = (
                    apply_exit_slippage(
                        exit_price,
                        side
                    )
                )

                entry_price = (
                    position[
                        "entry_price"
                    ]
                )

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
                    position[
                        "capital_at_entry"
                    ]
                    *
                    FEE_RATE
                )

                exit_value = (
                    position[
                        "capital_at_entry"
                    ]
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
                    position[
                        "capital_at_entry"
                    ]
                    -
                    entry_fee
                    -
                    exit_fee
                )

                net_pct = (
                    net_profit
                    /
                    position[
                        "capital_at_entry"
                    ]
                )

                capital += net_profit

                trades.append({

                    "entry_time":
                        position[
                            "entry_time"
                        ],

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
                        position[
                            "sl_pct"
                        ],

                    "tp_pct":
                        position[
                            "tp_pct"
                        ],

                    "rr":
                        position[
                            "rr"
                        ],

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
        # DO NOT OPEN SECOND POSITION
        # ====================================================

        if position is not None:
            continue

        # ====================================================
        # FIND SIGNAL
        # ====================================================

        side = None

        if long_signal(
            df,
            i
        ):

            side = "LONG"

        elif short_signal(
            df,
            i
        ):

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

            if sl >= entry:

                rejected_no_sl += 1
                continue

            sl_distance = (
                entry - sl
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

        # ====================================================
        # TAKE PROFIT
        # ====================================================

        if side == "LONG":

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
        # TP DISTANCE FILTER
        # ====================================================

        tp_pct = (
            tp_distance
            /
            entry
        )

        tp_distance_pct = (
            tp_pct * 100.0
        )

        if (
            tp_distance_pct
            >
            MAX_TP_DISTANCE_PCT
        ):

            rejected_tp_distance += 1

            continue

        # ====================================================
        # RR
        # ====================================================

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
    # OPEN POSITION AT END
    #
    # Do not artificially close it.
    # ========================================================

    return (
        trades,
        capital,
        signal_count,
        rejected_no_sl,
        rejected_no_tp,
        rejected_tp_distance,
        rejected_rr,
        position
    )


# ============================================================
# MAX DRAWDOWN
# ============================================================

def calculate_max_drawdown(
    equity
):

    if not equity:
        return 0.0

    series = pd.Series(
        equity,
        dtype=float
    )

    peak = (
        series.cummax()
    )

    drawdown = (
        series - peak
    ) / peak

    return float(
        drawdown.min()
        *
        100.0
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
    rejected_tp_distance,
    rejected_rr,
    open_position
):

    print("\n")

    print("=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

    total = len(trades)

    # --------------------------------------------------------
    # No trades
    # --------------------------------------------------------

    if total == 0:

        net_pnl_pct = (
            final_capital
            /
            INITIAL_CAPITAL
            -
            1.0
        ) * 100.0

        print(
            "Completed trades : 0"
        )

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
            f"TP too far       : "
            f"{rejected_tp_distance}"
        )

        print(
            f"RR <= {MIN_RR:.2f}        : "
            f"{rejected_rr}"
        )

        summary = pd.DataFrame([
            {

                "Trades":
                    0,

                "Long":
                    0,

                "Short":
                    0,

                "Wins":
                    0,

                "Losses":
                    0,

                "WinRate_%":
                    0.0,

                "ProfitFactor":
                    0.0,

                "NetPnL_%":
                    net_pnl_pct,

                "FinalCapital":
                    final_capital,

                "MaxDD_%":
                    0.0,

                "AvgRR":
                    0.0,

                "AvgWin_%":
                    0.0,

                "AvgLoss_%":
                    0.0,

                "TP_Exits":
                    0,

                "SL_Exits":
                    0,

                "RawSignals":
                    signal_count,

                "Rejected_NoSL":
                    rejected_no_sl,

                "Rejected_NoTP":
                    rejected_no_tp,

                "Rejected_TP_Distance":
                    rejected_tp_distance,

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

    # --------------------------------------------------------
    # DataFrame
    # --------------------------------------------------------

    trades_df = pd.DataFrame(
        trades
    )

    # --------------------------------------------------------
    # Wins / Losses
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
        win_count
        /
        total
        *
        100.0
    )

    # --------------------------------------------------------
    # Profit Factor
    # --------------------------------------------------------

    gross_profit = (
        wins["profit"].sum()
        if not wins.empty
        else 0.0
    )

    gross_loss = (
        abs(
            losses["profit"].sum()
        )
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
    # Equity
    # --------------------------------------------------------

    equity = [
        INITIAL_CAPITAL
    ]

    equity.extend(
        trades_df[
            "capital_after"
        ].tolist()
    )

    max_dd = (
        calculate_max_drawdown(
            equity
        )
    )

    # --------------------------------------------------------
    # Long / Short
    # --------------------------------------------------------

    long_count = len(
        trades_df[
            trades_df["side"]
            ==
            "LONG"
        ]
    )

    short_count = len(
        trades_df[
            trades_df["side"]
            ==
            "SHORT"
        ]
    )

    # --------------------------------------------------------
    # Exit reasons
    # --------------------------------------------------------

    tp_count = len(
        trades_df[
            trades_df["exit_reason"]
            ==
            "TP"
        ]
    )

    sl_count = len(
        trades_df[
            trades_df["exit_reason"]
            ==
            "SL"
        ]
    )

    # --------------------------------------------------------
    # Average metrics
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
    # Print results
    # --------------------------------------------------------

    print(
        f"Completed trades : "
        f"{total}"
    )

    print(
        f"Long trades      : "
        f"{long_count}"
    )

    print(
        f"Short trades     : "
        f"{short_count}"
    )

    print(
        f"Wins             : "
        f"{win_count}"
    )

    print(
        f"Losses           : "
        f"{loss_count}"
    )

    print(
        f"Win Rate         : "
        f"{win_rate:.2f}%"
    )

    if np.isinf(
        profit_factor
    ):

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

    # --------------------------------------------------------
    # Diagnostics
    # --------------------------------------------------------

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
        f"TP too far       : "
        f"{rejected_tp_distance}"
    )

    print(
        f"RR <= {MIN_RR:.2f}        : "
        f"{rejected_rr}"
    )

    print(
        f"Max TP distance  : "
        f"{MAX_TP_DISTANCE_PCT:.2f}%"
    )

    # --------------------------------------------------------
    # Open position
    # --------------------------------------------------------

    if open_position is not None:

        print(
            "\nOpen position at end:"
        )

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
    # Summary
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

            "Rejected_TP_Distance":
                rejected_tp_distance,

            "Rejected_RR":
                rejected_rr,

            "Max_TP_Distance_%":
                MAX_TP_DISTANCE_PCT,

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
            "\nNo completed trades."
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
    # Data
    # --------------------------------------------------------

    df = load_data()

    # --------------------------------------------------------
    # Ichimoku
    # --------------------------------------------------------

    print(
        "\nCalculating Ichimoku..."
    )

    df = calculate_ichimoku(
        df
    )

    # --------------------------------------------------------
    # Pivots
    # --------------------------------------------------------

    print(
        "Detecting pivots..."
    )

    df = detect_pivots(
        df
    )

    # --------------------------------------------------------
    # Backtest
    # --------------------------------------------------------

    print(
        "\nRunning backtest..."
    )

    (
        trades,
        final_capital,
        signal_count,
        rejected_no_sl,
        rejected_no_tp,
        rejected_tp_distance,
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
        rejected_tp_distance=rejected_tp_distance,
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
    # Finish
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
