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

MIN_EQUILIBRIUM_DISTANCE_ATR = 0.50
MAX_EQUILIBRIUM_DISTANCE_ATR = 2.00

# ------------------------------------------------------------
# 1H relaxed regime filter
# ------------------------------------------------------------

MAX_1H_TK_SEPARATION_ATR = 0.80
MAX_1H_KIJUN_SLOPE_ATR = 0.35
MAX_1H_CLOUD_EXTENSION_ATR = 1.25

# ------------------------------------------------------------
# Candle confirmation
# ------------------------------------------------------------

MIN_15M_BODY_RATIO = 0.25
MIN_5M_BODY_RATIO = 0.30

# ------------------------------------------------------------
# Stop loss
# ------------------------------------------------------------

SL_ATR_BUFFER = 0.25
SWING_LOOKBACK_5M = 12
MAX_SL_DISTANCE_PCT = 3.00

# ------------------------------------------------------------
# Take profit
# ------------------------------------------------------------

MIN_TP_RR = 1.50
MAX_TP_DISTANCE_PCT = 8.00

# ------------------------------------------------------------
# Fees
# ------------------------------------------------------------

FEE_PER_SIDE = 0.0006
ROUND_TRIP_FEE = FEE_PER_SIDE * 2

# ------------------------------------------------------------
# Backtest
# ------------------------------------------------------------

BACKTEST_DAYS = 365

# One active trade at a time
MAX_ACTIVE_TRADES = 1

REQUEST_TIMEOUT = 30
REQUEST_RETRIES = 4

# Output files
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
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def to_timestamp(dt):
    return int(dt.timestamp())


def safe_float(value):
    try:
        return float(value)
    except Exception:
        return np.nan


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_kraken_candles(symbol, resolution, start_ts, end_ts):
    """
    Fetch Kraken Futures Charts API candles.

    resolution:
        5m
        15m
        1h
    """

    url = f"{KRAKEN_CHARTS_URL}/trade/{symbol}/{resolution}"

    params = {
        "from": int(start_ts),
        "to": int(end_ts),
    }

    last_error = None

    for attempt in range(1, REQUEST_RETRIES + 1):

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
                    time.sleep(2 * attempt)
                    continue

                raise RuntimeError(
                    f"Kraken API error for {symbol} {resolution}: "
                    f"{last_error}"
                )

            data = response.json()

            candles = None

            if isinstance(data, dict):

                if "candles" in data:
                    candles = data["candles"]

                elif "data" in data:
                    candles = data["data"]

            elif isinstance(data, list):
                candles = data

            if not candles:
                raise RuntimeError(
                    f"No candle data returned for "
                    f"{symbol} {resolution}"
                )

            rows = []

            for c in candles:

                if isinstance(c, dict):

                    ts = (
                        c.get("time")
                        if c.get("time") is not None
                        else c.get("timestamp")
                    )

                    o = c.get("open")
                    h = c.get("high")
                    l = c.get("low")
                    cl = c.get("close")
                    v = c.get("volume", 0)

                    if ts is None:
                        continue

                    rows.append(
                        {
                            "timestamp": float(ts),
                            "open": safe_float(o),
                            "high": safe_float(h),
                            "low": safe_float(l),
                            "close": safe_float(cl),
                            "volume": safe_float(v),
                        }
                    )

                elif isinstance(c, (list, tuple)):

                    if len(c) < 5:
                        continue

                    rows.append(
                        {
                            "timestamp": float(c[0]),
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
                raise RuntimeError(
                    f"Could not parse candle data "
                    f"for {symbol} {resolution}"
                )

            df = pd.DataFrame(rows)

            # Kraken may return milliseconds
            if df["timestamp"].max() > 10_000_000_000:
                df["timestamp"] = df["timestamp"] / 1000.0

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

            df = df.reset_index(drop=True)

            return df

        except Exception as exc:

            last_error = str(exc)

            if attempt < REQUEST_RETRIES:
                time.sleep(2 * attempt)
                continue

            raise RuntimeError(
                f"Failed fetching {symbol} {resolution}: "
                f"{last_error}"
            )

    raise RuntimeError(
        f"Failed fetching {symbol} {resolution}"
    )


# ============================================================
# FETCH FULL BACKTEST DATA
# ============================================================

def fetch_backtest_data():

    end_dt = utc_now()

    start_dt = end_dt - timedelta(
        days=BACKTEST_DAYS + 20
    )

    start_ts = to_timestamp(start_dt)
    end_ts = to_timestamp(end_dt)

    print("=" * 70)
    print("DOWNLOADING KRAKEN FUTURES DATA")
    print("=" * 70)

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Period: {start_dt.isoformat()} -> "
        f"{end_dt.isoformat()}"
    )

    print()

    data = {}

    for resolution in ["1h", "15m", "5m"]:

        print(
            f"Fetching {resolution}..."
        )

        df = fetch_kraken_candles(
            SYMBOL,
            resolution,
            start_ts,
            end_ts
        )

        print(
            f"{resolution}: {len(df):,} candles"
        )

        if df.empty:
            raise RuntimeError(
                f"Missing market data: {resolution}"
            )

        data[resolution] = df

    print()

    return data["1h"], data["15m"], data["5m"]


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=14):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    previous_close = close.shift(1)

    tr1 = high - low

    tr2 = (high - previous_close).abs()

    tr3 = (low - previous_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
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
        high.rolling(TENKAN_PERIOD).max()
        +
        low.rolling(TENKAN_PERIOD).min()
    ) / 2.0

    df["kijun"] = (
        high.rolling(KIJUN_PERIOD).max()
        +
        low.rolling(KIJUN_PERIOD).min()
    ) / 2.0

    df["senkou_b"] = (
        high.rolling(SENKOU_B_PERIOD).max()
        +
        low.rolling(SENKOU_B_PERIOD).min()
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
        df,
        ATR_PERIOD
    )

    return df


