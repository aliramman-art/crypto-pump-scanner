# ============================================================
# REVERSE-TENKAN 6 COINS - 90 DAY BACKTEST
# ============================================================
#
# SAME STRATEGY AS 30-DAY VERSION
#
# Universe:
# AAVE, SUI, LINK, DOT, ARB, ZEC
#
# Main TF      : 5M
# Context TF   : 15M
# Tenkan       : 9
# Min Distance : 0.75%
# Min Score    : 8 / 14
# SL           : 2.00%
# Max Hold     : 144 bars = 12 hours
#
# Exit Models:
# TENKAN
# 1R
# 1.5R
# 2R
# 2.5R
# 3R
#
# CLOSED CANDLES ONLY
# SL_FIRST = True
#
# Kraken Futures
# ============================================================

import time
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

BASE_URL = "https://futures.kraken.com/api/charts/v1"

SYMBOLS = [
    "PF_AAVEUSD",
    "PF_SUIUSD",
    "PF_LINKUSD",
    "PF_DOTUSD",
    "PF_ARBUSD",
    "PF_ZECUSD",
]

DAYS = 90

MAIN_RESOLUTION = "5m"
CONTEXT_RESOLUTION = "15m"

TENKAN_PERIOD = 9

MIN_DISTANCE_PCT = 0.75
MIN_SCORE = 8

SL_PCT = 2.00

MAX_HOLD_BARS = 144

RSI_PERIOD = 14

RVOL_PERIOD = 20
RVOL_MIN = 1.20
RVOL_MAX = 3.00

AVG_BODY_PERIOD = 20
AVG_RANGE_PERIOD = 20

EMA_PERIOD_15M = 20

MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00

FEE_PER_SIDE_PCT = 0.06
ROUND_TRIP_FEE_PCT = FEE_PER_SIDE_PCT * 2

SL_FIRST = True

REQUEST_TIMEOUT = 30
REQUEST_SLEEP = 0.20

EXIT_MODELS = {
    "TENKAN": "TENKAN",
    "1R": 1.0,
    "1.5R": 1.5,
    "2R": 2.0,
    "2.5R": 2.5,
    "3R": 3.0,
}


# ============================================================
# HTTP SESSION
# ============================================================

session = requests.Session()
session.headers.update({
    "User-Agent": "Reverse-Tenkan-90D-Backtest/1.0"
})


# ============================================================
# FETCH KRAKEN FUTURES CANDLES
# ============================================================

def fetch_candles(symbol, resolution, start_ts, end_ts):

    url = f"{BASE_URL}/trade/{symbol}/{resolution}"

    all_rows = []

    cursor = int(start_ts)

    while cursor < end_ts:

        params = {
            "from": cursor,
            "to": int(end_ts),
        }

        try:
            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            data = response.json()

        except Exception as e:
            print(f"ERROR {symbol} {resolution}: {e}")
            time.sleep(2)
            continue

        candles = data.get("candles", [])

        if not candles:
            break

        all_rows.extend(candles)

        last_time_ms = max(
            int(x["time"]) for x in candles
        )

        last_time_sec = last_time_ms // 1000

        next_cursor = last_time_sec + 1

        if next_cursor <= cursor:
            break

        cursor = next_cursor

        if not data.get("more_candles", False):
            break

        time.sleep(REQUEST_SLEEP)

    if not all_rows:
        return pd.DataFrame()

    df = pd.DataFrame(all_rows)

    df = df.drop_duplicates(subset=["time"])
    df = df.sort_values("time")

    df["timestamp"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True,
        errors="coerce"
    )

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna(
        subset=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume",
        ]
    )

    df = df.set_index("timestamp")

    df = df[
        ["open", "high", "low", "close", "volume"]
    ]

    return df


# ============================================================
# INDICATORS
# ============================================================

