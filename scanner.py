# ============================================================
# KRAKEN FUTURES VOLUME-KHAT 100 v7.8
# ============================================================
#
# 1H  = TREND + TREND STRENGTH
# 15M = SUPPORT / RESISTANCE ZONE + PRICE REACTION
# 5M  = BREAKOUT -> RETEST -> CONFIRMATION
# VOL = RVOL CONFIRMATION
#
# CLOSED CANDLES ONLY
#
# v7.8
# ------------------------------------------------------------
# FEATURES
#
# - Kraken Futures candle endpoint
# - /api/charts/v1/trade/{symbol}/{resolution}
# - 1h / 15m / 5m
# - 15M Zone + Reaction
# - Sequential 5M Breakout -> Retest -> Confirmation
# - Breakout can happen several candles before confirmation
# - Retest uses actual breakout level
# - S/R based TP
# - TP must be valid S/R
# - RR > 1.00
# - TP <= 3.00%
# - SL 0.50% - 1.50%
# - Max 3 open trades
# - Max 3 new signals
# - Persistent SQLite
# - Closed trades never deleted
# - DB close operation verified
# - Compact Telegram
# - TOP CANDIDATE shown when there is no new signal
# - Rejection reason shown for TOP CANDIDATE
# - Diagnostics kept in GitHub Actions
# - Real trading DISABLED
# ============================================================

import os
import sqlite3
import time
from datetime import datetime, timezone

import requests
import numpy as np


# ============================================================
# VERSION
# ============================================================

VERSION = "v7.8"


# ============================================================
# API
# ============================================================

BASE_URL = "https://futures.kraken.com"

TICKERS_URL = (
    f"{BASE_URL}/derivatives/api/v3/tickers"
)

CHARTS_URL = (
    f"{BASE_URL}/api/charts/v1"
)

CANDLE_TICK_TYPE = "trade"

REQUEST_TIMEOUT = 15


# ============================================================
# TELEGRAM
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


# ============================================================
# DATABASE
# ============================================================

DB_FILE = "volume_khat_100_v78.db"


# ============================================================
# GENERAL
# ============================================================

TOP_N = 100

MAX_OPEN_TRADES = 3

MAX_NEW_SIGNALS = 3

PAPER_TRADING = True

COOLDOWN_MINUTES = 15


# ============================================================
# TIMEFRAMES
# ============================================================

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

TF_SECONDS = {
    "1h": 3600,
    "15m": 900,
    "5m": 300,
}


# ============================================================
# 1H TREND
# ============================================================

MA_FAST = 20
MA_SLOW = 50

MIN_TREND_SCORE = 3

TREND_LOOKBACK = 8


# ============================================================
# S/R
# ============================================================

SR_LOOKBACK_15M = 80
SR_LOOKBACK_1H = 80

PIVOT_LEFT = 2
PIVOT_RIGHT = 2

ZONE_TOLERANCE_PCT = 0.35


# ============================================================
# 15M SETUP
# ============================================================

SETUP_MIN_SCORE = 3
SETUP_MAX_SCORE = 4

SETUP_LOOKBACK = 5

SETUP_BODY_MIN_PCT = 0.12

SETUP_REJECTION_DISTANCE_PCT = 0.80

SETUP_WICK_BODY_RATIO = 0.90

SETUP_STRUCTURE_LOOKBACK = 5

SETUP_BREAKOUT_BUFFER_PCT = 0.10


# ============================================================
# 5M SEQUENTIAL CONFIRMATION
# ============================================================

BREAKOUT_LOOKBACK = 6

RETEST_WINDOW = 8

CONFIRMATION_WINDOW = 4

PULLBACK_TOLERANCE_PCT = 0.50

PULLBACK_MAX_DISTANCE_PCT = 0.80

MIN_BODY_PCT = 0.15

WICK_BODY_RATIO = 1.00

CONFIRMATION_MIN_SCORE = 3

CONFIRMATION_MAX_SCORE = 4

BREAKOUT_BUFFER_PCT = 0.10


# ============================================================
# RVOL
# ============================================================

RVOL_LOOKBACK = 20

RVOL_NORMAL = 1.50

RVOL_STRONG = 2.20

RVOL_VERY_STRONG = 3.00


# ============================================================
# SCORE
# ============================================================

MIN_SCORE = 9


# ============================================================
# RISK
# ============================================================

MIN_SL_PCT = 0.50

MAX_SL_PCT = 1.50

MIN_RR = 1.00

MAX_TP_PCT = 3.00

ATR_PERIOD = 14

ATR_MULTIPLIER = 1.20

ATR_BUFFER_PCT = 0.20


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "scanned": 0,

    "trend_long": 0,
    "trend_short": 0,
    "trend_neutral": 0,

    "data_fail": 0,
    "sr_fail": 0,

    "setup_pass": 0,
    "setup_fail": 0,

    "confirm_pass": 0,
    "confirm_fail": 0,

    "confirm_no_breakout": 0,
    "confirm_no_retest": 0,
    "confirm_no_confirmation": 0,

    "rvol_normal": 0,
    "rvol_strong": 0,
    "rvol_very_strong": 0,

    "score_checked": 0,
    "score_pass": 0,
    "score_fail": 0,

    "sl_pass": 0,
    "sl_fail": 0,

    "tp_pass": 0,
    "tp_fail": 0,

    "rr_pass": 0,
    "rr_fail": 0,

    "cooldown": 0,

    "db_close_ok": 0,
    "db_close_fail": 0,

    "errors": 0,
}


# ============================================================
# TOP CANDIDATE
# ============================================================

TOP_CANDIDATE = None


# ============================================================
# HTTP SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent":
        "VOLUME-KHAT-100/" + VERSION
})


# ============================================================
# UTILS
# ============================================================

def utc_now_ts():

    return int(time.time())


def utc_string(ts=None):

    if ts is None:
        ts = time.time()

    return datetime.fromtimestamp(
        ts,
        tz=timezone.utc
    ).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)

    except Exception:
        return default


def candle_body(c):

    return abs(
        c["close"] - c["open"]
    )


def body_pct(c):

    if c["open"] == 0:
        return 0.0

    return (
        abs(
            c["close"] - c["open"]
        )
        / c["open"]
        * 100.0
    )


def upper_wick(c):

    return (
        c["high"]
        -
        max(
            c["open"],
            c["close"]
        )
    )


def lower_wick(c):

    return (
        min(
            c["open"],
            c["close"]
        )
        -
        c["low"]
    )


def is_bullish(c):

    return c["close"] > c["open"]


def is_bearish(c):

    return c["close"] < c["open"]


def near_level(
    price,
    level,
    tolerance_pct
):

    if level <= 0:
        return False

    return (
        abs(price - level)
        / level
        * 100.0
        <= tolerance_pct
    )


# ============================================================
# SMA
# ============================================================

def sma(
    values,
    period
):

    if len(values) < period:
        return None

    return float(
        np.mean(
            values[-period:]
        )
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    candles,
    period=14
):

    if len(candles) < period + 1:
        return None

    trs = []

    for i in range(
        1,
        len(candles)
    ):

        high = candles[i]["high"]
        low = candles[i]["low"]
        prev_close = candles[i - 1]["close"]

        tr = max(
            high - low,
            abs(high - prev_close),
            abs(low - prev_close)
        )

        trs.append(tr)

    if len(trs) < period:
        return None

    return float(
        np.mean(
            trs[-period:]
        )
    )


# ============================================================
# API: TOP SYMBOLS
# ============================================================

