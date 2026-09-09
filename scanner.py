# ============================================================
# VOLUME-KHAT 100 v2.2
# KRAKEN FUTURES
# TOP 100 USD PERPETUAL
# ============================================================
#
# 1H  = Trend
# 15M = Dynamic S/R + RVOL + Setup
# 5M  = Confirmation
#
# SETUPS:
#   BREAKOUT
#   REJECTION
#
# SIGNAL:
#   Dynamic S/R
#   RVOL
#   Setup
#   5M Confirmation
#   Retest when applicable
#   Static S/R
#   SL / TP
#   RR >= 1:2
#   Signal Score
#
# REPORT:
#   OPEN SIGNALS
#   CLOSED SINCE PREVIOUS REPORT
#   STATS
#   COMPACT SCAN
#
# NO:
#   TOP 3
#   CANDIDATES
#
# ONE-SHOT
# FOR GITHUB ACTIONS
#
# REAL TRADING DISABLED
# ============================================================

import os
import time
import sqlite3
import requests
import numpy as np
import pandas as pd

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


# ============================================================
# CONFIG
# ============================================================

TOP_N = 100

TF_1H = "1h"
TF_15M = "15m"
TF_5M = "5m"

LIMIT_1H = 180
LIMIT_15M = 180
LIMIT_5M = 120

RVOL_PERIOD = 20
RVOL_MIN = 2.0
RVOL_STRONG = 3.0

SWING_LEFT = 2
SWING_RIGHT = 2

DYNAMIC_MAX_DISTANCE = 0.010
DYNAMIC_STRONG_DISTANCE = 0.005

BREAKOUT_MIN_DISTANCE = 0.0015

MIN_BODY_BREAKOUT = 0.50
MIN_BODY_CONFIRM = 0.45

STATIC_CLUSTER_DISTANCE = 0.003

ATR_PERIOD = 14
SL_ATR_BUFFER = 0.20

MIN_RR = 2.0

MIN_SIGNAL_SCORE = 9

DB_FILE = "volume_khat_100.db"

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
).strip()

MAX_TELEGRAM_LENGTH = 3900

TIMEOUT = 20

BASE = "https://futures.kraken.com"

INSTRUMENTS_URL = (
    BASE +
    "/derivatives/api/v3/instruments"
)

TICKERS_URL = (
    BASE +
    "/derivatives/api/v3/tickers"
)

CHART_URL = (
    BASE +
    "/api/charts/v1/trade/{symbol}/{resolution}"
)


# ============================================================
# SESSION
# ============================================================

SESSION = requests.Session()

SESSION.headers.update({
    "User-Agent": "Volume-Khat-100/2.2"
})


# ============================================================
# DIAGNOSTICS
# ============================================================

DIAG = {
    "instruments": 0,
    "tickers": 0,
    "perpetual": 0,
    "usd_perpetual": 0,
    "ranked": 0,

    "scanned": 0,
    "data_ok": 0,

    "bull": 0,
    "bear": 0,
    "neutral": 0,

    "dynamic_sr": 0,

    "rvol2": 0,
    "rvol3": 0,

    "breakout": 0,
    "rejection": 0,

    "confirmation": 0,
    "retest": 0,

    "static_sr": 0,
    "rr2": 0,

    "qualified": 0
}


# ============================================================
# HELPERS
# ============================================================

def now_utc():

    return datetime.now(
        timezone.utc
    )


def safe_float(
    value,
    default=0.0
):

    try:
        return float(value)
    except Exception:
        return default


def normalize_symbol(symbol):

    return str(
        symbol or ""
    ).strip().upper()


def display_symbol(symbol):

    s = normalize_symbol(symbol)

    return (
        s.replace("PF_", "")
         .replace("PI_", "")
         .replace("P_", "")
    )


def is_perpetual_symbol(symbol):

    s = normalize_symbol(symbol)

    return (
        s.startswith("PF_")
        or
        s.startswith("PI_")
        or
        s.startswith("P_")
    )


def is_usd_symbol(symbol):

    s = normalize_symbol(symbol)

    return (
        "USD" in s
        or
        "USDT" in s
    )


# ============================================================
# API
# ============================================================

def api_get(url):

    try:

        response = SESSION.get(
            url,
            timeout=TIMEOUT
        )

        response.raise_for_status()

        return response.json()

    except Exception as e:

        print(
            f"API ERROR: {url} -> {e}"
        )

        return {}


# ============================================================
# INSTRUMENTS
# ============================================================

def get_instruments():

    data = api_get(
        INSTRUMENTS_URL
    )

    if not data:
        return []

    items = data.get(
        "instruments",
        []
    )

    if isinstance(items, dict):

        items = list(
            items.values()
        )

    if not isinstance(items, list):
        return []

    DIAG["instruments"] = len(
        items
    )

    return items


# ============================================================
# TICKERS
# ============================================================

def get_tickers():

    data = api_get(
        TICKERS_URL
    )

    if not data:
        return []

    items = data.get(
        "tickers",
        []
    )

    if isinstance(items, dict):

        items = list(
            items.values()
        )

    if not isinstance(items, list):
        return []

    DIAG["tickers"] = len(
        items
    )

    return items


# ============================================================
# INSTRUMENT CHECKS
# ============================================================

def instrument_is_perpetual(
    instrument
):

    if not isinstance(
        instrument,
        dict
    ):
        return False

    symbol = normalize_symbol(
        instrument.get("symbol")
    )

    text = " ".join(
        str(
            instrument.get(
                key,
                ""
            )
        )
        for key in [
            "type",
            "contractType",
            "category",
            "tags",
            "status"
        ]
    ).lower()

    if "perpetual" in text:
        return True

    if is_perpetual_symbol(
        symbol
    ):
        return True

    return False


