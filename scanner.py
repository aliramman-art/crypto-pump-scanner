# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v3.0
# ============================================================
#
# GOAL:
#   High-quality volume + price-action setups
#   Approximately 0-1 valid signal/hour in normal markets
#   WITHOUT artificial hourly signal limitation.
#
# Kraken Futures
# TOP 100 USD perpetual markets by 24H volume
#
# TIMEFRAMES:
#   1H  = Main Trend
#   15M = Setup / Structure / Volume
#   5M  = Confirmation
#
# IMPORTANT:
#   - Only CLOSED candles are used
#   - No forced signal every hour
#   - No artificial "1 signal per hour" limiter
#   - RR >= 2.0 preserved
#   - Max SL <= 0.8% preserved
#   - Slightly relaxed setup RVOL to avoid over-filtering
#   - Detailed diagnostic counters
#   - CLOSED trade is reported once after successful Telegram delivery
#
# ============================================================

import os
import time
import math
import sqlite3
import requests
import pandas as pd
import numpy as np

from datetime import datetime, timezone, timedelta


# ============================================================
# CONFIG
# ============================================================

BASE = "https://futures.kraken.com"

INSTRUMENTS_URL = BASE + "/derivatives/api/v3/instruments"
TICKERS_URL = BASE + "/derivatives/api/v3/tickers"
CHART_URL = BASE + "/api/charts/v1/trade"

TOP_N = 100

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

OHLCV_LIMIT = 150

# ------------------------------------------------------------
# VOLUME FILTERS
# ------------------------------------------------------------
#
# Previous version used 2.0 as the abnormal threshold.
# This was one of the main reasons setup count became too low.
#
# 1.70 = acceptable abnormal volume for setup
# 2.50 = strong
# 3.00 = very strong
#
RVOL_PERIOD = 20

RVOL_ABNORMAL = 1.70
RVOL_STRONG = 2.50
RVOL_VERY_STRONG = 3.00


# ------------------------------------------------------------
# RISK
# ------------------------------------------------------------

MIN_RR = 2.0

# 0.80%
MAX_SL_PCT = 0.0080

ATR_PERIOD = 14
ATR_BUFFER_MULT = 0.15


# ------------------------------------------------------------
# SCORE
# ------------------------------------------------------------

MIN_SIGNAL_SCORE = 9


# ------------------------------------------------------------
# TRADING STATE
# ------------------------------------------------------------

MAX_OPEN_TRADES = 3
MAX_NEW_SIGNALS = 3

COOLDOWN_CANDLES = 3

DB_FILE = "volume_khat_100.db"

PAPER_TRADING = True


# ------------------------------------------------------------
# SWING
# ------------------------------------------------------------

SWING_LEFT = 2
SWING_RIGHT = 2


# ------------------------------------------------------------
# TELEGRAM
# ------------------------------------------------------------

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


# ============================================================
# TIME
# ============================================================

IRAN_TZ = timezone(timedelta(hours=3, minutes=30))


def utc_now():
    return datetime.now(timezone.utc)


def iran_now():
    return utc_now().astimezone(IRAN_TZ)


def fmt_time(dt=None):
    if dt is None:
        dt = utc_now()

    return dt.strftime("%Y-%m-%d %H:%M:%S UTC")


def fmt_num(x, digits=6):
    try:
        if x is None:
            return "-"
        return f"{float(x):.{digits}f}"
    except Exception:
        return "-"


def fmt_pct(x):
    try:
        return f"{float(x):+.2f}%"
    except Exception:
        return "0.00%"


# ============================================================
# HTTP
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "VOLUME-KHAT-100/3.0"
})


def http_get(url, params=None, timeout=15):
    try:
        r = SESSION.get(
            url,
            params=params,
            timeout=timeout
        )

        r.raise_for_status()

        return r.json()

    except Exception:
        return None


# ============================================================
# SQLITE
# ============================================================

def db_connect():
    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.execute("PRAGMA busy_timeout = 30000")

    return conn


