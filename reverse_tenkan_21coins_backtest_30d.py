# ============================================================
# REVERSE-TENKAN 21 COINS
# 30-DAY HISTORICAL BACKTEST
# EXIT COMPARISON
# ============================================================
#
# ENTRY STRATEGY: UNCHANGED
#
# EXIT MODELS:
#   1) TENKAN
#   2) 1R
#   3) 1.5R
#   4) 2R
#   5) 2.5R
#   6) 3R
#
# FIXES IN THIS VERSION:
#   - Robust Kraken retries
#   - Handles HTTP 429 / 5xx
#   - Handles empty candle responses
#   - Handles failed chunks without crashing
#   - Explicit summary columns
#   - No KeyError when summary is empty
#   - Saves CSV even when some symbols fail
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
    "PF_XBTUSD",
    "PF_ETHUSD",
    "PF_SOLUSD",
    "PF_XRPUSD",
    "PF_DOGEUSD",
    "PF_ADAUSD",
    "PF_AVAXUSD",
    "PF_LINKUSD",
    "PF_DOTUSD",
    "PF_LTCUSD",
    "PF_BCHUSD",
    "PF_UNIUSD",
    "PF_AAVEUSD",
    "PF_SUIUSD",
    "PF_HYPEUSD",
    "PF_NEARUSD",
    "PF_ATOMUSD",
    "PF_FILUSD",
    "PF_ARBUSD",
    "PF_OPUSD",
    "PF_ZECUSD",
]


SYMBOL_NAMES = {
    "PF_XBTUSD": "BTC",
    "PF_ETHUSD": "ETH",
    "PF_SOLUSD": "SOL",
    "PF_XRPUSD": "XRP",
    "PF_DOGEUSD": "DOGE",
    "PF_ADAUSD": "ADA",
    "PF_AVAXUSD": "AVAX",
    "PF_LINKUSD": "LINK",
    "PF_DOTUSD": "DOT",
    "PF_LTCUSD": "LTC",
    "PF_BCHUSD": "BCH",
    "PF_UNIUSD": "UNI",
    "PF_AAVEUSD": "AAVE",
    "PF_SUIUSD": "SUI",
    "PF_HYPEUSD": "HYPE",
    "PF_NEARUSD": "NEAR",
    "PF_ATOMUSD": "ATOM",
    "PF_FILUSD": "FIL",
    "PF_ARBUSD": "ARB",
    "PF_OPUSD": "OP",
    "PF_ZECUSD": "ZEC",
}


# ============================================================
# STRATEGY SETTINGS
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

ROUND_TRIP_FEE_PCT = 0.12

SL_FIRST = True


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
# KRAKEN
# ============================================================

KRAKEN_URL = (
    "https://futures.kraken.com/api/charts/v1/trade"
)

REQUEST_TIMEOUT = 30

MAX_RETRIES = 6

CHUNK_CANDLES = 1500

SLEEP_BETWEEN_CHUNKS = 0.50


session = requests.Session()

session.headers.update({
    "User-Agent": "Reverse-Tenkan-Backtest/1.5",
    "Accept": "application/json",
})


