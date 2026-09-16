# ============================================================
# ICHIMOKU EQUILIBRIUM v3.0
# 1 YEAR BACKTEST
# ============================================================
#
# KRAKEN FUTURES
#
# 1H  = MARKET REGIME
# 15M = TRUE EQUILIBRIUM DISTANCE + REACTION
# 5M  = REVERSAL CONFIRMATION
#
# TRUE EQUILIBRIUM:
#     (TENKAN + KIJUN) / 2
#
# IMPORTANT:
# - CLOSED CANDLES ONLY
# - NO LOOKAHEAD
# - REAL TRADING DISABLED
# - SL / TP LOGIC KEPT FROM v2
# - ONE ACTIVE TRADE AT A TIME
#
# DATA ENDPOINT:
# Kraken Futures Charts API
# /api/charts/v1/trade/{symbol}/{resolution}
#
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

VERSION = "v3.0"

REAL_TRADING = False

SYMBOL = "PF_DOGEUSD"

INITIAL_CAPITAL = 100.0

BACKTEST_DAYS = 365

# ============================================================
# ICHIMOKU
# ============================================================

TENKAN_PERIOD = 9
KIJUN_PERIOD = 26
SENKOU_B_PERIOD = 52
ICHIMOKU_SHIFT = 26

ATR_PERIOD = 14

# ============================================================
# EQUILIBRIUM
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
# 15M
# ============================================================

MIN_15M_BODY_RATIO = 0.25

# ============================================================
# 5M
# ============================================================

MIN_5M_BODY_RATIO = 0.30

# ============================================================
# STOP LOSS
# ============================================================

SL_ATR_BUFFER = 0.25
MAX_SL_DISTANCE_PCT = 3.00

SWING_LOOKBACK_5M = 12

# ============================================================
# TAKE PROFIT
# ============================================================

MIN_RR = 1.50
MAX_TP_DISTANCE_PCT = 8.00

# ============================================================
# FEES
# ============================================================

FEE_PER_SIDE = 0.0006
ROUND_TRIP_FEE = FEE_PER_SIDE * 2

# ============================================================
# KRAKEN FUTURES CHART API
# ============================================================

KRAKEN_CHARTS_URL = (
    "https://futures.kraken.com/api/charts/v1"
)

# ============================================================
# TIME HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def floor_time(dt, minutes):

    dt = dt.replace(
        second=0,
        microsecond=0
    )

    minute = (
        dt.minute // minutes
    ) * minutes

    return dt.replace(
        minute=minute
    )


# ============================================================
# KRAKEN DATA FETCH
# ============================================================