def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            setup TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,
            score REAL,
            status TEXT,
            result TEXT,
            pnl REAL,
            entry_time TEXT,
            exit_time TEXT,
            exit_reason TEXT,
            closed_reported INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_status
        ON trades(status)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_symbol
        ON trades(symbol)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_trades_closed_reported
        ON trades(closed_reported)
    """)

    conn.commit()

    conn.close()


# ============================================================
# MARKET DISCOVERY
# ============================================================

def discover_markets():

    data = http_get(INSTRUMENTS_URL)

    markets = []

    if isinstance(data, dict):

        items = data.get("instruments")

        if items is None:
            items = data.get("result")

        if items is None:
            items = data.get("data")

        if isinstance(items, list):

            for item in items:

                if isinstance(item, str):
                    symbol = item
                else:
                    symbol = (
                        item.get("symbol")
                        or item.get("instrument")
                        or item.get("name")
                    )

                if not symbol:
                    continue

                symbol = str(symbol)

                if (
                    symbol.startswith("PF_")
                    and symbol.endswith("USD")
                ):
                    markets.append(symbol)

    markets = list(dict.fromkeys(markets))

    # --------------------------------------------------------
    # fallback
    # --------------------------------------------------------

    if not markets:

        ticker_data = http_get(TICKERS_URL)

        if isinstance(ticker_data, dict):

            items = ticker_data.get("tickers")

            if isinstance(items, list):

                for item in items:

                    if not isinstance(item, dict):
                        continue

                    symbol = (
                        item.get("symbol")
                        or item.get("instrument")
                    )

                    if not symbol:
                        continue

                    symbol = str(symbol)

                    if (
                        symbol.startswith("PF_")
                        and symbol.endswith("USD")
                    ):
                        markets.append(symbol)

    return list(dict.fromkeys(markets))


# ============================================================
# TICKER VOLUME
# ============================================================

def get_ticker_volumes():

    data = http_get(TICKERS_URL)

    result = {}

    if not isinstance(data, dict):
        return result

    items = data.get("tickers")

    if not isinstance(items, list):
        return result

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("instrument")
        )

        if not symbol:
            continue

        volume = (
            item.get("vol24h")
            or item.get("volume24h")
            or item.get("volume")
        )

        try:
            volume = float(volume)
        except Exception:
            continue

        result[str(symbol)] = volume

    return result


# ============================================================
# TOP MARKETS
# ============================================================

def get_top_markets():

    markets = discover_markets()

    if not markets:
        return []

    volumes = get_ticker_volumes()

    if volumes:

        markets.sort(
            key=lambda x: volumes.get(x, 0),
            reverse=True
        )

    return markets[:TOP_N]


# ============================================================
# OHLCV
# ============================================================

def fetch_ohlcv(symbol, timeframe):

    params = {
        "symbol": symbol,
        "interval": timeframe
    }

    data = http_get(
        CHART_URL,
        params=params
    )

    if data is None:
        return None

    rows = None

    if isinstance(data, list):
        rows = data

    elif isinstance(data, dict):

        for key in (
            "candles",
            "data",
            "result",
            "ohlcv"
        ):

            value = data.get(key)

            if isinstance(value, list):
                rows = value
                break

    if not rows:
        return None

    parsed = []

    for row in rows:

        try:

            if isinstance(row, dict):

                ts = (
                    row.get("time")
                    or row.get("timestamp")
                    or row.get("ts")
                )

                o = row.get("open")
                h = row.get("high")
                l = row.get("low")
                c = row.get("close")

                v = (
                    row.get("volume")
                    or row.get("vol")
                )

            elif isinstance(row, (list, tuple)):

                if len(row) < 6:
                    continue

                ts, o, h, l, c, v = row[:6]

            else:
                continue

            ts = float(ts)

            # milliseconds -> seconds
            if ts > 10_000_000_000:
                ts = ts / 1000.0

            parsed.append([
                pd.to_datetime(ts, unit="s", utc=True),
                float(o),
                float(h),
                float(l),
                float(c),
                float(v)
            ])

        except Exception:
            continue

    if not parsed:
        return None

    df = pd.DataFrame(
        parsed,
        columns=[
            "timestamp",
            "open",
            "high",
            "low",
            "close",
            "volume"
        ]
    )

    df = df.sort_values("timestamp")

    df = df.drop_duplicates(
        subset=["timestamp"],
        keep="last"
    )

    df = df.tail(OHLCV_LIMIT).reset_index(drop=True)

    # --------------------------------------------------------
    # remove latest unfinished candle
    # --------------------------------------------------------

    if len(df) >= 2:
        df = df.iloc[:-1].copy()

    if len(df) < 60:
        return None

    return df


# ============================================================
# ATR
# ============================================================

def calculate_atr(df, period=ATR_PERIOD):

    high = df["high"]
    low = df["low"]
    close = df["close"]

    prev_close = close.shift(1)

    tr1 = high - low

    tr2 = (high - prev_close).abs()

    tr3 = (low - prev_close).abs()

    tr = pd.concat(
        [tr1, tr2, tr3],
        axis=1
    ).max(axis=1)

    atr = tr.rolling(period).mean()

    return atr


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(df, period=RVOL_PERIOD):

    avg_volume = (
        df["volume"]
        .rolling(period)
        .mean()
        .shift(1)
    )

    rvol = df["volume"] / avg_volume

    return rvol


# ============================================================
# SWINGS
# ============================================================

def swing_highs(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    highs = df["high"]

    result = []

    for i in range(left, len(df) - right):

        window = highs.iloc[
            i-left:i+right+1
        ]

        if highs.iloc[i] == window.max():
            result.append(
                (
                    i,
                    float(highs.iloc[i])
                )
            )

    return result


def swing_lows(
    df,
    left=SWING_LEFT,
    right=SWING_RIGHT
):

    lows = df["low"]

    result = []

    for i in range(left, len(df) - right):

        window = lows.iloc[
            i-left:i+right+1
        ]

        if lows.iloc[i] == window.min():
            result.append(
                (
                    i,
                    float(lows.iloc[i])
                )
            )

    return result


# ============================================================
# STRUCTURE LEVELS
# ============================================================

def recent_resistance(df):

    swings = swing_highs(df)

    if not swings:
        return None

    current = float(df["close"].iloc[-1])

    candidates = [
        price
        for _, price in swings
        if price > current
    ]

    if not candidates:
        return None

    return min(candidates)


def recent_support(df):

    swings = swing_lows(df)

    if not swings:
        return None

    current = float(df["close"].iloc[-1])

    candidates = [
        price
        for _, price in swings
        if price < current
    ]

    if not candidates:
        return None

    return max(candidates)


# ============================================================
# 1H TREND
# ============================================================

def get_1h_trend(df):

    if df is None or len(df) < 60:
        return "NEUTRAL"

    sma20 = df["close"].rolling(20).mean().iloc[-1]

    sma50 = df["close"].rolling(50).mean().iloc[-1]

    close = float(df["close"].iloc[-1])

    if not all(
        math.isfinite(x)
        for x in [sma20, sma50, close]
    ):
        return "NEUTRAL"

    if (
        close > sma20
        and sma20 > sma50
    ):
        return "LONG"

    if (
        close < sma20
        and sma20 < sma50
    ):
        return "SHORT"

    return "NEUTRAL"


# ============================================================
# CANDLE METRICS
# ============================================================

def candle_metrics(row):

    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    rng = h - l

    body = abs(c - o)

    upper_wick = h - max(o, c)

    lower_wick = min(o, c) - l

    if rng <= 0:
        return {
            "range": 0,
            "body": 0,
            "body_ratio": 0,
            "upper_wick": 0,
            "lower_wick": 0
        }

    return {
        "range": rng,
        "body": body,
        "body_ratio": body / rng,
        "upper_wick": upper_wick,
        "lower_wick": lower_wick
    }


# ============================================================
# BREAKOUT SETUP
# ============================================================

def detect_breakout_setup(
    df15,
    trend
):

    if df15 is None or len(df15) < 30:
        return None

    last = df15.iloc[-1]

    previous = df15.iloc[-6:-1]

    close = float(last["close"])

    high = float(last["high"])

    low = float(last["low"])

    rvol = float(last.get("rvol", 0))

    if not math.isfinite(rvol):
        return None

    previous_high = float(previous["high"].max())

    previous_low = float(previous["low"].min())

    # --------------------------------------------------------
    # LONG BREAKOUT
    # --------------------------------------------------------

    if trend == "LONG":

        broke = (
            close > previous_high
            and high >= previous_high
        )

        if broke and rvol >= RVOL_ABNORMAL:

            return {
                "type": "BREAKOUT",
                "side": "LONG",
                "rvol": rvol,
                "level": previous_high
            }

    # --------------------------------------------------------
    # SHORT BREAKOUT
    # --------------------------------------------------------

    if trend == "SHORT":

        broke = (
            close < previous_low
            and low <= previous_low
        )

        if broke and rvol >= RVOL_ABNORMAL:

            return {
                "type": "BREAKOUT",
                "side": "SHORT",
                "rvol": rvol,
                "level": previous_low
            }

    return None


# ============================================================
# REJECTION SETUP
# ============================================================

def detect_rejection_setup(
    df15,
    trend
):

    if df15 is None or len(df15) < 30:
        return None

    last = df15.iloc[-1]

    metrics = candle_metrics(last)

    if metrics["range"] <= 0:
        return None

    rvol = float(last.get("rvol", 0))

    if not math.isfinite(rvol):
        return None

    if rvol < RVOL_ABNORMAL:
        return None

    close = float(last["close"])

    high = float(last["high"])

    low = float(last["low"])

    resistance = recent_resistance(
        df15.iloc[:-1]
    )

    support = recent_support(
        df15.iloc[:-1]
    )

    # --------------------------------------------------------
    # LONG REJECTION
    # --------------------------------------------------------

    if trend == "LONG":

        near_support = False

        if support is not None:

            distance = abs(
                close - support
            ) / close

            near_support = distance <= 0.008

        bullish_wick = (
            metrics["lower_wick"]
            >= metrics["body"] * 1.15
        )

        bullish_close = (
            close > float(last["open"])
        )

        if (
            near_support
            and bullish_wick
            and bullish_close
        ):

            return {
                "type": "REJECTION",
                "side": "LONG",
                "rvol": rvol,
                "level": support
            }

    # --------------------------------------------------------
    # SHORT REJECTION
    # --------------------------------------------------------

    if trend == "SHORT":

        near_resistance = False

        if resistance is not None:

            distance = abs(
                resistance - close
            ) / close

            near_resistance = distance <= 0.008

        bearish_wick = (
            metrics["upper_wick"]
            >= metrics["body"] * 1.15
        )

        bearish_close = (
            close < float(last["open"])
        )

        if (
            near_resistance
            and bearish_wick
            and bearish_close
        ):

            return {
                "type": "REJECTION",
                "side": "SHORT",
                "rvol": rvol,
                "level": resistance
            }

    return None


# ============================================================
# COMBINED 15M SETUP
# ============================================================

def detect_15m_setup(df15, trend):

    setup = detect_breakout_setup(
        df15,
        trend
    )

    if setup:
        return setup

    setup = detect_rejection_setup(
        df15,
        trend
    )

    if setup:
        return setup

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirm_5m(
    df5,
    side
):

    if df5 is None or len(df5) < 20:
        return False

    last = df5.iloc[-1]

    metrics = candle_metrics(last)

    if metrics["range"] <= 0:
        return False

    # Slightly relaxed from old .35
    # but still requires meaningful candle body.
    if metrics["body_ratio"] < 0.30:
        return False

    o = float(last["open"])

    c = float(last["close"])

    if side == "LONG":

        if c <= o:
            return False

        return True

    if side == "SHORT":

        if c >= o:
            return False

        return True

    return False


# ============================================================
# STRUCTURAL STOP
# ============================================================

def calculate_structural_sl(
    df15,
    df1h,
    side,
    entry
):

    atr15_series = calculate_atr(df15)

    atr1h_series = calculate_atr(df1h)

    atr15 = float(
        atr15_series.iloc[-1]
    )

    atr1h = float(
        atr1h_series.iloc[-1]
    )

    atr_values = [
        x
        for x in [atr15, atr1h]
        if math.isfinite(x) and x > 0
    ]

    if not atr_values:
        return None

    atr = max(atr_values)

    buffer = atr * ATR_BUFFER_MULT

    if side == "LONG":

        supports = []

        s15 = recent_support(df15)

        s1h = recent_support(df1h)

        if s15 is not None:
            supports.append(s15)

        if s1h is not None:
            supports.append(s1h)

        below = [
            x
            for x in supports
            if x < entry
        ]

        if below:

            base = max(below)

            sl = base - buffer

        else:

            sl = entry - atr - buffer

        return sl

    if side == "SHORT":

        resistances = []

        r15 = recent_resistance(df15)

        r1h = recent_resistance(df1h)

        if r15 is not None:
            resistances.append(r15)

        if r1h is not None:
            resistances.append(r1h)

        above = [
            x
            for x in resistances
            if x > entry
        ]

        if above:

            base = min(above)

            sl = base + buffer

        else:

            sl = entry + atr + buffer

        return sl

    return None


# ============================================================
# TRADE CALCULATION
# ============================================================

def calculate_trade(
    df15,
    df1h,
    side
):

    entry = float(
        df5_last_close_placeholder(df15)
    )

    return None


# ============================================================
# ENTRY FROM 5M
# ============================================================

def get_entry_price(df5):

    return float(
        df5["close"].iloc[-1]
    )


# ============================================================
# TRADE CALCULATION ACTUAL
# ============================================================

def build_trade(
    df1h,
    df15,
    df5,
    side,
    setup,
    score
):

    entry = get_entry_price(df5)

    sl = calculate_structural_sl(
        df15,
        df1h,
        side,
        entry
    )

    if sl is None:
        return None, "SL_ERROR"

    # --------------------------------------------------------
    # SL distance
    # --------------------------------------------------------

    sl_distance = abs(
        entry - sl
    )

    if entry <= 0:
        return None, "BAD_ENTRY"

    sl_pct = (
        sl_distance / entry
    )

    if sl_pct <= 0:
        return None, "BAD_SL"

    if sl_pct > MAX_SL_PCT:
        return None, "SL_TOO_WIDE"

    # --------------------------------------------------------
    # TP
    #
    # Prefer structural target.
    # If structure does not provide enough RR,
    # calculate minimum 2R target.
    #
    # This does NOT weaken RR.
    # --------------------------------------------------------

    if side == "LONG":

        resistance15 = recent_resistance(
            df15
        )

        resistance1h = recent_resistance(
            df1h
        )

        targets = []

        if resistance15 is not None:
            if resistance15 > entry:
                targets.append(
                    resistance15
                )

        if resistance1h is not None:
            if resistance1h > entry:
                targets.append(
                    resistance1h
                )

        minimum_tp = entry + (
            sl_distance * MIN_RR
        )

        valid_targets = [
            x
            for x in targets
            if x >= minimum_tp
        ]

        if valid_targets:

            tp = min(valid_targets)

        else:

            # No nearby structural target capable
            # of supporting required RR.
            #
            # Use minimum 2R only if there is no
            # structural resistance blocking it.
            #
            tp = minimum_tp

            if resistance15 is not None:
                if (
                    entry < resistance15
                    < minimum_tp
                ):
                    return None, "TP_BLOCKED"

            if resistance1h is not None:
                if (
                    entry < resistance1h
                    < minimum_tp
                ):
                    return None, "TP_BLOCKED"

    else:

        support15 = recent_support(
            df15
        )

        support1h = recent_support(
            df1h
        )

        targets = []

        if support15 is not None:
            if support15 < entry:
                targets.append(
                    support15
                )

        if support1h is not None:
            if support1h < entry:
                targets.append(
                    support1h
                )

        minimum_tp = entry - (
            sl_distance * MIN_RR
        )

        valid_targets = [
            x
            for x in targets
            if x <= minimum_tp
        ]

        if valid_targets:

            tp = max(valid_targets)

        else:

            tp = minimum_tp

            if support15 is not None:
                if (
                    minimum_tp
                    < support15
                    < entry
                ):
                    return None, "TP_BLOCKED"

            if support1h is not None:
                if (
                    minimum_tp
                    < support1h
                    < entry
                ):
                    return None, "TP_BLOCKED"

    # --------------------------------------------------------
    # RR
    # --------------------------------------------------------

    reward = abs(
        tp - entry
    )

    rr = reward / sl_distance

    if rr < MIN_RR:
        return None, "RR_TOO_LOW"

    return {
        "side": side,
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "score": score,
        "setup": setup["type"],
        "rvol": setup["rvol"]
    }, None


# ============================================================
# SCORE
# ============================================================

def calculate_score(
    trend,
    setup,
    df5
):

    score = 0

    # Trend
    if trend in ("LONG", "SHORT"):
        score += 3

    # Setup
    if setup is not None:
        score += 4

    # RVOL
    rvol = float(
        setup.get("rvol", 0)
    )

    if rvol >= RVOL_VERY_STRONG:
        score += 4

    elif rvol >= RVOL_STRONG:
        score += 4

    elif rvol >= RVOL_ABNORMAL:
        score += 3

    # 5M confirmation
    if confirm_5m(
        df5,
        setup["side"]
    ):
        score += 4

    return score


# ============================================================
# DIAGNOSTICS
# ============================================================

def new_diagnostics():

    return {
        "markets": 0,
        "data_ok": 0,
        "data_errors": 0,

        "trend": 0,
        "neutral": 0,

        "setup": 0,
        "no_setup": 0,

        "confirmation": 0,
        "no_confirmation": 0,

        "valid_risk": 0,
        "invalid_risk": 0,

        "score_pass": 0,
        "low_score": 0,

        "final": 0,

        "reasons": {}
    }


def diag_reason(diag, reason):

    if not reason:
        return

    diag["reasons"][reason] = (
        diag["reasons"].get(reason, 0) + 1
    )


# ============================================================
# SCAN ONE MARKET
# ============================================================

def scan_market(
    symbol,
    diag
):

    diag["markets"] += 1

    # --------------------------------------------------------
    # DATA
    # --------------------------------------------------------

    df1h = fetch_ohlcv(
        symbol,
        TF_1H
    )

    df15 = fetch_ohlcv(
        symbol,
        TF_15M
    )

    df5 = fetch_ohlcv(
        symbol,
        TF_5M
    )

    if (
        df1h is None
        or df15 is None
        or df5 is None
    ):

        diag["data_errors"] += 1

        diag_reason(
            diag,
            "DATA"
        )

        return None

    diag["data_ok"] += 1

    # --------------------------------------------------------
    # RVOL
    # --------------------------------------------------------

    try:

        df15 = df15.copy()

        df15["rvol"] = calculate_rvol(
            df15
        )

        df5 = df5.copy()

        df5["rvol"] = calculate_rvol(
            df5
        )

    except Exception:

        diag["data_errors"] += 1

        diag_reason(
            diag,
            "RVOL"
        )

        return None

    # --------------------------------------------------------
    # TREND
    # --------------------------------------------------------

    trend = get_1h_trend(
        df1h
    )

    if trend == "NEUTRAL":

        diag["neutral"] += 1

        return None

    diag["trend"] += 1

    # --------------------------------------------------------
    # SETUP
    # --------------------------------------------------------

    setup = detect_15m_setup(
        df15,
        trend
    )

    if setup is None:

        diag["no_setup"] += 1

        return None

    diag["setup"] += 1

    # --------------------------------------------------------
    # CONFIRMATION
    # --------------------------------------------------------

    confirmed = confirm_5m(
        df5,
        setup["side"]
    )

    if not confirmed:

        diag["no_confirmation"] += 1

        return None

    diag["confirmation"] += 1

    # --------------------------------------------------------
    # SCORE
    # --------------------------------------------------------

    score = calculate_score(
        trend,
        setup,
        df5
    )

    if score < MIN_SIGNAL_SCORE:

        diag["low_score"] += 1

        diag_reason(
            diag,
            "SCORE"
        )

        return None

    diag["score_pass"] += 1

    # --------------------------------------------------------
    # RISK
    # --------------------------------------------------------

    trade, risk_reason = build_trade(
        df1h,
        df15,
        df5,
        setup["side"],
        setup,
        score
    )

    if trade is None:

        diag["invalid_risk"] += 1

        diag_reason(
            diag,
            risk_reason
        )

        return None

    diag["valid_risk"] += 1

    # --------------------------------------------------------
    # FINAL
    # --------------------------------------------------------

    diag["final"] += 1

    trade["symbol"] = symbol

    trade["trend"] = trend

    trade["confirmation"] = True

    return trade


# ============================================================
# SCAN ALL
# ============================================================

def scan_all_markets(
    markets
):

    diag = new_diagnostics()

    candidates = []

    for symbol in markets:

        try:

            trade = scan_market(
                symbol,
                diag
            )

            if trade is not None:
                candidates.append(
                    trade
                )

        except Exception as e:

            diag["data_errors"] += 1

            diag_reason(
                diag,
                "EXCEPTION"
            )

    # --------------------------------------------------------
    # Rank candidates
    # --------------------------------------------------------

    candidates.sort(
        key=lambda x: (
            x.get("score", 0),
            x.get("rr", 0),
            x.get("rvol", 0)
        ),
        reverse=True
    )

    return candidates, diag


# ============================================================
# OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    conn.close()

    columns = [
        "id",
        "symbol",
        "side",
        "setup",
        "entry",
        "sl",
        "tp",
        "rr",
        "score",
        "status",
        "result",
        "pnl",
        "entry_time",
        "exit_time",
        "exit_reason"
    ]

    return [
        dict(zip(columns, row))
        for row in rows
    ]


# ============================================================
# EXISTING SYMBOL OPEN
# ============================================================

def symbol_has_open_trade(symbol):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE symbol = ?
        AND status = 'OPEN'
    """, (symbol,))

    count = cur.fetchone()[0]

    conn.close()

    return count > 0


