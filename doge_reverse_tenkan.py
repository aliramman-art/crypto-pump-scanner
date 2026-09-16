# ============================================================
# DOGE REVERSE-TENKAN SIGNAL BOT v1.5
# ============================================================
#
# Kraken Futures data
# LBank Futures fee model
#
# Strategy:
#   Score >= 8 / 14
#   Entry distance >= 0.75% from Tenkan
#   TP = Tenkan at signal candle
#   SL = 2.00%
#   Max hold = 12 hours
#
# Telegram:
#   Sends one status message every 5 minutes
#   ALSO reports why no signal was generated
#
# REAL TRADING:
#   DISABLED
#
# IMPORTANT:
#   Strategy logic is unchanged.
#   Only rejection diagnostics were added.
#
# ============================================================

import os
import json
import time
import math
import requests
import pandas as pd
import numpy as np
from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

SYMBOL = "PF_DOGEUSD"

KRAKEN_BASE = "https://futures.kraken.com/api/charts/v1"

TIMEFRAME_5M = "5m"
TIMEFRAME_15M = "15m"

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

RVOL_MIN = 1.20
RVOL_MAX = 3.00

MAX_BODY_MULTIPLIER = 1.50
MAX_RANGE_MULTIPLIER = 2.00

# ------------------------------------------------------------
# LBank Futures fees
# Taker 0.06% each side
# ------------------------------------------------------------

LBANK_TAKER_FEE = 0.0006

ENTRY_FEE = LBANK_TAKER_FEE
EXIT_FEE = LBANK_TAKER_FEE

ROUND_TRIP_FEE = ENTRY_FEE + EXIT_FEE


# ------------------------------------------------------------
# Telegram
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)

STATE_FILE = "doge_reverse_tenkan_state.json"


# ============================================================
# HELPERS
# ============================================================

def utc_now():
    return datetime.now(timezone.utc)


def dt_to_seconds(dt):
    return int(dt.timestamp())


def safe_float(x):
    try:
        return float(x)
    except Exception:
        return np.nan


def pct(a, b):
    if b == 0 or pd.isna(a) or pd.isna(b):
        return np.nan

    return ((a / b) - 1.0) * 100.0


def fmt_price(x):
    if pd.isna(x):
        return "-"

    return f"{x:.8f}"


def fmt_pct(x):
    if pd.isna(x):
        return "-"

    sign = "+" if x >= 0 else ""

    return f"{sign}{x:.2f}%"


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:

        print("Telegram credentials are missing.")
        print(text)

        return False

    url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": text,
        "disable_web_page_preview": True
    }

    try:

        r = requests.post(
            url,
            json=payload,
            timeout=15
        )

        if r.status_code != 200:

            print(
                "Telegram error:",
                r.text
            )

            return False

        return True

    except Exception as e:

        print(
            "Telegram exception:",
            e
        )

        return False


# ============================================================
# STATE
# ============================================================

def default_state():

    return {
        "active_trade": None,
        "last_signal_candle": None,
        "last_status_candle": None,
        "signals_total": 0,
        "wins": 0,
        "losses": 0,
        "timeouts": 0,
        "net_pnl_pct": 0.0
    }


def load_state():

    if not os.path.exists(STATE_FILE):

        return default_state()

    try:

        with open(
            STATE_FILE,
            "r",
            encoding="utf-8"
        ) as f:

            data = json.load(f)

        state = default_state()

        state.update(data)

        return state

    except Exception:

        return default_state()


def save_state(state):

    tmp = STATE_FILE + ".tmp"

    with open(
        tmp,
        "w",
        encoding="utf-8"
    ) as f:

        json.dump(
            state,
            f,
            indent=2,
            ensure_ascii=False
        )

    os.replace(
        tmp,
        STATE_FILE
    )


# ============================================================
# KRAKEN DATA
# ============================================================