# ============================================================
# FETCH SINGLE CHUNK
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    start_ts,
    end_ts,
):

    url = (
        f"{KRAKEN_URL}/"
        f"{symbol}/"
        f"{resolution}"
    )

    params = {
        "from": int(start_ts),
        "to": int(end_ts),
        "count": CHUNK_CANDLES,
    }

    last_error = None

    for attempt in range(
        1,
        MAX_RETRIES + 1,
    ):

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT,
            )

            if response.status_code == 429:

                wait_time = min(
                    2 ** attempt,
                    15,
                )

                print(
                    f"  [429] {symbol} {resolution} "
                    f"retry {attempt}/{MAX_RETRIES} "
                    f"after {wait_time}s"
                )

                time.sleep(wait_time)
                continue


            if response.status_code >= 500:

                wait_time = min(
                    2 ** attempt,
                    15,
                )

                print(
                    f"  [HTTP {response.status_code}] "
                    f"{symbol} {resolution} "
                    f"retry {attempt}/{MAX_RETRIES}"
                )

                time.sleep(wait_time)
                continue


            response.raise_for_status()

            payload = response.json()

            candles = payload.get(
                "candles",
                [],
            )

            if not candles:

                last_error = (
                    "Empty candles response"
                )

                wait_time = min(
                    2 ** attempt,
                    10,
                )

                print(
                    f"  [EMPTY] {symbol} {resolution} "
                    f"retry {attempt}/{MAX_RETRIES}"
                )

                time.sleep(wait_time)
                continue


            df = pd.DataFrame(candles)


            rename_map = {
                "time": "timestamp",
                "ts": "timestamp",
                "open": "open",
                "high": "high",
                "low": "low",
                "close": "close",
                "volume": "volume",
            }


            df = df.rename(
                columns={
                    k: v
                    for k, v in rename_map.items()
                    if k in df.columns
                }
            )


            required = [
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]


            missing = [
                col
                for col in required
                if col not in df.columns
            ]


            if missing:

                last_error = (
                    f"Missing columns: {missing}"
                )

                time.sleep(
                    min(
                        2 ** attempt,
                        10,
                    )
                )

                continue


            df = df[
                required
            ].copy()


            df["timestamp"] = pd.to_datetime(
                df["timestamp"],
                unit="s",
                utc=True,
                errors="coerce",
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
                    errors="coerce",
                )


            df = df.dropna()


            df = (
                df
                .drop_duplicates(
                    subset=["timestamp"]
                )
                .sort_values(
                    "timestamp"
                )
                .reset_index(
                    drop=True
                )
            )


            if df.empty:

                last_error = (
                    "Empty after normalization"
                )

                time.sleep(
                    min(
                        2 ** attempt,
                        10,
                    )
                )

                continue


            return df


        except requests.RequestException as exc:

            last_error = str(exc)

            wait_time = min(
                2 ** attempt,
                15,
            )

            print(
                f"  [REQUEST ERROR] "
                f"{symbol} {resolution} "
                f"attempt {attempt}/{MAX_RETRIES}: "
                f"{exc}"
            )

            time.sleep(wait_time)


        except ValueError as exc:

            last_error = str(exc)

            print(
                f"  [JSON ERROR] "
                f"{symbol} {resolution} "
                f"attempt {attempt}/{MAX_RETRIES}: "
                f"{exc}"
            )

            time.sleep(
                min(
                    2 ** attempt,
                    10,
                )
            )


        except Exception as exc:

            last_error = str(exc)

            print(
                f"  [UNEXPECTED ERROR] "
                f"{symbol} {resolution} "
                f"attempt {attempt}/{MAX_RETRIES}: "
                f"{exc}"
            )

            time.sleep(
                min(
                    2 ** attempt,
                    10,
                )
            )


    print(
        f"  [FAILED] {symbol} {resolution} "
        f"after {MAX_RETRIES} attempts: "
        f"{last_error}"
    )

    return pd.DataFrame()


# ============================================================
# FETCH ALL CANDLES
# ============================================================

def fetch_all_candles(
    symbol,
    resolution,
    start_dt,
    end_dt,
):

    step_seconds = (
        5 * 60
        if resolution == "5m"
        else 15 * 60
    )


    current = int(
        start_dt.timestamp()
    )

    end_ts = int(
        end_dt.timestamp()
    )


    all_parts = []


    while current < end_ts:

        chunk_end = min(
            current
            + (
                CHUNK_CANDLES
                * step_seconds
            ),
            end_ts,
        )


        df = pd.DataFrame()


        for retry in range(
            1,
            MAX_RETRIES + 1,
        ):

            df = fetch_kraken_candles(
                symbol,
                resolution,
                current,
                chunk_end,
            )


            if not df.empty:
                break


            wait_time = min(
                2 ** retry,
                15,
            )

            print(
                f"  Chunk retry "
                f"{retry}/{MAX_RETRIES}: "
                f"{symbol} {resolution}"
            )

            time.sleep(wait_time)


        if not df.empty:

            all_parts.append(df)

        else:

            print(
                f"  [CHUNK FAILED] "
                f"{symbol} {resolution} "
                f"{pd.to_datetime(current, unit='s', utc=True)} "
                f"-> "
                f"{pd.to_datetime(chunk_end, unit='s', utc=True)}"
            )


        current = (
            chunk_end
            + step_seconds
        )


        time.sleep(
            SLEEP_BETWEEN_CHUNKS
        )


    if not all_parts:

        return pd.DataFrame()


    result = pd.concat(
        all_parts,
        ignore_index=True,
    )


    result = (
        result
        .drop_duplicates(
            subset=["timestamp"]
        )
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )


    result = result[
        (result["timestamp"] >= start_dt)
        &
        (result["timestamp"] <= end_dt)
    ].copy()


    return result


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    high = (
        df["high"]
        .rolling(
            TENKAN_PERIOD
        )
        .max()
    )


    low = (
        df["low"]
        .rolling(
            TENKAN_PERIOD
        )
        .min()
    )


    return (
        high + low
    ) / 2.0