# ============================================================
# BODY RATIO
# ============================================================

def body_ratio(row):

    candle_range = row["high"] - row["low"]

    if candle_range <= 0:
        return 0.0

    return abs(
        row["close"] - row["open"]
    ) / candle_range


# ============================================================
# 1H REGIME
# ============================================================

def valid_1h_regime(df, idx):

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

    if any(pd.isna(x) for x in required):
        return False

    atr = row["atr"]

    if atr <= 0:
        return False

    tk_separation = (
        abs(row["tenkan"] - row["kijun"])
        / atr
    )

    kijun_slope = (
        abs(row["kijun"] - prev["kijun"])
        / atr
    )

    cloud_mid = (
        row["senkou_a"]
        +
        row["senkou_b"]
    ) / 2.0

    cloud_extension = (
        abs(row["close"] - cloud_mid)
        / atr
    )

    if tk_separation > MAX_1H_TK_SEPARATION_ATR:
        return False

    if kijun_slope > MAX_1H_KIJUN_SLOPE_ATR:
        return False

    if cloud_extension > MAX_1H_CLOUD_EXTENSION_ATR:
        return False

    return True


# ============================================================
# 15M EQUILIBRIUM SETUP
# ============================================================

def valid_15m_reaction(df, idx, direction):

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

    if any(pd.isna(x) for x in required):
        return False

    atr = row["atr"]

    if atr <= 0:
        return False

    equilibrium = row["equilibrium"]

    distance_atr = (
        abs(row["close"] - equilibrium)
        / atr
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

    br = body_ratio(row)

    if br < MIN_15M_BODY_RATIO:
        return False

    # --------------------------------------------------------
    # LONG
    # Price is below equilibrium and reacts upward.
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
    # Price is above equilibrium and reacts downward.
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
# 5M REVERSAL CONFIRMATION
# ============================================================

def valid_5m_trigger(df, idx, direction):

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
        row["equilibrium"],
        prev["close"],
    ]

    if any(pd.isna(x) for x in required):
        return False

    br = body_ratio(row)

    if br < MIN_5M_BODY_RATIO:
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
# PIVOTS
# ============================================================

def find_pivots(df, left=2, right=2):

    highs = []
    lows = []

    for i in range(
        left,
        len(df) - right
    ):

        high = df.iloc[i]["high"]

        low = df.iloc[i]["low"]

        left_highs = df.iloc[
            i - left:i
        ]["high"]

        right_highs = df.iloc[
            i + 1:i + right + 1
        ]["high"]

        left_lows = df.iloc[
            i - left:i
        ]["low"]

        right_lows = df.iloc[
            i + 1:i + right + 1
        ]["low"]

        if high > left_highs.max() and high >= right_highs.max():
            highs.append(
                (
                    df.iloc[i]["datetime"],
                    float(high)
                )
            )

        if low < left_lows.min() and low <= right_lows.min():
            lows.append(
                (
                    df.iloc[i]["datetime"],
                    float(low)
                )
            )

    return highs, lows


