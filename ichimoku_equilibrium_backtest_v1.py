# ============================================================
# ICHIMOKU EQUILIBRIUM v2.0
# 12-MONTH KRAKEN FUTURES BACKTEST
# ============================================================
#
# CORE IDEA:
#
# 1H  = EQUILIBRIUM / RANGE REGIME FILTER
# 15M = DEVIATION FROM KIJUN + REACTION
# 5M  = REVERSAL CONFIRMATION + ENTRY
#
# The strategy is MEAN-REVERSION oriented.
#
# IMPORTANT:
# - Strong 1H trends are rejected.
# - Price must move sufficiently away from equilibrium.
# - 15M must show a reaction back toward Kijun.
# - 5M must confirm the reversal.
# - CLOSED CANDLES ONLY
# - NO LOOKAHEAD
# - REAL TRADING DISABLED
#
# ============================================================

import time
import requests
import numpy as np
import pandas as pd

from datetime import timezone


# ============================================================
# CONFIG
# ============================================================

VERSION = "v2.0"

SYMBOL = "PF_DOGEUSD"

BASE_URL = "https://futures.kraken.com/api/charts/v1"

BACKTEST_DAYS = 365
WARMUP_DAYS = 10

INITIAL_CAPITAL = 100.0

# Kraken/LBank-style taker fee assumption
FEE_RATE = 0.0006
ROUND_TRIP_FEE = FEE_RATE * 2.0


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
# v2 EQUILIBRIUM FILTERS
# ============================================================

# Minimum 15M distance from Kijun before a setup can exist.
#
# Example:
# 0.50 = price must be at least 0.50 ATR away.
#
MIN_EQUILIBRIUM_DISTANCE_ATR = 0.50

# Maximum distance allowed for a normal mean-reversion setup.
# Avoid trying to catch extremely extended moves.
MAX_EQUILIBRIUM_DISTANCE_ATR = 2.00


# ============================================================
# 1H MARKET REGIME
# ============================================================

# Tenkan/Kijun separation relative to ATR.
# If too large, market is considered trending.
MAX_1H_TK_SEPARATION_ATR = 0.50

# Kijun slope relative to ATR.
# Large slope = directional market.
MAX_1H_KIJUN_SLOPE_ATR = 0.20

# Price may not be too far outside the visible cloud.
MAX_1H_CLOUD_EXTENSION_ATR = 0.75


# ============================================================
# 15M REACTION
# ============================================================

# Candle body must have at least this ratio of total range.
MIN_15M_BODY_RATIO = 0.25

# Current 15M candle must move toward Kijun.
REQUIRE_15M_RETURN_TOWARD_KIJUN = True


# ============================================================
# 5M REVERSAL
# ============================================================

MIN_5M_BODY_RATIO = 0.35

# Entry candle must cross/reclaim Tenkan.
REQUIRE_5M_TENKAN_CONFIRMATION = True

# Entry candle must move in the intended direction.
REQUIRE_5M_DIRECTIONAL_CLOSE = True


# ============================================================
# STOP LOSS
# ============================================================

SL_ATR_BUFFER = 0.25

SWING_LOOKBACK_5M = 12

MAX_SL_DISTANCE_PCT = 3.00


# ============================================================
# TAKE PROFIT
# ============================================================

MIN_RR = 1.50

MAX_TP_DISTANCE_PCT = 8.00

PIVOT_LEFT = 2
PIVOT_RIGHT = 2


# ============================================================
# HTTP
# ============================================================

REQUEST_TIMEOUT = 30

MAX_CANDLES_PER_REQUEST = 900


# ============================================================
# OUTPUT
# ============================================================

TRADES_FILE = (
    "doge_ichimoku_equilibrium_v2_12m_trades.csv"
)

EQUITY_FILE = (
    "doge_ichimoku_equilibrium_v2_12m_equity.csv"
)

