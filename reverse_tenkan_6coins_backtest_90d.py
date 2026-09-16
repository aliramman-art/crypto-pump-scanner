# ============================================================
# REVERSE-TENKAN 6 COINS - 180 DAY BACKTEST
# ============================================================
#
# Kraken Futures
# Main TF : 5M
# Context : 15M
#
# STRATEGY LOGIC: SAME AS 90-DAY VERSION
#
# DATA:
# 180 DAYS
# 5M  -> 14 DAY CHUNKS
# 15M -> 30 DAY CHUNKS
#
# ============================================================

import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta
import time
import sys


# ============================================================
# CONFIG
# ============================================================

SYMBOLS = [
    "PF_AAVEUSD",
    "PF_SUIUSD",
    "PF_LINKUSD",
    "PF_DOTUSD",
    "PF_ARBUSD",
    "PF_ZECUSD",
]

DAYS = 180

TENKAN_PERIOD = 9

MIN_DISTANCE_PCT = 0.75
MIN_SCORE = 8

SL_PCT = 2.00

MAX_HOLD_BARS = 144

RSI_PERIOD = 14
RVOL_PERIOD = 20

AVG_BODY_PERIOD = 20
AVG_RANGE_PERIOD = 20

EMA_15M_PERIOD = 20

MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00

FEE_PER_SIDE_PCT = 0.06
ROUND_TRIP_FEE_PCT = 0.12

SL_FIRST = True


# ============================================================
# API SETTINGS
# ============================================================

# Kraken Futures officially supports:
# 1m, 5m, 15m, 30m, 1h, 4h, 12h, 1d, 1w

CHUNK_DAYS = {
    "5m": 14,
    "15m": 30,
}

API_RETRIES = 3
REQUEST_SLEEP = 0.25


BASE_URL = (
    "https://futures.kraken.com/"
    "api/charts/v1/trade"
)


# ============================================================
# EXIT MODELS
# ============================================================

EXIT_MODELS = {
    "TENKAN": "TENKAN",
    "1R": 1.0,
    "1.5R": 1.5,
    "2R": 2.0,
    "2.5R": 2.5,
    "3R": 3.0,
}


# ============================================================
# FETCH ONE CHUNK
# ============================================================

def fetch_kraken_chunk(
    symbol,
    resolution,
    start_ts,
    end_ts
):

    url = (
        f"{BASE_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "from": int(start_ts),
        "to": int(end_ts),
    }

    last_error = None

    for attempt in range(
        1,
        API_RETRIES + 1
    ):

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

            response.raise_for_status()

            payload = response.json()

            if not isinstance(
                payload,
                dict
            ):

                raise RuntimeError(
                    "Kraken response "
                    "is not a JSON object"
                )

            candles = payload.get(
                "candles",
                []
            )

            if not candles:

                return pd.DataFrame()

            rows = []

            for candle in candles:

                if not isinstance(
                    candle,
                    dict
                ):
                    continue

                rows.append({
                    "timestamp":
                        candle.get("time"),

                    "open":
                        candle.get("open"),

                    "high":
                        candle.get("high"),

                    "low":
                        candle.get("low"),

                    "close":
                        candle.get("close"),

                    "volume":
                        candle.get("volume"),
                })

            df = pd.DataFrame(
                rows
            )

            if df.empty:
                return df

            # Kraken returns candle time
            # in epoch milliseconds.
            df["timestamp"] = (
                pd.to_datetime(
                    df["timestamp"],
                    unit="ms",
                    utc=True,
                    errors="coerce"
                )
            )

            for column in [
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]:

                df[column] = pd.to_numeric(
                    df[column],
                    errors="coerce"
                )

            df = df.dropna()

            df = (
                df
                .sort_values(
                    "timestamp"
                )
                .drop_duplicates(
                    "timestamp"
                )
                .reset_index(
                    drop=True
                )
            )

            return df

        except Exception as error:

            last_error = error

            print(
                f"  API retry "
                f"{attempt}/{API_RETRIES} "
                f"{symbol} "
                f"{resolution}: "
                f"{error}"
            )

            if attempt < API_RETRIES:

                time.sleep(
                    attempt * 1.0
                )

    raise RuntimeError(
        f"API failed after "
        f"{API_RETRIES} attempts: "
        f"{symbol} "
        f"{resolution} | "
        f"{last_error}"
    )


