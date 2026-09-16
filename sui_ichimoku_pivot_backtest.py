# ============================================================
# SUI/USDT 5M
# ICHIMOKU + PIVOT BACKTEST
# ============================================================
#
# Strategy:
#
# LONG
# 1) Tenkan crosses above Kijun
# 2) Chikou crosses above price
# 3) Price above cloud
# 4) Cloud bullish
# 5) SL = latest confirmed pivot low below entry
# 6) TP = nearest confirmed pivot high above entry
# 7) RR must be > 1
#
# SHORT = inverse
#
# Pivot:
#   5 candles LEFT + 5 candles RIGHT
#   Pivot becomes usable only after right 5 candles close.
#
# No look-ahead.
# Closed candles only.
# One position at a time.
#
# Fees:
#   0.04% per side
#
# Slippage:
#   0.01% per entry/exit
#
# Initial capital:
#   $1000
#
# Compounding:
#   YES
#
# Additional diagnostics:
#   Every raw signal is saved with:
#   Entry / SL / TP / SL% / TP% / RR / rejection reason
#
# ============================================================

import io
import os
import time
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

ICHIMOKU_TENKAN = 9
ICHIMOKU_KIJUN = 26
ICHIMOKU_SENKOU_B = 52
ICHIMOKU_DISPLACEMENT = 26

PIVOT_LEFT = 5
PIVOT_RIGHT = 5

MIN_RR = 1.0

# Keep current TP-distance restriction
MAX_TP_DISTANCE_PCT = 3.0

START_DATE = "2025-09-16"
END_DATE = "2026-09-16"

TRADES_FILE = "sui_ichimoku_pivot_trades.csv"
SUMMARY_FILE = "sui_ichimoku_pivot_summary.csv"

RR_DIAGNOSTICS_FILE = "sui_ichimoku_pivot_rr_diagnostics.csv"
RR_DISTRIBUTION_FILE = "sui_ichimoku_pivot_rr_distribution.csv"


# ============================================================
# BINANCE DATA
# ============================================================

BINANCE_MONTHLY_URL = (
    "https://data.binance.vision/data/futures/um/monthly/"
    "klines/{symbol}/{interval}/{symbol}-{interval}-{year}-{month:02d}.zip"
)

BINANCE_DAILY_URL = (
    "https://data.binance.vision/data/futures/um/daily/"
    "klines/{symbol}/{interval}/{symbol}-{interval}-{date}.zip"
)


# ============================================================
# DOWNLOAD
# ============================================================

def download_bytes(url):
    try:
        r = requests.get(
            url,
            timeout=60,
            headers={"User-Agent": "Mozilla/5.0"},
        )

        if r.status_code == 200 and len(r.content) > 100:
            return r.content

    except Exception as e:
        print(f"Download error: {url}")
        print(e)

    return None


# ============================================================
# PARSE BINANCE CSV
# ============================================================