# ============================================================
# RSI
# ============================================================

def calculate_rsi(
    series,
    period=14,
):

    delta = series.diff()


    gain = delta.clip(
        lower=0
    )


    loss = (
        -delta.clip(
            upper=0
        )
    )


    avg_gain = (
        gain
        .ewm(
            alpha=1 / period,
            adjust=False,
        )
        .mean()
    )


    avg_loss = (
        loss
        .ewm(
            alpha=1 / period,
            adjust=False,
        )
        .mean()
    )


    rs = (
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan,
        )
    )


    return (
        100
        -
        (
            100
            /
            (1 + rs)
        )
    )


# ============================================================
# INDICATORS
# ============================================================

def add_indicators(df):

    df = df.copy()


    df["tenkan"] = (
        calculate_tenkan(df)
    )


    df["rsi"] = (
        calculate_rsi(
            df["close"],
            RSI_PERIOD,
        )
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
        df["close"]
        -
        df["open"]
    ).abs()


    df["range"] = (
        df["high"]
        -
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


    df["avg_volume"] = (
        df["volume"]
        .rolling(
            RVOL_PERIOD
        )
        .mean()
    )


    df["rvol"] = (
        df["volume"]
        /
        df["avg_volume"]
    )


    df["distance_pct"] = (
        (
            df["close"]
            -
            df["tenkan"]
        )
        /
        df["tenkan"]
        *
        100
    )


    df["prev_distance_pct"] = (
        df["distance_pct"].shift(1)
    )


    df["tenkan_slope"] = (
        df["tenkan"]
        -
        df["tenkan"].shift(1)
    )


    df["prev_tenkan_slope"] = (
        df["tenkan_slope"].shift(1)
    )


    return df


# ============================================================
# 15M CONTEXT
# ============================================================

def build_15m_context(df15):

    ctx = df15.copy()


    ctx["ema20"] = (
        ctx["close"]
        .ewm(
            span=EMA_15M_PERIOD,
            adjust=False,
        )
        .mean()
    )


    ctx = ctx[
        [
            "timestamp",
            "close",
            "ema20",
        ]
    ].copy()


    ctx = ctx.rename(
        columns={
            "close": "close_15m",
            "ema20": "ema20_15m",
        }
    )


    return ctx


# ============================================================
# MERGE 15M
# ============================================================

def merge_context(
    df5,
    df15,
):

    context = build_15m_context(
        df15
    )


    merged = pd.merge_asof(
        df5.sort_values(
            "timestamp"
        ),
        context.sort_values(
            "timestamp"
        ),
        on="timestamp",
        direction="backward",
    )


    return merged


# ============================================================
# REVERSAL CANDLE
# ============================================================

def long_reversal_candle(row):

    if row["range"] <= 0:
        return False


    close_position = (
        row["close"]
        -
        row["low"]
    ) / row["range"]


    return (
        row["close"] > row["open"]
        and
        row["close"] > row["prev_close"]
        and
        row["high"] > row["prev_high"]
        and
        close_position >= 0.55
    )


def short_reversal_candle(row):

    if row["range"] <= 0:
        return False


    close_position = (
        row["close"]
        -
        row["low"]
    ) / row["range"]


    return (
        row["close"] < row["open"]
        and
        row["close"] < row["prev_close"]
        and
        row["low"] < row["prev_low"]
        and
        close_position <= 0.45
    )


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    df,
    i,
    side,
):

    row = df.iloc[i]


    score = 0


    distance = (
        row["distance_pct"]
    )


    prev_distance = (
        row["prev_distance_pct"]
    )


    # --------------------------------------------------------
    # Distance
    # --------------------------------------------------------

    if side == "LONG":

        if distance <= -MIN_DISTANCE_PCT:
            score += 2

    else:

        if distance >= MIN_DISTANCE_PCT:
            score += 2


    # --------------------------------------------------------
    # Distance contracting
    # --------------------------------------------------------

    if (
        pd.notna(prev_distance)
        and
        abs(distance)
        <
        abs(prev_distance)
    ):

        score += 2


    # --------------------------------------------------------
    # Tenkan slope reversal
    # --------------------------------------------------------

    if side == "LONG":

        if (
            row["tenkan_slope"] > 0
            and
            row["prev_tenkan_slope"] <= 0
        ):

            score += 2

    else:

        if (
            row["tenkan_slope"] < 0
            and
            row["prev_tenkan_slope"] >= 0
        ):

            score += 2


    # --------------------------------------------------------
    # Reversal candle
    # --------------------------------------------------------

    if side == "LONG":

        if long_reversal_candle(row):
            score += 2

    else:

        if short_reversal_candle(row):
            score += 2


    # --------------------------------------------------------
    # RSI reversal
    # --------------------------------------------------------

    if i > 0:

        prev_rsi = (
            df.iloc[i - 1]["rsi"]
        )

    else:

        prev_rsi = np.nan


    if side == "LONG":

        if (
            distance < 0
            and
            row["rsi"] < 35
            and
            row["rsi"] > prev_rsi
        ):

            score += 2

    else:

        if (
            distance > 0
            and
            row["rsi"] > 65
            and
            row["rsi"] < prev_rsi
        ):

            score += 2


    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    if (
        row["rvol"] >= RVOL_MIN
        and
        row["rvol"] <= RVOL_MAX
    ):

        score += 1


    # --------------------------------------------------------
    # No explosion
    # --------------------------------------------------------

    no_explosion = True


    if (
        pd.notna(row["avg_body"])
        and
        row["body"]
        >
        row["avg_body"]
        *
        MAX_BODY_MULTIPLIER
    ):

        no_explosion = False


    if (
        pd.notna(row["avg_range"])
        and
        row["range"]
        >
        row["avg_range"]
        *
        MAX_RANGE_MULTIPLIER
    ):

        no_explosion = False


    if no_explosion:
        score += 1


    # --------------------------------------------------------
    # 15M EMA context
    # --------------------------------------------------------

    if side == "LONG":

        if (
            row["close_15m"]
            >=
            row["ema20_15m"]
        ):

            score += 2

    else:

        if (
            row["close_15m"]
            <=
            row["ema20_15m"]
        ):

            score += 2


    return score