# ============================================================
# FETCH FULL PERIOD IN CHUNKS
# ============================================================

def fetch_kraken(
    symbol,
    resolution,
    start_ts,
    end_ts
):

    chunk_days = CHUNK_DAYS[
        resolution
    ]

    chunk_seconds = (
        chunk_days *
        24 *
        60 *
        60
    )

    total_seconds = (
        end_ts -
        start_ts
    )

    total_chunks = int(
        np.ceil(
            total_seconds /
            chunk_seconds
        )
    )

    frames = []

    current_start = start_ts

    chunk_number = 0

    while current_start < end_ts:

        current_end = min(
            current_start +
            chunk_seconds,
            end_ts
        )

        chunk_number += 1

        print(
            f"  {resolution} "
            f"chunk "
            f"{chunk_number}/"
            f"{total_chunks}"
        )

        df_chunk = (
            fetch_kraken_chunk(
                symbol,
                resolution,
                current_start,
                current_end
            )
        )

        if not df_chunk.empty:

            frames.append(
                df_chunk
            )

        current_start = (
            current_end
        )

        time.sleep(
            REQUEST_SLEEP
        )

    if not frames:

        return pd.DataFrame()

    df = pd.concat(
        frames,
        ignore_index=True
    )

    df = (
        df
        .sort_values(
            "timestamp"
        )
        .drop_duplicates(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )

    return df


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14
):

    delta = series.diff()

    gain = delta.clip(
        lower=0
    )

    loss = -delta.clip(
        upper=0
    )

    avg_gain = (
        gain
        .rolling(period)
        .mean()
    )

    avg_loss = (
        loss
        .rolling(period)
        .mean()
    )

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    return (
        100 -
        (
            100 /
            (1 + rs)
        )
    )


# ============================================================
# PREPARE 5M
# ============================================================

def prepare_5m(df):

    df = df.copy()

    highest = (
        df["high"]
        .rolling(
            TENKAN_PERIOD
        )
        .max()
    )

    lowest = (
        df["low"]
        .rolling(
            TENKAN_PERIOD
        )
        .min()
    )

    df["tenkan"] = (
        highest +
        lowest
    ) / 2

    df["rsi"] = calculate_rsi(
        df["close"],
        RSI_PERIOD
    )

    df["avg_volume"] = (
        df["volume"]
        .rolling(
            RVOL_PERIOD
        )
        .mean()
    )

    df["rvol"] = (
        df["volume"] /
        df["avg_volume"]
    )

    df["body"] = (
        df["close"] -
        df["open"]
    ).abs()

    df["range"] = (
        df["high"] -
        df["low"]
    )

    df["avg_body"] = (
        df["body"]
        .rolling(
            AVG_BODY_PERIOD
        )
        .mean()
    )

    df["avg_range"] = (
        df["range"]
        .rolling(
            AVG_RANGE_PERIOD
        )
        .mean()
    )

    df["close_position"] = np.where(
        df["range"] > 0,
        (
            df["close"] -
            df["low"]
        ) /
        df["range"],
        0.5
    )

    df["distance_pct"] = (
        (
            df["close"] -
            df["tenkan"]
        )
        /
        df["tenkan"]
        *
        100
    )

    df["prev_distance_pct"] = (
        df["distance_pct"]
        .shift(1)
    )

    df["tenkan_slope"] = (
        df["tenkan"] -
        df["tenkan"].shift(1)
    )

    df["prev_tenkan_slope"] = (
        df["tenkan_slope"]
        .shift(1)
    )

    return df


# ============================================================
# PREPARE 15M
# ============================================================

def prepare_15m(df):

    df = df.copy()

    df["ema20"] = (
        df["close"]
        .ewm(
            span=EMA_15M_PERIOD,
            adjust=False
        )
        .mean()
    )

    return df


