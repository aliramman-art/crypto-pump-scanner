# ============================================================
# DOGE REVERSE-TENKAN SIGNAL BOT v1.5
# 21 COINS | REAL 30-DAY HISTORICAL BACKTEST
# EXIT MODEL COMPARISON
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


# ------------------------------------------------------------
# Kraken Futures Charts API
# ------------------------------------------------------------

BASE_URL = "https://futures.kraken.com/api/charts/v1"

# Correct endpoint:
# /api/charts/v1/{tick_type}/{symbol}/{resolution}
TICK_TYPE = "trade"

RESOLUTION_5M = "5m"
RESOLUTION_15M = "15m"


# ------------------------------------------------------------
# Backtest
# ------------------------------------------------------------

DAYS_BACK = 30

TENKAN_PERIOD = 9

MIN_DISTANCE_PCT = 0.75
MIN_SCORE = 8

SL_PCT = 2.00

MAX_HOLD_BARS = 144
MAX_HOLD_HOURS = 12

RSI_PERIOD = 14

RVOL_PERIOD = 20
MIN_RVOL = 1.20
MAX_RVOL = 3.00

AVG_BODY_PERIOD = 20
AVG_RANGE_PERIOD = 20

EMA_PERIOD_15M = 20

MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00

ROUND_TRIP_FEE_PCT = 0.12

SL_FIRST = True


# ------------------------------------------------------------
# Request
# ------------------------------------------------------------

REQUEST_TIMEOUT = 30
MAX_RETRIES = 5

# Kraken chart endpoint supports a maximum amount of candles
# per request. 1500 is used here.
CANDLE_LIMIT = 1500


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
# HTTP SESSION
# ============================================================

session = requests.Session()

session.headers.update({
    "User-Agent": "Mozilla/5.0 Reverse-Tenkan-Backtest/1.5"
})


# ============================================================
# SAFE GET
# ============================================================

def safe_get(url, params=None):

    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):

        try:

            response = session.get(
                url,
                params=params,
                timeout=REQUEST_TIMEOUT
            )

            if response.status_code != 200:

                try:
                    body = response.text[:500]
                except Exception:
                    body = ""

                raise RuntimeError(
                    f"HTTP {response.status_code}: {body}"
                )

            return response.json()

        except Exception as e:

            last_error = e

            print(
                f"    Request failed "
                f"(attempt {attempt}/{MAX_RETRIES}): {e}"
            )

            if attempt < MAX_RETRIES:
                time.sleep(
                    min(2 ** (attempt - 1), 10)
                )

    raise RuntimeError(
        f"Kraken request failed after "
        f"{MAX_RETRIES} attempts: {last_error}"
    )