REPORT_FILE = (
    "doge_ichimoku_equilibrium_v2_12m_report.txt"
)


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update(
    {
        "User-Agent":
            "Ichimoku-Equilibrium-Backtest/2.0"
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
# KRAKEN DATA
# ============================================================

def fetch_kraken_range(
    symbol,
    resolution,
    start_ts,
    end_ts,
):

    rows = []

    step = interval_seconds(resolution)

    chunk_seconds = (
        MAX_CANDLES_PER_REQUEST * step
    )

    current = int(start_ts)

    while current < int(end_ts):

        chunk_end = min(
            current + chunk_seconds,
            int(end_ts)
        )

        url = (
            f"{BASE_URL}/trade/"
            f"{symbol}/{resolution}"
            f"?from={current}"
            f"&to={chunk_end}"
        )

        print(
            f"Downloading {resolution}: "
            f"{pd.to_datetime(current, unit='s', utc=True)} "
            f"-> "
            f"{pd.to_datetime(chunk_end, unit='s', utc=True)}"
        )

        try:

            response = SESSION.get(
                url,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:

                print(
                    f"Kraken HTTP "
                    f"{response.status_code}"
                )

                print(
                    response.text[:1000]
                )

                response.raise_for_status()

            payload = response.json()

        except Exception as exc:

            raise RuntimeError(
                f"Kraken request failed "
                f"for {resolution}: {exc}"
            )

        data = payload.get(
            "candles",
            []
        )

        if not data:

            current = (
                chunk_end + step
            )

            time.sleep(0.20)

            continue

        for candle in data:

            try:

                if isinstance(candle, dict):

                    ts = (
                        candle.get("time")
                        or candle.get("timestamp")
                        or candle.get("t")
                    )

                    op = (
                        candle.get("open")
                        or candle.get("o")
                    )

                    hi = (
                        candle.get("high")
                        or candle.get("h")
                    )

                    lo = (
                        candle.get("low")
                        or candle.get("l")
                    )

                    cl = (
                        candle.get("close")
                        or candle.get("c")
                    )

                    vol = (
                        candle.get("volume")
                        or candle.get("v")
                        or 0
                    )

                else:

                    ts = candle[0]
                    op = candle[1]
                    hi = candle[2]
                    lo = candle[3]
                    cl = candle[4]

                    vol = (
                        candle[5]
                        if len(candle) > 5
                        else 0
                    )

                ts = float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000.0

                rows.append(
                    [
                        pd.to_datetime(
                            ts,
                            unit="s",
                            utc=True
                        ),
                        safe_float(op),
                        safe_float(hi),
                        safe_float(lo),
                        safe_float(cl),
                        safe_float(vol),
                    ]
                )

            except Exception:
                continue

        current = (
            chunk_end + step
        )

        time.sleep(0.20)

    if not rows:

        raise RuntimeError(
            f"No historical data for {resolution}"
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
# PREPARE
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
# REMOVE INCOMPLETE CANDLE
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

    close_time = (
        last_timestamp + duration
    )

    if close_time > now:

        df = df.iloc[:-1].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# CLOSE TIME
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

    df["senkou_a"] = (
        df["tenkan"]
        +
        df["kijun"]
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

    # Visible cloud only.
    # This avoids using future cloud values.
    shift = KIJUN_PERIOD

    df["visible_senkou_a"] = (
        df["senkou_a"].shift(shift)
    )

    df["visible_senkou_b"] = (
        df["senkou_b"].shift(shift)
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
        axis=1
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
# CLOSED ROW HELPERS
# ============================================================

def get_closed_rows(
    df,
    decision_time,
):

    return df[
        df["close_time"]
        <= decision_time
    ]


def get_latest_closed_row(
    df,
    decision_time,
):

    eligible = get_closed_rows(
        df,
        decision_time
    )

    if eligible.empty:
        return None

    return eligible.iloc[-1]


def get_previous_closed_row(
    df,
    decision_time,
):

    eligible = get_closed_rows(
        df,
        decision_time
    )

    if len(eligible) < 2:
        return None

    return eligible.iloc[-2]


# ============================================================
# 1H EQUILIBRIUM REGIME
# ============================================================

def valid_1h_equilibrium_regime(
    current,
    previous,
):

    if (
        current is None
        or previous is None
    ):
        return None

    required = [
        "close",
        "tenkan",
        "kijun",
        "atr",
        "kumo_top",
        "kumo_bottom",
    ]

    for column in required:

        if pd.isna(current[column]):
            return None

    price = float(
        current["close"]
    )

    tenkan = float(
        current["tenkan"]
    )

    kijun = float(
        current["kijun"]
    )

    atr = float(
        current["atr"]
    )

    if atr <= 0:
        return None

    # --------------------------------------------------------
    # 1H TK SEPARATION
    # --------------------------------------------------------

    tk_separation = (
        abs(tenkan - kijun)
        /
        atr
    )

    if (
        tk_separation
        >
        MAX_1H_TK_SEPARATION_ATR
    ):
        return None

    # --------------------------------------------------------
    # 1H KIJUN SLOPE
    # --------------------------------------------------------

    previous_kijun = float(
        previous["kijun"]
    )

    kijun_slope = (
        abs(kijun - previous_kijun)
        /
        atr
    )

    if (
        kijun_slope
        >
        MAX_1H_KIJUN_SLOPE_ATR
    ):
        return None

    # --------------------------------------------------------
    # PRICE VS CLOUD
    # --------------------------------------------------------

    cloud_top = float(
        current["kumo_top"]
    )

    cloud_bottom = float(
        current["kumo_bottom"]
    )

    if price > cloud_top:

        extension = (
            price - cloud_top
        ) / atr

        if (
            extension
            >
            MAX_1H_CLOUD_EXTENSION_ATR
        ):
            return None

    elif price < cloud_bottom:

        extension = (
            cloud_bottom - price
        ) / atr

        if (
            extension
            >
            MAX_1H_CLOUD_EXTENSION_ATR
        ):
            return None

    # --------------------------------------------------------
    # Determine side from position relative to Kijun
    # --------------------------------------------------------

    if price < kijun:
        return "LONG"

    if price > kijun:
        return "SHORT"

    return None


# ============================================================
# EQUILIBRIUM DISTANCE
# ============================================================

def equilibrium_distance(
    row,
):

    if row is None:
        return None

    if (
        pd.isna(row["close"])
        or pd.isna(row["kijun"])
        or pd.isna(row["atr"])
    ):
        return None

    atr = float(
        row["atr"]
    )

    if atr <= 0:
        return None

    distance = abs(
        float(row["close"])
        -
        float(row["kijun"])
    )

    return (
        distance / atr
    )


# ============================================================
# 15M REACTION
# ============================================================

def valid_15m_reaction(
    current,
    previous,
    direction,
):

    if (
        current is None
        or previous is None
    ):
        return False

    required = [
        "open",
        "high",
        "low",
        "close",
        "kijun",
        "atr",
    ]

    for column in required:

        if pd.isna(current[column]):
            return False

    atr = float(
        current["atr"]
    )

    if atr <= 0:
        return False

    current_close = float(
        current["close"]
    )

    current_open = float(
        current["open"]
    )

    current_high = float(
        current["high"]
    )

    current_low = float(
        current["low"]
    )

    kijun = float(
        current["kijun"]
    )

    candle_range = (
        current_high
        -
        current_low
    )

    if candle_range <= 0:
        return False

    body_ratio = (
        abs(
            current_close
            -
            current_open
        )
        /
        candle_range
    )

    if (
        body_ratio
        <
        MIN_15M_BODY_RATIO
    ):
        return False

    distance_atr = (
        abs(
            current_close
            -
            kijun
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

    previous_close = float(
        previous["close"]
    )

    # --------------------------------------------------------
    # LONG
    #
    # Price was below Kijun.
    # Current candle must show movement back upward.
    # --------------------------------------------------------

    if direction == "LONG":

        if current_close >= kijun:
            return False

        bullish = (
            current_close
            >
            current_open
        )

        return_toward_kijun = (
            current_close
            >
            previous_close
        )

        if REQUIRE_15M_RETURN_TOWARD_KIJUN:

            return (
                bullish
                and
                return_toward_kijun
            )

        return bullish

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if current_close <= kijun:
            return False

        bearish = (
            current_close
            <
            current_open
        )

        return_toward_kijun = (
            current_close
            <
            previous_close
        )

        if REQUIRE_15M_RETURN_TOWARD_KIJUN:

            return (
                bearish
                and
                return_toward_kijun
            )

        return bearish

    return False


# ============================================================
# 5M REVERSAL CONFIRMATION
# ============================================================

def valid_5m_trigger(
    current,
    previous,
    direction,
):

    if (
        current is None
        or previous is None
    ):
        return False

    required = [
        "open",
        "high",
        "low",
        "close",
        "tenkan",
        "kijun",
        "atr",
    ]

    for column in required:

        if pd.isna(current[column]):
            return False

    op = float(
        current["open"]
    )

    hi = float(
        current["high"]
    )

    lo = float(
        current["low"]
    )

    close = float(
        current["close"]
    )

    tenkan = float(
        current["tenkan"]
    )

    kijun = float(
        current["kijun"]
    )

    previous_close = float(
        previous["close"]
    )

    candle_range = (
        hi - lo
    )

    if candle_range <= 0:
        return False

    body_ratio = (
        abs(close - op)
        /
        candle_range
    )

    if (
        body_ratio
        <
        MIN_5M_BODY_RATIO
    ):
        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if close <= kijun:
            return False

        if REQUIRE_5M_DIRECTIONAL_CLOSE:

            if close <= op:
                return False

        if REQUIRE_5M_TENKAN_CONFIRMATION:

            if close <= tenkan:
                return False

        # Actual improvement:
        # current candle must be stronger than previous close.
        if close <= previous_close:
            return False

        return True

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if close >= kijun:
            return False

        if REQUIRE_5M_DIRECTIONAL_CLOSE:

            if close >= op:
                return False

        if REQUIRE_5M_TENKAN_CONFIRMATION:

            if close >= tenkan:
                return False

        if close >= previous_close:
            return False

        return True

    return False


# ============================================================
# CONFIRMED PIVOTS
# ============================================================

def find_confirmed_pivot_levels(
    df,
    decision_time,
):

    eligible = get_closed_rows(
        df,
        decision_time
    ).copy()

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
        eligible["high"].to_numpy()
    )

    low_values = (
        eligible["low"].to_numpy()
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

    atr = float(
        row_5m["atr"]
    )

    if atr <= 0:
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

    kijun = float(
        row_5m["kijun"]
    )

    if direction == "LONG":

        swing_low = float(
            recent["low"].min()
        )

        candidates = [
            swing_low - buffer
        ]

        if kijun < entry:
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
            (entry - stop)
            /
            entry
            *
            100
        )

    else:

        swing_high = float(
            recent["high"].max()
        )

        candidates = [
            swing_high + buffer
        ]

        if kijun > entry:
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
            (stop - entry)
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

    if direction == "LONG":

        risk = (
            entry - stop
        )

        if risk <= 0:
            return None

        resistance_levels = sorted(
            set(
                float(v)
                for v in pivot_highs
                if v > entry
            )
        )

        for level in resistance_levels:

            reward = (
                level - entry
            )

            if reward <= 0:
                continue

            tp_pct = (
                reward
                /
                entry
                *
                100
            )

            if (
                tp_pct
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

    else:

        risk = (
            stop - entry
        )

        if risk <= 0:
            return None

        support_levels = sorted(
            set(
                float(v)
                for v in pivot_lows
                if v < entry
            ),
            reverse=True
        )

        for level in support_levels:

            reward = (
                entry - level
            )

            if reward <= 0:
                continue

            tp_pct = (
                reward
                /
                entry
                *
                100
            )

            if (
                tp_pct
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
# EXIT
# ============================================================

def check_trade_exit(
    trade,
    candle,
):

    direction = (
        trade["direction"]
    )

    stop = float(
        trade["stop"]
    )

    target = float(
        trade["target"]
    )

    high = float(
        candle["high"]
    )

    low = float(
        candle["low"]
    )

    if direction == "LONG":

        hit_sl = (
            low <= stop
        )

        hit_tp = (
            high >= target
        )

        # Conservative assumption:
        # if both are hit in the same candle,
        # SL is counted first.
        if hit_sl:
            return stop, "SL"

        if hit_tp:
            return target, "TP"

    else:

        hit_sl = (
            high >= stop
        )

        hit_tp = (
            low <= target
        )

        if hit_sl:
            return stop, "SL"

        if hit_tp:
            return target, "TP"

    return None, None


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
            exit_price - entry
        ) / entry

    else:

        gross_return = (
            entry - exit_price
        ) / entry

    # Round trip fees.
    net_return = (
        gross_return
        -
        ROUND_TRIP_FEE
    )

    return net_return


# ============================================================
# MAIN BACKTEST
# ============================================================

def run_backtest():

    print("=" * 70)

    print(
        "ICHIMOKU EQUILIBRIUM v2.0"
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
        f"Backtest start: "
        f"{backtest_start}"
    )

    print(
        f"Backtest end: "
        f"{backtest_end}"
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

    df5 = prepare_dataframe(df5)
    df15 = prepare_dataframe(df15)
    df1h = prepare_dataframe(df1h)

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

    df5 = add_ichimoku(df5)
    df5 = add_atr(df5)

    df15 = add_ichimoku(df15)
    df15 = add_atr(df15)

    df1h = add_ichimoku(df1h)
    df1h = add_atr(df1h)

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
    # TEST WINDOW
    # ========================================================

    df5_test = df5[
        df5["timestamp"]
        >= backtest_start
    ].copy()

    df5_test = df5_test[
        df5_test["timestamp"]
        <
        backtest_end
    ].copy()

    df5_test = df5_test.reset_index(
        drop=True
    )

    print(
        f"5M candles: "
        f"{len(df5_test)}"
    )

    print()

    # ========================================================
    # STATE
    # ========================================================

    capital = INITIAL_CAPITAL

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
            current_5m["close_time"]
        )

        # ----------------------------------------------------
        # MANAGE ACTIVE TRADE
        # ----------------------------------------------------

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
                        active_trade["direction"],
                        active_trade["entry"],
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

                        "rr":
                            active_trade[
                                "rr"
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

                equity_rows.append(
                    {
                        "timestamp":
                            candle_close_time,

                        "equity":
                            capital,
                    }
                )

                continue

        # ----------------------------------------------------
        # IF TRADE STILL OPEN
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # DECISION TIME
        # ----------------------------------------------------

        decision_time = (
            candle_close_time
        )

        # ----------------------------------------------------
        # 1H EQUILIBRIUM REGIME
        # ----------------------------------------------------

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

        direction = (
            valid_1h_equilibrium_regime(
                row_1h,
                previous_1h
            )
        )

        if direction is None:

            equity_rows.append(
                {
                    "timestamp":
                        candle_close_time,

                    "equity":
                        capital,
                }
            )

            continue

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 15M EQUILIBRIUM DISTANCE
        # ----------------------------------------------------

        distance_atr = (
            equilibrium_distance(
                row_15m
            )
        )

        if distance_atr is None:

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
            distance_atr
            <
            MIN_EQUILIBRIUM_DISTANCE_ATR
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
            distance_atr
            >
            MAX_EQUILIBRIUM_DISTANCE_ATR
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

        # ----------------------------------------------------
        # 15M REACTION
        # ----------------------------------------------------

        if not valid_15m_reaction(
            row_15m,
            previous_15m,
            direction
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

        # ----------------------------------------------------
        # PREVIOUS 5M
        # ----------------------------------------------------

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

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        if not valid_5m_trigger(
            current_5m,
            previous_5m,
            direction
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

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = float(
            current_5m["close"]
        )

        if entry <= 0:

            continue

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        stop = calculate_stop_loss(
            direction,
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

        # ----------------------------------------------------
        # PIVOTS
        # ----------------------------------------------------

        pivot_highs, pivot_lows = (
            find_confirmed_pivot_levels(
                df15,
                decision_time
            )
        )

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        target = calculate_take_profit(
            direction,
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

        # ----------------------------------------------------
        # RR
        # ----------------------------------------------------

        if direction == "LONG":

            risk = (
                entry - stop
            )

            reward = (
                target - entry
            )

        else:

            risk = (
                stop - entry
            )

            reward = (
                entry - target
            )

        if (
            risk <= 0
            or
            reward <= 0
        ):

            continue

        rr = (
            reward
            /
            risk
        )

        if rr <= MIN_RR:

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

            continue

        if (
            tp_distance_pct
            >
            MAX_TP_DISTANCE_PCT
        ):

            continue

        # ----------------------------------------------------
        # OPEN TRADE
        # ----------------------------------------------------

        trade_counter += 1

        active_trade = {

            "trade_id":
                trade_counter,

            "direction":
                direction,

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
            f"{direction} | "
            f"Entry={entry:.8f} "
            f"SL={stop:.8f} "
            f"TP={target:.8f} "
            f"RR={rr:.2f} "
            f"Dev={distance_atr:.2f}ATR"
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
                active_trade["direction"],
                active_trade["entry"],
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

                "rr":
                    active_trade[
                        "rr"
                    ],

                "exit":
                    exit_price,

                "exit_reason":
                    "END_OF_BACKTEST",

                "price_change_pct":
                    net_return * 100,

                "net_return_pct":
                    net_return * 100,

                "pnl":
                    pnl_amount,

                "capital_after":
                    capital,
            }
        )

    # ========================================================
    # SAVE TRADES
    # ========================================================

    trades_df = pd.DataFrame(
        trades
    )

    trades_df.to_csv(
        TRADES_FILE,
        index=False
    )

    equity_df = pd.DataFrame(
        equity_rows
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

        profit_factor = (
            gross_profit
            /
            gross_loss
            if gross_loss > 0
            else float("inf")
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

        long_wr = (
            (
                long_df["pnl"] > 0
            ).sum()
            /
            len(long_df)
            *
            100
            if len(long_df) > 0
            else 0
        )

        short_wr = (
            (
                short_df["pnl"] > 0
            ).sum()
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
    # DRAW DOWN
    # ========================================================

    if not trades_df.empty:

        equity_series = (
            trades_df[
                "capital_after"
            ]
        )

        running_peak = (
            equity_series.cummax()
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
    # NET
    # ========================================================

    net_pnl = (
        capital
        -
        INITIAL_CAPITAL
    )

    net_return = (
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
    # MONTHLY
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
                    group["pnl"] > 0
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

    report = []

    report.append("=" * 60)

    report.append(
        "ICHIMOKU EQUILIBRIUM v2.0"
    )

    report.append(
        "12-MONTH KRAKEN FUTURES BACKTEST"
    )

    report.append("=" * 60)

    report.append(
        f"Symbol: {SYMBOL}"
    )

    report.append(
        f"Backtest start: "
        f"{backtest_start}"
    )

    report.append(
        f"Backtest end: "
        f"{backtest_end}"
    )

    report.append(
        f"Initial capital: "
        f"{INITIAL_CAPITAL:.2f}"
    )

    report.append(
        f"Final capital: "
        f"{capital:.2f}"
    )

    report.append(
        f"Net PnL: "
        f"{net_pnl:.4f}"
    )

    report.append(
        f"Net return: "
        f"{net_return:.3f}%"
    )

    report.append("")

    report.append(
        "TRADE STATISTICS"
    )

    report.append("-" * 60)

    report.append(
        f"Total trades: "
        f"{total_trades}"
    )

    report.append(
        f"Wins: {wins}"
    )

    report.append(
        f"Losses: {losses}"
    )

    report.append(
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

    report.append(
        f"Profit factor: "
        f"{pf_text}"
    )

    report.append(
        f"Average trade: "
        f"{avg_trade:.4f}%"
    )

    report.append(
        f"Max drawdown: "
        f"{max_drawdown:.3f}%"
    )

    report.append("")

    report.append(
        "LONG / SHORT"
    )

    report.append("-" * 60)

    report.append(
        f"Long trades: "
        f"{len(long_df)}"
    )

    report.append(
        f"Long WR: "
        f"{long_wr:.2f}%"
    )

    report.append(
        f"Short trades: "
        f"{len(short_df)}"
    )

    report.append(
        f"Short WR: "
        f"{short_wr:.2f}%"
    )

    report.append("")

    report.append(
        "EXIT TYPES"
    )

    report.append("-" * 60)

    for reason, count in sorted(
        exit_counts.items()
    ):

        report.append(
            f"{reason}: {count}"
        )

    report.append("")

    report.append(
        "MONTHLY PERFORMANCE"
    )

    report.append("-" * 60)

    report.extend(
        monthly_text
    )

    report.append("")

    report.append(
        "STRATEGY PARAMETERS"
    )

    report.append("-" * 60)

    report.append(
        f"Ichimoku: "
        f"{TENKAN_PERIOD}/"
        f"{KIJUN_PERIOD}/"
        f"{SENKOU_B_PERIOD}"
    )

    report.append(
        f"Min equilibrium distance: "
        f"{MIN_EQUILIBRIUM_DISTANCE_ATR:.2f} ATR"
    )

    report.append(
        f"Max equilibrium distance: "
        f"{MAX_EQUILIBRIUM_DISTANCE_ATR:.2f} ATR"
    )

    report.append(
        f"1H max TK separation: "
        f"{MAX_1H_TK_SEPARATION_ATR:.2f} ATR"
    )

    report.append(
        f"1H max Kijun slope: "
        f"{MAX_1H_KIJUN_SLOPE_ATR:.2f} ATR"
    )

    report.append(
        f"1H max cloud extension: "
        f"{MAX_1H_CLOUD_EXTENSION_ATR:.2f} ATR"
    )

    report.append(
        f"15M body ratio: "
        f"{MIN_15M_BODY_RATIO:.2f}"
    )

    report.append(
        f"5M body ratio: "
        f"{MIN_5M_BODY_RATIO:.2f}"
    )

    report.append(
        f"SL ATR buffer: "
        f"{SL_ATR_BUFFER:.2f}"
    )

    report.append(
        f"Max SL: "
        f"{MAX_SL_DISTANCE_PCT:.2f}%"
    )

    report.append(
        f"Min RR: "
        f"{MIN_RR:.2f}"
    )

    report.append(
        f"Max TP: "
        f"{MAX_TP_DISTANCE_PCT:.2f}%"
    )

    report.append(
        f"Fee each side: "
        f"{FEE_RATE * 100:.3f}%"
    )

    report.append(
        "CLOSED CANDLES ONLY: ENABLED"
    )

    report.append(
        "NO LOOKAHEAD: ENABLED"
    )

    report.append(
        "REAL TRADING: DISABLED"
    )

    report.append("=" * 60)

    with open(
        REPORT_FILE,
        "w",
        encoding="utf-8"
    ) as f:

        f.write(
            "\n".join(report)
        )

    # ========================================================
    # CONSOLE SUMMARY
    # ========================================================

    print()
    print("=" * 70)

    print(
        "ICHIMOKU EQUILIBRIUM v2.0 RESULT"
    )

    print("=" * 70)

    print(
        f"Final capital : "
        f"{capital:.2f}"
    )

    print(
        f"Net return    : "
        f"{net_return:.3f}%"
    )

    print(
        f"Trades        : "
        f"{total_trades}"
    )

    print(
        f"Win rate      : "
        f"{win_rate:.2f}%"
    )

    print(
        f"Profit factor : "
        f"{pf_text}"
    )

    print(
        f"Max drawdown  : "
        f"{max_drawdown:.3f}%"
    )

    print("=" * 70)

    print(
        f"Saved: {TRADES_FILE}"
    )

    print(
        f"Saved: {EQUITY_FILE}"
    )

    print(
        f"Saved: {REPORT_FILE}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":

    run_backtest()