# ============================================================
# SIGNAL GENERATION
# ============================================================

def generate_signals(
    df5,
    df15
):

    df = df5.copy()

    context = df15[
        [
            "timestamp",
            "close",
            "ema20",
        ]
    ].copy()

    context = context.rename(
        columns={
            "close":
                "close_15m",

            "ema20":
                "ema20_15m",
        }
    )

    df = pd.merge_asof(
        df.sort_values(
            "timestamp"
        ),
        context.sort_values(
            "timestamp"
        ),
        on="timestamp",
        direction="backward"
    )

    signals = []

    for i in range(
        1,
        len(df)
    ):

        row = df.iloc[i]

        prev = df.iloc[i - 1]

        required = [
            "tenkan",
            "rsi",
            "rvol",
            "ema20_15m",
        ]

        if any(
            pd.isna(row[x])
            for x in required
        ):
            continue

        price = row["close"]

        tenkan = row["tenkan"]

        distance = (
            row["distance_pct"]
        )

        # ====================================================
        # DISTANCE
        # ====================================================

        long_distance = (
            distance <=
            -MIN_DISTANCE_PCT
        )

        short_distance = (
            distance >=
            MIN_DISTANCE_PCT
        )

        # ====================================================
        # DISTANCE CONTRACTING
        # ====================================================

        long_contracting = (
            distance >
            row["prev_distance_pct"]
        )

        short_contracting = (
            distance <
            row["prev_distance_pct"]
        )

        # ====================================================
        # TENKAN SLOPE
        # ====================================================

        long_slope = (
            row["tenkan_slope"] > 0
            and
            row["prev_tenkan_slope"] <= 0
        )

        short_slope = (
            row["tenkan_slope"] < 0
            and
            row["prev_tenkan_slope"] >= 0
        )

        # ====================================================
        # REVERSAL CANDLE
        # ====================================================

        long_candle = (
            row["close"] >
            row["open"]
            and
            row["close"] >
            prev["close"]
            and
            row["high"] >
            prev["high"]
            and
            row["close_position"] >=
            0.55
        )

        short_candle = (
            row["close"] <
            row["open"]
            and
            row["close"] <
            prev["close"]
            and
            row["low"] <
            prev["low"]
            and
            row["close_position"] <=
            0.45
        )

        # ====================================================
        # RSI REVERSAL
        # ====================================================

        long_rsi = (
            distance < 0
            and
            row["rsi"] < 35
            and
            row["rsi"] >
            prev["rsi"]
        )

        short_rsi = (
            distance > 0
            and
            row["rsi"] > 65
            and
            row["rsi"] <
            prev["rsi"]
        )

        # ====================================================
        # RVOL
        # ====================================================

        rvol_ok = (
            row["rvol"] >= 1.20
            and
            row["rvol"] <= 3.00
        )

        # ====================================================
        # NO EXPLOSION
        # ====================================================

        no_explosion = (
            row["body"]
            <=
            row["avg_body"] *
            MAX_BODY_MULTIPLIER
            and
            row["range"]
            <=
            row["avg_range"] *
            MAX_RANGE_MULTIPLIER
        )

        # ====================================================
        # 15M CONTEXT
        # ====================================================

        long_context = (
            row["close_15m"]
            >=
            row["ema20_15m"]
        )

        short_context = (
            row["close_15m"]
            <=
            row["ema20_15m"]
        )

        # ====================================================
        # LONG SCORE
        # ====================================================

        long_score = 0

        if long_distance:
            long_score += 2

        if long_contracting:
            long_score += 2

        if long_slope:
            long_score += 2

        if long_candle:
            long_score += 2

        if long_rsi:
            long_score += 2

        if rvol_ok:
            long_score += 1

        if no_explosion:
            long_score += 1

        if long_context:
            long_score += 2

        # ====================================================
        # SHORT SCORE
        # ====================================================

        short_score = 0

        if short_distance:
            short_score += 2

        if short_contracting:
            short_score += 2

        if short_slope:
            short_score += 2

        if short_candle:
            short_score += 2

        if short_rsi:
            short_score += 2

        if rvol_ok:
            short_score += 1

        if no_explosion:
            short_score += 1

        if short_context:
            short_score += 2

        # ====================================================
        # LONG
        # ====================================================

        if (
            long_distance
            and
            long_score >= MIN_SCORE
        ):

            entry = price

            sl = (
                entry *
                (
                    1 -
                    SL_PCT / 100
                )
            )

            tp = tenkan

            if tp > entry:

                signals.append({
                    "index": i,
                    "timestamp":
                        row["timestamp"],
                    "symbol": None,
                    "side": "LONG",
                    "entry": entry,
                    "sl": sl,
                    "tenkan": tenkan,
                    "score":
                        long_score,
                    "distance_pct":
                        distance,
                })

        # ====================================================
        # SHORT
        # ====================================================

        if (
            short_distance
            and
            short_score >= MIN_SCORE
        ):

            entry = price

            sl = (
                entry *
                (
                    1 +
                    SL_PCT / 100
                )
            )

            tp = tenkan

            if tp < entry:

                signals.append({
                    "index": i,
                    "timestamp":
                        row["timestamp"],
                    "symbol": None,
                    "side": "SHORT",
                    "entry": entry,
                    "sl": sl,
                    "tenkan": tenkan,
                    "score":
                        short_score,
                    "distance_pct":
                        distance,
                })

    return signals, df