# ============================================================
# COOLDOWN
# ============================================================

def symbol_in_cooldown(symbol):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT entry_time
        FROM trades
        WHERE symbol = ?
        ORDER BY id DESC
        LIMIT 1
    """, (symbol,))

    row = cur.fetchone()

    conn.close()

    if not row:
        return False

    try:

        last_time = datetime.fromisoformat(
            row[0]
        )

        now = utc_now()

        # ----------------------------------------------------
        # 5-minute candles
        # ----------------------------------------------------

        cooldown_minutes = (
            COOLDOWN_CANDLES * 5
        )

        return (
            now - last_time
        ).total_seconds() < (
            cooldown_minutes * 60
        )

    except Exception:
        return False


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(trade):

    symbol = trade["symbol"]

    # --------------------------------------------------------
    # Duplicate open
    # --------------------------------------------------------

    if symbol_has_open_trade(symbol):
        return False

    # --------------------------------------------------------
    # Cooldown
    # --------------------------------------------------------

    if symbol_in_cooldown(symbol):
        return False

    conn = db_connect()

    cur = conn.cursor()

    now = utc_now().isoformat()

    cur.execute("""
        INSERT INTO trades (
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            status,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason,
            closed_reported,
            created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        symbol,
        trade["side"],
        trade["setup"],
        trade["entry"],
        trade["sl"],
        trade["tp"],
        trade["rr"],
        trade["score"],
        "OPEN",
        None,
        None,
        now,
        None,
        None,
        0,
        now
    ))

    conn.commit()

    conn.close()

    return True


