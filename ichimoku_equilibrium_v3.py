# ============================================================
# ICHIMOKU EQUILIBRIUM v3.0
# ============================================================
#
# DOGE / USD - KRAKEN FUTURES
#
# 1H  = MARKET REGIME
# 15M = TRUE ICHIMOKU EQUILIBRIUM + REACTION
# 5M  = REVERSAL CONFIRMATION
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
# NO REAL TRADING
#
# v3.0
#
# FIXES:
# - Kraken 2000-candle API limit handled with pagination
# - Full 12-month backtest data
# - Ichimoku warm-up preserved
# - Zero-trade statistics handled safely
# - Trades / Equity / Report files generated
#
# ============================================================

import time
import math
import requests
import numpy as np
import pandas as pd
from datetime import datetime, timedelta, timezone


# ============================================================
# CONFIG
# ============================================================

VERSION = "v3.0"

REAL_TRADING = False

SYMBOL = "PF_DOGEUSD"

KRAKEN_CHARTS_URL = "https://futures.kraken.com/api/charts/v1"

INITIAL_CAPITAL = 100.0

BACKTEST_DAYS = 365

# Extra historical data for Ichimoku / ATR / pivots
WARMUP_DAYS = 30

MAX_CANDLES_PER_REQUEST = 2000

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 4

# ============================================================
# ICHIMOKU
# ============================================================

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52

# ============================================================
# ATR
# ============================================================

ATR_PERIOD = 14

# ============================================================
# 15M EQUILIBRIUM
# ============================================================

MIN_EQUILIBRIUM_DISTANCE_ATR = 0.50
MAX_EQUILIBRIUM_DISTANCE_ATR = 2.00

# ============================================================
# 1H REGIME
# ============================================================

MAX_1H_TK_SEPARATION_ATR = 0.80
MAX_1H_KIJUN_SLOPE_ATR = 0.35
MAX_1H_CLOUD_EXTENSION_ATR = 1.25

# ============================================================
# CANDLE CONFIRMATION
# ============================================================

MIN_15M_BODY_RATIO = 0.25
MIN_5M_BODY_RATIO = 0.30

# ============================================================
# STOP LOSS
# ============================================================

SL_ATR_BUFFER = 0.25
SWING_LOOKBACK_5M = 12
MAX_SL_DISTANCE_PCT = 3.00

# ============================================================
# TAKE PROFIT
# ============================================================

MIN_TP_RR = 1.50
MAX_TP_DISTANCE_PCT = 8.00

# ============================================================
# FEES
# ============================================================

FEE_PER_SIDE = 0.0006
ROUND_TRIP_FEE = FEE_PER_SIDE * 2

# ============================================================
# OUTPUT
# ============================================================

TRADES_FILE = "doge_ichimoku_equilibrium_12m_trades.csv"
EQUITY_FILE = "doge_ichimoku_equilibrium_12m_equity.csv"
REPORT_FILE = "doge_ichimoku_equilibrium_12m_report.txt"


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update(
    {
        "User-Agent": "Ichimoku-Equilibrium-v3-Backtest/1.0"
    }
)


# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def to_timestamp(dt):
    return int(dt.timestamp())


# ============================================================
# SAFE FLOAT
# ============================================================

def safe_float(value):
    try:
        return float(value)
    except Exception:
        return np.nan


# ============================================================
# RESOLUTION SECONDS
# ============================================================

def resolution_seconds(resolution):

    mapping = {
        "5m": 5 * 60,
        "15m": 15 * 60,
        "1h": 60 * 60,
    }

    if resolution not in mapping:
        raise ValueError(
            f"Unsupported resolution: {resolution}"
        )

    return mapping[resolution]


# ============================================================
# PARSE KRAKEN RESPONSE
# ============================================================

def parse_kraken_response(data):

    if isinstance(data, dict):

        if "candles" in data:
            return data["candles"]

        if "data" in data:
            return data["data"]

        # Some Kraken responses may wrap the result
        # in another object.
        for value in data.values():

            if isinstance(value, list):
                return value

    elif isinstance(data, list):

        return data

    return []


# ============================================================
# PARSE CANDLES
# ============================================================