def fetch_kraken_candles(
    symbol,
    resolution,
    lookback_bars=500
):

    end_dt = utc_now()

    minutes = (
        5
        if resolution == "5m"
        else 15
    )

    start_dt = (
        end_dt
        - timedelta(
            minutes=minutes * (
                lookback_bars + 20
            )
        )
    )

    url = (
        f"{KRAKEN_BASE}/trade/"
        f"{symbol}/{resolution}"
    )

    params = {
        "from": dt_to_seconds(start_dt),
        "to": dt_to_seconds(end_dt),
        "count": 2000
    }

    r = requests.get(
        url,
        params=params,
        timeout=20
    )

    r.raise_for_status()

    data = r.json()

    candles = data.get(
        "candles",
        []
    )

    if not candles:

        raise RuntimeError(
            f"No candles returned for "
            f"{symbol} {resolution}"
        )

    rows = []

    for c in candles:

        if isinstance(c, dict):

            ts = (
                c.get("time")
                or c.get("timestamp")
                or c.get("ts")
            )

            o = c.get("open")
            h = c.get("high")
            l = c.get("low")
            cl = c.get("close")
            v = c.get("volume", 0)

        else:

            if len(c) < 6:
                continue

            ts = c[0]
            o = c[1]
            h = c[2]
            l = c[3]
            cl = c[4]
            v = c[5]

        rows.append({
            "timestamp": safe_float(ts),
            "open": safe_float(o),
            "high": safe_float(h),
            "low": safe_float(l),
            "close": safe_float(cl),
            "volume": safe_float(v)
        })

    df = pd.DataFrame(rows)

    if df.empty:

        raise RuntimeError(
            "Empty dataframe."
        )

    # Kraken may return milliseconds

    if (
        df["timestamp"].median()
        > 10_000_000_000
    ):

        df["timestamp"] /= 1000.0

    df["timestamp"] = pd.to_datetime(
        df["timestamp"],
        unit="s",
        utc=True
    )

    df = (
        df
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )

    # --------------------------------------------------------
    # CLOSED CANDLES ONLY
    # --------------------------------------------------------

    candle_minutes = minutes

    cutoff = (
        utc_now()
        - timedelta(
            minutes=candle_minutes
        )
    )

    df = df[
        df["timestamp"] <= cutoff
    ].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# TENKAN
# ============================================================

def calculate_tenkan(df):

    high = (
        df["high"]
        .rolling(TENKAN_PERIOD)
        .max()
    )

    low = (
        df["low"]
        .rolling(TENKAN_PERIOD)
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
        avg_gain
        /
        avg_loss.replace(
            0,
            np.nan
        )
    )

    rsi = 100 - (
        100 / (1 + rs)
    )

    return rsi


# ============================================================
# 5M INDICATORS
# ============================================================