def get_top_symbols():

    try:

        r = SESSION.get(
            TICKERS_URL,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        tickers = data.get(
            "tickers",
            []
        )

        rows = []

        for item in tickers:

            symbol = str(
                item.get(
                    "symbol",
                    ""
                )
            ).upper()

            if not symbol:
                continue

            if not (
                symbol.startswith("PF_")
                or
                symbol.startswith("PI_")
            ):
                continue

            if not symbol.endswith("USD"):
                continue

            tag = str(
                item.get(
                    "tag",
                    ""
                )
            ).lower()

            if (
                tag
                and
                "perpetual" not in tag
            ):
                continue

            volume = safe_float(
                item.get("vol24h")
                or item.get("volume24h")
                or item.get("volume")
            )

            price = safe_float(
                item.get("last")
                or item.get("markPrice")
                or item.get("price")
            )

            if volume <= 0:
                continue

            rows.append({
                "symbol": symbol,
                "volume": volume,
                "price": price,
            })

        rows.sort(
            key=lambda x: x["volume"],
            reverse=True
        )

        return rows[:TOP_N]

    except Exception as e:

        print(
            f"[ERROR] get_top_symbols: {e}"
        )

        DIAG["errors"] += 1

        return []


# ============================================================
# API: CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit=180
):

    try:

        url = (
            f"{CHARTS_URL}/"
            f"{CANDLE_TICK_TYPE}/"
            f"{symbol}/"
            f"{resolution}"
        )

        r = SESSION.get(
            url,
            timeout=REQUEST_TIMEOUT
        )

        if r.status_code != 200:

            print(
                f"[CANDLE ERROR] "
                f"{symbol} {resolution} "
                f"HTTP {r.status_code}"
            )

            DIAG["data_fail"] += 1

            return None

        data = r.json()

        raw = data.get(
            "candles",
            []
        )

        if not raw:

            raw = (
                data.get(
                    "result",
                    {}
                ).get(
                    "candles",
                    []
                )
            )

        candles = []

        for item in raw:

            try:

                if isinstance(
                    item,
                    dict
                ):

                    ts = (
                        item.get("time")
                        or item.get("timestamp")
                    )

                    o = (
                        item.get("open")
                        or item.get("o")
                    )

                    h = (
                        item.get("high")
                        or item.get("h")
                    )

                    l = (
                        item.get("low")
                        or item.get("l")
                    )

                    c = (
                        item.get("close")
                        or item.get("c")
                    )

                    v = (
                        item.get("volume")
                        or item.get("v")
                        or 0
                    )

                else:

                    if len(item) < 6:
                        continue

                    ts = item[0]
                    o = item[1]
                    h = item[2]
                    l = item[3]
                    c = item[4]
                    v = item[5]

                ts = safe_float(ts)

                if ts > 10_000_000_000:
                    ts /= 1000.0

                candles.append({
                    "time": int(ts),
                    "open": safe_float(o),
                    "high": safe_float(h),
                    "low": safe_float(l),
                    "close": safe_float(c),
                    "volume": safe_float(v),
                })

            except Exception:
                continue

        candles.sort(
            key=lambda x: x["time"]
        )

        now = utc_now_ts()

        interval = TF_SECONDS.get(
            resolution,
            300
        )

        closed = []

        for c in candles:

            if (
                c["time"]
                + interval
                <= now
            ):
                closed.append(c)

        if len(closed) > limit:

            closed = closed[-limit:]

        if len(closed) < 30:

            DIAG["data_fail"] += 1

            return None

        return closed

    except Exception as e:

        print(
            f"[ERROR] candles "
            f"{symbol} {resolution}: {e}"
        )

        DIAG["errors"] += 1

        return None


# ============================================================
# PIVOTS
# ============================================================

def find_pivots(
    candles,
    left=2,
    right=2
):

    supports = []
    resistances = []

    if len(candles) < (
        left + right + 5
    ):

        return (
            supports,
            resistances
        )

    for i in range(
        left,
        len(candles) - right
    ):

        low = candles[i]["low"]
        high = candles[i]["high"]

        left_lows = [
            candles[j]["low"]
            for j in range(
                i - left,
                i
            )
        ]

        right_lows = [
            candles[j]["low"]
            for j in range(
                i + 1,
                i + right + 1
            )
        ]

        left_highs = [
            candles[j]["high"]
            for j in range(
                i - left,
                i
            )
        ]

        right_highs = [
            candles[j]["high"]
            for j in range(
                i + 1,
                i + right + 1
            )
        ]

        if (
            low <= min(left_lows)
            and
            low <= min(right_lows)
        ):

            supports.append(low)

        if (
            high >= max(left_highs)
            and
            high >= max(right_highs)
        ):

            resistances.append(high)

    return (
        supports,
        resistances
    )


# ============================================================
# BUILD S/R
# ============================================================