def add_indicators_5m(df):

    df = df.copy()

    # --------------------------------------------------------
    # TENKAN
    # --------------------------------------------------------

    highest = df["high"].rolling(
        TENKAN_PERIOD
    ).max()

    lowest = df["low"].rolling(
        TENKAN_PERIOD
    ).min()

    df["tenkan"] = (
        highest + lowest
    ) / 2

    df["tenkan_prev"] = df["tenkan"].shift(1)

    # --------------------------------------------------------
    # DISTANCE
    # --------------------------------------------------------

    df["distance_pct"] = (
        (df["close"] - df["tenkan"])
        / df["tenkan"]
        * 100
    )

    df["distance_prev_pct"] = (
        df["distance_pct"].shift(1)
    )

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    delta = df["close"].diff()

    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(
        alpha=1 / RSI_PERIOD,
        min_periods=RSI_PERIOD,
        adjust=False
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / RSI_PERIOD,
        min_periods=RSI_PERIOD,
        adjust=False
    ).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)

    df["rsi"] = 100 - (
        100 / (1 + rs)
    )

    df["rsi_prev"] = df["rsi"].shift(1)

    # --------------------------------------------------------
    # VOLUME / RVOL
    # --------------------------------------------------------

    df["avg_volume"] = (
        df["volume"]
        .rolling(RVOL_PERIOD)
        .mean()
    )

    df["rvol"] = (
        df["volume"]
        / df["avg_volume"].replace(0, np.nan)
    )

    # --------------------------------------------------------
    # CANDLE STRUCTURE
    # --------------------------------------------------------

    df["body"] = (
        df["close"] - df["open"]
    ).abs()

    df["range"] = (
        df["high"] - df["low"]
    )

    df["avg_body"] = (
        df["body"]
        .rolling(AVG_BODY_PERIOD)
        .mean()
    )

    df["avg_range"] = (
        df["range"]
        .rolling(AVG_RANGE_PERIOD)
        .mean()
    )

    df["close_position"] = np.where(
        df["range"] > 0,
        (
            df["close"] - df["low"]
        ) / df["range"],
        0.5
    )

    # --------------------------------------------------------
    # PREVIOUS CANDLE
    # --------------------------------------------------------

    df["prev_close"] = df["close"].shift(1)
    df["prev_high"] = df["high"].shift(1)
    df["prev_low"] = df["low"].shift(1)

    return df


# ============================================================
# 15M INDICATORS
# ============================================================

def add_indicators_15m(df):

    df = df.copy()

    df["ema20"] = (
        df["close"]
        .ewm(
            span=EMA_PERIOD_15M,
            adjust=False
        )
        .mean()
    )

    return df


# ============================================================
# MERGE 15M CONTEXT INTO 5M
# ============================================================

def merge_context(df5, df15):

    context = df15[
        ["close", "ema20"]
    ].copy()

    context = context.rename(
        columns={
            "close": "close_15m",
            "ema20": "ema20_15m",
        }
    )

    merged = pd.merge_asof(
        df5.sort_index(),
        context.sort_index(),
        left_index=True,
        right_index=True,
        direction="backward"
    )

    return merged


# ============================================================
# SCORE
# ============================================================

def calculate_score(row, side):

    score = 0

    distance = row["distance_pct"]
    distance_prev = row["distance_prev_pct"]

    # --------------------------------------------------------
    # 1. DISTANCE
    # --------------------------------------------------------

    if side == "LONG":

        if distance <= -MIN_DISTANCE_PCT:
            score += 2

    else:

        if distance >= MIN_DISTANCE_PCT:
            score += 2

    # --------------------------------------------------------
    # 2. DISTANCE CONTRACTING
    # --------------------------------------------------------

    if side == "LONG":

        if distance > distance_prev:
            score += 2

    else:

        if distance < distance_prev:
            score += 2

    # --------------------------------------------------------
    # 3. TENKAN SLOPE REVERSAL
    # --------------------------------------------------------

    tenkan = row["tenkan"]
    tenkan_prev = row["tenkan_prev"]

    if side == "LONG":

        if tenkan > tenkan_prev:
            score += 2

    else:

        if tenkan < tenkan_prev:
            score += 2

    # --------------------------------------------------------
    # 4. REVERSAL CANDLE
    # --------------------------------------------------------

    if side == "LONG":

        reversal = (
            row["close"] > row["open"]
            and row["close"] > row["prev_close"]
            and row["high"] > row["prev_high"]
            and row["close_position"] >= 0.55
        )

    else:

        reversal = (
            row["close"] < row["open"]
            and row["close"] < row["prev_close"]
            and row["low"] < row["prev_low"]
            and row["close_position"] <= 0.45
        )

    if reversal:
        score += 2

    # --------------------------------------------------------
    # 5. RSI REVERSAL
    # --------------------------------------------------------

    if side == "LONG":

        if (
            distance < 0
            and row["rsi"] < 35
            and row["rsi"] > row["rsi_prev"]
        ):
            score += 2

    else:

        if (
            distance > 0
            and row["rsi"] > 65
            and row["rsi"] < row["rsi_prev"]
        ):
            score += 2

    # --------------------------------------------------------
    # 6. RVOL
    # --------------------------------------------------------

    if (
        row["rvol"] >= RVOL_MIN
        and row["rvol"] <= RVOL_MAX
    ):
        score += 1

    # --------------------------------------------------------
    # 7. NO EXPLOSION
    # --------------------------------------------------------

    no_explosion = (
        row["body"]
        <= row["avg_body"]
        * MAX_BODY_MULTIPLIER
        and
        row["range"]
        <= row["avg_range"]
        * MAX_RANGE_MULTIPLIER
    )

    if no_explosion:
        score += 1

    # --------------------------------------------------------
    # 8. 15M EMA CONTEXT
    # --------------------------------------------------------

    if side == "LONG":

        if row["close_15m"] >= row["ema20_15m"]:
            score += 2

    else:

        if row["close_15m"] <= row["ema20_15m"]:
            score += 2

    return score