def instrument_is_usd(
    instrument
):

    if not isinstance(
        instrument,
        dict
    ):
        return False

    fields = [
        "quoteCurrency",
        "quoteAsset",
        "settleCurrency",
        "settlementCurrency",
        "underlying",
        "symbol"
    ]

    text = " ".join(
        str(
            instrument.get(
                key,
                ""
            )
        ).upper()
        for key in fields
    )

    return (
        "USD" in text
        or
        "USDT" in text
    )


# ============================================================
# TICKER VOLUME
# ============================================================

def ticker_volume(
    ticker
):

    keys = [
        "vol24h",
        "volume24h",
        "volumeQuote",
        "quoteVolume",
        "turnover24h",
        "volume"
    ]

    for key in keys:

        value = safe_float(
            ticker.get(
                key
            )
        )

        if value > 0:
            return value

    return 0.0


# ============================================================
# TOP 100
# ============================================================

def get_top_markets():

    instruments = get_instruments()
    tickers = get_tickers()

    instrument_map = {}

    for item in instruments:

        symbol = normalize_symbol(
            item.get("symbol")
        )

        if symbol:

            instrument_map[
                symbol
            ] = item

    candidates = []

    for ticker in tickers:

        symbol = normalize_symbol(
            ticker.get("symbol")
        )

        if not symbol:
            continue

        instrument = (
            instrument_map.get(
                symbol
            )
        )

        perpetual = False
        usd = False

        if instrument:

            perpetual = (
                instrument_is_perpetual(
                    instrument
                )
            )

            usd = (
                instrument_is_usd(
                    instrument
                )
            )

        if not perpetual:

            perpetual = (
                is_perpetual_symbol(
                    symbol
                )
            )

        if not usd:

            usd = (
                is_usd_symbol(
                    symbol
                )
            )

        if not perpetual:
            continue

        DIAG["perpetual"] += 1

        if not usd:
            continue

        DIAG["usd_perpetual"] += 1

        volume = ticker_volume(
            ticker
        )

        if volume <= 0:
            continue

        price = safe_float(
            ticker.get("last")
        )

        if price <= 0:

            price = safe_float(
                ticker.get("markPrice")
            )

        candidates.append({
            "symbol": symbol,
            "volume": volume,
            "price": price
        })

    candidates.sort(
        key=lambda x: x["volume"],
        reverse=True
    )

    markets = candidates[
        :TOP_N
    ]

    DIAG["ranked"] = len(
        markets
    )

    print(
        "\n=================================================="
    )

    print(
        "KRAKEN MARKET DISCOVERY"
    )

    print(
        "=================================================="
    )

    print(
        "Instruments      :",
        DIAG["instruments"]
    )

    print(
        "Tickers          :",
        DIAG["tickers"]
    )

    print(
        "Perpetual        :",
        DIAG["perpetual"]
    )

    print(
        "USD Perpetual    :",
        DIAG["usd_perpetual"]
    )

    print(
        "TOP 100 Ranked   :",
        DIAG["ranked"]
    )

    print(
        "\nTOP MARKETS:"
    )

    for i, market in enumerate(
        markets[:10],
        1
    ):

        print(
            f"{i:02d}. "
            f"{market['symbol']} "
            f"VOL={market['volume']:.2f}"
        )

    print(
        "=================================================="
    )

    return markets


# ============================================================
# CANDLES
# ============================================================

def get_candles(
    symbol,
    resolution,
    limit
):

    url = CHART_URL.format(
        symbol=symbol,
        resolution=resolution
    )

    data = api_get(
        url
    )

    if not data:
        return None

    candles = data.get(
        "candles",
        []
    )

    if not isinstance(
        candles,
        list
    ):
        return None

    rows = []

    for candle in candles:

        if not isinstance(
            candle,
            dict
        ):
            continue

        t = (
            candle.get("time")
            or
            candle.get("timestamp")
            or
            candle.get("t")
        )

        o = candle.get(
            "open",
            candle.get("o")
        )

        h = candle.get(
            "high",
            candle.get("h")
        )

        l = candle.get(
            "low",
            candle.get("l")
        )

        c = candle.get(
            "close",
            candle.get("c")
        )

        v = candle.get(
            "volume",
            candle.get("v")
        )

        try:

            rows.append({
                "time": float(t),
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v)
            })

        except Exception:
            continue

    if len(rows) < 35:
        return None

    df = pd.DataFrame(
        rows
    )

    df = (
        df.sort_values("time")
          .drop_duplicates(
              subset=["time"]
          )
          .reset_index(drop=True)
    )

    # حذف آخرین کندل
    # فقط کندل بسته شده استفاده می‌شود.
    if len(df) > 2:

        df = df.iloc[
            :-1
        ].copy()

    if len(df) > limit:

        df = df.iloc[
            -limit:
        ].copy()

    return df.reset_index(
        drop=True
    )


# ============================================================
# ATR
# ============================================================

def calculate_atr(
    df,
    period=ATR_PERIOD
):

    if len(df) < period + 2:
        return 0.0

    prev_close = (
        df["close"].shift(1)
    )

    tr = pd.concat([
        df["high"] - df["low"],
        (
            df["high"]
            -
            prev_close
        ).abs(),
        (
            df["low"]
            -
            prev_close
        ).abs()
    ], axis=1).max(
        axis=1
    )

    value = (
        tr.rolling(
            period
        ).mean().iloc[-1]
    )

    return safe_float(
        value
    )


# ============================================================
# 1H TREND
# ============================================================

def get_trend(
    df
):

    if len(df) < 50:
        return "NEUTRAL"

    ema20 = (
        df["close"]
        .ewm(
            span=20,
            adjust=False
        )
        .mean()
        .iloc[-1]
    )

    ema50 = (
        df["close"]
        .ewm(
            span=50,
            adjust=False
        )
        .mean()
        .iloc[-1]
    )

    close = float(
        df["close"].iloc[-1]
    )

    if close > ema20 > ema50:
        return "BULL"

    if close < ema20 < ema50:
        return "BEAR"

    return "NEUTRAL"


# ============================================================
# SWINGS
# ============================================================

