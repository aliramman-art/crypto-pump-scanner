# ============================================================
# REVERSE-TENKAN 21 COINS
# 30-DAY HISTORICAL BACKTEST
# ============================================================
#
# Strategy:
# DOGE REVERSE-TENKAN SIGNAL BOT v1.5
#
# IMPORTANT:
# Strategy logic is kept unchanged.
# This file only adds a historical replay/backtest engine.
#
# DATA:
# Kraken Futures trade candles
#
# TIMEFRAMES:
# 5M  = signal
# 15M = context
#
# ============================================================

import time
import math
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, timezone


# ============================================================
# CONFIG
# ============================================================

SYMBOLS = [
    "PF_XBTUSD",       # BTC
    "PF_ETHUSD",       # ETH
    "PF_SOLUSD",       # SOL
    "PF_XRPUSD",       # XRP
    "PF_DOGEUSD",      # DOGE
    "PF_ADAUSD",       # ADA
    "PF_AVAXUSD",      # AVAX
    "PF_LINKUSD",      # LINK
    "PF_DOTUSD",       # DOT
    "PF_LTCUSD",       # LTC
    "PF_BCHUSD",       # BCH
    "PF_UNIUSD",       # UNI
    "PF_AAVEUSD",      # AAVE
    "PF_SUIUSD",       # SUI
    "PF_HYPEUSD",      # HYPE
    "PF_NEARUSD",      # NEAR
    "PF_ATOMUSD",      # ATOM
    "PF_FILUSD",       # FIL
    "PF_ARBUSD",       # ARB
    "PF_OPUSD",        # OP
    "PF_ZECUSD",       # ZEC
]


# ============================================================
# ORIGINAL STRATEGY SETTINGS
# ============================================================

TENKAN_PERIOD = 9

MIN_DISTANCE_PCT = 0.75

MIN_SCORE = 8

SL_PCT = 2.00

MAX_HOLD_BARS = 144

MAX_HOLD_HOURS = 12

RSI_PERIOD = 14

RVOL_PERIOD = 20
RVOL_MIN = 1.20
RVOL_MAX = 3.00

AVG_BODY_PERIOD = 20
AVG_RANGE_PERIOD = 20

EMA_15M_PERIOD = 20

MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00

# LBank taker fee
FEE_PER_SIDE_PCT = 0.06

ROUND_TRIP_FEE_PCT = FEE_PER_SIDE_PCT * 2.0


# ============================================================
# BACKTEST SETTINGS
# ============================================================

BACKTEST_DAYS = 30

SIGNAL_TIMEFRAME = "5m"
CONTEXT_TIMEFRAME = "15m"

API_BASE = "https://futures.kraken.com/api/charts/v1"

REQUEST_TIMEOUT = 30

REQUEST_SLEEP = 0.15

# If both SL and TP are touched inside the same 5M candle,
# we cannot know the true intrabar order from OHLC alone.
# Conservative deterministic rule:
SL_FIRST = True

OUTPUT_CSV = "reverse_tenkan_21coins_30d_results.csv"

TRADES_CSV = "reverse_tenkan_21coins_30d_trades.csv"


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Reverse-Tenkan-21Coins-Backtest/1.5"
})


# ============================================================
# SYMBOL NAME
# ============================================================

def symbol_name(symbol):

    return (
        symbol
        .replace("PF_", "")
        .replace("USD", "")
        .replace("XBT", "BTC")
    )