# ============================================================
# SIGNAL GENERATION
# ============================================================

def generate_signals(df):

    signals = []

    for i in range(len(df)):

        row = df.iloc[i]

        required = [
            row["tenkan"],
            row["tenkan_prev"],
            row["distance_pct"],
            row["distance_prev_pct"],
            row["rsi"],
            row["rsi_prev"],
            row["rvol"],
            row["avg_body"],
            row["avg_range"],
            row["close_15m"],
            row["ema20_15m"],
        ]

        if any(pd.isna(x) for x in required):
            continue

        # ====================================================
        # LONG
        # ====================================================

        if row["distance_pct"] <= -MIN_DISTANCE_PCT:

            score = calculate_score(
                row,
                "LONG"
            )

            if score >= MIN_SCORE:

                entry = row["close"]
                tenkan = row["tenkan"]

                sl = entry * (
                    1 - SL_PCT / 100
                )

                tp = tenkan

                # Original strategy:
                # Tenkan must be profitable
                if tp > entry:

                    signals.append({
                        "index": i,
                        "timestamp": df.index[i],
                        "side": "LONG",
                        "entry": entry,
                        "sl": sl,
                        "tenkan_tp": tp,
                        "score": score,
                    })

        # ====================================================
        # SHORT
        # ====================================================

        elif row["distance_pct"] >= MIN_DISTANCE_PCT:

            score = calculate_score(
                row,
                "SHORT"
            )

            if score >= MIN_SCORE:

                entry = row["close"]
                tenkan = row["tenkan"]

                sl = entry * (
                    1 + SL_PCT / 100
                )

                tp = tenkan

                # Original strategy:
                # Tenkan must be profitable
                if tp < entry:

                    signals.append({
                        "index": i,
                        "timestamp": df.index[i],
                        "side": "SHORT",
                        "entry": entry,
                        "sl": sl,
                        "tenkan_tp": tp,
                        "score": score,
                    })

    return signals


# ============================================================
# BUILD TP
# ============================================================

def build_tp(signal, model):

    entry = signal["entry"]
    sl = signal["sl"]
    tenkan = signal["tenkan_tp"]

    side = signal["side"]

    if model == "TENKAN":
        return tenkan

    risk = abs(entry - sl)

    r_multiple = EXIT_MODELS[model]

    if side == "LONG":
        return entry + (
            risk * r_multiple
        )

    return entry - (
        risk * r_multiple
    )


# ============================================================
# SIMULATE TRADE
# ============================================================