def add_5m_indicators(df):

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
        df["close"]
        - df["open"]
    ).abs()

    df["range"] = (
        df["high"]
        - df["low"]
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

    df["rvol"] = (
        df["volume"]
        /
        df["volume"]
        .rolling(
            RVOL_PERIOD
        )
        .mean()
    )

    df["distance_pct"] = (
        (
            df["close"]
            - df["tenkan"]
        )
        /
        df["tenkan"]
        * 100
    )

    df["distance_abs"] = (
        df["distance_pct"]
        .abs()
    )

    df["tenkan_slope"] = (
        df["tenkan"]
        - df["tenkan"].shift(1)
    )

    df["distance_contracting"] = (
        df["distance_abs"]
        <
        df["distance_abs"].shift(1)
    )

    df["close_position"] = np.where(
        df["range"] > 0,
        (
            df["close"]
            - df["low"]
        )
        /
        df["range"],
        0.5
    )

    df["no_explosion"] = (
        (
            df["body"]
            <=
            df["avg_body"]
            * MAX_BODY_MULTIPLIER
        )
        &
        (
            df["range"]
            <=
            df["avg_range"]
            * MAX_RANGE_MULTIPLIER
        )
    )

    return df


# ============================================================
# 15M EMA
# ============================================================

def add_15m_indicators(df):

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
# 15M CONTEXT
# ============================================================

def merge_15m_context(
    df5,
    df15
):

    left = df5.copy()

    right = df15[
        [
            "timestamp",
            "close",
            "ema20"
        ]
    ].copy()

    # --------------------------------------------------------
    # IMPORTANT:
    # 15M candle becomes available only after it closes.
    # Therefore its availability timestamp is shifted +15m.
    # --------------------------------------------------------

    right["available_at"] = (
        right["timestamp"]
        + pd.Timedelta(
            minutes=15
        )
    )

    right = right[
        [
            "available_at",
            "close",
            "ema20"
        ]
    ]

    right = right.rename(
        columns={
            "close": "close_15m",
            "ema20": "ema20_15m"
        }
    )

    left["available_at"] = (
        left["timestamp"]
        + pd.Timedelta(
            minutes=5
        )
    )

    merged = pd.merge_asof(
        left.sort_values(
            "available_at"
        ),
        right.sort_values(
            "available_at"
        ),
        on="available_at",
        direction="backward"
    )

    return merged.drop(
        columns=["available_at"]
    )


# ============================================================
# SCORE
# ============================================================

def calculate_signal(row):

    score_long = 0
    score_short = 0

    distance = row["distance_pct"]

    # --------------------------------------------------------
    # DISTANCE
    # --------------------------------------------------------

    if distance <= -MIN_DISTANCE_PCT:
        score_long += 2

    if distance >= MIN_DISTANCE_PCT:
        score_short += 2

    # --------------------------------------------------------
    # CONTRACTION
    # --------------------------------------------------------

    if row["distance_contracting"]:

        if distance < 0:
            score_long += 2

        if distance > 0:
            score_short += 2

    # --------------------------------------------------------
    # TENKAN SLOPE
    # --------------------------------------------------------

    if (
        distance < 0
        and row["tenkan_slope"] >= 0
    ):
        score_long += 2

    if (
        distance > 0
        and row["tenkan_slope"] <= 0
    ):
        score_short += 2

    # --------------------------------------------------------
    # REVERSAL CANDLE
    # --------------------------------------------------------

    prev_close = row["prev_close"]
    prev_high = row["prev_high"]
    prev_low = row["prev_low"]

    bullish_reversal = (
        row["close"] > row["open"]
        and row["close"] > prev_close
        and row["high"] > prev_high
        and row["close_position"] >= 0.55
    )

    bearish_reversal = (
        row["close"] < row["open"]
        and row["close"] < prev_close
        and row["low"] < prev_low
        and row["close_position"] <= 0.45
    )

    if (
        distance < 0
        and bullish_reversal
    ):
        score_long += 2

    if (
        distance > 0
        and bearish_reversal
    ):
        score_short += 2

    # --------------------------------------------------------
    # RSI
    # --------------------------------------------------------

    if (
        distance < 0
        and row["rsi"] < 35
        and row["rsi"] > row["prev_rsi"]
    ):
        score_long += 2

    if (
        distance > 0
        and row["rsi"] > 65
        and row["rsi"] < row["prev_rsi"]
    ):
        score_short += 2

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    rvol_ok = (
        RVOL_MIN
        <= row["rvol"]
        <= RVOL_MAX
    )

    if rvol_ok:

        if distance < 0:
            score_long += 1

        if distance > 0:
            score_short += 1

    # --------------------------------------------------------
    # NO EXPLOSION
    # --------------------------------------------------------

    if row["no_explosion"]:

        if distance < 0:
            score_long += 1

        if distance > 0:
            score_short += 1

    # --------------------------------------------------------
    # 15M TREND
    # --------------------------------------------------------

    if (
        distance < 0
        and row["close_15m"]
        >= row["ema20_15m"]
    ):
        score_long += 2

    if (
        distance > 0
        and row["close_15m"]
        <= row["ema20_15m"]
    ):
        score_short += 2

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    if (
        distance <= -MIN_DISTANCE_PCT
        and score_long >= MIN_SCORE
    ):
        return "LONG", score_long

    if (
        distance >= MIN_DISTANCE_PCT
        and score_short >= MIN_SCORE
    ):
        return "SHORT", score_short

    return None, max(
        score_long,
        score_short
    )


# ============================================================
# REJECTION DIAGNOSTICS
#
# IMPORTANT:
# This function DOES NOT participate in signal generation.
# It only explains the existing result.
# ============================================================

def get_rejection_reason(row):

    distance = row["distance_pct"]

    score_long = 0
    score_short = 0

    # --------------------------------------------------------
    # If data required for the strategy is missing
    # --------------------------------------------------------

    required = [
        "tenkan",
        "rsi",
        "rvol",
        "ema20_15m",
        "close_15m",
        "prev_close",
        "prev_high",
        "prev_low",
        "prev_rsi"
    ]

    for col in required:

        if pd.isna(row[col]):

            return (
                f"Missing indicator data: {col}"
            )

    # --------------------------------------------------------
    # Distance
    # --------------------------------------------------------

    if distance <= -MIN_DISTANCE_PCT:

        direction = "LONG"

    elif distance >= MIN_DISTANCE_PCT:

        direction = "SHORT"

    elif abs(distance) < MIN_DISTANCE_PCT:

        return (
            "Entry distance below minimum "
            f"({abs(distance):.2f}% < "
            f"{MIN_DISTANCE_PCT:.2f}%)"
        )

    else:

        return "No valid direction"

    # --------------------------------------------------------
    # Calculate EXACT SAME SCORE LOGIC
    # --------------------------------------------------------

    if distance <= -MIN_DISTANCE_PCT:
        score_long += 2

    if distance >= MIN_DISTANCE_PCT:
        score_short += 2

    if row["distance_contracting"]:

        if distance < 0:
            score_long += 2

        if distance > 0:
            score_short += 2

    if (
        distance < 0
        and row["tenkan_slope"] >= 0
    ):
        score_long += 2

    if (
        distance > 0
        and row["tenkan_slope"] <= 0
    ):
        score_short += 2

    prev_close = row["prev_close"]
    prev_high = row["prev_high"]
    prev_low = row["prev_low"]

    bullish_reversal = (
        row["close"] > row["open"]
        and row["close"] > prev_close
        and row["high"] > prev_high
        and row["close_position"] >= 0.55
    )

    bearish_reversal = (
        row["close"] < row["open"]
        and row["close"] < prev_close
        and row["low"] < prev_low
        and row["close_position"] <= 0.45
    )

    if (
        distance < 0
        and bullish_reversal
    ):
        score_long += 2

    if (
        distance > 0
        and bearish_reversal
    ):
        score_short += 2

    if (
        distance < 0
        and row["rsi"] < 35
        and row["rsi"] > row["prev_rsi"]
    ):
        score_long += 2

    if (
        distance > 0
        and row["rsi"] > 65
        and row["rsi"] < row["prev_rsi"]
    ):
        score_short += 2

    rvol_ok = (
        RVOL_MIN
        <= row["rvol"]
        <= RVOL_MAX
    )

    if rvol_ok:

        if distance < 0:
            score_long += 1

        if distance > 0:
            score_short += 1

    if row["no_explosion"]:

        if distance < 0:
            score_long += 1

        if distance > 0:
            score_short += 1

    if (
        distance < 0
        and row["close_15m"]
        >= row["ema20_15m"]
    ):
        score_long += 2

    if (
        distance > 0
        and row["close_15m"]
        <= row["ema20_15m"]
    ):
        score_short += 2

    # --------------------------------------------------------
    # Exact reason for current direction
    # --------------------------------------------------------

    score = (
        score_long
        if direction == "LONG"
        else score_short
    )

    if score < MIN_SCORE:

        return (
            f"{direction} score below minimum "
            f"({score}/{14} < {MIN_SCORE})"
        )

    # --------------------------------------------------------
    # This means score passed but final strategy
    # condition somehow failed.
    # This is only diagnostic.
    # --------------------------------------------------------

    if direction == "LONG":

        if distance > -MIN_DISTANCE_PCT:

            return (
                "LONG distance condition failed"
            )

    if direction == "SHORT":

        if distance < MIN_DISTANCE_PCT:

            return (
                "SHORT distance condition failed"
            )

    return (
        f"{direction} did not pass final "
        "signal condition"
    )


# ============================================================
# SIGNAL
# ============================================================

def generate_signal(df):

    if len(df) < 100:
        return None

    row = df.iloc[-1]

    required = [
        "tenkan",
        "rsi",
        "rvol",
        "ema20_15m",
        "close_15m"
    ]

    for col in required:

        if pd.isna(row[col]):
            return None

    side, score = calculate_signal(row)

    if side is None:
        return None

    entry = float(row["close"])
    tenkan = float(row["tenkan"])

    if side == "LONG":

        tp = tenkan

        sl = entry * (
            1 - SL_PCT / 100
        )

        tp_distance = (
            (tp / entry) - 1
        ) * 100

    else:

        tp = tenkan

        sl = entry * (
            1 + SL_PCT / 100
        )

        tp_distance = (
            (entry / tp) - 1
        ) * 100

    # --------------------------------------------------------
    # TP must be in the profitable direction
    # --------------------------------------------------------

    if (
        side == "LONG"
        and tp <= entry
    ):
        return None

    if (
        side == "SHORT"
        and tp >= entry
    ):
        return None

    # --------------------------------------------------------
    # Net TP after round-trip LBank fees
    # --------------------------------------------------------

    gross_tp_pct = abs(
        tp_distance
    )

    net_tp_pct = (
        gross_tp_pct
        - ROUND_TRIP_FEE * 100
    )

    net_sl_pct = (
        SL_PCT
        + ROUND_TRIP_FEE * 100
    )

    return {
        "side": side,
        "score": int(score),
        "entry": entry,
        "tp": tp,
        "sl": sl,
        "tenkan": tenkan,
        "gross_tp_pct": gross_tp_pct,
        "net_tp_pct": net_tp_pct,
        "net_sl_pct": net_sl_pct,
        "timestamp": row["timestamp"].isoformat(),
        "distance_pct": float(
            row["distance_pct"]
        ),
        "rsi": float(
            row["rsi"]
        ),
        "rvol": float(
            row["rvol"]
        )
    }


# ============================================================
# ACTIVE TRADE CHECK
# ============================================================

def check_active_trade(
    state,
    current_price,
    current_time
):

    trade = state.get(
        "active_trade"
    )

    if not trade:
        return None

    side = trade["side"]

    entry = float(
        trade["entry"]
    )

    tp = float(
        trade["tp"]
    )

    sl = float(
        trade["sl"]
    )

    opened_at = datetime.fromisoformat(
        trade["timestamp"]
    )

    if opened_at.tzinfo is None:

        opened_at = opened_at.replace(
            tzinfo=timezone.utc
        )

    age_hours = (
        current_time
        - opened_at
    ).total_seconds() / 3600

    result = None

    if side == "LONG":

        if current_price <= sl:

            result = "SL"

        elif current_price >= tp:

            result = "TP"

    else:

        if current_price >= sl:

            result = "SL"

        elif current_price <= tp:

            result = "TP"

    if (
        result is None
        and age_hours >= 12
    ):

        result = "TIMEOUT"

    if result is None:

        return None

    if side == "LONG":

        gross_pct = (
            current_price / entry
            - 1
        ) * 100

    else:

        gross_pct = (
            1
            - current_price / entry
        ) * 100

    net_pct = (
        gross_pct
        - ROUND_TRIP_FEE * 100
    )

    trade["exit"] = current_price
    trade["result"] = result
    trade["net_pnl_pct"] = net_pct
    trade["closed_at"] = (
        current_time.isoformat()
    )

    if result == "TP":

        state["wins"] += 1

    elif result == "SL":

        state["losses"] += 1

    else:

        state["timeouts"] += 1

    state["net_pnl_pct"] += net_pct

    state["active_trade"] = None

    return trade


# ============================================================
# OPEN TRADE
# ============================================================

def open_trade(
    state,
    signal
):

    state["active_trade"] = {

        "side": signal["side"],

        "score": signal["score"],

        "entry": signal["entry"],

        "tp": signal["tp"],

        "sl": signal["sl"],

        "tenkan": signal["tenkan"],

        "timestamp": signal["timestamp"]
    }

    state["signals_total"] += 1


# ============================================================
# MESSAGE
# ============================================================

def build_message(
    df,
    state,
    signal,
    closed_trade,
    rejection_reason=None
):

    row = df.iloc[-1]

    price = float(
        row["close"]
    )

    timestamp = row["timestamp"]

    lines = []

    lines.append(
        "📊 DOGE REVERSE-TENKAN v1.5"
    )

    lines.append(
        f"🕐 {timestamp.strftime('%Y-%m-%d %H:%M UTC')}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED"
    )

    lines.append("")

    # --------------------------------------------------------
    # CLOSED TRADE
    # --------------------------------------------------------

    if closed_trade:

        result = closed_trade["result"]

        emoji = (
            "✅"
            if result == "TP"
            else "❌"
            if result == "SL"
            else "⏱️"
        )

        lines.append(
            f"{emoji} CLOSED "
            f"{closed_trade['side']}"
        )

        lines.append(
            f"Result: {result}"
        )

        lines.append(
            "Net P&L: "
            f"{fmt_pct(closed_trade['net_pnl_pct'])}"
        )

        lines.append("")

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    if signal:

        emoji = (
            "🟢"
            if signal["side"] == "LONG"
            else "🔴"
        )

        lines.append(
            f"{emoji} NEW SIGNAL"
        )

        lines.append(
            f"{signal['side']} | "
            f"Score {signal['score']}/14"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(signal['entry'])}"
        )

        tp_pct = (
            signal["tp"]
            /
            signal["entry"]
            - 1
        ) * 100

        sl_pct = (
            signal["sl"]
            /
            signal["entry"]
            - 1
        ) * 100

        if signal["side"] == "SHORT":

            tp_pct = abs(tp_pct)

            sl_pct = abs(sl_pct)

        lines.append(
            f"TP: "
            f"{fmt_price(signal['tp'])} "
            f"({tp_pct:+.2f}%)"
        )

        lines.append(
            f"SL: "
            f"{fmt_price(signal['sl'])} "
            f"(-{abs(sl_pct):.2f}%)"
        )

        lines.append(
            "Net TP after LBank fee: "
            f"{signal['net_tp_pct']:+.2f}%"
        )

        lines.append("")

    # --------------------------------------------------------
    # NO SIGNAL
    # --------------------------------------------------------

    if (
        signal is None
        and rejection_reason
        and state.get("active_trade") is None
    ):

        lines.append(
            "❌ NO SIGNAL"
        )

        lines.append(
            f"Reason: {rejection_reason}"
        )

        lines.append("")

    # --------------------------------------------------------
    # ACTIVE TRADE
    # --------------------------------------------------------

    trade = state.get(
        "active_trade"
    )

    if trade:

        side = trade["side"]

        emoji = (
            "🟢"
            if side == "LONG"
            else "🔴"
        )

        entry = float(
            trade["entry"]
        )

        tp = float(
            trade["tp"]
        )

        sl = float(
            trade["sl"]
        )

        if side == "LONG":

            current_pnl = (
                price / entry
                - 1
            ) * 100

        else:

            current_pnl = (
                1
                - price / entry
            ) * 100

        lines.append(
            f"{emoji} OPEN {side}"
        )

        lines.append(
            f"Entry: "
            f"{fmt_price(entry)}"
        )

        lines.append(
            f"Current: "
            f"{fmt_price(price)} "
            f"({current_pnl:+.2f}%)"
        )

        lines.append(
            f"TP: {fmt_price(tp)}"
        )

        lines.append(
            f"SL: {fmt_price(sl)}"
        )

    else:

        lines.append(
            "No Open Trade"
        )

    # --------------------------------------------------------
    # PERFORMANCE
    # --------------------------------------------------------

    total_closed = (
        state["wins"]
        + state["losses"]
        + state["timeouts"]
    )

    if total_closed > 0:

        wr = (
            state["wins"]
            /
            total_closed
            * 100
        )

    else:

        wr = 0

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Signals: "
        f"{state['signals_total']}"
    )

    lines.append(
        f"Closed: "
        f"{total_closed}"
    )

    lines.append(
        "W/L/T: "
        f"{state['wins']}/"
        f"{state['losses']}/"
        f"{state['timeouts']}"
    )

    lines.append(
        f"WR: {wr:.2f}%"
    )

    lines.append(
        "Net P&L: "
        f"{state['net_pnl_pct']:+.2f}%"
    )

    lines.append("")

    lines.append(
        "💰 LBank Taker Fee: "
        "0.06% / side"
    )

    lines.append(
        "🤖 Real Trading: DISABLED"
    )

    return "\n".join(lines)