def detect_swings(
    df
):

    highs = []
    lows = []

    h = df["high"].values
    l = df["low"].values

    for i in range(
        SWING_LEFT,
        len(df) - SWING_RIGHT
    ):

        left_h = h[
            i-SWING_LEFT:i
        ]

        right_h = h[
            i+1:
            i+SWING_RIGHT+1
        ]

        left_l = l[
            i-SWING_LEFT:i
        ]

        right_l = l[
            i+1:
            i+SWING_RIGHT+1
        ]

        if (
            h[i] > max(left_h)
            and
            h[i] > max(right_h)
        ):

            highs.append(
                (
                    i,
                    float(h[i])
                )
            )

        if (
            l[i] < min(left_l)
            and
            l[i] < min(right_l)
        ):

            lows.append(
                (
                    i,
                    float(l[i])
                )
            )

    return highs, lows


# ============================================================
# PROJECT DYNAMIC LEVEL
# ============================================================

def project_level(
    points,
    current_index
):

    if len(points) < 2:
        return None

    x1, y1 = points[-2]
    x2, y2 = points[-1]

    if x2 == x1:
        return y2

    slope = (
        y2 - y1
    ) / (
        x2 - x1
    )

    return (
        y2
        +
        slope * (
            current_index - x2
        )
    )


# ============================================================
# DYNAMIC S/R
# ============================================================

def get_dynamic_sr(
    df
):

    highs, lows = detect_swings(
        df
    )

    index = len(df) - 1

    resistance = project_level(
        highs,
        index
    )

    support = project_level(
        lows,
        index
    )

    close = float(
        df["close"].iloc[-1]
    )

    support_distance = None
    resistance_distance = None

    if support:

        support_distance = (
            abs(close - support)
            / close
        )

    if resistance:

        resistance_distance = (
            abs(close - resistance)
            / close
        )

    return {
        "support": support,
        "resistance": resistance,

        "support_distance":
            support_distance,

        "resistance_distance":
            resistance_distance,

        "support_touches":
            len(lows),

        "resistance_touches":
            len(highs)
    }


# ============================================================
# RVOL
# ============================================================

def get_rvol(
    df
):

    if len(df) < (
        RVOL_PERIOD + 2
    ):
        return 0.0

    current = float(
        df["volume"].iloc[-1]
    )

    baseline = float(
        df["volume"]
        .iloc[
            -RVOL_PERIOD-1:-1
        ]
        .mean()
    )

    if baseline <= 0:
        return 0.0

    return current / baseline


# ============================================================
# CANDLE
# ============================================================

def candle_info(
    row
):

    o = float(
        row["open"]
    )

    h = float(
        row["high"]
    )

    l = float(
        row["low"]
    )

    c = float(
        row["close"]
    )

    rng = max(
        h - l,
        1e-12
    )

    body = abs(
        c - o
    )

    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,

        "range": rng,
        "body": body,

        "body_ratio":
            body / rng,

        "upper_wick":
            h - max(o, c),

        "lower_wick":
            min(o, c) - l,

        "bullish":
            c > o,

        "bearish":
            c < o
    }


# ============================================================
# BREAKOUT
# ============================================================

def detect_breakout(
    df,
    sr
):

    if len(df) < 3:
        return None

    current = candle_info(
        df.iloc[-1]
    )

    previous = candle_info(
        df.iloc[-2]
    )

    close = current[
        "close"
    ]

    resistance = sr[
        "resistance"
    ]

    support = sr[
        "support"
    ]

    # LONG BREAKOUT
    if resistance is not None:

        distance = (
            close - resistance
        ) / resistance

        if (
            close > resistance
            and
            previous["close"]
            <= resistance
            and
            distance
            >= BREAKOUT_MIN_DISTANCE
            and
            current["bullish"]
            and
            current["body_ratio"]
            >= MIN_BODY_BREAKOUT
        ):

            return {
                "type": "BREAKOUT",
                "side": "LONG",
                "level": resistance
            }

    # SHORT BREAKOUT
    if support is not None:

        distance = (
            support - close
        ) / support

        if (
            close < support
            and
            previous["close"]
            >= support
            and
            distance
            >= BREAKOUT_MIN_DISTANCE
            and
            current["bearish"]
            and
            current["body_ratio"]
            >= MIN_BODY_BREAKOUT
        ):

            return {
                "type": "BREAKOUT",
                "side": "SHORT",
                "level": support
            }

    return None


# ============================================================
# REJECTION
# ============================================================

def detect_rejection(
    df,
    sr,
    rvol
):

    if rvol < RVOL_MIN:
        return None

    c = candle_info(
        df.iloc[-1]
    )

    close = c["close"]

    body = max(
        c["body"],
        1e-12
    )

    resistance = sr[
        "resistance"
    ]

    support = sr[
        "support"
    ]

    # RESISTANCE REJECTION
    if resistance is not None:

        distance = (
            abs(
                close - resistance
            )
            / close
        )

        if (
            distance
            <= DYNAMIC_MAX_DISTANCE
            and
            c["upper_wick"]
            >= body * 1.2
            and
            close < resistance
        ):

            return {
                "type": "REJECTION",
                "side": "SHORT",
                "level": resistance
            }

    # SUPPORT REJECTION
    if support is not None:

        distance = (
            abs(
                close - support
            )
            / close
        )

        if (
            distance
            <= DYNAMIC_MAX_DISTANCE
            and
            c["lower_wick"]
            >= body * 1.2
            and
            close > support
        ):

            return {
                "type": "REJECTION",
                "side": "LONG",
                "level": support
            }

    return None


# ============================================================
# 5M CONFIRMATION
# ============================================================

def confirmation_5m(
    df,
    side
):

    if len(df) < 3:
        return False

    c = candle_info(
        df.iloc[-1]
    )

    if (
        c["body_ratio"]
        < MIN_BODY_CONFIRM
    ):
        return False

    if side == "LONG":

        return c["bullish"]

    if side == "SHORT":

        return c["bearish"]

    return False


# ============================================================
# RETEST
# ============================================================

