# ============================================================
# TENKAN + KIJUN + DISTANCE BACKTEST
# REAL TENKAN PULLBACK
# TP = 1R
# ============================================================

import requests
import pandas as pd
import numpy as np
import time
from datetime import datetime, timedelta, timezone

# ============================================================
# SETTINGS
# ============================================================

SYMBOL = "PF_DOGEUSD"

DAYS = 30
TICK_TYPE = "trade"

BASE_URL = "https://futures.kraken.com/api/charts/v1"

# Ichimoku
TENKAN_PERIOD = 9
KIJUN_PERIOD = 26

# Distance filter
MIN_DISTANCE_PCT = 0.20
MIN_DISTANCE_EXPANSION = 0.00
MAX_TENKAN_DISTANCE_PCT = 0.25

# Confirmation
MIN_CONFIRM_BODY_RATIO = 0.30

# Stop Loss
MIN_SL_PCT = 0.20
MAX_SL_PCT = 1.50
SL_BUFFER_PCT = 0.05

# Take Profit
MIN_RR = 1.00

# Risk
INITIAL_BALANCE = 1000.0
RISK_PER_TRADE_PCT = 1.0

# Time exit
MAX_HOLD_BARS = 288

# Pullback
TENKAN_TOUCH_TOLERANCE_PCT = 0.05

# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Mozilla/5.0"
})


# ============================================================
# DOWNLOAD KRAKEN CANDLES
# ============================================================