# ============================================================
# MAIN
# ============================================================

def main():

    print("=" * 60)

    print(
        "DOGE REVERSE-TENKAN v1.5"
    )

    print("=" * 60)

    state = load_state()

    # --------------------------------------------------------
    # DOWNLOAD
    # --------------------------------------------------------

    df5 = fetch_kraken_candles(
        SYMBOL,
        TIMEFRAME_5M,
        500
    )

    df15 = fetch_kraken_candles(
        SYMBOL,
        TIMEFRAME_15M,
        250
    )

    # --------------------------------------------------------
    # INDICATORS
    # --------------------------------------------------------

    df5 = add_5m_indicators(
        df5
    )

    df15 = add_15m_indicators(
        df15
    )

    df = merge_15m_context(
        df5,
        df15
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

    df["prev_rsi"] = (
        df["rsi"].shift(1)
    )

    df = df.dropna().reset_index(
        drop=True
    )

    if len(df) < 50:

        raise RuntimeError(
            "Not enough valid candles."
        )

    # --------------------------------------------------------
    # CURRENT CANDLE
    # --------------------------------------------------------

    row = df.iloc[-1]

    current_price = float(
        row["close"]
    )

    current_time = row["timestamp"]

    print(
        "Current:",
        fmt_price(current_price)
    )

    print(
        "Tenkan:",
        fmt_price(row["tenkan"])
    )

    print(
        "Distance:",
        fmt_pct(row["distance_pct"])
    )

    # --------------------------------------------------------
    # CLOSE ACTIVE TRADE
    # --------------------------------------------------------

    closed_trade = (
        check_active_trade(
            state,
            current_price,
            current_time
        )
    )

    # --------------------------------------------------------
    # NEW SIGNAL
    # --------------------------------------------------------

    signal = generate_signal(
        df
    )

    # --------------------------------------------------------
    # REJECTION REASON
    #
    # IMPORTANT:
    # This only reports why the existing strategy
    # did not generate a signal.
    # It does NOT modify signal generation.
    # --------------------------------------------------------

    rejection_reason = None

    if signal is None:

        rejection_reason = (
            get_rejection_reason(row)
        )

    # --------------------------------------------------------
    # DUPLICATE PROTECTION
    # --------------------------------------------------------

    if signal:

        signal_candle = (
            signal["timestamp"]
        )

        if (
            state["last_signal_candle"]
            == signal_candle
        ):

            signal = None

            rejection_reason = (
                "Duplicate signal candle"
            )

    # --------------------------------------------------------
    # DO NOT OPEN NEW TRADE WHILE ONE IS ACTIVE
    # --------------------------------------------------------

    if (
        state.get("active_trade")
        is not None
    ):

        if signal is not None:

            signal = None

        rejection_reason = (
            "Active trade already open"
        )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    if signal:

        open_trade(
            state,
            signal
        )

        state[
            "last_signal_candle"
        ] = signal["timestamp"]

        print(
            "NEW SIGNAL:",
            signal["side"],
            signal["score"]
        )

    else:

        print(
            "NO SIGNAL:"
        )

        print(
            rejection_reason
            or "No signal"
        )

    # --------------------------------------------------------
    # MESSAGE
    # --------------------------------------------------------

    message = build_message(
        df,
        state,
        signal,
        closed_trade,
        rejection_reason
    )

    print()
    print(message)

    telegram_send(
        message
    )

    # --------------------------------------------------------
    # SAVE
    # --------------------------------------------------------

    save_state(
        state
    )


if __name__ == "__main__":

    main()