def detect_retest(
    df,
    level,
    side
):

    if (
        level is None
        or
        len(df) < 4
    ):
        return False

    tolerance = 0.004

    for _, row in df.iloc[-3:].iterrows():

        high = float(
            row["high"]
        )

        low = float(
            row["low"]
        )

        close = float(
            row["close"]
        )

        if side == "LONG":

            touched = (
                abs(
                    low - level
                )
                / level
                <= tolerance
            )

            if touched and close > level:
                return True

        else:

            touched = (
                abs(
                    high - level
                )
                / level
                <= tolerance
            )

            if touched and close < level:
                return True

    return False


# ============================================================
# STATIC LEVELS
# ============================================================

def cluster_levels(
    levels
):

    if not levels:
        return []

    values = sorted(
        [
            float(x)
            for x in levels
            if x > 0
        ]
    )

    clusters = []

    for value in values:

        if not clusters:

            clusters.append(
                [value]
            )

            continue

        center = float(
            np.mean(
                clusters[-1]
            )
        )

        distance = (
            abs(
                value - center
            )
            / center
        )

        if (
            distance
            <= STATIC_CLUSTER_DISTANCE
        ):

            clusters[-1].append(
                value
            )

        else:

            clusters.append(
                [value]
            )

    return [
        float(
            np.mean(cluster)
        )
        for cluster in clusters
    ]


def get_static_sr(
    df1h,
    df15
):

    h1_highs, h1_lows = (
        detect_swings(df1h)
    )

    m15_highs, m15_lows = (
        detect_swings(df15)
    )

    resistance_levels = (
        [x[1] for x in h1_highs]
        +
        [x[1] for x in m15_highs]
    )

    support_levels = (
        [x[1] for x in h1_lows]
        +
        [x[1] for x in m15_lows]
    )

    resistances = cluster_levels(
        resistance_levels
    )

    supports = cluster_levels(
        support_levels
    )

    price = float(
        df15["close"].iloc[-1]
    )

    supports = sorted(
        [
            x for x in supports
            if x < price
        ],
        reverse=True
    )

    resistances = sorted(
        [
            x for x in resistances
            if x > price
        ]
    )

    return {
        "supports": supports,
        "resistances": resistances
    }


# ============================================================
# TRADE CALCULATION
# ============================================================

def calculate_trade(
    side,
    entry,
    df15,
    static
):

    atr_value = calculate_atr(
        df15
    )

    if atr_value <= 0:
        return None

    buffer = (
        atr_value
        *
        SL_ATR_BUFFER
    )

    supports = static[
        "supports"
    ]

    resistances = static[
        "resistances"
    ]

    if side == "LONG":

        if supports:

            sl_base = supports[0]

        else:

            sl_base = (
                entry - atr_value
            )

        sl = (
            sl_base - buffer
        )

        if sl >= entry:

            sl = (
                entry - atr_value
            )

        risk = (
            entry - sl
        )

        if risk <= 0:
            return None

        tp = None

        for level in resistances:

            rr = (
                level - entry
            ) / risk

            if rr >= MIN_RR:

                tp = level
                break

        if tp is None:
            return None

        rr = (
            tp - entry
        ) / risk

    else:

        if resistances:

            sl_base = resistances[0]

        else:

            sl_base = (
                entry + atr_value
            )

        sl = (
            sl_base + buffer
        )

        if sl <= entry:

            sl = (
                entry + atr_value
            )

        risk = (
            sl - entry
        )

        if risk <= 0:
            return None

        tp = None

        for level in supports:

            rr = (
                entry - level
            ) / risk

            if rr >= MIN_RR:

                tp = level
                break

        if tp is None:
            return None

        rr = (
            entry - tp
        ) / risk

    if rr < MIN_RR:
        return None

    return {
        "entry": entry,
        "sl": sl,
        "tp": tp,
        "rr": rr,
        "atr": atr_value
    }


# ============================================================
# SIGNAL SCORE
# ============================================================

def signal_score(
    side,
    trend,
    rvol,
    dynamic,
    setup,
    confirmation,
    retest,
    rr,
    entry
):

    score = 0

    # RVOL
    if rvol >= RVOL_MIN:
        score += 2

    if rvol >= RVOL_STRONG:
        score += 1

    # Dynamic S/R touches
    touches = max(
        dynamic[
            "support_touches"
        ],
        dynamic[
            "resistance_touches"
        ]
    )

    if touches >= 2:
        score += 2

    # Distance to dynamic level
    level = setup.get(
        "level"
    )

    if level:

        distance = (
            abs(
                entry - level
            )
            / entry
        )

        if (
            distance
            <= DYNAMIC_STRONG_DISTANCE
        ):

            score += 2

        elif (
            distance
            <= DYNAMIC_MAX_DISTANCE
        ):

            score += 1

    # Setup
    score += 2

    # 5M confirmation
    if confirmation:
        score += 2

    # Retest
    if retest:
        score += 1

    # 1H trend
    if (
        side == "LONG"
        and
        trend == "BULL"
    ):

        score += 2

    elif (
        side == "SHORT"
        and
        trend == "BEAR"
    ):

        score += 2

    # RR
    if rr >= 3.0:
        score += 2

    return score


# ============================================================
# PROCESS MARKET
# ============================================================