# ============================================================
# CLOSE TRADE
# ============================================================

def close_trade(
    trade_id,
    result,
    pnl,
    exit_price,
    reason
):

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        UPDATE trades
        SET
            status = 'CLOSED',
            result = ?,
            pnl = ?,
            exit_time = ?,
            exit_reason = ?,
            closed_reported = 0
        WHERE id = ?
        AND status = 'OPEN'
    """, (
        result,
        pnl,
        utc_now().isoformat(),
        reason,
        trade_id
    ))

    conn.commit()

    conn.close()


# ============================================================
# LIVE PRICE
# ============================================================

def get_live_price_map():

    data = http_get(
        TICKERS_URL
    )

    result = {}

    if not isinstance(data, dict):
        return result

    items = data.get("tickers")

    if not isinstance(items, list):
        return result

    for item in items:

        if not isinstance(item, dict):
            continue

        symbol = (
            item.get("symbol")
            or item.get("instrument")
        )

        if not symbol:
            continue

        price = (
            item.get("last")
            or item.get("lastPrice")
            or item.get("price")
        )

        try:
            result[str(symbol)] = float(price)
        except Exception:
            continue

    return result


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    open_trades = get_open_trades()

    if not open_trades:
        return

    prices = get_live_price_map()

    for trade in open_trades:

        symbol = trade["symbol"]

        df5 = fetch_ohlcv(
            symbol,
            TF_5M
        )

        if df5 is None or len(df5) == 0:
            continue

        candle = df5.iloc[-1]

        high = float(candle["high"])

        low = float(candle["low"])

        entry = float(
            trade["entry"]
        )

        sl = float(
            trade["sl"]
        )

        tp = float(
            trade["tp"]
        )

        side = trade["side"]

        exit_price = None

        result = None

        reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "LONG":

            sl_hit = (
                low <= sl
            )

            tp_hit = (
                high >= tp
            )

            # SL first if both touched
            if sl_hit:

                exit_price = sl

                result = "LOSS"

                reason = "SL"

            elif tp_hit:

                exit_price = tp

                result = "WIN"

                reason = "TP"

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        elif side == "SHORT":

            sl_hit = (
                high >= sl
            )

            tp_hit = (
                low <= tp
            )

            if sl_hit:

                exit_price = sl

                result = "LOSS"

                reason = "SL"

            elif tp_hit:

                exit_price = tp

                result = "WIN"

                reason = "TP"

        if result is None:
            continue

        # ----------------------------------------------------
        # PNL %
        # ----------------------------------------------------

        if side == "LONG":

            pnl = (
                (exit_price - entry)
                / entry
            ) * 100

        else:

            pnl = (
                (entry - exit_price)
                / entry
            ) * 100

        close_trade(
            trade["id"],
            result,
            pnl,
            exit_price,
            reason
        )


# ============================================================
# MAX OPEN CLEANUP
# ============================================================

def enforce_max_open_trades():

    open_trades = get_open_trades()

    if len(open_trades) <= MAX_OPEN_TRADES:
        return

    # --------------------------------------------------------
    # Keep newest MAX_OPEN_TRADES
    # --------------------------------------------------------

    open_trades = sorted(
        open_trades,
        key=lambda x: x["id"],
        reverse=True
    )

    excess = open_trades[
        MAX_OPEN_TRADES:
    ]

    conn = db_connect()

    cur = conn.cursor()

    for trade in excess:

        cur.execute("""
            UPDATE trades
            SET
                status = 'CLOSED',
                result = 'REMOVED',
                pnl = 0,
                exit_time = ?,
                exit_reason = 'ADMIN_MAX_OPEN',
                closed_reported = 1
            WHERE id = ?
            AND status = 'OPEN'
        """, (
            utc_now().isoformat(),
            trade["id"]
        ))

    conn.commit()

    conn.close()


# ============================================================
# PERFORMANCE
# ============================================================

def get_stats():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """)

    open_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result IN ('WIN','LOSS')
    """)

    closed_count = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'WIN'
    """)

    wins = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
        AND result = 'LOSS'
    """)

    losses = cur.fetchone()[0]

    cur.execute("""
        SELECT COALESCE(SUM(pnl), 0)
        FROM trades
        WHERE status = 'CLOSED'
        AND result IN ('WIN','LOSS')
    """)

    realized_pnl = cur.fetchone()[0]

    conn.close()

    if closed_count > 0:
        win_rate = (
            wins / closed_count
        ) * 100
    else:
        win_rate = 0

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "realized_pnl": realized_pnl
    }


# ============================================================
# UNREPORTED CLOSED
# ============================================================

def get_unreported_closed():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            result,
            pnl,
            entry_time,
            exit_time,
            exit_reason
        FROM trades
        WHERE status = 'CLOSED'
        AND closed_reported = 0
        ORDER BY id ASC
    """)

    rows = cur.fetchall()

    conn.close()

    columns = [
        "id",
        "symbol",
        "side",
        "setup",
        "entry",
        "sl",
        "tp",
        "rr",
        "score",
        "result",
        "pnl",
        "entry_time",
        "exit_time",
        "exit_reason"
    ]

    return [
        dict(zip(columns, row))
        for row in rows
    ]