def build_sr(
    candles_15m,
    candles_1h
):

    s15, r15 = find_pivots(
        candles_15m[
            -SR_LOOKBACK_15M:
        ],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    s1h, r1h = find_pivots(
        candles_1h[
            -SR_LOOKBACK_1H:
        ],
        PIVOT_LEFT,
        PIVOT_RIGHT
    )

    supports = sorted(
        set(s15 + s1h)
    )

    resistances = sorted(
        set(r15 + r1h)
    )

    if (
        not supports
        and
        not resistances
    ):

        return None

    return {
        "supports": supports,
        "resistances": resistances,
    }


# ============================================================
# TREND
# ============================================================

def get_trend(candles):

    if len(candles) < (
        MA_SLOW
        + TREND_LOOKBACK
    ):

        return (
            "NEUTRAL",
            0
        )

    closes = [
        c["close"]
        for c in candles
    ]

    fast = sma(
        closes,
        MA_FAST
    )

    slow = sma(
        closes,
        MA_SLOW
    )

    if (
        fast is None
        or
        slow is None
    ):

        return (
            "NEUTRAL",
            0
        )

    fast_prev = sma(
        closes[:-TREND_LOOKBACK],
        MA_FAST
    )

    slow_prev = sma(
        closes[:-TREND_LOOKBACK],
        MA_SLOW
    )

    price = closes[-1]

    score_long = 0
    score_short = 0

    if price > fast:
        score_long += 1

    if price < fast:
        score_short += 1

    if fast > slow:
        score_long += 1

    if fast < slow:
        score_short += 1

    if (
        fast_prev is not None
        and
        fast > fast_prev
    ):

        score_long += 1

    if (
        fast_prev is not None
        and
        fast < fast_prev
    ):

        score_short += 1

    if (
        slow_prev is not None
        and
        slow > slow_prev
    ):

        score_long += 1

    if (
        slow_prev is not None
        and
        slow < slow_prev
    ):

        score_short += 1

    recent = candles[-6:]

    if (
        recent[-1]["close"]
        >
        recent[0]["close"]
    ):

        score_long += 1

    if (
        recent[-1]["close"]
        <
        recent[0]["close"]
    ):

        score_short += 1

    score_long = min(
        score_long,
        5
    )

    score_short = min(
        score_short,
        5
    )

    if (
        score_long >= MIN_TREND_SCORE
        and
        score_long > score_short
    ):

        return (
            "LONG",
            score_long
        )

    if (
        score_short >= MIN_TREND_SCORE
        and
        score_short > score_long
    ):

        return (
            "SHORT",
            score_short
        )

    return (
        "NEUTRAL",
        max(
            score_long,
            score_short
        )
    )


# ============================================================
# NEAREST S/R
# ============================================================

def nearest_support(
    price,
    supports
):

    levels = [
        x
        for x in supports
        if x < price
    ]

    if not levels:
        return None

    return max(levels)


def nearest_resistance(
    price,
    resistances
):

    levels = [
        x
        for x in resistances
        if x > price
    ]

    if not levels:
        return None

    return min(levels)


# ============================================================
# 15M SETUP
# ============================================================

def analyze_15m_setup(
    candles,
    sr,
    direction
):

    if len(candles) < 20:

        return {
            "valid": False,
            "score": 0,
            "reason": "INSUFFICIENT_15M_DATA",
        }

    last = candles[-1]

    price = last["close"]

    supports = sr["supports"]
    resistances = sr["resistances"]

    score = 0

    reasons = []

    if direction == "LONG":

        resistance = nearest_resistance(
            price,
            resistances
        )

        near_support = any(
            near_level(
                price,
                level,
                ZONE_TOLERANCE_PCT
            )
            for level in supports
        )

        if near_support:

            score += 1
            reasons.append(
                "SUPPORT_ZONE"
            )

        body = candle_body(last)
        lw = lower_wick(last)

        bullish_reaction = (
            is_bullish(last)
            and
            body > 0
            and
            lw / body
            >= SETUP_WICK_BODY_RATIO
            and
            body_pct(last)
            >= SETUP_BODY_MIN_PCT
        )

        if bullish_reaction:

            score += 1
            reasons.append(
                "BULLISH_REACTION"
            )

        recent = candles[
            -SETUP_STRUCTURE_LOOKBACK:
        ]

        if len(recent) >= 4:

            lows = [
                c["low"]
                for c in recent
            ]

            if lows[-1] > lows[0]:

                score += 1
                reasons.append(
                    "HIGHER_LOW"
                )

        if resistance is not None:

            previous = candles[-2]

            breakout = (
                previous["close"]
                <= resistance
                and
                last["close"]
                >
                resistance
                * (
                    1
                    +
                    SETUP_BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

            if breakout:

                score += 2
                reasons.append(
                    "RESISTANCE_BREAKOUT"
                )

        if len(candles) >= 3:

            c1 = candles[-3]
            c2 = candles[-2]
            c3 = candles[-1]

            if (
                c1["close"]
                <
                c2["close"]
                <=
                c3["close"]
            ):

                score += 1
                reasons.append(
                    "BULLISH_STRUCTURE"
                )

    elif direction == "SHORT":

        support = nearest_support(
            price,
            supports
        )

        near_resistance = any(
            near_level(
                price,
                level,
                ZONE_TOLERANCE_PCT
            )
            for level in resistances
        )

        if near_resistance:

            score += 1
            reasons.append(
                "RESISTANCE_ZONE"
            )

        body = candle_body(last)
        uw = upper_wick(last)

        bearish_reaction = (
            is_bearish(last)
            and
            body > 0
            and
            uw / body
            >= SETUP_WICK_BODY_RATIO
            and
            body_pct(last)
            >= SETUP_BODY_MIN_PCT
        )

        if bearish_reaction:

            score += 1
            reasons.append(
                "BEARISH_REACTION"
            )

        recent = candles[
            -SETUP_STRUCTURE_LOOKBACK:
        ]

        if len(recent) >= 4:

            highs = [
                c["high"]
                for c in recent
            ]

            if highs[-1] < highs[0]:

                score += 1
                reasons.append(
                    "LOWER_HIGH"
                )

        if support is not None:

            previous = candles[-2]

            breakdown = (
                previous["close"]
                >= support
                and
                last["close"]
                <
                support
                * (
                    1
                    -
                    SETUP_BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

            if breakdown:

                score += 2
                reasons.append(
                    "SUPPORT_BREAKDOWN"
                )

        if len(candles) >= 3:

            c1 = candles[-3]
            c2 = candles[-2]
            c3 = candles[-1]

            if (
                c1["close"]
                >
                c2["close"]
                >=
                c3["close"]
            ):

                score += 1
                reasons.append(
                    "BEARISH_STRUCTURE"
                )

    score = min(
        score,
        SETUP_MAX_SCORE
    )

    valid = (
        score >= SETUP_MIN_SCORE
    )

    if valid:

        reason = (
            ",".join(reasons)
            if reasons
            else
            "ZONE_REACTION"
        )

    else:

        reason = (
            f"15M SETUP SCORE "
            f"{score} < "
            f"{SETUP_MIN_SCORE}"
        )

    return {
        "valid": valid,
        "score": score,
        "reason": reason,
    }


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirmation_candle_long(c):

    body = candle_body(c)

    lw = lower_wick(c)

    return (
        is_bullish(c)
        and
        body > 0
        and
        body_pct(c)
        >= MIN_BODY_PCT
        and
        lw / body
        <= WICK_BODY_RATIO
    )


def confirmation_candle_short(c):

    body = candle_body(c)

    uw = upper_wick(c)

    return (
        is_bearish(c)
        and
        body > 0
        and
        body_pct(c)
        >= MIN_BODY_PCT
        and
        uw / body
        <= WICK_BODY_RATIO
    )


def analyze_5m_confirmation(
    candles,
    direction
):

    minimum = (
        BREAKOUT_LOOKBACK
        +
        RETEST_WINDOW
        +
        3
    )

    if len(candles) < minimum:

        return {
            "valid": False,
            "score": 0,
            "reason": "5M INSUFFICIENT_DATA",
        }

    latest_index = len(candles) - 1

    sequence_start = max(
        BREAKOUT_LOOKBACK,
        latest_index
        -
        CONFIRMATION_WINDOW
        -
        RETEST_WINDOW
        -
        2
    )

    if direction == "LONG":

        found_breakout = False
        found_retest = False

        for breakout_idx in range(
            sequence_start,
            latest_index - 1
        ):

            base = candles[
                breakout_idx
                -
                BREAKOUT_LOOKBACK:
                breakout_idx
            ]

            if len(base) < BREAKOUT_LOOKBACK:
                continue

            breakout_level = max(
                c["high"]
                for c in base
            )

            breakout_price = (
                breakout_level
                *
                (
                    1
                    +
                    BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

            breakout_candle = candles[
                breakout_idx
            ]

            if (
                breakout_candle["close"]
                <= breakout_price
            ):
                continue

            found_breakout = True

            retest_start = (
                breakout_idx + 1
            )

            retest_end = min(
                latest_index - 1,
                breakout_idx
                + RETEST_WINDOW
            )

            retest_idx = None

            for i in range(
                retest_start,
                retest_end + 1
            ):

                c = candles[i]

                distance = (
                    abs(
                        c["low"]
                        -
                        breakout_level
                    )
                    /
                    breakout_level
                    *
                    100
                )

                touches_level = (
                    c["low"]
                    <=
                    breakout_level
                    *
                    (
                        1
                        +
                        PULLBACK_TOLERANCE_PCT
                        / 100
                    )
                )

                not_too_far = (
                    distance
                    <=
                    PULLBACK_MAX_DISTANCE_PCT
                )

                holds_level = (
                    c["close"]
                    >=
                    breakout_level
                    *
                    (
                        1
                        -
                        PULLBACK_TOLERANCE_PCT
                        / 100
                    )
                )

                if (
                    touches_level
                    and
                    not_too_far
                    and
                    holds_level
                ):

                    retest_idx = i

                    found_retest = True

                    break

            if retest_idx is None:
                continue

            confirm_start = (
                retest_idx + 1
            )

            confirm_end = min(
                latest_index,
                retest_idx
                +
                CONFIRMATION_WINDOW
            )

            for i in range(
                confirm_start,
                confirm_end + 1
            ):

                if i != latest_index:
                    continue

                c = candles[i]

                score = 0
                reasons = []

                if confirmation_candle_long(c):

                    score += 1

                    reasons.append(
                        "BULLISH_CONFIRM"
                    )

                if (
                    c["close"]
                    >
                    breakout_level
                ):

                    score += 1

                    reasons.append(
                        "CLOSE_ABOVE_BREAKOUT"
                    )

                if (
                    i > 0
                    and
                    c["close"]
                    >
                    candles[i - 1]["high"]
                ):

                    score += 1

                    reasons.append(
                        "HIGHER_CLOSE"
                    )

                if (
                    i > 0
                    and
                    c["low"]
                    >=
                    candles[i - 1]["low"]
                ):

                    score += 1

                    reasons.append(
                        "HOLDING_LOW"
                    )

                score = min(
                    score,
                    CONFIRMATION_MAX_SCORE
                )

                if (
                    score
                    >=
                    CONFIRMATION_MIN_SCORE
                ):

                    return {
                        "valid": True,
                        "score": score,
                        "reason":
                            "BREAKOUT,RETEST,"
                            + ",".join(reasons),
                        "breakout_level":
                            breakout_level,
                        "breakout_index":
                            breakout_idx,
                        "retest_index":
                            retest_idx,
                        "confirmation_index":
                            i,
                    }

        if not found_breakout:

            DIAG[
                "confirm_no_breakout"
            ] += 1

            return {
                "valid": False,
                "score": 0,
                "reason":
                    "5M NO BREAKOUT",
            }

        if not found_retest:

            DIAG[
                "confirm_no_retest"
            ] += 1

            return {
                "valid": False,
                "score": 1,
                "reason":
                    "5M BREAKOUT WITHOUT RETEST",
            }

        DIAG[
            "confirm_no_confirmation"
        ] += 1

        return {
            "valid": False,
            "score": 2,
            "reason":
                "5M RETEST WITHOUT CONFIRMATION",
        }

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        found_breakdown = False
        found_retest = False

        for breakdown_idx in range(
            sequence_start,
            latest_index - 1
        ):

            base = candles[
                breakdown_idx
                -
                BREAKOUT_LOOKBACK:
                breakdown_idx
            ]

            if len(base) < BREAKOUT_LOOKBACK:
                continue

            breakdown_level = min(
                c["low"]
                for c in base
            )

            breakdown_price = (
                breakdown_level
                *
                (
                    1
                    -
                    BREAKOUT_BUFFER_PCT
                    / 100
                )
            )

            breakdown_candle = candles[
                breakdown_idx
            ]

            if (
                breakdown_candle["close"]
                >= breakdown_price
            ):
                continue

            found_breakdown = True

            retest_start = (
                breakdown_idx + 1
            )

            retest_end = min(
                latest_index - 1,
                breakdown_idx
                + RETEST_WINDOW
            )

            retest_idx = None

            for i in range(
                retest_start,
                retest_end + 1
            ):

                c = candles[i]

                distance = (
                    abs(
                        c["high"]
                        -
                        breakdown_level
                    )
                    /
                    breakdown_level
                    *
                    100
                )

                touches_level = (
                    c["high"]
                    >=
                    breakdown_level
                    *
                    (
                        1
                        -
                        PULLBACK_TOLERANCE_PCT
                        / 100
                    )
                )

                not_too_far = (
                    distance
                    <=
                    PULLBACK_MAX_DISTANCE_PCT
                )

                holds_level = (
                    c["close"]
                    <=
                    breakdown_level
                    *
                    (
                        1
                        +
                        PULLBACK_TOLERANCE_PCT
                        / 100
                    )
                )

                if (
                    touches_level
                    and
                    not_too_far
                    and
                    holds_level
                ):

                    retest_idx = i

                    found_retest = True

                    break

            if retest_idx is None:
                continue

            confirm_start = (
                retest_idx + 1
            )

            confirm_end = min(
                latest_index,
                retest_idx
                +
                CONFIRMATION_WINDOW
            )

            for i in range(
                confirm_start,
                confirm_end + 1
            ):

                if i != latest_index:
                    continue

                c = candles[i]

                score = 0
                reasons = []

                if confirmation_candle_short(c):

                    score += 1

                    reasons.append(
                        "BEARISH_CONFIRM"
                    )

                if (
                    c["close"]
                    <
                    breakdown_level
                ):

                    score += 1

                    reasons.append(
                        "CLOSE_BELOW_BREAKDOWN"
                    )

                if (
                    i > 0
                    and
                    c["close"]
                    <
                    candles[i - 1]["low"]
                ):

                    score += 1

                    reasons.append(
                        "LOWER_CLOSE"
                    )

                if (
                    i > 0
                    and
                    c["high"]
                    <=
                    candles[i - 1]["high"]
                ):

                    score += 1

                    reasons.append(
                        "HOLDING_HIGH"
                    )

                score = min(
                    score,
                    CONFIRMATION_MAX_SCORE
                )

                if (
                    score
                    >=
                    CONFIRMATION_MIN_SCORE
                ):

                    return {
                        "valid": True,
                        "score": score,
                        "reason":
                            "BREAKDOWN,RETEST,"
                            + ",".join(reasons),
                        "breakout_level":
                            breakdown_level,
                        "breakout_index":
                            breakdown_idx,
                        "retest_index":
                            retest_idx,
                        "confirmation_index":
                            i,
                    }

        if not found_breakdown:

            DIAG[
                "confirm_no_breakout"
            ] += 1

            return {
                "valid": False,
                "score": 0,
                "reason":
                    "5M NO BREAKDOWN",
            }

        if not found_retest:

            DIAG[
                "confirm_no_retest"
            ] += 1

            return {
                "valid": False,
                "score": 1,
                "reason":
                    "5M BREAKDOWN WITHOUT RETEST",
            }

        DIAG[
            "confirm_no_confirmation"
        ] += 1

        return {
            "valid": False,
            "score": 2,
            "reason":
                "5M RETEST WITHOUT CONFIRMATION",
        }

    return {
        "valid": False,
        "score": 0,
        "reason":
            "INVALID_DIRECTION",
    }


# ============================================================
# RVOL
# ============================================================

def calculate_rvol(candles):

    if len(candles) < (
        RVOL_LOOKBACK + 1
    ):

        return 0.0

    volumes = [
        c["volume"]
        for c in candles[
            -(RVOL_LOOKBACK + 1):-1
        ]
    ]

    avg = np.mean(volumes)

    if avg <= 0:
        return 0.0

    return (
        candles[-1]["volume"]
        / avg
    )


def rvol_score(rvol):

    if rvol >= RVOL_VERY_STRONG:

        DIAG[
            "rvol_very_strong"
        ] += 1

        return 2

    if rvol >= RVOL_STRONG:

        DIAG[
            "rvol_strong"
        ] += 1

        return 2

    if rvol >= RVOL_NORMAL:

        DIAG[
            "rvol_normal"
        ] += 1

        return 1

    return 0


# ============================================================
# TRADE LEVELS
# ============================================================

def calculate_trade_levels(
    direction,
    entry,
    candles_5m,
    sr
):

    atr = calculate_atr(
        candles_5m,
        ATR_PERIOD
    )

    if atr is None:

        return (
            None,
            "ATR UNAVAILABLE"
        )

    supports = sorted(
        sr["supports"]
    )

    resistances = sorted(
        sr["resistances"]
    )

    # ========================================================
    # LONG
    # ========================================================

    if direction == "LONG":

        structural_sl = nearest_support(
            entry,
            supports
        )

        if structural_sl is None:

            return (
                None,
                "NO SUPPORT FOR SL"
            )

        atr_sl = (
            entry
            -
            atr * ATR_MULTIPLIER
        )

        sl_base = min(
            structural_sl,
            atr_sl
        )

        sl_buffer = (
            entry
            *
            ATR_BUFFER_PCT
            /
            100
        )

        sl = (
            sl_base
            -
            sl_buffer
        )

        risk_pct = (
            (entry - sl)
            /
            entry
            *
            100
        )

        if risk_pct < MIN_SL_PCT:

            return (
                None,
                f"SL TOO TIGHT "
                f"{risk_pct:.2f}% "
                f"< {MIN_SL_PCT:.2f}%"
            )

        if risk_pct > MAX_SL_PCT:

            return (
                None,
                f"SL TOO WIDE "
                f"{risk_pct:.2f}% "
                f"> {MAX_SL_PCT:.2f}%"
            )

        valid_resistances = [
            level
            for level in resistances
            if level > entry
        ]

        if not valid_resistances:

            return (
                None,
                "NO RESISTANCE FOR TP"
            )

        for tp in valid_resistances:

            reward_pct = (
                (tp - entry)
                /
                entry
                *
                100
            )

            if reward_pct <= 0:
                continue

            if reward_pct > MAX_TP_PCT:

                continue

            rr = (
                reward_pct
                /
                risk_pct
            )

            if rr > MIN_RR:

                return (
                    {
                        "sl": sl,
                        "tp": tp,
                        "sl_pct": risk_pct,
                        "tp_pct": reward_pct,
                        "rr": rr,
                    },
                    "OK"
                )

        nearest_tp = (
            valid_resistances[0]
        )

        nearest_reward = (
            nearest_tp - entry
        ) / entry * 100

        if nearest_reward > MAX_TP_PCT:

            return (
                None,
                f"NO TP <= "
                f"{MAX_TP_PCT:.2f}%"
            )

        return (
            None,
            f"NO VALID RR > "
            f"{MIN_RR:.2f}"
        )

    # ========================================================
    # SHORT
    # ========================================================

    if direction == "SHORT":

        structural_sl = nearest_resistance(
            entry,
            resistances
        )

        if structural_sl is None:

            return (
                None,
                "NO RESISTANCE FOR SL"
            )

        atr_sl = (
            entry
            +
            atr * ATR_MULTIPLIER
        )

        sl_base = max(
            structural_sl,
            atr_sl
        )

        sl_buffer = (
            entry
            *
            ATR_BUFFER_PCT
            /
            100
        )

        sl = (
            sl_base
            +
            sl_buffer
        )

        risk_pct = (
            (sl - entry)
            /
            entry
            *
            100
        )

        if risk_pct < MIN_SL_PCT:

            return (
                None,
                f"SL TOO TIGHT "
                f"{risk_pct:.2f}% "
                f"< {MIN_SL_PCT:.2f}%"
            )

        if risk_pct > MAX_SL_PCT:

            return (
                None,
                f"SL TOO WIDE "
                f"{risk_pct:.2f}% "
                f"> {MAX_SL_PCT:.2f}%"
            )

        valid_supports = [
            level
            for level in supports
            if level < entry
        ]

        valid_supports.sort(
            reverse=True
        )

        if not valid_supports:

            return (
                None,
                "NO SUPPORT FOR TP"
            )

        for tp in valid_supports:

            reward_pct = (
                (entry - tp)
                /
                entry
                *
                100
            )

            if reward_pct <= 0:
                continue

            if reward_pct > MAX_TP_PCT:
                continue

            rr = (
                reward_pct
                /
                risk_pct
            )

            if rr > MIN_RR:

                return (
                    {
                        "sl": sl,
                        "tp": tp,
                        "sl_pct": risk_pct,
                        "tp_pct": reward_pct,
                        "rr": rr,
                    },
                    "OK"
                )

        nearest_tp = (
            valid_supports[0]
        )

        nearest_reward = (
            entry - nearest_tp
        ) / entry * 100

        if nearest_reward > MAX_TP_PCT:

            return (
                None,
                f"NO TP <= "
                f"{MAX_TP_PCT:.2f}%"
            )

        return (
            None,
            f"NO VALID RR > "
            f"{MIN_RR:.2f}"
        )

    return (
        None,
        "INVALID_DIRECTION"
    )


# ============================================================
# TOP CANDIDATE TRACKER
# ============================================================

STAGE_RANK = {
    "TREND": 1,
    "SETUP": 2,
    "CONFIRMATION": 3,
    "SCORE": 4,
    "RISK": 5,
}


def consider_top_candidate(
    symbol,
    direction,
    stage,
    reason,
    score=0,
    entry=None,
    levels=None,
    trend_score=0,
    setup_score=0,
    confirmation_score=0,
    rvol=0.0,
):

    global TOP_CANDIDATE

    rank = STAGE_RANK.get(
        stage,
        0
    )

    candidate = {
        "symbol": symbol,
        "direction": direction,
        "stage": stage,
        "stage_rank": rank,
        "reason": reason,
        "score": score,
        "entry": entry,
        "levels": levels,
        "trend_score": trend_score,
        "setup_score": setup_score,
        "confirmation_score":
            confirmation_score,
        "rvol": rvol,
    }

    if TOP_CANDIDATE is None:

        TOP_CANDIDATE = candidate

        return

    current = TOP_CANDIDATE

    candidate_key = (
        rank,
        score,
        trend_score
        +
        setup_score
        +
        confirmation_score,
        rvol
    )

    current_key = (
        current["stage_rank"],
        current["score"],
        current["trend_score"]
        +
        current["setup_score"]
        +
        current["confirmation_score"],
        current["rvol"]
    )

    if candidate_key > current_key:

        TOP_CANDIDATE = candidate


# ============================================================
# DATABASE
# ============================================================

def db_connect():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    conn.row_factory = sqlite3.Row

    return conn


def init_db():

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            symbol TEXT NOT NULL,

            direction TEXT NOT NULL,

            entry REAL NOT NULL,

            sl REAL NOT NULL,

            tp REAL NOT NULL,

            sl_pct REAL NOT NULL,

            tp_pct REAL NOT NULL,

            rr REAL NOT NULL,

            entry_time INTEGER NOT NULL,

            exit_time INTEGER,

            exit_price REAL,

            exit_reason TEXT,

            pnl_pct REAL,

            status TEXT NOT NULL,

            created_at INTEGER NOT NULL,

            closed_reported INTEGER DEFAULT 0
        )
    """)

    columns = {
        row[1]
        for row in cur.execute(
            "PRAGMA table_info(trades)"
        ).fetchall()
    }

    required_columns = {
        "exit_time": "INTEGER",
        "exit_price": "REAL",
        "exit_reason": "TEXT",
        "pnl_pct": "REAL",
        "closed_reported":
            "INTEGER DEFAULT 0",
    }

    for name, definition in (
        required_columns.items()
    ):

        if name not in columns:

            cur.execute(
                f"""
                ALTER TABLE trades
                ADD COLUMN {name}
                {definition}
                """
            )

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_trades_status
        ON trades(status)
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS
        idx_trades_symbol
        ON trades(symbol)
    """)

    conn.commit()

    try:

        cur.execute(
            "PRAGMA journal_mode=WAL"
        )

        conn.commit()

    except Exception:
        pass

    conn.close()

    print(
        f"[DB] Ready: {DB_FILE}"
    )


# ============================================================
# DB OPEN TRADES
# ============================================================

def get_open_trades():

    conn = db_connect()

    rows = conn.execute("""
        SELECT *
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY entry_time ASC
    """).fetchall()

    conn.close()

    return rows


# ============================================================
# DB COOLDOWN
# ============================================================

def in_cooldown(symbol):

    conn = db_connect()

    row = conn.execute("""
        SELECT exit_time
        FROM trades
        WHERE symbol = ?
          AND status = 'CLOSED'
          AND exit_time IS NOT NULL
        ORDER BY exit_time DESC
        LIMIT 1
    """, (
        symbol,
    )).fetchone()

    conn.close()

    if not row:
        return False

    elapsed = (
        utc_now_ts()
        -
        int(row["exit_time"])
    )

    return (
        elapsed
        <
        COOLDOWN_MINUTES * 60
    )


# ============================================================
# DB INSERT
# ============================================================

def insert_trade(
    symbol,
    direction,
    entry,
    levels
):

    now = utc_now_ts()

    conn = db_connect()

    cur = conn.cursor()

    cur.execute("""
        INSERT INTO trades (
            symbol,
            direction,
            entry,
            sl,
            tp,
            sl_pct,
            tp_pct,
            rr,
            entry_time,
            status,
            created_at,
            closed_reported
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?
        )
    """, (
        symbol,
        direction,
        entry,
        levels["sl"],
        levels["tp"],
        levels["sl_pct"],
        levels["tp_pct"],
        levels["rr"],
        now,
        "OPEN",
        now,
        0,
    ))

    trade_id = cur.lastrowid

    conn.commit()

    conn.close()

    print(
        f"[DB OPEN] "
        f"id={trade_id} "
        f"{symbol} "
        f"{direction} "
        f"entry={entry:.8f}"
    )

    return trade_id


# ============================================================
# DB STATE
# ============================================================

def print_db_state():

    conn = db_connect()

    open_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()[0]

    closed_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    total_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
    """).fetchone()[0]

    conn.close()

    print(
        f"[DB] OPEN={open_count} "
        f"CLOSED={closed_count} "
        f"TOTAL={total_count}"
    )

    return (
        open_count,
        closed_count,
        total_count
    )


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades():

    open_trades = get_open_trades()

    if not open_trades:

        print(
            "[DB] No open trades to process."
        )

        return

    print(
        f"[DB] Processing "
        f"{len(open_trades)} open trade(s)"
    )

    conn = db_connect()

    for trade in open_trades:

        symbol = trade["symbol"]

        candles = get_candles(
            symbol,
            TF_5M,
            limit=180
        )

        if not candles:

            print(
                f"[DB HOLD] "
                f"{symbol}: no candles"
            )

            continue

        entry_time = int(
            trade["entry_time"]
        )

        relevant = [
            c
            for c in candles
            if (
                c["time"]
                +
                TF_SECONDS[TF_5M]
                >
                entry_time
            )
        ]

        if not relevant:

            print(
                f"[DB HOLD] "
                f"{symbol}: "
                f"no candle after entry"
            )

            continue

        direction = trade["direction"]

        sl = float(trade["sl"])
        tp = float(trade["tp"])
        entry = float(trade["entry"])

        exit_price = None
        exit_reason = None
        exit_time = None

        for c in relevant:

            high = c["high"]
            low = c["low"]

            if direction == "LONG":

                hit_sl = (
                    low <= sl
                )

                hit_tp = (
                    high >= tp
                )

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:

                    exit_price = tp
                    exit_reason = "TP"

            else:

                hit_sl = (
                    high >= sl
                )

                hit_tp = (
                    low <= tp
                )

                if hit_sl:

                    exit_price = sl
                    exit_reason = "SL"

                elif hit_tp:

                    exit_price = tp
                    exit_reason = "TP"

            if exit_reason:

                exit_time = (
                    c["time"]
                    +
                    TF_SECONDS[TF_5M]
                )

                break

        if exit_reason is None:

            print(
                f"[DB HOLD] "
                f"{symbol} "
                f"{direction}"
            )

            continue

        if direction == "LONG":

            pnl = (
                exit_price - entry
            ) / entry * 100

        else:

            pnl = (
                entry - exit_price
            ) / entry * 100

        cur = conn.cursor()

        cur.execute("""
            UPDATE trades
            SET
                exit_time = ?,
                exit_price = ?,
                exit_reason = ?,
                pnl_pct = ?,
                status = 'CLOSED'
            WHERE id = ?
              AND status = 'OPEN'
        """, (
            exit_time,
            exit_price,
            exit_reason,
            pnl,
            trade["id"],
        ))

        updated = cur.rowcount

        if updated == 1:

            DIAG[
                "db_close_ok"
            ] += 1

            print(
                f"[CLOSE] "
                f"id={trade['id']} "
                f"{symbol} "
                f"{direction} "
                f"reason={exit_reason} "
                f"entry={entry:.8f} "
                f"exit={exit_price:.8f} "
                f"pnl={pnl:+.4f}%"
            )

        else:

            DIAG[
                "db_close_fail"
            ] += 1

            print(
                f"[DB CLOSE ERROR] "
                f"id={trade['id']} "
                f"{symbol}: "
                f"UPDATE affected "
                f"{updated} rows"
            )

    conn.commit()

    conn.close()

    print_db_state()


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = db_connect()

    open_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'OPEN'
    """).fetchone()[0]

    closed_count = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    wins = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
          AND pnl_pct > 0
    """).fetchone()[0]

    losses = conn.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE status = 'CLOSED'
          AND pnl_pct <= 0
    """).fetchone()[0]

    net_pnl = conn.execute("""
        SELECT COALESCE(
            SUM(pnl_pct),
            0
        )
        FROM trades
        WHERE status = 'CLOSED'
    """).fetchone()[0]

    conn.close()

    win_rate = (
        wins
        /
        closed_count
        *
        100
        if closed_count > 0
        else 0.0
    )

    return {
        "open": open_count,
        "closed": closed_count,
        "wins": wins,
        "losses": losses,
        "win_rate": win_rate,
        "net_pnl": float(
            net_pnl or 0
        ),
    }


# ============================================================
# LIVE PRICE
# ============================================================

def get_live_price(symbol):

    try:

        r = SESSION.get(
            TICKERS_URL,
            timeout=REQUEST_TIMEOUT
        )

        r.raise_for_status()

        data = r.json()

        for item in data.get(
            "tickers",
            []
        ):

            if str(
                item.get(
                    "symbol",
                    ""
                )
            ).upper() != symbol.upper():

                continue

            return safe_float(
                item.get("last")
                or item.get("markPrice")
                or item.get("price")
            )

    except Exception:
        pass

    return None


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(text):

    if not TELEGRAM_BOT_TOKEN:
        return

    if not TELEGRAM_CHAT_ID:
        return

    url = (
        "https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    )

    try:

        SESSION.post(
            url,
            json={
                "chat_id":
                    TELEGRAM_CHAT_ID,
                "text":
                    text,
            },
            timeout=REQUEST_TIMEOUT
        )

    except Exception as e:

        print(
            f"[TELEGRAM ERROR] {e}"
        )


# ============================================================
# FORMAT
# ============================================================

def fmt_price(value):

    if value is None:
        return "N/A"

    value = float(value)

    if value >= 1000:
        return f"{value:.2f}"

    if value >= 1:
        return f"{value:.5f}"

    return f"{value:.8f}"


def format_open_trade(trade):

    symbol = trade["symbol"]

    direction = trade["direction"]

    icon = (
        "🟢"
        if direction == "LONG"
        else "🔴"
    )

    entry = float(
        trade["entry"]
    )

    sl = float(
        trade["sl"]
    )

    tp = float(
        trade["tp"]
    )

    live = get_live_price(
        symbol
    )

    if live is not None:

        if direction == "LONG":

            live_pnl = (
                live - entry
            ) / entry * 100

        else:

            live_pnl = (
                entry - live
            ) / entry * 100

        now_line = (
            f"Now: {fmt_price(live)} "
            f"({live_pnl:+.2f}%)"
        )

    else:

        now_line = "Now: N/A"

    return (
        f"{icon} {symbol} "
        f"{direction}\n"
        f"Entry: {fmt_price(entry)}\n"
        f"{now_line}\n"
        f"SL: {fmt_price(sl)} "
        f"({float(trade['sl_pct']):.2f}%)\n"
        f"TP: {fmt_price(tp)} "
        f"({float(trade['tp_pct']):.2f}%)"
    )


# ============================================================
# FORMAT TOP CANDIDATE
# ============================================================

def format_top_candidate():

    c = TOP_CANDIDATE

    if c is None:

        return (
            "🏆 TOP CANDIDATE\n"
            "None"
        )

    direction = c["direction"]

    if direction == "LONG":
        icon = "🟢"

    elif direction == "SHORT":
        icon = "🔴"

    else:
        icon = "⚪"

    lines = []

    lines.append(
        "🏆 TOP CANDIDATE"
    )

    lines.append(
        f"{icon} "
        f"{c['symbol']} "
        f"{direction}"
    )

    if c["entry"] is not None:

        lines.append(
            f"Entry: "
            f"{fmt_price(c['entry'])}"
        )

    levels = c.get(
        "levels"
    )

    if levels:

        lines.append(
            f"SL: "
            f"{fmt_price(levels['sl'])} "
            f"({levels['sl_pct']:.2f}%)"
        )

        lines.append(
            f"TP: "
            f"{fmt_price(levels['tp'])} "
            f"({levels['tp_pct']:.2f}%)"
        )

        lines.append(
            f"RR: "
            f"{levels['rr']:.2f}"
        )

    if c["score"] > 0:

        lines.append(
            f"Score: "
            f"{c['score']}"
            f"/{MIN_SCORE}"
        )

    lines.append("")

    lines.append(
        "❌ REJECTED"
    )

    lines.append(
        f"Reason: {c['reason']}"
    )

    return "\n".join(lines)


# ============================================================
# TELEGRAM REPORT
# ============================================================

def build_report(
    new_signals
):

    now = utc_string()

    open_trades = get_open_trades()

    stats = get_stats()

    lines = []

    lines.append(
        "📊 VOLUME-KHAT 100"
    )

    lines.append(
        f"🕐 {now}"
    )

    lines.append(
        "⚡ Kraken Futures | "
        "5M CLOSED | TOP 100"
    )

    lines.append(
        f"🛠 Strategy: {VERSION}"
    )

    lines.append(
        "🔒 Real trading: DISABLED"
    )

    lines.append("")

    # ========================================================
    # NEW SIGNALS
    # ========================================================

    lines.append(
        "🎯 NEW SIGNALS"
    )

    if not new_signals:

        lines.append(
            "None"
        )

        lines.append("")

        lines.append(
            format_top_candidate()
        )

    else:

        for s in new_signals:

            icon = (
                "🟢"
                if s["direction"] == "LONG"
                else "🔴"
            )

            lines.append(
                f"{icon} "
                f"{s['symbol']} "
                f"{s['direction']}"
            )

            lines.append(
                f"Entry: "
                f"{fmt_price(s['entry'])}"
            )

            lines.append(
                f"SL: "
                f"{fmt_price(s['sl'])} "
                f"({s['sl_pct']:.2f}%)"
            )

            lines.append(
                f"TP: "
                f"{fmt_price(s['tp'])} "
                f"({s['tp_pct']:.2f}%)"
            )

            lines.append(
                f"RR: "
                f"{s['rr']:.2f}"
            )

            lines.append("")

    lines.append("")

    # ========================================================
    # OPEN TRADES
    # ========================================================

    lines.append(
        f"📂 OPEN TRADES "
        f"({len(open_trades)}/"
        f"{MAX_OPEN_TRADES})"
    )

    if not open_trades:

        lines.append(
            "None"
        )

    else:

        for trade in open_trades:

            lines.append("")

            lines.append(
                format_open_trade(
                    trade
                )
            )

    lines.append("")

    # ========================================================
    # STATS
    # ========================================================

    lines.append(
        "📈 STATS"
    )

    lines.append(
        f"Open: {stats['open']}"
    )

    lines.append(
        f"Closed: {stats['closed']}"
    )

    lines.append(
        f"Wins: {stats['wins']}"
    )

    lines.append(
        f"Losses: {stats['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['win_rate']:.1f}%"
    )

    lines.append(
        f"Net PnL: "
        f"{stats['net_pnl']:+.2f}%"
    )

    return "\n".join(lines)


# ============================================================
# DIAGNOSTICS
# ============================================================

def print_diagnostics():

    print("")
    print("=" * 60)
    print("DIAGNOSTICS")
    print("=" * 60)

    print(
        f"Scanned: "
        f"{DIAG['scanned']}"
    )

    print(
        f"1H LONG: "
        f"{DIAG['trend_long']}"
    )

    print(
        f"1H SHORT: "
        f"{DIAG['trend_short']}"
    )

    print(
        f"1H NEUTRAL: "
        f"{DIAG['trend_neutral']}"
    )

    print(
        f"Data fail: "
        f"{DIAG['data_fail']}"
    )

    print(
        f"S/R fail: "
        f"{DIAG['sr_fail']}"
    )

    print(
        f"15M setup PASS: "
        f"{DIAG['setup_pass']}"
    )

    print(
        f"15M setup FAIL: "
        f"{DIAG['setup_fail']}"
    )

    print(
        f"5M confirmation PASS: "
        f"{DIAG['confirm_pass']}"
    )

    print(
        f"5M confirmation FAIL: "
        f"{DIAG['confirm_fail']}"
    )

    print(
        f"5M no breakout: "
        f"{DIAG['confirm_no_breakout']}"
    )

    print(
        f"5M breakout no retest: "
        f"{DIAG['confirm_no_retest']}"
    )

    print(
        f"5M no confirmation: "
        f"{DIAG['confirm_no_confirmation']}"
    )

    print(
        f"RVOL >= {RVOL_NORMAL}: "
        f"{DIAG['rvol_normal']}"
    )

    print(
        f"RVOL >= {RVOL_STRONG}: "
        f"{DIAG['rvol_strong']}"
    )

    print(
        f"RVOL >= {RVOL_VERY_STRONG}: "
        f"{DIAG['rvol_very_strong']}"
    )

    print(
        f"Score checked: "
        f"{DIAG['score_checked']}"
    )

    print(
        f"Score >= {MIN_SCORE}: "
        f"{DIAG['score_pass']}"
    )

    print(
        f"Score < {MIN_SCORE}: "
        f"{DIAG['score_fail']}"
    )

    print(
        f"SL pass: "
        f"{DIAG['sl_pass']}"
    )

    print(
        f"SL fail: "
        f"{DIAG['sl_fail']}"
    )

    print(
        f"TP pass: "
        f"{DIAG['tp_pass']}"
    )

    print(
        f"TP fail: "
        f"{DIAG['tp_fail']}"
    )

    print(
        f"RR pass: "
        f"{DIAG['rr_pass']}"
    )

    print(
        f"RR fail: "
        f"{DIAG['rr_fail']}"
    )

    print(
        f"Cooldown: "
        f"{DIAG['cooldown']}"
    )

    print(
        f"DB close OK: "
        f"{DIAG['db_close_ok']}"
    )

    print(
        f"DB close FAIL: "
        f"{DIAG['db_close_fail']}"
    )

    print(
        f"Errors: "
        f"{DIAG['errors']}"
    )

    print("=" * 60)


# ============================================================
# MAIN
# ============================================================

def main():

    global TOP_CANDIDATE

    TOP_CANDIDATE = None

    print("=" * 60)

    print(
        f"KRAKEN FUTURES "
        f"VOLUME-KHAT 100 "
        f"{VERSION}"
    )

    print(
        utc_string()
    )

    print("=" * 60)

    print(
        "SYSTEM: GitHub Actions"
    )

    print(
        "EXCHANGE: Kraken Futures"
    )

    print(
        "MARKETS: TOP 100 USD PERPETUAL"
    )

    print(
        "TIMEFRAME: 5M CLOSED CANDLES"
    )

    print(
        "REAL TRADING: DISABLED"
    )

    print(
        f"DATABASE: {DB_FILE}"
    )

    print("=" * 60)

    # ========================================================
    # DATABASE
    # ========================================================

    init_db()

    print_db_state()

    # ========================================================
    # UPDATE OPEN TRADES
    # ========================================================

    update_open_trades()

    open_trades = get_open_trades()

    print(
        f"[OPEN] "
        f"{len(open_trades)}/"
        f"{MAX_OPEN_TRADES}"
    )

    # ========================================================
    # TOP SYMBOLS
    # ========================================================

    symbols = get_top_symbols()

    print(
        f"[TOP] "
        f"{len(symbols)} symbols"
    )

    slots = max(
        0,
        MAX_OPEN_TRADES
        -
        len(open_trades)
    )

    print(
        f"[SLOTS] "
        f"{slots}"
    )

    new_signals = []

    # ========================================================
    # SCAN
    # ========================================================

    for item in symbols:

        if (
            len(new_signals)
            >= MAX_NEW_SIGNALS
        ):
            break

        symbol = item["symbol"]

        DIAG["scanned"] += 1

        # ----------------------------------------------------
        # Existing open trade
        # ----------------------------------------------------

        if any(
            t["symbol"] == symbol
            for t in open_trades
        ):

            continue

        # ----------------------------------------------------
        # Cooldown
        # ----------------------------------------------------

        if in_cooldown(symbol):

            DIAG["cooldown"] += 1

            continue

        # ----------------------------------------------------
        # 1H
        # ----------------------------------------------------

        candles_1h = get_candles(
            symbol,
            TF_1H,
            limit=180
        )

        if not candles_1h:
            continue

        (
            direction,
            trend_score
        ) = get_trend(
            candles_1h
        )

        if direction == "LONG":

            DIAG["trend_long"] += 1

        elif direction == "SHORT":

            DIAG["trend_short"] += 1

        else:

            DIAG["trend_neutral"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction="NEUTRAL",
                stage="TREND",
                reason=(
                    f"1H TREND TOO WEAK "
                    f"({trend_score}/"
                    f"{MIN_TREND_SCORE})"
                ),
                score=trend_score,
                entry=item.get("price"),
                trend_score=trend_score,
            )

            continue

        # ----------------------------------------------------
        # Candidate at trend stage
        # ----------------------------------------------------

        consider_top_candidate(
            symbol=symbol,
            direction=direction,
            stage="TREND",
            reason=(
                "PASSED 1H, "
                "15M SETUP REQUIRED"
            ),
            score=trend_score,
            entry=item.get("price"),
            trend_score=trend_score,
        )

        # ----------------------------------------------------
        # 15M
        # ----------------------------------------------------

        candles_15m = get_candles(
            symbol,
            TF_15M,
            limit=220
        )

        if not candles_15m:

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="TREND",
                reason="15M DATA UNAVAILABLE",
                score=trend_score,
                entry=item.get("price"),
                trend_score=trend_score,
            )

            continue

        # ----------------------------------------------------
        # 5M
        # ----------------------------------------------------

        candles_5m = get_candles(
            symbol,
            TF_5M,
            limit=250
        )

        if not candles_5m:

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="TREND",
                reason="5M DATA UNAVAILABLE",
                score=trend_score,
                entry=item.get("price"),
                trend_score=trend_score,
            )

            continue

        # ----------------------------------------------------
        # S/R
        # ----------------------------------------------------

        sr = build_sr(
            candles_15m,
            candles_1h
        )

        if not sr:

            DIAG["sr_fail"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="TREND",
                reason="NO VALID S/R",
                score=trend_score,
                entry=candles_5m[-1]["close"],
                trend_score=trend_score,
            )

            continue

        # ----------------------------------------------------
        # 15M SETUP
        # ----------------------------------------------------

        setup = analyze_15m_setup(
            candles_15m,
            sr,
            direction
        )

        if not setup["valid"]:

            DIAG["setup_fail"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="SETUP",
                reason=setup["reason"],
                score=(
                    trend_score
                    +
                    setup["score"]
                ),
                entry=candles_5m[-1]["close"],
                trend_score=trend_score,
                setup_score=setup["score"],
            )

            continue

        DIAG["setup_pass"] += 1

        consider_top_candidate(
            symbol=symbol,
            direction=direction,
            stage="SETUP",
            reason=(
                "15M PASSED, "
                "5M CONFIRMATION REQUIRED"
            ),
            score=(
                trend_score
                +
                setup["score"]
            ),
            entry=candles_5m[-1]["close"],
            trend_score=trend_score,
            setup_score=setup["score"],
        )

        # ----------------------------------------------------
        # 5M CONFIRMATION
        # ----------------------------------------------------

        confirmation = (
            analyze_5m_confirmation(
                candles_5m,
                direction
            )
        )

        if not confirmation["valid"]:

            DIAG["confirm_fail"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="CONFIRMATION",
                reason=confirmation["reason"],
                score=(
                    trend_score
                    +
                    setup["score"]
                    +
                    confirmation["score"]
                ),
                entry=candles_5m[-1]["close"],
                trend_score=trend_score,
                setup_score=setup["score"],
                confirmation_score=
                    confirmation["score"],
            )

            continue

        DIAG["confirm_pass"] += 1

        # ----------------------------------------------------
        # RVOL
        # ----------------------------------------------------

        rvol = calculate_rvol(
            candles_5m
        )

        volume_score = rvol_score(
            rvol
        )

        # ----------------------------------------------------
        # TOTAL SCORE
        # ----------------------------------------------------

        total_score = (
            trend_score
            +
            setup["score"]
            +
            confirmation["score"]
            +
            volume_score
        )

        DIAG["score_checked"] += 1

        if total_score < MIN_SCORE:

            DIAG["score_fail"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="SCORE",
                reason=(
                    f"SCORE {total_score} "
                    f"< {MIN_SCORE}"
                ),
                score=total_score,
                entry=candles_5m[-1]["close"],
                trend_score=trend_score,
                setup_score=setup["score"],
                confirmation_score=
                    confirmation["score"],
                rvol=rvol,
            )

            continue

        DIAG["score_pass"] += 1

        # ----------------------------------------------------
        # ENTRY
        # ----------------------------------------------------

        entry = candles_5m[-1]["close"]

        # ----------------------------------------------------
        # SL / TP
        # ----------------------------------------------------

        levels, level_reason = (
            calculate_trade_levels(
                direction,
                entry,
                candles_5m,
                sr
            )
        )

        if levels is None:

            if "SL" in level_reason:

                DIAG["sl_fail"] += 1

            if (
                "TP" in level_reason
                or
                "RR" in level_reason
            ):

                DIAG["tp_fail"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="RISK",
                reason=level_reason,
                score=total_score,
                entry=entry,
                trend_score=trend_score,
                setup_score=setup["score"],
                confirmation_score=
                    confirmation["score"],
                rvol=rvol,
            )

            continue

        DIAG["sl_pass"] += 1
        DIAG["tp_pass"] += 1

        if levels["rr"] <= MIN_RR:

            DIAG["rr_fail"] += 1

            consider_top_candidate(
                symbol=symbol,
                direction=direction,
                stage="RISK",
                reason=(
                    f"RR {levels['rr']:.2f} "
                    f"<= {MIN_RR:.2f}"
                ),
                score=total_score,
                entry=entry,
                levels=levels,
                trend_score=trend_score,
                setup_score=setup["score"],
                confirmation_score=
                    confirmation["score"],
                rvol=rvol,
            )

            continue

        DIAG["rr_pass"] += 1

        # ----------------------------------------------------
        # VALID SIGNAL
        # ----------------------------------------------------

        signal = {
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": levels["sl"],
            "tp": levels["tp"],
            "sl_pct": levels["sl_pct"],
            "tp_pct": levels["tp_pct"],
            "rr": levels["rr"],
            "score": total_score,
            "rvol": rvol,
            "trend_score": trend_score,
            "setup_score": setup["score"],
            "confirmation_score":
                confirmation["score"],
            "setup_reason":
                setup["reason"],
            "confirmation_reason":
                confirmation["reason"],
        }

        # ----------------------------------------------------
        # Open only if a slot exists
        # ----------------------------------------------------

        if slots <= 0:

            break

        new_signals.append(
            signal
        )

        insert_trade(
            symbol,
            direction,
            entry,
            levels
        )

        slots -= 1

        open_trades = list(
            get_open_trades()
        )

    # ========================================================
    # FINAL DB STATE
    # ========================================================

    print_db_state()

    # ========================================================
    # TELEGRAM
    # ========================================================

    report = build_report(
        new_signals
    )

    print("")
    print(report)
    print("")

    telegram_send(
        report
    )

    # ========================================================
    # DIAGNOSTICS
    # ========================================================

    print_diagnostics()

    print("=" * 60)

    print(
        "Scanner finished: "
        f"{utc_string()}"
    )

    print("=" * 60)


# ============================================================
# ENTRY
# ============================================================

if __name__ == "__main__":

    try:

        main()

    except Exception as e:

        DIAG["errors"] += 1

        print(
            f"[FATAL ERROR] {e}"
        )

        raise