def process_market(
    market
):

    symbol = market[
        "symbol"
    ]

    try:

        df1h = get_candles(
            symbol,
            TF_1H,
            LIMIT_1H
        )

        df15 = get_candles(
            symbol,
            TF_15M,
            LIMIT_15M
        )

        df5 = get_candles(
            symbol,
            TF_5M,
            LIMIT_5M
        )

        if (
            df1h is None
            or
            df15 is None
            or
            df5 is None
        ):

            return None

        DIAG["data_ok"] += 1

        trend = get_trend(
            df1h
        )

        if trend == "BULL":

            DIAG["bull"] += 1

        elif trend == "BEAR":

            DIAG["bear"] += 1

        else:

            DIAG["neutral"] += 1

        dynamic = get_dynamic_sr(
            df15
        )

        if (
            dynamic["support"]
            is not None
            or
            dynamic["resistance"]
            is not None
        ):

            DIAG["dynamic_sr"] += 1

        rvol = get_rvol(
            df15
        )

        if rvol >= RVOL_MIN:

            DIAG["rvol2"] += 1

        if rvol >= RVOL_STRONG:

            DIAG["rvol3"] += 1

        breakout = detect_breakout(
            df15,
            dynamic
        )

        rejection = detect_rejection(
            df15,
            dynamic,
            rvol
        )

        setup = (
            breakout
            or
            rejection
        )

        if breakout:

            DIAG["breakout"] += 1

        if rejection:

            DIAG["rejection"] += 1

        # ----------------------------------------------------
        # NO REAL SETUP
        # Nothing to return.
        # No candidates are created.
        # ----------------------------------------------------

        if setup is None:

            return None

        side = setup[
            "side"
        ]

        confirmation = (
            confirmation_5m(
                df5,
                side
            )
        )

        if confirmation:

            DIAG[
                "confirmation"
            ] += 1

        retest = False

        if (
            setup["type"]
            ==
            "BREAKOUT"
        ):

            retest = (
                detect_retest(
                    df5,
                    setup["level"],
                    side
                )
            )

            if retest:

                DIAG[
                    "retest"
                ] += 1

        static = get_static_sr(
            df1h,
            df15
        )

        if (
            static["supports"]
            or
            static["resistances"]
        ):

            DIAG[
                "static_sr"
            ] += 1

        entry = float(
            df5[
                "close"
            ].iloc[-1]
        )

        trade = calculate_trade(
            side,
            entry,
            df15,
            static
        )

        if trade:

            DIAG["rr2"] += 1

        score = signal_score(
            side=side,
            trend=trend,
            rvol=rvol,
            dynamic=dynamic,
            setup=setup,
            confirmation=confirmation,
            retest=retest,
            rr=(
                trade["rr"]
                if trade
                else 0
            ),
            entry=entry
        )

        # ----------------------------------------------------
        # FINAL SIGNAL
        # ----------------------------------------------------

        if (
            trade
            and
            confirmation
            and
            score
            >= MIN_SIGNAL_SCORE
        ):

            return {
                "symbol": symbol,

                "display":
                    display_symbol(
                        symbol
                    ),

                "side": side,

                "setup":
                    setup["type"],

                "entry":
                    trade["entry"],

                "sl":
                    trade["sl"],

                "tp":
                    trade["tp"],

                "rr":
                    trade["rr"],

                "atr":
                    trade["atr"],

                "score": score,

                "rvol": rvol,

                "trend": trend,

                "confirmation":
                    confirmation,

                "retest":
                    retest
            }

        return None

    except Exception as e:

        print(
            f"ERROR {symbol}: {e}"
        )

        return None


# ============================================================
# DATABASE
# ============================================================

def init_db():

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            side TEXT,
            setup TEXT,
            entry REAL,
            sl REAL,
            tp REAL,
            rr REAL,
            score INTEGER,
            rvol REAL,
            trend TEXT,
            status TEXT,
            result TEXT,
            pnl REAL,
            created_at TEXT,
            closed_at TEXT,
            exit_reason TEXT,
            closed_reported INTEGER DEFAULT 0
        )
    """)

    # --------------------------------------------------------
    # MIGRATION FOR OLD DATABASE
    # --------------------------------------------------------

    columns = []

    try:

        cur.execute(
            "PRAGMA table_info(trades)"
        )

        columns = [
            row[1]
            for row in cur.fetchall()
        ]

    except Exception:
        pass

    if "exit_reason" not in columns:

        try:

            cur.execute("""
                ALTER TABLE trades
                ADD COLUMN exit_reason TEXT
            """)

        except sqlite3.OperationalError:
            pass

    if "closed_reported" not in columns:

        try:

            cur.execute("""
                ALTER TABLE trades
                ADD COLUMN closed_reported INTEGER
                DEFAULT 0
            """)

        except sqlite3.OperationalError:
            pass

    conn.commit()
    conn.close()


# ============================================================
# CHECK OPEN SYMBOL
# ============================================================

def has_open_symbol(
    symbol
):

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT COUNT(*)
        FROM trades
        WHERE symbol = ?
        AND status = 'OPEN'
    """, (
        symbol,
    ))

    count = cur.fetchone()[0]

    conn.close()

    return count > 0


# ============================================================
# SAVE SIGNAL
# ============================================================

def save_signal(
    signal
):

    symbol = signal[
        "symbol"
    ]

    # یک معامله باز برای هر نماد
    if has_open_symbol(
        symbol
    ):

        print(
            f"SKIP DUPLICATE OPEN: "
            f"{symbol}"
        )

        return False

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

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
            rvol,
            trend,
            status,
            result,
            pnl,
            created_at,
            closed_at,
            exit_reason,
            closed_reported
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?,
            ?, ?, ?, ?, ?, ?, ?, ?
        )
    """, (
        signal["symbol"],
        signal["side"],
        signal["setup"],
        signal["entry"],
        signal["sl"],
        signal["tp"],
        signal["rr"],
        signal["score"],
        signal["rvol"],
        signal["trend"],
        "OPEN",
        "",
        0.0,
        now_utc().isoformat(),
        "",
        "",
        0
    ))

    conn.commit()
    conn.close()

    print(
        f"SAVED OPEN SIGNAL: "
        f"{signal['symbol']} "
        f"{signal['side']}"
    )

    return True


# ============================================================
# PREVIOUS UNREPORTED CLOSED SIGNALS
# ============================================================

def get_unreported_closed():

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp,
            pnl,
            result,
            exit_reason,
            closed_at
        FROM trades
        WHERE status = 'CLOSED'
        AND COALESCE(
            closed_reported,
            0
        ) = 0
        ORDER BY closed_at ASC
    """)

    rows = cur.fetchall()

    conn.close()

    closed = []

    for row in rows:

        closed.append({
            "id": row[0],
            "symbol": row[1],
            "side": row[2],
            "entry": safe_float(row[3]),
            "sl": safe_float(row[4]),
            "tp": safe_float(row[5]),
            "pnl": safe_float(row[6]),
            "result": row[7] or "",
            "exit_reason": row[8] or "",
            "closed_at": row[9] or ""
        })

    return closed