def simulate_trade(
    df,
    signal,
    tp
):

    entry = signal["entry"]
    sl = signal["sl"]
    side = signal["side"]

    start_i = signal["index"]

    end_i = min(
        start_i + MAX_HOLD_BARS,
        len(df) - 1
    )

    exit_price = None
    exit_reason = None
    exit_i = end_i

    for j in range(
        start_i + 1,
        end_i + 1
    ):

        candle = df.iloc[j]

        high = candle["high"]
        low = candle["low"]

        if side == "LONG":

            hit_sl = low <= sl
            hit_tp = high >= tp

            if hit_sl and hit_tp:

                if SL_FIRST:
                    exit_price = sl
                    exit_reason = "SL"
                else:
                    exit_price = tp
                    exit_reason = "TP"

                exit_i = j
                break

            if hit_sl:

                exit_price = sl
                exit_reason = "SL"
                exit_i = j
                break

            if hit_tp:

                exit_price = tp
                exit_reason = "TP"
                exit_i = j
                break

        else:

            hit_sl = high >= sl
            hit_tp = low <= tp

            if hit_sl and hit_tp:

                if SL_FIRST:
                    exit_price = sl
                    exit_reason = "SL"
                else:
                    exit_price = tp
                    exit_reason = "TP"

                exit_i = j
                break

            if hit_sl:

                exit_price = sl
                exit_reason = "SL"
                exit_i = j
                break

            if hit_tp:

                exit_price = tp
                exit_reason = "TP"
                exit_i = j
                break

    # ========================================================
    # TIMEOUT
    # ========================================================

    if exit_price is None:

        exit_price = df.iloc[
            end_i
        ]["close"]

        exit_reason = "TIMEOUT"

    # ========================================================
    # RAW PNL
    # ========================================================

    if side == "LONG":

        pnl_pct = (
            (exit_price - entry)
            / entry
            * 100
        )

    else:

        pnl_pct = (
            (entry - exit_price)
            / entry
            * 100
        )

    # ========================================================
    # FEES
    # ========================================================

    pnl_after_fee = (
        pnl_pct
        - ROUND_TRIP_FEE_PCT
    )

    return {
        "entry_time": signal["timestamp"],
        "exit_time": df.index[exit_i],
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "score": signal["score"],
        "exit_price": exit_price,
        "exit_reason": exit_reason,
        "pnl_pct": pnl_after_fee,
        "hold_bars": exit_i - start_i,
    }


# ============================================================
# RUN ONE MODEL
# ============================================================

