# ============================================================
# SUI/USDT 5M
# ICHIMOKU + PIVOT BACKTEST v2
# ============================================================
#
# ENTRY:
#   LONG:
#       1) Tenkan crosses above Kijun
#       2) Chikou crosses above price
#       3) Price above cloud
#       4) Cloud bullish
#
#   SHORT:
#       Inverse conditions
#
# EXIT:
#   LONG:
#       SL = nearest confirmed Pivot Low BELOW entry
#       TP = nearest confirmed Pivot High ABOVE entry
#
#   SHORT:
#       SL = nearest confirmed Pivot High ABOVE entry
#       TP = nearest confirmed Pivot Low BELOW entry
#
# IMPORTANT:
#   SL selection has been changed from:
#       latest pivot by TIME
#   to:
#       nearest valid pivot by PRICE
#
#   TP remains nearest valid pivot by PRICE.
#
# PIVOT:
#   5 candles left + 5 candles right
#   Pivot usable only after right 5 candles close.
#
# NO LOOK-AHEAD
# CLOSED CANDLES ONLY
# ONE POSITION AT A TIME
#
# FEES:
#   0.04% per side
#
# SLIPPAGE:
#   0.01% per side
#
# INITIAL CAPITAL:
#   $1000
#
# COMPOUNDING:
#   YES
#
# ============================================================

import io
import requests
import numpy as np
import pandas as pd


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "SUIUSDT"
INTERVAL = "5m"

INITIAL_CAPITAL = 1000.0

FEE_RATE = 0.0004
SLIPPAGE_RATE = 0.0001

# Ichimoku
ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
ICHIMOKU_DISPLACEMENT = 26

# Pivot
PIVOT_LEFT = 5
PIVOT_RIGHT = 5

# Risk / reward
MIN_RR = 1.0
MAX_TP_DISTANCE_PCT = 3.0

# Backtest period
START_DATE = "2025-09-16"
END_DATE = "2026-09-16"

# Output files
TRADES_FILE = "sui_ichimoku_pivot_trades.csv"
SUMMARY_FILE = "sui_ichimoku_pivot_summary.csv"

RR_DIAGNOSTICS_FILE = (
    "sui_ichimoku_pivot_rr_diagnostics.csv"
)

RR_DISTRIBUTION_FILE = (
    "sui_ichimoku_pivot_rr_distribution.csv"
)


# ============================================================
# BINANCE URLS
# ============================================================

BINANCE_MONTHLY_URL = (
    "https://data.binance.vision/data/futures/um/monthly/"
    "klines/{symbol}/{interval}/"
    "{symbol}-{interval}-{year}-{month:02d}.zip"
)

BINANCE_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/"
    "klines/{symbol}/{interval}/"
    "{symbol}-{interval}-{date}.zip"
)


# ============================================================
# DOWNLOAD
# ============================================================

def download_bytes(url):

    try:

        response = requests.get(
            url,
            timeout=60,
            headers={
                "User-Agent": "Mozilla/5.0"
            },
        )

        if (
            response.status_code == 200
            and len(response.content) > 100
        ):
            return response.content

    except Exception as e:

        print(
            f"Download error: {url}"
        )

        print(e)

    return None


# ============================================================
# PARSE BINANCE CSV
# ============================================================