# ============================================================
# UPDATE OPEN TRADES
# ============================================================

def update_open_trades(
    price_map
):

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT
            id,
            symbol,
            side,
            entry,
            sl,
            tp
        FROM trades
        WHERE status = 'OPEN'
    """)

    rows = cur.fetchall()

    newly_closed = []

    for (
        trade_id,
        symbol,
        side,
        entry,
        sl,
        tp
    ) in rows:

        price = price_map.get(
            normalize_symbol(
                symbol
            )
        )

        if price is None:
            continue

        price = safe_float(
            price
        )

        if price <= 0:
            continue

        result = None
        exit_reason = None

        # ----------------------------------------------------
        # LONG
        # ----------------------------------------------------

        if side == "LONG":

            pnl = (
                price - entry
            ) / entry * 100

            if price >= tp:

                result = "WIN"
                exit_reason = "TP"

            elif price <= sl:

                result = "LOSS"
                exit_reason = "SL"

        # ----------------------------------------------------
        # SHORT
        # ----------------------------------------------------

        else:

            pnl = (
                entry - price
            ) / entry * 100

            if price <= tp:

                result = "WIN"
                exit_reason = "TP"

            elif price >= sl:

                result = "LOSS"
                exit_reason = "SL"

        if result:

            closed_time = (
                now_utc().isoformat()
            )

            cur.execute("""
                UPDATE trades
                SET
                    status = 'CLOSED',
                    result = ?,
                    pnl = ?,
                    closed_at = ?,
                    exit_reason = ?,
                    closed_reported = 0
                WHERE id = ?
            """, (
                result,
                pnl,
                closed_time,
                exit_reason,
                trade_id
            ))

            newly_closed.append({
                "id": trade_id,
                "symbol": symbol,
                "side": side,
                "pnl": pnl,
                "result": result,
                "exit_reason": exit_reason
            })

            print(
                f"CLOSED: {symbol} "
                f"{side} "
                f"{exit_reason} "
                f"{pnl:+.2f}%"
            )

    conn.commit()
    conn.close()

    return newly_closed


# ============================================================
# MARK CLOSED AS REPORTED
# ============================================================

def mark_closed_reported(
    trade_ids
):

    if not trade_ids:
        return

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    for trade_id in trade_ids:

        cur.execute("""
            UPDATE trades
            SET closed_reported = 1
            WHERE id = ?
            AND status = 'CLOSED'
        """, (
            trade_id,
        ))

    conn.commit()
    conn.close()


# ============================================================
# PRICE MAP
# ============================================================

def build_price_map(
    markets
):

    price_map = {}

    for market in markets:

        symbol = normalize_symbol(
            market.get("symbol")
        )

        price = safe_float(
            market.get("price")
        )

        if (
            symbol
            and
            price > 0
        ):

            price_map[
                symbol
            ] = price

    return price_map


# ============================================================
# OPEN SIGNALS
# ============================================================

def get_open_trades(
    price_map
):

    conn = sqlite3.connect(
        DB_FILE
    )

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
            rvol,
            trend,
            created_at
        FROM trades
        WHERE status = 'OPEN'
        ORDER BY created_at ASC
    """)

    rows = cur.fetchall()

    conn.close()

    result = []

    for row in rows:

        (
            trade_id,
            symbol,
            side,
            setup,
            entry,
            sl,
            tp,
            rr,
            score,
            rvol,
            trend,
            created_at
        ) = row

        entry = safe_float(
            entry
        )

        price = price_map.get(
            normalize_symbol(
                symbol
            )
        )

        if price is not None:

            price = safe_float(
                price
            )

        else:

            price = entry

        if entry > 0:

            if side == "LONG":

                pnl = (
                    price - entry
                ) / entry * 100

            else:

                pnl = (
                    entry - price
                ) / entry * 100

        else:

            pnl = 0.0

        result.append({
            "id": trade_id,
            "symbol": symbol,
            "display":
                display_symbol(
                    symbol
                ),
            "side": side,
            "setup": setup,
            "entry": entry,
            "sl": safe_float(sl),
            "tp": safe_float(tp),
            "rr": safe_float(rr),
            "score": int(
                score or 0
            ),
            "rvol": safe_float(rvol),
            "trend": trend,
            "now": price,
            "pnl": pnl,
            "created_at": created_at
        })

    return result


# ============================================================
# STATS
# ============================================================

def get_stats():

    conn = sqlite3.connect(
        DB_FILE
    )

    cur = conn.cursor()

    cur.execute("""
        SELECT
            COUNT(*),

            SUM(
                CASE
                    WHEN result = 'WIN'
                    THEN 1
                    ELSE 0
                END
            ),

            SUM(
                CASE
                    WHEN result = 'LOSS'
                    THEN 1
                    ELSE 0
                END
            ),

            COALESCE(
                SUM(pnl),
                0
            ),

            SUM(
                CASE
                    WHEN status = 'OPEN'
                    THEN 1
                    ELSE 0
                END
            )
        FROM trades
    """)

    row = cur.fetchone()

    conn.close()

    total = row[0] or 0
    wins = row[1] or 0
    losses = row[2] or 0
    pnl = row[3] or 0
    opened = row[4] or 0

    closed = (
        wins + losses
    )

    win_rate = (
        wins / closed * 100
        if closed
        else 0
    )

    return {
        "total": total,
        "wins": wins,
        "losses": losses,
        "pnl": pnl,
        "open": opened,
        "closed": closed,
        "win_rate": win_rate
    }