# ============================================================
# TP
# ============================================================

def build_tp(
    signal,
    model
):

    entry = signal["entry"]

    sl = signal["sl"]

    side = signal["side"]

    tenkan = signal["tenkan"]

    if model == "TENKAN":

        return tenkan

    risk = abs(
        entry - sl
    )

    if side == "LONG":

        return (
            entry +
            risk *
            float(model)
        )

    return (
        entry -
        risk *
        float(model)
    )


# ============================================================
# TRADE SIMULATION
# ============================================================

def simulate_trade(
    df,
    signal,
    model
):

    entry_index = signal[
        "index"
    ]

    entry = signal[
        "entry"
    ]

    sl = signal[
        "sl"
    ]

    side = signal[
        "side"
    ]

    tp = build_tp(
        signal,
        model
    )

    last_index = min(
        entry_index +
        MAX_HOLD_BARS,
        len(df) - 1
    )

    exit_price = None
    exit_reason = None
    exit_index = None

    for j in range(
        entry_index + 1,
        last_index + 1
    ):

        row = df.iloc[j]

        high = row["high"]
        low = row["low"]

        if side == "LONG":

            sl_hit = (
                low <= sl
            )

            tp_hit = (
                high >= tp
            )

            if sl_hit and tp_hit:

                if SL_FIRST:

                    exit_price = sl
                    exit_reason = "SL"

                else:

                    exit_price = tp
                    exit_reason = "TP"

                exit_index = j
                break

            if sl_hit:

                exit_price = sl
                exit_reason = "SL"
                exit_index = j
                break

            if tp_hit:

                exit_price = tp
                exit_reason = "TP"
                exit_index = j
                break

        else:

            sl_hit = (
                high >= sl
            )

            tp_hit = (
                low <= tp
            )

            if sl_hit and tp_hit:

                if SL_FIRST:

                    exit_price = sl
                    exit_reason = "SL"

                else:

                    exit_price = tp
                    exit_reason = "TP"

                exit_index = j
                break

            if sl_hit:

                exit_price = sl
                exit_reason = "SL"
                exit_index = j
                break

            if tp_hit:

                exit_price = tp
                exit_reason = "TP"
                exit_index = j
                break

    if exit_price is None:

        exit_index = last_index

        exit_price = df.iloc[
            exit_index
        ]["close"]

        exit_reason = "TIMEOUT"

    if side == "LONG":

        gross_pnl_pct = (
            (
                exit_price -
                entry
            )
            /
            entry *
            100
        )

    else:

        gross_pnl_pct = (
            (
                entry -
                exit_price
            )
            /
            entry *
            100
        )

    net_pnl_pct = (
        gross_pnl_pct -
        ROUND_TRIP_FEE_PCT
    )

    return {
        "entry_time":
            signal["timestamp"],

        "exit_time":
            df.iloc[
                exit_index
            ]["timestamp"],

        "side":
            side,

        "entry":
            entry,

        "sl":
            sl,

        "tp":
            tp,

        "exit":
            exit_price,

        "reason":
            exit_reason,

        "gross_pnl_pct":
            gross_pnl_pct,

        "net_pnl_pct":
            net_pnl_pct,

        "win":
            net_pnl_pct > 0,

        "score":
            signal["score"],

        "distance_pct":
            signal[
                "distance_pct"
            ],
    }