def parse_binance_zip(content):
    try:
        z = pd.read_csv(
            io.BytesIO(content),
            compression="zip",
            header=None,
        )
    except Exception as e:
        print("CSV read error:", e)
        return pd.DataFrame()

    if z.empty:
        return pd.DataFrame()

    # Binance kline columns
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

    # Keep only expected columns
    z = z.iloc[:, :len(columns)]
    z.columns = columns[:z.shape[1]]

    # Remove accidental header row
    z = z[
        z["open_time"].astype(str).str.lower() != "open_time"
    ].copy()

    # Numeric conversion
    numeric_cols = [
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

    for col in numeric_cols:
        if col in z.columns:
            z[col] = pd.to_numeric(
                z[col],
                errors="coerce",
            )

    z = z.dropna(
        subset=[
            "open_time",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    if z.empty:
        return pd.DataFrame()

    z["datetime"] = pd.to_datetime(
        z["open_time"],
        unit="ms",
        utc=True,
        errors="coerce",
    )

    z = z.dropna(subset=["datetime"])

    return z[
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
# LOAD ONE YEAR OF DATA
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

    while current < end:

        year = current.year
        month = current.month

        month_start = current
        month_end = (
            current + pd.offsets.MonthBegin(1)
        )

        # Do not use monthly archive for current month.
        now = pd.Timestamp.now(tz="UTC")

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
                f"Monthly: {year}-{month:02d}"
            )

            content = download_bytes(url)

            if content is not None:
                df = parse_binance_zip(content)

                if not df.empty:
                    parts.append(df)

        else:

            print(
                f"Current month -> daily files: "
                f"{year}-{month:02d}"
            )

            day = month_start

            while day < month_end and day < end:

                date_str = day.strftime("%Y-%m-%d")

                url = BINANCE_DAILY_URL.format(
                    symbol=SYMBOL,
                    interval=INTERVAL,
                    date=date_str,
                )

                content = download_bytes(url)

                if content is not None:
                    df = parse_binance_zip(content)

                    if not df.empty:
                        parts.append(df)

                day += pd.Timedelta(days=1)

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
    ).reset_index(drop=True)

    data = data[
        (data["datetime"] >= start)
        & (data["datetime"] < end)
    ].copy()

    if data.empty:
        raise RuntimeError(
            "No data remains after date filtering."
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
        actual_days / expected_days * 100
        if expected_days > 0
        else 0
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
    close = df["close"]

    tenkan_high = (
        high.rolling(
            ICHIMOKU_TENKAN
        ).max()
    )

    tenkan_low = (
        low.rolling(
            ICHIMOKU_TENKAN
        ).min()
    )

    kijun_high = (
        high.rolling(
            ICHIMOKU_KIJUN
        ).max()
    )

    kijun_low = (
        low.rolling(
            ICHIMOKU_KIJUN
        ).min()
    )

    senkou_b_high = (
        high.rolling(
            ICHIMOKU_SENKOU_B
        ).max()
    )

    senkou_b_low = (
        low.rolling(
            ICHIMOKU_SENKOU_B
        ).min()
    )

    df["tenkan"] = (
        tenkan_high + tenkan_low
    ) / 2.0

    df["kijun"] = (
        kijun_high + kijun_low
    ) / 2.0

    senkou_a_raw = (
        df["tenkan"] + df["kijun"]
    ) / 2.0

    senkou_b_raw = (
        senkou_b_high + senkou_b_low
    ) / 2.0

    # Visible cloud at current candle.
    # Senkou values are plotted 26 candles forward,
    # therefore current visible cloud comes from
    # values calculated 26 candles earlier.
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

    # Chikou comparison:
    # Current close versus close 26 candles ago.
    df["chikou_price"] = (
        close.shift(
            ICHIMOKU_DISPLACEMENT
        )
    )

    return df


# ============================================================
# PIVOTS
# ============================================================

def calculate_pivots(df):

    n = len(df)

    pivot_high_confirmed = np.full(
        n,
        np.nan,
    )

    pivot_low_confirmed = np.full(
        n,
        np.nan,
    )

    # A pivot at i is confirmed only at i + PIVOT_RIGHT.
    for i in range(
        PIVOT_LEFT,
        n - PIVOT_RIGHT,
    ):

        left_highs = df["high"].iloc[
            i - PIVOT_LEFT:i
        ]

        right_highs = df["high"].iloc[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        center_high = df["high"].iloc[i]

        is_pivot_high = (
            center_high > left_highs.max()
            and
            center_high > right_highs.max()
        )

        if is_pivot_high:

            confirmation_index = (
                i + PIVOT_RIGHT
            )

            pivot_high_confirmed[
                confirmation_index
            ] = center_high

        left_lows = df["low"].iloc[
            i - PIVOT_LEFT:i
        ]

        right_lows = df["low"].iloc[
            i + 1:i + PIVOT_RIGHT + 1
        ]

        center_low = df["low"].iloc[i]

        is_pivot_low = (
            center_low < left_lows.min()
            and
            center_low < right_lows.min()
        )

        if is_pivot_low:

            confirmation_index = (
                i + PIVOT_RIGHT
            )

            pivot_low_confirmed[
                confirmation_index
            ] = center_low

    df["confirmed_pivot_high"] = (
        pivot_high_confirmed
    )

    df["confirmed_pivot_low"] = (
        pivot_low_confirmed
    )

    return df


# ============================================================
# CROSS CONDITIONS
# ============================================================

def long_tenkan_kijun_cross(df, i):

    if i < 1:
        return False

    prev_t = df["tenkan"].iloc[i - 1]
    prev_k = df["kijun"].iloc[i - 1]

    curr_t = df["tenkan"].iloc[i]
    curr_k = df["kijun"].iloc[i]

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


def short_tenkan_kijun_cross(df, i):

    if i < 1:
        return False

    prev_t = df["tenkan"].iloc[i - 1]
    prev_k = df["kijun"].iloc[i - 1]

    curr_t = df["tenkan"].iloc[i]
    curr_k = df["kijun"].iloc[i]

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

def long_chikou_cross(df, i):

    shift = ICHIMOKU_DISPLACEMENT

    if i < shift + 1:
        return False

    current_close = (
        df["close"].iloc[i]
    )

    previous_close = (
        df["close"].iloc[i - 1]
    )

    price_26 = (
        df["close"].iloc[i - shift]
    )

    price_27 = (
        df["close"].iloc[i - shift - 1]
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


def short_chikou_cross(df, i):

    shift = ICHIMOKU_DISPLACEMENT

    if i < shift + 1:
        return False

    current_close = (
        df["close"].iloc[i]
    )

    previous_close = (
        df["close"].iloc[i - 1]
    )

    price_26 = (
        df["close"].iloc[i - shift]
    )

    price_27 = (
        df["close"].iloc[i - shift - 1]
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
# ENTRY CONDITIONS
# ============================================================

def long_entry_signal(df, i):

    if i < 1:
        return False

    price = df["close"].iloc[i]

    sa = df["senkou_a"].iloc[i]
    sb = df["senkou_b"].iloc[i]

    if any(
        pd.isna(x)
        for x in [
            price,
            sa,
            sb,
        ]
    ):
        return False

    cloud_top = max(sa, sb)
    cloud_bottom = min(sa, sb)

    price_above_cloud = (
        price > cloud_top
    )

    bullish_cloud = (
        sa > sb
    )

    tenkan_cross = (
        long_tenkan_kijun_cross(
            df,
            i,
        )
    )

    chikou_cross = (
        long_chikou_cross(
            df,
            i,
        )
    )

    return (
        tenkan_cross
        and
        chikou_cross
        and
        price_above_cloud
        and
        bullish_cloud
    )


def short_entry_signal(df, i):

    if i < 1:
        return False

    price = df["close"].iloc[i]

    sa = df["senkou_a"].iloc[i]
    sb = df["senkou_b"].iloc[i]

    if any(
        pd.isna(x)
        for x in [
            price,
            sa,
            sb,
        ]
    ):
        return False

    cloud_top = max(sa, sb)
    cloud_bottom = min(sa, sb)

    price_below_cloud = (
        price < cloud_bottom
    )

    bearish_cloud = (
        sa < sb
    )

    tenkan_cross = (
        short_tenkan_kijun_cross(
            df,
            i,
        )
    )

    chikou_cross = (
        short_chikou_cross(
            df,
            i,
        )
    )

    return (
        tenkan_cross
        and
        chikou_cross
        and
        price_below_cloud
        and
        bearish_cloud
    )


# ============================================================
# PIVOT SL / TP
# ============================================================

def get_long_sl(df, i, entry):

    values = df[
        "confirmed_pivot_low"
    ].iloc[:i + 1]

    values = values.dropna()

    values = values[
        values < entry
    ]

    if values.empty:
        return None

    # Latest confirmed pivot low
    return float(values.iloc[-1])


def get_short_sl(df, i, entry):

    values = df[
        "confirmed_pivot_high"
    ].iloc[:i + 1]

    values = values.dropna()

    values = values[
        values > entry
    ]

    if values.empty:
        return None

    # Latest confirmed pivot high
    return float(values.iloc[-1])


def get_long_tp(df, i, entry):

    values = df[
        "confirmed_pivot_high"
    ].iloc[:i + 1]

    values = values.dropna()

    values = values[
        values > entry
    ]

    if values.empty:
        return None

    # IMPORTANT:
    # Nearest pivot by PRICE, not latest pivot by TIME.
    return float(values.min())


def get_short_tp(df, i, entry):

    values = df[
        "confirmed_pivot_low"
    ].iloc[:i + 1]

    values = values.dropna()

    values = values[
        values < entry
    ]

    if values.empty:
        return None

    # IMPORTANT:
    # Nearest pivot by PRICE, not latest pivot by TIME.
    return float(values.max())


# ============================================================
# RR CALCULATION
# ============================================================

def calculate_rr(side, entry, sl, tp):

    if side == "LONG":

        risk = entry - sl
        reward = tp - entry

    else:

        risk = sl - entry
        reward = entry - tp

    if risk <= 0:
        return None

    if reward <= 0:
        return None

    return reward / risk


def sl_pct(side, entry, sl):

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


def tp_pct(side, entry, tp):

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

    if rr < 0.5:
        return "<0.50"

    if rr < 1.0:
        return "0.50-0.99"

    if rr < 1.5:
        return "1.00-1.49"

    if rr < 2.0:
        return "1.50-1.99"

    return "2.00+"


# ============================================================
# EXECUTION PRICE
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
# TRADE PNL
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
        capital * gross_return
    )

    entry_fee = (
        capital * FEE_RATE
    )

    exit_equity_before_fee = (
        capital + gross_pnl
    )

    exit_fee = (
        abs(exit_equity_before_fee)
        * FEE_RATE
    )

    net_pnl = (
        gross_pnl
        - entry_fee
        - exit_fee
    )

    return net_pnl


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df):

    capital = INITIAL_CAPITAL
    peak_capital = capital

    position = None

    trades = []
    diagnostics = []

    raw_signals = 0

    no_sl = 0
    no_tp = 0
    tp_too_far = 0
    rr_rejected = 0
    accepted_signals = 0

    long_signals = 0
    short_signals = 0

    max_dd = 0.0

    n = len(df)

    print()
    print("=" * 70)
    print("RUNNING BACKTEST")
    print("=" * 70)

    for i in range(
        ICHIMOKU_SENKOU_B
        + ICHIMOKU_DISPLACEMENT
        + 5,
        n,
    ):

        candle = df.iloc[i]

        # ====================================================
        # MANAGE OPEN POSITION
        # ====================================================

        if position is not None:

            side = position["side"]

            sl = position["sl"]
            tp = position["tp"]

            high = candle["high"]
            low = candle["low"]

            exit_price = None
            exit_reason = None

            if side == "LONG":

                sl_hit = (
                    low <= sl
                )

                tp_hit = (
                    high >= tp
                )

                # If both are hit on same candle,
                # SL is assumed first.
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

                pnl = calculate_trade_pnl(
                    side,
                    position["entry"],
                    actual_exit,
                    capital,
                )

                capital_before = capital

                capital += pnl

                if capital < 0:
                    capital = 0

                if capital > peak_capital:
                    peak_capital = capital

                drawdown = (
                    (capital - peak_capital)
                    / peak_capital
                    * 100
                )

                max_dd = min(
                    max_dd,
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
                            position["entry_time"],

                        "exit_time":
                            candle["datetime"],

                        "side":
                            side,

                        "entry":
                            position["entry"],

                        "sl":
                            position["sl"],

                        "tp":
                            position["tp"],

                        "exit":
                            actual_exit,

                        "sl_pct":
                            position["sl_pct"],

                        "tp_pct":
                            position["tp_pct"],

                        "rr":
                            position["rr"],

                        "exit_reason":
                            exit_reason,

                        "result":
                            result,

                        "pnl":
                            pnl,

                        "pnl_pct":
                            pnl / capital_before * 100
                            if capital_before
                            else 0,

                        "capital":
                            capital,
                    }
                )

                position = None

            # Continue to next candle.
            # We do not open a new position
            # on the same candle after an exit.
            continue

        # ====================================================
        # CHECK ENTRY
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

        side = (
            "LONG"
            if long_signal
            else "SHORT"
        )

        if side == "LONG":
            long_signals += 1
        else:
            short_signals += 1

        raw_entry = float(
            candle["close"]
        )

        entry = apply_entry_slippage(
            raw_entry,
            side,
        )

        # ====================================================
        # GET SL / TP
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
        # SL VALIDATION
        # ====================================================

        if sl is None:

            no_sl += 1

            diagnostic[
                "rejection_reason"
            ] = "NO_VALID_SL"

            diagnostics.append(
                diagnostic
            )

            continue

        # ====================================================
        # TP VALIDATION
        # ====================================================

        if tp is None:

            no_tp += 1

            diagnostic[
                "sl_pct"
            ] = sl_pct(
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

        current_sl_pct = sl_pct(
            side,
            entry,
            sl,
        )

        current_tp_pct = tp_pct(
            side,
            entry,
            tp,
        )

        rr = calculate_rr(
            side,
            entry,
            sl,
            tp,
        )

        diagnostic[
            "sl_pct"
        ] = current_sl_pct

        diagnostic[
            "tp_pct"
        ] = current_tp_pct

        diagnostic[
            "rr"
        ] = rr

        diagnostic[
            "rr_bucket"
        ] = rr_bucket(rr)

        # ====================================================
        # TP DISTANCE FILTER
        # ====================================================

        tp_distance_ok = (
            current_tp_pct
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
        # RR FILTER
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
        # ACCEPT SIGNAL
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
                current_sl_pct,

            "tp_pct":
                current_tp_pct,

            "rr":
                rr,
        }

    # ========================================================
    # SAVE DIAGNOSTICS
    # ========================================================

    diagnostics_df = pd.DataFrame(
        diagnostics
    )

    if not diagnostics_df.empty:

        diagnostics_df.to_csv(
            RR_DIAGNOSTICS_FILE,
            index=False,
        )

    # ========================================================
    # RR DISTRIBUTION
    # ========================================================

    distribution_rows = []

    total_diag = len(
        diagnostics_df
    )

    if total_diag > 0:

        bucket_order = [
            "<0.50",
            "0.50-0.99",
            "1.00-1.49",
            "1.50-1.99",
            "2.00+",
            "INVALID",
        ]

        for bucket in bucket_order:

            subset = diagnostics_df[
                diagnostics_df[
                    "rr_bucket"
                ] == bucket
            ]

            count = len(subset)

            percentage = (
                count / total_diag * 100
            )

            distribution_rows.append(
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
        distribution_rows
    )

    distribution_df.to_csv(
        RR_DISTRIBUTION_FILE,
        index=False,
    )

    # ========================================================
    # TRADE DATAFRAME
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
            wins / completed * 100
        )

        gross_profit = trades_df.loc[
            trades_df["pnl"] > 0,
            "pnl",
        ].sum()

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

        avg_rr = (
            trades_df["rr"].mean()
        )

    else:

        wins = 0
        losses = 0
        win_rate = 0.0
        profit_factor = 0.0
        net_pnl = 0.0
        avg_rr = 0.0

    final_capital = capital

    net_pnl_pct = (
        (
            final_capital
            / INITIAL_CAPITAL
        ) - 1
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
                    no_sl,

                "no_valid_tp":
                    no_tp,

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
                    max_dd,

                "average_rr":
                    avg_rr,

                "min_rr":
                    MIN_RR,

                "max_tp_distance_pct":
                    MAX_TP_DISTANCE_PCT,

                "fee_per_side_pct":
                    FEE_RATE * 100,

                "slippage_per_side_pct":
                    SLIPPAGE_RATE * 100,
            }
        ]
    )

    summary.to_csv(
        SUMMARY_FILE,
        index=False,
    )

    # ========================================================
    # CONSOLE REPORT
    # ========================================================

    print()
    print("=" * 70)
    print("SUI ICHIMOKU + PIVOT BACKTEST")
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
        f"{no_sl}"
    )

    print(
        f"No Valid TP     : "
        f"{no_tp}"
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

    if np.isfinite(profit_factor):
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
        f"{max_dd:.2f}%"
    )

    print(
        f"Average RR      : "
        f"{avg_rr:.3f}"
    )

    # ========================================================
    # RR DISTRIBUTION REPORT
    # ========================================================

    print()
    print("=" * 70)
    print("RR DISTRIBUTION OF RAW SIGNALS")
    print("=" * 70)

    if not distribution_df.empty:

        for _, row in distribution_df.iterrows():

            print(
                f"{row['rr_bucket']:>10} : "
                f"{int(row['signals']):>4} signals "
                f"({row['percentage']:.2f}%)"
            )

    # ========================================================
    # FILES
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