# ============================================================
# TP SELECTION
# ============================================================

def select_take_profit(
    entry,
    stop_loss,
    direction,
    resistance_levels,
    support_levels
):

    risk = abs(entry - stop_loss)

    if risk <= 0:
        return None

    if direction == "LONG":

        candidates = [
            level
            for level in resistance_levels
            if level > entry
        ]

        candidates = sorted(candidates)

    else:

        candidates = [
            level
            for level in support_levels
            if level < entry
        ]

        candidates = sorted(
            candidates,
            reverse=True
        )

    for level in candidates:

        distance = abs(
            level - entry
        )

        distance_pct = (
            distance / entry
        ) * 100.0

        if distance_pct > MAX_TP_DISTANCE_PCT:
            continue

        rr = distance / risk

        if rr > MIN_TP_RR:
            return float(level)

    return None


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

        swing_low = window["low"].min()

        stop = (
            swing_low
            -
            atr * SL_ATR_BUFFER
        )

        if stop >= entry:
            return None

    else:

        swing_high = window["high"].max()

        stop = (
            swing_high
            +
            atr * SL_ATR_BUFFER
        )

        if stop <= entry:
            return None

    distance_pct = (
        abs(entry - stop)
        /
        entry
    ) * 100.0

    if distance_pct > MAX_SL_DISTANCE_PCT:
        return None

    return float(stop)


# ============================================================
# TRADE PNL
# ============================================================