# ============================================================
# PERFORMANCE
# ============================================================

def calculate_performance(
    trades
):

    if not trades:

        return {
            "Trades": 0,
            "Wins": 0,
            "Losses": 0,
            "Timeouts": 0,
            "WR": 0,
            "PF": 0,
            "Net": 0,
            "DD": 0,
        }

    df = pd.DataFrame(
        trades
    )

    wins = df[
        df["net_pnl_pct"] > 0
    ]

    losses = df[
        df["net_pnl_pct"] <= 0
    ]

    timeouts = df[
        df["reason"] ==
        "TIMEOUT"
    ]

    gross_profit = (
        wins["net_pnl_pct"].sum()
    )

    gross_loss = abs(
        losses["net_pnl_pct"].sum()
    )

    if gross_loss > 0:

        pf = (
            gross_profit /
            gross_loss
        )

    else:

        pf = np.inf

    wr = (
        len(wins) /
        len(df) *
        100
    )

    net = (
        df["net_pnl_pct"].sum()
    )

    equity = (
        df["net_pnl_pct"]
        .cumsum()
    )

    running_max = (
        equity.cummax()
    )

    drawdown = (
        running_max -
        equity
    )

    dd = drawdown.max()

    return {
        "Trades": len(df),
        "Wins": len(wins),
        "Losses": len(losses),
        "Timeouts": len(timeouts),
        "WR": wr,
        "PF": pf,
        "Net": net,
        "DD": dd,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 90)
    print(
        "REVERSE-TENKAN 6 COINS - "
        "180 DAY BACKTEST"
    )
    print("=" * 90)

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt -
        timedelta(
            days=DAYS
        )
    )

    start_ts = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )

    print(
        f"Start UTC : "
        f"{start_dt.isoformat()}"
    )

    print(
        f"End UTC   : "
        f"{end_dt.isoformat()}"
    )

    all_summary = []

    all_trades = []

    all_aggregate = []

    aggregate_trades = {
        model: []
        for model in EXIT_MODELS
    }

    failed_symbols = []

    # ========================================================
    # SYMBOLS
    # ========================================================

    for symbol in SYMBOLS:

        print()
        print("=" * 90)
        print(symbol)
        print("=" * 90)

        try:

            # ------------------------------------------------
            # 5M
            # ------------------------------------------------

            print(
                f"Fetching "
                f"{symbol} 5M..."
            )

            df5 = fetch_kraken(
                symbol,
                "5m",
                start_ts,
                end_ts
            )

            # ------------------------------------------------
            # 15M
            # ------------------------------------------------

            print(
                f"Fetching "
                f"{symbol} 15M..."
            )

            df15 = fetch_kraken(
                symbol,
                "15m",
                start_ts,
                end_ts
            )

            if df5.empty:

                raise RuntimeError(
                    f"No 5M data "
                    f"returned for "
                    f"{symbol}"
                )

            if df15.empty:

                raise RuntimeError(
                    f"No 15M data "
                    f"returned for "
                    f"{symbol}"
                )

            # ------------------------------------------------
            # PREPARE
            # ------------------------------------------------

            df5 = prepare_5m(
                df5
            )

            df15 = prepare_15m(
                df15
            )

            # ------------------------------------------------
            # SIGNALS
            # ------------------------------------------------

            signals, df = (
                generate_signals(
                    df5,
                    df15
                )
            )

            for signal in signals:

                signal["symbol"] = (
                    symbol
                )

            print(
                f"5M candles  : "
                f"{len(df5)}"
            )

            print(
                f"15M candles : "
                f"{len(df15)}"
            )

            print(
                f"Signals      : "
                f"{len(signals)}"
            )

            # ------------------------------------------------
            # EXIT MODELS
            # ------------------------------------------------

            for (
                model_name,
                model_value
            ) in EXIT_MODELS.items():

                model_trades = []

                for signal in signals:

                    trade = (
                        simulate_trade(
                            df,
                            signal,
                            model_value
                        )
                    )

                    trade["symbol"] = (
                        symbol
                    )

                    trade["model"] = (
                        model_name
                    )

                    model_trades.append(
                        trade
                    )

                    aggregate_trades[
                        model_name
                    ].append(
                        trade
                    )

                perf = (
                    calculate_performance(
                        model_trades
                    )
                )

                print(
                    f"{model_name:<7} | "
                    f"Trades="
                    f"{perf['Trades']:>4} | "
                    f"WR="
                    f"{perf['WR']:>6.2f}% | "
                    f"PF="
                    f"{perf['PF']:>6.3f} | "
                    f"Net="
                    f"{perf['Net']:>8.3f}% | "
                    f"DD="
                    f"{perf['DD']:>8.3f}%"
                )

                all_summary.append({
                    "Symbol":
                        symbol,

                    "Model":
                        model_name,

                    **perf
                })

                all_trades.extend(
                    model_trades
                )

        except Exception as error:

            print()
            print(
                f"FAILED: {symbol}"
            )

            print(
                f"Reason: {error}"
            )

            failed_symbols.append(
                symbol
            )

    # ========================================================
    # FAIL IF ANY SYMBOL FAILED
    # ========================================================

    if failed_symbols:

        print()
        print("=" * 90)
        print(
            "BACKTEST FAILED"
        )
        print("=" * 90)

        print(
            "Failed symbols:"
        )

        for symbol in failed_symbols:

            print(
                f"  - {symbol}"
            )

        print()
        print(
            "No partial result "
            "will be accepted."
        )

        sys.exit(1)

    # ========================================================
    # AGGREGATE
    # ========================================================

    print()
    print("=" * 90)
    print(
        "AGGREGATE - ALL 6 COINS"
    )
    print("=" * 90)

    for model_name in EXIT_MODELS:

        trades = (
            aggregate_trades[
                model_name
            ]
        )

        perf = (
            calculate_performance(
                trades
            )
        )

        print(
            f"{model_name:<7} | "
            f"Trades="
            f"{perf['Trades']:>4} | "
            f"WR="
            f"{perf['WR']:>6.2f}% | "
            f"PF="
            f"{perf['PF']:>6.3f} | "
            f"Net="
            f"{perf['Net']:>8.3f}% | "
            f"DD="
            f"{perf['DD']:>8.3f}%"
        )

        all_aggregate.append({
            "Model":
                model_name,

            **perf
        })

    # ========================================================
    # SAVE
    # ========================================================

    summary_df = pd.DataFrame(
        all_summary
    )

    trades_df = pd.DataFrame(
        all_trades
    )

    aggregate_df = pd.DataFrame(
        all_aggregate
    )

    summary_df.to_csv(
        "reverse_tenkan_6coins_180d_summary.csv",
        index=False
    )

    trades_df.to_csv(
        "reverse_tenkan_6coins_180d_trades.csv",
        index=False
    )

    aggregate_df.to_csv(
        "reverse_tenkan_6coins_180d_aggregate.csv",
        index=False
    )

    print()
    print("=" * 90)
    print(
        "RESULT FILES CREATED"
    )
    print("=" * 90)

    print(
        "reverse_tenkan_6coins_180d_summary.csv"
    )

    print(
        "reverse_tenkan_6coins_180d_trades.csv"
    )

    print(
        "reverse_tenkan_6coins_180d_aggregate.csv"
    )

    print()
    print(
        "BACKTEST COMPLETED SUCCESSFULLY"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