def parse_binance_zip(content):

    try:

        df = pd.read_csv(
            io.BytesIO(content),
            compression="zip",
            header=None,
        )

    except Exception as e:

        print(
            "CSV read error:",
            e,
        )

        return pd.DataFrame()

    if df.empty:
        return pd.DataFrame()

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
        "taker_buy_base",
        "taker_buy_quote",
        "ignore",
    ]

    df = df.iloc[
        :,
        :len(columns)
    ]

    df.columns = columns[
        :df.shape[1]
    ]

    # Remove possible CSV header row
    df = df[
        df["open_time"]
        .astype(str)
        .str.lower()
        != "open_time"
    ].copy()

    numeric_columns = [
        "open_time",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "close_time",
        "quote_volume",
        "trades",
        "taker_buy_base",
        "taker_buy_quote",
    ]

    for column in numeric_columns:

        if column in df.columns:

            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    df = df.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    if df.empty:
        return pd.DataFrame()

    df["datetime"] = pd.to_datetime(
        df["open_time"],
        unit="ms",
        utc=True,
        errors="coerce",
    )

    df = df.dropna(
        subset=["datetime"]
    )

    return df[
        [
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ].copy()


# ============================================================
# LOAD DATA
# ============================================================

def load_data():

    start = pd.Timestamp(
        START_DATE,
        tz="UTC",
    )

    end = pd.Timestamp(
        END_DATE,
        tz="UTC",
    )

    current = start.replace(
        day=1,
        hour=0,
        minute=0,
        second=0,
        microsecond=0,
    )

    parts = []

    print("=" * 70)
    print("DOWNLOADING BINANCE DATA")
    print("=" * 70)

    now = pd.Timestamp.now(
        tz="UTC"
    )

    while current < end:

        year = current.year
        month = current.month

        month_end = (
            current
            + pd.offsets.MonthBegin(1)
        )

        use_daily = (
            year == now.year
            and month == now.month
        )

        if not use_daily:

            url = BINANCE_MONTHLY_URL.format(
                symbol=SYMBOL,
                interval=INTERVAL,
                year=year,
                month=month,
            )

            print(
                f"Monthly: "
                f"{year}-{month:02d}"
            )

            content = download_bytes(
                url
            )

            if content is not None:

                df = parse_binance_zip(
                    content
                )

                if not df.empty:
                    parts.append(df)

        else:

            print(
                f"Current month -> daily files: "
                f"{year}-{month:02d}"
            )

            day = current

            while (
                day < month_end
                and day < end
            ):

                date_string = (
                    day.strftime(
                        "%Y-%m-%d"
                    )
                )

                url = BINANCE_DAILY_URL.format(
                    symbol=SYMBOL,
                    interval=INTERVAL,
                    date=date_string,
                )

                content = download_bytes(
                    url
                )

                if content is not None:

                    df = parse_binance_zip(
                        content
                    )

                    if not df.empty:
                        parts.append(df)

                day += pd.Timedelta(
                    days=1
                )

        current = month_end

    if not parts:

        raise RuntimeError(
            "No Binance data downloaded."
        )

    data = pd.concat(
        parts,
        ignore_index=True,
    )

    data = data.drop_duplicates(
        subset=["datetime"]
    )

    data = data.sort_values(
        "datetime"
    ).reset_index(
        drop=True
    )

    data = data[
        (
            data["datetime"]
            >= start
        )
        &
        (
            data["datetime"]
            < end
        )
    ].copy()

    if data.empty:

        raise RuntimeError(
            "No data remains after "
            "date filtering."
        )

    print()
    print("=" * 70)
    print("DATA COVERAGE")
    print("=" * 70)

    print(
        "Rows:",
        f"{len(data):,}",
    )

    print(
        "First candle:",
        data["datetime"].iloc[0],
    )

    print(
        "Last candle:",
        data["datetime"].iloc[-1],
    )

    expected_days = (
        end - start
    ).total_seconds() / 86400

    actual_days = (
        data["datetime"].iloc[-1]
        - data["datetime"].iloc[0]
    ).total_seconds() / 86400

    coverage = (
        actual_days
        / expected_days
        * 100
    )

    print(
        "Approx coverage:",
        f"{coverage:.2f}%",
    )

    return data


# ============================================================
# ICHIMOKU
# ============================================================

def calculate_ichimoku(df):

    high = df["high"]
    low = df["low"]

    tenkan_high = (
        high
        .rolling(
            ICHIMOKU_TENKAN
        )
        .max()
    )

    tenkan_low = (
        low
        .rolling(
            ICHIMOKU_TENKAN
        )
        .min()
    )

    kijun_high = (
        high
        .rolling(
            ICHIMOKU_KIJUN
        )
        .max()
    )

    kijun_low = (
        low
        .rolling(
            ICHIMOKU_KIJUN
        )
        .min()
    )

    senkou_b_high = (
        high
        .rolling(
            ICHIMOKU_SENKOU_B
        )
        .max()
    )

    senkou_b_low = (
        low
        .rolling(
            ICHIMOKU_SENKOU_B
        )
        .min()
    )

    df["tenkan"] = (
        tenkan_high
        + tenkan_low
    ) / 2

    df["kijun"] = (
        kijun_high
        + kijun_low
    ) / 2

    senkou_a_raw = (
        df["tenkan"]
        + df["kijun"]
    ) / 2

    senkou_b_raw = (
        senkou_b_high
        + senkou_b_low
    ) / 2

    # Visible cloud
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

    return df


# ============================================================
# PIVOT CALCULATION
# ============================================================

def calculate_pivots(df):

    n = len(df)

    confirmed_high = np.full(
        n,
        np.nan,
    )

    confirmed_low = np.full(
        n,
        np.nan,
    )

    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        center_high = (
            df["high"].iloc[i]
        )

        left_high = (
            df["high"]
            .iloc[
                i - PIVOT_LEFT:i
            ]
        )

        right_high = (
            df["high"]
            .iloc[
                i + 1:
                i + PIVOT_RIGHT + 1
            ]
        )

        if (
            center_high
            > left_high.max()
            and
            center_high
            > right_high.max()
        ):

            confirmation_index = (
                i + PIVOT_RIGHT
            )

            confirmed_high[
                confirmation_index
            ] = center_high

        center_low = (
            df["low"].iloc[i]
        )

        left_low = (
            df["low"]
            .iloc[
                i - PIVOT_LEFT:i
            ]
        )

        right_low = (
            df["low"]
            .iloc[
                i + 1:
                i + PIVOT_RIGHT + 1
            ]
        )

        if (
            center_low
            < left_low.min()
            and
            center_low
            < right_low.min()
        ):

            confirmation_index = (
                i + PIVOT_RIGHT
            )

            confirmed_low[
                confirmation_index
            ] = center_low

    df[
        "confirmed_pivot_high"
    ] = confirmed_high

    df[
        "confirmed_pivot_low"
    ] = confirmed_low

    return df


# ============================================================
# TENKAN / KIJUN CROSS
# ============================================================

def long_tenkan_kijun_cross(
    df,
    i,
):

    if i < 1:
        return False

    prev_t = df[
        "tenkan"
    ].iloc[i - 1]

    prev_k = df[
        "kijun"
    ].iloc[i - 1]

    curr_t = df[
        "tenkan"
    ].iloc[i]

    curr_k = df[
        "kijun"
    ].iloc[i]

    if any(
        pd.isna(x)
        for x in [
            prev_t,
            prev_k,
            curr_t,
            curr_k,
        ]
    ):
        return False

    return (
        prev_t <= prev_k
        and curr_t > curr_k
    )


def short_tenkan_kijun_cross(
    df,
    i,
):

    if i < 1:
        return False

    prev_t = df[
        "tenkan"
    ].iloc[i - 1]

    prev_k = df[
        "kijun"
    ].iloc[i - 1]

    curr_t = df[
        "tenkan"
    ].iloc[i]

    curr_k = df[
        "kijun"
    ].iloc[i]

    if any(
        pd.isna(x)
        for x in [
            prev_t,
            prev_k,
            curr_t,
            curr_k,
        ]
    ):
        return False

    return (
        prev_t >= prev_k
        and curr_t < curr_k
    )


# ============================================================
# CHIKOU CROSS
# ============================================================

def long_chikou_cross(
    df,
    i,
):

    shift = (
        ICHIMOKU_DISPLACEMENT
    )

    if i < shift + 1:
        return False

    current_close = (
        df["close"].iloc[i]
    )

    previous_close = (
        df["close"].iloc[i - 1]
    )

    price_26 = (
        df["close"]
        .iloc[i - shift]
    )

    price_27 = (
        df["close"]
        .iloc[i - shift - 1]
    )

    if any(
        pd.isna(x)
        for x in [
            current_close,
            previous_close,
            price_26,
            price_27,
        ]
    ):
        return False

    return (
        previous_close <= price_27
        and
        current_close > price_26
    )


def short_chikou_cross(
    df,
    i,
):

    shift = (
        ICHIMOKU_DISPLACEMENT
    )

    if i < shift + 1:
        return False

    current_close = (
        df["close"].iloc[i]
    )

    previous_close = (
        df["close"].iloc[i - 1]
    )

    price_26 = (
        df["close"]
        .iloc[i - shift]
    )

    price_27 = (
        df["close"]
        .iloc[i - shift - 1]
    )

    if any(
        pd.isna(x)
        for x in [
            current_close,
            previous_close,
            price_26,
            price_27,
        ]
    ):
        return False

    return (
        previous_close >= price_27
        and
        current_close < price_26
    )


# ============================================================
# ENTRY SIGNALS
# ============================================================

def long_entry_signal(
    df,
    i,
):

    if i < 1:
        return False

    price = df[
        "close"
    ].iloc[i]

    senkou_a = df[
        "senkou_a"
    ].iloc[i]

    senkou_b = df[
        "senkou_b"
    ].iloc[i]

    if any(
        pd.isna(x)
        for x in [
            price,
            senkou_a,
            senkou_b,
        ]
    ):
        return False

    cloud_top = max(
        senkou_a,
        senkou_b,
    )

    price_above_cloud = (
        price > cloud_top
    )

    bullish_cloud = (
        senkou_a > senkou_b
    )

    return (
        long_tenkan_kijun_cross(
            df,
            i,
        )
        and
        long_chikou_cross(
            df,
            i,
        )
        and
        price_above_cloud
        and
        bullish_cloud
    )


def short_entry_signal(
    df,
    i,
):

    if i < 1:
        return False

    price = df[
        "close"
    ].iloc[i]

    senkou_a = df[
        "senkou_a"
    ].iloc[i]

    senkou_b = df[
        "senkou_b"
    ].iloc[i]

    if any(
        pd.isna(x)
        for x in [
            price,
            senkou_a,
            senkou_b,
        ]
    ):
        return False

    cloud_bottom = min(
        senkou_a,
        senkou_b,
    )

    price_below_cloud = (
        price < cloud_bottom
    )

    bearish_cloud = (
        senkou_a < senkou_b
    )

    return (
        short_tenkan_kijun_cross(
            df,
            i,
        )
        and
        short_chikou_cross(
            df,
            i,
        )
        and
        price_below_cloud
        and
        bearish_cloud
    )


# ============================================================
# PIVOT EXIT LOGIC
# ============================================================
#
# IMPORTANT CHANGE:
#
# LONG SL:
#   highest confirmed pivot low below entry
#   = closest valid SL by PRICE
#
# SHORT SL:
#   lowest confirmed pivot high above entry
#   = closest valid SL by PRICE
#
# TP:
#   nearest valid pivot by PRICE
#
# ============================================================

def get_long_sl(
    df,
    i,
    entry,
):

    values = (
        df[
            "confirmed_pivot_low"
        ]
        .iloc[:i + 1]
        .dropna()
    )

    values = values[
        values < entry
    ]

    if values.empty:
        return None

    # Closest pivot low below entry
    return float(
        values.max()
    )


def get_short_sl(
    df,
    i,
    entry,
):

    values = (
        df[
            "confirmed_pivot_high"
        ]
        .iloc[:i + 1]
        .dropna()
    )

    values = values[
        values > entry
    ]

    if values.empty:
        return None

    # Closest pivot high above entry
    return float(
        values.min()
    )


def get_long_tp(
    df,
    i,
    entry,
):

    values = (
        df[
            "confirmed_pivot_high"
        ]
        .iloc[:i + 1]
        .dropna()
    )

    values = values[
        values > entry
    ]

    if values.empty:
        return None

    # Closest pivot high above entry
    return float(
        values.min()
    )


def get_short_tp(
    df,
    i,
    entry,
):

    values = (
        df[
            "confirmed_pivot_low"
        ]
        .iloc[:i + 1]
        .dropna()
    )

    values = values[
        values < entry
    ]

    if values.empty:
        return None

    # Closest pivot low below entry
    return float(
        values.max()
    )


# ============================================================
# RR
# ============================================================

def calculate_rr(
    side,
    entry,
    sl,
    tp,
):

    if side == "LONG":

        risk = (
            entry - sl
        )

        reward = (
            tp - entry
        )

    else:

        risk = (
            sl - entry
        )

        reward = (
            entry - tp
        )

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    return (
        reward / risk
    )


# ============================================================
# SL / TP PERCENTAGES
# ============================================================

def calculate_sl_pct(
    side,
    entry,
    sl,
):

    if side == "LONG":

        return (
            (entry - sl)
            / entry
            * 100
        )

    return (
        (sl - entry)
        / entry
        * 100
    )


def calculate_tp_pct(
    side,
    entry,
    tp,
):

    if side == "LONG":

        return (
            (tp - entry)
            / entry
            * 100
        )

    return (
        (entry - tp)
        / entry
        * 100
    )


# ============================================================
# RR BUCKET
# ============================================================

def rr_bucket(rr):

    if rr is None:
        return "INVALID"

    if rr < 0.50:
        return "<0.50"

    if rr < 1.00:
        return "0.50-0.99"

    if rr < 1.50:
        return "1.00-1.49"

    if rr < 2.00:
        return "1.50-1.99"

    return "2.00+"


# ============================================================
# SLIPPAGE
# ============================================================

def apply_entry_slippage(
    price,
    side,
):

    if side == "LONG":

        return (
            price
            * (1 + SLIPPAGE_RATE)
        )

    return (
        price
        * (1 - SLIPPAGE_RATE)
    )


def apply_exit_slippage(
    price,
    side,
):

    if side == "LONG":

        return (
            price
            * (1 - SLIPPAGE_RATE)
        )

    return (
        price
        * (1 + SLIPPAGE_RATE)
    )


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(
    side,
    entry,
    exit_price,
    capital,
):

    if side == "LONG":

        gross_return = (
            exit_price / entry
        ) - 1

    else:

        gross_return = (
            entry / exit_price
        ) - 1

    gross_pnl = (
        capital
        * gross_return
    )

    entry_fee = (
        capital
        * FEE_RATE
    )

    equity_before_exit_fee = (
        capital
        + gross_pnl
    )

    exit_fee = (
        abs(
            equity_before_exit_fee
        )
        * FEE_RATE
    )

    return (
        gross_pnl
        - entry_fee
        - exit_fee
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df):

    capital = (
        INITIAL_CAPITAL
    )

    peak_capital = capital
    max_drawdown = 0.0

    position = None

    trades = []
    diagnostics = []

    raw_signals = 0

    long_signals = 0
    short_signals = 0

    no_valid_sl = 0
    no_valid_tp = 0
    tp_too_far = 0
    rr_rejected = 0
    accepted_signals = 0

    for i in range(
        ICHIMOKU_SENKOU_B
        + ICHIMOKU_DISPLACEMENT
        + 5,
        len(df),
    ):

        candle = df.iloc[i]

        # ====================================================
        # MANAGE OPEN POSITION
        # ====================================================

        if position is not None:

            side = (
                position["side"]
            )

            sl = (
                position["sl"]
            )

            tp = (
                position["tp"]
            )

            high = candle[
                "high"
            ]

            low = candle[
                "low"
            ]

            exit_price = None
            exit_reason = None

            if side == "LONG":

                sl_hit = (
                    low <= sl
                )

                tp_hit = (
                    high >= tp
                )

                # SL assumed first
                # if both hit same candle.
                if sl_hit:

                    exit_price = sl
                    exit_reason = "SL"

                elif tp_hit:

                    exit_price = tp
                    exit_reason = "TP"

            else:

                sl_hit = (
                    high >= sl
                )

                tp_hit = (
                    low <= tp
                )

                if sl_hit:

                    exit_price = sl
                    exit_reason = "SL"

                elif tp_hit:

                    exit_price = tp
                    exit_reason = "TP"

            if exit_price is not None:

                actual_exit = (
                    apply_exit_slippage(
                        exit_price,
                        side,
                    )
                )

                capital_before = (
                    capital
                )

                pnl = (
                    calculate_trade_pnl(
                        side,
                        position["entry"],
                        actual_exit,
                        capital,
                    )
                )

                capital += pnl

                if capital < 0:
                    capital = 0

                peak_capital = max(
                    peak_capital,
                    capital,
                )

                drawdown = (
                    (
                        capital
                        - peak_capital
                    )
                    / peak_capital
                    * 100
                )

                max_drawdown = min(
                    max_drawdown,
                    drawdown,
                )

                result = (
                    "WIN"
                    if pnl > 0
                    else "LOSS"
                )

                trades.append(
                    {
                        "entry_time":
                            position[
                                "entry_time"
                            ],

                        "exit_time":
                            candle[
                                "datetime"
                            ],

                        "side":
                            side,

                        "entry":
                            position[
                                "entry"
                            ],

                        "sl":
                            position[
                                "sl"
                            ],

                        "tp":
                            position[
                                "tp"
                            ],

                        "exit":
                            actual_exit,

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

                        "exit_reason":
                            exit_reason,

                        "result":
                            result,

                        "pnl":
                            pnl,

                        "pnl_pct":
                            (
                                pnl
                                / capital_before
                                * 100
                            ),

                        "capital":
                            capital,
                    }
                )

                position = None

            # Do not open another position
            # on the same candle.
            continue

        # ====================================================
        # ENTRY
        # ====================================================

        long_signal = (
            long_entry_signal(
                df,
                i,
            )
        )

        short_signal = (
            short_entry_signal(
                df,
                i,
            )
        )

        if not (
            long_signal
            or short_signal
        ):
            continue

        raw_signals += 1

        if long_signal:

            side = "LONG"
            long_signals += 1

        else:

            side = "SHORT"
            short_signals += 1

        raw_entry = float(
            candle["close"]
        )

        entry = (
            apply_entry_slippage(
                raw_entry,
                side,
            )
        )

        # ====================================================
        # EXIT LEVELS
        # ====================================================

        if side == "LONG":

            sl = get_long_sl(
                df,
                i,
                entry,
            )

            tp = get_long_tp(
                df,
                i,
                entry,
            )

        else:

            sl = get_short_sl(
                df,
                i,
                entry,
            )

            tp = get_short_tp(
                df,
                i,
                entry,
            )

        diagnostic = {
            "signal_time":
                candle["datetime"],

            "side":
                side,

            "entry":
                entry,

            "sl":
                sl,

            "tp":
                tp,

            "sl_pct":
                None,

            "tp_pct":
                None,

            "rr":
                None,

            "rr_bucket":
                None,

            "tp_distance_ok":
                False,

            "rr_ok":
                False,

            "accepted":
                False,

            "rejection_reason":
                "",
        }

        # ====================================================
        # NO SL
        # ====================================================

        if sl is None:

            no_valid_sl += 1

            diagnostic[
                "rejection_reason"
            ] = "NO_VALID_SL"

            diagnostics.append(
                diagnostic
            )

            continue

        # ====================================================
        # NO TP
        # ====================================================

        if tp is None:

            no_valid_tp += 1

            diagnostic[
                "sl_pct"
            ] = calculate_sl_pct(
                side,
                entry,
                sl,
            )

            diagnostic[
                "rejection_reason"
            ] = "NO_VALID_TP"

            diagnostics.append(
                diagnostic
            )

            continue

        # ====================================================
        # DISTANCES
        # ====================================================

        sl_percentage = (
            calculate_sl_pct(
                side,
                entry,
                sl,
            )
        )

        tp_percentage = (
            calculate_tp_pct(
                side,
                entry,
                tp,
            )
        )

        rr = calculate_rr(
            side,
            entry,
            sl,
            tp,
        )

        diagnostic[
            "sl_pct"
        ] = sl_percentage

        diagnostic[
            "tp_pct"
        ] = tp_percentage

        diagnostic[
            "rr"
        ] = rr

        diagnostic[
            "rr_bucket"
        ] = rr_bucket(rr)

        # ====================================================
        # TP DISTANCE
        # ====================================================

        tp_distance_ok = (
            tp_percentage
            <= MAX_TP_DISTANCE_PCT
        )

        diagnostic[
            "tp_distance_ok"
        ] = tp_distance_ok

        if not tp_distance_ok:

            tp_too_far += 1

            diagnostic[
                "rejection_reason"
            ] = "TP_TOO_FAR"

            diagnostics.append(
                diagnostic
            )

            continue

        # ====================================================
        # RR
        # ====================================================

        rr_ok = (
            rr is not None
            and rr > MIN_RR
        )

        diagnostic[
            "rr_ok"
        ] = rr_ok

        if not rr_ok:

            rr_rejected += 1

            diagnostic[
                "rejection_reason"
            ] = "RR_LE_1"

            diagnostics.append(
                diagnostic
            )

            continue

        # ====================================================
        # ACCEPT
        # ====================================================

        accepted_signals += 1

        diagnostic[
            "accepted"
        ] = True

        diagnostic[
            "rejection_reason"
        ] = "ACCEPTED"

        diagnostics.append(
            diagnostic
        )

        position = {
            "side":
                side,

            "entry_time":
                candle["datetime"],

            "entry":
                entry,

            "sl":
                sl,

            "tp":
                tp,

            "sl_pct":
                sl_percentage,

            "tp_pct":
                tp_percentage,

            "rr":
                rr,
        }

    # ========================================================
    # DIAGNOSTICS CSV
    # ========================================================

    diagnostics_df = pd.DataFrame(
        diagnostics
    )

    diagnostics_df.to_csv(
        RR_DIAGNOSTICS_FILE,
        index=False,
    )

    # ========================================================
    # RR DISTRIBUTION
    # ========================================================

    distribution = []

    bucket_order = [
        "<0.50",
        "0.50-0.99",
        "1.00-1.49",
        "1.50-1.99",
        "2.00+",
        "INVALID",
    ]

    total = len(
        diagnostics_df
    )

    for bucket in bucket_order:

        count = 0

        if total > 0:

            count = int(
                (
                    diagnostics_df[
                        "rr_bucket"
                    ]
                    == bucket
                ).sum()
            )

        percentage = (
            count / total * 100
            if total > 0
            else 0
        )

        distribution.append(
            {
                "rr_bucket":
                    bucket,

                "signals":
                    count,

                "percentage":
                    percentage,
            }
        )

    distribution_df = pd.DataFrame(
        distribution
    )

    distribution_df.to_csv(
        RR_DISTRIBUTION_FILE,
        index=False,
    )

    # ========================================================
    # TRADES CSV
    # ========================================================

    trades_df = pd.DataFrame(
        trades
    )

    if trades_df.empty:

        trades_df = pd.DataFrame(
            columns=[
                "entry_time",
                "exit_time",
                "side",
                "entry",
                "sl",
                "tp",
                "exit",
                "sl_pct",
                "tp_pct",
                "rr",
                "exit_reason",
                "result",
                "pnl",
                "pnl_pct",
                "capital",
            ]
        )

    trades_df.to_csv(
        TRADES_FILE,
        index=False,
    )

    # ========================================================
    # STATISTICS
    # ========================================================

    completed = len(
        trades_df
    )

    if completed > 0:

        wins = int(
            (
                trades_df["result"]
                == "WIN"
            ).sum()
        )

        losses = int(
            (
                trades_df["result"]
                == "LOSS"
            ).sum()
        )

        win_rate = (
            wins
            / completed
            * 100
        )

        gross_profit = (
            trades_df.loc[
                trades_df["pnl"] > 0,
                "pnl",
            ].sum()
        )

        gross_loss = abs(
            trades_df.loc[
                trades_df["pnl"] < 0,
                "pnl",
            ].sum()
        )

        if gross_loss > 0:

            profit_factor = (
                gross_profit
                / gross_loss
            )

        else:

            profit_factor = np.inf

        net_pnl = (
            trades_df["pnl"].sum()
        )

        average_rr = (
            trades_df["rr"].mean()
        )

    else:

        wins = 0
        losses = 0
        win_rate = 0.0
        profit_factor = 0.0
        net_pnl = 0.0
        average_rr = 0.0

    final_capital = capital

    net_pnl_pct = (
        (
            final_capital
            / INITIAL_CAPITAL
        )
        - 1
    ) * 100

    # ========================================================
    # SUMMARY
    # ========================================================

    summary = pd.DataFrame(
        [
            {
                "initial_capital":
                    INITIAL_CAPITAL,

                "final_capital":
                    final_capital,

                "raw_signals":
                    raw_signals,

                "long_signals":
                    long_signals,

                "short_signals":
                    short_signals,

                "no_valid_sl":
                    no_valid_sl,

                "no_valid_tp":
                    no_valid_tp,

                "tp_too_far":
                    tp_too_far,

                "rr_le_1":
                    rr_rejected,

                "accepted_signals":
                    accepted_signals,

                "completed_trades":
                    completed,

                "wins":
                    wins,

                "losses":
                    losses,

                "win_rate_pct":
                    win_rate,

                "profit_factor":
                    profit_factor,

                "net_pnl":
                    net_pnl,

                "net_pnl_pct":
                    net_pnl_pct,

                "max_drawdown_pct":
                    max_drawdown,

                "average_rr":
                    average_rr,

                "min_rr":
                    MIN_RR,

                "max_tp_distance_pct":
                    MAX_TP_DISTANCE_PCT,

                "fee_per_side_pct":
                    FEE_RATE * 100,

                "slippage_per_side_pct":
                    SLIPPAGE_RATE * 100,

                "sl_rule":
                    "NEAREST_PIVOT_BY_PRICE",

                "tp_rule":
                    "NEAREST_PIVOT_BY_PRICE",
            }
        ]
    )

    summary.to_csv(
        SUMMARY_FILE,
        index=False,
    )

    # ========================================================
    # CONSOLE
    # ========================================================

    print()
    print("=" * 70)
    print(
        "SUI ICHIMOKU + PIVOT BACKTEST v2"
    )
    print("=" * 70)

    print(
        f"Initial Capital : "
        f"${INITIAL_CAPITAL:,.2f}"
    )

    print(
        f"Final Capital   : "
        f"${final_capital:,.2f}"
    )

    print(
        f"Raw Signals     : "
        f"{raw_signals}"
    )

    print(
        f"Long Signals    : "
        f"{long_signals}"
    )

    print(
        f"Short Signals   : "
        f"{short_signals}"
    )

    print(
        f"No Valid SL     : "
        f"{no_valid_sl}"
    )

    print(
        f"No Valid TP     : "
        f"{no_valid_tp}"
    )

    print(
        f"TP Too Far      : "
        f"{tp_too_far}"
    )

    print(
        f"RR <= 1         : "
        f"{rr_rejected}"
    )

    print(
        f"Accepted        : "
        f"{accepted_signals}"
    )

    print(
        f"Completed Trades: "
        f"{completed}"
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

    if np.isfinite(
        profit_factor
    ):

        print(
            f"Profit Factor   : "
            f"{profit_factor:.3f}"
        )

    else:

        print(
            "Profit Factor   : INF"
        )

    print(
        f"Net PnL         : "
        f"${net_pnl:,.2f}"
    )

    print(
        f"Net PnL %       : "
        f"{net_pnl_pct:.2f}%"
    )

    print(
        f"Max Drawdown    : "
        f"{max_drawdown:.2f}%"
    )

    print(
        f"Average RR      : "
        f"{average_rr:.3f}"
    )

    # ========================================================
    # RR DISTRIBUTION
    # ========================================================

    print()
    print("=" * 70)
    print(
        "RR DISTRIBUTION OF RAW SIGNALS"
    )
    print("=" * 70)

    for _, row in (
        distribution_df.iterrows()
    ):

        print(
            f"{row['rr_bucket']:>10} : "
            f"{int(row['signals']):>4} signals "
            f"({row['percentage']:.2f}%)"
        )

    # ========================================================
    # OUTPUT FILES
    # ========================================================

    print()
    print("=" * 70)
    print("OUTPUT FILES")
    print("=" * 70)

    print(
        TRADES_FILE
    )

    print(
        SUMMARY_FILE
    )

    print(
        RR_DIAGNOSTICS_FILE
    )

    print(
        RR_DISTRIBUTION_FILE
    )

    print("=" * 70)


# ============================================================
# MAIN
# ============================================================

def main():

    df = load_data()

    df = calculate_ichimoku(
        df
    )

    df = calculate_pivots(
        df
    )

    run_backtest(
        df
    )


if __name__ == "__main__":

    main()