# ============================================================
# MARK CLOSED REPORTED
# ============================================================

def mark_closed_reported(
    trade_ids
):

    if not trade_ids:
        return

    conn = db_connect()

    cur = conn.cursor()

    for trade_id in trade_ids:

        cur.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE id = ?
            AND status = 'CLOSED'
        """, (trade_id,))

    conn.commit()

    conn.close()


# ============================================================
# SIGNAL COOLDOWN DISPLAY
# ============================================================

def last_signal_time():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        SELECT entry_time
        FROM trades
        WHERE status IN ('OPEN','CLOSED')
        AND result != 'REMOVED'
        ORDER BY id DESC
        LIMIT 1
    """)

    row = cur.fetchone()

    conn.close()

    if not row:
        return None

    try:
        return datetime.fromisoformat(
            row[0]
        )
    except Exception:
        return None


# ============================================================
# DIAGNOSTIC FORMAT
# ============================================================

def diagnostic_text(diag):

    text = []

    text.append(
        "🔬 DIAGNOSTIC"
    )

    text.append(
        f"Markets {diag['markets']} | "
        f"Data OK {diag['data_ok']}"
    )

    text.append(
        f"Trend {diag['trend']} | "
        f"Setup {diag['setup']}"
    )

    text.append(
        f"Confirmation {diag['confirmation']} | "
        f"Valid SL/TP {diag['valid_risk']}"
    )

    text.append(
        f"Score ≥ {MIN_SIGNAL_SCORE}: "
        f"{diag['score_pass']} | "
        f"Final: {diag['final']}"
    )

    text.append(
        f"Neutral {diag['neutral']} | "
        f"No Setup {diag['no_setup']}"
    )

    text.append(
        f"No 5M Confirm {diag['no_confirmation']} | "
        f"Invalid SL/TP {diag['invalid_risk']}"
    )

    text.append(
        f"Low Score {diag['low_score']} | "
        f"Data Errors {diag['data_errors']}"
    )

    # --------------------------------------------------------
    # Risk rejection reasons
    # --------------------------------------------------------

    risk_reasons = []

    for reason, count in sorted(
        diag["reasons"].items(),
        key=lambda x: x[1],
        reverse=True
    ):

        if reason in (
            "DATA",
            "RVOL",
            "EXCEPTION",
            "SCORE"
        ):
            continue

        risk_reasons.append(
            f"{reason}:{count}"
        )

    if risk_reasons:

        text.append(
            "Risk Reject: "
            + " | ".join(risk_reasons)
        )

    return "\n".join(text)


