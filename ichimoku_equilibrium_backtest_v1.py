# ============================================================
# ICHIMOKU EQUILIBRIUM v1.0
# 12-MONTH KRAKEN FUTURES BACKTEST
# ============================================================
#
# 1H  = MARKET REGIME
# 15M = KIJUN EQUILIBRIUM + REACTION
# 5M  = ENTRY TRIGGER
#
# STANDARD ICHIMOKU:
# Tenkan  = 9
# Kijun   = 26
# SenkouB = 52
#
# CLOSED CANDLES ONLY
# NO LOOKAHEAD
#
# FEES:
# LBank taker = 0.06% each side
# Round trip  = 0.12%
#
# REAL TRADING = DISABLED
#
# ============================================================

import time
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timezone


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "PF_DOGEUSD"

BASE_URL = (
    "https://futures.kraken.com/api/charts/v1"
)

BACKTEST_DAYS = 365
WARMUP_DAYS = 10

INITIAL_CAPITAL = 100.0

FEE_RATE = 0.0006

# ------------------------------------------------------------
# Ichimoku
# ------------------------------------------------------------

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52

# ------------------------------------------------------------
# ATR
# ------------------------------------------------------------

ATR_PERIOD = 14

# ------------------------------------------------------------
# 15M equilibrium
# ------------------------------------------------------------

EQUILIBRIUM_ATR_MULT = 0.75

# ------------------------------------------------------------
# Stop Loss
# ------------------------------------------------------------

SL_ATR_BUFFER = 0.25
SWING_LOOKBACK_5M = 12

MAX_SL_DISTANCE_PCT = 3.00

# ------------------------------------------------------------
# Take Profit
# ------------------------------------------------------------

MIN_RR = 1.50
MAX_TP_DISTANCE_PCT = 8.00

# ------------------------------------------------------------
# Pivot confirmation
# ------------------------------------------------------------

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

# ------------------------------------------------------------
# HTTP
# ------------------------------------------------------------

REQUEST_TIMEOUT = 30

# IMPORTANT:
# Kraken Futures Charts endpoint can reject large requests.
# Keep this conservative.
MAX_CANDLES_PER_REQUEST = 900

# ------------------------------------------------------------
# Output
# ------------------------------------------------------------

TRADES_FILE = (
    "doge_ichimoku_equilibrium_12m_trades.csv"
)

EQUITY_FILE = (
    "doge_ichimoku_equilibrium_12m_equity.csv"
)

REPORT_FILE = (
    "doge_ichimoku_equilibrium_12m_report.txt"
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Ichimoku-Equilibrium-Backtest/1.0"
    }
)


# ============================================================
# HELPERS
# ============================================================

def safe_float(value, default=np.nan):

    try:
        return float(value)

    except Exception:
        return default


def interval_seconds(resolution):

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


def resolution_timedelta(resolution):

    return pd.Timedelta(
        seconds=interval_seconds(resolution)
    )


# ============================================================
# KRAKEN HISTORICAL DATA
# ============================================================

def fetch_kraken_range(
    symbol,
    resolution,
    start_ts,
    end_ts,
):
    """
    Download Kraken Futures historical candles.

    IMPORTANT:
    Correct endpoint:

        /charts/v1/trade/{symbol}/{resolution}

    NOT:

        /charts/v1/{symbol}/{resolution}

    Uses small chunks to avoid HTTP 400.
    """

    rows = []

    step = interval_seconds(
        resolution
    )

    chunk_seconds = (
        MAX_CANDLES_PER_REQUEST
        * step
    )

    current = int(start_ts)

    while current < int(end_ts):

        chunk_end = min(
            current + chunk_seconds,
            int(end_ts)
        )

        # ====================================================
        # CORRECT KRAKEN FUTURES CHARTS ENDPOINT
        # ====================================================

        url = (
            f"{BASE_URL}/trade/"
            f"{symbol}/{resolution}"
            f"?from={current}"
            f"&to={chunk_end}"
        )

        start_dt = datetime.fromtimestamp(
            current,
            tz=timezone.utc
        )

        end_dt = datetime.fromtimestamp(
            chunk_end,
            tz=timezone.utc
        )

        print(
            f"Downloading {resolution}: "
            f"{start_dt} -> {end_dt}"
        )

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            # Print response body if Kraken rejects it.
            if response.status_code != 200:

                print(
                    f"Kraken HTTP "
                    f"{response.status_code}"
                )

                print(
                    "Kraken response:"
                )

                print(
                    response.text[:1000]
                )

                response.raise_for_status()

            payload = response.json()

        except Exception as exc:

            print(
                f"Download error "
                f"{resolution}: {exc}"
            )

            raise RuntimeError(
                f"Kraken historical data "
                f"request failed for "
                f"{resolution}: {exc}"
            )

        data = payload.get(
            "candles",
            []
        )

        if not data:

            print(
                f"No candles returned "
                f"for {resolution}: "
                f"{start_dt} -> {end_dt}"
            )

            current = (
                chunk_end + step
            )

            time.sleep(0.20)

            continue

        # ====================================================
        # PARSE CANDLES
        # ====================================================

        for candle in data:

            try:

                if isinstance(
                    candle,
                    dict
                ):

                    ts = (
                        candle.get("time")
                        or
                        candle.get("timestamp")
                        or
                        candle.get("t")
                    )

                    open_price = (
                        candle.get("open")
                        or
                        candle.get("o")
                    )

                    high = (
                        candle.get("high")
                        or
                        candle.get("h")
                    )

                    low = (
                        candle.get("low")
                        or
                        candle.get("l")
                    )

                    close = (
                        candle.get("close")
                        or
                        candle.get("c")
                    )

                    volume = (
                        candle.get("volume")
                        or
                        candle.get("v")
                        or
                        0
                    )

                else:

                    ts = candle[0]

                    open_price = candle[1]

                    high = candle[2]

                    low = candle[3]

                    close = candle[4]

                    volume = (
                        candle[5]
                        if len(candle) > 5
                        else 0
                    )

                ts = float(ts)

                # milliseconds -> seconds
                if ts > 10_000_000_000:

                    ts /= 1000.0

                rows.append(
                    [
                        pd.to_datetime(
                            ts,
                            unit="s",
                            utc=True
                        ),

                        safe_float(
                            open_price
                        ),

                        safe_float(
                            high
                        ),

                        safe_float(
                            low
                        ),

                        safe_float(
                            close
                        ),

                        safe_float(
                            volume
                        ),
                    ]
                )

            except Exception:

                continue

        current = (
            chunk_end + step
        )

        time.sleep(0.20)

    # ========================================================
    # VALIDATION
    # ========================================================

    if not rows:

        raise RuntimeError(
            f"No historical data received "
            f"for {resolution}"
        )

    df = pd.DataFrame(
        rows,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ],
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    return df