# ============================================================
# FETCH KRAKEN CANDLES
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    start_dt,
    end_dt
):

    all_rows = []

    resolution_seconds = {
        "5m": 5 * 60,
        "15m": 15 * 60,
    }[resolution]

    # IMPORTANT:
    # API from/to = epoch seconds
    current = int(
        start_dt.timestamp()
    )

    final_ts = int(
        end_dt.timestamp()
    )

    print(
        f"  Fetching {resolution}: "
        f"{start_dt.isoformat()} -> "
        f"{end_dt.isoformat()}"
    )

    request_number = 0

    while current < final_ts:

        request_number += 1

        # Number of seconds covered by 1500 candles
        chunk_seconds = (
            resolution_seconds
            * CANDLE_LIMIT
        )

        chunk_end = min(
            current + chunk_seconds,
            final_ts
        )

        # ====================================================
        # CORRECT KRAKEN ENDPOINT
        # ====================================================
        #
        # /api/charts/v1/trade/PF_XBTUSD/5m
        #
        # NOT:
        # /api/charts/v1/5m/PF_XBTUSD
        #
        # ====================================================

        url = (
            f"{BASE_URL}/"
            f"{TICK_TYPE}/"
            f"{symbol}/"
            f"{resolution}"
        )

        params = {
            "from": current,
            "to": chunk_end,
        }

        try:

            data = safe_get(
                url,
                params=params
            )

        except Exception as e:

            print(
                f"    DATA ERROR "
                f"{symbol} {resolution}: {e}"
            )

            # Move to next chunk instead of
            # killing the complete symbol.
            current = chunk_end
            continue

        candles = data.get(
            "candles",
            []
        )

        if candles:

            all_rows.extend(
                candles
            )

            print(
                f"    Chunk {request_number}: "
                f"{len(candles)} candles"
            )

        else:

            print(
                f"    Chunk {request_number}: "
                f"0 candles"
            )

        # Prevent infinite loop
        if chunk_end <= current:
            break

        current = chunk_end

        time.sleep(0.15)

    # ========================================================
    # EMPTY RESULT
    # ========================================================

    if not all_rows:

        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    # ========================================================
    # PARSE CANDLES
    # ========================================================

    rows = []

    for candle in all_rows:

        try:

            # Kraken Futures chart response normally uses
            # dictionary candle objects.
            #
            # Expected fields:
            # time, open, high, low, close, volume

            if isinstance(candle, dict):

                timestamp = candle.get(
                    "time"
                )

                open_price = candle.get(
                    "open"
                )

                high_price = candle.get(
                    "high"
                )

                low_price = candle.get(
                    "low"
                )

                close_price = candle.get(
                    "close"
                )

                volume = candle.get(
                    "volume"
                )

            else:

                # Fallback for list-style response

                timestamp = candle[0]
                open_price = candle[1]
                high_price = candle[2]
                low_price = candle[3]
                close_price = candle[4]
                volume = candle[5]

            rows.append({
                "timestamp": timestamp,
                "open": open_price,
                "high": high_price,
                "low": low_price,
                "close": close_price,
                "volume": volume,
            })

        except Exception:

            continue

    df = pd.DataFrame(
        rows
    )

    if df.empty:

        return pd.DataFrame(
            columns=[
                "timestamp",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ]
        )

    # ========================================================
    # CRITICAL TIMESTAMP FIX
    # ========================================================
    #
    # Kraken:
    #
    # from/to            = epoch seconds
    # candles[].time     = epoch milliseconds
    #
    # ========================================================

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="ms",
        utc=True,
        errors="coerce"
    )

    # ========================================================
    # NUMERIC COLUMNS
    # ========================================================

    numeric_cols = [
        "open",
        "high",
        "low",
        "close",
        "volume",
    ]

    for col in numeric_cols:

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
        ]
    )

    # ========================================================
    # SORT / DEDUPLICATE
    # ========================================================

    df = df.sort_values(
        "timestamp"
    )

    df = df.drop_duplicates(
        subset=["timestamp"],
        keep="last"
    )

    # ========================================================
    # FILTER REQUESTED RANGE
    # ========================================================

    start_ts = pd.Timestamp(
        start_dt
    )

    end_ts = pd.Timestamp(
        end_dt
    )

    df = df[
        (df["timestamp"] >= start_ts) &
        (df["timestamp"] <= end_ts)
    ]

    df = df.reset_index(
        drop=True
    )

    return df


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    highest = (
        df["high"]
        .rolling(TENKAN_PERIOD)
        .max()
    )

    lowest = (
        df["low"]
        .rolling(TENKAN_PERIOD)
        .min()
    )

    return (
        highest + lowest
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

    avg_gain = gain.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

    avg_loss = loss.ewm(
        alpha=1 / period,
        adjust=False,
        min_periods=period
    ).mean()

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

    df["tenkan"] = (
        calculate_tenkan(df)
    )

    df["rsi"] = (
        calculate_rsi(
            df["close"],
            RSI_PERIOD
        )
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
        .rolling(AVG_BODY_PERIOD)
        .mean()
    )

    df["avg_range"] = (
        df["range"]
        .rolling(AVG_RANGE_PERIOD)
        .mean()
    )

    df["rvol_avg"] = (
        df["volume"]
        .rolling(RVOL_PERIOD)
        .mean()
    )

    df["rvol"] = (
        df["volume"] /
        df["rvol_avg"].replace(
            0,
            np.nan
        )
    )

    df["close_position"] = np.where(
        df["range"] > 0,
        (
            df["close"] -
            df["low"]
        ) / df["range"],
        0.5
    )

    df["tenkan_distance_pct"] = (
        (
            df["close"] -
            df["tenkan"]
        )
        / df["tenkan"]
    ) * 100

    return df


# ============================================================
# PREPARE 15M
# ============================================================

def prepare_15m(df):

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
# MERGE 15M CONTEXT
# ============================================================

def merge_context(
    df5,
    df15
):

    left = (
        df5
        .sort_values("timestamp")
        .copy()
    )

    right = (
        df15[
            [
                "timestamp",
                "close",
                "ema20",
            ]
        ]
        .sort_values("timestamp")
        .copy()
    )

    right = right.rename(
        columns={
            "close": "close_15m",
            "ema20": "ema20_15m",
        }
    )

    merged = pd.merge_asof(
        left,
        right,
        on="timestamp",
        direction="backward"
    )

    return merged


# ============================================================
# GET SIGNAL
# ============================================================

def get_signal(
    df,
    i
):

    if i < 2:
        return None

    row = df.iloc[i]
    prev = df.iloc[i - 1]
    prev2 = df.iloc[i - 2]

    required = [
        "close",
        "open",
        "high",
        "low",
        "tenkan",
        "rsi",
        "rvol",
        "avg_body",
        "avg_range",
        "close_position",
        "close_15m",
        "ema20_15m",
    ]

    for col in required:

        if pd.isna(row[col]):

            return None

    close = float(
        row["close"]
    )

    open_price = float(
        row["open"]
    )

    high = float(
        row["high"]
    )

    low = float(
        row["low"]
    )

    tenkan = float(
        row["tenkan"]
    )

    prev_close = float(
        prev["close"]
    )

    prev_high = float(
        prev["high"]
    )

    prev_low = float(
        prev["low"]
    )

    prev_tenkan = float(
        prev["tenkan"]
    )

    prev2_tenkan = float(
        prev2["tenkan"]
    )

    rsi = float(
        row["rsi"]
    )

    prev_rsi = float(
        prev["rsi"]
    )

    rvol = float(
        row["rvol"]
    )

    body = float(
        row["body"]
    )

    candle_range = float(
        row["range"]
    )

    avg_body = float(
        row["avg_body"]
    )

    avg_range = float(
        row["avg_range"]
    )

    close_position = float(
        row["close_position"]
    )

    close_15m = float(
        row["close_15m"]
    )

    ema20_15m = float(
        row["ema20_15m"]
    )

    distance_pct = (
        (
            close -
            tenkan
        )
        / tenkan
    ) * 100

    # ========================================================
    # LONG
    # ========================================================

    if distance_pct <= -MIN_DISTANCE_PCT:

        score = 0

        # Distance
        score += 2

        # Distance contracting
        current_abs_distance = abs(
            distance_pct
        )

        prev_distance_pct = (
            (
                prev_close -
                prev_tenkan
            )
            / prev_tenkan
        ) * 100

        prev_abs_distance = abs(
            prev_distance_pct
        )

        if (
            current_abs_distance
            < prev_abs_distance
        ):
            score += 2

        # Tenkan slope reversal
        tenkan_slope_now = (
            tenkan -
            prev_tenkan
        )

        tenkan_slope_prev = (
            prev_tenkan -
            prev2_tenkan
        )

        if (
            tenkan_slope_now
            >
            tenkan_slope_prev
        ):
            score += 2

        # Reversal candle
        reversal_candle = (
            close > open_price
            and
            close > prev_close
            and
            high > prev_high
            and
            close_position >= 0.55
        )

        if reversal_candle:
            score += 2

        # RSI reversal
        rsi_reversal = (
            distance_pct < 0
            and
            rsi < 35
            and
            rsi > prev_rsi
        )

        if rsi_reversal:
            score += 2

        # RVOL
        rvol_ok = (
            MIN_RVOL
            <= rvol
            <= MAX_RVOL
        )

        if rvol_ok:
            score += 1

        # No explosion
        no_explosion = (
            body
            <= (
                avg_body *
                MAX_BODY_MULTIPLIER
            )
            and
            candle_range
            <= (
                avg_range *
                MAX_RANGE_MULTIPLIER
            )
        )

        if no_explosion:
            score += 1

        # 15M EMA
        ema_context = (
            close_15m
            >= ema20_15m
        )

        if ema_context:
            score += 2

        if score < MIN_SCORE:
            return None

        tp_tenkan = tenkan

        if tp_tenkan <= close:
            return None

        # Preserve original strategy:
        # Long setup = price below Tenkan,
        # therefore SL is below entry.
        sl = close * (
            1 -
            SL_PCT / 100
        )

        return {
            "side": "LONG",
            "entry": close,
            "sl": sl,
            "tp_tenkan": tp_tenkan,
            "score": score,
        }

    # ========================================================
    # SHORT
    # ========================================================

    if distance_pct >= MIN_DISTANCE_PCT:

        score = 0

        # Distance
        score += 2

        # Distance contracting
        current_abs_distance = abs(
            distance_pct
        )

        prev_distance_pct = (
            (
                prev_close -
                prev_tenkan
            )
            / prev_tenkan
        ) * 100

        prev_abs_distance = abs(
            prev_distance_pct
        )

        if (
            current_abs_distance
            < prev_abs_distance
        ):
            score += 2

        # Tenkan slope reversal
        tenkan_slope_now = (
            tenkan -
            prev_tenkan
        )

        tenkan_slope_prev = (
            prev_tenkan -
            prev2_tenkan
        )

        if (
            tenkan_slope_now
            <
            tenkan_slope_prev
        ):
            score += 2

        # Reversal candle
        reversal_candle = (
            close < open_price
            and
            close < prev_close
            and
            low < prev_low
            and
            close_position <= 0.45
        )

        if reversal_candle:
            score += 2

        # RSI reversal
        rsi_reversal = (
            distance_pct > 0
            and
            rsi > 65
            and
            rsi < prev_rsi
        )

        if rsi_reversal:
            score += 2

        # RVOL
        rvol_ok = (
            MIN_RVOL
            <= rvol
            <= MAX_RVOL
        )

        if rvol_ok:
            score += 1

        # No explosion
        no_explosion = (
            body
            <= (
                avg_body *
                MAX_BODY_MULTIPLIER
            )
            and
            candle_range
            <= (
                avg_range *
                MAX_RANGE_MULTIPLIER
            )
        )

        if no_explosion:
            score += 1

        # 15M EMA
        ema_context = (
            close_15m
            <= ema20_15m
        )

        if ema_context:
            score += 2

        if score < MIN_SCORE:
            return None

        tp_tenkan = tenkan

        if tp_tenkan >= close:
            return None

        # Preserve original strategy:
        # Short setup = price above Tenkan,
        # therefore SL is above entry.
        sl = close * (
            1 +
            SL_PCT / 100
        )

        return {
            "side": "SHORT",
            "entry": close,
            "sl": sl,
            "tp_tenkan": tp_tenkan,
            "score": score,
        }

    return None


# ============================================================
# BUILD SIGNALS
# ============================================================

def build_signals(df):

    signals = []

    for i in range(
        len(df)
    ):

        signal = get_signal(
            df,
            i
        )

        if signal is None:
            continue

        signal["index"] = i

        signal["timestamp"] = (
            df.iloc[i]["timestamp"]
        )

        signals.append(
            signal
        )

    return signals


# ============================================================
# BUILD TP
# ============================================================

def build_tp(
    side,
    entry,
    sl,
    tp_tenkan,
    model
):

    if model == "TENKAN":

        return float(
            tp_tenkan
        )

    risk = abs(
        entry - sl
    )

    multiple = float(
        model
    )

    if side == "LONG":

        return float(
            entry +
            risk *
            multiple
        )

    return float(
        entry -
        risk *
        multiple
    )


# ============================================================
# NET PNL
# ============================================================

def calculate_net_pnl(
    side,
    entry,
    exit_price
):

    if side == "LONG":

        gross_pct = (
            (
                exit_price -
                entry
            )
            / entry
        ) * 100

    else:

        gross_pct = (
            (
                entry -
                exit_price
            )
            / entry
        ) * 100

    return (
        gross_pct -
        ROUND_TRIP_FEE_PCT
    )


# ============================================================
# BACKTEST EXIT MODEL
# ============================================================

def backtest_exit_model(
    df,
    signals,
    model
):

    trades = []

    for signal in signals:

        entry_index = int(
            signal["index"]
        )

        entry = float(
            signal["entry"]
        )

        sl = float(
            signal["sl"]
        )

        tp_tenkan = float(
            signal["tp_tenkan"]
        )

        side = signal["side"]

        tp = build_tp(
            side,
            entry,
            sl,
            tp_tenkan,
            model
        )

        # Validate TP direction
        if (
            side == "LONG"
            and
            tp <= entry
        ):
            continue

        if (
            side == "SHORT"
            and
            tp >= entry
        ):
            continue

        exit_price = None
        exit_reason = None
        exit_index = None

        max_index = min(
            entry_index +
            MAX_HOLD_BARS,
            len(df) - 1
        )

        for j in range(
            entry_index + 1,
            max_index + 1
        ):

            candle = df.iloc[j]

            high = float(
                candle["high"]
            )

            low = float(
                candle["low"]
            )

            # =================================================
            # LONG
            # =================================================

            if side == "LONG":

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp
                )

                if (
                    hit_sl
                    and
                    hit_tp
                ):

                    if SL_FIRST:
                        exit_price = sl
                        exit_reason = "SL"
                    else:
                        exit_price = tp
                        exit_reason = "TP"

                    exit_index = j
                    break

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"
                    exit_index = j
                    break

                if hit_tp:

                    exit_price = tp
                    exit_reason = "TP"
                    exit_index = j
                    break

            # =================================================
            # SHORT
            # =================================================

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp
                )

                if (
                    hit_sl
                    and
                    hit_tp
                ):

                    if SL_FIRST:
                        exit_price = sl
                        exit_reason = "SL"
                    else:
                        exit_price = tp
                        exit_reason = "TP"

                    exit_index = j
                    break

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"
                    exit_index = j
                    break

                if hit_tp:

                    exit_price = tp
                    exit_reason = "TP"
                    exit_index = j
                    break

        # ====================================================
        # TIMEOUT
        # ====================================================

        if exit_price is None:

            exit_index = max_index

            exit_price = float(
                df.iloc[
                    exit_index
                ]["close"]
            )

            exit_reason = "TIMEOUT"

        pnl = calculate_net_pnl(
            side,
            entry,
            exit_price
        )

        trades.append({
            "model": model,
            "side": side,
            "entry_time": signal["timestamp"],
            "exit_time": (
                df.iloc[
                    exit_index
                ]["timestamp"]
            ),
            "entry": entry,
            "sl": sl,
            "tp_tenkan": tp_tenkan,
            "tp": tp,
            "exit": exit_price,
            "exit_reason": exit_reason,
            "score": signal["score"],
            "pnl_pct": pnl,
        })

    return trades