def fetch_kraken_range(
    symbol,
    resolution,
    start_ts,
    end_ts
):

    url = (
        f"{KRAKEN_CHARTS_URL}"
        f"/trade/{symbol}/{resolution}"
    )

    # Kraken chart endpoint supports
    # from/to in epoch seconds.
    #
    # We use conservative chunks.

    resolution_seconds = {
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

    if resolution not in resolution_seconds:

        raise ValueError(
            f"Unsupported resolution: {resolution}"
        )

    step = resolution_seconds[
        resolution
    ]

    # Keep requests reasonably sized.
    # 900 candles max per request.

    chunk_size = 900

    rows = []

    current = int(start_ts)

    total_requests = 0

    while current < end_ts:

        chunk_end = min(
            current
            + step * chunk_size,
            end_ts
        )

        params = {
            "from": current,
            "to": chunk_end
        }

        total_requests += 1

        try:

            response = requests.get(
                url,
                params=params,
                headers={
                    "Accept":
                    "application/json"
                },
                timeout=30
            )

            if response.status_code != 200:

                raise RuntimeError(
                    f"HTTP {response.status_code}: "
                    f"{response.text[:300]}"
                )

            data = response.json()

            candles = data.get(
                "candles",
                []
            )

            if not candles:

                print(
                    f"WARNING: No candles returned "
                    f"{symbol} {resolution} "
                    f"{datetime.fromtimestamp(current, timezone.utc)}"
                )

            for candle in candles:

                if not isinstance(
                    candle,
                    dict
                ):
                    continue

                ts = candle.get(
                    "time"
                )

                o = candle.get(
                    "open"
                )

                h = candle.get(
                    "high"
                )

                l = candle.get(
                    "low"
                )

                c = candle.get(
                    "close"
                )

                v = candle.get(
                    "volume"
                )

                if any(
                    x is None
                    for x in [
                        ts,
                        o,
                        h,
                        l,
                        c,
                        v
                    ]
                ):
                    continue

                # Kraken Charts API returns
                # candle time in milliseconds.
                timestamp_ms = int(ts)

                timestamp_s = (
                    timestamp_ms // 1000
                )

                rows.append(
                    {
                        "timestamp":
                            timestamp_s,

                        "open":
                            float(o),

                        "high":
                            float(h),

                        "low":
                            float(l),

                        "close":
                            float(c),

                        "volume":
                            float(v),
                    }
                )

            print(
                f"{symbol} {resolution} | "
                f"{datetime.fromtimestamp(current, timezone.utc)} "
                f"-> "
                f"{datetime.fromtimestamp(chunk_end, timezone.utc)} | "
                f"{len(candles)} candles"
            )

        except Exception as e:

            print(
                f"Data error "
                f"{symbol} {resolution}: {e}"
            )

            # Do not silently continue.
            # Missing chunks can corrupt a backtest.

            raise

        # Move exactly to next candle boundary.
        current = (
            chunk_end
            + step
        )

        time.sleep(0.15)

    if not rows:

        raise RuntimeError(
            f"No market data returned "
            f"for {symbol} {resolution}"
        )

    df = pd.DataFrame(
        rows
    )

    df = df.drop_duplicates(
        subset=[
            "timestamp"
        ]
    )

    df = df.sort_values(
        "timestamp"
    ).reset_index(
        drop=True
    )

    df["open_time"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    df["close_time"] = (
        df["open_time"]
        + pd.to_timedelta(
            step,
            unit="s"
        )
    )

    # Keep only requested range.

    df = df[
        (
            df["open_time"]
            >= pd.to_datetime(
                start_ts,
                unit="s",
                utc=True
            )
        )
        &
        (
            df["open_time"]
            <= pd.to_datetime(
                end_ts,
                unit="s",
                utc=True
            )
        )
    ].copy()

    df = df.reset_index(
        drop=True
    )

    print(
        f"\nTOTAL {symbol} {resolution}: "
        f"{len(df)} candles\n"
    )

    return df


# ============================================================
# REMOVE INCOMPLETE CANDLE
# ============================================================

def remove_incomplete_candle(df):

    if df.empty:
        return df

    now = utc_now()

    return df[
        df["close_time"] <= now
    ].copy().reset_index(
        drop=True
    )


# ============================================================
# ATR
# ============================================================

def add_atr(df):

    df = df.copy()

    prev_close = (
        df["close"].shift(1)
    )

    tr1 = (
        df["high"]
        - df["low"]
    )

    tr2 = (
        df["high"]
        - prev_close
    ).abs()

    tr3 = (
        df["low"]
        - prev_close
    ).abs()

    tr = pd.concat(
        [
            tr1,
            tr2,
            tr3
        ],
        axis=1
    ).max(
        axis=1
    )

    df["atr"] = (
        tr.rolling(
            ATR_PERIOD
        ).mean()
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
    ) / 2

    df["kijun"] = (
        high.rolling(
            KIJUN_PERIOD
        ).max()
        +
        low.rolling(
            KIJUN_PERIOD
        ).min()
    ) / 2

    # TRUE EQUILIBRIUM

    df["senkou_a"] = (
        df["tenkan"]
        +
        df["kijun"]
    ) / 2

    df["senkou_b"] = (
        high.rolling(
            SENKOU_B_PERIOD
        ).max()
        +
        low.rolling(
            SENKOU_B_PERIOD
        ).min()
    ) / 2

    # Visible cloud only.
    # No future values are used.

    df["cloud_a"] = (
        df["senkou_a"]
        .shift(
            ICHIMOKU_SHIFT
        )
    )

    df["cloud_b"] = (
        df["senkou_b"]
        .shift(
            ICHIMOKU_SHIFT
        )
    )

    return df


# ============================================================
# PREPARE
# ============================================================

def prepare_df(df):

    if df.empty:
        return df

    df = add_atr(
        df
    )

    df = add_ichimoku(
        df
    )

    return df


# ============================================================
# LATEST CLOSED ROW
# ============================================================

def get_latest_closed_row(
    df,
    decision_time
):

    data = df[
        df["close_time"]
        <= decision_time
    ]

    if data.empty:
        return None

    return data.iloc[-1]


# ============================================================
# EQUILIBRIUM
# ============================================================

def calculate_equilibrium(row):

    if (
        pd.isna(row["tenkan"])
        or
        pd.isna(row["kijun"])
    ):
        return np.nan

    return (
        row["tenkan"]
        +
        row["kijun"]
    ) / 2


# ============================================================
# EQUILIBRIUM DISTANCE
# ============================================================

def equilibrium_distance_atr(
    row
):

    equilibrium = (
        calculate_equilibrium(
            row
        )
    )

    atr = row["atr"]

    if (
        pd.isna(equilibrium)
        or
        pd.isna(atr)
        or
        atr <= 0
    ):
        return np.nan

    return (
        abs(
            row["close"]
            - equilibrium
        )
        / atr
    )


# ============================================================
# 1H REGIME
# ============================================================

def valid_1h_regime(row):

    if row is None:

        return (
            False,
            None
        )

    close = row["close"]

    tenkan = row["tenkan"]

    kijun = row["kijun"]

    atr = row["atr"]

    prev_kijun = (
        row["prev_kijun"]
    )

    if any(
        pd.isna(x)
        for x in [
            close,
            tenkan,
            kijun,
            atr,
            prev_kijun
        ]
    ):

        return (
            False,
            None
        )

    if atr <= 0:

        return (
            False,
            None
        )

    tk_sep = (
        abs(
            tenkan
            - kijun
        )
        / atr
    )

    kijun_slope = (
        abs(
            kijun
            - prev_kijun
        )
        / atr
    )

    cloud_values = []

    if not pd.isna(
        row["cloud_a"]
    ):

        cloud_values.append(
            row["cloud_a"]
        )

    if not pd.isna(
        row["cloud_b"]
    ):

        cloud_values.append(
            row["cloud_b"]
        )

    if cloud_values:

        cloud_mid = np.mean(
            cloud_values
        )

        cloud_extension = (
            abs(
                close
                - cloud_mid
            )
            / atr
        )

    else:

        cloud_extension = 0.0

    # --------------------------------------------------------
    # EXTREME TREND REJECTION ONLY
    # --------------------------------------------------------

    if (
        tk_sep
        > MAX_1H_TK_SEPARATION_ATR
    ):

        return (
            False,
            None
        )

    if (
        kijun_slope
        > MAX_1H_KIJUN_SLOPE_ATR
    ):

        return (
            False,
            None
        )

    if (
        cloud_extension
        > MAX_1H_CLOUD_EXTENSION_ATR
    ):

        return (
            False,
            None
        )

    equilibrium = (
        tenkan
        + kijun
    ) / 2

    if close < equilibrium:

        direction = "LONG"

    elif close > equilibrium:

        direction = "SHORT"

    else:

        return (
            False,
            None
        )

    return (
        True,
        direction
    )


# ============================================================
# 15M EQUILIBRIUM REACTION
# ============================================================

def valid_15m_reaction(
    current,
    previous,
    direction
):

    if (
        current is None
        or
        previous is None
    ):

        return False

    required = [
        current["open"],
        current["high"],
        current["low"],
        current["close"],
        current["tenkan"],
        current["kijun"],
        current["atr"],
        previous["close"]
    ]

    if any(
        pd.isna(x)
        for x in required
    ):

        return False

    equilibrium = (
        current["tenkan"]
        +
        current["kijun"]
    ) / 2

    atr = current["atr"]

    if atr <= 0:

        return False

    distance = (
        abs(
            current["close"]
            - equilibrium
        )
        / atr
    )

    if (
        distance
        < MIN_EQUILIBRIUM_DISTANCE_ATR
    ):

        return False

    if (
        distance
        > MAX_EQUILIBRIUM_DISTANCE_ATR
    ):

        return False

    candle_range = (
        current["high"]
        -
        current["low"]
    )

    if candle_range <= 0:

        return False

    body = abs(
        current["close"]
        -
        current["open"]
    )

    body_ratio = (
        body
        / candle_range
    )

    if (
        body_ratio
        < MIN_15M_BODY_RATIO
    ):

        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if (
            current["close"]
            >= equilibrium
        ):

            return False

        if (
            current["close"]
            <= current["open"]
        ):

            return False

        if (
            current["close"]
            <= previous["close"]
        ):

            return False

        return True

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if (
            current["close"]
            <= equilibrium
        ):

            return False

        if (
            current["close"]
            >= current["open"]
        ):

            return False

        if (
            current["close"]
            >= previous["close"]
        ):

            return False

        return True

    return False


# ============================================================
# 5M REVERSAL
# ============================================================

def valid_5m_trigger(
    current,
    previous,
    direction
):

    if (
        current is None
        or
        previous is None
    ):

        return False

    required = [
        current["open"],
        current["high"],
        current["low"],
        current["close"],
        previous["close"],
        current["tenkan"],
        current["kijun"]
    ]

    if any(
        pd.isna(x)
        for x in required
    ):

        return False

    candle_range = (
        current["high"]
        -
        current["low"]
    )

    if candle_range <= 0:

        return False

    body = abs(
        current["close"]
        -
        current["open"]
    )

    body_ratio = (
        body
        /
        candle_range
    )

    if (
        body_ratio
        < MIN_5M_BODY_RATIO
    ):

        return False

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        if (
            current["close"]
            <= current["open"]
        ):

            return False

        if (
            current["close"]
            <= previous["close"]
        ):

            return False

        reclaim_tenkan = (
            current["close"]
            >
            current["tenkan"]
        )

        break_previous_high = (
            current["close"]
            >
            previous["high"]
        )

        if not (
            reclaim_tenkan
            or
            break_previous_high
        ):

            return False

        return True

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if direction == "SHORT":

        if (
            current["close"]
            >= current["open"]
        ):

            return False

        if (
            current["close"]
            >= previous["close"]
        ):

            return False

        lose_tenkan = (
            current["close"]
            <
            current["tenkan"]
        )

        break_previous_low = (
            current["close"]
            <
            previous["low"]
        )

        if not (
            lose_tenkan
            or
            break_previous_low
        ):

            return False

        return True

    return False


# ============================================================
# CONFIRMED PIVOTS
# ============================================================

def find_confirmed_pivot_levels(
    df,
    decision_time,
    window=3
):

    data = df[
        df["close_time"]
        <= decision_time
    ].copy()

    if len(data) < (
        window * 2 + 1
    ):

        return (
            [],
            []
        )

    highs = []
    lows = []

    high_values = (
        data["high"].values
    )

    low_values = (
        data["low"].values
    )

    times = (
        data["close_time"].values
    )

    for i in range(
        window,
        len(data) - window
    ):

        left_highs = (
            high_values[
                i - window:i
            ]
        )

        right_highs = (
            high_values[
                i + 1:
                i + window + 1
            ]
        )

        left_lows = (
            low_values[
                i - window:i
            ]
        )

        right_lows = (
            low_values[
                i + 1:
                i + window + 1
            ]
        )

        current_high = (
            high_values[i]
        )

        current_low = (
            low_values[i]
        )

        if (
            current_high
            >
            np.max(left_highs)
            and
            current_high
            >
            np.max(right_highs)
        ):

            highs.append(
                {
                    "price":
                        current_high,

                    "time":
                        times[i]
                }
            )

        if (
            current_low
            <
            np.min(left_lows)
            and
            current_low
            <
            np.min(right_lows)
        ):

            lows.append(
                {
                    "price":
                        current_low,

                    "time":
                        times[i]
                }
            )

    return (
        highs,
        lows
    )


# ============================================================
# STOP LOSS
# ============================================================

def calculate_stop_loss(
    direction,
    entry,
    atr,
    df5_test
):

    if (
        df5_test.empty
        or
        pd.isna(atr)
        or
        atr <= 0
    ):

        return None

    recent = (
        df5_test.tail(
            SWING_LOOKBACK_5M
        )
    )

    if recent.empty:

        return None

    if direction == "LONG":

        swing_low = (
            recent["low"].min()
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
            recent["high"].max()
        )

        stop = (
            swing_high
            +
            atr * SL_ATR_BUFFER
        )

        if stop <= entry:

            return None

    sl_distance_pct = (
        abs(
            entry
            -
            stop
        )
        /
        entry
        *
        100
    )

    if (
        sl_distance_pct
        > MAX_SL_DISTANCE_PCT
    ):

        return None

    return float(
        stop
    )


# ============================================================
# TAKE PROFIT
# ============================================================

def calculate_take_profit(
    direction,
    entry,
    stop,
    highs,
    lows
):

    risk = abs(
        entry
        -
        stop
    )

    if risk <= 0:

        return (
            None,
            None
        )

    candidates = []

    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if direction == "LONG":

        for level in highs:

            price = level["price"]

            if price <= entry:

                continue

            distance_pct = (
                (
                    price
                    -
                    entry
                )
                /
                entry
                *
                100
            )

            if (
                distance_pct
                >
                MAX_TP_DISTANCE_PCT
            ):

                continue

            rr = (
                price
                -
                entry
            ) / risk

            if rr > MIN_RR:

                candidates.append(
                    (
                        price,
                        rr
                    )
                )

        if not candidates:

            return (
                None,
                None
            )

        candidates.sort(
            key=lambda x: x[0]
        )

        return candidates[0]

    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    for level in lows:

        price = level["price"]

        if price >= entry:

            continue

        distance_pct = (
            (
                entry
                -
                price
            )
            /
            entry
            *
            100
        )

        if (
            distance_pct
            >
            MAX_TP_DISTANCE_PCT
        ):

            continue

        rr = (
            entry
            -
            price
        ) / risk

        if rr > MIN_RR:

            candidates.append(
                (
                    price,
                    rr
                )
            )

    if not candidates:

        return (
            None,
            None
        )

    candidates.sort(
        key=lambda x: x[0],
        reverse=True
    )

    return candidates[0]


# ============================================================
# PNL
# ============================================================

def calculate_trade_pnl(
    direction,
    entry,
    exit_price
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

    net = (
        gross
        -
        ROUND_TRIP_FEE
    )

    return net


# ============================================================
# EXIT
# ============================================================

def check_trade_exit(
    trade,
    candle
):

    direction = (
        trade["direction"]
    )

    stop = (
        trade["stop"]
    )

    target = (
        trade["target"]
    )

    high = candle["high"]

    low = candle["low"]

    # Conservative:
    # SL wins if both are touched
    # inside the same candle.

    if direction == "LONG":

        if low <= stop:

            return (
                stop,
                "SL"
            )

        if high >= target:

            return (
                target,
                "TP"
            )

    else:

        if high >= stop:

            return (
                stop,
                "SL"
            )

        if low <= target:

            return (
                target,
                "TP"
            )

    return (
        None,
        None
    )


# ============================================================
# MAIN BACKTEST
# ============================================================

def run_backtest():

    print("=" * 70)
    print(
        f"ICHIMOKU EQUILIBRIUM {VERSION}"
    )
    print(
        "1 YEAR BACKTEST"
    )
    print("=" * 70)

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Initial Capital: "
        f"${INITIAL_CAPITAL:.2f}"
    )

    print(
        f"Real Trading: "
        f"{REAL_TRADING}"
    )

    # --------------------------------------------------------
    # TIME
    # --------------------------------------------------------

    end_dt = floor_time(
        utc_now(),
        5
    )

    start_dt = (
        end_dt
        -
        timedelta(
            days=BACKTEST_DAYS
        )
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    print(
        f"Start: {start_dt}"
    )

    print(
        f"End:   {end_dt}"
    )

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    print(
        "\nDownloading 1H..."
    )

    df1h = fetch_kraken_range(
        SYMBOL,
        "1h",
        start_ts,
        end_ts
    )

    print(
        "\nDownloading 15M..."
    )

    df15 = fetch_kraken_range(
        SYMBOL,
        "15m",
        start_ts,
        end_ts
    )

    print(
        "\nDownloading 5M..."
    )

    df5 = fetch_kraken_range(
        SYMBOL,
        "5m",
        start_ts,
        end_ts
    )

    # --------------------------------------------------------
    # VALIDATE
    # --------------------------------------------------------

    if df1h.empty:

        raise RuntimeError(
            "1H market data is empty."
        )

    if df15.empty:

        raise RuntimeError(
            "15M market data is empty."
        )

    if df5.empty:

        raise RuntimeError(
            "5M market data is empty."
        )

    # --------------------------------------------------------
    # CLOSED CANDLES
    # --------------------------------------------------------

    df1h = (
        remove_incomplete_candle(
            df1h
        )
    )

    df15 = (
        remove_incomplete_candle(
            df15
        )
    )

    df5 = (
        remove_incomplete_candle(
            df5
        )
    )

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

    df1h = prepare_df(
        df1h
    )

    df15 = prepare_df(
        df15
    )

    df5 = prepare_df(
        df5
    )

    # --------------------------------------------------------
    # 1H PREVIOUS KIJUN
    # --------------------------------------------------------

    df1h["prev_kijun"] = (
        df1h["kijun"].shift(1)
    )

    # --------------------------------------------------------
    # TEST WINDOW
    # --------------------------------------------------------

    df5_test = df5[
        (
            df5["close_time"]
            >= start_dt
        )
        &
        (
            df5["close_time"]
            <= end_dt
        )
    ].copy()

    if df5_test.empty:

        raise RuntimeError(
            "5M test window is empty."
        )

    # --------------------------------------------------------
    # STATE
    # --------------------------------------------------------

    capital = (
        INITIAL_CAPITAL
    )

    active_trade = None

    trades = []

    trade_id = 0

    # ========================================================
    # LOOP
    # ========================================================

    for i in range(
        1,
        len(df5_test)
    ):

        candle = (
            df5_test.iloc[i]
        )

        decision_time = (
            candle["close_time"]
        )

        # ----------------------------------------------------
        # EXIT
        # ----------------------------------------------------

        if active_trade is not None:

            exit_price, reason = (
                check_trade_exit(
                    active_trade,
                    candle
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
                        exit_price
                    )
                )

                pnl = (
                    capital
                    *
                    net_return
                )

                capital += pnl

                active_trade[
                    "exit"
                ] = exit_price

                active_trade[
                    "exit_time"
                ] = decision_time

                active_trade[
                    "exit_reason"
                ] = reason

                active_trade[
                    "net_return"
                ] = net_return

                active_trade[
                    "pnl"
                ] = pnl

                active_trade[
                    "capital_after"
                ] = capital

                active_trade[
                    "price_change_pct"
                ] = (
                    (
                        exit_price
                        -
                        active_trade[
                            "entry"
                        ]
                    )
                    /
                    active_trade[
                        "entry"
                    ]
                    *
                    100
                )

                if (
                    active_trade[
                        "direction"
                    ]
                    ==
                    "SHORT"
                ):

                    active_trade[
                        "price_change_pct"
                    ] *= -1

                trades.append(
                    active_trade.copy()
                )

                active_trade = None

        # ----------------------------------------------------
        # ONE ACTIVE TRADE ONLY
        # ----------------------------------------------------

        if active_trade is not None:

            continue

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        row1h = (
            get_latest_closed_row(
                df1h,
                decision_time
            )
        )

        valid_regime, direction = (
            valid_1h_regime(
                row1h
            )
        )

        if not valid_regime:

            continue

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        row15_current = (
            get_latest_closed_row(
                df15,
                decision_time
            )
        )

        if row15_current is None:

            continue

        idx15 = (
            row15_current.name
        )

        if idx15 <= 0:

            continue

        row15_previous = (
            df15.iloc[
                idx15 - 1
            ]
        )

        if not valid_15m_reaction(
            row15_current,
            row15_previous,
            direction
        ):

            continue

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        current_idx5 = (
            df5_test.index[
                df5_test[
                    "close_time"
                ]
                ==
                decision_time
            ]
        )

        if len(current_idx5) == 0:

            continue

        current_global_idx = (
            current_idx5[0]
        )

        if current_global_idx <= 0:

            continue

        row5_current = (
            df5.loc[
                current_global_idx
            ]
        )

        row5_previous = (
            df5.loc[
                current_global_idx - 1
            ]
        )

        if not valid_5m_trigger(
            row5_current,
            row5_previous,
            direction
        ):

            continue

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = float(
            row5_current[
                "close"
            ]
        )

        atr = float(
            row5_current[
                "atr"
            ]
        )

        if pd.isna(atr):

            continue

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        historical_5m = df5[
            df5[
                "close_time"
            ]
            <=
            decision_time
        ]

        stop = (
            calculate_stop_loss(
                direction,
                entry,
                atr,
                historical_5m
            )
        )

        if stop is None:

            continue

        # ----------------------------------------------------
        # PIVOTS
        # ----------------------------------------------------

        highs, lows = (
            find_confirmed_pivot_levels(
                df15,
                decision_time
            )
        )

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        target, rr = (
            calculate_take_profit(
                direction,
                entry,
                stop,
                highs,
                lows
            )
        )

        if target is None:

            continue

        # ----------------------------------------------------
        # TRADE
        # ----------------------------------------------------

        trade_id += 1

        active_trade = {

            "trade_id":
                trade_id,

            "direction":
                direction,

            "entry_time":
                decision_time,

            "entry":
                entry,

            "stop":
                stop,

            "target":
                target,

            "rr":
                rr,

            "equilibrium_1h":
                calculate_equilibrium(
                    row1h
                ),

            "equilibrium_15m":
                calculate_equilibrium(
                    row15_current
                ),

            "equilibrium_distance_atr":
                equilibrium_distance_atr(
                    row15_current
                )
        }

    # ========================================================
    # CLOSE OPEN TRADE
    # ========================================================

    if active_trade is not None:

        final_price = float(
            df5_test.iloc[-1][
                "close"
            ]
        )

        net_return = (
            calculate_trade_pnl(
                active_trade[
                    "direction"
                ],
                active_trade[
                    "entry"
                ],
                final_price
            )
        )

        pnl = (
            capital
            *
            net_return
        )

        capital += pnl

        active_trade[
            "exit"
        ] = final_price

        active_trade[
            "exit_time"
        ] = (
            df5_test.iloc[-1][
                "close_time"
            ]
        )

        active_trade[
            "exit_reason"
        ] = (
            "END_OF_BACKTEST"
        )

        active_trade[
            "net_return"
        ] = net_return

        active_trade[
            "pnl"
        ] = pnl

        active_trade[
            "capital_after"
        ] = capital

        active_trade[
            "price_change_pct"
        ] = (
            (
                final_price
                -
                active_trade[
                    "entry"
                ]
            )
            /
            active_trade[
                "entry"
            ]
            *
            100
        )

        if (
            active_trade[
                "direction"
            ]
            ==
            "SHORT"
        ):

            active_trade[
                "price_change_pct"
            ] *= -1

        trades.append(
            active_trade.copy()
        )

    # ========================================================
    # RESULTS
    # ========================================================

    results = pd.DataFrame(
        trades
    )

    print("\n")
    print("=" * 70)
    print("BACKTEST RESULT")
    print("=" * 70)

    print(
        f"Version: {VERSION}"
    )

    print(
        f"Initial Capital: "
        f"${INITIAL_CAPITAL:.2f}"
    )

    print(
        f"Final Capital: "
        f"${capital:.2f}"
    )

    total_return = (
        capital
        /
        INITIAL_CAPITAL
        -
        1
    ) * 100

    print(
        f"Net Return: "
        f"{total_return:.3f}%"
    )

    if results.empty:

        print(
            "\nNO TRADES"
        )

        results.to_csv(
            "ichimoku_equilibrium_v3_trades.csv",
            index=False
        )

        return

    # --------------------------------------------------------
    # WIN / LOSS
    # --------------------------------------------------------

    wins = results[
        results[
            "net_return"
        ]
        > 0
    ]

    losses = results[
        results[
            "net_return"
        ]
        <= 0
    ]

    win_count = len(
        wins
    )

    loss_count = len(
        losses
    )

    total_trades = len(
        results
    )

    win_rate = (
        win_count
        /
        total_trades
        *
        100
    )

    print(
        f"Trades: "
        f"{total_trades}"
    )

    print(
        f"Wins: "
        f"{win_count}"
    )

    print(
        f"Losses: "
        f"{loss_count}"
    )

    print(
        f"Win Rate: "
        f"{win_rate:.2f}%"
    )

    # --------------------------------------------------------
    # PROFIT FACTOR
    # --------------------------------------------------------

    gross_profit = (
        wins["pnl"].sum()
        if not wins.empty
        else 0
    )

    gross_loss = (
        abs(
            losses["pnl"].sum()
        )
        if not losses.empty
        else 0
    )

    if gross_loss > 0:

        profit_factor = (
            gross_profit
            /
            gross_loss
        )

    else:

        profit_factor = np.inf

    print(
        f"Profit Factor: "
        f"{profit_factor:.3f}"
    )

    # --------------------------------------------------------
    # AVG TRADE
    # --------------------------------------------------------

    avg_trade = (
        results[
            "net_return"
        ].mean()
        *
        100
    )

    print(
        f"Avg Trade: "
        f"{avg_trade:.4f}%"
    )

    # --------------------------------------------------------
    # MAX DD
    # --------------------------------------------------------

    equity = (
        results[
            "capital_after"
        ]
        .astype(float)
    )

    running_max = (
        equity.cummax()
    )

    drawdown = (
        equity
        /
        running_max
        -
        1
    ) * 100

    max_dd = (
        drawdown.min()
    )

    print(
        f"Max Drawdown: "
        f"{max_dd:.3f}%"
    )

    # --------------------------------------------------------
    # LONG / SHORT
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("LONG / SHORT")
    print("=" * 70)

    for side in [
        "LONG",
        "SHORT"
    ]:

        side_df = results[
            results[
                "direction"
            ]
            ==
            side
        ]

        if side_df.empty:

            continue

        side_wins = side_df[
            side_df[
                "net_return"
            ]
            > 0
        ]

        side_wr = (
            len(side_wins)
            /
            len(side_df)
            *
            100
        )

        side_pnl = (
            side_df[
                "pnl"
            ].sum()
        )

        print(
            f"{side}: "
            f"{len(side_df)} trades | "
            f"WR {side_wr:.2f}% | "
            f"PnL {side_pnl:+.4f}"
        )

    # --------------------------------------------------------
    # EXIT TYPES
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("EXIT TYPES")
    print("=" * 70)

    print(
        results[
            "exit_reason"
        ].value_counts()
    )

    # --------------------------------------------------------
    # MONTHLY
    # --------------------------------------------------------

    print("\n")
    print("=" * 70)
    print("MONTHLY PERFORMANCE")
    print("=" * 70)

    results["month"] = (
        pd.to_datetime(
            results[
                "exit_time"
            ]
        )
        .dt.strftime(
            "%Y-%m"
        )
    )

    monthly = (
        results
        .groupby(
            "month"
        )[
            "net_return"
        ]
        .sum()
        *
        100
    )

    for month, value in (
        monthly.items()
    ):

        print(
            f"{month}: "
            f"{value:+.4f}%"
        )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    results.to_csv(
        "ichimoku_equilibrium_v3_trades.csv",
        index=False
    )

    print("\n")
    print(
        "Saved: "
        "ichimoku_equilibrium_v3_trades.csv"
    )

    print("\n")
    print("=" * 70)
    print(
        "BACKTEST COMPLETE"
    )
    print("=" * 70)


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    if REAL_TRADING:

        raise RuntimeError(
            "REAL TRADING MUST REMAIN DISABLED "
            "FOR THIS BACKTEST."
        )

    run_backtest()