def parse_candles(candles):

    rows = []

    for c in candles:

        # ----------------------------------------------------
        # Dictionary format
        # ----------------------------------------------------

        if isinstance(c, dict):

            ts = c.get("time")

            if ts is None:
                ts = c.get("timestamp")

            o = c.get("open")
            h = c.get("high")
            l = c.get("low")
            cl = c.get("close")
            v = c.get("volume", 0)

            if ts is None:
                continue

            rows.append(
                {
                    "timestamp": safe_float(ts),
                    "open": safe_float(o),
                    "high": safe_float(h),
                    "low": safe_float(l),
                    "close": safe_float(cl),
                    "volume": safe_float(v),
                }
            )

        # ----------------------------------------------------
        # Array format
        # ----------------------------------------------------

        elif isinstance(c, (list, tuple)):

            if len(c) < 5:
                continue

            rows.append(
                {
                    "timestamp": safe_float(c[0]),
                    "open": safe_float(c[1]),
                    "high": safe_float(c[2]),
                    "low": safe_float(c[3]),
                    "close": safe_float(c[4]),
                    "volume": (
                        safe_float(c[5])
                        if len(c) > 5
                        else 0.0
                    ),
                }
            )

    if not rows:
        return pd.DataFrame(
            columns=[
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    df = pd.DataFrame(rows)

    # Kraken may return milliseconds
    if df["timestamp"].max() > 10_000_000_000:
        df["timestamp"] /= 1000.0

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    df = df[
        [
            "datetime",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    ]

    df = df.dropna(
        subset=[
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.sort_values("datetime")

    df = df.drop_duplicates(
        subset=["datetime"],
        keep="last"
    )

    return df.reset_index(drop=True)


# ============================================================
# FETCH SINGLE KRAKEN WINDOW
# ============================================================

def fetch_kraken_window(
    symbol,
    resolution,
    start_ts,
    end_ts
):

    url = (
        f"{KRAKEN_CHARTS_URL}/trade/"
        f"{symbol}/{resolution}"
    )

    params = {
        "from": int(start_ts),
        "to": int(end_ts),
    }

    last_error = None

    for attempt in range(
        1,
        REQUEST_RETRIES + 1
    ):

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:

                last_error = (
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

                if attempt < REQUEST_RETRIES:
                    time.sleep(
                        2 * attempt
                    )
                    continue

                raise RuntimeError(
                    last_error
                )

            data = response.json()

            candles = parse_kraken_response(
                data
            )

            df = parse_candles(
                candles
            )

            return df

        except Exception as exc:

            last_error = str(exc)

            if attempt < REQUEST_RETRIES:

                time.sleep(
                    2 * attempt
                )

            else:

                raise RuntimeError(
                    f"Kraken request failed "
                    f"for {symbol} {resolution}: "
                    f"{last_error}"
                )

    raise RuntimeError(
        f"Kraken request failed "
        f"for {symbol} {resolution}"
    )


# ============================================================
# PAGINATED KRAKEN DATA
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    start_ts,
    end_ts
):
    """
    Kraken Charts API returns a limited number of candles
    per request.

    This function walks forward through the requested period
    in windows of MAX_CANDLES_PER_REQUEST candles.
    """

    step = resolution_seconds(
        resolution
    )

    window_seconds = (
        MAX_CANDLES_PER_REQUEST
        *
        step
    )

    # Small overlap prevents gaps caused by API boundaries.
    overlap = step

    current = int(start_ts)

    all_parts = []

    total_span = max(
        1,
        int(end_ts - start_ts)
    )

    while current < end_ts:

        window_end = min(
            current
            +
            window_seconds,
            end_ts
        )

        print(
            f"  {resolution}: "
            f"{datetime.fromtimestamp(current, timezone.utc).strftime('%Y-%m-%d')} "
            f"-> "
            f"{datetime.fromtimestamp(window_end, timezone.utc).strftime('%Y-%m-%d')}"
        )

        df = fetch_kraken_window(
            symbol,
            resolution,
            current,
            window_end
        )

        if not df.empty:
            all_parts.append(df)

        # ----------------------------------------------------
        # Progress
        # ----------------------------------------------------

        progress = (
            (window_end - start_ts)
            /
            total_span
        ) * 100.0

        print(
            f"    received: {len(df):,} | "
            f"progress: {min(progress, 100):.1f}%"
        )

        # ----------------------------------------------------
        # Advance
        # ----------------------------------------------------

        if window_end >= end_ts:
            break

        next_current = (
            window_end
            -
            overlap
        )

        if next_current <= current:
            next_current = (
                current
                +
                window_seconds
            )

        current = next_current

        # Be polite to the API.
        time.sleep(0.15)

    if not all_parts:

        raise RuntimeError(
            f"No market data returned for "
            f"{symbol} {resolution}"
        )

    result = pd.concat(
        all_parts,
        ignore_index=True
    )

    result = result.sort_values(
        "datetime"
    )

    result = result.drop_duplicates(
        subset=["datetime"],
        keep="last"
    )

    result = result.reset_index(
        drop=True
    )

    # Final requested range.
    result = result[
        (result["datetime"] >= pd.to_datetime(
            start_ts,
            unit="s",
            utc=True
        ))
        &
        (result["datetime"] <= pd.to_datetime(
            end_ts,
            unit="s",
            utc=True
        ))
    ].copy()

    return result.reset_index(
        drop=True
    )


# ============================================================
# FETCH BACKTEST DATA
# ============================================================

def fetch_backtest_data():

    end_dt = utc_now()

    # Warm-up is BEFORE the actual one-year test period.
    test_start_dt = (
        end_dt
        -
        timedelta(days=BACKTEST_DAYS)
    )

    data_start_dt = (
        test_start_dt
        -
        timedelta(days=WARMUP_DAYS)
    )

    print(
        "=" * 70
    )

    print(
        "DOWNLOADING KRAKEN FUTURES DATA"
    )

    print(
        "=" * 70
    )

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Backtest start: "
        f"{test_start_dt.isoformat()}"
    )

    print(
        f"Data start: "
        f"{data_start_dt.isoformat()}"
    )

    print(
        f"Backtest end: "
        f"{end_dt.isoformat()}"
    )

    print()

    start_ts = to_timestamp(
        data_start_dt
    )

    end_ts = to_timestamp(
        end_dt
    )

    result = {}

    for resolution in [
        "1h",
        "15m",
        "5m",
    ]:

        print(
            f"\nFetching {resolution}..."
        )

        df = fetch_kraken_candles(
            SYMBOL,
            resolution,
            start_ts,
            end_ts
        )

        if df.empty:

            raise RuntimeError(
                f"Missing market data "
                f"for {resolution}"
            )

        result[resolution] = df

        print(
            f"{resolution}: "
            f"{len(df):,} candles"
        )

    print()

    return (
        result["1h"],
        result["15m"],
        result["5m"],
        test_start_dt
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD
):

    previous_close = (
        df["close"].shift(1)
    )

    tr1 = (
        df["high"]
        -
        df["low"]
    )

    tr2 = (
        df["high"]
        -
        previous_close
    ).abs()

    tr3 = (
        df["low"]
        -
        previous_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1
    ).max(axis=1)

    return tr.rolling(
        period
    ).mean()


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

    df["senkou_b"] = (
        high.rolling(
            SENKOU_B_PERIOD
        ).max()
        +
        low.rolling(
            SENKOU_B_PERIOD
        ).min()
    ) / 2.0

    df["senkou_a"] = (
        df["tenkan"]
        +
        df["kijun"]
    ) / 2.0

    # TRUE EQUILIBRIUM
    df["equilibrium"] = (
        df["tenkan"]
        +
        df["kijun"]
    ) / 2.0

    df["atr"] = calculate_atr(
        df
    )

    return df


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

    return (
        abs(
            row["close"]
            -
            row["open"]
        )
        /
        candle_range
    )


# ============================================================
# 1H REGIME
# ============================================================

def valid_1h_regime(
    df,
    idx
):

    if idx < 2:
        return False

    row = df.iloc[idx]

    prev = df.iloc[idx - 1]

    required = [
        row["close"],
        row["tenkan"],
        row["kijun"],
        row["senkou_a"],
        row["senkou_b"],
        row["atr"],
        prev["kijun"],
    ]

    if any(
        pd.isna(x)
        for x in required
    ):
        return False

    atr = row["atr"]

    if atr <= 0:
        return False

    tk_separation = (
        abs(
            row["tenkan"]
            -
            row["kijun"]
        )
        /
        atr
    )

    kijun_slope = (
        abs(
            row["kijun"]
            -
            prev["kijun"]
        )
        /
        atr
    )

    cloud_mid = (
        row["senkou_a"]
        +
        row["senkou_b"]
    ) / 2.0

    cloud_extension = (
        abs(
            row["close"]
            -
            cloud_mid
        )
        /
        atr
    )

    if (
        tk_separation
        >
        MAX_1H_TK_SEPARATION_ATR
    ):
        return False

    if (
        kijun_slope
        >
        MAX_1H_KIJUN_SLOPE_ATR
    ):
        return False

    if (
        cloud_extension
        >
        MAX_1H_CLOUD_EXTENSION_ATR
    ):
        return False

    return True


# ============================================================
# 15M REACTION
# ============================================================

def valid_15m_reaction(
    df,
    idx,
    direction
):

    if idx < 2:
        return False

    row = df.iloc[idx]

    prev = df.iloc[idx - 1]

    required = [
        row["close"],
        row["open"],
        row["high"],
        row["low"],
        row["equilibrium"],
        row["atr"],
        prev["close"],
    ]

    if any(
        pd.isna(x)
        for x in required
    ):
        return False

    atr = row["atr"]

    if atr <= 0:
        return False

    equilibrium = row["equilibrium"]

    distance_atr = (
        abs(
            row["close"]
            -
            equilibrium
        )
        /
        atr
    )

    if (
        distance_atr
        <
        MIN_EQUILIBRIUM_DISTANCE_ATR
    ):
        return False

    if (
        distance_atr
        >
        MAX_EQUILIBRIUM_DISTANCE_ATR
    ):
        return False

    if (
        body_ratio(row)
        <
        MIN_15M_BODY_RATIO
    ):
        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if row["close"] >= equilibrium:
            return False

        if row["close"] <= row["open"]:
            return False

        if row["close"] <= prev["close"]:
            return False

        return True

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if row["close"] <= equilibrium:
            return False

        if row["close"] >= row["open"]:
            return False

        if row["close"] >= prev["close"]:
            return False

        return True

    return False


# ============================================================
# 5M TRIGGER
# ============================================================

def valid_5m_trigger(
    df,
    idx,
    direction
):

    if idx < 2:
        return False

    row = df.iloc[idx]

    prev = df.iloc[idx - 1]

    required = [
        row["open"],
        row["high"],
        row["low"],
        row["close"],
        row["tenkan"],
        prev["close"],
    ]

    if any(
        pd.isna(x)
        for x in required
    ):
        return False

    if (
        body_ratio(row)
        <
        MIN_5M_BODY_RATIO
    ):
        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if row["close"] <= row["open"]:
            return False

        if row["close"] <= prev["close"]:
            return False

        if row["close"] <= row["tenkan"]:
            return False

        return True

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if row["close"] >= row["open"]:
            return False

        if row["close"] >= prev["close"]:
            return False

        if row["close"] >= row["tenkan"]:
            return False

        return True

    return False


# ============================================================
# STOP LOSS
# ============================================================

def calculate_stop_loss(
    df5,
    idx,
    direction,
    entry
):

    start = max(
        0,
        idx - SWING_LOOKBACK_5M
    )

    window = df5.iloc[
        start:idx + 1
    ]

    if window.empty:
        return None

    atr = df5.iloc[idx]["atr"]

    if pd.isna(atr) or atr <= 0:
        return None

    if direction == "LONG":

        swing_low = (
            window["low"].min()
        )

        stop = (
            swing_low
            -
            atr * SL_ATR_BUFFER
        )

        if stop >= entry:
            return None

    else:

        swing_high = (
            window["high"].max()
        )

        stop = (
            swing_high
            +
            atr * SL_ATR_BUFFER
        )

        if stop <= entry:
            return None

    distance_pct = (
        abs(
            entry - stop
        )
        /
        entry
    ) * 100.0

    if (
        distance_pct
        >
        MAX_SL_DISTANCE_PCT
    ):
        return None

    return float(stop)


# ============================================================
# TAKE PROFIT
# ============================================================

def select_take_profit(
    entry,
    stop_loss,
    direction,
    resistance_levels,
    support_levels
):

    risk = abs(
        entry - stop_loss
    )

    if risk <= 0:
        return None

    if direction == "LONG":

        candidates = [
            level
            for level
            in resistance_levels
            if level > entry
        ]

        candidates.sort()

    else:

        candidates = [
            level
            for level
            in support_levels
            if level < entry
        ]

        candidates.sort(
            reverse=True
        )

    for level in candidates:

        distance = abs(
            level - entry
        )

        distance_pct = (
            distance
            /
            entry
        ) * 100.0

        if (
            distance_pct
            >
            MAX_TP_DISTANCE_PCT
        ):
            continue

        rr = (
            distance
            /
            risk
        )

        if rr > MIN_TP_RR:
            return float(level)

    return None


# ============================================================
# TRADE RETURN
# ============================================================

def calculate_trade_return(
    entry,
    exit_price,
    direction
):

    if direction == "LONG":

        gross = (
            exit_price
            -
            entry
        ) / entry

    else:

        gross = (
            entry
            -
            exit_price
        ) / entry

    return (
        gross
        -
        ROUND_TRIP_FEE
    )


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(
    df1h,
    df15,
    df5,
    test_start_dt
):

    print(
        "=" * 70
    )

    print(
        "RUNNING ICHIMOKU EQUILIBRIUM v3.0"
    )

    print(
        "=" * 70
    )

    # --------------------------------------------------------
    # Only the requested 12-month period is traded.
    # Warm-up data remains available for indicators.
    # --------------------------------------------------------

    test_start = pd.Timestamp(
        test_start_dt
    )

    test_indices = df5.index[
        df5["datetime"] >= test_start
    ].tolist()

    print(
        f"5M candles in test period: "
        f"{len(test_indices):,}"
    )

    if not test_indices:

        raise RuntimeError(
            "No 5M candles available "
            "inside the requested "
            "12-month backtest period."
        )

    capital = INITIAL_CAPITAL

    trades = []

    equity_rows = []

    active_trade = None

    # Confirmed pivot storage
    pivot_highs = []
    pivot_lows = []

    # Used to prevent duplicate pivot insertion.
    known_high_times = set()
    known_low_times = set()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    for pos5 in test_indices:

        current_time = df5.iloc[
            pos5
        ]["datetime"]

        row5 = df5.iloc[
            pos5
        ]

        # ====================================================
        # MANAGE OPEN TRADE
        # ====================================================

        if active_trade is not None:

            direction = (
                active_trade["direction"]
            )

            stop = (
                active_trade["stop_loss"]
            )

            target = (
                active_trade["take_profit"]
            )

            high = float(
                row5["high"]
            )

            low = float(
                row5["low"]
            )

            exit_price = None
            exit_reason = None

            if direction == "LONG":

                stop_hit = (
                    low <= stop
                )

                target_hit = (
                    high >= target
                )

                if stop_hit:

                    exit_price = stop
                    exit_reason = "SL"

                elif target_hit:

                    exit_price = target
                    exit_reason = "TP"

            else:

                stop_hit = (
                    high >= stop
                )

                target_hit = (
                    low <= target
                )

                if stop_hit:

                    exit_price = stop
                    exit_reason = "SL"

                elif target_hit:

                    exit_price = target
                    exit_reason = "TP"

            # ------------------------------------------------
            # Close trade
            # ------------------------------------------------

            if exit_price is not None:

                trade_return = (
                    calculate_trade_return(
                        active_trade["entry"],
                        exit_price,
                        direction
                    )
                )

                pnl = (
                    capital
                    *
                    trade_return
                )

                capital += pnl

                trades.append(
                    {
                        "entry_time":
                            active_trade[
                                "entry_time"
                            ],

                        "exit_time":
                            current_time,

                        "direction":
                            direction,

                        "entry":
                            active_trade[
                                "entry"
                            ],

                        "stop_loss":
                            stop,

                        "take_profit":
                            target,

                        "exit":
                            exit_price,

                        "return_pct":
                            trade_return * 100.0,

                        "pnl":
                            pnl,

                        "capital_after":
                            capital,

                        "exit_reason":
                            exit_reason,

                        "rr":
                            active_trade[
                                "rr"
                            ],
                    }
                )

                active_trade = None

        # ====================================================
        # EQUITY SNAPSHOT
        # ====================================================

        equity_rows.append(
            {
                "datetime":
                    current_time,

                "capital":
                    capital,
            }
        )

        # ====================================================
        # UPDATE CONFIRMED 15M PIVOTS
        #
        # A pivot at p is confirmed only after two candles
        # on its right side have closed.
        # ====================================================

        pos15_for_pivot = (
            df15["datetime"].searchsorted(
                current_time,
                side="right"
            )
            -
            1
        )

        if pos15_for_pivot >= 4:

            p = (
                pos15_for_pivot
                -
                2
            )

            if (
                p >= 2
                and
                p + 2 < len(df15)
            ):

                pivot_row = df15.iloc[p]

                left_high = (
                    df15.iloc[
                        p - 2:p
                    ]["high"].max()
                )

                right_high = (
                    df15.iloc[
                        p + 1:p + 3
                    ]["high"].max()
                )

                left_low = (
                    df15.iloc[
                        p - 2:p
                    ]["low"].min()
                )

                right_low = (
                    df15.iloc[
                        p + 1:p + 3
                    ]["low"].min()
                )

                pivot_time = (
                    pivot_row["datetime"]
                )

                # ----------------------------
                # HIGH
                # ----------------------------

                if (
                    pivot_row["high"]
                    >
                    left_high
                    and
                    pivot_row["high"]
                    >=
                    right_high
                ):

                    if pivot_time not in known_high_times:

                        pivot_highs.append(
                            (
                                pivot_time,
                                float(
                                    pivot_row[
                                        "high"
                                    ]
                                )
                            )
                        )

                        known_high_times.add(
                            pivot_time
                        )

                # ----------------------------
                # LOW
                # ----------------------------

                if (
                    pivot_row["low"]
                    <
                    left_low
                    and
                    pivot_row["low"]
                    <=
                    right_low
                ):

                    if pivot_time not in known_low_times:

                        pivot_lows.append(
                            (
                                pivot_time,
                                float(
                                    pivot_row[
                                        "low"
                                    ]
                                )
                            )
                        )

                        known_low_times.add(
                            pivot_time
                        )

        # ====================================================
        # ONLY ONE ACTIVE TRADE
        # ====================================================

        if active_trade is not None:
            continue

        # ====================================================
        # CURRENT COMPLETED 15M
        # ====================================================

        pos15 = (
            df15["datetime"].searchsorted(
                current_time,
                side="right"
            )
            -
            1
        )

        if pos15 < 2:
            continue

        # ====================================================
        # CURRENT COMPLETED 1H
        # ====================================================

        pos1h = (
            df1h["datetime"].searchsorted(
                current_time,
                side="right"
            )
            -
            1
        )

        if pos1h < 2:
            continue

        # ====================================================
        # 1H REGIME
        # ====================================================

        if not valid_1h_regime(
            df1h,
            pos1h
        ):
            continue

        # ====================================================
        # CURRENT 5M CANDLE
        # ====================================================

        # We are evaluating the closed 5M candle itself.
        # Entry is at its close.
        # No future candle is used.
        # ====================================================

        # ====================================================
        # LONG
        # ====================================================

        if valid_15m_reaction(
            df15,
            pos15,
            "LONG"
        ):

            if valid_5m_trigger(
                df5,
                pos5,
                "LONG"
            ):

                entry = float(
                    row5["close"]
                )

                stop = (
                    calculate_stop_loss(
                        df5,
                        pos5,
                        "LONG",
                        entry
                    )
                )

                if stop is not None:

                    resistance_levels = [
                        price
                        for dt, price
                        in pivot_highs
                        if dt < current_time
                    ]

                    support_levels = [
                        price
                        for dt, price
                        in pivot_lows
                        if dt < current_time
                    ]

                    target = (
                        select_take_profit(
                            entry,
                            stop,
                            "LONG",
                            resistance_levels,
                            support_levels
                        )
                    )

                    if target is not None:

                        risk = abs(
                            entry
                            -
                            stop
                        )

                        reward = abs(
                            target
                            -
                            entry
                        )

                        rr = (
                            reward / risk
                            if risk > 0
                            else 0
                        )

                        active_trade = {
                            "entry_time":
                                current_time,

                            "direction":
                                "LONG",

                            "entry":
                                entry,

                            "stop_loss":
                                stop,

                            "take_profit":
                                target,

                            "rr":
                                rr,
                        }

                        continue

        # ====================================================
        # SHORT
        # ====================================================

        if valid_15m_reaction(
            df15,
            pos15,
            "SHORT"
        ):

            if valid_5m_trigger(
                df5,
                pos5,
                "SHORT"
            ):

                entry = float(
                    row5["close"]
                )

                stop = (
                    calculate_stop_loss(
                        df5,
                        pos5,
                        "SHORT",
                        entry
                    )
                )

                if stop is not None:

                    resistance_levels = [
                        price
                        for dt, price
                        in pivot_highs
                        if dt < current_time
                    ]

                    support_levels = [
                        price
                        for dt, price
                        in pivot_lows
                        if dt < current_time
                    ]

                    target = (
                        select_take_profit(
                            entry,
                            stop,
                            "SHORT",
                            resistance_levels,
                            support_levels
                        )
                    )

                    if target is not None:

                        risk = abs(
                            entry
                            -
                            stop
                        )

                        reward = abs(
                            target
                            -
                            entry
                        )

                        rr = (
                            reward / risk
                            if risk > 0
                            else 0
                        )

                        active_trade = {
                            "entry_time":
                                current_time,

                            "direction":
                                "SHORT",

                            "entry":
                                entry,

                            "stop_loss":
                                stop,

                            "take_profit":
                                target,

                            "rr":
                                rr,
                        }

                        continue

    # ========================================================
    # CLOSE OPEN TRADE AT END
    # ========================================================

    if active_trade is not None:

        last_row = df5.iloc[
            test_indices[-1]
        ]

        exit_price = float(
            last_row["close"]
        )

        direction = (
            active_trade["direction"]
        )

        trade_return = (
            calculate_trade_return(
                active_trade["entry"],
                exit_price,
                direction
            )
        )

        pnl = (
            capital
            *
            trade_return
        )

        capital += pnl

        trades.append(
            {
                "entry_time":
                    active_trade[
                        "entry_time"
                    ],

                "exit_time":
                    last_row[
                        "datetime"
                    ],

                "direction":
                    direction,

                "entry":
                    active_trade[
                        "entry"
                    ],

                "stop_loss":
                    active_trade[
                        "stop_loss"
                    ],

                "take_profit":
                    active_trade[
                        "take_profit"
                    ],

                "exit":
                    exit_price,

                "return_pct":
                    trade_return * 100.0,

                "pnl":
                    pnl,

                "capital_after":
                    capital,

                "exit_reason":
                    "END_OF_BACKTEST",

                "rr":
                    active_trade[
                        "rr"
                    ],
            }
        )

        equity_rows.append(
            {
                "datetime":
                    last_row["datetime"],

                "capital":
                    capital,
            }
        )

    # ========================================================
    # DATAFRAMES
    # ========================================================

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

def calculate_statistics(
    trades_df,
    equity_df,
    final_capital
):

    # --------------------------------------------------------
    # Always initialize every field.
    # This fixes the previous KeyError.
    # --------------------------------------------------------

    stats = {
        "initial_capital":
            INITIAL_CAPITAL,

        "final_capital":
            final_capital,

        "net_return_pct":
            (
                final_capital
                /
                INITIAL_CAPITAL
                -
                1.0
            ) * 100.0,

        "trades":
            0,

        "wins":
            0,

        "losses":
            0,

        "win_rate":
            0.0,

        "profit_factor":
            0.0,

        "avg_trade_pct":
            0.0,

        "max_drawdown_pct":
            0.0,

        "long_trades":
            0,

        "short_trades":
            0,

        "long_wr":
            0.0,

        "short_wr":
            0.0,

        "tp_exits":
            0,

        "sl_exits":
            0,

        "end_exits":
            0,
    }

    # --------------------------------------------------------
    # No trades
    # --------------------------------------------------------

    if trades_df.empty:

        if not equity_df.empty:

            eq = equity_df.copy()

            eq["peak"] = (
                eq["capital"]
                .cummax()
            )

            eq["drawdown_pct"] = (
                (
                    eq["capital"]
                    /
                    eq["peak"]
                )
                -
                1.0
            ) * 100.0

            stats["max_drawdown_pct"] = (
                eq["drawdown_pct"].min()
            )

        return stats

    # --------------------------------------------------------
    # Normal statistics
    # --------------------------------------------------------

    stats["trades"] = len(
        trades_df
    )

    wins = trades_df[
        trades_df["return_pct"] > 0
    ]

    losses = trades_df[
        trades_df["return_pct"] <= 0
    ]

    stats["wins"] = len(
        wins
    )

    stats["losses"] = len(
        losses
    )

    stats["win_rate"] = (
        len(wins)
        /
        len(trades_df)
    ) * 100.0

    gross_profit = (
        wins["pnl"].sum()
    )

    gross_loss = abs(
        losses["pnl"].sum()
    )

    if gross_loss > 0:

        stats["profit_factor"] = (
            gross_profit
            /
            gross_loss
        )

    elif gross_profit > 0:

        stats["profit_factor"] = math.inf

    else:

        stats["profit_factor"] = 0.0

    stats["avg_trade_pct"] = (
        trades_df[
            "return_pct"
        ].mean()
    )

    # --------------------------------------------------------
    # Equity drawdown
    # --------------------------------------------------------

    if not equity_df.empty:

        eq = equity_df.copy()

        eq["peak"] = (
            eq["capital"]
            .cummax()
        )

        eq["drawdown_pct"] = (
            (
                eq["capital"]
                /
                eq["peak"]
            )
            -
            1.0
        ) * 100.0

        stats["max_drawdown_pct"] = (
            eq["drawdown_pct"].min()
        )

    # --------------------------------------------------------
    # Direction
    # --------------------------------------------------------

    long_df = trades_df[
        trades_df["direction"]
        ==
        "LONG"
    ]

    short_df = trades_df[
        trades_df["direction"]
        ==
        "SHORT"
    ]

    stats["long_trades"] = len(
        long_df
    )

    stats["short_trades"] = len(
        short_df
    )

    if len(long_df) > 0:

        stats["long_wr"] = (
            (
                long_df["return_pct"]
                > 0
            ).sum()
            /
            len(long_df)
        ) * 100.0

    if len(short_df) > 0:

        stats["short_wr"] = (
            (
                short_df["return_pct"]
                > 0
            ).sum()
            /
            len(short_df)
        ) * 100.0

    # --------------------------------------------------------
    # Exit types
    # --------------------------------------------------------

    stats["tp_exits"] = len(
        trades_df[
            trades_df["exit_reason"]
            ==
            "TP"
        ]
    )

    stats["sl_exits"] = len(
        trades_df[
            trades_df["exit_reason"]
            ==
            "SL"
        ]
    )

    stats["end_exits"] = len(
        trades_df[
            trades_df["exit_reason"]
            ==
            "END_OF_BACKTEST"
        ]
    )

    return stats


# ============================================================
# MONTHLY PERFORMANCE
# ============================================================

def monthly_performance(
    trades_df
):

    if trades_df.empty:

        return pd.DataFrame(
            columns=[
                "month",
                "trades",
                "pnl_pct",
                "pnl",
            ]
        )

    df = trades_df.copy()

    df["exit_time"] = pd.to_datetime(
        df["exit_time"],
        utc=True
    )

    df["month"] = (
        df["exit_time"]
        .dt.to_period("M")
        .astype(str)
    )

    monthly = (
        df.groupby("month")
        .agg(
            trades=(
                "return_pct",
                "count"
            ),

            pnl_pct=(
                "return_pct",
                "sum"
            ),

            pnl=(
                "pnl",
                "sum"
            ),
        )
        .reset_index()
    )

    return monthly


# ============================================================
# SAVE OUTPUTS
# ============================================================

def save_outputs(
    trades_df,
    equity_df,
    stats
):

    trades_df.to_csv(
        TRADES_FILE,
        index=False
    )

    equity_df.to_csv(
        EQUITY_FILE,
        index=False
    )

    monthly = monthly_performance(
        trades_df
    )

    lines = []

    lines.append(
        "=" * 70
    )

    lines.append(
        "ICHIMOKU EQUILIBRIUM v3.0 "
        "- DOGE 12M BACKTEST"
    )

    lines.append(
        "=" * 70
    )

    lines.append("")

    lines.append(
        f"Symbol: {SYMBOL}"
    )

    lines.append(
        f"Version: {VERSION}"
    )

    lines.append(
        f"Real Trading: {REAL_TRADING}"
    )

    lines.append("")

    lines.append(
        "RESULTS"
    )

    lines.append(
        "-" * 70
    )

    lines.append(
        f"Initial Capital: "
        f"${stats['initial_capital']:.2f}"
    )

    lines.append(
        f"Final Capital:   "
        f"${stats['final_capital']:.2f}"
    )

    lines.append(
        f"Net Return:      "
        f"{stats['net_return_pct']:.3f}%"
    )

    lines.append(
        f"Trades:          "
        f"{stats['trades']}"
    )

    lines.append(
        f"Wins:            "
        f"{stats['wins']}"
    )

    lines.append(
        f"Losses:          "
        f"{stats['losses']}"
    )

    lines.append(
        f"Win Rate:        "
        f"{stats['win_rate']:.2f}%"
    )

    if math.isinf(
        stats["profit_factor"]
    ):

        pf_text = "INF"

    else:

        pf_text = (
            f"{stats['profit_factor']:.3f}"
        )

    lines.append(
        f"Profit Factor:   "
        f"{pf_text}"
    )

    lines.append(
        f"Avg Trade:       "
        f"{stats['avg_trade_pct']:.4f}%"
    )

    lines.append(
        f"Max Drawdown:    "
        f"{stats['max_drawdown_pct']:.3f}%"
    )

    lines.append("")

    lines.append(
        "DIRECTION"
    )

    lines.append(
        "-" * 70
    )

    lines.append(
        f"Long Trades:     "
        f"{stats['long_trades']}"
    )

    lines.append(
        f"Long Win Rate:   "
        f"{stats['long_wr']:.2f}%"
    )

    lines.append(
        f"Short Trades:    "
        f"{stats['short_trades']}"
    )

    lines.append(
        f"Short Win Rate:  "
        f"{stats['short_wr']:.2f}%"
    )

    lines.append("")

    lines.append(
        "EXITS"
    )

    lines.append(
        "-" * 70
    )

    lines.append(
        f"TP:              "
        f"{stats['tp_exits']}"
    )

    lines.append(
        f"SL:              "
        f"{stats['sl_exits']}"
    )

    lines.append(
        f"End of Backtest: "
        f"{stats['end_exits']}"
    )

    lines.append("")

    lines.append(
        "PARAMETERS"
    )

    lines.append(
        "-" * 70
    )

    lines.append(
        f"Ichimoku: "
        f"{TENKAN_PERIOD}/"
        f"{KIJUN_PERIOD}/"
        f"{SENKOU_B_PERIOD}"
    )

    lines.append(
        f"Min Equilibrium Distance ATR: "
        f"{MIN_EQUILIBRIUM_DISTANCE_ATR}"
    )

    lines.append(
        f"Max Equilibrium Distance ATR: "
        f"{MAX_EQUILIBRIUM_DISTANCE_ATR}"
    )

    lines.append(
        f"1H TK Separation Max ATR: "
        f"{MAX_1H_TK_SEPARATION_ATR}"
    )

    lines.append(
        f"1H Kijun Slope Max ATR: "
        f"{MAX_1H_KIJUN_SLOPE_ATR}"
    )

    lines.append(
        f"1H Cloud Extension Max ATR: "
        f"{MAX_1H_CLOUD_EXTENSION_ATR}"
    )

    lines.append(
        f"SL ATR Buffer: "
        f"{SL_ATR_BUFFER}"
    )

    lines.append(
        f"Max SL: "
        f"{MAX_SL_DISTANCE_PCT:.2f}%"
    )

    lines.append(
        f"Min TP RR: "
        f"{MIN_TP_RR:.2f}"
    )

    lines.append(
        f"Max TP Distance: "
        f"{MAX_TP_DISTANCE_PCT:.2f}%"
    )

    lines.append(
        f"Fee Per Side: "
        f"{FEE_PER_SIDE * 100:.3f}%"
    )

    lines.append("")

    lines.append(
        "MONTHLY PERFORMANCE"
    )

    lines.append(
        "-" * 70
    )

    if monthly.empty:

        lines.append(
            "No closed trades."
        )

    else:

        for _, row in monthly.iterrows():

            lines.append(
                f"{row['month']} | "
                f"Trades={int(row['trades'])} | "
                f"Return={row['pnl_pct']:.3f}% | "
                f"PnL=${row['pnl']:.4f}"
            )

    lines.append("")

    lines.append(
        "=" * 70
    )

    lines.append(
        "END OF REPORT"
    )

    lines.append(
        "=" * 70
    )

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "\n".join(lines)
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print()

    print(
        "=" * 70
    )

    print(
        "ICHIMOKU EQUILIBRIUM v3.0"
    )

    print(
        "=" * 70
    )

    print(
        f"REAL TRADING: {REAL_TRADING}"
    )

    print()

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False."
        )

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    (
        df1h,
        df15,
        df5,
        test_start_dt
    ) = fetch_backtest_data()

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

    print(
        "Calculating Ichimoku + ATR..."
    )

    df1h = add_ichimoku(
        df1h
    )

    df15 = add_ichimoku(
        df15
    )

    df5 = add_ichimoku(
        df5
    )

    print(
        f"1H candles:  {len(df1h):,}"
    )

    print(
        f"15M candles: {len(df15):,}"
    )

    print(
        f"5M candles:  {len(df5):,}"
    )

    print()

    # --------------------------------------------------------
    # BACKTEST
    # --------------------------------------------------------

    (
        trades_df,
        equity_df,
        final_capital
    ) = run_backtest(
        df1h,
        df15,
        df5,
        test_start_dt
    )

    # --------------------------------------------------------
    # STATS
    # --------------------------------------------------------

    stats = calculate_statistics(
        trades_df,
        equity_df,
        final_capital
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_outputs(
        trades_df,
        equity_df,
        stats
    )

    # --------------------------------------------------------
    # CONSOLE
    # --------------------------------------------------------

    print()

    print(
        "=" * 70
    )

    print(
        "BACKTEST RESULTS"
    )

    print(
        "=" * 70
    )

    print(
        f"Initial Capital : "
        f"${stats['initial_capital']:.2f}"
    )

    print(
        f"Final Capital   : "
        f"${stats['final_capital']:.2f}"
    )

    print(
        f"Net Return      : "
        f"{stats['net_return_pct']:.3f}%"
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
        f"{stats['win_rate']:.2f}%"
    )

    if math.isinf(
        stats["profit_factor"]
    ):

        print(
            "Profit Factor   : INF"
        )

    else:

        print(
            f"Profit Factor   : "
            f"{stats['profit_factor']:.3f}"
        )

    print(
        f"Avg Trade       : "
        f"{stats['avg_trade_pct']:.4f}%"
    )

    print(
        f"Max Drawdown    : "
        f"{stats['max_drawdown_pct']:.3f}%"
    )

    print()

    print(
        f"Long Trades     : "
        f"{stats['long_trades']}"
    )

    print(
        f"Long Win Rate   : "
        f"{stats['long_wr']:.2f}%"
    )

    print(
        f"Short Trades    : "
        f"{stats['short_trades']}"
    )

    print(
        f"Short Win Rate  : "
        f"{stats['short_wr']:.2f}%"
    )

    print()

    print(
        f"TP Exits        : "
        f"{stats['tp_exits']}"
    )

    print(
        f"SL Exits        : "
        f"{stats['sl_exits']}"
    )

    print(
        f"End Exit        : "
        f"{stats['end_exits']}"
    )

    print()

    print(
        "=" * 70
    )

    print(
        "OUTPUT FILES"
    )

    print(
        "=" * 70
    )

    print(
        f"✓ {TRADES_FILE}"
    )

    print(
        f"✓ {EQUITY_FILE}"
    )

    print(
        f"✓ {REPORT_FILE}"
    )

    print(
        "=" * 70
    )


if __name__ == "__main__":
    main()