# ============================================================
# SIGNAL
# ============================================================

def get_signal(
    df,
    i,
):

    row = df.iloc[i]


    required_values = [
        "tenkan",
        "rsi",
        "rvol",
        "ema20_15m",
    ]


    for col in required_values:

        if not np.isfinite(
            row[col]
        ):

            return None


    distance = (
        row["distance_pct"]
    )


    # --------------------------------------------------------
    # LONG
    # --------------------------------------------------------

    if (
        distance
        <=
        -MIN_DISTANCE_PCT
    ):

        score = calculate_score(
            df,
            i,
            "LONG",
        )


        if score >= MIN_SCORE:

            entry = row["close"]

            tenkan = row["tenkan"]

            sl = (
                entry
                *
                (
                    1
                    -
                    SL_PCT / 100
                )
            )

            tp_tenkan = tenkan


            # Original strategy rule:
            # Tenkan TP must be profitable

            if tp_tenkan > entry:

                return {
                    "side": "LONG",
                    "entry": entry,
                    "sl": sl,
                    "tp_tenkan": tp_tenkan,
                    "score": score,
                }


    # --------------------------------------------------------
    # SHORT
    # --------------------------------------------------------

    if (
        distance
        >=
        MIN_DISTANCE_PCT
    ):

        score = calculate_score(
            df,
            i,
            "SHORT",
        )


        if score >= MIN_SCORE:

            entry = row["close"]

            tenkan = row["tenkan"]

            sl = (
                entry
                *
                (
                    1
                    +
                    SL_PCT / 100
                )
            )

            tp_tenkan = tenkan


            # Original strategy rule:
            # Tenkan TP must be profitable

            if tp_tenkan < entry:

                return {
                    "side": "SHORT",
                    "entry": entry,
                    "sl": sl,
                    "tp_tenkan": tp_tenkan,
                    "score": score,
                }


    return None