def run_model(
    df,
    signals,
    model
):

    trades = []

    for signal in signals:

        tp = build_tp(
            signal,
            model
        )

        # ----------------------------------------------------
        # Safety: TP must be profitable
        # ----------------------------------------------------

        if signal["side"] == "LONG":

            if tp <= signal["entry"]:
                continue

        else:

            if tp >= signal["entry"]:
                continue

        trade = simulate_trade(
            df,
            signal,
            tp
        )

        trade["model"] = model

        trades.append(trade)

    return trades


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(trades):

    if not trades:

        return {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "timeouts": 0,
            "win_rate": 0,
            "gross_profit": 0,
            "gross_loss": 0,
            "pf": 0,
            "net": 0,
            "max_dd": 0,
        }

    pnl = np.array([
        t["pnl_pct"]
        for t in trades
    ])

    wins = pnl[pnl > 0]
    losses = pnl[pnl < 0]

    win_count = len(wins)
    loss_count = len(losses)

    timeout_count = sum(
        t["exit_reason"] == "TIMEOUT"
        for t in trades
    )

    gross_profit = (
        wins.sum()
        if len(wins)
        else 0
    )

    gross_loss = abs(
        losses.sum()
    ) if len(losses) else 0

    if gross_loss > 0:
        pf = (
            gross_profit
            / gross_loss
        )
    else:
        pf = np.inf

    equity = np.cumsum(pnl)

    peak = np.maximum.accumulate(
        equity
    )

    drawdown = peak - equity

    max_dd = (
        drawdown.max()
        if len(drawdown)
        else 0
    )

    return {
        "trades": len(trades),
        "wins": win_count,
        "losses": loss_count,
        "timeouts": timeout_count,
        "win_rate": (
            win_count
            / len(trades)
            * 100
        ),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "pf": pf,
        "net": pnl.sum(),
        "max_dd": max_dd,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 90)
    print("REVERSE-TENKAN 6 COINS - 90 DAY BACKTEST")
    print("=" * 90)

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt
        - timedelta(days=DAYS)
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    all_summary = []
    all_trades = []

    for symbol in SYMBOLS:

        print()
        print("=" * 90)
        print(f"{symbol}")
        print("=" * 90)

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df5 = fetch_candles(
            symbol,
            MAIN_RESOLUTION,
            start_ts,
            end_ts
        )

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        df15 = fetch_candles(
            symbol,
            CONTEXT_RESOLUTION,
            start_ts,
            end_ts
        )

        if df5.empty or df15.empty:

            print(
                f"DATA ERROR: {symbol}"
            )

            continue

        print(
            f"5M candles  : {len(df5)}"
        )

        print(
            f"15M candles : {len(df15)}"
        )

        # ----------------------------------------------------
        # CLOSED CANDLES ONLY
        # ----------------------------------------------------

        now = pd.Timestamp.now(
            tz="UTC"
        )

        df5 = df5[
            df5.index < now
        ]

        df15 = df15[
            df15.index < now
        ]

        # ----------------------------------------------------
        # INDICATORS
        # ----------------------------------------------------

        df5 = add_indicators_5m(
            df5
        )

        df15 = add_indicators_15m(
            df15
        )

        df = merge_context(
            df5,
            df15
        )

        # ----------------------------------------------------
        # SIGNALS
        # ----------------------------------------------------

        signals = generate_signals(
            df
        )

        print(
            f"Signals      : {len(signals)}"
        )

        if not signals:
            continue

        # ----------------------------------------------------
        # ALL EXIT MODELS
        # ----------------------------------------------------

        for model in EXIT_MODELS:

            trades = run_model(
                df,
                signals,
                model
            )

            stats = calculate_stats(
                trades
            )

            summary = {
                "symbol": symbol,
                "model": model,
                **stats
            }

            all_summary.append(
                summary
            )

            for trade in trades:

                trade["symbol"] = symbol

                all_trades.append(
                    trade
                )

            print(
                f"{model:7s} | "
                f"Trades={stats['trades']:4d} | "
                f"WR={stats['win_rate']:6.2f}% | "
                f"PF={stats['pf']:6.3f} | "
                f"Net={stats['net']:8.3f}% | "
                f"DD={stats['max_dd']:8.3f}%"
            )

    # ========================================================
    # SAVE RESULTS
    # ========================================================

    summary_df = pd.DataFrame(
        all_summary
    )

    trades_df = pd.DataFrame(
        all_trades
    )

    summary_file = (
        "reverse_tenkan_6coins_90d_summary.csv"
    )

    trades_file = (
        "reverse_tenkan_6coins_90d_trades.csv"
    )

    aggregate_file = (
        "reverse_tenkan_6coins_90d_aggregate.csv"
    )

    summary_df.to_csv(
        summary_file,
        index=False
    )

    trades_df.to_csv(
        trades_file,
        index=False
    )

    # ========================================================
    # AGGREGATE BY MODEL
    # ========================================================

    aggregate = []

    if not trades_df.empty:

        for model in EXIT_MODELS:

            subset = trades_df[
                trades_df["model"] == model
            ]

            if subset.empty:
                continue

            stats = calculate_stats(
                subset.to_dict(
                    "records"
                )
            )

            aggregate.append({
                "model": model,
                **stats
            })

    aggregate_df = pd.DataFrame(
        aggregate
    )

    aggregate_df.to_csv(
        aggregate_file,
        index=False
    )

    # ========================================================
    # FINAL REPORT
    # ========================================================

    print()
    print("=" * 90)
    print("90 DAY AGGREGATE")
    print("=" * 90)

    if not aggregate_df.empty:

        for _, row in aggregate_df.iterrows():

            print(
                f"{row['model']:7s} | "
                f"Trades={int(row['trades']):5d} | "
                f"WR={row['win_rate']:6.2f}% | "
                f"PF={row['pf']:6.3f} | "
                f"Net={row['net']:9.3f}% | "
                f"DD={row['max_dd']:9.3f}%"
            )

    print()
    print("=" * 90)
    print("FILES")
    print("=" * 90)

    print(summary_file)
    print(trades_file)
    print(aggregate_file)

    print()
    print("BACKTEST COMPLETE")


if __name__ == "__main__":
    main()