# ============================================================
# FORMAT OPEN SIGNAL
# ============================================================

def format_open_signal(
    trade
):

    icon = (
        "🟢"
        if trade["side"] == "LONG"
        else "🔴"
    )

    pnl = trade[
        "pnl"
    ]

    if pnl >= 0:

        pnl_text = (
            f"+{pnl:.2f}%"
        )

    else:

        pnl_text = (
            f"{pnl:.2f}%"
        )

    return (
        f"{icon} "
        f"{trade['display']} "
        f"{trade['side']}\n"
        f"💰 Entry: "
        f"{trade['entry']:.8g}\n"
        f"📍 Now: "
        f"{trade['now']:.8g}\n"
        f"🛑 SL: "
        f"{trade['sl']:.8g}\n"
        f"🎯 TP: "
        f"{trade['tp']:.8g}\n"
        f"📊 PnL: "
        f"{pnl_text}\n"
    )


# ============================================================
# FORMAT CLOSED SIGNAL
# ============================================================

def format_closed_signal(
    trade
):

    icon = (
        "✅"
        if trade["exit_reason"] == "TP"
        else "❌"
    )

    pnl = trade[
        "pnl"
    ]

    if pnl >= 0:

        pnl_text = (
            f"+{pnl:.2f}%"
        )

    else:

        pnl_text = (
            f"{pnl:.2f}%"
        )

    return (
        f"{icon} "
        f"{display_symbol(trade['symbol'])} "
        f"{trade['side']} "
        f"→ {trade['exit_reason']}\n"
        f"📊 PnL: {pnl_text}\n"
    )


# ============================================================
# COMPACT PIPELINE
# ============================================================

def format_pipeline():

    return (
        "📊 SCAN\n"
        f"Scanned: {DIAG['scanned']} | "
        f"Data: {DIAG['data_ok']}\n"
        f"RVOL≥2: {DIAG['rvol2']} | "
        f"BO: {DIAG['breakout']} | "
        f"REJ: {DIAG['rejection']}\n"
        f"5M Confirm: {DIAG['confirmation']} | "
        f"RR≥1:2: {DIAG['rr2']}\n"
        f"Qualified: {DIAG['qualified']}"
    )


# ============================================================
# REPORT
# ============================================================