# ============================================================
# PREPARE DATAFRAME
# ============================================================

def prepare_dataframe(df):

    df = df.copy()

    numeric_columns = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for column in numeric_columns:

        df[column] = pd.to_numeric(
            df[column],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
        ]
    )

    df = df.drop_duplicates(
        subset=["timestamp"]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    return df


# ============================================================
# REMOVE INCOMPLETE LAST CANDLE
# ============================================================

def remove_incomplete_candle(
    df,
    resolution,
):

    if df.empty:

        return df

    now = pd.Timestamp.now(
        tz="UTC"
    )

    duration = (
        resolution_timedelta(
            resolution
        )
    )

    last_timestamp = (
        df.iloc[-1]["timestamp"]
    )

    last_close_time = (
        last_timestamp + duration
    )

    if last_close_time > now:

        df = df.iloc[:-1].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# ADD CLOSE TIME
# ============================================================

def add_close_time(
    df,
    resolution,
):

    df = df.copy()

    df["close_time"] = (
        df["timestamp"]
        +
        resolution_timedelta(
            resolution
        )
    )

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def add_ichimoku(df):

    df = df.copy()

    high = df["high"]

    low = df["low"]

    # --------------------------------------------------------
    # Tenkan
    # --------------------------------------------------------

    df["tenkan"] = (
        high.rolling(
            TENKAN_PERIOD
        ).max()
        +
        low.rolling(
            TENKAN_PERIOD
        ).min()
    ) / 2.0

    # --------------------------------------------------------
    # Kijun
    # --------------------------------------------------------

    df["kijun"] = (
        high.rolling(
            KIJUN_PERIOD
        ).max()
        +
        low.rolling(
            KIJUN_PERIOD
        ).min()
    ) / 2.0

    # --------------------------------------------------------
    # Senkou A
    # --------------------------------------------------------

    df["senkou_a"] = (
        df["tenkan"]
        +
        df["kijun"]
    ) / 2.0

    # --------------------------------------------------------
    # Senkou B
    # --------------------------------------------------------

    df["senkou_b"] = (
        high.rolling(
            SENKOU_B_PERIOD
        ).max()
        +
        low.rolling(
            SENKOU_B_PERIOD
        ).min()
    ) / 2.0

    # --------------------------------------------------------
    # Visible Kumo
    #
    # Senkou values are plotted 26 periods forward.
    # Therefore current visible cloud is based on
    # values calculated 26 candles earlier.
    # --------------------------------------------------------

    shift = KIJUN_PERIOD

    df["visible_senkou_a"] = (
        df["senkou_a"].shift(
            shift
        )
    )

    df["visible_senkou_b"] = (
        df["senkou_b"].shift(
            shift
        )
    )

    df["kumo_top"] = df[
        [
            "visible_senkou_a",
            "visible_senkou_b",
        ]
    ].max(axis=1)

    df["kumo_bottom"] = df[
        [
            "visible_senkou_a",
            "visible_senkou_b",
        ]
    ].min(axis=1)

    return df


# ============================================================
# ATR
# ============================================================

def add_atr(df):

    df = df.copy()

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

    true_range = pd.concat(
        [
            tr1,
            tr2,
            tr3,
        ],
        axis=1,
    ).max(axis=1)

    df["atr"] = (
        true_range
        .rolling(
            ATR_PERIOD
        )
        .mean()
    )

    return df


# ============================================================
# LATEST CLOSED ROW
# ============================================================

def get_latest_closed_row(
    df,
    decision_time,
):

    if df.empty:

        return None

    eligible = df[
        df["close_time"]
        <= decision_time
    ]

    if eligible.empty:

        return None

    return eligible.iloc[-1]


# ============================================================
# PREVIOUS CLOSED ROW
# ============================================================

def get_previous_closed_row(
    df,
    decision_time,
):

    if df.empty:

        return None

    eligible = df[
        df["close_time"]
        <= decision_time
    ]

    if len(eligible) < 2:

        return None

    return eligible.iloc[-2]


# ============================================================
# 1H MARKET REGIME
# ============================================================

def get_regime(row):

    if row is None:

        return None

    required = [
        "close",
        "tenkan",
        "kijun",
        "kumo_top",
        "kumo_bottom",
    ]

    for column in required:

        if pd.isna(
            row[column]
        ):

            return None

    price = row["close"]

    tenkan = row["tenkan"]

    kijun = row["kijun"]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if (
        price > row["kumo_top"]
        and
        tenkan > kijun
    ):

        return "LONG"

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if (
        price < row["kumo_bottom"]
        and
        tenkan < kijun
    ):

        return "SHORT"

    return None


# ============================================================
# KIJUN SLOPE
# ============================================================

def kijun_slope_is_valid(
    current_row,
    previous_row,
    direction,
):

    if (
        current_row is None
        or
        previous_row is None
    ):

        return False

    current_kijun = (
        current_row["kijun"]
    )

    previous_kijun = (
        previous_row["kijun"]
    )

    if (
        pd.isna(current_kijun)
        or
        pd.isna(previous_kijun)
    ):

        return False

    if direction == "LONG":

        return (
            current_kijun
            >
            previous_kijun
        )

    if direction == "SHORT":

        return (
            current_kijun
            <
            previous_kijun
        )

    return False


# ============================================================
# EQUILIBRIUM ZONE
# ============================================================

def equilibrium_zone(
    kijun,
    atr,
):

    if (
        pd.isna(kijun)
        or
        pd.isna(atr)
        or
        atr <= 0
    ):

        return None, None

    distance = (
        atr
        *
        EQUILIBRIUM_ATR_MULT
    )

    return (
        kijun - distance,
        kijun + distance,
    )


# ============================================================
# CANDLE INTERACTS WITH EQUILIBRIUM
# ============================================================

def candle_interacts_with_equilibrium(
    row,
    kijun,
    atr,
):

    if row is None:

        return False

    lower, upper = (
        equilibrium_zone(
            kijun,
            atr
        )
    )

    if lower is None:

        return False

    return (
        row["high"] >= lower
        and
        row["low"] <= upper
    )


# ============================================================
# 15M REACTION
# ============================================================

def valid_15m_reaction(
    current_row,
    previous_row,
    direction,
):

    if (
        current_row is None
        or
        previous_row is None
    ):

        return False

    if (
        pd.isna(
            current_row["kijun"]
        )
        or
        pd.isna(
            current_row["atr"]
        )
    ):

        return False

    kijun = (
        current_row["kijun"]
    )

    atr = (
        current_row["atr"]
    )

    if not candle_interacts_with_equilibrium(
        current_row,
        kijun,
        atr,
    ):

        return False

    close = (
        current_row["close"]
    )

    open_price = (
        current_row["open"]
    )

    if direction == "LONG":

        bullish = (
            close > open_price
        )

        directional_close = (
            close >= kijun
        )

        return (
            bullish
            and
            directional_close
        )

    if direction == "SHORT":

        bearish = (
            close < open_price
        )

        directional_close = (
            close <= kijun
        )

        return (
            bearish
            and
            directional_close
        )

    return False


# ============================================================
# 5M ENTRY TRIGGER
# ============================================================

def valid_5m_trigger(
    current_row,
    previous_row,
    direction,
):

    if (
        current_row is None
        or
        previous_row is None
    ):

        return False

    required = [
        "tenkan",
        "kijun",
        "atr",
    ]

    for column in required:

        if pd.isna(
            current_row[column]
        ):

            return False

    close = (
        current_row["close"]
    )

    open_price = (
        current_row["open"]
    )

    previous_high = (
        previous_row["high"]
    )

    previous_low = (
        previous_row["low"]
    )

    tenkan = (
        current_row["tenkan"]
    )

    kijun = (
        current_row["kijun"]
    )

    atr = (
        current_row["atr"]
    )

    if not candle_interacts_with_equilibrium(
        current_row,
        kijun,
        atr,
    ):

        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        bullish = (
            close > open_price
        )

        tenkan_reclaim = (
            close > tenkan
        )

        structure_break = (
            close > previous_high
        )

        return (
            bullish
            and
            (
                tenkan_reclaim
                or
                structure_break
            )
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        bearish = (
            close < open_price
        )

        tenkan_loss = (
            close < tenkan
        )

        structure_break = (
            close < previous_low
        )

        return (
            bearish
            and
            (
                tenkan_loss
                or
                structure_break
            )
        )

    return False


# ============================================================
# CONFIRMED PIVOT LEVELS
# ============================================================

def find_confirmed_pivot_levels(
    df,
    decision_time,
):

    if df.empty:

        return [], []

    eligible = df[
        df["close_time"]
        <= decision_time
    ].copy()

    minimum = (
        PIVOT_LEFT
        +
        PIVOT_RIGHT
        +
        1
    )

    if len(eligible) < minimum:

        return [], []

    highs = []
    lows = []

    high_values = (
        eligible["high"]
        .to_numpy()
    )

    low_values = (
        eligible["low"]
        .to_numpy()
    )

    for i in range(
        PIVOT_LEFT,
        len(eligible)
        -
        PIVOT_RIGHT
    ):

        high_value = (
            high_values[i]
        )

        left_highs = (
            high_values[
                i - PIVOT_LEFT:i
            ]
        )

        right_highs = (
            high_values[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ]
        )

        if (
            high_value
            >
            left_highs.max()
            and
            high_value
            >
            right_highs.max()
        ):

            highs.append(
                float(high_value)
            )

        low_value = (
            low_values[i]
        )

        left_lows = (
            low_values[
                i - PIVOT_LEFT:i
            ]
        )

        right_lows = (
            low_values[
                i + 1:
                i + 1 + PIVOT_RIGHT
            ]
        )

        if (
            low_value
            <
            left_lows.min()
            and
            low_value
            <
            right_lows.min()
        ):

            lows.append(
                float(low_value)
            )

    return highs, lows


# ============================================================
# STOP LOSS
# ============================================================

def calculate_stop_loss(
    direction,
    entry,
    row_5m,
    df5,
):

    atr = row_5m["atr"]

    if (
        pd.isna(atr)
        or
        atr <= 0
    ):

        return None

    current_index = int(
        row_5m.name
    )

    start_index = max(
        0,
        current_index
        -
        SWING_LOOKBACK_5M
    )

    recent = df5.iloc[
        start_index:
        current_index + 1
    ]

    if recent.empty:

        return None

    buffer = (
        atr
        *
        SL_ATR_BUFFER
    )

    kijun = (
        row_5m["kijun"]
    )

    candidates = []

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        swing_low = (
            recent["low"].min()
        )

        candidates.append(
            swing_low - buffer
        )

        if not pd.isna(kijun):

            candidates.append(
                kijun - buffer
            )

        candidates = [
            value
            for value in candidates
            if value < entry
        ]

        if not candidates:

            return None

        stop = max(
            candidates
        )

        distance_pct = (
            (
                entry
                -
                stop
            )
            /
            entry
            *
            100
        )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        swing_high = (
            recent["high"].max()
        )

        candidates.append(
            swing_high + buffer
        )

        if not pd.isna(kijun):

            candidates.append(
                kijun + buffer
            )

        candidates = [
            value
            for value in candidates
            if value > entry
        ]

        if not candidates:

            return None

        stop = min(
            candidates
        )

        distance_pct = (
            (
                stop
                -
                entry
            )
            /
            entry
            *
            100
        )

    if distance_pct <= 0:

        return None

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

def calculate_take_profit(
    direction,
    entry,
    stop,
    pivot_highs,
    pivot_lows,
):

    if (
        entry <= 0
        or
        stop is None
    ):

        return None

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        risk = (
            entry
            -
            stop
        )

        if risk <= 0:

            return None

        resistance_levels = sorted(
            set(
                float(value)
                for value in pivot_highs
                if value > entry
            )
        )

        for level in resistance_levels:

            reward = (
                level
                -
                entry
            )

            if reward <= 0:

                continue

            tp_distance_pct = (
                reward
                /
                entry
                *
                100
            )

            if (
                tp_distance_pct
                >
                MAX_TP_DISTANCE_PCT
            ):

                continue

            rr = (
                reward
                /
                risk
            )

            if rr > MIN_RR:

                return float(level)

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        risk = (
            stop
            -
            entry
        )

        if risk <= 0:

            return None

        support_levels = sorted(
            set(
                float(value)
                for value in pivot_lows
                if value < entry
            ),
            reverse=True
        )

        for level in support_levels:

            reward = (
                entry
                -
                level
            )

            if reward <= 0:

                continue

            tp_distance_pct = (
                reward
                /
                entry
                *
                100
            )

            if (
                tp_distance_pct
                >
                MAX_TP_DISTANCE_PCT
            ):

                continue

            rr = (
                reward
                /
                risk
            )

            if rr > MIN_RR:

                return float(level)

    return None


# ============================================================
# TRADE PNL
# ============================================================

def calculate_trade_pnl(
    direction,
    entry,
    exit_price,
):

    if direction == "LONG":

        gross_return = (
            exit_price
            -
            entry
        ) / entry

    else:

        gross_return = (
            entry
            -
            exit_price
        ) / entry

    net_return = (
        gross_return
        -
        FEE_RATE * 2
    )

    return net_return


# ============================================================
# CHECK TRADE EXIT
# ============================================================

def check_trade_exit(
    trade,
    candle,
):

    direction = trade[
        "direction"
    ]

    stop = trade[
        "stop"
    ]

    target = trade[
        "target"
    ]

    high = candle[
        "high"
    ]

    low = candle[
        "low"
    ]

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        hit_sl = (
            low <= stop
        )

        hit_tp = (
            high >= target
        )

        # Conservative assumption:
        # if both are hit in the same candle,
        # SL is assumed first.

        if hit_sl and hit_tp:

            return (
                stop,
                "SL"
            )

        if hit_sl:

            return (
                stop,
                "SL"
            )

        if hit_tp:

            return (
                target,
                "TP"
            )

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    else:

        hit_sl = (
            high >= stop
        )

        hit_tp = (
            low <= target
        )

        if hit_sl and hit_tp:

            return (
                stop,
                "SL"
            )

        if hit_sl:

            return (
                stop,
                "SL"
            )

        if hit_tp:

            return (
                target,
                "TP"
            )

    return None, None


# ============================================================
# MAIN BACKTEST
# ============================================================

def run_backtest():

    print("=" * 70)

    print(
        "ICHIMOKU EQUILIBRIUM v1.0"
    )

    print(
        "12-MONTH KRAKEN FUTURES BACKTEST"
    )

    print("=" * 70)

    now = pd.Timestamp.now(
        tz="UTC"
    )

    backtest_end = now.floor(
        "5min"
    )

    backtest_start = (
        backtest_end
        -
        pd.Timedelta(
            days=BACKTEST_DAYS
        )
    )

    data_start = (
        backtest_start
        -
        pd.Timedelta(
            days=WARMUP_DAYS
        )
    )

    start_ts = int(
        data_start.timestamp()
    )

    end_ts = int(
        backtest_end.timestamp()
    )

    print(
        f"Data start : {data_start}"
    )

    print(
        f"Backtest   : {backtest_start}"
    )

    print(
        f"Backtest end: {backtest_end}"
    )

    print()

    # ========================================================
    # DOWNLOAD
    # ========================================================

    df5 = fetch_kraken_range(
        SYMBOL,
        "5m",
        start_ts,
        end_ts,
    )

    df15 = fetch_kraken_range(
        SYMBOL,
        "15m",
        start_ts,
        end_ts,
    )

    df1h = fetch_kraken_range(
        SYMBOL,
        "1h",
        start_ts,
        end_ts,
    )

    # ========================================================
    # PREPARE
    # ========================================================

    df5 = prepare_dataframe(
        df5
    )

    df15 = prepare_dataframe(
        df15
    )

    df1h = prepare_dataframe(
        df1h
    )

    df5 = remove_incomplete_candle(
        df5,
        "5m"
    )

    df15 = remove_incomplete_candle(
        df15,
        "15m"
    )

    df1h = remove_incomplete_candle(
        df1h,
        "1h"
    )

    # ========================================================
    # INDICATORS
    # ========================================================

    df5 = add_ichimoku(
        df5
    )

    df5 = add_atr(
        df5
    )

    df15 = add_ichimoku(
        df15
    )

    df15 = add_atr(
        df15
    )

    df1h = add_ichimoku(
        df1h
    )

    df1h = add_atr(
        df1h
    )

    # ========================================================
    # CLOSE TIMES
    # ========================================================

    df5 = add_close_time(
        df5,
        "5m"
    )

    df15 = add_close_time(
        df15,
        "15m"
    )

    df1h = add_close_time(
        df1h,
        "1h"
    )

    # ========================================================
    # BACKTEST WINDOW
    # ========================================================

    df5_test = df5[
        df5["timestamp"]
        >= backtest_start
    ].copy()

    df5_test = df5_test[
        df5_test["timestamp"]
        < backtest_end
    ].copy()

    df5_test = df5_test.reset_index(
        drop=True
    )

    print(
        f"5M candles for backtest: "
        f"{len(df5_test)}"
    )

    print()

    # ========================================================
    # STATE
    # ========================================================

    capital = (
        INITIAL_CAPITAL
    )

    active_trade = None

    trades = []

    equity_rows = []

    trade_counter = 0

    # ========================================================
    # MAIN LOOP
    # ========================================================

    for index in range(
        len(df5_test)
    ):

        current_5m = (
            df5_test.iloc[index]
        )

        candle_close_time = (
            current_5m[
                "close_time"
            ]
        )

        closed_this_bar = False

        # ====================================================
        # MANAGE ACTIVE TRADE
        # ====================================================

        if active_trade is not None:

            exit_price, exit_reason = (
                check_trade_exit(
                    active_trade,
                    current_5m
                )
            )

            if exit_price is not None:

                net_return = (
                    calculate_trade_pnl(
                        active_trade[
                            "direction"
                        ],
                        active_trade[
                            "entry"
                        ],
                        exit_price,
                    )
                )

                starting_capital = (
                    active_trade[
                        "capital_before"
                    ]
                )

                pnl_amount = (
                    starting_capital
                    *
                    net_return
                )

                capital = (
                    starting_capital
                    +
                    pnl_amount
                )

                entry = (
                    active_trade[
                        "entry"
                    ]
                )

                if (
                    active_trade[
                        "direction"
                    ]
                    ==
                    "LONG"
                ):

                    price_change_pct = (
                        (
                            exit_price
                            -
                            entry
                        )
                        /
                        entry
                        *
                        100
                    )

                else:

                    price_change_pct = (
                        (
                            entry
                            -
                            exit_price
                        )
                        /
                        entry
                        *
                        100
                    )

                trades.append(
                    {
                        "trade_id":
                            active_trade[
                                "trade_id"
                            ],

                        "direction":
                            active_trade[
                                "direction"
                            ],

                        "entry_time":
                            active_trade[
                                "entry_time"
                            ],

                        "exit_time":
                            candle_close_time,

                        "entry":
                            entry,

                        "stop":
                            active_trade[
                                "stop"
                            ],

                        "target":
                            active_trade[
                                "target"
                            ],

                        "exit":
                            exit_price,

                        "exit_reason":
                            exit_reason,

                        "price_change_pct":
                            price_change_pct,

                        "net_return_pct":
                            net_return * 100,

                        "pnl":
                            pnl_amount,

                        "capital_after":
                            capital,
                    }
                )

                print(
                    f"TRADE "
                    f"#{active_trade['trade_id']} "
                    f"{active_trade['direction']} "
                    f"{exit_reason} | "
                    f"Entry={entry:.8f} "
                    f"Exit={exit_price:.8f} "
                    f"PnL="
                    f"{net_return * 100:.3f}%"
                )

                active_trade = None

                closed_this_bar = True

        # ====================================================
        # DO NOT RE-ENTER SAME BAR
        # ====================================================

        if closed_this_bar:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # IF STILL OPEN
        # ====================================================

        if active_trade is not None:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # DECISION TIME
        # ====================================================

        decision_time = (
            candle_close_time
        )

        # ====================================================
        # 1H REGIME
        # ====================================================

        row_1h = (
            get_latest_closed_row(
                df1h,
                decision_time
            )
        )

        previous_1h = (
            get_previous_closed_row(
                df1h,
                decision_time
            )
        )

        regime = get_regime(
            row_1h
        )

        if regime is None:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        if not kijun_slope_is_valid(
            row_1h,
            previous_1h,
            regime,
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # 15M
        # ====================================================

        row_15m = (
            get_latest_closed_row(
                df15,
                decision_time
            )
        )

        previous_15m = (
            get_previous_closed_row(
                df15,
                decision_time
            )
        )

        if (
            row_15m is None
            or
            previous_15m is None
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        if not valid_15m_reaction(
            row_15m,
            previous_15m,
            regime,
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # PREVIOUS 5M
        # ====================================================

        if index <= 0:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        previous_5m = (
            df5_test.iloc[
                index - 1
            ]
        )

        # ====================================================
        # 5M ENTRY TRIGGER
        # ====================================================

        if not valid_5m_trigger(
            current_5m,
            previous_5m,
            regime,
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # ENTRY
        # ====================================================

        entry = float(
            current_5m["close"]
        )

        if entry <= 0:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # SL
        # ====================================================

        stop = calculate_stop_loss(
            regime,
            entry,
            current_5m,
            df5_test,
        )

        if stop is None:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # CONFIRMED 15M PIVOTS
        # ====================================================

        pivot_highs, pivot_lows = (
            find_confirmed_pivot_levels(
                df15,
                decision_time,
            )
        )

        # ====================================================
        # TP
        # ====================================================

        target = calculate_take_profit(
            regime,
            entry,
            stop,
            pivot_highs,
            pivot_lows,
        )

        if target is None:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # RR
        # ====================================================

        if regime == "LONG":

            risk = (
                entry
                -
                stop
            )

            reward = (
                target
                -
                entry
            )

        else:

            risk = (
                stop
                -
                entry
            )

            reward = (
                entry
                -
                target
            )

        if (
            risk <= 0
            or
            reward <= 0
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        rr = (
            reward
            /
            risk
        )

        if rr <= MIN_RR:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        sl_distance_pct = (
            risk
            /
            entry
            *
            100
        )

        tp_distance_pct = (
            reward
            /
            entry
            *
            100
        )

        if (
            sl_distance_pct
            >
            MAX_SL_DISTANCE_PCT
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        if (
            tp_distance_pct
            >
            MAX_TP_DISTANCE_PCT
        ):

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ====================================================
        # OPEN TRADE
        # ====================================================

        trade_counter += 1

        active_trade = {

            "trade_id":
                trade_counter,

            "direction":
                regime,

            "entry_time":
                candle_close_time,

            "entry":
                entry,

            "stop":
                stop,

            "target":
                target,

            "rr":
                rr,

            "capital_before":
                capital,
        }

        print(
            f"OPEN "
            f"#{trade_counter} "
            f"{regime} | "
            f"Entry={entry:.8f} "
            f"SL={stop:.8f} "
            f"TP={target:.8f} "
            f"RR={rr:.2f}"
        )

        equity_rows.append(
            {
                "timestamp":
                    candle_close_time,

                "equity":
                    capital,
            }
        )

    # ========================================================
    # CLOSE REMAINING TRADE
    # ========================================================

    if active_trade is not None:

        last_row = (
            df5_test.iloc[-1]
        )

        exit_price = float(
            last_row["close"]
        )

        net_return = (
            calculate_trade_pnl(
                active_trade[
                    "direction"
                ],
                active_trade[
                    "entry"
                ],
                exit_price,
            )
        )

        starting_capital = (
            active_trade[
                "capital_before"
            ]
        )

        pnl_amount = (
            starting_capital
            *
            net_return
        )

        capital = (
            starting_capital
            +
            pnl_amount
        )

        entry = (
            active_trade["entry"]
        )

        if (
            active_trade["direction"]
            ==
            "LONG"
        ):

            price_change_pct = (
                (
                    exit_price
                    -
                    entry
                )
                /
                entry
                *
                100
            )

        else:

            price_change_pct = (
                (
                    entry
                    -
                    exit_price
                )
                /
                entry
                *
                100
            )

        trades.append(
            {
                "trade_id":
                    active_trade[
                        "trade_id"
                    ],

                "direction":
                    active_trade[
                        "direction"
                    ],

                "entry_time":
                    active_trade[
                        "entry_time"
                    ],

                "exit_time":
                    last_row[
                        "close_time"
                    ],

                "entry":
                    entry,

                "stop":
                    active_trade[
                        "stop"
                    ],

                "target":
                    active_trade[
                        "target"
                    ],

                "exit":
                    exit_price,

                "exit_reason":
                    "END_OF_BACKTEST",

                "price_change_pct":
                    price_change_pct,

                "net_return_pct":
                    net_return * 100,

                "pnl":
                    pnl_amount,

                "capital_after":
                    capital,
            }
        )

        active_trade = None

    # ========================================================
    # SAVE TRADE DATA
    # ========================================================

    trades_df = pd.DataFrame(
        trades
    )

    if trades_df.empty:

        trades_df = pd.DataFrame(
            columns=[
                "trade_id",
                "direction",
                "entry_time",
                "exit_time",
                "entry",
                "stop",
                "target",
                "exit",
                "exit_reason",
                "price_change_pct",
                "net_return_pct",
                "pnl",
                "capital_after",
            ]
        )

    trades_df.to_csv(
        TRADES_FILE,
        index=False
    )

    # ========================================================
    # SAVE EQUITY
    # ========================================================

    equity_df = pd.DataFrame(
        equity_rows
    )

    if equity_df.empty:

        equity_df = pd.DataFrame(
            columns=[
                "timestamp",
                "equity",
            ]
        )

    equity_df.to_csv(
        EQUITY_FILE,
        index=False
    )

    # ========================================================
    # PERFORMANCE
    # ========================================================

    total_trades = len(
        trades_df
    )

    if total_trades > 0:

        wins = int(
            (
                trades_df["pnl"]
                > 0
            ).sum()
        )

        losses = int(
            (
                trades_df["pnl"]
                <= 0
            ).sum()
        )

        win_rate = (
            wins
            /
            total_trades
            *
            100
        )

        gross_profit = float(
            trades_df.loc[
                trades_df["pnl"] > 0,
                "pnl"
            ].sum()
        )

        gross_loss = abs(
            float(
                trades_df.loc[
                    trades_df["pnl"] < 0,
                    "pnl"
                ].sum()
            )
        )

        if gross_loss > 0:

            profit_factor = (
                gross_profit
                /
                gross_loss
            )

        else:

            profit_factor = float(
                "inf"
            )

        avg_trade = float(
            trades_df[
                "net_return_pct"
            ].mean()
        )

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

        long_wins = int(
            (
                long_df["pnl"]
                > 0
            ).sum()
        )

        short_wins = int(
            (
                short_df["pnl"]
                > 0
            ).sum()
        )

        long_wr = (
            long_wins
            /
            len(long_df)
            *
            100
            if len(long_df) > 0
            else 0
        )

        short_wr = (
            short_wins
            /
            len(short_df)
            *
            100
            if len(short_df) > 0
            else 0
        )

    else:

        wins = 0
        losses = 0
        win_rate = 0.0

        gross_profit = 0.0
        gross_loss = 0.0

        profit_factor = 0.0
        avg_trade = 0.0

        long_df = pd.DataFrame()
        short_df = pd.DataFrame()

        long_wr = 0.0
        short_wr = 0.0

    # ========================================================
    # MAX DRAWDOWN
    # ========================================================

    if not trades_df.empty:

        equity_series = (
            trades_df[
                "capital_after"
            ]
        )

        running_peak = (
            equity_series
            .cummax()
        )

        drawdown = (
            (
                equity_series
                -
                running_peak
            )
            /
            running_peak
            *
            100
        )

        max_drawdown = float(
            drawdown.min()
        )

    else:

        max_drawdown = 0.0

    # ========================================================
    # NET PNL
    # ========================================================

    net_pnl = (
        capital
        -
        INITIAL_CAPITAL
    )

    net_pnl_pct = (
        net_pnl
        /
        INITIAL_CAPITAL
        *
        100
    )

    # ========================================================
    # EXIT COUNTS
    # ========================================================

    if not trades_df.empty:

        exit_counts = (
            trades_df[
                "exit_reason"
            ]
            .value_counts()
            .to_dict()
        )

    else:

        exit_counts = {}

    # ========================================================
    # MONTHLY PERFORMANCE
    # ========================================================

    monthly_text = []

    if not trades_df.empty:

        temp = trades_df.copy()

        temp["exit_time"] = (
            pd.to_datetime(
                temp["exit_time"],
                utc=True
            )
        )

        temp["month"] = (
            temp["exit_time"]
            .dt.strftime(
                "%Y-%m"
            )
        )

        for month, group in (
            temp.groupby("month")
        ):

            month_pnl = float(
                group["pnl"].sum()
            )

            month_wr = (
                (
                    group["pnl"]
                    > 0
                ).sum()
                /
                len(group)
                *
                100
            )

            monthly_text.append(
                f"{month}: "
                f"Trades={len(group)} "
                f"WR={month_wr:.2f}% "
                f"PnL={month_pnl:.4f}"
            )

    # ========================================================
    # REPORT
    # ========================================================

    report_lines = []

    report_lines.append(
        "=" * 60
    )

    report_lines.append(
        "ICHIMOKU EQUILIBRIUM v1.0"
    )

    report_lines.append(
        "12-MONTH KRAKEN FUTURES BACKTEST"
    )

    report_lines.append(
        "=" * 60
    )

    report_lines.append(
        f"Symbol: {SYMBOL}"
    )

    report_lines.append(
        f"Backtest start: "
        f"{backtest_start}"
    )

    report_lines.append(
        f"Backtest end: "
        f"{backtest_end}"
    )

    report_lines.append(
        f"Initial capital: "
        f"{INITIAL_CAPITAL:.2f}"
    )

    report_lines.append(
        f"Final capital: "
        f"{capital:.2f}"
    )

    report_lines.append(
        f"Net PnL: "
        f"{net_pnl:.4f}"
    )

    report_lines.append(
        f"Net return: "
        f"{net_pnl_pct:.3f}%"
    )

    report_lines.append("")

    report_lines.append(
        "TRADE STATISTICS"
    )

    report_lines.append(
        "-" * 60
    )

    report_lines.append(
        f"Total trades: "
        f"{total_trades}"
    )

    report_lines.append(
        f"Wins: {wins}"
    )

    report_lines.append(
        f"Losses: {losses}"
    )

    report_lines.append(
        f"Win rate: "
        f"{win_rate:.2f}%"
    )

    if np.isinf(
        profit_factor
    ):

        pf_text = "INF"

    else:

        pf_text = (
            f"{profit_factor:.3f}"
        )

    report_lines.append(
        f"Profit factor: "
        f"{pf_text}"
    )

    report_lines.append(
        f"Average trade: "
        f"{avg_trade:.4f}%"
    )

    report_lines.append(
        f"Max drawdown: "
        f"{max_drawdown:.3f}%"
    )

    report_lines.append("")

    report_lines.append(
        "LONG / SHORT"
    )

    report_lines.append(
        "-" * 60
    )

    report_lines.append(
        f"Long trades: "
        f"{len(long_df)}"
    )

    report_lines.append(
        f"Long WR: "
        f"{long_wr:.2f}%"
    )

    report_lines.append(
        f"Short trades: "
        f"{len(short_df)}"
    )

    report_lines.append(
        f"Short WR: "
        f"{short_wr:.2f}%"
    )

    report_lines.append("")

    report_lines.append(
        "EXIT TYPES"
    )

    report_lines.append(
        "-" * 60
    )

    if exit_counts:

        for reason, count in sorted(
            exit_counts.items()
        ):

            report_lines.append(
                f"{reason}: {count}"
            )

    else:

        report_lines.append(
            "No trades"
        )

    report_lines.append("")

    report_lines.append(
        "MONTHLY PERFORMANCE"
    )

    report_lines.append(
        "-" * 60
    )

    if monthly_text:

        report_lines.extend(
            monthly_text
        )

    else:

        report_lines.append(
            "No monthly trades"
        )

    report_lines.append("")

    report_lines.append(
        "STRATEGY PARAMETERS"
    )

    report_lines.append(
        "-" * 60
    )

    report_lines.append(
        f"Ichimoku: "
        f"{TENKAN_PERIOD}/"
        f"{KIJUN_PERIOD}/"
        f"{SENKOU_B_PERIOD}"
    )

    report_lines.append(
        f"Equilibrium ATR multiplier: "
        f"{EQUILIBRIUM_ATR_MULT}"
    )

    report_lines.append(
        f"SL ATR buffer: "
        f"{SL_ATR_BUFFER}"
    )

    report_lines.append(
        f"Max SL: "
        f"{MAX_SL_DISTANCE_PCT:.2f}%"
    )

    report_lines.append(
        f"Min RR: "
        f"{MIN_RR:.2f}"
    )

    report_lines.append(
        f"Max TP distance: "
        f"{MAX_TP_DISTANCE_PCT:.2f}%"
    )

    report_lines.append(
        f"Fee each side: "
        f"{FEE_RATE * 100:.3f}%"
    )

    report_lines.append(
        f"Round-trip fee: "
        f"{FEE_RATE * 2 * 100:.3f}%"
    )

    report_lines.append("")

    report_lines.append(
        f"Kraken endpoint: "
        f"/charts/v1/trade/"
        f"{SYMBOL}/{{resolution}}"
    )

    report_lines.append(
        f"Max candles/request: "
        f"{MAX_CANDLES_PER_REQUEST}"
    )

    report_lines.append(
        "NO LOOKAHEAD: ENABLED"
    )

    report_lines.append(
        "CLOSED CANDLES ONLY: ENABLED"
    )

    report_lines.append(
        "REAL TRADING: DISABLED"
    )

    report_lines.append(
        "=" * 60
    )

    report = "\n".join(
        report_lines
    )

    print()
    print(report)

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as file:

        file.write(
            report
        )

    print()

    print(
        "Files generated:"
    )

    print(
        f"- {TRADES_FILE}"
    )

    print(
        f"- {EQUITY_FILE}"
    )

    print(
        f"- {REPORT_FILE}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    run_backtest()