def calculate_trade_return(
    entry,
    exit_price,
    direction
):

    if direction == "LONG":

        gross = (
            exit_price - entry
        ) / entry

    else:

        gross = (
            entry - exit_price
        ) / entry

    net = gross - ROUND_TRIP_FEE

    return net


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df1h, df15, df5):

    print("=" * 70)
    print("RUNNING ICHIMOKU EQUILIBRIUM v3.0")
    print("=" * 70)

    capital = INITIAL_CAPITAL

    trades = []

    equity_rows = []

    active_trade = None

    # --------------------------------------------------------
    # Pivot levels are based only on completed historical
    # 15M candles.
    # --------------------------------------------------------

    pivot_highs = []
    pivot_lows = []

    start_time = (
        df5["datetime"].min()
        +
        pd.Timedelta(days=20)
    )

    df5_indices = df5.index[
        df5["datetime"] >= start_time
    ].tolist()

    print(
        f"5M candles used: {len(df5_indices):,}"
    )

    print()

    # --------------------------------------------------------
    # Main loop
    # --------------------------------------------------------

    for global_idx in df5_indices:

        current_time = df5.loc[
            global_idx,
            "datetime"
        ]

        row5 = df5.loc[
            global_idx
        ]

        # ====================================================
        # UPDATE / CLOSE ACTIVE TRADE
        # ====================================================

        if active_trade is not None:

            direction = active_trade["direction"]

            stop = active_trade["stop_loss"]

            target = active_trade["take_profit"]

            exit_price = None
            exit_reason = None

            high = float(row5["high"])
            low = float(row5["low"])

            if direction == "LONG":

                stop_hit = low <= stop
                target_hit = high >= target

                # Conservative rule:
                # if both SL and TP occur inside the same
                # candle, assume SL happened first.

                if stop_hit and target_hit:

                    exit_price = stop
                    exit_reason = "SL"

                elif stop_hit:

                    exit_price = stop
                    exit_reason = "SL"

                elif target_hit:

                    exit_price = target
                    exit_reason = "TP"

            else:

                stop_hit = high >= stop
                target_hit = low <= target

                if stop_hit and target_hit:

                    exit_price = stop
                    exit_reason = "SL"

                elif stop_hit:

                    exit_price = stop
                    exit_reason = "SL"

                elif target_hit:

                    exit_price = target
                    exit_reason = "TP"

            if exit_price is not None:

                trade_return = calculate_trade_return(
                    active_trade["entry"],
                    exit_price,
                    direction
                )

                pnl = capital * trade_return

                capital += pnl

                trade = {
                    "entry_time": active_trade["entry_time"],
                    "exit_time": current_time,
                    "direction": direction,
                    "entry": active_trade["entry"],
                    "stop_loss": stop,
                    "take_profit": target,
                    "exit": exit_price,
                    "return_pct": trade_return * 100.0,
                    "pnl": pnl,
                    "capital_after": capital,
                    "exit_reason": exit_reason,
                    "rr": active_trade["rr"],
                }

                trades.append(trade)

                equity_rows.append(
                    {
                        "datetime": current_time,
                        "capital": capital,
                    }
                )

                active_trade = None

                continue

        # ====================================================
        # EQUITY
        # ====================================================

        equity_rows.append(
            {
                "datetime": current_time,
                "capital": capital,
            }
        )

        # ====================================================
        # ONLY ONE ACTIVE TRADE
        # ====================================================

        if active_trade is not None:
            continue

        # ====================================================
        # FIND CURRENT 15M CANDLE
        # ====================================================

        pos15 = df15["datetime"].searchsorted(
            current_time,
            side="right"
        ) - 1

        if pos15 < 2:
            continue

        # Must use completed 15M candle.
        row15 = df15.iloc[pos15]

        # ====================================================
        # FIND CURRENT 1H CANDLE
        # ====================================================

        pos1h = df1h["datetime"].searchsorted(
            current_time,
            side="right"
        ) - 1

        if pos1h < 2:
            continue

        # Completed 1H candle only.
        row1h = df1h.iloc[pos1h]

        # ====================================================
        # 1H REGIME
        # ====================================================

        if not valid_1h_regime(
            df1h,
            pos1h
        ):
            continue

        # ====================================================
        # 5M CURRENT POSITION
        # ====================================================

        pos5 = global_idx

        if pos5 < 2:
            continue

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
                    df5.loc[
                        global_idx,
                        "close"
                    ]
                )

                stop = calculate_stop_loss(
                    df5,
                    pos5,
                    "LONG",
                    entry
                )

                if stop is not None:

                    # Add only pivots confirmed before
                    # the current 15M candle.

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

                    target = select_take_profit(
                        entry,
                        stop,
                        "LONG",
                        resistance_levels,
                        support_levels
                    )

                    if target is not None:

                        risk = abs(
                            entry - stop
                        )

                        reward = abs(
                            target - entry
                        )

                        rr = (
                            reward / risk
                            if risk > 0
                            else 0
                        )

                        active_trade = {
                            "entry_time": current_time,
                            "direction": "LONG",
                            "entry": entry,
                            "stop_loss": stop,
                            "take_profit": target,
                            "rr": rr,
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
                    df5.loc[
                        global_idx,
                        "close"
                    ]
                )

                stop = calculate_stop_loss(
                    df5,
                    pos5,
                    "SHORT",
                    entry
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

                    target = select_take_profit(
                        entry,
                        stop,
                        "SHORT",
                        resistance_levels,
                        support_levels
                    )

                    if target is not None:

                        risk = abs(
                            entry - stop
                        )

                        reward = abs(
                            target - entry
                        )

                        rr = (
                            reward / risk
                            if risk > 0
                            else 0
                        )

                        active_trade = {
                            "entry_time": current_time,
                            "direction": "SHORT",
                            "entry": entry,
                            "stop_loss": stop,
                            "take_profit": target,
                            "rr": rr,
                        }

                        continue

        # ====================================================
        # UPDATE PIVOTS AFTER SIGNAL PROCESSING
        #
        # This prevents future information from being used.
        # ====================================================

        if pos15 >= 4:

            p = pos15 - 2

            if p >= 2 and p + 2 < len(df15):

                high = df15.iloc[p]["high"]

                low = df15.iloc[p]["low"]

                left_high = df15.iloc[
                    p - 2:p
                ]["high"].max()

                right_high = df15.iloc[
                    p + 1:p + 3
                ]["high"].max()

                left_low = df15.iloc[
                    p - 2:p
                ]["low"].min()

                right_low = df15.iloc[
                    p + 1:p + 3
                ]["low"].min()

                pivot_time = df15.iloc[
                    p
                ]["datetime"]

                if (
                    high > left_high
                    and high >= right_high
                ):

                    if not any(
                        dt == pivot_time
                        for dt, _ in pivot_highs
                    ):

                        pivot_highs.append(
                            (
                                pivot_time,
                                float(high)
                            )
                        )

                if (
                    low < left_low
                    and low <= right_low
                ):

                    if not any(
                        dt == pivot_time
                        for dt, _ in pivot_lows
                    ):

                        pivot_lows.append(
                            (
                                pivot_time,
                                float(low)
                            )
                        )

    # ========================================================
    # CLOSE OPEN TRADE AT END
    # ========================================================

    if active_trade is not None:

        last_row = df5.iloc[-1]

        exit_price = float(
            last_row["close"]
        )

        direction = active_trade["direction"]

        trade_return = calculate_trade_return(
            active_trade["entry"],
            exit_price,
            direction
        )

        pnl = capital * trade_return

        capital += pnl

        trades.append(
            {
                "entry_time": active_trade["entry_time"],
                "exit_time": last_row["datetime"],
                "direction": direction,
                "entry": active_trade["entry"],
                "stop_loss": active_trade["stop_loss"],
                "take_profit": active_trade["take_profit"],
                "exit": exit_price,
                "return_pct": trade_return * 100.0,
                "pnl": pnl,
                "capital_after": capital,
                "exit_reason": "END_OF_BACKTEST",
                "rr": active_trade["rr"],
            }
        )

        equity_rows.append(
            {
                "datetime": last_row["datetime"],
                "capital": capital,
            }
        )

    trades_df = pd.DataFrame(trades)

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

    stats = {}

    stats["initial_capital"] = INITIAL_CAPITAL

    stats["final_capital"] = final_capital

    stats["net_return_pct"] = (
        (
            final_capital
            /
            INITIAL_CAPITAL
        )
        -
        1.0
    ) * 100.0

    stats["trades"] = len(
        trades_df
    )

    if trades_df.empty:

        stats["wins"] = 0
        stats["losses"] = 0
        stats["win_rate"] = 0.0
        stats["profit_factor"] = 0.0
        stats["avg_trade_pct"] = 0.0
        stats["max_drawdown_pct"] = 0.0

        return stats

    wins = trades_df[
        trades_df["return_pct"] > 0
    ]

    losses = trades_df[
        trades_df["return_pct"] <= 0
    ]

    stats["wins"] = len(wins)

    stats["losses"] = len(losses)

    stats["win_rate"] = (
        len(wins)
        /
        len(trades_df)
    ) * 100.0

    gross_profit = wins["pnl"].sum()

    gross_loss = abs(
        losses["pnl"].sum()
    )

    if gross_loss > 0:

        stats["profit_factor"] = (
            gross_profit
            /
            gross_loss
        )

    else:

        stats["profit_factor"] = (
            math.inf
            if gross_profit > 0
            else 0.0
        )

    stats["avg_trade_pct"] = (
        trades_df["return_pct"].mean()
    )

    if not equity_df.empty:

        eq = equity_df.copy()

        eq = eq.sort_values(
            "datetime"
        )

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

    else:

        stats["max_drawdown_pct"] = 0.0

    stats["long_trades"] = len(
        trades_df[
            trades_df["direction"] == "LONG"
        ]
    )

    stats["short_trades"] = len(
        trades_df[
            trades_df["direction"] == "SHORT"
        ]
    )

    stats["long_wr"] = 0.0
    stats["short_wr"] = 0.0

    long_df = trades_df[
        trades_df["direction"] == "LONG"
    ]

    short_df = trades_df[
        trades_df["direction"] == "SHORT"
    ]

    if len(long_df) > 0:

        stats["long_wr"] = (
            (
                long_df["return_pct"] > 0
            ).sum()
            /
            len(long_df)
        ) * 100.0

    if len(short_df) > 0:

        stats["short_wr"] = (
            (
                short_df["return_pct"] > 0
            ).sum()
            /
            len(short_df)
        ) * 100.0

    stats["tp_exits"] = len(
        trades_df[
            trades_df["exit_reason"] == "TP"
        ]
    )

    stats["sl_exits"] = len(
        trades_df[
            trades_df["exit_reason"] == "SL"
        ]
    )

    stats["end_exits"] = len(
        trades_df[
            trades_df["exit_reason"]
            == "END_OF_BACKTEST"
        ]
    )

    return stats


# ============================================================
# MONTHLY PERFORMANCE
# ============================================================

def monthly_performance(trades_df):

    if trades_df.empty:
        return pd.DataFrame()

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
            trades=("return_pct", "count"),
            pnl_pct=("return_pct", "sum"),
            pnl=("pnl", "sum"),
        )
        .reset_index()
    )

    return monthly