# ============================================================
# TELEGRAM
# ============================================================

def send_telegram(message):

    if not TELEGRAM_BOT_TOKEN:
        return False

    if not TELEGRAM_CHAT_ID:
        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "disable_web_page_preview": True
    }

    try:

        response = SESSION.post(
            url,
            data=payload,
            timeout=20
        )

        if response.status_code != 200:
            return False

        data = response.json()

        return bool(
            data.get("ok")
        )

    except Exception:
        return False


# ============================================================
# REPORT
# ============================================================

def build_report(
    markets_count,
    saved_signals,
    diag
):

    stats = get_stats()

    closed = get_unreported_closed()

    open_trades = get_open_trades()

    prices = get_live_price_map()

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {fmt_time()}"
    )

    lines.append(
        "⚡ Kraken Futures | 5M CLOSED"
    )

    lines.append(
        f"🔎 Markets: {markets_count} | "
        f"Signals: {len(saved_signals)}"
    )

    lines.append("")

    lines.append(
        "📈 PERFORMANCE"
    )

    lines.append(
        f"Open {stats['open']}/{MAX_OPEN_TRADES} | "
        f"Closed {stats['closed']}"
    )

    lines.append(
        f"W {stats['wins']} | "
        f"L {stats['losses']} | "
        f"WR {stats['win_rate']:.1f}%"
    )

    lines.append(
        f"Realized PnL "
        f"{stats['realized_pnl']:+.3f}%"
    )

    lines.append("")

    lines.append(
        diagnostic_text(diag)
    )

    # --------------------------------------------------------
    # NEW SIGNALS
    # --------------------------------------------------------

    if saved_signals:

        lines.append("")

        lines.append(
            "🚨 NEW SIGNALS"
        )

        for trade in saved_signals:

            side_icon = (
                "🟢"
                if trade["side"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{side_icon} "
                f"{trade['symbol']} "
                f"{trade['side']}"
            )

            lines.append(
                f"Setup {trade['setup']} | "
                f"Score {trade['score']}/15 | "
                f"RVOL {trade['rvol']:.2f}"
            )

            lines.append(
                f"Entry {fmt_num(trade['entry'])}"
            )

            lines.append(
                f"SL {fmt_num(trade['sl'])} | "
                f"TP {fmt_num(trade['tp'])} | "
                f"RR 1:{trade['rr']:.2f}"
            )

    # --------------------------------------------------------
    # CLOSED
    # --------------------------------------------------------

    if closed:

        lines.append("")

        lines.append(
            "🔔 CLOSED"
        )

        for trade in closed:

            icon = (
                "✅"
                if trade["result"] == "WIN"
                else "❌"
            )

            exit_price = (
                trade["sl"]
                if trade["exit_reason"] == "SL"
                else trade["tp"]
            )

            lines.append(
                f"{icon} "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"{trade['result']} "
                f"{trade['pnl']:+.2f}%"
            )

            lines.append(
                f"Entry {fmt_num(trade['entry'])} "
                f"→ Exit {fmt_num(exit_price)}"
            )

    # --------------------------------------------------------
    # OPEN
    # --------------------------------------------------------

    if open_trades:

        lines.append("")

        lines.append(
            "📂 OPEN TRADES"
        )

        for trade in open_trades:

            entry = float(
                trade["entry"]
            )

            live = prices.get(
                trade["symbol"],
                entry
            )

            if trade["side"] == "LONG":

                pnl = (
                    (live - entry)
                    / entry
                ) * 100

                icon = "🟢" if pnl >= 0 else "🔴"

            else:

                pnl = (
                    (entry - live)
                    / entry
                ) * 100

                icon = "🟢" if pnl >= 0 else "🔴"

            lines.append(
                f"{icon} "
                f"{trade['symbol']} "
                f"{trade['side']} "
                f"PnL {pnl:+.2f}%"
            )

            lines.append(
                f"Entry {fmt_num(entry)} | "
                f"Now {fmt_num(live)}"
            )

            lines.append(
                f"SL {fmt_num(trade['sl'])} | "
                f"TP {fmt_num(trade['tp'])} | "
                f"RR 1:{float(trade['rr']):.2f}"
            )

    lines.append("")

    if PAPER_TRADING:
        lines.append(
            "🧪 PAPER TRADING"
        )
    else:
        lines.append(
            "⚠️ LIVE TRADING"
        )

    return "\n".join(lines)


# ============================================================
# FIX PLACEHOLDER
# ============================================================
#
# Kept only to prevent accidental use of an old helper.
# Actual entry is always taken from 5M close.
#

def df5_last_close_placeholder(df):
    return float(
        df["close"].iloc[-1]
    )


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        "=================================================="
    )

    print(
        "VOLUME-KHAT 100 v3.0"
    )

    print(
        "Kraken Futures | 5M CLOSED"
    )

    print(
        "=================================================="
    )

    # --------------------------------------------------------
    # DB
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # UPDATE OPEN
    # --------------------------------------------------------

    try:
        update_open_trades()
    except Exception as e:
        print(
            f"Open trade update error: {e}"
        )

    # --------------------------------------------------------
    # MAX OPEN
    # --------------------------------------------------------

    try:
        enforce_max_open_trades()
    except Exception as e:
        print(
            f"Max open cleanup error: {e}"
        )

    # --------------------------------------------------------
    # MARKETS
    # --------------------------------------------------------

    markets = get_top_markets()

    if not markets:

        diag = new_diagnostics()

        report = build_report(
            0,
            [],
            diag
        )

        send_telegram(report)

        print(report)

        return

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    candidates, diag = scan_all_markets(
        markets
    )

    # --------------------------------------------------------
    # AVAILABLE SLOTS
    # --------------------------------------------------------

    open_count = len(
        get_open_trades()
    )

    available_slots = max(
        0,
        MAX_OPEN_TRADES - open_count
    )

    # --------------------------------------------------------
    # SAVE BEST SIGNALS
    # --------------------------------------------------------

    saved_signals = []

    if available_slots > 0:

        for trade in candidates:

            if len(saved_signals) >= min(
                available_slots,
                MAX_NEW_SIGNALS
            ):
                break

            saved = save_signal(
                trade
            )

            if saved:

                saved_signals.append(
                    trade
                )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    report = build_report(
        len(markets),
        saved_signals,
        diag
    )

    print(report)

    telegram_ok = send_telegram(
        report
    )

    # --------------------------------------------------------
    # CLOSED REPORTING
    #
    # A CLOSED trade appears only once after successful
    # Telegram delivery.
    #
    # If Telegram fails, it remains unreported and will
    # appear in the next successful report, preventing
    # loss of closure information.
    # --------------------------------------------------------

    if telegram_ok:

        closed = get_unreported_closed()

        if closed:

            mark_closed_reported(
                [
                    x["id"]
                    for x in closed
                ]
            )

    print(
        "=================================================="
    )

    print(
        f"Candidates: {len(candidates)}"
    )

    print(
        f"Saved Signals: {len(saved_signals)}"
    )

    print(
        f"Telegram: {'OK' if telegram_ok else 'FAILED'}"
    )

    print(
        "=================================================="
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    main()