# ============================================================
# CALCULATE STATS
# ============================================================

def calculate_stats(
    trades
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

    wins = int(
        (
            df["pnl_pct"] > 0
        ).sum()
    )

    losses = int(
        (
            df["pnl_pct"] <= 0
        ).sum()
    )

    timeouts = int(
        (
            df["exit_reason"]
            == "TIMEOUT"
        ).sum()
    )

    signals = len(df)

    wr = (
        wins /
        signals *
        100
        if signals
        else 0
    )

    gross_profit = df.loc[
        df["pnl_pct"] > 0,
        "pnl_pct"
    ].sum()

    gross_loss = abs(
        df.loc[
            df["pnl_pct"] < 0,
            "pnl_pct"
        ].sum()
    )

    if gross_loss > 0:

        pf = (
            gross_profit /
            gross_loss
        )

    else:

        pf = (
            float("inf")
            if gross_profit > 0
            else 0.0
        )

    net = df[
        "pnl_pct"
    ].sum()

    equity = (
        df["pnl_pct"]
        .cumsum()
    )

    running_max = (
        equity
        .cummax()
    )

    drawdown = (
        running_max -
        equity
    )

    dd = (
        drawdown.max()
        if len(drawdown)
        else 0.0
    )

    return {
        "signals": signals,
        "wins": wins,
        "losses": losses,
        "timeouts": timeouts,
        "wr": wr,
        "pf": pf,
        "net": net,
        "dd": dd,
    }


# ============================================================
# BACKTEST SYMBOL
# ============================================================

def backtest_symbol(
    symbol,
    start_dt,
    end_dt
):

    name = SYMBOL_NAMES.get(
        symbol,
        symbol
    )

    print("")
    print("=" * 70)
    print(
        f"[{name}] {symbol}"
    )
    print("=" * 70)

    # ========================================================
    # 5M
    # ========================================================

    df5 = fetch_kraken_candles(
        symbol,
        RESOLUTION_5M,
        start_dt,
        end_dt
    )

    print(
        f"  5M candles: "
        f"{len(df5)}"
    )

    if df5.empty:

        print(
            f"[{name}] "
            f"5M DATA ERROR"
        )

        return None

    # ========================================================
    # 15M
    # ========================================================

    df15 = fetch_kraken_candles(
        symbol,
        RESOLUTION_15M,
        start_dt,
        end_dt
    )

    print(
        f"  15M candles: "
        f"{len(df15)}"
    )

    if df15.empty:

        print(
            f"[{name}] "
            f"15M DATA ERROR"
        )

        return None

    # ========================================================
    # INDICATORS
    # ========================================================

    df5 = prepare_5m(
        df5
    )

    df15 = prepare_15m(
        df15
    )

    # ========================================================
    # MERGE
    # ========================================================

    df = merge_context(
        df5,
        df15
    )

    # ========================================================
    # CLOSED CANDLES ONLY
    # ========================================================

    now_utc = pd.Timestamp.now(
        tz="UTC"
    )

    df = df[
        df["timestamp"]
        <= now_utc
    ].copy()

    df = df.reset_index(
        drop=True
    )

    if df.empty:

        return None

    # ========================================================
    # SIGNALS
    # ========================================================

    signals = build_signals(
        df
    )

    print(
        f"  Signals: "
        f"{len(signals)}"
    )

    if not signals:

        return {
            "symbol": symbol,
            "name": name,
            "summary": {},
            "trades": [],
        }

    # ========================================================
    # EXIT MODELS
    # ========================================================

    summary = {}
    all_trades = []

    for model_name, model_value in (
        EXIT_MODELS.items()
    ):

        trades = backtest_exit_model(
            df,
            signals,
            model_value
        )

        stats = calculate_stats(
            trades
        )

        summary[
            model_name
        ] = stats

        for trade in trades:

            trade["symbol"] = symbol
            trade["name"] = name
            trade["exit_model"] = (
                model_name
            )

            all_trades.append(
                trade
            )

    # ========================================================
    # PRINT SYMBOL RESULTS
    # ========================================================

    print("")

    print(
        f"  {'MODEL':<8}"
        f"{'TRADES':>8}"
        f"{'W':>6}"
        f"{'L':>6}"
        f"{'TO':>6}"
        f"{'WR':>9}"
        f"{'PF':>9}"
        f"{'NET':>11}"
        f"{'DD':>11}"
    )

    print(
        "  " + "-" * 76
    )

    for model_name in (
        EXIT_MODELS.keys()
    ):

        s = summary[
            model_name
        ]

        if math.isinf(
            s["pf"]
        ):
            pf_text = "INF"
        else:
            pf_text = (
                f"{s['pf']:.3f}"
            )

        print(
            f"  {model_name:<8}"
            f"{s['signals']:>8}"
            f"{s['wins']:>6}"
            f"{s['losses']:>6}"
            f"{s['timeouts']:>6}"
            f"{s['wr']:>8.2f}%"
            f"{pf_text:>9}"
            f"{s['net']:>10.3f}%"
            f"{s['dd']:>10.3f}%"
        )

    return {
        "symbol": symbol,
        "name": name,
        "summary": summary,
        "trades": all_trades,
    }


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 80)
    print(
        "DOGE REVERSE-TENKAN v1.5"
    )
    print(
        "21 COINS | REAL 30-DAY HISTORICAL BACKTEST"
    )
    print(
        "EXIT MODEL COMPARISON"
    )
    print("=" * 80)

    end_dt = datetime.now(
        timezone.utc
    )

    start_dt = (
        end_dt -
        timedelta(
            days=DAYS_BACK
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

    print(
        f"Coins: "
        f"{len(SYMBOLS)}"
    )

    print(
        "Exit models: "
        + ", ".join(
            EXIT_MODELS.keys()
        )
    )

    results = []
    all_trades = []

    # ========================================================
    # RUN ALL SYMBOLS
    # ========================================================

    for symbol in SYMBOLS:

        try:

            result = backtest_symbol(
                symbol,
                start_dt,
                end_dt
            )

            if result is None:

                print(
                    f"[{SYMBOL_NAMES.get(symbol, symbol)}] "
                    f"DATA ERROR"
                )

                continue

            results.append(
                result
            )

            all_trades.extend(
                result["trades"]
            )

        except Exception as e:

            print(
                f"[{SYMBOL_NAMES.get(symbol, symbol)}] "
                f"BACKTEST ERROR: {e}"
            )

            continue

    # ========================================================
    # SUMMARY
    # ========================================================

    summary_rows = []

    for result in results:

        symbol = result[
            "symbol"
        ]

        name = result[
            "name"
        ]

        for model_name in (
            EXIT_MODELS.keys()
        ):

            stats = result[
                "summary"
            ].get(
                model_name,
                {}
            )

            summary_rows.append({
                "symbol": symbol,
                "coin": name,
                "exit_model": model_name,
                "signals": stats.get(
                    "signals",
                    0
                ),
                "wins": stats.get(
                    "wins",
                    0
                ),
                "losses": stats.get(
                    "losses",
                    0
                ),
                "timeouts": stats.get(
                    "timeouts",
                    0
                ),
                "wr_pct": stats.get(
                    "wr",
                    0.0
                ),
                "pf": stats.get(
                    "pf",
                    0.0
                ),
                "net_pct": stats.get(
                    "net",
                    0.0
                ),
                "dd_pct": stats.get(
                    "dd",
                    0.0
                ),
            })

    summary_df = pd.DataFrame(
        summary_rows
    )

    # ========================================================
    # TRADES
    # ========================================================

    if all_trades:

        trades_df = pd.DataFrame(
            all_trades
        )

    else:

        trades_df = pd.DataFrame(
            columns=[
                "symbol",
                "name",
                "exit_model",
                "side",
                "entry_time",
                "exit_time",
                "entry",
                "sl",
                "tp_tenkan",
                "tp",
                "exit",
                "exit_reason",
                "score",
                "pnl_pct",
            ]
        )

    # ========================================================
    # AGGREGATE
    # ========================================================

    aggregate_rows = []

    for model_name in (
        EXIT_MODELS.keys()
    ):

        model_trades = [
            t
            for t in all_trades
            if t.get(
                "exit_model"
            ) == model_name
        ]

        stats = calculate_stats(
            model_trades
        )

        aggregate_rows.append({
            "exit_model": model_name,
            "signals": stats[
                "signals"
            ],
            "wins": stats[
                "wins"
            ],
            "losses": stats[
                "losses"
            ],
            "timeouts": stats[
                "timeouts"
            ],
            "wr_pct": stats[
                "wr"
            ],
            "pf": stats[
                "pf"
            ],
            "net_pct": stats[
                "net"
            ],
            "dd_pct": stats[
                "dd"
            ],
        })

    aggregate_df = pd.DataFrame(
        aggregate_rows
    )

    # ========================================================
    # PRINT AGGREGATE
    # ========================================================

    print("")
    print("=" * 90)
    print(
        "AGGREGATE EXIT MODEL COMPARISON"
    )
    print("=" * 90)

    print(
        f"{'MODEL':<10}"
        f"{'TRADES':>9}"
        f"{'W':>7}"
        f"{'L':>7}"
        f"{'TO':>7}"
        f"{'WR':>10}"
        f"{'PF':>10}"
        f"{'NET':>12}"
        f"{'DD':>12}"
    )

    print(
        "-" * 90
    )

    for row in aggregate_rows:

        if math.isinf(
            row["pf"]
        ):
            pf_text = "INF"
        else:
            pf_text = (
                f"{row['pf']:.3f}"
            )

        print(
            f"{row['exit_model']:<10}"
            f"{row['signals']:>9}"
            f"{row['wins']:>7}"
            f"{row['losses']:>7}"
            f"{row['timeouts']:>7}"
            f"{row['wr_pct']:>9.2f}%"
            f"{pf_text:>10}"
            f"{row['net_pct']:>11.3f}%"
            f"{row['dd_pct']:>11.3f}%"
        )

    # ========================================================
    # SAVE CSV FILES
    # ========================================================

    summary_file = (
        "reverse_tenkan_21coins_30d_"
        "exit_comparison_summary.csv"
    )

    trades_file = (
        "reverse_tenkan_21coins_30d_"
        "exit_comparison_trades.csv"
    )

    aggregate_file = (
        "reverse_tenkan_21coins_30d_"
        "exit_comparison_aggregate.csv"
    )

    summary_df.to_csv(
        summary_file,
        index=False
    )

    trades_df.to_csv(
        trades_file,
        index=False
    )

    aggregate_df.to_csv(
        aggregate_file,
        index=False
    )

    # ========================================================
    # FINAL
    # ========================================================

    print("")
    print("=" * 80)
    print(
        "BACKTEST COMPLETE"
    )
    print("=" * 80)

    print(
        f"Symbols processed: "
        f"{len(results)}/{len(SYMBOLS)}"
    )

    print(
        f"Total trades: "
        f"{len(all_trades)}"
    )

    print(
        f"Summary CSV: "
        f"{summary_file}"
    )

    print(
        f"Trades CSV: "
        f"{trades_file}"
    )

    print(
        f"Aggregate CSV: "
        f"{aggregate_file}"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