# ============================================================
# SAVE REPORT
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
        "============================================================"
    )

    lines.append(
        "ICHIMOKU EQUILIBRIUM v3.0 - DOGE 12M BACKTEST"
    )

    lines.append(
        "============================================================"
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
        "------------------------------------------------------------"
    )

    lines.append(
        f"Initial Capital: ${stats['initial_capital']:.2f}"
    )

    lines.append(
        f"Final Capital:   ${stats['final_capital']:.2f}"
    )

    lines.append(
        f"Net Return:      {stats['net_return_pct']:.3f}%"
    )

    lines.append(
        f"Trades:          {stats['trades']}"
    )

    lines.append(
        f"Wins:            {stats['wins']}"
    )

    lines.append(
        f"Losses:          {stats['losses']}"
    )

    lines.append(
        f"Win Rate:        {stats['win_rate']:.2f}%"
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
        f"Profit Factor:   {pf_text}"
    )

    lines.append(
        f"Avg Trade:       {stats['avg_trade_pct']:.4f}%"
    )

    lines.append(
        f"Max Drawdown:    {stats['max_drawdown_pct']:.3f}%"
    )

    lines.append("")

    lines.append(
        "DIRECTION"
    )

    lines.append(
        "------------------------------------------------------------"
    )

    lines.append(
        f"Long Trades:     {stats['long_trades']}"
    )

    lines.append(
        f"Long Win Rate:   {stats['long_wr']:.2f}%"
    )

    lines.append(
        f"Short Trades:    {stats['short_trades']}"
    )

    lines.append(
        f"Short Win Rate:  {stats['short_wr']:.2f}%"
    )

    lines.append("")

    lines.append(
        "EXITS"
    )

    lines.append(
        "------------------------------------------------------------"
    )

    lines.append(
        f"TP:              {stats['tp_exits']}"
    )

    lines.append(
        f"SL:              {stats['sl_exits']}"
    )

    lines.append(
        f"End of Backtest: {stats['end_exits']}"
    )

    lines.append("")

    lines.append(
        "PARAMETERS"
    )

    lines.append(
        "------------------------------------------------------------"
    )

    lines.append(
        f"Ichimoku: {TENKAN_PERIOD}/"
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
        f"SL ATR Buffer: {SL_ATR_BUFFER}"
    )

    lines.append(
        f"Max SL: {MAX_SL_DISTANCE_PCT:.2f}%"
    )

    lines.append(
        f"Min TP RR: {MIN_TP_RR:.2f}"
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
        "------------------------------------------------------------"
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
        "============================================================"
    )

    lines.append(
        "END OF REPORT"
    )

    lines.append(
        "============================================================"
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
    print("=" * 70)
    print(
        f"ICHIMOKU EQUILIBRIUM {VERSION}"
    )
    print("=" * 70)

    print(
        f"REAL TRADING: {REAL_TRADING}"
    )

    if REAL_TRADING:

        raise RuntimeError(
            "REAL_TRADING must remain False "
            "for this backtest."
        )

    print()

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    df1h, df15, df5 = (
        fetch_backtest_data()
    )

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

    # --------------------------------------------------------
    # BACKTEST
    # --------------------------------------------------------

    trades_df, equity_df, final_capital = (
        run_backtest(
            df1h,
            df15,
            df5
        )
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
    # CONSOLE REPORT
    # --------------------------------------------------------

    print()
    print("=" * 70)
    print("BACKTEST RESULTS")
    print("=" * 70)

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

    print("=" * 70)
    print("OUTPUT FILES")
    print("=" * 70)

    print(
        TRADES_FILE
    )

    print(
        EQUITY_FILE
    )

    print(
        REPORT_FILE
    )

    print("=" * 70)


if __name__ == "__main__":
    main()
