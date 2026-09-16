# ============================================================
# TENKAN + KIJUN + DISTANCE BACKTEST
# Kraken Futures
# ============================================================
#
# STRATEGY
#
# 1H  = TREND DIRECTION
#       LONG  : Tenkan > Kijun
#       SHORT : Tenkan < Kijun
#
# 15M = TREND STRENGTH
#       Distance between Tenkan/Kijun must be sufficient
#       Distance must be increasing
#
# 5M  = ENTRY
#       Price pulls back to Tenkan
#       Candle closes back in trend direction
#
# SL  = Kijun / Swing
# TP  = Risk * RR
#
# CLOSED CANDLES ONLY
#
# IMPORTANT:
# If both SL and TP are touched inside the same candle,
# SL is assumed first (conservative backtest).
#
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timezone


# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "PF_DOGEUSD"

DAYS = 30

# Ichimoku
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

# ------------------------------------------------------------
# 15M DISTANCE FILTER
# ------------------------------------------------------------

MIN_DISTANCE_PCT = 0.20

# Distance must increase by at least this amount
# Example:
# previous = 0.30%
# current  = 0.35%
# expansion = 0.05%
MIN_DISTANCE_EXPANSION_PCT = 0.00

# ------------------------------------------------------------
# 5M ENTRY
# ------------------------------------------------------------

# Price must come close enough to Tenkan
MAX_TENKAN_DISTANCE_PCT = 0.25

# Candle body confirmation
MIN_BODY_RATIO = 0.20

# ------------------------------------------------------------
# SL
# ------------------------------------------------------------

# Swing lookback on 5M
SWING_LOOKBACK = 6

# Minimum / maximum SL distance
MIN_SL_PCT = 0.20
MAX_SL_PCT = 1.50

# Small buffer beyond structural SL
SL_BUFFER_PCT = 0.05

# ------------------------------------------------------------
# TP
# ------------------------------------------------------------

MIN_RR = 1.50

# ------------------------------------------------------------
# TRADE SETTINGS
# ------------------------------------------------------------

START_BALANCE = 1000.0

RISK_PER_TRADE_PCT = 1.0

MAX_HOLD_BARS = 288
# 288 x 5M = 24 hours

# Allow both directions
ALLOW_LONG = True
ALLOW_SHORT = True

# Cooldown after closing a trade
COOLDOWN_BARS = 1


# ============================================================
# KRAKEN FUTURES API
# ============================================================

BASE_URL = "https://futures.kraken.com/derivatives/api/v3/klines"


# ============================================================
# DOWNLOAD KRAKEN DATA
# ============================================================