def fetch_kraken_candles(symbol, resolution, start_dt, end_dt):

    url = f"{BASE_URL}/{TICK_TYPE}/{symbol}/{resolution}"

    params = {
        "from": int(start_dt.timestamp()),
        "to": int(end_dt.timestamp())
    }

    r = session.get(url, params=params, timeout=30)
    r.raise_for_status()

    data = r.json()

    if "candles" not in data:
        raise RuntimeError(
            f"Kraken response does not contain candles: {data}"
        )

    candles = data["candles"]

    if not candles:
        return pd.DataFrame(
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

    rows = []

    for c in candles:

        rows.append({
            "time": pd.to_datetime(
                int(c["time"]),
                unit="ms",
                utc=True
            ),
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low": float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c["volume"])
        })

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    df = df.sort_values("time")
    df = df.drop_duplicates("time")
    df = df.reset_index(drop=True)

    return df


# ============================================================
# DOWNLOAD FULL PERIOD IN CHUNKS
# ============================================================

def download_full_period(symbol, resolution, days):

    end_dt = datetime.now(timezone.utc)
    start_dt = end_dt - timedelta(days=days)

    minutes_per_candle = {
        "1m": 1,
        "5m": 5,
        "15m": 15,
        "30m": 30,
        "1h": 60,
        "4h": 240,
        "12h": 720,
        "1d": 1440
    }[resolution]

    max_candles = 2000

    chunk_minutes = minutes_per_candle * max_candles

    all_parts = []

    current_start = start_dt
    request_number = 1

    print(
        f"\nDownloading {symbol} {resolution}..."
    )

    while current_start < end_dt:

        current_end = min(
            current_start +
            timedelta(minutes=chunk_minutes),
            end_dt
        )

        print(
            f"  Request {request_number}: "
            f"{current_start} -> {current_end}"
        )

        df = fetch_kraken_candles(
            symbol,
            resolution,
            current_start,
            current_end
        )

        print(
            f"  received {len(df)} candles"
        )

        if not df.empty:
            all_parts.append(df)

        request_number += 1

        # Move forward exactly to avoid missing candles
        current_start = current_end

        time.sleep(0.15)

    if not all_parts:

        return pd.DataFrame(
            columns=[
                "time",
                "open",
                "high",
                "low",
                "close",
                "volume"
            ]
        )

    df = pd.concat(
        all_parts,
        ignore_index=True
    )

    df = df.sort_values("time")
    df = df.drop_duplicates("time")
    df = df.reset_index(drop=True)

    expected = int(
        days * 24 * 60 / minutes_per_candle
    )

    coverage = (
        len(df) / expected * 100
        if expected > 0
        else 0
    )

    print(
        f"Final {resolution} candles: {len(df)}"
    )

    if not df.empty:

        print(
            f"Range: "
            f"{df['time'].iloc[0]} -> "
            f"{df['time'].iloc[-1]}"
        )

    print(
        f"Expected ~{expected} candles | "
        f"Coverage: {coverage:.1f}%"
    )

    return df


# ============================================================
# ICHIMOKU
# ============================================================

def add_ichimoku(df):

    df = df.copy()

    high_9 = (
        df["high"]
        .rolling(TENKAN_PERIOD)
        .max()
    )

    low_9 = (
        df["low"]
        .rolling(TENKAN_PERIOD)
        .min()
    )

    df["tenkan"] = (
        high_9 + low_9
    ) / 2.0

    high_26 = (
        df["high"]
        .rolling(KIJUN_PERIOD)
        .max()
    )

    low_26 = (
        df["low"]
        .rolling(KIJUN_PERIOD)
        .min()
    )

    df["kijun"] = (
        high_26 + low_26
    ) / 2.0

    return df


# ============================================================
# PREPARE HIGHER TIMEFRAMES
# ============================================================

def prepare_higher_timeframe(df):

    df = add_ichimoku(df)

    # Conservative:
    # do not allow current unfinished HTF candle
    df["tenkan"] = df["tenkan"].shift(1)
    df["kijun"] = df["kijun"].shift(1)

    df["distance_pct"] = (
        abs(df["tenkan"] - df["kijun"])
        / df["kijun"]
        * 100
    )

    df["distance_prev"] = (
        df["distance_pct"].shift(1)
    )

    df["distance_expanding"] = (
        df["distance_pct"]
        >=
        df["distance_prev"] *
        (1.0 + MIN_DISTANCE_EXPANSION / 100.0)
    )

    return df


# ============================================================
# ALIGN HIGHER TIMEFRAME TO 5M
# ============================================================

def align_htf(base_df, htf_df, prefix):

    cols = [
        "time",
        "tenkan",
        "kijun",
        "distance_pct",
        "distance_expanding"
    ]

    temp = htf_df[cols].copy()

    temp = temp.rename(
        columns={
            "tenkan": f"{prefix}_tenkan",
            "kijun": f"{prefix}_kijun",
            "distance_pct": f"{prefix}_distance_pct",
            "distance_expanding":
                f"{prefix}_distance_expanding"
        }
    )

    base_df = pd.merge_asof(
        base_df.sort_values("time"),
        temp.sort_values("time"),
        on="time",
        direction="backward"
    )

    return base_df


# ============================================================
# REAL TENKAN PULLBACK
# ============================================================

def real_long_pullback(row, previous_row):

    tenkan = row["tenkan_5m"]

    if pd.isna(tenkan):
        return False

    previous_close = previous_row["close"]

    current_low = row["low"]
    current_close = row["close"]

    # Price must have been clearly above Tenkan
    previous_distance = (
        (previous_close - tenkan)
        / tenkan
        * 100
    )

    was_above = (
        previous_close > tenkan
        and previous_distance >=
        TENKAN_TOUCH_TOLERANCE_PCT
    )

    # Current candle must actually pull back
    # into Tenkan zone
    touch_zone = (
        current_low
        <=
        tenkan *
        (1.0 + TENKAN_TOUCH_TOLERANCE_PCT / 100.0)
    )

    # Confirmation must close back above Tenkan
    confirmation = (
        current_close > tenkan
    )

    return (
        was_above
        and touch_zone
        and confirmation
    )


def real_short_pullback(row, previous_row):

    tenkan = row["tenkan_5m"]

    if pd.isna(tenkan):
        return False

    previous_close = previous_row["close"]

    current_high = row["high"]
    current_close = row["close"]

    # Price must have been clearly below Tenkan
    previous_distance = (
        (tenkan - previous_close)
        / tenkan
        * 100
    )

    was_below = (
        previous_close < tenkan
        and previous_distance >=
        TENKAN_TOUCH_TOLERANCE_PCT
    )

    # Current candle must actually pull back
    # into Tenkan zone
    touch_zone = (
        current_high
        >=
        tenkan *
        (1.0 - TENKAN_TOUCH_TOLERANCE_PCT / 100.0)
    )

    # Confirmation must close back below Tenkan
    confirmation = (
        current_close < tenkan
    )

    return (
        was_below
        and touch_zone
        and confirmation
    )


# ============================================================
# BODY CONFIRMATION
# ============================================================

def body_ratio(row):

    candle_range = (
        row["high"] - row["low"]
    )

    if candle_range <= 0:
        return 0.0

    body = abs(
        row["close"] - row["open"]
    )

    return body / candle_range


# ============================================================
# BACKTEST
# ============================================================

def run_backtest(df):

    trades = []

    balance = INITIAL_BALANCE
    equity_curve = [balance]

    open_trade = None

    for i in range(1, len(df)):

        row = df.iloc[i]
        prev = df.iloc[i - 1]

        if (
            pd.isna(row["h1_tenkan"])
            or pd.isna(row["h1_kijun"])
            or pd.isna(row["m15_tenkan"])
            or pd.isna(row["m15_kijun"])
            or pd.isna(row["tenkan_5m"])
            or pd.isna(row["kijun_5m"])
        ):
            continue

        # ====================================================
        # MANAGE OPEN TRADE
        # ====================================================

        if open_trade is not None:

            direction = open_trade["direction"]

            entry = open_trade["entry"]
            sl = open_trade["sl"]
            tp = open_trade["tp"]

            bars_held = (
                i - open_trade["entry_index"]
            )

            exit_price = None
            result = None

            if direction == "LONG":

                # Conservative:
                # if SL and TP happen in same candle,
                # SL is assumed first.
                if row["low"] <= sl:
                    exit_price = sl
                    result = "LOSS"

                elif row["high"] >= tp:
                    exit_price = tp
                    result = "WIN"

            else:

                if row["high"] >= sl:
                    exit_price = sl
                    result = "LOSS"

                elif row["low"] <= tp:
                    exit_price = tp
                    result = "WIN"

            if (
                exit_price is None
                and bars_held >= MAX_HOLD_BARS
            ):

                exit_price = row["close"]
                result = "TIME"

            if exit_price is not None:

                risk_distance = abs(
                    entry - sl
                )

                if direction == "LONG":

                    r_value = (
                        exit_price - entry
                    ) / risk_distance

                else:

                    r_value = (
                        entry - exit_price
                    ) / risk_distance

                risk_amount = (
                    balance *
                    RISK_PER_TRADE_PCT /
                    100.0
                )

                pnl_pct = (
                    r_value *
                    RISK_PER_TRADE_PCT
                )

                pnl_amount = (
                    risk_amount *
                    r_value
                )

                balance += pnl_amount

                trades.append({
                    "entry_time":
                        open_trade["entry_time"],
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
                    "result":
                        result,
                    "pnl_pct":
                        pnl_pct,
                    "R":
                        r_value
                })

                equity_curve.append(balance)

                open_trade = None

                continue

        # ====================================================
        # ENTRY FILTERS
        # ====================================================

        # 1H direction
        if row["h1_tenkan"] > row["h1_kijun"]:
            direction = "LONG"

        elif row["h1_tenkan"] < row["h1_kijun"]:
            direction = "SHORT"

        else:
            continue

        # 15M direction must agree
        if direction == "LONG":

            if row["m15_tenkan"] <= row["m15_kijun"]:
                continue

        else:

            if row["m15_tenkan"] >= row["m15_kijun"]:
                continue

        # 15M minimum distance
        if (
            row["m15_distance_pct"]
            < MIN_DISTANCE_PCT
        ):
            continue

        # 15M distance expansion
        if not row["m15_distance_expanding"]:
            continue

        # 5M Tenkan/Kijun direction
        if direction == "LONG":

            if row["tenkan_5m"] <= row["kijun_5m"]:
                continue

        else:

            if row["tenkan_5m"] >= row["kijun_5m"]:
                continue

        # ====================================================
        # REAL TENKAN PULLBACK
        # ====================================================

        if direction == "LONG":

            if not real_long_pullback(
                row,
                prev
            ):
                continue

        else:

            if not real_short_pullback(
                row,
                prev
            ):
                continue

        # ====================================================
        # BODY CONFIRMATION
        # ====================================================

        ratio = body_ratio(row)

        if ratio < MIN_CONFIRM_BODY_RATIO:
            continue

        # Confirmation candle direction
        if direction == "LONG":

            if row["close"] <= row["open"]:
                continue

        else:

            if row["close"] >= row["open"]:
                continue

        # ====================================================
        # ENTRY
        # ====================================================

        entry = row["close"]

        # ====================================================
        # STOP LOSS
        # ====================================================

        if direction == "LONG":

            kijun = row["kijun_5m"]

            fallback_sl = (
                df.iloc[
                    max(0, i - 10):i + 1
                ]["low"].min()
            )

            if kijun < entry:
                sl_base = kijun
            else:
                sl_base = fallback_sl

            sl = (
                sl_base *
                (1.0 - SL_BUFFER_PCT / 100.0)
            )

            sl_distance_pct = (
                (entry - sl)
                / entry
                * 100
            )

        else:

            kijun = row["kijun_5m"]

            fallback_sl = (
                df.iloc[
                    max(0, i - 10):i + 1
                ]["high"].max()
            )

            if kijun > entry:
                sl_base = kijun
            else:
                sl_base = fallback_sl

            sl = (
                sl_base *
                (1.0 + SL_BUFFER_PCT / 100.0)
            )

            sl_distance_pct = (
                (sl - entry)
                / entry
                * 100
            )

        # SL validity
        if (
            sl_distance_pct < MIN_SL_PCT
            or
            sl_distance_pct > MAX_SL_PCT
        ):
            continue

        # ====================================================
        # TP = 1R
        # ====================================================

        risk_distance = abs(
            entry - sl
        )

        if direction == "LONG":

            tp = (
                entry +
                risk_distance *
                MIN_RR
            )

        else:

            tp = (
                entry -
                risk_distance *
                MIN_RR
            )

        # ====================================================
        # OPEN TRADE
        # ====================================================

        open_trade = {
            "entry_index": i,
            "entry_time": row["time"],
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp": tp
        }

    return (
        trades,
        balance,
        equity_curve
    )


# ============================================================
# REPORT
# ============================================================

def print_report(
    trades,
    final_balance,
    equity_curve
):

    print("\n")
    print("=" * 70)
    print("TENKAN + KIJUN + DISTANCE BACKTEST RESULTS")
    print("=" * 70)

    print(
        f"Symbol              : {SYMBOL}"
    )

    print(
        f"Period              : {DAYS} days"
    )

    print(
        f"TP RR               : {MIN_RR:.2f}R"
    )

    print(
        f"Initial Balance     : "
        f"${INITIAL_BALANCE:.2f}"
    )

    print(
        f"Final Balance       : "
        f"${final_balance:.2f}"
    )

    print("-" * 70)

    total = len(trades)

    wins = sum(
        1 for t in trades
        if t["result"] == "WIN"
    )

    losses = sum(
        1 for t in trades
        if t["result"] == "LOSS"
    )

    time_exits = sum(
        1 for t in trades
        if t["result"] == "TIME"
    )

    wr = (
        wins / total * 100
        if total
        else 0
    )

    gross_profit = sum(
        t["pnl_pct"]
        for t in trades
        if t["pnl_pct"] > 0
    )

    gross_loss = abs(sum(
        t["pnl_pct"]
        for t in trades
        if t["pnl_pct"] < 0
    ))

    pf = (
        gross_profit / gross_loss
        if gross_loss > 0
        else float("inf")
    )

    net_pnl = (
        (final_balance - INITIAL_BALANCE)
        / INITIAL_BALANCE
        * 100
    )

    avg_r = (
        np.mean([t["R"] for t in trades])
        if trades
        else 0
    )

    # ========================================================
    # MAX DRAWDOWN
    # ========================================================

    eq = np.array(equity_curve)

    if len(eq):

        peaks = np.maximum.accumulate(eq)

        drawdowns = (
            (eq - peaks)
            / peaks
            * 100
        )

        max_dd = drawdowns.min()

    else:

        max_dd = 0

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
        f"Net PnL             : {net_pnl:+.2f}%"
    )

    print(
        f"Max Drawdown        : {max_dd:.2f}%"
    )

    print(
        f"Average R           : {avg_r:.3f}"
    )

    # ========================================================
    # DIRECTION STATISTICS
    # ========================================================

    print("\n")
    print("DIRECTION STATISTICS")
    print("-" * 70)

    for direction in ["LONG", "SHORT"]:

        subset = [
            t for t in trades
            if t["direction"] == direction
        ]

        if subset:

            d_wins = sum(
                1 for t in subset
                if t["result"] == "WIN"
            )

            d_wr = (
                d_wins /
                len(subset) *
                100
            )

            d_r = sum(
                t["R"]
                for t in subset
            )

            print(
                f"{direction:<8}"
                f" Trades={len(subset):<4}"
                f" WR={d_wr:6.2f}%"
                f" R={d_r:+.3f}"
            )

    # ========================================================
    # LAST 20 TRADES
    # ========================================================

    print("\n")
    print("LAST 20 TRADES")
    print("-" * 70)

    if trades:

        trade_df = pd.DataFrame(
            trades[-20:]
        )

        print(
            trade_df.to_string(
                index=False
            )
        )

    else:

        print("No trades.")

    # ========================================================
    # SAVE CSV
    # ========================================================

    if trades:

        out = pd.DataFrame(
            trades
        )

        out.to_csv(
            "tenkan_kijun_backtest.csv",
            index=False
        )

        print(
            "\nTrade history saved: "
            "tenkan_kijun_backtest.csv"
        )

    else:

        print(
            "\nNo trade history to save."
        )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 70)
    print("TENKAN + KIJUN + DISTANCE BACKTEST")
    print("REAL TENKAN PULLBACK")
    print("=" * 70)

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    df_1h = download_full_period(
        SYMBOL,
        "1h",
        DAYS
    )

    df_15m = download_full_period(
        SYMBOL,
        "15m",
        DAYS
    )

    df_5m = download_full_period(
        SYMBOL,
        "5m",
        DAYS
    )

    if (
        df_1h.empty
        or df_15m.empty
        or df_5m.empty
    ):

        raise RuntimeError(
            "One or more timeframes returned no data."
        )

    # --------------------------------------------------------
    # PREPARE HTF
    # --------------------------------------------------------

    df_1h = prepare_higher_timeframe(
        df_1h
    )

    df_15m = prepare_higher_timeframe(
        df_15m
    )

    # --------------------------------------------------------
    # PREPARE 5M
    # --------------------------------------------------------

    df_5m = add_ichimoku(
        df_5m
    )

    # --------------------------------------------------------
    # ALIGN 1H + 15M
    # --------------------------------------------------------

    df = df_5m.copy()

    df = align_htf(
        df,
        df_1h,
        "h1"
    )

    df = align_htf(
        df,
        df_15m,
        "m15"
    )

    # --------------------------------------------------------
    # REMOVE INVALID ROWS
    # --------------------------------------------------------

    df = df.dropna(
        subset=[
            "h1_tenkan",
            "h1_kijun",
            "m15_tenkan",
            "m15_kijun",
            "tenkan",
            "kijun"
        ]
    ).reset_index(drop=True)

    # Rename 5M fields
    df = df.rename(
        columns={
            "tenkan": "tenkan_5m",
            "kijun": "kijun_5m"
        }
    )

    print("\n")
    print("=" * 70)
    print(
        f"Backtest 5M candles: {len(df)}"
    )
    print("=" * 70)

    # --------------------------------------------------------
    # RUN
    # --------------------------------------------------------

    trades, final_balance, equity_curve = (
        run_backtest(df)
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    print_report(
        trades,
        final_balance,
        equity_curve
    )


if __name__ == "__main__":
    main()