# ============================================================
# FETCH KRAKEN FUTURES CANDLES
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    start_ts,
    end_ts
):

    url = (
        f"{API_BASE}/trade/"
        f"{symbol}/"
        f"{resolution}"
    )

    all_candles = []

    current_from = int(start_ts)

    # Kraken Futures chart endpoint can limit candle count.
    # We therefore fetch in windows.
    if resolution == "5m":

        window_seconds = (
            2000 *
            5 *
            60
        )

    elif resolution == "15m":

        window_seconds = (
            2000 *
            15 *
            60
        )

    else:

        window_seconds = (
            2000 *
            60
        )

    while current_from < end_ts:

        current_to = min(
            current_from + window_seconds,
            end_ts
        )

        params = {
            "from": current_from,
            "to": current_to,
            "count": 2000,
        }

        try:

            response = SESSION.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            response.raise_for_status()

            payload = response.json()

        except Exception as exc:

            print(
                f"ERROR fetching "
                f"{symbol} {resolution}: {exc}"
            )

            break

        candles = payload.get(
            "candles",
            []
        )

        if not candles:

            break

        all_candles.extend(
            candles
        )

        last_ms = int(
            candles[-1]["time"]
        )

        last_sec = (
            last_ms // 1000
        )

        next_from = (
            last_sec + 1
        )

        if next_from <= current_from:

            break

        current_from = next_from

        time.sleep(
            REQUEST_SLEEP
        )

        if not payload.get(
            "more_candles",
            False
        ):

            if current_from >= end_ts:

                break

    if not all_candles:

        return pd.DataFrame()

    df = pd.DataFrame(
        all_candles
    )

    required = [
        "time",
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    missing = [
        c
        for c in required
        if c not in df.columns
    ]

    if missing:

        print(
            f"{symbol} {resolution}: "
            f"missing columns {missing}"
        )

        return pd.DataFrame()

    df = df[
        required
    ].copy()

    df["time"] = pd.to_datetime(
        df["time"],
        unit="ms",
        utc=True
    )

    for col in [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]:

        df[col] = pd.to_numeric(
            df[col],
            errors="coerce"
        )

    df = df.dropna()

    df = (
        df
        .drop_duplicates(
            subset=["time"]
        )
        .sort_values("time")
        .reset_index(drop=True)
    )

    return df


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    high_n = (
        df["high"]
        .rolling(
            TENKAN_PERIOD
        )
        .max()
    )

    low_n = (
        df["low"]
        .rolling(
            TENKAN_PERIOD
        )
        .min()
    )

    return (
        high_n +
        low_n
    ) / 2.0


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
        .ewm(
            alpha=1 / period,
            adjust=False
        )
        .mean()
    )

    avg_loss = (
        loss
        .ewm(
            alpha=1 / period,
            adjust=False
        )
        .mean()
    )

    rs = (
        avg_gain /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = (
        100 -
        (
            100 /
            (1 + rs)
        )
    )

    return rsi


# ============================================================
# ADD 5M INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()

    df["tenkan"] = (
        calculate_tenkan(df)
    )

    df["rsi"] = (
        calculate_rsi(
            df["close"],
            RSI_PERIOD
        )
    )

    df["prev_rsi"] = (
        df["rsi"].shift(1)
    )

    df["prev_close"] = (
        df["close"].shift(1)
    )

    df["prev_high"] = (
        df["high"].shift(1)
    )

    df["prev_low"] = (
        df["low"].shift(1)
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

    df["volume_ma"] = (
        df["volume"]
        .rolling(
            RVOL_PERIOD
        )
        .mean()
    )

    df["rvol"] = (
        df["volume"] /
        df["volume_ma"].replace(
            0,
            np.nan
        )
    )

    # Distance from Tenkan
    df["distance_pct"] = (
        (
            df["close"] -
            df["tenkan"]
        )
        /
        df["tenkan"]
        * 100
    )

    df["prev_distance_pct"] = (
        df["distance_pct"].shift(1)
    )

    # Candle close position
    df["close_position"] = np.where(
        df["range"] > 0,
        (
            df["close"] -
            df["low"]
        )
        /
        df["range"],
        0.5
    )

    # Tenkan slope
    df["tenkan_prev"] = (
        df["tenkan"].shift(1)
    )

    df["tenkan_prev2"] = (
        df["tenkan"].shift(2)
    )

    df["tenkan_slope"] = (
        df["tenkan"] -
        df["tenkan_prev"]
    )

    df["tenkan_slope_prev"] = (
        df["tenkan_prev"] -
        df["tenkan_prev2"]
    )

    return df


# ============================================================
# 15M CONTEXT
# ============================================================

def build_15m_context(df15):

    df15 = df15.copy()

    df15["ema20"] = (
        df15["close"]
        .ewm(
            span=EMA_15M_PERIOD,
            adjust=False
        )
        .mean()
    )

    return df15[
        [
            "time",
            "close",
            "ema20",
        ]
    ].rename(
        columns={
            "close": "close_15m",
            "ema20": "ema20_15m",
        }
    )


# ============================================================
# MERGE 15M CONTEXT
# ============================================================

def merge_context(
    df5,
    context15
):

    left = df5.copy()

    right = context15.copy()

    left["context_time"] = (
        left["time"] +
        pd.Timedelta(
            minutes=5
        )
    )

    right["context_time"] = (
        right["time"] +
        pd.Timedelta(
            minutes=15
        )
    )

    right = right.sort_values(
        "context_time"
    )

    left = left.sort_values(
        "context_time"
    )

    merged = pd.merge_asof(
        left,
        right[
            [
                "context_time",
                "close_15m",
                "ema20_15m",
            ]
        ],
        on="context_time",
        direction="backward"
    )

    return merged.drop(
        columns=[
            "context_time"
        ]
    )


# ============================================================
# REVERSAL CANDLE
# ============================================================

def long_reversal_candle(row):

    return (
        row["close"] >
        row["open"]
        and
        row["close"] >
        row["prev_close"]
        and
        row["high"] >
        row["prev_high"]
        and
        row["close_position"] >= 0.55
    )


def short_reversal_candle(row):

    return (
        row["close"] <
        row["open"]
        and
        row["close"] <
        row["prev_close"]
        and
        row["low"] <
        row["prev_low"]
        and
        row["close_position"] <= 0.45
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    row,
    side
):

    score = 0

    distance = (
        row["distance_pct"]
    )

    prev_distance = (
        row["prev_distance_pct"]
    )

    # --------------------------------------------------------
    # 1. DISTANCE
    # --------------------------------------------------------

    if side == "LONG":

        if (
            distance <=
            -MIN_DISTANCE_PCT
        ):

            score += 2

    else:

        if (
            distance >=
            MIN_DISTANCE_PCT
        ):

            score += 2

    # --------------------------------------------------------
    # 2. DISTANCE CONTRACTING
    # --------------------------------------------------------

    if side == "LONG":

        if (
            distance < 0
            and
            abs(distance) <
            abs(prev_distance)
        ):

            score += 2

    else:

        if (
            distance > 0
            and
            abs(distance) <
            abs(prev_distance)
        ):

            score += 2

    # --------------------------------------------------------
    # 3. TENKAN SLOPE REVERSAL
    # --------------------------------------------------------

    if side == "LONG":

        if (
            row["tenkan_slope"] > 0
            and
            row["tenkan_slope_prev"] <= 0
        ):

            score += 2

    else:

        if (
            row["tenkan_slope"] < 0
            and
            row["tenkan_slope_prev"] >= 0
        ):

            score += 2

    # --------------------------------------------------------
    # 4. REVERSAL CANDLE
    # --------------------------------------------------------

    if side == "LONG":

        if long_reversal_candle(row):

            score += 2

    else:

        if short_reversal_candle(row):

            score += 2

    # --------------------------------------------------------
    # 5. RSI REVERSAL
    # --------------------------------------------------------

    if side == "LONG":

        if (
            distance < 0
            and
            row["rsi"] < 35
            and
            row["rsi"] >
            row["prev_rsi"]
        ):

            score += 2

    else:

        if (
            distance > 0
            and
            row["rsi"] > 65
            and
            row["rsi"] <
            row["prev_rsi"]
        ):

            score += 2

    # --------------------------------------------------------
    # 6. RVOL
    # --------------------------------------------------------

    if (
        row["rvol"] >= RVOL_MIN
        and
        row["rvol"] <= RVOL_MAX
    ):

        score += 1

    # --------------------------------------------------------
    # 7. NO EXPLOSION
    # --------------------------------------------------------

    body_ok = (
        row["body"] <=
        row["avg_body"] *
        MAX_BODY_MULTIPLIER
    )

    range_ok = (
        row["range"] <=
        row["avg_range"] *
        MAX_RANGE_MULTIPLIER
    )

    if (
        body_ok and
        range_ok
    ):

        score += 1

    # --------------------------------------------------------
    # 8. 15M EMA CONTEXT
    # --------------------------------------------------------

    if side == "LONG":

        if (
            row["close_15m"] >=
            row["ema20_15m"]
        ):

            score += 2

    else:

        if (
            row["close_15m"] <=
            row["ema20_15m"]
        ):

            score += 2

    return score


# ============================================================
# SIGNAL
# ============================================================

def get_signal(row):

    if pd.isna(
        row["tenkan"]
    ):

        return None, 0

    if pd.isna(
        row["distance_pct"]
    ):

        return None, 0

    if pd.isna(
        row["prev_distance_pct"]
    ):

        return None, 0

    if pd.isna(
        row["rsi"]
    ):

        return None, 0

    if pd.isna(
        row["prev_rsi"]
    ):

        return None, 0

    if pd.isna(
        row["rvol"]
    ):

        return None, 0

    if pd.isna(
        row["avg_body"]
    ):

        return None, 0

    if pd.isna(
        row["avg_range"]
    ):

        return None, 0

    if pd.isna(
        row["close_15m"]
    ):

        return None, 0

    if pd.isna(
        row["ema20_15m"]
    ):

        return None, 0

    distance = (
        row["distance_pct"]
    )

    # ========================================================
    # LONG
    # ========================================================

    if (
        distance <=
        -MIN_DISTANCE_PCT
    ):

        score = calculate_score(
            row,
            "LONG"
        )

        if score >= MIN_SCORE:

            tp = row["tenkan"]

            if tp > row["close"]:

                return "LONG", score

    # ========================================================
    # SHORT
    # ========================================================

    if (
        distance >=
        MIN_DISTANCE_PCT
    ):

        score = calculate_score(
            row,
            "SHORT"
        )

        if score >= MIN_SCORE:

            tp = row["tenkan"]

            if tp < row["close"]:

                return "SHORT", score

    return None, 0


# ============================================================
# TRADE RESULT
# ============================================================

def calculate_trade_pnl(
    side,
    entry,
    exit_price
):

    if side == "LONG":

        gross = (
            (
                (
                    exit_price -
                    entry
                )
                /
                entry
            )
            * 100
        )

    else:

        gross = (
            (
                (
                    entry -
                    exit_price
                )
                /
                entry
            )
            * 100
        )

    net = (
        gross -
        ROUND_TRIP_FEE_PCT
    )

    return gross, net


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    df
):

    trades = []

    active_trade = None

    total_signals = 0

    wins = 0
    losses = 0
    timeouts = 0

    net_pnl = 0.0

    equity = 0.0
    peak_equity = 0.0
    max_drawdown = 0.0

    # --------------------------------------------------------
    # Need enough warmup data for indicators.
    # --------------------------------------------------------

    for i in range(
        len(df)
    ):

        row = df.iloc[i]

        current_time = (
            row["time"]
        )

        # ====================================================
        # MANAGE OPEN TRADE
        # ====================================================

        if active_trade is not None:

            side = active_trade[
                "side"
            ]

            entry = active_trade[
                "entry"
            ]

            sl = active_trade[
                "sl"
            ]

            tp = active_trade[
                "tp"
            ]

            entry_index = (
                active_trade[
                    "entry_index"
                ]
            )

            age_bars = (
                i -
                entry_index
            )

            exit_reason = None

            exit_price = None

            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                sl_hit = (
                    row["low"] <= sl
                )

                tp_hit = (
                    row["high"] >= tp
                )

                if (
                    sl_hit and
                    tp_hit
                ):

                    if SL_FIRST:

                        exit_price = sl

                        exit_reason = "SL"

                    else:

                        exit_price = tp

                        exit_reason = "TP"

                elif sl_hit:

                    exit_price = sl

                    exit_reason = "SL"

                elif tp_hit:

                    exit_price = tp

                    exit_reason = "TP"

            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                sl_hit = (
                    row["high"] >= sl
                )

                tp_hit = (
                    row["low"] <= tp
                )

                if (
                    sl_hit and
                    tp_hit
                ):

                    if SL_FIRST:

                        exit_price = sl

                        exit_reason = "SL"

                    else:

                        exit_price = tp

                        exit_reason = "TP"

                elif sl_hit:

                    exit_price = sl

                    exit_reason = "SL"

                elif tp_hit:

                    exit_price = tp

                    exit_reason = "TP"

            # ------------------------------------------------
            # TIMEOUT
            # ------------------------------------------------

            if (
                exit_reason is None
                and
                age_bars >=
                MAX_HOLD_BARS
            ):

                exit_price = (
                    row["close"]
                )

                exit_reason = "TIMEOUT"

            # ------------------------------------------------
            # CLOSE TRADE
            # ------------------------------------------------

            if (
                exit_reason is not None
            ):

                (
                    gross_pnl,
                    net_trade_pnl
                ) = calculate_trade_pnl(
                    side,
                    entry,
                    exit_price
                )

                if net_trade_pnl > 0:

                    wins += 1

                else:

                    losses += 1

                if (
                    exit_reason ==
                    "TIMEOUT"
                ):

                    timeouts += 1

                net_pnl += (
                    net_trade_pnl
                )

                equity += (
                    net_trade_pnl
                )

                if equity > peak_equity:

                    peak_equity = (
                        equity
                    )

                drawdown = (
                    peak_equity -
                    equity
                )

                if (
                    drawdown >
                    max_drawdown
                ):

                    max_drawdown = (
                        drawdown
                    )

                trades.append({

                    "symbol":
                        symbol_name(
                            symbol
                        ),

                    "symbol_raw":
                        symbol,

                    "side":
                        side,

                    "entry_time":
                        active_trade[
                            "entry_time"
                        ],

                    "exit_time":
                        current_time,

                    "entry":
                        entry,

                    "exit":
                        exit_price,

                    "sl":
                        sl,

                    "tp":
                        tp,

                    "gross_pnl_pct":
                        gross_pnl,

                    "net_pnl_pct":
                        net_trade_pnl,

                    "reason":
                        exit_reason,

                    "score":
                        active_trade[
                            "score"
                        ],

                    "hold_bars":
                        age_bars,

                    "hold_hours":
                        (
                            age_bars *
                            5 /
                            60
                        ),
                })

                active_trade = None

                # Important:
                # After closing a trade on this candle,
                # do not open another trade on the same candle.
                continue

        # ====================================================
        # FIND NEW SIGNAL
        # ====================================================

        if active_trade is None:

            side, score = (
                get_signal(row)
            )

            if side is None:

                continue

            entry = float(
                row["close"]
            )

            tp = float(
                row["tenkan"]
            )

            # ------------------------------------------------
            # SL
            # ------------------------------------------------

            if side == "LONG":

                sl = (
                    entry *
                    (
                        1 -
                        SL_PCT / 100
                    )
                )

                # TP must be above entry
                if tp <= entry:

                    continue

            else:

                sl = (
                    entry *
                    (
                        1 +
                        SL_PCT / 100
                    )
                )

                # TP must be below entry
                if tp >= entry:

                    continue

            total_signals += 1

            active_trade = {

                "side":
                    side,

                "entry":
                    entry,

                "sl":
                    sl,

                "tp":
                    tp,

                "entry_time":
                    current_time,

                "entry_index":
                    i,

                "score":
                    score,
            }

    # ========================================================
    # CLOSE REMAINING OPEN TRADE AT END OF BACKTEST
    # ========================================================

    if active_trade is not None:

        last_row = df.iloc[-1]

        exit_price = float(
            last_row["close"]
        )

        (
            gross_pnl,
            net_trade_pnl
        ) = calculate_trade_pnl(
            active_trade["side"],
            active_trade["entry"],
            exit_price
        )

        if net_trade_pnl > 0:

            wins += 1

        else:

            losses += 1

        net_pnl += (
            net_trade_pnl
        )

        equity += (
            net_trade_pnl
        )

        if equity > peak_equity:

            peak_equity = equity

        drawdown = (
            peak_equity -
            equity
        )

        if drawdown > max_drawdown:

            max_drawdown = (
                drawdown
            )

        age_bars = (
            len(df) -
            1 -
            active_trade[
                "entry_index"
            ]
        )

        trades.append({

            "symbol":
                symbol_name(
                    symbol
                ),

            "symbol_raw":
                symbol,

            "side":
                active_trade[
                    "side"
                ],

            "entry_time":
                active_trade[
                    "entry_time"
                ],

            "exit_time":
                last_row["time"],

            "entry":
                active_trade[
                    "entry"
                ],

            "exit":
                exit_price,

            "sl":
                active_trade[
                    "sl"
                ],

            "tp":
                active_trade[
                    "tp"
                ],

            "gross_pnl_pct":
                gross_pnl,

            "net_pnl_pct":
                net_trade_pnl,

            "reason":
                "END_OF_TEST",

            "score":
                active_trade[
                    "score"
                ],

            "hold_bars":
                age_bars,

            "hold_hours":
                (
                    age_bars *
                    5 /
                    60
                ),
        })

    # ========================================================
    # STATISTICS
    # ========================================================

    closed_trades = len(
        trades
    )

    if closed_trades > 0:

        win_rate = (
            wins /
            closed_trades
            * 100
        )

    else:

        win_rate = 0.0

    winning_pnl = sum(

        t["net_pnl_pct"]

        for t in trades

        if t["net_pnl_pct"] > 0
    )

    losing_pnl = abs(
        sum(

            t["net_pnl_pct"]

            for t in trades

            if t["net_pnl_pct"] < 0
        )
    )

    if losing_pnl > 0:

        profit_factor = (
            winning_pnl /
            losing_pnl
        )

    elif winning_pnl > 0:

        profit_factor = (
            float("inf")
        )

    else:

        profit_factor = 0.0

    return {

        "symbol":
            symbol_name(symbol),

        "signals":
            total_signals,

        "wins":
            wins,

        "losses":
            losses,

        "timeouts":
            timeouts,

        "win_rate":
            win_rate,

        "profit_factor":
            profit_factor,

        "net_pnl":
            net_pnl,

        "max_drawdown":
            max_drawdown,

        "trades":
            trades,
    }


# ============================================================
# PRINT RESULT
# ============================================================

def print_symbol_result(
    result
):

    pf = result[
        "profit_factor"
    ]

    if math.isinf(pf):

        pf_text = "INF"

    else:

        pf_text = (
            f"{pf:.3f}"
        )

    print(

        f"{result['symbol']:>6} | "

        f"Signals "
        f"{result['signals']:>4} | "

        f"W "
        f"{result['wins']:>4} | "

        f"L "
        f"{result['losses']:>4} | "

        f"TO "
        f"{result['timeouts']:>3} | "

        f"WR "
        f"{result['win_rate']:>6.2f}% | "

        f"PF "
        f"{pf_text:>7} | "

        f"Net "
        f"{result['net_pnl']:>8.3f}% | "

        f"DD "
        f"{result['max_drawdown']:>8.3f}%"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 90)

    print(
        "REVERSE-TENKAN 21 COINS"
    )

    print(
        "30-DAY HISTORICAL BACKTEST"
    )

    print("=" * 90)

    print()

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt -
        timedelta(
            days=BACKTEST_DAYS
        )
    )

    print(
        f"Start: "
        f"{start_dt.isoformat()}"
    )

    print(
        f"End:   "
        f"{end_dt.isoformat()}"
    )

    print()

    print(
        "Strategy: "
        "Reverse-Tenkan v1.5"
    )

    print(
        f"Minimum distance: "
        f"{MIN_DISTANCE_PCT:.2f}%"
    )

    print(
        f"Minimum score: "
        f"{MIN_SCORE}/14"
    )

    print(
        f"SL: "
        f"{SL_PCT:.2f}%"
    )

    print(
        "TP: "
        "Signal candle Tenkan"
    )

    print(
        f"Round-trip fee: "
        f"{ROUND_TRIP_FEE_PCT:.2f}%"
    )

    print(
        f"Max hold: "
        f"{MAX_HOLD_HOURS} hours"
    )

    print(
        "Same-candle SL/TP rule: "
        f"{'SL FIRST' if SL_FIRST else 'TP FIRST'}"
    )

    print()

    all_results = []

    all_trades = []

    # ========================================================
    # PROCESS EACH COIN
    # ========================================================

    for number, symbol in enumerate(
        SYMBOLS,
        start=1
    ):

        name = symbol_name(
            symbol
        )

        print("=" * 90)

        print(
            f"[{number}/{len(SYMBOLS)}] "
            f"BACKTESTING {name}"
        )

        print("=" * 90)

        start_ts = int(
            start_dt.timestamp()
        )

        end_ts = int(
            end_dt.timestamp()
        )

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        df5 = fetch_kraken_candles(
            symbol,
            "5m",
            start_ts,
            end_ts
        )

        if df5.empty:

            print(
                f"{name}: NO 5M DATA"
            )

            continue

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        df15 = fetch_kraken_candles(
            symbol,
            "15m",
            start_ts,
            end_ts
        )

        if df15.empty:

            print(
                f"{name}: NO 15M DATA"
            )

            continue

        print(
            f"5M candles:  "
            f"{len(df5)}"
        )

        print(
            f"15M candles: "
            f"{len(df15)}"
        )

        # ----------------------------------------------------
        # INDICATORS
        # ----------------------------------------------------

        df5 = add_indicators(
            df5
        )

        context15 = (
            build_15m_context(
                df15
            )
        )

        df = merge_context(
            df5,
            context15
        )

        # ----------------------------------------------------
        # BACKTEST
        # ----------------------------------------------------

        result = backtest_symbol(
            symbol,
            df
        )

        all_results.append(
            result
        )

        all_trades.extend(
            result["trades"]
        )

        print()

        print_symbol_result(
            result
        )

        print()

    # ========================================================
    # COMBINED RESULTS
    # ========================================================

    print()

    print("=" * 110)

    print(
        "21-COIN COMPARISON"
    )

    print("=" * 110)

    print()

    print(
        "COIN   | SIGNALS | WINS | LOSS | TIMEOUT | "
        "WIN RATE | PROFIT FACTOR | NET PNL | MAX DD"
    )

    print("-" * 110)

    for result in all_results:

        pf = result[
            "profit_factor"
        ]

        if math.isinf(pf):

            pf_text = "INF"

        else:

            pf_text = (
                f"{pf:.3f}"
            )

        print(

            f"{result['symbol']:>6} | "

            f"{result['signals']:>7} | "

            f"{result['wins']:>4} | "

            f"{result['losses']:>4} | "

            f"{result['timeouts']:>7} | "

            f"{result['win_rate']:>8.2f}% | "

            f"{pf_text:>13} | "

            f"{result['net_pnl']:>7.3f}% | "

            f"{result['max_drawdown']:>7.3f}%"
        )

    # ========================================================
    # PORTFOLIO-LIKE AGGREGATE
    # ========================================================

    total_signals = sum(

        r["signals"]

        for r in all_results
    )

    total_wins = sum(

        r["wins"]

        for r in all_results
    )

    total_losses = sum(

        r["losses"]

        for r in all_results
    )

    total_timeouts = sum(

        r["timeouts"]

        for r in all_results
    )

    total_net_pnl = sum(

        r["net_pnl"]

        for r in all_results
    )

    total_closed = (
        total_wins +
        total_losses
    )

    if total_closed > 0:

        total_wr = (
            total_wins /
            total_closed
            * 100
        )

    else:

        total_wr = 0.0

    total_winning_pnl = sum(

        t["net_pnl_pct"]

        for t in all_trades

        if t["net_pnl_pct"] > 0
    )

    total_losing_pnl = abs(
        sum(

            t["net_pnl_pct"]

            for t in all_trades

            if t["net_pnl_pct"] < 0
        )
    )

    if total_losing_pnl > 0:

        total_pf = (
            total_winning_pnl /
            total_losing_pnl
        )

    elif total_winning_pnl > 0:

        total_pf = (
            float("inf")
        )

    else:

        total_pf = 0.0

    # ========================================================
    # PRINT AGGREGATE
    # ========================================================

    print()

    print("=" * 110)

    print(
        "AGGREGATE 21-COIN RESULT"
    )

    print("=" * 110)

    print(
        f"Total signals : "
        f"{total_signals}"
    )

    print(
        f"Wins          : "
        f"{total_wins}"
    )

    print(
        f"Losses        : "
        f"{total_losses}"
    )

    print(
        f"Timeouts      : "
        f"{total_timeouts}"
    )

    print(
        f"Win rate      : "
        f"{total_wr:.2f}%"
    )

    if math.isinf(
        total_pf
    ):

        print(
            "Profit factor : INF"
        )

    else:

        print(
            f"Profit factor : "
            f"{total_pf:.3f}"
        )

    print(
        f"Net PnL       : "
        f"{total_net_pnl:.3f}%"
    )

    print()

    # ========================================================
    # SAVE SUMMARY CSV
    # ========================================================

    summary_rows = []

    for result in all_results:

        pf = result[
            "profit_factor"
        ]

        if math.isinf(pf):

            pf = np.inf

        summary_rows.append({

            "coin":
                result["symbol"],

            "signals":
                result["signals"],

            "wins":
                result["wins"],

            "losses":
                result["losses"],

            "timeouts":
                result["timeouts"],

            "win_rate_pct":
                result["win_rate"],

            "profit_factor":
                pf,

            "net_pnl_pct":
                result["net_pnl"],

            "max_drawdown_pct":
                result["max_drawdown"],
        })

    summary_df = pd.DataFrame(
        summary_rows
    )

    summary_df.to_csv(
        OUTPUT_CSV,
        index=False
    )

    # ========================================================
    # SAVE TRADE CSV
    # ========================================================

    if all_trades:

        trades_df = pd.DataFrame(
            all_trades
        )

        trades_df.to_csv(
            TRADES_CSV,
            index=False
        )

    # ========================================================
    # FINAL MESSAGE
    # ========================================================

    print()

    print("=" * 110)

    print(
        "FILES CREATED"
    )

    print("=" * 110)

    print(
        OUTPUT_CSV
    )

    print(
        TRADES_CSV
    )

    print()

    print(
        "BACKTEST COMPLETE"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