def fetch_kraken_ohlc(symbol, interval, days):
    """
    Download historical OHLC candles from Kraken Futures.

    Kraken public endpoint is requested in chunks.
    """

    now = int(time.time())
    start = now - days * 86400

    # Approximate candle duration
    interval_seconds = {
        "1m": 60,
        "5m": 300,
        "15m": 900,
        "30m": 1800,
        "1h": 3600,
        "4h": 14400,
    }

    if interval not in interval_seconds:
        raise ValueError(f"Unsupported interval: {interval}")

    step = interval_seconds[interval]

    all_rows = []

    current = start

    print(f"Downloading {symbol} {interval} data...")

    while current < now:

        params = {
            "symbol": symbol,
            "interval": interval,
            "from": current,
            "to": min(current + step * 500, now),
        }

        try:
            response = requests.get(
                BASE_URL,
                params=params,
                timeout=20
            )

            response.raise_for_status()

            data = response.json()

        except Exception as e:
            print("Download error:", e)
            time.sleep(3)
            continue

        # Kraken may return:
        # {"candles": [...]}
        #
        # or another wrapper depending on API version.

        rows = data.get("candles", [])

        if not rows:
            break

        all_rows.extend(rows)

        try:
            last_ts = int(rows[-1][0])
        except Exception:
            break

        next_current = last_ts + step

        if next_current <= current:
            break

        current = next_current

        time.sleep(0.15)

    if not all_rows:
        raise RuntimeError(
            f"No data returned for {symbol} {interval}"
        )

    df = pd.DataFrame(all_rows)

    # --------------------------------------------------------
    # Normalize Kraken response
    # --------------------------------------------------------

    # Common Kraken Futures format:
    # time, open, high, low, close, volume

    if df.shape[1] >= 6:

        df = df.iloc[:, :6]

        df.columns = [
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]

    else:
        raise RuntimeError(
            f"Unexpected Kraken candle format: {df.head()}"
        )

    # Convert types
    df["timestamp"] = pd.to_numeric(
        df["timestamp"],
        errors="coerce"
    )

    # Kraken timestamps can be seconds or milliseconds
    if df["timestamp"].median() > 10_000_000_000:
        df["timestamp"] = df["timestamp"] / 1000

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume"
    ]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna()

    df["datetime"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    df = df.sort_values("datetime")

    df = df.drop_duplicates(
        subset=["datetime"]
    )

    df = df.reset_index(drop=True)

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def add_ichimoku(df):

    df = df.copy()

    df["tenkan"] = (
        df["high"].rolling(TENKAN_PERIOD).max()
        +
        df["low"].rolling(TENKAN_PERIOD).min()
    ) / 2

    df["kijun"] = (
        df["high"].rolling(KIJUN_PERIOD).max()
        +
        df["low"].rolling(KIJUN_PERIOD).min()
    ) / 2

    df["tk_distance_pct"] = (
        abs(df["tenkan"] - df["kijun"])
        /
        df["kijun"]
        *
        100
    )

    df["tk_distance_prev"] = (
        df["tk_distance_pct"].shift(1)
    )

    df["tk_distance_expansion"] = (
        df["tk_distance_pct"]
        -
        df["tk_distance_prev"]
    )

    return df


# ============================================================
# MERGE MULTI TIMEFRAME DATA
# ============================================================

def prepare_data(df_1h, df_15m, df_5m):

    df_1h = add_ichimoku(df_1h)
    df_15m = add_ichimoku(df_15m)
    df_5m = add_ichimoku(df_5m)

    # Rename higher timeframe columns
    h1 = df_1h[
        [
            "datetime",
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion"
        ]
    ].copy()

    h1.columns = [
        "datetime",
        "h1_tenkan",
        "h1_kijun",
        "h1_distance",
        "h1_expansion"
    ]

    m15 = df_15m[
        [
            "datetime",
            "tenkan",
            "kijun",
            "tk_distance_pct",
            "tk_distance_expansion"
        ]
    ].copy()

    m15.columns = [
        "datetime",
        "m15_tenkan",
        "m15_kijun",
        "m15_distance",
        "m15_expansion"
    ]

    m5 = df_5m.copy()

    # merge_asof means:
    # for each 5M candle, use the latest CLOSED
    # higher timeframe candle.

    result = pd.merge_asof(
        m5.sort_values("datetime"),
        h1.sort_values("datetime"),
        on="datetime",
        direction="backward"
    )

    result = pd.merge_asof(
        result.sort_values("datetime"),
        m15.sort_values("datetime"),
        on="datetime",
        direction="backward"
    )

    return result.reset_index(drop=True)


# ============================================================
# HELPERS
# ============================================================

def body_ratio(row):

    candle_range = row["high"] - row["low"]

    if candle_range <= 0:
        return 0

    body = abs(
        row["close"] - row["open"]
    )

    return body / candle_range


def is_long_confirmation(row):

    if row["close"] <= row["open"]:
        return False

    ratio = body_ratio(row)

    if ratio < MIN_BODY_RATIO:
        return False

    return True


def is_short_confirmation(row):

    if row["close"] >= row["open"]:
        return False

    ratio = body_ratio(row)

    if ratio < MIN_BODY_RATIO:
        return False

    return True


# ============================================================
# SL CALCULATION
# ============================================================

def calculate_long_sl(df, i):

    row = df.iloc[i]

    start = max(
        0,
        i - SWING_LOOKBACK
    )

    recent_low = df.iloc[
        start:i + 1
    ]["low"].min()

    candidates = []

    # Kijun
    if pd.notna(row["kijun"]):
        candidates.append(
            row["kijun"]
        )

    # Swing
    candidates.append(
        recent_low
    )

    # For LONG choose the highest valid
    # structural support below entry.
    valid = [
        x for x in candidates
        if x < row["close"]
    ]

    if not valid:
        return None

    sl = max(valid)

    sl *= (
        1 -
        SL_BUFFER_PCT / 100
    )

    return sl


def calculate_short_sl(df, i):

    row = df.iloc[i]

    start = max(
        0,
        i - SWING_LOOKBACK
    )

    recent_high = df.iloc[
        start:i + 1
    ]["high"].max()

    candidates = []

    if pd.notna(row["kijun"]):
        candidates.append(
            row["kijun"]
        )

    candidates.append(
        recent_high
    )

    valid = [
        x for x in candidates
        if x > row["close"]
    ]

    if not valid:
        return None

    sl = min(valid)

    sl *= (
        1 +
        SL_BUFFER_PCT / 100
    )

    return sl


# ============================================================
# ENTRY CONDITIONS
# ============================================================

def long_signal(df, i):

    row = df.iloc[i]

    # Need all values
    required = [
        "h1_tenkan",
        "h1_kijun",
        "m15_tenkan",
        "m15_kijun",
        "m15_distance",
        "m15_expansion",
        "tenkan",
        "kijun"
    ]

    for col in required:
        if pd.isna(row[col]):
            return False

    # --------------------------------------------------------
    # 1H TREND
    # --------------------------------------------------------

    if row["h1_tenkan"] <= row["h1_kijun"]:
        return False

    # --------------------------------------------------------
    # 15M DISTANCE
    # --------------------------------------------------------

    if row["m15_distance"] < MIN_DISTANCE_PCT:
        return False

    if row["m15_expansion"] < MIN_DISTANCE_EXPANSION_PCT:
        return False

    # --------------------------------------------------------
    # 5M PRICE / TENKAN
    # --------------------------------------------------------

    tenkan = row["tenkan"]

    distance = (
        abs(row["close"] - tenkan)
        /
        tenkan
        *
        100
    )

    if distance > MAX_TENKAN_DISTANCE_PCT:
        return False

    # Price must close above Tenkan
    if row["close"] <= tenkan:
        return False

    # Confirmation candle
    if not is_long_confirmation(row):
        return False

    return True


def short_signal(df, i):

    row = df.iloc[i]

    required = [
        "h1_tenkan",
        "h1_kijun",
        "m15_tenkan",
        "m15_kijun",
        "m15_distance",
        "m15_expansion",
        "tenkan",
        "kijun"
    ]

    for col in required:
        if pd.isna(row[col]):
            return False

    # --------------------------------------------------------
    # 1H TREND
    # --------------------------------------------------------

    if row["h1_tenkan"] >= row["h1_kijun"]:
        return False

    # --------------------------------------------------------
    # 15M DISTANCE
    # --------------------------------------------------------

    if row["m15_distance"] < MIN_DISTANCE_PCT:
        return False

    if row["m15_expansion"] < MIN_DISTANCE_EXPANSION_PCT:
        return False

    # --------------------------------------------------------
    # 5M PRICE / TENKAN
    # --------------------------------------------------------

    tenkan = row["tenkan"]

    distance = (
        abs(row["close"] - tenkan)
        /
        tenkan
        *
        100
    )

    if distance > MAX_TENKAN_DISTANCE_PCT:
        return False

    # Price must close below Tenkan
    if row["close"] >= tenkan:
        return False

    # Confirmation candle
    if not is_short_confirmation(row):
        return False

    return True


# ============================================================
# TRADE EXIT
# ============================================================

def simulate_trade(
    df,
    entry_index,
    direction,
    entry,
    sl,
    tp
):

    max_index = min(
        len(df) - 1,
        entry_index + MAX_HOLD_BARS
    )

    for j in range(
        entry_index + 1,
        max_index + 1
    ):

        row = df.iloc[j]

        high = row["high"]
        low = row["low"]

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if direction == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            # Conservative:
            # if both happen on same candle,
            # assume SL first.
            if hit_sl and hit_tp:

                exit_price = sl
                result = "LOSS"

            elif hit_sl:

                exit_price = sl
                result = "LOSS"

            elif hit_tp:

                exit_price = tp
                result = "WIN"

            else:
                continue

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl and hit_tp:

                exit_price = sl
                result = "LOSS"

            elif hit_sl:

                exit_price = sl
                result = "LOSS"

            elif hit_tp:

                exit_price = tp
                result = "WIN"

            else:
                continue

        return {
            "exit_index": j,
            "exit_time": row["datetime"],
            "exit_price": exit_price,
            "result": result,
        }

    # --------------------------------------------------------
    # TIME EXIT
    # --------------------------------------------------------

    last_row = df.iloc[max_index]

    return {
        "exit_index": max_index,
        "exit_time": last_row["datetime"],
        "exit_price": last_row["close"],
        "result": "TIME"
    }


# ============================================================
# BACKTEST ENGINE
# ============================================================

def run_backtest(df):

    balance = START_BALANCE

    equity_curve = [balance]

    trades = []

    i = 1

    cooldown_until = -1

    while i < len(df) - 1:

        if i <= cooldown_until:
            i += 1
            continue

        row = df.iloc[i]

        direction = None

        # ----------------------------------------------------
        # SIGNAL
        # ----------------------------------------------------

        if ALLOW_LONG and long_signal(df, i):

            direction = "LONG"

        elif ALLOW_SHORT and short_signal(df, i):

            direction = "SHORT"

        if direction is None:

            i += 1
            continue

        entry = row["close"]

        # ----------------------------------------------------
        # SL
        # ----------------------------------------------------

        if direction == "LONG":

            sl = calculate_long_sl(
                df,
                i
            )

        else:

            sl = calculate_short_sl(
                df,
                i
            )

        if sl is None:

            i += 1
            continue

        # ----------------------------------------------------
        # SL DISTANCE
        # ----------------------------------------------------

        sl_distance_pct = (
            abs(entry - sl)
            /
            entry
            *
            100
        )

        if sl_distance_pct < MIN_SL_PCT:

            i += 1
            continue

        if sl_distance_pct > MAX_SL_PCT:

            i += 1
            continue

        # ----------------------------------------------------
        # TP
        # ----------------------------------------------------

        risk = abs(
            entry - sl
        )

        if direction == "LONG":

            tp = entry + (
                risk * MIN_RR
            )

        else:

            tp = entry - (
                risk * MIN_RR
            )

        # ----------------------------------------------------
        # SIMULATE
        # ----------------------------------------------------

        outcome = simulate_trade(
            df,
            i,
            direction,
            entry,
            sl,
            tp
        )

        exit_price = outcome[
            "exit_price"
        ]

        # ----------------------------------------------------
        # PNL
        # ----------------------------------------------------

        if direction == "LONG":

            pnl_pct = (
                exit_price - entry
            ) / entry * 100

        else:

            pnl_pct = (
                entry - exit_price
            ) / entry * 100

        # ----------------------------------------------------
        # Risk-normalized R
        # ----------------------------------------------------

        if direction == "LONG":

            risk_price = entry - sl
            reward_price = (
                exit_price - entry
            )

        else:

            risk_price = sl - entry
            reward_price = (
                entry - exit_price
            )

        if risk_price > 0:

            r_multiple = (
                reward_price
                /
                risk_price
            )

        else:

            r_multiple = 0

        # ----------------------------------------------------
        # ACCOUNT PNL
        # ----------------------------------------------------

        account_pnl = (
            balance
            *
            (
                RISK_PER_TRADE_PCT / 100
            )
            *
            r_multiple
        )

        balance += account_pnl

        equity_curve.append(
            balance
        )

        trades.append({

            "entry_time":
                row["datetime"],

            "exit_time":
                outcome["exit_time"],

            "direction":
                direction,

            "entry":
                entry,

            "sl":
                sl,

            "tp":
                tp,

            "exit":
                exit_price,

            "sl_pct":
                sl_distance_pct,

            "tp_pct":
                (
                    abs(tp - entry)
                    /
                    entry
                    *
                    100
                ),

            "rr":
                MIN_RR,

            "result":
                outcome["result"],

            "pnl_pct":
                pnl_pct,

            "R":
                r_multiple,

            "account_pnl":
                account_pnl,

            "balance":
                balance,

        })

        # ----------------------------------------------------
        # MOVE AFTER EXIT
        # ----------------------------------------------------

        cooldown_until = (
            outcome["exit_index"]
            +
            COOLDOWN_BARS
        )

        i = (
            outcome["exit_index"]
            +
            1
        )

    return (
        pd.DataFrame(trades),
        equity_curve
    )


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    trades,
    equity_curve
):

    if trades.empty:

        print("\nNO TRADES FOUND.")
        return

    total = len(trades)

    wins = len(
        trades[
            trades["result"] == "WIN"
        ]
    )

    losses = len(
        trades[
            trades["result"] == "LOSS"
        ]
    )

    time_exits = len(
        trades[
            trades["result"] == "TIME"
        ]
    )

    wr = (
        wins / total * 100
        if total
        else 0
    )

    gross_profit = trades.loc[
        trades["account_pnl"] > 0,
        "account_pnl"
    ].sum()

    gross_loss = abs(
        trades.loc[
            trades["account_pnl"] < 0,
            "account_pnl"
        ].sum()
    )

    if gross_loss > 0:

        pf = (
            gross_profit
            /
            gross_loss
        )

    else:

        pf = np.inf

    final_balance = (
        equity_curve[-1]
    )

    net_pnl = (
        final_balance
        -
        START_BALANCE
    )

    net_pnl_pct = (
        net_pnl
        /
        START_BALANCE
        *
        100
    )

    # --------------------------------------------------------
    # MAX DRAWDOWN
    # --------------------------------------------------------

    equity = np.array(
        equity_curve,
        dtype=float
    )

    peaks = np.maximum.accumulate(
        equity
    )

    drawdowns = (
        equity - peaks
    ) / peaks * 100

    max_dd = drawdowns.min()

    avg_r = trades["R"].mean()

    avg_win_r = trades.loc[
        trades["R"] > 0,
        "R"
    ].mean()

    avg_loss_r = trades.loc[
        trades["R"] < 0,
        "R"
    ].mean()

    print("\n")
    print("=" * 65)
    print("TENKAN + KIJUN DISTANCE BACKTEST")
    print("=" * 65)

    print(
        f"Symbol              : {SYMBOL}"
    )

    print(
        f"Period              : {DAYS} days"
    )

    print(
        f"Initial Balance     : ${START_BALANCE:.2f}"
    )

    print(
        f"Final Balance       : ${final_balance:.2f}"
    )

    print("-" * 65)

    print(
        f"Trades              : {total}"
    )

    print(
        f"Wins                : {wins}"
    )

    print(
        f"Losses              : {losses}"
    )

    print(
        f"Time Exits          : {time_exits}"
    )

    print(
        f"Win Rate            : {wr:.2f}%"
    )

    print(
        f"Profit Factor       : {pf:.3f}"
    )

    print(
        f"Net PnL             : {net_pnl_pct:+.2f}%"
    )

    print(
        f"Max Drawdown        : {max_dd:.2f}%"
    )

    print(
        f"Average R           : {avg_r:.3f}"
    )

    print(
        f"Average Win R       : {avg_win_r:.3f}"
    )

    print(
        f"Average Loss R      : {avg_loss_r:.3f}"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # DIRECTION STATS
    # --------------------------------------------------------

    print("\nDIRECTION STATS")
    print("-" * 65)

    for direction in [
        "LONG",
        "SHORT"
    ]:

        d = trades[
            trades["direction"] == direction
        ]

        if d.empty:
            continue

        dw = len(
            d[
                d["result"] == "WIN"
            ]
        )

        dwr = (
            dw / len(d) * 100
        )

        dpnl = d[
            "account_pnl"
        ].sum()

        print(
            f"{direction:<8}"
            f" Trades={len(d):<5}"
            f" WR={dwr:>6.2f}%"
            f" PnL=${dpnl:+.2f}"
        )

    print("=" * 65)

    # --------------------------------------------------------
    # LAST TRADES
    # --------------------------------------------------------

    print("\nLAST 20 TRADES")
    print("-" * 65)

    display_cols = [
        "entry_time",
        "direction",
        "entry",
        "sl",
        "tp",
        "exit",
        "result",
        "pnl_pct",
        "R"
    ]

    print(
        trades[
            display_cols
        ].tail(20).to_string(
            index=False
        )
    )

    print("=" * 65)


# ============================================================
# SAVE RESULTS
# ============================================================

def save_results(trades):

    if trades.empty:
        return

    filename = (
        "tenkan_kijun_backtest.csv"
    )

    trades.to_csv(
        filename,
        index=False
    )

    print(
        f"\nTrade history saved to: "
        f"{filename}"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 65)
    print(
        "TENKAN + KIJUN + DISTANCE BACKTEST"
    )
    print("=" * 65)

    print(
        f"Symbol: {SYMBOL}"
    )

    print(
        f"Days: {DAYS}"
    )

    print(
        f"1H Trend: Tenkan/Kijun"
    )

    print(
        f"15M Min Distance: "
        f"{MIN_DISTANCE_PCT:.2f}%"
    )

    print(
        f"15M Min Expansion: "
        f"{MIN_DISTANCE_EXPANSION_PCT:.2f}%"
    )

    print(
        f"5M Max Tenkan Distance: "
        f"{MAX_TENKAN_DISTANCE_PCT:.2f}%"
    )

    print(
        f"Minimum RR: {MIN_RR:.2f}"
    )

    print("=" * 65)

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    df_1h = fetch_kraken_ohlc(
        SYMBOL,
        "1h",
        DAYS
    )

    df_15m = fetch_kraken_ohlc(
        SYMBOL,
        "15m",
        DAYS
    )

    df_5m = fetch_kraken_ohlc(
        SYMBOL,
        "5m",
        DAYS
    )

    print(
        f"\n1H candles : {len(df_1h)}"
    )

    print(
        f"15M candles: {len(df_15m)}"
    )

    print(
        f"5M candles : {len(df_5m)}"
    )

    # --------------------------------------------------------
    # PREPARE
    # --------------------------------------------------------

    df = prepare_data(
        df_1h,
        df_15m,
        df_5m
    )

    # Remove incomplete/latest candle
    # to guarantee closed candles only.
    if len(df) > 1:

        df = df.iloc[:-1].copy()

    print(
        f"Backtest candles: {len(df)}"
    )

    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    trades, equity_curve = run_backtest(
        df
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    calculate_performance(
        trades,
        equity_curve
    )

    save_results(
        trades
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