# ============================================================
# BUILD TP
# ============================================================

def build_tp(
    side,
    entry,
    sl,
    tp_tenkan,
    model,
):

    if model == "TENKAN":

        return tp_tenkan


    r = abs(
        entry - sl
    )


    multiple = EXIT_MODELS[
        model
    ]


    if side == "LONG":

        return (
            entry
            +
            r * multiple
        )


    return (
        entry
        -
        r * multiple
    )


# ============================================================
# TRADE PNL
# ============================================================

def calculate_trade_pnl(
    side,
    entry,
    exit_price,
):

    if side == "LONG":

        gross = (
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

        gross = (
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


    net = (
        gross
        -
        ROUND_TRIP_FEE_PCT
    )


    return gross, net


# ============================================================
# BACKTEST ONE EXIT MODEL
# ============================================================

def backtest_exit_model(
    df,
    signals,
    model,
):

    trades = []


    for signal in signals:

        entry_index = signal["index"]

        side = signal["side"]

        entry = signal["entry"]

        sl = signal["sl"]

        tp_tenkan = signal[
            "tp_tenkan"
        ]


        tp = build_tp(
            side,
            entry,
            sl,
            tp_tenkan,
            model,
        )


        # ----------------------------------------------------
        # Safety
        # ----------------------------------------------------

        if side == "LONG":

            if tp <= entry:
                continue

        else:

            if tp >= entry:
                continue


        exit_price = None

        exit_reason = None

        exit_index = None


        max_index = min(
            entry_index
            +
            MAX_HOLD_BARS,
            len(df) - 1,
        )


        for j in range(
            entry_index + 1,
            max_index + 1,
        ):

            candle = df.iloc[j]


            high = candle["high"]

            low = candle["low"]


            # ------------------------------------------------
            # LONG
            # ------------------------------------------------

            if side == "LONG":

                sl_hit = (
                    low <= sl
                )

                tp_hit = (
                    high >= tp
                )


                if (
                    sl_hit
                    and
                    tp_hit
                ):

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


            # ------------------------------------------------
            # SHORT
            # ------------------------------------------------

            else:

                sl_hit = (
                    high >= sl
                )

                tp_hit = (
                    low <= tp
                )


                if (
                    sl_hit
                    and
                    tp_hit
                ):

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


        # ----------------------------------------------------
        # TIMEOUT
        # ----------------------------------------------------

        if exit_price is None:

            exit_index = max_index

            exit_price = df.iloc[
                exit_index
            ]["close"]

            exit_reason = "TIMEOUT"


        exit_timestamp = df.iloc[
            exit_index
        ]["timestamp"]


        signal_timestamp = df.iloc[
            entry_index
        ]["timestamp"]


        gross_pct, net_pct = (
            calculate_trade_pnl(
                side,
                entry,
                exit_price,
            )
        )


        trades.append(
            {
                "signal_index": entry_index,
                "exit_index": exit_index,
                "timestamp": signal_timestamp,
                "exit_timestamp": exit_timestamp,
                "side": side,
                "entry": entry,
                "sl": sl,
                "tp_tenkan": tp_tenkan,
                "tp": tp,
                "exit": exit_price,
                "exit_reason": exit_reason,
                "score": signal["score"],
                "gross_pct": gross_pct,
                "net_pct": net_pct,
                "exit_model": model,
            }
        )


    return trades


# ============================================================
# STATISTICS
# ============================================================

def calculate_stats(
    trades,
):

    if not trades:

        return {
            "signals": 0,
            "wins": 0,
            "losses": 0,
            "timeouts": 0,
            "wr": 0.0,
            "pf": 0.0,
            "net": 0.0,
            "dd": 0.0,
        }


    df = pd.DataFrame(
        trades
    )


    signals = len(df)


    wins = int(
        (
            df["net_pct"] > 0
        ).sum()
    )


    losses = int(
        (
            df["net_pct"] < 0
        ).sum()
    )


    timeouts = int(
        (
            df["exit_reason"]
            ==
            "TIMEOUT"
        ).sum()
    )


    wr = (
        wins
        /
        signals
        *
        100
    ) if signals else 0.0


    gross_profit = float(
        df.loc[
            df["net_pct"] > 0,
            "net_pct",
        ].sum()
    )


    gross_loss = abs(
        float(
            df.loc[
                df["net_pct"] < 0,
                "net_pct",
            ].sum()
        )
    )


    if gross_loss > 0:

        pf = (
            gross_profit
            /
            gross_loss
        )

    else:

        pf = 0.0


    net = float(
        df["net_pct"].sum()
    )


    # --------------------------------------------------------
    # Drawdown
    # --------------------------------------------------------

    ordered = (
        df
        .sort_values(
            "timestamp"
        )
        .reset_index(
            drop=True
        )
    )


    equity = 0.0

    peak = 0.0

    max_dd = 0.0


    for pnl in ordered[
        "net_pct"
    ]:

        equity += float(
            pnl
        )


        peak = max(
            peak,
            equity,
        )


        dd = (
            peak
            -
            equity
        )


        max_dd = max(
            max_dd,
            dd,
        )


    return {
        "signals": signals,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "wr": wr,
        "pf": pf,
        "net": net,
        "dd": max_dd,
    }


# ============================================================
# BACKTEST ONE SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    start_dt,
    end_dt,
):

    name = SYMBOL_NAMES.get(
        symbol,
        symbol,
    )


    print(
        "\n"
        +
        "=" * 90
    )

    print(
        f"[{name}] BACKTESTING"
    )

    print(
        "=" * 90
    )


    # --------------------------------------------------------
    # 5M
    # --------------------------------------------------------

    df5 = fetch_all_candles(
        symbol,
        "5m",
        start_dt,
        end_dt,
    )


    print(
        f"5M candles:  {len(df5)}"
    )


    if df5.empty:

        print(
            f"[{name}] DATA ERROR: "
            f"5M candles unavailable"
        )

        return [], []


    # --------------------------------------------------------
    # 15M
    # --------------------------------------------------------

    df15 = fetch_all_candles(
        symbol,
        "15m",
        start_dt,
        end_dt,
    )


    print(
        f"15M candles: {len(df15)}"
    )


    if df15.empty:

        print(
            f"[{name}] DATA ERROR: "
            f"15M candles unavailable"
        )

        return [], []


    # --------------------------------------------------------
    # Indicators
    # --------------------------------------------------------

    df5 = add_indicators(
        df5
    )


    df = merge_context(
        df5,
        df15,
    )


    if df.empty:

        print(
            f"[{name}] DATA ERROR: "
            f"merged dataframe empty"
        )

        return [], []


    # --------------------------------------------------------
    # Signals
    # --------------------------------------------------------

    signals = []


    warmup = 100


    for i in range(
        warmup,
        len(df),
    ):

        signal = get_signal(
            df,
            i,
        )


        if signal is None:
            continue


        signal["index"] = i

        signal["symbol"] = symbol


        signals.append(
            signal
        )


    print(
        f"Signals found: {len(signals)}"
    )


    # --------------------------------------------------------
    # EXIT MODELS
    # --------------------------------------------------------

    all_trades = []

    summary_rows = []


    for model in EXIT_MODELS:

        trades = backtest_exit_model(
            df,
            signals,
            model,
        )


        for trade in trades:

            trade["symbol"] = symbol

            trade["coin"] = name


        stats = calculate_stats(
            trades
        )


        summary_rows.append(
            {
                "coin": name,
                "symbol": symbol,
                "exit_model": model,
                **stats,
            }
        )


        all_trades.extend(
            trades
        )


    # --------------------------------------------------------
    # Coin result
    # --------------------------------------------------------

    print(
        "\n"
        f"{name} EXIT COMPARISON"
    )

    print(
        "-" * 110
    )


    for row in summary_rows:

        pf = row["pf"]


        pf_text = (
            f"{pf:.3f}"
            if math.isfinite(pf)
            else "INF"
        )


        print(
            f"{row['exit_model']:>7} | "
            f"Signals {row['signals']:>3} | "
            f"W {row['wins']:>3} | "
            f"L {row['losses']:>3} | "
            f"TO {row['timeouts']:>2} | "
            f"WR {row['wr']:>6.2f}% | "
            f"PF {pf_text:>6} | "
            f"Net {row['net']:>9.3f}% | "
            f"DD {row['dd']:>9.3f}%"
        )


    return (
        summary_rows,
        all_trades,
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "\n"
        + "=" * 120
    )

    print(
        "REVERSE-TENKAN 21 COINS"
    )

    print(
        "30-DAY HISTORICAL BACKTEST"
    )

    print(
        "EXIT MODEL COMPARISON"
    )

    print(
        "=" * 120
    )


    end_dt = datetime.now(
        timezone.utc
    )


    start_dt = (
        end_dt
        -
        timedelta(
            days=30
        )
    )


    print(
        f"Start UTC: {start_dt.isoformat()}"
    )

    print(
        f"End UTC:   {end_dt.isoformat()}"
    )


    print(
        "\n"
        "Exit models:"
    )


    for model in EXIT_MODELS:
        print(
            f"  - {model}"
        )


    all_summary = []

    all_trades = []


    # ========================================================
    # 21 COINS
    # ========================================================

    for symbol in SYMBOLS:

        try:

            summary_rows, trades = (
                backtest_symbol(
                    symbol,
                    start_dt,
                    end_dt,
                )
            )


            all_summary.extend(
                summary_rows
            )


            all_trades.extend(
                trades
            )


        except Exception as exc:

            print(
                "\n"
                f"[FATAL SYMBOL ERROR] "
                f"{symbol}: {exc}"
            )

            # Continue with next coin
            continue


    # ========================================================
    # DATAFRAME COLUMNS
    # ========================================================

    SUMMARY_COLUMNS = [
        "coin",
        "symbol",
        "exit_model",
        "signals",
        "wins",
        "losses",
        "timeouts",
        "wr",
        "pf",
        "net",
        "dd",
    ]


    TRADE_COLUMNS = [
        "signal_index",
        "exit_index",
        "timestamp",
        "exit_timestamp",
        "side",
        "entry",
        "sl",
        "tp_tenkan",
        "tp",
        "exit",
        "exit_reason",
        "score",
        "gross_pct",
        "net_pct",
        "exit_model",
        "symbol",
        "coin",
    ]


    summary_df = pd.DataFrame(
        all_summary,
        columns=SUMMARY_COLUMNS,
    )


    trades_df = pd.DataFrame(
        all_trades,
        columns=TRADE_COLUMNS,
    )


    # ========================================================
    # 21 COIN RESULT
    # ========================================================

    print(
        "\n"
        + "=" * 130
    )

    print(
        "21-COIN / EXIT MODEL COMPARISON"
    )

    print(
        "=" * 130
    )


    if not summary_df.empty:

        display_df = summary_df[
            [
                "coin",
                "exit_model",
                "signals",
                "wins",
                "losses",
                "timeouts",
                "wr",
                "pf",
                "net",
                "dd",
            ]
        ].copy()


        print(
            display_df.to_string(
                index=False
            )
        )


    else:

        print(
            "No valid backtest data was produced."
        )


    # ========================================================
    # AGGREGATE
    # ========================================================

    print(
        "\n"
        + "=" * 130
    )

    print(
        "AGGREGATE RESULT BY EXIT MODEL"
    )

    print(
        "=" * 130
    )


    aggregate_rows = []


    if not summary_df.empty:

        for model in EXIT_MODELS:

            model_df = summary_df[
                summary_df[
                    "exit_model"
                ]
                ==
                model
            ].copy()


            if model_df.empty:
                continue


            total_signals = int(
                model_df[
                    "signals"
                ].sum()
            )


            total_wins = int(
                model_df[
                    "wins"
                ].sum()
            )


            total_losses = int(
                model_df[
                    "losses"
                ].sum()
            )


            total_timeouts = int(
                model_df[
                    "timeouts"
                ].sum()
            )


            total_net = float(
                model_df[
                    "net"
                ].sum()
            )


            model_trades = (
                trades_df[
                    trades_df[
                        "exit_model"
                    ]
                    ==
                    model
                ].copy()
                if not trades_df.empty
                else pd.DataFrame()
            )


            gross_profit = 0.0

            gross_loss = 0.0


            if not model_trades.empty:

                gross_profit = float(
                    model_trades.loc[
                        model_trades[
                            "net_pct"
                        ] > 0,
                        "net_pct",
                    ].sum()
                )


                gross_loss = abs(
                    float(
                        model_trades.loc[
                            model_trades[
                                "net_pct"
                            ] < 0,
                            "net_pct",
                        ].sum()
                    )
                )


            if gross_loss > 0:

                pf = (
                    gross_profit
                    /
                    gross_loss
                )

            else:

                pf = 0.0


            wr = (
                total_wins
                /
                total_signals
                *
                100
            ) if total_signals else 0.0


            # ------------------------------------------------
            # Aggregate DD
            # ------------------------------------------------

            equity = 0.0

            peak = 0.0

            max_dd = 0.0


            if not model_trades.empty:

                ordered = (
                    model_trades
                    .sort_values(
                        [
                            "timestamp",
                            "symbol",
                        ]
                    )
                )


                for pnl in ordered[
                    "net_pct"
                ]:

                    equity += float(
                        pnl
                    )


                    peak = max(
                        peak,
                        equity,
                    )


                    dd = (
                        peak
                        -
                        equity
                    )


                    max_dd = max(
                        max_dd,
                        dd,
                    )


            aggregate_rows.append(
                {
                    "exit_model": model,
                    "signals": total_signals,
                    "wins": total_wins,
                    "losses": total_losses,
                    "timeouts": total_timeouts,
                    "wr": wr,
                    "pf": pf,
                    "net": total_net,
                    "dd": max_dd,
                }
            )


    aggregate_df = pd.DataFrame(
        aggregate_rows,
        columns=[
            "exit_model",
            "signals",
            "wins",
            "losses",
            "timeouts",
            "wr",
            "pf",
            "net",
            "dd",
        ],
    )


    if not aggregate_df.empty:

        print(
            aggregate_df.to_string(
                index=False
            )
        )

    else:

        print(
            "No aggregate results available."
        )


    # ========================================================
    # SAVE CSV
    # ========================================================

    summary_file = (
        "reverse_tenkan_21coins_"
        "30d_exit_comparison_summary.csv"
    )


    trades_file = (
        "reverse_tenkan_21coins_"
        "30d_exit_comparison_trades.csv"
    )


    aggregate_file = (
        "reverse_tenkan_21coins_"
        "30d_exit_comparison_aggregate.csv"
    )


    summary_df.to_csv(
        summary_file,
        index=False,
    )


    trades_df.to_csv(
        trades_file,
        index=False,
    )


    aggregate_df.to_csv(
        aggregate_file,
        index=False,
    )


    # ========================================================
    # FINAL REPORT
    # ========================================================

    print(
        "\n"
        + "=" * 120
    )

    print(
        "BACKTEST COMPLETE"
    )

    print(
        "=" * 120
    )


    print(
        f"Summary CSV:   {summary_file}"
    )

    print(
        f"Trades CSV:    {trades_file}"
    )

    print(
        f"Aggregate CSV: {aggregate_file}"
    )


    print(
        "\n"
        f"Coins requested: {len(SYMBOLS)}"
    )


    if not summary_df.empty:

        completed_coins = (
            summary_df[
                "symbol"
            ]
            .nunique()
        )

    else:

        completed_coins = 0


    print(
        f"Coins with results: "
        f"{completed_coins}"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