def build_report(
    signals,
    open_trades,
    closed_trades,
    elapsed
):

    stats = get_stats()

    lines = []

    lines.append(
        "🤖 VOLUME-KHAT 100"
    )

    lines.append(
        "📡 VOLUME-KHAT 100 v2.2"
    )

    lines.append(
        f"🕐 "
        f"{now_utc().strftime('%Y-%m-%d %H:%M:%S')}"
        f" UTC"
    )

    lines.append(
        "⏱ TOP 100 | 5M CLOSED"
    )

    lines.append(
        "💻 Real trading: DISABLED"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # OPEN SIGNALS
    # ========================================================

    lines.append(
        "📂 OPEN SIGNALS"
    )

    lines.append("")

    if open_trades:

        for i, trade in enumerate(
            open_trades
        ):

            lines.append(
                format_open_signal(
                    trade
                )
            )

            if (
                i
                <
                len(open_trades) - 1
            ):

                lines.append(
                    "━━━━━━━━━━━━━━━━━━"
                )

    else:

        lines.append(
            "❌ NONE"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # CLOSED SINCE PREVIOUS REPORT
    # ========================================================

    lines.append(
        "📌 CLOSED SINCE PREVIOUS REPORT"
    )

    lines.append("")

    if closed_trades:

        for i, trade in enumerate(
            closed_trades
        ):

            lines.append(
                format_closed_signal(
                    trade
                )
            )

            if (
                i
                <
                len(closed_trades) - 1
            ):

                lines.append(
                    "━━━━━━━━━━━━━━━━━━"
                )

    else:

        lines.append(
            "NONE"
        )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # STATS
    # ========================================================

    lines.append(
        "📈 STATS"
    )

    lines.append(
        f"Open: "
        f"{stats['open']}"
    )

    lines.append(
        f"Closed: "
        f"{stats['closed']}"
    )

    lines.append(
        f"Wins: "
        f"{stats['wins']}"
    )

    lines.append(
        f"Losses: "
        f"{stats['losses']}"
    )

    lines.append(
        f"Win Rate: "
        f"{stats['win_rate']:.1f}%"
    )

    pnl = stats[
        "pnl"
    ]

    if pnl >= 0:

        pnl_text = (
            f"+{pnl:.2f}%"
        )

    else:

        pnl_text = (
            f"{pnl:.2f}%"
        )

    lines.append(
        f"Net PnL: "
        f"{pnl_text}"
    )

    lines.append(
        "━━━━━━━━━━━━━━━━━━"
    )

    # ========================================================
    # SCAN
    # ========================================================

    lines.append(
        format_pipeline()
    )

    lines.append(
        f"⏱ {elapsed:.1f}s"
    )

    text = "\n".join(
        lines
    )

    if len(text) > MAX_TELEGRAM_LENGTH:

        # ابتدا سعی می‌کنیم بخش Scan را نگه داریم.
        text = text[
            :MAX_TELEGRAM_LENGTH - 40
        ]

        text += (
            "\n...\n"
            "REPORT TRUNCATED"
        )

    return text


# ============================================================
# TELEGRAM
# ============================================================

def telegram_send(
    text
):

    if (
        not TELEGRAM_BOT_TOKEN
        or
        not TELEGRAM_CHAT_ID
    ):

        print(
            "Telegram credentials missing."
        )

        return False

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        f"sendMessage"
    )

    payload = {
        "chat_id":
            TELEGRAM_CHAT_ID,

        "text":
            text,

        "disable_web_page_preview":
            True
    }

    try:

        response = SESSION.post(
            url,
            json=payload,
            timeout=TIMEOUT
        )

        print(
            "Telegram:",
            response.status_code
        )

        if not response.ok:

            print(
                response.text[:500]
            )

        return response.ok

    except Exception as e:

        print(
            "Telegram ERROR:",
            e
        )

        return False


# ============================================================
# MAIN
# ============================================================

def main():

    start = time.time()

    print(
        "\n"
        "============================================================"
    )

    print(
        "VOLUME-KHAT 100 v2.2"
    )

    print(
        "KRAKEN FUTURES"
    )

    print(
        "============================================================"
    )

    # --------------------------------------------------------
    # DATABASE
    # --------------------------------------------------------

    init_db()

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Closed trades from the PREVIOUS execution are loaded
    # BEFORE we update current open trades.
    #
    # Therefore:
    #
    # Run 1 -> trade reaches SL/TP
    # Run 2 -> closed trade is reported
    #
    # Exactly what was requested.
    # --------------------------------------------------------

    previous_closed = (
        get_unreported_closed()
    )

    # --------------------------------------------------------
    # MARKET DISCOVERY
    # --------------------------------------------------------

    markets = get_top_markets()

    if not markets:

        report = (
            "🤖 VOLUME-KHAT 100\n"
            "📡 v2.2\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "❌ MARKET DISCOVERY FAILED\n"
            f"Instruments: "
            f"{DIAG['instruments']}\n"
            f"Tickers: "
            f"{DIAG['tickers']}\n"
            f"Perpetual: "
            f"{DIAG['perpetual']}\n"
            f"USD Perpetual: "
            f"{DIAG['usd_perpetual']}\n"
            f"Ranked: "
            f"{DIAG['ranked']}\n"
        )

        print(
            report
        )

        sent = telegram_send(
            report
        )

        # اگر ارسال موفق بود،
        # معاملات بسته قبلی هم گزارش شده‌اند.
        if sent and previous_closed:

            mark_closed_reported([
                x["id"]
                for x in previous_closed
            ])

        return

    DIAG["scanned"] = len(
        markets
    )

    # --------------------------------------------------------
    # PRICE MAP
    # --------------------------------------------------------

    price_map = build_price_map(
        markets
    )

    # --------------------------------------------------------
    # UPDATE EXISTING OPEN TRADES
    #
    # معاملات موجود قبل از ساخت سیگنال جدید بررسی می‌شوند.
    # بنابراین یک سیگنال تازه همان لحظه با قیمت جاری بسته نمی‌شود.
    # --------------------------------------------------------

    newly_closed = (
        update_open_trades(
            price_map
        )
    )

    # --------------------------------------------------------
    # SCAN
    # --------------------------------------------------------

    signals = []

    print(
        f"\nScanning "
        f"{len(markets)} markets..."
    )

    workers = min(
        10,
        max(
            2,
            len(markets)
        )
    )

    with ThreadPoolExecutor(
        max_workers=workers
    ) as executor:

        futures = {
            executor.submit(
                process_market,
                market
            ): market
            for market in markets
        }

        for future in as_completed(
            futures
        ):

            market = futures[
                future
            ]

            try:

                signal = (
                    future.result()
                )

                if signal:

                    signals.append(
                        signal
                    )

            except Exception as e:

                print(
                    f"Worker ERROR "
                    f"{market['symbol']}: "
                    f"{e}"
                )

    # --------------------------------------------------------
    # SORT SIGNALS
    # --------------------------------------------------------

    signals.sort(
        key=lambda x: (
            x["score"],
            x["rr"],
            x["rvol"]
        ),
        reverse=True
    )

    DIAG["qualified"] = len(
        signals
    )

    # --------------------------------------------------------
    # SAVE QUALIFIED SIGNALS
    #
    # همه سیگنال‌های معتبر ذخیره می‌شوند،
    # اما برای هر نماد فقط یک OPEN مجاز است.
    # --------------------------------------------------------

    for signal in signals:

        save_signal(
            signal
        )

    # --------------------------------------------------------
    # OPEN TRADES AFTER SAVING
    # --------------------------------------------------------

    open_trades = get_open_trades(
        price_map
    )

    # --------------------------------------------------------
    # CLOSED REPORT
    #
    # فقط معاملات بسته‌شده‌ای که از اجرای قبلی هنوز گزارش نشده‌اند.
    #
    # newly_closed در این گزارش نمایش داده نمی‌شود.
    # این همان منطق "گزارش بعدی" است.
    # --------------------------------------------------------

    closed_for_report = (
        previous_closed
    )

    # --------------------------------------------------------
    # REPORT
    # --------------------------------------------------------

    elapsed = (
        time.time()
        -
        start
    )

    report = build_report(
        signals=signals,
        open_trades=open_trades,
        closed_trades=closed_for_report,
        elapsed=elapsed
    )

    print(
        "\n"
        +
        report
        +
        "\n"
    )

    # --------------------------------------------------------
    # TELEGRAM
    # --------------------------------------------------------

    sent = telegram_send(
        report
    )

    # --------------------------------------------------------
    # IMPORTANT:
    #
    # Closed signals are marked as reported ONLY if Telegram
    # successfully received the report.
    #
    # اگر تلگرام خطا بدهد، معامله بسته‌شده در اجرای بعدی
    # دوباره گزارش خواهد شد و گم نمی‌شود.
    # --------------------------------------------------------

    if sent and closed_for_report:

        mark_closed_reported([
            x["id"]
            for x in closed_for_report
        ])

    # --------------------------------------------------------
    # CONSOLE SUMMARY
    # --------------------------------------------------------

    print(
        "============================================================"
    )

    print(
        "SCAN FINISHED"
    )

    print(
        f"Markets scanned : "
        f"{len(markets)}"
    )

    print(
        f"Qualified       : "
        f"{len(signals)}"
    )

    print(
        f"Open trades     : "
        f"{len(open_trades)}"
    )

    print(
        f"Previous closed : "
        f"{len(closed_for_report)}"
    )

    print(
        f"Newly closed    : "
        f"{len(newly_closed)}"
    )

    print(
        f"Scan time       : "
        f"{elapsed:.2f}s"
    )

    print(
        "============================================================"
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()
